"""The tests that prove the headline claims:

  1. A client gets a snapshot of current state the moment it connects.
  2. A change reaches connected clients in real time, including a change made
     directly in the database, bypassing the API entirely.
"""
import asyncio
import json

import pytest


async def _next_line(lines, timeout=5.0):
    return await asyncio.wait_for(lines.__anext__(), timeout)


async def _read_snapshot(lines):
    """Read forward until the snapshot event, return the list of orders in it."""
    event = "message"
    while True:
        line = await _next_line(lines)
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:") and event == "snapshot":
            return json.loads(line[5:].strip())


async def _read_delta(lines):
    """Read forward until the next change event, return it. Skips heartbeats."""
    event = "message"
    while True:
        line = await _next_line(lines)
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
            if data and event == "message":
                return json.loads(data)
            event = "message"


async def test_snapshot_is_sent_on_connect(client):
    async with client.stream("GET", "/events") as resp:
        snapshot = await _read_snapshot(resp.aiter_lines())

    assert len(snapshot) == 3
    assert {o["id"] for o in snapshot} == {1, 2, 3}


async def test_api_change_is_pushed_to_client(client):
    async with client.stream("GET", "/events") as resp:
        lines = resp.aiter_lines()
        await _read_snapshot(lines)  # once we've read this, we're subscribed

        # Make a change through the API.
        await client.post(
            "/orders",
            json={"customer_name": "Zoe", "product_name": "Cable", "status": "pending"},
        )

        delta = await _read_delta(lines)

    assert delta["operation"] == "INSERT"
    assert delta["data"]["customer_name"] == "Zoe"


async def test_direct_database_change_is_pushed_to_client(client, raw_db):
    """The key property: a change that never touches the API still reaches clients."""
    async with client.stream("GET", "/events") as resp:
        lines = resp.aiter_lines()
        await _read_snapshot(lines)

        # Change a row directly in the database, not through the API.
        await raw_db.execute("UPDATE orders SET status = 'delivered' WHERE id = 1")

        delta = await _read_delta(lines)

    assert delta["operation"] == "UPDATE"
    assert delta["id"] == 1
    assert delta["data"]["status"] == "delivered"
