#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/mempool-trieguard}"
DURATION="${LIVE_BENCHMARK_DURATION:-6h}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${LIVE_BENCHMARK_OUT:-results/live_mempool_$RUN_ID}"
LOG_DIR="${LOG_DIR:-logs}"
LOG_PATH="$LOG_DIR/live_mempool_$RUN_ID.log"
PID_PATH="$LOG_DIR/live_mempool_$RUN_ID.pid"

cd "$APP_DIR"
mkdir -p "$LOG_DIR" "$(dirname "$OUT_DIR")" data

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${APP_PROTECTED_ACCOUNTS_PATH:=results/live_active_protected_accounts_24h_1000victims.json}"
export APP_PROTECTED_ACCOUNTS_PATH
export LIVE_BENCHMARK_REGION="${LIVE_BENCHMARK_REGION:-unknown}"

nohup ./server \
  --config configs/app.yaml \
  --live-benchmark-duration "$DURATION" \
  --live-benchmark-out "$OUT_DIR" \
  > "$LOG_PATH" 2>&1 &

pid=$!
echo "$pid" > "$PID_PATH"
printf 'started run_id=%s pid=%s out=%s log=%s\n' "$RUN_ID" "$pid" "$OUT_DIR" "$LOG_PATH"
