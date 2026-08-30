"""Scan the machine for streaming apps WheelHat can talk to."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from .. import discovery as discovery_module
from ..integrations.registry import CONNECTOR_TYPES, KIND_LABELS, registry
from ..models import IntegrationConfig

router = APIRouter(prefix="/discovery", tags=["discovery"])


class ScanRequest(BaseModel):
    host: str = "127.0.0.1"
    apps: Optional[list[str]] = None


class AdoptRequest(BaseModel):
    """Turn a discovered app into a saved connection in one click."""

    app_id: str
    host: str = "127.0.0.1"
    port: int
    kind: str
    password: str = ""


@router.get("")
async def scan_default() -> dict[str, Any]:
    found = await discovery_module.scan()
    return {"results": found, "configured": [c.model_dump() for c in registry.configs()]}


@router.post("/scan")
async def scan(payload: ScanRequest) -> dict[str, Any]:
    found = await discovery_module.scan(payload.host, payload.apps)
    return {"results": found, "configured": [c.model_dump() for c in registry.configs()]}


@router.post("/adopt")
async def adopt(payload: AdoptRequest) -> dict[str, Any]:
    if payload.kind not in CONNECTOR_TYPES:
        return {
            "ok": False,
            "detail": (
                f"WheelHat has no native connector for that app yet. "
                f"Use an HTTP request action pointed at {payload.host}:{payload.port}."
            ),
        }
    existing = next(
        (c for c in registry.configs() if c.kind == payload.kind and c.port == payload.port),
        None,
    )
    cfg = IntegrationConfig(
        id=existing.id if existing else payload.app_id,
        kind=payload.kind,  # type: ignore[arg-type]
        name=KIND_LABELS.get(payload.kind, payload.kind),
        enabled=True,
        host=payload.host,
        port=payload.port,
        password=payload.password or (existing.password if existing else ""),
        token=existing.token if existing else "",
    )
    await registry.apply(cfg)
    return {"ok": True, "integration_id": cfg.id}
