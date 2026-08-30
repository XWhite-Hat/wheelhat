"""Find streaming apps that are already running on this machine.

Two signals are combined. A process scan says "OBS is open"; a port probe says
"and its WebSocket server is actually listening". Reporting them separately is
what lets the UI say something useful like "OBS is running but its WebSocket
server is switched off - here is where the setting lives", instead of just
failing to connect.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import websockets

log = logging.getLogger("wheelhat.discovery")

try:  # psutil is optional; discovery degrades to port probing without it.
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


@dataclass
class KnownApp:
    id: str
    name: str
    port: int
    #: Connector kind, or "" when WheelHat can only talk to it over plain HTTP.
    kind: str = ""
    probe: str = "tcp"
    processes: tuple[str, ...] = ()
    setup_hint: str = ""
    docs: str = ""
    notes: str = ""
    alt_ports: tuple[int, ...] = ()


KNOWN_APPS: list[KnownApp] = [
    KnownApp(
        id="obs",
        name="OBS Studio",
        port=4455,
        kind="obs",
        probe="obs",
        processes=("obs64.exe", "obs32.exe", "obs.exe", "obs"),
        setup_hint="In OBS: Tools > WebSocket Server Settings > Enable WebSocket server.",
        docs="https://github.com/obsproject/obs-websocket",
    ),
    KnownApp(
        id="vtube_studio",
        name="VTube Studio",
        port=8001,
        kind="vtube_studio",
        probe="vts",
        processes=("vtube studio.exe", "vtubestudio.exe", "vtube studio"),
        setup_hint="In VTube Studio: Settings > General > 'Start API (allow plugins)'.",
        docs="https://github.com/DenchiSoft/VTubeStudio",
    ),
    KnownApp(
        id="streamerbot",
        name="Streamer.bot",
        port=8080,
        kind="streamer_bot",
        probe="streamerbot",
        processes=("streamer.bot.exe", "streamerbot.exe"),
        setup_hint="In Streamer.bot: Servers/Clients > WebSocket Server > Enable.",
        docs="https://docs.streamer.bot/api/websocket",
    ),
    KnownApp(
        id="vnyan",
        name="VNyan",
        port=8000,
        kind="vnyan",
        probe="vnyan",
        processes=("vnyan.exe",),
        setup_hint="In VNyan: Settings > Misc > enable the WebSocket server.",
        notes="VNyan cannot list its own triggers, so trigger names are typed by hand.",
    ),
    KnownApp(
        id="mixitup",
        name="Mix It Up",
        port=8911,
        kind="mix_it_up",
        probe="mixitup",
        processes=("mixitup.exe", "mixitup.desktop.exe"),
        setup_hint="In Mix It Up: Services > Developer API > Connect.",
        docs="https://mixitup.bot/docs/reference/developer-api",
    ),
    KnownApp(
        id="speakerbot",
        name="Speaker.bot",
        port=7680,
        kind="speaker_bot",
        probe="ws",
        processes=("speaker.bot.exe", "speakerbot.exe"),
        setup_hint="In Speaker.bot: Servers/Clients > WebSocket Server > Enable.",
        docs="https://speaker.bot/api/websocket",
        notes="Speaker.bot has no identify request, so this is matched on its port alone.",
    ),
    KnownApp(
        id="sammi",
        name="SAMMI",
        port=9450,
        kind="sammi",
        probe="sammi",
        processes=("sammi.exe",),
        setup_hint="SAMMI's API is on by default. Settings > API lets you set a password.",
        docs="https://sammi.solutions/docs/api/reference",
        notes="Port 9450 is the SAMMI Core API. 9425 is SAMMI Bridge, which is a different service.",
    ),
    KnownApp(
        id="warudo",
        name="Warudo",
        port=19190,
        probe="tcp",
        processes=("warudo.exe",),
        setup_hint="Enable the Warudo receiver/HTTP asset for remote control.",
    ),
    KnownApp(
        id="touchportal",
        name="Touch Portal",
        port=12136,
        probe="tcp",
        processes=("touchportal.exe",),
        notes=(
            "Touch Portal's API is for plugins to add actions to it, not for pressing its buttons remotely. Use an "
            "HTTP request action against something else instead."
        ),
    ),
    KnownApp(
        id="lumiastream",
        name="Lumia Stream",
        port=39231,
        probe="http",
        processes=("lumiastream.exe", "lumia stream.exe"),
        setup_hint="Generate an API token in Lumia Stream's settings.",
        docs="https://dev.lumiastream.com/",
        notes="Reachable with an HTTP request action: POST /api/send?token=... .",
    ),
    KnownApp(
        id="voicemod",
        name="Voicemod",
        port=59129,
        probe="tcp",
        processes=("voicemod.exe", "voicemoddesktop.exe"),
        docs="https://control-api.voicemod.net/",
        notes=(
            "Its Control API can list voices, but needs a client key issued by Voicemod, so WheelHat cannot ship a "
            "connector for it."
        ),
    ),
    KnownApp(
        id="streamlabs",
        name="Streamlabs Desktop",
        port=59650,
        probe="tcp",
        processes=("streamlabs obs.exe", "streamlabs desktop.exe"),
        setup_hint="Settings > Remote Control reveals the API token.",
        notes="Speaks JSON-RPC over SockJS and needs that token; not wired up yet.",
    ),
    KnownApp(
        id="homeassistant",
        name="Home Assistant",
        port=8123,
        probe="http",
        setup_hint="Use an HTTP request action with a long-lived access token.",
    ),
]


@dataclass
class Finding:
    app: KnownApp
    host: str = "127.0.0.1"
    port: int = 0
    port_open: bool = False
    identified: bool = False
    version: str = ""
    needs_auth: bool = False
    process_running: bool = False
    process_name: str = ""
    pid: Optional[int] = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.app.id,
            "name": self.app.name,
            "kind": self.app.kind,
            "supported": bool(self.app.kind),
            "host": self.host,
            "port": self.port or self.app.port,
            "port_open": self.port_open,
            "identified": self.identified,
            "version": self.version,
            "needs_auth": self.needs_auth,
            "process_running": self.process_running,
            "process_name": self.process_name,
            "pid": self.pid,
            "detail": self.detail,
            "setup_hint": self.app.setup_hint,
            "docs": self.app.docs,
            "notes": self.app.notes,
            "status": self.status(),
        }

    def status(self) -> str:
        if self.identified:
            return "ready"
        if self.port_open:
            return "listening"
        if self.process_running:
            return "running_no_server"
        return "not_found"


# ------------------------------------------------------------------- primitives


async def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def probe_obs(host: str, port: int) -> dict[str, Any]:
    """Read the Hello frame; it carries the version and whether auth is on."""
    async with websockets.connect(f"ws://{host}:{port}", open_timeout=2.5) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=2.5)
        message = json.loads(raw)
        if message.get("op") != 0:
            return {"identified": False}
        payload = message.get("d", {})
        return {
            "identified": True,
            "version": f"obs-websocket {payload.get('obsWebSocketVersion', '?')}",
            "needs_auth": bool(payload.get("authentication")),
            "detail": "Authentication required"
            if payload.get("authentication")
            else "No password set",
        }


async def probe_vts(host: str, port: int) -> dict[str, Any]:
    async with websockets.connect(f"ws://{host}:{port}", open_timeout=2.5) as ws:
        await ws.send(
            json.dumps(
                {
                    "apiName": "VTubeStudioPublicAPI",
                    "apiVersion": "1.0",
                    "requestID": "wh-discover",
                    "messageType": "APIStateRequest",
                    "data": {},
                }
            )
        )
        raw = await asyncio.wait_for(ws.recv(), timeout=2.5)
        message = json.loads(raw)
        data = message.get("data", {})
        if message.get("messageType") != "APIStateResponse":
            return {"identified": False}
        return {
            "identified": True,
            "version": f"VTube Studio {data.get('vTubeStudioVersion', '?')}",
            "needs_auth": not data.get("currentSessionAuthenticated", False),
            "detail": "Plugin API active",
        }


async def probe_streamerbot(host: str, port: int) -> dict[str, Any]:
    """Identify Streamer.bot positively.

    Port 8080 is one of the busiest on a dev machine, so "a WebSocket answered"
    is not good enough - we only claim a match if the instance identifies itself,
    either through the Hello frame (v0.2.5+) or a GetInfo reply (older builds).
    """
    async with websockets.connect(f"ws://{host}:{port}/", open_timeout=2.5) as ws:
        info: dict[str, Any] = {}
        needs_auth = False

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1.5)
            hello = json.loads(raw)
            if hello.get("request") == "Hello":
                info = hello.get("info", {}) or {}
                needs_auth = bool(hello.get("authentication"))
        except (asyncio.TimeoutError, ValueError):
            pass

        if not info:
            await ws.send(json.dumps({"request": "GetInfo", "id": "wh-discover"}))
            deadline = asyncio.get_running_loop().time() + 2.5
            while asyncio.get_running_loop().time() < deadline:
                try:
                    message = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                except (asyncio.TimeoutError, ValueError):
                    break
                if message.get("id") == "wh-discover":
                    info = message.get("info", {}) or {}
                    break

        name = str(info.get("name", ""))
        if "streamer.bot" not in name.lower().replace(" ", ""):
            return {"identified": False, "detail": "A WebSocket answered, but it is not Streamer.bot"}

        return {
            "identified": True,
            "version": f"Streamer.bot {info.get('version', '?')}",
            "needs_auth": needs_auth,
            "detail": "Password required" if needs_auth else "No password set",
        }


async def probe_vnyan(host: str, port: int) -> dict[str, Any]:
    """VNyan listens on a fixed /vnyan path, which is the only thing that tells
    it apart from anything else squatting on port 8000."""
    async with websockets.connect(f"ws://{host}:{port}/vnyan", open_timeout=2.5):
        return {
            "identified": True,
            "version": "VNyan",
            "detail": "Accepted a connection on /vnyan",
        }


async def probe_mixitup(host: str, port: int) -> dict[str, Any]:
    from .httpclient import client

    response = await client().get(
        f"http://{host}:{port}/api/v2/commands", params={"pageSize": 1}, timeout=2.5
    )
    if response.status_code >= 400:
        return {"identified": False, "detail": f"HTTP {response.status_code}"}
    try:
        payload = response.json()
    except ValueError:
        return {"identified": False, "detail": "Not the Mix It Up API"}
    if not isinstance(payload, dict) or "Commands" not in payload:
        return {"identified": False, "detail": "Not the Mix It Up API"}
    return {
        "identified": True,
        "version": "Mix It Up Developer API v2",
        "detail": f"{payload.get('TotalCount', 0)} commands available",
    }


async def probe_sammi(host: str, port: int) -> dict[str, Any]:
    from .httpclient import client

    response = await client().get(
        f"http://{host}:{port}/api", params={"request": "getVersion"}, timeout=2.5
    )
    if response.status_code in (401, 403):
        return {"identified": True, "version": "SAMMI", "needs_auth": True, "detail": "Password required"}
    if response.status_code >= 400:
        return {"identified": False, "detail": f"HTTP {response.status_code}"}
    try:
        payload = response.json()
    except ValueError:
        return {"identified": False, "detail": "Not the SAMMI API"}
    version = payload.get("version") or payload.get("Version") or ""
    return {
        "identified": True,
        "version": f"SAMMI {version}".strip(),
        "detail": "Core API responding",
    }


async def probe_ws(host: str, port: int) -> dict[str, Any]:
    async with websockets.connect(f"ws://{host}:{port}", open_timeout=2.5):
        return {"identified": True, "detail": "WebSocket server responding"}


async def probe_http(host: str, port: int) -> dict[str, Any]:
    from .httpclient import client

    response = await client().get(f"http://{host}:{port}/", timeout=2.5)
    return {
        "identified": response.status_code < 500,
        "detail": f"HTTP {response.status_code}",
    }


PROBES = {
    "obs": probe_obs,
    "vts": probe_vts,
    "streamerbot": probe_streamerbot,
    "vnyan": probe_vnyan,
    "mixitup": probe_mixitup,
    "sammi": probe_sammi,
    "ws": probe_ws,
    "http": probe_http,
}


# ------------------------------------------------------------------- processes


def running_processes() -> dict[str, tuple[int, str]]:
    """Lowercased process name -> (pid, original name)."""
    if psutil is None:
        return {}
    found: dict[str, tuple[int, str]] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").strip()
        except Exception:  # noqa: BLE001 - processes vanish mid-iteration
            continue
        if name:
            found.setdefault(name.lower(), (proc.info["pid"], name))
    return found


# ----------------------------------------------------------------------- scan


async def _inspect(app: KnownApp, host: str, processes: dict[str, tuple[int, str]]) -> Finding:
    finding = Finding(app=app, host=host, port=app.port)

    for candidate in app.processes:
        hit = processes.get(candidate.lower())
        if hit:
            finding.process_running = True
            finding.pid, finding.process_name = hit
            break

    ports = (app.port, *app.alt_ports)
    for port in ports:
        if await port_open(host, port):
            finding.port_open = True
            finding.port = port
            break

    if not finding.port_open:
        if finding.process_running and app.setup_hint:
            finding.detail = "Running, but nothing is listening on the expected port."
        return finding

    probe = PROBES.get(app.probe)
    if probe is None:
        # A bare TCP connect proves something is listening, not what it is, and
        # certainly not that WheelHat can drive it. Leave it as "listening".
        finding.detail = "Port is open, but WheelHat cannot verify what is behind it"
        return finding

    try:
        result = await asyncio.wait_for(probe(host, finding.port), timeout=4.0)
        finding.identified = bool(result.get("identified"))
        finding.version = result.get("version", "")
        finding.needs_auth = bool(result.get("needs_auth"))
        finding.detail = result.get("detail", "")
    except Exception as exc:  # noqa: BLE001 - a failed probe is still information
        finding.detail = f"Port open but the handshake failed: {exc}"
    return finding


async def scan(host: str = "127.0.0.1", app_ids: Optional[list[str]] = None) -> list[dict[str, Any]]:
    apps = [a for a in KNOWN_APPS if not app_ids or a.id in app_ids]
    processes = await asyncio.to_thread(running_processes)
    findings = await asyncio.gather(*(_inspect(app, host, processes) for app in apps))
    order = {"ready": 0, "listening": 1, "running_no_server": 2, "not_found": 3}
    results = sorted(
        (f.to_dict() for f in findings), key=lambda f: (order[f["status"]], f["name"])
    )
    return results


async def probe_one(kind: str, host: str, port: int) -> dict[str, Any]:
    """Test a specific host/port the user typed in the connection form."""
    if not await port_open(host, port, timeout=1.5):
        return {"ok": False, "detail": f"Nothing is listening on {host}:{port}"}
    probe = PROBES.get(
        {
            "obs": "obs",
            "vtube_studio": "vts",
            "streamer_bot": "streamerbot",
            "vnyan": "vnyan",
            "mix_it_up": "mixitup",
            "sammi": "sammi",
            "speaker_bot": "ws",
        }.get(kind, "ws")
    )
    if probe is None:
        return {"ok": True, "detail": "Port is open"}
    try:
        result = await asyncio.wait_for(probe(host, port), timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"Handshake failed: {exc}"}
    return {"ok": bool(result.get("identified")), **result}
