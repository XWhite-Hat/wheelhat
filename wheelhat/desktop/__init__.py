"""The PySide6 desktop shell.

WheelHat is a local server by necessity - OBS browser sources consume a URL, so
the HTTP server has to exist regardless of what the control panel looks like.
This package wraps that server in a real application window, so nobody has to
remember a localhost address or keep a browser tab open during a stream.

``run_desktop`` is resolved lazily, on attribute access, rather than imported
here. Qt is an optional extra: a headless install, CI, and the test suite all
import Qt-free submodules such as ``wheelhat.desktop.server``, and an eager
import would drag PySide6 in and fail for all of them. Callers that genuinely
want the window still get ImportError from ``from wheelhat.desktop import
run_desktop``, which is what __main__ catches to fall back to the browser.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - for type checkers only, never at runtime
    from .app import run_desktop

__all__ = ["run_desktop"]


def __getattr__(name: str):
    if name == "run_desktop":
        from .app import run_desktop

        return run_desktop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
