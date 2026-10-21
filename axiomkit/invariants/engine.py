"""Invariant engine: propose -> verify -> arbitrate -> append-only locked ledger.

The ground rule set is append-only and hash-locked: approved invariants are
chained by SHA-256 so any tampering is detectable. Without ``z3-solver``
installed, :meth:`InvariantPipeline.verify` falls back to a structural check
and marks the candidate ``manual-review``.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["InvariantPipeline", "Candidate", "VerificationReport", "LedgerError"]


class LedgerError(RuntimeError):
    """Raised on attempts to mutate the locked (append-only) invariant set."""


class Status(str, Enum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    REFUTED = "refuted"
    MANUAL_REVIEW = "manual-review"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class Candidate:
    """A proposed invariant awaiting verification and arbitration."""

    layer: str
    category: str
    statement: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = Status.PROPOSED.value
    created_at: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationReport:
    candidate_id: str
    status: str
    engine: str          # "z3" | "structural"
    detail: str = ""


def _z3_available() -> bool:
    try:
        import z3  # noqa: F401
        return True
    except ImportError:
        return False


class InvariantPipeline:
    """Manage the full lifecycle of invariants guarding an AI agent.

    Parameters
    ----------
    ledger_path:
        Optional path; when set the locked invariant set is persisted as a
        hash-chained JSONL ledger that survives restarts.
    """

    def __init__(self, ledger_path: Optional[str | Path] = None):
        self._candidates: Dict[str, Candidate] = {}
        self._locked: List[Dict[str, Any]] = []
        self._head_hash = "0" * 64
        self._ledger_path = Path(ledger_path) if ledger_path else None
        if self._ledger_path and self._ledger_path.exists():
            self._load_ledger()

    # -- propose ------------------------------------------------------------
    def propose(self, layer: str, category: str, statement: str,
                **meta: Any) -> Candidate:
        cand = Candidate(layer=layer, category=category,
                         statement=statement, meta=dict(meta))
        self._candidates[cand.id] = cand
        return cand

    # -- verify -------------------------------------------------------------
    def verify(self, cand: Candidate) -> VerificationReport:
        if _z3_available():
            status, detail = self._verify_z3(cand)
            engine = "z3"
        else:
            status, detail = self._verify_structural(cand)
            engine = "structural"
        cand.status = status
        report = VerificationReport(candidate_id=cand.id, status=status,
                                    engine=engine, detail=detail)
        cand.meta.setdefault("verification", []).append(asdict(report))
        return report

    @staticmethod
    def _verify_structural(cand: Candidate) -> tuple[str, str]:
        """Fallback check: statement must be non-trivial and well-scoped."""
        s = cand.statement.strip()
        if len(s) < 8:
            return Status.REFUTED.value, "statement too short to be an invariant"
        if not cand.layer or not cand.category:
            return Status.REFUTED.value, "missing layer or category"
        return (Status.MANUAL_REVIEW.value,
                "no z3-solver installed; structural check passed, needs human/z3 review")

    @staticmethod
    def _verify_z3(cand: Candidate) -> tuple[str, str]:
        """Lightweight Z3 check: the statement must be satisfiable as a
        consistency constraint (no immediate contradiction)."""
        try:
            import z3
            goal = z3.Goal()
            goal.add(z3.Const(cand.id, z3.BoolSort()))
            # Contradiction probe: if the statement itself encodes False the
            # candidate is refuted. Full SMT encodings are supplied by the
            # caller via ``meta["z3_expr"]``.
            expr = cand.meta.get("z3_expr")
            if expr is None:
                return (Status.MANUAL_REVIEW.value,
                        "z3 available but no z3_expr encoding supplied")
            s = z3.Solver()
            s.set("timeout", 5000)
            s.add(z3.parse_smt2_string(expr))
            if s.check() == z3.unsat:
                return Status.REFUTED.value, "encoding is unsatisfiable"
            return Status.VERIFIED.value, f"encoding is {s.check()}"
        except Exception as exc:  # malformed encoding etc.
            return Status.MANUAL_REVIEW.value, f"z3 error: {exc}"

    # -- arbitrate ----------------------------------------------------------
    def arbitrate(self, candidate_id: str, decision: str,
                  arbiter: str) -> Candidate:
        """Only an explicit arbiter decision moves a candidate into the locked set."""
        cand = self._candidates.get(candidate_id)
        if cand is None:
            raise KeyError(f"unknown candidate {candidate_id}")
        if decision == "approve":
            if cand.status not in (Status.VERIFIED.value, Status.MANUAL_REVIEW.value):
                raise LedgerError(
                    f"cannot approve candidate in status {cand.status!r}; "
                    "verify it first")
            cand.status = Status.APPROVED.value
            self._append_locked(cand, arbiter)
        elif decision == "reject":
            cand.status = Status.REJECTED.value
        else:
            raise ValueError("decision must be 'approve' or 'reject'")
        return cand

    # -- locked ledger ------------------------------------------------------
    def _append_locked(self, cand: Candidate, arbiter: str) -> None:
        entry = {
            "id": cand.id,
            "layer": cand.layer,
            "category": cand.category,
            "statement": cand.statement,
            "arbiter": arbiter,
            "approved_at": time.time(),
            "prev_hash": self._head_hash,
        }
        entry["hash"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        self._locked.append(entry)
        self._head_hash = entry["hash"]
        if self._ledger_path:
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self._ledger_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _load_ledger(self) -> None:
        prev = "0" * 64
        with self._ledger_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("prev_hash") != prev:
                    raise LedgerError("ledger chain broken — tampering detected")
                digest = {k: v for k, v in entry.items() if k != "hash"}
                expected = hashlib.sha256(
                    json.dumps(digest, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                if expected != entry.get("hash"):
                    raise LedgerError("ledger entry hash mismatch")
                self._locked.append(entry)
                prev = entry["hash"]
        self._head_hash = prev

    def locked_snapshot(self) -> Dict[str, Any]:
        return {"count": len(self._locked), "head_hash": self._head_hash,
                "invariants": list(self._locked)}

    def candidates(self) -> List[Candidate]:
        return list(self._candidates.values())
