"""Fan-out of server and robot events to SSE subscribers.

Every connected client gets its own bounded queue. A client that stops reading
(a stalled browser tab, a suspended eco session, a laptop that went to sleep)
must never be able to block the poller: when a queue fills, its oldest entries
are dropped and a counter is bumped, rather than the publisher blocking. This
is the one place where dropping data is correct -- the payloads are periodic
status snapshots, so the next one supersedes whatever was lost.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

#: Per-subscriber backlog. At the default 5 Hz poll this is ~50 s of history,
#: far more than any healthy client needs.
DEFAULT_QUEUE_SIZE = 256


class Subscriber:
    def __init__(self, name, events=None, maxsize=DEFAULT_QUEUE_SIZE):
        self.name = name
        #: Event names this client wants; None means everything.
        self.events = set(events) if events else None
        self.queue = queue.Queue(maxsize=maxsize)
        self.dropped = 0
        self.created = time.time()

    def wants(self, event_name) -> bool:
        return self.events is None or event_name in self.events

    def put(self, item):
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            try:
                self.queue.get_nowait()      # drop oldest
                self.dropped += 1
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                self.dropped += 1


class EventBus:
    """Publish/subscribe with SSE framing."""

    def __init__(self):
        self._subscribers = []
        self._lock = threading.Lock()
        #: Last value of each event, replayed to a client on connect so a fresh
        #: subscriber does not have to wait a full poll period to show anything.
        self._latest = {}

    def subscribe(self, name="client", events=None) -> Subscriber:
        subscriber = Subscriber(name, events)
        with self._lock:
            self._subscribers.append(subscriber)
            replay = [(n, v) for n, v in self._latest.items() if subscriber.wants(n)]
        for event_name, value in replay:
            subscriber.put(self.format(event_name, value))
        logger.info("SSE subscriber %s connected (%d total)",
                    name, len(self._subscribers))
        return subscriber

    def unsubscribe(self, subscriber):
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)
        logger.info("SSE subscriber %s disconnected after %.0f s (%d dropped)",
                    subscriber.name, time.time() - subscriber.created,
                    subscriber.dropped)

    def publish(self, event_name, value):
        with self._lock:
            self._latest[event_name] = value
            targets = [s for s in self._subscribers if s.wants(event_name)]
        if not targets:
            return
        frame = self.format(event_name, value)
        for subscriber in targets:
            subscriber.put(frame)

    @staticmethod
    def format(event_name, value) -> str:
        """One SSE frame. Payload is always JSON, matching the pshell server."""
        try:
            data = json.dumps(value, default=str)
        except (TypeError, ValueError):
            data = json.dumps(str(value))
        return f"event: {event_name}\ndata: {data}\n\n"

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def stats(self):
        with self._lock:
            return [
                {"name": s.name, "events": sorted(s.events) if s.events else None,
                 "queued": s.queue.qsize(), "dropped": s.dropped,
                 "age_s": round(time.time() - s.created, 1)}
                for s in self._subscribers
            ]
