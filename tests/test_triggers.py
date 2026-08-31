"""Turning Twitch events into spins: normalisation, matching and cooldowns."""


import time

import pytest

from wheelhat import config, db, triggers
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


def signed_in_service():
    from wheelhat.twitch.service import TwitchService

    service = TwitchService()
    service._eventsub = FakeEventSub()
    service.tokens.access_token = "tok"
    service.tokens.user_id = "42"
    service.tokens.scopes = list(config.TWITCH_SCOPES)
    return service


async def test_signed_out_holds_no_socket():
    service = signed_in_service()
    service.tokens.access_token = ""

    await service.sync_eventsub()
    assert service._eventsub.starts == 0


async def test_signing_in_connects_even_with_no_triggers():
    """Otherwise the Twitch page reads "disconnected" straight after a
    successful sign-in, which looks like the sign-in failed."""
    service = signed_in_service()
    db.save_wheel(Wheel(name="Quiet", slices=[Slice(id="sl_1", label="One")]))
    assert service.needed_subscription_types() == []

    await service.sync_eventsub()
    assert service._eventsub.starts == 1


async def test_the_baseline_always_gives_the_socket_something_to_do():
    """Twitch closes a socket with no subscriptions (code 4003), so the
    baseline is what stops the connect/drop/retry loop."""
    service = signed_in_service()
    db.save_wheel(Wheel(name="Quiet", slices=[Slice(id="sl_1", label="One")]))

    assert service.all_subscription_types(), "a signed-in client must always want something"


async def test_the_baseline_survives_a_channel_without_channel_points():
    """Channel points need affiliate status. If that were the only baseline, a
    non-affiliate would be back to an unused socket and the 4003 loop."""
    from wheelhat.twitch.service import BASELINE_SUBSCRIPTIONS, SUBSCRIPTION_SPECS

    unscoped = [s for s in BASELINE_SUBSCRIPTIONS if not SUBSCRIPTION_SPECS[s]["scope"]]
    assert unscoped, "at least one baseline subscription must need no scope and no affiliate status"


async def test_a_trigger_adds_its_topic_on_top_of_the_baseline():
    service = signed_in_service()
    db.save_wheel(
        Wheel(
            name="Listening",
            slices=[Slice(id="sl_1", label="One")],
            triggers=[Trigger(type="cheer", config={})],
        )
    )

    assert "channel.cheer" in service.all_subscription_types()
    await service.sync_eventsub()
    assert service._eventsub.starts == 1


async def test_subscriptions_report_which_are_always_on(monkeypatch):
    """The card separates the two, so the payload has to distinguish them.

    Channel point redemptions are subscribed whether or not a wheel uses them,
    so presenting everything as "added by your wheels" would be untrue.
    """
    from wheelhat.twitch.service import TwitchService

    service = TwitchService()
    service.tokens.access_token = "tok"
    service.tokens.user_id = "42"
    service.tokens.broadcaster_type = "affiliate"
    service.tokens.scopes = list(config.TWITCH_SCOPES)

    async def fake_helix(self, method, path, *, params=None, json_body=None, _retried=False):
        return {"data": []}

    monkeypatch.setattr(TwitchService, "helix", fake_helix)
    monkeypatch.setattr(TwitchService, "broadcast_status", lambda self: _noop())

    db.save_wheel(
        Wheel(
            name="Cheers",
            slices=[Slice(id="sl_1", label="One")],
            triggers=[Trigger(type="cheer", config={})],
        )
    )

    await service.subscribe_all("session-1")
    by_type = {s["type"]: s for s in service.subscriptions}

    assert by_type["stream.online"]["baseline"] is True
    assert by_type["channel.channel_points_custom_reward_redemption.add"]["baseline"] is True
    assert by_type["channel.cheer"]["baseline"] is False, "a wheel asked for this one"


async def _noop():
    return None


# ------------------------------------------------------ identifying a reward


def redemption_event(reward_id="r-7", title="Spin the wheel", cost=500):
    return {
        "id": "redemption-1",
        "user_id": "42",
        "user_login": "viewer",
        "user_name": "Viewer",
        "user_input": "",
        "reward": {"id": reward_id, "title": title, "cost": cost},
        "redeemed_at": "2026-08-31T12:00:00Z",
    }


async def test_a_listen_identifies_the_next_reward_redeemed():
    """So nobody has to go and find a reward id, including for rewards created
    on Twitch itself, which WheelHat cannot create but can still see."""
    from wheelhat.twitch.service import twitch

    await twitch.start_reward_capture(60)
    assert twitch.capture_state()["listening"] is True

    await triggers.handle_twitch_event(REDEEM, redemption_event())

    captured = twitch.capture_state()["reward"]
    assert captured == {"id": "r-7", "title": "Spin the wheel", "cost": 500}
    assert twitch.capture_state()["listening"] is False, "one reward is the point"


async def test_nothing_is_recorded_unless_a_listen_is_armed():
    """WheelHat sees every redemption because of the baseline subscription.
    Remembering them without being asked is the thing not to do."""
    from wheelhat.twitch.service import twitch

    await twitch.stop_reward_capture()
    await triggers.handle_twitch_event(REDEEM, redemption_event())
    assert twitch.capture_state()["reward"] is None


async def test_a_listen_expires_on_its_own():
    """It cannot be left armed and forgotten."""
    from wheelhat.twitch.service import twitch

    await twitch.start_reward_capture(60)
    twitch._capture_until = time.time() - 1  # as if the window had passed

    await triggers.handle_twitch_event(REDEEM, redemption_event())
    assert twitch.capture_state()["reward"] is None
    assert twitch.capture_state()["listening"] is False


async def test_the_listen_window_is_bounded():
    """A caller cannot arm it for a week."""
    from wheelhat.twitch.service import twitch

    state = await twitch.start_reward_capture(99999)
    assert state["expires_in_ms"] <= 300_000
    await twitch.stop_reward_capture()


async def test_only_the_reward_is_kept_never_the_viewer():
    """The viewer's name and id are in the event and must not be retained."""
    from wheelhat.twitch.service import twitch

    await twitch.start_reward_capture(60)
    await triggers.handle_twitch_event(REDEEM, redemption_event())

    kept = twitch.capture_state()["reward"]
    assert set(kept) == {"id", "title", "cost"}
    assert "Viewer" not in str(kept)
    assert "42" not in str(kept.get("id", "")) + str(kept.get("title", ""))
