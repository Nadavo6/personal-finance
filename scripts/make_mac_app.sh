#!/bin/bash
# Builds a clickable Finance.app (macOS) that runs scripts/open_app.sh from this clone.
# Usage: scripts/make_mac_app.sh [--icon icon.png|icon.icns] [--dest ~/Applications]
# Re-run it after moving the clone: the app stores this folder's path.
set -euo pipefail
[[ "$(uname)" == "Darwin" ]] || { echo "macOS only (uses osacompile)." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/Applications"
ICON=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --icon) ICON="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 1 ;;
  esac
done
APP="$DEST/Finance.app"
mkdir -p "$DEST"
chmod +x "$ROOT/scripts/open_app.sh" "$ROOT/finance"

# AppleScript string literal: escape backslashes and quotes in the path
script_path="${ROOT//\\/\\\\}/scripts/open_app.sh"
script_path="${script_path//\"/\\\"}"
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script quoted form of \"$script_path\""

if [[ -n "$ICON" ]]; then
  res="$APP/Contents/Resources"
  case "$ICON" in
    *.icns) cp "$ICON" "$res/applet.icns" ;;
    *)  # any image sips can read -> .icns via an iconset
      tmp="$(mktemp -d)"; set="$tmp/icon.iconset"; mkdir "$set"
      for s in 16 32 128 256 512; do
        sips -z $s $s "$ICON" --out "$set/icon_${s}x${s}.png" >/dev/null
        sips -z $((s * 2)) $((s * 2)) "$ICON" --out "$set/icon_${s}x${s}@2x.png" >/dev/null
      done
      iconutil -c icns "$set" -o "$res/applet.icns"
      rm -rf "$tmp" ;;
  esac
  touch "$APP"  # make Finder pick up the new icon
fi
echo "Built $APP — double-click it (first launch: right-click → Open)."
