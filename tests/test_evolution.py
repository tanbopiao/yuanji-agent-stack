"""Tests for axiomkit.evolution — controlled RSI loop."""

import statistics
import unittest

from axiomkit.evolution import (
    EvolutionKernel,
    FrozenCoreError,
    State,
)


def make_kernel(**overrides):
    spec = {
        "params": {
            "doubt_threshold": {"value": 0.4, "min": 0.1, "max": 0.9},
            "confidence_floor": {"value": 0.3, "min": 0.1, "max": 0.7},
        },
        "frozen": ("axiom_root", "core_weights"),
        "min_samples": 5,
    }
    spec.update(overrides)
    return EvolutionKernel(**spec)


def improving_evaluator(good_value: float):
    """Scores are higher when doubt_threshold is close to good_value."""
    def evaluate(params: dict):
        base = 1.0 - abs(params["doubt_threshold"] - good_value)
        # deterministic pseudo-samples around the mean
        return [base + (0.01 if i % 2 else -0.01) for i in range(10)]
    return evaluate


class TestPropose(unittest.TestCase):
    def test_whitelist_enforced(self):
        k = make_kernel()
        with self.assertRaises(KeyError):
            k.propose("unknown_param", 0.5)

    def test_bounds_enforced(self):
        k = make_kernel()
        with self.assertRaises(ValueError):
            k.propose("doubt_threshold", 5.0)

    def test_frozen_core_untouchable(self):
        k = make_kernel()
        with self.assertRaises(FrozenCoreError):
            k.propose("axiom_root", 0.5)
        with self.assertRaises(FrozenCoreError):
            k.propose("core_weights", 0.5)


class TestEvaluate(unittest.TestCase):
    def test_shadow_trial_does_not_mutate_live_params(self):
        k = make_kernel()
        before = k.params["doubt_threshold"]
        m = k.propose("doubt_threshold", 0.6)
        k.evaluate(m, improving_evaluator(0.6))
        self.assertEqual(k.params["doubt_threshold"], before)
        self.assertEqual(m.state, State.TRIALED.value)

    def test_insufficient_samples_keeps_proposed(self):
        k = make_kernel(min_samples=100)
        m = k.propose("doubt_threshold", 0.6)
        rep = k.evaluate(m, improving_evaluator(0.6))
        self.assertEqual(m.state, State.PROPOSED.value)
        self.assertIn("insufficient", rep.detail)


class TestPromote(unittest.TestCase):
    def test_promotion_on_real_improvement(self):
        k = make_kernel()
        m = k.propose("doubt_threshold", 0.6, reason="tune toward optimum")
        rep = k.evaluate(m, improving_evaluator(0.6))
        self.assertGreater(rep.improvement, 0.05)
        entry = k.promote(m, rep)
        self.assertEqual(entry["action"], "promote")
        self.assertEqual(k.params["doubt_threshold"], 0.6)
        self.assertEqual(m.state, State.PROMOTED.value)

    def test_auto_rollback_when_improvement_below_gate(self):
        k = make_kernel(min_improvement=0.5)  # impossible gate
        m = k.propose("doubt_threshold", 0.6)
        rep = k.evaluate(m, improving_evaluator(0.6))
        entry = k.promote(m, rep)
        self.assertEqual(entry["action"], "rollback")
        self.assertEqual(m.state, State.ROLLED_BACK.value)
        self.assertEqual(k.params["doubt_threshold"], 0.4)

    def test_cannot_promote_untrialed(self):
        k = make_kernel()
        m = k.propose("doubt_threshold", 0.6)
        with self.assertRaises(ValueError):
            k.promote(m)


class TestAudit(unittest.TestCase):
    def test_history_is_hash_chained(self):
        k = make_kernel()
        m = k.propose("doubt_threshold", 0.6)
        rep = k.evaluate(m, improving_evaluator(0.6))
        k.promote(m, rep)
        m2 = k.propose("confidence_floor", 0.35)
        k.rollback(m2, reason="no trial")
        hist = k.history()
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[1]["head_prev"], hist[0]["hash"])
        for entry in hist:
            body = {k2: v for k2, v in entry.items() if k2 != "hash"}
            import hashlib, json
            self.assertEqual(
                hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest(),
                entry["hash"])


if __name__ == "__main__":
    unittest.main()
