"""HTTP API behaviour, driven through the ASGI app directly."""

import json
import os
import pathlib
import re

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


async def test_label_wrap_round_trips_and_defaults_on(client):
    """Wrapping is the default: cutting words is worse than another line."""
    made = (await client.post("/api/wheels", json={})).json()
    assert made["appearance"]["label_wrap"] is True

    made["appearance"]["label_wrap"] = False
    saved = (await client.put(f"/api/wheels/{made['id']}", json=made)).json()
    assert saved["appearance"]["label_wrap"] is False

    from wheelhat.engine import render_payload

    assert render_payload(db.get_wheel(made["id"]))["appearance"]["label_wrap"] is False


def test_bake_script_round_trips_the_client_id(tmp_path, monkeypatch):
    """The release build depends on this, and it only runs at tag time.

    A rename of BUNDLED_CLIENT_ID must fail loudly rather than quietly ship an
    executable with no application id in it.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bake", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "bake_client_id.py"
    )
    bake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bake)

    target = tmp_path / "client_id.py"
    target.write_text('X = 1\nBUNDLED_CLIENT_ID = ""\nY = 2\n', encoding="utf-8")
    monkeypatch.setattr(bake, "TARGET", target)

    bake.bake("abc123")
    assert 'BUNDLED_CLIENT_ID = "abc123"' in target.read_text(encoding="utf-8")
    # Surrounding code is untouched.
    assert "X = 1" in target.read_text(encoding="utf-8")

    # Baking twice replaces rather than appends.
    bake.bake("def456")
    body = target.read_text(encoding="utf-8")
    assert body.count("BUNDLED_CLIENT_ID") == 1
    assert 'BUNDLED_CLIENT_ID = "def456"' in body

    bake.bake("")
    assert 'BUNDLED_CLIENT_ID = ""' in target.read_text(encoding="utf-8")

    # A renamed constant must stop the release, not pass silently.
    target.write_text("RENAMED_CONSTANT = ''\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        bake.bake("abc123")


def test_dotenv_fills_gaps_but_never_overrides_the_environment(tmp_path, monkeypatch):
    """A .env is a convenience for source runs, not an authority.

    The frozen bootstrap sets WHEELHAT_DATA_DIR before config is imported. If a
    .env could override that, a stray file next to the executable would move
    someone's wheels and tokens out from under them.
    """
    from wheelhat import config

    (tmp_path / ".env").write_text(
        "# a comment\n"
        "\n"
        "WHEELHAT_TWITCH_CLIENT_ID=fromdotenv\n"
        'QUOTED_VALUE="quoted"\n'
        "ALREADY_SET=from-the-file\n"
        "malformed line with no equals\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("WHEELHAT_TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("QUOTED_VALUE", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from-the-shell")

    config._load_dotenv()

    assert os.environ["WHEELHAT_TWITCH_CLIENT_ID"] == "fromdotenv"
    assert os.environ["QUOTED_VALUE"] == "quoted", "surrounding quotes should be stripped"
    assert os.environ["ALREADY_SET"] == "from-the-shell", "the environment must win"


def test_a_missing_or_unreadable_dotenv_is_not_fatal(tmp_path, monkeypatch):
    """Starting up must not depend on a file that is optional by design."""
    from wheelhat import config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path / "nowhere")
    config._load_dotenv()  # must simply do nothing


def test_no_native_append_can_render_the_text_null():
    """Element.append() and replaceChildren() stringify anything that is not a
    Node, so a null from a ternary is rendered as the literal text "null".

    Our own h() filters those, which makes the native calls easy to reach for by
    mistake - this has now happened twice. Any native call whose arguments can
    produce null must spread a .filter(Boolean) list.
    """
    offenders = []
    js_dir = pathlib.Path(__file__).resolve().parent.parent / "wheelhat" / "web" / "static" / "js"
    for path in sorted(js_dir.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\.(append|replaceChildren)\(", source):
            index, depth = match.end(), 1
            while index < len(source) and depth:
                depth += (source[index] == "(") - (source[index] == ")")
                index += 1
            args = source[match.end() : index - 1]
            nullable = re.search(r":\s*null\b", args) or re.search(r"\?\?\s*null\b", args)
            if nullable and "filter(Boolean)" not in args:
                line = source[: match.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line} {match.group(1)}()")

    assert not offenders, "these can render the text 'null': " + ", ".join(offenders)


async def test_creating_a_reward_validates_before_calling_twitch(client):
    """Twitch rejects these too, but a clear message beats a relayed HTTP error."""
    blank = await client.post("/api/twitch/rewards", json={"title": "  ", "cost": 500})
    assert blank.status_code == 422
    assert "name" in blank.json()["detail"].lower()

    free = await client.post("/api/twitch/rewards", json={"title": "Spin", "cost": 0})
    assert free.status_code == 422
    assert "1 point" in free.json()["detail"]


async def test_creating_a_reward_keeps_redemptions_in_the_queue(monkeypatch):
    """Skipping the queue would fulfil redemptions on arrival, which makes both
    closing them after a spin and refunding a blocked one impossible."""
    from wheelhat.twitch.service import TwitchService

    sent: dict[str, object] = {}

    async def fake_helix(self, method, path, *, params=None, json_body=None, _retried=False):
        sent["method"], sent["path"] = method, path
        sent["params"], sent["body"] = params, json_body
        return {"data": [{"id": "reward-1", "title": json_body["title"]}]}

    monkeypatch.setattr(TwitchService, "helix", fake_helix)
    service = TwitchService()
    service.tokens.user_id = "42"

    created = await service.create_reward("Spin the wheel", 500, cooldown_seconds=30)

    assert created["id"] == "reward-1"
    assert sent["method"] == "POST"
    assert sent["params"] == {"broadcaster_id": "42"}
    assert sent["body"]["should_redemptions_skip_request_queue"] is False
    assert sent["body"]["cost"] == 500
    assert sent["body"]["is_global_cooldown_enabled"] is True
    assert sent["body"]["global_cooldown_seconds"] == 30


async def test_closing_a_redemption_sends_what_twitch_expects(monkeypatch):
    from wheelhat.twitch.service import TwitchService

    sent: dict[str, object] = {}

    async def fake_helix(self, method, path, *, params=None, json_body=None, _retried=False):
        sent["method"], sent["path"] = method, path
        sent["params"], sent["body"] = params, json_body
        return {}

    monkeypatch.setattr(TwitchService, "helix", fake_helix)
    service = TwitchService()
    service.tokens.user_id = "42"

    assert await service.close_redemption("r-1", "red-9", fulfilled=True) is True
    assert sent["method"] == "PATCH"
    assert sent["params"] == {"broadcaster_id": "42", "reward_id": "r-1", "id": "red-9"}
    assert sent["body"] == {"status": "FULFILLED"}

    await service.close_redemption("r-1", "red-9", fulfilled=False)
    assert sent["body"] == {"status": "CANCELED"}


async def test_a_reward_we_do_not_own_fails_quietly(monkeypatch):
    """Twitch answers 403 for rewards created in its own dashboard. That must
    not turn a successful spin into an error the streamer sees."""
    from wheelhat.twitch.auth import AuthError
    from wheelhat.twitch.service import TwitchService

    async def fake_helix(self, *a, **k):
        raise AuthError("custom reward was created by a different client_id")

    monkeypatch.setattr(TwitchService, "helix", fake_helix)
    service = TwitchService()
    service.tokens.user_id = "42"

    assert await service.close_redemption("r-1", "red-9", fulfilled=True) is False


async def test_closing_needs_all_three_ids(monkeypatch):
    from wheelhat.twitch.service import TwitchService

    service = TwitchService()
    service.tokens.user_id = "42"
    assert await service.close_redemption("", "red-9", fulfilled=True) is False
    assert await service.close_redemption("r-1", "", fulfilled=True) is False


async def test_a_regular_channel_is_told_what_to_use_instead(client, monkeypatch):
    """Channel points need affiliate status. Relaying Twitch's error leaves
    someone stuck; naming the alternative does not."""
    from wheelhat.twitch.service import twitch

    monkeypatch.setattr(type(twitch), "has_channel_points", property(lambda self: False))
    response = await client.post("/api/twitch/rewards", json={"title": "Spin", "cost": 500})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "affiliate" in detail
    assert "chat command" in detail.lower(), "must name the thing that does work"


async def test_channel_points_are_not_subscribed_on_a_regular_channel(monkeypatch):
    """Otherwise every reconnect makes a request Twitch is certain to refuse."""
    from wheelhat.twitch.service import TwitchService

    service = TwitchService()
    service.tokens.access_token = "tok"
    service.tokens.user_id = "42"

    service.tokens.broadcaster_type = ""
    assert service.has_channel_points is False
    types = service.all_subscription_types()
    assert "channel.channel_points_custom_reward_redemption.add" not in types
    assert types, "the socket still needs something, or Twitch closes it as unused"

    service.tokens.broadcaster_type = "affiliate"
    assert service.has_channel_points is True
    assert "channel.channel_points_custom_reward_redemption.add" in service.all_subscription_types()


def test_every_bundled_package_is_named_in_the_notices():
    """MIT and BSD-3-Clause bind binary redistribution, and the executable is a
    distribution of every package inside it. The table drifted once already."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gen_notices",
        pathlib.Path(__file__).resolve().parent.parent / "scripts" / "gen_notices.py",
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    notices = gen.NOTICES.read_text(encoding="utf-8").lower()
    missing = [name for name in gen.bundled_distributions() if name.lower() not in notices]
    assert not missing, f"not attributed in THIRD-PARTY-NOTICES.md: {missing}"


def test_the_pages_fetch_nothing_from_third_party_hosts():
    """A webfont from a CDN discloses every viewer's IP to that CDN, and an OBS
    browser source would do it on every scene load."""
    web = pathlib.Path(__file__).resolve().parent.parent / "wheelhat" / "web"
    offenders = []
    for path in list(web.glob("*.html")) + list((web / "static" / "css").glob("*.css")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(href|src|@import)\s*=?\s*[\"'(]?https?://", line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"external resource requests: {offenders}"


async def test_listen_is_not_routed_as_a_reward_named_listen(client):
    """A path parameter swallows literals declared after it. /rewards/listen
    was reaching the delete-a-reward handler with reward_id="listen"."""
    from wheelhat.twitch.service import twitch

    twitch.tokens.access_token = "tok"
    twitch.tokens.user_id = "42"
    try:
        response = await client.delete("/api/twitch/rewards/listen")
        assert response.status_code == 200
        assert "reward_capture" in response.json()
    finally:
        twitch.tokens.access_token = ""
        twitch.tokens.user_id = ""


async def test_shadow_settings_round_trip_with_the_old_look_as_default(client):
    """The defaults reproduce the stylesheet value they replaced
    (0 18px 45px rgba(0,0,0,0.45)), so existing wheels are unchanged."""
    made = (await client.post("/api/wheels", json={})).json()
    look = made["appearance"]
    assert look["shadow_enabled"] is True
    assert look["shadow_blur"] == 45.0
    assert look["shadow_offset_y"] == 18.0
    assert look["shadow_offset_x"] == 0.0
    assert look["shadow_opacity"] == 0.45
    assert look["shadow_color"] == "#000000"

    look["shadow_enabled"] = False
    look["shadow_blur"] = 12.5
    look["shadow_color"] = "#ff0066"
    saved = (await client.put(f"/api/wheels/{made['id']}", json=made)).json()

    assert saved["appearance"]["shadow_enabled"] is False
    assert saved["appearance"]["shadow_blur"] == 12.5
    assert saved["appearance"]["shadow_color"] == "#ff0066"

    from wheelhat.engine import render_payload

    overlay = render_payload(db.get_wheel(made["id"]))["appearance"]
    assert overlay["shadow_blur"] == 12.5, "the overlay needs it to draw the shadow"


async def test_manageable_rewards_are_a_separate_option_source(client, monkeypatch):
    """The closing options are only offered for rewards WheelHat created, so the
    editor needs a list of exactly those - not every reward on the channel."""
    from wheelhat.actions.options import RESOLVERS
    from wheelhat.twitch.service import twitch

    assert "twitch.rewards.manageable" in RESOLVERS

    asked: list[bool] = []

    async def fake_list(manageable_only=False):
        asked.append(manageable_only)
        return [{"id": "r-1", "title": "Ours", "cost": 500}] if manageable_only else [
            {"id": "r-1", "title": "Ours", "cost": 500},
            {"id": "r-2", "title": "Made on Twitch", "cost": 100},
        ]

    monkeypatch.setattr(twitch, "list_rewards", fake_list)

    every = await client.get("/api/options/twitch.rewards")
    ours = await client.get("/api/options/twitch.rewards.manageable")

    assert len(every.json()["options"]) == 2
    assert len(ours.json()["options"]) == 1
    assert ours.json()["options"][0]["value"] == "r-1"
    assert asked == [False, True], "the manageable list must ask Twitch for only ours"


async def test_label_colour_defaults_to_automatic_contrast(client):
    """The Look tab's colour used to be labelled "Default text" but only ever
    coloured the hub. label_color is the wheel-wide label colour, and it starts
    empty so existing wheels keep their per-wedge contrast."""
    made = (await client.post("/api/wheels", json={})).json()
    assert made["appearance"]["label_color"] == ""
    assert made["appearance"]["text_color"] == "#ffffff", "the hub label keeps its own colour"

    made["appearance"]["label_color"] = "#101010"
    saved = (await client.put(f"/api/wheels/{made['id']}", json=made)).json()
    assert saved["appearance"]["label_color"] == "#101010"

    from wheelhat.engine import render_payload

    assert render_payload(db.get_wheel(made["id"]))["appearance"]["label_color"] == "#101010"


async def test_a_frame_can_be_fitted_to_the_whole_source(client):
    """A frame fitted to the wheel is confined to the square the wheel sits in,
    so a 16:9 overlay was cropped on import."""
    made = (await client.post("/api/wheels", json={})).json()
    assert made["appearance"]["frame_fills_source"] is False

    made["appearance"]["frame_fills_source"] = True
    saved = (await client.put(f"/api/wheels/{made['id']}", json=made)).json()
    assert saved["appearance"]["frame_fills_source"] is True

    from wheelhat.engine import render_payload

    overlay = render_payload(db.get_wheel(made["id"]))["appearance"]
    assert overlay["frame_fills_source"] is True, "the overlay needs it to place the frame"


def test_no_em_dashes_in_the_interface():
    """They were being used as a general-purpose separator, mostly where a
    colon or a full stop reads better, and they are noisy at small sizes."""
    web = pathlib.Path(__file__).resolve().parent.parent / "wheelhat" / "web"
    offenders = []
    for path in list(web.glob("*.html")) + list((web / "static" / "js").glob("*.js")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "—" in line:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"em dashes in user-facing text: {offenders}"


def test_the_simulator_covers_every_trigger_type_a_wheel_can_use():
    """The test card offers whichever trigger types your wheels use, so every
    one of them has to survive normalise() - it used to only ever send a
    channel point redemption, which a non-affiliate channel cannot produce."""
    from wheelhat.triggers import normalise

    samples = {
        "channel_points": (
            "channel.channel_points_custom_reward_redemption.add",
            {"id": "t", "user_name": "V", "reward": {"id": "", "title": "Spin", "cost": 100}},
        ),
        "chat_command": (
            "channel.chat.message",
            {"chatter_user_name": "V", "message": {"text": "!spin"}, "badges": []},
        ),
        "cheer": ("channel.cheer", {"user_name": "V", "bits": 100}),
        "subscription": ("channel.subscribe", {"user_name": "V", "tier": "1000"}),
        "follow": ("channel.follow", {"user_name": "V"}),
        "raid": ("channel.raid", {"from_broadcaster_user_name": "V", "viewers": 25}),
    }
    for expected, (event_type, event) in samples.items():
        data = normalise(event_type, event)
        assert data is not None, f"{event_type} did not normalise"
        assert data["trigger_type"] == expected
        assert data["user"] == "V", f"{expected} lost the viewer name"


async def test_a_wheel_summary_says_whether_it_can_spin(client):
    """The Wheels page disables Spin on a wheel with nothing to land on, rather
    than letting the server refuse it with a 409 after the click."""
    made = (await client.post("/api/wheels", json={})).json()
    listed = (await client.get("/api/wheels")).json()["wheels"]
    summary = next(w for w in listed if w["id"] == made["id"])
    assert summary["spinnable_count"] > 0

    for slice_ in made["slices"]:
        slice_["enabled"] = False
    await client.put(f"/api/wheels/{made['id']}", json=made)

    listed = (await client.get("/api/wheels")).json()["wheels"]
    summary = next(w for w in listed if w["id"] == made["id"])
    assert summary["spinnable_count"] == 0, "with every slice off there is nothing to spin"

    # And the server agrees, which is what the button is saving the user from.
    refused = await client.post(f"/api/wheels/{made['id']}/spin", json={"source": "manual"})
    assert refused.status_code == 409


def test_no_hand_rolled_plurals_in_the_interface():
    """core.js has plural(); three places wrote "(s)" instead and one did it
    properly, which is three ways of saying the same thing."""
    js = pathlib.Path(__file__).resolve().parent.parent / "wheelhat" / "web" / "static" / "js"
    offenders = []
    for path in sorted(js.glob("*.js")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\(s\)`|\(s\)'|\(s\)\"", line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"use plural() instead: {offenders}"


def test_the_seed_wheel_description_is_not_a_tutorial():
    """It renders on the wheel card for as long as the wheel exists."""
    from wheelhat.seed import ensure_starter_wheel

    ensure_starter_wheel()
    wheel = next(w for w in db.list_wheels() if w.name == "Punishment Wheel")
    description = wheel.description
    assert len(description) < 90, f"too long for a card subtitle: {description!r}"
    for imperative in ("open one", "then enable", "point it at"):
        assert imperative not in description.lower(), "instructions belong in the UI, not in data"


def test_no_stray_control_characters_in_tracked_files():
    """Escape sequences written through tooling have been mangled three times:
    a backspace in the changelog, and twice a CSS escape that became a literal
    0x15 and rendered as the text "b8" where an arrow belonged."""
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent
    listing = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=False
    )
    offenders = []
    for name in listing.stdout.split():
        path = root / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            stray = [hex(ord(c)) for c in line if ord(c) < 32 and c != "\t"]
            if stray:
                offenders.append(f"{name}:{number} {stray}")
    assert not offenders, f"control characters in tracked files: {offenders}"


async def test_background_fit_and_auto_source_round_trip(client):
    """A background with a shape is cropped by "cover"; the source can follow
    the artwork instead of the artwork being cut to the source."""
    made = (await client.post("/api/wheels", json={})).json()
    look = made["appearance"]
    assert look["background_fit"] == "cover", "unchanged for existing wheels"
    assert look["source_auto"] is True, "size follows the content until told otherwise"

    look["background_fit"] = "contain"
    look["source_auto"] = False
    look["source_width"] = 1043
    saved = (await client.put(f"/api/wheels/{made['id']}", json=made)).json()
    assert saved["appearance"]["background_fit"] == "contain"
    assert saved["appearance"]["source_auto"] is False
    assert saved["appearance"]["source_width"] == 1043

    from wheelhat.engine import render_payload

    overlay = render_payload(db.get_wheel(made["id"]))["appearance"]
    assert overlay["background_fit"] == "contain", "the overlay draws it, so it needs this"


async def test_background_fit_rejects_anything_else(client):
    """The renderer branches on this value."""
    made = (await client.post("/api/wheels", json={})).json()
    made["appearance"]["background_fit"] = "stretch"
    response = await client.put(f"/api/wheels/{made['id']}", json=made)
    assert response.status_code == 422


def test_the_release_attaches_every_file_it_globs_for():
    """A release shipped the licences and no binary.

    upload-artifact roots an artifact at the least common ancestor of its
    paths. Listing `dist/WheelHat.exe` alongside `LICENSE` moved that root from
    `dist/` to the repo root, so the exe arrived one directory deeper than the
    publish glob expected and was silently dropped. Nothing fails - the release
    is simply built without the thing people came for, and only at tag time.
    """
    import fnmatch
    import posixpath

    import yaml

    root = pathlib.Path(__file__).resolve().parent.parent
    spec = yaml.safe_load((root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))

    upload = spec["jobs"]["windows-app"]["steps"][-1]["with"]
    paths = [p.strip() for p in str(upload["path"]).splitlines() if p.strip()]
    assert len(paths) == 1, (
        "Several upload paths make the artifact root their common ancestor, "
        "which moves everything deeper. Stage files into one directory instead."
    )

    artifact_root = paths[0].rstrip("/")
    produced = ["dist/WheelHat.exe", "dist/LICENSE", "dist/THIRD-PARTY-NOTICES.md"]
    available = [f"artifacts/{upload['name']}/{posixpath.relpath(f, artifact_root)}" for f in produced]
    available += ["artifacts/dist/wheelhat-0.1.0-py3-none-any.whl"]

    globs = spec["jobs"]["publish"]["steps"][-1]["with"]["files"].split()
    unmatched = [g for g in globs if not any(fnmatch.fnmatch(a, g) for a in available)]
    assert not unmatched, f"the release would attach nothing for: {unmatched}"

    assert any("WheelHat.exe" in g for g in globs), "the release must attach the executable"


def test_a_select_shows_the_value_it_was_given():
    """h() used to set `value` with the other props, before the options existed.

    Setting .value on an empty <select> does nothing, so the control fell back
    to its first option: it displayed the wrong choice, saved correctly when
    changed, and could not be changed back, because re-picking the value it was
    already showing fires no change event.
    """
    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "wheelhat" / "web" / "static" / "js" / "core.js"
    ).read_text(encoding="utf-8")

    body = source[source.index("export function h("):]
    body = body[: body.index("\nfunction append(")]
    assert "deferredValue" in body, "value must be held back"
    assert body.index("append(el, children)") < body.index("el.value = deferredValue"), (
        "value has to be applied after the children, or a <select> cannot match it"
    )


async def test_the_removed_refresh_endpoint_is_gone(client):
    """Saving already pushes to overlays and a reconnecting source is sent
    state again, so the manual resend had nothing left to do."""
    made = (await client.post("/api/wheels", json={})).json()
    response = await client.post(f"/api/wheels/{made['id']}/refresh-overlays")
    assert response.status_code == 404


def test_redraws_do_not_throw_the_reader_back_to_the_top():
    """`.main` is the scroll container. Emptying it collapses its height, which
    clamps scrollTop to zero - so changing a dropdown, or a status arriving over
    the socket while someone reads, jumped them to the top of the page, and took
    the focus and caret of whatever they were typing in with it.

    Every redraw that is not navigation has to go through preserveView.
    """
    js = pathlib.Path(__file__).resolve().parent.parent / "wheelhat" / "web" / "static" / "js"
    offenders = []
    for name in ("views.js", "editor.js"):
        text = (js / name).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # A subscription callback that redraws without preserving position.
            if "reason)" in stripped and "draw()" in stripped and "preserveView" not in stripped:
                offenders.append(f"{name}:{number} {stripped[:60]}")
            if stripped == "return subscribe(() => draw());":
                offenders.append(f"{name}:{number} {stripped}")
    assert not offenders, f"these redraws lose the scroll position: {offenders}"


# ------------------------------------------------------------------ templates


async def test_a_template_saves_the_look_and_not_the_wheel(client):
    """Slices, their actions and triggers are what make a wheel a particular
    wheel. Inheriting them means deleting them before you can start."""
    made = (await client.post("/api/wheels", json={})).json()
    made["appearance"]["rim_color"] = "#ff00aa"
    made["appearance"]["label_color"] = "#101010"
    made["spin"]["duration_ms"] = 9000
    await client.put(f"/api/wheels/{made['id']}", json=made)

    saved = (await client.post("/api/templates", json={"wheel_id": made["id"]})).json()
    assert saved["appearance"]["rim_color"] == "#ff00aa"
    assert saved["spin"]["duration_ms"] == 9000
    assert "slices" not in saved
    assert "triggers" not in saved
    assert saved["name"].endswith("look"), "named after the wheel it came from"


async def test_a_wheel_started_from_a_template_wears_it(client):
    made = (await client.post("/api/wheels", json={})).json()
    made["appearance"]["rim_color"] = "#00ddff"
    await client.put(f"/api/wheels/{made['id']}", json=made)
    template = (await client.post("/api/templates", json={"wheel_id": made["id"], "name": "Neon"})).json()

    fresh = (await client.post("/api/wheels", json={"template_id": template["id"]})).json()
    assert fresh["appearance"]["rim_color"] == "#00ddff"
    assert fresh["id"] != made["id"]
    assert fresh["slices"], "a new wheel still gets slices to start from"
    assert all(not s["actions"] for s in fresh["slices"]), "and none of them carry actions"
    assert fresh["triggers"] == [], "nor any triggers"


async def test_editing_a_wheel_does_not_rewrite_its_template(client):
    """The template is a copy taken at the time, not a live link."""
    made = (await client.post("/api/wheels", json={})).json()
    made["appearance"]["rim_color"] = "#111111"
    await client.put(f"/api/wheels/{made['id']}", json=made)
    template = (await client.post("/api/templates", json={"wheel_id": made["id"]})).json()

    made["appearance"]["rim_color"] = "#eeeeee"
    await client.put(f"/api/wheels/{made['id']}", json=made)

    again = (await client.get(f"/api/templates/{template['id']}")).json()
    assert again["appearance"]["rim_color"] == "#111111"


async def test_templates_can_be_listed_renamed_and_deleted(client):
    created = (await client.post("/api/templates", json={"name": "Plain"})).json()
    listed = (await client.get("/api/templates")).json()["templates"]
    assert any(t["id"] == created["id"] and t["name"] == "Plain" for t in listed)
    assert "palette" in listed[0], "the card shows a swatch"

    renamed = (await client.put(f"/api/templates/{created['id']}", json={"name": "Neon"})).json()
    assert renamed["name"] == "Neon"

    blank = await client.put(f"/api/templates/{created['id']}", json={"name": "   "})
    assert blank.status_code == 422

    assert (await client.delete(f"/api/templates/{created['id']}")).status_code == 200
    assert (await client.delete(f"/api/templates/{created['id']}")).status_code == 404


async def test_an_unknown_template_is_refused_rather_than_ignored(client):
    response = await client.post("/api/wheels", json={"template_id": "tpl_nope"})
    assert response.status_code == 404
