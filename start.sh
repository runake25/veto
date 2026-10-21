#!/usr/bin/env bash
# Start the whole demo in one command: tunnel, then the runner.
#
#   ./start.sh              bring up a public tunnel + the runner
#   ./start.sh --no-tunnel  local only (no Slack callbacks)
#
# The tunnel URL changes on every start (that is what a free quick tunnel does),
# so the script notices the new URL, writes it to .env, and tells you what to
# re-paste in the Slack app if the host moved.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8787}"
TUNNEL_LOG="$(mktemp -t veto-tunnel)"
TUNNEL_PID=""

cleanup() {
  [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" 2>/dev/null || true
  rm -f "$TUNNEL_LOG"
}
trap cleanup EXIT INT TERM

env_get() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- ; }
env_set() {
  local key="$1" value="$2"
  if grep -qE "^$key=" .env 2>/dev/null; then
    python3 - "$key" "$value" <<'PY'
import pathlib, sys
key, value = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env")
lines = p.read_text().splitlines()
p.write_text("\n".join(f"{key}={value}" if l.startswith(key + "=") else l for l in lines) + "\n")
PY
  else
    printf '\n%s=%s\n' "$key" "$value" >> .env
  fi
}

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use. Stop the other runner first, or: PORT=8788 ./start.sh"
  exit 1
fi

PUBLIC="$(env_get VETO_PUBLIC_URL || true)"

if [[ "${1:-}" != "--no-tunnel" ]]; then
  if ! command -v cloudflared >/dev/null 2>&1; then
    echo "cloudflared is not installed (brew install cloudflared). Starting without a tunnel."
  else
    echo "Opening a tunnel…"
    cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate >"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!
    for _ in $(seq 1 40); do
      NEW="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)"
      [[ -n "$NEW" ]] && break
      sleep 0.5
    done
    if [[ -n "${NEW:-}" ]]; then
      if [[ "$NEW" != "$PUBLIC" ]]; then
        echo "Tunnel host changed:"
        echo "  was  ${PUBLIC:-none}"
        echo "  now  $NEW"
        echo "  → in the Slack app settings, update the slash command URL and the"
        echo "    interactivity URL, and re-run: python3 slack/link.py"
      fi
      PUBLIC="$NEW"
      env_set VETO_PUBLIC_URL "$PUBLIC"
    else
      echo "The tunnel did not come up; continuing locally. See $TUNNEL_LOG"
    fi
  fi
fi

export VETO_PUBLIC_URL="$PUBLIC"
export PORT
echo
exec python3 server.py
