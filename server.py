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
import threading
import time
import sys
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veto.engine import ApprovalPending, Engine, VaultRequired  # noqa: E402

ENGINE = Engine()
PORT = int(os.environ.get("PORT", "8787"))
# set VETO_PUBLIC_URL when the app is behind a tunnel, so Slack buttons link out
PUBLIC_URL = (os.environ.get("VETO_PUBLIC_URL") or "").strip().rstrip("/")


def public_base() -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    return f"http://127.0.0.1:{PORT}"


def connect_url(path: str = "/connect") -> str:
    return public_base() + path


def post_json(url: str, payload: dict) -> None:
    """Post a Slack response_url body. Fire and forget; failures are not fatal."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:  # noqa: BLE001
        pass

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
        response_url = payload.get("response_url") or ""

        # PayPal captures take a second or two; Slack gives us three. Ack first,
        # then replace the message through response_url from a worker thread.
        if response_url:
            self._json(200, {"ok": True})
            threading.Thread(
                target=self._slack_decide_job, args=(rid, verb, user, response_url), daemon=True
            ).start()
            return
        self._json(200, self._slack_decide_payload(rid, verb, user))

    def _slack_decide_job(self, rid: str, verb: str, user: str, response_url: str) -> None:
        post_json(response_url, self._slack_decide_payload(rid, verb, user))

    def _slack_decide_payload(self, rid: str, verb: str, user: str) -> dict:
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
            return {
                "replace_original": True,
                "response_type": "in_channel",
                "text": text,
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            }
        except VaultRequired as exc:
            wallet_url = connect_url("/connect")
            return {
                "replace_original": False,
                "response_type": "ephemeral",
                "text": "The company wallet has to be connected before this can capture.",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn",
                     "text": "*Connect the wallet first*\nOne payer approval, then every rule-pass captures on its own."}},
                    {"type": "actions", "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "Connect PayPal"},
                         "style": "primary", "url": wallet_url, "action_id": "veto_connect"},
                    ]},
                ],
            }
        except ApprovalPending:
            return {
                "replace_original": False,
                "response_type": "ephemeral",
                "text": "Still waiting on the PayPal approval. Open the wallet page and press Approve.",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn",
                     "text": "*Waiting on the payer*\nThe wallet approval has not come back yet."}},
                    {"type": "actions", "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "Finish connecting"},
                         "style": "primary", "url": connect_url("/connect"), "action_id": "veto_connect"},
                    ]},
                ],
            }
        except Exception as exc:  # noqa: BLE001
            return {"response_type": "ephemeral", "text": f"Could not action {rid}: {exc}"}

    def _slack_veto(self) -> None:
        """The slash command itself. Slack sends command/text/user_name/channel_name;
        we answer in Slack's own shape, so the same URL works as a Slack app endpoint
        and from curl:
            curl -X POST localhost:8787/api/slack/veto \
                 -d 'command=/veto&text=2 boxes of capsules from kettle supply&user_name=dan'

        Searching the catalogue takes 1-4s, and Slack drops the slash command after
        three, so a real Slack call gets an immediate "on it" and the result arrives
        through response_url. curl (no response_url) still gets the answer inline.
        """
        form = self._form()
        text = (form.get("text") or "").strip()
        command = form.get("command") or "/veto"
        response_url = form.get("response_url") or ""
        if text and response_url:
            self._json(200, {
                "response_type": "ephemeral",
                "text": f"Checking the charter for “{text[:80]}”…",
            })
            threading.Thread(target=self._slack_veto_job, args=(form, response_url), daemon=True).start()
            return
        self._json(200, self._slack_veto_payload(form))

    def _slack_veto_job(self, form: dict, response_url: str) -> None:
        message = self._slack_veto_payload(form)
        message.setdefault("response_type", "in_channel")
        post_json(response_url, message)

    def _slack_veto_payload(self, form: dict) -> dict:
        command = form.get("command") or "/veto"
        text = (form.get("text") or "").strip()
        channel = "#" + (form.get("channel_name") or "office-ops").lstrip("#")
        requester = (form.get("user_name") or "dan").strip() or "dan"
        if not text:
            return {
                "response_type": "ephemeral",
                "text": f"`{command}` needs something to buy. Try: `{command} 2 boxes of capsules from kettle supply`",
            }
        try:
            req = ENGINE.create_request(text, channel=channel, requester=requester.title())
        except VaultRequired:
            return {
                "response_type": "ephemeral",
                "text": "This workspace has to connect PayPal once before the agent can spend.",
                "blocks": [{"type": "section", "text": {"type": "mrkdwn",
                 "text": "*Connect PayPal first*\nThe agent can price and judge a cart, but the capture needs one payer approval."}},
                 {"type": "actions", "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "Connect PayPal"},
                     "style": "primary", "url": connect_url("/connect"), "action_id": "veto_connect"}]}],
            }
        except ApprovalPending:
            return {
                "response_type": "ephemeral",
                "text": "The wallet approval is still pending. Finish it, then ask again.",
                "blocks": [{"type": "actions", "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "Finish connecting"},
                     "style": "primary", "url": connect_url("/connect"), "action_id": "veto_connect"}]}],
            }
        except Exception as exc:  # noqa: BLE001
            return {"response_type": "ephemeral", "text": f"Could not price that: {exc}"}

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
        return {
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
        }

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
            signed = bool((os.environ.get("SLACK_SIGNING_SECRET") or "").strip())
            bot = bool((os.environ.get("SLACK_BOT_TOKEN") or "").strip())
            saved = ENGINE._read_vault_state()
            vault = ENGINE.vault_id or saved.get("vault_id")
            return self._json(200, {
                "ok": True, "paypal": ENGINE.paypal_mode, "llm": ENGINE.llm_mode,
                "catalog": ENGINE.catalog_mode, "vault": bool(vault),
                "vault_id": vault,
                "vault_pending": bool(ENGINE.pending_setup or saved.get("pending_setup")),
                "public_url": PUBLIC_URL or None,
                "slack": {"signing_secret": signed, "bot_token": bot,
                          "app_ready": signed and bot},
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
                if body.get("reset"):
                    ENGINE.vault_id = None
                    ENGINE.pending_setup = None
                    ENGINE._vault_file.unlink(missing_ok=True)
                vault = ENGINE.ensure_vault()
                return self._json(200, {"vault_id": vault})

            if path == "/api/connect/finish":
                return self._json(200, {"vault_id": ENGINE.finish_connect()})

            if path == "/api/slack/test":
                # build, but do not send, the payload a real Slack call would get
                return self._json(200, self._slack_veto_payload({
                    "command": "/veto", "text": body.get("text") or "2 packs of bamboo toilet paper",
                    "user_name": body.get("user") or "dan", "channel_name": "office-ops",
                }))

            return self._json(404, {"error": "unknown route"})

        except VaultRequired as exc:
            return self._json(428, {
                "error": "payer approval required",
                "approve_url": exc.approve_url,
                "setup_id": exc.setup_id,
                "connect": connect_url("/connect"),
                "next": "open approve_url as the sandbox buyer, then POST /api/connect/finish",
            })
        except ApprovalPending as exc:
            return self._json(425, {
                "error": "waiting on the PayPal approval",
                "setup_id": exc.setup_id,
                "connect": connect_url("/connect"),
                "next": "press Approve in the open PayPal tab, then POST /api/connect/finish",
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
    print(f"  wallet    http://127.0.0.1:{PORT}/connect")
    print(f"  paypal    {ENGINE.paypal_mode}   llm  {ENGINE.llm_mode}   catalog {ENGINE.catalog_mode}")
    if PUBLIC_URL:
        print(f"  public    {PUBLIC_URL}")
        print(f"  slack     {PUBLIC_URL}/api/slack/veto   {PUBLIC_URL}/api/slack/interact")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
