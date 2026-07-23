"""Shared test fixtures.

Tests need a real Postgres, because the whole point of the system is the database
trigger and LISTEN/NOTIFY. To keep the tests easy to run, this boots a throwaway
Postgres in-process using `pgserver` (no install needed). If `TEST_DATABASE_URL` is
set, that database is used instead, which is how you'd run the tests on Windows or
against the one from docker compose.

The app is run in a real uvicorn server on a spare port and hit over HTTP. That is
the honest way to test Server-Sent Events: a real connection that really closes,
just like a browser.
"""
import asyncio
import importlib
import os
import socket
import tempfile
import threading
import time

import asyncpg
import httpx
import pytest
import pytest_asyncio
import uvicorn

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "db", "init.sql")

SEED = [
    ("Ada Lovelace", "Mechanical Keyboard", "pending"),
    ("Alan Turing", "USB-C Hub", "shipped"),
    ("Grace Hopper", "Laptop Stand", "delivered"),
]


async def _apply_schema(url: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        with open(SCHEMA_PATH) as f:
            await conn.execute(f.read())
    finally:
        await conn.close()


async def _reseed(url: str) -> None:
    """Reset to a known set of 3 orders with ids 1..3, before each test."""
    conn = await asyncpg.connect(url)
    try:
        await conn.execute("TRUNCATE orders RESTART IDENTITY")
        await conn.executemany(
            "INSERT INTO orders (customer_name, product_name, status) VALUES ($1, $2, $3)",
            SEED,
        )
    finally:
        await conn.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def pg_url():
    """A Postgres to test against, with the schema applied."""
    provided = os.getenv("TEST_DATABASE_URL")
    if provided:
        asyncio.run(_apply_schema(provided))
        yield provided
        return

    import pgserver

    data_dir = tempfile.mkdtemp(prefix="orders_test_pg_")
    server = pgserver.get_server(data_dir)
    try:
        server.psql("CREATE DATABASE orders_test;")
        url = server.get_uri(database="orders_test")
        asyncio.run(_apply_schema(url))
        yield url
    finally:
        server.cleanup()


@pytest.fixture(scope="session")
def base_url(pg_url):
    """Start the real app once for the whole test session and return its URL."""
    os.environ["DATABASE_URL"] = pg_url
    # Short heartbeat so a closed SSE stream is noticed quickly.
    os.environ["HEARTBEAT_SECONDS"] = "0.5"
    from app import config
    importlib.reload(config)  # pick up the test DATABASE_URL and heartbeat
    from app.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    with httpx.Client(trust_env=False) as probe:
        for _ in range(100):
            try:
                if probe.get(url + "/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("app did not start in time")

    yield url

    server.should_exit = True
    thread.join(timeout=5)


@pytest_asyncio.fixture(autouse=True)
async def reseed(pg_url):
    """Reset the table before every test so tests are independent."""
    await _reseed(pg_url)


@pytest_asyncio.fixture
async def client(base_url):
    """An HTTP client pointed at the running app."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False) as c:
        yield c


@pytest_asyncio.fixture
async def raw_db(pg_url):
    """A direct database connection, for changes that bypass the API."""
    conn = await asyncpg.connect(pg_url)
    try:
        yield conn
    finally:
        await conn.close()
