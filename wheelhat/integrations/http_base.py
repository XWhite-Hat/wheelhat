"""Base class for apps that expose a plain HTTP API rather than a WebSocket.

Mix It Up and SAMMI both work this way. There is no persistent socket to hold
open, so "connected" means "the API answered a probe recently" - a light poll
keeps the status pill honest without hammering the app.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import abstractmethod
from typing import Any, Optional

import httpx

from ..httpclient import client
from .base import ConnectorBase, ConnectorError, ConnectorState, _describe_error

log = logging.getLogger("wheelhat.integrations")

#: How often to re-check that the app is still answering.
POLL_SECONDS = 20.0


class HttpConnector(ConnectorBase):
    """A managed HTTP connection to one local application."""

    scheme: str = "http"

    @property
    def uri(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def base_url(self) -> str:
        """Where API paths hang off. Override when the API lives under a prefix."""
        return self.uri

    def headers(self) -> dict[str, str]:
        return {}

    @abstractmethod
    async def probe(self) -> None:
        """Verify the app is there and set ``self.version``. Raise on failure."""

    # --------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._supervisor and not self._supervisor.done():
            return
        self._want_running = True
        self._supervisor = asyncio.create_task(self._poll_loop(), name=f"{self.kind}-poll")

    async def stop(self) -> None:
        self._want_running = False
        if self._supervisor:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None
        await self._set_state(ConnectorState.DISCONNECTED)

    async def connect_once(self, timeout: float = 8.0) -> None:
        await self._set_state(ConnectorState.CONNECTING)
        try:
            await asyncio.wait_for(self.probe(), timeout=timeout)
        except Exception as exc:
            self.last_error = _describe_error(exc)
            if self.state is not ConnectorState.NEEDS_AUTH:
                await self._set_state(ConnectorState.ERROR)
            raise ConnectorError(self.last_error) from exc
        self.last_error = ""
        self.invalidate()
        await self._set_state(ConnectorState.CONNECTED)

    async def supervise_existing(self) -> None:
        """Symmetry with the WebSocket connectors; here it just starts polling."""
        await self.start()

    async def _poll_loop(self) -> None:
        backoff = 2.0
        while self._want_running:
            try:
                if self.state is not ConnectorState.CONNECTED:
                    await self._set_state(ConnectorState.CONNECTING)
                await asyncio.wait_for(self.probe(), timeout=8.0)
                self.last_error = ""
                await self._set_state(ConnectorState.CONNECTED)
                backoff = 2.0
                await asyncio.sleep(POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = _describe_error(exc)
                if self.state is not ConnectorState.NEEDS_AUTH:
                    await self._set_state(ConnectorState.ERROR)
                log.debug("%s poll failed: %s", self.kind, self.last_error)
                if self.state is ConnectorState.NEEDS_AUTH:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.8, 30.0)

    # ----------------------------------------------------------------- requests

    async def call(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Any = None,
        timeout: float = 10.0,
        expect_json: bool = True,
    ) -> Any:
        """One API call, with the app's own errors turned into ConnectorError."""
        url = f"{self.base_url}{path}"
        try:
            response = await client().request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self.headers(),
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{self.kind} at {url} did not respond: {exc}") from exc

        if response.status_code in (401, 403):
            raise ConnectorError(
                f"{self.kind} rejected the request ({response.status_code}). Check the password."
            )
        if response.status_code >= 400:
            detail = response.text[:200].replace("\n", " ")
            raise ConnectorError(f"{self.kind}: HTTP {response.status_code} {detail}".strip())

        if not expect_json or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}
