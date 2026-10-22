"""Veto store: one SQLite file behind a single, lock-guarded connection.

Everything the product knows lives here: workspaces, the humans who own them,
sessions, agents and their charters, and (schema-only for now) the request /
payment / event history that the M2 pipeline will write.

Conventions, applied everywhere:
  * timestamps are ISO-8601 UTC with second precision
    (``2026-10-22T05:00:00+00:00``), so they sort lexicographically;
  * money is integer cents, never floats;
  * ids are prefixed hex strings generated with ``secrets.token_hex``.

Only ``sqlite3`` and the standard library are used.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / ".state" / "veto.db"
SCHEMA_VERSION = "1"

# Role names mirror auth.VALID_ROLES. The CHECK constraints below are the
# final authority; this tuple gives callers a readable error before SQLite does.
ROLES = ("admin", "approver", "viewer")

# Columns a caller may change through update_workspace().
WORKSPACE_UPDATABLE = (
    "name",
    "currency",
    "monthly_budget_cents",
    "default_agent_id",
    "slack_team_id",
)

_AGENT_FIELDS = (
    "name",
    "tag",
    "channel",
    "status",
    "monthly_cap_cents",
    "per_order_cap_cents",
    "per_unit_cap_cents",
    "approval_threshold_cents",
)

_MEMORY_PATHS = (":", ":memory:")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    """Current time as the one timestamp format we store anywhere."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    """Parse a stored timestamp; return None for empty / unparseable values."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def new_id(prefix: str, nbytes: int = 6) -> str:
    """``ws_`` + 12 hex / ``agt_`` + 8 hex, per the id prefix table."""
    return prefix + secrets.token_hex(nbytes)


def _is_memory_path(path: str) -> bool:
    return path in _MEMORY_PATHS


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL UNIQUE,
    currency             TEXT NOT NULL DEFAULT 'USD',
    monthly_budget_cents INTEGER NOT NULL DEFAULT 0,
    default_agent_id     TEXT,
    slack_team_id        TEXT,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wallets (
    workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    vault_id     TEXT,
    payer_email  TEXT,
    mode         TEXT,
    status       TEXT NOT NULL DEFAULT 'disconnected',
    connected_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'approver', 'viewer')),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id  TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    last_seen_at  TEXT
);

CREATE TABLE IF NOT EXISTS agents (
    id                       TEXT PRIMARY KEY,
    workspace_id             TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name                     TEXT NOT NULL,
    tag                      TEXT NOT NULL DEFAULT 'general',
    channel                  TEXT,
    status                   TEXT NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active', 'paused')),
    monthly_cap_cents        INTEGER,
    per_order_cap_cents      INTEGER,
    per_unit_cap_cents       INTEGER,
    approval_threshold_cents INTEGER,
    created_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_vendors (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    domain   TEXT NOT NULL,
    PRIMARY KEY (agent_id, domain)
);

CREATE TABLE IF NOT EXISTS agent_categories (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    PRIMARY KEY (agent_id, category)
);

CREATE TABLE IF NOT EXISTS agent_rules (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,
    enabled  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (agent_id, kind)
);

CREATE TABLE IF NOT EXISTS rule_overrides (
    id            TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    category      TEXT NOT NULL,
    kind          TEXT NOT NULL,
    value_cents   INTEGER NOT NULL,
    UNIQUE (agent_id, category, kind)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    prefix       TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);

CREATE TABLE IF NOT EXISTS requests (
    id                 TEXT PRIMARY KEY,
    seq                INTEGER NOT NULL UNIQUE,
    workspace_id       TEXT NOT NULL,
    agent_id           TEXT NOT NULL,
    origin             TEXT NOT NULL
                       CHECK (origin IN ('api', 'slack', 'web', 'web_sim', 'seed')),
    channel            TEXT,
    requester          TEXT,
    text               TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL,
    decision_outcome   TEXT,
    subtotal_cents     INTEGER NOT NULL DEFAULT 0,
    total_cents        INTEGER NOT NULL DEFAULT 0,
    currency           TEXT NOT NULL DEFAULT 'USD',
    initiator_user_id  TEXT,
    approver_user_id   TEXT,
    created_at         TEXT NOT NULL,
    decided_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_ws_created
    ON requests (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_requests_ws_status
    ON requests (workspace_id, status);

CREATE TABLE IF NOT EXISTS request_lines (
    id          TEXT PRIMARY KEY,
    request_id  TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    sku         TEXT,
    title       TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    unit_cents  INTEGER NOT NULL,
    line_cents  INTEGER NOT NULL,
    vendor      TEXT,
    category    TEXT,
    brand       TEXT,
    product_url TEXT,
    image_url   TEXT,
    source      TEXT
);

CREATE TABLE IF NOT EXISTS request_reasons (
    id             TEXT PRIMARY KEY,
    request_id     TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    rule           TEXT NOT NULL,
    severity       TEXT NOT NULL,
    message        TEXT NOT NULL,
    observed_cents INTEGER,
    limit_cents    INTEGER,
    unblock        TEXT
);

CREATE TABLE IF NOT EXISTS consents (
    id               TEXT PRIMARY KEY,
    request_id       TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    cart_hash        TEXT NOT NULL,
    nonce            TEXT NOT NULL UNIQUE,
    issued_at        TEXT,
    expires_at       TEXT,
    used_at          TEXT,
    approver_user_id TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id               TEXT PRIMARY KEY,
    request_id       TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    mode             TEXT NOT NULL,
    vault_id         TEXT,
    order_id         TEXT,
    authorization_id TEXT,
    capture_id       TEXT,
    status           TEXT NOT NULL,
    amount_cents     INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    label      TEXT NOT NULL,
    detail     TEXT,
    href       TEXT,
    at         TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
class Store:
    """A thread-safe SQLite store. One connection, one RLock, autocommit.

    ``Store()`` uses ``$VETO_DB`` or ``<repo>/.state/veto.db``. Pass
    ``":memory:"`` for a throwaway database (tests).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        chosen = path if path is not None else os.environ.get("VETO_DB") or DEFAULT_DB_PATH
        self.db_path = str(chosen)
        if not _is_memory_path(self.db_path):
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tx_depth = 0
        self._closed = False
        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,  # explicit transactions only; DML autocommits
            timeout=5.0,
        )
        self.conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self.init_schema()

    # -- lifecycle ------------------------------------------------------
    def _apply_pragmas(self) -> None:
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=5000")

    def init_schema(self) -> None:
        """Create every table/index idempotently and stamp the schema version."""
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.conn.close()
                self._closed = True

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator["Store"]:
        """Serialise a multi-statement write. Safe to call twice on one thread."""
        with self._lock:
            if self._tx_depth:
                self._tx_depth += 1
                try:
                    yield self
                finally:
                    self._tx_depth -= 1
                return
            self.conn.execute("BEGIN IMMEDIATE")
            self._tx_depth = 1
            try:
                yield self
            except BaseException:
                self._tx_depth = 0
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:  # connection already gone; nothing to undo
                    pass
                raise
            else:
                self._tx_depth = 0
                self.conn.execute("COMMIT")

    # -- primitives -----------------------------------------------------
    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self._lock:
            cur = self.conn.execute(sql, params)
            try:
                return [dict(row) for row in cur.fetchall()]
            finally:
                cur.close()

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        with self._lock:
            cur = self.conn.execute(sql, params)
            try:
                row = cur.fetchone()
                return dict(row) if row is not None else None
            finally:
                cur.close()

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        """Run one statement; with autocommit the write is durable on return."""
        with self._lock:
            return self.conn.execute(sql, params)

    def table_names(self) -> list[str]:
        return [
            row["name"]
            for row in self.query(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

    # -- meta -----------------------------------------------------------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- workspaces -----------------------------------------------------
    def create_workspace(
        self,
        name: str,
        currency: str = "USD",
        monthly_budget_cents: int = 0,
        default_agent_id: str | None = None,
        slack_team_id: str | None = None,
    ) -> dict:
        clean = (name or "").strip()
        if not clean:
            raise ValueError("workspace name is required")
        workspace_id = new_id("ws_")
        self.execute(
            "INSERT INTO workspaces "
            "(id, name, currency, monthly_budget_cents, default_agent_id, slack_team_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                workspace_id,
                clean,
                currency or "USD",
                int(monthly_budget_cents or 0),
                default_agent_id,
                slack_team_id,
                now_iso(),
            ),
        )
        return self.workspace(workspace_id) or {}

    def workspace(self, workspace_id: str) -> dict | None:
        return self.one("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))

    def workspace_by_name(self, name: str) -> dict | None:
        return self.one("SELECT * FROM workspaces WHERE name = ?", (name,))

    def list_workspaces(self) -> list[dict]:
        return self.query("SELECT * FROM workspaces ORDER BY created_at, rowid")

    def update_workspace(self, workspace_id: str, **fields: Any) -> dict | None:
        unknown = set(fields) - set(WORKSPACE_UPDATABLE)
        if unknown:
            raise ValueError(f"unknown workspace fields: {', '.join(sorted(unknown))}")
        if not fields:
            return self.workspace(workspace_id)
        for key in ("monthly_budget_cents",):
            if key in fields and fields[key] is not None:
                fields[key] = int(fields[key])
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [workspace_id]
        self.execute(f"UPDATE workspaces SET {assignments} WHERE id = ?", params)
        return self.workspace(workspace_id)

    def count_workspaces(self) -> int:
        row = self.one("SELECT COUNT(*) AS n FROM workspaces")
        return int(row["n"]) if row else 0

    # -- users ----------------------------------------------------------
    def create_user(
        self,
        workspace_id: str,
        email: str,
        name: str = "",
        password_hash: str = "",
        role: str = "admin",
    ) -> dict:
        clean_email = (email or "").strip().lower()
        if not clean_email:
            raise ValueError("email is required")
        if role not in ROLES:
            raise ValueError(f"invalid role: {role}")
        user_id = new_id("usr_")
        self.execute(
            "INSERT INTO users (id, workspace_id, email, name, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, workspace_id, clean_email, (name or "").strip(), password_hash, role, now_iso()),
        )
        return self.user(user_id) or {}

    def user(self, user_id: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE id = ?", (user_id,))

    def user_by_email(self, email: str) -> dict | None:
        return self.one(
            "SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)
        )

    def list_users(self, workspace_id: str) -> list[dict]:
        return self.query(
            "SELECT * FROM users WHERE workspace_id = ? ORDER BY created_at, rowid",
            (workspace_id,),
        )

    def count_users(self, workspace_id: str | None = None) -> int:
        if workspace_id is None:
            row = self.one("SELECT COUNT(*) AS n FROM users")
        else:
            row = self.one(
                "SELECT COUNT(*) AS n FROM users WHERE workspace_id = ?", (workspace_id,)
            )
        return int(row["n"]) if row else 0

    # -- sessions -------------------------------------------------------
    def create_session(
        self,
        user_id: str,
        workspace_id: str,
        token_hash: str,
        expires_at: str,
        created_at: str | None = None,
    ) -> dict:
        session_id = new_id("ses_")
        self.execute(
            "INSERT INTO sessions "
            "(id, user_id, workspace_id, token_hash, created_at, expires_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (session_id, user_id, workspace_id, token_hash, created_at or now_iso(), expires_at),
        )
        return self.one("SELECT * FROM sessions WHERE id = ?", (session_id,)) or {}

    def session_by_token_hash(self, token_hash: str) -> dict | None:
        return self.one(
            "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
        )

    def touch_session(self, session_id: str, expires_at: str) -> None:
        self.execute(
            "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
            (now_iso(), expires_at, session_id),
        )

    def delete_session(self, session_id: str) -> bool:
        cur = self.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0

    def delete_expired_sessions(self) -> int:
        cur = self.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
        return cur.rowcount

    # -- agents ---------------------------------------------------------
    def create_agent(
        self,
        workspace_id: str,
        fields: dict,
        vendors: list[str] | None = None,
        categories: list[str] | None = None,
        rules: list[str] | None = None,
        overrides: list[dict] | None = None,
    ) -> dict:
        name = str(fields.get("name") or "").strip()
        if not name:
            raise ValueError("agent name is required")
        status = fields.get("status") or "active"
        if status not in ("active", "paused"):
            raise ValueError(f"invalid agent status: {status}")
        agent_id = new_id("agt_", 4)
        values = {
            "name": name,
            "tag": str(fields.get("tag") or "general"),
            "channel": fields.get("channel"),
            "status": status,
            "monthly_cap_cents": fields.get("monthly_cap_cents"),
            "per_order_cap_cents": fields.get("per_order_cap_cents"),
            "per_unit_cap_cents": fields.get("per_unit_cap_cents"),
            "approval_threshold_cents": fields.get("approval_threshold_cents"),
        }
        with self.transaction():
            self.execute(
                "INSERT INTO agents (id, workspace_id, name, tag, channel, status, "
                "monthly_cap_cents, per_order_cap_cents, per_unit_cap_cents, "
                "approval_threshold_cents, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    workspace_id,
                    values["name"],
                    values["tag"],
                    values["channel"],
                    values["status"],
                    values["monthly_cap_cents"],
                    values["per_order_cap_cents"],
                    values["per_unit_cap_cents"],
                    values["approval_threshold_cents"],
                    now_iso(),
                ),
            )
            for domain in dict.fromkeys(vendors or []):
                self.execute(
                    "INSERT OR IGNORE INTO agent_vendors (agent_id, domain) VALUES (?, ?)",
                    (agent_id, str(domain)),
                )
            for category in dict.fromkeys(categories or []):
                self.execute(
                    "INSERT OR IGNORE INTO agent_categories (agent_id, category) VALUES (?, ?)",
                    (agent_id, str(category)),
                )
            for kind in dict.fromkeys(rules or []):
                self.execute(
                    "INSERT OR IGNORE INTO agent_rules (agent_id, kind, enabled) VALUES (?, ?, 1)",
                    (agent_id, str(kind)),
                )
            for override in overrides or []:
                self.execute(
                    "INSERT INTO rule_overrides (id, agent_id, category, kind, value_cents) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        new_id("rov_", 4),
                        agent_id,
                        str(override.get("category") or ""),
                        str(override.get("kind") or ""),
                        int(override.get("value_cents") or 0),
                    ),
                )
        return self.agent(workspace_id, agent_id) or {}

    def _agent_dict(self, row: sqlite3.Row | dict) -> dict:
        agent = dict(row)
        agent_id = agent["id"]
        agent["vendors"] = [
            item["domain"]
            for item in self.query(
                "SELECT domain FROM agent_vendors WHERE agent_id = ? ORDER BY rowid",
                (agent_id,),
            )
        ]
        agent["categories"] = [
            item["category"]
            for item in self.query(
                "SELECT category FROM agent_categories WHERE agent_id = ? ORDER BY rowid",
                (agent_id,),
            )
        ]
        agent["rules"] = [
            item["kind"]
            for item in self.query(
                "SELECT kind FROM agent_rules WHERE agent_id = ? AND enabled = 1 ORDER BY rowid",
                (agent_id,),
            )
        ]
        agent["overrides"] = [
            {
                "category": item["category"],
                "kind": item["kind"],
                "value_cents": item["value_cents"],
            }
            for item in self.query(
                "SELECT category, kind, value_cents FROM rule_overrides "
                "WHERE agent_id = ? ORDER BY rowid",
                (agent_id,),
            )
        ]
        return agent

    def agent(self, workspace_id: str, agent_id: str) -> dict | None:
        row = self.one(
            "SELECT * FROM agents WHERE workspace_id = ? AND id = ?",
            (workspace_id, agent_id),
        )
        return self._agent_dict(row) if row is not None else None

    def list_agents(self, workspace_id: str) -> list[dict]:
        rows = self.query(
            "SELECT * FROM agents WHERE workspace_id = ? ORDER BY rowid", (workspace_id,)
        )
        return [self._agent_dict(row) for row in rows]

    def count_agents(self, workspace_id: str | None = None) -> int:
        if workspace_id is None:
            row = self.one("SELECT COUNT(*) AS n FROM agents")
        else:
            row = self.one(
                "SELECT COUNT(*) AS n FROM agents WHERE workspace_id = ?", (workspace_id,)
            )
        return int(row["n"]) if row else 0

    # -- wallet ---------------------------------------------------------
    def set_wallet(
        self,
        workspace_id: str,
        vault_id: str | None = None,
        payer_email: str | None = None,
        mode: str | None = None,
        status: str | None = None,
        connected_at: str | None = None,
    ) -> dict:
        """Insert or merge the single wallet row for a workspace."""
        existing = self.wallet(workspace_id)
        if existing is None:
            self.execute(
                "INSERT INTO wallets (workspace_id, vault_id, payer_email, mode, status, connected_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    vault_id,
                    payer_email,
                    mode,
                    status or "disconnected",
                    connected_at,
                ),
            )
        else:
            updates: dict[str, Any] = {}
            for key, value in (
                ("vault_id", vault_id),
                ("payer_email", payer_email),
                ("mode", mode),
                ("status", status),
                ("connected_at", connected_at),
            ):
                if value is not None:
                    updates[key] = value
            if updates:
                assignments = ", ".join(f"{key} = ?" for key in updates)
                self.execute(
                    f"UPDATE wallets SET {assignments} WHERE workspace_id = ?",
                    list(updates.values()) + [workspace_id],
                )
        return self.wallet(workspace_id) or {}

    def wallet(self, workspace_id: str) -> dict | None:
        return self.one("SELECT * FROM wallets WHERE workspace_id = ?", (workspace_id,))

    # -- api keys -------------------------------------------------------
    def create_api_key(
        self,
        agent_id: str,
        workspace_id: str,
        prefix: str,
        key_hash: str,
    ) -> dict:
        key_id = new_id("key_", 4)
        self.execute(
            "INSERT INTO api_keys (id, agent_id, workspace_id, prefix, key_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key_id, agent_id, workspace_id, prefix, key_hash, now_iso()),
        )
        return self.one("SELECT * FROM api_keys WHERE id = ?", (key_id,)) or {}

    def api_key_by_hash(self, key_hash: str) -> dict | None:
        return self.one("SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,))

    def touch_api_key(self, key_id: str) -> None:
        self.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now_iso(), key_id)
        )

    def list_api_keys(self, workspace_id: str) -> list[dict]:
        return self.query(
            "SELECT * FROM api_keys WHERE workspace_id = ? ORDER BY created_at, rowid",
            (workspace_id,),
        )
