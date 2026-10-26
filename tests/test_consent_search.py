"""Consent state and search: what "needs you" means, and what the list can find.

A lapsed consent is not a signature waiting to happen. The store separates it
from the actionable queue, keeps the counts honest, and lets the same filters
drive the list, the count and the money total.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veto import requests as veto_requests  # noqa: E402
from veto.store import Store, now_iso  # noqa: E402


class ConsentSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.pantry = self.store.create_agent(self.workspace["id"], {"name": "Pantry runner"})
        self.desk = self.store.create_agent(self.workspace["id"], {"name": "Desk kit"})

    def request(self, title: str, *, agent: dict | None = None, status: str = "awaiting",
                total: int = 1_000, requester: str = "Caroline") -> dict:
        return self.store.create_request(
            self.workspace["id"],
            (agent or self.pantry)["id"],
            origin="web",
            status=status,
            title=title,
            text=f"buy {title}",
            total_cents=total,
            requester=requester,
        )

    def consent(self, request_id: str, *, minutes: int, used: bool = False) -> None:
        moment = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        issued = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.store.execute(
            "INSERT INTO consents (id, request_id, cart_hash, nonce, issued_at, expires_at, "
            "used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"cns_{request_id}_{minutes}", request_id, "hash", f"nonce-{request_id}-{minutes}",
                issued.isoformat(timespec="seconds"), moment.isoformat(timespec="seconds"),
                now_iso() if used else None,
            ),
        )

    def test_actionable_and_expired_are_separate(self) -> None:
        live = self.request("Coffee beans")
        self.consent(live["id"], minutes=5)
        lapsed = self.request("Folding chairs")
        self.consent(lapsed["id"], minutes=-5)
        used = self.request("Pens", status="captured")
        self.consent(used["id"], minutes=-5, used=True)

        actionable = [r["id"] for r in self.store.list_requests(
            self.workspace["id"], status="awaiting", consent_state="actionable")]
        expired = [r["id"] for r in self.store.list_requests(
            self.workspace["id"], status="awaiting", consent_state="expired")]
        self.assertEqual(actionable, [live["id"]])
        self.assertEqual(expired, [lapsed["id"]])
        self.assertEqual(self.store.count_requests(
            self.workspace["id"], status="awaiting", consent_state="expired"), 1)
        self.assertEqual(self.store.sum_requests(
            self.workspace["id"], status="awaiting", consent_state="expired"), 1_000)

    def test_a_used_consent_is_not_expired(self) -> None:
        used = self.request("Pens", status="captured")
        self.consent(used["id"], minutes=-5, used=True)
        rows = self.store.list_requests(
            self.workspace["id"], status="awaiting", consent_state="expired")
        self.assertEqual(rows, [])

    def test_overview_needs_you_excludes_lapsed_consents(self) -> None:
        live = self.request("Coffee beans")
        self.consent(live["id"], minutes=5)
        lapsed = self.request("Folding chairs")
        self.consent(lapsed["id"], minutes=-5)
        counts = self.store.overview_counts(self.workspace["id"])
        self.assertEqual(counts["needs_you"], 1)
        self.assertEqual(counts["expired_consents"], 1)

    def test_summary_marks_the_expired_consent(self) -> None:
        lapsed = self.request("Folding chairs")
        self.consent(lapsed["id"], minutes=-5)
        summary = veto_requests.public_summary(
            self.store.list_requests(self.workspace["id"], status="awaiting")[0])
        self.assertTrue(summary["consent_expired"])
        self.assertEqual(summary["consent_expires_at"], self.store.one(
            "SELECT expires_at FROM consents WHERE request_id = ?",
            (lapsed["id"],))["expires_at"])

    def test_search_matches_title_agent_and_requester(self) -> None:
        self.request("Coffee beans", requester="Caroline Hayes")
        self.request("Standing mat", agent=self.desk, requester="Dan")
        for needle, expected in (
            ("coffee", "Coffee beans"),
            ("desk", "Standing mat"),
            ("caroline", "Coffee beans"),
        ):
            rows = self.store.list_requests(self.workspace["id"], q=needle)
            self.assertEqual([row["title"] for row in rows], [expected])
        self.assertEqual(self.store.count_requests(self.workspace["id"], q="nothing"), 0)
        self.assertEqual(self.store.sum_requests(self.workspace["id"], q="coffee"), 1_000)

    def test_search_and_consent_compose_with_status(self) -> None:
        live = self.request("Coffee beans")
        self.consent(live["id"], minutes=5)
        lapsed = self.request("Coffee pods")
        self.consent(lapsed["id"], minutes=-5)
        rows = self.store.list_requests(
            self.workspace["id"], status="awaiting", q="coffee", consent_state="expired")
        self.assertEqual([row["id"] for row in rows], [lapsed["id"]])


if __name__ == "__main__":
    unittest.main()
