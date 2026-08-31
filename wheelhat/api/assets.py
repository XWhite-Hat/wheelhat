"""The image and sound library backing wheel customisation.

Files live in the data directory and are served read-only from ``/assets``.
Uploads are the one place a user hands WheelHat a filename, so names are
rebuilt from scratch rather than trusted, and content is checked against the
extension it claims.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import config

router = APIRouter(prefix="/assets", tags=["assets"])

MAX_BYTES = 8 * 1024 * 1024

#: extension -> leading bytes that a real file of that type starts with.
#: SVG is text and has no reliable signature, so it is validated separately.
SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    ".wav": (b"RIFF",),
    ".ogg": (b"OggS",),
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
SOUND_EXTENSIONS = {".mp3", ".wav", ".ogg"}
ALLOWED = IMAGE_EXTENSIONS | SOUND_EXTENSIONS

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
# Both separators, on every platform. Path().name only splits on the ones
# native to the host, so a Windows-style name uploaded to a Linux stream box
# would otherwise keep its directories, flattened into the filename.
_SEPARATORS = re.compile(r"[\\/]")


def safe_name(raw: str) -> str:
    """Build a filename we are willing to write, ignoring whatever was sent.

    Only the final path component is considered, and everything outside a
    conservative character set is collapsed, so no input can escape the assets
    directory or produce a hidden/relative name.
    """
    stem = _SEPARATORS.split(raw or "")[-1]
    cleaned = _SAFE.sub("-", stem).strip("-._")
    if not cleaned:
        raise HTTPException(status_code=422, detail="That filename cannot be used.")
    suffix = Path(cleaned).suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(
            status_code=422,
            detail=f"'{suffix or 'that file type'}' is not allowed. "
            f"Use one of: {', '.join(sorted(ALLOWED))}",
        )
    base = Path(cleaned).stem[:60].strip("-._") or "asset"
    return f"{base}{suffix}"


def resolve(name: str) -> Path:
    """Map a stored name onto a path, refusing anything outside the folder."""
    target = (config.ASSETS_DIR / _SEPARATORS.split(name or "")[-1]).resolve()
    root = config.ASSETS_DIR.resolve()
    if root not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid asset name.")
    return target


def kind_of(suffix: str) -> str:
    return "image" if suffix in IMAGE_EXTENSIONS else "sound"


def describe(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "url": f"/assets/{path.name}",
        "kind": kind_of(path.suffix.lower()),
        "bytes": stat.st_size,
        "modified_at": stat.st_mtime,
    }


def content_looks_right(suffix: str, head: bytes) -> bool:
    if suffix == ".svg":
        text = head[:1024].lstrip().lower()
        return text.startswith(b"<?xml") or text.startswith(b"<svg") or b"<svg" in text
    signatures = SIGNATURES.get(suffix)
    return not signatures or head.startswith(signatures)


@router.get("")
async def list_assets() -> dict[str, Any]:
    config.ensure_dirs()
    files = [
        describe(p)
        for p in sorted(config.ASSETS_DIR.iterdir())
        if p.is_file() and p.suffix.lower() in ALLOWED
    ]
    return {
        "assets": files,
        "folder": str(config.ASSETS_DIR),
        "max_bytes": MAX_BYTES,
        "allowed": sorted(ALLOWED),
    }


@router.post("", status_code=201)
async def upload_asset(file: UploadFile = File(...)) -> dict[str, Any]:
    config.ensure_dirs()
    name = safe_name(file.filename or "")
    suffix = Path(name).suffix.lower()

    payload = await file.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That file is larger than {MAX_BYTES // (1024 * 1024)} MB.",
        )
    if not payload:
        raise HTTPException(status_code=422, detail="That file is empty.")
    if not content_looks_right(suffix, payload[:64]):
        raise HTTPException(
            status_code=422,
            detail=f"That does not look like a {suffix} file. Check the file and try again.",
        )

    # Never silently overwrite something a wheel already points at.
    target = resolve(name)
    if target.exists():
        stem, index = target.stem, 2
        while target.exists():
            target = config.ASSETS_DIR / f"{stem}-{index}{suffix}"
            index += 1

    target.write_bytes(payload)
    return {"asset": describe(target)}


@router.delete("/{name}")
async def delete_asset(name: str) -> dict[str, Any]:
    target = resolve(name)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="No such asset.")
    target.unlink()
    return {"deleted": target.name}


@router.post("/import-url")
async def import_from_url(payload: dict[str, str]) -> dict[str, Any]:
    """Copy an image that already exists on this machine into the library."""
    source = Path(payload.get("path", "")).expanduser()
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"No file at {source}")
    if source.stat().st_size > MAX_BYTES:
        raise HTTPException(status_code=413, detail="That file is too large.")

    config.ensure_dirs()
    name = safe_name(source.name)
    target = resolve(name)
    stem, suffix, index = target.stem, target.suffix, 2
    while target.exists():
        target = config.ASSETS_DIR / f"{stem}-{index}{suffix}"
        index += 1
    shutil.copyfile(source, target)
    return {"asset": describe(target)}
