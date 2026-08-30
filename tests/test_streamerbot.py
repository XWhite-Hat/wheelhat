"""Streamer.bot connector, actions and discovery fingerprint."""

import base64
import hashlib
import json

import pytest
import websockets

from wheelhat import discovery
from wheelhat.actions.executor import ActionFailed, ExecContext, execute_single
from wheelhat.integrations.base import ConnectorError, ConnectorState
from wheelhat.integrations.streamerbot import StreamerBotConnector, auth_response

from .fakes import FakeStreamerBot

CURSED = "47da7c2c-1b7e-4ee7-9bbf-306bf18ff1b8"


@pytest.fixture
async def sb_server():
    server = FakeStreamerBot()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def sb(sb_server):
    connector = StreamerBotConnector(host="127.0.0.1", port=sb_server.port)
    await connector.connect_once()
    yield connector
    await connector.stop()


def sent(server, request_name):
    return [msg for name, msg in server.received if name == request_name]


# ------------------------------------------------------------------ handshake


async def test_connects_and_reads_the_version(sb, sb_server):
    assert sb.state is ConnectorState.CONNECTED
    assert sb.version == "Streamer.bot 0.2.5"
    assert sb.instance_name == "Streamer.bot"


async def test_falls_back_to_getinfo_on_older_builds():
    """v0.2.4 and older send no Hello frame."""
    server = FakeStreamerBot(send_hello=False, version="0.2.4")
    await server.start()
    try:
        connector = StreamerBotConnector(host="127.0.0.1", port=server.port)
        await connector.connect_once()
        assert connector.state is ConnectorState.CONNECTED
        assert connector.version == "Streamer.bot 0.2.4"
        assert sent(server, "GetInfo")
        await connector.stop()
    finally:
        await server.stop()


async def test_authenticates_when_a_password_is_configured():
    server = FakeStreamerBot(password="hunter2")
    await server.start()
    try:
        connector = StreamerBotConnector(host="127.0.0.1", port=server.port, password="hunter2")
        await connector.connect_once()
        assert connector.state is ConnectorState.CONNECTED
        assert connector.authenticated is True
        await connector.stop()
    finally:
        await server.stop()


async def test_wrong_password_is_reported():
    server = FakeStreamerBot(password="hunter2")
    await server.start()
    try:
        connector = StreamerBotConnector(host="127.0.0.1", port=server.port, password="nope")
        with pytest.raises(ConnectorError, match="rejected the password"):
            await connector.connect_once()
        assert connector.state is ConnectorState.NEEDS_AUTH
        await connector.stop()
    finally:
        await server.stop()


async def test_missing_password_points_at_the_setting():
    server = FakeStreamerBot(password="hunter2")
    await server.start()
    try:
        connector = StreamerBotConnector(host="127.0.0.1", port=server.port)
        with pytest.raises(ConnectorError, match="authentication switched on"):
            await connector.connect_once()
        assert connector.state is ConnectorState.NEEDS_AUTH
        await connector.stop()
    finally:
        await server.stop()


def test_auth_hash_is_the_documented_two_step_hash():
    """base64(sha256(base64(sha256(password + salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256(b"pwsalt").digest()).decode()
    expected = base64.b64encode(hashlib.sha256((secret + "chal").encode()).digest()).decode()
    assert auth_response("pw", "salt", "chal") == expected


async def test_a_websocket_that_is_not_streamerbot_is_rejected():
    """Port 8080 is crowded; answering is not the same as being Streamer.bot."""

    async def handler(ws):
        async for _ in ws:
            await ws.send(json.dumps({"hello": "i am a dev server"}))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        connector = StreamerBotConnector(host="127.0.0.1", port=port)
        with pytest.raises(ConnectorError):
            await connector.connect_once(timeout=6)
        await connector.stop()
    finally:
        server.close()
        await server.wait_closed()


# --------------------------------------------------------------- capabilities


async def test_actions_are_listed(sb):
    actions = await sb.actions()
    assert [a["name"] for a in actions] == ["Cursed outfit", "Airhorn", "Retired thing"]


async def test_code_triggers_are_listed(sb):
    triggers = await sb.code_triggers()
    assert triggers[0]["name"] == "wheel_result"


async def test_globals_are_listed(sb):
    assert (await sb.globals())["spinCount"]["value"] == "7"


async def test_capability_lookups_are_cached(sb, sb_server):
    await sb.actions()
    await sb.actions()
    assert len(sent(sb_server, "GetActions")) == 1


# -------------------------------------------------------------------- actions


@pytest.fixture
def use_sb(sb, monkeypatch):
    monkeypatch.setattr(
        "wheelhat.actions.handlers.registry.resolve", lambda kind, ident="": sb
    )
    return sb


def ctx():
    return ExecContext(
        wheel_id="whl_1",
        wheel_name="Punishment",
        winner="Tiny mode",
        source="channel_points",
        variables={"user": "Ann", "reward": "Spin"},
    )


async def test_run_action_by_id(use_sb, sb_server):
    detail = await execute_single(
        {"type": "streamerbot_action", "config": {"action": CURSED, "pass_variables": False}},
        ctx(),
    )
    assert CURSED in detail
    request = sent(sb_server, "DoAction")[0]
    assert request["action"] == {"id": CURSED}


async def test_run_action_by_name_when_it_is_not_a_guid(use_sb, sb_server):
    await execute_single(
        {
            "type": "streamerbot_action",
            "config": {"action": "Cursed outfit", "pass_variables": False},
        },
        ctx(),
    )
    assert sent(sb_server, "DoAction")[0]["action"] == {"name": "Cursed outfit"}


async def test_wheel_variables_are_passed_as_arguments(use_sb, sb_server):
    await execute_single(
        {"type": "streamerbot_action", "config": {"action": CURSED, "pass_variables": True}},
        ctx(),
    )
    args = sent(sb_server, "DoAction")[0]["args"]
    assert args["winner"] == "Tiny mode"
    assert args["user"] == "Ann"
    assert args["wheel"] == "Punishment"


async def test_custom_arguments_win_over_wheel_variables(use_sb, sb_server):
    await execute_single(
        {
            "type": "streamerbot_action",
            "config": {
                "action": CURSED,
                "pass_variables": True,
                "args": [{"key": "winner", "value": "overridden"}, {"key": "extra", "value": "1"}],
            },
        },
        ctx(),
    )
    args = sent(sb_server, "DoAction")[0]["args"]
    assert args["winner"] == "overridden"
    assert args["extra"] == "1"


async def test_arguments_are_templated(use_sb, sb_server):
    await execute_single(
        {
            "type": "streamerbot_action",
            "config": {
                "action": CURSED,
                "pass_variables": False,
                "args": [{"key": "note", "value": "{{winner}} for {{user}}"}],
            },
        },
        ctx(),
    )
    assert sent(sb_server, "DoAction")[0]["args"]["note"] == "Tiny mode for Ann"


async def test_action_without_a_selection_is_refused(use_sb):
    with pytest.raises(ActionFailed, match="Pick a Streamer.bot action"):
        await execute_single({"type": "streamerbot_action", "config": {}}, ctx())


async def test_code_trigger_fires(use_sb, sb_server):
    detail = await execute_single(
        {"type": "streamerbot_code_trigger", "config": {"trigger": "wheel_result"}}, ctx()
    )
    assert "wheel_result" in detail
    assert sent(sb_server, "ExecuteCodeTrigger")[0]["triggerName"] == "wheel_result"


async def test_chat_needs_an_authenticated_connection(use_sb):
    with pytest.raises(ActionFailed, match="needs an authenticated connection"):
        await execute_single(
            {"type": "streamerbot_chat", "config": {"platform": "kick", "message": "hi"}}, ctx()
        )


async def test_chat_sends_once_authenticated(monkeypatch):
    server = FakeStreamerBot(password="pw")
    await server.start()
    try:
        connector = StreamerBotConnector(host="127.0.0.1", port=server.port, password="pw")
        await connector.connect_once()
        monkeypatch.setattr(
            "wheelhat.actions.handlers.registry.resolve", lambda kind, ident="": connector
        )
        detail = await execute_single(
            {
                "type": "streamerbot_chat",
                "config": {"platform": "youtube", "message": "{{winner}} won"},
            },
            ctx(),
        )
        assert "youtube" in detail
        request = sent(server, "SendMessage")[0]
        assert request["message"] == "Tiny mode won"
        assert request["platform"] == "youtube"
        await connector.stop()
    finally:
        await server.stop()


async def test_raw_request_passes_through(use_sb, sb_server):
    with pytest.raises(ActionFailed, match="Unknown request"):
        await execute_single(
            {"type": "streamerbot_raw", "config": {"request": "Nonsense", "payload": "{}"}}, ctx()
        )


async def test_action_without_a_connection_explains_itself():
    with pytest.raises(ActionFailed, match="No enabled Streamer.bot connection"):
        await execute_single(
            {"type": "streamerbot_action", "config": {"action": CURSED}}, ctx()
        )


# ------------------------------------------------------------------ discovery


async def test_discovery_identifies_streamerbot(sb_server):
    result = await discovery.probe_streamerbot("127.0.0.1", sb_server.port)
    assert result["identified"] is True
    assert "0.2.5" in result["version"]
    assert result["needs_auth"] is False


async def test_discovery_flags_a_password_protected_instance():
    server = FakeStreamerBot(password="pw")
    await server.start()
    try:
        result = await discovery.probe_streamerbot("127.0.0.1", server.port)
        assert result["identified"] is True
        assert result["needs_auth"] is True
    finally:
        await server.stop()


async def test_discovery_identifies_older_builds_via_getinfo():
    server = FakeStreamerBot(send_hello=False, version="0.2.4")
    await server.start()
    try:
        result = await discovery.probe_streamerbot("127.0.0.1", server.port)
        assert result["identified"] is True
        assert "0.2.4" in result["version"]
    finally:
        await server.stop()


async def test_discovery_does_not_claim_a_random_websocket_on_8080():
    async def handler(ws):
        async for _ in ws:
            await ws.send(json.dumps({"id": "wh-discover", "status": "ok"}))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        result = await discovery.probe_streamerbot("127.0.0.1", port)
        assert result["identified"] is False
        assert "not Streamer.bot" in result["detail"]
    finally:
        server.close()
        await server.wait_closed()


async def test_probe_one_routes_to_the_streamerbot_fingerprint(sb_server):
    result = await discovery.probe_one("streamer_bot", "127.0.0.1", sb_server.port)
    assert result["ok"] is True
    assert "Streamer.bot" in result["version"]
