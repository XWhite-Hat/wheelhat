"""Connectors driven against stand-in OBS and VTube Studio servers."""

import asyncio

import pytest

from wheelhat.actions.executor import ActionFailed, ExecContext, execute_single
from wheelhat.integrations.base import ConnectorError, ConnectorState
from wheelhat.integrations.obs import OBSConnector
from wheelhat.integrations.vtube_studio import VTubeStudioConnector

from .fakes import FakeOBS, FakeVTubeStudio


@pytest.fixture
async def obs_server():
    server = FakeOBS()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def obs(obs_server):
    connector = OBSConnector(host="127.0.0.1", port=obs_server.port)
    await connector.connect_once()
    yield connector
    await connector.stop()


@pytest.fixture
async def vts_server():
    server = FakeVTubeStudio()
    await server.start()
    yield server
    await server.stop()


# ------------------------------------------------------------------------ OBS


async def test_obs_connects_without_a_password(obs, obs_server):
    assert obs.state is ConnectorState.CONNECTED
    assert obs.version == "5.4.2"


async def test_obs_authenticates_with_a_password():
    server = FakeOBS(password="hunter2")
    await server.start()
    try:
        connector = OBSConnector(host="127.0.0.1", port=server.port, password="hunter2")
        await connector.connect_once()
        assert connector.state is ConnectorState.CONNECTED
        await connector.stop()
    finally:
        await server.stop()


async def test_obs_rejects_a_wrong_password():
    server = FakeOBS(password="hunter2")
    await server.start()
    try:
        connector = OBSConnector(host="127.0.0.1", port=server.port, password="wrong")
        with pytest.raises(ConnectorError, match="rejected the connection"):
            await connector.connect_once()
        await connector.stop()
    finally:
        await server.stop()


async def test_obs_missing_password_is_flagged_as_needing_auth():
    server = FakeOBS(password="hunter2")
    await server.start()
    try:
        connector = OBSConnector(host="127.0.0.1", port=server.port)
        with pytest.raises(ConnectorError, match="requires a WebSocket password"):
            await connector.connect_once()
        assert connector.state is ConnectorState.NEEDS_AUTH
        await connector.stop()
    finally:
        await server.stop()


async def test_obs_unreachable_reports_a_useful_message():
    connector = OBSConnector(host="127.0.0.1", port=1)
    with pytest.raises(ConnectorError):
        await connector.connect_once(timeout=3)
    assert connector.state is ConnectorState.ERROR
    await connector.stop()


async def test_obs_scenes_are_returned_in_ui_order(obs):
    scenes = [s["sceneName"] for s in await obs.scenes()]
    assert scenes == ["Starting Soon", "Gameplay", "Ending"]


async def test_obs_capability_lookups_are_cached(obs, obs_server):
    await obs.scenes()
    await obs.scenes()
    assert sum(1 for name, _ in obs_server.received if name == "GetSceneList") == 1


async def test_obs_request_error_surfaces_the_comment(obs):
    with pytest.raises(ConnectorError, match="No such request type"):
        await obs.request("Explode")


async def test_obs_scene_action_switches_scene(obs, obs_server, monkeypatch):
    monkeypatch.setattr(
        "wheelhat.actions.handlers.registry.resolve", lambda kind, ident="": obs
    )
    detail = await execute_single(
        {"type": "obs_scene", "config": {"scene": "Gameplay", "target": "program"}},
        ExecContext(wheel_name="w", winner="x"),
    )
    assert "Gameplay" in detail
    assert obs_server.current_scene == "Gameplay"


async def test_obs_text_action_templates_the_winner(obs, obs_server, monkeypatch):
    monkeypatch.setattr(
        "wheelhat.actions.handlers.registry.resolve", lambda kind, ident="": obs
    )
    await execute_single(
        {"type": "obs_text", "config": {"source": "Winner Text", "text": "{{winner}} wins!"}},
        ExecContext(wheel_name="w", winner="Tiny mode"),
    )
    settings = next(
        data for name, data in obs_server.received if name == "SetInputSettings"
    )
    assert settings["inputSettings"]["text"] == "Tiny mode wins!"


async def test_obs_toggle_reads_current_visibility(obs, obs_server, monkeypatch):
    monkeypatch.setattr(
        "wheelhat.actions.handlers.registry.resolve", lambda kind, ident="": obs
    )
    detail = await execute_single(
        {
            "type": "obs_source_visibility",
            "config": {"scene": "Gameplay", "source": "Webcam", "state": "toggle"},
        },
        ExecContext(),
    )
    # The fake reports the item as hidden, so a toggle must show it.
    assert "Showed" in detail


async def test_obs_action_without_a_connection_explains_itself():
    with pytest.raises(ActionFailed, match="No enabled OBS Studio connection"):
        await execute_single({"type": "obs_scene", "config": {"scene": "X"}}, ExecContext())


# --------------------------------------------------------------- VTube Studio


async def test_vts_requires_authorisation_before_connecting(vts_server):
    connector = VTubeStudioConnector(host="127.0.0.1", port=vts_server.port)
    with pytest.raises(ConnectorError, match="has not been authorised"):
        await connector.connect_once()
    assert connector.state is ConnectorState.NEEDS_AUTH
    await connector.stop()


async def test_vts_request_access_returns_a_token(vts_server):
    connector = VTubeStudioConnector(host="127.0.0.1", port=vts_server.port)
    stored = []
    connector.on_token = lambda token: _record(stored, token)
    token = await connector.request_access(timeout=5)
    assert token == "TOKEN-123"
    assert stored == ["TOKEN-123"]
    await connector.stop()


async def _record(bucket, token):
    bucket.append(token)


async def test_vts_denied_access_is_reported():
    server = FakeVTubeStudio(auto_approve=False)
    await server.start()
    try:
        connector = VTubeStudioConnector(host="127.0.0.1", port=server.port)
        with pytest.raises(ConnectorError, match="denied"):
            await connector.request_access(timeout=5)
    finally:
        await server.stop()


async def test_vts_connects_with_a_stored_token(vts_server):
    connector = VTubeStudioConnector(
        host="127.0.0.1", port=vts_server.port, token="TOKEN-123"
    )
    await connector.connect_once()
    assert connector.state is ConnectorState.CONNECTED
    assert connector.version == "1.28.0"
    await connector.stop()


async def test_vts_stale_token_is_cleared(vts_server):
    connector = VTubeStudioConnector(host="127.0.0.1", port=vts_server.port, token="OLD")
    with pytest.raises(ConnectorError, match="rejected the saved token"):
        await connector.connect_once()
    assert connector.token == ""
    assert connector.state is ConnectorState.NEEDS_AUTH
    await connector.stop()


async def test_vts_hotkey_options_are_labelled(vts_server):
    connector = VTubeStudioConnector(
        host="127.0.0.1", port=vts_server.port, token="TOKEN-123"
    )
    await connector.connect_once()
    hotkeys = await connector.hotkeys()
    assert [h["name"] for h in hotkeys] == ["Cursed outfit", "Wave"]
    await connector.stop()


async def test_vts_hotkey_action_fires(vts_server, monkeypatch):
    connector = VTubeStudioConnector(
        host="127.0.0.1", port=vts_server.port, token="TOKEN-123"
    )
    await connector.connect_once()
    monkeypatch.setattr(
        "wheelhat.actions.handlers.registry.resolve", lambda kind, ident="": connector
    )
    detail = await execute_single(
        {"type": "vts_hotkey", "config": {"hotkey": "hk-1"}}, ExecContext()
    )
    assert "hk-1" in detail
    assert ("HotkeyTriggerRequest", {"hotkeyID": "hk-1"}) in vts_server.received
    await connector.stop()


# --------------------------------------------------------------------- shared


async def test_supervisor_reconnects_after_the_app_restarts():
    server = FakeOBS()
    port = await server.start()
    connector = OBSConnector(host="127.0.0.1", port=port)
    await connector.start()

    from .fakes import wait_for

    assert await wait_for(lambda: connector.state is ConnectorState.CONNECTED, 6)

    await server.stop()
    assert await wait_for(lambda: connector.state is not ConnectorState.CONNECTED, 6)

    # OBS comes back on the same port.
    revived = FakeOBS()
    revived.server = None
    import websockets

    revived.server = await websockets.serve(revived._handle, "127.0.0.1", port)
    try:
        assert await wait_for(lambda: connector.state is ConnectorState.CONNECTED, 15)
    finally:
        await connector.stop()
        await revived.stop()


async def test_supervising_an_open_connection_does_not_open_a_second():
    """reconnect() adopts the socket it just opened rather than duplicating it."""
    server = FakeOBS()
    port = await server.start()
    connector = OBSConnector(host="127.0.0.1", port=port)
    try:
        await connector.connect_once()
        await connector.supervise_existing()
        await asyncio.sleep(0.5)

        assert connector.state is ConnectorState.CONNECTED
        # One Identify handshake means one live connection to OBS.
        await connector.request("GetVersion")
        assert len(server.server.connections) == 1
    finally:
        await connector.stop()
        await server.stop()


async def test_requests_fail_fast_when_disconnected():
    connector = OBSConnector(host="127.0.0.1", port=1)
    with pytest.raises(ConnectorError, match="not connected"):
        await asyncio.wait_for(connector.request("GetSceneList"), timeout=10)
