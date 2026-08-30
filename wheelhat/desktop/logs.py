"""Logging for a windowed application.

Two problems come with having no console:

* ``sys.stdout`` and ``sys.stderr`` are ``None`` in a windowed PyInstaller
  build. Anything that writes to them raises, and uvicorn logs on startup - so
  the application dies before its first window appears.
* Even when it runs, there is nowhere for a message to go. A streamer whose
  wheel misbehaves mid-stream needs a file they can send.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from .. import config

LOG_NAME = "wheelhat.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 3


def log_path():
    return config.DATA_DIR / LOG_NAME


def ensure_streams() -> None:
    """Guarantee stdout and stderr exist.

    Must run before anything logs. In a windowed build these are None, and the
    first log record would otherwise raise inside the logging machinery.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = sys.stdout


def configure(level: str = "info") -> None:
    """Send logs to a rotating file next to the user's wheels."""
    ensure_streams()
    config.ensure_dirs()

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid stacking handlers if this is called twice.
    for handler in list(root.handlers):
        if getattr(handler, "_wheelhat_desktop", False):
            root.removeHandler(handler)

    try:
        handler = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
    except OSError:
        # A read-only or missing data folder must not stop the app opening.
        return

    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-7s %(name)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    handler._wheelhat_desktop = True  # type: ignore[attr-defined]
    root.addHandler(handler)
