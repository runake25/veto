"""Round-4 identity + Settings endpoints.

Identity is tested at the seed/store level (fresh seed, legacy DB migration,
partial migration, workspace rename). The HTTP handlers are driven through the
real ``server.Handler`` methods with the shared stub, so the status codes and
JSON bodies are the shipping ones.
"""
from __future__ import annotations

import queue
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server_harness import StubHandler, load_server  # noqa: E402
from veto import auth as veto_auth  # noqa: E402
from veto.events import FEED  # noqa: E402
from veto.seed import (  # noqa: E402
    ADMIN_EMAIL,
    ADMIN_NAME,
    APPROVER_EMAIL,
    APPROVER_NAME,
    DEMO_PASSWORD,
    LEGACY_APPROVER_EMAIL,
    LEGACY_APPROVER_NAME,
    migrate_demo_identity,
    seed_demo,
)
from veto.store import Store  # noqa: E402


def drain(subscriber) -> list[str]:
    items: list[str] = []
    while True:
        try:
            items.append(subscriber.get_nowait())
        except queue.Empty:
            return items


class IdentityMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)

    def legacy_demo(self) -> tuple[dict, dict]:
        workspace = self.store.create_workspace("Bracken & Field")
        self.store.create_user(
            workspace["id"], ADMIN_EMAIL, name=ADMIN_NAME,
            password_hash=veto_auth.hash_password(DEMO_PASSWORD), role="admin",
        )
        approver = self.store.create_user(
            workspace["id"], LEGACY_APPROVER_EMAIL, name=LEGACY_APPROVER_NAME,
            password_hash=veto_auth.hash_password(DEMO_PASSWORD), role="approver",
        )
        return workspace, approver

    def test_fresh_seed_uses_caroline_hayes(self) -> None:
        seeded = seed_demo(self.store)
        approver = seeded["approver"]
        self.assertEqual(approver["name"], "Caroline Hayes")
        self.assertEqual(approver["email"], "caroline@brackenfield.demo")
        self.assertIsNone(self.store.user_by_email(LEGACY_APPROVER_EMAIL))

    def test_migration_renames_legacy_approver_row(self) -> None:
        _workspace, approver = self.legacy_demo()
        legacy_session = self.store.create_session(
            approver["id"], approver["workspace_id"],
            "legacy-token-hash", "2999-01-01T00:00:00+00:00",
        )
        self.assertTrue(migrate_demo_identity(self.store))
        migrated = self.store.user(approver["id"])
        self.assertIsNotNone(migrated)
        self.assertEqual(migrated["name"], "Caroline Hayes")
        self.assertEqual(migrated["email"], "caroline@brackenfield.demo")
        # the user id never changes, so sessions and request references survive
        session = self.store.session_by_token_hash("legacy-token-hash")
        self.assertEqual(session["id"], legacy_session["id"])
        self.assertEqual(session["user_id"], approver["id"])
        self.assertIsNone(self.store.user_by_email(LEGACY_APPROVER_EMAIL))

    def test_migration_is_idempotent(self) -> None:
        self.legacy_demo()
        self.assertTrue(migrate_demo_identity(self.store))
        self.assertFalse(migrate_demo_identity(self.store))
        self.assertFalse(migrate_demo_identity(self.store))
        self.assertEqual(self.store.count_users(), 2)

    def test_migration_finishes_a_partial_rename(self) -> None:
        workspace = self.store.create_workspace("Bracken & Field")
        user = self.store.create_user(
            workspace["id"], APPROVER_EMAIL, name=LEGACY_APPROVER_NAME,
            password_hash=veto_auth.hash_password(DEMO_PASSWORD), role="approver",
        )
        self.assertTrue(migrate_demo_identity(self.store))
        self.assertEqual(self.store.user(user["id"])["name"], "Caroline Hayes")

    def test_migration_is_a_noop_without_legacy_rows(self) -> None:
        self.assertFalse(migrate_demo_identity(self.store))

    def test_seed_repairs_a_legacy_db_without_duplicates(self) -> None:
        self.legacy_demo()
        seeded = seed_demo(self.store)
        self.assertEqual(seeded["approver"]["name"], "Caroline Hayes")
        self.assertEqual(self.store.count_users(), 2)
        self.assertIsNone(self.store.user_by_email(LEGACY_APPROVER_EMAIL))

    def test_seed_after_a_workspace_rename_does_not_duplicate_it(self) -> None:
        seeded = seed_demo(self.store)
        self.store.update_workspace(seeded["workspace"]["id"], name="Bracken Field Ltd")
        again = seed_demo(self.store)
        self.assertEqual(again["workspace"]["id"], seeded["workspace"]["id"])
        self.assertEqual(len(self.store.list_workspaces()), 1)
        self.assertEqual(self.store.count_users(), 2)


class SettingsHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = load_server()

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.workspace = self.server.STORE.create_workspace(f"Settings {suffix}")
        self.admin = self.server.STORE.create_user(
            self.workspace["id"], f"admin-{suffix}@example.com",
            name="Settings Admin",
            password_hash=veto_auth.hash_password("admin-password-1"),
            role="admin",
        )
        self.approver = self.server.STORE.create_user(
            self.workspace["id"], f"approver-{suffix}@example.com",
            name="Settings Approver",
            password_hash=veto_auth.hash_password("approver-password-1"),
            role="approver",
        )
        self.session = {"user": self.admin, "workspace": self.workspace}
        self.stub = StubHandler(self.server, self.session)
        self.subscriber = FEED.subscribe()
        self.addCleanup(FEED.unsubscribe, self.subscriber)

    # -- helpers --------------------------------------------------------
    def patch_me(self, name: str) -> dict:
        self.stub.body = {"name": name}
        self.server.Handler._v1_update_me(self.stub)
        return self.stub.response

    def patch_workspace(self, name: str, *, user: dict | None = None) -> dict:
        if user is not None:
            self.stub.session = {"user": user, "workspace": self.workspace}
        self.stub.body = {"name": name}
        self.server.Handler._v1_update_workspace(self.stub)
        return self.stub.response

    # -- PATCH /api/v1/me ----------------------------------------------
    def test_patch_me_renames_the_user(self) -> None:
        response = self.patch_me("  Caroline Hayes  ")
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["data"]["user"]["name"], "Caroline Hayes")
        self.assertEqual(
            self.server.STORE.user(self.admin["id"])["name"], "Caroline Hayes"
        )
        self.assertEqual(drain(self.subscriber), ["requests"])

    def test_patch_me_validates_the_name(self) -> None:
        for bad in ("", "   ", "x" * 81):
            with self.subTest(name=bad[:8]):
                with self.assertRaises(self.server.ApiError) as caught:
                    self.patch_me(bad)
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(caught.exception.code, "invalid_name")
        # the failed calls did not change the row
        self.assertEqual(self.server.STORE.user(self.admin["id"])["name"], "Settings Admin")

    def test_patch_me_requires_a_session(self) -> None:
        stub = StubHandler(self.server, None, body={"name": "Nobody"})
        with self.assertRaises(self.server.ApiError) as caught:
            self.server.Handler._v1_update_me(stub)
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (401, "unauthorized"))

    # -- PATCH /api/v1/workspace ---------------------------------------
    def test_patch_workspace_renames_it_for_an_admin(self) -> None:
        response = self.patch_workspace("  Bracken Field Ltd  ")
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["data"]["workspace"]["name"], "Bracken Field Ltd")
        self.assertEqual(
            self.server.STORE.workspace(self.workspace["id"])["name"], "Bracken Field Ltd"
        )
        self.assertEqual(drain(self.subscriber), ["agents"])

    def test_patch_workspace_rejects_a_non_admin(self) -> None:
        with self.assertRaises(self.server.ApiError) as caught:
            self.patch_workspace("Should Not Apply", user=self.approver)
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (403, "forbidden"))
        self.assertEqual(
            self.server.STORE.workspace(self.workspace["id"])["name"],
            self.workspace["name"],
        )

    def test_patch_workspace_validates_the_name(self) -> None:
        for bad in ("", "a", "x" * 81):
            with self.subTest(name=bad[:8]):
                with self.assertRaises(self.server.ApiError) as caught:
                    self.patch_workspace(bad)
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(caught.exception.code, "invalid_name")

    def test_patch_workspace_reports_a_taken_name(self) -> None:
        other = self.server.STORE.create_workspace(f"Taken {uuid.uuid4().hex[:6]}")
        with self.assertRaises(self.server.ApiError) as caught:
            self.patch_workspace(other["name"])
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (409, "name_taken"))

    # -- POST /api/v1/me/password --------------------------------------
    def change_password(self, current: str, new: str) -> dict:
        self.stub.body = {"current_password": current, "new_password": new}
        self.server.Handler._v1_change_password(self.stub)
        return self.stub.response

    def test_change_password_verifies_the_current_one(self) -> None:
        with self.assertRaises(self.server.ApiError) as caught:
            self.change_password("wrong-password", "a-new-long-password")
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (401, "invalid_password"))

    def test_change_password_enforces_the_length_rule(self) -> None:
        with self.assertRaises(self.server.ApiError) as caught:
            self.change_password("admin-password-1", "short")
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (400, "invalid_password_rule"))

    def test_change_password_updates_the_hash(self) -> None:
        response = self.change_password("admin-password-1", "a-new-long-password")
        self.assertEqual(response, {"ok": True, "data": {}, "status": 200})
        stored = self.server.STORE.user(self.admin["id"])
        self.assertTrue(
            veto_auth.verify_password(stored["password_hash"], "a-new-long-password")
        )
        self.assertFalse(
            veto_auth.verify_password(stored["password_hash"], "admin-password-1")
        )

    # -- routing --------------------------------------------------------
    def test_settings_routes_dispatch(self) -> None:
        calls: list[str] = []
        self.stub._v1_update_me = lambda: calls.append("me")
        self.stub._v1_update_workspace = lambda: calls.append("workspace")
        self.stub._v1_change_password = lambda: calls.append("password")
        patch = self.server.Handler._v2_patch
        post = self.server.Handler._v2_post
        self.assertTrue(post(self.stub, "/api/v1/me/password"))
        self.assertTrue(patch(self.stub, "/api/v1/me"))
        self.assertTrue(patch(self.stub, "/api/v1/workspace"))
        self.assertEqual(calls, ["password", "me", "workspace"])


if __name__ == "__main__":
    unittest.main()
