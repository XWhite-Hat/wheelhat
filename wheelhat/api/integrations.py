"""Connections to local applications: configure, connect, authorise, test."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import discovery
from ..integrations.base import ConnectorError
from ..integrations.registry import CONNECTOR_TYPES, KIND_LABELS, registry
from ..integrations.vtube_studio import VTubeStudioConnector
from ..models import IntegrationConfig, new_id

router = APIRouter(prefix="/integrations", tags=["integrations"])


class IntegrationPayload(BaseModel):
    id: Optional[str] = None
    kind: str
    name: str = ""
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 4455
    password: Optional[str] = None
    auto_connect: bool = True


class ProbeRequest(BaseModel):
    kind: str
    host: str = "127.0.0.1"
    port: int


@router.get("")
async def list_integrations() -> dict[str, Any]:
    return {
        "integrations": registry.status(),
        "kinds": [{"kind": k, "label": KIND_LABELS.get(k, k)} for k in CONNECTOR_TYPES],
    }


@router.post("")
async def upsert_integration(payload: IntegrationPayload) -> dict[str, Any]:
    if payload.kind not in CONNECTOR_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported connection kind '{payload.kind}'")

    integration_id = payload.id or new_id("int_")
    existing = registry.config(integration_id)
    cfg = IntegrationConfig(
        id=integration_id,
        kind=payload.kind,  # type: ignore[arg-type]
        name=payload.name or KIND_LABELS.get(payload.kind, payload.kind),
        enabled=payload.enabled,
        host=payload.host,
        port=payload.port,
        # A blank password means "leave the stored one alone".
        password=payload.password if payload.password is not None else (existing.password if existing else ""),
        token=existing.token if existing else "",
        auto_connect=payload.auto_connect,
    )
    await registry.apply(cfg)
    return {"integration": next(i for i in registry.status() if i["id"] == cfg.id)}


@router.delete("/{integration_id}")
async def delete_integration(integration_id: str) -> dict[str, Any]:
    if not await registry.remove(integration_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"deleted": integration_id}


@router.post("/{integration_id}/connect")
async def connect(integration_id: str) -> dict[str, Any]:
    try:
        connector = await registry.reconnect(integration_id)
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "status": connector.describe()}


@router.post("/{integration_id}/disconnect")
async def disconnect(integration_id: str) -> dict[str, Any]:
    cfg = registry.config(integration_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    cfg.enabled = False
    await registry.apply(cfg)
    return {"ok": True}


@router.post("/{integration_id}/authorise")
async def authorise(integration_id: str) -> dict[str, Any]:
    """VTube Studio's plugin approval flow: pops a dialog inside VTS."""
    cfg = registry.config(integration_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    if cfg.kind != "vtube_studio":
        raise HTTPException(status_code=400, detail="Only VTube Studio needs this step")

    connector = registry.get(integration_id)
    if not isinstance(connector, VTubeStudioConnector):
        connector = VTubeStudioConnector(host=cfg.host, port=cfg.port)
    try:
        token = await connector.request_access()
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    cfg.token = token
    cfg.enabled = True
    await registry.apply(cfg)
    try:
        await registry.reconnect(integration_id)
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "message": "VTube Studio authorised"}


@router.post("/probe")
async def probe(payload: ProbeRequest) -> dict[str, Any]:
    return await discovery.probe_one(payload.kind, payload.host, payload.port)
