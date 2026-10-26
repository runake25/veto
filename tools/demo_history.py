#!/usr/bin/env python3
"""Put honest, real history into a demo workspace — through the product's own API.

This is how the hosted demo's Inbox gets its evidence: nothing is written to
SQLite by hand. Every request below runs the live pipeline (model extraction,
Channel3 product search, the agent's rules, consent, PayPal sandbox capture),
and the approvals/vetoes are real decisions. The runner must have its normal
keys configured; the demo workspace must be seeded.

    python3 tools/demo_history.py                          # http://127.0.0.1:8787
    python3 tools/demo_history.py --base https://veto-bracken-field.onrender.com
    python3 tools/demo_history.py --skip-routines

It spends sandbox money only (PAYPAL_LIVE=1 with a vaulted sandbox wallet). The
script is idempotent in spirit but not in fact: running it twice adds a second
set of genuine requests, which is fine for a demo but not what a reset wants —
a fresh instance starts from zero anyway.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8787"

# What to ask for, and what to do with a cart that waits for a human. The small
# carts sit inside the seeded agents' thresholds and capture on their own; the
# big ones are deliberately over threshold so both decision paths exist.
CARTS: list[tuple[str, str]] = [
    ("2 packs of bamboo toilet paper", "auto"),
    ("a box of envelopes and 2 packs of pens", "auto"),
    ("pens and sticky notes for the studio", "capture"),
    ("2 anti-fatigue standing mats for the studio", "capture"),
    ("an ergonomic office chair for the studio", "veto"),
    ("a 27-inch 4K monitor for the edit bay", "await"),
]

BLOCKED_CARTS = [
    "fresh coffee beans for the kitchen",
]


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.cookies: dict[str, str] = {}

    def call(self, method: str, path: str, body=None):
        data = None
        headers = {}
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
    return f"{request.get('seq')} · {request.get('title')} · {amount} · {request.get('status')} · {agent}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE, help="runner origin")
    parser.add_argument("--skip-routines", action="store_true", help="do not run the standing jobs")
    args = parser.parse_args()

    client = Client(args.base)
    client.data("POST", "/api/v1/demo-session")
    print(f"demo session opened on {client.base}")

    created: list[dict] = []
    for text, intent in CARTS:
        request = client.data("POST", "/api/v1/requests", {"text": text})["request"]
        print(f"  created  {summarise(request)}  ({intent})")
        created.append(request)
        if request.get("status") == "awaiting" and intent in ("capture", "veto"):
            action = "approve" if intent == "capture" else "veto"
            decided = client.data(
                "POST", f"/api/v1/requests/{request['id']}/decision", {"action": action}
            )["request"]
            print(f"  {action:<8} {summarise(decided)}")
        time.sleep(1)

    for text in BLOCKED_CARTS:
        request = client.data("POST", "/api/v1/requests", {"text": text})["request"]
        print(f"  created  {summarise(request)}  (expected refusal)")

    if not args.skip_routines:
        agents = client.data("GET", "/api/v1/agents")["agents"]
        for agent in agents:
            routine = agent.get("routine")
            if not routine:
                continue
            result = client.data("POST", f"/api/v1/agents/{agent['id']}/routine/run")
            run = result.get("run") or {}
            amount = f"${(run.get('amount_cents') or 0) / 100:,.2f}"
            print(
                f"  routine  {routine['name']} · {run.get('outcome')} · {amount}"
                f" · next {routine.get('next_run_at')}"
            )
            time.sleep(1)

    requests = client.data("GET", "/api/v1/requests?limit=100")["requests"]
    print(f"\n{len(requests)} requests in the record")
    for row in requests:
        print(f"  {summarise({**row, 'total_cents': row.get('total_cents')})}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
