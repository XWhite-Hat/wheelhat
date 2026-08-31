"""Document models for wheels, slices, actions and triggers.

A wheel is stored as a single JSON document so slice/action shapes can evolve
without migrations. These models are the contract between the API, the editor UI
and the action executor.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

DEFAULT_PALETTE = [
    "#e5484d", "#f76b15", "#ffb224", "#46a758",
    "#12a594", "#0091ff", "#8e4ec6", "#e93d82",
]


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


def now() -> float:
    return time.time()


class ImageLayer(BaseModel):
    """One image drawn as part of the wheel.

    Offsets and sizes are fractions of the wheel radius rather than pixels, so a
    layout keeps working when the browser source is resized.
    """

    url: str = ""
    enabled: bool = True
    #: Multiplier on the layer's natural size for its slot.
    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation: float = 0.0
    opacity: float = 1.0

    def is_drawable(self) -> bool:
        return bool(self.url) and self.enabled and self.opacity > 0


class SliceImage(ImageLayer):
    """An image inside one wedge."""

    #: Distance from the hub, 0 at the centre and 1 at the rim.
    radial: float = 0.60
    #: Longest edge, as a fraction of the wheel radius.
    size: float = 0.26
    #: Turn with the wheel, or stay upright while it spins.
    rotate_with_wheel: bool = True
    #: Clip to the wedge so a large image cannot bleed into its neighbours.
    clip_to_slice: bool = True
    #: Hide the wedge's text label when an image is set.
    replace_label: bool = False


class Action(BaseModel):
    """One step in a slice's action chain."""

    id: str = Field(default_factory=lambda: new_id("act_"))
    type: str = "http_request"
    name: str = ""
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class Slice(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sl_"))
    label: str = "New slice"
    weight: float = 1.0
    color: Optional[str] = None
    text_color: Optional[str] = None
    #: Inline colour for this wedge. Falls back to the wheel's own setting.
    border_color: Optional[str] = None
    #: Label outline colour. Falls back to the wheel's own setting. The
    #: outline is only drawn when the wheel gives it a width.
    text_stroke_color: Optional[str] = None
    image: SliceImage = Field(default_factory=SliceImage)
    enabled: bool = True
    # Temporarily disable this slice after it wins (0 = never).
    cooldown_spins: int = 0
    # Permanently disable this slice once it has won.
    remove_on_win: bool = False
    actions: list[Action] = Field(default_factory=list)
    # Runtime bookkeeping, persisted so a restart does not reset an elimination wheel.
    won_count: int = 0
    cooldown_remaining: int = 0

    def is_spinnable(self) -> bool:
        return self.enabled and self.weight > 0 and self.cooldown_remaining <= 0


class Trigger(BaseModel):
    id: str = Field(default_factory=lambda: new_id("trg_"))
    type: Literal[
        "manual",
        "channel_points",
        "chat_command",
        "cheer",
        "subscription",
        "follow",
        "raid",
    ] = "manual"
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class Appearance(BaseModel):
    palette: list[str] = Field(default_factory=lambda: list(DEFAULT_PALETTE))
    text_color: str = "#ffffff"
    rim_color: str = "#111318"
    rim_width: int = 10
    pointer_color: str = "#ffffff"
    hub_color: str = "#16181d"
    hub_label: str = ""
    font_family: str = "Inter, Segoe UI, system-ui, sans-serif"
    font_size: int = 20
    font_weight: int = 700
    label_max_chars: int = 22
    size: int = 720
    background: str = "transparent"
    #: The browser source this wheel is designed for. Nothing is forced to these
    #: numbers - the overlay always fits whatever size the source really is -
    #: but they drive the editor preview's shape and the sizes we recommend.
    source_width: int = 1280
    source_height: int = 720

    # -- wedge shape ---------------------------------------------------------
    #: Gap between wedges, in degrees. Turns the wheel into separated segments.
    wedge_gap: float = 0.0
    #: Donut hole, as a fraction of the radius.
    inner_radius: float = 0.0
    slice_border_color: str = "#00000030"
    slice_border_width: float = 1.0
    #: Darkening towards the hub, 0 for flat colour.
    wedge_shading: float = 0.0

    # -- labels --------------------------------------------------------------
    #: Where a label ends, as a fraction of the radius.
    text_radial: float = 0.94
    text_stroke_color: str = ""
    text_stroke_width: float = 0.0
    text_shadow: bool = True
    text_uppercase: bool = False
    #: Bend labels along their wedge instead of running them straight out.
    text_curved: bool = False

    # -- hub and pointer -----------------------------------------------------
    show_hub: bool = True
    hub_radius: float = 0.14
    show_pointer: bool = True
    pointer_size: float = 1.0

    # -- image layers --------------------------------------------------------
    #: Behind the wheel, covering the whole browser source.
    background_image: ImageLayer = Field(default_factory=ImageLayer)
    #: Centred on the hub, clipped to a circle.
    hub_image: ImageLayer = Field(default_factory=ImageLayer)
    #: Drawn over the wheel and does not spin - frames, glass, bezels, glow.
    frame_image: ImageLayer = Field(default_factory=ImageLayer)
    #: Replaces the drawn pointer triangle.
    pointer_image: ImageLayer = Field(default_factory=ImageLayer)
    show_title: bool = True
    show_result: bool = True
    result_duration_ms: int = 5000
    #: Where the winner banner sits. "under" keeps it below the wheel and
    #: reserves the room whether or not it is showing, so the wheel never
    #: changes size when a result appears. "over" floats it across the wheel,
    #: which needs no reserved space at all.
    result_position: Literal["under", "over"] = "under"
    # Hide the wheel entirely between spins - handy for a browser source that
    # should only appear when something is actually happening.
    hide_when_idle: bool = False
    idle_spin_speed: float = 0.0


class SpinSettings(BaseModel):
    duration_ms: int = 6500
    min_turns: int = 5
    max_turns: int = 8
    easing: Literal["easeOutQuint", "easeOutCubic", "easeOutExpo"] = "easeOutQuint"
    # Delay between the wheel stopping and actions firing.
    action_delay_ms: int = 400
    cooldown_seconds: int = 0
    sound_start: Optional[str] = None
    sound_win: Optional[str] = None
    volume: float = 0.7


class Wheel(BaseModel):
    id: str = Field(default_factory=lambda: new_id("whl_"))
    name: str = "Untitled wheel"
    description: str = ""
    enabled: bool = True
    slices: list[Slice] = Field(default_factory=list)
    triggers: list[Trigger] = Field(default_factory=list)
    appearance: Appearance = Field(default_factory=Appearance)
    spin: SpinSettings = Field(default_factory=SpinSettings)
    # Fired before/after every spin regardless of which slice wins.
    pre_actions: list[Action] = Field(default_factory=list)
    post_actions: list[Action] = Field(default_factory=list)
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)

    def slice_by_id(self, slice_id: str) -> Optional[Slice]:
        return next((s for s in self.slices if s.id == slice_id), None)

    def spinnable(self) -> list[Slice]:
        return [s for s in self.slices if s.is_spinnable()]

    def visible(self) -> list[Slice]:
        """Slices drawn on the wheel. Cooldowns grey out rather than remove."""
        return [s for s in self.slices if s.enabled]


class SpinRecord(BaseModel):
    id: int | None = None
    wheel_id: str = ""
    wheel_name: str = ""
    slice_id: str = ""
    label: str = ""
    source: str = "manual"
    actor: str = ""
    created_at: float = Field(default_factory=now)


class IntegrationConfig(BaseModel):
    """Connection details for one local application."""

    id: str
    kind: Literal[
        "obs", "vtube_studio", "streamer_bot", "mix_it_up", "speaker_bot", "sammi", "vnyan"
    ]
    name: str = ""
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""
    # Opaque per-integration state (e.g. the VTube Studio plugin token).
    token: str = ""
    auto_connect: bool = True
