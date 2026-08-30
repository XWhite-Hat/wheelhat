"""Maps incoming Twitch events onto the wheels that listen for them."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from . import db
from .engine import SpinRejected, engine
from .hub import hub
from .models import Trigger, Wheel

log = logging.getLogger("wheelhat.triggers")

PERMISSION_RANK = {"everyone": 0, "subscriber": 1, "vip": 2, "moderator": 3, "broadcaster": 4}

#: Per-trigger and per-trigger-per-user cooldown bookkeeping.
_trigger_cooldowns: dict[str, float] = {}
_user_cooldowns: dict[tuple[str, str], float] = {}


def _badge_rank(badges: list[dict[str, Any]]) -> int:
    rank = 0
    for badge in badges or []:
        set_id = badge.get("set_id", "")
        if set_id == "broadcaster":
            rank = max(rank, PERMISSION_RANK["broadcaster"])
        elif set_id == "moderator":
            rank = max(rank, PERMISSION_RANK["moderator"])
        elif set_id == "vip":
            rank = max(rank, PERMISSION_RANK["vip"])
        elif set_id in {"subscriber", "founder"}:
            rank = max(rank, PERMISSION_RANK["subscriber"])
    return rank


def normalise(event_type: str, event: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Flatten a Twitch event into the trigger + template variable shape."""
    if event_type == "channel.channel_points_custom_reward_redemption.add":
        reward = event.get("reward", {})
        return {
            "trigger_type": "channel_points",
            "user": event.get("user_name", ""),
            "user_login": event.get("user_login", ""),
            "user_id": event.get("user_id", ""),
            "reward": reward.get("title", ""),
            "reward_id": reward.get("id", ""),
            "reward_cost": reward.get("cost", 0),
            "redemption_id": event.get("id", ""),
            "user_input": event.get("user_input", ""),
            "amount": reward.get("cost", 0),
        }

    if event_type == "channel.chat.message":
        message = event.get("message", {})
        return {
            "trigger_type": "chat_command",
            "user": event.get("chatter_user_name", ""),
            "user_login": event.get("chatter_user_login", ""),
            "user_id": event.get("chatter_user_id", ""),
            "text": message.get("text", "") or "",
            "message_id": event.get("message_id", ""),
            "rank": _badge_rank(event.get("badges", [])),
        }

    if event_type == "channel.cheer":
        return {
            "trigger_type": "cheer",
            "user": event.get("user_name", "") or "Anonymous",
            "user_login": event.get("user_login", ""),
            "user_id": event.get("user_id", ""),
            "amount": event.get("bits", 0),
            "user_input": event.get("message", ""),
        }

    if event_type in {
        "channel.subscribe",
        "channel.subscription.gift",
        "channel.subscription.message",
    }:
        return {
            "trigger_type": "subscription",
            "user": event.get("user_name", "") or "Anonymous",
            "user_login": event.get("user_login", ""),
            "user_id": event.get("user_id", ""),
            "tier": event.get("tier", ""),
            "is_gift": bool(event.get("is_gift")) or event_type == "channel.subscription.gift",
            "is_resub": event_type == "channel.subscription.message",
            "amount": event.get("total", 1) if event_type == "channel.subscription.gift" else 1,
            "user_input": event.get("message", {}).get("text", "")
            if isinstance(event.get("message"), dict)
            else "",
        }

    if event_type == "channel.follow":
        return {
            "trigger_type": "follow",
            "user": event.get("user_name", ""),
            "user_login": event.get("user_login", ""),
            "user_id": event.get("user_id", ""),
        }

    if event_type == "channel.raid":
        return {
            "trigger_type": "raid",
            "user": event.get("from_broadcaster_user_name", ""),
            "user_login": event.get("from_broadcaster_user_login", ""),
            "user_id": event.get("from_broadcaster_user_id", ""),
            "amount": event.get("viewers", 0),
        }

    return None


def matches(trigger: Trigger, data: dict[str, Any]) -> bool:
    cfg = trigger.config or {}

    if trigger.type == "channel_points":
        reward_id = str(cfg.get("reward_id", "")).strip()
        reward_title = str(cfg.get("reward_title", "")).strip().lower()
        if reward_id:
            return data.get("reward_id") == reward_id
        if reward_title:
            return str(data.get("reward", "")).strip().lower() == reward_title
        # No reward chosen yet - do not fire on every redemption by accident.
        return False

    if trigger.type == "chat_command":
        command = str(cfg.get("command", "")).strip().lower()
        if not command:
            return False
        text = str(data.get("text", "")).strip()
        first = text.split(" ", 1)[0].lower() if text else ""
        if cfg.get("match_anywhere"):
            if command not in text.lower():
                return False
        elif first != command:
            return False
        required = str(cfg.get("permission", "everyone"))
        return int(data.get("rank", 0)) >= PERMISSION_RANK.get(required, 0)

    if trigger.type == "cheer":
        return int(data.get("amount", 0) or 0) >= int(cfg.get("min_bits", 1) or 1)

    if trigger.type == "subscription":
        if data.get("is_gift") and not cfg.get("include_gifts", True):
            return False
        if data.get("is_resub") and not cfg.get("include_resubs", True):
            return False
        tiers = cfg.get("tiers") or []
        return not tiers or str(data.get("tier", "")) in [str(t) for t in tiers]

    if trigger.type == "raid":
        return int(data.get("amount", 0) or 0) >= int(cfg.get("min_viewers", 1) or 1)

    return trigger.type == "follow"


def _cooldown_blocked(trigger: Trigger, data: dict[str, Any]) -> str:
    cfg = trigger.config or {}
    now = time.time()

    global_cd = float(cfg.get("cooldown_seconds", 0) or 0)
    if global_cd > 0:
        ready_at = _trigger_cooldowns.get(trigger.id, 0.0)
        if now < ready_at:
            return f"trigger cooldown ({ready_at - now:.0f}s left)"

    user_cd = float(cfg.get("user_cooldown_seconds", 0) or 0)
    user_key = (trigger.id, str(data.get("user_id") or data.get("user_login") or ""))
    if user_cd > 0 and user_key[1]:
        ready_at = _user_cooldowns.get(user_key, 0.0)
        if now < ready_at:
            return f"per-user cooldown ({ready_at - now:.0f}s left)"

    if global_cd > 0:
        _trigger_cooldowns[trigger.id] = now + global_cd
    if user_cd > 0 and user_key[1]:
        _user_cooldowns[user_key] = now + user_cd
    return ""


def _template_vars(data: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "user",
        "user_login",
        "user_id",
        "reward",
        "reward_id",
        "redemption_id",
        "user_input",
        "amount",
        "message_id",
        "tier",
        "text",
    )
    return {k: data[k] for k in keep if k in data}


async def handle_twitch_event(event_type: str, event: dict[str, Any]) -> None:
    data = normalise(event_type, event)
    if data is None:
        return

    await hub.broadcast_control(
        {
            "type": "twitch_event",
            "event_type": event_type,
            "summary": _summarise(data),
            "at": time.time(),
        }
    )

    trigger_type = data["trigger_type"]
    for wheel in db.list_wheels():
        if not wheel.enabled:
            continue
        for trigger in wheel.triggers:
            if not trigger.enabled or trigger.type != trigger_type:
                continue
            if not matches(trigger, data):
                continue
            await _fire(wheel, trigger, data)
            break  # one spin per wheel per event


async def _fire(wheel: Wheel, trigger: Trigger, data: dict[str, Any]) -> None:
    blocked = _cooldown_blocked(trigger, data)
    if blocked:
        await hub.broadcast_control(
            {
                "type": "trigger_skipped",
                "wheel_id": wheel.id,
                "wheel_name": wheel.name,
                "reason": blocked,
            }
        )
        return

    try:
        result = await engine.spin(
            wheel.id,
            source=trigger.type,
            variables=_template_vars(data),
        )
        log.info("Trigger %s spun '%s' -> %s", trigger.type, wheel.name, result["winner"])
    except SpinRejected as exc:
        log.info("Trigger %s could not spin '%s': %s", trigger.type, wheel.name, exc)
        await hub.broadcast_control(
            {
                "type": "trigger_skipped",
                "wheel_id": wheel.id,
                "wheel_name": wheel.name,
                "reason": str(exc),
            }
        )


def _summarise(data: dict[str, Any]) -> str:
    kind = data["trigger_type"]
    user = data.get("user", "someone")
    if kind == "channel_points":
        return f"{user} redeemed '{data.get('reward', '')}'"
    if kind == "chat_command":
        return f"{user}: {data.get('text', '')[:80]}"
    if kind == "cheer":
        return f"{user} cheered {data.get('amount', 0)} bits"
    if kind == "subscription":
        return f"{user} subscribed (tier {data.get('tier', '')})"
    if kind == "follow":
        return f"{user} followed"
    if kind == "raid":
        return f"{user} raided with {data.get('amount', 0)} viewers"
    return kind
