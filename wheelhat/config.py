"""Runtime configuration and filesystem locations."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "WheelHat"

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
WEB_DIR = PACKAGE_DIR / "web"


def _load_dotenv() -> None:
    """Read a .env beside the project, so a source run needs no shell setup.

    Deliberately not python-dotenv: this is a handful of lines and the frozen
    build should not carry a dependency for a development convenience.

    Anything already in the environment wins. That matters more than it looks:
    the frozen bootstrap sets WHEELHAT_DATA_DIR before this module is imported,
    and a stray .env must never be able to move someone's data folder out from
    under them. It also means an explicit variable on the command line still
    beats the file, which is what anyone would expect.
    """
    for candidate in (PROJECT_ROOT / ".env", Path.cwd() / ".env"):
        try:
            if not candidate.is_file():
                continue
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key or key in os.environ:
                    continue
                os.environ[key] = value.strip().strip("\"'")
        except OSError:
            # An unreadable .env is not worth failing to start over.
            continue


_load_dotenv()


def _default_data_dir() -> Path:
    """Store data next to the project in dev, or in the OS app-data dir once installed."""
    if (PROJECT_ROOT / "pyproject.toml").exists():
        return PROJECT_ROOT / "data"
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "wheelhat"


DATA_DIR = Path(os.environ.get("WHEELHAT_DATA_DIR") or _default_data_dir())
DB_PATH = DATA_DIR / "wheelhat.db"
ASSETS_DIR = DATA_DIR / "assets"

HOST = os.environ.get("WHEELHAT_HOST", "127.0.0.1")
PORT = int(os.environ.get("WHEELHAT_PORT", "8777"))

# Twitch's own device-flow endpoints. Only the client id is user supplied; device
# flow is designed for public clients so there is no secret to keep.
TWITCH_DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_HELIX_URL = "https://api.twitch.tv/helix"
TWITCH_EVENTSUB_WS = "wss://eventsub.wss.twitch.tv/ws"

TWITCH_SCOPES = [
    "channel:read:redemptions",
    "channel:manage:redemptions",
    "bits:read",
    "channel:read:subscriptions",
    "moderator:read:followers",
    "user:read:chat",
    "user:write:chat",
    "user:bot",
]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
