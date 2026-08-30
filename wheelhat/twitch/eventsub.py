"""EventSub over WebSocket.

Twitch pushes events down a socket; subscriptions are still created over Helix
using the socket's session id. The client handles the welcome/keepalive/reconnect
choreography and hands decoded notifications to a callback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

import websockets

from .. import config

log = logging.getLogger("wheelhat.twitch.eventsub")

NotificationHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
SessionHandler = Callable[[str], Awaitable[None]]


class EventSubClient:
    def __init__(
        self,
        *,
        on_notification: NotificationHandler,
        on_session: SessionHandler,
        on_state: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ) -> None:
        self.on_notification = on_notification
        self.on_session = on_session
        self.on_state = on_state

        self.session_id: str = ""
        self.state: str = "disconnected"
        self.last_error: str = ""
        self.connected_at: float = 0.0

        self._task: Optional[asyncio.Task] = None
        self._want_running = False
        self._seen: dict[str, float] = {}

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._want_running = True
        self._task = asyncio.create_task(self._run(), name="eventsub")

    async def stop(self) -> None:
        self._want_running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.session_id = ""
        await self._set_state("disconnected")

    async def _set_state(self, state: str, error: str = "") -> None:
        self.state = state
        self.last_error = error
        if self.on_state:
            with contextlib.suppress(Exception):
                await self.on_state(state, error)

    async def _run(self) -> None:
        url = config.TWITCH_EVENTSUB_WS
        backoff = 1.0
        while self._want_running:
            try:
                await self._set_state("connecting")
                next_url = await self._session(url)
                backoff = 1.0
                # A reconnect message hands us a fresh URL to use immediately.
                url = next_url or config.TWITCH_EVENTSUB_WS
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await self._set_state("error", str(exc) or exc.__class__.__name__)
                log.warning("EventSub connection dropped: %s", exc)
                url = config.TWITCH_EVENTSUB_WS
            if not self._want_running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _session(self, url: str) -> str:
        """Run one socket to completion. Returns a reconnect URL, or ''."""
        async with websockets.connect(url, open_timeout=10, max_size=4 * 1024 * 1024) as ws:
            keepalive = 30.0
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=keepalive + 15)
                except asyncio.TimeoutError as exc:
                    raise ConnectionError("no keepalive from Twitch") from exc

                message = json.loads(raw)
                metadata = message.get("metadata", {})
                payload = message.get("payload", {})
                message_type = metadata.get("message_type", "")

                # Twitch may redeliver; ignore anything we have already handled.
                message_id = metadata.get("message_id", "")
                if message_id:
                    self._prune_seen()
                    if message_id in self._seen:
                        continue
                    self._seen[message_id] = time.time()

                if message_type == "session_welcome":
                    session = payload.get("session", {})
                    self.session_id = session.get("id", "")
                    keepalive = float(session.get("keepalive_timeout_seconds", 30) or 30)
                    self.connected_at = time.time()
                    await self._set_state("connected")
                    await self.on_session(self.session_id)

                elif message_type == "session_keepalive":
                    continue

                elif message_type == "notification":
                    subscription = payload.get("subscription", {})
                    await self.on_notification(
                        subscription.get("type", ""), payload.get("event", {})
                    )

                elif message_type == "session_reconnect":
                    reconnect_url = payload.get("session", {}).get("reconnect_url", "")
                    log.info("Twitch asked us to reconnect")
                    return reconnect_url

                elif message_type == "revocation":
                    subscription = payload.get("subscription", {})
                    log.warning(
                        "Subscription %s revoked: %s",
                        subscription.get("type"),
                        subscription.get("status"),
                    )
                    await self._set_state(
                        "connected",
                        f"Twitch revoked the '{subscription.get('type')}' subscription "
                        f"({subscription.get('status')}).",
                    )

    def _prune_seen(self) -> None:
        if len(self._seen) < 500:
            return
        cutoff = time.time() - 600
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
