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
from .client_id import bundled
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
    "stream.online": {
        "version": "1",
        "condition": ["broadcaster_user_id"],
        "scope": "",
    },
}

#: Subscribed to whenever signed in, whether or not any wheel wants them.
#:
#: Without these the connection sits closed until someone adds a trigger, and
#: the Twitch page reads "disconnected" straight after a successful sign-in -
#: which looks like the sign-in failed.
#:
#: Channel points first, because it is what most wheels end up using, so the
#: subscription is already in place by the time a trigger is added. It needs
#: affiliate status though: a channel without channel points has that request
#: refused. stream.online needs no scope and exists on every channel, so there
#: is always at least one live subscription and Twitch never closes the socket
#: as unused.
BASELINE_SUBSCRIPTIONS: list[str] = [
    "channel.channel_points_custom_reward_redemption.add",
    "stream.online",
]

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
        #: Set only while someone is deliberately identifying a reward.
        self._capture_until: float = 0.0
        self._captured: Optional[dict[str, Any]] = None
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
        self.client_id = self._resolve_client_id()
        self.tokens = Tokens.load()

        # Tokens saved by a different application cannot be used or refreshed.
        # This is the upgrade path too: a build that starts shipping its own
        # application must not present someone else's stale token as a session.
        if self.tokens.valid and self.tokens.client_id and self.tokens.client_id != self.client_id:
            log.info("Stored Twitch token belongs to another application; discarding it")
            Tokens.clear()
            self.tokens = Tokens()

        if not (self.client_id and self.tokens.valid):
            return
        try:
            await self.ensure_token()
        except AuthError as exc:
            log.warning("Stored Twitch token unusable: %s", exc)
            await self.broadcast_status()
            return
        await self.sync_eventsub()
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

    def _resolve_client_id(self) -> str:
        """A saved id wins over the one this build ships with.

        Most people never set one and use WheelHat's own application. Anyone
        who would rather run their own - or who is building from source,
        where nothing is bundled - saves theirs and it takes over.
        """
        return (db.get_setting(CLIENT_ID_KEY, "") or "").strip() or bundled()

    async def set_client_id(self, client_id: str) -> None:
        # Saving an empty value clears the override and falls back to the
        # bundled application rather than leaving Twitch unusable.
        previous = self.client_id
        db.set_setting(CLIENT_ID_KEY, client_id.strip())
        resolved = self._resolve_client_id()

        # A token belongs to the application that obtained it. Swapping the
        # application while keeping the token leaves the UI saying "signed in"
        # while every Helix call 401s and every refresh 400s, so sign out first
        # - and do it before self.client_id moves, so the revoke reaches Twitch
        # with the id the token was actually issued to.
        if resolved != previous and self.tokens.valid:
            log.info("Twitch application changed; signing out the previous session")
            await self.logout()

        self.client_id = resolved
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
                await self.sync_eventsub()
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
        """Display name, and the channel type, from the same request.

        broadcaster_type comes back here for free. Knowing whether a channel is
        affiliate or partner is what lets WheelHat point a regular channel at
        chat commands instead of offering channel points it cannot have.
        """
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
        if not data:
            return tokens.login
        tokens.broadcaster_type = str(data[0].get("broadcaster_type", "") or "")
        return data[0].get("display_name", tokens.login)

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

    async def list_rewards(self, manageable_only: bool = False) -> list[dict[str, Any]]:
        """The channel's custom rewards.

        manageable_only limits the list to rewards this application created.
        Those are the only ones whose redemptions Twitch will let it close, so
        it is what the reward picker uses when offering to manage redemptions.
        """
        if not self.tokens.user_id:
            raise AuthError("Sign in to Twitch first.")
        params: dict[str, Any] = {"broadcaster_id": self.tokens.user_id}
        if manageable_only:
            params["only_manageable_rewards"] = "true"
        data = await self.helix("GET", "/channel_points/custom_rewards", params=params)
        return list(data.get("data", []))

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
        """What the configured wheels actually listen for."""
        wanted: set[str] = set()
        for wheel in db.list_wheels():
            for trigger in wheel.triggers:
                if not trigger.enabled:
                    continue
                wanted.update(TRIGGER_SUBSCRIPTIONS.get(trigger.type, []))
        return sorted(wanted)

    @property
    def has_channel_points(self) -> bool:
        """Affiliate and partner channels only. Regular channels have neither
        channel points nor bits, so wheels there are driven from chat."""
        return self.tokens.broadcaster_type in {"affiliate", "partner"}

    def all_subscription_types(self) -> list[str]:
        """Everything to subscribe to: the baseline, plus whatever wheels want."""
        baseline = set(BASELINE_SUBSCRIPTIONS)
        if not self.has_channel_points:
            # Asking for it would just be refused on every reconnect.
            baseline.discard("channel.channel_points_custom_reward_redemption.add")
        return sorted(baseline | set(self.needed_subscription_types()))

    async def _on_session(self, session_id: str) -> None:
        await self.subscribe_all(session_id)

    async def sync_eventsub(self) -> None:
        """Hold an EventSub socket only while something is listening on it.

        Twitch closes a socket that has no subscriptions about ten seconds after
        the welcome, with code 4003 "connection unused". Connecting when no
        wheel has a trigger therefore does not idle - it produces a permanent
        connect, drop, retry loop against Twitch, filling the log with dropped
        connections and re-listing subscriptions once a minute forever.

        Only ever called from outside the socket's own task. stop() cancels that
        task, so calling this from _on_session would cancel the task from within
        itself.
        """
        if self.tokens.valid and self.all_subscription_types():
            await self._eventsub.start()  # no-op when already running
            return
        if self._eventsub.session_id or self._eventsub.running:
            log.info("No wheel is listening for Twitch events; closing the EventSub socket")
            await self._eventsub.stop()
        self.subscriptions = []
        self.sub_errors = []

    async def resubscribe(self) -> None:
        """Called after wheels change so new trigger types start listening."""
        await self.sync_eventsub()
        if self._eventsub.session_id:
            await self.subscribe_all(self._eventsub.session_id)
        else:
            await self.broadcast_status()

    async def subscribe_all(self, session_id: str) -> None:
        self.subscriptions = []
        self.sub_errors = []
        broadcaster = self.tokens.user_id
        if not broadcaster:
            return

        existing = await self._existing_subscription_types(session_id)
        needed = set(self.needed_subscription_types())
        for sub_type in self.all_subscription_types():
            if sub_type in existing:
                self.subscriptions.append(
                    {"type": sub_type, "status": "enabled", "baseline": sub_type not in needed}
                )
                continue
            spec = SUBSCRIPTION_SPECS.get(sub_type)
            if spec is None:
                continue
            scope = spec["scope"]
            if scope and scope not in self.tokens.scopes:
                if sub_type in needed:
                    self.sub_errors.append(
                        f"{sub_type} needs the '{scope}' scope - sign in again to grant it."
                    )
                else:
                    log.info("Skipping baseline %s: missing the '%s' scope", sub_type, scope)
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
                self.subscriptions.append(
                    {"type": sub_type, "status": "enabled", "baseline": sub_type not in needed}
                )
            except Exception as exc:  # noqa: BLE001
                if sub_type in needed:
                    self.sub_errors.append(f"{sub_type}: {exc}")
                    log.warning("EventSub subscribe failed for %s: %s", sub_type, exc)
                else:
                    # A baseline subscription nobody asked for. Channel points
                    # are refused on a channel without affiliate status, and
                    # reporting that as an error to someone who never mentioned
                    # channel points is noise about a feature they are not using.
                    log.info("Baseline subscription %s unavailable: %s", sub_type, exc)
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


    # --------------------------------------------------------- channel points

    async def create_reward(
        self,
        title: str,
        cost: int,
        *,
        prompt: str = "",
        background_color: str = "",
        user_input: bool = False,
        cooldown_seconds: int = 0,
        max_per_stream: int = 0,
        max_per_user_per_stream: int = 0,
    ) -> dict[str, Any]:
        """Create a reward owned by this application.

        Redemptions deliberately go to the queue rather than being fulfilled on
        the spot: WheelHat marks them fulfilled once the wheel has actually
        spun, and refunds them when it could not. Skipping the queue would make
        both impossible, because the redemption is already closed on arrival.
        """
        body: dict[str, Any] = {
            "title": title.strip()[:45],
            "cost": max(1, int(cost)),
            "is_enabled": True,
            "is_user_input_required": bool(user_input),
            "should_redemptions_skip_request_queue": False,
        }
        if prompt.strip():
            body["prompt"] = prompt.strip()[:200]
        if background_color.strip():
            body["background_color"] = background_color.strip()
        if cooldown_seconds > 0:
            body["is_global_cooldown_enabled"] = True
            body["global_cooldown_seconds"] = int(cooldown_seconds)
        if max_per_stream > 0:
            body["is_max_per_stream_enabled"] = True
            body["max_per_stream"] = int(max_per_stream)
        if max_per_user_per_stream > 0:
            body["is_max_per_user_per_stream_enabled"] = True
            body["max_per_user_per_stream"] = int(max_per_user_per_stream)

        data = await self.helix(
            "POST",
            "/channel_points/custom_rewards",
            params={"broadcaster_id": self.tokens.user_id},
            json_body=body,
        )
        created = list(data.get("data", []))
        if not created:
            raise AuthError("Twitch accepted the reward but returned nothing.")
        return created[0]

    async def delete_reward(self, reward_id: str) -> None:
        await self.helix(
            "DELETE",
            "/channel_points/custom_rewards",
            params={"broadcaster_id": self.tokens.user_id, "id": reward_id},
        )

    async def close_redemption(self, reward_id: str, redemption_id: str, fulfilled: bool) -> bool:
        """Mark a redemption fulfilled, or cancel it to refund the points.

        Only works on rewards this application created - Twitch answers 403 for
        anything made in the Twitch dashboard or by another app. That is not an
        error worth interrupting a spin over, so it is reported and swallowed.
        """
        if not (reward_id and redemption_id and self.tokens.user_id):
            return False
        try:
            await self.helix(
                "PATCH",
                "/channel_points/custom_rewards/redemptions",
                params={
                    "broadcaster_id": self.tokens.user_id,
                    "reward_id": reward_id,
                    "id": redemption_id,
                },
                json_body={"status": "FULFILLED" if fulfilled else "CANCELED"},
            )
            return True
        except AuthError as exc:
            log.info("Could not close redemption %s: %s", redemption_id, exc)
            return False


    # ------------------------------------------------------- identify a reward

    #: How long a listen lasts. Long enough to alt-tab and redeem, short enough
    #: that it cannot be left on and forgotten.
    CAPTURE_SECONDS = 90

    def capture_state(self) -> dict[str, Any]:
        remaining = max(0.0, self._capture_until - time.time())
        return {
            "listening": remaining > 0,
            "expires_in_ms": int(remaining * 1000),
            "reward": self._captured,
        }

    async def start_reward_capture(self, seconds: Optional[int] = None) -> dict[str, Any]:
        """Watch for the next redemption, once, to learn which reward it was.

        Deliberately a moment rather than a habit. WheelHat sees every
        redemption on the channel because of the baseline subscription, so it
        could keep a running list - but a tool quietly recording everything
        viewers redeem is not something to switch on without being asked. This
        arms on a button press, remembers one reward, and forgets by itself.

        Nothing about the viewer is kept: only the reward's id, name and cost.
        """
        window = int(seconds or self.CAPTURE_SECONDS)
        self._capture_until = time.time() + max(5, min(window, 300))
        self._captured = None
        await self.broadcast_status()
        return self.capture_state()

    async def stop_reward_capture(self) -> dict[str, Any]:
        self._capture_until = 0.0
        self._captured = None
        await self.broadcast_status()
        return self.capture_state()

    async def offer_redemption(self, data: dict[str, Any]) -> bool:
        """Show a redemption to an armed listen. True if it was taken.

        Called for every channel point redemption; does nothing at all unless
        someone armed a listen and it has not expired.
        """
        if time.time() >= self._capture_until:
            return False
        reward_id = str(data.get("reward_id", ""))
        if not reward_id:
            return False
        self._captured = {
            "id": reward_id,
            "title": str(data.get("reward", "")),
            "cost": int(data.get("reward_cost", 0) or 0),
        }
        # One reward is the whole point; stop listening immediately.
        self._capture_until = 0.0
        log.info("Identified channel point reward %r for a trigger", self._captured["title"])
        await self.broadcast_status()
        return True

    # ------------------------------------------------------------------ status

    def status(self) -> dict[str, Any]:
        missing = [s for s in config.TWITCH_SCOPES if s not in (self.tokens.scopes or [])]
        return {
            "client_id_set": bool(self.client_id),
            # Only ever the user's own id. The bundled one is not shown: it is
            # not theirs to edit, and prefilling it invites accidental changes.
            "client_id": (db.get_setting(CLIENT_ID_KEY, "") or "").strip(),
            "using_bundled_client_id": bool(self.client_id) and self.client_id == bundled(),
            "signed_in": bool(self.tokens.valid and self.tokens.user_id),
            "login": self.tokens.login,
            "display_name": self.tokens.display_name,
            "broadcaster_type": self.tokens.broadcaster_type,
            "has_channel_points": self.has_channel_points,
            "user_id": self.tokens.user_id,
            "scopes": self.tokens.scopes,
            "missing_scopes": missing,
            "token_expires_in": int(self.tokens.expires_in),
            "eventsub_state": self._eventsub.state,
            "eventsub_error": self._eventsub.last_error,
            "subscriptions": self.subscriptions,
            "reward_capture": self.capture_state(),
            "subscription_errors": self.sub_errors,
            "pending_flow": self.flow.to_public() if self.flow else None,
            "flow_error": self.flow_error,
            "updated_at": time.time(),
        }

    async def broadcast_status(self) -> None:
        await hub.broadcast_control({"type": "twitch_status", "twitch": self.status()})


twitch = TwitchService()
