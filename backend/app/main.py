"""FastAPI app: REST endpoints for orders + an SSE stream of live changes.

Flow:
    client --HTTP--> FastAPI --SQL--> Postgres
    Postgres --trigger--> pg_notify('orders_changes', payload)
    Postgres --LISTEN--> FastAPI listener --> Hub --> SSE --> all clients

The write path and the notify path are decoupled: even a change made directly in
psql (not through this API) reaches every connected client.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import Database
from .hub import Hub
from .models import Order, OrderCreate, OrderUpdate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("main")

db = Database()
hub = Hub()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await db.connect()
    await db.start_listener(hub)
    yield
    await db.close()


app = FastAPI(title="Real-time Orders", lifespan=lifespan)

# Allow the static client (and any demo tooling) to call the API from a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _dumps(obj) -> str:
    """JSON dump that knows how to serialise timestamps."""
    return json.dumps(
        obj,
        default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o),
    )


# --- SSE stream ------------------------------------------------------------

@app.get("/events")
async def events(request: Request) -> StreamingResponse:
    """One-way stream of the orders state.

    Delivery order on this single stream:
      1. a `snapshot` event with every current order, and
      2. `data` events for each later change.

    We subscribe to the hub BEFORE reading the snapshot. That ordering matters:
    if a change lands in the tiny window while we're reading the snapshot, it's
    already sitting in our queue and gets delivered right after the snapshot,
    so nothing is lost. Any overlap (a change that made it into both) is
    harmless because the client de-duplicates by id and updated_at.

    This is what makes a client that just refreshed, or one that connects late,
    end up with correct, current state without a gap.
    """
    queue = await hub.subscribe()

    async def event_generator():
        try:
            snapshot = await db.list_orders()
            yield f"event: snapshot\ndata: {_dumps(snapshot)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=config.HEARTBEAT_SECONDS)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Comment line is a heartbeat; keeps the connection alive.
                    yield ": ping\n\n"
        finally:
            await hub.unsubscribe(queue)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # disable nginx buffering if present
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


# --- REST (so the demo UI can drive changes) -------------------------------

@app.get("/orders", response_model=list[Order])
async def list_orders():
    return await db.list_orders()


@app.post("/orders", response_model=Order, status_code=201)
async def create_order(body: OrderCreate):
    return await db.create_order(body.customer_name, body.product_name, body.status)


@app.patch("/orders/{order_id}", response_model=Order)
async def update_order(order_id: int, body: OrderUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    order = await db.update_order(order_id, fields)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@app.delete("/orders/{order_id}", status_code=204)
async def delete_order(order_id: int):
    if not await db.delete_order(order_id):
        raise HTTPException(status_code=404, detail="order not found")


@app.get("/health")
async def health():
    return {"status": "ok", "subscribers": hub.size}


# Serve the browser client at /  (mounted last so the API routes above win).
app.mount("/", StaticFiles(directory="static", html=True), name="static")
