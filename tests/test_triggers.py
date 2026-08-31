"""Turning Twitch events into spins: normalisation, matching and cooldowns."""


import pytest

from wheelhat import db, triggers
from wheelhat.engine import engine
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
    await engine.wait_for(wheel.id)
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
    await engine.wait_for(wheel.id)
    assert db.list_spins(wheel.id) == []


async def test_disabled_trigger_does_not_spin():
    wheel = make_wheel(
        Trigger(type="channel_points", enabled=False, config={"reward_id": "r-1"})
    )
    await triggers.handle_twitch_event(REDEEM, redemption())
    await engine.wait_for(wheel.id)
    assert db.list_spins(wheel.id) == []


async def test_two_wheels_can_watch_different_rewards():
    first = make_wheel(Trigger(type="channel_points", config={"reward_id": "r-1"}), "First")
    second = make_wheel(Trigger(type="channel_points", config={"reward_id": "r-2"}), "Second")

    await triggers.handle_twitch_event(REDEEM, redemption(reward_id="r-2"))
    await engine.wait_for(second.id)

    assert db.list_spins(first.id) == []
    assert len(db.list_spins(second.id)) == 1


async def test_trigger_cooldown_blocks_the_second_event():
    wheel = make_wheel(
        Trigger(type="channel_points", config={"reward_id": "r-1", "cooldown_seconds": 300})
    )
    await triggers.handle_twitch_event(REDEEM, redemption())
    await engine.wait_for(wheel.id)
    await triggers.handle_twitch_event(REDEEM, redemption())
    await engine.wait_for(wheel.id)
    assert len(db.list_spins(wheel.id)) == 1


async def test_per_user_cooldown_still_lets_another_viewer_through():
    wheel = make_wheel(
        Trigger(type="channel_points", config={"reward_id": "r-1", "user_cooldown_seconds": 300})
    )
    await triggers.handle_twitch_event(REDEEM, redemption(user="Ann"))
    await engine.wait_for(wheel.id)

    second = redemption(user="Ann")
    await triggers.handle_twitch_event(REDEEM, second)
    await engine.wait_for(wheel.id)
    assert len(db.list_spins(wheel.id)) == 1

    other = redemption(user="Bob")
    other["user_id"] = "99"
    await triggers.handle_twitch_event(REDEEM, other)
    await engine.wait_for(wheel.id)
    assert len(db.list_spins(wheel.id)) == 2


# ------------------------------------------------------- closing redemptions


class FakeTwitch:
    """Records what would have been sent to Twitch."""

    def __init__(self, succeeds: bool = True) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.succeeds = succeeds

    async def close_redemption(self, reward_id: str, redemption_id: str, fulfilled: bool) -> bool:
        self.calls.append((reward_id, redemption_id, fulfilled))
        return self.succeeds


def redemption_data():
    return {
        "trigger_type": "channel_points",
        "reward_id": "r-1",
        "redemption_id": "red-9",
        "reward": "Spin the wheel",
        "user": "Viewer",
    }


async def test_a_spin_fulfils_the_redemption_when_asked(monkeypatch):
    """Otherwise every spin leaves an entry in the streamer's queue to clear."""
    fake = FakeTwitch()
    monkeypatch.setattr("wheelhat.twitch.service.twitch", fake)
    trigger = Trigger(type="channel_points", config={"reward_id": "r-1", "auto_close": True})

    await triggers._close_redemption(trigger, redemption_data(), fulfilled=True)
    assert fake.calls == [("r-1", "red-9", True)]


async def test_a_blocked_spin_refunds_the_viewer(monkeypatch):
    """They paid for a spin that never happened."""
    fake = FakeTwitch()
    monkeypatch.setattr("wheelhat.twitch.service.twitch", fake)
    trigger = Trigger(type="channel_points", config={"reward_id": "r-1", "auto_close": True})

    await triggers._close_redemption(trigger, redemption_data(), fulfilled=False)
    assert fake.calls == [("r-1", "red-9", False)]


async def test_refunds_can_be_turned_off_while_fulfilment_stays_on(monkeypatch):
    fake = FakeTwitch()
    monkeypatch.setattr("wheelhat.twitch.service.twitch", fake)
    trigger = Trigger(
        type="channel_points",
        config={"reward_id": "r-1", "auto_close": True, "refund_on_failure": False},
    )

    await triggers._close_redemption(trigger, redemption_data(), fulfilled=False)
    assert fake.calls == []
    await triggers._close_redemption(trigger, redemption_data(), fulfilled=True)
    assert fake.calls == [("r-1", "red-9", True)]


async def test_nothing_is_closed_unless_the_trigger_asks(monkeypatch):
    """Off by default: closing someone's redemptions uninvited is not ours to do."""
    fake = FakeTwitch()
    monkeypatch.setattr("wheelhat.twitch.service.twitch", fake)
    trigger = Trigger(type="channel_points", config={"reward_id": "r-1"})

    await triggers._close_redemption(trigger, redemption_data(), fulfilled=True)
    assert fake.calls == []


async def test_only_channel_point_triggers_close_anything(monkeypatch):
    """A cheer or a follow has no redemption behind it."""
    fake = FakeTwitch()
    monkeypatch.setattr("wheelhat.twitch.service.twitch", fake)
    trigger = Trigger(type="cheer", config={"auto_close": True})

    await triggers._close_redemption(trigger, redemption_data(), fulfilled=True)
    assert fake.calls == []


# ------------------------------------------------- eventsub connection policy


class FakeEventSub:
    """Tracks start/stop without opening a socket."""

    def __init__(self) -> None:
        self.session_id = ""
        self.running = False
        self.starts = 0
        self.stops = 0

    async def start(self) -> None:
        self.starts += 1
        self.running = True

    async def stop(self) -> None:
        self.stops += 1
        self.running = False
        self.session_id = ""


async def test_no_eventsub_socket_when_nothing_listens(monkeypatch):
    """Twitch closes a socket with no subscriptions after ~10s, code 4003.

    Connecting with no triggers configured does not idle - it becomes a
    permanent connect/drop/retry loop against Twitch.
    """
    from wheelhat.twitch.service import TwitchService

    service = TwitchService()
    fake = FakeEventSub()
    service._eventsub = fake
    service.tokens.user_id = "42"

    # A wheel with no triggers at all.
    db.save_wheel(Wheel(name="Quiet", slices=[Slice(id="sl_1", label="One")]))
    assert service.needed_subscription_types() == []

    await service.sync_eventsub()
    assert fake.starts == 0, "should not open a socket with nothing to subscribe to"


async def test_the_socket_opens_once_a_trigger_needs_it(monkeypatch):
    from wheelhat.twitch.service import TwitchService

    service = TwitchService()
    fake = FakeEventSub()
    service._eventsub = fake
    service.tokens.user_id = "42"

    db.save_wheel(
        Wheel(
            name="Listening",
            slices=[Slice(id="sl_1", label="One")],
            triggers=[Trigger(type="channel_points", config={"reward_id": "r-1"})],
        )
    )
    assert service.needed_subscription_types() != []

    await service.sync_eventsub()
    assert fake.starts == 1


async def test_the_socket_closes_when_the_last_trigger_goes(monkeypatch):
    """Otherwise removing your only trigger leaves the 4003 loop running."""
    from wheelhat.twitch.service import TwitchService

    service = TwitchService()
    fake = FakeEventSub()
    service._eventsub = fake
    service.tokens.user_id = "42"

    wheel = db.save_wheel(
        Wheel(
            name="Listening",
            slices=[Slice(id="sl_1", label="One")],
            triggers=[Trigger(type="channel_points", config={"reward_id": "r-1"})],
        )
    )
    await service.sync_eventsub()
    assert fake.running is True

    wheel.triggers = []
    db.save_wheel(wheel)
    await service.sync_eventsub()
    assert fake.stops == 1
    assert fake.running is False


async def test_a_disabled_trigger_does_not_hold_the_socket_open(monkeypatch):
    from wheelhat.twitch.service import TwitchService

    service = TwitchService()
    fake = FakeEventSub()
    service._eventsub = fake
    service.tokens.user_id = "42"

    db.save_wheel(
        Wheel(
            name="Off",
            slices=[Slice(id="sl_1", label="One")],
            triggers=[Trigger(type="channel_points", enabled=False, config={"reward_id": "r-1"})],
        )
    )
    await service.sync_eventsub()
    assert fake.starts == 0
