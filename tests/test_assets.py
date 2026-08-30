"""The asset library: uploads, validation, and the paths that must not escape."""

import httpx
import pytest

from wheelhat import config
from wheelhat.api.assets import safe_name
from wheelhat.app import app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'


@pytest.fixture
async def client():
    config.ensure_dirs()
    for existing in config.ASSETS_DIR.iterdir():
        if existing.is_file():
            existing.unlink()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def upload(name, payload, content_type="image/png"):
    return {"file": (name, payload, content_type)}


# ------------------------------------------------------------------ filenames


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("logo.png", "logo.png"),
        ("my logo!.png", "my-logo.png"),
        ("../../../../etc/passwd.png", "passwd.png"),
        (r"..\..\windows\system32\evil.png", "evil.png"),
        ("/absolute/path/pic.jpg", "pic.jpg"),
        (".hidden.png", "hidden.png"),
    ],
)
def test_filenames_are_rebuilt_not_trusted(raw, expected):
    assert safe_name(raw) == expected


def test_filenames_without_a_usable_name_are_refused():
    from fastapi import HTTPException

    for raw in ("", "   ", "../..", "....png"):
        with pytest.raises(HTTPException):
            safe_name(raw)


def test_disallowed_extensions_are_refused():
    from fastapi import HTTPException

    for raw in ("payload.exe", "script.js", "thing.html", "note.txt", "archive.zip"):
        with pytest.raises(HTTPException) as exc:
            safe_name(raw)
        assert exc.value.status_code == 422


# --------------------------------------------------------------------- upload


async def test_upload_and_list(client):
    response = await client.post("/api/assets", files=upload("logo.png", PNG))
    assert response.status_code == 201
    asset = response.json()["asset"]
    assert asset["name"] == "logo.png"
    assert asset["url"] == "/assets/logo.png"
    assert asset["kind"] == "image"

    listing = (await client.get("/api/assets")).json()
    assert [a["name"] for a in listing["assets"]] == ["logo.png"]


async def test_upload_writes_inside_the_assets_folder_only(client):
    await client.post("/api/assets", files=upload("../../escape.png", PNG))
    written = [p.name for p in config.ASSETS_DIR.iterdir() if p.is_file()]
    assert written == ["escape.png"]
    assert not (config.ASSETS_DIR.parent / "escape.png").exists()


async def test_a_second_upload_does_not_overwrite_the_first(client):
    """A wheel may already point at the existing file."""
    await client.post("/api/assets", files=upload("logo.png", PNG))
    second = await client.post("/api/assets", files=upload("logo.png", PNG))
    assert second.json()["asset"]["name"] == "logo-2.png"
    assert len((await client.get("/api/assets")).json()["assets"]) == 2


async def test_content_must_match_the_extension(client):
    """A renamed executable must not become a servable asset."""
    response = await client.post("/api/assets", files=upload("evil.png", b"MZ\x90\x00not a png"))
    assert response.status_code == 422
    assert "does not look like" in response.json()["detail"]


async def test_real_formats_are_accepted(client):
    for name, payload in (("a.png", PNG), ("b.jpg", JPEG), ("c.gif", GIF), ("d.svg", SVG)):
        response = await client.post("/api/assets", files=upload(name, payload))
        assert response.status_code == 201, (name, response.text)


async def test_empty_file_is_refused(client):
    response = await client.post("/api/assets", files=upload("empty.png", b""))
    assert response.status_code == 422


async def test_oversized_file_is_refused(client):
    from wheelhat.api.assets import MAX_BYTES

    payload = PNG + b"\x00" * (MAX_BYTES + 1)
    response = await client.post("/api/assets", files=upload("huge.png", payload))
    assert response.status_code == 413


async def test_executable_extension_is_refused_by_the_endpoint(client):
    response = await client.post("/api/assets", files=upload("nasty.exe", b"MZ\x90\x00"))
    assert response.status_code == 422
    assert "not allowed" in response.json()["detail"]


# --------------------------------------------------------------------- delete


async def test_delete_removes_the_file(client):
    await client.post("/api/assets", files=upload("gone.png", PNG))
    assert (await client.delete("/api/assets/gone.png")).status_code == 200
    assert (await client.get("/api/assets")).json()["assets"] == []


async def test_delete_of_a_missing_asset_is_a_404(client):
    assert (await client.delete("/api/assets/nope.png")).status_code == 404


async def test_delete_cannot_escape_the_assets_folder(client):
    """A traversal attempt must not reach the database next door."""
    database = config.DB_PATH
    assert database.exists()
    response = await client.delete("/api/assets/..%2Fwheelhat.db")
    assert response.status_code in (400, 404)
    assert database.exists()


# ------------------------------------------------------------------- serving


async def test_uploaded_assets_are_served_with_an_inert_csp(client):
    """SVGs are same-origin, so opening one directly must not run scripts."""
    await client.post("/api/assets", files=upload("art.svg", SVG))
    response = await client.get("/assets/art.svg")
    assert response.status_code == 200
    csp = response.headers.get("content-security-policy", "")
    assert "sandbox" in csp
    assert "default-src 'none'" in csp
    assert response.headers.get("x-content-type-options") == "nosniff"
