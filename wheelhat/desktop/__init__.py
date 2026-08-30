"""The PySide6 desktop shell.

WheelHat is a local server by necessity - OBS browser sources consume a URL, so
the HTTP server has to exist regardless of what the control panel looks like.
This package wraps that server in a real application window, so nobody has to
remember a localhost address or keep a browser tab open during a stream.
"""

from .app import run_desktop

__all__ = ["run_desktop"]
