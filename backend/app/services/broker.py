"""In-process event fan-out for the live chat and the staff inbox.

The database holds the state; this only pushes changes to the streams that are open right now.
A restart drops the open streams, and each client re-reads its state when it reconnects. This
is correct for the single backend process the deployment runs. Several processes would need
Postgres LISTEN/NOTIFY or Redis instead.
"""

import asyncio
import json
import logging
from collections import defaultdict
from uuid import UUID

logger = logging.getLogger(__name__)

QUEUE_SIZE = 200
HEARTBEAT_SECONDS = 15


def staff_channel(client_id: UUID) -> str:
    return f"staff:{client_id}"


def visitor_channel(handover_id: UUID) -> str:
    return f"visitor:{handover_id}"


class Broker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, channel: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers[channel].add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(channel)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(channel, None)

    def publish(self, channel: str, event: str, data: dict) -> None:
        """Never blocks. A stream that cannot keep up misses events and re-reads its state."""
        for queue in list(self._subscribers.get(channel, ())):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                logger.warning("Live stream fell behind; it will resync on reconnect")

    def subscriber_count(self, channel: str) -> int:
        return len(self._subscribers.get(channel, ()))


broker = Broker()


def sse_message(event: str, data: dict) -> str:
    """One Server-Sent Events frame. The payload is JSON on a single data line."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def stream_frames(queue: asyncio.Queue, *, is_closed=None):
    """Yields SSE frames from a subscription, with a comment every HEARTBEAT_SECONDS.

    Stops when is_closed() returns True (a request was ended) or the consumer goes away.
    """
    while True:
        if is_closed is not None and is_closed():
            return
        try:
            event, data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            yield ": keep-alive\n\n"
            continue
        yield sse_message(event, data)
        if event == "handover_ended":
            return
