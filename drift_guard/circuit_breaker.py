"""circuit_breaker.py — 三级熔断自愈调度算子(核心控制中枢)

三级熔断严格匹配方案逻辑:

一级熔断(预警级·自动纠偏)
    触发: 语义漂移超阈值 / 推理步数超限 / 资源临近上限
    动作: 日志留存、推送目标重锚定指令、强制重新对齐初始任务
    恢复: 重锚定校验通过后自动接续原有任务,无断点丢失

二级熔断(限制级·强制管控)
    触发: 连续 2 次一级熔断 / 检测到死循环 / 子Agent严重偏离目标
    动作: 暂停所有业务执行、锁定工具调用权限、进入只读观测模式
    恢复: 人工确认目标 / 自动触发备用任务流程

三级熔断(终止级·彻底止损)
    触发: 连续 3 次二级熔断 / 恶意无效执行 / 资源严重溢出
    动作: 终止全部任务进程、回收所有资源、生成标准化故障报告
          (含漂移轨迹、异常节点、资源消耗数据)
    恢复: 需人工排查修复后,重启任务链路

自愈策略由 configs/self_heal.yaml 驱动,常见场景自动修复,无法自愈生成工单。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional

from .config import CONFIG
from .observability import METRICS, TraceLogger


class BreakerLevel(IntEnum):
    NONE = 0
    L1_WARNING = 1     # 预警级
    L2_RESTRICT = 2    # 限制级
    L3_TERMINATE = 3   # 终止级


@dataclass
class BreakerEvent:
    level: int
    reason: str
    detail: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"level": int(self.level), "reason": self.reason,
                "detail": self.detail, "ts": self.ts}


class CircuitBreakerOperator:
    """三级熔断 + 自愈恢复调度中枢。"""

    def __init__(self, logger: Optional[TraceLogger] = None,
                 reanchor_fn: Optional[Callable[[str], bool]] = None,
                 halt_fn: Optional[Callable[[], None]] = None,
                 terminate_fn: Optional[Callable[[], None]] = None,
                 ticket_fn: Optional[Callable[[Dict[str, Any]], str]] = None) -> None:
        """回调注入(与业务侧解耦,由中间件装配):
            reanchor_fn: 执行目标重锚定,返回校验是否通过
            halt_fn:     暂停业务执行、锁定工具权限、进入只读观测
            terminate_fn: 终止全部任务进程、回收资源
            ticket_fn:   生成工单告警(无法自愈时),返回工单号
        """
        self.logger = logger or TraceLogger("breaker")
        self._reanchor = reanchor_fn
        self._halt = halt_fn
        self._terminate = terminate_fn
        self._ticket = ticket_fn

        self.level = BreakerLevel.NONE
        self.l1_streak = 0
        self.l2_streak = 0
        self.events: List[BreakerEvent] = []

    # ---- 事件升级判定 ----
    def _fire(self, level: BreakerLevel, reason: str,
              detail: Dict[str, Any]) -> BreakerEvent:
        ev = BreakerEvent(level=int(level), reason=reason, detail=detail)
        self.events.append(ev)
        self.level = max(self.level, level)
        METRICS.record_breaker(int(level), "breaker")
        self.logger.log("circuit", f"breaker_L{int(level)}_triggered",
                        {**ev.to_dict()}, level=30 + int(level))
        return ev

    def trigger_level1(self, reason: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """一级熔断(预警级·自动纠偏): 日志留存 → 推送重锚定 → 校验通过自动接续。"""
        detail = detail or {}
        ev = self._fire(BreakerLevel.L1_WARNING, reason, detail)
        self.l1_streak += 1
        self.l2_streak = 0

        # 升级判定: 连续 2 次一级熔断 → 二级
        if self.l1_streak >= CONFIG.level1_to_level2:
            return self.trigger_level2(f"连续{self.l1_streak}次一级熔断",
                                       {"origin": reason})

        action = {"logged": True, "reanchor_requested": True, "resumed": False}
        strategy = CONFIG.heal_strategy("semantic_drift")
        if strategy == "re_anchor" and self._reanchor:
            attempts = CONFIG.heal_attempts("semantic_drift")
            for i in range(attempts):
                if self._reanchor(f"reanchor_after_L1:{reason}"):
                    action["resumed"] = True
                    action["attempt"] = i + 1
                    self.l1_streak = 0      # 纠偏成功,接续任务,无断点丢失
                    break
            if not action["resumed"]:
                return self.trigger_level2("重锚定校验未通过", {**detail, "origin": reason})
        self.logger.log("trace", "breaker_L1_action", action)
        return {"event": ev.to_dict(), "action": action}

    def trigger_level2(self, reason: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """二级熔断(限制级·强制管控): 暂停业务、锁工具权限、只读观测;
        恢复走人工确认或备用任务流程。"""
        ev = self._fire(BreakerLevel.L2_RESTRICT, reason, detail or {})
        self.l2_streak += 1
        self.l1_streak = 0

        # 升级判定: 连续 3 次二级熔断 → 三级
        if self.l2_streak >= CONFIG.level2_to_level3:
            return self.trigger_level3(f"连续{self.l2_streak}次二级熔断",
                                       {"origin": reason})

        action = {"paused": True, "tool_calls_locked": True,
                  "readonly_observation": True}
        if self._halt:
            try:
                self._halt()
                action["halt_executed"] = True
            except Exception as e:  # 挂起失败不阻断熔断状态机
                action["halt_error"] = str(e)
        self.logger.log("circuit", "breaker_L2_action", action)
        return {"event": ev.to_dict(), "action": action}

    def trigger_level3(self, reason: str, detail: Optional[Dict[str, Any]] = None,
                       fault_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """三级熔断(终止级·彻底止损): 终止进程、回收资源、生成标准化故障报告;
        恢复需人工排查后重启任务链路。"""
        ev = self._fire(BreakerLevel.L3_TERMINATE, reason, detail or {})
        action = {"terminated": True, "resources_reclaimed": True}
        if self._terminate:
            try:
                self._terminate()
                action["terminate_executed"] = True
            except Exception as e:
                action["terminate_error"] = str(e)
        report = self.build_fault_report(reason, fault_report)
        action["fault_report_id"] = report["report_id"]
        self.logger.log("circuit", "breaker_L3_action", action, level=40)
        return {"event": ev.to_dict(), "action": action, "fault_report": report}

    # ---- 资源严重溢出直达三级(恶意无效执行同理) ----
    def trigger_fatal(self, reason: str,
                      detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """资源严重溢出 / 恶意无效执行: 跳过 1/2 级,直接终止止损。"""
        return self.trigger_level3(f"致命异常: {reason}", detail)

    # ---- 标准化故障报告 ----
    def build_fault_report(self, reason: str,
                           extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rid = f"FR-{int(time.time()*1000)}"
        report = {
            "report_id": rid,
            "ts": time.time(),
            "final_level": int(self.level),
            "reason": reason,
            "breaker_event_history": [e.to_dict() for e in self.events],
            "l1_streak": self.l1_streak,
            "l2_streak": self.l2_streak,
            "extra": extra or {},
        }
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "drift_guard", "storage", "faults")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, f"{rid}.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report

    # ---- 自愈 ----
    def create_ticket(self, scene: str, detail: Dict[str, Any]) -> Optional[str]:
        """无法自愈 → 生成工单告警(标准 JSON 工单落盘 + 回调)。"""
        ticket = {"scene": scene, "detail": detail,
                  "ts": time.time(), "requires_human": True}
        tid = None
        if self._ticket:
            tid = self._ticket(ticket)
        else:
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "drift_guard", CONFIG.ticket_dir.lstrip("./"))
            os.makedirs(path, exist_ok=True)
            tid = f"TK-{int(time.time()*1000)}"
            with open(os.path.join(path, f"{tid}.json"), "w", encoding="utf-8") as f:
                json.dump({"ticket_id": tid, **ticket}, f, ensure_ascii=False, indent=2)
        METRICS.incr("tickets_created")
        self.logger.log("circuit", "ticket_created",
                        {"ticket_id": tid, "scene": scene}, level=30)
        return tid

    # ---- 状态 ----
    def human_confirm_reset(self, note: str = "") -> None:
        """人工排查修复后重启任务链路: 清零熔断状态(仅限 L2;L3 需新实例)。"""
        self.logger.log("circuit", "human_reset",
                        {"from_level": int(self.level), "note": note})
        if self.level < BreakerLevel.L3_TERMINATE:
            self.level = BreakerLevel.NONE
            self.l1_streak = self.l2_streak = 0

    def is_operational(self) -> bool:
        return self.level < BreakerLevel.L2_RESTRICT
