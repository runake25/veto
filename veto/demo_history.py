"""Honest demo history — the real pipeline, through the product's own API.

The hosted demo runs on a free instance whose disk is ephemeral: a sleep or a
restart resets the store. Rather than show a judge an empty Inbox, the runner can
replay a short month of activity on boot (``VETO_DEMO_HISTORY=1``). Nothing is
written to SQLite by hand — every request runs the live pipeline (model
extraction, Channel3 product search, the agent's rules, consent, PayPal sandbox
capture), decisions are real calls, and API-key requests use a real minted key.

``spread`` then arranges the timestamps across the current month so the monthly
views (Analytics by day, Inbox dates) read like a company mid-month. Only
timestamps move: amounts, rules, consent hashes and PayPal ids are untouched, and
the boot replay documents that in the log. One request is left "today" with a
fresh consent window so a visitor always has something to sign.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

DEFAULT_BASE = "http://127.0.0.1:8787"

# (text, what to do when the outcome is "a human has to sign")
# auto: expected to capture by itself; capture: leave awaiting -> approve;
# veto: leave awaiting -> veto; void: leave awaiting -> void; await: leave it.
CARTS: list[tuple[str, str]] = [
    # pantry — small restocks, the routine workhorse
    ("2 packs of bamboo toilet paper", "auto"),
    ("dish soap and hand soap for the kitchen", "auto"),
    ("a 12 pack of paper towels", "auto"),
    # studio — inside the per-unit cap, one larger order approved
    ("a box of envelopes and 2 packs of pens", "auto"),
    ("printer paper and a box of sticky notes", "auto"),
    # desk — bigger equipment, a mix of self-serve and signed
    ("an ergonomic standing mat for the studio", "capture"),
    ("a mechanical keyboard for the desk", "auto"),
    ("a USB-C dock for the edit bay", "auto"),
    ("pens and sticky notes for the studio", "auto"),
    ("an ultrawide monitor for the design desk", "capture"),
    ("a standing desk for the office", "capture"),
    # client lunch — the hospitality budget
    ("lunch for the client meeting", "capture"),
    ("coffee and pastries for the team standup", "auto"),
    # a lapsed / withdrawn request and a couple of refusals
    ("an ergonomic office chair for the studio", "veto"),
    ("a second 27-inch monitor for the edit bay", "void"),
    # left waiting for the visitor, with a fresh window
    ("a 27-inch 4K monitor for the edit bay", "await"),
]

# Requests the seeded charters must refuse: a category the agent may not buy, a
# merchant it does not carry, and a cart over the per-order cap.
BLOCKED_CARTS = [
    "a coffee machine for the kitchen",
    "sit-stand desk frame with a walnut top",
]

# Requests that go in through a real agent API key (source: API key): one that
# captures, one the studio charter must refuse because it only buys stationery.
API_KEY_CARTS = [
    ("2 packs of bamboo toilet paper", "pantry"),
    ("a box of envelopes and 2 packs of pens", "studio"),
]
API_KEY_BLOCKED = [
    ("a standing desk for the studio", "studio"),
]


class Client:
    """The same HTTP surface a person or an agent uses; no private shortcuts."""

    def __init__(self, base: str, headers: dict | None = None) -> None:
        self.base = base.rstrip("/")
        self.cookies: dict[str, str] = {}
        self.headers = dict(headers or {})

    def call(self, method: str, path: str, body=None):
        data = None
        headers = dict(self.headers)
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                for cookie in res.headers.get_all("Set-Cookie") or []:
                    pair = cookie.split(";")[0]
                    if "=" in pair:
                        name, _, value = pair.partition("=")
                        self.cookies[name] = value
                return res.status, json.loads(res.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"raw": raw}

    def data(self, method: str, path: str, body=None):
        status, payload = self.call(method, path, body)
        if status != 200 and status != 201:
            raise RuntimeError(f"{method} {path} -> {status}: {payload}")
        return payload.get("data") or {}


def summarise(request: dict) -> str:
    agent = (request.get("agent") or {}).get("name") or "?"
    amount = f"${(request.get('total_cents') or 0) / 100:,.2f}"
    return (
        f"{request.get('seq')} · {request.get('title')} · {amount} · "
        f"{request.get('status')} · {agent}"
    )


def _decide(client: Client, request: dict, action: str, log) -> None:
    if action == "void":
        decided = client.data("POST", f"/api/v1/requests/{request['id']}/void")["request"]
    else:
        decided = client.data(
            "POST", f"/api/v1/requests/{request['id']}/decision", {"action": action}
        )["request"]
    log(f"  {action:<8} {summarise(decided)}")


def _mint_key(admin: Client, agent_id: str, log) -> str | None:
    try:
        created = admin.data("POST", f"/api/v1/agents/{agent_id}/keys")
    except RuntimeError as exc:
        log(f"  api key  skipped ({exc})")
        return None
    secret = created.get("secret")
    if secret:
        log(f"  api key  minted for {agent_id}")
    return secret


def populate(
    base: str = DEFAULT_BASE,
    *,
    skip_routines: bool = False,
    log=print,
) -> dict:
    """Create the demo history through the public API and return a summary."""
    client = Client(base)
    client.data("POST", "/api/v1/demo-session")
    log(f"demo session opened on {client.base}")

    created: list[dict] = []
    for text, intent in CARTS:
        try:
            request = client.data("POST", "/api/v1/requests", {"text": text})["request"]
        except RuntimeError as exc:
            log(f"  skipped  {text!r} ({exc})")
            continue
        log(f"  created  {summarise(request)}  ({intent})")
        created.append(request)
        try:
            if request.get("status") == "awaiting" and intent in ("capture", "veto", "void"):
                _decide(client, request, "approve" if intent == "capture" else (
                    "veto" if intent == "veto" else "void"
                ), log)
            elif request.get("status") == "awaiting" and intent == "auto":
                # the small cart still needs a signature: approve it so the month has
                # captures rather than a pile of stale consents
                _decide(client, request, "approve", log)
        except RuntimeError as exc:
            log(f"  skipped  decision on {request.get('id')} ({exc})")
        time.sleep(0.6)

    for text in BLOCKED_CARTS:
        try:
            request = client.data("POST", "/api/v1/requests", {"text": text})["request"]
            log(f"  created  {summarise(request)}  (expected refusal)")
        except RuntimeError as exc:
            log(f"  skipped  {text!r} ({exc})")

    # One real agent key per door, used for two requests (source: API key).
    agents = client.data("GET", "/api/v1/agents")["agents"]
    by_tag = {agent.get("tag"): agent for agent in agents}
    admin: Client | None = None
    try:
        from veto.seed import ADMIN_EMAIL, DEMO_PASSWORD

        admin = Client(base)
        admin.data("POST", "/api/v1/login", {"email": ADMIN_EMAIL, "password": DEMO_PASSWORD})
    except Exception:  # noqa: BLE001 - key minting is optional decoration
        admin = None
    if admin is not None:
        for text, tag in API_KEY_CARTS:
            agent = by_tag.get(tag)
            if agent is None:
                continue
            secret = _mint_key(admin, agent["id"], log)
            if not secret:
                continue
            keyed = Client(base, headers={"Authorization": f"Bearer {secret}"})
            request = keyed.data("POST", "/api/v1/requests", {"text": text})["request"]
            log(f"  created  {summarise(request)}  (api key)")
            created.append(request)
        for text, tag in API_KEY_BLOCKED:
            agent = by_tag.get(tag)
            if agent is None:
                continue
            secret = _mint_key(admin, agent["id"], log)
            if not secret:
                continue
            keyed = Client(base, headers={"Authorization": f"Bearer {secret}"})
            request = keyed.data("POST", "/api/v1/requests", {"text": text})["request"]
            log(f"  created  {summarise(request)}  (api key, expected refusal)")

    if not skip_routines:
        for agent in agents:
            routine = agent.get("routine")
            if not routine:
                continue
            try:
                result = client.data("POST", f"/api/v1/agents/{agent['id']}/routine/run")
            except RuntimeError as exc:
                log(f"  routine  {routine['name']} skipped ({exc})")
                continue
            run = result.get("run") or {}
            amount = f"${(run.get('amount_cents') or 0) / 100:,.2f}"
            log(
                f"  routine  {routine['name']} · {run.get('outcome')} · {amount}"
                f" · next {routine.get('next_run_at')}"
            )
            time.sleep(0.6)

    rows = client.data("GET", "/api/v1/requests?limit=100")["requests"]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("status") or "?"] = counts.get(row.get("status") or "?", 0) + 1
    log(f"{len(rows)} requests in the record ({counts})")
    return {"requests": len(rows), "captured": counts.get("captured", 0), **counts}


def _shift_iso(value: str | None, delta: timedelta) -> str | None:
    if not value:
        return value
    try:
        return (datetime.fromisoformat(value) + delta).isoformat(timespec="seconds")
    except ValueError:
        return value


# Days before today for the replayed history (newest last). A few days carry more
# than one request, like a company that buys in bursts.
SPREAD_DAYS = [25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10,
               9, 8, 7, 6, 5, 4, 3, 2, 1]


def spread(store, *, now: datetime | None = None, log=print) -> int:
    """Arrange the replayed history across the current month.

    Only timestamps move. The newest awaiting request keeps a fresh consent
    window (today), so the visitor always has a cart to sign; the rest are
    distributed over the last weeks so Analytics has a month and the Inbox has
    dates. Returns the number of requests re-dated.
    """
    now = now or datetime.now(timezone.utc)
    conn = store.conn
    rows = conn.execute(
        "SELECT id, status, created_at, decided_at FROM requests ORDER BY seq"
    ).fetchall()
    if not rows:
        return 0

    awaiting = [row for row in rows if row["status"] == "awaiting"]
    keep_id = awaiting[-1]["id"] if awaiting else None

    shifted = 0
    with store.transaction():
        for index, row in enumerate(rows):
            if row["id"] == keep_id:
                minutes = int(os.environ.get("VETO_CONSENT_MINUTES", "10") or 10)
                issued = now - timedelta(minutes=2)
                expires = now + timedelta(minutes=max(5, minutes - 2))
                conn.execute(
                    "UPDATE consents SET issued_at=?, expires_at=?, used_at=NULL "
                    "WHERE request_id=?",
                    (issued.isoformat(timespec="seconds"),
                     expires.isoformat(timespec="seconds"), row["id"]),
                )
                conn.execute(
                    "UPDATE requests SET created_at=?, decided_at=NULL WHERE id=?",
                    (issued.isoformat(timespec="seconds"), row["id"]),
                )
                continue

            days = SPREAD_DAYS[index % len(SPREAD_DAYS)]
            day = now - timedelta(days=days)
            new_created = day.replace(
                hour=8 + (index * 3) % 10, minute=(index * 17) % 60, second=0, microsecond=0
            )
            old_created = datetime.fromisoformat(row["created_at"])
            delta = new_created - old_created

            conn.execute(
                "UPDATE requests SET created_at=?, decided_at=? WHERE id=?",
                (_shift_iso(row["created_at"], delta),
                 _shift_iso(row["decided_at"], delta), row["id"]),
            )
            for event in conn.execute(
                "SELECT id, at FROM events WHERE request_id=?", (row["id"],)
            ).fetchall():
                conn.execute(
                    "UPDATE events SET at=? WHERE id=?",
                    (_shift_iso(event["at"], delta), event["id"]),
                )
            for consent in conn.execute(
                "SELECT id, issued_at, expires_at, used_at FROM consents WHERE request_id=?",
                (row["id"],),
            ).fetchall():
                conn.execute(
                    "UPDATE consents SET issued_at=?, expires_at=?, used_at=? WHERE id=?",
                    (_shift_iso(consent["issued_at"], delta),
                     _shift_iso(consent["expires_at"], delta),
                     _shift_iso(consent["used_at"], delta), consent["id"]),
                )
            for payment in conn.execute(
                "SELECT id, created_at, updated_at FROM payments WHERE request_id=?",
                (row["id"],),
            ).fetchall():
                conn.execute(
                    "UPDATE payments SET created_at=?, updated_at=? WHERE id=?",
                    (_shift_iso(payment["created_at"], delta),
                     _shift_iso(payment["updated_at"], delta), payment["id"]),
                )
            for run in conn.execute(
                "SELECT id, due_at, started_at, finished_at FROM routine_runs "
                "WHERE request_id=?",
                (row["id"],),
            ).fetchall():
                conn.execute(
                    "UPDATE routine_runs SET due_at=?, started_at=?, finished_at=? WHERE id=?",
                    (_shift_iso(run["due_at"], delta),
                     _shift_iso(run["started_at"], delta),
                     _shift_iso(run["finished_at"], delta), run["id"]),
                )
            shifted += 1

    log(f"  spread   {shifted} requests across the last {SPREAD_DAYS[0]} days")
    return shifted
