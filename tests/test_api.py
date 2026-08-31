"""HTTP API behaviour, driven through the ASGI app directly."""

import json
import pathlib

import httpx
import pytest

from wheelhat import db
from wheelhat.app import app
from wheelhat.integrations.registry import registry


@pytest.fixture
async def client():
    registry.load()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_create_wheel_gets_starter_slices(client):
    response = await client.post("/api/wheels", json={})
    assert response.status_code == 201
    body = response.json()
    assert len(body["slices"]) == 3
    assert body.get("overlay_url", True)


async def test_wheel_list_summarises(client, wheel):
    body = (await client.get("/api/wheels")).json()
    entry = next(w for w in body["wheels"] if w["id"] == wheel.id)
    assert entry["slice_count"] == 3
    assert entry["spinnable_count"] == 2  # one slice is disabled
    assert entry["overlay_url"].endswith(f"/overlay/{wheel.id}")


async def test_get_missing_wheel_is_a_404(client):
    response = await client.get("/api/wheels/nope")
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


async def test_update_round_trips(client, wheel):
    payload = (await client.get(f"/api/wheels/{wheel.id}")).json()
    payload["name"] = "Renamed"
    payload["slices"][0]["label"] = "Changed"
    response = await client.put(f"/api/wheels/{wheel.id}", json=payload)
    assert response.status_code == 200

    stored = db.get_wheel(wheel.id)
    assert stored.name == "Renamed"
    assert stored.slices[0].label == "Changed"


async def test_update_ignores_read_only_extras(client, wheel):
    payload = (await client.get(f"/api/wheels/{wheel.id}")).json()
    # The editor strips these, but the API must not choke if they arrive.
    payload["overlay_clients"] = 7
    payload["spinning"] = True
    assert (await client.put(f"/api/wheels/{wheel.id}", json=payload)).status_code == 200


async def test_patch_merges(client, wheel):
    await client.patch(f"/api/wheels/{wheel.id}", json={"enabled": False})
    assert db.get_wheel(wheel.id).enabled is False
    assert len(db.get_wheel(wheel.id).slices) == 3


async def test_duplicate_makes_fresh_ids_and_disables_triggers(client, wheel):
    stored = db.get_wheel(wheel.id)
    stored.triggers.append(
        __import__("wheelhat.models", fromlist=["Trigger"]).Trigger(
            type="channel_points", enabled=True, config={"reward_id": "r1"}
        )
    )
    db.save_wheel(stored)

    clone = (await client.post(f"/api/wheels/{wheel.id}/duplicate")).json()
    assert clone["id"] != wheel.id
    assert clone["name"].endswith("(copy)")
    assert {s["id"] for s in clone["slices"]}.isdisjoint({s.id for s in stored.slices})
    assert clone["triggers"][0]["enabled"] is False


async def test_bulk_slices_replace(client, wheel):
    response = await client.post(
        f"/api/wheels/{wheel.id}/slices/bulk",
        json={"text": "One\nTwo\n\n  Three  \n", "replace": True},
    )
    labels = [s["label"] for s in response.json()["slices"]]
    assert labels == ["One", "Two", "Three"]


async def test_bulk_slices_append(client, wheel):
    response = await client.post(
        f"/api/wheels/{wheel.id}/slices/bulk", json={"text": "Extra", "replace": False}
    )
    assert len(response.json()["slices"]) == 4


async def test_reset_reenables_everything(client, wheel):
    stored = db.get_wheel(wheel.id)
    stored.slices[0].enabled = False
    stored.slices[0].cooldown_remaining = 3
    stored.slices[0].won_count = 5
    db.save_wheel(stored)

    body = (await client.post(f"/api/wheels/{wheel.id}/reset")).json()
    assert all(s["enabled"] for s in body["slices"])
    assert all(s["cooldown_remaining"] == 0 for s in body["slices"])


async def test_spin_conflict_is_a_409(client, action_wheel):
    assert (await client.post(f"/api/wheels/{action_wheel.id}/spin", json={})).status_code == 200
    second = await client.post(f"/api/wheels/{action_wheel.id}/spin", json={})
    assert second.status_code == 409
    assert "already spinning" in second.json()["detail"]
    await client.post(f"/api/wheels/{action_wheel.id}/cancel")


async def test_render_payload_endpoint(client, wheel):
    body = (await client.get(f"/api/wheels/{wheel.id}/render")).json()
    assert [s["label"] for s in body["slices"]] == ["Alpha", "Beta"]
    assert body["spin"]["duration_ms"] > 0


async def test_action_schema_endpoint_shape(client):
    body = (await client.get("/api/actions/schemas")).json()
    types = {t["type"]: t for t in body["types"]}
    assert "http_request" in types and "obs_scene" in types and "vts_hotkey" in types
    scene_field = next(f for f in types["obs_scene"]["fields"] if f["key"] == "scene")
    assert scene_field["source"] == "obs.scenes"
    assert any(v["name"] == "winner" for v in body["variables"])


async def test_options_endpoint_reports_a_missing_connection(client):
    body = (await client.get("/api/options/obs.scenes")).json()
    assert body["options"] == []
    assert "OBS Studio" in body["error"]


async def test_options_endpoint_rejects_unknown_sources(client):
    body = (await client.get("/api/options/not.a.source")).json()
    assert "Unknown option source" in body["error"]


async def test_test_action_endpoint_reports_failure_without_raising(client):
    body = (
        await client.post(
            "/api/actions/test",
            json={"action": {"type": "http_request", "config": {"url": ""}}},
        )
    ).json()
    assert body["ok"] is False and "No URL" in body["detail"]


async def test_export_then_import_recreates_the_wheel(client, wheel):
    exported = (await client.get("/api/export")).json()
    assert exported["kind"] == "wheelhat-backup"

    result = (await client.post("/api/import", json={"data": exported, "replace": False})).json()
    assert result["imported"] == 1
    names = [w.name for w in db.list_wheels()]
    assert "Test wheel" in names and "Test wheel (imported)" in names


async def test_import_rejects_a_foreign_file(client):
    response = await client.post("/api/import", json={"data": {"kind": "something-else"}})
    assert response.status_code == 422


async def test_settings_reject_unknown_keys(client):
    response = await client.put("/api/settings", json={"values": {"nope": 1}})
    assert response.status_code == 422


async def test_settings_round_trip(client):
    await client.put("/api/settings", json={"values": {"allow_shell_actions": True}})
    body = (await client.get("/api/settings")).json()
    assert body["settings"]["allow_shell_actions"] is True
    assert body["paths"]["database"].endswith(".db")
    await client.put("/api/settings", json={"values": {"allow_shell_actions": False}})


async def test_status_snapshot(client, wheel):
    body = (await client.get("/api/status")).json()
    assert body["version"]
    assert any(w["id"] == wheel.id for w in body["wheels"])
    assert "twitch" in body and "integrations" in body


async def test_integration_password_is_never_returned(client):
    await client.post(
        "/api/integrations",
        json={"id": "obs", "kind": "obs", "host": "127.0.0.1", "port": 4455, "password": "secret", "enabled": False},
    )
    body = (await client.get("/api/integrations")).json()
    entry = next(i for i in body["integrations"] if i["id"] == "obs")
    assert entry["has_password"] is True
    assert "secret" not in json.dumps(body)


async def test_blank_password_keeps_the_saved_one(client):
    await client.post(
        "/api/integrations",
        json={"id": "obs", "kind": "obs", "host": "127.0.0.1", "port": 4455, "password": "keepme", "enabled": False},
    )
    await client.post(
        "/api/integrations",
        json={"id": "obs", "kind": "obs", "host": "127.0.0.1", "port": 4456, "password": None, "enabled": False},
    )
    assert registry.config("obs").password == "keepme"
    assert registry.config("obs").port == 4456


async def test_unsupported_integration_kind_is_rejected(client):
    response = await client.post(
        "/api/integrations", json={"kind": "nonsense", "host": "x", "port": 1}
    )
    assert response.status_code == 422


async def test_slice_colours_round_trip_and_reach_the_overlay(client):
    """Per-slice label and inline colours have to survive a save and be sent on.

    text_color existed on the model but had no way in and was never asserted;
    a field the overlay never receives is invisible however well it is stored.
    """
    made = (await client.post("/api/wheels", json={})).json()
    made["slices"][0]["text_color"] = "#101010"
    made["slices"][0]["border_color"] = "#ff00ff"
    made["slices"][0]["text_stroke_color"] = "#00ff00"
    saved = (await client.put(f"/api/wheels/{made['id']}", json=made)).json()

    assert saved["slices"][0]["text_color"] == "#101010"
    assert saved["slices"][0]["border_color"] == "#ff00ff"
    assert saved["slices"][0]["text_stroke_color"] == "#00ff00"

    # Unset stays unset rather than defaulting to a colour.
    assert saved["slices"][1]["border_color"] is None
    assert saved["slices"][1]["text_color"] is None
    assert saved["slices"][1]["text_stroke_color"] is None

    from wheelhat.engine import render_payload

    payload = render_payload(db.get_wheel(made["id"]))
    assert payload["slices"][0]["border_color"] == "#ff00ff"
    assert payload["slices"][0]["text_color"] == "#101010"
    assert payload["slices"][0]["text_stroke_color"] == "#00ff00"


async def test_source_size_and_result_position_round_trip(client):
    """Each wheel records the browser source it is built for, and where the
    winner banner sits, so the overlay can reserve room for it up front."""
    made = (await client.post("/api/wheels", json={})).json()
    assert made["appearance"]["source_width"] == 1280
    assert made["appearance"]["source_height"] == 720
    assert made["appearance"]["result_position"] == "under"

    made["appearance"]["source_width"] = 1080
    made["appearance"]["source_height"] = 1080
    made["appearance"]["result_position"] = "over"
    saved = (await client.put(f"/api/wheels/{made['id']}", json=made)).json()

    assert saved["appearance"]["source_width"] == 1080
    assert saved["appearance"]["source_height"] == 1080
    assert saved["appearance"]["result_position"] == "over"

    from wheelhat.engine import render_payload

    payload = render_payload(db.get_wheel(made["id"]))
    assert payload["appearance"]["result_position"] == "over"
    assert payload["appearance"]["source_width"] == 1080


async def test_result_position_rejects_anything_else(client):
    """The overlay branches on this value, so a typo must not reach it."""
    made = (await client.post("/api/wheels", json={})).json()
    made["appearance"]["result_position"] = "sideways"
    response = await client.put(f"/api/wheels/{made['id']}", json=made)
    assert response.status_code == 422


async def test_bundled_client_id_is_used_when_the_user_has_not_set_one(monkeypatch):
    """A release build ships an application id so nobody has to register one."""
    from wheelhat.twitch import client_id as client_id_module
    from wheelhat.twitch.service import TwitchService

    monkeypatch.setattr(client_id_module, "BUNDLED_CLIENT_ID", "bundledid123")
    monkeypatch.delenv(client_id_module.ENV_VAR, raising=False)

    service = TwitchService()
    assert service._resolve_client_id() == "bundledid123"
    assert service.status()["client_id"] == ""


async def test_a_saved_client_id_overrides_the_bundled_one(monkeypatch):
    """Anyone who would rather use their own application still can."""
    from wheelhat.twitch import client_id as client_id_module
    from wheelhat.twitch.service import TwitchService

    monkeypatch.setattr(client_id_module, "BUNDLED_CLIENT_ID", "bundledid123")
    monkeypatch.delenv(client_id_module.ENV_VAR, raising=False)

    service = TwitchService()
    await service.set_client_id("myownid456")
    assert service.client_id == "myownid456"
    assert service.status()["client_id"] == "myownid456"
    assert service.status()["using_bundled_client_id"] is False

    # Clearing it falls back rather than leaving Twitch unusable.
    await service.set_client_id("")
    assert service.client_id == "bundledid123"
    assert service.status()["using_bundled_client_id"] is True


async def test_the_environment_overrides_the_baked_in_id(monkeypatch):
    """So a developer can point a normal build at their own app."""
    from wheelhat.twitch import client_id as client_id_module

    monkeypatch.setattr(client_id_module, "BUNDLED_CLIENT_ID", "bundledid123")
    monkeypatch.setenv(client_id_module.ENV_VAR, "fromenv789")
    assert client_id_module.bundled() == "fromenv789"


def test_no_client_id_is_committed_to_the_repository():
    """The literal is injected at build time and must stay out of git.

    Not secrecy - a client id is public and ships readable in the binary. This
    keeps forks from inheriting this project's identity and rate limits, and
    catches a local release build being committed by accident.
    """
    from wheelhat.twitch import client_id as client_id_module

    source = pathlib.Path(client_id_module.__file__).read_text(encoding="utf-8")
    assert 'BUNDLED_CLIENT_ID = ""' in source, "a client id was committed; inject it at build instead"


async def test_changing_the_twitch_application_signs_the_old_session_out(monkeypatch):
    """A token only works with the client id that obtained it.

    Keeping it across an application change left the UI reporting a live
    session while every Twitch call failed - signed in by appearance only.
    """
    from wheelhat.twitch import client_id as client_id_module
    from wheelhat.twitch.auth import Tokens
    from wheelhat.twitch.service import TwitchService

    monkeypatch.setattr(client_id_module, "BUNDLED_CLIENT_ID", "bundledid123")
    monkeypatch.delenv(client_id_module.ENV_VAR, raising=False)

    service = TwitchService()
    service.client_id = service._resolve_client_id()
    service.tokens = Tokens(access_token="tok", refresh_token="ref", client_id="bundledid123")
    service.tokens.save()

    revoked: list[tuple[str, str]] = []

    async def fake_revoke(cid, token):
        revoked.append((cid, token))

    monkeypatch.setattr("wheelhat.twitch.service.auth.revoke", fake_revoke)

    await service.set_client_id("myownid456")

    assert service.client_id == "myownid456"
    assert not service.tokens.valid, "the stale token should have been cleared"
    assert service.status()["signed_in"] is False
    # Revoked against the application that issued it, not the new one.
    assert revoked == [("bundledid123", "tok")]


async def test_a_token_from_another_application_is_discarded_on_startup(monkeypatch):
    """Covers the upgrade: a build that starts shipping its own application
    must not present a token issued to a different one as a live session."""
    from wheelhat.twitch import client_id as client_id_module
    from wheelhat.twitch.auth import Tokens
    from wheelhat.twitch.service import TwitchService

    monkeypatch.setattr(client_id_module, "BUNDLED_CLIENT_ID", "bundledid123")
    monkeypatch.delenv(client_id_module.ENV_VAR, raising=False)
    Tokens(access_token="tok", refresh_token="ref", client_id="someotherapp").save()

    service = TwitchService()
    await service.start()

    assert not service.tokens.valid
    assert Tokens.load().access_token == ""


async def test_a_token_saved_before_this_was_recorded_is_kept(monkeypatch):
    """Existing users must not be signed out by the upgrade itself."""
    from wheelhat.twitch import client_id as client_id_module
    from wheelhat.twitch.auth import Tokens
    from wheelhat.twitch.service import TwitchService

    monkeypatch.setattr(client_id_module, "BUNDLED_CLIENT_ID", "bundledid123")
    monkeypatch.delenv(client_id_module.ENV_VAR, raising=False)
    # No client_id field, as saved by an older build.
    Tokens(access_token="tok", refresh_token="ref").save()

    service = TwitchService()
    service.client_id = service._resolve_client_id()
    service.tokens = Tokens.load()
    assert service.tokens.valid, "a legacy token has no application recorded and must be kept"
