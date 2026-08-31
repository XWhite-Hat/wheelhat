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


class RewardPayload(BaseModel):
    title: str
    cost: int = 1000
    prompt: str = ""
    background_color: str = ""
    user_input: bool = False
    cooldown_seconds: int = 0
    max_per_stream: int = 0
    max_per_user_per_stream: int = 0


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
async def rewards(manageable: bool = False) -> dict[str, Any]:
    """The channel's rewards. `manageable` limits it to the ones WheelHat made,
    which are the only ones whose redemptions Twitch lets it close."""
    try:
        return {"rewards": await twitch.list_rewards(manageable_only=manageable)}
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rewards", status_code=201)
async def create_reward(payload: RewardPayload) -> dict[str, Any]:
    """Create a reward so nobody has to go and find a reward id.

    A reward made here belongs to WheelHat, which is also what makes closing
    its redemptions possible - Twitch refuses that for rewards created
    anywhere else.
    """
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="The reward needs a name.")
    if payload.cost < 1:
        raise HTTPException(status_code=422, detail="A reward has to cost at least 1 point.")
    if not twitch.has_channel_points:
        # Twitch would refuse this anyway, with a message about the broadcaster.
        # Saying what to do instead is more use than relaying that.
        raise HTTPException(
            status_code=409,
            detail=(
                "Channel points need affiliate or partner status, so there are no rewards "
                "to create on this channel yet. A chat command trigger works on any channel."
            ),
        )
    try:
        return {"reward": await twitch.create_reward(
            payload.title,
            payload.cost,
            prompt=payload.prompt,
            background_color=payload.background_color,
            user_input=payload.user_input,
            cooldown_seconds=payload.cooldown_seconds,
            max_per_stream=payload.max_per_stream,
            max_per_user_per_stream=payload.max_per_user_per_stream,
        )}
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# Declared before /rewards/{reward_id}: FastAPI matches in order, and a path
# parameter happily swallows the literal "listen".
@router.post("/rewards/listen")
async def start_reward_listen(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Watch for the next redemption so a reward can be identified by redeeming it.

    Exists so nobody has to find a reward id, including for rewards created on
    Twitch itself, which WheelHat cannot create or manage but can still see.
    """
    if not twitch.tokens.valid:
        raise HTTPException(status_code=400, detail="Sign in to Twitch first.")
    seconds = int((payload or {}).get("seconds") or 0) or None
    return {"reward_capture": await twitch.start_reward_capture(seconds)}


@router.delete("/rewards/listen")
async def stop_reward_listen() -> dict[str, Any]:
    return {"reward_capture": await twitch.stop_reward_capture()}


@router.delete("/rewards/{reward_id}")
async def delete_reward(reward_id: str) -> dict[str, Any]:
    try:
        await twitch.delete_reward(reward_id)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": reward_id}


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
