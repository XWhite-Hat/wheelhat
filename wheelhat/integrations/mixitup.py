"""Mix It Up connector (Developer API).

A plain REST API on http://localhost:8911/api/v2 with no authentication, and -
unusually for the smaller tools - it can list the user's commands. That means
the action editor can offer a real dropdown instead of asking for a GUID.

Enable it in Mix It Up under Services > Developer API > Connect.
"""

from __future__ import annotations

from typing import Any

from .base import ConnectorError
from .http_base import HttpConnector


class MixItUpConnector(HttpConnector):
    kind = "mix_it_up"
    default_port = 8911

    @property
    def base_url(self) -> str:
        return f"{self.uri}/api/v2"

    async def probe(self) -> None:
        # There is no version endpoint, so a tiny commands page doubles as the
        # health check and confirms we are talking to Mix It Up and not something
        # else that happens to hold port 8911.
        data = await self.call("GET", "/commands", params={"pageSize": 1}, timeout=6)
        if not isinstance(data, dict) or "Commands" not in data:
            raise ConnectorError(
                "Something answered on this port, but it is not the Mix It Up Developer API."
            )
        self.version = "Mix It Up Developer API v2"

    async def commands(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.call("GET", "/commands", params={"pageSize": 500})
            return data.get("Commands", []) or []

        return await self.cached("commands", load)

    async def run_command(
        self, command_id: str, *, arguments: str = "", platform: str = "", ignore_requirements: bool = True
    ) -> None:
        body: dict[str, Any] = {"IgnoreRequirements": ignore_requirements}
        if arguments:
            body["Arguments"] = arguments
        if platform:
            body["Platform"] = platform
        await self.call("POST", f"/commands/{command_id}", json_body=body, expect_json=False)
