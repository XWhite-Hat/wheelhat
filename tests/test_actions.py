"""Template rendering, the action schema registry and the chain executor."""

import pytest

from wheelhat.actions.executor import ActionFailed, ExecContext, execute_chain, execute_single
from wheelhat.actions.schema import ACTION_TYPES, schema_payload


def ctx(**kwargs):
    base: dict = dict(  # noqa: C408 - keyword form reads better for a fixture
        wheel_id="whl_1",
        wheel_name="Punishment",
        slice_id="sl_1",
        winner='The "cursed" outfit',
        source="channel_points",
        variables={"user": "Streamer", "reward": "Spin", "amount": 500},
    )
    base.update(kwargs)
    return ExecContext(**base)


def test_render_substitutes_known_variables():
    assert ctx().render("{{user}} spun {{wheel}}") == "Streamer spun Punishment"


def test_render_blanks_unknown_variables():
    assert ctx().render("[{{nope}}]") == "[]"


def test_json_filter_escapes_quotes():
    body = ctx().render('{"winner": "{{winner|json}}"}')
    import json

    assert json.loads(body)["winner"] == 'The "cursed" outfit'


def test_other_filters():
    context = ctx()
    assert context.render("{{user|upper}}") == "STREAMER"
    assert context.render("{{winner|url}}") == "The%20%22cursed%22%20outfit"
    assert context.render("{{wheel|slug}}") == "punishment"


def test_render_leaves_plain_text_alone():
    assert ctx().render("no placeholders here") == "no placeholders here"


def test_every_registered_type_has_a_handler_and_serialises():
    payload = schema_payload()
    assert len(payload["types"]) == len(ACTION_TYPES)
    for spec in ACTION_TYPES.values():
        assert spec.handler is not None
        assert spec.group in payload["groups"], spec.type
    for entry in payload["types"]:
        for field in entry["fields"]:
            assert "key" in field and "type" in field


async def test_unknown_action_type_fails_clearly():
    with pytest.raises(ActionFailed, match="Unknown action type"):
        await execute_single({"type": "nope", "config": {}}, ctx())


async def test_delay_action_runs():
    detail = await execute_single({"type": "delay", "config": {"seconds": 0}}, ctx())
    assert "Waited" in detail


async def test_dry_run_does_not_call_out():
    detail = await execute_single(
        {"type": "http_request", "config": {"url": "http://127.0.0.1:1/never"}},
        ctx(dry_run=True),
    )
    assert detail.startswith("[dry run]")


async def test_http_request_reports_a_connection_failure():
    with pytest.raises(ActionFailed, match="failed"):
        await execute_single(
            {
                "type": "http_request",
                "config": {"url": "http://127.0.0.1:1/nothing", "body_type": "none", "timeout": 1},
            },
            ctx(),
        )


async def test_invalid_json_body_names_the_field():
    with pytest.raises(ActionFailed, match="Payload is not valid JSON"):
        await execute_single(
            {
                "type": "http_request",
                "config": {"url": "http://127.0.0.1:9/x", "body_type": "json", "body": "{oops"},
            },
            ctx(),
        )


async def test_chain_continues_past_a_failing_action():
    results = await execute_chain(
        [
            {"id": "a1", "type": "http_request", "enabled": True, "config": {"url": ""}},
            {"id": "a2", "type": "delay", "enabled": True, "config": {"seconds": 0}},
        ],
        ctx(),
    )
    assert [r["ok"] for r in results] == [False, True]
    assert "No URL set" in results[0]["detail"]


async def test_chain_skips_disabled_actions():
    results = await execute_chain(
        [{"id": "a1", "type": "delay", "enabled": False, "config": {"seconds": 0}}], ctx()
    )
    assert results == []


async def test_shell_action_is_refused_until_enabled():
    with pytest.raises(ActionFailed, match="Shell actions are disabled"):
        await execute_single({"type": "shell_command", "config": {"command": "cmd"}}, ctx())


# The action picker hides a group when the app it needs is not connected. That
# gate reads `requires`, so an action that forgets to declare it would be
# offered on a machine that cannot run it - the exact failure the gate removes.
GROUP_REQUIREMENTS = {
    "OBS Studio": "obs",
    "VTube Studio": "vtube_studio",
    "Streamer.bot": "streamer_bot",
    "Mix It Up": "mix_it_up",
    "Speaker.bot": "speaker_bot",
    "SAMMI": "sammi",
    "VNyan": "vnyan",
    "Twitch": "twitch",
}


def test_every_action_declares_the_app_it_needs():
    from wheelhat.actions.schema import schema_payload as schemas

    wrong = []
    for spec in schemas()["types"]:
        expected = GROUP_REQUIREMENTS.get(spec.get("group", ""))
        if expected and spec.get("requires") != expected:
            wrong.append(f"{spec['type']} (group {spec['group']}) declares {spec.get('requires')!r}")
    assert not wrong, "these would be offered while their app is disconnected: " + "; ".join(wrong)


def test_actions_that_need_nothing_stay_available():
    """The picker must never be empty on a fresh install, or it reads as broken."""
    from wheelhat.actions.schema import schema_payload as schemas

    always = [s for s in schemas()["types"] if not s.get("requires") and s["type"] != "shell_command"]
    assert always, "at least one action must work with nothing connected"
    groups = {s["group"] for s in always}
    assert "Web" in groups, "sending a webhook needs no local app and must always be offered"
