"""FederationPool — knowledge sharing without data leakage.

Tenants submit raw text; only *purified semantic fragments* corroborated by
at least K independent tenants become publishable. Raw tenant text is stored
per-tenant and never appears in published fragments.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = ["FederationPool", "Fragment"]

# Boilerplate stripped when purifying a fragment.
_NOISE = re.compile(r"\b(i|we|my|our|please|kindly|urgent|asap)\b[,.!]?", re.IGNORECASE)


@dataclass
class Fragment:
    """A purified, publishable semantic fragment. Contains no raw tenant text."""

    fragment_id: str
    summary: str
    tags: List[str]
    grade: str                      # A | B | C | D
    tenants: List[str]
    corroboration: int
    created_at: float
    content_hash: str

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _grade(corroboration: int) -> str:
    if corroboration >= 4:
        return "A"
    if corroboration == 3:
        return "B"
    if corroboration == 2:
        return "C"
    return "D"


class FederationPool:
    """Collect tenant submissions and publish only corroborated fragments.

    Parameters
    ----------
    min_k_tenants:
        Minimum number of independent tenants that must submit semantically
        similar content before a fragment becomes publishable.
    similarity_threshold:
        Token-overlap Jaccard threshold treating two submissions as the same
        semantic unit.
    """

    def __init__(self, min_k_tenants: int = 2, similarity_threshold: float = 0.4):
        if min_k_tenants < 1:
            raise ValueError("min_k_tenants must be >= 1")
        self._min_k = min_k_tenants
        self._threshold = similarity_threshold
        # tenant -> unit_id -> raw text (never published)
        self._raw: Dict[str, Dict[str, str]] = defaultdict(dict)
        # unit_id -> {tenant: token_set}
        self._units: Dict[str, Dict[str, set]] = defaultdict(dict)

    # -- submit -------------------------------------------------------------
    def submit(self, tenant: str, unit_id: str, text: str,
               tags: Optional[List[str]] = None) -> bool:
        """Record a tenant's submission. Returns True if the unit is now publishable."""
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        self._raw[tenant][unit_id] = text
        self._units[unit_id][tenant] = self._tokenize(text)
        self._tags = getattr(self, "_tags", {})
        self._tags.setdefault(unit_id, list(tags or []))
        return self._distinct_tenants(unit_id) >= self._min_k

    # -- publish ------------------------------------------------------------
    def publishable(self) -> List[Fragment]:
        """Return purified fragments corroborated by >= min_k tenants."""
        out: List[Fragment] = []
        for unit_id, tenants in self._units.items():
            k = self._distinct_tenants(unit_id)
            if k < self._min_k:
                continue
            summary = self._purify(unit_id)
            digest = hashlib.sha256(summary.encode()).hexdigest()
            out.append(Fragment(
                fragment_id=unit_id,
                summary=summary,
                tags=list(self._tags.get(unit_id, [])),
                grade=_grade(k),
                tenants=sorted(tenants),
                corroboration=k,
                created_at=time.time(),
                content_hash=digest,
            ))
        out.sort(key=lambda f: (-f.corroboration, f.fragment_id))
        return out

    def query(self, text: str, top_k: int = 5) -> List[Fragment]:
        """Rank publishable fragments by token overlap with the query."""
        q = self._tokenize(text)
        if not q:
            return []
        scored = []
        for frag in self.publishable():
            overlap = len(q & self._tokenize(frag.summary))
            scored.append((overlap / len(q), frag))
        scored.sort(key=lambda x: -x[0])
        return [f for s, f in scored[:top_k] if s > 0]

    # -- internals -----------------------------------------------------------
    def _distinct_tenants(self, unit_id: str) -> int:
        return len(self._units.get(unit_id, {}))

    @staticmethod
    def _tokenize(text: str) -> set:
        return set(re.findall(r"[\w]+", text.lower()))

    def _similar(self, a: set, b: set) -> bool:
        if not a or not b:
            return False
        return len(a & b) / len(a | b) >= self._threshold

    def _purify(self, unit_id: str) -> str:
        """Merge tenant wordings into one neutral summary; raw text is not returned."""
        texts = [d[unit_id] for d in self._raw.values() if unit_id in d]
        tokens: List[set] = [self._tokenize(t) for t in texts]
        # keep tokens shared by >= 2 tenants, else the longest tenant's tokens
        shared: set = set()
        for i, a in enumerate(tokens):
            for b in tokens[i + 1:]:
                shared |= (a & b)
        if not shared and tokens:
            shared = max(tokens, key=len)
        words = sorted(shared, key=lambda w: (-len(w), w))
        cleaned = _NOISE.sub("", " ".join(words)).strip()
        return cleaned or unit_id
