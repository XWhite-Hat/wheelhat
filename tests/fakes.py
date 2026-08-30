"""Stand-in servers for every app WheelHat connects to.

Each one speaks enough of the real protocol to exercise the real connector -
handshakes, authentication, capability lookups and failure paths - so the
integrations are tested without any of these applications installed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import urllib.parse
from typing import Any

import websockets


class FakeOBS:
    """obs-websocket v5, enough of it to identify and answer requests."""

    def __init__(self, password: str = "") -> None:
        self.password = password
        self.salt = base64.b64encode(secrets.token_bytes(16)).decode()
        self.challenge = base64.b64encode(secrets.token_bytes(16)).decode()
        self.received: list[tuple[str, dict[str, Any]]] = []
        self.current_scene = "Starting Soon"
        self.server: websockets.Server | None = None
        self.port = 0

    def expected_auth(self) -> str:
        secret = base64.b64encode(
            hashlib.sha256((self.password + self.salt).encode()).digest()
        ).decode()
        return base64.b64encode(
            hashlib.sha256((secret + self.challenge).encode()).digest()
        ).decode()

    async def start(self) -> int:
        self.server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, ws) -> None:
        hello: dict[str, Any] = {
            "op": 0,
            "d": {"obsWebSocketVersion": "5.4.2", "rpcVersion": 1},
        }
        if self.password:
            hello["d"]["authentication"] = {"challenge": self.challenge, "salt": self.salt}
        await ws.send(json.dumps(hello))

        identify = json.loads(await ws.recv())
        if self.password and identify["d"].get("authentication") != self.expected_auth():
            await ws.send(
                json.dumps({"op": 2, "d": {}})
                if False
                else json.dumps({"op": 3, "d": {"comment": "Authentication failed."}})
            )
            await ws.close()
            return

        await ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))

        async for raw in ws:
            message = json.loads(raw)
            if message.get("op") != 6:
                continue
            payload = message["d"]
            request_type = payload["requestType"]
            data = payload.get("requestData", {})
            self.received.append((request_type, data))
            response_data, ok, comment = self._respond(request_type, data)
            await ws.send(
                json.dumps(
                    {
                        "op": 7,
                        "d": {
                            "requestType": request_type,
                            "requestId": payload["requestId"],
                            "requestStatus": {"result": ok, "code": 100 if ok else 204, "comment": comment},
                            "responseData": response_data,
                        },
                    }
                )
            )

    def _respond(self, request_type: str, data: dict[str, Any]):
        if request_type == "GetSceneList":
            return (
                {
                    "currentProgramSceneName": self.current_scene,
                    # OBS returns scenes in reverse UI order.
                    "scenes": [
                        {"sceneName": "Ending", "sceneIndex": 2},
                        {"sceneName": "Gameplay", "sceneIndex": 1},
                        {"sceneName": "Starting Soon", "sceneIndex": 0},
                    ],
                },
                True,
                None,
            )
        if request_type == "GetInputList":
            return (
                {
                    "inputs": [
                        {"inputName": "Winner Text", "inputKind": "text_gdiplus_v3"},
                        {"inputName": "Airhorn", "inputKind": "ffmpeg_source"},
                        {"inputName": "Webcam", "inputKind": "dshow_input"},
                    ]
                },
                True,
                None,
            )
        if request_type == "GetSceneItemList":
            if data.get("sceneName") == "Gameplay":
                return (
                    {"sceneItems": [{"sourceName": "Webcam", "sceneItemId": 4}]},
                    True,
                    None,
                )
            return ({"sceneItems": []}, True, None)
        if request_type == "GetSceneItemId":
            return ({"sceneItemId": 4}, True, None)
        if request_type == "GetSceneItemEnabled":
            return ({"sceneItemEnabled": False}, True, None)
        if request_type == "GetHotkeyList":
            return ({"hotkeys": ["OBSBasic.StartRecording", "OBSBasic.StopRecording"]}, True, None)
        if request_type == "SetCurrentProgramScene":
            self.current_scene = data.get("sceneName", self.current_scene)
            return ({}, True, None)
        if request_type in {"SetSceneItemEnabled", "SetInputSettings", "TriggerHotkeyByName"}:
            return ({}, True, None)
        if request_type == "Explode":
            return ({}, False, "No such request type.")
        return ({}, True, None)


class FakeVTubeStudio:
    """VTube Studio public API, enough of it to authenticate and fire hotkeys."""

    def __init__(self, *, auto_approve: bool = True, expected_token: str = "TOKEN-123") -> None:
        self.auto_approve = auto_approve
        self.expected_token = expected_token
        self.received: list[tuple[str, dict[str, Any]]] = []
        self.server: websockets.Server | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, ws) -> None:
        async for raw in ws:
            message = json.loads(raw)
            kind = message.get("messageType", "")
            data = message.get("data", {})
            self.received.append((kind, data))
            await ws.send(json.dumps(self._respond(message["requestID"], kind, data)))

    def _envelope(self, request_id: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": request_id,
            "messageType": kind,
            "data": data,
        }

    def _error(self, request_id: str, code: int, message: str) -> dict[str, Any]:
        return self._envelope(request_id, "APIError", {"errorID": code, "message": message})

    def _respond(self, request_id: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        if kind == "APIStateRequest":
            return self._envelope(
                request_id,
                "APIStateResponse",
                {
                    "active": True,
                    "vTubeStudioVersion": "1.28.0",
                    "currentSessionAuthenticated": False,
                },
            )
        if kind == "AuthenticationTokenRequest":
            if not self.auto_approve:
                return self._error(request_id, 50, "User denied the request.")
            return self._envelope(
                request_id,
                "AuthenticationTokenResponse",
                {"authenticationToken": self.expected_token},
            )
        if kind == "AuthenticationRequest":
            ok = data.get("authenticationToken") == self.expected_token
            return self._envelope(
                request_id,
                "AuthenticationResponse",
                {"authenticated": ok, "reason": "" if ok else "Token is invalid."},
            )
        if kind == "HotkeysInCurrentModelRequest":
            return self._envelope(
                request_id,
                "HotkeysInCurrentModelResponse",
                {
                    "availableHotkeys": [
                        {"name": "Cursed outfit", "type": "ToggleExpression", "hotkeyID": "hk-1"},
                        {"name": "Wave", "type": "TriggerAnimation", "hotkeyID": "hk-2"},
                    ]
                },
            )
        if kind == "HotkeyTriggerRequest":
            return self._envelope(request_id, "HotkeyTriggerResponse", {"hotkeyID": data.get("hotkeyID")})
        if kind == "AvailableModelsRequest":
            return self._envelope(
                request_id,
                "AvailableModelsResponse",
                {"availableModels": [{"modelName": "Akari", "modelID": "m-1", "modelLoaded": True}]},
            )
        if kind == "MoveModelRequest":
            return self._envelope(request_id, "MoveModelResponse", {})
        return self._error(request_id, 100, f"Unhandled message type {kind}")


class FakeStreamerBot:
    """Streamer.bot WebSocket Server, enough of it to identify, auth and act.

    ``send_hello=False`` emulates v0.2.4 and older, which greet the client with
    nothing at all and have to be identified with a GetInfo round trip.
    """

    def __init__(
        self,
        *,
        password: str = "",
        send_hello: bool = True,
        name: str = "Streamer.bot",
        version: str = "0.2.5",
        supports_code_triggers: bool = True,
    ) -> None:
        self.password = password
        self.send_hello = send_hello
        self.name = name
        self.version = version
        self.supports_code_triggers = supports_code_triggers
        self.salt = base64.b64encode(secrets.token_bytes(16)).decode()
        self.challenge = base64.b64encode(secrets.token_bytes(16)).decode()
        self.received: list[tuple[str, dict[str, Any]]] = []
        self.server: websockets.Server | None = None
        self.port = 0

    def expected_auth(self) -> str:
        secret = base64.b64encode(
            hashlib.sha256((self.password + self.salt).encode()).digest()
        ).decode()
        return base64.b64encode(
            hashlib.sha256((secret + self.challenge).encode()).digest()
        ).decode()

    async def start(self) -> int:
        self.server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, ws) -> None:
        authenticated = not self.password

        if self.send_hello:
            hello: dict[str, Any] = {
                "request": "Hello",
                "timestamp": "2026-01-01T00:00:00.000-00:00",
                "session": "fake-session",
                "info": {
                    "instanceId": "fake-instance",
                    "name": self.name,
                    "version": self.version,
                    "os": "windows",
                },
            }
            if self.password:
                hello["authentication"] = {"salt": self.salt, "challenge": self.challenge}
            await ws.send(json.dumps(hello))

        async for raw in ws:
            message = json.loads(raw)
            request = message.get("request", "")
            request_id = message.get("id", "")
            self.received.append((request, message))

            if request == "Authenticate":
                if message.get("authentication") == self.expected_auth():
                    authenticated = True
                    await ws.send(json.dumps({"id": request_id, "status": "ok"}))
                else:
                    await ws.send(
                        json.dumps(
                            {"id": request_id, "status": "error", "error": "Authentication failed."}
                        )
                    )
                continue

            payload, error = self._respond(request, message, authenticated)
            if error:
                await ws.send(json.dumps({"id": request_id, "status": "error", "error": error}))
            else:
                await ws.send(json.dumps({"id": request_id, "status": "ok", **payload}))

    def _respond(self, request: str, message: dict[str, Any], authenticated: bool):
        if request == "GetInfo":
            return (
                {
                    "info": {
                        "instanceId": "fake-instance",
                        "name": self.name,
                        "version": self.version,
                        "os": "windows",
                    }
                },
                None,
            )
        if request == "GetActions":
            return (
                {
                    "count": 3,
                    "actions": [
                        {
                            "id": "47da7c2c-1b7e-4ee7-9bbf-306bf18ff1b8",
                            "name": "Cursed outfit",
                            "group": "Wheel",
                            "enabled": True,
                            "subaction_count": 2,
                        },
                        {
                            "id": "9f1c0f6e-2c3d-4a5b-8e7f-1a2b3c4d5e6f",
                            "name": "Airhorn",
                            "group": "Sounds",
                            "enabled": True,
                            "subaction_count": 1,
                        },
                        {
                            "id": "0a0a0a0a-1b1b-2c2c-3d3d-4e4e4e4e4e4e",
                            "name": "Retired thing",
                            "group": "None",
                            "enabled": False,
                            "subaction_count": 0,
                        },
                    ],
                },
                None,
            )
        if request == "GetCodeTriggers":
            if not self.supports_code_triggers:
                return ({}, "Unknown request")
            return (
                {
                    "count": 1,
                    "triggers": [
                        {"name": "wheel_result", "eventName": "Wheel Result", "category": "WheelHat"}
                    ],
                },
                None,
            )
        if request == "GetGlobals":
            return (
                {
                    "count": 1,
                    "variables": {"spinCount": {"name": "spinCount", "value": "7"}},
                },
                None,
            )
        if request in {"DoAction", "ExecuteCodeTrigger"}:
            return ({}, None)
        if request == "SendMessage":
            if not authenticated:
                return ({}, "Authentication required")
            return ({}, None)
        return ({}, f"Unknown request '{request}'")


async def wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll until `predicate()` is truthy or the timeout expires."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


class FakeSpeakerBot:
    """Speaker.bot WebSocket server. It accepts requests and stays silent."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.server: websockets.Server | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, ws) -> None:
        async for raw in ws:
            with contextlib.suppress(ValueError):
                self.received.append(json.loads(raw))
            # Speaker.bot does not answer; that is the point of these tests.


class FakeVNyan:
    """VNyan: bare text frames, and only on the /vnyan path."""

    def __init__(self) -> None:
        self.received: list[str] = []
        self.rejected_paths: list[str] = []
        self.server: websockets.Server | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, ws) -> None:
        path = getattr(getattr(ws, "request", None), "path", "/") or "/"
        if path != "/vnyan":
            self.rejected_paths.append(path)
            await ws.close(code=1008, reason="wrong path")
            return
        async for raw in ws:
            self.received.append(raw if isinstance(raw, str) else raw.decode())


class FakeHttpApp:
    """A tiny HTTP/1.1 server, so the HTTP-based connectors can be tested
    without pulling in a web framework just for the test suite."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            with contextlib.suppress(Exception):
                await self.server.wait_closed()

    def route(self, method: str, path: str, query: dict[str, str], body: Any):
        """Return (status, payload). Payload of None means an empty body."""
        raise NotImplementedError

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            method, target, _ = request_line.decode().split(" ", 2)

            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                name, _, value = line.decode().partition(":")
                headers[name.strip().lower()] = value.strip()

            body: Any = None
            length = int(headers.get("content-length", 0) or 0)
            if length:
                raw = await reader.readexactly(length)
                with contextlib.suppress(ValueError):
                    body = json.loads(raw)

            parsed = urllib.parse.urlparse(target)
            query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            self.requests.append((method, parsed.path, {**query, **headers}, body))

            status, payload = self.route(method, parsed.path, query, body)
            encoded = b"" if payload is None else json.dumps(payload).encode()
            reason = {200: "OK", 204: "No Content", 401: "Unauthorized", 404: "Not Found"}.get(
                status, "OK"
            )
            writer.write(
                f"HTTP/1.1 {status} {reason}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(encoded)}\r\n"
                "Connection: close\r\n\r\n".encode()
                + encoded
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()


class FakeMixItUp(FakeHttpApp):
    """Mix It Up Developer API: lists commands and runs them."""

    COMMANDS = [
        {"ID": "1783e5d9-c2ab-423a-ae64-7dc9a086b194", "Name": "Cursed outfit", "Type": "Chat", "IsEnabled": True},
        {"ID": "2c9d1a7b-0000-4444-8888-aaaabbbbcccc", "Name": "Airhorn", "Type": "Chat", "IsEnabled": True},
        {"ID": "3e0f2b8c-1111-5555-9999-ddddeeeeffff", "Name": "Retired", "Type": "Event", "IsEnabled": False},
    ]

    def __init__(self, *, impostor: bool = False) -> None:
        super().__init__()
        #: Pretend to be some other service that happens to hold port 8911.
        self.impostor = impostor
        self.ran: list[tuple[str, Any]] = []

    def route(self, method: str, path: str, query: dict[str, str], body: Any):
        if self.impostor:
            return 200, {"hello": "some other app"}
        if method == "GET" and path == "/api/v2/commands":
            size = int(query.get("pageSize", 10))
            return 200, {"TotalCount": len(self.COMMANDS), "Commands": self.COMMANDS[:size]}
        if method == "POST" and path.startswith("/api/v2/commands/"):
            self.ran.append((path.rsplit("/", 1)[-1], body))
            return 204, None
        return 404, {"error": "not found"}


class FakeSammi(FakeHttpApp):
    """SAMMI Core API on /api, with the optional Authorization password."""

    def __init__(self, *, password: str = "", version: str = "2024.1") -> None:
        super().__init__()
        self.password = password
        self.version = version
        self.calls: list[dict[str, Any]] = []

    def route(self, method: str, path: str, query: dict[str, str], body: Any):
        if path != "/api":
            return 404, {"error": "not found"}
        if self.password and query.get("authorization") != self.password:
            return 401, {"error": "bad password"}
        if method == "GET":
            if query.get("request") == "getVersion":
                return 200, {"version": self.version}
            return 200, {}
        self.calls.append(body or {})
        return 200, {}
