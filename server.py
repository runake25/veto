#!/usr/bin/env python3
"""Veto runner. One process: console, demo harness, and the pipeline API.

    python3 server.py            # http://127.0.0.1:8787
    PAYPAL_LIVE=1 python3 server.py   # real sandbox (needs a vaulted wallet)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import re
import threading
import time
import sys
import traceback
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veto.engine import ApprovalPending, Engine, VaultRequired  # noqa: E402
from veto.store import Store, month_start_iso, parse_iso  # noqa: E402
from veto import auth as veto_auth  # noqa: E402
from veto import pipeline as veto_pipeline  # noqa: E402
from veto.policy import RULE_KINDS  # noqa: E402
from veto import requests as veto_requests  # noqa: E402
from veto import check as veto_check  # noqa: E402
from veto import routing as veto_routing  # noqa: E402
from veto import routines as veto_routines  # noqa: E402
from veto.events import FEED  # noqa: E402
from veto.ratelimit import RateLimiter  # noqa: E402
from veto import settlement as veto_settlement  # noqa: E402
from veto import slack_commands as veto_slack  # noqa: E402
from veto.seed import seed_demo  # noqa: E402

ENGINE = Engine()
PORT = int(os.environ.get("PORT", "8787"))
# containers and hosted platforms need 0.0.0.0; local runs stay on loopback
HOST = (os.environ.get("HOST") or "127.0.0.1").strip()
# set VETO_PUBLIC_URL when the app is behind a tunnel, so Slack buttons link out
PUBLIC_URL = (os.environ.get("VETO_PUBLIC_URL") or "").strip().rstrip("/")


def public_base() -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    return f"http://127.0.0.1:{PORT}"


def connect_url(path: str = "/connect") -> str:
    return public_base() + path


def post_json(url: str, payload: dict) -> None:
    """Post a Slack response_url body. Fire and forget; failures are not fatal."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:  # noqa: BLE001
        pass

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}

# ---------------------------------------------------------------------------
# v2 state: SQLite store + demo seed
# ---------------------------------------------------------------------------
STORE = Store()
SEEDED: dict = {}
if os.environ.get("VETO_SEED_DEMO", "1") != "0":
    try:
        SEEDED = seed_demo(STORE)
    except Exception:  # noqa: BLE001 - a seed failure must not stop the legacy demo
        traceback.print_exc()

APP_DIR = ROOT / "app"

# The money mover is a singleton: one PayPal client (or one in-process simulator)
# for the process. Live deploys may hand the approved vault in through the env.
LIMITER = RateLimiter()

# calls per minute, per door. Generous for a human clicking around; a ceiling for
# a key, a leaked link or a loop. Override with VETO_RATE_* in .env.
RATE_KEY_PER_MIN = int(os.environ.get("VETO_RATE_KEY_PER_MIN", "30") or 30)
RATE_SESSION_PER_MIN = int(os.environ.get("VETO_RATE_SESSION_PER_MIN", "120") or 120)
RATE_SEARCH_PER_MIN = int(os.environ.get("VETO_RATE_SEARCH_PER_MIN", "60") or 60)
RATE_SLACK_PER_MIN = int(os.environ.get("VETO_RATE_SLACK_PER_MIN", "20") or 20)

SETTLEMENT = veto_settlement.Settlement()
try:
    SETTLEMENT.prepare(STORE)
except Exception:  # noqa: BLE001 - a wallet sync failure must not stop the runner
    traceback.print_exc()


class ApiError(Exception):
    """A v1 route failure with a status and a machine-readable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class RateLimited(ApiError):
    """Too many calls in the window; the response carries Retry-After."""

    def __init__(self, retry_after: float) -> None:
        seconds = max(1, int(round(retry_after)))
        super().__init__(
            429,
            "rate_limited",
            f"Too many requests. Try again in {seconds}s.",
        )
        self.retry_after = seconds


def public_user(user: dict) -> dict:
    """The user fields the web app may see."""
    return {
        "id": user.get("id"),
        "name": user.get("name") or "",
        "email": user.get("email") or "",
        "role": user.get("role") or "viewer",
    }


def public_workspace(workspace: dict) -> dict:
    """The workspace fields the web app may see."""
    return {
        "id": workspace.get("id"),
        "name": workspace.get("name") or "",
        "currency": workspace.get("currency") or "USD",
        "monthly_budget_cents": int(workspace.get("monthly_budget_cents") or 0),
    }


def _changed(*scopes: str) -> None:
    """Tell every open SSE tab that a slice of the workspace moved.

    Best-effort by design: publishing a refresh signal must never be able to
    fail the mutation that already succeeded.
    """
    for scope in scopes:
        try:
            FEED.publish(scope)
        except Exception:  # noqa: BLE001 - a broken feed cannot undo a write
            traceback.print_exc()


def request_date_bounds(params: dict) -> dict:
    """The Inbox's optional date window, expressed as ``[start, end)`` instants.

    ``from`` / ``to`` are ``YYYY-MM-DD`` in the viewer's local calendar and
    ``tz`` is that calendar's offset from UTC in east-positive minutes (Hong
    Kong = 480; a browser sends ``-new Date().getTimezoneOffset()``). Day D in
    the viewer's calendar starts at ``D 00:00 UTC - tz minutes``, so the
    returned bounds are exact UTC instants. Both days are inclusive; absent
    parameters mean "no bound" (there is deliberately no default range here --
    the inbox shows everything unless the user filters).

    Returns ``{"created_from", "created_before", "from", "to", "tz"}``; the
    store takes the first two, the response echoes the last three. Raises
    ``ApiError(400, "invalid_range")`` for malformed dates, a reversed window or
    an out-of-range / non-integer ``tz``.
    """
    raw_from = (params.get("from") or [""])[0].strip()
    raw_to = (params.get("to") or [""])[0].strip()
    raw_tz = (params.get("tz") or [""])[0].strip()
    try:
        tz_minutes = int(raw_tz) if raw_tz else 0
    except (TypeError, ValueError):
        raise ApiError(
            400, "invalid_range", "tz must be whole minutes from UTC (-840..840)."
        ) from None
    if not -840 <= tz_minutes <= 840:
        raise ApiError(
            400, "invalid_range", "tz must be whole minutes from UTC (-840..840)."
        )
    try:
        start_day = date.fromisoformat(raw_from) if raw_from else None
        end_day = date.fromisoformat(raw_to) if raw_to else None
    except ValueError:
        raise ApiError(400, "invalid_range", "Pass from and to as YYYY-MM-DD.") from None
    if start_day is not None and end_day is not None and end_day < start_day:
        raise ApiError(400, "invalid_range", "`from` must not be after `to`.")

    offset = timedelta(minutes=tz_minutes)

    def utc_midnight(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

    created_from = (
        (utc_midnight(start_day) - offset).isoformat(timespec="seconds")
        if start_day is not None
        else None
    )
    created_before = (
        (utc_midnight(end_day) + timedelta(days=1) - offset).isoformat(timespec="seconds")
        if end_day is not None
        else None
    )
    return {
        "created_from": created_from,
        "created_before": created_before,
        "from": raw_from or None,
        "to": raw_to or None,
        "tz": tz_minutes,
    }


def date_range(params: dict) -> tuple[str, str]:
    """Two ``YYYY-MM-DD`` query dates -> one ``[start, end)`` pair of UTC instants.

    The UI's ``to`` day is inclusive, so the exclusive upper bound is the next
    midnight. Defaults to the current month through today.
    """
    today = datetime.now(timezone.utc).date()
    raw_from = (params.get("from") or [""])[0].strip()
    raw_to = (params.get("to") or [""])[0].strip()
    try:
        start_day = date.fromisoformat(raw_from) if raw_from else today.replace(day=1)
        end_day = date.fromisoformat(raw_to) if raw_to else today
    except ValueError:
        raise ApiError(400, "invalid_range", "Pass from and to as YYYY-MM-DD.") from None
    if end_day < start_day:
        raise ApiError(400, "invalid_range", "`from` must not be after `to`.")
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)
    end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=timezone.utc) + timedelta(
        days=1
    )
    return (
        start.isoformat(timespec="seconds"),
        end.isoformat(timespec="seconds"),
    )


# ---------------------------------------------------------------------------
# agent input validation (create / PATCH)
# ---------------------------------------------------------------------------
AGENT_WRITE_ROLES = ("admin", "approver")
REQUEST_STATUSES = ("blocked", "awaiting", "approved", "captured", "vetoed", "voided")
REQUEST_SORTS = (
    "priority", "newest", "oldest", "amount_desc", "amount_asc", "agent", "agent_desc",
)
MAX_AGENT_NAME = 80
MAX_AGENT_TAG = 40
MAX_AGENT_CHANNEL = 60
MAX_SIMULATE_CHARS = 1000
_CAP_FIELDS = ("monthly_cap_cents", "per_order_cap_cents",
               "per_unit_cap_cents", "approval_threshold_cents")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _invalid_agent(message: str) -> None:
    raise ApiError(400, "invalid_agent", message)


def _clean_domain(value) -> str:
    """'https://WWW.Amazon.com/path' -> 'www.amazon.com'."""
    raw = str(value or "").strip().lower()
    raw = re.sub(r"^[a-z][a-z0-9+.-]*://", "", raw)
    raw = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip().strip(".")
    return raw


def _whole_cents(value) -> int:
    """Validate a JSON money field (whole, non-negative cents)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(400, "invalid_agent", "caps must be whole numbers of cents or null.")
    whole = int(value)
    if whole != value:
        raise ApiError(400, "invalid_agent", "caps must be whole numbers of cents or null.")
    if whole < 0:
        raise ApiError(400, "invalid_agent", "caps cannot be negative.")
    return whole


def _clean_agent_payload(body: dict, *, partial: bool) -> dict:
    """Validate an agent create/PATCH body into store-shaped kwargs.

    ``partial=True`` only touches keys the caller sent; on create every optional
    field gets its documented default (tag 'general', all six rules, no children).
    Raises ``ApiError(400, "invalid_agent")`` with a field message.
    """
    if not isinstance(body, dict):
        _invalid_agent("The request body must be a JSON object.")
    fields: dict = {}

    if not partial or "name" in body:
        name = str(body.get("name") or "").strip()
        if not name:
            _invalid_agent("name is required.")
        if len(name) > MAX_AGENT_NAME:
            _invalid_agent(f"name must be {MAX_AGENT_NAME} characters or fewer.")
        fields["name"] = name
    if "tag" in body:
        tag = str(body.get("tag") or "").strip() or "general"
        if len(tag) > MAX_AGENT_TAG:
            _invalid_agent(f"tag must be {MAX_AGENT_TAG} characters or fewer.")
        fields["tag"] = tag
    elif not partial:
        fields["tag"] = "general"
    if "channel" in body:
        channel = body.get("channel")
        channel = str(channel).strip() if channel not in (None, "") else None
        if channel and len(channel) > MAX_AGENT_CHANNEL:
            _invalid_agent(f"channel must be {MAX_AGENT_CHANNEL} characters or fewer.")
        fields["channel"] = channel
    if "status" in body:
        status = str(body.get("status") or "").strip()
        if status not in ("active", "paused"):
            _invalid_agent("status must be active or paused.")
        fields["status"] = status
    for cap in _CAP_FIELDS:
        if cap not in body:
            continue
        fields[cap] = None if body[cap] is None else _whole_cents(body[cap])

    vendors = None
    if "vendors" in body:
        raw_vendors = body.get("vendors") or []
        if not isinstance(raw_vendors, list):
            _invalid_agent("vendors must be a list of domains.")
        vendors = []
        for item in raw_vendors:
            domain = _clean_domain(item)
            if not domain or any(ch.isspace() for ch in domain):
                _invalid_agent(f"vendors: {str(item)!r} is not a domain.")
            if domain not in vendors:
                vendors.append(domain)
    elif not partial:
        vendors = []

    categories = None
    if "categories" in body:
        raw_categories = body.get("categories") or []
        if not isinstance(raw_categories, list):
            _invalid_agent("categories must be a list of slugs.")
        categories = []
        for item in raw_categories:
            slug = str(item or "").strip().lower()
            if not _SLUG_RE.match(slug):
                _invalid_agent(f"categories: {str(item)!r} is not a category slug.")
            if slug not in categories:
                categories.append(slug)
    elif not partial:
        categories = []

    rules = None
    if "rules" in body:
        raw_rules = body.get("rules")
        if raw_rules is None:
            raw_rules = list(RULE_KINDS)
        if not isinstance(raw_rules, list):
            _invalid_agent("rules must be a list of rule kinds.")
        rules = []
        for item in raw_rules:
            kind = str(item or "").strip()
            if kind not in RULE_KINDS:
                _invalid_agent(f"rules: {str(item)!r} is not a rule kind.")
            if kind not in rules:
                rules.append(kind)
    elif not partial:
        rules = list(RULE_KINDS)

    overrides = None
    if "overrides" in body:
        raw_overrides = body.get("overrides") or []
        if not isinstance(raw_overrides, list):
            _invalid_agent("overrides must be a list of objects.")
        overrides = []
        seen: set[tuple[str, str]] = set()
        for item in raw_overrides:
            if not isinstance(item, dict):
                _invalid_agent("overrides must be objects with category, kind, value_cents.")
            category = str(item.get("category") or "").strip().lower()
            if not _SLUG_RE.match(category):
                _invalid_agent(f"overrides: {category!r} is not a category slug.")
            kind = str(item.get("kind") or "").strip()
            if kind not in RULE_KINDS:
                _invalid_agent(f"overrides: {str(item.get('kind'))!r} is not a rule kind.")
            if (category, kind) in seen:
                continue
            seen.add((category, kind))
            overrides.append({
                "category": category,
                "kind": kind,
                "value_cents": _whole_cents(item.get("value_cents")),
            })
    elif not partial:
        overrides = []

    return {
        "fields": fields,
        "vendors": vendors,
        "categories": categories,
        "rules": rules,
        "overrides": overrides,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "Veto/1.0"

    def log_message(self, fmt, *args):  # quieter console
        if os.environ.get("VETO_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers --------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, str(value))
        for cookie in getattr(self, "_pending_cookies", ()):
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, code: int = 302) -> None:
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for cookie in getattr(self, "_pending_cookies", ()):
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _json(self, code: int, payload: dict, headers: dict | None = None) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json", headers)

    # -- v2 helpers -----------------------------------------------------
    def _ok(self, data: dict | None = None, status: int = 200) -> None:
        return self._json(status, {"ok": True, "data": data if data is not None else {}})

    def _api_error(self, status: int, code: str, message: str) -> None:
        raise ApiError(status, code, message)

    def _send_api_error(self, exc: ApiError) -> None:
        headers = {}
        if isinstance(exc, RateLimited):
            headers["Retry-After"] = exc.retry_after
        return self._json(
            exc.status,
            {"ok": False, "error": {"code": exc.code, "message": exc.message}},
            headers or None,
        )

    def _limit(self, scope: str, identity: str, limit: int) -> None:
        """One bucket per door and identity; raises a 429 with Retry-After."""
        allowed, retry = LIMITER.allow(
            f"{scope}:{identity}", limit=limit, per_seconds=60
        )
        if not allowed:
            raise RateLimited(retry)

    def _queue_cookie(self, value: str) -> None:
        if not hasattr(self, "_pending_cookies"):
            self._pending_cookies: list[str] = []
        self._pending_cookies.append(value)

    def _secure_cookies(self) -> bool:
        """Only mark the cookie Secure when this request is actually https.

        PUBLIC_URL points at the tunnel, but the runner is also opened directly on
        http://127.0.0.1:8787 for local work; a Secure cookie there is rejected by
        the browser and the session would silently never stick."""
        forwarded = (self.headers.get("X-Forwarded-Proto") or "").strip().lower()
        if forwarded:
            return forwarded == "https"
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        if host in ("127.0.0.1", "localhost", "[::1]", "::1"):
            return False
        return PUBLIC_URL.startswith("https://")

    def _set_session_cookie(self, token: str) -> None:
        self._queue_cookie(veto_auth.session_cookie(token, secure=self._secure_cookies()))

    def _clear_session_cookie(self) -> None:
        self._queue_cookie(veto_auth.clear_cookie(secure=self._secure_cookies()))

    def _cookie_token(self) -> str | None:
        cookies = veto_auth.parse_cookies(self.headers.get("Cookie"))
        return cookies.get(veto_auth.COOKIE_NAME)

    def _session(self) -> dict | None:
        token = self._cookie_token()
        if not token:
            return None
        return veto_auth.resolve_session(STORE, token)

    def _require_session(self) -> dict:
        session = self._session()
        if session is None:
            self._api_error(401, "unauthorized", "Sign in to continue.")
        return session

    def _require_role(self, *roles: str) -> dict:
        session = self._require_session()
        try:
            veto_auth.require_role(session["user"], *roles)
        except PermissionError:
            self._api_error(403, "forbidden", "You do not have access to that.")
        return session

    def _file(self, rel: str) -> None:
        path = (ROOT / rel).resolve()
        if not str(path).startswith(str(ROOT)) or not path.is_file():
            self._json(404, {"error": "not found"})
            return
        ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._send(200, path.read_bytes(), ctype)

    def _raw(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _body(self) -> dict:
        raw = self._raw()
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def _form(self) -> dict:
        """Slack posts a slash command as application/x-www-form-urlencoded."""
        raw = self._raw().decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(raw).items()}

    # -- v2 routes ------------------------------------------------------
    def _serve_app(self, path: str) -> None:
        """Serve the built SPA from app/: real files straight from disk, every
        other path falls back to app/index.html so the client router owns it."""
        rel = path[len("/app"):].lstrip("/")
        app_root = APP_DIR.resolve()
        if rel:
            candidate = (APP_DIR / rel).resolve()
            try:
                candidate.relative_to(app_root)
            except ValueError:
                self._api_error(404, "not_found", "Not found.")
            if candidate.is_file():
                ctype = CONTENT_TYPES.get(candidate.suffix, "application/octet-stream")
                return self._send(200, candidate.read_bytes(), ctype)
        return self._send_app_index()

    def _send_app_index(self) -> None:
        index = APP_DIR / "index.html"
        if not index.is_file():
            self._api_error(
                503,
                "app_not_built",
                "The web app is not built yet. Run `npm run build` in web/.",
            )
        return self._send(200, index.read_bytes(), "text/html; charset=utf-8")

    def _v2_get(self, path: str) -> bool:
        """Handle /app, /login and every GET /api/v1/* route. True = handled."""
        if path == "/login":
            self._redirect("/app/#/login")
            return True
        if path == "/":
            self._send_app_index()
            return True
        if path == "/app" or path.startswith("/app/"):
            self._serve_app(path)
            return True
        if not path.startswith("/api/v1/"):
            return False
        if path == "/api/v1/me":
            session = self._require_session()
            user, workspace = session["user"], session["workspace"]
            wallet = STORE.wallet(workspace["id"]) or {"status": "disconnected"}
            wallet_public: dict = {"status": wallet.get("status") or "disconnected"}
            if wallet.get("vault_id"):
                wallet_public["vault_id"] = wallet["vault_id"]
            self._ok({
                "user": public_user(user),
                "workspace": public_workspace(workspace),
                "agent_count": STORE.count_agents(workspace["id"]),
                "wallet": wallet_public,
            })
            return True
        if path == "/api/v1/events":
            self._v1_change_feed()
            return True
        if path == "/api/v1/agents":
            session = self._require_session()
            workspace_id = session["workspace"]["id"]
            totals = STORE.agent_month_totals(workspace_id, since=month_start_iso())
            routines = {row["agent_id"]: row for row in STORE.list_routines(workspace_id)}
            agents = []
            for agent in STORE.list_agents(workspace_id):
                month = totals.get(agent["id"]) or {}
                routine = routines.get(agent["id"])
                agents.append({
                    **agent,
                    "spent_month_cents": int(month.get("spent_cents") or 0),
                    "committed_month_cents": int(month.get("committed_cents") or 0),
                    "routine": (
                        {
                            "id": routine["id"],
                            "name": routine["name"],
                            "cadence": routine["cadence"],
                            "cadence_word": veto_routines.cadence_word(routine["cadence"]),
                            "status": routine["status"],
                            "next_run_at": routine["next_run_at"],
                        }
                        if routine
                        else None
                    ),
                })
            self._ok({"agents": agents})
            return True
        if path.startswith("/api/v1/agents/"):
            parts = [unquote(part) for part in path[len("/api/v1/agents/"):].split("/") if part]
            if len(parts) == 1:
                self._v1_agent_detail(parts[0])
                return True
            if len(parts) == 2 and parts[1] == "search":
                self._v1_agent_search(parts[0])
                return True
            if len(parts) == 2 and parts[1] == "keys":
                self._v1_list_keys(parts[0])
                return True
        if path == "/api/v1/overview":
            self._v1_overview()
            return True
        if path == "/api/v1/analytics":
            self._v1_analytics()
            return True
        if path == "/api/v1/integrations":
            self._v1_integrations()
            return True
        if path == "/api/v1/routines":
            self._v1_list_routines()
            return True
        if path == "/api/v1/requests":
            self._v1_list_requests()
            return True
        if path.startswith("/api/v1/requests/"):
            parts = [unquote(part) for part in path[len("/api/v1/requests/"):].split("/") if part]
            if len(parts) == 1:
                self._v1_request_detail(parts[0])
                return True
        self._api_error(404, "not_found", "Unknown route.")
        return True

    def _v2_post(self, path: str) -> bool:
        """Handle every POST /api/v1/* route. True = handled."""
        if not path.startswith("/api/v1/"):
            return False
        if path == "/api/v1/signup":
            self._v1_signup()
            return True
        if path == "/api/v1/login":
            self._v1_login()
            return True
        if path == "/api/v1/logout":
            self._v1_logout()
            return True
        if path == "/api/v1/demo-session":
            self._v1_demo_session()
            return True
        if path == "/api/v1/agents":
            self._v1_create_agent()
            return True
        if path == "/api/v1/simulate":
            self._v1_simulate()
            return True
        if path == "/api/v1/simulate/stream":
            self._v1_simulate_stream()
            return True
        if path == "/api/v1/requests":
            self._v1_create_request()
            return True
        if path.startswith("/api/v1/requests/"):
            parts = [unquote(part) for part in path[len("/api/v1/requests/"):].split("/") if part]
            if len(parts) == 2 and parts[1] == "decision":
                self._v1_decide_request(parts[0])
                return True
            if len(parts) == 2 and parts[1] == "void":
                self._v1_void_request(parts[0])
                return True
        if path.startswith("/api/v1/agents/"):
            parts = [unquote(part) for part in path[len("/api/v1/agents/"):].split("/") if part]
            if len(parts) == 2 and parts[1] == "keys":
                self._v1_create_key(parts[0])
                return True
            if len(parts) == 2 and parts[1] == "routine":
                self._v1_save_routine(parts[0])
                return True
            if len(parts) == 3 and parts[1] == "routine" and parts[2] in ("pause", "resume"):
                self._v1_set_routine_status(parts[0], parts[2])
                return True
            if len(parts) == 3 and parts[1] == "routine" and parts[2] == "run":
                self._v1_routine_run(parts[0])
                return True
            if len(parts) == 4 and parts[1] == "routine" and parts[2] == "run" \
                    and parts[3] == "stream":
                self._v1_routine_run_stream(parts[0])
                return True
        self._api_error(404, "not_found", "Unknown route.")
        return True

    def _v2_delete(self, path: str) -> bool:
        """Handle DELETE /api/v1/agents/{id}[/routine|/keys/{kid}]. True = handled."""
        if not path.startswith("/api/v1/"):
            return False
        prefix = "/api/v1/agents/"
        if path.startswith(prefix):
            parts = [unquote(part) for part in path[len(prefix):].split("/") if part]
            if len(parts) == 1:
                self._v1_delete_agent(parts[0])
                return True
            if len(parts) == 2 and parts[1] == "routine":
                self._v1_delete_routine(parts[0])
                return True
            if len(parts) == 3 and parts[1] == "keys":
                self._v1_revoke_key(parts[0], parts[2])
                return True
        self._api_error(404, "not_found", "Unknown route.")
        return True

    def _v2_patch(self, path: str) -> bool:
        """Handle PATCH /api/v1/agents/{id}. True = handled."""
        if not path.startswith("/api/v1/"):
            return False
        prefix = "/api/v1/agents/"
        if path.startswith(prefix):
            parts = [unquote(part) for part in path[len(prefix):].split("/") if part]
            if len(parts) == 1:
                self._v1_update_agent(parts[0])
                return True
        self._api_error(404, "not_found", "Unknown route.")
        return True

    def _v1_signup(self) -> None:
        body = self._body()
        workspace_name = str(body.get("workspace_name") or "").strip()
        name = str(body.get("name") or "").strip()
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")

        if not workspace_name:
            self._api_error(400, "workspace_required", "Workspace name is required.")
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            self._api_error(400, "invalid_email", "Enter a valid email address.")
        if len(password) < 8:
            self._api_error(400, "weak_password", "Password must be at least 8 characters.")
        if STORE.user_by_email(email) is not None:
            self._api_error(409, "email_taken", "An account with that email already exists.")

        password_hash = veto_auth.hash_password(password)
        try:
            with STORE.transaction():
                workspace = STORE.create_workspace(workspace_name)
                user = STORE.create_user(
                    workspace["id"],
                    email,
                    name=name or email.split("@")[0],
                    password_hash=password_hash,
                    role="admin",
                )
        except Exception as exc:  # noqa: BLE001 - SQLite UNIQUE races
            if STORE.user_by_email(email) is not None:
                self._api_error(409, "email_taken", "An account with that email already exists.")
            if "workspaces.name" in str(exc):
                self._api_error(409, "workspace_taken", "That workspace name is already taken.")
            raise

        token = veto_auth.issue_session(STORE, user)
        self._set_session_cookie(token)
        return self._ok({
            "user": public_user(user),
            "workspace": public_workspace(workspace),
        }, status=201)

    def _v1_login(self) -> None:
        body = self._body()
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")
        user = STORE.user_by_email(email) if email else None
        if user is None or not veto_auth.verify_password(user.get("password_hash") or "", password):
            self._api_error(401, "bad_credentials", "Email or password is incorrect.")
        workspace = STORE.workspace(user["workspace_id"])
        if workspace is None:
            self._api_error(401, "bad_credentials", "Email or password is incorrect.")
        token = veto_auth.issue_session(STORE, user)
        self._set_session_cookie(token)
        return self._ok({
            "user": public_user(user),
            "workspace": public_workspace(workspace),
        })

    def _v1_logout(self) -> None:
        self._body()  # consume any JSON body so the socket closes cleanly
        token = self._cookie_token()
        if token:
            veto_auth.revoke_session(STORE, token)
        self._clear_session_cookie()
        return self._ok({})

    def _v1_demo_session(self) -> None:
        self._body()  # the UI posts an empty JSON object; read it and ignore it
        if not SEEDED:
            self._api_error(404, "demo_unavailable", "The demo workspace is not seeded.")
        approver = STORE.user((SEEDED.get("approver") or {}).get("id") or "")
        workspace = STORE.workspace(approver["workspace_id"]) if approver else None
        if approver is None or workspace is None:
            self._api_error(404, "demo_unavailable", "The demo workspace is not seeded.")
        token = veto_auth.issue_session(STORE, approver)
        self._set_session_cookie(token)
        return self._ok({
            "user": public_user(approver),
            "workspace": public_workspace(workspace),
        })

    # -- M2: agents, scoped search, dry run -----------------------------
    def _agent_month(self, workspace_id: str, agent: dict) -> dict:
        """The stored agent plus the two figures its guardrail track draws."""
        totals = STORE.agent_month_totals(workspace_id, since=month_start_iso())
        month = totals.get(agent["id"]) or {}
        return {
            **agent,
            "spent_month_cents": int(month.get("spent_cents") or 0),
            "committed_month_cents": int(month.get("committed_cents") or 0),
        }

    def _workspace_agent(self, agent_id: str) -> dict:
        session = self._require_session()
        agent = STORE.agent(session["workspace"]["id"], agent_id)
        if agent is None:
            self._api_error(404, "agent_not_found", "That agent does not exist.")
        return self._agent_month(session["workspace"]["id"], agent)

    # -- M3: requests, decisions, voids, keys ---------------------------
    def _bearer_agent(self) -> dict | None:
        """An agent calling with its own key; None when no bearer is present."""
        header = self.headers.get("Authorization") or ""
        if not header:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            self._api_error(
                401, "invalid_key", "Send an agent key as 'Authorization: Bearer vkey_…'."
            )
        resolved = veto_auth.resolve_api_key(STORE, token.strip())
        if resolved is None:
            self._api_error(401, "invalid_key", "That agent key is unknown or revoked.")
        return resolved

    def _request_actor(self) -> dict:
        """The door this request came through: an agent key, or a signed-in human."""
        resolved = self._bearer_agent()
        if resolved is not None:
            return {
                "agent": resolved["agent"],
                "workspace": STORE.workspace(resolved["workspace_id"]),
                "user": None,
                "origin": "api",
            }
        session = self._require_session()
        return {
            "agent": None,
            "workspace": session["workspace"],
            "user": session["user"],
            "origin": "web",
        }

    def _workspace_approver(self, workspace_id: str) -> dict | None:
        """Slack has no store user of its own; sign with the workspace's approver."""
        for user in STORE.list_users(workspace_id):
            if user.get("role") == "approver":
                return user
        for user in STORE.list_users(workspace_id):
            if user.get("role") == "admin":
                return user
        return None

    def _demo_workspace(self) -> dict | None:
        workspace = (SEEDED.get("workspace") or {})
        if workspace.get("id"):
            fresh = STORE.workspace(workspace["id"])
            if fresh is not None:
                return fresh
        workspaces = STORE.list_workspaces()
        return workspaces[0] if workspaces else None

    def _public_request(self, request_id: str, workspace_id: str) -> dict:
        record = STORE.request(workspace_id, request_id)
        if record is None:
            self._api_error(404, "request_not_found", "That request does not exist.")
        return veto_requests.public_request(record)

    def _send_request_error(self, exc: veto_requests.RequestError) -> None:
        error = {"code": exc.code, "message": exc.message}
        if isinstance(exc, veto_requests.WalletRequired):
            error["approve_url"] = exc.approve_url
            error["connect"] = public_base() + "/app/#/agents"
        self._json(exc.status, {"ok": False, "error": error})

    # -- M3: handlers ---------------------------------------------------
    def _v1_overview(self) -> None:
        session = self._require_session()
        workspace_id = session["workspace"]["id"]
        counts = STORE.overview_counts(workspace_id)
        pending = STORE.list_requests(
            workspace_id, status="awaiting", limit=3, oldest_first=True,
            consent_state="actionable",
        )
        expired = STORE.list_requests(
            workspace_id, status="awaiting", limit=3, oldest_first=True,
            consent_state="expired",
        )
        wallet = STORE.wallet(workspace_id) or {"status": "disconnected"}
        budget = int(session["workspace"].get("monthly_budget_cents") or 0)
        used = int(counts["settled_month_cents"]) + int(counts["committed_cents"])
        self._ok({
            "counts": counts,
            "pending": [veto_requests.public_summary(row) for row in pending],
            "expired": [veto_requests.public_summary(row) for row in expired],
            "wallet": {
                "status": wallet.get("status") or "disconnected",
                "mode": SETTLEMENT.mode,
            },
            "budget_cents": budget,
            "remaining_cents": max(0, budget - used),
            "month": month_start_iso(),
        })

    def _v1_analytics(self) -> None:
        session = self._require_session()
        start, end = date_range(parse_qs(urlparse(self.path).query))
        try:
            data = STORE.analytics(session["workspace"]["id"], start, end)
        except ValueError as exc:
            self._api_error(400, "invalid_range", str(exc))
        self._ok(data)

    def _v1_integrations(self) -> None:
        session = self._require_session()
        workspace = session["workspace"]
        wallet = STORE.wallet(workspace["id"]) or {}
        agents = STORE.list_agents(workspace["id"])
        names = {agent["id"]: agent["name"] for agent in agents}
        keys = []
        for row in STORE.list_api_keys(workspace["id"]):
            item = self._public_key(row)
            item["agent_name"] = names.get(row["agent_id"]) or row["agent_id"]
            keys.append(item)
        default_id = workspace.get("default_agent_id")
        base = public_base()
        signing = bool((os.environ.get("SLACK_SIGNING_SECRET") or "").strip())
        bot = bool((os.environ.get("SLACK_BOT_TOKEN") or "").strip())
        self._ok({
            "workspace": public_workspace(workspace),
            "wallet": {
                "status": wallet.get("status") or "disconnected",
                "mode": SETTLEMENT.mode,
                "vault_id": wallet.get("vault_id"),
                "payer_email": wallet.get("payer_email"),
                "connected_at": wallet.get("connected_at"),
                "connect_url": base + "/connect",
            },
            "slack": {
                "signing_secret": signing,
                "bot_token": bot,
                "app_ready": signing and bot,
                "slash_command": "/veto",
                "slash_url": base + "/api/slack/veto",
                "interact_url": base + "/api/slack/interact",
                "public_url": base,
                "commands": [dict(command) for command in veto_slack.COMMANDS],
                "default_agent": {"id": default_id, "name": names[default_id]}
                if default_id in names
                else None,
                "routing": "model",
            },
            "agents": [
                {
                    "id": agent["id"],
                    "name": agent["name"],
                    "tag": agent["tag"],
                    "status": agent["status"],
                    "channel": agent["channel"],
                    "categories": agent.get("categories") or [],
                    "merchants": agent.get("vendors") or [],
                }
                for agent in agents
            ],
            "api": {"requests_path": "/api/v1/requests", "base_url": base},
            "keys": keys,
        })

    def _v1_list_requests(self) -> None:
        session = self._require_session()
        workspace_id = session["workspace"]["id"]
        params = parse_qs(urlparse(self.path).query)
        status = (params.get("status") or [""])[0].strip() or None
        if status == "expired":
            pass  # the synthetic expired tab is handled below
        elif status and status not in REQUEST_STATUSES:
            self._api_error(400, "invalid_status", f"Unknown status: {status}")
        agent_id = (params.get("agent_id") or [""])[0].strip() or None
        sort = (params.get("sort") or [""])[0].strip()
        if sort and sort not in REQUEST_SORTS:
            self._api_error(400, "invalid_sort", f"Unknown order: {sort}")
        q = (params.get("q") or [""])[0].strip()[:80] or None
        consent_state = (params.get("consent") or [""])[0].strip() or None
        if consent_state not in (None, "", "actionable", "expired"):
            self._api_error(400, "invalid_consent", "consent must be actionable or expired.")
        if status == "expired":
            status = "awaiting"
            consent_state = "expired"
        elif status == "awaiting" and consent_state is None:
            consent_state = "actionable"
        window = request_date_bounds(params)
        try:
            limit = max(1, min(100, int((params.get("limit") or ["50"])[0])))
            offset = max(0, int((params.get("offset") or ["0"])[0]))
        except (TypeError, ValueError):
            limit, offset = 50, 0
        filters = {
            "status": status,
            "agent_id": agent_id,
            "q": q,
            "consent_state": consent_state,
            "created_from": window["created_from"],
            "created_before": window["created_before"],
        }
        rows = STORE.list_requests(
            workspace_id,
            limit=limit,
            offset=offset,
            sort=sort or "priority",
            **filters,
        )
        self._ok({
            "requests": [veto_requests.public_summary(row) for row in rows],
            "returned": len(rows),
            "total": STORE.count_requests(workspace_id, **filters),
            "sum_cents": STORE.sum_requests(workspace_id, **filters),
            "sort": sort or "priority",
            "query": q or "",
            "from": window["from"],
            "to": window["to"],
            "tz": window["tz"],
        })

    def _v1_request_detail(self, request_id: str) -> None:
        session = self._require_session()
        self._ok({"request": self._public_request(request_id, session["workspace"]["id"])})

    def _v1_create_request(self) -> None:
        actor = self._request_actor()
        workspace = actor["workspace"]
        if workspace is None:
            self._api_error(401, "no_workspace", "The agent key has no workspace.")
        if actor["agent"] is not None:
            self._limit("requests", f"agent:{actor['agent']['id']}", RATE_KEY_PER_MIN)
        elif actor["user"] is not None:
            self._limit("requests", f"user:{actor['user']['id']}", RATE_SESSION_PER_MIN)
        body = self._body()
        text = str(body.get("text") or "").strip()
        if len(text) > MAX_SIMULATE_CHARS:
            text = text[:MAX_SIMULATE_CHARS]
        agent = actor["agent"]
        route_note = None
        if agent is None:
            requested_id = str(body.get("agent_id") or "").strip() or None
            if requested_id:
                try:
                    agent = veto_pipeline.resolve_agent(STORE, workspace["id"], requested_id)
                except LookupError as exc:
                    code = str(exc) or "no_agent"
                    message = (
                        "That agent does not exist."
                        if code == "agent_not_found"
                        else "This workspace has no agents yet."
                    )
                    self._api_error(404, code, message)
            else:
                # no agent named: the model reads the request and picks one
                agent, decision = veto_routing.route_request(STORE, workspace, text)
                if agent is None:
                    self._api_error(404, "no_agent", "This workspace has no agents yet.")
                route_note = veto_routing.trail_note(decision)
        requester = str(body.get("requester") or "").strip() or None
        if requester is None:
            requester = (actor["user"] or {}).get("name") or agent.get("name")
        record = veto_requests.create(
            STORE,
            workspace=workspace,
            agent=agent,
            text=text,
            origin=actor["origin"],
            channel=str(body.get("channel") or "").strip() or None,
            requester=requester,
            initiator_user_id=(actor["user"] or {}).get("id"),
            settlement=SETTLEMENT,
            route=route_note,
        )
        _changed("requests")
        self._ok({"request": veto_requests.public_request(record)}, status=201)

    def _v1_decide_request(self, request_id: str) -> None:
        session = self._require_role("admin", "approver")
        workspace = session["workspace"]
        user = session["user"]
        body = self._body()
        action = str(body.get("action") or "").strip().lower()
        if action not in ("approve", "veto"):
            self._api_error(400, "invalid_action", "action must be 'approve' or 'veto'.")
        if action == "approve":
            record = veto_requests.approve(
                STORE,
                workspace=workspace,
                request_id=request_id,
                user=user,
                settlement=SETTLEMENT,
            )
        else:
            record = veto_requests.veto(
                STORE, workspace=workspace, request_id=request_id, user=user
            )
        _changed("requests")
        self._ok({"request": veto_requests.public_request(record)})

    def _v1_void_request(self, request_id: str) -> None:
        session = self._require_role("admin", "approver")
        self._body()
        record = veto_requests.void(
            STORE,
            workspace=session["workspace"],
            request_id=request_id,
            user=session["user"],
        )
        _changed("requests")
        self._ok({"request": veto_requests.public_request(record)})

    def _public_key(self, row: dict) -> dict:
        return {
            "id": row.get("id"),
            "agent_id": row.get("agent_id"),
            "prefix": row.get("prefix"),
            "created_at": row.get("created_at"),
            "last_used_at": row.get("last_used_at"),
            "revoked_at": row.get("revoked_at"),
        }

    def _v1_list_keys(self, agent_id: str) -> None:
        session = self._require_role(*AGENT_WRITE_ROLES)
        agent = STORE.agent(session["workspace"]["id"], agent_id)
        if agent is None:
            self._api_error(404, "agent_not_found", "That agent does not exist.")
        rows = STORE.list_api_keys(session["workspace"]["id"], agent_id=agent_id)
        self._ok({
            "keys": [self._public_key(row) for row in rows],
            "agent": {"id": agent["id"], "name": agent["name"]},
        })

    def _v1_create_key(self, agent_id: str) -> None:
        session = self._require_role("admin")
        agent = STORE.agent(session["workspace"]["id"], agent_id)
        if agent is None:
            self._api_error(404, "agent_not_found", "That agent does not exist.")
        token, record = veto_auth.mint_api_key(STORE, agent)
        _changed("integrations")
        self._ok(
            {"key": self._public_key(record), "secret": token,
             "note": "This is the only time the key is shown."},
            status=201,
        )

    def _v1_revoke_key(self, agent_id: str, key_id: str) -> None:
        session = self._require_role("admin")
        if not STORE.revoke_api_key(session["workspace"]["id"], key_id):
            self._api_error(404, "key_not_found", "That key does not exist.")
        _changed("integrations")
        rows = STORE.list_api_keys(session["workspace"]["id"], agent_id=agent_id)
        match = next((row for row in rows if row["id"] == key_id), None)
        self._ok({"key": self._public_key(match) if match else {"id": key_id}})


    def _v1_agent_detail(self, agent_id: str) -> None:
        session = self._require_session()
        workspace = session["workspace"]
        agent = self._workspace_agent(agent_id)
        routine = STORE.routine_for_agent(workspace["id"], agent_id)
        runs = (
            STORE.routine_runs_for(workspace["id"], routine["id"], limit=8)
            if routine is not None
            else []
        )
        self._ok({
            "agent": agent,
            "routine": (
                veto_routines.public_routine(STORE, workspace, routine)
                if routine is not None
                else None
            ),
            "runs": [veto_routines.public_run(run) for run in runs],
        })

    # -- routines: the agent's standing job ------------------------------
    def _v1_list_routines(self) -> None:
        session = self._require_session()
        workspace = session["workspace"]
        rows = STORE.list_routines(workspace["id"])
        self._ok({
            "routines": [veto_routines.public_routine(STORE, workspace, row) for row in rows]
        })

    def _v1_save_routine(self, agent_id: str) -> None:
        """Create or edit an agent's standing job. A human signs off every edit."""
        session = self._require_role(*AGENT_WRITE_ROLES)
        workspace = session["workspace"]
        agent = STORE.agent(workspace["id"], agent_id)
        if agent is None:
            self._api_error(404, "agent_not_found", "That agent does not exist.")
        routine = STORE.routine_for_agent(workspace["id"], agent_id)
        body = self._body()

        cadence = str(
            body.get("cadence") if "cadence" in body
            else (routine or {}).get("cadence") or "weekly"
        ).strip().lower()
        if cadence not in veto_routines.CADENCES:
            self._api_error(400, "invalid_cadence", "cadence must be daily, weekly or monthly.")

        instruction = str(
            body.get("instruction") if "instruction" in body
            else (routine or {}).get("instruction") or ""
        ).strip()
        if len(instruction) < 3:
            self._api_error(
                400, "missing_instruction",
                "Say what the agent should buy on its routine.",
            )
        instruction = instruction[:MAX_SIMULATE_CHARS]

        name = str(
            body.get("name") if "name" in body
            else (routine or {}).get("name") or f"{agent['name']} routine"
        ).strip()[:80] or f"{agent['name']} routine"

        try:
            envelope = int(body.get("envelope_cents")
                           if "envelope_cents" in body
                           else (routine or {}).get("envelope_cents") or 0)
            dedupe = int(body.get("dedupe_days")
                         if "dedupe_days" in body
                         else (routine or {}).get("dedupe_days") or 0)
        except (TypeError, ValueError):
            self._api_error(400, "invalid_routine", "The envelope and window must be numbers.")
        envelope = max(0, min(10_000_000, envelope))
        dedupe = max(0, min(365, dedupe))

        user_id = session["user"]["id"]
        if routine is None:
            routine = STORE.create_routine(
                workspace["id"],
                agent_id,
                name=name,
                instruction=instruction,
                cadence=cadence,
                envelope_cents=envelope,
                dedupe_days=dedupe,
                next_run_at=veto_routines.next_run_at(cadence),
                created_by=user_id,
                approved_by=user_id,
            )
        else:
            fields = {
                "name": name,
                "instruction": instruction,
                "cadence": cadence,
                "envelope_cents": envelope,
                "dedupe_days": dedupe,
                "approved_by": user_id,
            }
            if cadence != routine["cadence"]:
                fields["next_run_at"] = veto_routines.next_run_at(cadence)
            routine = STORE.update_routine(workspace["id"], routine["id"], fields=fields)
        _changed("agents")
        self._ok({"routine": veto_routines.public_routine(STORE, workspace, routine)})

    def _v1_set_routine_status(self, agent_id: str, action: str) -> None:
        session = self._require_role(*AGENT_WRITE_ROLES)
        workspace = session["workspace"]
        routine = STORE.routine_for_agent(workspace["id"], agent_id)
        if routine is None:
            self._api_error(404, "no_routine", "This agent has no routine yet.")
        fields: dict = {"status": "paused" if action == "pause" else "active"}
        if action == "resume":
            nxt = parse_iso(routine.get("next_run_at"))
            if nxt is not None and nxt <= datetime.now(timezone.utc):
                # a paused routine resumes on its cadence; it does not backfill
                fields["next_run_at"] = veto_routines.next_run_at(routine["cadence"])
        routine = STORE.update_routine(workspace["id"], routine["id"], fields=fields)
        _changed("agents")
        self._ok({"routine": veto_routines.public_routine(STORE, workspace, routine)})

    def _routine_run_payload(self, workspace: dict, run: dict) -> dict:
        record = (
            STORE.request(workspace["id"], run["request_id"])
            if run.get("request_id")
            else None
        )
        return {
            "run": veto_routines.public_run(run),
            "request": veto_requests.public_request(record) if record else None,
        }

    def _routine_for_run(self, agent_id: str) -> tuple[dict, dict]:
        session = self._require_role(*AGENT_WRITE_ROLES)
        workspace = session["workspace"]
        routine = STORE.routine_for_agent(workspace["id"], agent_id)
        if routine is None:
            self._api_error(404, "no_routine", "This agent has no routine yet.")
        if routine["status"] != "active":
            self._api_error(
                409, "routine_paused", "Resume the routine before running it."
            )
        self._limit("simulate", f"user:{session['user']['id']}", RATE_SESSION_PER_MIN)
        return workspace, routine

    def _v1_routine_run(self, agent_id: str) -> None:
        workspace, routine = self._routine_for_run(agent_id)
        run = veto_routines.run_once(STORE, workspace, routine, settlement=SETTLEMENT)
        _changed("agents", "requests")
        self._ok(self._routine_run_payload(workspace, run))

    def _v1_routine_run_stream(self, agent_id: str) -> None:
        """Run the routine now, streaming the same frames as a dry run, then the run."""
        workspace, routine = self._routine_for_run(agent_id)
        emit = self._start_sse()
        try:
            run = veto_routines.run_once(
                STORE, workspace, routine, settlement=SETTLEMENT, progress=emit
            )
        except Exception:  # noqa: BLE001 - the stream is already open; report, don't 500
            traceback.print_exc()
            emit({"type": "error",
                  "error": {"code": "server_error", "message": "The run failed."}})
            return
        _changed("agents", "requests")
        payload = self._routine_run_payload(workspace, run)
        emit({"type": "run", **payload})

    def _v1_agent_search(self, agent_id: str) -> None:
        agent = self._workspace_agent(agent_id)
        session = self._require_session()
        self._limit("search", f"user:{session['user']['id']}", RATE_SEARCH_PER_MIN)
        params = parse_qs(urlparse(self.path).query)
        query = (params.get("q") or [""])[0].strip()
        if not query:
            self._api_error(400, "missing_query", "Pass ?q=<what to buy>.")
        try:
            qty = max(1, min(99, int((params.get("qty") or ["1"])[0])))
        except (TypeError, ValueError):
            qty = 1
        self._ok(veto_pipeline.scoped_search(agent, query, qty))

    def _v1_create_agent(self) -> None:
        session = self._require_role(*AGENT_WRITE_ROLES)
        payload = _clean_agent_payload(self._body(), partial=False)
        agent = STORE.create_agent(
            session["workspace"]["id"],
            payload["fields"],
            vendors=payload["vendors"],
            categories=payload["categories"],
            rules=payload["rules"],
            overrides=payload["overrides"],
        )
        _changed("agents")
        self._ok({"agent": agent}, status=201)

    def _v1_update_agent(self, agent_id: str) -> None:
        session = self._require_role(*AGENT_WRITE_ROLES)
        payload = _clean_agent_payload(self._body(), partial=True)
        agent = STORE.update_agent(
            session["workspace"]["id"],
            agent_id,
            fields=payload["fields"],
            vendors=payload["vendors"],
            categories=payload["categories"],
            rules=payload["rules"],
            overrides=payload["overrides"],
        )
        if agent is None:
            self._api_error(404, "agent_not_found", "That agent does not exist.")
        _changed("agents")
        self._ok({"agent": agent})

    def _v1_delete_agent(self, agent_id: str) -> None:
        """Delete an agent that never bought anything.

        An agent with recorded requests is part of the ledger; deleting it would
        orphan history the board and the analytics still count, so the operator
        is told to pause it instead.
        """
        session = self._require_role(*AGENT_WRITE_ROLES)
        workspace = session["workspace"]
        agent = STORE.agent(workspace["id"], agent_id)
        if agent is None:
            self._api_error(404, "agent_not_found", "That agent does not exist.")
        history = STORE.count_requests(workspace["id"], agent_id=agent_id)
        if history:
            self._api_error(
                409,
                "agent_has_history",
                f"{agent['name']} has {history} recorded request"
                f"{'' if history == 1 else 's'} and the ledger keeps them. "
                "Pause the agent instead.",
            )
        if not STORE.delete_agent(workspace["id"], agent_id):
            self._api_error(404, "agent_not_found", "That agent does not exist.")
        _changed("agents")
        self._ok({"deleted": True})

    def _v1_delete_routine(self, agent_id: str) -> None:
        session = self._require_role(*AGENT_WRITE_ROLES)
        workspace = session["workspace"]
        routine = STORE.routine_for_agent(workspace["id"], agent_id)
        if routine is None:
            self._api_error(404, "no_routine", "This agent has no standing job.")
        STORE.delete_routine(workspace["id"], routine["id"])
        _changed("agents")
        self._ok({"deleted": True})

    def _v1_simulate(self) -> None:
        session = self._require_session()
        self._limit("simulate", f"user:{session['user']['id']}", RATE_SESSION_PER_MIN)
        body = self._body()
        text = str(body.get("text") or "").strip()
        if not text:
            self._api_error(400, "missing_text", "What should the agent buy?")
        if len(text) > MAX_SIMULATE_CHARS:
            text = text[:MAX_SIMULATE_CHARS]  # long threads are truncated, not rejected
        agent_id = str(body.get("agent_id") or "").strip() or None
        try:
            agent = veto_pipeline.resolve_agent(STORE, session["workspace"]["id"], agent_id)
        except LookupError as exc:
            code = str(exc) or "no_agent"
            message = ("That agent does not exist." if code == "agent_not_found"
                       else "This workspace has no agents yet.")
            self._api_error(404, code, message)
        workspace = session["workspace"]
        month_spent = STORE.spent_cents(
            workspace["id"], agent_id=agent["id"], since=month_start_iso()
        )
        try:
            result = veto_pipeline.simulate(
                agent,
                text,
                month_spent_cents=month_spent,
                workspace_monthly_budget_cents=workspace.get("monthly_budget_cents"),
            )
        except ValueError as exc:
            self._api_error(400, "no_intent", str(exc))
        self._ok(result)

    def _start_sse(self):
        """Open a server-sent-event response and return its emit(payload) writer."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        lock = threading.Lock()
        closed = False

        def emit(payload: dict) -> None:
            nonlocal closed
            if closed:
                return
            frame = b"data: " + json.dumps(payload, separators=(",", ":")).encode() + b"\n\n"
            try:
                with lock:
                    self.wfile.write(frame)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                # the reader went away; the work keeps going but says nothing
                closed = True

        emit({"type": "start"})
        return emit

    def _v1_change_feed(self) -> None:
        """Authenticated SSE: a tiny "something changed" frame per write.

        Open tabs keep this connection for hours; it is quiet most of the time,
        so it sends a heartbeat comment every 20 seconds and otherwise only the
        scope name that moved. Writes never block on it (the feed drops frames
        for a slow reader); a tab that closes is unsubscribed silently.
        """
        self._require_session()
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def write(frame: str) -> bool:
            try:
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        if not write("retry: 4000\n\n"):
            return
        if not write('data: {"type":"ready"}\n\n'):
            return
        subscriber = FEED.subscribe()
        try:
            while True:
                try:
                    scope = subscriber.get(timeout=20)
                except queue.Empty:
                    if not write(": ping\n\n"):
                        return
                    continue
                frame = json.dumps(
                    {"type": "change", "scope": scope}, separators=(",", ":")
                )
                if not write(f"data: {frame}\n\n"):
                    return
        except (BrokenPipeError, ConnectionResetError, OSError):
            # the tab went away; that is the normal end of this stream
            return
        except Exception:  # noqa: BLE001 - headers are out; just close quietly
            traceback.print_exc()
        finally:
            FEED.unsubscribe(subscriber)

    def _v1_simulate_stream(self) -> None:
        """The same dry run as ``/simulate``, streamed as server-sent events.

        The pipeline calls ``emit`` per stage, so the console can show the work
        as it happens instead of a spinner. The run is still read-only: nothing
        is written to the store and no PayPal object is created.
        """
        session = self._require_session()
        self._limit("simulate", f"user:{session['user']['id']}", RATE_SESSION_PER_MIN)
        body = self._body()
        text = str(body.get("text") or "").strip()
        if not text:
            self._api_error(400, "missing_text", "What should the agent buy?")
        if len(text) > MAX_SIMULATE_CHARS:
            text = text[:MAX_SIMULATE_CHARS]
        agent_id = str(body.get("agent_id") or "").strip() or None
        try:
            agent = veto_pipeline.resolve_agent(STORE, session["workspace"]["id"], agent_id)
        except LookupError as exc:
            code = str(exc) or "no_agent"
            message = ("That agent does not exist." if code == "agent_not_found"
                       else "This workspace has no agents yet.")
            self._api_error(404, code, message)
        workspace = session["workspace"]
        month_spent = STORE.spent_cents(
            workspace["id"], agent_id=agent["id"], since=month_start_iso()
        )

        emit = self._start_sse()
        try:
            result = veto_pipeline.simulate(
                agent,
                text,
                progress=emit,
                month_spent_cents=month_spent,
                workspace_monthly_budget_cents=workspace.get("monthly_budget_cents"),
            )
        except ValueError as exc:
            emit({"type": "error", "error": {"code": "no_intent", "message": str(exc)}})
            return
        except Exception:  # noqa: BLE001 - the stream is already open; report, don't 500
            traceback.print_exc()
            emit({"type": "error",
                  "error": {"code": "server_error", "message": "Something went wrong."}})
            return
        emit({"type": "result", "result": result})

    # -- routes ---------------------------------------------------------
    def _verify_slack(self, raw: bytes) -> bool:
        """Slack signs every request: v0=HMAC-SHA256(signing_secret, "v0:" + ts + ":" + body)."""
        secret = os.environ.get("SLACK_SIGNING_SECRET", "").strip()
        if not secret:
            return True  # dev mode: no secret configured yet
        ts = self.headers.get("X-Slack-Request-Timestamp", "")
        sig = self.headers.get("X-Slack-Signature", "")
        if not ts or not sig:
            return False
        try:
            if abs(time.time() - int(ts)) > 60 * 5:
                return False
        except ValueError:
            return False
        base = ("v0:" + ts + ":").encode() + raw
        want = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, sig)

    def _slack_interact(self) -> None:
        """A human pressed Approve or Veto in Slack. Slack posts a urlencoded form
        with a `payload` JSON blob; we answer with an updated message.

        It is also the URL Slack probes when the app is created, before a signing
        secret exists, so a challenge has to be echoed back or the manifest fails
        with "Request URL did not respond with a challenge"."""
        raw = self._raw()
        if not self._verify_slack(raw):
            return self._json(401, {"error": "bad signature"})
        form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        try:
            payload = json.loads(form.get("payload") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        challenge = payload.get("challenge") or form.get("challenge") or ""
        if challenge and payload.get("type") in ("url_verification", None):
            return self._json(200, {"challenge": challenge})
        actions = payload.get("actions") or []
        if not actions:
            return self._json(200, {"ok": True})
        action = actions[0]
        action_id = action.get("action_id") or ""
        value = action.get("value") or ""
        user = (payload.get("user") or {}).get("username") or (payload.get("user") or {}).get("name") or "a human"
        response_url = payload.get("response_url") or ""

        # A person picked one offer from a /veto-check shortlist.
        if action_id == "veto_pick":
            if response_url:
                self._json(200, {"ok": True})
                threading.Thread(
                    target=self._slack_pick_job,
                    args=(value, user, response_url), daemon=True
                ).start()
                return
            return self._json(200, self._slack_pick_payload(value, user))

        rid = value
        verb = "approve" if action_id == "veto_approve" else "veto"

        # PayPal captures take a second or two; Slack gives us three. Ack first,
        # then replace the message through response_url from a worker thread.
        if response_url:
            self._json(200, {"ok": True})
            threading.Thread(
                target=self._slack_decide_job, args=(rid, verb, user, response_url), daemon=True
            ).start()
            return
        self._json(200, self._slack_decide_payload(rid, verb, user))

    def _slack_decide_job(self, rid: str, verb: str, user: str, response_url: str) -> None:
        post_json(response_url, self._slack_decide_payload(rid, verb, user))

    SLACK_STATUS_HEAD = {
        "awaiting": "Waiting on a human",
        "blocked": "Refused by a rule",
        "captured": "Ordered and captured",
        "approved": "Approved, capture waits for the wallet",
        "vetoed": "Vetoed",
        "voided": "Voided",
    }

    def _slack_money(self, cents: int | None) -> str:
        return "${:,.2f}".format((cents or 0) / 100)

    def _slack_request_blocks(self, record: dict) -> dict:
        """One Slack message for any state of a stored request."""
        agent = record.get("agent") or {}
        head = self.SLACK_STATUS_HEAD.get(record["status"], record["status"])
        money = self._slack_money(record.get("total_cents"))
        reasons = "\n".join(
            "• " + str(reason.get("message") or "") for reason in record.get("reasons") or []
        ) or "• within every rule"
        context = f"{record['id']} · tag `{agent.get('tag') or 'general'}`"
        consent = record.get("consent") or {}
        if consent.get("cart_hash"):
            context += f" · `{consent['cart_hash'][:16]}…`"
        payment = record.get("payment") or {}
        paypal_id = payment.get("capture_id") or payment.get("order_id") or ""
        if paypal_id:
            context += f" · `{paypal_id}`"
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"*{head}* · {record.get('title') or record['id']}\n"
                     f"{money} · {agent.get('name') or 'an agent'}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": reasons}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": context}]},
        ]
        if record["status"] == "awaiting":
            blocks.append({"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Approve"},
                 "style": "primary", "action_id": "veto_approve", "value": record["id"]},
                {"type": "button", "text": {"type": "plain_text", "text": "Veto"},
                 "style": "danger", "action_id": "veto_veto", "value": record["id"]},
            ]})
        return {
            "response_type": "in_channel",
            "text": f"{head} · {record.get('title') or record['id']} · {money}",
            "blocks": blocks,
        }

    def _slack_decide_payload(self, rid: str, verb: str, user: str) -> dict:
        workspace = self._demo_workspace()
        if workspace is None:
            return {"response_type": "ephemeral", "text": "No workspace is seeded."}
        approver = self._workspace_approver(workspace["id"])
        if approver is None:
            return {"response_type": "ephemeral", "text": "This workspace has no approver to sign."}
        actor = {"id": approver["id"], "name": f"@{user} in Slack"}
        try:
            if verb == "approve":
                veto_requests.approve(
                    STORE, workspace=workspace, request_id=rid, user=actor,
                    settlement=SETTLEMENT,
                )
            else:
                veto_requests.veto(
                    STORE, workspace=workspace, request_id=rid, user=actor
                )
        except veto_requests.WalletRequired as exc:
            _changed("requests")
            return {
                "response_type": "ephemeral",
                "text": "The company wallet has to be approved once before this can capture.",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn",
                     "text": "*One payer approval first*\n"
                             "The cart is approved; the capture waits for the wallet. "
                             "Connect it in the console, then press Approve again."}},
                ],
            }
        except veto_requests.RequestError as exc:
            return {"response_type": "ephemeral",
                    "text": f"Could not action {rid}: {exc.message}"}
        record = STORE.request(workspace["id"], rid)
        if record is None:
            return {"response_type": "ephemeral", "text": f"{rid} no longer exists."}
        _changed("requests")
        payload = self._slack_request_blocks(veto_requests.public_request(record))
        payload["replace_original"] = True
        payload["text"] = f"{payload['text']} · actioned by @{user}"
        if payload["blocks"]:
            first = payload["blocks"][0]["text"]["text"]
            payload["blocks"][0]["text"]["text"] = first + f"\n_Actioned by @{user} in Slack_"
        return payload

    # -- veto-check: show the shortlist, then buy the exact pick ----------
    def _slack_check(self, text: str, response_url: str, user: str) -> None:
        if not text:
            return self._json(200, {
                "response_type": "ephemeral",
                "text": "`/veto-check` needs something to look for. "
                        "Try: `/veto-check paper towels`",
            })
        # routing + a catalogue search take longer than Slack's three seconds
        if response_url:
            self._json(200, {
                "response_type": "ephemeral",
                "text": f"Checking the catalogue for “{text[:80]}”…",
            })
            threading.Thread(
                target=self._slack_check_job,
                args=(text, response_url, user), daemon=True
            ).start()
            return
        self._json(200, self._slack_check_payload(text))

    def _slack_check_job(self, text: str, response_url: str, user: str) -> None:
        message = self._slack_check_payload(text)
        message.setdefault("response_type", "ephemeral")
        post_json(response_url, message)

    def _slack_check_payload(self, text: str) -> dict:
        workspace = self._demo_workspace()
        if workspace is None:
            return {"response_type": "ephemeral", "text": "No workspace is seeded."}
        try:
            agent, _decision = veto_routing.route_request(STORE, workspace, text)
        except Exception:  # noqa: BLE001 - routing has its own fallback; be defensive
            traceback.print_exc()
            agent = None
        if agent is None:
            return {"response_type": "ephemeral", "text": "This workspace has no agents yet."}
        try:
            check_id, result = veto_check.remember(agent, text)
        except ValueError:
            return {
                "response_type": "ephemeral",
                "text": "I couldn't find a product to check in that message. "
                        "Try naming it plainly, e.g. `paper towels`.",
            }
        except Exception:  # noqa: BLE001 - the catalogue may be down
            traceback.print_exc()
            return {"response_type": "ephemeral",
                    "text": "The catalogue did not answer. Try again."}
        return veto_slack.check_payload(
            check_id,
            query=result["query"],
            agent_name=agent.get("name") or "agent",
            qty=result["qty"],
            candidates=result["candidates"],
            source="Channel3" if result["source"] == "channel3" else "local catalogue",
            checked=result["checked"],
            base_url=public_base(),
        )

    def _slack_pick_payload(self, value: str, user: str) -> dict:
        try:
            check_id, raw_index = value.split(":", 1)
            index = int(raw_index)
        except (AttributeError, ValueError):
            return {"response_type": "ephemeral",
                    "text": "That choice is not one I can price."}
        picked = veto_check.resolve(check_id, index)
        if picked is None:
            return {"response_type": "ephemeral",
                    "text": "That check expired. Run `/veto-check` again."}
        workspace = self._demo_workspace()
        if workspace is None:
            return {"response_type": "ephemeral", "text": "No workspace is seeded."}
        agent = STORE.agent(workspace["id"], picked["agent"]["id"])
        if agent is None:
            return {"response_type": "ephemeral",
                    "text": "The agent behind that check is gone."}
        try:
            record = veto_requests.create(
                STORE,
                workspace=workspace,
                agent=agent,
                text=picked["text"],
                origin="slack",
                requester=user.title() if user else None,
                settlement=SETTLEMENT,
                pinned_lines=[picked["line"]],
            )
        except veto_requests.WalletRequired:
            return {
                "response_type": "ephemeral",
                "text": "The company wallet has to be approved once before this can "
                        "capture. Connect it in the console, then choose again.",
            }
        except veto_requests.RequestError as exc:
            return {"response_type": "ephemeral",
                    "text": f"Could not price that pick: {exc.message}"}
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return {"response_type": "ephemeral",
                    "text": "The pick failed. Nothing was ordered."}
        record = STORE.request(workspace["id"], record["id"]) or record
        _changed("requests")
        payload = self._slack_request_blocks(veto_requests.public_request(record))
        payload["replace_original"] = True
        payload["text"] = f"{payload['text']} · chosen by @{user}"
        if payload.get("blocks"):
            payload["blocks"][0]["text"]["text"] += (
                f"\n_Chosen by @{user} from a veto-check_"
            )
        return payload

    def _slack_pick_job(self, value: str, user: str, response_url: str) -> None:
        message = self._slack_pick_payload(value, user)
        message.setdefault("response_type", "ephemeral")
        post_json(response_url, message)

    def _slack_veto(self) -> None:
        """The slash command itself. Slack sends command/text/user_name/channel_name;
        we answer in Slack's own shape, so the same URL works as a Slack app endpoint
        and from curl (sign the body, or unset SLACK_SIGNING_SECRET while testing):
            curl -X POST localhost:8787/api/slack/veto \
                 -d 'command=/veto&text=2 boxes of capsules from kettle supply&user_name=dan'

        Slack signs the slash command exactly like it signs buttons, and this door can
        create and capture, so the signature is checked before anything else happens.
        Searching the catalogue takes 1-4s, and Slack drops the slash command after
        three, so a real Slack call gets an immediate "on it" and the result arrives
        through response_url. curl (no response_url) still gets the answer inline.
        """
        raw = self._raw()
        if not self._verify_slack(raw):
            return self._json(401, {"error": "bad signature"})
        form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        user = (form.get("user_name") or "someone").strip() or "someone"
        allowed, retry = LIMITER.allow(
            f"slack:{user}", limit=RATE_SLACK_PER_MIN, per_seconds=60
        )
        if not allowed:
            self._json(200, {
                "response_type": "ephemeral",
                "text": f"Easy — too many Veto commands. Try again in "
                        f"{max(1, int(round(retry)))}s.",
            })
            return
        text = (form.get("text") or "").strip()
        command = (form.get("command") or "/veto").strip()
        response_url = form.get("response_url") or ""

        # Several Slack commands share this one Request URL; the payload names
        # the command that was actually typed.
        if command == "/veto-status":
            return self._json(200, self._slack_status_payload(text))
        if command == "/veto-budget":
            return self._json(200, self._slack_budget_payload(text))
        if command == "/veto-routines":
            return self._json(200, self._slack_routines_payload(text))
        if command == "/veto-check":
            return self._slack_check(text, response_url, user)
        if command == "/veto-help" or text.lower() in ("help", "?", "commands"):
            return self._json(200, veto_slack.help_payload(public_base()))
        if command != "/veto":
            return self._json(200, veto_slack.help_payload(public_base()))

        if text and response_url:
            self._json(200, {
                "response_type": "ephemeral",
                "text": f"Checking the charter for “{text[:80]}”…",
            })
            threading.Thread(target=self._slack_veto_job, args=(form, response_url), daemon=True).start()
            return
        self._json(200, self._slack_veto_payload(form))

    def _slack_status_payload(self, text: str) -> dict:
        """What waits for a signature, with the same Approve/Veto buttons as a request."""
        workspace = self._demo_workspace()
        if workspace is None:
            return {"response_type": "ephemeral", "text": "No workspace is seeded."}
        agents = STORE.list_agents(workspace["id"])
        only = veto_slack.match_agents(text, agents)
        pending = STORE.list_requests(
            workspace["id"], status="awaiting", limit=10, oldest_first=True
        )
        if only is not None:
            pending = [row for row in pending if row["agent_id"] in only]
            if not pending:
                return {
                    "response_type": "ephemeral",
                    "text": f"Nothing is waiting for “{text}”. Try `/veto-status` for everything.",
                }
        pending = pending[:5]
        counts = STORE.overview_counts(workspace["id"], month_start=month_start_iso())
        routines = STORE.list_routines(workspace["id"])
        nxt = min(routines, key=lambda row: row["next_run_at"]) if routines else None
        next_routine = (
            {
                "name": nxt["name"],
                "agent_name": nxt.get("agent_name") or "",
                "next_run_at": nxt["next_run_at"],
            }
            if nxt
            else None
        )
        return veto_slack.status_payload(
            pending,
            committed_cents=int(counts.get("committed_cents") or 0),
            capture_owed=STORE.count_requests(workspace["id"], status="approved"),
            next_routine=next_routine,
            base_url=public_base(),
        )

    def _slack_budget_payload(self, text: str) -> dict:
        """This month's money: workspace total, then one line per agent."""
        workspace = self._demo_workspace()
        if workspace is None:
            return {"response_type": "ephemeral", "text": "No workspace is seeded."}
        agents = STORE.list_agents(workspace["id"])
        only = veto_slack.match_agents(text, agents)
        totals = STORE.agent_month_totals(workspace["id"], since=month_start_iso())
        rows = []
        for agent in agents:
            if only is not None and agent["id"] not in only:
                continue
            month = totals.get(agent["id"]) or {}
            rows.append({
                "name": agent["name"],
                "monthly_cap_cents": agent.get("monthly_cap_cents") or 0,
                "spent_cents": int(month.get("spent_cents") or 0),
                "committed_cents": int(month.get("committed_cents") or 0),
            })
        if only is not None and not rows:
            return {
                "response_type": "ephemeral",
                "text": f"No agent matches “{text}”. Try `/veto-budget` for the workspace.",
            }
        counts = STORE.overview_counts(workspace["id"], month_start=month_start_iso())
        return veto_slack.budget_payload(
            agents=rows,
            budget_cents=int(workspace.get("monthly_budget_cents") or 0),
            settled_cents=int(counts.get("settled_month_cents") or 0),
            committed_cents=int(counts.get("committed_cents") or 0),
            base_url=public_base(),
        )

    def _slack_routines_payload(self, text: str) -> dict:
        """The standing jobs: cadence, envelope, next run, last outcome."""
        workspace = self._demo_workspace()
        if workspace is None:
            return {"response_type": "ephemeral", "text": "No workspace is seeded."}
        agents = STORE.list_agents(workspace["id"])
        only = veto_slack.match_agents(text, agents)
        rows = STORE.list_routines(workspace["id"])
        if only is not None:
            rows = [row for row in rows if row["agent_id"] in only]
            if not rows:
                return {
                    "response_type": "ephemeral",
                    "text": f"No routine matches “{text}”. Try `/veto-routines`.",
                }
        enriched = []
        for row in rows:
            public = veto_routines.public_routine(STORE, workspace, row)
            runs = STORE.routine_runs_for(workspace["id"], row["id"], limit=1)
            enriched.append({
                "name": public["name"],
                "agent_name": row.get("agent_name") or "",
                "cadence_word": public["cadence_word"],
                "next_run_at": public["next_run_at"],
                "envelope_cents": public["envelope_cents"],
                "spent_cents": public["spent_cents"],
                "last": runs[0] if runs else None,
            })
        return veto_slack.routines_payload(enriched, base_url=public_base())

    def _slack_veto_job(self, form: dict, response_url: str) -> None:
        message = self._slack_veto_payload(form)
        message.setdefault("response_type", "in_channel")
        post_json(response_url, message)

    def _slack_veto_payload(self, form: dict) -> dict:
        command = form.get("command") or "/veto"
        text = (form.get("text") or "").strip()
        channel = "#" + (form.get("channel_name") or "office-ops").lstrip("#")
        requester = (form.get("user_name") or "dan").strip() or "dan"
        if not text:
            return {
                "response_type": "ephemeral",
                "text": f"`{command}` needs something to buy. Try: `{command} 2 packs of toilet paper`",
            }
        workspace = self._demo_workspace()
        if workspace is None:
            return {"response_type": "ephemeral", "text": "No workspace is seeded."}
        # the channel is where it was typed; the model decides whose charter judges it
        agent, decision = veto_routing.route_request(STORE, workspace, text)
        if agent is None:
            return {"response_type": "ephemeral", "text": "This workspace has no agents yet."}
        try:
            record = veto_requests.create(
                STORE,
                workspace=workspace,
                agent=agent,
                text=text,
                origin="slack",
                channel=channel,
                requester=requester.title(),
                settlement=SETTLEMENT,
                route=veto_routing.trail_note(decision),
            )
        except veto_requests.WalletRequired:
            return {
                "response_type": "ephemeral",
                "text": "The company wallet has to be approved once before this can capture.",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn",
                     "text": "*One payer approval first*\n"
                             "The cart was judged; the capture waits for the wallet. "
                             "Connect it in the console, then send the request again."}},
                ],
            }
        except veto_requests.RequestError as exc:
            return {"response_type": "ephemeral",
                    "text": f"Could not price that: {exc.message}"}
        record = STORE.request(workspace["id"], record["id"])
        if record is None:
            return {"response_type": "ephemeral", "text": "The request could not be read back."}
        _changed("requests")
        return self._slack_request_blocks(veto_requests.public_request(record))


    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # v2 routes first, with their own JSON error envelope. Legacy routes
        # below keep exactly the behaviour they had before.
        try:
            if self._v2_get(path):
                return
        except ApiError as exc:
            return self._send_api_error(exc)
        except veto_requests.RequestError as exc:
            return self._send_request_error(exc)
        except Exception:  # noqa: BLE001 - a v2 failure must not break the legacy demo
            traceback.print_exc()
            return self._send_api_error(
                ApiError(500, "server_error", "Something went wrong.")
            )
        if path == "/api/slack/veto" and urlparse(self.path).query.startswith("ssl_check"):
            return self._json(200, {"ok": True})
        if path == "/connect":
            return self._file("demo/connect.html")
        if path == "/api/health":
            signed = bool((os.environ.get("SLACK_SIGNING_SECRET") or "").strip())
            bot = bool((os.environ.get("SLACK_BOT_TOKEN") or "").strip())
            saved = {**ENGINE._env_vault(), **ENGINE._read_vault_state()}
            vault = ENGINE.vault_id or saved.get("vault_id")
            try:
                db_ok = STORE.one("SELECT 1 AS ok") is not None
                db_stats = {
                    "db": "ok" if db_ok else "error",
                    "seeded": bool(SEEDED),
                    "workspaces": STORE.count_workspaces(),
                    "users": STORE.count_users(),
                    "agents": STORE.count_agents(),
                    "requests": STORE.one("SELECT COUNT(*) AS n FROM requests")["n"],
                    "captured": STORE.one(
                        "SELECT COUNT(*) AS n FROM requests WHERE status = 'captured'"
                    )["n"],
                }
            except Exception:  # noqa: BLE001 - health must answer even if the db is down
                traceback.print_exc()
                db_stats = {
                    "db": "error", "seeded": bool(SEEDED),
                    "workspaces": 0, "users": 0, "agents": 0, "requests": 0,
                    "captured": 0,
                }
            return self._json(200, {
                "ok": True, "paypal": ENGINE.paypal_mode, "llm": ENGINE.llm_mode,
                "catalog": ENGINE.catalog_mode, "vault": bool(vault),
                "settlement": SETTLEMENT.mode,
                "vault_pending": bool(ENGINE.pending_setup or saved.get("pending_setup")),
                "public_url": PUBLIC_URL or None,
                "slack": {"signing_secret": signed, "bot_token": bot,
                          "app_ready": signed and bot},
                **db_stats,
            })
        return self._file(path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # v2 routes first; Slack stays on its own (unchanged) path below.
        try:
            if self._v2_post(path):
                return
        except ApiError as exc:
            return self._send_api_error(exc)
        except veto_requests.RequestError as exc:
            return self._send_request_error(exc)
        except Exception:  # noqa: BLE001 - a v2 failure must not break Slack/legacy
            traceback.print_exc()
            return self._send_api_error(
                ApiError(500, "server_error", "Something went wrong.")
            )
        if path == "/api/slack/veto":
            return self._slack_veto()
        if path == "/api/slack/interact":
            return self._slack_interact()
        body = self._body()
        try:
            if path == "/api/connect":
                if body.get("reset"):
                    ENGINE.vault_id = None
                    ENGINE.pending_setup = None
                    ENGINE._vault_file.unlink(missing_ok=True)
                vault = ENGINE.ensure_vault()
                return self._json(200, {"vault_id": vault})

            if path == "/api/connect/finish":
                return self._json(200, {"vault_id": ENGINE.finish_connect()})

            return self._json(404, {"error": "unknown route"})

        except VaultRequired as exc:
            return self._json(428, {
                "error": "payer approval required",
                "approve_url": exc.approve_url,
                "setup_id": exc.setup_id,
                "connect": connect_url("/connect"),
                "next": "open approve_url as the sandbox buyer, then POST /api/connect/finish",
            })
        except ApprovalPending as exc:
            return self._json(425, {
                "error": "waiting on the PayPal approval",
                "setup_id": exc.setup_id,
                "connect": connect_url("/connect"),
                "next": "press Approve in the open PayPal tab, then POST /api/connect/finish",
            })
        except KeyError as exc:
            return self._json(404, {"error": f"no such request: {exc}"})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if self._v2_patch(path):
                return
        except ApiError as exc:
            return self._send_api_error(exc)
        except veto_requests.RequestError as exc:
            return self._send_request_error(exc)
        except Exception:  # noqa: BLE001 - keep the legacy routes untouched
            traceback.print_exc()
            return self._send_api_error(
                ApiError(500, "server_error", "Something went wrong.")
            )
        return self._json(404, {"error": "unknown route"})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if self._v2_delete(path):
                return
        except ApiError as exc:
            return self._send_api_error(exc)
        except veto_requests.RequestError as exc:
            return self._send_request_error(exc)
        except Exception:  # noqa: BLE001 - a v2 failure must not break anything else
            traceback.print_exc()
            return self._send_api_error(
                ApiError(500, "server_error", "Something went wrong.")
            )
        return self._json(404, {"error": "unknown route"})


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"\n  Veto runner")
    print(f"  app       http://{HOST}:{PORT}/")
    print(f"  wallet    http://127.0.0.1:{PORT}/connect")
    print(f"  paypal    {ENGINE.paypal_mode}   llm  {ENGINE.llm_mode}   catalog {ENGINE.catalog_mode}")
    if PUBLIC_URL:
        print(f"  public    {PUBLIC_URL}")
        print(f"  slack     {PUBLIC_URL}/api/slack/veto   {PUBLIC_URL}/api/slack/interact")
    # the schedule lives in this process: due routines run through the same pipeline
    veto_routines.start_scheduler(STORE, SETTLEMENT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
