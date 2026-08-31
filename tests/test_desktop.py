"""The desktop shell's server thread and port selection.

Qt itself is exercised by driving the real application; what is unit-tested here
is the part with user-visible behaviour and no display requirement - notably the
port fallback, which decides whether a streamer with a busy 8777 gets a working
app or an error.
"""

import contextlib
import importlib
import socket
import sys

import httpx
import pytest

from wheelhat.desktop.server import ServerThread, find_free_port


def occupy(port: int) -> socket.socket:
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    return holder


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --------------------------------------------------------------- port picking


def test_preferred_port_is_used_when_free():
    port = free_port()
    assert find_free_port(port) == port


def test_a_busy_port_falls_forward():
    """Someone else on 8777 should not stop WheelHat starting."""
    port = free_port()
    holder = occupy(port)
    try:
        chosen = find_free_port(port)
        assert chosen != port
        assert chosen > port
    finally:
        holder.close()


def test_several_busy_ports_are_skipped():
    base = free_port()
    holders = []
    try:
        for offset in range(3):
            with contextlib.suppress(OSError):
                holders.append(occupy(base + offset))
        chosen = find_free_port(base)
        assert chosen not in {h.getsockname()[1] for h in holders}
    finally:
        for holder in holders:
            holder.close()


def test_a_fully_busy_range_still_returns_something():
    """The last resort is any port the OS will give us."""
    port = free_port()
    holder = occupy(port)
    try:
        assert find_free_port(port, attempts=1) > 0
    finally:
        holder.close()


# ------------------------------------------------------------- server thread


def test_base_url_uses_localhost_for_loopback():
    assert ServerThread("127.0.0.1", 8777).base_url == "http://localhost:8777"
    assert ServerThread("0.0.0.0", 8777).base_url == "http://localhost:8777"
    assert ServerThread("192.168.1.5", 8777).base_url == "http://192.168.1.5:8777"


def test_server_thread_serves_and_stops_cleanly():
    port = free_port()
    server = ServerThread("127.0.0.1", port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=30), f"server never came up: {server.error}"
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=10)
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert server.error is None
    finally:
        server.stop()

    # Once stopped the port must actually be released, or a restart would fail.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_server_thread_can_be_restarted_on_the_same_port():
    port = free_port()
    for _ in range(2):
        server = ServerThread("127.0.0.1", port)
        server.start()
        try:
            assert server.wait_until_ready(timeout=30)
        finally:
            server.stop()


def test_stopping_a_server_that_never_started_is_harmless():
    ServerThread("127.0.0.1", free_port()).stop()


class _BlockPySide6:
    """An import hook that makes PySide6 look uninstalled."""

    def find_spec(self, name, path=None, target=None):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return None


@contextlib.contextmanager
def pyside6_uninstalled():
    saved = {k: v for k, v in sys.modules.items() if k.startswith(("PySide6", "wheelhat.desktop"))}
    for key in saved:
        del sys.modules[key]
    sys.meta_path.insert(0, _BlockPySide6())
    try:
        yield
    finally:
        sys.meta_path.pop(0)
        for key in [k for k in sys.modules if k.startswith(("PySide6", "wheelhat.desktop"))]:
            del sys.modules[key]
        sys.modules.update(saved)


def test_server_module_imports_without_pyside6():
    """A headless install has no Qt, and CI installs no desktop extra.

    The package used to import the Qt application eagerly, so merely importing
    the Qt-free server module pulled in PySide6 and broke every test run that
    lacked it. Blocked explicitly here so the test still means something on a
    machine where PySide6 happens to be installed.
    """
    with pyside6_uninstalled():
        module = importlib.import_module("wheelhat.desktop.server")
        assert callable(module.find_free_port)


def test_run_desktop_still_raises_importerror_without_pyside6():
    """__main__ catches ImportError to fall back to the browser interface.

    Making the import lazy must not turn that into an AttributeError, or the
    fallback stops working and the user sees a traceback instead.
    """
    with pyside6_uninstalled():
        package = importlib.import_module("wheelhat.desktop")
        with pytest.raises(ImportError):
            _ = package.run_desktop
