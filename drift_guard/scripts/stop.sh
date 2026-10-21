#!/usr/bin/env bash
# stop.sh — 停止指标面板 API
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f storage/metrics_api.pid ]; then
  kill "$(cat storage/metrics_api.pid)" 2>/dev/null || true
  rm -f storage/metrics_api.pid
  echo "[DriftGuard] metrics API 已停止"
else
  echo "[DriftGuard] 未发现运行中的 metrics API"
fi
