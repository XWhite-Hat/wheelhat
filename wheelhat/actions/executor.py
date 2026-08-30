"""Runs action chains and substitutes template variables."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .. import db
from ..hub import hub
from .schema import ACTION_TYPES

log = logging.getLogger("wheelhat.actions")

_TOKEN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*(?:\|\s*([a-zA-Z0-9_]+)\s*)?\}\}")


def _apply_filter(value: str, name: str | None) -> str:
    if not name:
        return value
    if name == "json":
        # Strip the surrounding quotes; the template already sits inside them.
        return json.dumps(value)[1:-1]
    if name == "url":
        return urllib.parse.quote(value, safe="")
    if name == "upper":
        return value.upper()
    if name == "lower":
        return value.lower()
    if name == "trim":
        return value.strip()
    if name == "slug":
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value


@dataclass
class ExecContext:
    """Everything an action can interpolate, plus bookkeeping for the log."""

    wheel_id: str = ""
    wheel_name: str = ""
    slice_id: str = ""
    winner: str = ""
    source: str = "manual"
    variables: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False

    def as_vars(self) -> dict[str, str]:
        now = time.time()
        base: dict[str, Any] = {
            "winner": self.winner,
            "wheel": self.wheel_name,
            "wheel_id": self.wheel_id,
            "slice_id": self.slice_id,
            "source": self.source,
            "timestamp": int(now),
            "time": time.strftime("%H:%M:%S", time.localtime(now)),
            "date": time.strftime("%Y-%m-%d", time.localtime(now)),
            "user": "",
            "user_login": "",
            "user_id": "",
            "reward": "",
            "reward_id": "",
            "user_input": "",
            "amount": "",
        }
        base.update(self.variables)
        return {k: ("" if v is None else str(v)) for k, v in base.items()}

    def render(self, value: str) -> str:
        if not value or "{{" not in value:
            return value
        table = self.as_vars()

        def replace(match: re.Match[str]) -> str:
            name, filter_name = match.group(1), match.group(2)
            return _apply_filter(table.get(name, ""), filter_name)

        return _TOKEN.sub(replace, value)

    def render_any(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.render(value)
        if isinstance(value, list):
            return [self.render_any(v) for v in value]
        if isinstance(value, dict):
            return {k: self.render_any(v) for k, v in value.items()}
        return value


class ActionFailed(RuntimeError):
    pass


def _prepare_config(action_type: str, config: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    spec = ACTION_TYPES.get(action_type)
    if spec is None:
        return ctx.render_any(config)
    literal = {f.key for f in spec.fields if not f.templatable}
    merged = spec.defaults()
    merged.update(config)
    return {k: (v if k in literal else ctx.render_any(v)) for k, v in merged.items()}


async def execute_single(action: dict[str, Any], ctx: ExecContext) -> str:
    """Run one action. Raises ActionFailed with a human-readable reason."""
    action_type = action.get("type", "")
    spec = ACTION_TYPES.get(action_type)
    if spec is None or spec.handler is None:
        raise ActionFailed(f"Unknown action type '{action_type}'")

    config = _prepare_config(action_type, action.get("config") or {}, ctx)
    if ctx.dry_run and action_type not in {"delay"}:
        return f"[dry run] would run {spec.label} with {json.dumps(config, default=str)[:400]}"
    try:
        return await spec.handler(config, ctx)
    except ActionFailed:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure is reported, never fatal
        raise ActionFailed(str(exc) or exc.__class__.__name__) from exc


async def execute_chain(
    actions: list[dict[str, Any]], ctx: ExecContext, *, label: str = ""
) -> list[dict[str, Any]]:
    """Run actions in order. A failure is logged and the chain continues."""
    results: list[dict[str, Any]] = []
    for action in actions:
        if not action.get("enabled", True):
            continue
        spec = ACTION_TYPES.get(action.get("type", ""))
        name = action.get("name") or (spec.label if spec else action.get("type", "action"))
        started = time.time()
        try:
            detail = await execute_single(action, ctx)
            ok = True
        except ActionFailed as exc:
            detail = str(exc)
            ok = False
            log.warning("Action '%s' failed: %s", name, detail)

        entry = {
            "action_id": action.get("id", ""),
            "type": action.get("type", ""),
            "name": name,
            "ok": ok,
            "detail": detail,
            "duration_ms": int((time.time() - started) * 1000),
            "chain": label,
            "wheel_id": ctx.wheel_id,
            "created_at": time.time(),
        }
        results.append(entry)
        db.log_action(
            wheel_id=ctx.wheel_id,
            action_id=entry["action_id"],
            action_type=entry["type"],
            name=name,
            ok=ok,
            detail=detail,
        )
        await hub.broadcast_control({"type": "action_result", **entry})
    return results
