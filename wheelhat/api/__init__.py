from fastapi import APIRouter

from . import actions, assets, discovery, integrations, settings, twitch, wheels

api_router = APIRouter(prefix="/api")
api_router.include_router(wheels.router)
api_router.include_router(actions.router)
api_router.include_router(assets.router)
api_router.include_router(integrations.router)
api_router.include_router(discovery.router)
api_router.include_router(twitch.router)
api_router.include_router(settings.router)

__all__ = ["api_router"]
