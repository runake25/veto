#!/usr/bin/env python3
"""Live regression harness for the buying pipeline.

Runs a fixed battery of realistic requests against a running Veto runner through
the same HTTP API the demo uses, and checks each one ends where it should:

    purchasable  → a priced cart that may auto-pay or need a signature
    refused      → a block, and the block names a catalogue/category/vendor rule
    review       → a priced cart that needs a signature

These cases are the demo's edge cases: household staples that must resolve, and
snacks / coffee / milk that the Channel3 catalogue genuinely does not stock and
must be refused honestly (not filled with a coffee pot or a Pringles t-shirt).

Usage:
    python3 tools/check_pipeline.py                       # local runner
    python3 tools/check_pipeline.py --base http://127.0.0.1:8787
    python3 tools/check_pipeline.py --only pantry         # one group
    python3 tools/check_pipeline.py --json                # machine-readable

Exit code is 1 when any case misses its expected class.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

CASES = [
    # ---- pantry: household consumables the catalogue really stocks ----------
    ("pantry", "Pantry runner", "2 packs of toilet paper", "purchasable"),
    ("pantry", "Pantry runner", "bamboo toilet paper", "purchasable"),
    ("pantry", "Pantry runner", "paper towels", "purchasable"),
    ("pantry", "Pantry runner", "dish soap", "purchasable"),
    ("pantry", "Pantry runner", "sponges", "purchasable"),
    ("pantry", "Pantry runner", "trash bags", "purchasable"),
    # ---- pantry: groceries the Channel3 index does not carry -----------------
    ("pantry", "Pantry runner", "pringles", "refused"),
    ("pantry", "Pantry runner", "chips", "refused"),
    ("pantry", "Pantry runner", "doritos", "refused"),
    ("pantry", "Pantry runner", "coffee", "refused"),
    ("pantry", "Pantry runner", "milk", "refused"),
    # ---- desk: equipment and stationery --------------------------------------
    ("desk", "Desk kit", "keyboard", "purchasable"),
    ("desk", "Desk kit", "whiteboard markers", "purchasable"),
    ("desk", "Desk kit", "standing mat", "purchasable"),
    ("desk", "Desk kit", "printer paper", "purchasable"),
    ("desk", "Desk kit", "envelopes", "purchasable"),
    ("desk", "Desk kit", "a logitech keyboard", "purchasable"),
    ("desk", "Desk kit", "monitor", "review"),
    # ---- multi-item extraction and pricing ----------------------------------
    ("multi", "Studio ops", "a box of envelopes and 2 packs of pens", "purchasable"),
]

REFUSAL_RULES = {"catalogue", "category_allowlist", "vendor_allowlist"}


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=120) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{method} {path} -> {exc.code} {exc.read().decode()[:200]}")

    def data(self, method: str, path: str, body: dict | None = None) -> dict:
        return self.call(method, path, body)["data"]


def classify(result: dict) -> tuple[str, str]:
    """(class, rule) for one simulate response."""
    decision = result.get("decision") or {}
    outcome = decision.get("outcome") or "block"
    reasons = decision.get("reasons") or []
    rule = reasons[0].get("rule") if reasons else ""
    if outcome == "block":
        return "refused", rule
    return outcome, rule


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8787")
    parser.add_argument("--only", default="", help="pantry | desk | multi")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    api = Api(args.base)
    api.data("POST", "/api/v1/demo-session")
    agents = {agent["name"]: agent["id"] for agent in api.data("GET", "/api/v1/agents")["agents"]}

    rows, failures = [], 0
    for group, agent_name, text, expected in CASES:
        if args.only and group != args.only:
            continue
        result = api.data("POST", "/api/v1/simulate",
                          {"agent_id": agents[agent_name], "text": text})
        outcome, rule = classify(result)
        ok = True
        detail = ""
        if expected == "purchasable":
            ok = outcome in {"auto", "review"} and bool(result.get("lines"))
            detail = f"{outcome} · {len(result.get('lines') or [])} line(s)"
        elif expected == "review":
            ok = outcome == "review"
            detail = outcome
        elif expected == "refused":
            ok = outcome == "refused" and rule in REFUSAL_RULES
            detail = f"{outcome} · {rule or 'no reason'}"
            if result.get("near_misses"):
                detail += f" · {len(result['near_misses'][0]['offers'])} near miss(es)"
        failures += 0 if ok else 1
        rows.append({
            "group": group, "agent": agent_name, "text": text, "expected": expected,
            "outcome": outcome, "rule": rule, "pass": ok,
            "elapsed_ms": result.get("elapsed_ms"),
            "lines": [(line.get("title"), line.get("unit_cents")) for line in result.get("lines") or []],
        })

    if args.json:
        print(json.dumps({"rows": rows, "failures": failures}, indent=2))
    else:
        width = max(len(row["text"]) for row in rows) if rows else 10
        print(f"{'':4} {'case':{width}}  {'expected':11}  result")
        for row in rows:
            mark = "PASS" if row["pass"] else "FAIL"
            print(f"{mark:4} {row['text']:{width}}  {row['expected']:11}  "
                  f"{row['outcome']:6} {row['rule'] or '':20} {row['elapsed_ms'] or 0:5}ms")
        print(f"\n{len(rows) - failures}/{len(rows)} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
