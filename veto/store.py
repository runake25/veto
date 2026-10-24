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
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / ".state" / "veto.db"
SCHEMA_VERSION = "1"

# One analytics call renders one series point per day; refuse to build an
# unbounded series from an accidental 20-year range.
MAX_ANALYTICS_DAYS = 366

# The exact aggregate fields of the analytics read model. Kept here so the
# shape lives in one place and every SQL row is projected through them.
_ANALYTICS_TOTALS = (
    "requests", "captured", "awaiting", "approved", "blocked", "vetoed", "voided",
    "captured_cents", "pending_cents", "blocked_cents",
    "auto", "review", "block",
)
_ANALYTICS_AGENT_KEYS = (
    "requests", "captured", "captured_cents", "pending", "pending_cents",
    "blocked", "vetoed",
)

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

# Scalar agent columns a caller may change through update_agent(). The child
# lists (vendors/categories/rules/overrides) are replaced through their own
# keyword arguments.
AGENT_UPDATABLE = (
    "name",
    "tag",
    "channel",
    "status",
    "monthly_cap_cents",
    "per_order_cap_cents",
    "per_unit_cap_cents",
    "approval_threshold_cents",
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


def month_start_iso(moment: datetime | None = None) -> str:
    """The first instant of the current UTC month — one boundary for every screen."""
    moment = moment or datetime.now(timezone.utc)
    return moment.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")


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


def _analytics_days(start_dt: datetime, end_dt: datetime) -> list[date]:
    """Every UTC calendar day touched by ``[start_dt, end_dt)``, ascending.

    A window that ends exactly at UTC midnight does not include the new day, so
    the series has no trailing empty point. Raises ``ValueError`` when the span
    is wider than ``MAX_ANALYTICS_DAYS``.
    """
    start_day = start_dt.astimezone(timezone.utc).date()
    end_day = end_dt.astimezone(timezone.utc).date()
    end_utc = end_dt.astimezone(timezone.utc)
    if end_utc == datetime.combine(end_day, time.min, tzinfo=timezone.utc):
        last_day = end_day - timedelta(days=1)
    else:
        last_day = end_day
    count = (last_day - start_day).days + 1
    if count > MAX_ANALYTICS_DAYS:
        raise ValueError(f"range must cover {MAX_ANALYTICS_DAYS} days or fewer")
    return [start_day + timedelta(days=offset) for offset in range(count)]


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

    def update_agent(
        self,
        workspace_id: str,
        agent_id: str,
        *,
        fields: dict | None = None,
        vendors: list[str] | None = None,
        categories: list[str] | None = None,
        rules: list[str] | None = None,
        overrides: list[dict] | None = None,
    ) -> dict | None:
        """Update one agent's scalars and/or replace the child lists given.

        ``None`` means "leave this child list untouched". Returns the assembled
        agent, or ``None`` when the agent does not belong to the workspace.
        """
        if self.agent(workspace_id, agent_id) is None:
            return None
        updates = dict(fields or {})
        unknown = set(updates) - set(AGENT_UPDATABLE)
        if unknown:
            raise ValueError(f"unknown agent fields: {', '.join(sorted(unknown))}")
        if "name" in updates:
            updates["name"] = str(updates["name"] or "").strip()
            if not updates["name"]:
                raise ValueError("agent name is required")
        if "status" in updates and updates["status"] not in ("active", "paused"):
            raise ValueError(f"invalid agent status: {updates['status']}")
        for cap in ("monthly_cap_cents", "per_order_cap_cents",
                    "per_unit_cap_cents", "approval_threshold_cents"):
            if cap in updates and updates[cap] is not None:
                updates[cap] = int(updates[cap])

        with self.transaction():
            if updates:
                assignments = ", ".join(f"{key} = ?" for key in updates)
                self.execute(
                    f"UPDATE agents SET {assignments} WHERE workspace_id = ? AND id = ?",
                    list(updates.values()) + [workspace_id, agent_id],
                )
            if vendors is not None:
                self.execute("DELETE FROM agent_vendors WHERE agent_id = ?", (agent_id,))
                for domain in dict.fromkeys(vendors):
                    self.execute(
                        "INSERT OR IGNORE INTO agent_vendors (agent_id, domain) VALUES (?, ?)",
                        (agent_id, str(domain)),
                    )
            if categories is not None:
                self.execute("DELETE FROM agent_categories WHERE agent_id = ?", (agent_id,))
                for category in dict.fromkeys(categories):
                    self.execute(
                        "INSERT OR IGNORE INTO agent_categories (agent_id, category) VALUES (?, ?)",
                        (agent_id, str(category)),
                    )
            if rules is not None:
                self.execute("DELETE FROM agent_rules WHERE agent_id = ?", (agent_id,))
                for kind in dict.fromkeys(rules):
                    self.execute(
                        "INSERT OR IGNORE INTO agent_rules (agent_id, kind, enabled) "
                        "VALUES (?, ?, 1)",
                        (agent_id, str(kind)),
                    )
            if overrides is not None:
                self.execute("DELETE FROM rule_overrides WHERE agent_id = ?", (agent_id,))
                for override in overrides:
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
        return self.agent(workspace_id, agent_id)

    def delete_agent(self, workspace_id: str, agent_id: str) -> bool:
        """Hard delete. Charter children cascade through foreign keys."""
        cur = self.execute(
            "DELETE FROM agents WHERE workspace_id = ? AND id = ?", (workspace_id, agent_id)
        )
        return cur.rowcount > 0

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

    def list_api_keys(self, workspace_id: str, agent_id: str | None = None) -> list[dict]:
        if agent_id is None:
            return self.query(
                "SELECT * FROM api_keys WHERE workspace_id = ? ORDER BY created_at, rowid",
                (workspace_id,),
            )
        return self.query(
            "SELECT * FROM api_keys WHERE workspace_id = ? AND agent_id = ? "
            "ORDER BY created_at, rowid",
            (workspace_id, agent_id),
        )

    def revoke_api_key(self, workspace_id: str, key_id: str) -> bool:
        """Soft-revoke: the row stays for the audit trail, the key stops resolving."""
        row = self.one(
            "SELECT id FROM api_keys WHERE workspace_id = ? AND id = ?",
            (workspace_id, key_id),
        )
        if row is None:
            return False
        self.execute(
            "UPDATE api_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
            (now_iso(), key_id),
        )
        return True

    # -- requests: the record ------------------------------------------
    # Request ids keep the old console's shape (REQ-1041) so a demo thread and
    # an inbox row read as the same object. The counter lives in `meta`, inside
    # the same transaction as the insert, so two threads cannot mint one seq.
    REQUEST_SEQ_START = 1040

    def _next_request_seq(self) -> int:
        row = self.one("SELECT value FROM meta WHERE key = 'request_seq'")
        seq = int(row["value"]) + 1 if row else self.REQUEST_SEQ_START + 1
        self.execute(
            "INSERT INTO meta (key, value) VALUES ('request_seq', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(seq),),
        )
        return seq

    def create_request(
        self,
        workspace_id: str,
        agent_id: str,
        *,
        origin: str,
        status: str,
        title: str = "",
        text: str = "",
        channel: str | None = None,
        requester: str | None = None,
        decision_outcome: str | None = None,
        subtotal_cents: int = 0,
        total_cents: int = 0,
        currency: str = "USD",
        initiator_user_id: str | None = None,
        lines: list[dict] | None = None,
        reasons: list[dict] | None = None,
        events: list[dict] | None = None,
        consent: dict | None = None,
    ) -> dict:
        """Write one request and everything it owns. Returns the assembled record."""
        created = now_iso()
        with self.transaction():
            seq = self._next_request_seq()
            request_id = f"REQ-{seq}"
            self.execute(
                "INSERT INTO requests (id, seq, workspace_id, agent_id, origin, channel, "
                "requester, text, title, status, decision_outcome, subtotal_cents, "
                "total_cents, currency, initiator_user_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    seq,
                    workspace_id,
                    agent_id,
                    origin,
                    channel,
                    requester,
                    text,
                    title,
                    status,
                    decision_outcome,
                    int(subtotal_cents),
                    int(total_cents),
                    currency,
                    initiator_user_id,
                    created,
                ),
            )
            for idx, line in enumerate(lines or []):
                self.execute(
                    "INSERT INTO request_lines (id, request_id, idx, sku, title, qty, "
                    "unit_cents, line_cents, vendor, category, brand, product_url, "
                    "image_url, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("ln_", 4),
                        request_id,
                        idx,
                        line.get("sku"),
                        line.get("title") or "",
                        max(1, int(line.get("qty") or 1)),
                        int(line.get("unit_cents") or 0),
                        int(line.get("line_cents") or 0),
                        line.get("vendor"),
                        line.get("category"),
                        line.get("brand"),
                        line.get("url") or line.get("product_url"),
                        line.get("image") or line.get("image_url"),
                        line.get("source"),
                    ),
                )
            for reason in reasons or []:
                self.execute(
                    "INSERT INTO request_reasons (id, request_id, rule, severity, message, "
                    "observed_cents, limit_cents, unblock) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("rsn_", 4),
                        request_id,
                        reason.get("rule") or "unknown",
                        reason.get("severity") or "block",
                        reason.get("message") or "",
                        reason.get("observed_cents"),
                        reason.get("limit_cents"),
                        reason.get("unblock"),
                    ),
                )
            if consent:
                self.execute(
                    "INSERT INTO consents (id, request_id, cart_hash, nonce, issued_at, "
                    "expires_at, used_at, approver_user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("cns_", 4),
                        request_id,
                        consent.get("cart_hash") or "",
                        consent.get("nonce") or "",
                        consent.get("issued_at") or created,
                        consent.get("expires_at"),
                        consent.get("used_at"),
                        consent.get("approver_user_id"),
                    ),
                )
            for event in events or []:
                self.add_event(
                    request_id,
                    event.get("kind") or "event",
                    event.get("label") or "",
                    detail=event.get("detail"),
                    href=event.get("href"),
                    at=created,
                )
        return self.request(workspace_id, request_id) or {}

    def request(self, workspace_id: str, request_id: str) -> dict | None:
        """One request with its lines, reasons, consents, payments, events and agent."""
        row = self.one(
            "SELECT * FROM requests WHERE workspace_id = ? AND id = ?",
            (workspace_id, request_id),
        )
        if row is None:
            return None
        row["lines"] = self.query(
            "SELECT * FROM request_lines WHERE request_id = ? ORDER BY idx", (request_id,)
        )
        row["reasons"] = self.query(
            "SELECT * FROM request_reasons WHERE request_id = ? ORDER BY rowid", (request_id,)
        )
        row["consents"] = self.query(
            "SELECT * FROM consents WHERE request_id = ? ORDER BY issued_at, rowid",
            (request_id,),
        )
        row["payments"] = self.query(
            "SELECT * FROM payments WHERE request_id = ? ORDER BY created_at, rowid",
            (request_id,),
        )
        row["events"] = self.query(
            "SELECT * FROM events WHERE request_id = ? ORDER BY seq", (request_id,)
        )
        row["agent"] = self.agent(workspace_id, row["agent_id"])
        return row

    def list_requests(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        agent_id: str | None = None,
        before: str | None = None,
        limit: int = 50,
        offset: int = 0,
        oldest_first: bool = False,
    ) -> list[dict]:
        """Request summaries, newest first, with the agent's name and tag joined in."""
        sql = (
            "SELECT r.id, r.seq, r.title, r.status, r.decision_outcome, r.total_cents, "
            "r.currency, r.channel, r.requester, r.origin, r.created_at, r.decided_at, "
            "r.agent_id, a.name AS agent_name, a.tag AS agent_tag, "
            "(SELECT COUNT(*) FROM request_lines l WHERE l.request_id = r.id) AS line_count "
            "FROM requests r LEFT JOIN agents a ON a.id = r.agent_id "
            "WHERE r.workspace_id = ?"
        )
        params: list[Any] = [workspace_id]
        if status:
            sql += " AND r.status = ?"
            params.append(status)
        if agent_id:
            sql += " AND r.agent_id = ?"
            params.append(agent_id)
        if before:
            sql += " AND r.created_at < ?"
            params.append(before)
        order = "ASC" if oldest_first else "DESC"
        sql += f" ORDER BY r.created_at {order}, r.seq {order} LIMIT ? OFFSET ?"
        params.extend([max(1, min(200, int(limit))), max(0, int(offset))])
        return self.query(sql, params)

    def count_requests(self, workspace_id: str, status: str | None = None) -> int:
        if status is None:
            row = self.one(
                "SELECT COUNT(*) AS n FROM requests WHERE workspace_id = ?", (workspace_id,)
            )
        else:
            row = self.one(
                "SELECT COUNT(*) AS n FROM requests WHERE workspace_id = ? AND status = ?",
                (workspace_id, status),
            )
        return int(row["n"]) if row else 0

    def spent_cents(
        self,
        workspace_id: str,
        *,
        agent_id: str | None = None,
        since: str | None = None,
    ) -> int:
        """Settled money (captured), optionally for one agent and after a timestamp."""
        sql = (
            "SELECT COALESCE(SUM(total_cents), 0) AS n FROM requests "
            "WHERE workspace_id = ? AND status = 'captured'"
        )
        params: list[Any] = [workspace_id]
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        row = self.one(sql, params)
        return int(row["n"]) if row else 0

    def committed_cents(self, workspace_id: str) -> int:
        """Money held by the rules: awaiting a human signature, not moving yet."""
        row = self.one(
            "SELECT COALESCE(SUM(total_cents), 0) AS n FROM requests "
            "WHERE workspace_id = ? AND status = 'awaiting'",
            (workspace_id,),
        )
        return int(row["n"]) if row else 0

    def overview_counts(self, workspace_id: str, *, month_start: str | None = None) -> dict:
        """The four Overview figures, computed from the one table that owns requests."""
        needs_you = self.count_requests(workspace_id, status="awaiting")
        return {
            "needs_you": needs_you,
            "committed_cents": self.committed_cents(workspace_id),
            "settled_month_cents": self.spent_cents(
                workspace_id, since=month_start or month_start_iso()
            ),
            "agent_count": self.count_agents(workspace_id),
            "request_count": self.count_requests(workspace_id),
        }

    def analytics(self, workspace_id: str, start: str, end: str) -> dict:
        """The M4 Analytics read model for one workspace over ``[start, end)``.

        Requests are selected by ``created_at``. Only the daily ``series``
        attributes a request to the UTC calendar day it was *decided* on, via
        ``COALESCE(decided_at, created_at)``: a cart created at 23:55 and
        captured at 00:05 counts on the capture day. Every amount is integer
        cents; every key/label/message is a string.

        Raises ``ValueError`` when either bound is not an ISO-8601 timestamp,
        when ``start >= end``, or when the window spans more than
        ``MAX_ANALYTICS_DAYS`` days. The HTTP layer should map that to a 400.
        """
        start_dt = parse_iso(start)
        end_dt = parse_iso(end)
        if start_dt is None or end_dt is None:
            raise ValueError("start and end must be ISO-8601 timestamps")
        if start_dt >= end_dt:
            raise ValueError("start must be before end")

        days = _analytics_days(start_dt, end_dt)
        # Stored timestamps are always UTC, so normalise the bounds to UTC too
        # and the range check stays a correct lexicographic comparison.
        bounds = (
            workspace_id,
            start_dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
            end_dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
        )

        totals_row = self.one(
            "SELECT COUNT(*) AS requests, "
            "COUNT(CASE WHEN status = 'captured' THEN 1 END) AS captured, "
            "COUNT(CASE WHEN status = 'awaiting' THEN 1 END) AS awaiting, "
            "COUNT(CASE WHEN status = 'approved' THEN 1 END) AS approved, "
            "COUNT(CASE WHEN status = 'blocked' THEN 1 END) AS blocked, "
            "COUNT(CASE WHEN status = 'vetoed' THEN 1 END) AS vetoed, "
            "COUNT(CASE WHEN status = 'voided' THEN 1 END) AS voided, "
            "COALESCE(SUM(CASE WHEN status = 'captured' THEN total_cents ELSE 0 END), 0) "
            "  AS captured_cents, "
            "COALESCE(SUM(CASE WHEN status IN ('awaiting', 'approved') "
            "  THEN total_cents ELSE 0 END), 0) AS pending_cents, "
            "COALESCE(SUM(CASE WHEN status IN ('blocked', 'vetoed') "
            "  THEN total_cents ELSE 0 END), 0) AS blocked_cents, "
            "COUNT(CASE WHEN decision_outcome = 'auto' THEN 1 END) AS auto, "
            "COUNT(CASE WHEN decision_outcome = 'review' THEN 1 END) AS review, "
            "COUNT(CASE WHEN decision_outcome = 'block' THEN 1 END) AS block "
            "FROM requests "
            "WHERE workspace_id = ? AND created_at >= ? AND created_at < ?",
            bounds,
        )
        totals = {
            key: int((totals_row or {}).get(key) or 0) for key in _ANALYTICS_TOTALS
        }

        agent_rows = self.query(
            "SELECT r.agent_id AS key, "
            "       COALESCE(a.name, r.agent_id) AS label, "
            "       COALESCE(a.tag, '') AS tag, "
            "       COUNT(*) AS requests, "
            "       COUNT(CASE WHEN r.status = 'captured' THEN 1 END) AS captured, "
            "       COALESCE(SUM(CASE WHEN r.status = 'captured' "
            "         THEN r.total_cents ELSE 0 END), 0) AS captured_cents, "
            "       COUNT(CASE WHEN r.status IN ('awaiting', 'approved') THEN 1 END) "
            "         AS pending, "
            "       COALESCE(SUM(CASE WHEN r.status IN ('awaiting', 'approved') "
            "         THEN r.total_cents ELSE 0 END), 0) AS pending_cents, "
            "       COUNT(CASE WHEN r.status = 'blocked' THEN 1 END) AS blocked, "
            "       COUNT(CASE WHEN r.status = 'vetoed' THEN 1 END) AS vetoed "
            "FROM requests r "
            "LEFT JOIN agents a "
            "  ON a.id = r.agent_id AND a.workspace_id = r.workspace_id "
            "WHERE r.workspace_id = ? AND r.created_at >= ? AND r.created_at < ? "
            "GROUP BY r.agent_id, a.name, a.tag",
            bounds,
        )
        by_agent = [
            {
                "key": str(row["key"]),
                "label": str(row["label"] or row["key"]),
                "tag": str(row["tag"] or ""),
                **{key: int(row[key] or 0) for key in _ANALYTICS_AGENT_KEYS},
            }
            for row in agent_rows
        ]
        by_agent.sort(
            key=lambda row: (-row["captured_cents"], -row["requests"], row["label"])
        )

        tag_rows = self.query(
            "SELECT COALESCE(a.tag, '') AS key, "
            "       COUNT(*) AS requests, "
            "       COUNT(CASE WHEN r.status = 'captured' THEN 1 END) AS captured, "
            "       COALESCE(SUM(CASE WHEN r.status = 'captured' "
            "         THEN r.total_cents ELSE 0 END), 0) AS captured_cents, "
            "       COUNT(CASE WHEN r.status IN ('awaiting', 'approved') THEN 1 END) "
            "         AS pending, "
            "       COALESCE(SUM(CASE WHEN r.status IN ('awaiting', 'approved') "
            "         THEN r.total_cents ELSE 0 END), 0) AS pending_cents, "
            "       COUNT(CASE WHEN r.status = 'blocked' THEN 1 END) AS blocked, "
            "       COUNT(CASE WHEN r.status = 'vetoed' THEN 1 END) AS vetoed "
            "FROM requests r "
            "LEFT JOIN agents a "
            "  ON a.id = r.agent_id AND a.workspace_id = r.workspace_id "
            "WHERE r.workspace_id = ? AND r.created_at >= ? AND r.created_at < ? "
            "GROUP BY COALESCE(a.tag, '')",
            bounds,
        )
        by_tag = [
            {
                "key": str(row["key"] or ""),
                "label": str(row["key"] or "") or "Untagged",
                **{key: int(row[key] or 0) for key in _ANALYTICS_AGENT_KEYS},
            }
            for row in tag_rows
        ]
        by_tag.sort(
            key=lambda row: (-row["captured_cents"], -row["requests"], row["label"])
        )

        def line_rollup(column: str) -> list[dict]:
            """One row per vendor/category from the request lines in range."""
            rows = self.query(
                f"SELECT COALESCE(l.{column}, '') AS key, "
                "       COUNT(DISTINCT r.id) AS requests, "
                "       COALESCE(SUM(CASE WHEN r.status = 'captured' "
                "         THEN l.line_cents ELSE 0 END), 0) AS captured_cents, "
                "       COUNT(*) AS lines, "
                "       COUNT(DISTINCT CASE WHEN r.status = 'blocked' "
                "         THEN r.id END) AS blocked, "
                "       COUNT(DISTINCT CASE WHEN r.status = 'vetoed' "
                "         THEN r.id END) AS vetoed "
                "FROM request_lines l "
                "JOIN requests r ON r.id = l.request_id "
                "WHERE r.workspace_id = ? AND r.created_at >= ? AND r.created_at < ? "
                f"GROUP BY COALESCE(l.{column}, '')",
                bounds,
            )
            rolled = [
                {
                    "key": str(row["key"] or ""),
                    "label": str(row["key"] or "") or "Unknown",
                    "requests": int(row["requests"] or 0),
                    "captured_cents": int(row["captured_cents"] or 0),
                    "lines": int(row["lines"] or 0),
                    "blocked": int(row["blocked"] or 0),
                    "vetoed": int(row["vetoed"] or 0),
                }
                for row in rows
            ]
            rolled.sort(
                key=lambda row: (-row["captured_cents"], -row["requests"], row["key"])
            )
            return rolled

        by_vendor = line_rollup("vendor")
        by_category = line_rollup("category")

        rule_rows = self.query(
            "SELECT rr.rule AS key, rr.severity AS severity, "
            "       COUNT(*) AS count, "
            "       COUNT(DISTINCT r.id) AS requests, "
            "       COUNT(DISTINCT CASE WHEN r.status = 'blocked' "
            "         THEN r.id END) AS blocked, "
            "       COUNT(DISTINCT CASE WHEN r.status <> 'blocked' "
            "         THEN r.id END) AS review, "
            "       MIN(rr.message) AS example "
            "FROM request_reasons rr "
            "JOIN requests r ON r.id = rr.request_id "
            "WHERE r.workspace_id = ? AND r.created_at >= ? AND r.created_at < ? "
            "GROUP BY rr.rule, rr.severity",
            bounds,
        )
        by_rule = [
            {
                "key": str(row["key"]),
                "severity": str(row["severity"]),
                "count": int(row["count"] or 0),
                "requests": int(row["requests"] or 0),
                "blocked": int(row["blocked"] or 0),
                "review": int(row["review"] or 0),
                "example": str(row["example"] or ""),
            }
            for row in rule_rows
        ]
        by_rule.sort(key=lambda row: (-row["count"], row["key"], row["severity"]))

        series_rows = self.query(
            "SELECT date(COALESCE(decided_at, created_at)) AS day, "
            "       COUNT(*) AS requests, "
            "       COALESCE(SUM(total_cents), 0) AS captured_cents "
            "FROM requests "
            "WHERE workspace_id = ? AND created_at >= ? AND created_at < ? "
            "  AND status = 'captured' "
            "GROUP BY day",
            bounds,
        )
        captured_by_day = {
            str(row["day"]): {
                "requests": int(row["requests"] or 0),
                "captured_cents": int(row["captured_cents"] or 0),
            }
            for row in series_rows
        }
        series = [
            {
                "date": day.isoformat(),
                "captured_cents": captured_by_day.get(day.isoformat(), {}).get(
                    "captured_cents", 0
                ),
                "requests": captured_by_day.get(day.isoformat(), {}).get("requests", 0),
            }
            for day in days
        ]

        return {
            "range": {"from": start, "to": end},
            "totals": totals,
            "by_agent": by_agent,
            "by_tag": by_tag,
            "by_vendor": by_vendor,
            "by_category": by_category,
            "by_rule": by_rule,
            "series": series,
        }

    def update_request(
        self,
        workspace_id: str,
        request_id: str,
        *,
        status: str | None = None,
        decision_outcome: str | None = None,
        approver_user_id: str | None = None,
        decided_at: str | None = None,
        total_cents: int | None = None,
    ) -> bool:
        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = status
        if decision_outcome is not None:
            updates["decision_outcome"] = decision_outcome
        if approver_user_id is not None:
            updates["approver_user_id"] = approver_user_id
        if decided_at is not None:
            updates["decided_at"] = decided_at
        if total_cents is not None:
            updates["total_cents"] = int(total_cents)
        if not updates:
            return False
        assignments = ", ".join(f"{key} = ?" for key in updates)
        cur = self.execute(
            f"UPDATE requests SET {assignments} WHERE workspace_id = ? AND id = ?",
            list(updates.values()) + [workspace_id, request_id],
        )
        return cur.rowcount > 0

    def add_event(
        self,
        request_id: str,
        kind: str,
        label: str,
        *,
        detail: str | None = None,
        href: str | None = None,
        at: str | None = None,
    ) -> dict:
        """Append one audit step; seq is per request so the trail always sorts."""
        row = self.one(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM events WHERE request_id = ?",
            (request_id,),
        )
        seq = int(row["seq"]) if row else 1
        stamp = at or now_iso()
        self.execute(
            "INSERT INTO events (request_id, seq, kind, label, detail, href, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request_id, seq, kind, label, detail, href, stamp),
        )
        return {
            "request_id": request_id,
            "seq": seq,
            "kind": kind,
            "label": label,
            "detail": detail,
            "href": href,
            "at": stamp,
        }

    # -- payments + consent --------------------------------------------
    def create_payment(
        self,
        request_id: str,
        *,
        mode: str,
        amount_cents: int,
        vault_id: str | None = None,
        status: str = "creating",
    ) -> dict:
        payment_id = new_id("pay_", 4)
        stamp = now_iso()
        self.execute(
            "INSERT INTO payments (id, request_id, mode, vault_id, status, amount_cents, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (payment_id, request_id, mode, vault_id, status, int(amount_cents), stamp, stamp),
        )
        return self.one("SELECT * FROM payments WHERE id = ?", (payment_id,)) or {}

    def update_payment(
        self,
        payment_id: str,
        *,
        status: str | None = None,
        vault_id: str | None = None,
        order_id: str | None = None,
        authorization_id: str | None = None,
        capture_id: str | None = None,
    ) -> dict | None:
        updates: dict[str, Any] = {}
        for key, value in (
            ("status", status),
            ("vault_id", vault_id),
            ("order_id", order_id),
            ("authorization_id", authorization_id),
            ("capture_id", capture_id),
        ):
            if value is not None:
                updates[key] = value
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            self.execute(
                f"UPDATE payments SET {assignments}, updated_at = ? WHERE id = ?",
                list(updates.values()) + [now_iso(), payment_id],
            )
        return self.one("SELECT * FROM payments WHERE id = ?", (payment_id,))

    def mark_consent_used(
        self,
        consent_id: str,
        *,
        approver_user_id: str | None = None,
        used_at: str | None = None,
    ) -> dict | None:
        self.execute(
            "UPDATE consents SET used_at = ?, approver_user_id = COALESCE(?, approver_user_id) "
            "WHERE id = ?",
            (used_at or now_iso(), approver_user_id, consent_id),
        )
        return self.one("SELECT * FROM consents WHERE id = ?", (consent_id,))
