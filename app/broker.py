"""In-process pub/sub: wakes every open dashboard stream when a reading arrives.

Works because the app runs as a single process (see db.py). If you ever need more
than one process, replace this with a real broker (e.g. Redis or NATS).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class Broker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[None]] = set()

    def publish(self) -> None:
        for q in self._subscribers:
            if q.empty():  # one pending wake-up is enough; the stream re-reads the DB
                q.put_nowait(None)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[None]]:
        q: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
