"""A single shared httpx client so outbound calls reuse connections."""

from __future__ import annotations

from typing import Optional

import httpx

from . import __version__

_client: Optional[httpx.AsyncClient] = None

USER_AGENT = f"WheelHat/{__version__}"


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=6.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
