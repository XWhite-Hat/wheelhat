"""Spin engine: selection fairness, cooldowns, eliminations, concurrency."""

from collections import Counter

import pytest

from wheelhat import db
from wheelhat.engine import SpinEngine, SpinRejected, render_payload
from wheelhat.models import Slice, SliceImage, Wheel


def test_pick_respects_weights(wheel):
    engine = SpinEngine()
    counts = Counter(engine.pick(wheel).label for _ in range(4000))
    # Beta has three times Alpha's weight; Gamma is disabled and must never win.
    assert "Gamma" not in counts
    ratio = counts["Beta"] / counts["Alpha"]
    assert 2.4 < ratio < 3.6, counts


def test_pick_rejects_when_nothing_can_win():
    empty = db.save_wheel(Wheel(name="Empty", slices=[Slice(label="x", enabled=False)]))
    with pytest.raises(SpinRejected, match="no slices that can win"):
        SpinEngine().pick(empty)


def test_render_payload_only_includes_spinnable(wheel):
    payload = render_payload(wheel)
    assert [s["label"] for s in payload["slices"]] == ["Alpha", "Beta"]
    assert payload["appearance"]["palette"]


async def test_spin_resolves_and_records_history(action_wheel):
    engine = SpinEngine()
    result = await engine.spin(action_wheel.id, source="manual")
    assert result["winner"] == "Only"
    assert engine.is_spinning(action_wheel.id)

    await engine.wait_for(action_wheel.id)
    assert not engine.is_spinning(action_wheel.id)

    spins = db.list_spins(action_wheel.id)
    assert len(spins) == 1
    assert spins[0].label == "Only"


async def test_second_spin_rejected_while_first_is_running(action_wheel):
    engine = SpinEngine()
    await engine.spin(action_wheel.id)
    with pytest.raises(SpinRejected, match="already spinning"):
        await engine.spin(action_wheel.id)
    await engine.cancel(action_wheel.id)


async def test_cooldown_blocks_triggered_spins(action_wheel):
    action_wheel.spin.cooldown_seconds = 60
    db.save_wheel(action_wheel)

    engine = SpinEngine()
    await engine.spin(action_wheel.id)
    await engine.wait_for(action_wheel.id)

    with pytest.raises(SpinRejected, match="cooldown"):
        await engine.spin(action_wheel.id, source="channel_points")

    # The control panel deliberately bypasses the cooldown.
    result = await engine.spin(action_wheel.id, ignore_cooldown=True)
    assert result["winner"] == "Only"
    await engine.cancel(action_wheel.id)


async def test_remove_on_win_disables_the_slice(action_wheel):
    stored = db.get_wheel(action_wheel.id)
    stored.slices[0].remove_on_win = True
    db.save_wheel(stored)

    engine = SpinEngine()
    await engine.spin(action_wheel.id)
    await engine.wait_for(action_wheel.id)

    after = db.get_wheel(action_wheel.id)
    assert after.slices[0].enabled is False
    assert after.slices[0].won_count == 1


async def test_slice_cooldown_counts_down_over_spins():
    made = db.save_wheel(
        Wheel(
            name="Cooldowns",
            slices=[
                Slice(id="sl_1", label="One", cooldown_spins=2),
                Slice(id="sl_2", label="Two"),
            ],
        )
    )
    made.spin.duration_ms = 200
    made.spin.action_delay_ms = 0
    db.save_wheel(made)

    engine = SpinEngine()
    await engine.spin(made.id, force_slice_id="sl_1")
    await engine.wait_for(made.id)

    after = db.get_wheel(made.id)
    one = after.slice_by_id("sl_1")
    assert one.cooldown_remaining == 2
    assert not one.is_spinnable()

    # A later spin ticks it down.
    await engine.spin(made.id, force_slice_id="sl_2")
    await engine.wait_for(made.id)
    assert db.get_wheel(made.id).slice_by_id("sl_1").cooldown_remaining == 1


async def test_force_slice_wins_even_when_on_cooldown(action_wheel):
    engine = SpinEngine()
    result = await engine.spin(action_wheel.id, force_slice_id="sl_only")
    assert result["winner_id"] == "sl_only"
    await engine.cancel(action_wheel.id)


async def test_skip_actions_still_records_the_spin(action_wheel):
    engine = SpinEngine()
    await engine.spin(action_wheel.id, skip_actions=True)
    await engine.wait_for(action_wheel.id)
    assert db.list_spins(action_wheel.id)
    assert not db.list_action_log(10)


async def test_render_payload_carries_slice_images():
    """Overlays need each slice's image config, not just its colour."""
    made = db.save_wheel(
        Wheel(
            name="Imagery",
            slices=[
                Slice(
                    id="sl_pic",
                    label="With art",
                    image=SliceImage(url="/assets/logo.png", size=0.4, rotate_with_wheel=False),
                )
            ],
        )
    )
    payload = render_payload(made)
    image = payload["slices"][0]["image"]
    assert image["url"] == "/assets/logo.png"
    assert image["size"] == 0.4
    assert image["rotate_with_wheel"] is False


async def test_appearance_image_layers_round_trip():
    made = db.save_wheel(Wheel(name="Framed", slices=[Slice(label="One")]))
    made.appearance.frame_image.url = "/assets/bezel.png"
    made.appearance.frame_image.opacity = 0.8
    made.appearance.background_image.url = "/assets/bg.jpg"
    made.appearance.text_curved = True
    made.appearance.inner_radius = 0.3
    db.save_wheel(made)

    stored = db.get_wheel(made.id)
    assert stored.appearance.frame_image.url == "/assets/bezel.png"
    assert stored.appearance.frame_image.opacity == 0.8
    assert stored.appearance.background_image.url == "/assets/bg.jpg"
    assert stored.appearance.text_curved is True
    assert stored.appearance.inner_radius == 0.3


def test_image_layer_is_only_drawable_when_usable():
    assert not SliceImage().is_drawable()
    assert not SliceImage(url="/a.png", enabled=False).is_drawable()
    assert not SliceImage(url="/a.png", opacity=0).is_drawable()
    assert SliceImage(url="/a.png").is_drawable()


async def test_a_wheel_saved_before_images_still_loads():
    """Documents written by an older version have no image key at all."""
    legacy = {
        "id": "whl_legacy",
        "name": "Old",
        "slices": [{"id": "sl_1", "label": "One", "weight": 1}],
        "appearance": {"palette": ["#ffffff"]},
    }
    wheel = Wheel(**legacy)
    assert wheel.slices[0].image.url == ""
    assert wheel.appearance.frame_image.url == ""
    assert wheel.appearance.text_radial == 0.94
    db.save_wheel(wheel)
    assert db.get_wheel("whl_legacy").slices[0].image.is_drawable() is False


async def test_resync_tells_a_reconnecting_overlay_which_slice_won():
    """A source that connects mid-spin has to be able to move the wheel.

    The payload used to carry only the winner's label, so an overlay that
    reloaded during a spin showed the result banner while the wheel stayed at
    rest - the pointer sat on whatever slice happened to be at the top, which
    reads as the wheel contradicting its own result.
    """
    from wheelhat.api.ws import resync_payload

    made = db.save_wheel(
        Wheel(
            name="Resync",
            slices=[Slice(id="sl_1", label="One"), Slice(id="sl_2", label="Two")],
        )
    )
    engine = SpinEngine()
    try:
        await engine.spin(made.id, force_slice_id="sl_2")
        active = engine.active_spin(made.id)
        assert active is not None

        payload = resync_payload(active)
        assert payload["winner_id"] == "sl_2"
        assert payload["winner"] == "Two"
        # The id has to match a slice the overlay actually draws, or it cannot
        # find the wedge to stop on.
        drawn = [s["id"] for s in render_payload(db.get_wheel(made.id))["slices"]]
        assert payload["winner_id"] in drawn
    finally:
        await engine.cancel(made.id)


async def test_resync_reports_time_left_on_the_wheel_not_on_the_actions():
    """A reconnecting source animates the rest of the spin, so it needs the
    moment the wheel stops - not the moment the actions finish.

    ends_at deliberately includes action_delay_ms. Animating to that would keep
    the wheel turning after it should have come to rest on the winner.
    """
    from wheelhat.api.ws import resync_payload

    made = db.save_wheel(Wheel(name="Timing", slices=[Slice(id="sl_1", label="One")]))
    made.spin.duration_ms = 4000
    made.spin.action_delay_ms = 3000
    db.save_wheel(made)

    engine = SpinEngine()
    try:
        await engine.spin(made.id, force_slice_id="sl_1")
        payload = resync_payload(engine.active_spin(made.id))

        # The wheel stops well before the actions do.
        assert 3000 < payload["stops_in_ms"] <= 4000
        assert 6000 < payload["ends_in_ms"] <= 7000
        assert payload["stops_in_ms"] < payload["ends_in_ms"]
    finally:
        await engine.cancel(made.id)
