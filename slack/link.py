#!/usr/bin/env python3
"""Create the Veto Slack app in one click.

    python3 slack/link.py                       # uses VETO_PUBLIC_URL from .env
    python3 slack/link.py https://foo.trycloudflare.com

It prints a URL that opens Slack's "create an app" flow with the whole manifest
already filled in: slash command, interactivity URL, and the three bot scopes.
Pick a workspace, press Create, then Install.

Slack documents this link shape at
https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests/#sharing
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def dotenv() -> dict:
    path = ROOT / ".env"
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def manifest(base: str) -> dict:
    return {
        "display_information": {
            "name": "Veto",
            "description": (
                "Every purchase an agent proposes has to pass the team's spending "
                "charter. Over the line? A human signs first."
            ),
            "background_color": "#17191b",
        },
        "features": {
            "bot_user": {"display_name": "Veto", "always_online": True},
            "slash_commands": [
                {
                    "command": "/veto",
                    "url": f"{base}/api/slack/veto",
                    "description": "Order supplies through the team's spending rules.",
                    "usage_hint": "2 packs of bamboo toilet paper",
                    "should_escape": False,
                }
            ],
        },
        "oauth_config": {
            "scopes": {"bot": ["commands", "chat:write", "chat:write.public"]}
        },
        "settings": {
            "interactivity": {"is_enabled": True, "request_url": f"{base}/api/slack/interact"},
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }


def main(argv: list[str]) -> int:
    env = dotenv()
    base = (argv[1] if len(argv) > 1 else env.get("VETO_PUBLIC_URL", "")).strip().rstrip("/")
    if not base.startswith("https://"):
        print("Need the public https URL of the runner (the tunnel), e.g.\n"
              "  python3 slack/link.py https://something.trycloudflare.com", file=sys.stderr)
        return 2

    doc = manifest(base)
    (ROOT / "slack" / "manifest.json").write_text(json.dumps(doc, indent=2) + "\n")

    link = "https://api.slack.com/apps?new_app=1&manifest_json=" + urllib.parse.quote(
        json.dumps(doc, separators=(",", ":"))
    )
    print("Slack app manifest written to slack/manifest.json for", base)
    print("\nOpen this to create the app:\n")
    print(link)
    print("\nThen: Install to Workspace → copy the Signing Secret (Basic Information)")
    print("      and the Bot token xoxb-… (OAuth & Permissions) into .env, and restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
