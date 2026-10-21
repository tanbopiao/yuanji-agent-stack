#!/usr/bin/env bash
# start.sh — 启动指标面板 API(独立启停,不动业务)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p storage
nohup python3 - <<'PY' > storage/metrics_api.log 2>&1 &
import sys; sys.path.insert(0, ".")
from drift_guard.observability import MetricsAPIServer
print("DriftGuard metrics API on :9820  GET /metrics /healthz /trajectory/<task_id>")
MetricsAPIServer().serve_forever()
PY
echo $! > storage/metrics_api.pid
echo "[DriftGuard] metrics API 已启动 (pid $(cat storage/metrics_api.pid), :9820)"
