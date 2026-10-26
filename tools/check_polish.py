#!/usr/bin/env python3
"""Polish-round regression harness: the fixes after the UI audit, over HTTP.

Read-only against a live runner. Covers the pieces this round added: the SSE
change feed, the Inbox date window + line-vendor search, the delete guard that
protects the ledger, and the small evidence that the built app carries the new
controls (table sort semantics, the live feed, the date range, the copy and
delete affordances).

    python3 server.py
    python3 tools/check_polish.py
    python3 tools/check_polish.py --base https://veto-bracken-field.onrender.com

No requests are created and nothing is deleted: the delete checks only call the
guard paths, which refuse without writing.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "http://127.0.0.1:8787"
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

    def stream_head(self, path: str, limit: int = 1200) -> str:
        """Read the first frames of a server-sent stream, then close it."""
        hdrs = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        req = urllib.request.Request(BASE + path, headers=hdrs)
        with urllib.request.urlopen(req, timeout=20) as res:
            chunks: list[bytes] = []
            size = 0
            while size < limit:
                piece = res.read(1)
                if not piece:
                    break
                chunks.append(piece)
                size += 1
                text = b"".join(chunks).decode("utf-8", "replace")
                if text.count("\n\n") >= 2:
                    break
            return b"".join(chunks).decode("utf-8", "replace")


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"label": label, "ok": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL") + f"  {label}" + (f" · {detail}" if detail else ""))
    return ok


def data(payload):
    return payload.get("data") if isinstance(payload, dict) else None


def code(payload):
    return ((payload or {}).get("error") or {}).get("code") if isinstance(payload, dict) else None


def main() -> int:
    client = Client()
    status, payload = client.call("POST", "/api/v1/demo-session", {})
    if status != 200:
        print(f"FAIL  demo session · {status}")
        return 1

    # -- change feed --------------------------------------------------------
    anon = Client()
    status, _ = anon.call("GET", "/api/v1/events")
    check("the change feed needs a session", status == 401, f"{status}")

    try:
        head = client.stream_head("/api/v1/events")
        check("the change feed opens with a retry hint", head.startswith("retry:"), head[:12])
        check(
            "the change feed announces itself before any change",
            '"type":"ready"' in head,
            head.splitlines()[-2][:40] if "\n" in head else head[:40],
        )
    except Exception as exc:  # noqa: BLE001 - a broken stream is the finding
        check("the change feed opens", False, str(exc))

    # -- in-box date window -------------------------------------------------
    today = datetime.now(timezone.utc).date().isoformat()
    status, payload = client.call(
        "GET", f"/api/v1/requests?limit=100&from={today}&to={today}&tz=0"
    )
    window = data(payload) or {}
    rows = window.get("requests") or []
    check("the inbox accepts a day window", status == 200, f"from={window.get('from')} tz={window.get('tz')}")
    inside = all(str(row.get("created_at", "")).startswith(today) for row in rows)
    check("a day window returns only that day", inside, f"{len(rows)} rows")
    reconciled = int(window.get("sum_cents") or 0) == sum(
        int(row.get("total_cents") or 0) for row in rows
    )
    check("a filtered list reconciles its own money", reconciled, f"sum={window.get('sum_cents')}")
    check(
        "the window carries the viewer offset back",
        window.get("tz") == 0,
        f"tz={window.get('tz')}",
    )

    status, payload = client.call("GET", "/api/v1/requests?from=2026-10-30&to=2026-10-01")
    check("a reversed window is a 400", status == 400 and code(payload) == "invalid_range", str(status))
    status, payload = client.call("GET", "/api/v1/requests?from=2026-10-01&to=2026-10-02&tz=999")
    check("an impossible offset is a 400", status == 400 and code(payload) == "invalid_range", str(status))

    # -- a merchant is searchable through its cart line ---------------------
    vendor = None
    status, payload = client.call("GET", "/api/v1/requests?limit=100")
    for row in (data(payload) or {}).get("requests") or []:
        status, detail = client.call("GET", f"/api/v1/requests/{row['id']}")
        lines = ((data(detail) or {}).get("request") or {}).get("lines") or []
        for line in lines:
            if line.get("vendor"):
                vendor = str(line["vendor"]).split(".")[0]
                break
        if vendor:
            break
    if vendor:
        status, payload = client.call("GET", f"/api/v1/requests?limit=100&q={vendor}")
        found = (data(payload) or {}).get("total") or 0
        check("a merchant is searchable through its cart line", status == 200 and found > 0, f"q={vendor} · {found}")
    else:
        check("a merchant is searchable through its cart line", False, "no line vendor in the record")

    # -- the ledger cannot lose an agent that bought anything ---------------
    status, payload = client.call("GET", "/api/v1/requests?limit=100")
    rows = (data(payload) or {}).get("requests") or []
    agent_id = rows[0]["agent"]["id"] if rows else None
    if agent_id:
        status, payload = client.call("DELETE", f"/api/v1/agents/{agent_id}")
        check(
            "an agent with ledger history is refused a delete",
            status == 409 and code(payload) == "agent_has_history",
            f"{status} {code(payload)}",
        )
        check(
            "the refusal names the way out",
            "pause" in (((payload or {}).get("error") or {}).get("message") or "").lower(),
            ((payload or {}).get("error") or {}).get("message", "")[:70],
        )
    status, payload = client.call("DELETE", "/api/v1/agents/agt_doesnotexist/routine")
    check("deleting a job that does not exist is a clean 404", status == 404 and code(payload) == "no_routine", str(status))

    # -- the built app carries the new controls -----------------------------
    status, html = client.call("GET", "/app/")
    check("the app shell is built and served", status == 200 and "<div id=\"root\">" in html, str(status))
    match = re.search(r'src="(/app/assets/index-[^"]+\.js)"', html)
    if not match:
        check("the built bundle is reachable", False, "no script tag")
    else:
        status, bundle = client.call("GET", match.group(1))
        if not isinstance(bundle, str):
            bundle = ""
        for needle, label in (
            ("api/v1/events", "the board subscribes to the change feed"),
            ("aria-sort", "the inbox is sorted by real column headers"),
            ("Last 7 days", "the inbox offers a day range"),
            ("Delete agent", "an agent can be removed"),
            ("Delete job", "a standing job can be removed"),
            ("Copy the PayPal capture id", "ledger ids can be copied"),
        ):
            check(f"bundle: {label}", needle in bundle, needle)

    passed = sum(1 for row in RESULTS if row["ok"])
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks passed")
    if "--json" in sys.argv:
        print(json.dumps(RESULTS, indent=2))
    return 0 if passed == total else 1


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        if arg.startswith("--base="):
            BASE = arg.split("=", 1)[1].rstrip("/")
    if "--base" in sys.argv:
        BASE = sys.argv[sys.argv.index("--base") + 1].rstrip("/")
    raise SystemExit(main())
