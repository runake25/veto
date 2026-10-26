"""The change feed: one in-process fan-out for every write worth refreshing on.

The web app keeps a long-lived Server-Sent Events connection open to
``GET /api/v1/events``. A mutation does not know (or care) which tabs are
watching; it publishes a scope name here and every subscriber receives it. A tab
refreshes only the slice it asked about.

Design rules that keep a slow browser from ever slowing a write down:

* a subscriber is a bounded ``queue.Queue``; publishing is ``put_nowait`` and a
  full queue simply drops that frame (the tab will refresh on its next event or
  reload, which is better than blocking a request thread);
* publishing never raises, whatever state a subscriber is in;
* the subscriber set is guarded by one lock, but the lock is released before a
  frame is offered, so one wedged queue cannot hold up the others.
"""
from __future__ import annotations

import queue
import threading

# Known scopes. The feed itself does not enforce this list (any string works),
# but keeping the vocabulary here means publishers and the UI cannot drift apart
# by typo without a failing test.
SCOPES = ("requests", "agents", "integrations")

DEFAULT_QUEUE_SIZE = 64


class ChangeFeed:
    """Thread-safe fan-out of small string scopes to bounded per-tab queues."""

    def __init__(self, *, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._queue_size = max(1, int(queue_size))

    def subscribe(self) -> queue.Queue:
        """Create and register a bounded queue for one SSE connection."""
        subscriber: queue.Queue = queue.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue | None) -> None:
        """Forget a queue. Unknown/already-removed queues are a no-op."""
        if subscriber is None:
            return
        with self._lock:
            self._subscribers.discard(subscriber)

    def publish(self, scope: str) -> int:
        """Offer ``scope`` to every subscriber; return how many accepted it.

        Never blocks and never raises: a full or broken subscriber just misses
        this frame.
        """
        if not scope:
            return 0
        with self._lock:
            subscribers = tuple(self._subscribers)
        delivered = 0
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(scope)
                delivered += 1
            except queue.Full:
                # the tab is not draining; drop the frame rather than wait for it
                continue
            except Exception:  # noqa: BLE001 - a broken subscriber is not our problem
                continue
        return delivered

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


# The one feed the runner and the scheduler share. Importing modules publish to
# this object; the HTTP layer is the only subscriber in practice (plus tests).
FEED = ChangeFeed()
