"""Rate limiter tests: one process, sliding minute, no sleeping.

The clock is injected, so the window is deterministic.
"""
from __future__ import annotations

import unittest

from veto.ratelimit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class RateLimiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.limiter = RateLimiter(clock=self.clock)

    def test_allows_up_to_the_limit_then_refuses(self) -> None:
        for _ in range(3):
            allowed, retry = self.limiter.allow("k", limit=3)
            self.assertTrue(allowed)
            self.assertEqual(retry, 0.0)
        allowed, retry = self.limiter.allow("k", limit=3)
        self.assertFalse(allowed)
        self.assertGreater(retry, 0)
        self.assertLessEqual(retry, 60)

    def test_the_window_slides(self) -> None:
        for _ in range(2):
            self.assertTrue(self.limiter.allow("k", limit=2)[0])
        self.assertFalse(self.limiter.allow("k", limit=2)[0])
        self.clock.now += 30
        self.assertFalse(self.limiter.allow("k", limit=2)[0])
        self.clock.now += 31  # first two calls are now outside the minute
        self.assertTrue(self.limiter.allow("k", limit=2)[0])

    def test_denied_calls_do_not_extend_the_lockout(self) -> None:
        for _ in range(2):
            self.limiter.allow("k", limit=2)
        for _ in range(5):
            self.assertFalse(self.limiter.allow("k", limit=2)[0])
        self.clock.now += 61
        self.assertTrue(self.limiter.allow("k", limit=2)[0])

    def test_keys_are_independent(self) -> None:
        for _ in range(2):
            self.assertTrue(self.limiter.allow("a", limit=2)[0])
        self.assertFalse(self.limiter.allow("a", limit=2)[0])
        self.assertTrue(self.limiter.allow("b", limit=2)[0])

    def test_custom_windows_are_honoured(self) -> None:
        self.assertTrue(self.limiter.allow("k", limit=1, per_seconds=10)[0])
        allowed, retry = self.limiter.allow("k", limit=1, per_seconds=10)
        self.assertFalse(allowed)
        self.assertLessEqual(retry, 10)
        self.clock.now += 11
        self.assertTrue(self.limiter.allow("k", limit=1, per_seconds=10)[0])

    def test_remaining_counts_the_window(self) -> None:
        self.assertEqual(self.limiter.remaining("k", limit=5), 5)
        self.limiter.allow("k", limit=5)
        self.limiter.allow("k", limit=5)
        self.assertEqual(self.limiter.remaining("k", limit=5), 3)
        self.clock.now += 61
        self.assertEqual(self.limiter.remaining("k", limit=5), 5)

    def test_reset(self) -> None:
        self.limiter.allow("k", limit=1)
        self.assertFalse(self.limiter.allow("k", limit=1)[0])
        self.limiter.reset("k")
        self.assertTrue(self.limiter.allow("k", limit=1)[0])
        self.limiter.reset()
        self.assertEqual(self.limiter.keys(), [])


if __name__ == "__main__":
    unittest.main()
