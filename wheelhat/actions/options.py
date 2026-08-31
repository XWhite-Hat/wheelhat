"""Live option lists for dropdowns in the action editor.

This is what turns "type the destination and payload yourself" into "pick your
scene from a list": the editor asks for e.g. ``obs.scenes`` and gets the real
names straight out of the running application.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import db
from ..integrations.base import ConnectorError, ConnectorState
from ..integrations.registry import KIND_LABELS, registry

TEXT_INPUT_KINDS = ("text_gdiplus", "text_ft2_source", "text_pango")
MEDIA_INPUT_KINDS = ("ffmpeg_source", "vlc_source")


class OptionError(RuntimeError):
    """Raised with a message the UI can show inline under the field."""


def _opt(value: str, label: str | None = None, group: str = "") -> dict[str, Any]:
    entry = {"value": value, "label": label if label is not None else value}
    if group:
        entry["group"] = group
    return entry


def _require(kind: str, integration_id: str):
    try:
        connector = registry.resolve(kind, integration_id)
    except ConnectorError as exc:
        raise OptionError(str(exc)) from exc
    if connector.state is not ConnectorState.CONNECTED:
        label = KIND_LABELS.get(kind, kind)
        detail = connector.last_error or connector.state.value
        raise OptionError(f"{label} is not connected ({detail})")
    return connector


# ------------------------------------------------------------------- resolvers


async def _integrations_of_kind(kind: str, params: dict[str, str]) -> list[dict[str, Any]]:
    out = [_opt("", f"First available {KIND_LABELS.get(kind, kind)}")]
    for cfg in registry.configs():
        if cfg.kind != kind:
            continue
        connector = registry.get(cfg.id)
        state = connector.state.value if connector else "disabled"
        out.append(_opt(cfg.id, f"{cfg.name or cfg.id} ({state})"))
    return out


async def obs_scenes(params: dict[str, str]) -> list[dict[str, Any]]:
    obs = _require("obs", params.get("integration", ""))
    return [_opt(s.get("sceneName", "")) for s in await obs.scenes()]


async def obs_scene_sources(params: dict[str, str]) -> list[dict[str, Any]]:
    obs = _require("obs", params.get("integration", ""))
    scene = params.get("scene", "")
    if not scene:
        raise OptionError("Pick a scene first")
    items = await obs.scene_items(scene)
    return [_opt(i.get("sourceName", "")) for i in items]


async def obs_sources(params: dict[str, str]) -> list[dict[str, Any]]:
    obs = _require("obs", params.get("integration", ""))
    return [
        _opt(item["source"], f"{item['source']}", group=item["scene"])
        for item in await obs.all_sources()
    ]


async def obs_filterable(params: dict[str, str]) -> list[dict[str, Any]]:
    obs = _require("obs", params.get("integration", ""))
    out = [_opt(i.get("inputName", ""), group="Sources") for i in await obs.inputs()]
    out += [_opt(s.get("sceneName", ""), group="Scenes") for s in await obs.scenes()]
    return out


async def obs_filters(params: dict[str, str]) -> list[dict[str, Any]]:
    obs = _require("obs", params.get("integration", ""))
    source = params.get("source", "")
    if not source:
        raise OptionError("Pick a source first")
    return [_opt(f.get("filterName", "")) for f in await obs.filters(source)]


async def obs_text_inputs(params: dict[str, str]) -> list[dict[str, Any]]:
    obs = _require("obs", params.get("integration", ""))
    return [
        _opt(i.get("inputName", ""))
        for i in await obs.inputs()
        if str(i.get("inputKind", "")).startswith(TEXT_INPUT_KINDS)
    ]


async def obs_media_inputs(params: dict[str, str]) -> list[dict[str, Any]]:
    obs = _require("obs", params.get("integration", ""))
    return [
        _opt(i.get("inputName", ""))
        for i in await obs.inputs()
        if str(i.get("inputKind", "")) in MEDIA_INPUT_KINDS
    ]


async def obs_hotkeys(params: dict[str, str]) -> list[dict[str, Any]]:
    obs = _require("obs", params.get("integration", ""))
    return [_opt(name) for name in await obs.hotkeys()]


async def vts_hotkeys(params: dict[str, str]) -> list[dict[str, Any]]:
    vts = _require("vtube_studio", params.get("integration", ""))
    out = []
    for hotkey in await vts.hotkeys():
        name = hotkey.get("name") or hotkey.get("file") or hotkey.get("hotkeyID", "")
        kind = hotkey.get("type", "")
        out.append(_opt(hotkey.get("hotkeyID", ""), f"{name}  ·  {kind}" if kind else name))
    return out


async def vts_models(params: dict[str, str]) -> list[dict[str, Any]]:
    vts = _require("vtube_studio", params.get("integration", ""))
    return [
        _opt(m.get("modelID", ""), m.get("modelName", "") + (" (loaded)" if m.get("modelLoaded") else ""))
        for m in await vts.models()
    ]


async def vts_expressions(params: dict[str, str]) -> list[dict[str, Any]]:
    vts = _require("vtube_studio", params.get("integration", ""))
    return [_opt(e.get("file", ""), e.get("name") or e.get("file", "")) for e in await vts.expressions()]


async def sb_actions(params: dict[str, str]) -> list[dict[str, Any]]:
    client = _require("streamer_bot", params.get("integration", ""))
    out = []
    for action in await client.actions():
        name = action.get("name", "")
        group = action.get("group") or ""
        label = name if action.get("enabled", True) else f"{name}  (disabled)"
        out.append(
            _opt(action.get("id", ""), label, group="" if group in ("", "None") else group)
        )
    return out


async def sb_code_triggers(params: dict[str, str]) -> list[dict[str, Any]]:
    client = _require("streamer_bot", params.get("integration", ""))
    try:
        triggers = await client.code_triggers()
    except ConnectorError as exc:
        # GetCodeTriggers predates some Streamer.bot builds.
        raise OptionError(f"{exc} - you can still type a trigger name by hand.") from exc
    return [
        _opt(t.get("name", ""), t.get("name", ""), group=t.get("category", "") or "")
        for t in triggers
    ]


async def sb_globals(params: dict[str, str]) -> list[dict[str, Any]]:
    client = _require("streamer_bot", params.get("integration", ""))
    variables = await client.globals()
    return [_opt(name, f"{name} = {info.get('value', '')}") for name, info in variables.items()]


async def mixitup_commands(params: dict[str, str]) -> list[dict[str, Any]]:
    client = _require("mix_it_up", params.get("integration", ""))
    out = []
    for command in await client.commands():
        name = command.get("Name", "")
        label = name if command.get("IsEnabled", True) else f"{name}  (disabled)"
        out.append(_opt(command.get("ID", ""), label, group=command.get("Type", "") or ""))
    return out


async def twitch_rewards(params: dict[str, str]) -> list[dict[str, Any]]:
    from ..twitch.service import twitch  # late import: twitch imports the wheel engine

    try:
        rewards = await twitch.list_rewards()
    except Exception as exc:  # noqa: BLE001
        raise OptionError(str(exc)) from exc
    return [
        _opt(r.get("id", ""), f"{r.get('title', '')}  ·  {r.get('cost', 0)} pts")
        for r in rewards
    ]


async def twitch_rewards_manageable(params: dict[str, str]) -> list[dict[str, Any]]:
    """Only the rewards WheelHat created.

    Twitch refuses to close a redemption for a reward made by anything else, so
    this is what decides whether the closing options can honestly be offered.
    """
    from ..twitch.service import twitch

    try:
        rewards = await twitch.list_rewards(manageable_only=True)
    except Exception as exc:  # noqa: BLE001
        raise OptionError(str(exc)) from exc
    return [
        _opt(r.get("id", ""), f"{r.get('title', '')}  ·  {r.get('cost', 0)} pts")
        for r in rewards
    ]


async def wheels(params: dict[str, str]) -> list[dict[str, Any]]:
    out = [_opt("", "The wheel that was spun")]
    out += [_opt(w.id, w.name) for w in db.list_wheels()]
    return out


RESOLVERS: dict[str, Callable[[dict[str, str]], Any]] = {
    "integrations.obs": lambda p: _integrations_of_kind("obs", p),
    "integrations.vtube_studio": lambda p: _integrations_of_kind("vtube_studio", p),
    "integrations.streamer_bot": lambda p: _integrations_of_kind("streamer_bot", p),
    "integrations.mix_it_up": lambda p: _integrations_of_kind("mix_it_up", p),
    "integrations.speaker_bot": lambda p: _integrations_of_kind("speaker_bot", p),
    "integrations.sammi": lambda p: _integrations_of_kind("sammi", p),
    "integrations.vnyan": lambda p: _integrations_of_kind("vnyan", p),
    "miu.commands": mixitup_commands,
    "sb.actions": sb_actions,
    "sb.code_triggers": sb_code_triggers,
    "sb.globals": sb_globals,
    "obs.scenes": obs_scenes,
    "obs.scene_sources": obs_scene_sources,
    "obs.sources": obs_sources,
    "obs.filterable": obs_filterable,
    "obs.filters": obs_filters,
    "obs.text_inputs": obs_text_inputs,
    "obs.media_inputs": obs_media_inputs,
    "obs.hotkeys": obs_hotkeys,
    "vts.hotkeys": vts_hotkeys,
    "vts.models": vts_models,
    "vts.expressions": vts_expressions,
    "twitch.rewards": twitch_rewards,
    "twitch.rewards.manageable": twitch_rewards_manageable,
    "wheels": wheels,
}


async def resolve(source: str, params: dict[str, str]) -> dict[str, Any]:
    resolver = RESOLVERS.get(source)
    if resolver is None:
        return {"options": [], "error": f"Unknown option source '{source}'"}
    try:
        options = await resolver(params)
    except OptionError as exc:
        return {"options": [], "error": str(exc)}
    except ConnectorError as exc:
        return {"options": [], "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"options": [], "error": f"Could not load options: {exc}"}
    return {"options": [o for o in options if o["value"] != "" or o["label"]], "error": None}
