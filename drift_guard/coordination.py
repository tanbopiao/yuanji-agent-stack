"""coordination.py — 跨Agent协同校验算子

职责: 管控父子Agent、多节点协同任务,杜绝分层传递过程中的目标衰减、认知偏差。

固定规则:
    1. 父Agent下发子任务时,必须完整携带结构化目标 JSON + 全部约束规则,
       禁止简化、省略(descendant_payload 强制内嵌完整 AnchoredGoal)
    2. 子Agent输出结果强制对齐父目标: 父子目标语义相似度 < 0.6 →
       直接拦截输出、触发告警
    3. 多Agent任务溯源链路: 记录每一级任务传递偏差值(provenance 链)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG
from .observability import METRICS, TraceLogger


@dataclass
class ProvenanceNode:
    """溯源链节点: 一级任务传递的偏差留痕。"""
    parent_agent: str
    child_agent: str
    similarity: float
    intercepted: bool
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"parent": self.parent_agent, "child": self.child_agent,
                "similarity": round(self.similarity, 4),
                "intercepted": self.intercepted, "ts": self.ts}


class CoordinationOperator:
    """跨Agent协同校验: 子任务下发封装 + 输出对齐拦截 + 溯源链。"""

    def __init__(self, similarity_fn: Any, anchor: Any,
                 logger: Optional[TraceLogger] = None) -> None:
        """similarity_fn: (text_a, text_b) -> float ∈ [0,1] 语义相似度函数
        (生产注入 semantic 后端的余弦相似度;模块自身不持有嵌入模型,保持解耦)。
        anchor: AnchorOperator,提供父目标真值。"""
        self.similarity_fn = similarity_fn
        self.anchor = anchor
        self.logger = logger or TraceLogger("coordination")
        self._provenance: List[ProvenanceNode] = []

    # ---- 1. 父→子 任务下发(完整目标 JSON 强制携带) ----
    def dispatch_child_task(self, child_agent: str, parent_agent: str = "root",
                            subtask_brief: str = "") -> Dict[str, Any]:
        """构造子任务载荷: 完整结构化目标 JSON + 全部约束规则,禁止省略。"""
        goal = self.anchor.goal  # 未锚定将直接 RuntimeError,符合"先锚定再下发"
        payload = {
            "parent_agent": parent_agent,
            "child_agent": child_agent,
            "subtask_brief": subtask_brief,
            # 完整携带,禁止简化/省略:
            "anchored_goal": goal.to_dict(),
            "constraint_rules": list(goal.constraint_conditions),
            "success_criteria": list(goal.success_criteria),
            "dispatch_ts": time.time(),
        }
        self.logger.log("trace", "child_task_dispatched",
                        {"child": child_agent, "goal_id": goal.goal_id,
                         "constraints": len(payload["constraint_rules"])})
        METRICS.incr("child_tasks_dispatched")
        return payload

    def verify_payload_completeness(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        """子Agent侧防衰减校验: 收到的载荷必须含完整目标 JSON 与全部约束。"""
        missing = []
        for key in ("anchored_goal", "constraint_rules", "success_criteria"):
            if key not in payload or payload[key] in (None, [], ""):
                missing.append(key)
        goal = payload.get("anchored_goal") or {}
        for f in ("core_target", "constraint_conditions",
                  "success_criteria", "deadline"):
            if f not in goal:
                missing.append(f"anchored_goal.{f}")
        ok = not missing
        reason = None if ok else f"载荷缺失字段(目标衰减): {missing}"
        self.logger.log("trace" if ok else "threshold",
                        "payload_completeness_check",
                        {"ok": ok, "missing": missing},
                        level=0 if ok else 30)
        if not ok:
            METRICS.incr("payload_decay_detected")
        return ok, reason or ""

    # ---- 2. 子输出对齐父目标(< 0.6 直接拦截) ----
    def verify_child_output(self, child_agent: str, output_text: str,
                            payload: Optional[Dict[str, Any]] = None) -> Tuple[bool, float]:
        """子Agent输出强制对齐父目标。

        返回 (allowed, similarity);similarity < 0.6 → 拦截输出并告警。
        """
        parent_target = self.anchor.goal.core_target
        sim = float(self.similarity_fn(output_text, parent_target))
        threshold = CONFIG.child_alignment_threshold
        intercepted = sim < threshold

        node = ProvenanceNode(parent_agent="root", child_agent=child_agent,
                              similarity=sim, intercepted=intercepted)
        self._provenance.append(node)
        METRICS.record_similarity("coordination", sim, step=len(self._provenance))
        self.logger.log("trace" if not intercepted else "threshold",
                        "child_output_check",
                        {"child": child_agent, "similarity": round(sim, 4),
                         "threshold": threshold, "intercepted": intercepted},
                        level=20 if not intercepted else 30)
        if intercepted:
            METRICS.incr("child_output_intercepted")
        return (not intercepted), sim

    # ---- 3. 溯源链 ----
    def provenance(self) -> List[Dict[str, Any]]:
        """多Agent任务溯源链: 每一级传递的偏差值留痕。"""
        return [n.to_dict() for n in self._provenance]

    def deviation_report(self) -> Dict[str, Any]:
        """汇总各级传递偏差,供故障报告与阈值迭代使用。"""
        sims = [n.similarity for n in self._provenance]
        return {
            "hops": len(sims),
            "min_similarity": round(min(sims), 4) if sims else None,
            "interceptions": sum(1 for n in self._provenance if n.intercepted),
            "chain": self.provenance(),
        }
