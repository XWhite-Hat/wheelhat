"""Twitch sign-in, status and reward helpers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from ..triggers import handle_twitch_event
from ..twitch.auth import AuthError
from ..twitch.service import twitch

router = APIRouter(prefix="/twitch", tags=["twitch"])


class ClientIdPayload(BaseModel):
    client_id: str


class ChatPayload(BaseModel):
    message: str


class SimulatePayload(BaseModel):
    """Fire a synthetic Twitch event so triggers can be tested off-stream."""

    event_type: str = "channel.channel_points_custom_reward_redemption.add"
    event: dict[str, Any] = {}


@router.get("/status")
async def status() -> dict[str, Any]:
    return {"twitch": twitch.status(), "scopes": config.TWITCH_SCOPES}


@router.post("/client-id")
async def set_client_id(payload: ClientIdPayload) -> dict[str, Any]:
    await twitch.set_client_id(payload.client_id)
    return {"twitch": twitch.status()}


@router.post("/login")
async def login() -> dict[str, Any]:
    try:
        flow = await twitch.begin_login()
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"flow": flow, "twitch": twitch.status()}


@router.post("/logout")
async def logout() -> dict[str, Any]:
    await twitch.logout()
    return {"twitch": twitch.status()}


@router.get("/rewards")
async def rewards() -> dict[str, Any]:
    try:
        return {"rewards": await twitch.list_rewards()}
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/resubscribe")
async def resubscribe() -> dict[str, Any]:
    await twitch.resubscribe()
    return {"twitch": twitch.status()}


@router.post("/chat")
async def chat(payload: ChatPayload) -> dict[str, Any]:
    try:
        await twitch.send_chat(payload.message)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/simulate")
async def simulate(payload: SimulatePayload) -> dict[str, Any]:
    await handle_twitch_event(payload.event_type, payload.event)
    return {"ok": True}
