"""Built-in action types.

Each handler receives an already-templated config dict and returns a short
human-readable description of what it did, which lands in the action log.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import Any

from .. import db
from ..httpclient import client
from ..hub import hub
from ..integrations.base import ConnectorError
from ..integrations.registry import registry
from .executor import ActionFailed, ExecContext
from .schema import Field, action_type

# --------------------------------------------------------------------------- helpers


def _obs(config: dict[str, Any]):
    try:
        return registry.resolve("obs", config.get("integration", ""))
    except ConnectorError as exc:
        raise ActionFailed(str(exc)) from exc


def _vts(config: dict[str, Any]):
    try:
        return registry.resolve("vtube_studio", config.get("integration", ""))
    except ConnectorError as exc:
        raise ActionFailed(str(exc)) from exc


def _streamerbot(config: dict[str, Any]):
    try:
        return registry.resolve("streamer_bot", config.get("integration", ""))
    except ConnectorError as exc:
        raise ActionFailed(str(exc)) from exc


def _mixitup(config: dict[str, Any]):
    try:
        return registry.resolve("mix_it_up", config.get("integration", ""))
    except ConnectorError as exc:
        raise ActionFailed(str(exc)) from exc


def _speakerbot(config: dict[str, Any]):
    try:
        return registry.resolve("speaker_bot", config.get("integration", ""))
    except ConnectorError as exc:
        raise ActionFailed(str(exc)) from exc


def _sammi(config: dict[str, Any]):
    try:
        return registry.resolve("sammi", config.get("integration", ""))
    except ConnectorError as exc:
        raise ActionFailed(str(exc)) from exc


def _vnyan(config: dict[str, Any]):
    try:
        return registry.resolve("vnyan", config.get("integration", ""))
    except ConnectorError as exc:
        raise ActionFailed(str(exc)) from exc


def _integration_field(kind: str, label: str) -> Field:
    """Connection picker for one integration kind."""
    return Field(
        key="integration",
        label="Connection",
        type="select",
        source="integrations." + kind,
        help="Leave blank to use the first enabled " + label + " connection.",
        allow_custom=False,
    )


def _parse_json(raw: Any, label: str) -> Any:
    if raw in (None, ""):
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ActionFailed(f"{label} is not valid JSON: {exc.msg} (position {exc.pos})") from exc


def _pairs(raw: Any) -> dict[str, str]:
    """Accept either a dict or the UI's list-of-pairs shape."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if str(k).strip()}
    out: dict[str, str] = {}
    for item in raw or []:
        if isinstance(item, dict) and str(item.get("key", "")).strip():
            out[str(item["key"])] = str(item.get("value", ""))
    return out


INTEGRATION_FIELD_OBS = Field(
    key="integration",
    label="Connection",
    type="select",
    source="integrations.obs",
    help="Leave blank to use the first enabled OBS connection.",
    allow_custom=False,
)

INTEGRATION_FIELD_VTS = Field(
    key="integration",
    label="Connection",
    type="select",
    source="integrations.vtube_studio",
    help="Leave blank to use the first enabled VTube Studio connection.",
    allow_custom=False,
)

INTEGRATION_FIELD_SB = Field(
    key="integration",
    label="Connection",
    type="select",
    source="integrations.streamer_bot",
    help="Leave blank to use the first enabled Streamer.bot connection.",
    allow_custom=False,
)

PASS_VARIABLES_FIELD = Field(
    key="pass_variables",
    label="Send the wheel result as arguments",
    type="bool",
    default=True,
    help="Available inside Streamer.bot as %winner%, %user% and so on.",
)

TOGGLE_STATES = [
    {"value": "on", "label": "Turn on"},
    {"value": "off", "label": "Turn off"},
    {"value": "toggle", "label": "Toggle"},
]


# ------------------------------------------------------------------------------ web


@action_type(
    "http_request",
    "HTTP request / webhook",
    "Web",
    description=(
        "Fire any webhook. Works with Pushcut, Home Assistant, IFTTT, Streamer.bot, n8n and anything else with a URL."
    ),
    icon="globe",
    fields=[
        Field(
            key="method",
            label="Method",
            type="select",
            default="POST",
            allow_custom=False,
            options=[{"value": m, "label": m} for m in ("GET", "POST", "PUT", "PATCH", "DELETE")],
        ),
        Field(key="url", label="URL", required=True, placeholder="https://example.com/hook"),
        Field(
            key="body_type",
            label="Body",
            type="select",
            default="json",
            allow_custom=False,
            options=[
                {"value": "none", "label": "No body"},
                {"value": "json", "label": "JSON"},
                {"value": "form", "label": "Form encoded"},
                {"value": "text", "label": "Raw text"},
            ],
        ),
        Field(
            key="body",
            label="Payload",
            type="code",
            rows=8,
            default='{\n  "winner": "{{winner|json}}",\n  "wheel": "{{wheel|json}}",\n  "user": "{{user|json}}"\n}',
            when={"field": "body_type", "not_equals": ["none"]},
            # The variable bar under the field already lists the variables.
            help="Add |json inside a JSON string to escape quotes.",
        ),
        Field(key="headers", label="Headers", type="keyvalue"),
        Field(key="timeout", label="Timeout (seconds)", type="number", default=10, minimum=1, maximum=120),
    ],
)
async def http_request(config: dict[str, Any], ctx: ExecContext) -> str:
    url = (config.get("url") or "").strip()
    if not url:
        raise ActionFailed("No URL set")
    method = (config.get("method") or "POST").upper()
    body_type = config.get("body_type", "json")
    headers = _pairs(config.get("headers"))
    timeout = float(config.get("timeout") or 10)

    kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout}
    if body_type == "json" and method != "GET":
        kwargs["json"] = _parse_json(config.get("body"), "Payload")
    elif body_type == "form" and method != "GET":
        kwargs["data"] = _parse_json(config.get("body"), "Payload")
    elif body_type == "text" and method != "GET":
        kwargs["content"] = str(config.get("body") or "")
        headers.setdefault("Content-Type", "text/plain; charset=utf-8")

    try:
        response = await client().request(method, url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - network errors are expected
        raise ActionFailed(f"Request to {url} failed: {exc}") from exc

    snippet = response.text[:200].replace("\n", " ")
    if response.status_code >= 400:
        raise ActionFailed(f"{method} {url} -> HTTP {response.status_code}: {snippet}")
    return f"{method} {url} -> HTTP {response.status_code}"


@action_type(
    "discord_webhook",
    "Discord message",
    "Web",
    description="Post to a Discord channel via a webhook URL.",
    icon="message",
    fields=[
        Field(key="url", label="Webhook URL", required=True, placeholder="https://discord.com/api/webhooks/..."),
        Field(
            key="content",
            label="Message",
            type="textarea",
            default="🎡 **{{wheel}}** landed on **{{winner}}** (spun by {{user}})",
        ),
        Field(key="username", label="Override username", placeholder="WheelHat"),
        Field(key="avatar_url", label="Override avatar URL"),
    ],
)
async def discord_webhook(config: dict[str, Any], ctx: ExecContext) -> str:
    url = (config.get("url") or "").strip()
    if not url:
        raise ActionFailed("No webhook URL set")
    payload: dict[str, Any] = {"content": config.get("content") or ""}
    if config.get("username"):
        payload["username"] = config["username"]
    if config.get("avatar_url"):
        payload["avatar_url"] = config["avatar_url"]
    response = await client().post(url, json=payload)
    if response.status_code >= 400:
        raise ActionFailed(f"Discord returned HTTP {response.status_code}: {response.text[:200]}")
    return "Posted to Discord"


@action_type(
    "pushover",
    "Pushover notification",
    "Web",
    description="Send a push notification to your phone via Pushover.",
    icon="bell",
    fields=[
        Field(key="token", label="Application token", required=True),
        Field(key="user", label="User / group key", required=True),
        Field(key="title", label="Title", default="{{wheel}}"),
        Field(key="message", label="Message", type="textarea", default="The wheel landed on {{winner}}"),
        Field(
            key="priority",
            label="Priority",
            type="select",
            default="0",
            allow_custom=False,
            options=[
                {"value": "-2", "label": "Lowest (no alert)"},
                {"value": "-1", "label": "Low"},
                {"value": "0", "label": "Normal"},
                {"value": "1", "label": "High"},
            ],
        ),
        Field(key="sound", label="Sound", placeholder="pushover"),
    ],
)
async def pushover(config: dict[str, Any], ctx: ExecContext) -> str:
    data = {
        "token": config.get("token", ""),
        "user": config.get("user", ""),
        "title": config.get("title", ""),
        "message": config.get("message", "") or "(no message)",
        "priority": config.get("priority", "0"),
    }
    if config.get("sound"):
        data["sound"] = config["sound"]
    response = await client().post("https://api.pushover.net/1/messages.json", data=data)
    if response.status_code >= 400:
        raise ActionFailed(f"Pushover returned HTTP {response.status_code}: {response.text[:200]}")
    return "Pushover notification sent"


# ------------------------------------------------------------------------------ OBS


@action_type(
    "obs_scene",
    "Switch scene",
    "OBS Studio",
    description="Change the program (or preview) scene.",
    icon="layers",
    requires="obs",
    fields=[
        INTEGRATION_FIELD_OBS,
        Field(key="scene", label="Scene", type="select", source="obs.scenes", required=True),
        Field(
            key="target",
            label="Target",
            type="select",
            default="program",
            allow_custom=False,
            options=[
                {"value": "program", "label": "Program"},
                {"value": "preview", "label": "Preview (studio mode)"},
            ],
        ),
    ],
)
async def obs_scene(config: dict[str, Any], ctx: ExecContext) -> str:
    obs = _obs(config)
    scene = config.get("scene", "")
    if not scene:
        raise ActionFailed("No scene selected")
    request = "SetCurrentPreviewScene" if config.get("target") == "preview" else "SetCurrentProgramScene"
    await obs.request(request, {"sceneName": scene})
    return f"Switched {config.get('target', 'program')} scene to '{scene}'"


@action_type(
    "obs_source_visibility",
    "Show / hide a source",
    "OBS Studio",
    description="Toggle a source's visibility inside a scene.",
    icon="eye",
    requires="obs",
    fields=[
        INTEGRATION_FIELD_OBS,
        Field(key="scene", label="Scene", type="select", source="obs.scenes", required=True),
        Field(
            key="source",
            label="Source",
            type="select",
            source="obs.scene_sources",
            depends_on=["scene"],
            required=True,
        ),
        Field(key="state", label="Action", type="select", default="on", allow_custom=False, options=TOGGLE_STATES),
    ],
)
async def obs_source_visibility(config: dict[str, Any], ctx: ExecContext) -> str:
    obs = _obs(config)
    scene, source = config.get("scene", ""), config.get("source", "")
    if not scene or not source:
        raise ActionFailed("Pick a scene and a source")
    item_id = await obs.scene_item_id(scene, source)
    state = config.get("state", "on")
    if state == "toggle":
        current = await obs.request(
            "GetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item_id}
        )
        enabled = not current.get("sceneItemEnabled", False)
    else:
        enabled = state == "on"
    await obs.request(
        "SetSceneItemEnabled",
        {"sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": enabled},
    )
    return f"{'Showed' if enabled else 'Hid'} '{source}' in '{scene}'"


@action_type(
    "obs_filter",
    "Toggle a filter",
    "OBS Studio",
    description="Enable or disable a filter on a source.",
    icon="sliders",
    requires="obs",
    fields=[
        INTEGRATION_FIELD_OBS,
        Field(key="source", label="Source", type="select", source="obs.filterable", required=True),
        Field(
            key="filter",
            label="Filter",
            type="select",
            source="obs.filters",
            depends_on=["source"],
            required=True,
        ),
        Field(key="state", label="Action", type="select", default="on", allow_custom=False, options=TOGGLE_STATES),
    ],
)
async def obs_filter(config: dict[str, Any], ctx: ExecContext) -> str:
    obs = _obs(config)
    source, filter_name = config.get("source", ""), config.get("filter", "")
    if not source or not filter_name:
        raise ActionFailed("Pick a source and a filter")
    state = config.get("state", "on")
    if state == "toggle":
        current = await obs.request(
            "GetSourceFilter", {"sourceName": source, "filterName": filter_name}
        )
        enabled = not current.get("filterEnabled", False)
    else:
        enabled = state == "on"
    await obs.request(
        "SetSourceFilterEnabled",
        {"sourceName": source, "filterName": filter_name, "filterEnabled": enabled},
    )
    return f"{'Enabled' if enabled else 'Disabled'} filter '{filter_name}' on '{source}'"


@action_type(
    "obs_text",
    "Set text of a source",
    "OBS Studio",
    description="Write the winner (or anything else) into a GDI+/FreeType text source.",
    icon="type",
    requires="obs",
    fields=[
        INTEGRATION_FIELD_OBS,
        Field(key="source", label="Text source", type="select", source="obs.text_inputs", required=True),
        Field(key="text", label="Text", type="textarea", default="{{winner}}", rows=3),
    ],
)
async def obs_text(config: dict[str, Any], ctx: ExecContext) -> str:
    obs = _obs(config)
    source = config.get("source", "")
    if not source:
        raise ActionFailed("Pick a text source")
    await obs.request(
        "SetInputSettings",
        {"inputName": source, "inputSettings": {"text": config.get("text", "")}},
    )
    return f"Set text of '{source}'"


@action_type(
    "obs_media",
    "Control a media source",
    "OBS Studio",
    description="Play, pause, restart or stop a media source.",
    icon="play",
    requires="obs",
    fields=[
        INTEGRATION_FIELD_OBS,
        Field(key="source", label="Media source", type="select", source="obs.media_inputs", required=True),
        Field(
            key="action",
            label="Action",
            type="select",
            default="restart",
            allow_custom=False,
            options=[
                {"value": "restart", "label": "Restart"},
                {"value": "play", "label": "Play"},
                {"value": "pause", "label": "Pause"},
                {"value": "stop", "label": "Stop"},
                {"value": "next", "label": "Next"},
                {"value": "previous", "label": "Previous"},
            ],
        ),
    ],
)
async def obs_media(config: dict[str, Any], ctx: ExecContext) -> str:
    obs = _obs(config)
    source = config.get("source", "")
    if not source:
        raise ActionFailed("Pick a media source")
    mapping = {
        "restart": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
        "play": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY",
        "pause": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE",
        "stop": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP",
        "next": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_NEXT",
        "previous": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PREVIOUS",
    }
    action = config.get("action", "restart")
    await obs.request(
        "TriggerMediaInputAction",
        {"inputName": source, "mediaAction": mapping.get(action, mapping["restart"])},
    )
    return f"Media '{source}': {action}"


@action_type(
    "obs_hotkey",
    "Trigger a hotkey",
    "OBS Studio",
    description="Fire any OBS hotkey by name, including ones from plugins.",
    icon="keyboard",
    requires="obs",
    fields=[
        INTEGRATION_FIELD_OBS,
        Field(key="hotkey", label="Hotkey", type="select", source="obs.hotkeys", required=True),
    ],
)
async def obs_hotkey(config: dict[str, Any], ctx: ExecContext) -> str:
    obs = _obs(config)
    hotkey = config.get("hotkey", "")
    if not hotkey:
        raise ActionFailed("Pick a hotkey")
    await obs.request("TriggerHotkeyByName", {"hotkeyName": hotkey})
    return f"Triggered OBS hotkey '{hotkey}'"


@action_type(
    "obs_control",
    "Recording / streaming control",
    "OBS Studio",
    description="Start or stop recording, streaming, the virtual camera or the replay buffer.",
    icon="record",
    requires="obs",
    fields=[
        INTEGRATION_FIELD_OBS,
        Field(
            key="target",
            label="Target",
            type="select",
            default="recording",
            allow_custom=False,
            options=[
                {"value": "recording", "label": "Recording"},
                {"value": "streaming", "label": "Streaming"},
                {"value": "virtualcam", "label": "Virtual camera"},
                {"value": "replay_buffer", "label": "Replay buffer"},
                {"value": "save_replay", "label": "Save replay buffer"},
            ],
        ),
        Field(
            key="action",
            label="Action",
            type="select",
            default="toggle",
            allow_custom=False,
            options=[
                {"value": "start", "label": "Start"},
                {"value": "stop", "label": "Stop"},
                {"value": "toggle", "label": "Toggle"},
            ],
            when={"field": "target", "not_equals": ["save_replay"]},
        ),
    ],
)
async def obs_control(config: dict[str, Any], ctx: ExecContext) -> str:
    obs = _obs(config)
    target = config.get("target", "recording")
    if target == "save_replay":
        await obs.request("SaveReplayBuffer")
        return "Saved replay buffer"
    action = config.get("action", "toggle")
    verbs = {
        "recording": {"start": "StartRecord", "stop": "StopRecord", "toggle": "ToggleRecord"},
        "streaming": {"start": "StartStream", "stop": "StopStream", "toggle": "ToggleStream"},
        "virtualcam": {
            "start": "StartVirtualCam",
            "stop": "StopVirtualCam",
            "toggle": "ToggleVirtualCam",
        },
        "replay_buffer": {
            "start": "StartReplayBuffer",
            "stop": "StopReplayBuffer",
            "toggle": "ToggleReplayBuffer",
        },
    }
    request = verbs[target][action]
    await obs.request(request)
    return f"{action.title()} {target.replace('_', ' ')}"


@action_type(
    "obs_raw",
    "Raw OBS request",
    "OBS Studio",
    description="Any obs-websocket request, for things with no dedicated action.",
    icon="terminal",
    requires="obs",
    fields=[
        INTEGRATION_FIELD_OBS,
        Field(key="request_type", label="Request type", required=True, placeholder="SetInputMute"),
        Field(key="request_data", label="Request data (JSON)", type="code", rows=6, default="{}"),
    ],
)
async def obs_raw(config: dict[str, Any], ctx: ExecContext) -> str:
    obs = _obs(config)
    request_type = (config.get("request_type") or "").strip()
    if not request_type:
        raise ActionFailed("No request type set")
    data = _parse_json(config.get("request_data"), "Request data")
    result = await obs.request(request_type, data)
    return f"{request_type} -> {json.dumps(result)[:200]}"


# --------------------------------------------------------------------- VTube Studio


@action_type(
    "vts_hotkey",
    "Trigger a hotkey",
    "VTube Studio",
    description="Trigger a VTube Studio hotkey: outfits, accessories, animations.",
    icon="sparkles",
    requires="vtube_studio",
    fields=[
        INTEGRATION_FIELD_VTS,
        Field(key="hotkey", label="Hotkey", type="select", source="vts.hotkeys", required=True, allow_custom=False),
    ],
)
async def vts_hotkey(config: dict[str, Any], ctx: ExecContext) -> str:
    vts = _vts(config)
    hotkey = config.get("hotkey", "")
    if not hotkey:
        raise ActionFailed("Pick a hotkey")
    result = await vts.request("HotkeyTriggerRequest", {"hotkeyID": hotkey})
    return f"Triggered VTS hotkey {result.get('hotkeyID', hotkey)}"


@action_type(
    "vts_model",
    "Load a model",
    "VTube Studio",
    description="Swap the whole avatar.",
    icon="user",
    requires="vtube_studio",
    fields=[
        INTEGRATION_FIELD_VTS,
        Field(key="model", label="Model", type="select", source="vts.models", required=True, allow_custom=False),
    ],
)
async def vts_model(config: dict[str, Any], ctx: ExecContext) -> str:
    vts = _vts(config)
    model = config.get("model", "")
    if not model:
        raise ActionFailed("Pick a model")
    await vts.request("ModelLoadRequest", {"modelID": model})
    return f"Loaded VTS model {model}"


@action_type(
    "vts_expression",
    "Set an expression",
    "VTube Studio",
    icon="smile",
    requires="vtube_studio",
    fields=[
        INTEGRATION_FIELD_VTS,
        Field(
            key="expression",
            label="Expression",
            type="select",
            source="vts.expressions",
            required=True,
            allow_custom=False,
        ),
        Field(key="state", label="Action", type="select", default="on", allow_custom=False, options=TOGGLE_STATES),
        Field(key="fade", label="Fade time (seconds)", type="number", default=0.5, minimum=0, maximum=2, step=0.1),
    ],
)
async def vts_expression(config: dict[str, Any], ctx: ExecContext) -> str:
    vts = _vts(config)
    expression = config.get("expression", "")
    if not expression:
        raise ActionFailed("Pick an expression")
    state = config.get("state", "on")
    if state == "toggle":
        current = await vts.request("ExpressionStateRequest", {"details": False})
        active = {e.get("file"): e.get("active") for e in current.get("expressions", [])}
        enable = not active.get(expression, False)
    else:
        enable = state == "on"
    await vts.request(
        "ExpressionActivationRequest",
        {"expressionFile": expression, "active": enable, "fadeTime": float(config.get("fade") or 0.5)},
    )
    return f"{'Activated' if enable else 'Deactivated'} expression '{expression}'"


@action_type(
    "vts_move",
    "Move the model",
    "VTube Studio",
    description="Nudge, rotate or resize the avatar.",
    icon="move",
    requires="vtube_studio",
    fields=[
        INTEGRATION_FIELD_VTS,
        Field(key="time", label="Move over (seconds)", type="number", default=0.5, minimum=0, maximum=2, step=0.1),
        Field(key="relative", label="Relative to current position", type="bool", default=False),
        Field(key="x", label="X position", type="number", default=0, minimum=-10, maximum=10, step=0.05),
        Field(key="y", label="Y position", type="number", default=0, minimum=-10, maximum=10, step=0.05),
        Field(key="rotation", label="Rotation (degrees)", type="number", default=0, minimum=-360, maximum=360),
        Field(key="size", label="Size", type="number", default=0, minimum=-100, maximum=100),
    ],
)
async def vts_move(config: dict[str, Any], ctx: ExecContext) -> str:
    vts = _vts(config)
    payload = {
        "timeInSeconds": float(config.get("time") or 0.5),
        "valuesAreRelativeToModel": bool(config.get("relative")),
        "positionX": float(config.get("x") or 0),
        "positionY": float(config.get("y") or 0),
        "rotation": float(config.get("rotation") or 0),
        "size": float(config.get("size") or 0),
    }
    await vts.request("MoveModelRequest", payload)
    return "Moved VTS model"


@action_type(
    "vts_raw",
    "Raw VTube Studio request",
    "VTube Studio",
    description="Send any message type from the VTube Studio public API.",
    icon="terminal",
    requires="vtube_studio",
    fields=[
        INTEGRATION_FIELD_VTS,
        Field(key="message_type", label="Message type", required=True, placeholder="ColorTintRequest"),
        Field(key="request_data", label="Data (JSON)", type="code", rows=6, default="{}"),
    ],
)
async def vts_raw(config: dict[str, Any], ctx: ExecContext) -> str:
    vts = _vts(config)
    message_type = (config.get("message_type") or "").strip()
    if not message_type:
        raise ActionFailed("No message type set")
    data = _parse_json(config.get("request_data"), "Data")
    result = await vts.request(message_type, data)
    return f"{message_type} -> {json.dumps(result)[:200]}"


# --------------------------------------------------------------------- Streamer.bot


def _sb_args(config: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    """Merge the wheel's context variables with any arguments the user added."""
    args: dict[str, Any] = {}
    if config.get("pass_variables", True):
        args.update(ctx.as_vars())
    args.update(_pairs(config.get("args")))
    return args


@action_type(
    "streamerbot_action",
    "Run an action",
    "Streamer.bot",
    description=(
        "Run one of your Streamer.bot actions."
    ),
    icon="robot",
    requires="streamer_bot",
    fields=[
        INTEGRATION_FIELD_SB,
        Field(
            key="action",
            label="Action",
            type="select",
            source="sb.actions",
            required=True,
            help="Matched by id, so renaming it in Streamer.bot is safe.",
        ),
        Field(
            key="args",
            label="Extra arguments",
            type="keyvalue",
            help="Available inside Streamer.bot as %yourKey%.",
        ),
        PASS_VARIABLES_FIELD,
    ],
)
async def streamerbot_action(config: dict[str, Any], ctx: ExecContext) -> str:
    client = _streamerbot(config)
    reference = (config.get("action") or "").strip()
    if not reference:
        raise ActionFailed("Pick a Streamer.bot action")
    await client.do_action(reference, _sb_args(config, ctx))
    return f"Ran Streamer.bot action {reference}"


@action_type(
    "streamerbot_code_trigger",
    "Fire a custom trigger",
    "Streamer.bot",
    description="Fire a custom code trigger, so several Streamer.bot actions can listen for the same wheel result.",
    icon="zap",
    requires="streamer_bot",
    fields=[
        INTEGRATION_FIELD_SB,
        Field(
            key="trigger",
            label="Trigger",
            type="select",
            source="sb.code_triggers",
            required=True,
            placeholder="wheel_result",
        ),
        Field(key="args", label="Extra arguments", type="keyvalue"),
        PASS_VARIABLES_FIELD,
    ],
)
async def streamerbot_code_trigger(config: dict[str, Any], ctx: ExecContext) -> str:
    client = _streamerbot(config)
    trigger = (config.get("trigger") or "").strip()
    if not trigger:
        raise ActionFailed("Name the custom trigger to fire")
    await client.execute_code_trigger(trigger, _sb_args(config, ctx))
    return f"Fired Streamer.bot trigger '{trigger}'"


@action_type(
    "streamerbot_chat",
    "Send a chat message",
    "Streamer.bot",
    description=(
        "Send chat through Streamer.bot to YouTube, Kick or Trovo."
    ),
    icon="message",
    requires="streamer_bot",
    fields=[
        INTEGRATION_FIELD_SB,
        Field(
            key="platform",
            label="Platform",
            type="select",
            default="twitch",
            allow_custom=False,
            options=[
                {"value": "twitch", "label": "Twitch"},
                {"value": "youtube", "label": "YouTube"},
                {"value": "kick", "label": "Kick"},
                {"value": "trovo", "label": "Trovo"},
            ],
        ),
        Field(
            key="message",
            label="Message",
            type="textarea",
            default="🎡 The wheel landed on {{winner}}",
            required=True,
        ),
        Field(key="bot", label="Send from the bot account", type="bool", default=False),
        Field(
            key="internal",
            label="Mark as internal",
            type="bool",
            default=True,
            help="Internal messages can be ignored by your own commands, which stops loops.",
        ),
    ],
)
async def streamerbot_chat(config: dict[str, Any], ctx: ExecContext) -> str:
    client = _streamerbot(config)
    message = (config.get("message") or "").strip()
    if not message:
        raise ActionFailed("Message is empty")
    platform = config.get("platform", "twitch")
    try:
        await client.send_message(
            platform,
            message,
            bot=bool(config.get("bot")),
            internal=bool(config.get("internal", True)),
        )
    except ConnectorError as exc:
        raise ActionFailed(str(exc)) from exc
    return f"Sent to {platform} chat via Streamer.bot"


@action_type(
    "streamerbot_raw",
    "Raw Streamer.bot request",
    "Streamer.bot",
    description="Send any request the Streamer.bot WebSocket API supports.",
    icon="terminal",
    requires="streamer_bot",
    fields=[
        INTEGRATION_FIELD_SB,
        Field(key="request", label="Request", required=True, placeholder="GetActiveViewers"),
        Field(key="payload", label="Payload (JSON)", type="code", rows=6, default="{}"),
    ],
)
async def streamerbot_raw(config: dict[str, Any], ctx: ExecContext) -> str:
    client = _streamerbot(config)
    request = (config.get("request") or "").strip()
    if not request:
        raise ActionFailed("No request name set")
    payload = _parse_json(config.get("payload"), "Payload")
    result = await client.request(request, payload)
    return f"{request} -> {json.dumps(result)[:200]}"


# --------------------------------------------------------------------------- Twitch


@action_type(
    "twitch_chat",
    "Send a chat message",
    "Twitch",
    description="Announce the result in your own chat.",
    icon="message",
    requires="twitch",
    fields=[
        Field(
            key="message",
            label="Message",
            type="textarea",
            default="🎡 {{user}} spun {{wheel}} and got: {{winner}}",
            required=True,
        ),
        Field(key="reply_to_trigger", label="Reply to the triggering message", type="bool", default=False),
    ],
)
async def twitch_chat(config: dict[str, Any], ctx: ExecContext) -> str:
    from ..twitch.service import twitch  # imported late to avoid an import cycle

    message = (config.get("message") or "").strip()
    if not message:
        raise ActionFailed("Message is empty")
    reply_to = ""
    if config.get("reply_to_trigger"):
        reply_to = str(ctx.variables.get("message_id") or "")
    try:
        await twitch.send_chat(message, reply_parent_message_id=reply_to or None)
    except Exception as exc:  # noqa: BLE001
        raise ActionFailed(str(exc)) from exc
    return f"Sent to chat: {message[:80]}"


@action_type(
    "twitch_refund",
    "Refund / fulfil the redemption",
    "Twitch",
    description=(
        "Mark the channel point redemption that triggered this spin as fulfilled or cancelled (refunding the points)."
    ),
    icon="undo",
    requires="twitch",
    fields=[
        Field(
            key="status",
            label="Set status to",
            type="select",
            default="FULFILLED",
            allow_custom=False,
            options=[
                {"value": "FULFILLED", "label": "Fulfilled (keep the points)"},
                {"value": "CANCELED", "label": "Cancelled (refund the points)"},
            ],
        )
    ],
)
async def twitch_refund(config: dict[str, Any], ctx: ExecContext) -> str:
    from ..twitch.service import twitch

    reward_id = str(ctx.variables.get("reward_id") or "")
    redemption_id = str(ctx.variables.get("redemption_id") or "")
    if not reward_id or not redemption_id:
        raise ActionFailed("This spin was not started by a channel point redemption")
    status = config.get("status", "FULFILLED")
    await twitch.update_redemption(reward_id, redemption_id, status)
    return f"Redemption marked {status}"


# -------------------------------------------------------------------------- overlay


@action_type(
    "overlay_message",
    "Show a message on the overlay",
    "Overlay",
    description="Flash text over the wheel's browser source.",
    icon="megaphone",
    fields=[
        Field(key="text", label="Text", type="textarea", default="{{winner}}!", required=True),
        Field(key="duration", label="Duration (ms)", type="number", default=4000, minimum=250, maximum=60000),
        Field(
            key="style",
            label="Style",
            type="select",
            default="banner",
            allow_custom=False,
            options=[
                {"value": "banner", "label": "Banner"},
                {"value": "toast", "label": "Corner toast"},
                {"value": "fullscreen", "label": "Full screen"},
            ],
        ),
        Field(
            key="target",
            label="Show on",
            type="select",
            default="",
            source="wheels",
            help="Blank means the wheel that was spun.",
        ),
    ],
)
async def overlay_message(config: dict[str, Any], ctx: ExecContext) -> str:
    wheel_id = (config.get("target") or "").strip() or ctx.wheel_id
    payload = {
        "type": "overlay_message",
        "text": config.get("text", ""),
        "duration": int(config.get("duration") or 4000),
        "style": config.get("style", "banner"),
    }
    await hub.broadcast_overlay(wheel_id, payload)
    return f"Showed overlay message on {wheel_id}"


@action_type(
    "overlay_sound",
    "Play a sound on the overlay",
    "Overlay",
    description=(
        "Play an audio file through the browser source. Point it at a file you dropped in the assets folder or any "
        "public URL."
    ),
    icon="volume",
    fields=[
        Field(key="url", label="Sound URL or asset name", required=True, placeholder="/assets/airhorn.mp3"),
        Field(key="volume", label="Volume", type="number", default=0.8, minimum=0, maximum=1, step=0.05),
        Field(key="target", label="Play on", type="select", default="", source="wheels"),
    ],
)
async def overlay_sound(config: dict[str, Any], ctx: ExecContext) -> str:
    wheel_id = (config.get("target") or "").strip() or ctx.wheel_id
    url = (config.get("url") or "").strip()
    if not url:
        raise ActionFailed("No sound URL set")
    await hub.broadcast_overlay(
        wheel_id,
        {"type": "overlay_sound", "url": url, "volume": float(config.get("volume") or 0.8)},
    )
    return f"Played {url}"


# ----------------------------------------------------------------------------- flow


@action_type(
    "delay",
    "Wait",
    "Flow",
    description="Pause before the next action in the chain.",
    icon="clock",
    fields=[
        Field(key="seconds", label="Seconds", type="number", default=1, minimum=0, maximum=600, step=0.1),
    ],
)
async def delay(config: dict[str, Any], ctx: ExecContext) -> str:
    seconds = max(0.0, float(config.get("seconds") or 0))
    await asyncio.sleep(seconds)
    return f"Waited {seconds:g}s"


@action_type(
    "shell_command",
    "Run a program",
    "System",
    description="Launch a local program or script.",
    icon="terminal",
    fields=[
        Field(key="command", label="Command", required=True, placeholder="C:\\tools\\confetti.exe"),
        Field(key="args", label="Arguments", placeholder="--winner \"{{winner}}\""),
        Field(key="wait", label="Wait for it to finish", type="bool", default=False),
        Field(
            key="timeout",
            label="Timeout (seconds)",
            type="number",
            default=20,
            minimum=1,
            maximum=600,
            when={"field": "wait", "equals": [True]},
        ),
    ],
)
async def shell_command(config: dict[str, Any], ctx: ExecContext) -> str:
    if not db.get_setting("allow_shell_actions", False):
        raise ActionFailed(
            "Shell actions are disabled. Turn on 'Allow shell actions' in Settings if you want this."
        )
    command = (config.get("command") or "").strip()
    if not command:
        raise ActionFailed("No command set")
    try:
        args = shlex.split(config.get("args") or "", posix=False)
    except ValueError as exc:
        raise ActionFailed(f"Could not parse arguments: {exc}") from exc

    process = await asyncio.create_subprocess_exec(
        command,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if not config.get("wait"):
        return f"Launched {command}"
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=float(config.get("timeout") or 20)
        )
    except asyncio.TimeoutError:
        process.kill()
        raise ActionFailed(f"{command} did not finish in time") from None
    output = (stdout or b"").decode("utf-8", "replace").strip()[:300]
    if process.returncode != 0:
        raise ActionFailed(f"{command} exited with {process.returncode}: {output}")
    return f"{command} finished{': ' + output if output else ''}"


# ------------------------------------------------------------------ Mix It Up


@action_type(
    "mixitup_command",
    "Run a command",
    "Mix It Up",
    description="Run one of your Mix It Up commands.",
    icon="robot",
    requires="mix_it_up",
    fields=[
        _integration_field("mix_it_up", "Mix It Up"),
        Field(
            key="command",
            label="Command",
            type="select",
            source="miu.commands",
            required=True,
            help="Selected by id, so renaming it in Mix It Up will not break this.",
        ),
        Field(
            key="arguments",
            label="Arguments",
            placeholder="{{winner}}",
            help="Passed to the command as though a viewer had typed them.",
        ),
        Field(
            key="ignore_requirements",
            label="Ignore the command's requirements",
            type="bool",
            default=True,
            help="Skips cooldowns, currency costs and role checks that would otherwise block it.",
        ),
    ],
)
async def mixitup_command(config: dict[str, Any], ctx: ExecContext) -> str:
    client_ = _mixitup(config)
    command_id = (config.get("command") or "").strip()
    if not command_id:
        raise ActionFailed("Pick a Mix It Up command")
    await client_.run_command(
        command_id,
        arguments=config.get("arguments", "") or "",
        ignore_requirements=bool(config.get("ignore_requirements", True)),
    )
    return f"Ran Mix It Up command {command_id}"


# ----------------------------------------------------------------- Speaker.bot


@action_type(
    "speakerbot_speak",
    "Speak a message",
    "Speaker.bot",
    description="Read something out through Speaker.bot's text to speech.",
    icon="speaker",
    requires="speaker_bot",
    fields=[
        _integration_field("speaker_bot", "Speaker.bot"),
        Field(
            key="message",
            label="Message",
            type="textarea",
            default="The wheel has spoken. {{user}} gets {{winner}}.",
            required=True,
        ),
        Field(
            key="voice",
            label="Voice alias",
            placeholder="Brian",
            help=(
                "The alias as named in Speaker.bot. Blank uses the default voice."
            ),
        ),
        Field(key="bad_word_filter", label="Apply the bad word filter", type="bool", default=True),
    ],
)
async def speakerbot_speak(config: dict[str, Any], ctx: ExecContext) -> str:
    client_ = _speakerbot(config)
    message = (config.get("message") or "").strip()
    if not message:
        raise ActionFailed("Message is empty")
    await client_.speak(
        message,
        voice=(config.get("voice") or "").strip(),
        bad_word_filter=bool(config.get("bad_word_filter", True)),
    )
    return f"Speaker.bot said: {message[:70]}"


@action_type(
    "speakerbot_control",
    "Control the TTS queue",
    "Speaker.bot",
    description="Pause, resume or clear the speech queue, or switch TTS off entirely.",
    icon="sliders",
    requires="speaker_bot",
    fields=[
        _integration_field("speaker_bot", "Speaker.bot"),
        Field(
            key="command",
            label="Do what",
            type="select",
            default="Clear",
            allow_custom=False,
            options=[
                {"value": "Pause", "label": "Pause the queue"},
                {"value": "Resume", "label": "Resume the queue"},
                {"value": "Clear", "label": "Clear pending speech"},
                {"value": "Stop", "label": "Stop what is being said now"},
                {"value": "Enable", "label": "Enable TTS"},
                {"value": "Disable", "label": "Disable TTS"},
            ],
        ),
    ],
)
async def speakerbot_control(config: dict[str, Any], ctx: ExecContext) -> str:
    client_ = _speakerbot(config)
    command = config.get("command", "Clear")
    await client_.queue(command)
    return f"Speaker.bot: {command}"


# ----------------------------------------------------------------------- SAMMI


@action_type(
    "sammi_button",
    "Trigger a button",
    "SAMMI",
    description="Press one of your SAMMI buttons.",
    icon="grid",
    requires="sammi",
    fields=[
        _integration_field("sammi", "SAMMI"),
        Field(
            key="button_id",
            label="Button ID",
            required=True,
            placeholder="ID19",
            help="Copy the ID from the button in SAMMI.",
        ),
        Field(
            key="release",
            label="Release it instead of pressing it",
            type="bool",
            default=False,
        ),
    ],
)
async def sammi_button(config: dict[str, Any], ctx: ExecContext) -> str:
    client_ = _sammi(config)
    button_id = (config.get("button_id") or "").strip()
    if not button_id:
        raise ActionFailed("Enter the SAMMI button ID")
    if config.get("release"):
        await client_.release_button(button_id)
        return f"Released SAMMI button {button_id}"
    await client_.trigger_button(button_id)
    return f"Triggered SAMMI button {button_id}"


@action_type(
    "sammi_variable",
    "Set a variable",
    "SAMMI",
    description="Write a SAMMI variable - handy for showing the winner on a SAMMI deck.",
    icon="type",
    requires="sammi",
    fields=[
        _integration_field("sammi", "SAMMI"),
        Field(key="name", label="Variable name", required=True, placeholder="wheelWinner"),
        Field(key="value", label="Value", default="{{winner}}"),
        Field(
            key="button_id",
            label="Button ID",
            placeholder="ID19",
            help="Leave blank for a global variable.",
        ),
    ],
)
async def sammi_variable(config: dict[str, Any], ctx: ExecContext) -> str:
    client_ = _sammi(config)
    name = (config.get("name") or "").strip()
    if not name:
        raise ActionFailed("Name the variable to set")
    await client_.set_variable(
        name, config.get("value", ""), button_id=(config.get("button_id") or "").strip()
    )
    return f"Set SAMMI variable {name}"


# ----------------------------------------------------------------------- VNyan


@action_type(
    "vnyan_trigger",
    "Send a trigger",
    "VNyan",
    description=(
        "Fire a VNyan node graph by its Websocket Command name - the VNyan equivalent of a VTube Studio hotkey."
    ),
    icon="sparkles",
    requires="vnyan",
    fields=[
        _integration_field("vnyan", "VNyan"),
        Field(
            key="trigger",
            label="Trigger name",
            required=True,
            placeholder="cursed_outfit",
            help="Must match the Websocket Command on a VNyan trigger.",
        ),
    ],
)
async def vnyan_trigger(config: dict[str, Any], ctx: ExecContext) -> str:
    client_ = _vnyan(config)
    trigger = (config.get("trigger") or "").strip()
    if not trigger:
        raise ActionFailed("Enter the VNyan trigger name")
    await client_.trigger(trigger)
    return f"Sent VNyan trigger '{trigger}'"
