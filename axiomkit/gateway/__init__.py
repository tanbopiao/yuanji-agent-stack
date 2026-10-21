"""Anchored memory-gateway: HMAC-signed lookups over a memory index."""

from .server import Gateway
from .client import GatewayClient

__all__ = ["Gateway", "GatewayClient"]


def create_app(index_path: str, secret: str | None = None):
    """Build a fastapi app exposing the gateway. Requires the [http] extra."""
    try:
        from fastapi import FastAPI, Header, HTTPException
    except ImportError as exc:
        raise ImportError("install axiomkit[http] to use create_app") from exc

    gw = Gateway(index_path, secret=secret)
    app = FastAPI(title="AxiomKit Gateway", version="0.1.0")

    @app.post("/query")
    def query(payload: dict, x_signature: str = Header(default="")):  # noqa: ANN202
        import json as _json
        body = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if not gw.verify(x_signature, body):
            raise HTTPException(status_code=401, detail="bad signature")
        return gw.query(tags=payload.get("tags"),
                        priority=payload.get("priority"),
                        max_tokens=payload.get("max_tokens"),
                        limit=payload.get("limit", 20))

    return app
