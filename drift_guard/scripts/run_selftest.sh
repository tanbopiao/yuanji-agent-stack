#!/usr/bin/env bash
# run_selftest.sh — 运行自测套件,输出准确率/误报率验证结果
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s drift_guard/tests -v 2>&1 | tee storage/selftest.log
echo "[DriftGuard] 自测完成,详细报告见 docs/selftest_report.md"
