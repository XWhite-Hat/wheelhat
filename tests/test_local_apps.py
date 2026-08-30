"""Mix It Up, Speaker.bot, SAMMI and VNyan connectors, actions and discovery."""

import asyncio

import pytest
import websockets

from wheelhat import discovery
from wheelhat.actions.executor import ActionFailed, ExecContext, execute_single
from wheelhat.actions.options import resolve
from wheelhat.integrations.base import ConnectorError, ConnectorState
from wheelhat.integrations.mixitup import MixItUpConnector
from wheelhat.integrations.sammi import SammiConnector
from wheelhat.integrations.speakerbot import SpeakerBotConnector
from wheelhat.integrations.vnyan import VNyanConnector

from .fakes import FakeMixItUp, FakeSammi, FakeSpeakerBot, FakeVNyan

CURSED = "1783e5d9-c2ab-423a-ae64-7dc9a086b194"


def ctx():
    return ExecContext(
        wheel_id="whl_1",
        wheel_name="Punishment",
        winner="Tiny mode",
        variables={"user": "Ann"},
    )


def use(monkeypatch, connector):
    monkeypatch.setattr(
        "wheelhat.actions.handlers.registry.resolve", lambda kind, ident="": connector
    )


# ------------------------------------------------------------------ Mix It Up


@pytest.fixture
async def miu_server():
    server = FakeMixItUp()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def miu(miu_server):
    connector = MixItUpConnector(host="127.0.0.1", port=miu_server.port)
    await connector.connect_once()
    yield connector
    await connector.stop()


async def test_mixitup_connects(miu):
    assert miu.state is ConnectorState.CONNECTED
    assert "Developer API" in miu.version


async def test_mixitup_lists_commands(miu):
    commands = await miu.commands()
    assert [c["Name"] for c in commands] == ["Cursed outfit", "Airhorn", "Retired"]


async def test_mixitup_rejects_an_impostor_on_its_port():
    """Something else answering on 8911 must not be mistaken for Mix It Up."""
    server = FakeMixItUp(impostor=True)
    await server.start()
    try:
        connector = MixItUpConnector(host="127.0.0.1", port=server.port)
        with pytest.raises(ConnectorError, match="not the Mix It Up Developer API"):
            await connector.connect_once()
        await connector.stop()
    finally:
        await server.stop()


async def test_mixitup_unreachable_is_reported():
    connector = MixItUpConnector(host="127.0.0.1", port=1)
    with pytest.raises(ConnectorError):
        await connector.connect_once(timeout=5)
    assert connector.state is ConnectorState.ERROR
    await connector.stop()


async def test_mixitup_run_command_action(miu, miu_server, monkeypatch):
    use(monkeypatch, miu)
    detail = await execute_single(
        {
            "type": "mixitup_command",
            "config": {"command": CURSED, "arguments": "{{winner}}", "ignore_requirements": True},
        },
        ctx(),
    )
    assert CURSED in detail
    command_id, body = miu_server.ran[0]
    assert command_id == CURSED
    assert body["Arguments"] == "Tiny mode"
    assert body["IgnoreRequirements"] is True


async def test_mixitup_command_options_are_grouped_by_type(miu, monkeypatch):
    monkeypatch.setattr(
        "wheelhat.actions.options.registry.resolve", lambda kind, ident="": miu
    )
    result = await resolve("miu.commands", {})
    assert result["error"] is None
    labels = {o["label"]: o.get("group") for o in result["options"]}
    assert labels["Cursed outfit"] == "Chat"
    assert "Retired  (disabled)" in labels


async def test_mixitup_action_without_a_selection():
    with pytest.raises(ActionFailed):
        await execute_single({"type": "mixitup_command", "config": {}}, ctx())


# ----------------------------------------------------------------- Speaker.bot


@pytest.fixture
async def speaker_server():
    server = FakeSpeakerBot()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def speaker(speaker_server):
    connector = SpeakerBotConnector(host="127.0.0.1", port=speaker_server.port)
    await connector.connect_once()
    yield connector
    await connector.stop()


async def test_speakerbot_connects(speaker):
    assert speaker.state is ConnectorState.CONNECTED


async def test_speakerbot_speak_does_not_wait_for_a_reply(speaker, speaker_server, monkeypatch):
    """The server never answers, so this would hang if we awaited a response."""
    use(monkeypatch, speaker)
    detail = await asyncio.wait_for(
        execute_single(
            {
                "type": "speakerbot_speak",
                "config": {"message": "{{user}} got {{winner}}", "voice": "Brian"},
            },
            ctx(),
        ),
        timeout=5,
    )
    assert "Ann got Tiny mode" in detail
    await asyncio.sleep(0.2)
    sent = speaker_server.received[-1]
    assert sent["request"] == "Speak"
    assert sent["message"] == "Ann got Tiny mode"
    assert sent["voice"] == "Brian"


async def test_speakerbot_omits_a_blank_voice(speaker, speaker_server, monkeypatch):
    use(monkeypatch, speaker)
    await execute_single(
        {"type": "speakerbot_speak", "config": {"message": "hi", "voice": ""}}, ctx()
    )
    await asyncio.sleep(0.2)
    assert "voice" not in speaker_server.received[-1]


async def test_speakerbot_queue_control(speaker, speaker_server, monkeypatch):
    use(monkeypatch, speaker)
    await execute_single({"type": "speakerbot_control", "config": {"command": "Pause"}}, ctx())
    await asyncio.sleep(0.2)
    assert speaker_server.received[-1]["request"] == "Pause"


async def test_speakerbot_empty_message_is_refused(speaker, monkeypatch):
    use(monkeypatch, speaker)
    with pytest.raises(ActionFailed, match="Message is empty"):
        await execute_single({"type": "speakerbot_speak", "config": {"message": "  "}}, ctx())


# ----------------------------------------------------------------------- SAMMI


@pytest.fixture
async def sammi_server():
    server = FakeSammi()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def sammi(sammi_server):
    connector = SammiConnector(host="127.0.0.1", port=sammi_server.port)
    await connector.connect_once()
    yield connector
    await connector.stop()


async def test_sammi_connects_and_reads_version(sammi):
    assert sammi.state is ConnectorState.CONNECTED
    assert sammi.version == "SAMMI 2024.1"


async def test_sammi_trigger_button(sammi, sammi_server, monkeypatch):
    use(monkeypatch, sammi)
    detail = await execute_single(
        {"type": "sammi_button", "config": {"button_id": "ID19"}}, ctx()
    )
    assert "ID19" in detail
    assert sammi_server.calls[-1] == {"request": "triggerButton", "buttonID": "ID19"}


async def test_sammi_release_button(sammi, sammi_server, monkeypatch):
    use(monkeypatch, sammi)
    await execute_single(
        {"type": "sammi_button", "config": {"button_id": "ID19", "release": True}}, ctx()
    )
    assert sammi_server.calls[-1]["request"] == "releaseButton"


async def test_sammi_set_variable_is_templated(sammi, sammi_server, monkeypatch):
    use(monkeypatch, sammi)
    await execute_single(
        {"type": "sammi_variable", "config": {"name": "wheelWinner", "value": "{{winner}}"}},
        ctx(),
    )
    call = sammi_server.calls[-1]
    assert call["request"] == "setVariable"
    assert call["value"] == "Tiny mode"


async def test_sammi_button_without_an_id_is_refused(sammi, monkeypatch):
    use(monkeypatch, sammi)
    with pytest.raises(ActionFailed, match="button ID"):
        await execute_single({"type": "sammi_button", "config": {}}, ctx())


# ----------------------------------------------------------------------- VNyan


@pytest.fixture
async def vnyan_server():
    server = FakeVNyan()
    await server.start()
    yield server
    await server.stop()


async def test_vnyan_connects_on_its_path(vnyan_server):
    connector = VNyanConnector(host="127.0.0.1", port=vnyan_server.port)
    await connector.connect_once()
    assert connector.state is ConnectorState.CONNECTED
    assert connector.uri.endswith("/vnyan")
    await connector.stop()


async def test_vnyan_trigger_sends_bare_text(vnyan_server, monkeypatch):
    connector = VNyanConnector(host="127.0.0.1", port=vnyan_server.port)
    await connector.connect_once()
    use(monkeypatch, connector)
    detail = await execute_single(
        {"type": "vnyan_trigger", "config": {"trigger": "cursed_{{winner|slug}}"}}, ctx()
    )
    await asyncio.sleep(0.2)
    assert "cursed_tiny-mode" in detail
    assert vnyan_server.received == ["cursed_tiny-mode"]
    await connector.stop()


async def test_vnyan_empty_trigger_is_refused(vnyan_server, monkeypatch):
    connector = VNyanConnector(host="127.0.0.1", port=vnyan_server.port)
    await connector.connect_once()
    use(monkeypatch, connector)
    with pytest.raises(ActionFailed, match="trigger name"):
        await execute_single({"type": "vnyan_trigger", "config": {"trigger": ""}}, ctx())
    await connector.stop()


# ------------------------------------------------------------------ discovery


async def test_discovery_identifies_mixitup(miu_server):
    result = await discovery.probe_mixitup("127.0.0.1", miu_server.port)
    assert result["identified"] is True
    assert "3 commands" in result["detail"]


async def test_discovery_rejects_an_impostor_on_the_mixitup_port():
    server = FakeMixItUp(impostor=True)
    await server.start()
    try:
        result = await discovery.probe_mixitup("127.0.0.1", server.port)
        assert result["identified"] is False
    finally:
        await server.stop()


async def test_discovery_identifies_sammi(sammi_server):
    result = await discovery.probe_sammi("127.0.0.1", sammi_server.port)
    assert result["identified"] is True
    assert "2024.1" in result["version"]


async def test_discovery_flags_a_password_protected_sammi():
    server = FakeSammi(password="letmein")
    await server.start()
    try:
        result = await discovery.probe_sammi("127.0.0.1", server.port)
        assert result["identified"] is True
        assert result["needs_auth"] is True
    finally:
        await server.stop()


async def test_discovery_identifies_vnyan_by_its_path(vnyan_server):
    result = await discovery.probe_vnyan("127.0.0.1", vnyan_server.port)
    assert result["identified"] is True


async def test_discovery_does_not_claim_a_plain_websocket_on_port_8000():
    """The fixed /vnyan path is all that separates VNyan from a dev server."""

    async def handler(ws):
        async for _ in ws:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0, process_request=_reject_non_root)
    port = server.sockets[0].getsockname()[1]
    try:
        # The handshake is refused, so no VNyan is claimed.
        with pytest.raises(websockets.exceptions.InvalidStatus):
            await discovery.probe_vnyan("127.0.0.1", port)
    finally:
        server.close()
        await server.wait_closed()


def _reject_non_root(connection, request):
    from http import HTTPStatus

    if request.path != "/":
        return connection.respond(HTTPStatus.NOT_FOUND, "no\n")
    return None


def test_known_apps_have_the_ports_the_docs_specify():
    """Guards against the 9425/9450 mix-up that SAMMI nearly shipped with."""
    ports = {app.id: app.port for app in discovery.KNOWN_APPS}
    assert ports["sammi"] == 9450
    assert ports["speakerbot"] == 7680
    assert ports["mixitup"] == 8911
    assert ports["vnyan"] == 8000
    assert ports["streamerbot"] == 8080


async def test_a_bare_tcp_probe_never_claims_the_app_is_ready():
    """An open port proves something is listening, not that we can drive it."""
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    app = discovery.KnownApp(id="x", name="Mystery", port=port, probe="tcp")
    try:
        finding = await discovery._inspect(app, "127.0.0.1", {})
        assert finding.port_open is True
        assert finding.identified is False
        assert finding.status() == "listening"
    finally:
        server.close()
        await server.wait_closed()
