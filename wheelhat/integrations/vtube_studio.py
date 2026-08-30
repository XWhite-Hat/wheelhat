"""VTube Studio connector (public plugin API).

Authentication is a two-step dance: the plugin asks for a token, the streamer
approves a popup inside VTube Studio, and the token is reused forever after. The
token request runs on its own short-lived socket so a pending popup never blocks
the supervised connection.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

import websockets

from .base import Connector, ConnectorError, ConnectorState

API_NAME = "VTubeStudioPublicAPI"
API_VERSION = "1.0"

PLUGIN_NAME = "WheelHat"
PLUGIN_DEVELOPER = "WheelHat"


def _envelope(request_id: str, message_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "apiName": API_NAME,
        "apiVersion": API_VERSION,
        "requestID": request_id,
        "messageType": message_type,
        "data": data or {},
    }


class VTubeStudioConnector(Connector):
    kind = "vtube_studio"
    default_port = 8001

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.on_token: Optional[Callable[[str], Awaitable[None]]] = None

    async def handshake(self) -> None:
        state = await self._exchange("APIStateRequest")
        self.version = str(state.get("vTubeStudioVersion", ""))

        if not self.token:
            await self._set_state(ConnectorState.NEEDS_AUTH)
            raise ConnectorError(
                "WheelHat has not been authorised in VTube Studio yet - "
                "use 'Request access' and approve the popup in VTS."
            )

        result = await self._exchange(
            "AuthenticationRequest",
            {
                "pluginName": PLUGIN_NAME,
                "pluginDeveloper": PLUGIN_DEVELOPER,
                "authenticationToken": self.token,
            },
        )
        if not result.get("authenticated"):
            self.token = ""
            await self._set_state(ConnectorState.NEEDS_AUTH)
            raise ConnectorError(
                f"VTube Studio rejected the saved token: {result.get('reason', 'unknown reason')}. "
                "Request access again."
            )

    async def _exchange(
        self, message_type: str, data: dict[str, Any] | None = None, *, timeout: float = 10.0
    ) -> dict[str, Any]:
        """Handshake-time request/response on the raw socket."""
        await self.raw_send(_envelope(f"wh-hs-{message_type}", message_type, data))
        while True:
            message = await self.raw_recv(timeout=timeout)
            if message.get("messageType") == "APIError":
                payload = message.get("data", {})
                raise ConnectorError(
                    f"VTube Studio error {payload.get('errorID')}: {payload.get('message')}"
                )
            if message.get("requestID", "").startswith("wh-hs-"):
                return message.get("data", {})

    def build_frame(self, request_id: str, request_type: str, data: dict[str, Any]) -> dict[str, Any]:
        return _envelope(request_id, request_type, data)

    def route_message(
        self, message: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any] | None, str | None]:
        request_id = message.get("requestID")
        if not request_id or not str(request_id).startswith("wh-"):
            return None, None, None
        data = message.get("data", {})
        if message.get("messageType") == "APIError":
            return request_id, None, f"VTube Studio: {data.get('message', 'request failed')}"
        return request_id, data, None

    # ------------------------------------------------------------------ auth

    async def request_access(self, timeout: float = 120.0) -> str:
        """Pop the VTS permission dialog and return the granted token.

        Runs on its own connection because the streamer may take a while to click
        allow, and we do not want that blocking the supervisor.
        """
        try:
            ws = await asyncio.wait_for(websockets.connect(self.uri, open_timeout=6), timeout=8)
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the UI
            raise ConnectorError(
                f"Could not reach VTube Studio at {self.uri}. "
                "Enable the plugin API under Settings > General."
            ) from exc

        try:
            await ws.send(
                json.dumps(
                    _envelope(
                        "wh-token",
                        "AuthenticationTokenRequest",
                        {"pluginName": PLUGIN_NAME, "pluginDeveloper": PLUGIN_DEVELOPER},
                    )
                )
            )
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ConnectorError("Timed out waiting for approval in VTube Studio.")
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                message = json.loads(raw)
                if message.get("messageType") == "APIError":
                    payload = message.get("data", {})
                    if payload.get("errorID") == 50:
                        raise ConnectorError("Access was denied in VTube Studio.")
                    raise ConnectorError(f"VTube Studio: {payload.get('message')}")
                if message.get("messageType") == "AuthenticationTokenResponse":
                    token = message.get("data", {}).get("authenticationToken", "")
                    if not token:
                        raise ConnectorError("VTube Studio returned an empty token.")
                    self.token = token
                    if self.on_token:
                        await self.on_token(token)
                    return token
        finally:
            await ws.close()

    # ------------------------------------------------------------- capabilities

    async def hotkeys(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request("HotkeysInCurrentModelRequest")
            return data.get("availableHotkeys", [])

        return await self.cached("hotkeys", load)

    async def models(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request("AvailableModelsRequest")
            return data.get("availableModels", [])

        return await self.cached("models", load)

    async def expressions(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request("ExpressionStateRequest", {"details": False})
            return data.get("expressions", [])

        return await self.cached("expressions", load)

    async def current_model(self) -> dict[str, Any]:
        return await self.request("CurrentModelRequest")

    async def items(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request(
                "ItemListRequest",
                {
                    "includeAvailableSpots": False,
                    "includeItemInstancesInScene": False,
                    "includeAvailableItemFiles": True,
                },
            )
            return data.get("availableItemFiles", [])

        return await self.cached("items", load)
