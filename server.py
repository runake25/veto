#!/usr/bin/env python3
"""Veto runner. One process: console, demo harness, and the pipeline API.

    python3 server.py            # http://127.0.0.1:8787
    PAYPAL_LIVE=1 python3 server.py   # real sandbox (needs a vaulted wallet)
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veto.engine import Engine, VaultRequired  # noqa: E402

ENGINE = Engine()
PORT = int(os.environ.get("PORT", "8787"))

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "Veto/1.0"

    def log_message(self, fmt, *args):  # quieter console
        if os.environ.get("VETO_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers --------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

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

    # -- routes ---------------------------------------------------------
    def _slack_veto(self) -> None:
        """The slash command itself. Slack sends command/text/user_name/channel_name;
        we answer in Slack's own shape, so the same URL works as a Slack app endpoint
        and from curl:
            curl -X POST localhost:8787/api/slack/veto \
                 -d 'command=/veto&text=2 boxes of capsules from kettle supply&user_name=dan'
        """
        form = self._form()
        command = form.get("command") or "/veto"
        text = (form.get("text") or "").strip()
        channel = "#" + (form.get("channel_name") or "office-ops").lstrip("#")
        requester = (form.get("user_name") or "dan").strip() or "dan"
        if not text:
            return self._json(200, {
                "response_type": "ephemeral",
                "text": f"`{command}` needs something to buy. Try: `{command} 2 boxes of capsules from kettle supply`",
            })
        try:
            req = ENGINE.create_request(text, channel=channel, requester=requester.title())
        except VaultRequired as exc:
            return self._json(200, {
                "response_type": "ephemeral",
                "text": "This workspace has to connect PayPal once before the agent can spend.",
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"*Connect PayPal first*\n{exc}"}}],
            })
        except Exception as exc:  # noqa: BLE001
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        view = ENGINE._view(req)
        agent = view.get("agent") or {"name": "an agent", "tag": "general"}
        reasons = "\n".join("• " + r["message"] for r in view["reasons"]) or "• within every rule"
        head = {
            "awaiting": "Waiting on a human",
            "blocked": "Refused by a rule",
            "vetoed": "Vetoed",
            "auto": "Ordered automatically",
            "captured": "Ordered and captured",
        }.get(view["status"], view["status"])
        return self._json(200, {
            "response_type": "in_channel",
            "text": f"{head} · {view['title']} · {view['total']}",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn",
                 "text": f"*{head}* · {view['title']}\n{view['total']} · {view['charter']} · {agent['name']}"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": reasons}},
                {"type": "context", "elements": [{"type": "mrkdwn",
                 "text": f"{view['id']} · tag `{agent['tag']}` · `{view['consent']['cart_hash'][:16]}…`"}]},
            ] + ([{"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Approve"}, "style": "primary",
                 "action_id": "veto_approve", "value": view["id"]},
                {"type": "button", "text": {"type": "plain_text", "text": "Veto"}, "style": "danger",
                 "action_id": "veto_veto", "value": view["id"]},
            ]}] if view["status"] == "awaiting" else []),
        })

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            return self._file("index.html")
        if path == "/demo":
            return self._file("demo/demo.html")
        if path == "/api/state":
            return self._json(200, ENGINE.snapshot())
        if path == "/api/health":
            return self._json(200, {
                "ok": True, "paypal": ENGINE.paypal_mode, "llm": ENGINE.llm_mode,
                "catalog": ENGINE.catalog_mode, "vault": bool(ENGINE.vault_id),
            })
        return self._file(path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/slack/veto":
            return self._slack_veto()
        body = self._body()
        try:
            if path == "/api/request":
                text = (body.get("text") or "").strip()
                if not text:
                    return self._json(400, {"error": "text is required"})
                req = ENGINE.create_request(
                    text,
                    channel=body.get("channel") or "#office-ops",
                    requester=body.get("requester") or "Dan Okafor",
                )
                return self._json(200, {"request": ENGINE._view(req)})

            if path == "/api/decide":
                rid = body.get("id") or ""
                action = body.get("action") or ""
                if action == "approve":
                    req = ENGINE.approve(rid)
                elif action == "veto":
                    req = ENGINE.veto(rid)
                else:
                    return self._json(400, {"error": "action must be approve or veto"})
                return self._json(200, {"request": ENGINE._view(req)})

            if path == "/api/connect":
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
                "next": "open approve_url as the sandbox buyer, then POST /api/connect/finish",
            })
        except KeyError as exc:
            return self._json(404, {"error": f"no such request: {exc}"})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"\n  Veto runner")
    print(f"  console   http://127.0.0.1:{PORT}/")
    print(f"  harness   http://127.0.0.1:{PORT}/demo")
    print(f"  paypal    {ENGINE.paypal_mode}   llm  {ENGINE.llm_mode}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
