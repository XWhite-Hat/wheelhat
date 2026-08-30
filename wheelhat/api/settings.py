"""General settings, backup/restore and the app-wide status snapshot."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import __version__, config, db
from ..engine import engine
from ..hub import hub
from ..integrations.registry import registry
from ..models import Wheel, new_id
from ..twitch.service import twitch
from .wheels import overlay_url

router = APIRouter(tags=["settings"])

DEFAULTS: dict[str, Any] = {
    "allow_shell_actions": False,
    "theme": "dark",
    "confirm_destructive": True,
}


class SettingsPayload(BaseModel):
    values: dict[str, Any]


class ImportPayload(BaseModel):
    data: dict[str, Any]
    replace: bool = False


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    values = {key: db.get_setting(key, default) for key, default in DEFAULTS.items()}
    return {
        "settings": values,
        "paths": {
            "data_dir": str(config.DATA_DIR),
            "assets_dir": str(config.ASSETS_DIR),
            "database": str(config.DB_PATH),
        },
        "server": {"host": config.HOST, "port": config.PORT, "version": __version__},
    }


@router.put("/settings")
async def update_settings(payload: SettingsPayload) -> dict[str, Any]:
    for key, value in payload.values.items():
        if key not in DEFAULTS:
            raise HTTPException(status_code=422, detail=f"Unknown setting '{key}'")
        db.set_setting(key, value)
    await hub.broadcast_control({"type": "settings_changed"})
    return await get_settings()


@router.get("/status")
async def status() -> dict[str, Any]:
    wheels = db.list_wheels()
    return {
        "version": __version__,
        "wheels": [
            {
                "id": w.id,
                "name": w.name,
                "enabled": w.enabled,
                "spinning": engine.is_spinning(w.id),
                "overlay_clients": hub.overlay_count(w.id),
                "overlay_url": overlay_url(w.id),
            }
            for w in wheels
        ],
        "integrations": registry.status(),
        "twitch": twitch.status(),
        "recent_spins": [s.model_dump() for s in db.list_spins(limit=15)],
        "action_log": db.list_action_log(25),
        "time": time.time(),
    }


@router.get("/export")
async def export_all() -> dict[str, Any]:
    return {
        "kind": "wheelhat-backup",
        "version": __version__,
        "exported_at": time.time(),
        "wheels": [w.model_dump() for w in db.list_wheels()],
        "settings": {key: db.get_setting(key, default) for key, default in DEFAULTS.items()},
    }


@router.post("/import")
async def import_all(payload: ImportPayload) -> dict[str, Any]:
    data = payload.data
    if data.get("kind") != "wheelhat-backup":
        raise HTTPException(status_code=422, detail="That file is not a WheelHat backup.")

    if payload.replace:
        for wheel in db.list_wheels():
            db.delete_wheel(wheel.id)

    imported = 0
    existing_ids = {w.id for w in db.list_wheels()}
    for raw in data.get("wheels", []):
        try:
            wheel = Wheel(**raw)
        except Exception:  # noqa: BLE001 - skip anything unreadable, report the count
            continue
        if wheel.id in existing_ids:
            wheel.id = new_id("whl_")
            wheel.name = f"{wheel.name} (imported)"
        db.save_wheel(wheel)
        imported += 1

    for key, value in (data.get("settings") or {}).items():
        if key in DEFAULTS:
            db.set_setting(key, value)

    await hub.broadcast_control({"type": "wheels_changed"})
    return {"imported": imported}


@router.get("/export/{wheel_id}")
async def export_wheel(wheel_id: str) -> dict[str, Any]:
    wheel = db.get_wheel(wheel_id)
    if wheel is None:
        raise HTTPException(status_code=404, detail="Wheel not found")
    return {
        "kind": "wheelhat-backup",
        "version": __version__,
        "exported_at": time.time(),
        "wheels": [json.loads(wheel.model_dump_json())],
    }
