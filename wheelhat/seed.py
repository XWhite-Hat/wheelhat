"""First-run content so the app is not an empty screen."""

from __future__ import annotations

from . import db
from .models import Action, Appearance, Slice, SpinSettings, Trigger, Wheel

STARTER_SLICES = [
    ("Nothing happens", "#46a758", []),
    ("Swap to the cursed outfit", "#8e4ec6", [("vts_hotkey", "Trigger the outfit hotkey")]),
    ("Zoom in for 30 seconds", "#0091ff", [("obs_scene", "Switch to a close-up scene")]),
    ("Chat picks the next game", "#ffb224", [("twitch_chat", "Announce it in chat")]),
    ("Airhorn", "#f76b15", [("overlay_sound", "Play a sound on the overlay")]),
    ("Buzz the phone", "#e5484d", [("pushover", "Send a push notification")]),
    ("Tiny mode", "#12a594", [("vts_move", "Shrink the avatar")]),
    ("Double or nothing", "#e93d82", []),
]


def ensure_starter_wheel() -> None:
    if db.list_wheels():
        return

    wheel = Wheel(
        name="Punishment Wheel",
        description="A starter wheel. Every slice already has an example action wired up "
        "but switched off - open one, point it at your own setup, then enable it.",
        appearance=Appearance(hub_label="SPIN"),
        spin=SpinSettings(duration_ms=6500, cooldown_seconds=0),
        triggers=[
            Trigger(
                type="channel_points",
                enabled=False,
                config={"reward_id": "", "reward_title": "Spin the wheel"},
            )
        ],
    )

    for label, color, action_specs in STARTER_SLICES:
        slice_actions = [
            Action(type=action_type, name=name, enabled=False)
            for action_type, name in action_specs
        ]
        wheel.slices.append(Slice(label=label, color=color, actions=slice_actions))

    db.save_wheel(wheel)
