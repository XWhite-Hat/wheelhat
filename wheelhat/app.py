"""FastAPI application: REST API, WebSocket hub, control panel and overlays."""

from __future__ import annotations

import contextlib
import logging
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, db, httpclient
from .api import api_router
from .api.ws import ws_router
from .engine import engine
from .integrations.registry import registry
from .seed import ensure_starter_wheel
from .twitch.service import twitch

log = logging.getLogger("wheelhat")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config.ensure_dirs()
    db.connect()
    ensure_starter_wheel()
    registry.load()
    await registry.start_all()
    await twitch.start()
    log.info("WheelHat %s ready on http://%s:%s", __version__, config.HOST, config.PORT)
    try:
        yield
    finally:
        await engine.shutdown()
        await twitch.stop()
        await registry.stop_all()
        await httpclient.aclose()
        db.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="WheelHat",
        version=__version__,
        description="Spinner wheels for Twitch streamers.",
        lifespan=lifespan,
    )

    # Browser sources load from this same origin, but a streamer may well run the
    # overlay from a second machine on their LAN, so keep this permissive.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        """Make browsers revalidate assets instead of serving a stale copy.

        OBS browser sources cache hard, so without this an overlay can keep
        running last version's JavaScript long after WheelHat is updated.
        Revalidation is cheap - StaticFiles answers with a 304.
        """
        response = await call_next(request)
        path = request.url.path
        if path.startswith(("/static", "/overlay", "/assets")) or path == "/":
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        if path.startswith("/assets"):
            # Uploaded SVGs are served from this origin. Canvas never executes
            # their scripts, but opening one directly would, so lock the whole
            # assets tree down to inert content.
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.include_router(api_router)
    app.include_router(ws_router)

    config.ensure_dirs()
    app.mount("/static", StaticFiles(directory=config.WEB_DIR / "static"), name="static")
    app.mount("/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")

    @app.get("/licences", include_in_schema=False)
    async def licences() -> PlainTextResponse:
        """WheelHat's licence and the notices for everything it bundles.

        Served rather than merely shipped: the executable contains around
        twenty-five third-party packages, and MIT and BSD-3-Clause both require
        the notice to accompany the binary. Someone who has only the .exe can
        read it here.
        """
        parts = []
        for name in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
            path = config.LICENCE_DIR / name
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                parts.append(f"===== {name} =====\n\n{text}")
        body = "\n\n".join(parts) or "Licence texts are not available in this build."
        return PlainTextResponse(body)

    @app.get("/health", include_in_schema=False)
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True, "version": __version__})

    @app.get("/overlay/{wheel_id}", include_in_schema=False)
    async def overlay(wheel_id: str) -> FileResponse:
        # The page reads the wheel id out of its own URL and subscribes over WS.
        return FileResponse(config.WEB_DIR / "overlay.html")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(config.WEB_DIR / "index.html")

    return app


app = create_app()
