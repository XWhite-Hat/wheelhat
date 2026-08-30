"""Declarative schema for action types.

Every action type describes its own form fields. The editor UI renders those
fields generically, which is what lets a new integration show up as a proper
graphical form - with live dropdowns of real scenes and hotkeys - instead of
asking the streamer to hand-write a URL and a JSON body.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Awaitable, Callable, Optional

Handler = Callable[[dict[str, Any], Any], Awaitable[str]]


@dataclass
class Field:
    key: str
    label: str
    type: str = "text"
    default: Any = None
    help: str = ""
    placeholder: str = ""
    required: bool = False
    #: Fixed choices, as ``{"value": ..., "label": ...}``.
    options: Optional[list[dict[str, Any]]] = None
    #: Key into the live-options endpoint, e.g. ``obs.scenes``.
    source: str = ""
    #: Other field keys whose values are passed to the options endpoint.
    depends_on: list[str] = dc_field(default_factory=list)
    #: Allow typing a value that is not in the fetched list.
    allow_custom: bool = True
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    rows: int = 4
    #: Conditional visibility: ``{"field": "mode", "equals": ["raw"]}``.
    when: Optional[dict[str, Any]] = None
    #: Whether ``{{placeholders}}`` are substituted at run time.
    templatable: bool = True

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
        }
        optional: dict[str, Any] = {
            "help": self.help,
            "placeholder": self.placeholder,
            "required": self.required,
            "options": self.options,
            "source": self.source,
            "depends_on": self.depends_on,
            "allow_custom": self.allow_custom,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
            "rows": self.rows,
            "when": self.when,
            "templatable": self.templatable,
        }
        for key, value in optional.items():
            if value is None or value == "" or value == []:
                continue
            out[key] = value
        return out


@dataclass
class ActionType:
    type: str
    label: str
    group: str
    description: str = ""
    icon: str = "bolt"
    #: Which integration kind must be connected for this action to work.
    requires: str = ""
    fields: list[Field] = dc_field(default_factory=list)
    handler: Optional[Handler] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "group": self.group,
            "description": self.description,
            "icon": self.icon,
            "requires": self.requires,
            "fields": [f.to_dict() for f in self.fields],
        }

    def defaults(self) -> dict[str, Any]:
        return {f.key: f.default for f in self.fields if f.default is not None}


ACTION_TYPES: dict[str, ActionType] = {}


def action_type(
    type_: str,
    label: str,
    group: str,
    *,
    description: str = "",
    icon: str = "bolt",
    requires: str = "",
    fields: Optional[list[Field]] = None,
) -> Callable[[Handler], Handler]:
    """Register an action type and bind the decorated coroutine as its handler."""

    def decorator(handler: Handler) -> Handler:
        ACTION_TYPES[type_] = ActionType(
            type=type_,
            label=label,
            group=group,
            description=description,
            icon=icon,
            requires=requires,
            fields=fields or [],
            handler=handler,
        )
        return handler

    return decorator


GROUP_ORDER = [
    "Web",
    "OBS Studio",
    "VTube Studio",
    "Streamer.bot",
    "Mix It Up",
    "Speaker.bot",
    "SAMMI",
    "VNyan",
    "Twitch",
    "Overlay",
    "Flow",
    "System",
]


def schema_payload() -> dict[str, Any]:
    types = sorted(
        (t.to_dict() for t in ACTION_TYPES.values()),
        key=lambda t: (
            GROUP_ORDER.index(t["group"]) if t["group"] in GROUP_ORDER else len(GROUP_ORDER),
            t["label"],
        ),
    )
    return {"groups": GROUP_ORDER, "types": types, "variables": TEMPLATE_VARIABLES}


TEMPLATE_VARIABLES = [
    {"name": "winner", "description": "Label of the slice that won"},
    {"name": "wheel", "description": "Name of the wheel"},
    {"name": "wheel_id", "description": "Internal id of the wheel"},
    {"name": "slice_id", "description": "Internal id of the winning slice"},
    {"name": "user", "description": "Display name of whoever triggered the spin"},
    {"name": "user_login", "description": "Twitch login of the trigger user"},
    {"name": "user_id", "description": "Twitch user id of the trigger user"},
    {"name": "reward", "description": "Channel point reward title, if that was the trigger"},
    {"name": "reward_id", "description": "Channel point reward id"},
    {"name": "user_input", "description": "Text the viewer typed into the redemption"},
    {"name": "amount", "description": "Bits cheered / months subbed / raid viewers"},
    {"name": "source", "description": "What triggered the spin (manual, channel_points, ...)"},
    {"name": "timestamp", "description": "Unix timestamp of the spin"},
    {"name": "time", "description": "Local time, HH:MM:SS"},
    {"name": "date", "description": "Local date, YYYY-MM-DD"},
]
