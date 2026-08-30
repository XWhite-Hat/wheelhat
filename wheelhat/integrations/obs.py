"""OBS Studio connector (obs-websocket v5).

Implements the v5 opcode protocol directly rather than pulling in a client
library, so the only dependency is the WebSocket transport we already need.
The capability helpers exist to populate dropdowns in the action editor.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from .base import Connector, ConnectorError, ConnectorState

# obs-websocket v5 opcodes.
OP_HELLO = 0
OP_IDENTIFY = 1
OP_IDENTIFIED = 2
OP_EVENT = 5
OP_REQUEST = 6
OP_REQUEST_RESPONSE = 7

# Subscribe to the general + scenes + inputs event categories only. Anything
# high-volume (like the input volume meters) stays off.
EVENT_SUBSCRIPTIONS = (1 << 0) | (1 << 2) | (1 << 3) | (1 << 5)


class OBSConnector(Connector):
    kind = "obs"
    default_port = 4455

    def _auth_response(self, challenge: str, salt: str) -> str:
        secret = base64.b64encode(
            hashlib.sha256((self.password + salt).encode("utf-8")).digest()
        ).decode()
        return base64.b64encode(
            hashlib.sha256((secret + challenge).encode("utf-8")).digest()
        ).decode()

    async def handshake(self) -> None:
        hello = await self.raw_recv(timeout=8)
        if hello.get("op") != OP_HELLO:
            raise ConnectorError(f"unexpected opening frame from OBS: op={hello.get('op')}")

        payload = hello.get("d", {})
        self.version = str(payload.get("obsWebSocketVersion", ""))
        identify: dict[str, Any] = {
            "rpcVersion": payload.get("rpcVersion", 1),
            "eventSubscriptions": EVENT_SUBSCRIPTIONS,
        }

        auth = payload.get("authentication")
        if auth:
            if not self.password:
                await self._set_state(ConnectorState.NEEDS_AUTH)
                raise ConnectorError(
                    "OBS requires a WebSocket password "
                    "(Tools > WebSocket Server Settings > Show Connect Info)"
                )
            identify["authentication"] = self._auth_response(auth["challenge"], auth["salt"])

        await self.raw_send({"op": OP_IDENTIFY, "d": identify})
        reply = await self.raw_recv(timeout=8)
        if reply.get("op") != OP_IDENTIFIED:
            detail = reply.get("d", {})
            raise ConnectorError(
                f"OBS rejected the connection: {detail.get('comment') or 'authentication failed'}"
            )

    def build_frame(self, request_id: str, request_type: str, data: dict[str, Any]) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "op": OP_REQUEST,
            "d": {"requestType": request_type, "requestId": request_id},
        }
        if data:
            frame["d"]["requestData"] = data
        return frame

    def route_message(
        self, message: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any] | None, str | None]:
        if message.get("op") != OP_REQUEST_RESPONSE:
            return None, None, None
        payload = message.get("d", {})
        status = payload.get("requestStatus", {})
        request_id = payload.get("requestId")
        if not status.get("result", False):
            comment = status.get("comment") or f"code {status.get('code')}"
            return request_id, None, f"OBS: {comment}"
        return request_id, payload.get("responseData") or {}, None

    async def handle_event(self, message: dict[str, Any]) -> None:
        if message.get("op") != OP_EVENT:
            return
        event_type = message.get("d", {}).get("eventType", "")
        # Anything that changes the set of names we offer in dropdowns.
        if event_type in {
            "SceneListChanged",
            "SceneCreated",
            "SceneRemoved",
            "SceneNameChanged",
            "InputCreated",
            "InputRemoved",
            "InputNameChanged",
            "CurrentProgramSceneChanged",
        }:
            self.invalidate()

    # ------------------------------------------------------------- capabilities

    async def scenes(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request("GetSceneList")
            # OBS returns scenes in reverse UI order.
            return list(reversed(data.get("scenes", [])))

        return await self.cached("scenes", load)

    async def inputs(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request("GetInputList")
            return data.get("inputs", [])

        return await self.cached("inputs", load)

    async def scene_items(self, scene_name: str) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request("GetSceneItemList", {"sceneName": scene_name})
            return data.get("sceneItems", [])

        return await self.cached(f"items:{scene_name}", load)

    async def all_sources(self) -> list[dict[str, Any]]:
        """Every scene item across every scene, labelled with its owning scene."""

        async def load() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for scene in await self.scenes():
                name = scene.get("sceneName", "")
                try:
                    items = await self.scene_items(name)
                except ConnectorError:
                    continue
                for item in items:
                    out.append(
                        {
                            "scene": name,
                            "source": item.get("sourceName", ""),
                            "id": item.get("sceneItemId"),
                        }
                    )
            return out

        return await self.cached("all_sources", load)

    async def filters(self, source_name: str) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request("GetSourceFilterList", {"sourceName": source_name})
            return data.get("filters", [])

        return await self.cached(f"filters:{source_name}", load)

    async def hotkeys(self) -> list[str]:
        async def load() -> list[str]:
            data = await self.request("GetHotkeyList")
            return data.get("hotkeys", [])

        return await self.cached("hotkeys", load)

    async def profile_info(self) -> dict[str, Any]:
        data = await self.request("GetVersion")
        self.version = str(data.get("obsWebSocketVersion", self.version))
        return data

    async def scene_item_id(self, scene_name: str, source_name: str) -> int:
        data = await self.request(
            "GetSceneItemId", {"sceneName": scene_name, "sourceName": source_name}
        )
        return int(data["sceneItemId"])
