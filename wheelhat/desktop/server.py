"""Runs the WheelHat HTTP server on a background thread for the desktop shell.

uvicorn owns an asyncio loop and blocks; Qt owns the main thread. Keeping the
server on its own thread lets both run without either one starving the other.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Optional

import uvicorn

log = logging.getLogger("wheelhat.desktop.server")


def find_free_port(preferred: int, host: str = "127.0.0.1", attempts: int = 20) -> int:
    """Return `preferred` if it is free, otherwise the next port that is.

    A streamer who already has something on 8777 should still get a working app
    rather than a stack trace, so this never raises for a busy port.
    """
    for offset in range(attempts):
        candidate = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # Deliberately no SO_REUSEADDR. On Windows that option lets a bind
            # succeed on a port another process is already using, which would
            # make every busy port look free and hand uvicorn a doomed port.
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate
    # Nothing nearby was free; let the OS choose.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


class ServerThread:
    """Owns the uvicorn server and its thread."""

    def __init__(self, host: str, port: int, log_level: str = "info") -> None:
        self.host = host
        self.port = port
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None

    @property
    def base_url(self) -> str:
        display = "localhost" if self.host in {"0.0.0.0", "127.0.0.1"} else self.host
        return f"http://{display}:{self.port}"

    @property
    def error(self) -> Optional[BaseException]:
        return self._error

    def start(self) -> None:
        from ..app import app  # imported here so a frozen build sees the module

        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            # The Qt shell owns the process lifetime; uvicorn must not install
            # its own SIGINT handler or it fights the application for shutdown.
            **({"install_signal_handlers": False} if _supports_no_signals() else {}),
        )
        self._server = uvicorn.Server(config)
        if not _supports_no_signals():
            self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

        def run() -> None:
            try:
                self._server.run()
            except BaseException as exc:  # noqa: BLE001 - surfaced to the UI
                self._error = exc
                log.exception("The WheelHat server stopped unexpectedly")

        self._thread = threading.Thread(target=run, name="wheelhat-server", daemon=True)
        self._thread.start()

    def wait_until_ready(self, timeout: float = 25.0) -> bool:
        """Block until the socket accepts connections, or give up."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._error is not None:
                return False
            if self._server is not None and getattr(self._server, "started", False):
                return True
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return True
            time.sleep(0.15)
        return False

    def stop(self, timeout: float = 8.0) -> None:
        if self._server is not None:
            # uvicorn's run loop watches this flag and unwinds its lifespan,
            # which is what closes the OBS/VTube Studio connections cleanly.
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                log.warning("Server thread did not stop within %ss", timeout)
        self._thread = None
        self._server = None


def _supports_no_signals() -> bool:
    import inspect

    return "install_signal_handlers" in inspect.signature(uvicorn.Config).parameters
