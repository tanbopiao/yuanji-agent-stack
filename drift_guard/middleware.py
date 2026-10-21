"""middleware.py — 低侵入挂载器(插件/中间件形式)

职责: 监控模块与业务 Agent 完全隔离,以中间件形式挂载运行,
不改动原有 Agent 核心业务代码;支持一键挂载、一键卸载、独立启停。

使用方式(业务侧零侵入):

    from drift_guard import mount

    guard = mount(task="把销售额报表整理成周报发给运营组")   # 一行挂载

    # 业务推理循环中:
    verdict = guard.before_step("正在检索报表数据")      # 每轮推理前强制校验
    if not verdict["allowed"]:
        ...   # 按 verdict["action"] 处理(纠偏/暂停/终止)

    guard.after_tool("search", {"query": "Q3 销售额"}, tokens=820)  # 工具调用后上报

    guard.unmount()   # 一键卸载,业务链路回到原状

装配关系: Anchor → Semantic → Behavior → Coordination → CircuitBreaker,
全部回调注入,DriftGuard 不持有业务对象,卸载即解除引用。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from .anchor import AnchorOperator
from .behavior import BehaviorRiskOperator
from .circuit_breaker import BreakerLevel, CircuitBreakerOperator
from .config import CONFIG
from .coordination import CoordinationOperator
from .observability import METRICS, TraceLogger
from .semantic import SemanticBackend, SemanticDriftOperator, cosine_distance


def _backend_similarity(backend: SemanticBackend) -> Callable[[str, str], float]:
    """基于语义后端的 (a,b)->similarity 函数(供锚定对齐与协同校验复用)。"""
    def sim(a: str, b: str) -> float:
        import numpy as np
        va, vb = backend.encode([a, b])
        return 1.0 - cosine_distance(va, vb)
    return sim


class DriftGuardMiddleware:
    """低侵入防控中间件: 装配五大算子,暴露 before_step / after_tool 钩子。"""

    def __init__(self, task: str, task_id: Optional[str] = None,
                 context_budget_tokens: int = 128000,
                 halt_fn: Optional[Callable[[], None]] = None,
                 terminate_fn: Optional[Callable[[], None]] = None,
                 ticket_fn: Optional[Callable[[Dict[str, Any]], str]] = None) -> None:
        self.task_id = task_id or f"T{int(time.time()*1000)}"
        self.logger = TraceLogger(self.task_id)
        self.mounted = False

        # 1. 目标结构化锚定(核心底座)
        self.anchor = AnchorOperator(logger=self.logger)
        self.goal = self.anchor.anchor(task)

        # 2. 语义漂移监测
        self.backend = SemanticBackend(logger=self.logger)
        self.similarity = _backend_similarity(self.backend)
        self.semantic = SemanticDriftOperator(self.anchor, self.backend,
                                              logger=self.logger)
        self.semantic.prime()

        # 3. 行为模式风控
        self.behavior = BehaviorRiskOperator(logger=self.logger,
                                             context_budget_tokens=context_budget_tokens)
        self.behavior.set_goal_summary_provider(
            lambda: self.anchor.goal.core_target)

        # 5. 三级熔断调度(先建,供协同校验联动)
        self.breaker = CircuitBreakerOperator(
            logger=self.logger,
            reanchor_fn=self._do_reanchor,
            halt_fn=halt_fn,
            terminate_fn=terminate_fn,
            ticket_fn=ticket_fn)

        # 4. 跨Agent协同校验
        self.coordination = CoordinationOperator(self.similarity, self.anchor,
                                                 logger=self.logger)
        self._halted = False
        self.mounted = True
        self.logger.log("trace", "guard_mounted",
                        {"goal_id": self.goal.goal_id,
                         "core_target": self.goal.core_target})
        METRICS.incr("guards_mounted")

    # ---- 推理循环钩子 -----------------------------------------------------
    def before_step(self, reasoning_text: str) -> Dict[str, Any]:
        """每轮推理/每次工具调用前强制校验(目标对齐 + 语义漂移 + 熔断门禁)。

        返回:
            allowed  — False 时业务必须停步
            action   — none | re_anchor | pause | terminate
            reason   — 触发依据(面板/日志可读)
        """
        if not self.mounted or not self.breaker.is_operational():
            return {"allowed": False, "action": "pause" if self._halted else "terminate",
                    "reason": f"熔断状态 L{int(self.breaker.level)}"}

        # (a) 目标相似度对齐校验(< 0.75 → 一级预警)
        aligned, sim = self.anchor.verify_alignment(reasoning_text, self.similarity)

        # (b) 语义漂移判定(> 0.3 → 漂移)与步数管控(> 20 → 预警)
        sem = self.semantic.check_step(reasoning_text)

        if not aligned:
            r = self.breaker.trigger_level1(
                "目标对齐校验低于0.75", {"similarity": round(sim, 4)})
            return self._verdict(r, f"目标相似度 {sim:.2f} < {CONFIG.alignment_threshold}")
        if sem["drifted"]:
            r = self.breaker.trigger_level1(
                "语义漂移超过0.3", {"distance": round(sem["distance"], 4),
                                   "step": sem["step"]})
            return self._verdict(r, f"语义距离 {sem['distance']:.2f} > {CONFIG.drift_threshold}")
        if sem["step_overflow"]:
            r = self.breaker.trigger_level1(
                "推理步数超过上限20", {"step": sem["step"]})
            return self._verdict(r, f"推理步数 {sem['step']} 超过 {CONFIG.max_reasoning_steps}")

        return {"allowed": True, "action": "none", "reason": "ok"}

    def after_tool(self, tool_name: str, params: Dict[str, Any],
                   outputs: Optional[Dict[str, Any]] = None,
                   tokens: int = 0, action_fingerprint: Optional[str] = None) -> Dict[str, Any]:
        """工具调用后上报: 参数强校验 + 死循环检测 + 资源监控 + 上下文饱和度。

        返回 {allowed, action, reason};False 时业务必须停步。
        """
        if not self.mounted or not self.breaker.is_operational():
            return {"allowed": False, "action": "pause",
                    "reason": f"熔断状态 L{int(self.breaker.level)}"}

        # (a) 工具入参/出参强校验(越界直接拦截)
        io_ok, io_detail = self.semantic.verify_tool_io(tool_name, params, outputs)
        if not io_ok:
            r = self.breaker.trigger_level1("工具调用参数越界", io_detail)
            return self._verdict(r, io_detail["reason"])

        # (b) 死循环检测(连续 3 次重复动作 → 二级熔断)
        if action_fingerprint:
            loop = self.behavior.record_action(action_fingerprint)
            if loop["loop_detected"]:
                r = self.breaker.trigger_level2(
                    "死循环: 连续3次重复动作", loop)
                return self._verdict(r, "检测到无效循环(二级熔断)")

        # (c) 资源监控
        res = self.behavior.report_usage(tokens_delta=tokens, api_calls_delta=1)
        if res["exhausted"]:
            r = self.breaker.trigger_fatal("资源严重溢出", res["usage"])
            return self._verdict(r, "资源超限(三级熔断)")
        if res["nearing_limit"]:
            r = self.breaker.trigger_level1("资源临近上限", res["ratios"])
            return self._verdict(r, "资源临近上限(一级预警)")

        return {"allowed": True, "action": "none", "reason": "ok"}

    def report_context(self, context_tokens: int) -> Dict[str, Any]:
        """上下文饱和度上报(业务侧每轮上报当前上下文长度)。"""
        ctx = self.behavior.report_context(context_tokens)
        if ctx["must_reanchor"]:
            plan = self.behavior.compact_context(
                keep_recent_steps=3,
                carry_items=self.anchor.goal.constraint_conditions)
            r = self.breaker.trigger_level1(
                "上下文饱和度≥90%", {"saturation": ctx["saturation"],
                                    "compact_plan": plan})
            return self._verdict(r, f"上下文占用 {ctx['saturation']:.0%},已强制重锚定")
        return {"allowed": True, "action": "none", "reason": "ok"}

    # ---- 跨Agent钩子 -------------------------------------------------------
    def dispatch_child(self, child_agent: str, subtask_brief: str = "") -> Dict[str, Any]:
        """父Agent下发子任务: 完整携带结构化目标 JSON + 全部约束规则。"""
        return self.coordination.dispatch_child_task(child_agent,
                                                     subtask_brief=subtask_brief)

    def verify_child(self, child_agent: str, output_text: str,
                     payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """子Agent输出对齐校验(< 0.6 直接拦截并告警)。"""
        ok, sim = self.coordination.verify_child_output(child_agent, output_text, payload)
        if not ok:
            self.breaker.trigger_level2(
                "子Agent严重偏离目标",
                {"child": child_agent, "similarity": round(sim, 4),
                 "threshold": CONFIG.child_alignment_threshold})
        return {"allowed": ok, "similarity": round(sim, 4),
                "action": "none" if ok else "pause"}

    # ---- 内部 --------------------------------------------------------------
    def _do_reanchor(self, note: str) -> bool:
        """重锚定指令: 强制重新对齐初始任务,校验通过则自动接续。"""
        goal = self.anchor.goal
        ok, _ = self.anchor.verify_alignment(goal.core_target, self.similarity)
        self.logger.log("trace", "reanchor_executed",
                        {"note": note, "passed": ok})
        return ok

    def _verdict(self, breaker_result: Dict[str, Any], reason: str) -> Dict[str, Any]:
        level = breaker_result["event"]["level"]
        action = {1: "re_anchor", 2: "pause", 3: "terminate"}.get(level, "none")
        if level >= 2:
            self._halted = True
        if level == 2:
            tid = self.breaker.create_ticket("L2_restrict", {"reason": reason})
            if tid:
                breaker_result["action"]["ticket_id"] = tid
        return {"allowed": False, "action": action, "reason": reason,
                "breaker_level": level, "result": breaker_result}

    # ---- 一键卸载 / 独立启停 ------------------------------------------------
    def unmount(self) -> None:
        """一键卸载: 解除算子引用与业务回调,业务链路回到原状。"""
        self.mounted = False
        self.logger.log("trace", "guard_unmounted", {"task_id": self.task_id})
        METRICS.incr("guards_unmounted")

    def pause(self) -> None:
        """独立停止(观测): 防控继续留痕,校验一律放行到暂停态。"""
        self._halted = True
        self.logger.log("trace", "guard_paused", {})

    def resume(self) -> None:
        self._halted = False
        self.breaker.human_confirm_reset("middleware_resume")
        self.logger.log("trace", "guard_resumed", {})

    # ---- 面板快照 ----
    def snapshot(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal.to_dict(),
            "mounted": self.mounted,
            "breaker_level": int(self.breaker.level),
            "semantic_trajectory": self.semantic.trajectory(),
            "behavior": self.behavior.snapshot(),
            "coordination": self.coordination.deviation_report(),
            "metrics": METRICS.export(),
        }


def mount(task: str, **kwargs: Any) -> DriftGuardMiddleware:
    """一键挂载入口。"""
    return DriftGuardMiddleware(task, **kwargs)


def unmount(guard: DriftGuardMiddleware) -> None:
    """一键卸载入口。"""
    guard.unmount()
