"""A tiny in-process rate limiter (sliding window), shared by one runner process.

Veto spends real money and calls two paid APIs behind one public URL, so the
doors need a ceiling: agent keys, console sessions and Slack users each get a
budget of calls per minute. It is deliberately process-local — a hosted deploy
with several workers would move this to the store — but for one runner it is
enough and costs nothing.

The limiter is injectable with a clock so tests do not sleep.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """Allow at most ``limit`` calls per ``per_seconds`` for each key."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def note(self, key: str, value: float) -> None:
        """Record one allowed call (used for tests and explicit bookkeeping)."""
        with self._lock:
            self._hits.setdefault(key, []).append(value)

    def allow(
        self,
        key: str,
        *,
        limit: int,
        per_seconds: float = 60.0,
    ) -> tuple[bool, float]:
        """Return ``(allowed, retry_after_seconds)``.

        A denied call records nothing, so a burst does not extend its own lockout
        beyond the window that is already full.
        """
        window = max(1.0, float(per_seconds))
        limit = max(1, int(limit))
        now = self._clock()
        cutoff = now - window
        with self._lock:
            hits = self._hits.setdefault(key, [])
            while hits and hits[0] <= cutoff:
                hits.pop(0)
            if len(hits) >= limit:
                retry = window - (now - hits[0])
                return False, max(0.5, retry)
            hits.append(now)
            return True, 0.0

    def remaining(self, key: str, *, limit: int, per_seconds: float = 60.0) -> int:
        window = max(1.0, float(per_seconds))
        now = self._clock()
        cutoff = now - window
        with self._lock:
            hits = self._hits.get(key) or []
            return max(0, int(limit) - sum(1 for hit in hits if hit > cutoff))

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def keys(self) -> list[str]:
        with self._lock:
            return sorted(self._hits)
