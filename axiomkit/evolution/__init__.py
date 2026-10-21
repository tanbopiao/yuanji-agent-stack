"""Controlled recursive self-improvement (RSI) for AI agents.

An agent may propose changes to its own tunable parameters, but every change
passes through a guarded loop:

    propose -> verify (invariants) -> shadow/grayscale trial -> promote or rollback

Core guarantees (distilled from the ZONGYUAN-ROOT L5 RSI workbench):
- **Whitelist**: only declared tunable parameters can be mutated, within
  explicit min/max bounds.
- **Frozen core**: a protected subspace (axioms, L0 root, core weights) can
  never be touched by evolution.
- **Grayscale**: a candidate runs on a fraction of traffic first; it is
  promoted only on measured improvement, otherwise rolled back automatically.
"""

from .core import (
    EvolutionKernel,
    Mutation,
    FrozenCoreError,
    TrialReport,
    State,
)

__all__ = ["EvolutionKernel", "Mutation", "FrozenCoreError", "TrialReport", "State"]
