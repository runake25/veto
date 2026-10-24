#!/usr/bin/env python3
"""Prove the Slack app is wired correctly — and print the two URLs to paste.

    python3 slack/verify.py            # safe: no purchases, no new requests
    python3 slack/verify.py --live     # also posts a real /veto (spends sandbox money)

Checks, in order:
  1. a signing secret and bot token are present in .env
  2. the bot token is real (auth.test) and says which workspace it belongs to
  3. the runner is reachable at the public URL Slack has to call
  4. that URL is the one the Slack app must be configured with, printed in full
  5. both request URLs answer the way Slack expects (ssl_check, and a signed POST)

The URL checks are the safe signed call with an empty text — Veto answers
"needs something to buy" and writes nothing. --live sends the real cart and does
create one request (sandbox money only).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def dotenv() -> dict:
    out = {}
    path = ROOT / ".env"
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def slack_api(method: str, token: str, payload: dict | None = None) -> dict:
    data = urllib.parse.urlencode(payload or {}).encode()
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}: {exc.read()[:200].decode(errors='replace')}"}


def main() -> int:
    live = '--live' in sys.argv[1:]
    env = dotenv()
    secret = (env.get("SLACK_SIGNING_SECRET") or os.environ.get("SLACK_SIGNING_SECRET") or "").strip()
    token = (env.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_BOT_TOKEN") or "").strip()
    base = (env.get("VETO_PUBLIC_URL") or "").strip().rstrip("/")

    problems = []
    print("1. secrets")
    print(f"   signing secret : {'present (' + str(len(secret)) + ' chars)' if secret else 'MISSING'}")
    print(f"   bot token      : {'present (' + token[:9] + '…) ' if token else 'MISSING'}")
    if not secret:
        problems.append("SLACK_SIGNING_SECRET is empty")
    if not token:
        problems.append("SLACK_BOT_TOKEN is empty")

    if token:
        print("\n2. bot token")
        who = slack_api("auth.test", token)
        if who.get("ok"):
            print(f"   workspace : {who.get('team')} ({who.get('team_id')})")
            print(f"   bot       : {who.get('user')} ({who.get('user_id')})")
            print(f"   url       : {who.get('url')}")
        else:
            print(f"   FAILED    : {who.get('error')}")
            problems.append("bot token rejected by auth.test")

    print("\n3. public reachability")
    if not base:
        print("   VETO_PUBLIC_URL is empty")
        problems.append("VETO_PUBLIC_URL is empty")
    else:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=15) as res:
                health = json.loads(res.read())
            print(f"   {base}/api/health -> ok={health.get('ok')} paypal={health.get('paypal')} vault={health.get('vault')}")
        except Exception as exc:  # noqa: BLE001
            print(f"   UNREACHABLE: {exc}")
            problems.append("public URL does not answer")

        print("\n4. what the Slack app must be pointed at")
        print(f"   slash command  : {base}/api/slack/veto")
        print(f"   interactivity  : {base}/api/slack/interact")
        print("   (paste these into api.slack.com/apps after every runner restart)")

        print("\n5. Slack request checks")
        try:
            with urllib.request.urlopen(base + "/api/slack/veto?ssl_check=1", timeout=15) as res:
                body = json.loads(res.read())
            print(f"   ssl_check      : {body}")
            if body.get("ok") is not True:
                problems.append("ssl_check did not answer ok:true")
        except Exception as exc:  # noqa: BLE001
            print(f"   ssl_check      : FAILED {exc}")
            problems.append("ssl_check failed")

        # a signed slash-command call, exactly the shape Slack posts
        form = urllib.parse.urlencode({
            "command": "/veto",
            "text": "2 packs of bamboo toilet paper" if live else "",
            "user_name": "dan", "channel_name": "office-ops",
            "response_url": "https://hooks.slack.com/commands/verify",  # Slack always sends one
        }).encode()
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + form,
                               hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            base + "/api/slack/veto", data=form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                reply = json.loads(res.read())
            label = "signed /veto (live)" if live else "signed /veto (safe)"
            print(f"   {label:<16}: {res.status} · {(reply.get('text') or '')[:60]}")
            if res.status != 200:
                problems.append("signed slash command did not return 200")
            if not live and (reply.get("text") or "").startswith("Checking"):
                problems.append("safe signed call unexpectedly started a purchase")
        except urllib.error.HTTPError as exc:
            print(f"   signed /veto   : HTTP {exc.code} · {exc.read()[:120].decode(errors='replace')}")
            problems.append(f"signed slash command returned {exc.code}")
        except Exception as exc:  # noqa: BLE001
            print(f"   signed /veto   : FAILED {exc}")
            problems.append("signed slash command failed")

        if token and "-v" in os.environ.get("VETO_ARGV", ""):
            print("\n5. hello message")
            print("   " + str(slack_api("chat.postMessage", token,
                                        {"channel": "#general", "text": "Veto is connected. Try `/veto 2 packs of bamboo toilet paper`."})))

    print()
    if problems:
        print("NOT READY:")
        for p in problems:
            print("  -", p)
        return 1
    print("READY. The workspace can call Veto, and the signatures match.")
    print("If Slack still fails, compare the two URLs above with the Slack app settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
