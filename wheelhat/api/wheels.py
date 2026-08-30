"""Wheel CRUD, spinning and history."""

from __future__ import annotations

import contextlib
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import config, db
from ..engine import SpinRejected, engine, render_payload
from ..hub import hub
from ..models import DEFAULT_PALETTE, Slice, Wheel, new_id

router = APIRouter(prefix="/wheels", tags=["wheels"])


class SpinRequest(BaseModel):
    source: str = "manual"
    actor: str = ""
    force_slice_id: str = ""
    skip_actions: bool = False
    ignore_cooldown: bool = True
    variables: dict[str, Any] = Field(default_factory=dict)


class ReorderRequest(BaseModel):
    ids: list[str]


class QuickSlicesRequest(BaseModel):
    """Bulk entry: one slice label per line."""

    text: str
    replace: bool = True


def _get(wheel_id: str) -> Wheel:
    wheel = db.get_wheel(wheel_id)
    if wheel is None:
        raise HTTPException(status_code=404, detail=f"No wheel with id '{wheel_id}'")
    return wheel


def _summary(wheel: Wheel) -> dict[str, Any]:
    return {
        "id": wheel.id,
        "name": wheel.name,
        "description": wheel.description,
        "enabled": wheel.enabled,
        "slice_count": len(wheel.slices),
        "spinnable_count": len(wheel.spinnable()),
        "trigger_count": len([t for t in wheel.triggers if t.enabled]),
        "action_count": sum(len(s.actions) for s in wheel.slices)
        + len(wheel.pre_actions)
        + len(wheel.post_actions),
        "overlay_url": overlay_url(wheel.id),
        "overlay_clients": hub.overlay_count(wheel.id),
        "spinning": engine.is_spinning(wheel.id),
        "updated_at": wheel.updated_at,
    }


def base_url() -> str:
    host = "localhost" if config.HOST in {"0.0.0.0", "127.0.0.1", ""} else config.HOST
    return f"http://{host}:{config.PORT}"


def overlay_url(wheel_id: str) -> str:
    return f"{base_url()}/overlay/{wheel_id}"


def trigger_url(wheel_id: str) -> str:
    """URL another app can fetch to spin this wheel."""
    return f"{base_url()}/api/wheels/{wheel_id}/trigger"


# ------------------------------------------------------------------------ CRUD


@router.get("")
async def list_wheels() -> dict[str, Any]:
    return {"wheels": [_summary(w) for w in db.list_wheels()]}


@router.post("", status_code=201)
async def create_wheel(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    if "id" in data:
        data.pop("id")
    wheel = Wheel(**data) if data else Wheel(name="New wheel")
    if not wheel.slices:
        wheel.slices = [
            Slice(label=label, color=DEFAULT_PALETTE[index % len(DEFAULT_PALETTE)])
            for index, label in enumerate(["Option one", "Option two", "Option three"])
        ]
    db.save_wheel(wheel)
    await hub.broadcast_control({"type": "wheels_changed"})
    return wheel.model_dump()


@router.get("/{wheel_id}")
async def get_wheel(wheel_id: str) -> dict[str, Any]:
    wheel = _get(wheel_id)
    payload = wheel.model_dump()
    payload["overlay_url"] = overlay_url(wheel.id)
    payload["trigger_url"] = trigger_url(wheel.id)
    payload["overlay_clients"] = hub.overlay_count(wheel.id)
    payload["spinning"] = engine.is_spinning(wheel.id)
    return payload


@router.put("/{wheel_id}")
async def update_wheel(wheel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    existing = _get(wheel_id)
    payload = dict(payload)
    payload["id"] = wheel_id
    payload.setdefault("created_at", existing.created_at)
    try:
        wheel = Wheel(**payload)
    except Exception as exc:  # noqa: BLE001 - pydantic message is the useful part
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.save_wheel(wheel)
    await _after_change(wheel)
    return wheel.model_dump()


@router.patch("/{wheel_id}")
async def patch_wheel(wheel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    wheel = _get(wheel_id)
    merged = wheel.model_dump()
    merged.update(payload)
    merged["id"] = wheel_id
    wheel = Wheel(**merged)
    db.save_wheel(wheel)
    await _after_change(wheel)
    return wheel.model_dump()


@router.delete("/{wheel_id}")
async def delete_wheel(wheel_id: str) -> dict[str, Any]:
    if not db.delete_wheel(wheel_id):
        raise HTTPException(status_code=404, detail="Wheel not found")
    await hub.broadcast_control({"type": "wheels_changed"})
    return {"deleted": wheel_id}


@router.post("/{wheel_id}/duplicate")
async def duplicate_wheel(wheel_id: str) -> dict[str, Any]:
    wheel = _get(wheel_id)
    clone = Wheel(**wheel.model_dump())
    clone.id = new_id("whl_")
    clone.name = f"{wheel.name} (copy)"
    # Fresh ids everywhere so editing the copy never touches the original.
    for item in clone.slices:
        item.id = new_id("sl_")
        item.won_count = 0
        item.cooldown_remaining = 0
        for action in item.actions:
            action.id = new_id("act_")
    for trigger in clone.triggers:
        trigger.id = new_id("trg_")
        trigger.enabled = False  # avoid two wheels fighting over one reward
    db.save_wheel(clone)
    await hub.broadcast_control({"type": "wheels_changed"})
    return clone.model_dump()


@router.post("/reorder")
async def reorder(payload: ReorderRequest) -> dict[str, Any]:
    db.reorder_wheels(payload.ids)
    await hub.broadcast_control({"type": "wheels_changed"})
    return {"ok": True}


@router.post("/{wheel_id}/slices/bulk")
async def bulk_slices(wheel_id: str, payload: QuickSlicesRequest) -> dict[str, Any]:
    """Paste a list of labels; optionally keep existing slices and append."""
    wheel = _get(wheel_id)
    labels = [line.strip() for line in payload.text.splitlines() if line.strip()]
    palette = wheel.appearance.palette or DEFAULT_PALETTE
    start = 0 if payload.replace else len(wheel.slices)
    new_slices = [
        Slice(label=label, color=palette[(start + i) % len(palette)])
        for i, label in enumerate(labels)
    ]
    wheel.slices = new_slices if payload.replace else wheel.slices + new_slices
    db.save_wheel(wheel)
    await _after_change(wheel)
    return wheel.model_dump()


@router.post("/{wheel_id}/reset")
async def reset_wheel(wheel_id: str) -> dict[str, Any]:
    """Undo eliminations and cooldowns from an elimination-style wheel."""
    wheel = _get(wheel_id)
    for item in wheel.slices:
        item.enabled = True
        item.cooldown_remaining = 0
        item.won_count = 0
    db.save_wheel(wheel)
    await _after_change(wheel)
    return wheel.model_dump()


# ------------------------------------------------------------------------ spin


@router.post("/{wheel_id}/spin")
async def spin(wheel_id: str, payload: Optional[SpinRequest] = None) -> dict[str, Any]:
    request = payload or SpinRequest()
    variables = dict(request.variables)
    if request.actor:
        variables.setdefault("user", request.actor)
    try:
        return await engine.spin(
            wheel_id,
            source=request.source,
            variables=variables,
            force_slice_id=request.force_slice_id,
            skip_actions=request.skip_actions,
            ignore_cooldown=request.ignore_cooldown,
        )
    except SpinRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{wheel_id}/trigger")
async def trigger(
    wheel_id: str,
    user: str = Query("", description="Name to report as {{user}}"),
    source: str = Query("external", description="Shown in the activity log"),
) -> dict[str, Any]:
    """Spin from another application.

    Deliberately a GET so it can be pasted into tools that only fetch a URL -
    Streamer.bot's Fetch URL sub-action, stream deck buttons, Touch Portal,
    SAMMI, or just a browser bookmark.
    """
    try:
        return await engine.spin(
            wheel_id,
            source=source,
            variables={"user": user} if user else {},
            ignore_cooldown=False,
        )
    except SpinRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{wheel_id}/cancel")
async def cancel(wheel_id: str) -> dict[str, Any]:
    return {"cancelled": await engine.cancel(wheel_id)}


@router.get("/{wheel_id}/render")
async def render(wheel_id: str) -> dict[str, Any]:
    return render_payload(_get(wheel_id))


@router.post("/{wheel_id}/refresh-overlays")
async def refresh_overlays(wheel_id: str) -> dict[str, Any]:
    wheel = _get(wheel_id)
    await hub.broadcast_overlay(wheel_id, {"type": "wheel_state", **render_payload(wheel)})
    return {"clients": hub.overlay_count(wheel_id)}


# --------------------------------------------------------------------- history


@router.get("/{wheel_id}/history")
async def history(wheel_id: str, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return {"spins": [s.model_dump() for s in db.list_spins(wheel_id, limit)]}


@router.delete("/{wheel_id}/history")
async def clear_history(wheel_id: str) -> dict[str, Any]:
    db.clear_spins(wheel_id)
    return {"ok": True}


async def _after_change(wheel: Wheel) -> None:
    """Push the new look to overlays and re-sync Twitch subscriptions."""
    await hub.broadcast_overlay(wheel.id, {"type": "wheel_state", **render_payload(wheel)})
    await hub.broadcast_control({"type": "wheels_changed", "wheel_id": wheel.id})
    from ..twitch.service import twitch

    # Never block a save on Twitch being unreachable.
    with contextlib.suppress(Exception):
        await twitch.resubscribe()
