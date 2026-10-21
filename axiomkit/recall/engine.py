"""TF-IDF semantic recall — stdlib only, works offline.

Memory entries are dicts with at least ``{"id", "text"}`` plus optional
routing dimensions (``type``, ``priority``, ``tags``). Multi-dimensional
routing narrows the candidate set before TF-IDF ranking, which keeps recall
fast even for large local corpora.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

__all__ = ["SemanticRecall"]

_TOKEN = re.compile(r"[\w]+", re.UNICODE)

DIMENSIONS = ("type", "priority", "tags")  # routing dimensions


class SemanticRecall:
    """In-memory TF-IDF recall over local truth/memory entries."""

    def __init__(self, entries: Optional[Iterable[Dict[str, Any]]] = None):
        self._docs: List[Dict[str, Any]] = []
        self._tf: List[Counter] = []
        self._idf: Dict[str, float] = {}
        if entries:
            for e in entries:
                self.add(e)

    # -- ingest ---------------------------------------------------------------
    def add(self, entry: Dict[str, Any]) -> None:
        text = entry.get("text") or entry.get("content") or ""
        if len(text) < 3:
            raise ValueError("entry needs non-trivial text")
        doc = {"id": entry.get("id"), "text": text}
        for d in DIMENSIONS:
            if d in entry:
                doc[d] = entry[d]
        self._docs.append(doc)
        self._tf.append(Counter(self._tokenize(text)))
        self._idf = {}  # invalidate; rebuilt lazily

    @classmethod
    def from_index_file(cls, path: str | Path) -> "SemanticRecall":
        """Load from a JSON index with a ``truths`` or ``asset_entries`` list."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        items = data.get("truths") or data.get("asset_entries") or []
        return cls(items)

    # -- query ----------------------------------------------------------------
    def query(self, text: str, top_k: int = 5, *,
              type: Optional[str] = None,
              priority: Optional[int] = None,
              tags: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Rank entries by TF-IDF cosine similarity, after routing filters."""
        routed = [i for i, d in enumerate(self._docs)
                  if self._route(d, type=type, priority=priority, tags=tags)]
        if not routed:
            return []
        q = Counter(self._tokenize(text))
        if not q:
            return []
        idf = self._idf_values(routed)
        scores = []
        for i in routed:
            scores.append((self._cosine(q, self._tf[i], idf), self._docs[i]))
        scores.sort(key=lambda x: -x[0])
        return [dict(d, score=round(s, 4))
                for s, d in scores[:top_k] if s > 0]

    # -- internals --------------------------------------------------------------
    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return _TOKEN.findall(text.lower())

    @staticmethod
    def _route(doc: Dict[str, Any], **filters: Any) -> bool:
        if filters.get("type") is not None and doc.get("type") != filters["type"]:
            return False
        if filters.get("priority") is not None and doc.get("priority") != filters["priority"]:
            return False
        want = filters.get("tags")
        if want and not (set(doc.get("tags", [])) & set(want)):
            return False
        return True

    def _idf_values(self, idx: List[int]) -> Dict[str, float]:
        if self._idf:
            return self._idf
        n = len(idx)
        df: Counter = Counter()
        for i in idx:
            df.update(self._tf[i].keys())
        self._idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}
        return self._idf

    def _cosine(self, q: Counter, doc: Counter, idf: Dict[str, float]) -> float:
        common = set(q) & set(doc)
        if not common:
            return 0.0
        num = sum(q[w] * idf.get(w, 1.0) * doc[w] * idf.get(w, 1.0) for w in common)
        dq = math.sqrt(sum((v * idf.get(w, 1.0)) ** 2 for w, v in q.items()))
        dd = math.sqrt(sum((v * idf.get(w, 1.0)) ** 2 for w, v in doc.items()))
        return num / (dq * dd) if dq and dd else 0.0
