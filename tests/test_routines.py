"""Routine tests: a standing job, its schedule, its envelope and its runs.

The pipeline is injected (extract/search/choose) and the money mover is the shared
fake, so nothing here calls a model, a catalogue or PayPal. Time is fixed except
for the run's own finish stamp.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veto import routines  # noqa: E402
from veto.policy import RULE_KINDS  # noqa: E402
from veto.settlement import SettlementError, VaultRequired  # noqa: E402
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


class ScheduleTests(unittest.TestCase):
    def test_next_slot_for_each_cadence(self) -> None:
        start = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(routines.next_run_at("daily", start=start),
                         "2026-02-01T09:00:00+00:00")
        self.assertEqual(routines.next_run_at("weekly", start=start),
                         "2026-02-07T09:00:00+00:00")
        self.assertEqual(routines.next_run_at("monthly", start=start),
                         "2026-02-28T09:00:00+00:00")

    def test_monthly_rolls_over_the_year(self) -> None:
        start = datetime(2026, 12, 15, 8, 30, tzinfo=timezone.utc)
        self.assertEqual(routines.next_run_at("monthly", start=start),
                         "2027-01-15T08:30:00+00:00")

    def test_unknown_cadence_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            routines.next_run_at("hourly", start=FIXED)

    def test_cadence_words_are_plain(self) -> None:
        self.assertEqual(routines.cadence_word("weekly"), "every week")
        self.assertEqual(routines.cadence_word("monthly"), "every month")
        self.assertEqual(routines.cadence_word("later"), "on a schedule")


class RoutineTestCase(unittest.TestCase):
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

    def make_routine(self, **overrides) -> dict:
        fields = {
            "name": "Pantry restock",
            "instruction": "2 packs of toilet paper",
            "cadence": "weekly",
            "envelope_cents": 25_000,
            "dedupe_days": 10,
            "next_run_at": stamp(FIXED + timedelta(days=7)),
        }
        fields.update(overrides)
        return self.store.create_routine(self.workspace["id"], self.agent["id"], **fields)

    def run_once(self, routine: dict, *, now: datetime = FIXED, **overrides) -> dict:
        return routines.run_once(
            self.store,
            self.workspace,
            routine,
            settlement=self.settlement,
            now=now,
            extract_fn=overrides.pop("extract_fn", extract_one()),
            search_fn=overrides.pop("search_fn", search_one()),
            choose_fn=overrides.pop("choose_fn", choose_first),
            **overrides,
        )

    def requests(self) -> list[dict]:
        return self.store.list_requests(self.workspace["id"])


class RunTests(RoutineTestCase):
    def test_a_run_creates_a_real_captured_request(self) -> None:
        routine = self.make_routine()
        run = self.run_once(routine)
        self.assertEqual(run["outcome"], "captured")
        self.assertEqual(run["amount_cents"], 2_000)
        self.assertTrue(run["request_id"].startswith("REQ-"))
        record = self.store.request(self.workspace["id"], run["request_id"])
        self.assertEqual(record["status"], "captured")
        self.assertEqual(record["routine"]["name"], "Pantry restock")
        self.assertEqual(self.store.routine_for_agent(
            self.workspace["id"], self.agent["id"])["last_run_at"], stamp(FIXED))
        self.assertEqual(self.requests()[0]["routine_name"], "Pantry restock")

    def test_a_run_inside_the_dedupe_window_is_a_recorded_skip(self) -> None:
        routine = self.make_routine()
        first = self.run_once(routine)
        second = self.run_once(routine)
        self.assertEqual(second["outcome"], "skipped")
        self.assertIn(first["request_id"], second["note"])
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(self.settlement.calls and len(self.settlement.calls), 1)

    def test_a_paused_agent_skips_the_job_instead_of_ordering(self) -> None:
        routine = self.make_routine()
        self.store.update_agent(
            self.workspace["id"], self.agent["id"], fields={"status": "paused"}
        )
        run = self.run_once(routine)
        self.assertEqual(run["outcome"], "skipped")
        self.assertIn("paused", run["note"])
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.settlement.calls, [])

    def test_an_used_envelope_skips_the_run(self) -> None:
        routine = self.make_routine(envelope_cents=2_000, dedupe_days=0)
        self.assertEqual(self.run_once(routine)["outcome"], "captured")
        skipped = self.run_once(routine)
        self.assertEqual(skipped["outcome"], "skipped")
        self.assertIn("envelope", skipped["note"])
        # a skip moved no money, so it cannot be shown with an amount
        self.assertEqual(skipped["amount_cents"], 0)
        self.assertEqual(len(self.requests()), 1)

    def test_a_cart_past_the_envelope_escalates_instead_of_capturing(self) -> None:
        routine = self.make_routine(envelope_cents=1_000, dedupe_days=0)
        run = self.run_once(routine)
        self.assertEqual(run["outcome"], "awaiting")
        self.assertEqual(self.settlement.calls, [])
        record = self.store.request(self.workspace["id"], run["request_id"])
        self.assertEqual(record["status"], "awaiting")
        self.assertIn("routine_envelope",
                      [reason["rule"] for reason in record["reasons"]])
        self.assertIsNone(record["consents"][0]["used_at"])

    def test_a_pipeline_refusal_is_recorded_as_failed(self) -> None:
        routine = self.make_routine()
        run = self.run_once(routine, extract_fn=lambda text: {"items": []})
        self.assertEqual(run["outcome"], "failed")
        self.assertIsNone(run["request_id"])
        self.assertEqual(self.requests(), [])

    def test_public_routine_reports_the_month_and_the_cadence(self) -> None:
        routine = self.make_routine()
        self.run_once(routine)
        fresh = self.store.routine(self.workspace["id"], routine["id"])
        view = routines.public_routine(self.store, self.workspace, fresh)
        self.assertEqual(view["spent_cents"], 2_000)
        self.assertEqual(view["envelope_cents"], 25_000)
        self.assertEqual(view["remaining_cents"], 23_000)
        self.assertEqual(view["cadence_word"], "every week")
        # the dedupe window is part of the public job: a skipped run has to be
        # explainable in the app, not only in the scheduler
        self.assertEqual(view["dedupe_days"], 10)
        self.assertEqual(view["last_run_at"], stamp(FIXED))


class SchedulerTests(RoutineTestCase):
    def due_routine(self, **overrides) -> dict:
        return self.make_routine(
            next_run_at=stamp(FIXED - timedelta(days=1)), dedupe_days=0, **overrides
        )

    def tick(self, now: datetime = FIXED) -> list[dict]:
        return routines.tick(
            self.store,
            self.settlement,
            now=now,
            extract_fn=extract_one(),
            search_fn=search_one(),
            choose_fn=choose_first,
        )

    def test_a_due_routine_runs_once_and_moves_forward(self) -> None:
        routine = self.due_routine()
        self.assertEqual(len(self.tick()), 1)
        self.assertEqual(self.requests()[0]["status"], "captured")
        moved = self.store.routine_for_agent(self.workspace["id"], self.agent["id"])
        self.assertEqual(moved["next_run_at"], stamp(FIXED + timedelta(days=7)))
        # a second tick at the same moment must not run it again
        self.assertEqual(self.tick(), [])
        self.assertEqual(len(self.requests()), 1)

    def test_a_missed_slot_is_caught_up_once_then_resumes_from_now(self) -> None:
        self.make_routine(
            cadence="daily", dedupe_days=0,
            next_run_at=stamp(FIXED - timedelta(days=3)),
        )
        runs = self.tick()
        self.assertEqual(len(runs), 1)
        self.assertEqual(self.requests()[0]["status"], "captured")
        moved = self.store.routine_for_agent(self.workspace["id"], self.agent["id"])
        self.assertEqual(moved["next_run_at"], stamp(FIXED + timedelta(days=1)))

    def test_a_paused_routine_is_not_due(self) -> None:
        routine = self.due_routine()
        self.store.update_routine(self.workspace["id"], routine["id"],
                                  fields={"status": "paused"})
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.requests(), [])

    def test_the_claim_is_atomic(self) -> None:
        routine = self.due_routine()
        claimed = self.store.claim_routine(
            routine["id"],
            expected_next=routine["next_run_at"],
            next_run_at=stamp(FIXED + timedelta(days=7)),
        )
        self.assertTrue(claimed)
        again = self.store.claim_routine(
            routine["id"],
            expected_next=routine["next_run_at"],
            next_run_at=stamp(FIXED + timedelta(days=14)),
        )
        self.assertFalse(again)


class ProgressFrameTests(RoutineTestCase):
    """The run stream: pipeline frames, then the record, consent and money steps."""

    def make_agent_for(self, **overrides) -> dict:
        extra = {
            "vendors": overrides.pop("vendors", ["amazon.com"]),
            "categories": overrides.pop("categories", ["pantry"]),
            "rules": overrides.pop("rules", list(RULE_KINDS)),
            "overrides": overrides.pop("overrides", []),
        }
        return self.store.create_agent(
            self.workspace["id"], agent_fields(**overrides), **extra
        )

    def make_routine_for(self, agent: dict, **overrides) -> dict:
        fields = {
            "name": "Pantry restock",
            "instruction": "2 packs of toilet paper",
            "cadence": "weekly",
            "envelope_cents": 25_000,
            "dedupe_days": 0,
            "next_run_at": stamp(FIXED + timedelta(days=7)),
        }
        fields.update(overrides)
        return self.store.create_routine(self.workspace["id"], agent["id"], **fields)

    def run_stream(self, routine: dict, *, settlement=None, search=None) -> tuple[dict, list]:
        frames: list[dict] = []
        run = routines.run_once(
            self.store,
            self.workspace,
            routine,
            settlement=settlement or self.settlement,
            progress=frames.append,
            now=FIXED,
            extract_fn=extract_one(),
            search_fn=search or search_one(),
            choose_fn=choose_first,
        )
        return run, frames

    @staticmethod
    def events(frames: list[dict]) -> list[dict]:
        return [frame["event"] for frame in frames if frame.get("type") == "event"]

    @staticmethod
    def kinds(frames: list[dict]) -> list[str]:
        return [event["kind"] for event in ProgressFrameTests.events(frames)]

    def test_auto_run_streams_pipeline_record_consent_and_settlement(self) -> None:
        run, frames = self.run_stream(self.make_routine_for(self.agent))
        self.assertEqual(run["outcome"], "captured")
        kinds = self.kinds(frames)
        for kind in ("extract", "catalogue", "price", "decision",
                     "record", "consent", "order", "auth", "capture"):
            self.assertIn(kind, kinds)
        # the record and the consent frame both land before money moves
        self.assertLess(kinds.index("record"), kinds.index("consent"))
        self.assertLess(kinds.index("consent"), kinds.index("order"))
        self.assertLess(kinds.index("order"), kinds.index("capture"))
        # each settlement step is streamed exactly once
        self.assertEqual(kinds.count("order"), 1)
        self.assertEqual(kinds.count("auth"), 1)
        self.assertEqual(kinds.count("capture"), 1)
        record = self.events(frames)[kinds.index("record")]
        self.assertEqual(record["label"], "Request recorded")
        self.assertTrue(record["detail"].startswith(run["request_id"] + " · "))
        consent = self.events(frames)[kinds.index("consent")]
        self.assertEqual(consent["label"], "Consent issued")
        self.assertTrue(consent["detail"].startswith("single use · 10 min · sha256:"))

    def test_review_run_streams_a_hold_frame(self) -> None:
        review_agent = self.make_agent_for(approval_threshold_cents=100)
        routine = self.make_routine_for(review_agent)
        run, frames = self.run_stream(routine)
        self.assertEqual(run["outcome"], "awaiting")
        kinds = self.kinds(frames)
        self.assertIn("record", kinds)
        self.assertIn("consent", kinds)
        self.assertIn("hold", kinds)
        self.assertNotIn("order", kinds)
        self.assertNotIn("capture", kinds)
        hold = self.events(frames)[kinds.index("hold")]
        self.assertEqual(hold["label"], "Waiting for a signature")
        self.assertIn(run["request_id"], hold["detail"])

    def test_blocked_run_streams_a_block_frame_without_consent(self) -> None:
        equipment_agent = self.make_agent_for(categories=["equipment"])
        routine = self.make_routine_for(equipment_agent)
        run, frames = self.run_stream(routine)
        self.assertEqual(run["outcome"], "blocked")
        kinds = self.kinds(frames)
        self.assertIn("record", kinds)
        self.assertIn("block", kinds)
        self.assertNotIn("consent", kinds)
        self.assertNotIn("order", kinds)
        block = self.events(frames)[kinds.index("block")]
        self.assertEqual(block["label"], "Rules blocked the cart")

    def test_wallet_required_streams_a_wallet_frame(self) -> None:
        settlement = FakeSettlement(error=VaultRequired("https://approve", "setup_1"))
        run, frames = self.run_stream(self.make_routine_for(self.agent),
                                      settlement=settlement)
        self.assertEqual(run["outcome"], "approved")
        kinds = self.kinds(frames)
        self.assertIn("wallet", kinds)
        self.assertNotIn("capture", kinds)
        wallet = self.events(frames)[kinds.index("wallet")]
        self.assertEqual(wallet["label"], "Wallet not connected")

    def test_capture_failure_streams_carried_steps_and_capture_failed(self) -> None:
        error = SettlementError(
            "paypal said no",
            steps=[{"kind": "order", "label": "Order created", "detail": "ORDER-X"}],
        )
        settlement = FakeSettlement(error=error)
        run, frames = self.run_stream(self.make_routine_for(self.agent),
                                      settlement=settlement)
        self.assertEqual(run["outcome"], "approved")
        kinds = self.kinds(frames)
        self.assertIn("order", kinds)
        self.assertIn("capture_failed", kinds)
        failed = self.events(frames)[kinds.index("capture_failed")]
        self.assertEqual(failed["label"], "Capture failed")
        self.assertIn("paypal said no", failed["detail"])


if __name__ == "__main__":
    unittest.main()
