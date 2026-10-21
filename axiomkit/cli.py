"""AxiomKit command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="axiomkit",
                                description="AxiomKit — unbreakable rules and leak-free memory for AI agents")
    sub = p.add_subparsers(dest="cmd", required=True)

    inv = sub.add_parser("invariants", help="manage the invariant ledger")
    inv.add_argument("--ledger", required=True, help="path to the JSONL ledger")
    inv.add_argument("--snapshot", action="store_true",
                     help="print the locked invariant snapshot and exit")

    rec = sub.add_parser("recall", help="query a JSON memory index")
    rec.add_argument("--index", required=True, help="path to a JSON index file")
    rec.add_argument("query")
    rec.add_argument("--top-k", type=int, default=5)

    args = p.parse_args(argv)

    if args.cmd == "invariants":
        from .invariants import InvariantPipeline
        pipe = InvariantPipeline(args.ledger)
        print(json.dumps(pipe.locked_snapshot(), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "recall":
        from .recall import SemanticRecall
        engine = SemanticRecall.from_index_file(args.index)
        hits = engine.query(args.query, top_k=args.top_k)
        print(json.dumps(hits, indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
