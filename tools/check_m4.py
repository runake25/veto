#!/usr/bin/env python3
"""M4 regression harness: the five screens read the same store, over HTTP.

Read-only. Runs against a live runner and checks the parts M4 added: `/` serves
the built app and the old console routes are gone, Analytics aggregates reconcile
with the Overview figures and with their own series, Integrations reports the
three doors (Slack, agent keys, wallet), and a record carries its cart, consent,
PayPal ids and trail while a refused record carries none.

    python3 server.py
    python3 tools/check_m4.py
    python3 tools/check_m4.py --base http://127.0.0.1:8788 --json

Fast: no model or catalogue calls, no requests created, no money moved.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "http://127.0.0.1:8787"
AS_JSON = False
RESULTS: list[dict] = []


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
            with urllib.request.urlopen(req, timeout=60) as res:
                raw = res.read().decode()
                for cookie in res.headers.get_all("Set-Cookie") or []:
                    pair = cookie.split(";")[0]
                    if "=" in pair:
                        name, _, value = pair.partition("=")
                        self.cookies[name] = value
                if raw.strip().startswith("{"):
                    return res.status, json.loads(raw)
                return res.status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"label": label, "ok": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL") + f"  {label}" + (f" · {detail}" if detail else ""))
    return ok


def data(payload):
    return payload.get("data") if isinstance(payload, dict) else None


def code(payload):
    return ((payload or {}).get("error") or {}).get("code") if isinstance(payload, dict) else None


def get_bytes(path: str) -> tuple[int, bytes]:
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def range_path(from_day: date, to_day: date) -> str:
    query = urllib.parse.urlencode({"from": from_day.isoformat(), "to": to_day.isoformat()})
    return f"/api/v1/analytics?{query}"


def run_checks() -> bool:
    client = Client()

    # --- the console is the app ------------------------------------------
    status, body = get_bytes("/")
    html = body.decode("utf-8", "replace")
    check("GET / serves the built app", status == 200 and 'id="root"' in html,
          f"{status}, {len(body)} bytes")
    check("GET / is not the old console",
          "Veto console" not in html and "framed sheet" not in html)
    status, _ = get_bytes("/app/")
    check("GET /app/ still serves the app", status == 200)
    status, _ = get_bytes("/ui/src/app.css")
    check("old console assets are gone", status == 404, f"/ui/src/app.css → {status}")
    status, _ = get_bytes("/demo")
    check("old demo harness route is gone", status == 404, f"/demo → {status}")

    # --- analytics --------------------------------------------------------
    status, payload = client.call("GET", "/api/v1/analytics")
    check("analytics needs a session", status == 401, f"{status} {code(payload)}")

    client.call("POST", "/api/v1/demo-session")
    today = datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)

    status, payload = client.call(
        "GET", range_path(date(2026, 10, 24), date(2026, 10, 1))
    )
    check("an inverted range is a 400",
          status == 400 and code(payload) == "invalid_range", f"{status} {code(payload)}")

    status, payload = client.call("GET", range_path(month_start, today))
    analytics = data(payload) or {}
    totals = analytics.get("totals") or {}
    keys = {"range", "totals", "by_agent", "by_tag", "by_vendor", "by_category",
            "by_rule", "series"}
    check("analytics returns the read model",
          status == 200 and keys <= set(analytics),
          f"missing {sorted(keys - set(analytics))}" if keys - set(analytics) else "all keys")

    days = (today - month_start).days + 1
    check("series covers every day of the range, zero-filled",
          len(analytics.get("series") or []) == days, f"{len(analytics.get('series') or [])} days")
    series_sum = sum(day.get("captured_cents", 0) for day in analytics.get("series") or [])
    check("series sums to the captured total",
          series_sum == totals.get("captured_cents"),
          f"series {series_sum} vs totals {totals.get('captured_cents')}")

    status, overview_payload = client.call("GET", "/api/v1/overview")
    overview = data(overview_payload) or {}
    counts = overview.get("counts") or {}
    check("analytics reconciles with the Overview",
          totals.get("captured_cents") == counts.get("settled_month_cents"),
          f"analytics {totals.get('captured_cents')} vs overview {counts.get('settled_month_cents')}")
    status, captured_payload = client.call("GET", "/api/v1/requests?status=captured&limit=100")
    captured_rows = (data(captured_payload) or {}).get("requests") or []
    check("captured money has a matching capture count",
          totals.get("captured") == len(captured_rows), f"{totals.get('captured')} captured")
    agent_sum = sum(row.get("captured_cents", 0) for row in analytics.get("by_agent") or [])
    vendor_sum = sum(row.get("captured_cents", 0) for row in analytics.get("by_vendor") or [])
    check("by-agent money equals the frame", agent_sum == totals.get("captured_cents"),
          f"agents {agent_sum}")
    check("by-vendor money equals the frame (captured lines only)",
          vendor_sum == totals.get("captured_cents"), f"vendors {vendor_sum}")
    check("rule rows name a severity and a rule",
          all(row.get("severity") in ("block", "review") and row.get("key")
              for row in analytics.get("by_rule") or []))

    # --- agent money (the guardrail track) --------------------------------
    status, payload = client.call("GET", "/api/v1/agents")
    agent_rows = (data(payload) or {}).get("agents") or []
    check("agents carry this month's money",
          bool(agent_rows) and all(
              isinstance(row.get("spent_month_cents"), int)
              and isinstance(row.get("committed_month_cents"), int)
              for row in agent_rows
          ),
          f"{len(agent_rows)} agents")
    agent_spent = sum(row.get("spent_month_cents", 0) for row in agent_rows)
    check("agent spend reconciles with Analytics",
          agent_spent == totals.get("captured_cents"),
          f"agents {agent_spent} vs analytics {totals.get('captured_cents')}")

    # --- integrations -----------------------------------------------------
    status, payload = client.call("GET", "/api/v1/integrations")
    integrations = data(payload) or {}
    slack = integrations.get("slack") or {}
    wallet = integrations.get("wallet") or {}
    check("integrations reports the three doors",
          status == 200 and {"wallet", "slack", "agents", "api", "keys"} <= set(integrations))
    check("wallet names its mode", wallet.get("mode") in ("sandbox", "mock"),
          str(wallet.get("mode")))
    check("slack URLs point at the public base",
          slack.get("slash_url", "").startswith(slack.get("public_url", "x"))
          and slack.get("slash_url", "").endswith("/api/slack/veto")
          and slack.get("interact_url", "").endswith("/api/slack/interact"))
    check("slack routes by model, not by channel",
          slack.get("routing") == "model" and "routes" not in slack,
          f"routing={slack.get('routing')}")
    commands = slack.get("commands") or []
    check("slack exposes its command surface",
          {row.get("command") for row in commands} >= {
              "/veto", "/veto-check", "/veto-status", "/veto-budget",
              "/veto-routines", "/veto-help"},
          ", ".join(row.get("command", "?") for row in commands))
    check("every Slack command carries its own copy",
          len(commands) >= 6
          and all(row.get("description") for row in commands)
          and all(row.get("usage_hint")
                  for row in commands if row.get("command") != "/veto-help"),
          f"{sum(1 for row in commands if row.get('usage_hint'))} with hints")
    scopes = [
        (agent.get("categories"), agent.get("merchants"))
        for agent in integrations.get("agents") or []
    ]
    check("agents expose the scope the model routes on",
          bool(scopes) and all(isinstance(cats, list) and isinstance(merchants, list)
                               for cats, merchants in scopes),
          f"{len(scopes)} agents with scopes")
    key_names = {key.get("agent_name") for key in integrations.get("keys") or []}
    agent_names = {agent.get("name") for agent in integrations.get("agents") or []}
    check("keys carry the agent they belong to", key_names <= agent_names,
          f"{sorted(key_names)}")

    # --- inbox filters ----------------------------------------------------
    status, payload = client.call("GET", "/api/v1/requests?status=awaiting&limit=100")
    awaiting = (data(payload) or {}).get("requests") or []
    check("status filter returns only that status",
          status == 200 and all(row.get("status") == "awaiting" for row in awaiting),
          f"{len(awaiting)} awaiting")
    status, payload = client.call("GET", "/api/v1/requests?status=nope")
    check("an unknown status is a 400",
          status == 400 and code(payload) == "invalid_status", f"{status} {code(payload)}")
    agents = (data(client.call("GET", "/api/v1/agents")) or {}).get("agents") or []
    if agents:
        agent_id = agents[0]["id"]
        status, payload = client.call(
            "GET", f"/api/v1/requests?agent_id={urllib.parse.quote(agent_id)}&limit=100"
        )
        rows = (data(payload) or {}).get("requests") or []
        check("agent filter stays inside one agent",
              status == 200 and all(row.get("agent", {}).get("id") == agent_id for row in rows),
              f"{len(rows)} rows")

    status, payload = client.call("GET", "/api/v1/requests?status=expired&limit=100")
    expired = (data(payload) or {}).get("requests") or []
    check("expired consents are their own queue",
          status == 200 and all(row.get("consent_expired") for row in expired),
          f"{len(expired)} expired")
    status, payload = client.call("GET", "/api/v1/requests?status=awaiting&limit=100")
    signable = (data(payload) or {}).get("requests") or []
    check("the needs-you queue never contains a lapsed consent",
          all(not row.get("consent_expired") for row in signable),
          f"{len(signable)} signable")
    status, payload = client.call("GET", "/api/v1/requests?q=zzzz-no-such-request")
    found = (data(payload) or {})
    check("search narrows the record to nothing for a nonsense query",
          status == 200 and found.get("total") == 0 and found.get("sum_cents") == 0,
          f"total={found.get('total')}")

    # --- a record is a document -------------------------------------------
    status, payload = client.call("GET", "/api/v1/requests?status=captured&limit=1")
    captured = ((data(payload) or {}).get("requests") or [None])[0]
    record = None
    if captured:
        status, payload = client.call("GET", f"/api/v1/requests/{captured['id']}")
        record = data(payload) or {}
    record = record.get("request") if isinstance(record, dict) else record
    if record:
        lines = record.get("lines") or []
        consent = record.get("consent") or {}
        payment = record.get("payment") or {}
        events = record.get("events") or []
        check("a record carries cart, consent, payment and trail",
              bool(lines) and bool(consent.get("cart_hash")) and bool(payment.get("capture_id"))
              and len(events) >= 4,
              f"{len(lines)} lines, {len(events)} events")
        check("the PayPal capture id is in the record",
              (payment.get("capture_id") or "").isalnum(),
              str(payment.get("capture_id")))
        check("the consent nonce is never serialised",
              "nonce" not in json.dumps(record))
        check("the trail is ordered by step",
              [event.get("seq") for event in events] == sorted(
                  event.get("seq") for event in events))
    else:
        check("a record carries cart, consent, payment and trail", False, "no captured request")

    status, payload = client.call("GET", "/api/v1/requests?status=blocked&limit=1")
    blocked = ((data(payload) or {}).get("requests") or [None])[0]
    if blocked:
        status, payload = client.call("GET", f"/api/v1/requests/{blocked['id']}")
        refused = (data(payload) or {}).get("request") or {}
        check("a refused record has no wallet object",
              refused.get("payment") is None and refused.get("consent") is None
              and bool(refused.get("reasons")),
              f"{len(refused.get('reasons') or [])} reasons")
    else:
        check("a refused record has no wallet object", True, "no blocked request in this store")

    status, payload = client.call("GET", "/api/v1/requests/REQ-999999")
    check("an unknown record is a 404",
          status == 404 and code(payload) == "request_not_found", f"{status} {code(payload)}")

    # --- health -----------------------------------------------------------
    status, payload = client.call("GET", "/api/health")
    health = payload if isinstance(payload, dict) else {}
    check("health reports the settlement and the store",
          status == 200 and health.get("db") == "ok" and health.get("settlement") in ("sandbox", "mock")
          and isinstance(health.get("requests"), int),
          f"{health.get('settlement')} · {health.get('requests')} requests")

    return all(result["ok"] for result in RESULTS)


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
    print(f"\n{sum(1 for r in RESULTS if r['ok'])}/{len(RESULTS)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
