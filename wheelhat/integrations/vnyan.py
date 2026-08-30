"""VNyan connector.

VNyan listens on ws://127.0.0.1:8000/vnyan and accepts bare text. Whatever
string you send is matched against the "Websocket Command" triggers in the
user's node graphs, which is how a wheel slice can change a VNyan avatar the way
a hotkey changes a VTube Studio one.

There is no request/response protocol and no way to ask VNyan what triggers
exist, so trigger names are typed. The fixed /vnyan path is the only thing that
distinguishes it from anything else on port 8000, which is a busy port - so
detection is treated as a weak signal and labelled as such.
"""

from __future__ import annotations

from typing import Any

from .base import Connector


class VNyanConnector(Connector):
    kind = "vnyan"
    default_port = 8000
    path = "/vnyan"

    async def handshake(self) -> None:
        self.version = "VNyan"

    def build_frame(self, request_id: str, request_type: str, data: dict[str, Any]) -> dict[str, Any]:
        # Unused: VNyan speaks plain text, so actions call send_text directly.
        return {"request": request_type, **data}

    def route_message(
        self, message: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any] | None, str | None]:
        # VNyan pushes parameter updates; none of them answer anything we sent.
        return None, None, None

    async def trigger(self, name: str) -> None:
        await self.send_text(name)
