#!/bin/bash
# Opens the finance app: starts the local server if it isn't running, then opens the browser.
# Works from any clone location. Used by the Finance.app that scripts/make_mac_app.sh builds
# (Finder-launched apps get a minimal PATH, so add the usual uv / Homebrew locations here).
# Port: FINANCE_PORT (default 8747).
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${FINANCE_PORT:-8747}"
URL="http://127.0.0.1:$PORT/"
mkdir -p "$ROOT/data"
LOG="$ROOT/data/server.log"

healthy() { curl -fsS -m 2 "${URL}api/health" >/dev/null 2>&1; }
alert() { osascript -e "display alert \"Finance\" message \"$1\"" >/dev/null 2>&1 || echo "$1" >&2; }

if ! healthy; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    alert "Port $PORT is used by another program. Close it and try again."
    exit 1
  fi
  if ! command -v uv >/dev/null 2>&1; then
    alert "uv was not found. Install it (https://docs.astral.sh/uv/) and try again."
    exit 1
  fi
  cd "$ROOT" && nohup ./finance serve --port "$PORT" >"$LOG" 2>&1 &
  for _ in $(seq 1 60); do healthy && break; sleep 0.5; done
  if ! healthy; then
    alert "The app didn't start. See data/server.log."
    exit 1
  fi
fi
open "$URL"
