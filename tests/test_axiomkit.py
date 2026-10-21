"""Run with: PYTHONPATH=. python3 -m unittest discover -s tests -v
Also compatible with pytest."""

import json
import tempfile
import unittest
from pathlib import Path

from axiomkit.invariants import InvariantPipeline, LedgerError
from axiomkit.fedmem import FederationPool
from axiomkit.gateway import Gateway
from axiomkit.recall import SemanticRecall


# ---------------- invariants ----------------
class TestInvariants(unittest.TestCase):
    def test_propose_verify_approve(self):
        with tempfile.TemporaryDirectory() as td:
            pipe = InvariantPipeline(Path(td) / "ledger.jsonl")
            cand = pipe.propose("L3", "circuit-breaker",
                                "energy_draw must be monotone within quota")
            report = pipe.verify(cand)
            self.assertIn(report.status, ("verified", "manual-review"))
            pipe.arbitrate(cand.id, "approve", arbiter="admin")
            snap = pipe.locked_snapshot()
            self.assertEqual(snap["count"], 1)
            self.assertEqual(snap["invariants"][0]["arbiter"], "admin")

    def test_ledger_is_hash_chained_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            pipe = InvariantPipeline(path)
            for i in range(3):
                c = pipe.propose("L0", "axiom", f"axiom closure holds for rule {i}")
                pipe.verify(c)
                pipe.arbitrate(c.id, "approve", arbiter="admin")

            # tamper with the middle entry
            lines = path.read_text(encoding="utf-8").splitlines()
            entry = json.loads(lines[1])
            entry["statement"] = "TAMPERED"
            lines[1] = json.dumps(entry, ensure_ascii=False)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaises(LedgerError):
                InvariantPipeline(path)

    def test_cannot_approve_refuted(self):
        with tempfile.TemporaryDirectory() as td:
            pipe = InvariantPipeline(Path(td) / "l.jsonl")
            c = pipe.propose("L0", "axiom", "short")  # too short -> refuted
            pipe.verify(c)
            self.assertEqual(c.status, "refuted")
            with self.assertRaises(LedgerError):
                pipe.arbitrate(c.id, "approve", arbiter="admin")


# ---------------- fedmem ----------------
class TestFedmem(unittest.TestCase):
    def test_fragment_requires_k_tenants(self):
        pool = FederationPool(min_k_tenants=2)
        pool.submit("alpha", "u1", "Rate limits must apply per API key")
        self.assertEqual(pool.publishable(), [])
        pool.submit("beta", "u1", "Per-key rate limits are mandatory")
        frags = pool.publishable()
        self.assertEqual(len(frags), 1)
        f = frags[0]
        self.assertEqual(f.tenants, ["alpha", "beta"])
        self.assertEqual(f.corroboration, 2)
        self.assertNotIn("kindly", f.summary.lower())

    def test_query_returns_relevant_fragment(self):
        pool = FederationPool(min_k_tenants=2)
        pool.submit("a", "u1", "rate limits per api key")
        pool.submit("b", "u1", "rate limit per api key mandatory")
        pool.submit("a", "u2", "logging is disabled by default")
        pool.submit("b", "u2", "logging disabled default configuration")
        hits = pool.query("how are rate limits enforced")
        self.assertTrue(hits)
        self.assertEqual(hits[0].fragment_id, "u1")


# ---------------- gateway ----------------
class TestGateway(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.index_file = Path(self._tmp.name) / "index.json"
        self.index_file.write_text(json.dumps({
            "index_meta": {},
            "asset_entries": [
                {"asset_id": "A-1", "summary": "gateway auth uses HMAC",
                 "tags": ["auth", "gateway"], "priority": "A"},
                {"asset_id": "B-2", "summary": "recall uses tfidf",
                 "tags": ["recall"], "priority": "B"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

    def test_signing_and_query(self):
        gw = Gateway(self.index_file, secret="s3cret")
        self.assertTrue(gw.verify(gw.sign("payload"), "payload"))
        self.assertFalse(gw.verify("bad", "payload"))
        hits = gw.query(tags=["auth"])
        self.assertEqual([h["asset_id"] for h in hits], ["A-1"])
        self.assertEqual(gw.query(priority="Z"), [])

    def test_cache_invalidation(self):
        gw = Gateway(self.index_file)
        self.assertEqual(len(gw.query()), 2)
        self.index_file.write_text(json.dumps({"asset_entries": []}),
                                   encoding="utf-8")
        self.assertEqual(gw.query(), [])


# ---------------- recall ----------------
class TestRecall(unittest.TestCase):
    def test_ranks_by_similarity(self):
        eng = SemanticRecall([
            {"id": "t1", "text": "circuit breaker trips when energy exceeds quota",
             "type": "governance", "priority": 1},
            {"id": "t2", "text": "tfidf recall ranks documents by similarity",
             "type": "engine", "priority": 2},
        ])
        hits = eng.query("when does the circuit breaker trip")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["id"], "t1")

    def test_routing_narrows_results(self):
        eng = SemanticRecall([
            {"id": "t1", "text": "memory pool shares fragments", "type": "fedmem"},
            {"id": "t2", "text": "memory pool shares fragments too", "type": "engine"},
        ])
        hits = eng.query("memory pool fragments", type="fedmem")
        self.assertEqual([h["id"] for h in hits], ["t1"])

    def test_rejects_empty_text(self):
        with self.assertRaises(ValueError):
            SemanticRecall([{"id": "x", "text": ""}])


if __name__ == "__main__":
    unittest.main()
