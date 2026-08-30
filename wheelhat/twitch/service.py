"""Twitch integration: sign-in, Helix calls and EventSub subscriptions."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Optional

from .. import config, db
from ..httpclient import client
from ..hub import hub
from . import auth
from .auth import AuthError, DeviceFlow, Tokens
from .eventsub import EventSubClient

log = logging.getLogger("wheelhat.twitch")

CLIENT_ID_KEY = "twitch_client_id"

# EventSub type -> (version, condition template keys, scope that unlocks it).
SUBSCRIPTION_SPECS: dict[str, dict[str, Any]] = {
    "channel.channel_points_custom_reward_redemption.add": {
        "version": "1",
        "condition": ["broadcaster_user_id"],
        "scope": "channel:read:redemptions",
    },
    "channel.chat.message": {
        "version": "1",
        "condition": ["broadcaster_user_id", "user_id"],
        "scope": "user:read:chat",
    },
    "channel.cheer": {
        "version": "1",
        "condition": ["broadcaster_user_id"],
        "scope": "bits:read",
    },
    "channel.subscribe": {
        "version": "1",
        "condition": ["broadcaster_user_id"],
        "scope": "channel:read:subscriptions",
    },
    "channel.subscription.gift": {
        "version": "1",
        "condition": ["broadcaster_user_id"],
        "scope": "channel:read:subscriptions",
    },
    "channel.subscription.message": {
        "version": "1",
        "condition": ["broadcaster_user_id"],
        "scope": "channel:read:subscriptions",
    },
    "channel.follow": {
        "version": "2",
        "condition": ["broadcaster_user_id", "moderator_user_id"],
        "scope": "moderator:read:followers",
    },
    "channel.raid": {
        "version": "1",
        "condition": ["to_broadcaster_user_id"],
        "scope": "",
    },
}

# Which EventSub types each wheel trigger kind needs.
TRIGGER_SUBSCRIPTIONS: dict[str, list[str]] = {
    "channel_points": ["channel.channel_points_custom_reward_redemption.add"],
    "chat_command": ["channel.chat.message"],
    "cheer": ["channel.cheer"],
    "subscription": [
        "channel.subscribe",
        "channel.subscription.gift",
        "channel.subscription.message",
    ],
    "follow": ["channel.follow"],
    "raid": ["channel.raid"],
}


class TwitchService:
    def __init__(self) -> None:
        self.tokens = Tokens()
        self.client_id: str = ""
        self.flow: Optional[DeviceFlow] = None
        self.flow_error: str = ""
        self.subscriptions: list[dict[str, Any]] = []
        self.sub_errors: list[str] = []

        self._flow_task: Optional[asyncio.Task] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._eventsub = EventSubClient(
            on_notification=self._on_notification,
            on_session=self._on_session,
            on_state=self._on_eventsub_state,
        )

    # ----------------------------------------------------------------- startup

    async def start(self) -> None:
        self.client_id = db.get_setting(CLIENT_ID_KEY, "") or ""
        self.tokens = Tokens.load()
        if not (self.client_id and self.tokens.valid):
            return
        try:
            await self.ensure_token()
        except AuthError as exc:
            log.warning("Stored Twitch token unusable: %s", exc)
            await self.broadcast_status()
            return
        await self._eventsub.start()
        self._refresh_task = asyncio.create_task(self._refresh_loop(), name="twitch-refresh")

    async def stop(self) -> None:
        for task in (self._flow_task, self._refresh_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._flow_task = self._refresh_task = None
        await self._eventsub.stop()

    # -------------------------------------------------------------------- auth

    async def set_client_id(self, client_id: str) -> None:
        self.client_id = client_id.strip()
        db.set_setting(CLIENT_ID_KEY, self.client_id)
        await self.broadcast_status()

    async def begin_login(self) -> dict[str, Any]:
        if not self.client_id:
            raise AuthError("Set your Twitch application Client ID first.")
        if self._flow_task and not self._flow_task.done():
            self._flow_task.cancel()
        self.flow_error = ""
        self.flow = await auth.start_device_flow(self.client_id)
        self._flow_task = asyncio.create_task(self._poll_flow(), name="twitch-device-poll")
        await self.broadcast_status()
        return self.flow.to_public()

    async def _poll_flow(self) -> None:
        flow = self.flow
        assert flow is not None
        try:
            while not flow.expired:
                await asyncio.sleep(flow.interval)
                try:
                    tokens = await auth.poll_device_token(self.client_id, flow)
                except AuthError as exc:
                    self.flow_error = str(exc)
                    self.flow = None
                    await self.broadcast_status()
                    return
                if tokens is None:
                    continue

                info = await auth.validate(tokens.access_token)
                tokens.user_id = info.get("user_id", "")
                tokens.login = info.get("login", "")
                tokens.scopes = info.get("scopes", tokens.scopes)
                tokens.display_name = await self._fetch_display_name(tokens)
                tokens.save()
                self.tokens = tokens
                self.flow = None

                await self._eventsub.stop()
                await self._eventsub.start()
                if self._refresh_task is None or self._refresh_task.done():
                    self._refresh_task = asyncio.create_task(
                        self._refresh_loop(), name="twitch-refresh"
                    )
                await self.broadcast_status()
                return

            self.flow_error = "The sign-in code expired."
            self.flow = None
            await self.broadcast_status()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.flow_error = str(exc)
            self.flow = None
            await self.broadcast_status()

    async def logout(self) -> None:
        await self._eventsub.stop()
        if self.tokens.access_token and self.client_id:
            await auth.revoke(self.client_id, self.tokens.access_token)
        Tokens.clear()
        self.tokens = Tokens()
        self.subscriptions = []
        self.sub_errors = []
        await self.broadcast_status()

    async def ensure_token(self) -> str:
        if not self.tokens.valid:
            raise AuthError("Not signed in to Twitch.")
        if self.tokens.expires_in < 300:
            self.tokens = await auth.refresh(self.client_id, self.tokens)
        return self.tokens.access_token

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(max(60.0, self.tokens.expires_in - 600))
                await self.ensure_token()
            except asyncio.CancelledError:
                raise
            except AuthError as exc:
                log.warning("Twitch token refresh failed: %s", exc)
                await self.broadcast_status()
                await asyncio.sleep(300)
            except Exception:  # noqa: BLE001
                log.exception("Unexpected error in the Twitch refresh loop")
                await asyncio.sleep(300)

    async def _fetch_display_name(self, tokens: Tokens) -> str:
        response = await client().get(
            f"{config.TWITCH_HELIX_URL}/users",
            headers={
                "Client-Id": self.client_id,
                "Authorization": f"Bearer {tokens.access_token}",
            },
        )
        if response.status_code != 200:
            return tokens.login
        data = response.json().get("data", [])
        return data[0].get("display_name", tokens.login) if data else tokens.login

    # ------------------------------------------------------------------- helix

    async def helix(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        _retried: bool = False,
    ) -> dict[str, Any]:
        token = await self.ensure_token()
        response = await client().request(
            method,
            f"{config.TWITCH_HELIX_URL}{path}",
            params=params,
            json=json_body,
            headers={"Client-Id": self.client_id, "Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401 and not _retried:
            self.tokens = await auth.refresh(self.client_id, self.tokens)
            return await self.helix(
                method, path, params=params, json_body=json_body, _retried=True
            )
        if response.status_code == 204:
            return {}
        if response.status_code >= 400:
            detail = ""
            with contextlib.suppress(Exception):
                detail = response.json().get("message", "")
            raise AuthError(
                f"Twitch API {method} {path} failed (HTTP {response.status_code})"
                + (f": {detail}" if detail else "")
            )
        with contextlib.suppress(Exception):
            return response.json()
        return {}

    async def list_rewards(self) -> list[dict[str, Any]]:
        if not self.tokens.user_id:
            raise AuthError("Sign in to Twitch first.")
        data = await self.helix(
            "GET",
            "/channel_points/custom_rewards",
            params={"broadcaster_id": self.tokens.user_id},
        )
        return data.get("data", [])

    async def send_chat(self, message: str, reply_parent_message_id: str | None = None) -> None:
        if not self.tokens.user_id:
            raise AuthError("Sign in to Twitch first.")
        body: dict[str, Any] = {
            "broadcaster_id": self.tokens.user_id,
            "sender_id": self.tokens.user_id,
            "message": message[:500],
        }
        if reply_parent_message_id:
            body["reply_parent_message_id"] = reply_parent_message_id
        await self.helix("POST", "/chat/messages", json_body=body)

    async def update_redemption(self, reward_id: str, redemption_id: str, status: str) -> None:
        await self.helix(
            "PATCH",
            "/channel_points/custom_rewards/redemptions",
            params={
                "broadcaster_id": self.tokens.user_id,
                "reward_id": reward_id,
                "id": redemption_id,
            },
            json_body={"status": status},
        )

    # --------------------------------------------------------------- eventsub

    def needed_subscription_types(self) -> list[str]:
        """Only subscribe to what the configured wheels actually listen for."""
        wanted: set[str] = set()
        for wheel in db.list_wheels():
            for trigger in wheel.triggers:
                if not trigger.enabled:
                    continue
                wanted.update(TRIGGER_SUBSCRIPTIONS.get(trigger.type, []))
        return sorted(wanted)

    async def _on_session(self, session_id: str) -> None:
        await self.subscribe_all(session_id)

    async def resubscribe(self) -> None:
        """Called after wheels change so new trigger types start listening."""
        if self._eventsub.session_id:
            await self.subscribe_all(self._eventsub.session_id)

    async def subscribe_all(self, session_id: str) -> None:
        self.subscriptions = []
        self.sub_errors = []
        broadcaster = self.tokens.user_id
        if not broadcaster:
            return

        existing = await self._existing_subscription_types(session_id)
        for sub_type in self.needed_subscription_types():
            if sub_type in existing:
                self.subscriptions.append({"type": sub_type, "status": "enabled"})
                continue
            spec = SUBSCRIPTION_SPECS.get(sub_type)
            if spec is None:
                continue
            scope = spec["scope"]
            if scope and scope not in self.tokens.scopes:
                self.sub_errors.append(
                    f"{sub_type} needs the '{scope}' scope - sign in again to grant it."
                )
                continue
            condition = dict.fromkeys(spec["condition"], broadcaster)
            # user_id / moderator_user_id are "who is watching", also us.
            try:
                await self.helix(
                    "POST",
                    "/eventsub/subscriptions",
                    json_body={
                        "type": sub_type,
                        "version": spec["version"],
                        "condition": condition,
                        "transport": {"method": "websocket", "session_id": session_id},
                    },
                )
                self.subscriptions.append({"type": sub_type, "status": "enabled"})
            except Exception as exc:  # noqa: BLE001
                self.sub_errors.append(f"{sub_type}: {exc}")
                log.warning("EventSub subscribe failed for %s: %s", sub_type, exc)
        await self.broadcast_status()

    async def _existing_subscription_types(self, session_id: str) -> set[str]:
        try:
            data = await self.helix("GET", "/eventsub/subscriptions", params={"status": "enabled"})
        except Exception:  # noqa: BLE001
            return set()
        return {
            item.get("type", "")
            for item in data.get("data", [])
            if item.get("transport", {}).get("session_id") == session_id
        }

    async def _on_eventsub_state(self, state: str, error: str) -> None:
        await self.broadcast_status()

    async def _on_notification(self, event_type: str, event: dict[str, Any]) -> None:
        from ..triggers import handle_twitch_event  # late import: triggers uses the engine

        try:
            await handle_twitch_event(event_type, event)
        except Exception:  # noqa: BLE001
            log.exception("Failed handling Twitch event %s", event_type)

    # ------------------------------------------------------------------ status

    def status(self) -> dict[str, Any]:
        missing = [s for s in config.TWITCH_SCOPES if s not in (self.tokens.scopes or [])]
        return {
            "client_id_set": bool(self.client_id),
            "client_id": self.client_id,
            "signed_in": bool(self.tokens.valid and self.tokens.user_id),
            "login": self.tokens.login,
            "display_name": self.tokens.display_name,
            "user_id": self.tokens.user_id,
            "scopes": self.tokens.scopes,
            "missing_scopes": missing,
            "token_expires_in": int(self.tokens.expires_in),
            "eventsub_state": self._eventsub.state,
            "eventsub_error": self._eventsub.last_error,
            "subscriptions": self.subscriptions,
            "subscription_errors": self.sub_errors,
            "pending_flow": self.flow.to_public() if self.flow else None,
            "flow_error": self.flow_error,
            "updated_at": time.time(),
        }

    async def broadcast_status(self) -> None:
        await hub.broadcast_control({"type": "twitch_status", "twitch": self.status()})


twitch = TwitchService()
