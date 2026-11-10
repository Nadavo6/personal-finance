#!/usr/bin/env bash
# Store scraper credentials in the macOS Keychain, one item per provider and field:
#   service "finapp-<provider>", account "username" / "password".
# Usage: sync/setup_credentials.sh            # leumi + max
#        sync/setup_credentials.sh visaCal    # only the providers you name (leumi|max|visaCal)
# Nothing is written to disk or echoed. Re-running updates the items (-U).
# The password is typed into `security`'s own prompt (-w last), so it never appears in
# argv / `ps` or in this shell's variables.
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script needs macOS (security / Keychain)." >&2
  exit 1
fi

providers=("$@")
if [[ ${#providers[@]} -eq 0 ]]; then
  providers=(leumi max)
fi

for p in "${providers[@]}"; do
  case "$p" in
    leumi|max|visaCal) ;;
    *) echo "Unknown provider '$p' (use leumi, max or visaCal)" >&2; exit 1 ;;
  esac
  echo "== $p =="
  read -r -p "  username / ID: " user
  security add-generic-password -U -s "finapp-$p" -a username -l "finapp $p username" -w "$user" >/dev/null
  echo "  Keychain will now ask for the $p password (twice):"
  security add-generic-password -U -s "finapp-$p" -a password -l "finapp $p password" -w
  unset user
  echo "  saved to Keychain as finapp-$p"
done
echo "Done. The first sync will ask to allow 'security' access; choose Always Allow."
