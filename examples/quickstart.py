"""End-to-end tour of AxiomKit — run: python examples/quickstart.py"""

import json
import tempfile
from pathlib import Path

from axiomkit.invariants import InvariantPipeline
from axiomkit.fedmem import FederationPool
from axiomkit.gateway import Gateway
from axiomkit.recall import SemanticRecall


def main() -> None:
    # 1) Invariants: propose -> verify -> arbitrate -> hash-chained ledger
    with tempfile.TemporaryDirectory() as td:
        pipe = InvariantPipeline(Path(td) / "ledger.jsonl")
        cand = pipe.propose("L3", "circuit-breaker",
                            "energy_draw(t2) <= energy_draw(t1) + quota for t2 > t1")
        print("verify:", pipe.verify(cand).status)
        pipe.arbitrate(cand.id, "approve", arbiter="human-admin")
        snap = pipe.locked_snapshot()
        print(f"locked invariants: {snap['count']}, head={snap['head_hash'][:12]}…")

    # 2) Federated memory: 2 tenants corroborate -> publishable fragment
    pool = FederationPool(min_k_tenants=2)
    pool.submit("tenant-alpha", "u1", "Rate limits must apply per API key.")
    pool.submit("tenant-beta", "u1", "Per-key rate limits are mandatory.")
    for frag in pool.publishable():
        print(f"fragment {frag.fragment_id}: grade={frag.grade} "
              f"tenants={frag.tenants} summary={frag.summary!r}")

    # 3) Gateway: HMAC-signed anchored lookup over an index
    with tempfile.TemporaryDirectory() as td:
        index = Path(td) / "index.json"
        index.write_text(json.dumps({
            "asset_entries": [
                {"asset_id": "A-1", "summary": "auth is HMAC-SHA256",
                 "tags": ["auth"], "priority": "A"},
                {"asset_id": "B-2", "summary": "recall is tfidf based",
                 "tags": ["recall"], "priority": "B"},
            ]}, ensure_ascii=False), encoding="utf-8")
        gw = Gateway(index, secret="demo-secret")
        print("gateway auth hits:", [e["asset_id"] for e in gw.query(tags=["auth"])])

    # 4) Recall: offline TF-IDF with routing
    eng = SemanticRecall([
        {"id": "t1", "text": "circuit breaker trips when energy exceeds quota",
         "type": "governance"},
        {"id": "t2", "text": "tfidf recall ranks documents by similarity",
         "type": "engine"},
    ])
    top = eng.query("when does the circuit breaker trip", type="governance")
    print("recall top hit:", top[0]["id"], top[0]["score"])


if __name__ == "__main__":
    main()
