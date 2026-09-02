"""Saved looks that new wheels can start from.

A template holds a wheel's appearance and spin settings and nothing else. What
makes a wheel a particular wheel - its slices, their actions, its triggers - is
exactly what nobody wants to inherit and then delete.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..models import Template

router = APIRouter(prefix="/templates", tags=["templates"])


class SavePayload(BaseModel):
    name: str = ""
    #: Capture the look from this wheel. Without it the template is the
    #: defaults, which is a reasonable starting point of its own.
    wheel_id: str = ""


class RenamePayload(BaseModel):
    name: str


def _summary(template: Template) -> dict[str, Any]:
    """Enough for a list without sending every appearance field."""
    look = template.appearance
    return {
        "id": template.id,
        "name": template.name,
        "updated_at": template.updated_at,
        # A few things worth showing on a card.
        "palette": look.palette[:6],
        "background": look.background,
        "has_background_image": bool(look.background_image.url),
        "has_frame_image": bool(look.frame_image.url),
    }


@router.get("")
async def list_templates() -> dict[str, Any]:
    return {"templates": [_summary(t) for t in db.list_templates()]}


@router.get("/{template_id}")
async def get_template(template_id: str) -> dict[str, Any]:
    template = db.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="No such template.")
    return template.model_dump()


@router.post("", status_code=201)
async def save_template(payload: SavePayload) -> dict[str, Any]:
    """Save a look, usually captured from a wheel someone has already styled."""
    template = Template(name=payload.name.strip() or "Untitled template")

    if payload.wheel_id:
        wheel = db.get_wheel(payload.wheel_id)
        if wheel is None:
            raise HTTPException(status_code=404, detail="No such wheel.")
        # Copies, so later edits to the wheel do not rewrite the template.
        template.appearance = wheel.appearance.model_copy(deep=True)
        template.spin = wheel.spin.model_copy(deep=True)
        if not payload.name.strip():
            template.name = f"{wheel.name} look"

    db.save_template(template)
    return template.model_dump()


@router.put("/{template_id}")
async def rename_template(template_id: str, payload: RenamePayload) -> dict[str, Any]:
    template = db.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="No such template.")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A template needs a name.")
    template.name = name
    db.save_template(template)
    return template.model_dump()


@router.delete("/{template_id}")
async def delete_template(template_id: str) -> dict[str, Any]:
    if not db.delete_template(template_id):
        raise HTTPException(status_code=404, detail="No such template.")
    return {"deleted": template_id}
