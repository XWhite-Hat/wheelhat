"""WebSocket endpoints for overlays and the control panel."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import db
from ..engine import ActiveSpin, engine, render_payload
from ..hub import hub
from ..integrations.registry import registry
from ..twitch.service import twitch

log = logging.getLogger("wheelhat.ws")

ws_router = APIRouter()


def resync_payload(active: ActiveSpin) -> dict[str, object]:
    """What a source that connects mid-spin needs to catch up.

    winner_id is what lets the overlay put the pointer on the winning slice.
    Without it the banner names one slice while the wheel sits wherever it
    was at rest, which reads as the wheel disagreeing with its own result.
    """
    return {
        "type": "spin_resync",
        "spin_id": active.spin_id,
        "winner": active.winner,
        "winner_id": active.winner_id,
        "ends_in_ms": max(0, int((active.ends_at - time.time()) * 1000)),
        # Time left on the wheel itself, so a source that joins mid-spin can
        # animate the rest of it instead of cutting straight to the answer.
        "stops_in_ms": max(0, int((active.stops_at - time.time()) * 1000)),
    }


@ws_router.websocket("/ws/overlay/{wheel_id}")
async def overlay_socket(websocket: WebSocket, wheel_id: str) -> None:
    await websocket.accept()
    wheel = db.get_wheel(wheel_id)
    if wheel is None:
        await websocket.send_json({"type": "error", "message": f"No wheel '{wheel_id}'"})
        await websocket.close()
        return

    await hub.join_overlay(wheel_id, websocket)
    try:
        await websocket.send_json({"type": "wheel_state", **render_payload(wheel)})

        # A browser source that reloads mid-spin should catch up rather than sit idle.
        active = engine.active_spin(wheel_id)
        if active is not None:
            await websocket.send_json(resync_payload(active))

        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong", "at": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - a malformed frame should not kill the server
        log.debug("Overlay socket for %s ended unexpectedly", wheel_id, exc_info=True)
    finally:
        await hub.leave_overlay(wheel_id, websocket)


@ws_router.websocket("/ws/control")
async def control_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    await hub.join_control(websocket)
    try:
        await websocket.send_json(
            {
                "type": "hello",
                "integrations": registry.status(),
                "twitch": twitch.status(),
                "overlay_counts": hub.overlay_counts(),
                "recent_spins": [s.model_dump() for s in db.list_spins(limit=15)],
            }
        )
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong", "at": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("Control socket ended unexpectedly", exc_info=True)
    finally:
        await hub.leave_control(websocket)
