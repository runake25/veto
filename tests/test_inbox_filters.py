"""Inbox filters: line-aware search, UTC day bounds, viewer timezone, state groups."""
from __future__ import annotations

import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server_harness import StubHandler, load_server  # noqa: E402
from veto.store import STATE_GROUPS, Store  # noqa: E402


def line(title="Bamboo toilet paper", vendor="amazon.com", category="pantry",
         cents=1_000) -> dict:
    return {
        "title": title,
        "qty": 1,
        "unit_cents": cents,
        "line_cents": cents,
        "vendor": vendor,
        "category": category,
    }


class InboxFilterStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.pantry = self.store.create_agent(
            self.workspace["id"], {"name": "Pantry runner", "tag": "pantry"}
        )

    def add_request(self, *, title="A cart", text="", requester="Caroline", agent=None,
                    status="captured", total_cents=1_000,
                    created_at="2026-10-26T12:00:00+00:00", lines=None) -> dict:
        record = self.store.create_request(
            self.workspace["id"],
            (agent or self.pantry)["id"],
            origin="web",
            status=status,
            title=title,
            text=text,
            requester=requester,
            total_cents=total_cents,
            subtotal_cents=total_cents,
            lines=list(lines or []),
        )
        self.store.execute(
            "UPDATE requests SET created_at = ? WHERE id = ?",
            (created_at, record["id"]),
        )
        return record

    def ids(self, **filters) -> list[str]:
        return [row["id"] for row in self.store.list_requests(self.workspace["id"], **filters)]

    # -- q: request fields and lines -----------------------------------
    def test_q_still_matches_request_fields(self) -> None:
        title = self.add_request(title="Coffee capsules")
        text = self.add_request(title="Something", text="we are out of toilet paper")
        requester = self.add_request(title="Other", requester="Tom Iverson")
        self.assertIn(title["id"], self.ids(q="coffee"))
        self.assertIn(text["id"], self.ids(q="TOILET"))
        self.assertIn(requester["id"], self.ids(q="iverson"))
        # agent name is matched too, case-insensitively
        self.assertIn(title["id"], self.ids(q="pantry RUNNER"))

    def test_q_matches_a_line_vendor(self) -> None:
        record = self.add_request(
            title="Misc", lines=[line(vendor="Staples.COM")]
        )
        self.assertEqual(self.ids(q="staples.com"), [record["id"]])
        self.assertEqual(self.ids(q="staples"), [record["id"]])

    def test_q_matches_a_line_title(self) -> None:
        record = self.add_request(
            title="Misc", lines=[line(title="Bamboo toilet paper, 24 rolls")]
        )
        self.assertEqual(self.ids(q="toilet paper"), [record["id"]])

    def test_q_with_no_match_returns_nothing(self) -> None:
        self.add_request(title="Coffee capsules", lines=[line()])
        self.assertEqual(self.ids(q="unicorn tears"), [])

    def test_a_multi_line_match_does_not_duplicate_the_request(self) -> None:
        record = self.add_request(
            title="Misc",
            lines=[line(vendor="staples.com"), line(vendor="staples.com")],
        )
        self.assertEqual(self.ids(q="staples.com"), [record["id"]])

    # -- created_from / created_before --------------------------------
    def test_created_from_is_inclusive(self) -> None:
        before = self.add_request(created_at="2026-10-25T23:59:59+00:00")
        edge = self.add_request(created_at="2026-10-26T00:00:00+00:00")
        after = self.add_request(created_at="2026-10-26T00:00:01+00:00")
        found = self.ids(created_from="2026-10-26T00:00:00+00:00")
        self.assertNotIn(before["id"], found)
        self.assertIn(edge["id"], found)
        self.assertIn(after["id"], found)

    def test_created_before_is_exclusive(self) -> None:
        before = self.add_request(created_at="2026-10-26T11:59:59+00:00")
        edge = self.add_request(created_at="2026-10-26T12:00:00+00:00")
        found = self.ids(created_before="2026-10-26T12:00:00+00:00")
        self.assertIn(before["id"], found)
        self.assertNotIn(edge["id"], found)

    def test_window_combines_both_bounds(self) -> None:
        early = self.add_request(created_at="2026-10-26T08:00:00+00:00")
        middle = self.add_request(created_at="2026-10-26T12:00:00+00:00")
        late = self.add_request(created_at="2026-10-26T18:00:00+00:00")
        found = self.ids(
            created_from="2026-10-26T10:00:00+00:00",
            created_before="2026-10-26T16:00:00+00:00",
        )
        self.assertEqual(found, [middle["id"]])
        self.assertNotIn(early["id"], found)
        self.assertNotIn(late["id"], found)

    # -- reconciliation -------------------------------------------------
    def test_count_and_sum_reconcile_with_the_filtered_list(self) -> None:
        self.add_request(title="Coffee capsules", total_cents=1_200, lines=[line()])
        self.add_request(
            title="Misc", total_cents=2_500,
            lines=[line(vendor="staples.com", title="Markers", category="stationery")],
        )
        self.add_request(
            title="Older", total_cents=9_900, created_at="2026-10-20T12:00:00+00:00",
            lines=[line()],
        )
        filters = {
            "q": "staples",
            "created_from": "2026-10-26T00:00:00+00:00",
            "created_before": "2026-10-27T00:00:00+00:00",
        }
        rows = self.store.list_requests(self.workspace["id"], **filters)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Misc")
        self.assertEqual(self.store.count_requests(self.workspace["id"], **filters), 1)
        self.assertEqual(self.store.sum_requests(self.workspace["id"], **filters), 2_500)

    def test_line_filter_matches_the_count_without_filters(self) -> None:
        first = self.add_request(title="A", total_cents=700, lines=[line(cents=700)])
        self.add_request(title="B", total_cents=900, lines=[line()])
        self.assertEqual(
            self.store.count_requests(self.workspace["id"]), 2
        )
        self.assertEqual(
            self.store.sum_requests(self.workspace["id"]), 1_600
        )
        self.assertEqual(len(self.ids(q="toilet")), 2)
        self.assertIn(first["id"], self.ids(q="toilet"))

    # -- ordering keeps working under filters --------------------------
    def test_sort_still_works_with_filters(self) -> None:
        small = self.add_request(title="Paper small", total_cents=500, lines=[line()])
        big = self.add_request(title="Paper big", total_cents=9_000, lines=[line()])
        newest = self.add_request(
            title="Paper newest", total_cents=1_000,
            created_at="2026-10-26T13:00:00+00:00", lines=[line()],
        )
        found = self.ids(
            q="paper",
            created_from="2026-10-26T00:00:00+00:00",
            sort="amount_desc",
        )
        self.assertEqual(found, [big["id"], newest["id"], small["id"]])
        oldest = self.ids(q="paper", sort="oldest")
        self.assertEqual(oldest[0], small["id"])


class InboxDateBoundsTests(unittest.TestCase):
    """The HTTP layer turns viewer-local days into exact UTC instants."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = load_server()

    def bounds(self, **params) -> dict:
        query = {
            ("from" if key == "from_" else key): [value]
            for key, value in params.items()
        }
        return self.server.request_date_bounds(query)

    def test_hong_kong_day_maps_to_the_previous_utc_evening(self) -> None:
        window = self.bounds(from_="2026-10-26", to="2026-10-26", tz="480")
        self.assertEqual(window["created_from"], "2026-10-25T16:00:00+00:00")
        self.assertEqual(window["created_before"], "2026-10-26T16:00:00+00:00")
        self.assertEqual(window["from"], "2026-10-26")
        self.assertEqual(window["to"], "2026-10-26")
        self.assertEqual(window["tz"], 480)

    def test_no_dates_means_no_bounds(self) -> None:
        window = self.bounds()
        self.assertIsNone(window["created_from"])
        self.assertIsNone(window["created_before"])
        self.assertIsNone(window["from"])
        self.assertIsNone(window["to"])
        self.assertEqual(window["tz"], 0)

    def test_one_sided_windows(self) -> None:
        from_only = self.bounds(from_="2026-10-26")
        self.assertEqual(from_only["created_from"], "2026-10-26T00:00:00+00:00")
        self.assertIsNone(from_only["created_before"])
        to_only = self.bounds(to="2026-10-26")
        self.assertIsNone(to_only["created_from"])
        self.assertEqual(to_only["created_before"], "2026-10-27T00:00:00+00:00")

    def test_negative_offsets_are_east_negative(self) -> None:
        # UTC-5 (New York in winter) starts its day five hours after UTC midnight.
        window = self.bounds(from_="2026-10-26", to="2026-10-26", tz="-300")
        self.assertEqual(window["created_from"], "2026-10-26T05:00:00+00:00")
        self.assertEqual(window["created_before"], "2026-10-27T05:00:00+00:00")

    def test_invalid_input_is_a_400_invalid_range(self) -> None:
        cases = (
            {"from_": "26-10-2026"},
            {"to": "not-a-day"},
            {"from_": "2026-10-27", "to": "2026-10-26"},  # reversed
            {"tz": "nope"},
            {"tz": "999"},
            {"tz": "-841"},
        )
        for params in cases:
            with self.subTest(params=params):
                with self.assertRaises(self.server.ApiError) as caught:
                    self.bounds(**params)
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(caught.exception.code, "invalid_range")

    def test_local_midnight_boundary_counts_the_right_requests(self) -> None:
        """A cart at 17:30Z is 01:30 *next day* in Hong Kong, so day 26 excludes it."""
        store = Store(":memory:")
        self.addCleanup(store.close)
        workspace = store.create_workspace("Tz boundary")
        agent = store.create_agent(workspace["id"], {"name": "Pantry runner"})
        inside = self._request(store, workspace, agent, "2026-10-26T02:00:00+00:00")
        before = self._request(store, workspace, agent, "2026-10-25T15:30:00+00:00")
        end = self._request(store, workspace, agent, "2026-10-26T16:00:00+00:00")
        window = self.bounds(from_="2026-10-26", to="2026-10-26", tz="480")
        rows = store.list_requests(
            workspace["id"],
            created_from=window["created_from"],
            created_before=window["created_before"],
        )
        ids = [row["id"] for row in rows]
        self.assertEqual(ids, [inside["id"]])
        self.assertNotIn(before["id"], ids)
        self.assertNotIn(end["id"], ids)  # local midnight on the 27th, exclusive

    @staticmethod
    def _request(store, workspace, agent, created_at: str) -> dict:
        record = store.create_request(
            workspace["id"], agent["id"], origin="web", status="captured",
            title="Cart", total_cents=100,
        )
        store.execute(
            "UPDATE requests SET created_at = ? WHERE id = ?",
            (created_at, record["id"]),
        )
        return record


class StateGroupStoreTests(unittest.TestCase):
    """Round 4: the five Inbox tabs are five status sets, one SQL filter."""

    STATUSES = (
        ("awaiting", 1_000),
        ("approved", 500),
        ("captured", 2_000),
        ("blocked", 300),
        ("vetoed", 400),
        ("voided", 700),
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.agent = self.store.create_agent(
            self.workspace["id"], {"name": "Pantry runner"}
        )
        self.records = {}
        for status, total in self.STATUSES:
            self.records[status] = self.store.create_request(
                self.workspace["id"],
                self.agent["id"],
                origin="web",
                status=status,
                title=f"{status} cart",
                total_cents=total,
                subtotal_cents=total,
            )

    def ids(self, **filters) -> list[str]:
        rows = self.store.list_requests(self.workspace["id"], limit=100, **filters)
        return [row["id"] for row in rows]

    def test_group_mapping(self) -> None:
        self.assertEqual(
            STATE_GROUPS,
            {
                "needs": ("awaiting", "approved"),
                "settled": ("captured",),
                "refused": ("blocked", "vetoed"),
                "withdrawn": ("voided",),
            },
        )

    def test_each_group_selects_its_statuses(self) -> None:
        expectations = {
            "needs": {"awaiting", "approved"},
            "settled": {"captured"},
            "refused": {"blocked", "vetoed"},
            "withdrawn": {"voided"},
        }
        for state, statuses in expectations.items():
            with self.subTest(state=state):
                self.assertEqual(
                    set(self.ids(state=state)),
                    {self.records[status]["id"] for status in statuses},
                )

    def test_counts_and_sums_respect_the_group(self) -> None:
        self.assertEqual(self.store.count_requests(self.workspace["id"], state="needs"), 2)
        self.assertEqual(self.store.sum_requests(self.workspace["id"], state="needs"), 1_500)
        self.assertEqual(
            self.store.count_requests(self.workspace["id"], state="refused"), 2
        )
        self.assertEqual(
            self.store.sum_requests(self.workspace["id"], state="refused"), 700
        )
        self.assertEqual(
            self.store.sum_requests(self.workspace["id"], state="settled"), 2_000
        )
        self.assertEqual(
            self.store.sum_requests(self.workspace["id"], state="withdrawn"), 700
        )

    def test_state_composes_with_status(self) -> None:
        found = self.ids(state="needs", status="awaiting")
        self.assertEqual(found, [self.records["awaiting"]["id"]])
        self.assertEqual(self.ids(state="settled", status="blocked"), [])

    def test_state_composes_with_q(self) -> None:
        self.assertEqual(
            self.ids(state="refused", q="blocked"),
            [self.records["blocked"]["id"]],
        )

    def test_state_composes_with_sort(self) -> None:
        self.assertEqual(
            self.ids(state="needs", sort="amount_desc"),
            [self.records["awaiting"]["id"], self.records["approved"]["id"]],
        )

    def test_unknown_state_raises_from_the_store(self) -> None:
        with self.assertRaises(ValueError):
            self.store.list_requests(self.workspace["id"], state="bogus")


class StateGroupHandlerTests(unittest.TestCase):
    """The HTTP layer: validation, envelope and the grouped total/sum."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = load_server()

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.workspace = self.server.STORE.create_workspace(f"States {suffix}")
        self.agent = self.server.STORE.create_agent(
            self.workspace["id"], {"name": f"State agent {suffix}"}
        )
        for status, total in StateGroupStoreTests.STATUSES:
            self.server.STORE.create_request(
                self.workspace["id"], self.agent["id"], origin="web", status=status,
                title=f"{status} cart", total_cents=total, subtotal_cents=total,
            )
        session = {
            "user": {"id": "usr_state_test", "name": "State Test", "role": "admin"},
            "workspace": self.workspace,
        }
        self.stub = StubHandler(self.server, session)

    def list_requests(self, query: str = "") -> dict:
        self.stub.path = f"/api/v1/requests?{query}" if query else "/api/v1/requests"
        self.stub.response = None
        self.server.Handler._v1_list_requests(self.stub)
        return self.stub.response

    def test_state_groups_end_to_end(self) -> None:
        data = self.list_requests("state=needs")["data"]
        self.assertEqual(data["state"], "needs")
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["returned"], 2)
        self.assertEqual(data["sum_cents"], 1_500)
        self.assertEqual({row["status"] for row in data["requests"]},
                         {"awaiting", "approved"})

    def test_all_and_absent_state_are_the_same(self) -> None:
        everything = self.list_requests()
        self.assertEqual(everything["data"]["state"], None)
        self.assertEqual(everything["data"]["total"], 6)
        explicit_all = self.list_requests("state=all")
        self.assertEqual(explicit_all["data"]["state"], None)
        self.assertEqual(explicit_all["data"]["total"], 6)

    def test_invalid_state_is_400_invalid_state(self) -> None:
        with self.assertRaises(self.server.ApiError) as caught:
            self.list_requests("state=everything")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.code, "invalid_state")

    def test_legacy_status_param_still_works(self) -> None:
        data = self.list_requests("status=captured")["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["sum_cents"], 2_000)
        self.assertEqual(data["requests"][0]["status"], "captured")
        # the synthetic expired tab keeps its old semantics instead of 400ing
        expired = self.list_requests("status=expired")
        self.assertEqual(expired["status"], 200)
        self.assertEqual(expired["data"]["total"], 0)  # these carts have no consent


if __name__ == "__main__":
    unittest.main()
