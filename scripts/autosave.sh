#!/bin/bash
# Commits and pushes work-in-progress every N seconds; exits loudly if the privacy guard blocks a commit.
cd "$(dirname "$0")/.." || exit 1
interval=${1:-600}; rounds=${2:-36}
for i in $(seq 1 "$rounds"); do
  sleep "$interval"
  git add -A
  if ! git diff --cached --quiet; then
    if ! out=$(git commit -q -m "wip: autosave $(date +%H:%M)" 2>&1); then
      echo "AUTOSAVE BLOCKED: $out"; git diff --cached --name-only; exit 2
    fi
    git push -q origin HEAD 2>&1 | tail -1
    echo "saved $(date +%H:%M) $(git log -1 --stat --oneline | tail -1)"
  fi
done
