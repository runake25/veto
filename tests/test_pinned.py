"""Pinned-cart tests: a human picked one offer, and it still has to pass policy.

The pinned path skips extract/search/choose only. Pricing, the rules, consent,
PayPal settlement and persistence are the ordinary create() path, so these
tests reuse the shared fixtures.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veto import requests as veto_requests  # noqa: E402
from veto.policy import RULE_KINDS  # noqa: E402
from veto.store import Store  # noqa: E402
from test_requests import FakeSettlement, agent_fields  # noqa: E402


class PinnedCartTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.agent = self.store.create_agent(
            self.workspace["id"],
            agent_fields(),
            vendors=["amazon.com", "staples.com"],
            categories=["pantry"],
            rules=list(RULE_KINDS),
        )
        self.settlement = FakeSettlement()

    def line(self, **overrides) -> dict:
        line = {
            "sku": "sku-1",
            "title": "Bamboo toilet paper",
            "qty": 2,
            "unit_cents": 1_000,
            "vendor": "amazon.com",
            "category": "pantry",
            "brand": "Bamboo",
            "image": "",
            "url": "https://amazon.example/tp",
            "source": "channel3",
        }
        line.update(overrides)
        return line

    def create(self, lines: list[dict] | None = None) -> dict:
        return veto_requests.create(
            self.store,
            workspace=self.workspace,
            agent=self.agent,
            text="2 boxes of pens",
            origin="slack",
            settlement=self.settlement,
            pinned_lines=[self.line()] if lines is None else lines,
        )

    def test_a_pick_is_priced_judged_captured_and_recorded(self) -> None:
        record = self.create()
        self.assertEqual(record["status"], "captured")
        self.assertEqual(record["decision_outcome"], "auto")
        self.assertEqual(record["total_cents"], 2_000)
        self.assertEqual(record["title"], "Bamboo toilet paper")
        self.assertEqual(record["lines"][0]["title"], "Bamboo toilet paper")
        self.assertEqual(record["lines"][0]["qty"], 2)
        self.assertEqual(record["lines"][0]["unit_cents"], 1_000)
        kinds = [event["kind"] for event in record["events"]]
        for kind in ("pick", "price", "decision", "consent", "order", "auth", "capture"):
            self.assertIn(kind, kinds)
        self.assertEqual(len(self.settlement.calls), 1)
        self.assertEqual(self.settlement.calls[0]["total_cents"], 2_000)

    def test_the_consent_is_the_picked_cart(self) -> None:
        one = self.create(lines=[self.line()])
        two = self.create(lines=[self.line(sku="sku-1", title="Bamboo toilet paper")])
        self.assertEqual(one["consents"][0]["cart_hash"], two["consents"][0]["cart_hash"])
        different = self.create(lines=[self.line(unit_cents=1_100)])
        self.assertNotEqual(one["consents"][0]["cart_hash"],
                            different["consents"][0]["cart_hash"])

    def test_a_pick_over_the_signature_threshold_waits(self) -> None:
        record = self.create(lines=[self.line(unit_cents=6_000, qty=1)])
        self.assertEqual(record["status"], "awaiting")
        self.assertEqual(record["reasons"][0]["rule"], "approval_threshold")
        self.assertEqual(self.settlement.calls, [])

    def test_a_pick_outside_the_category_is_blocked(self) -> None:
        record = self.create(lines=[self.line(category="general")])
        self.assertEqual(record["status"], "blocked")
        self.assertEqual(record["reasons"][0]["rule"], "category_allowlist")
        self.assertEqual(self.settlement.calls, [])
        self.assertEqual(record["consents"], [])

    def test_a_pick_with_no_price_is_refused(self) -> None:
        with self.assertRaises(veto_requests.RequestError) as caught:
            self.create(lines=[self.line(unit_cents=0)])
        self.assertEqual(caught.exception.code, "invalid_cart")
        with self.assertRaises(veto_requests.RequestError):
            self.create(lines=[])


if __name__ == "__main__":
    unittest.main()
