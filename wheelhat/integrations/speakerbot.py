"""Speaker.bot connector (WebSocket API).

Speaker.bot is the TTS companion to Streamer.bot, which makes it a natural fit
for a punishment wheel: "the wheel decides what your bot says, and in whose
voice".

Two honest limitations, both reflected in the UI:

* The API has no request for listing voice aliases, so the voice is typed rather
  than picked from a dropdown.
* It has no info/version request either, so a successful connection on port 7680
  is the only available evidence that this is really Speaker.bot.
"""

from __future__ import annotations

from typing import Any

from .base import Connector

QUEUE_REQUESTS = ("Pause", "Resume", "Clear")


class SpeakerBotConnector(Connector):
    kind = "speaker_bot"
    default_port = 7680
    path = "/"

    async def handshake(self) -> None:
        # Nothing to negotiate and nothing to interrogate; being accepted is all
        # the confirmation the protocol offers.
        self.version = "Speaker.bot"

    def build_frame(self, request_id: str, request_type: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"id": request_id, "request": request_type, **data}

    def route_message(
        self, message: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any] | None, str | None]:
        request_id = message.get("id")
        if not request_id or not str(request_id).startswith("wh-"):
            return None, None, None
        if str(message.get("status", "")).lower() == "error":
            return request_id, None, f"Speaker.bot: {message.get('error', 'request failed')}"
        return request_id, message, None

    # Every call below is fire-and-forget: Speaker.bot acts on requests but does
    # not reliably answer them.

    async def speak(self, message: str, voice: str = "", bad_word_filter: bool = True) -> None:
        payload: dict[str, Any] = {"message": message, "badWordFilter": bad_word_filter}
        if voice:
            payload["voice"] = voice
        await self.notify("Speak", payload)

    async def queue(self, action: str) -> None:
        await self.notify(action)

    async def set_enabled(self, enabled: bool) -> None:
        await self.notify("Enable" if enabled else "Disable")

    async def set_events(self, on: bool) -> None:
        await self.notify("Events", {"state": "on" if on else "off"})

    async def set_mode(self, mode: str) -> None:
        await self.notify("Mode", {"mode": mode})
