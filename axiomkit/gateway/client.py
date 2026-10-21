"""HMAC-signing client for the AxiomKit gateway."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional
from urllib import request as _urlreq

__all__ = ["GatewayClient"]


class GatewayClient:
    """Talk to a gateway endpoint with HMAC-SHA256 request signing.

    Works with any HTTP framework on the server side; the signature covers
    the raw request body so replay/tampering is detectable.
    """

    def __init__(self, base_url: str, secret: str):
        self.base_url = base_url.rstrip("/")
        self._secret = secret.encode()

    def sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode(),
                        hashlib.sha256).hexdigest()

    def query(self, tags: Optional[List[str]] = None,
              priority: Optional[str] = None,
              max_tokens: Optional[int] = None,
              limit: int = 20) -> List[Dict[str, Any]]:
        body = json.dumps({"tags": tags or [], "priority": priority,
                           "max_tokens": max_tokens, "limit": limit})
        req = _urlreq.Request(
            f"{self.base_url}/query", data=body.encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "X-Signature": self.sign(body)})
        with _urlreq.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
