#!/usr/bin/env python3
"""Publish the Veto runner as a Docker Space on Hugging Face.

Free CPU Spaces need no credit card, keep their URL (`https://<owner>-<space>.hf.space`)
and sleep only after a long idle window, which is why the public demo lives here.
The image is the repository's own Dockerfile; secrets are set as Space secrets from
the local `.env` + the approved sandbox wallet, never written to the image.

    /tmp/hfenv/bin/python tools/deploy_space.py --space veto
    /tmp/hfenv/bin/python tools/deploy_space.py --space veto --no-wait

Requires a logged-in Hugging Face CLI (`hf auth login`) or HF_TOKEN. The Space is
created public and rebuilt from the staged files on every run.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = Path("/tmp/veto-space")

# env names the hosted runner needs; values come from .env (+ the vault token)
SECRET_KEYS = (
    "PAYPAL_LIVE",
    "PAYPAL_BASE",
    "PAYPAL_CLIENT_ID",
    "PAYPAL_CLIENT_SECRET",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE",
    "CHANNEL3_API_KEY",
    "CHANNEL3_COUNTRY",
    "CHANNEL3_CURRENCY",
    "SLACK_SIGNING_SECRET",
    "SLACK_BOT_TOKEN",
)


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def wallet_vault() -> str:
    path = ROOT / ".state" / "vault.json"
    try:
        saved = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    return str(saved.get("vault_id") or "") if isinstance(saved, dict) else ""


def stage_files(space_readme: Path) -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    shutil.copy2(ROOT / "Dockerfile", STAGE / "Dockerfile")
    shutil.copy2(space_readme, STAGE / "README.md")
    shutil.copy2(ROOT / "server.py", STAGE / "server.py")
    for package in ("veto", "spike"):
        shutil.copytree(
            ROOT / package,
            STAGE / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copytree(ROOT / "app", STAGE / "app")


def public_host(api, repo_id: str) -> str:
    info = api.space_info(repo_id)
    host = getattr(info, "host", None)
    if host:
        return host
    owner, name = repo_id.split("/", 1)
    return f"{owner.lower()}-{name.lower()}.hf.space"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--space", default="veto", help="Space name under your account")
    parser.add_argument("--no-wait", action="store_true", help="upload and return")
    parser.add_argument("--timeout", type=int, default=900, help="seconds to wait for RUNNING")
    args = parser.parse_args(argv[1:])

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("huggingface_hub is not installed. Run this with /tmp/hfenv/bin/python.",
              file=sys.stderr)
        return 2

    api = HfApi()
    who = api.whoami()
    owner = who.get("name") or ""
    if not owner:
        print("Not logged in. Run `hf auth login` first.", file=sys.stderr)
        return 2
    repo_id = f"{owner}/{args.space}"

    env = read_env()
    secrets = {key: env[key] for key in SECRET_KEYS if env.get(key)}
    vault = wallet_vault()
    if vault:
        secrets["PAYPAL_VAULT_ID"] = vault
    host = f"{owner.lower()}-{args.space.lower()}.hf.space"
    secrets["VETO_PUBLIC_URL"] = f"https://{host}"

    print(f"space   {repo_id}")
    print(f"public  https://{host}")
    print("staging files", end="", flush=True)
    stage_files(ROOT / "deploy" / "huggingface" / "README.md")
    print(f" · {sum(1 for _ in STAGE.rglob('*') if _.is_file())} files")

    url = api.create_repo(
        repo_id=repo_id,
        repo_type="space",
        space_sdk="docker",
        private=False,
        exist_ok=True,
    )
    print("repo    ready", url)

    for key, value in secrets.items():
        api.add_space_secret(repo_id=repo_id, key=key, value=value)
    print(f"secrets {len(secrets)} set")

    api.upload_folder(
        repo_id=repo_id,
        repo_type="space",
        folder_path=str(STAGE),
        commit_message="Deploy the Veto runner to Hugging Face Spaces",
    )
    print("files   uploaded (build starts now)")

    if args.no_wait:
        return 0

    started = time.time()
    last = ""
    while time.time() - started < args.timeout:
        runtime = api.get_space_runtime(repo_id)
        stage = str(getattr(runtime, "stage", "") or "").upper()
        if stage != last:
            print(f"runtime {stage or 'unknown'}")
            last = stage
        if stage in ("RUNNING",):
            host = public_host(api, repo_id)
            print(f"\nlive at https://{host}")
            print(f"health  https://{host}/api/health")
            print(f"slack   https://{host}/api/slack/veto")
            return 0
        if stage in ("BUILD_ERROR", "RUNTIME_ERROR", "STOPPED"):
            print(f"the Space stopped in stage {stage}; check the logs on huggingface.co",
                  file=sys.stderr)
            return 1
        time.sleep(8)
    print("timed out waiting for the Space to run", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
