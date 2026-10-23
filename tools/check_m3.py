#!/usr/bin/env python3
"""M3 regression harness: the request board, end to end, over HTTP.

Runs against a live runner and asserts the whole loop the way the app and Slack
use it: an agent request is priced, judged, stored, consented and captured; a
review request waits and is then approved; a veto and a block write no money; an
agent API key can open a request and stops working when revoked; the Overview
figures reconcile with the records.

    python3 server.py                                   # terminal 1
    python3 tools/check_m3.py                           # terminal 2

    python3 tools/check_m3.py --base http://127.0.0.1:8788 --json

The pipeline calls a model and a catalogue, so a run takes ~30-60s. Repeated runs
add real (not seeded) history to the demo workspace; point --base at a scratch
runner with its own VETO_DB when you want a clean slate.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8787"
AS_JSON = False
RESULTS: list[dict] = []


# ---------------------------------------------------------------------------
# tiny HTTP client with one cookie jar
# ---------------------------------------------------------------------------
class Client:
    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def call(self, method: str, path: str, body=None, headers=None):
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        if self.cookies:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                raw = res.read().decode()
                for cookie in res.headers.get_all("Set-Cookie") or []:
                    pair = cookie.split(";")[0]
                    if "=" in pair:
                        name, _, value = pair.partition("=")
                        self.cookies[name] = value
                return res.status, json.loads(raw) if raw.strip().startswith("{") else raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"label": label, "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"{mark}  {label}" + (f" · {detail}" if detail else ""))
    return ok


def data(payload):
    return payload.get("data") if isinstance(payload, dict) else None


def error_code(payload):
    if isinstance(payload, dict):
        return (payload.get("error") or {}).get("code")
    return None


def find_agent(client: Client, name: str) -> dict:
    _, payload = client.call("GET", "/api/v1/agents")
    return next(agent for agent in data(payload)["agents"] if agent["name"] == name)


def create_request(client: Client, text: str, agent_id: str | None = None, headers=None):
    body = {"text": text}
    if agent_id:
        body["agent_id"] = agent_id
    status, payload = client.call("POST", "/api/v1/requests", body, headers=headers)
    record = data(payload) or {}
    return status, payload, record.get("request") or {}


def record(client: Client, request_id: str) -> dict:
    _, payload = client.call("GET", f"/api/v1/requests/{request_id}")
    return (data(payload) or {}).get("request") or {}


def overview(client: Client) -> dict:
    _, payload = client.call("GET", "/api/v1/overview")
    return (data(payload) or {}) or {}


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def run_checks() -> bool:
    started = time.time()
    approver = Client()
    status, payload = approver.call("POST", "/api/v1/demo-session", {})
    if not check("demo session", status == 200, f"HTTP {status}"):
        return False

    status, payload = approver.call("GET", "/api/health")
    health = payload if isinstance(payload, dict) else {}
    check(
        "health",
        status == 200 and health.get("db") == "ok",
        f"db={health.get('db')} settlement={health.get('settlement')} requests={health.get('requests')}",
    )

    before = overview(approver)
    check(
        "overview reads",
        bool(before) and "counts" in before,
        f"needs={before.get('counts', {}).get('needs_you')} settled=${before.get('counts', {}).get('settled_month_cents', 0) / 100:,.2f}",
    )

    # 1. inside the rules: priced, consented, captured without asking
    t0 = time.time()
    status, payload, auto = create_request(approver, "2 packs of toilet paper")
    ok = status == 201 and auto.get("status") in ("captured", "approved")
    check(
        "auto request",
        ok,
        f"{auto.get('id')} {auto.get('status')} ${auto.get('total_cents', 0) / 100:,.2f} "
        f"{(time.time() - t0):.1f}s",
    )
    if auto:
        auto_record = record(approver, auto["id"])
        check(
            "auto record: consent + payment + events",
            bool(auto_record.get("consent", {}).get("used_at"))
            and bool((auto_record.get("payment") or {}).get("capture_id"))
            and {"extract", "decision", "consent", "capture"}
            <= {event["kind"] for event in auto_record.get("events", [])},
            f"consent={bool(auto_record.get('consent'))} "
            f"payment={(auto_record.get('payment') or {}).get('status')} "
            f"events={len(auto_record.get('events', []))}",
        )
        check(
            "auto record: no nonce leaks",
            "nonce" not in json.dumps(auto_record),
            f"{len(json.dumps(auto_record))} bytes",
        )

    # 2. over a review rule: it waits, then a human signs and it captures
    desk = find_agent(approver, "Desk kit")
    t0 = time.time()
    status, payload, review = create_request(approver, "1 monitor for the studio", desk["id"])
    awaiting = review.get("status") == "awaiting"
    check(
        "review request waits",
        status == 201 and awaiting,
        f"{review.get('id')} {review.get('status')} ${review.get('total_cents', 0) / 100:,.2f} "
        f"{(time.time() - t0):.1f}s rules={[r['rule'] for r in review.get('reasons', [])]}",
    )
    if awaiting:
        counts = overview(approver).get("counts", {})
        check(
            "review is committed, not spent",
            counts.get("committed_cents", 0) >= review.get("total_cents", 0)
            and counts.get("needs_you", 0) >= 1,
            f"needs={counts.get('needs_you')} committed=${counts.get('committed_cents', 0) / 100:,.2f}",
        )
        status, payload = approver.call(
            "POST", f"/api/v1/requests/{review['id']}/decision", {"action": "approve"}
        )
        approved = (data(payload) or {}).get("request") or {}
        check(
            "approve captures and names the approver",
            status == 200
            and approved.get("status") == "captured"
            and bool(approved.get("approver_user_id"))
            and bool((approved.get("consent") or {}).get("used_at")),
            f"HTTP {status} status={approved.get('status')} "
            f"capture={(approved.get('payment') or {}).get('capture_id')}",
        )
        check(
            "approving twice is refused",
            approver.call(
                "POST", f"/api/v1/requests/{review['id']}/decision", {"action": "approve"}
            )[0]
            == 409,
            "HTTP 409",
        )

    # 3. a second review request is vetoed: no consent used, no PayPal object
    status, payload, to_veto = create_request(approver, "1 monitor for the studio", desk["id"])
    if to_veto.get("status") == "awaiting":
        status, payload = approver.call(
            "POST", f"/api/v1/requests/{to_veto['id']}/decision", {"action": "veto"}
        )
        vetoed = record(approver, to_veto["id"])
        check(
            "veto writes no money",
            status == 200
            and vetoed.get("status") == "vetoed"
            and not vetoed.get("payments")
            and not (vetoed.get("consent") or {}).get("used_at"),
            f"HTTP {status} status={vetoed.get('status')} payments={len(vetoed.get('payments', []))}",
        )
    else:
        check("veto fixture", False, f"expected awaiting, got {to_veto.get('status')}")

    # 4. a catalogue refusal is stored as a block with no consent at all
    status, payload, blocked = create_request(approver, "a bag of coffee beans")
    blocked_record = record(approver, blocked["id"]) if blocked else {}
    check(
        "catalogue refusal is a stored block",
        status == 201
        and blocked.get("status") == "blocked"
        and "catalogue" in [reason["rule"] for reason in blocked_record.get("reasons", [])]
        and not blocked_record.get("payments")
        and blocked_record.get("consent") is None,
        f"{blocked.get('id')} {blocked.get('status')} rules={[r['rule'] for r in blocked_record.get('reasons', [])]}",
    )

    # 5. the overview reconciles with the records it owns
    after = overview(approver)
    counts = after.get("counts", {})
    expected_settled = 0
    if auto.get("status") == "captured":
        expected_settled += auto.get("total_cents", 0)
    if review.get("status") == "captured":
        expected_settled += review.get("total_cents", 0)
    check(
        "overview settled >= these captures",
        counts.get("settled_month_cents", 0) >= expected_settled > 0,
        f"settled=${counts.get('settled_month_cents', 0) / 100:,.2f} this run=${expected_settled / 100:,.2f}",
    )
    _, payload = approver.call("GET", "/api/v1/requests?limit=100")
    rows = (data(payload) or {}).get("requests", [])
    listed = {row["id"]: row["status"] for row in rows}
    expected = {
        auto.get("id"): auto.get("status"),
        review.get("id"): "captured",
        to_veto.get("id"): "vetoed",
        blocked.get("id"): "blocked",
    }
    check(
        "request list carries every state",
        all(listed.get(request_id) == state for request_id, state in expected.items()),
        ", ".join(f"{request_id}={listed.get(request_id)}" for request_id in expected),
    )

    # 6. an agent key opens a request as that agent; revoking it closes the door
    admin = Client()
    status, _ = admin.call(
        "POST", "/api/v1/login",
        {"email": os.environ.get("VETO_ADMIN_EMAIL", "ops@brackenfield.demo"),
         "password": os.environ.get("VETO_ADMIN_PASSWORD", "veto-demo-2026")},
    )
    if not check("admin login", status == 200, f"HTTP {status}"):
        return False
    status, payload = admin.call("POST", f"/api/v1/agents/{desk['id']}/keys", {})
    key = data(payload) or {}
    secret = key.get("secret") or ""
    check(
        "mint agent key",
        status == 201 and secret.startswith("vkey_") and key.get("key", {}).get("prefix"),
        f"HTTP {status} prefix={key.get('key', {}).get('prefix')}",
    )
    status, payload, key_request = create_request(
        admin, "2 packs of pens", headers={"Authorization": f"Bearer {secret}"}
    )
    check(
        "key request is the agent's own",
        status == 201
        and key_request.get("origin") == "api"
        and key_request.get("agent", {}).get("id") == desk["id"],
        f"HTTP {status} origin={key_request.get('origin')} agent={key_request.get('agent', {}).get('name')} "
        f"status={key_request.get('status')}",
    )
    status, _ = admin.call("DELETE", f"/api/v1/agents/{desk['id']}/keys/{key.get('key', {}).get('id')}")
    status2, payload2 = admin.call(
        "POST", "/api/v1/requests", {"text": "2 packs of pens"},
        headers={"Authorization": f"Bearer {secret}"},
    )
    check(
        "revoked key is refused",
        status == 200 and status2 == 401 and error_code(payload2) == "invalid_key",
        f"revoke HTTP {status} reuse HTTP {status2} ({error_code(payload2)})",
    )

    # 7. Slack posts a request into the same store (signature when configured)
    slack = Client()
    slack.call("POST", "/api/v1/demo-session", {})
    status, payload = slack.call("GET", "/api/health")
    signing = bool((payload or {}).get("slack", {}).get("signing_secret"))
    fields = {
        "command": "/veto", "text": "2 packs of toilet paper",
        "user_name": "dan", "channel_name": "pantry",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    secret_key = ""
    if signing:
        secret_key = _dotenv().get("SLACK_SIGNING_SECRET", "")
        if secret_key:
            headers.update(_slack_signature(secret_key, urllib.parse.urlencode(fields).encode()))
        else:
            secret_key = ""
    if signing and not secret_key:
        check("slack slash command", True, "skipped: no local SLACK_SIGNING_SECRET")
    else:
        body = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            BASE + "/api/slack/veto", data=body, method="POST", headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                slack_payload = json.loads(res.read().decode())
            status = 200
        except urllib.error.HTTPError as exc:
            status, slack_payload = exc.code, {}
        text = str(slack_payload.get("text") or "")
        check(
            "slack slash command",
            status == 200 and bool(slack_payload.get("blocks")),
            f"HTTP {status} {text[:80]}",
        )

    elapsed = time.time() - started
    failed = [row for row in RESULTS if not row["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed in {elapsed:.1f}s")
    return not failed


def _dotenv() -> dict:
    path = ROOT / ".env"
    found: dict[str, str] = {}
    if not path.exists():
        return found
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def _slack_signature(secret: str, body: bytes) -> dict:
    import hashlib
    import hmac

    stamp = str(int(time.time()))
    base = ("v0:" + stamp + ":").encode() + body
    signature = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": signature}


def main(argv: list[str]) -> int:
    global BASE, AS_JSON
    args = list(argv[1:])
    for index, arg in enumerate(args):
        if arg == "--base" and index + 1 < len(args):
            BASE = args[index + 1].rstrip("/")
        if arg.startswith("--base="):
            BASE = arg.split("=", 1)[1].rstrip("/")
        if arg == "--json":
            AS_JSON = True
    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    ok = False
    try:
        ok = run_checks()
    except Exception as exc:  # noqa: BLE001 - a harness failure is a failed check
        print(f"FAIL  harness: {type(exc).__name__}: {exc}")
        RESULTS.append({"label": "harness", "ok": False, "detail": str(exc)})
    if AS_JSON:
        print(json.dumps({"base": BASE, "ok": ok, "checks": RESULTS}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
