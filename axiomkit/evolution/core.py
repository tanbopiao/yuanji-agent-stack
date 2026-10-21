"""EvolutionKernel — guarded self-improvement loop for autonomous agents.

Lifecycle of one mutation::

    kernel.propose("doubt_threshold", 0.45)          # whitelist + bounds checked
    kernel.evaluate(m, evaluator=my_fn)              # shadow trial, no side effects
    kernel.promote(m) | kernel.rollback(m)           # by measured improvement

The frozen core (axioms / L0 root / protected params) is untouchable: any
attempt raises :class:`FrozenCoreError`. Promotion history is hash-chained so
the evolution path itself is auditable.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = ["EvolutionKernel", "Mutation", "TrialReport", "FrozenCoreError"]


class FrozenCoreError(PermissionError):
    """Raised when a mutation targets the protected/immutable subspace."""


class State(str, Enum):
    PROPOSED = "proposed"
    TRIALED = "trialed"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled-back"
    REJECTED = "rejected"


@dataclass
class Mutation:
    """A proposed self-modification of one tunable parameter."""

    param: str
    new_value: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: str = State.PROPOSED.value
    reason: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class TrialReport:
    """Shadow-trial outcome comparing baseline vs candidate behavior."""

    mutation_id: str
    samples: int
    baseline_score: float
    candidate_score: float
    improvement: float          # (cand - base) / max(base, eps)
    promoted: Optional[bool] = None
    detail: str = ""


class EvolutionKernel:
    """Controlled RSI loop with a whitelist, a frozen core, and grayscale trials.

    Parameters
    ----------
    params:
        Whitelist of evolvable parameters, each with bounds and current value,
        e.g. ``{"doubt_threshold": {"value": 0.4, "min": 0.1, "max": 0.9}}``.
    frozen:
        Names (or a predicate) declaring the immutable subspace; mutations
        targeting them raise :class:`FrozenCoreError`.
    min_improvement:
        Minimum relative improvement for a candidate to be promotable
        (default 0.05, i.e. +5%).
    min_samples:
        Minimum shadow samples before a trial verdict (default 20).
    """

    def __init__(
        self,
        params: Dict[str, Dict[str, float]],
        frozen: Optional[Sequence[str]] = None,
        min_improvement: float = 0.05,
        min_samples: int = 20,
    ):
        self._params: Dict[str, Dict[str, float]] = {
            name: {
                "value": float(spec["value"]),
                "min": float(spec.get("min", float("-inf"))),
                "max": float(spec.get("max", float("inf"))),
            }
            for name, spec in params.items()
        }
        self._frozen = set(frozen or ())
        self._min_improvement = min_improvement
        self._min_samples = min_samples
        self._mutations: Dict[str, Mutation] = {}
        self._history: List[Dict[str, Any]] = []
        self._head = "0" * 64

    # -- inspection -----------------------------------------------------------
    @property
    def params(self) -> Dict[str, float]:
        """Current live parameter values."""
        return {k: v["value"] for k, v in self._params.items()}

    @property
    def frozen_core(self) -> frozenset:
        return frozenset(self._frozen)

    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    # -- propose ---------------------------------------------------------------
    def propose(self, param: str, new_value: float,
                reason: str = "") -> Mutation:
        """Propose a self-modification. Whitelist + bounds + frozen checks apply."""
        if param in self._frozen:
            raise FrozenCoreError(
                f"{param!r} belongs to the frozen core; evolution is forbidden")
        if param not in self._params:
            raise KeyError(
                f"{param!r} is not in the evolvable whitelist {sorted(self._params)}")
        spec = self._params[param]
        if not spec["min"] <= new_value <= spec["max"]:
            raise ValueError(
                f"{param}={new_value} outside bounds [{spec['min']}, {spec['max']}]")
        m = Mutation(param=param, new_value=float(new_value), reason=reason)
        self._mutations[m.id] = m
        return m

    # -- shadow trial ------------------------------------------------------------
    def evaluate(
        self,
        mutation: Mutation,
        evaluator: Callable[[Dict[str, float]], Sequence[float]],
    ) -> TrialReport:
        """Run a shadow trial: ``evaluator(params)`` returns a score sample
        (e.g. per-task accuracy) for both baseline and candidate configs.
        The live params are NOT modified here."""
        if mutation.state != State.PROPOSED.value:
            raise ValueError(f"mutation {mutation.id} already in state {mutation.state}")
        base_params = self.params
        cand_params = dict(base_params)
        cand_params[mutation.param] = mutation.new_value

        base_scores = list(evaluator(base_params))
        cand_scores = list(evaluator(cand_params))
        if len(base_scores) < self._min_samples or len(cand_scores) < self._min_samples:
            report = TrialReport(
                mutation_id=mutation.id, samples=min(len(base_scores), len(cand_scores)),
                baseline_score=float("nan"), candidate_score=float("nan"),
                improvement=0.0, detail=f"insufficient samples (<{self._min_samples})")
            mutation.state = State.PROPOSED.value
            mutation.reason = report.detail
            return report

        base = statistics.fmean(base_scores)
        cand = statistics.fmean(cand_scores)
        improvement = (cand - base) / max(abs(base), 1e-12)
        report = TrialReport(
            mutation_id=mutation.id, samples=len(cand_scores),
            baseline_score=base, candidate_score=cand, improvement=improvement)
        mutation.state = State.TRIALED.value
        mutation.reason = report.detail = (
            f"improvement {improvement:+.2%} over {report.samples} samples")
        mutation.meta = {"report": report.__dict__} if hasattr(mutation, "meta") else None
        self._mutations[mutation.id] = mutation
        return report

    # -- promote / rollback -------------------------------------------------------
    def promote(self, mutation: Mutation,
                report: Optional[TrialReport] = None) -> Dict[str, Any]:
        """Promote a trialed mutation into the live config (grayscale gate passed)."""
        if mutation.state != State.TRIALED.value and report is None:
            raise ValueError("promote requires a trialed mutation or a report")
        rep = report or mutation.meta and mutation.meta.get("report")  # type: ignore[operator]
        if rep is None:
            raise ValueError("no trial report available for this mutation")
        if rep.improvement < self._min_improvement:
            return self.rollback(mutation, reason=(
                f"improvement {rep.improvement:+.2%} < required "
                f"{self._min_improvement:+.2%}"))
        spec = self._params[mutation.param]
        old = spec["value"]
        spec["value"] = mutation.new_value
        mutation.state = State.PROMOTED.value
        return self._record("promote", mutation, old=old, new=mutation.new_value,
                            improvement=rep.improvement)

    def rollback(self, mutation: Mutation,
                 reason: str = "manual rollback") -> Dict[str, Any]:
        """Discard a mutation; live config unchanged (already-verified safe path)."""
        mutation.state = State.ROLLED_BACK.value
        return self._record("rollback", mutation, reason=reason)

    # -- audit chain ---------------------------------------------------------------
    def _record(self, action: str, mutation: Mutation, **extra: Any) -> Dict[str, Any]:
        entry = {"action": action, "mutation_id": mutation.id,
                 "param": mutation.param, "ts": time.time(),
                 "head_prev": self._head, **extra}
        entry["hash"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()
        self._history.append(entry)
        self._head = entry["hash"]
        return entry
