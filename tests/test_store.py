"""Store unit tests: schema, isolation, persistence, assembly of agent charters."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from veto.store import SCHEMA_VERSION, Store, parse_iso

EXPECTED_TABLES = {
    "meta",
    "workspaces",
    "wallets",
    "users",
    "sessions",
    "agents",
    "agent_vendors",
    "agent_categories",
    "agent_rules",
    "rule_overrides",
    "api_keys",
    "requests",
    "request_lines",
    "request_reasons",
    "consents",
    "payments",
    "events",
}

AGENT_FIELDS = {
    "name": "Pantry runner",
    "tag": "pantry",
    "channel": "#pantry",
    "monthly_cap_cents": 80_000,
    "per_order_cap_cents": 30_000,
    "per_unit_cap_cents": 1_500,
    "approval_threshold_cents": 5_000,
}


def hex_len(value: str, prefix: str) -> int:
    assert value.startswith(prefix), value
    return len(value) - len(prefix)


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "state" / "veto.db"
        self.store = Store(self.db_path)
        self.addCleanup(self.store.close)

    def make_workspace(self, name: str = "Bracken & Field") -> dict:
        return self.store.create_workspace(name, monthly_budget_cents=285_000)

    def make_user(self, workspace_id: str, email: str, role: str = "admin") -> dict:
        return self.store.create_user(
            workspace_id, email, name=email.split("@")[0], password_hash="x", role=role
        )

    def make_agent(self, workspace_id: str, name: str = "Pantry runner", **extra) -> dict:
        fields = dict(AGENT_FIELDS, name=name, **extra)
        return self.store.create_agent(
            workspace_id,
            fields,
            vendors=["grove.co", "amazon.com"],
            categories=["pantry"],
            rules=["monthly_cap", "per_order_cap", "approval_threshold"],
            overrides=[{"category": "pantry", "kind": "per_unit_cap", "value_cents": 900}],
        )


class SchemaTests(StoreTestCase):
    def test_init_schema_creates_every_table_and_stamps_version(self) -> None:
        tables = set(self.store.table_names())
        self.assertTrue(EXPECTED_TABLES <= tables, EXPECTED_TABLES - tables)
        self.assertEqual(self.store.get_meta("schema_version"), SCHEMA_VERSION)

    def test_init_schema_is_idempotent(self) -> None:
        self.store.init_schema()
        self.store.init_schema()
        self.assertEqual(self.store.get_meta("schema_version"), "1")
        self.assertTrue(EXPECTED_TABLES <= set(self.store.table_names()))

    def test_creates_parent_directory_for_db_file(self) -> None:
        self.assertTrue(self.db_path.is_file())

    def test_prevents_duplicate_workspace_names(self) -> None:
        self.make_workspace("Acme")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_workspace("Acme")

    def test_timestamps_are_iso_utc_seconds(self) -> None:
        workspace = self.make_workspace()
        created = workspace["created_at"]
        self.assertRegex(created, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
        parsed = parse_iso(created)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.isoformat(timespec="seconds"), created)

    def test_ids_use_documented_prefixes_and_lengths(self) -> None:
        workspace = self.make_workspace()
        user = self.make_user(workspace["id"], "admin@example.com")
        agent = self.make_agent(workspace["id"])
        session = self.store.create_session(
            user["id"], workspace["id"], "hash", "2999-01-01T00:00:00+00:00"
        )
        key = self.store.create_api_key(agent["id"], workspace["id"], "vkey_abcd1234", "h")
        self.assertEqual(hex_len(workspace["id"], "ws_"), 12)
        self.assertEqual(hex_len(user["id"], "usr_"), 12)
        self.assertEqual(hex_len(agent["id"], "agt_"), 8)
        self.assertEqual(hex_len(session["id"], "ses_"), 12)
        self.assertEqual(hex_len(key["id"], "key_"), 8)
        override = self.store.one(
            "SELECT id FROM rule_overrides WHERE agent_id = ?", (agent["id"],)
        )
        self.assertIsNotNone(override)
        self.assertEqual(hex_len(override["id"], "rov_"), 8)
        self.assertRegex(workspace["id"], r"^ws_[0-9a-f]{12}$")


class WorkspaceTests(StoreTestCase):
    def test_workspace_crud_and_lookup(self) -> None:
        workspace = self.make_workspace()
        self.assertEqual(self.store.workspace(workspace["id"])["name"], "Bracken & Field")
        self.assertEqual(self.store.workspace_by_name("Bracken & Field")["id"], workspace["id"])
        self.assertIsNone(self.store.workspace("ws_missing"))
        self.assertIsNone(self.store.workspace_by_name("Nope"))
        self.assertEqual([w["id"] for w in self.store.list_workspaces()], [workspace["id"]])

    def test_update_workspace_whitelist(self) -> None:
        workspace = self.make_workspace()
        updated = self.store.update_workspace(
            workspace["id"],
            name="Bracken & Field Ltd",
            currency="EUR",
            monthly_budget_cents=123_456,
        )
        self.assertEqual(updated["name"], "Bracken & Field Ltd")
        self.assertEqual(updated["currency"], "EUR")
        self.assertEqual(updated["monthly_budget_cents"], 123_456)
        with self.assertRaises(ValueError):
            self.store.update_workspace(workspace["id"], injected="DROP TABLE users")

    def test_empty_workspace_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_workspace("   ")

    def test_workspace_isolation(self) -> None:
        first = self.make_workspace("Acme")
        second = self.make_workspace("Globex")
        self.make_user(first["id"], "a@acme.test")
        self.make_user(second["id"], "b@globex.test")
        first_agent = self.make_agent(first["id"], "Pantry runner")
        second_agent = self.make_agent(second["id"], "Desk kit")

        self.assertEqual([a["id"] for a in self.store.list_agents(first["id"])], [first_agent["id"]])
        self.assertEqual([a["id"] for a in self.store.list_agents(second["id"])], [second_agent["id"]])
        self.assertIsNone(self.store.agent(first["id"], second_agent["id"]))
        self.assertIsNone(self.store.agent(second["id"], first_agent["id"]))
        self.assertEqual(
            [u["email"] for u in self.store.list_users(first["id"])], ["a@acme.test"]
        )
        self.assertEqual(self.store.count_users(first["id"]), 1)
        self.assertEqual(self.store.count_agents(first["id"]), 1)
        self.assertEqual(self.store.count_workspaces(), 2)
        self.assertEqual(self.store.count_users(), 2)
        self.assertEqual(self.store.count_agents(), 2)

    def test_email_is_normalised_and_unique(self) -> None:
        workspace = self.make_workspace()
        user = self.store.create_user(
            workspace["id"], "  Ops@Example.COM ", name="Ops", password_hash="x", role="admin"
        )
        self.assertEqual(user["email"], "ops@example.com")
        self.assertEqual(self.store.user_by_email("OPS@example.com")["id"], user["id"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_user(
                workspace["id"], "ops@example.com", name="Dup", password_hash="x", role="viewer"
            )

    def test_invalid_role_is_rejected(self) -> None:
        workspace = self.make_workspace()
        with self.assertRaises(ValueError):
            self.make_user(workspace["id"], "root@example.com", role="superuser")


class AgentTests(StoreTestCase):
    def test_agent_round_trip_assembles_children(self) -> None:
        workspace = self.make_workspace()
        agent = self.make_agent(workspace["id"])
        self.assertEqual(agent["workspace_id"], workspace["id"])
        self.assertEqual(agent["status"], "active")
        self.assertEqual(agent["monthly_cap_cents"], 80_000)
        self.assertEqual(agent["vendors"], ["grove.co", "amazon.com"])
        self.assertEqual(agent["categories"], ["pantry"])
        self.assertEqual(
            agent["rules"], ["monthly_cap", "per_order_cap", "approval_threshold"]
        )
        self.assertEqual(
            agent["overrides"],
            [{"category": "pantry", "kind": "per_unit_cap", "value_cents": 900}],
        )

    def test_agent_children_are_deduplicated(self) -> None:
        workspace = self.make_workspace()
        agent = self.store.create_agent(
            workspace["id"],
            dict(AGENT_FIELDS, name="Duplicate test"),
            vendors=["amazon.com", "amazon.com"],
            categories=["pantry", "pantry"],
            rules=["monthly_cap", "monthly_cap"],
            overrides=[],
        )
        self.assertEqual(agent["vendors"], ["amazon.com"])
        self.assertEqual(agent["categories"], ["pantry"])
        self.assertEqual(agent["rules"], ["monthly_cap"])

    def test_agent_defaults_and_errors(self) -> None:
        workspace = self.make_workspace()
        agent = self.store.create_agent(workspace["id"], {"name": "Bare"})
        self.assertEqual(agent["tag"], "general")
        self.assertEqual(agent["channel"], None)
        self.assertEqual(agent["rules"], [])
        self.assertEqual(agent["vendors"], [])
        with self.assertRaises(ValueError):
            self.store.create_agent(workspace["id"], {"name": ""})
        with self.assertRaises(ValueError):
            self.store.create_agent(workspace["id"], {"name": "Bad", "status": "sleepy"})

    def test_agent_lists_only_enabled_rules(self) -> None:
        workspace = self.make_workspace()
        agent = self.make_agent(workspace["id"])
        self.store.execute(
            "UPDATE agent_rules SET enabled = 0 WHERE agent_id = ? AND kind = 'per_order_cap'",
            (agent["id"],),
        )
        reloaded = self.store.agent(workspace["id"], agent["id"])
        self.assertNotIn("per_order_cap", reloaded["rules"])
        self.assertIn("monthly_cap", reloaded["rules"])

    def test_paused_agent_is_stored(self) -> None:
        workspace = self.make_workspace()
        agent = self.make_agent(workspace["id"], status="paused")
        self.assertEqual(self.store.agent(workspace["id"], agent["id"])["status"], "paused")

    def test_deleting_workspace_cascades_agents_and_children(self) -> None:
        workspace = self.make_workspace()
        agent = self.make_agent(workspace["id"])
        self.store.execute("DELETE FROM workspaces WHERE id = ?", (workspace["id"],))
        self.assertIsNone(self.store.agent(workspace["id"], agent["id"]))
        self.assertEqual(
            self.store.query(
                "SELECT * FROM agent_vendors WHERE agent_id = ?", (agent["id"],)
            ),
            [],
        )
        self.assertEqual(
            self.store.query(
                "SELECT * FROM rule_overrides WHERE agent_id = ?", (agent["id"],)
            ),
            [],
        )


class WalletTests(StoreTestCase):
    def test_wallet_starts_disconnected_and_connects(self) -> None:
        workspace = self.make_workspace()
        self.assertIsNone(self.store.wallet(workspace["id"]))
        wallet = self.store.set_wallet(workspace["id"], status="disconnected")
        self.assertEqual(wallet["status"], "disconnected")
        self.assertIsNone(wallet["vault_id"])

        connected = self.store.set_wallet(
            workspace["id"],
            vault_id="vault-123",
            payer_email="payer@example.com",
            mode="sandbox",
            status="connected",
            connected_at="2026-10-22T05:00:00+00:00",
        )
        self.assertEqual(connected["vault_id"], "vault-123")
        self.assertEqual(connected["status"], "connected")
        self.assertEqual(connected["payer_email"], "payer@example.com")

    def test_wallet_update_merges_only_given_fields(self) -> None:
        workspace = self.make_workspace()
        self.store.set_wallet(workspace["id"], vault_id="vault-1", status="connected")
        updated = self.store.set_wallet(workspace["id"], payer_email="new@example.com")
        self.assertEqual(updated["vault_id"], "vault-1")
        self.assertEqual(updated["payer_email"], "new@example.com")
        self.assertEqual(updated["status"], "connected")

    def test_wallet_is_workspace_scoped(self) -> None:
        first = self.make_workspace("Acme")
        second = self.make_workspace("Globex")
        self.store.set_wallet(first["id"], vault_id="vault-a", status="connected")
        self.assertIsNone(self.store.wallet(second["id"]))


class SessionStoreTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        workspace = self.make_workspace()
        self.user = self.make_user(workspace["id"], "ops@example.com")
        self.future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(
            timespec="seconds"
        )
        self.past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(
            timespec="seconds"
        )

    def test_session_round_trip(self) -> None:
        session = self.store.create_session(
            self.user["id"], self.user["workspace_id"], "tok-hash", self.future
        )
        self.assertEqual(self.store.session_by_token_hash("tok-hash")["id"], session["id"])
        self.assertIsNone(self.store.session_by_token_hash("nope"))
        self.assertIsNone(session["last_seen_at"])

    def test_touch_session_moves_expiry(self) -> None:
        session = self.store.create_session(
            self.user["id"], self.user["workspace_id"], "tok-hash", self.future
        )
        self.store.touch_session(session["id"], "2999-01-01T00:00:00+00:00")
        touched = self.store.session_by_token_hash("tok-hash")
        self.assertEqual(touched["expires_at"], "2999-01-01T00:00:00+00:00")
        self.assertIsNotNone(touched["last_seen_at"])

    def test_delete_session(self) -> None:
        session = self.store.create_session(
            self.user["id"], self.user["workspace_id"], "tok-hash", self.future
        )
        self.assertTrue(self.store.delete_session(session["id"]))
        self.assertFalse(self.store.delete_session(session["id"]))
        self.assertIsNone(self.store.session_by_token_hash("tok-hash"))

    def test_delete_expired_sessions_only(self) -> None:
        expired = self.store.create_session(
            self.user["id"], self.user["workspace_id"], "expired-hash", self.past
        )
        live = self.store.create_session(
            self.user["id"], self.user["workspace_id"], "live-hash", self.future
        )
        self.assertEqual(self.store.delete_expired_sessions(), 1)
        self.assertIsNone(self.store.session_by_token_hash("expired-hash"))
        self.assertIsNotNone(self.store.session_by_token_hash("live-hash"))
        self.assertNotEqual(expired["id"], live["id"])


class ApiKeyStoreTests(StoreTestCase):
    def test_api_key_round_trip(self) -> None:
        workspace = self.make_workspace()
        agent = self.make_agent(workspace["id"])
        key = self.store.create_api_key(
            agent["id"], workspace["id"], "vkey_abcd1234", "key-hash"
        )
        self.assertEqual(key["prefix"], "vkey_abcd1234")
        self.assertIsNone(key["last_used_at"])
        found = self.store.api_key_by_hash("key-hash")
        self.assertEqual(found["id"], key["id"])
        self.assertEqual(found["agent_id"], agent["id"])
        self.store.touch_api_key(key["id"])
        self.assertIsNotNone(self.store.api_key_by_hash("key-hash")["last_used_at"])
        self.assertEqual(len(self.store.list_api_keys(workspace["id"])), 1)


class PersistenceTests(unittest.TestCase):
    def test_data_survives_store_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "veto.db"
            store = Store(db_path)
            workspace = store.create_workspace("Persisted Co", monthly_budget_cents=42_000)
            user = store.create_user(
                workspace["id"], "ops@persisted.test", name="Ops", password_hash="x", role="admin"
            )
            agent = store.create_agent(
                workspace["id"],
                dict(AGENT_FIELDS, name="Persisted agent"),
                vendors=["amazon.com"],
                categories=["pantry"],
                rules=["monthly_cap"],
                overrides=[],
            )
            store.set_wallet(workspace["id"], status="disconnected")
            store.close()

            reopened = Store(db_path)
            try:
                self.assertEqual(reopened.workspace(workspace["id"])["name"], "Persisted Co")
                self.assertEqual(reopened.user(user["id"])["email"], "ops@persisted.test")
                reloaded = reopened.agent(workspace["id"], agent["id"])
                self.assertEqual(reloaded["name"], "Persisted agent")
                self.assertEqual(reloaded["vendors"], ["amazon.com"])
                self.assertEqual(reopened.wallet(workspace["id"])["status"], "disconnected")
            finally:
                reopened.close()

    def test_memory_database_is_supported(self) -> None:
        store = Store(":memory:")
        try:
            self.assertIn("workspaces", store.table_names())
            workspace = store.create_workspace("Memory Co")
            self.assertEqual(store.workspace(workspace["id"])["name"], "Memory Co")
        finally:
            store.close()


class HelperTests(unittest.TestCase):
    def test_now_iso_parses(self) -> None:
        parsed = parse_iso("2026-10-22T05:00:00+00:00")
        self.assertEqual(parsed.year, 2026)
        self.assertIsNone(parse_iso(""))
        self.assertIsNone(parse_iso("not a date"))
        self.assertIsNone(parse_iso(None))


if __name__ == "__main__":
    unittest.main()
