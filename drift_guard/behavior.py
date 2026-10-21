"""behavior.py — 行为模式风控算子

职责: 识别显性异常行为(死循环、资源滥用、上下文溢出),解决长任务熵增崩塌。

固定规则:
    1. 死循环检测: 连续 3 次重复动作序列 → 判定无效循环,触发二级熔断
    2. 资源监控: 单任务 Token 消耗、API 调用次数、运行时长,超阈值即刻告警;
       用量达上限 80%(warn_ratio)触发"临近上限"一级熔断条件
    3. 上下文饱和度管控: 占用率 >= 90% 强制触发目标重锚定流程,
       清理无效上下文,保留核心目标
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple
from collections import deque

from .config import CONFIG
from .observability import METRICS, TraceLogger


@dataclass
class ResourceUsage:
    tokens: int = 0
    api_calls: int = 0
    start_ts: float = field(default_factory=time.time)

    def runtime_seconds(self) -> float:
        return time.time() - self.start_ts


class BehaviorRiskOperator:
    """行为模式风控: 死循环检测 + 资源监控 + 上下文饱和度管控。"""

    def __init__(self, logger: Optional[TraceLogger] = None,
                 context_budget_tokens: int = 128000) -> None:
        self.logger = logger or TraceLogger("behavior")
        self._actions: Deque[str] = deque(maxlen=64)   # 最近动作序列(规范化指纹)
        self.usage = ResourceUsage()
        self.context_budget = context_budget_tokens    # 上下文窗口 Token 预算
        self.context_used = 0

    # ---- 1. 死循环检测 ---------------------------------------------------
    def record_action(self, action_fingerprint: str) -> Dict[str, Any]:
        """记录一次动作(规范化指纹: 如 'tool=search&query=X 规范化后哈希')。

        连续 3 次重复同一动作 → 无效循环,触发二级熔断条件。
        """
        self._actions.append(action_fingerprint)
        limit = CONFIG.loop_repeat_limit
        streak = self._trailing_streak()
        looped = streak >= limit
        result = {"fingerprint": action_fingerprint, "streak": streak,
                  "loop_detected": looped}
        self.logger.log("trace" if not looped else "threshold",
                        "loop_check", result,
                        level=logging.INFO if not looped else logging.WARNING)
        if looped:
            METRICS.incr("infinite_loop_detected")
        return result

    def _trailing_streak(self) -> int:
        """尾部连续相同动作的次数。"""
        if not self._actions:
            return 0
        last = self._actions[-1]
        n = 0
        for a in reversed(self._actions):
            if a == last:
                n += 1
            else:
                break
        return n

    # ---- 2. 资源监控 -----------------------------------------------------
    def report_usage(self, tokens_delta: int = 0, api_calls_delta: int = 0) -> Dict[str, Any]:
        """每次推理/调用后上报资源增量,返回用量检查结果。

        返回:
            exhausted=True  → 超阈值(三级熔断条件: 资源严重溢出)
            nearing_limit=True → 达 warn_ratio(一级熔断条件: 资源临近上限)
        """
        self.usage.tokens += max(0, tokens_delta)
        self.usage.api_calls += max(0, api_calls_delta)
        runtime = self.usage.runtime_seconds()

        checks = {
            "tokens": (self.usage.tokens, CONFIG.res_max_tokens),
            "api_calls": (self.usage.api_calls, CONFIG.res_max_api_calls),
            "runtime": (runtime, CONFIG.res_max_runtime),
        }
        ratios = {k: v / lim for k, (v, lim) in checks.items()}
        worst = max(ratios.values())
        result = {
            "usage": {"tokens": self.usage.tokens, "api_calls": self.usage.api_calls,
                      "runtime_seconds": round(runtime, 1)},
            "ratios": {k: round(r, 4) for k, r in ratios.items()},
            "nearing_limit": worst >= CONFIG.res_warn_ratio and worst < 1.0,
            "exhausted": worst >= 1.0,
        }
        METRICS.record_resources("behavior", self.usage.tokens,
                                 self.usage.api_calls, runtime)
        level = None
        if result["exhausted"]:
            level = "threshold"
            METRICS.incr("resource_exhausted")
        elif result["nearing_limit"]:
            level = "threshold"
            METRICS.incr("resource_nearing_limit")
        self.logger.log(level or "trace", "resource_check", result,
                        level=0 if level is None else
                        (__import__("logging").WARNING if level == "threshold" else 20))
        return result

    # ---- 3. 上下文饱和度管控 ---------------------------------------------
    def report_context(self, context_tokens: int) -> Dict[str, Any]:
        """上报当前上下文 Token 占用;>= 90% 返回 must_reanchor=True,
        由熔断调度器强制触发目标重锚定并清理无效上下文。"""
        self.context_used = context_tokens
        ratio = context_tokens / self.context_budget if self.context_budget else 0.0
        saturated = ratio >= CONFIG.context_saturation
        result = {"context_tokens": context_tokens, "budget": self.context_budget,
                  "saturation": round(ratio, 4), "must_reanchor": saturated}
        METRICS.record_context_saturation(ratio)
        self.logger.log("threshold" if saturated else "trace",
                        "context_saturation_check", result,
                        level=(0 if not saturated else
                               __import__("logging").WARNING))
        if saturated:
            METRICS.incr("context_saturation_events")
        return result

    def compact_context(self, keep_recent_steps: int = 3,
                        carry_items: Optional[List[str]] = None) -> Dict[str, Any]:
        """重锚定清理: 模拟上下文压缩——丢弃中间历史,仅保留核心目标与最近 N 步摘要。

        返回新上下文的构成说明(由调度器注入真实上下文管理器执行裁剪)。
        """
        goal = self.anchor_goal_summary() if hasattr(self, "anchor_goal_summary") else ""
        plan = {
            "strategy": "compact_context",
            "keep_recent_steps": keep_recent_steps,
            "preserve": ["core_target"] + list(carry_items or []),
            "core_target_placeholder": goal,
            "estimated_new_usage_tokens": int(self.context_budget *
                                              (1 - CONFIG.context_saturation) * 0.5),
        }
        self.context_used = plan["estimated_new_usage_tokens"]
        self.logger.log("trace", "context_compacted", plan)
        return plan

    def set_goal_summary_provider(self, provider: Any) -> None:
        """注入目标摘要提供函数(来自 AnchorOperator),保持模块解耦。"""
        self.anchor_goal_summary = provider  # type: ignore[attr-defined]

    # ---- 快照 ------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {
            "recent_actions": list(self._actions)[-8:],
            "trailing_streak": self._trailing_streak(),
            "usage": {"tokens": self.usage.tokens,
                      "api_calls": self.usage.api_calls,
                      "runtime_seconds": round(self.usage.runtime_seconds(), 1)},
            "context_used": self.context_used,
        }
