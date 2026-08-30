"""Action schemas, live dropdown options, and one-off action testing."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import db
from ..actions import options as options_module
from ..actions import schema_payload
from ..actions.executor import ActionFailed, ExecContext, execute_single

router = APIRouter(tags=["actions"])


class TestActionRequest(BaseModel):
    action: dict[str, Any]
    wheel_id: str = ""
    winner: str = "Test slice"
    variables: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


@router.get("/actions/schemas")
async def action_schemas() -> dict[str, Any]:
    return schema_payload()


@router.get("/actions/log")
async def action_log(limit: int = 100) -> dict[str, Any]:
    return {"entries": db.list_action_log(limit)}


@router.get("/options/{source:path}")
async def options(source: str, request: Request) -> dict[str, Any]:
    """Resolve a dynamic option list. Query params become resolver params."""
    params = dict(request.query_params)
    return await options_module.resolve(source, params)


@router.post("/actions/test")
async def test_action(payload: TestActionRequest) -> dict[str, Any]:
    wheel = db.get_wheel(payload.wheel_id) if payload.wheel_id else None
    variables = {"user": "TestUser", "user_login": "testuser", **payload.variables}
    ctx = ExecContext(
        wheel_id=payload.wheel_id,
        wheel_name=wheel.name if wheel else "Test wheel",
        winner=payload.winner,
        source="test",
        variables=variables,
        dry_run=payload.dry_run,
    )
    try:
        detail = await execute_single(payload.action, ctx)
    except ActionFailed as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "detail": detail}
