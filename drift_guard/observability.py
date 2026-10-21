"""observability.py — 可观测性模块

全链路日志记录、推理轨迹留存、阈值超限日志、熔断事件日志;
预留可视化面板数据接口(相似度、推理步数、资源消耗、熔断次数核心指标)。
零第三方重依赖: 日志用标准库 logging + JSONL 落盘,指标用内存计数器 + 快照导出。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .config import CONFIG

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "drift_guard", "storage", "logs")
os.makedirs(LOG_DIR, exist_ok=True)


class TraceLogger:
    """全链路 JSONL 日志: 每条事件带时间戳/任务ID/事件类型,可回放推理轨迹。

    文件:
        trace.jsonl   — 全链路事件(推理步、工具调用、校验结果)
        threshold.jsonl — 阈值超限日志
        circuit.jsonl — 熔断事件日志
    """

    FILES = {
        "trace": "trace.jsonl",
        "threshold": "threshold.jsonl",
        "circuit": "circuit.jsonl",
    }

    def __init__(self, task_id: str, log_dir: str = LOG_DIR) -> None:
        self.task_id = task_id
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._std = logging.getLogger(f"driftguard.{task_id}")
        if not self._std.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            self._std.addHandler(h)
            self._std.setLevel(logging.INFO)

    def log(self, kind: str, event: str, data: Optional[Dict[str, Any]] = None,
            level: int = logging.INFO) -> None:
        """写一条结构化事件。kind ∈ {trace, threshold, circuit}。"""
        record = {
            "ts": round(time.time(), 3),
            "task_id": self.task_id,
            "kind": kind,
            "event": event,
            "data": data or {},
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            path = os.path.join(self.log_dir, self.FILES[kind])
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        self._std.log(level, "%s %s", event, line if level >= logging.WARNING else "")

    def replay_trajectory(self) -> List[Dict[str, Any]]:
        """回放本任务推理语义轨迹链(trace 事件按时间序)。"""
        path = os.path.join(self.log_dir, self.FILES["trace"])
        if not os.path.exists(path):
            return []
        out = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("task_id") == self.task_id:
                    out.append(rec)
        return out


class MetricsRegistry:
    """内存指标注册表 + 面板数据快照接口。

    核心指标: 相似度、推理步数、资源消耗、熔断次数。
    GET /metrics 快照即 export() 返回值,可直接喂给 Grafana/面板。
    """

    def __init__(self, maxlen: int = 1000) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}
        self._gauges: Dict[str, float] = {}
        self._series: Dict[str, Deque[Dict[str, float]]] = {}
        self._maxlen = maxlen

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + n

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def push(self, series: str, point: Dict[str, float]) -> None:
        """追加时序点(如每步相似度),超过 maxlen 自动淘汰最旧。"""
        point = dict(point, ts=round(time.time(), 3))
        with self._lock:
            dq = self._series.setdefault(series, deque(maxlen=self._maxlen))
            dq.append(point)

    def export(self) -> Dict[str, Any]:
        """面板数据快照: counters + gauges + 最近时序。"""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "series": {k: list(v) for k, v in self._series.items()},
            }

    # 面板核心指标便捷方法 -------------------------------------------------
    def record_similarity(self, task_id: str, similarity: float, step: int) -> None:
        self.push("similarity", {"task": hash(task_id) % 10**8, "step": step,
                                 "similarity": round(similarity, 4)})

    def record_steps(self, task_id: str, steps: int) -> None:
        self.gauge("reasoning_steps", steps)

    def record_resources(self, task_id: str, tokens: int, api_calls: int,
                         runtime_s: float) -> None:
        self.gauge("tokens_used", float(tokens))
        self.gauge("api_calls", float(api_calls))
        self.gauge("runtime_seconds", runtime_s)

    def record_breaker(self, level: int, task_id: str) -> None:
        self.incr(f"circuit_breaker_L{level}_total")
        self.incr("circuit_breaker_total")

    def record_context_saturation(self, ratio: float) -> None:
        self.gauge("context_saturation", round(ratio, 4))


# 进程级单例,供所有算子共享
METRICS = MetricsRegistry()


class MetricsAPIServer:
    """轻量可视化面板数据接口(标准库 http.server,零额外依赖)。

    端点:
        GET /metrics        — 指标快照 JSON
        GET /trajectory/<task_id> — 指定任务推理轨迹回放
        GET /healthz        — 存活探针
    """

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        registry = METRICS

        class Handler(BaseHTTPRequestHandler):
            def _json(self, code: int, payload: Any) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/metrics":
                    self._json(200, registry.export())
                elif self.path == "/healthz":
                    self._json(200, {"status": "ok"})
                elif self.path.startswith("/trajectory/"):
                    tid = self.path.rsplit("/", 1)[-1]
                    self._json(200, {"task_id": tid,
                                     "events": TraceLogger(tid).replay_trajectory()})
                else:
                    self._json(404, {"error": "not found"})

            def log_message(self, *a: Any) -> None:  # 静默访问日志
                pass

        self._server = ThreadingHTTPServer(
            (host or CONFIG.metrics_api_host, port or CONFIG.metrics_api_port), Handler)

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def start_background(self) -> None:
        threading.Thread(target=self.serve_forever, daemon=True,
                         name="driftguard-metrics-api").start()

    def stop(self) -> None:
        self._server.shutdown()
