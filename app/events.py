"""In-memory SSE pub/sub hub. Single process, single user — a set of asyncio
queues is all we need. Thread-safe publish (the worker runs jobs in a thread)."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Optional

_subscribers: set[asyncio.Queue] = set()
_loop: Optional[asyncio.AbstractEventLoop] = None


def init(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def publish(event: str, data: dict[str, Any]) -> None:
    """Safe to call from any thread."""
    if _loop is None:
        return
    payload = {"event": event, **data}

    def _fanout() -> None:
        for q in list(_subscribers):
            if q.qsize() < 500:
                q.put_nowait(payload)

    try:
        _loop.call_soon_threadsafe(_fanout)
    except RuntimeError:
        pass  # loop closed during shutdown


async def stream() -> AsyncIterator[str]:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)
    try:
        yield ": connected\n\n"
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=15)
                yield f"data: {json.dumps(item)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        _subscribers.discard(q)
