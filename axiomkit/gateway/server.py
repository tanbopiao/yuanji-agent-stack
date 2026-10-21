"""Core gateway logic — framework-free so it can be tested without fastapi."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["Gateway"]


class Gateway:
    """Serve anchored lookups over a JSON memory index.

    The index is a JSON document of the shape::

        {"index_meta": {...}, "asset_entries": [
            {"asset_id": "...", "summary": "...", "tags": ["..."],
             "priority": "A"}]}

    Lookups are filtered by anchor conditions (tags / priority / max results)
    instead of scanning everything client-side.
    """

    def __init__(self, index_path: str | Path,
                 secret: Optional[str] = None,
                 max_tokens_default: int = 4000):
        self._index_path = Path(index_path)
        self._secret = secret or os.getenv(
            "AXIOMKIT_GATEWAY_SECRET", "axiomkit-dev-secret-change-me")
        self.max_tokens_default = max_tokens_default
        self._cache: Optional[Dict[str, Any]] = None
        self._mtime: float = -1.0

    # -- auth ---------------------------------------------------------------
    def sign(self, payload: str) -> str:
        return hmac.new(self._secret.encode(), payload.encode(),
                        hashlib.sha256).hexdigest()

    def verify(self, signature: str, payload: str) -> bool:
        return hmac.compare_digest(self.sign(payload), signature or "")

    # -- index --------------------------------------------------------------
    def load_index(self) -> Dict[str, Any]:
        try:
            stat = self._index_path.stat()
        except OSError:
            return {"index_meta": {}, "asset_entries": [], "error": "index not found"}
        if self._cache is not None and (stat.st_mtime, stat.st_size) == self._mtime:
            return self._cache
        try:
            self._cache = json.loads(
                self._index_path.read_text(encoding="utf-8"))
            self._mtime = (stat.st_mtime, stat.st_size)
        except (OSError, json.JSONDecodeError) as exc:
            return {"index_meta": {}, "asset_entries": [], "error": str(exc)}
        return self._cache

    # -- query --------------------------------------------------------------
    def query(self, tags: Optional[List[str]] = None,
              priority: Optional[str] = None,
              max_tokens: Optional[int] = None,
              limit: int = 20) -> List[Dict[str, Any]]:
        """Return matching entries, trimmed to a token budget."""
        index = self.load_index()
        entries: List[Dict[str, Any]] = index.get("asset_entries", [])
        budget = max_tokens or self.max_tokens_default
        used = 0
        out: List[Dict[str, Any]] = []
        for e in entries:
            if tags and not (set(e.get("tags", [])) & set(tags)):
                continue
            if priority and e.get("priority") != priority:
                continue
            text = f"[{e.get('priority', 'C')}] {e.get('asset_id', '')} | " \
                   f"{','.join(e.get('tags', [])[:5])} | {e.get('summary', '')}"
            used += len(text) // 4  # ~4 chars per token
            if used > budget:
                break
            out.append(e)
            if len(out) >= limit:
                break
        return out
