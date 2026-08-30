"""Shared scaffolding for connectors to local streaming apps.

Both OBS and VTube Studio speak request/response JSON over a WebSocket with a
correlation id, so the socket plumbing, pending-request bookkeeping and
supervised reconnect live here. Subclasses supply the handshake and the shape of
a request frame.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

import websockets

log = logging.getLogger("wheelhat.integrations")


class ConnectorState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    #: Reachable, but the user still has to approve or supply credentials.
    NEEDS_AUTH = "needs_auth"
    ERROR = "error"


class ConnectorError(RuntimeError):
    pass


class ConnectorBase(ABC):
    """State, capability caching and change notification, shared by transports.

    WebSocket connectors (OBS, VTube Studio, Streamer.bot) and HTTP-polled ones
    (Mix It Up, SAMMI) present the same surface to the registry and the UI, so
    everything that is not transport-specific lives here.
    """

    kind: str = "generic"
    default_port: int = 0

    def __init__(self, *, host: str, port: int, password: str = "", token: str = "") -> None:
        self.host = host
        self.port = port
        self.password = password
        self.token = token

        self.state: ConnectorState = ConnectorState.DISCONNECTED
        self.last_error: str = ""
        self.version: str = ""

        self._connected = asyncio.Event()
        self._want_running = False
        self._supervisor: Optional[asyncio.Task] = None
        self._on_change: Optional[Callable[["ConnectorBase"], Awaitable[None]]] = None
        self._cache: dict[str, Any] = {}

    # ------------------------------------------------------------------ public

    @property
    def uri(self) -> str:
        return f"{self.host}:{self.port}"

    def on_change(self, callback: Callable[["ConnectorBase"], Awaitable[None]]) -> None:
        self._on_change = callback

    async def wait_ready(self, timeout: float = 5.0) -> bool:
        if self.state is ConnectorState.CONNECTED:
            return True
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return self.state is ConnectorState.CONNECTED

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "host": self.host,
            "port": self.port,
            "state": self.state.value,
            "version": self.version,
            "last_error": self.last_error,
            "uri": self.uri,
        }

    # ------------------------------------------------------- lifecycle contract

    @abstractmethod
    async def start(self) -> None:
        """Begin keeping this connection alive, retrying until stop()."""

    @abstractmethod
    async def stop(self) -> None:
        """Tear the connection down and stop retrying."""

    @abstractmethod
    async def connect_once(self, timeout: float = 8.0) -> None:
        """One attempt that raises ConnectorError so the caller can show why."""

    @abstractmethod
    async def supervise_existing(self) -> None:
        """Keep alive whatever connect_once() established."""

    async def _set_state(self, state: ConnectorState) -> None:
        if state is ConnectorState.CONNECTED:
            self._connected.set()
        else:
            self._connected.clear()
        if self.state is state:
            return
        self.state = state
        if self._on_change:
            with contextlib.suppress(Exception):
                await self._on_change(self)

    async def cached(self, key: str, loader: Callable[[], Awaitable[Any]], ttl: float = 15.0) -> Any:
        """Memoise capability lookups so opening a dropdown is not a round trip storm."""
        loop = asyncio.get_running_loop()
        entry = self._cache.get(key)
        if entry and loop.time() - entry[0] < ttl:
            return entry[1]
        value = await loader()
        self._cache[key] = (loop.time(), value)
        return value

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)


class Connector(ConnectorBase):
    """A managed WebSocket connection to one local application."""

    #: Appended to the URL; some apps listen on a path rather than the root.
    path: str = ""

    def __init__(self, *, host: str, port: int, password: str = "", token: str = "") -> None:
        super().__init__(host=host, port=port, password=password, token=token)
        self._ws: Optional[Any] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._reader: Optional[asyncio.Task] = None
        self._counter = 0

    # ------------------------------------------------------------------ public

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}{self.path}"

    async def start(self) -> None:
        """Begin supervising the connection; retries until stop() is called."""
        if self._supervisor and not self._supervisor.done():
            return
        self._want_running = True
        self._supervisor = asyncio.create_task(self._supervise(), name=f"{self.kind}-supervisor")

    async def stop(self) -> None:
        self._want_running = False
        if self._supervisor:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None
        await self._teardown()
        await self._set_state(ConnectorState.DISCONNECTED)

    async def connect_once(self, timeout: float = 8.0) -> None:
        """Single connection attempt that surfaces the failure to the caller."""
        await self._teardown()
        await self._set_state(ConnectorState.CONNECTING)
        try:
            await asyncio.wait_for(self._open(), timeout=timeout)
        except Exception as exc:
            self.last_error = _describe_error(exc)
            # A handshake may already have diagnosed the problem as "waiting on a
            # human"; that is more useful to show than a generic error.
            if self.state is not ConnectorState.NEEDS_AUTH:
                await self._set_state(ConnectorState.ERROR)
            raise ConnectorError(self.last_error) from exc

    async def supervise_existing(self) -> None:
        """Hand an already-open connection to the supervisor.

        Pairs with connect_once(): the caller gets the connection error directly,
        and the socket that succeeded is then kept alive and retried on drop -
        without opening a second one alongside it.
        """
        if self._supervisor and not self._supervisor.done():
            return
        self._want_running = True
        self._supervisor = asyncio.create_task(
            self._supervise(reuse=True), name=f"{self.kind}-supervisor"
        )

    # ------------------------------------------------------------- subclass API

    @abstractmethod
    async def handshake(self) -> None:
        """Authenticate over the freshly opened socket. Raise on failure."""

    @abstractmethod
    def build_frame(self, request_id: str, request_type: str, data: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    def route_message(self, message: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None, str | None]:
        """Map an inbound frame to ``(request_id, payload, error)``.

        ``request_id`` of ``None`` means the frame was an unsolicited event.
        """

    async def handle_event(self, message: dict[str, Any]) -> None:  # pragma: no cover - optional
        return None

    # ------------------------------------------------------------------ request

    async def request(
        self, request_type: str, data: dict[str, Any] | None = None, *, timeout: float = 10.0
    ) -> dict[str, Any]:
        if self.state is not ConnectorState.CONNECTED:
            ready = await self.wait_ready(timeout=3.0)
            if not ready:
                raise ConnectorError(
                    f"{self.kind} is not connected ({self.last_error or self.state.value})"
                )
        ws = self._ws
        if ws is None:
            raise ConnectorError(f"{self.kind} socket is gone")

        self._counter += 1
        request_id = f"wh-{self._counter}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        frame = self.build_frame(request_id, request_type, data or {})
        try:
            await ws.send(json.dumps(frame))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ConnectorError(f"{self.kind} request '{request_type}' timed out") from exc
        finally:
            self._pending.pop(request_id, None)

    # ----------------------------------------------------------------- internals

    async def _supervise(self, reuse: bool = False) -> None:
        backoff = 1.0
        while self._want_running:
            try:
                if reuse and self._ws is not None and self._reader is not None:
                    # First pass only: adopt the connection the caller opened.
                    reuse = False
                else:
                    await self._set_state(ConnectorState.CONNECTING)
                    await asyncio.wait_for(self._open(), timeout=10.0)
                backoff = 1.0
                # _open() leaves the reader running; wait for it to finish.
                if self._reader:
                    await self._reader
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = _describe_error(exc)
                if self.state is not ConnectorState.NEEDS_AUTH:
                    await self._set_state(ConnectorState.ERROR)
                log.debug("%s connection failed: %s", self.kind, self.last_error)
            await self._teardown()
            if not self._want_running:
                break
            if self.state is ConnectorState.NEEDS_AUTH:
                # Blocked on a human decision. Reconnecting on a timer would just
                # spam the app with permission dialogs, so wait to be restarted.
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.8, 30.0)

    async def _open(self) -> None:
        self._ws = await websockets.connect(
            self.uri,
            open_timeout=6,
            ping_interval=20,
            ping_timeout=20,
            max_size=8 * 1024 * 1024,
        )
        # The handshake reads raw frames off the socket, so it runs before the
        # dispatch loop takes ownership of recv().
        await self.handshake()
        self._reader = asyncio.create_task(self._read_loop(), name=f"{self.kind}-reader")
        self.last_error = ""
        self._cache.clear()
        await self._set_state(ConnectorState.CONNECTED)

    async def notify(self, request_type: str, data: dict[str, Any] | None = None) -> None:
        """Send a request without waiting for a reply.

        Some apps (Speaker.bot, VNyan) accept commands but never answer them, so
        awaiting a correlated response would just stall until the timeout.
        """
        if self.state is not ConnectorState.CONNECTED and not await self.wait_ready(timeout=3.0):
            raise ConnectorError(
                f"{self.kind} is not connected ({self.last_error or self.state.value})"
            )
        if self._ws is None:
            raise ConnectorError(f"{self.kind} socket is gone")
        self._counter += 1
        frame = self.build_frame(f"wh-{self._counter}", request_type, data or {})
        await self._ws.send(json.dumps(frame))

    async def send_text(self, text: str) -> None:
        """Send a bare text frame, for apps with no JSON envelope at all."""
        if self.state is not ConnectorState.CONNECTED and not await self.wait_ready(timeout=3.0):
            raise ConnectorError(
                f"{self.kind} is not connected ({self.last_error or self.state.value})"
            )
        if self._ws is None:
            raise ConnectorError(f"{self.kind} socket is gone")
        await self._ws.send(text)

    async def raw_send(self, payload: dict[str, Any]) -> None:
        """Send during the handshake, before the dispatch loop starts."""
        if self._ws is None:
            raise ConnectorError(f"{self.kind} socket is not open")
        await self._ws.send(json.dumps(payload))

    async def raw_recv(self, timeout: float = 10.0) -> dict[str, Any]:
        """Receive during the handshake, before the dispatch loop starts."""
        if self._ws is None:
            raise ConnectorError(f"{self.kind} socket is not open")
        raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        return json.loads(raw)

    async def _read_loop(self) -> None:
        ws = self._ws
        assert ws is not None
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                request_id, payload, error = self.route_message(message)
                if request_id is None:
                    await self.handle_event(message)
                    continue
                future = self._pending.get(request_id)
                if future is None or future.done():
                    continue
                if error:
                    future.set_exception(ConnectorError(error))
                else:
                    future.set_result(payload or {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = _describe_error(exc)

    async def _teardown(self) -> None:
        self._connected.clear()
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
            self._reader = None
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectorError(f"{self.kind} disconnected"))
        self._pending.clear()

def _describe_error(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused - is the app running with its WebSocket server enabled?"
    if isinstance(exc, OSError) and exc.errno:
        return f"{exc.strerror or exc.__class__.__name__} (errno {exc.errno})"
    message = str(exc).strip()
    return message or exc.__class__.__name__
