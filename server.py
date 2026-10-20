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
import time
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
        with a `payload` JSON blob; we answer with an updated message."""
        raw = self._raw()
        if not self._verify_slack(raw):
            return self._json(401, {"error": "bad signature"})
        form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        try:
            payload = json.loads(form.get("payload") or "{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "payload must be JSON"})
        actions = payload.get("actions") or []
        if not actions:
            return self._json(200, {"ok": True})
        action = actions[0]
        rid = action.get("value") or ""
        verb = "approve" if action.get("action_id") == "veto_approve" else "veto"
        user = (payload.get("user") or {}).get("username") or (payload.get("user") or {}).get("name") or "a human"
        try:
            req = ENGINE.requests.get(rid)
            if req is None:
                raise KeyError(rid)
            if verb == "approve":
                ENGINE.approve(rid)
            else:
                ENGINE.veto(rid)
            view = ENGINE._view(req)
            verdict = "approved and captured" if view["status"] == "captured" else (
                "approved, capture waits for the wallet" if view["status"] in ("auto", "approved") else "vetoed")
            tag = (view.get("agent") or {}).get("tag") or "general"
            paypal_id = (view.get("paypal") or {}).get("capture_id") or (view.get("paypal") or {}).get("order_id") or ""
            text = (f"*{verdict}* by {user} · {view['title']} · {view['total']}\n"
                    f"{rid} · tag `{tag}`" + (f" · `{paypal_id}`" if paypal_id else ""))
            return self._json(200, {
                "replace_original": True,
                "response_type": "in_channel",
                "text": text,
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            })
        except VaultRequired as exc:
            return self._json(200, {
                "replace_original": False,
                "response_type": "ephemeral",
                "text": f"Approved, but the company wallet has to be connected first: {exc}",
            })
        except Exception as exc:  # noqa: BLE001
            return self._json(200, {"response_type": "ephemeral", "text": f"Could not action {rid}: {exc}"})

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
        if path == "/api/slack/veto" and urlparse(self.path).query.startswith("ssl_check"):
            return self._json(200, {"ok": True})
        if path == "/":
            return self._file("index.html")
        if path == "/connect":
            return self._file("demo/connect.html")
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
        if path == "/api/slack/interact":
            return self._slack_interact()
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
