"""Change feed tests: fan-out, bounded queues, and the scheduler's signals."""
from __future__ import annotations

import queue
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veto import routines  # noqa: E402
from veto.events import FEED, SCOPES, ChangeFeed  # noqa: E402
from veto.policy import RULE_KINDS  # noqa: E402
from veto.store import Store  # noqa: E402
from test_requests import (  # noqa: E402
    FakeSettlement,
    agent_fields,
    choose_first,
    extract_one,
    search_one,
)

FIXED = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def drain(subscriber: queue.Queue) -> list[str]:
    items: list[str] = []
    while True:
        try:
            items.append(subscriber.get_nowait())
        except queue.Empty:
            return items


class ChangeFeedTests(unittest.TestCase):
    def test_subscribe_returns_a_queue_and_counts(self) -> None:
        feed = ChangeFeed()
        self.assertEqual(feed.subscriber_count(), 0)
        subscriber = feed.subscribe()
        self.assertIsInstance(subscriber, queue.Queue)
        self.assertEqual(feed.subscriber_count(), 1)
        feed.unsubscribe(subscriber)
        self.assertEqual(feed.subscriber_count(), 0)

    def test_publish_delivers_to_every_subscriber(self) -> None:
        feed = ChangeFeed()
        first = feed.subscribe()
        second = feed.subscribe()
        self.assertEqual(feed.publish("requests"), 2)
        self.assertEqual(drain(first), ["requests"])
        self.assertEqual(drain(second), ["requests"])

    def test_unsubscribe_stops_delivery(self) -> None:
        feed = ChangeFeed()
        subscriber = feed.subscribe()
        feed.unsubscribe(subscriber)
        self.assertEqual(feed.publish("agents"), 0)
        self.assertEqual(feed.subscriber_count(), 0)
        # unsubscribing twice / an unknown object is harmless
        feed.unsubscribe(subscriber)
        feed.unsubscribe(None)
        self.assertEqual(feed.subscriber_count(), 0)

    def test_publish_with_no_subscribers_is_a_no_op(self) -> None:
        feed = ChangeFeed()
        self.assertEqual(feed.publish("integrations"), 0)
        self.assertEqual(feed.publish("requests"), 0)

    def test_empty_scope_is_not_published(self) -> None:
        feed = ChangeFeed()
        subscriber = feed.subscribe()
        self.assertEqual(feed.publish(""), 0)
        self.assertEqual(drain(subscriber), [])

    def test_bounded_queue_drops_instead_of_blocking(self) -> None:
        feed = ChangeFeed(queue_size=2)
        subscriber = feed.subscribe()
        delivered = sum(feed.publish("requests") for _ in range(5))
        self.assertEqual(delivered, 2)
        self.assertEqual(drain(subscriber), ["requests", "requests"])

    def test_slow_subscriber_does_not_block_a_fast_one(self) -> None:
        feed = ChangeFeed(queue_size=2)
        slow = feed.subscribe()   # never drained
        fast = feed.subscribe()
        self.assertEqual(feed.publish("requests"), 2)
        self.assertEqual(feed.publish("requests"), 2)   # both queues full now
        self.assertEqual(drain(fast), ["requests", "requests"])
        self.assertEqual(feed.publish("agents"), 1)     # slow is full, fast accepts
        self.assertEqual(drain(fast), ["agents"])
        self.assertEqual(drain(slow), ["requests", "requests"])

    def test_broken_subscriber_never_raises(self) -> None:
        feed = ChangeFeed()
        broken = feed.subscribe()
        good = feed.subscribe()

        def explode(scope: str) -> None:
            raise RuntimeError("the socket is gone")

        broken.put_nowait = explode  # type: ignore[method-assign]
        self.assertEqual(feed.publish("agents"), 1)
        self.assertEqual(drain(good), ["agents"])

    def test_concurrent_publishers_are_safe(self) -> None:
        feed = ChangeFeed(queue_size=1000)
        subscriber = feed.subscribe()

        def worker() -> None:
            for _ in range(50):
                feed.publish("requests")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(drain(subscriber)), 200)

    def test_module_feed_exists(self) -> None:
        self.assertIsInstance(FEED, ChangeFeed)
        self.assertIn("requests", SCOPES)
        self.assertIn("agents", SCOPES)
        self.assertIn("integrations", SCOPES)


class TickPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.agent = self.store.create_agent(
            self.workspace["id"],
            agent_fields(),
            vendors=["amazon.com"],
            categories=["pantry"],
            rules=list(RULE_KINDS),
        )
        self.settlement = FakeSettlement()
        self.subscriber = FEED.subscribe()
        self.addCleanup(FEED.unsubscribe, self.subscriber)

    def make_due_routine(self, agent_id: str | None = None) -> dict:
        return self.store.create_routine(
            self.workspace["id"],
            agent_id or self.agent["id"],
            name="Pantry restock",
            instruction="2 packs of toilet paper",
            cadence="weekly",
            envelope_cents=25_000,
            dedupe_days=0,
            next_run_at=stamp(FIXED - timedelta(days=1)),
        )

    def tick(self):
        return routines.tick(
            self.store,
            self.settlement,
            now=FIXED,
            extract_fn=extract_one(),
            search_fn=search_one(),
            choose_fn=choose_first,
        )

    def test_tick_publishes_agents_then_requests_when_a_run_orders(self) -> None:
        self.make_due_routine()
        runs = self.tick()
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0]["request_id"])
        self.assertEqual(drain(self.subscriber), ["agents", "requests"])

    def test_tick_publishes_nothing_when_no_routine_is_due(self) -> None:
        self.assertEqual(self.tick(), [])
        self.assertEqual(drain(self.subscriber), [])

    def test_tick_publishes_agents_only_when_no_request_was_made(self) -> None:
        # A paused agent still records a skipped run, but no request appears, so
        # the inbox does not need a refresh beside the agents screen.
        self.store.update_agent(
            self.workspace["id"], self.agent["id"], fields={"status": "paused"}
        )
        self.make_due_routine()
        runs = self.tick()
        self.assertEqual(len(runs), 1)
        self.assertIsNone(runs[0]["request_id"])
        self.assertEqual(drain(self.subscriber), ["agents"])


if __name__ == "__main__":
    unittest.main()
