"""Database layer: connection pool, CRUD, and the LISTEN background task."""
import asyncio
import logging

import asyncpg

from . import config
from .hub import Hub

logger = logging.getLogger("db")


class Database:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self._listen_conn: asyncpg.Connection | None = None
        self._listen_task: asyncio.Task | None = None

    # --- lifecycle ---------------------------------------------------------

    async def connect(self, retries: int = 10, delay: float = 2.0) -> None:
        """Create the pool, retrying while Postgres finishes booting."""
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self._pool = await asyncpg.create_pool(
                    config.DATABASE_URL, min_size=1, max_size=10
                )
                logger.info("connected to postgres")
                return
            except (OSError, asyncpg.PostgresError) as err:  # not ready yet
                last_err = err
                logger.warning("db not ready (attempt %d/%d): %s", attempt, retries, err)
                await asyncio.sleep(delay)
        raise RuntimeError(f"could not connect to postgres: {last_err}")

    async def start_listener(self, hub: Hub) -> None:
        """Hold a dedicated connection that LISTENs and forwards to the hub.

        We use a standalone connection (not one from the pool) because it lives
        for the whole app lifetime and must not be recycled back into the pool.
        """
        self._listen_conn = await asyncpg.connect(config.DATABASE_URL)

        def _on_notify(_conn, _pid, _channel, payload: str) -> None:
            # Callback runs on the event loop; schedule the async broadcast.
            asyncio.create_task(hub.broadcast(payload))

        await self._listen_conn.add_listener(config.NOTIFY_CHANNEL, _on_notify)
        logger.info("listening on channel '%s'", config.NOTIFY_CHANNEL)

    async def close(self) -> None:
        if self._listen_conn is not None:
            await self._listen_conn.close()
        if self._pool is not None:
            await self._pool.close()

    # --- CRUD --------------------------------------------------------------

    async def list_orders(self) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM orders ORDER BY id")
            return [dict(r) for r in rows]

    async def create_order(self, customer_name: str, product_name: str, status: str) -> dict:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO orders (customer_name, product_name, status)
                   VALUES ($1, $2, $3) RETURNING *""",
                customer_name, product_name, status,
            )
            return dict(row)

    async def update_order(self, order_id: int, fields: dict) -> dict | None:
        if not fields:
            return await self.get_order(order_id)
        # Build a parameterised SET clause from the provided fields only.
        cols = list(fields.keys())
        assignments = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
        values = [fields[c] for c in cols]
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE orders SET {assignments} WHERE id = $1 RETURNING *",
                order_id, *values,
            )
            return dict(row) if row else None

    async def get_order(self, order_id: int) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
            return dict(row) if row else None

    async def delete_order(self, order_id: int) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM orders WHERE id = $1", order_id)
            return result.endswith("1")
