"""semantic.py — 语义漂移监测算子

职责:
    1. 默认接入 BGE-m3 嵌入模型,实现文本向量语义距离计算
    2. 固定漂移阈值: 语义距离差值 > 0.3 判定为有效意图漂移
    3. 实时记录每一步推理向量,生成推理语义轨迹链
    4. 推理步数管控: 单任务最大推理步数上限 20 步,超限触发预警
    5. 工具调用参数强校验: 入参/出参必须在目标约束范围内,越界直接拦截

嵌入模型策略(最小资源消耗):
    - 优先加载 FlagEmbedding/transformers 的 BGE-m3(YAML 可配置本地路径)
    - 模型不可用时自动降级到内置轻量语义近似(词面+字符 n-gram 余弦),
      降级事件写日志;保证防控链路在任何环境都不中断。
"""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG
from .observability import METRICS, TraceLogger

import numpy as np


# ---- 嵌入后端 -----------------------------------------------------------

class _FallbackEmbedder:
    """降级嵌入器: 字符 n-gram + 词袋余弦,零依赖,保持漂移检测可用。"""

    name = "fallback-ngram"

    def encode(self, texts: List[str]) -> "np.ndarray":
        vecs = []
        for t in texts:
            tokens = re.findall(r"[\w\u4e00-\u9fff]+", t.lower())
            grams = list(tokens)
            for tok in tokens:
                if len(tok) > 2:
                    grams += [tok[i:i + 3] for i in range(len(tok) - 2)]
            c = Counter(grams)
            dim = 512
            v = np.zeros(dim, dtype=np.float32)
            for g, n in c.items():
                v[hash(g) % dim] += float(n)
            norm = float(np.linalg.norm(v)) or 1.0
            vecs.append(v / norm)
        return np.vstack(vecs)


class SemanticBackend:
    """BGE-m3 嵌入后端(懒加载 + 失败降级),线程安全。"""

    def __init__(self, model_name: Optional[str] = None, device: Optional[str] = None,
                 logger: Optional[TraceLogger] = None) -> None:
        self.model_name = model_name or CONFIG.embedding_model
        self.device = device or CONFIG.embedding_device
        self.logger = logger or TraceLogger("semantic")
        self._model: Any = None
        self._lock = threading.Lock()
        self.degraded = False

    def _load(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            if self.degraded:
                return None
            try:
                from FlagEmbedding import BGEM3FlagModel  # type: ignore
                self._model = BGEM3FlagModel(
                    self.model_name, use_fp16=(self.device != "cpu"))
                return self._model
            except Exception:
                try:
                    from sentence_transformers import SentenceTransformer  # type: ignore
                    self._model = SentenceTransformer(self.model_name, device=self.device)
                    return self._model
                except Exception as e:
                    self.degraded = True
                    self.logger.log("trace", "embedder_degraded",
                                    {"reason": str(e), "fallback": _FallbackEmbedder.name},
                                    level=logging.WARNING)
                    return None

    def encode(self, texts: List[str]) -> "np.ndarray":
        model = self._load()
        if model is None:
            return _FallbackEmbedder().encode(texts)
        try:
            if hasattr(model, "encode"):  # SentenceTransformer
                out = model.encode(texts, normalize_embeddings=True)
                return np.asarray(out, dtype=np.float32)
            out = model.encode(texts, return_dense=True)["dense_vecs"]  # BGEM3
            return np.asarray(out, dtype=np.float32)
        except Exception as e:
            self.logger.log("trace", "embedder_encode_error",
                            {"reason": str(e)}, level=logging.ERROR)
            return _FallbackEmbedder().encode(texts)


def cosine_distance(a: "np.ndarray", b: "np.ndarray") -> float:
    """余弦距离 = 1 - cos(a,b), ∈ [0,2];归一化向量下等价语义距离。"""
    na, nb = float(np.linalg.norm(a)) or 1.0, float(np.linalg.norm(b)) or 1.0
    return 1.0 - float(np.dot(a, b) / (na * nb))


# ---- 算子主体 ------------------------------------------------------------

class SemanticDriftOperator:
    """语义漂移监测: 语义距离、漂移判定、轨迹链、步数管控、参数越界拦截。"""

    def __init__(self, anchor: Any, backend: Optional[SemanticBackend] = None,
                 logger: Optional[TraceLogger] = None) -> None:
        self.anchor = anchor                     # AnchorOperator 实例(真值基准)
        self.backend = backend or SemanticBackend(logger=logger)
        self.logger = logger or TraceLogger("semantic")
        self._traj_lock = threading.Lock()
        self._trajectory: List[Dict[str, Any]] = []   # 推理语义轨迹链
        self._baseline_vec: Optional["np.ndarray"] = None
        self.step = 0

    # ---- 基准向量 ----
    def prime(self) -> None:
        """任务启动时缓存初始目标的基准向量。"""
        goal = self.anchor.goal
        self._baseline_vec = self.backend.encode([goal.core_target])[0]
        self.logger.log("trace", "semantic_primed", {"goal_id": goal.goal_id})

    # ---- 语义距离与漂移判定 ----
    def check_step(self, reasoning_text: str) -> Dict[str, Any]:
        """每步推理后调用: 计算与目标的语义距离,记录轨迹,判定漂移。

        返回 {step, similarity, distance, drifted, step_overflow}。
        drifted=True 或 step_overflow=True → 一级熔断触发条件。
        """
        if self._baseline_vec is None:
            self.prime()
        vec = self.backend.encode([reasoning_text])[0]
        distance = cosine_distance(vec, self._baseline_vec)  # type: ignore[arg-type]
        similarity = 1.0 - distance

        self.step += 1
        record = {"step": self.step, "distance": round(distance, 4),
                  "similarity": round(similarity, 4)}
        with self._traj_lock:
            self._trajectory.append(record)

        threshold = CONFIG.drift_threshold
        max_steps = CONFIG.max_reasoning_steps
        drifted = distance > threshold
        overflow = self.step > max_steps

        METRICS.record_similarity("semantic", similarity, self.step)
        METRICS.record_steps("semantic", self.step)
        self.logger.log(
            "trace" if not (drifted or overflow) else "threshold",
            "semantic_step_check",
            {**record, "drift_threshold": threshold,
             "max_reasoning_steps": max_steps, "drifted": drifted,
             "step_overflow": overflow},
            level=logging.INFO if not (drifted or overflow) else logging.WARNING)
        if drifted:
            METRICS.incr("semantic_drift_detected")
        if overflow:
            METRICS.incr("step_overflow_warnings")
        return {"step": self.step, "similarity": similarity,
                "distance": distance, "drifted": drifted, "step_overflow": overflow}

    # ---- 轨迹链 ----
    def trajectory(self) -> List[Dict[str, Any]]:
        with self._traj_lock:
            return list(self._trajectory)

    # ---- 工具调用参数强校验 ----
    def verify_tool_io(self, tool_name: str, inputs: Dict[str, Any],
                       outputs: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        """入参/出参必须在目标约束范围内,越界直接拦截。

        校验逻辑:
            1. 语义校验: 工具名+入参文本 与目标/约束 的相似度不得低于对齐阈值
            2. 约束词校验: 入参命中约束条件中的禁止词 → 直接拦截
        返回 (allowed, detail);拦截时 detail.reason 说明越界依据。
        """
        goal = self.anchor.goal
        text = f"{tool_name} " + " ".join(str(v) for v in inputs.values())
        out_text = ""
        if outputs:
            out_text = " ".join(str(v) for v in outputs.values())

        vec = self.backend.encode([text])[0]
        distance = cosine_distance(vec, self._baseline_vec) if self._baseline_vec is not None else 0.0
        sim = 1.0 - distance

        forbidden_hits = []
        joined = f"{text} {out_text}"
        for cond in goal.constraint_conditions:
            for kw in _extract_forbidden_terms(cond):
                if kw and kw in joined:
                    forbidden_hits.append({"constraint": cond, "term": kw})

        allowed = (forbidden_hits == []) and sim >= CONFIG.alignment_threshold
        detail = {"tool": tool_name, "similarity": round(sim, 4),
                  "forbidden_hits": forbidden_hits,
                  "reason": None if allowed else
                  ("命中禁止约束" if forbidden_hits else
                   f"与目标相似度 {sim:.2f} 低于对齐阈值 {CONFIG.alignment_threshold}")}
        self.logger.log("trace" if allowed else "threshold",
                        "tool_io_check", detail,
                        level=logging.INFO if allowed else logging.WARNING)
        if not allowed:
            METRICS.incr("tool_io_intercepted")
        return allowed, detail


def _extract_forbidden_terms(constraint: str) -> List[str]:
    """从约束条件中提取禁止对象词表(支持 '禁止/不得/不能 X' 句式)。"""
    terms: List[str] = []
    for m in re.finditer(r"(?:禁止|不得|不能|不要|避免)\s*([\w\u4e00-\u9fff,，、 ]+)",
                         constraint):
        for piece in re.split(r"[,，、 ]", m.group(1)):
            piece = piece.strip()
            if piece:
                terms.append(piece)
    return terms
