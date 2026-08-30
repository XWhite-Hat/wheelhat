"""Owns the live connector instances and their persisted configuration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .. import db
from ..hub import hub
from ..models import IntegrationConfig
from .base import ConnectorBase, ConnectorError, ConnectorState
from .mixitup import MixItUpConnector
from .obs import OBSConnector
from .sammi import SammiConnector
from .speakerbot import SpeakerBotConnector
from .streamerbot import StreamerBotConnector
from .vnyan import VNyanConnector
from .vtube_studio import VTubeStudioConnector

log = logging.getLogger("wheelhat.registry")

SETTINGS_KEY = "integrations"

CONNECTOR_TYPES: dict[str, type[ConnectorBase]] = {
    "obs": OBSConnector,
    "vtube_studio": VTubeStudioConnector,
    "streamer_bot": StreamerBotConnector,
    "mix_it_up": MixItUpConnector,
    "speaker_bot": SpeakerBotConnector,
    "sammi": SammiConnector,
    "vnyan": VNyanConnector,
}

KIND_LABELS = {
    "obs": "OBS Studio",
    "vtube_studio": "VTube Studio",
    "streamer_bot": "Streamer.bot",
    "mix_it_up": "Mix It Up",
    "speaker_bot": "Speaker.bot",
    "sammi": "SAMMI",
    "vnyan": "VNyan",
}

#: Connections that need a password box on the Connections page.
KINDS_WITH_PASSWORD = {"obs", "streamer_bot", "sammi"}

DEFAULT_CONFIGS = [
    IntegrationConfig(id="obs", kind="obs", name="OBS Studio", port=4455),
    IntegrationConfig(id="vts", kind="vtube_studio", name="VTube Studio", port=8001),
    IntegrationConfig(id="sb", kind="streamer_bot", name="Streamer.bot", port=8080),
    IntegrationConfig(id="miu", kind="mix_it_up", name="Mix It Up", port=8911),
    IntegrationConfig(id="speakerbot", kind="speaker_bot", name="Speaker.bot", port=7680),
    IntegrationConfig(id="sammi", kind="sammi", name="SAMMI", port=9450),
    IntegrationConfig(id="vnyan", kind="vnyan", name="VNyan", port=8000),
]


class IntegrationRegistry:
    def __init__(self) -> None:
        self._configs: dict[str, IntegrationConfig] = {}
        self._connectors: dict[str, ConnectorBase] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ config

    def load(self) -> None:
        raw = db.get_setting(SETTINGS_KEY)
        if not raw:
            self._configs = {cfg.id: cfg.model_copy(deep=True) for cfg in DEFAULT_CONFIGS}
            self._persist()
        else:
            self._configs = {}
            for item in raw:
                try:
                    cfg = IntegrationConfig(**item)
                except Exception:  # noqa: BLE001 - skip anything unreadable
                    log.warning("Ignoring malformed integration config: %r", item)
                    continue
                self._configs[cfg.id] = cfg

            # A connector added in a later version should still show up for
            # someone who already has saved connections.
            missing = [c for c in DEFAULT_CONFIGS if c.id not in self._configs]
            if missing:
                for cfg in missing:
                    self._configs[cfg.id] = cfg.model_copy(deep=True)
                self._persist()

    def _persist(self) -> None:
        db.set_setting(SETTINGS_KEY, [cfg.model_dump() for cfg in self._configs.values()])

    def configs(self) -> list[IntegrationConfig]:
        return list(self._configs.values())

    def config(self, integration_id: str) -> Optional[IntegrationConfig]:
        return self._configs.get(integration_id)

    def get(self, integration_id: str) -> Optional[ConnectorBase]:
        return self._connectors.get(integration_id)

    def first_of_kind(self, kind: str) -> Optional[ConnectorBase]:
        """Resolve an action that just says 'OBS' without naming an instance."""
        for cfg in self._configs.values():
            if cfg.kind == kind and cfg.enabled and cfg.id in self._connectors:
                return self._connectors[cfg.id]
        return None

    def resolve(self, kind: str, integration_id: str = "") -> ConnectorBase:
        connector = self.get(integration_id) if integration_id else None
        if connector is None:
            connector = self.first_of_kind(kind)
        if connector is None:
            raise ConnectorError(
                f"No enabled {KIND_LABELS.get(kind, kind)} connection. "
                "Enable one on the Connections page first."
            )
        return connector

    # --------------------------------------------------------------- lifecycle

    async def apply(self, cfg: IntegrationConfig) -> IntegrationConfig:
        """Persist a config change and reconcile the running connector."""
        async with self._lock:
            self._configs[cfg.id] = cfg
            self._persist()
            await self._stop(cfg.id)
            if cfg.enabled:
                await self._spawn(cfg)
        await self._broadcast()
        return cfg

    async def remove(self, integration_id: str) -> bool:
        async with self._lock:
            if integration_id not in self._configs:
                return False
            await self._stop(integration_id)
            self._configs.pop(integration_id, None)
            self._persist()
        await self._broadcast()
        return True

    async def start_all(self) -> None:
        async with self._lock:
            for cfg in self._configs.values():
                if cfg.enabled and cfg.auto_connect:
                    await self._spawn(cfg)

    async def stop_all(self) -> None:
        async with self._lock:
            for integration_id in list(self._connectors):
                await self._stop(integration_id)

    async def reconnect(self, integration_id: str) -> ConnectorBase:
        cfg = self._configs.get(integration_id)
        if cfg is None:
            raise ConnectorError(f"Unknown connection '{integration_id}'")
        async with self._lock:
            await self._stop(integration_id)
            connector = self._build(cfg)
            self._connectors[integration_id] = connector
        await connector.connect_once()
        # Keep the socket that just succeeded and supervise it, so a later drop is
        # retried without opening a second connection alongside this one.
        await connector.supervise_existing()
        await self._broadcast()
        return connector

    async def _spawn(self, cfg: IntegrationConfig) -> ConnectorBase:
        connector = self._build(cfg)
        self._connectors[cfg.id] = connector
        await connector.start()
        return connector

    def _build(self, cfg: IntegrationConfig) -> ConnectorBase:
        cls = CONNECTOR_TYPES[cfg.kind]
        connector = cls(host=cfg.host, port=cfg.port, password=cfg.password, token=cfg.token)
        connector.on_change(self._on_change)
        if isinstance(connector, VTubeStudioConnector):
            connector.on_token = lambda token, _id=cfg.id: self._save_token(_id, token)
        return connector

    async def _stop(self, integration_id: str) -> None:
        connector = self._connectors.pop(integration_id, None)
        if connector is not None:
            await connector.stop()

    async def _save_token(self, integration_id: str, token: str) -> None:
        cfg = self._configs.get(integration_id)
        if cfg is None:
            return
        cfg.token = token
        self._persist()

    async def _on_change(self, connector: ConnectorBase) -> None:
        await self._broadcast()

    async def _broadcast(self) -> None:
        await hub.broadcast_control({"type": "integrations", "integrations": self.status()})

    # ------------------------------------------------------------------ status

    def status(self) -> list[dict[str, Any]]:
        out = []
        for cfg in self._configs.values():
            connector = self._connectors.get(cfg.id)
            entry: dict[str, Any] = {
                "id": cfg.id,
                "kind": cfg.kind,
                "kind_label": KIND_LABELS.get(cfg.kind, cfg.kind),
                "name": cfg.name or KIND_LABELS.get(cfg.kind, cfg.kind),
                "enabled": cfg.enabled,
                "host": cfg.host,
                "port": cfg.port,
                "auto_connect": cfg.auto_connect,
                "has_password": bool(cfg.password),
                "uses_password": cfg.kind in KINDS_WITH_PASSWORD,
                "has_token": bool(cfg.token),
                "state": ConnectorState.DISCONNECTED.value,
                "version": "",
                "last_error": "",
            }
            if connector is not None:
                entry.update(
                    {
                        "state": connector.state.value,
                        "version": connector.version,
                        "last_error": connector.last_error,
                    }
                )
            out.append(entry)
        return out


registry = IntegrationRegistry()
