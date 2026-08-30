"""Turning Twitch events into spins: normalisation, matching and cooldowns."""

import asyncio

import pytest

from wheelhat import db, triggers
from wheelhat.models import Slice, Trigger, Wheel

REDEEM = "channel.channel_points_custom_reward_redemption.add"


def redemption(reward_id="r-1", title="Spin the wheel", user="Viewer"):
    return {
        "id": "redemption-1",
        "user_id": "42",
        "user_login": user.lower(),
        "user_name": user,
        "user_input": "hello",
        "reward": {"id": reward_id, "title": title, "cost": 500},
    }


@pytest.fixture(autouse=True)
def reset_cooldowns():
    triggers._trigger_cooldowns.clear()
    triggers._user_cooldowns.clear()


def make_wheel(trigger: Trigger, name="Triggered") -> Wheel:
    wheel = Wheel(
        name=name,
        slices=[Slice(id="sl_1", label="Only")],
        triggers=[trigger],
    )
    wheel.spin.duration_ms = 200
    wheel.spin.action_delay_ms = 0
    return db.save_wheel(wheel)


# ------------------------------------------------------------- normalisation


def test_redemption_normalises_to_template_variables():
    data = triggers.normalise(REDEEM, redemption())
    assert data["trigger_type"] == "channel_points"
    assert data["user"] == "Viewer"
    assert data["reward"] == "Spin the wheel"
    assert data["redemption_id"] == "redemption-1"
    assert data["user_input"] == "hello"


def test_chat_message_normalises_badges_to_a_rank():
    data = triggers.normalise(
        "channel.chat.message",
        {
            "chatter_user_name": "Mod",
            "chatter_user_login": "mod",
            "chatter_user_id": "7",
            "message": {"text": "!spin now"},
            "message_id": "m-1",
            "badges": [{"set_id": "moderator", "id": "1"}],
        },
    )
    assert data["rank"] == triggers.PERMISSION_RANK["moderator"]
    assert data["text"] == "!spin now"


def test_unknown_event_type_is_ignored():
    assert triggers.normalise("channel.ban", {}) is None


def test_raid_and_cheer_carry_an_amount():
    assert triggers.normalise("channel.raid", {"viewers": 30})["amount"] == 30
    assert triggers.normalise("channel.cheer", {"bits": 400})["amount"] == 400


# ----------------------------------------------------------------- matching


def test_channel_points_matches_by_id():
    trigger = Trigger(type="channel_points", config={"reward_id": "r-1"})
    assert triggers.matches(trigger, triggers.normalise(REDEEM, redemption()))
    assert not triggers.matches(
        trigger, triggers.normalise(REDEEM, redemption(reward_id="other"))
    )


def test_channel_points_falls_back_to_title():
    trigger = Trigger(type="channel_points", config={"reward_title": "spin the WHEEL"})
    assert triggers.matches(trigger, triggers.normalise(REDEEM, redemption(reward_id="")))


def test_unconfigured_channel_points_trigger_never_fires():
    """Otherwise every redemption on the channel would spin the wheel."""
    trigger = Trigger(type="channel_points", config={})
    assert not triggers.matches(trigger, triggers.normalise(REDEEM, redemption()))


def test_chat_command_requires_the_first_word():
    trigger = Trigger(type="chat_command", config={"command": "!spin"})
    make = lambda text: {"trigger_type": "chat_command", "text": text, "rank": 0}  # noqa: E731
    assert triggers.matches(trigger, make("!spin"))
    assert triggers.matches(trigger, make("!spin please"))
    assert not triggers.matches(trigger, make("please !spin"))


def test_chat_command_can_match_anywhere():
    trigger = Trigger(type="chat_command", config={"command": "!spin", "match_anywhere": True})
    assert triggers.matches(trigger, {"trigger_type": "chat_command", "text": "hey !spin", "rank": 0})


def test_chat_command_permission_gate():
    trigger = Trigger(type="chat_command", config={"command": "!spin", "permission": "moderator"})
    viewer = {"trigger_type": "chat_command", "text": "!spin", "rank": 0}
    mod = {"trigger_type": "chat_command", "text": "!spin", "rank": triggers.PERMISSION_RANK["moderator"]}
    assert not triggers.matches(trigger, viewer)
    assert triggers.matches(trigger, mod)


def test_cheer_threshold():
    trigger = Trigger(type="cheer", config={"min_bits": 500})
    assert not triggers.matches(trigger, {"trigger_type": "cheer", "amount": 100})
    assert triggers.matches(trigger, {"trigger_type": "cheer", "amount": 500})


def test_subscription_filters():
    trigger = Trigger(type="subscription", config={"include_gifts": False})
    assert not triggers.matches(trigger, {"trigger_type": "subscription", "is_gift": True})
    assert triggers.matches(trigger, {"trigger_type": "subscription", "is_gift": False})


def test_raid_threshold():
    trigger = Trigger(type="raid", config={"min_viewers": 10})
    assert not triggers.matches(trigger, {"trigger_type": "raid", "amount": 3})
    assert triggers.matches(trigger, {"trigger_type": "raid", "amount": 11})


# ------------------------------------------------------------------ dispatch


async def test_matching_event_spins_the_wheel():
    wheel = make_wheel(Trigger(type="channel_points", config={"reward_id": "r-1"}))
    await triggers.handle_twitch_event(REDEEM, redemption())
    await asyncio.sleep(0.9)
    spins = db.list_spins(wheel.id)
    assert len(spins) == 1
    assert spins[0].source == "channel_points"
    assert spins[0].actor == "Viewer"


async def test_disabled_wheel_does_not_spin():
    wheel = make_wheel(Trigger(type="channel_points", config={"reward_id": "r-1"}))
    stored = db.get_wheel(wheel.id)
    stored.enabled = False
    db.save_wheel(stored)

    await triggers.handle_twitch_event(REDEEM, redemption())
    await asyncio.sleep(0.9)
    assert db.list_spins(wheel.id) == []


async def test_disabled_trigger_does_not_spin():
    wheel = make_wheel(
        Trigger(type="channel_points", enabled=False, config={"reward_id": "r-1"})
    )
    await triggers.handle_twitch_event(REDEEM, redemption())
    await asyncio.sleep(0.9)
    assert db.list_spins(wheel.id) == []


async def test_two_wheels_can_watch_different_rewards():
    first = make_wheel(Trigger(type="channel_points", config={"reward_id": "r-1"}), "First")
    second = make_wheel(Trigger(type="channel_points", config={"reward_id": "r-2"}), "Second")

    await triggers.handle_twitch_event(REDEEM, redemption(reward_id="r-2"))
    await asyncio.sleep(0.9)

    assert db.list_spins(first.id) == []
    assert len(db.list_spins(second.id)) == 1


async def test_trigger_cooldown_blocks_the_second_event():
    wheel = make_wheel(
        Trigger(type="channel_points", config={"reward_id": "r-1", "cooldown_seconds": 300})
    )
    await triggers.handle_twitch_event(REDEEM, redemption())
    await asyncio.sleep(0.9)
    await triggers.handle_twitch_event(REDEEM, redemption())
    await asyncio.sleep(0.9)
    assert len(db.list_spins(wheel.id)) == 1


async def test_per_user_cooldown_still_lets_another_viewer_through():
    wheel = make_wheel(
        Trigger(type="channel_points", config={"reward_id": "r-1", "user_cooldown_seconds": 300})
    )
    await triggers.handle_twitch_event(REDEEM, redemption(user="Ann"))
    await asyncio.sleep(0.9)

    second = redemption(user="Ann")
    await triggers.handle_twitch_event(REDEEM, second)
    await asyncio.sleep(0.6)
    assert len(db.list_spins(wheel.id)) == 1

    other = redemption(user="Bob")
    other["user_id"] = "99"
    await triggers.handle_twitch_event(REDEEM, other)
    await asyncio.sleep(0.9)
    assert len(db.list_spins(wheel.id)) == 2
