"""Inbox ordering: a closed sort set, priority-first by default, agent-aware counts."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veto.store import Store  # noqa: E402


class InboxSortTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.pantry = self.store.create_agent(self.workspace["id"], {"name": "Pantry runner"})
        self.desk = self.store.create_agent(self.workspace["id"], {"name": "Desk kit"})
        self.ids = {}
        for key, status, total, agent in (
            ("old-captured", "captured", 900, self.pantry),
            ("awaiting-big", "awaiting", 40_000, self.desk),
            ("blocked", "blocked", 0, self.pantry),
            ("new-captured", "captured", 5_000, self.desk),
            ("awaiting-small", "awaiting", 1_500, self.pantry),
        ):
            record = self.store.create_request(
                self.workspace["id"], agent["id"],
                origin="web", status=status, title=key, total_cents=total,
            )
            self.ids[key] = record["id"]

    def ids_for(self, **kwargs) -> list[str]:
        rows = self.store.list_requests(self.workspace["id"], **kwargs)
        return [row["id"] for row in rows]

    def test_priority_keeps_waiting_first_then_newest(self) -> None:
        order = self.ids_for(sort="priority")
        self.assertEqual(set(order[:2]), {self.ids["awaiting-big"], self.ids["awaiting-small"]})
        self.assertEqual(order[0], self.ids["awaiting-small"])  # newest of the two
        self.assertEqual(order[-1], self.ids["old-captured"])

    def test_newest_and_oldest_are_stable(self) -> None:
        self.assertEqual(self.ids_for(sort="newest")[0], self.ids["awaiting-small"])
        self.assertEqual(self.ids_for(sort="oldest")[0], self.ids["old-captured"])

    def test_amount_orders_ignore_status(self) -> None:
        high = self.ids_for(sort="amount_desc")
        low = self.ids_for(sort="amount_asc")
        self.assertEqual(high[0], self.ids["awaiting-big"])
        self.assertEqual(high[1], self.ids["new-captured"])
        self.assertEqual(low[0], self.ids["blocked"])
        self.assertEqual(low[-1], self.ids["awaiting-big"])

    def test_agent_order_uses_the_joined_name(self) -> None:
        a_to_z = self.ids_for(sort="agent")
        z_to_a = self.ids_for(sort="agent_desc")
        self.assertEqual(a_to_z[0], self.ids["new-captured"])  # Desk kit first
        self.assertEqual(z_to_a[0], self.ids["awaiting-small"])  # Pantry runner first
        self.assertEqual(set(a_to_z), set(z_to_a))

    def test_an_unknown_order_keeps_the_legacy_default(self) -> None:
        # the HTTP layer rejects unknown sorts; the store itself never interpolates one
        self.assertEqual(self.ids_for(sort="; DROP TABLE requests"), self.ids_for())
        self.assertEqual(self.ids_for(sort="not-a-sort"), self.ids_for())

    def test_total_counts_the_filters(self) -> None:
        self.assertEqual(self.store.count_requests(self.workspace["id"]), 5)
        self.assertEqual(self.store.count_requests(self.workspace["id"], status="awaiting"), 2)
        self.assertEqual(
            self.store.count_requests(self.workspace["id"], agent_id=self.pantry["id"]), 3
        )
        self.assertEqual(
            self.store.count_requests(
                self.workspace["id"], status="awaiting", agent_id=self.pantry["id"]
            ),
            1,
        )

    def test_sorting_composes_with_status_and_agent(self) -> None:
        rows = self.store.list_requests(
            self.workspace["id"], status="awaiting", sort="amount_desc"
        )
        self.assertEqual([row["id"] for row in rows],
                         [self.ids["awaiting-big"], self.ids["awaiting-small"]])
        rows = self.store.list_requests(
            self.workspace["id"], agent_id=self.pantry["id"], sort="amount_desc"
        )
        self.assertEqual(rows[0]["id"], self.ids["awaiting-small"])
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
