"""Entry point: ``python -m wheelhat`` or the ``wheelhat`` console script.

By default this opens the desktop application. ``--server`` keeps the original
headless behaviour, which is what CI, containers and remote stream boxes want.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser

from . import config


def _run_server(args) -> int:
    """Headless: serve, and optionally point a browser at it."""
    import uvicorn

    display_host = "localhost" if args.host in {"0.0.0.0", "127.0.0.1"} else args.host
    url = f"http://{display_host}:{args.port}/"
    print(f"\n  WheelHat control panel:  {url}\n")

    if not args.no_browser and not args.reload:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    # --reload needs an import string so the worker can re-import on change.
    # Everything else passes the app object directly, which keeps the module
    # statically visible - a frozen build cannot resolve an import string,
    # because nothing in the bundle references wheelhat.app by name.
    if args.reload:
        uvicorn.run(
            "wheelhat.app:app",
            host=args.host,
            port=args.port,
            reload=True,
            log_level=args.log_level,
        )
    else:
        from .app import app

        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def _ensure_streams() -> None:
    """A windowed build has no console, so stdout and stderr are None.

    uvicorn logs during startup, and logging raises when its stream is None, so
    the application would die before showing a window. Fixed here rather than in
    the desktop package because argparse and logging both run before that
    imports.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = sys.stdout


def main() -> int:
    _ensure_streams()
    parser = argparse.ArgumentParser(
        prog="wheelhat", description="Spinner wheels for Twitch streamers."
    )
    parser.add_argument("--host", default=config.HOST, help="Interface to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=config.PORT, help="Port to listen on (default 8777)")
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run headless with no application window (for stream boxes and CI)",
    )
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (development)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser in --server mode")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    # Overlay URLs are built from these. Set the live module attributes (for the
    # in-process case) and the environment (for the --reload child process).
    config.HOST = args.host
    config.PORT = args.port
    os.environ["WHEELHAT_HOST"] = args.host
    os.environ["WHEELHAT_PORT"] = str(args.port)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # --reload rebuilds the process on every edit, which a GUI cannot survive.
    if args.server or args.reload:
        return _run_server(args)

    try:
        from .desktop import run_desktop
    except ImportError:
        print(
            "The desktop application needs PySide6, which is not installed.\n"
            "  Install it with:  pip install 'wheelhat[desktop]'\n"
            "  Or run headless:  wheelhat --server\n"
            "Falling back to the browser interface.\n",
            file=sys.stderr,
        )
        return _run_server(args)

    return run_desktop(host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    sys.exit(main())
