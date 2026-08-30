"""Test fixtures. Points WheelHat at a throwaway data directory before import."""

import os
import tempfile

# Must happen before anything imports wheelhat.config.
_TMP = tempfile.mkdtemp(prefix="wheelhat-tests-")
os.environ["WHEELHAT_DATA_DIR"] = _TMP

import pytest  # noqa: E402

from wheelhat import db  # noqa: E402
from wheelhat.models import Action, Slice, Wheel  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    db.connect()
    for wheel in db.list_wheels():
        db.delete_wheel(wheel.id)
    db.clear_spins()
    db.clear_action_log()
    yield
    for wheel in db.list_wheels():
        db.delete_wheel(wheel.id)


@pytest.fixture
def wheel() -> Wheel:
    made = Wheel(
        name="Test wheel",
        slices=[
            Slice(id="sl_a", label="Alpha", weight=1),
            Slice(id="sl_b", label="Beta", weight=3),
            Slice(id="sl_c", label="Gamma", weight=1, enabled=False),
        ],
    )
    return db.save_wheel(made)


@pytest.fixture
def action_wheel() -> Wheel:
    made = Wheel(
        name="Action wheel",
        slices=[
            Slice(
                id="sl_only",
                label="Only",
                actions=[Action(id="act_1", type="delay", config={"seconds": 0})],
            )
        ],
    )
    made.spin.duration_ms = 500
    made.spin.action_delay_ms = 0
    return db.save_wheel(made)
