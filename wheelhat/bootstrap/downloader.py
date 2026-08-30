"""Fetch PySide6 from PyPI with integrity verification.

Two layers:

1. A Socket.dev score check, which flags malware and supply-chain problems.
   Fail-open - a Socket outage must never block a legitimate install.
2. A PyPI SHA-256 check, which is the hard gate. A mismatch deletes the file
   and refuses the install.

Standard library only: this runs before Qt exists and must not depend on
anything the application itself needs.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

USER_AGENT = "wheelhat-bootstrap/1.0"

_PYPI_LATEST = "https://pypi.org/pypi/{name}/json"
_PYPI_VER = "https://pypi.org/pypi/{name}/{version}/json"

# PURL form for PyPI is pkg:pypi/<name>@<version>.
_SOCKET_PURL = "https://socket.dev/api/report/purl?purl=pkg%3Apypi%2F{name}%40{version}"
_SOCKET_MIN = 0.60
_SOCKET_TIMEOUT = 10

#: Everything `pip install PySide6` would pull in. shiboken6 comes first
#: because PySide6/__init__.py expects it as a sibling at import time.
PYSIDE6_PACKAGES = ["shiboken6", "PySide6", "PySide6_Essentials", "PySide6_Addons"]

#: (bytes_done, bytes_total, status_message)
ProgressCb = Callable[[int, int, str], None]

VERSION_SENTINEL = ".pyside6_version"


def download_pyside6(
    target_dir: Path,
    progress_cb: ProgressCb | None = None,
) -> tuple[bool, str]:
    """Download, verify and extract PySide6 into *target_dir*.

    Returns ``(ok, message)``; on success the message is the installed version.
    """

    def report(done: int, total: int, msg: str) -> None:
        if progress_cb:
            progress_cb(done, total, msg)

    # Wipe first. extractall only overwrites files the new wheels contain, so
    # extracting over an older install leaves a mix of versions that each
    # "exist" but do not work together - which surfaces as exactly the opaque
    # "DLL load failed" this whole mechanism exists to avoid.
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    report(0, 0, "Checking PyPI for the latest PySide6 release...")
    version, err = _find_safe_version(report)
    if not version:
        return False, err

    for package in PYSIDE6_PACKAGES:
        report(0, 0, f"Locating {package} {version}...")
        url, digest, err = _wheel_info(package, version)
        if err:
            return False, f"Cannot locate {package} {version}: {err}"

        wheel = target_dir / f"_dl_{package}.whl"
        ok, err = _download(
            url, wheel, lambda n, t, p=package: report(n, t, f"Downloading {p}...")
        )
        if not ok:
            wheel.unlink(missing_ok=True)
            return False, f"Download failed for {package}: {err}"

        report(0, 0, f"Verifying {package}...")
        if not _sha256_matches(wheel, digest):
            wheel.unlink(missing_ok=True)
            return False, (
                f"SHA-256 mismatch for {package}. The download may have been "
                "tampered with in transit, so it has been discarded."
            )

        report(0, 0, f"Extracting {package}...")
        with zipfile.ZipFile(wheel, "r") as archive:
            archive.extractall(target_dir)
        wheel.unlink(missing_ok=True)

    # Written last, and only on full success: the bootstrap treats this file as
    # proof that an install completed rather than merely started.
    (target_dir / VERSION_SENTINEL).write_text(version, encoding="utf-8")
    report(1, 1, "Done.")
    return True, version


def _find_safe_version(progress_cb: ProgressCb | None) -> tuple[str | None, str]:
    """Newest PySide6 whose Socket.dev score is acceptable."""
    try:
        request = urllib.request.Request(
            _PYPI_LATEST.format(name="PySide6"), headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
    except Exception as exc:  # noqa: BLE001 - reported to the user verbatim
        return None, f"Cannot reach PyPI: {exc}"

    versions = sorted(data.get("releases", {}), key=_version_tuple, reverse=True)
    if not versions:
        return None, "No PySide6 releases found on PyPI."

    socket_unreachable = False
    for version in versions[:5]:
        if progress_cb:
            progress_cb(0, 0, f"Checking PySide6 {version} with Socket.dev...")
        passed, note = _socket_check("pyside6", version)
        if passed:
            return version, ""
        if note.startswith("unreachable"):
            socket_unreachable = True
        else:
            print(f"[bootstrap] PySide6 {version} flagged by Socket.dev: {note}", flush=True)

    latest = versions[0]
    reason = (
        "Socket.dev unreachable"
        if socket_unreachable
        else "no clean score in the last 5 releases"
    )
    print(
        f"[bootstrap] WARNING: {reason}; using PySide6 {latest}. "
        "The PyPI SHA-256 is still verified.",
        flush=True,
    )
    return latest, ""


def _socket_check(name: str, version: str) -> tuple[bool, str]:
    """Fail-open safety score. The hash check is the hard gate, not this."""
    if _SOCKET_MIN is None:
        return True, "check disabled"
    try:
        request = urllib.request.Request(
            _SOCKET_PURL.format(name=name.lower(), version=version),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=_SOCKET_TIMEOUT) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True, "not yet analysed by Socket.dev"
        return True, f"unreachable (HTTP {exc.code})"
    except Exception as exc:  # noqa: BLE001
        return True, f"unreachable ({exc})"

    for alert in body.get("alerts") or body.get("issues") or []:
        if not isinstance(alert, dict):
            continue
        kind = str(alert.get("type") or alert.get("severity") or "").lower()
        if kind in {"malware", "suspicious", "obfuscatedcode", "obfuscated-code"}:
            return False, f"Socket.dev alert: {kind} in {name}=={version}"

    score = body.get("score") or body.get("overallScore") or body.get("overall_score")
    if score is None:
        return True, "no score in the Socket.dev response"
    if isinstance(score, (int, float)) and score > 1:
        score = score / 100.0
    if isinstance(score, (int, float)) and score < _SOCKET_MIN:
        return False, f"Socket.dev score {score:.2f} is below {_SOCKET_MIN}"
    return True, f"Socket.dev score {score:.2f}"


def _wheel_info(package: str, version: str) -> tuple[str | None, str | None, str]:
    """Pick the best wheel for this interpreter and return its URL and hash."""
    try:
        request = urllib.request.Request(
            _PYPI_VER.format(name=package, version=version),
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        return None, None, str(exc)

    wheels = [f for f in data.get("urls", []) if f.get("packagetype") == "bdist_wheel"]
    if not wheels:
        return None, None, f"no wheel on PyPI for {package}=={version}"

    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    machine = platform.machine().lower()
    plat_tag = {
        "amd64": "win_amd64",
        "x86_64": "win_amd64",
        "arm64": "win_arm64",
    }.get(machine, f"win_{machine}")

    def rank(entry: dict) -> int:
        filename = entry.get("filename", "")
        # shiboken6 publishes a thin cp-specific wheel (just Shiboken.pyd) and a
        # full abi3 wheel that includes __init__.py. Prefer abi3, or shiboken6
        # becomes a namespace package whose __file__ is None, and Qt's signature
        # bootstrap dies on it.
        if package.lower() == "shiboken6" and "abi3" in filename and plat_tag in filename:
            return 0
        if py_tag in filename and plat_tag in filename:
            return 1
        if "abi3" in filename and plat_tag in filename:
            return 2
        if plat_tag in filename:
            return 3
        if "none-any" in filename:
            return 4
        return 99

    wheels.sort(key=rank)
    best = wheels[0]
    if rank(best) == 99:
        return None, None, f"no wheel for this platform ({plat_tag}) in {package}=={version}"

    digest = best.get("digests", {}).get("sha256")
    if not digest:
        return None, None, f"PyPI returned no SHA-256 for {package}=={version}"
    return best["url"], digest, ""


def _download(
    url: str, destination: Path, progress_cb: Callable[[int, int], None] | None = None
) -> tuple[bool, str]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with destination.open("wb") as handle:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _sha256_matches(path: Path, expected: str | None) -> bool:
    if not expected:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected.lower()


def _version_tuple(version: str) -> tuple:
    """Sortable version key; unparsable segments sort last."""
    parts = []
    for chunk in version.replace("-", ".").split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return tuple(parts)
