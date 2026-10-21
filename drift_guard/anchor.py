"""anchor.py — 目标结构化锚定算子(核心底座)

职责:
    1. 将用户自然语言任务解析为标准 JSON 结构化目标文档
       固定字段: core_target / constraint_conditions / success_criteria / deadline
    2. 结构化目标写入只读独立存储,运行全程禁止修改、覆盖、刷新、遗忘
    3. 每轮推理、每次工具调用前强制执行目标相似度对齐校验
       固定阈值: 语义相似度 < 0.75 即刻触发一级预警

只读锁档实现: 目标 JSON 落盘后立即置文件只读权限(0o444),
Anchor 常驻内存副本对运行期只暴露只读视图,任何写接口都不存在。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG
from .observability import METRICS, TraceLogger

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "drift_guard", "storage", "anchors")
os.makedirs(STORAGE_DIR, exist_ok=True)

# 结构化目标固定字段(真值唯一,不新增不删减)
REQUIRED_FIELDS = ("core_target", "constraint_conditions",
                   "success_criteria", "deadline")


@dataclass(frozen=True)
class AnchoredGoal:
    """结构化目标文档(不可变,frozen=True 从类型层面杜绝运行期篡改)。"""
    core_target: str
    constraint_conditions: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    deadline: Optional[str] = None
    goal_id: str = ""
    created_ts: float = 0.0
    source_text_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "core_target": self.core_target,
            "constraint_conditions": list(self.constraint_conditions),
            "success_criteria": list(self.success_criteria),
            "deadline": self.deadline,
            "goal_id": self.goal_id,
            "created_ts": self.created_ts,
            "source_text_hash": self.source_text_hash,
        }


class ReadOnlyGoalStore:
    """只读独立存储: 写入一次后文件置 0o444,全程禁止修改/覆盖/刷新/遗忘。"""

    def __init__(self, store_dir: str = STORAGE_DIR) -> None:
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

    def _path(self, goal_id: str) -> str:
        return os.path.join(self.store_dir, f"{goal_id}.goal.json")

    def lock(self, goal: AnchoredGoal) -> str:
        """首次写入并锁档(只读);已存在同 ID 文件时校验内容一致,拒绝覆盖。"""
        path = self._path(goal.goal_id)
        payload = json.dumps(goal.to_dict(), ensure_ascii=False, indent=2)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                if f.read() != payload:
                    raise PermissionError(
                        f"基准目标 {goal.goal_id} 已锁档且内容不一致,禁止覆盖")
            return path
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(path, 0o444)  # 只读锁档
        return path

    def load(self, goal_id: str) -> Optional[AnchoredGoal]:
        path = self._path(goal_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AnchoredGoal(
            core_target=data["core_target"],
            constraint_conditions=data.get("constraint_conditions", []),
            success_criteria=data.get("success_criteria", []),
            deadline=data.get("deadline"),
            goal_id=data.get("goal_id", goal_id),
            created_ts=data.get("created_ts", 0.0),
            source_text_hash=data.get("source_text_hash", ""),
        )


# ---- 自然语言 → 结构化目标解析(零外部模型依赖的规则解析器) ----

_FIELD_PATTERNS = {
    "constraint_conditions": (r"(?:约束|限制|前提|不得|禁止|必须遵守)[::]?(?P<body>.*)",),
    "success_criteria": (r"(?:成功标准|验收标准|完成标志|交付标准)[::]?(?P<body>.*)",),
    "deadline": (r"(?:期限|截止|deadline|超时)[::]?(?P<body>.*)",),
}

_SPLIT = re.compile(r"[;;\n]")


def parse_goal(text: str) -> AnchoredGoal:
    """规则解析器: 从自然语言任务提取四固定字段,生成结构化目标文档。

    解析不到显式标注时,core_target 取首句,其余字段给安全缺省。
    """
    constraints: List[str] = []
    criteria: List[str] = []
    deadline: Optional[str] = None
    core_parts: List[str] = []

    for raw in _SPLIT.split(text):
        seg = raw.strip()
        if not seg:
            continue
        matched = False
        for fname, patterns in _FIELD_PATTERNS.items():
            for pat in patterns:
                m = re.search(pat, seg, flags=re.IGNORECASE)
                if m:
                    body = m.group("body").strip()
                    if fname == "constraint_conditions" and body:
                        constraints.append(body)
                    elif fname == "success_criteria" and body:
                        criteria.append(body)
                    elif fname == "deadline" and body:
                        deadline = body
                    matched = True
                    break
            if matched:
                break
        if not matched:
            core_parts.append(seg)

    core_target = core_parts[0] if core_parts else text.strip()
    if not criteria:
        criteria = [f"完成: {core_target}"]
    now = __import__("time").time()
    gid = hashlib.sha256(f"{core_target}|{now}".encode("utf-8")).hexdigest()[:16]
    return AnchoredGoal(
        core_target=core_target,
        constraint_conditions=constraints,
        success_criteria=criteria,
        deadline=deadline,
        goal_id=gid,
        created_ts=now,
        source_text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


class AnchorOperator:
    """目标结构化锚定算子: 锚定 → 锁档 → 每步强制对齐校验。"""

    def __init__(self, store: Optional[ReadOnlyGoalStore] = None,
                 logger: Optional[TraceLogger] = None) -> None:
        self.store = store or ReadOnlyGoalStore()
        self.logger = logger or TraceLogger("anchor")
        self._goal: Optional[AnchoredGoal] = None
        self._lock = threading.Lock()

    # ---- 锚定 ----
    def anchor(self, task_text: str) -> AnchoredGoal:
        """任务启动时唯一入口: 解析结构化目标并只读锁档。"""
        goal = parse_goal(task_text)
        self.store.lock(goal)
        with self._lock:
            self._goal = goal
        self.logger.log("trace", "goal_anchored",
                        {"goal_id": goal.goal_id, "core_target": goal.core_target,
                         "constraints": len(goal.constraint_conditions)})
        METRICS.incr("goals_anchored")
        return goal

    @property
    def goal(self) -> AnchoredGoal:
        if self._goal is None:
            raise RuntimeError("目标尚未锚定: 必须先调用 anchor() 再运行任务")
        return self._goal

    # ---- 对齐校验(每轮推理/每次工具调用前强制执行) ----
    def verify_alignment(self, current_text: str,
                         similarity_fn: Optional[Any] = None) -> Tuple[bool, float]:
        """目标相似度对齐校验。

        similarity_fn: 可注入的相似度计算函数(a,b)->float ∈[0,1];
        缺省用内置轻量词面相似度(生产建议注入 semantic 模块的 BGE-m3 余弦)。
        返回 (passed, similarity); 相似度 < 0.75 → 一级预警告警。
        """
        goal = self.goal
        if similarity_fn is None:
            similarity_fn = _lexical_similarity
        sim = float(similarity_fn(current_text, goal.core_target))
        threshold = CONFIG.alignment_threshold
        METRICS.record_similarity("anchor", sim, step=0)
        passed = sim >= threshold
        self.logger.log(
            "trace" if passed else "threshold", "goal_alignment_check",
            {"goal_id": goal.goal_id, "similarity": round(sim, 4),
             "threshold": threshold, "passed": passed},
            level=logging.INFO if passed else logging.WARNING)
        if not passed:
            METRICS.incr("alignment_warnings")
        return passed, sim

    def verify_tool_call(self, tool_name: str, params: Dict[str, Any],
                         similarity_fn: Optional[Any] = None) -> Tuple[bool, float]:
        """工具调用前强制对齐校验: 工具意图描述与目标比对。"""
        desc = f"{tool_name} {' '.join(str(v) for v in params.values())}"
        return self.verify_alignment(desc, similarity_fn)


# ---- 内置轻量相似度(词面 Jaccard;生产注入 BGE-m3 语义相似度) ----

def _lexical_similarity(a: str, b: str) -> float:
    ta = set(re.findall(r"[\w\u4e00-\u9fff]+", a.lower()))
    tb = set(re.findall(r"[\w\u4e00-\u9fff]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
