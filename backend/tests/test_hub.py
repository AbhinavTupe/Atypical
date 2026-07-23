"""Unit tests for the fan-out hub. Fast, no database needed."""
import asyncio

import pytest

from app.hub import Hub


async def test_broadcast_reaches_every_subscriber():
    hub = Hub()
    a = await hub.subscribe()
    b = await hub.subscribe()

    await hub.broadcast("hello")

    assert a.get_nowait() == "hello"
    assert b.get_nowait() == "hello"
    assert hub.size == 2


async def test_unsubscribe_stops_delivery():
    hub = Hub()
    a = await hub.subscribe()
    await hub.unsubscribe(a)

    await hub.broadcast("hello")

    assert hub.size == 0
    assert a.empty()


async def test_slow_client_is_dropped_and_others_are_safe():
    """A client whose queue is full gets dropped, so it can't stall the rest."""
    hub = Hub()
    slow = await hub.subscribe()
    healthy = await hub.subscribe()

    # Fill the slow client's queue to its limit without draining it.
    while not slow.full():
        slow.put_nowait("x")

    await hub.broadcast("new-change")

    # The slow one is gone; the healthy one still received the message.
    assert hub.size == 1
    assert healthy.get_nowait() == "new-change"
