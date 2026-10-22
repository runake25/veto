"""Auth unit tests: scrypt passwords, sessions, agent keys, cookies, roles."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from veto import auth
from veto.store import Store, parse_iso


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.user = self.store.create_user(
            self.workspace["id"],
            "priya@brackenfield.demo",
            name="Priya Raman",
            password_hash=auth.hash_password("veto-demo-2026"),
            role="approver",
        )
        self.agent = self.store.create_agent(
            self.workspace["id"],
            {"name": "Pantry runner", "tag": "pantry"},
            vendors=["amazon.com"],
            categories=["pantry"],
            rules=["monthly_cap"],
            overrides=[],
        )


class PasswordTests(unittest.TestCase):
    def test_hash_shape_and_verify(self) -> None:
        stored = auth.hash_password("correct horse battery staple")
        self.assertRegex(
            stored,
            r"^scrypt\$16384\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{64}$",
        )
        self.assertTrue(auth.verify_password(stored, "correct horse battery staple"))
        self.assertFalse(auth.verify_password(stored, "wrong password"))
        self.assertNotIn("correct horse", stored)

    def test_salted_hashes_differ(self) -> None:
        first = auth.hash_password("same password")
        second = auth.hash_password("same password")
        self.assertNotEqual(first, second)
        self.assertTrue(auth.verify_password(first, "same password"))
        self.assertTrue(auth.verify_password(second, "same password"))

    def test_unicode_password(self) -> None:
        stored = auth.hash_password("pässwörd-2026-✓")
        self.assertTrue(auth.verify_password(stored, "pässwörd-2026-✓"))
        self.assertFalse(auth.verify_password(stored, "passwort-2026"))

    def test_malformed_stored_hashes_are_rejected_without_raising(self) -> None:
        malformed = [
            "",
            "plaintext",
            "bcrypt$12$salt$hash",
            "scrypt$16384$8$1$only-five-parts",
            "scrypt$notanint$8$1$aa$bb",
            "scrypt$16384$8$1$zz$bb",  # invalid hex
            "scrypt$16384$8$1$aa$bb$cc",
            "$".join(["scrypt"] * 6),
        ]
        for stored in malformed:
            with self.subTest(stored=stored):
                self.assertFalse(auth.verify_password(stored, "anything"))
        self.assertFalse(auth.verify_password(None, "anything"))  # type: ignore[arg-type]
        self.assertFalse(auth.verify_password("scrypt$1$1$1$00$00", "anything"))


class SessionTests(AuthTestCase):
    def test_issue_session_stores_only_the_hash(self) -> None:
        token = auth.issue_session(self.store, self.user)
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        rows = self.store.query("SELECT * FROM sessions")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["token_hash"], sha256_hex(token))
        self.assertNotEqual(rows[0]["token_hash"], token)
        self.assertRegex(rows[0]["id"], r"^ses_[0-9a-f]{12}$")
        self.assertEqual(rows[0]["workspace_id"], self.workspace["id"])
        self.assertIsNone(rows[0]["last_seen_at"])

    def test_resolve_session_returns_user_workspace_and_touches(self) -> None:
        token = auth.issue_session(self.store, self.user)
        resolved = auth.resolve_session(self.store, token)
        self.assertIsNotNone(resolved)
        self.assertRegex(resolved["session_id"], r"^ses_")
        self.assertEqual(resolved["user"]["email"], "priya@brackenfield.demo")
        self.assertEqual(resolved["user"]["role"], "approver")
        self.assertEqual(resolved["workspace"]["name"], "Bracken & Field")
        row = self.store.session_by_token_hash(sha256_hex(token))
        self.assertIsNotNone(row["last_seen_at"])

    def test_slide_happens_at_most_once_an_hour(self) -> None:
        token = auth.issue_session(self.store, self.user)
        first = auth.resolve_session(self.store, token)
        row_after_first = self.store.session_by_token_hash(sha256_hex(token))
        expiry_after_first = row_after_first["expires_at"]

        second = auth.resolve_session(self.store, token)
        row_after_second = self.store.session_by_token_hash(sha256_hex(token))
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(row_after_second["expires_at"], expiry_after_first)

    def test_expired_session_is_rejected_and_deleted(self) -> None:
        token = auth.issue_session(self.store, self.user)
        token_hash = sha256_hex(token)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(
            timespec="seconds"
        )
        self.store.execute(
            "UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (past, token_hash)
        )
        self.assertIsNone(auth.resolve_session(self.store, token))
        self.assertIsNone(self.store.session_by_token_hash(token_hash))

    def test_unknown_and_empty_tokens(self) -> None:
        self.assertIsNone(auth.resolve_session(self.store, "0" * 64))
        self.assertIsNone(auth.resolve_session(self.store, ""))
        self.assertIsNone(auth.resolve_session(self.store, None))

    def test_revoke_session(self) -> None:
        token = auth.issue_session(self.store, self.user)
        self.assertTrue(auth.revoke_session(self.store, token))
        self.assertIsNone(auth.resolve_session(self.store, token))
        self.assertFalse(auth.revoke_session(self.store, token))
        self.assertFalse(auth.revoke_session(self.store, None))

    def test_sessions_expire_in_seven_days(self) -> None:
        auth.issue_session(self.store, self.user)
        row = self.store.query("SELECT expires_at FROM sessions")[0]
        expires = parse_iso(row["expires_at"])
        delta = expires - datetime.now(timezone.utc)
        self.assertGreater(delta.total_seconds(), 6 * 24 * 3600)
        self.assertLessEqual(delta.total_seconds(), 7 * 24 * 3600 + 5)


class ApiKeyTests(AuthTestCase):
    def test_mint_and_resolve(self) -> None:
        key, record = auth.mint_api_key(self.store, self.agent)
        self.assertRegex(key, r"^vkey_[0-9a-f]{32}$")
        self.assertEqual(record["prefix"], key[:13])
        self.assertNotIn("key_hash", record)
        stored = self.store.api_key_by_hash(sha256_hex(key))
        self.assertIsNotNone(stored)
        self.assertNotIn(key, str(stored))

        resolved = auth.resolve_api_key(self.store, key)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["workspace_id"], self.workspace["id"])
        self.assertEqual(resolved["agent"]["id"], self.agent["id"])
        self.assertEqual(resolved["agent"]["name"], "Pantry runner")
        self.assertIsNotNone(
            self.store.api_key_by_hash(sha256_hex(key))["last_used_at"]
        )

    def test_each_minted_key_is_unique(self) -> None:
        first, _ = auth.mint_api_key(self.store, self.agent)
        second, _ = auth.mint_api_key(self.store, self.agent)
        self.assertNotEqual(first, second)
        self.assertIsNotNone(auth.resolve_api_key(self.store, first))
        self.assertIsNotNone(auth.resolve_api_key(self.store, second))

    def test_revoked_key_is_ignored(self) -> None:
        key, _ = auth.mint_api_key(self.store, self.agent)
        self.store.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE key_hash = ?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), sha256_hex(key)),
        )
        self.assertIsNone(auth.resolve_api_key(self.store, key))

    def test_unknown_and_malformed_keys(self) -> None:
        self.assertIsNone(auth.resolve_api_key(self.store, "vkey_" + "0" * 32))
        self.assertIsNone(auth.resolve_api_key(self.store, "not-a-key"))
        self.assertIsNone(auth.resolve_api_key(self.store, ""))
        self.assertIsNone(auth.resolve_api_key(self.store, None))

    def test_key_for_deleted_agent_is_ignored(self) -> None:
        key, _ = auth.mint_api_key(self.store, self.agent)
        self.store.execute("DELETE FROM agents WHERE id = ?", (self.agent["id"],))
        self.assertIsNone(auth.resolve_api_key(self.store, key))


class CookieTests(unittest.TestCase):
    def test_parse_cookies(self) -> None:
        header = "theme=dark; veto_session=abc123; empty=; odd"
        parsed = auth.parse_cookies(header)
        self.assertEqual(parsed["theme"], "dark")
        self.assertEqual(parsed["veto_session"], "abc123")
        self.assertEqual(parsed["empty"], "")
        self.assertEqual(auth.parse_cookies(None), {})
        self.assertEqual(auth.parse_cookies(""), {})
        self.assertEqual(auth.parse_cookies("=novalue"), {})

    def test_session_cookie_flags(self) -> None:
        cookie = auth.session_cookie("tok")
        self.assertTrue(cookie.startswith("veto_session=tok; Path=/; HttpOnly; SameSite=Lax"))
        self.assertIn("Max-Age=604800", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertIn("; Secure", auth.session_cookie("tok", secure=True))
        self.assertIn("Max-Age=60", auth.session_cookie("tok", max_age=60))

    def test_clear_cookie_expires_immediately(self) -> None:
        cookie = auth.clear_cookie()
        self.assertTrue(cookie.startswith("veto_session=; Path=/; HttpOnly; SameSite=Lax"))
        self.assertIn("Max-Age=0", cookie)
        self.assertIn("; Secure", auth.clear_cookie(secure=True))

    def test_cookie_name_constant(self) -> None:
        self.assertEqual(auth.COOKIE_NAME, "veto_session")


class RoleTests(unittest.TestCase):
    def test_require_role(self) -> None:
        admin = {"role": "admin"}
        approver = {"role": "approver"}
        viewer = {"role": "viewer"}
        self.assertEqual(auth.require_role(admin, "admin"), admin)
        self.assertEqual(auth.require_role(admin, "admin", "approver"), admin)
        self.assertEqual(auth.require_role(viewer, "admin", "viewer"), viewer)
        with self.assertRaises(PermissionError):
            auth.require_role(approver, "admin")
        with self.assertRaises(PermissionError):
            auth.require_role(None, "admin")
        with self.assertRaises(PermissionError):
            auth.require_role({"role": "owner"}, "admin")

    def test_valid_roles_constant(self) -> None:
        self.assertEqual(auth.VALID_ROLES, ("admin", "approver", "viewer"))


if __name__ == "__main__":
    unittest.main()
