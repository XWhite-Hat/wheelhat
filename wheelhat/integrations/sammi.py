"""SAMMI connector (SAMMI Core API).

SAMMI listens on http://localhost:9450/api and takes a JSON body naming the
request. Note this is the Core API port - 9425 is SAMMI Bridge, which is a
different thing that talks to OBS.

SAMMI has no endpoint for listing decks or buttons, so button IDs are typed
rather than picked. WheelHat still owns the URL, the optional password header
and the payload shape, which is the fiddly part.
"""

from __future__ import annotations

from typing import Any

from .base import ConnectorError
from .http_base import HttpConnector


class SammiConnector(HttpConnector):
    kind = "sammi"
    default_port = 9450

    def headers(self) -> dict[str, str]:
        # SAMMI's password, when set, goes in a plain Authorization header.
        return {"Authorization": self.password} if self.password else {}

    async def probe(self) -> None:
        data = await self.call(
            "GET", "/api", params={"request": "getVersion"}, timeout=6
        )
        if not isinstance(data, dict):
            raise ConnectorError("Unexpected reply from SAMMI.")
        version = data.get("version") or data.get("Version") or ""
        self.version = f"SAMMI {version}".strip()

    async def api(self, request: str, payload: dict[str, Any] | None = None) -> Any:
        return await self.call(
            "POST", "/api", json_body={"request": request, **(payload or {})}, expect_json=False
        )

    async def trigger_button(self, button_id: str) -> None:
        await self.api("triggerButton", {"buttonID": button_id})

    async def release_button(self, button_id: str) -> None:
        await self.api("releaseButton", {"buttonID": button_id})

    async def set_variable(self, name: str, value: Any, button_id: str = "") -> None:
        payload: dict[str, Any] = {"name": name, "value": value}
        if button_id:
            payload["buttonID"] = button_id
        await self.api("setVariable", payload)

    async def alert(self, message: str) -> None:
        await self.api("alertMessage", {"message": message})
