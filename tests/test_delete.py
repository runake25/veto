"""Delete paths: an agent with no history, an agent with history, a routine.

The handler-level cases drive the real ``server.Handler`` methods through a
stub, so the 409/404 codes and the change-feed publishes are the shipping ones.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server_harness import StubHandler, load_server  # noqa: E402
from veto.events import FEED  # noqa: E402
from veto.store import Store  # noqa: E402

FIXED = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def drain(subscriber) -> list[str]:
    items: list[str] = []
    while True:
        try:
            items.append(subscriber.get_nowait())
        except Exception:  # queue.Empty
            return items


class DeleteStoreTests(unittest.TestCase):
    """The store half: cascade rules, and the ledger's immutability."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.agent = self.store.create_agent(
            self.workspace["id"],
            {"name": "Pantry runner", "tag": "pantry"},
            vendors=["amazon.com"],
            categories=["pantry"],
            rules=["monthly_cap"],
            overrides=[],
        )

    def add_request(self, agent_id: str | None = None) -> dict:
        return self.store.create_request(
            self.workspace["id"],
            agent_id or self.agent["id"],
            origin="web",
            status="captured",
            title="Toilet paper",
            total_cents=1_000,
            subtotal_cents=1_000,
            lines=[{
                "title": "Bamboo toilet paper",
                "qty": 1,
                "unit_cents": 1_000,
                "line_cents": 1_000,
                "vendor": "amazon.com",
                "category": "pantry",
            }],
        )

    def test_delete_routine_cascades_runs_but_never_touches_requests(self) -> None:
        request = self.add_request()
        routine = self.store.create_routine(
            self.workspace["id"],
            self.agent["id"],
            name="Pantry restock",
            instruction="2 packs of toilet paper",
            cadence="weekly",
            next_run_at=stamp(FIXED + timedelta(days=7)),
        )
        self.store.create_routine_run(
            routine["id"],
            self.workspace["id"],
            due_at=stamp(FIXED),
            started_at=stamp(FIXED),
            outcome="captured",
            request_id=request["id"],
            amount_cents=1_000,
        )
        self.assertEqual(len(self.store.routine_runs_for(self.workspace["id"])), 1)

        self.assertTrue(self.store.delete_routine(self.workspace["id"], routine["id"]))
        self.assertIsNone(self.store.routine(self.workspace["id"], routine["id"]))
        self.assertEqual(self.store.routine_runs_for(self.workspace["id"]), [])
        # the money trail is untouched
        kept = self.store.request(self.workspace["id"], request["id"])
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "captured")
        self.assertEqual(len(kept["lines"]), 1)
        self.assertEqual(self.store.count_requests(self.workspace["id"]), 1)

    def test_delete_routine_is_workspace_scoped_and_missing_is_false(self) -> None:
        routine = self.store.create_routine(
            self.workspace["id"],
            self.agent["id"],
            name="Pantry restock",
            instruction="2 packs of toilet paper",
        )
        other = self.store.create_workspace("Globex")
        self.assertFalse(self.store.delete_routine(other["id"], routine["id"]))
        self.assertFalse(self.store.delete_routine(self.workspace["id"], "rtn_missing"))
        self.assertIsNotNone(self.store.routine(self.workspace["id"], routine["id"]))
        self.assertTrue(self.store.delete_routine(self.workspace["id"], routine["id"]))

    def test_delete_agent_without_history(self) -> None:
        self.assertTrue(self.store.delete_agent(self.workspace["id"], self.agent["id"]))
        self.assertIsNone(self.store.agent(self.workspace["id"], self.agent["id"]))
        self.assertFalse(self.store.delete_agent(self.workspace["id"], self.agent["id"]))


class DeleteHandlerTests(unittest.TestCase):
    """The HTTP half: error codes, JSON bodies and feed publishes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = load_server()

    def setUp(self) -> None:
        self.suffix = uuid.uuid4().hex[:8]
        self.workspace = self.server.STORE.create_workspace(f"Delete handlers {self.suffix}")
        self.agent = self.server.STORE.create_agent(
            self.workspace["id"],
            {"name": f"Temp agent {self.suffix}", "tag": "pantry"},
        )
        session = {
            "user": {
                "id": "usr_test_admin",
                "name": "Test Admin",
                "email": "test@example.com",
                "role": "admin",
            },
            "workspace": self.workspace,
        }
        self.stub = StubHandler(self.server, session)
        self.subscriber = FEED.subscribe()
        self.addCleanup(FEED.unsubscribe, self.subscriber)

    def tearDown(self) -> None:
        # leave the shared test store tidy; the ledger rows are kept on purpose
        self.server.STORE.delete_agent(self.workspace["id"], self.agent["id"])

    def test_delete_agent_without_history_returns_deleted_and_publishes(self) -> None:
        self.server.Handler._v1_delete_agent(self.stub, self.agent["id"])
        self.assertEqual(self.stub.response, {"ok": True, "data": {"deleted": True},
                                              "status": 200})
        self.assertIsNone(self.server.STORE.agent(self.workspace["id"], self.agent["id"]))
        self.assertEqual(drain(self.subscriber), ["agents"])

    def test_delete_agent_with_history_is_refused(self) -> None:
        self.server.STORE.create_request(
            self.workspace["id"],
            self.agent["id"],
            origin="web",
            status="blocked",
            title="A past cart",
            total_cents=0,
        )
        with self.assertRaises(self.server.ApiError) as caught:
            self.server.Handler._v1_delete_agent(self.stub, self.agent["id"])
        error = caught.exception
        self.assertEqual(error.status, 409)
        self.assertEqual(error.code, "agent_has_history")
        self.assertIn("1 recorded request", error.message)
        self.assertIn("Pause the agent instead.", error.message)
        # nothing was deleted and nothing was published
        self.assertIsNotNone(self.server.STORE.agent(self.workspace["id"], self.agent["id"]))
        self.assertEqual(drain(self.subscriber), [])

    def test_delete_unknown_agent_is_404(self) -> None:
        with self.assertRaises(self.server.ApiError) as caught:
            self.server.Handler._v1_delete_agent(self.stub, "agt_00000000")
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (404, "agent_not_found"))

    def test_delete_routine_missing_is_404(self) -> None:
        with self.assertRaises(self.server.ApiError) as caught:
            self.server.Handler._v1_delete_routine(self.stub, self.agent["id"])
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.code, "no_routine")
        self.assertEqual(caught.exception.message, "This agent has no standing job.")

    def test_delete_routine_removes_it_and_publishes(self) -> None:
        routine = self.server.STORE.create_routine(
            self.workspace["id"],
            self.agent["id"],
            name="Temp restock",
            instruction="2 packs of toilet paper",
            cadence="weekly",
            next_run_at=stamp(FIXED + timedelta(days=7)),
        )
        self.server.Handler._v1_delete_routine(self.stub, self.agent["id"])
        self.assertEqual(self.stub.response, {"ok": True, "data": {"deleted": True},
                                              "status": 200})
        self.assertIsNone(self.server.STORE.routine(self.workspace["id"], routine["id"]))
        self.assertEqual(drain(self.subscriber), ["agents"])

    def test_viewer_cannot_delete_agents_or_routines(self) -> None:
        viewer_session = {
            "user": {"id": "usr_test_viewer", "name": "Viewer", "role": "viewer"},
            "workspace": self.workspace,
        }
        viewer = StubHandler(self.server, viewer_session)
        for method in (
            lambda: self.server.Handler._v1_delete_agent(viewer, self.agent["id"]),
            lambda: self.server.Handler._v1_delete_routine(viewer, self.agent["id"]),
        ):
            with self.assertRaises(self.server.ApiError) as caught:
                method()
            self.assertEqual((caught.exception.status, caught.exception.code),
                             (403, "forbidden"))

    def test_delete_routes_dispatch_to_the_right_handlers(self) -> None:
        calls: list[tuple] = []
        self.stub._v1_delete_agent = lambda agent_id: calls.append(("agent", agent_id))
        self.stub._v1_delete_routine = lambda agent_id: calls.append(("routine", agent_id))
        self.stub._v1_revoke_key = lambda agent_id, key_id: calls.append(
            ("key", agent_id, key_id)
        )
        dispatch = self.server.Handler._v2_delete
        self.assertTrue(dispatch(self.stub, "/api/v1/agents/agt_1"))
        self.assertTrue(dispatch(self.stub, "/api/v1/agents/agt_1/routine"))
        self.assertTrue(dispatch(self.stub, "/api/v1/agents/agt_1/keys/key_1"))
        self.assertEqual(calls, [
            ("agent", "agt_1"),
            ("routine", "agt_1"),
            ("key", "agt_1", "key_1"),
        ])
        with self.assertRaises(self.server.ApiError) as caught:
            dispatch(self.stub, "/api/v1/agents/agt_1/nope")
        self.assertEqual(caught.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
