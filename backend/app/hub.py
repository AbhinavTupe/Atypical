"""In-memory fan-out hub.

A single process holds one Postgres LISTEN connection and re-broadcasts every
notification to all locally-connected SSE clients. Each client gets its own
bounded asyncio.Queue; a slow client that fills its queue is dropped rather than
being allowed to back-pressure the listener (isolation between clients).

Scaling note: this hub is per-process and that is fine. With N backend replicas,
each replica keeps its own LISTEN connection, Postgres delivers every NOTIFY to
all listeners, and each replica fans out to the clients it holds. No shared state
needed. See README for where you'd swap this for Redis/NATS at higher scale.
"""
import asyncio
import logging

logger = logging.getLogger("hub")

# Max messages buffered per client before we consider it too slow and drop it.
_MAX_QUEUE = 100


class Hub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_MAX_QUEUE)
        async with self._lock:
            self._subscribers.add(queue)
        logger.info("client subscribed (total=%d)", len(self._subscribers))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
        logger.info("client unsubscribed (total=%d)", len(self._subscribers))

    async def broadcast(self, message: str) -> None:
        """Push a message to every subscriber. Drop clients that can't keep up."""
        async with self._lock:
            targets = list(self._subscribers)

        for queue in targets:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("dropping slow client (queue full)")
                await self.unsubscribe(queue)

    @property
    def size(self) -> int:
        return len(self._subscribers)
