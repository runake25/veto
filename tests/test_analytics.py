"""Analytics read-model tests: totals, rollups, series, isolation, bounds.

Rows are written through ``Store.create_request`` and then stamped with explicit
timestamps so every assertion about ranges and day attribution is deterministic.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from veto.store import MAX_ANALYTICS_DAYS, Store

START = "2026-10-01T00:00:00+00:00"
END = "2026-10-05T00:00:00+00:00"

TOTAL_KEYS = {
    "requests", "captured", "awaiting", "approved", "blocked", "vetoed", "voided",
    "captured_cents", "pending_cents", "blocked_cents", "auto", "review", "block",
}
AGENT_KEYS = {
    "key", "label", "tag", "requests", "captured", "captured_cents", "pending",
    "pending_cents", "blocked", "vetoed",
}
TAG_KEYS = AGENT_KEYS - {"tag"}
VENDOR_KEYS = {"key", "label", "requests", "captured_cents", "lines", "blocked", "vetoed"}
RULE_KEYS = {"key", "severity", "count", "requests", "blocked", "review", "example"}


def make_line(vendor="amazon.com", category="pantry", line_cents=1_000,
              title="Bamboo toilet paper, 24 rolls") -> dict:
    return {
        "sku": "sku-1",
        "title": title,
        "qty": 1,
        "unit_cents": line_cents,
        "line_cents": line_cents,
        "vendor": vendor,
        "category": category,
        "brand": "Bamboo",
        "image": "https://img.example/tp.jpg",
        "url": "https://amazon.com/dp/tp",
        "source": "local",
    }


def make_reason(rule="vendor_allowlist", severity="block",
                message="vendor not approved") -> dict:
    return {
        "rule": rule,
        "severity": severity,
        "message": message,
        "observed_cents": 1_000,
        "limit_cents": 500,
        "unblock": "add the vendor",
    }


class AnalyticsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace(
            "Bracken & Field", monthly_budget_cents=285_000
        )
        self.agent = self.make_agent()

    def make_agent(self, name="Pantry runner", tag="pantry", workspace=None) -> dict:
        return self.store.create_agent(
            (workspace or self.workspace)["id"],
            {"name": name, "tag": tag},
            vendors=["amazon.com"],
            categories=["pantry"],
            rules=[],
            overrides=[],
        )

    def add_request(self, *, agent=None, workspace=None, created_at="2026-10-02T10:00:00+00:00",
                    status="captured", total_cents=1_000, decided_at=None,
                    decision_outcome=None, lines=None, reasons=None,
                    title="Toilet paper") -> dict:
        request = self.store.create_request(
            (workspace or self.workspace)["id"],
            (agent or self.agent)["id"],
            origin="api",
            status=status,
            title=title,
            total_cents=total_cents,
            subtotal_cents=total_cents,
            decision_outcome=decision_outcome,
            lines=list(lines or []),
            reasons=list(reasons or []),
        )
        self.store.execute(
            "UPDATE requests SET created_at = ?, decided_at = ? WHERE id = ?",
            (created_at, decided_at, request["id"]),
        )
        return request

    def analytics(self, *, workspace=None, start=START, end=END) -> dict:
        return self.store.analytics((workspace or self.workspace)["id"], start, end)


class EmptyAndSeriesTests(AnalyticsTestCase):
    def test_empty_workspace_returns_zeroes_and_full_day_series(self) -> None:
        result = self.analytics()
        self.assertEqual(set(result), {
            "range", "totals", "by_agent", "by_tag", "by_vendor",
            "by_category", "by_rule", "series",
        })
        self.assertEqual(result["range"], {"from": START, "to": END})
        self.assertEqual(result["totals"], {key: 0 for key in TOTAL_KEYS})
        for key in ("by_agent", "by_tag", "by_vendor", "by_category", "by_rule"):
            self.assertEqual(result[key], [], key)
        self.assertEqual(
            result["series"],
            [
                {"date": "2026-10-01", "captured_cents": 0, "requests": 0},
                {"date": "2026-10-02", "captured_cents": 0, "requests": 0},
                {"date": "2026-10-03", "captured_cents": 0, "requests": 0},
                {"date": "2026-10-04", "captured_cents": 0, "requests": 0},
            ],
        )

    def test_series_is_zero_filled_and_ascending(self) -> None:
        self.add_request(created_at="2026-10-01T09:00:00+00:00", total_cents=1_200,
                         lines=[make_line(line_cents=1_200)])
        self.add_request(created_at="2026-10-03T09:00:00+00:00", total_cents=500,
                         lines=[make_line(line_cents=500)])
        series = self.analytics()["series"]
        self.assertEqual([point["date"] for point in series],
                         ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"])
        self.assertEqual(series[0], {"date": "2026-10-01", "captured_cents": 1_200,
                                     "requests": 1})
        self.assertEqual(series[1], {"date": "2026-10-02", "captured_cents": 0,
                                     "requests": 0})
        self.assertEqual(series[2], {"date": "2026-10-03", "captured_cents": 500,
                                     "requests": 1})
        self.assertEqual(series[3], {"date": "2026-10-04", "captured_cents": 0,
                                     "requests": 0})

    def test_series_end_exclusive_at_utc_midnight(self) -> None:
        # Four days 01T00:00 -> 05T00:00; the 05th is outside the window.
        self.add_request(created_at="2026-10-04T23:59:59+00:00", total_cents=333)
        result = self.analytics()
        self.assertEqual(result["series"][-1]["date"], "2026-10-04")
        self.assertEqual(len(result["series"]), 4)
        self.assertEqual(result["series"][-1]["captured_cents"], 333)

    def test_day_attribution_uses_decided_at_when_set(self) -> None:
        # Created late on the 1st, captured just after midnight -> 2nd.
        self.add_request(created_at="2026-10-01T23:50:00+00:00",
                         decided_at="2026-10-02T00:10:00+00:00", total_cents=1_000)
        # Captured with no decided_at falls back to created_at -> 3rd.
        self.add_request(created_at="2026-10-03T12:00:00+00:00",
                         decided_at=None, total_cents=700)
        series = {point["date"]: point for point in self.analytics()["series"]}
        self.assertEqual(series["2026-10-01"]["captured_cents"], 0)
        self.assertEqual(series["2026-10-02"], {"date": "2026-10-02",
                                                "captured_cents": 1_000,
                                                "requests": 1})
        self.assertEqual(series["2026-10-03"], {"date": "2026-10-03",
                                                "captured_cents": 700,
                                                "requests": 1})

    def test_non_captured_requests_do_not_appear_in_series(self) -> None:
        self.add_request(created_at="2026-10-02T10:00:00+00:00", status="awaiting",
                         decided_at="2026-10-03T10:00:00+00:00", total_cents=5_000)
        series = self.analytics()["series"]
        self.assertTrue(all(point["captured_cents"] == 0 and point["requests"] == 0
                            for point in series))


class TotalsTests(AnalyticsTestCase):
    def test_status_counts_and_cents(self) -> None:
        self.add_request(status="captured", total_cents=1_000, decision_outcome="auto")
        self.add_request(status="captured", total_cents=5_000, decision_outcome="review")
        self.add_request(status="awaiting", total_cents=700, decision_outcome="review")
        self.add_request(status="approved", total_cents=300, decision_outcome="review")
        self.add_request(status="blocked", total_cents=900, decision_outcome="block")
        self.add_request(status="vetoed", total_cents=400, decision_outcome="block")
        self.add_request(status="voided", total_cents=100, decision_outcome="review")

        totals = self.analytics()["totals"]
        self.assertEqual(totals, {
            "requests": 7,
            "captured": 2, "awaiting": 1, "approved": 1,
            "blocked": 1, "vetoed": 1, "voided": 1,
            "captured_cents": 6_000,
            "pending_cents": 1_000,
            "blocked_cents": 1_300,
            "auto": 1, "review": 4, "block": 2,
        })
        self.assertEqual(set(totals), TOTAL_KEYS)

    def test_bad_decision_outcomes_are_not_counted(self) -> None:
        self.add_request(status="captured", decision_outcome=None)
        self.add_request(status="captured", decision_outcome="something-else")
        totals = self.analytics()["totals"]
        self.assertEqual(totals["auto"] + totals["review"] + totals["block"], 0)
        self.assertEqual(totals["captured"], 2)


class AgentAndTagRollupTests(AnalyticsTestCase):
    def test_by_agent_aggregates_and_sorts(self) -> None:
        pantry = self.make_agent("Pantry runner", "pantry")
        desk = self.make_agent("Desk kit", "equipment")
        studio = self.make_agent("Studio ops", "facilities")
        # pantry: 5000 captured + 1000 blocked
        self.add_request(agent=pantry, status="captured", total_cents=5_000)
        self.add_request(agent=pantry, status="blocked", total_cents=1_000)
        # desk: 2000 captured + 500 awaiting
        self.add_request(agent=desk, status="captured", total_cents=2_000)
        self.add_request(agent=desk, status="approved", total_cents=500)
        # studio: 300 vetoed
        self.add_request(agent=studio, status="vetoed", total_cents=300)

        by_agent = self.analytics()["by_agent"]
        self.assertEqual([row["label"] for row in by_agent],
                         ["Pantry runner", "Desk kit", "Studio ops"])
        self.assertEqual(by_agent[0], {
            "key": pantry["id"], "label": "Pantry runner", "tag": "pantry",
            "requests": 2, "captured": 1, "captured_cents": 5_000,
            "pending": 0, "pending_cents": 0, "blocked": 1, "vetoed": 0,
        })
        self.assertEqual(by_agent[1]["captured_cents"], 2_000)
        self.assertEqual(by_agent[1]["pending"], 1)
        self.assertEqual(by_agent[1]["pending_cents"], 500)
        self.assertEqual(by_agent[2]["vetoed"], 1)
        for row in by_agent:
            self.assertEqual(set(row), AGENT_KEYS)

    def test_by_agent_tie_break_by_requests_then_label(self) -> None:
        alpha = self.make_agent("Alpha", "pantry")
        beta = self.make_agent("Beta", "pantry")
        self.add_request(agent=alpha, status="blocked", total_cents=1_000)
        self.add_request(agent=beta, status="vetoed", total_cents=1_000)
        labels = [row["label"] for row in self.analytics()["by_agent"]]
        self.assertEqual(labels, ["Alpha", "Beta"])

    def test_by_agent_requires_requests_in_range(self) -> None:
        self.make_agent("Idle", "pantry")
        self.add_request(created_at="2026-09-30T10:00:00+00:00", total_cents=1_000)
        self.assertEqual(self.analytics()["by_agent"], [])

    def test_by_tag_rollup_across_agents(self) -> None:
        first = self.make_agent("Pantry runner", "pantry")
        second = self.make_agent("Coffee ops", "pantry")
        desk = self.make_agent("Desk kit", "equipment")
        self.add_request(agent=first, status="captured", total_cents=3_000)
        self.add_request(agent=second, status="captured", total_cents=1_000)
        self.add_request(agent=desk, status="awaiting", total_cents=500)

        by_tag = self.analytics()["by_tag"]
        self.assertEqual([row["key"] for row in by_tag], ["pantry", "equipment"])
        pantry = by_tag[0]
        self.assertEqual(pantry["label"], "pantry")
        self.assertEqual(pantry["requests"], 2)
        self.assertEqual(pantry["captured"], 2)
        self.assertEqual(pantry["captured_cents"], 4_000)
        for row in by_tag:
            self.assertEqual(set(row), TAG_KEYS)

    def test_untagged_agents_roll_up_as_untagged(self) -> None:
        untagged = self.make_agent("Facilities", "general")
        # create_agent defaults an empty tag to "general"; blank it directly to
        # cover legacy/imported agents that have no tag at all.
        self.store.execute(
            "UPDATE agents SET tag = '' WHERE id = ?", (untagged["id"],)
        )
        self.add_request(agent=untagged, status="captured", total_cents=1_000)
        by_tag = self.analytics()["by_tag"]
        self.assertEqual(len(by_tag), 1)
        self.assertEqual(by_tag[0]["key"], "")
        self.assertEqual(by_tag[0]["label"], "Untagged")
        self.assertEqual(by_tag[0]["captured_cents"], 1_000)

    def test_requests_of_deleted_agents_still_report(self) -> None:
        agent = self.make_agent("Gone soon", "pantry")
        self.add_request(agent=agent, status="captured", total_cents=1_000)
        self.assertTrue(self.store.delete_agent(self.workspace["id"], agent["id"]))
        by_agent = self.analytics()["by_agent"]
        self.assertEqual(len(by_agent), 1)
        self.assertEqual(by_agent[0]["key"], agent["id"])
        self.assertEqual(by_agent[0]["label"], agent["id"])
        self.assertEqual(by_agent[0]["tag"], "")
        self.assertEqual(by_agent[0]["captured_cents"], 1_000)


class LineRollupTests(AnalyticsTestCase):
    def test_by_vendor_aggregates(self) -> None:
        self.add_request(
            status="captured", total_cents=3_000,
            lines=[make_line("amazon.com", "pantry", 1_000),
                   make_line("amazon.com", "pantry", 2_000)],
        )
        self.add_request(status="captured", total_cents=1_500,
                         lines=[make_line("staples.com", "pantry", 1_500)])
        self.add_request(status="blocked", total_cents=1_000,
                         lines=[make_line("amazon.com", "pantry", 700),
                                make_line("staples.com", "pantry", 300)])
        self.add_request(status="vetoed", total_cents=400,
                         lines=[make_line("target.com", "pantry", 400)])

        by_vendor = self.analytics()["by_vendor"]
        self.assertEqual([row["key"] for row in by_vendor],
                         ["amazon.com", "staples.com", "target.com"])
        self.assertEqual(by_vendor[0], {
            "key": "amazon.com", "label": "amazon.com", "requests": 2,
            "captured_cents": 3_000, "lines": 3, "blocked": 1, "vetoed": 0,
        })
        self.assertEqual(by_vendor[1], {
            "key": "staples.com", "label": "staples.com", "requests": 2,
            "captured_cents": 1_500, "lines": 2, "blocked": 1, "vetoed": 0,
        })
        self.assertEqual(by_vendor[2]["vetoed"], 1)
        self.assertEqual(by_vendor[2]["captured_cents"], 0)
        for row in by_vendor:
            self.assertEqual(set(row), VENDOR_KEYS)

    def test_by_category_aggregates(self) -> None:
        self.add_request(status="captured", total_cents=2_000,
                         lines=[make_line("amazon.com", "pantry", 2_000)])
        self.add_request(status="captured", total_cents=900,
                         lines=[make_line("staples.com", "equipment", 600),
                                make_line("amazon.com", "pantry", 300)])
        self.add_request(status="blocked", total_cents=1_500,
                         lines=[make_line("target.com", "equipment", 1_500)])
        self.add_request(status="vetoed", total_cents=250,
                         lines=[make_line("amazon.com", "meals", 250)])

        by_category = {row["key"]: row for row in self.analytics()["by_category"]}
        self.assertEqual(set(by_category), {"pantry", "equipment", "meals"})
        self.assertEqual(by_category["equipment"], {
            "key": "equipment", "label": "equipment", "requests": 2,
            "captured_cents": 600, "lines": 2, "blocked": 1, "vetoed": 0,
        })
        self.assertEqual(by_category["pantry"]["captured_cents"], 2_300)
        self.assertEqual(by_category["pantry"]["requests"], 2)
        self.assertEqual(by_category["meals"]["vetoed"], 1)
        for row in by_category.values():
            self.assertEqual(set(row), VENDOR_KEYS)

    def test_lines_without_vendor_roll_up_as_unknown(self) -> None:
        self.add_request(status="captured", total_cents=750, lines=[{
            "title": "Mystery item", "qty": 1,
            "unit_cents": 750, "line_cents": 750,
            "vendor": None, "category": None,
        }])
        result = self.analytics()
        self.assertEqual(result["by_vendor"][0]["key"], "")
        self.assertEqual(result["by_vendor"][0]["label"], "Unknown")
        self.assertEqual(result["by_category"][0]["key"], "")
        self.assertEqual(result["by_category"][0]["label"], "Unknown")


class RuleRollupTests(AnalyticsTestCase):
    def test_by_rule_aggregates_by_rule_and_severity(self) -> None:
        self.add_request(
            status="blocked", total_cents=1_000,
            reasons=[
                make_reason("vendor_allowlist", "block", "beta vendor not approved"),
                make_reason("vendor_allowlist", "block", "alpha vendor not approved"),
                make_reason("per_unit_cap", "review", "unit over cap"),
            ],
        )
        self.add_request(
            status="vetoed", total_cents=500,
            reasons=[make_reason("vendor_allowlist", "block", "gamma vendor not approved")],
        )
        self.add_request(
            status="awaiting", total_cents=900,
            reasons=[make_reason("per_unit_cap", "review", "second unit over cap")],
        )
        self.add_request(status="captured", total_cents=100)  # no reasons

        by_rule = self.analytics()["by_rule"]
        self.assertEqual([(row["key"], row["severity"]) for row in by_rule],
                         [("vendor_allowlist", "block"), ("per_unit_cap", "review")])
        vendor = by_rule[0]
        self.assertEqual(vendor, {
            "key": "vendor_allowlist", "severity": "block", "count": 3,
            "requests": 2, "blocked": 1, "review": 1,
            "example": "alpha vendor not approved",
        })
        per_unit = by_rule[1]
        self.assertEqual(per_unit["count"], 2)
        self.assertEqual(per_unit["requests"], 2)
        self.assertEqual(per_unit["blocked"], 1)
        self.assertEqual(per_unit["review"], 1)
        for row in by_rule:
            self.assertEqual(set(row), RULE_KEYS)


class RangeAndIsolationTests(AnalyticsTestCase):
    def test_range_is_start_inclusive_end_exclusive(self) -> None:
        self.add_request(created_at="2026-09-30T23:59:59+00:00", total_cents=111)
        self.add_request(created_at="2026-10-01T00:00:00+00:00", total_cents=222)
        self.add_request(created_at="2026-10-02T12:00:00+00:00", total_cents=300)
        self.add_request(created_at="2026-10-04T23:59:59+00:00", total_cents=333)
        self.add_request(created_at="2026-10-05T00:00:00+00:00", total_cents=444)
        result = self.analytics()
        self.assertEqual(result["totals"]["requests"], 3)
        self.assertEqual(result["totals"]["captured_cents"], 222 + 300 + 333)
        self.assertEqual([point["captured_cents"] for point in result["series"]],
                         [222, 300, 0, 333])
        self.assertEqual(result["by_agent"][0]["requests"], 3)

    def test_workspace_isolation(self) -> None:
        other = self.store.create_workspace("Globex")
        other_agent = self.make_agent("Globex buyer", "purchasing", workspace=other)
        self.add_request(agent=other_agent, workspace=other, status="captured",
                         total_cents=9_999,
                         lines=[make_line("globex.example", "pantry", 9_999)],
                         reasons=[make_reason("monthly_cap", "block", "over budget")])
        self.add_request(status="captured", total_cents=1_000)

        ours = self.analytics()
        self.assertEqual(ours["totals"]["requests"], 1)
        self.assertEqual(ours["totals"]["captured_cents"], 1_000)
        self.assertEqual([row["key"] for row in ours["by_agent"]], [self.agent["id"]])
        self.assertNotIn("globex.example", [row["key"] for row in ours["by_vendor"]])

        theirs = self.store.analytics(other["id"], START, END)
        self.assertEqual(theirs["totals"]["requests"], 1)
        self.assertEqual(theirs["totals"]["captured_cents"], 9_999)
        self.assertEqual(theirs["by_vendor"][0]["key"], "globex.example")
        self.assertEqual(theirs["by_rule"][0]["key"], "monthly_cap")

    def test_invalid_bounds_raise(self) -> None:
        with self.assertRaises(ValueError):
            self.store.analytics(self.workspace["id"], "not-a-date", END)
        with self.assertRaises(ValueError):
            self.store.analytics(self.workspace["id"], START, "")
        with self.assertRaises(ValueError):
            self.store.analytics(self.workspace["id"], END, START)
        with self.assertRaises(ValueError):
            self.store.analytics(self.workspace["id"], START, START)
        with self.assertRaises(ValueError):
            self.store.analytics(
                self.workspace["id"],
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",  # equal, not a range
            )

    def test_range_longer_than_limit_raises(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=MAX_ANALYTICS_DAYS + 5)
        with self.assertRaises(ValueError):
            self.store.analytics(
                self.workspace["id"],
                start.isoformat(timespec="seconds"),
                end.isoformat(timespec="seconds"),
            )
        # A range of exactly the limit is still allowed.
        exact_end = start + timedelta(days=MAX_ANALYTICS_DAYS)
        result = self.store.analytics(
            self.workspace["id"],
            start.isoformat(timespec="seconds"),
            exact_end.isoformat(timespec="seconds"),
        )
        self.assertEqual(len(result["series"]), MAX_ANALYTICS_DAYS)


class JsonShapeTests(AnalyticsTestCase):
    def test_payload_is_json_ready_with_integer_cents(self) -> None:
        self.add_request(
            status="captured", total_cents=1_234, decision_outcome="auto",
            lines=[make_line(line_cents=1_234)],
            reasons=[make_reason("approval_threshold", "review", "over threshold")],
        )
        result = self.analytics()
        json.dumps(result)  # serialisable, no Decimal/date objects
        for key, value in result["totals"].items():
            self.assertIsInstance(value, int, key)
            self.assertNotIsInstance(value, bool, key)
        for point in result["series"]:
            self.assertIsInstance(point["captured_cents"], int)
            self.assertIsInstance(point["requests"], int)
            self.assertIsInstance(point["date"], str)
        for section in ("by_agent", "by_tag", "by_vendor", "by_category", "by_rule"):
            for row in result[section]:
                self.assertIsInstance(row["key"], str, f"{section}.key")
                if section != "by_rule":
                    self.assertIsInstance(row["label"], str, f"{section}.label")
                for field, value in row.items():
                    if field not in ("key", "label", "tag", "severity", "example"):
                        self.assertIsInstance(value, int, f"{section}.{field}")
        self.assertIsInstance(result["by_rule"][0]["severity"], str)
        self.assertIsInstance(result["by_rule"][0]["example"], str)

    def test_typed_empty_store_shape(self) -> None:
        result = self.analytics()
        self.assertEqual(result["range"], {"from": START, "to": END})
        for key, value in result["totals"].items():
            self.assertEqual(value, 0, key)
            self.assertIsInstance(value, int)
        self.assertTrue(all(isinstance(point["date"], str) for point in result["series"]))


if __name__ == "__main__":
    unittest.main()
