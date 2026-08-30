"""Twitch OAuth via the device code grant.

Device flow is the right fit for a desktop tool: it is designed for public
clients, so the streamer only has to paste a client id - there is no secret to
store on their machine, and no redirect URI to register.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import config, db
from ..httpclient import client

log = logging.getLogger("wheelhat.twitch.auth")

SETTINGS_KEY = "twitch_tokens"


class AuthError(RuntimeError):
    pass


@dataclass
class Tokens:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    scopes: list[str] = field(default_factory=list)
    user_id: str = ""
    login: str = ""
    display_name: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.access_token)

    @property
    def expires_in(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scopes": self.scopes,
            "user_id": self.user_id,
            "login": self.login,
            "display_name": self.display_name,
        }

    @classmethod
    def load(cls) -> "Tokens":
        raw = db.get_setting(SETTINGS_KEY) or {}
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})

    def save(self) -> None:
        db.set_setting(SETTINGS_KEY, self.to_dict())

    @staticmethod
    def clear() -> None:
        db.delete_setting(SETTINGS_KEY)


@dataclass
class DeviceFlow:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_public(self) -> dict[str, Any]:
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "expires_in": int(max(0, self.expires_at - time.time())),
        }


async def start_device_flow(client_id: str, scopes: Optional[list[str]] = None) -> DeviceFlow:
    scope_str = " ".join(scopes or config.TWITCH_SCOPES)
    response = await client().post(
        config.TWITCH_DEVICE_URL,
        data={"client_id": client_id, "scopes": scope_str},
    )
    if response.status_code >= 400:
        raise AuthError(
            f"Twitch rejected the device code request (HTTP {response.status_code}). "
            "Double-check the Client ID, and that the app's OAuth type allows public clients."
        )
    data = response.json()
    return DeviceFlow(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data.get("verification_uri") or data.get("verification_uri_complete", ""),
        interval=int(data.get("interval", 5)),
        expires_at=time.time() + int(data.get("expires_in", 1800)),
    )


async def poll_device_token(client_id: str, flow: DeviceFlow, scopes: Optional[list[str]] = None):
    """One poll. Returns Tokens once granted, None while still pending."""
    response = await client().post(
        config.TWITCH_TOKEN_URL,
        data={
            "client_id": client_id,
            "device_code": flow.device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "scopes": " ".join(scopes or config.TWITCH_SCOPES),
        },
    )
    if response.status_code == 200:
        payload = response.json()
        return Tokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", ""),
            expires_at=time.time() + int(payload.get("expires_in", 14400)),
            scopes=payload.get("scope", []) or [],
        )

    body = _safe_json(response)
    message = str(body.get("message", "")).lower()
    if "authorization_pending" in message:
        return None
    if "slow_down" in message:
        flow.interval += 2
        return None
    if "expired" in message:
        raise AuthError("The device code expired. Start the sign-in again.")
    if "denied" in message or response.status_code == 403:
        raise AuthError("Authorisation was denied on Twitch.")
    if response.status_code == 400:
        # Any other 400 during polling means "not yet"; the flow's own deadline
        # stops this looping forever.
        return None
    raise AuthError(f"Twitch token request failed (HTTP {response.status_code}): {body}")


async def refresh(client_id: str, tokens: Tokens) -> Tokens:
    if not tokens.refresh_token:
        raise AuthError("No refresh token stored; sign in again.")
    response = await client().post(
        config.TWITCH_TOKEN_URL,
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
        },
    )
    if response.status_code >= 400:
        raise AuthError(
            f"Could not refresh the Twitch token (HTTP {response.status_code}). Sign in again."
        )
    payload = response.json()
    tokens.access_token = payload["access_token"]
    tokens.refresh_token = payload.get("refresh_token", tokens.refresh_token)
    tokens.expires_at = time.time() + int(payload.get("expires_in", 14400))
    tokens.scopes = payload.get("scope", tokens.scopes) or tokens.scopes
    tokens.save()
    return tokens


async def validate(access_token: str) -> dict[str, Any]:
    response = await client().get(
        "https://id.twitch.tv/oauth2/validate",
        headers={"Authorization": f"OAuth {access_token}"},
    )
    if response.status_code != 200:
        raise AuthError("The stored Twitch token is no longer valid.")
    return response.json()


async def revoke(client_id: str, access_token: str) -> None:
    try:
        await client().post(
            "https://id.twitch.tv/oauth2/revoke",
            data={"client_id": client_id, "token": access_token},
        )
    except Exception:  # noqa: BLE001 - best effort on sign-out
        log.debug("Token revoke failed", exc_info=True)


def _safe_json(response) -> dict[str, Any]:
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return {"message": response.text[:200]}
