"""WebSocket fan-out to overlay browser sources and control-panel clients.

Overlays subscribe per wheel so a spin only wakes the sources that render it.
Control clients get everything, which is what drives the live status pills and
the action log in the UI.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("wheelhat.hub")


class Hub:
    def __init__(self) -> None:
        self._overlays: dict[str, set[WebSocket]] = defaultdict(set)
        self._controls: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- register

    async def join_overlay(self, wheel_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._overlays[wheel_id].add(ws)
        await self.broadcast_control(
            {"type": "overlay_count", "wheel_id": wheel_id, "count": self.overlay_count(wheel_id)}
        )

    async def leave_overlay(self, wheel_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._overlays[wheel_id].discard(ws)
            if not self._overlays[wheel_id]:
                self._overlays.pop(wheel_id, None)
        await self.broadcast_control(
            {"type": "overlay_count", "wheel_id": wheel_id, "count": self.overlay_count(wheel_id)}
        )

    async def join_control(self, ws: WebSocket) -> None:
        async with self._lock:
            self._controls.add(ws)

    async def leave_control(self, ws: WebSocket) -> None:
        async with self._lock:
            self._controls.discard(ws)

    def overlay_count(self, wheel_id: str) -> int:
        return len(self._overlays.get(wheel_id, ()))

    def overlay_counts(self) -> dict[str, int]:
        return {wheel_id: len(conns) for wheel_id, conns in self._overlays.items()}

    # ---------------------------------------------------------------- broadcast

    async def broadcast_overlay(self, wheel_id: str, message: dict[str, Any]) -> None:
        await self._send_many(list(self._overlays.get(wheel_id, ())), message)

    async def broadcast_control(self, message: dict[str, Any]) -> None:
        await self._send_many(list(self._controls), message)

    async def broadcast_all(self, wheel_id: str, message: dict[str, Any]) -> None:
        await asyncio.gather(
            self.broadcast_overlay(wheel_id, message),
            self.broadcast_control(message),
        )

    async def _send_many(self, targets: list[WebSocket], message: dict[str, Any]) -> None:
        if not targets:
            return
        results = await asyncio.gather(
            *(self._send_one(ws, message) for ws in targets), return_exceptions=True
        )
        dead = [ws for ws, res in zip(targets, results, strict=False) if isinstance(res, Exception)]
        if dead:
            async with self._lock:
                for ws in dead:
                    self._controls.discard(ws)
                    for conns in self._overlays.values():
                        conns.discard(ws)

    @staticmethod
    async def _send_one(ws: WebSocket, message: dict[str, Any]) -> None:
        # Failures propagate so _send_many can evict the closed socket.
        await ws.send_json(message)


hub = Hub()
