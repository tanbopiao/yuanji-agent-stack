"""Meta-evolution tour — run: PYTHONPATH=. python examples/evolution_demo.py

Shows the guarded RSI loop: an agent proposes to retune its own doubt
threshold, a shadow trial measures the effect, and the kernel promotes or
rolls back automatically — with the axiom core frozen and every step
hash-chained for audit.
"""

from axiomkit.evolution import EvolutionKernel, FrozenCoreError

kernel = EvolutionKernel(
    params={
        "doubt_threshold": {"value": 0.4, "min": 0.1, "max": 0.9},
        "confidence_floor": {"value": 0.3, "min": 0.1, "max": 0.7},
    },
    frozen=("axiom_root", "core_weights"),   # L0-style immutable subspace
    min_samples=5,
)


def evaluator(params: dict):
    """Toy 'task suite': the agent performs best at doubt_threshold≈0.6."""
    base = 1.0 - abs(params["doubt_threshold"] - 0.6)
    return [base + (0.01 if i % 2 else -0.01) for i in range(10)]


# 1) The frozen core can never be touched
try:
    kernel.propose("axiom_root", 0.9)
except FrozenCoreError as exc:
    print("frozen core blocked:", exc)

# 2) Propose a self-modification
m = kernel.propose("doubt_threshold", 0.6, reason="shadow samples suggest optimum is higher")
print("proposed:", m.param, "->", m.new_value)

# 3) Shadow trial — live params untouched
rep = kernel.evaluate(m, evaluator)
print(f"trial: base={rep.baseline_score:.3f} cand={rep.candidate_score:.3f} "
      f"improvement={rep.improvement:+.2%}")

# 4) Grayscale gate: promote on real improvement, auto-rollback otherwise
entry = kernel.promote(m, rep)
print("verdict:", entry["action"], "| live params:", kernel.params)

# 5) Auditable evolution path
for h in kernel.history():
    print(f"  {h['action']:8s} {h['param']} hash={h['hash'][:12]}…")
