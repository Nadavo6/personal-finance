#!/bin/bash
# Starts a throwaway copy of the app for UX review on :8795, using a copy of the real DB
# under data/tmp/reviewdata (never the real data/finance.db). Usage: scripts/review_server.sh [fresh]
cd "$(dirname "$0")/.." || exit 1
R="$PWD/data/tmp/reviewdata"
if [ "$1" = "fresh" ]; then sqlite3 data/finance.db ".backup $R/finance.db"; fi
pkill -f "finapp serve --port 8795" 2>/dev/null; sleep 1
FINAPP_DATA="$R" FINAPP_DB="$R/finance.db" nohup ./finance serve --port 8795 > "$R/server.log" 2>&1 &
sleep 4; tail -1 "$R/server.log"
