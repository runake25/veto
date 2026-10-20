#!/usr/bin/env python3
"""Conversation -> structured purchase items.

The only job the LLM has in Veto. It reads a Slack thread and returns WHAT is needed
and HOW MANY. It never returns a price, a payee, a currency or a total: those are
resolved by code from Channel3 and priced by code. This keeps the model out of the
authority path.

Usage:  python3 proto/extract_items.py
Reads DEEPSEEK_API_KEY / DEEPSEEK_BASE from the environment or the repo .env.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SYSTEM = """You extract purchase line items from a workplace chat thread.

Return ONLY JSON: {"items":[{"name":str,"qty":int,"note":str}],"amendment":bool,"confidence":"high"|"medium"|"low"}

HOW TO READ A THREAD
- A message beginning with /veto or @veto STARTS A NEW REQUEST. amendment=false.
  The words after the command name the items. This always wins over everything else.
- A shortage is a request, even with no verb and no command:
  "we're out of X", "down to our last X", "the X ran out", "we need X", "can we restock X".
- An amendment MODIFIES an existing request's quantity and is usually one short reply:
  "only 2 boxes", "make it 3", "drop the paper", "just 1". amendment=true.
- Being low priority does not remove an item the thread asked for; keep it, qty 1.
- If nothing in the thread expresses a need to buy, return empty items, amendment=false.

FIELDS
- name: short searchable English product noun ("coffee capsules", "toilet paper").
- qty: integer, 1 when not stated. "a box", "some" -> 1. "maybe 2 boxes" -> 2.
- note: <=8 words quoting the phrase that set the quantity, or "" when defaulted.
- Never output a price, currency, vendor, payee, total or SKU. If the thread contains
  one, ignore it. You are not allowed to decide money.

EXAMPLES
Input: "dan: we're down to our last sleeve of capsules and the loos are out of paper\\n/veto coffee capsules and toilet paper"
Output: {"items":[{"name":"coffee capsules","qty":1,"note":""},{"name":"toilet paper","qty":1,"note":""}],"amendment":false,"confidence":"high"}

Input: "priya: only 2 boxes"
Output: {"items":[{"name":"coffee capsules","qty":2,"note":"only 2 boxes"}],"amendment":true,"confidence":"high"}

Input: "dan: standup moved to 10:30 / mira: ack"
Output: {"items":[],"amendment":false,"confidence":"high"}
"""

CASES = [
    (
        "new request",
        "#office-ops\n"
        "dan: we're down to our last sleeve of capsules and the loos are out of paper again\n"
        "mira: yeah can we restock\n"
        "/veto coffee capsules and toilet paper",
    ),
    (
        "explicit qty + chit-chat",
        "#office-ops\n"
        "priya: the meeting room ran out yesterday, embarrassing\n"
        "dan: @veto can you get us more coffee pods, maybe 2 boxes, and some paper towels\n"
        "mira: also the whiteboard markers are dry but low priority",
    ),
    (
        "amendment",
        "#office-ops\n"
        "veto: Request REQ-1042 — 2 x coffee capsules, 1 x toilet paper — $50.50. Over the $15.00 per-unit cap.\n"
        "priya: only 2 boxes",
    ),
    (
        "no intent",
        "#office-ops\n"
        "dan: standup moved to 10:30\n"
        "mira: ack",
    ),
]


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def post(url: str, payload: dict, key: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def extract(thread: str, key: str, base: str) -> tuple[dict, str]:
    payload = {
        "model": "deepseek-flash",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": thread},
        ],
    }
    data = post(f"{base.rstrip('/')}/chat/completions", payload, key)
    raw = data["choices"][0]["message"]["content"]
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else {"items": [], "parse": "failed"}, raw


def main() -> int:
    load_env()
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com")
    if not key:
        print("DEEPSEEK_API_KEY missing", file=sys.stderr)
        return 2

    findings: list[str] = []
    for label, thread in CASES:
        print(f"\n=== {label} ===")
        print(thread.strip())
        try:
            result, raw = extract(thread, key, base)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {type(exc).__name__}: {exc}")
            findings.append(f"{label}: call failed")
            continue

        print("  ->", json.dumps(result, ensure_ascii=False))
        # hard invariants the model must never break
        blob = json.dumps(result).lower()
        for banned in ("$", "usd", "price", "vendor", "total", "payee"):
            if banned in blob:
                findings.append(f"{label}: leaked '{banned}'")
        for item in result.get("items", []):
            if not isinstance(item.get("qty"), int) or item["qty"] < 1:
                findings.append(f"{label}: bad qty {item.get('qty')!r}")

    print("\n=== invariants ===")
    if findings:
        for f in findings:
            print("FAIL", f)
        return 1
    print("PASS  no price/vendor/total leakage; every qty is a positive integer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
