"""Streamer.bot connector (WebSocket Server API).

Streamer.bot already brokers YouTube, Kick, Trovo, StreamElements and a hundred
other things, so being able to run one of its actions turns every one of those
into something a wheel slice can do.

Protocol notes that shaped this implementation:

* Requests are ``{"request": <name>, "id": <correlation id>, ...payload}`` and
  responses come back as ``{"id": ..., "status": "ok" | "error"}``.
* v0.2.5+ greets the client with a ``Hello`` frame carrying the instance info,
  and an ``authentication`` object when a password is configured. Older builds
  send nothing on connect, so the handshake falls back to ``GetInfo``.
* Authentication is the same two-step SHA256/base64 dance OBS uses.
* ``SendMessage`` is privileged: it only works once authenticated, which means a
  password must be set in Streamer.bot even though everything else works without.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from typing import Any

from .base import Connector, ConnectorError, ConnectorState

#: Action ids are GUIDs; anything else the user typed is treated as a name.
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

CHAT_PLATFORMS = ("twitch", "youtube", "trovo", "kick")


def auth_response(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode("utf-8")).digest()).decode()


class StreamerBotConnector(Connector):
    kind = "streamer_bot"
    default_port = 8080

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.instance_name: str = ""
        self.authenticated: bool = False

    async def handshake(self) -> None:
        self.authenticated = False
        hello = await self._await_hello()

        if hello is not None:
            self._absorb_info(hello.get("info", {}))
            auth = hello.get("authentication")
            if auth:
                if not self.password:
                    await self._set_state(ConnectorState.NEEDS_AUTH)
                    raise ConnectorError(
                        "Streamer.bot's WebSocket server has authentication switched on. "
                        "Copy its password into WheelHat (Servers/Clients > WebSocket Server)."
                    )
                await self._authenticate(auth["salt"], auth["challenge"])
        else:
            # Streamer.bot 0.2.4 and older do not greet the client.
            info = await self._raw_request("GetInfo")
            self._absorb_info(info.get("info", {}))

        if not self.version:
            raise ConnectorError(
                "Connected, but this does not look like Streamer.bot "
                "(no instance info was returned)."
            )

    async def _await_hello(self, timeout: float = 2.0) -> dict[str, Any] | None:
        """Read frames until Hello arrives, or give up on older builds."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                message = await self.raw_recv(timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if message.get("request") == "Hello":
                return message
            # Anything else this early is an unsolicited event; ignore it.

    async def _authenticate(self, salt: str, challenge: str) -> None:
        rejected = (
            "Streamer.bot rejected the password. Check it under "
            "Servers/Clients > WebSocket Server."
        )
        try:
            result = await self._raw_request(
                "Authenticate", {"authentication": auth_response(self.password, salt, challenge)}
            )
        except ConnectorError as exc:
            # A bad password comes back as a normal error response. Mark it as
            # NEEDS_AUTH so the supervisor stops retrying and waits for the user.
            await self._set_state(ConnectorState.NEEDS_AUTH)
            raise ConnectorError(rejected) from exc

        if result.get("status") != "ok":
            await self._set_state(ConnectorState.NEEDS_AUTH)
            raise ConnectorError(rejected)
        self.authenticated = True

    async def _raw_request(self, request: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Request/response on the raw socket, before the dispatch loop starts."""
        request_id = f"wh-hs-{request}"
        await self.raw_send({"request": request, "id": request_id, **(payload or {})})
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 8
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ConnectorError(f"Streamer.bot did not answer '{request}'")
            message = await self.raw_recv(timeout=remaining)
            if message.get("id") == request_id:
                if message.get("status") == "error":
                    raise ConnectorError(
                        f"Streamer.bot: {message.get('error', 'request failed')}"
                    )
                return message

    def _absorb_info(self, info: dict[str, Any]) -> None:
        if not info:
            return
        self.instance_name = str(info.get("name", "")).strip()
        version = str(info.get("version", ""))
        if version:
            self.version = f"Streamer.bot {version}"

    def build_frame(self, request_id: str, request_type: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"request": request_type, "id": request_id, **data}

    def route_message(
        self, message: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any] | None, str | None]:
        request_id = message.get("id")
        # Events carry no id; Hello carries no id of ours.
        if not request_id or not str(request_id).startswith("wh-"):
            return None, None, None
        if message.get("status") == "error":
            return request_id, None, f"Streamer.bot: {message.get('error', 'request failed')}"
        return request_id, message, None

    # ------------------------------------------------------------- capabilities

    async def actions(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request("GetActions")
            return data.get("actions", [])

        return await self.cached("actions", load)

    async def code_triggers(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self.request("GetCodeTriggers")
            return data.get("triggers", [])

        return await self.cached("code_triggers", load)

    async def globals(self) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            data = await self.request("GetGlobals", {"persisted": True})
            return data.get("variables", {}) or {}

        return await self.cached("globals", load)

    async def info(self) -> dict[str, Any]:
        data = await self.request("GetInfo")
        self._absorb_info(data.get("info", {}))
        return data.get("info", {})

    # ------------------------------------------------------------------ actions

    async def do_action(
        self, reference: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run an action by GUID, or by name when that is what the user gave us."""
        action = {"id": reference} if GUID.match(reference) else {"name": reference}
        return await self.request("DoAction", {"action": action, "args": args or {}})

    async def execute_code_trigger(
        self, trigger_name: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self.request(
            "ExecuteCodeTrigger", {"triggerName": trigger_name, "args": args or {}}
        )

    async def send_message(
        self, platform: str, message: str, *, bot: bool = False, internal: bool = True
    ) -> dict[str, Any]:
        if not self.authenticated:
            raise ConnectorError(
                "Sending chat through Streamer.bot needs an authenticated connection. "
                "Set a password on its WebSocket server and enter it in WheelHat."
            )
        return await self.request(
            "SendMessage",
            {"platform": platform, "message": message, "bot": bot, "internal": internal},
        )

    def describe(self) -> dict[str, Any]:
        payload = super().describe()
        payload["authenticated"] = self.authenticated
        payload["instance_name"] = self.instance_name
        return payload
