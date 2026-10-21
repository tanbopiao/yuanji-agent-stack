#!/usr/bin/env bash
# install.sh — DriftGuard 安装脚本
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[DriftGuard] 创建虚拟环境..."
python3 -m venv .venv 2>/dev/null || true
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true

echo "[DriftGuard] 安装基础依赖..."
python3 -m pip install --quiet -r drift_guard/requirements.txt

echo "[DriftGuard] 校验核心链路导入..."
python3 - <<'PY'
import sys; sys.path.insert(0, ".")
import drift_guard
from drift_guard.middleware import DriftGuardMiddleware
g = DriftGuardMiddleware(task="安装自检: 验证防控链路可用", task_id="install_check")
v = g.before_step("安装自检: 验证防控链路可用")
assert v["allowed"], v
g.unmount()
print("[DriftGuard] 核心链路 OK — 降级嵌入模式可用")
PY

echo "[DriftGuard] 安装完成。生产环境建议: pip install FlagEmbedding"
