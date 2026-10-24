"""The agent guardrail track reads real month money, per agent.

``agent_month_totals`` is the read model behind the bar on an agent page: spent
is captured only, unsigned is awaiting plus approved, and the window is the same
month boundary every other screen uses.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from veto.store import Store, month_start_iso

AGENT = {
    "name": "Desk kit",
    "tag": "equipment",
    "channel": "#studio",
    "monthly_cap_cents": 120_000,
    "per_order_cap_cents": 60_000,
    "per_unit_cap_cents": 15_000,
    "approval_threshold_cents": 5_000,
}


class AgentMonthTotalsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.agent = self.store.create_agent(
            self.workspace["id"],
            AGENT,
            vendors=["walmart.com"],
            categories=["equipment"],
            rules=["monthly_cap", "per_order_cap", "per_unit_cap", "approval_threshold"],
            overrides=[],
        )
        self.other = self.store.create_agent(
            self.workspace["id"], {**AGENT, "name": "Pantry runner", "tag": "pantry"}
        )

    def make(self, agent: dict, status: str, cents: int, *, created_at: str | None = None) -> str:
        record = self.store.create_request(
            self.workspace["id"],
            agent["id"],
            origin="api",
            status=status,
            title="A cart",
            text="a cart",
            decision_outcome=None,
            total_cents=cents,
            lines=[],
            reasons=[],
            events=[],
        )
        if created_at:
            self.store.execute(
                "UPDATE requests SET created_at = ? WHERE id = ?", (created_at, record["id"])
            )
        return record["id"]

    def test_spent_is_captured_only_and_unsigned_is_awaiting_plus_approved(self) -> None:
        self.make(self.agent, "captured", 1_000)
        self.make(self.agent, "captured", 500)
        self.make(self.agent, "awaiting", 400)
        self.make(self.agent, "approved", 250)
        self.make(self.agent, "blocked", 900)  # refused money is not spend
        self.make(self.agent, "vetoed", 700)  # nor is a veto
        self.make(self.agent, "voided", 300)  # nor a withdrawal
        totals = self.store.agent_month_totals(
            self.workspace["id"], since=month_start_iso()
        )
        self.assertEqual(totals[self.agent["id"]]["spent_cents"], 1_500)
        self.assertEqual(totals[self.agent["id"]]["committed_cents"], 650)

    def test_agents_without_activity_are_absent(self) -> None:
        self.make(self.agent, "captured", 1_000)
        totals = self.store.agent_month_totals(
            self.workspace["id"], since=month_start_iso()
        )
        self.assertNotIn(self.other["id"], totals)

    def test_money_before_the_month_is_excluded(self) -> None:
        old = self.make(self.agent, "captured", 5_000, created_at="2020-01-01T00:00:00+00:00")
        self.make(self.agent, "captured", 100)
        totals = self.store.agent_month_totals(
            self.workspace["id"], since=month_start_iso()
        )
        self.assertEqual(totals[self.agent["id"]]["spent_cents"], 100)
        self.assertIn(old, [row["id"] for row in self.store.list_requests(self.workspace["id"])])

    def test_workspace_isolation(self) -> None:
        other_ws = self.store.create_workspace("Other Co")
        totals = self.store.agent_month_totals(other_ws["id"], since=month_start_iso())
        self.assertEqual(totals, {})


if __name__ == "__main__":
    unittest.main()
