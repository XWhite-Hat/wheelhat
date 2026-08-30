r"""Pre-Qt bootstrap orchestration.

``run()`` must be called before anything in the process touches PySide6.

Running from source it does nothing but pick a data folder: PySide6 comes from
the virtualenv, and there is no wizard to show. In the frozen build it owns the
whole first-run experience.

Bootstrap state lives at a fixed location that never moves, so WheelHat can
always find the user's chosen data folder again:

    Windows:  %LOCALAPPDATA%\WheelHat\bootstrap.json
    Fallback: ~/.wheelhat-bootstrap.json

After ``run()`` returns:

* ``WHEELHAT_DATA_DIR`` is set, so wheelhat.config resolves to the chosen folder
* ``<data_dir>/pyside6`` is on ``sys.path`` (frozen only)
* its native DLL directories are registered and preloaded (Windows, frozen only)
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

APP_NAME = "WheelHat"
DATA_DIR_ENV = "WHEELHAT_DATA_DIR"
PYSIDE_SUBDIR = "pyside6"
SMOKE_TEST_FLAG = "--pyside6-smoke-test"

#: Argument that means "no window, so no Qt, so no first-run download".
HEADLESS_FLAG = "--server"


def bootstrap_config_path() -> Path:
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        return Path(local_app) / APP_NAME / "bootstrap.json"
    return Path.home() / f".{APP_NAME.lower()}-bootstrap.json"


def default_data_parent() -> Path:
    documents = Path.home() / "Documents"
    return documents if documents.is_dir() else Path.home()


def read_bootstrap_config() -> dict:
    try:
        with bootstrap_config_path().open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_bootstrap_config(data_dir: str, pyside6_version: str = "") -> None:
    path = bootstrap_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_bootstrap_config()
    existing["data_dir"] = data_dir
    existing["pyside6_version"] = pyside6_version or existing.get("pyside6_version", "")
    with path.open("w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2)


def installed_version(data_dir: Path) -> str:
    from .downloader import VERSION_SENTINEL

    sentinel = Path(data_dir) / PYSIDE_SUBDIR / VERSION_SENTINEL
    try:
        return sentinel.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def install_looks_complete(data_dir: Path) -> bool:
    """Structural check that an install finished rather than merely started.

    The sentinel is written only after every wheel extracted successfully, so a
    part-finished download fails here instead of crashing later at import time.
    """
    from .downloader import VERSION_SENTINEL

    root = Path(data_dir) / PYSIDE_SUBDIR
    return (
        (root / "PySide6").is_dir()
        and (root / "shiboken6").is_dir()
        and (root / VERSION_SENTINEL).exists()
    )


def _log_path() -> Path:
    return bootstrap_config_path().parent / "bootstrap.log"


def trace(message: str) -> None:
    """Record a bootstrap step to a file.

    A windowed build has no console, so this is the only way anyone - user or
    maintainer - can find out why setup did not get as far as a window.
    """
    print(f"[bootstrap] {message}", flush=True)
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {message}\n")
    except OSError:
        pass


def ensure_streams() -> None:
    """Guarantee stdout and stderr exist before anything writes to them.

    A windowed PyInstaller build has neither. The bootstrap runs before
    wheelhat.__main__ gets a chance to fix that, and it prints diagnostics and
    imports customtkinter, so without this the very first write raises and the
    setup wizard never appears - the app just dies silently on a machine that
    needed the wizard most.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = sys.stdout


def run() -> None:
    """Entry point. Safe to call from source builds, where it does almost nothing."""
    ensure_streams()
    if not getattr(sys, "frozen", False):
        # From source, PySide6 lives in the venv and config.py picks its own
        # default folder. Nothing to bootstrap.
        return

    # Disposable subprocess mode, handled before anything else touches state.
    if len(sys.argv) >= 3 and sys.argv[1] == SMOKE_TEST_FLAG:
        _run_smoke_test(sys.argv[2])
        return  # unreachable

    trace(f"starting; argv={sys.argv[1:]}")
    config = read_bootstrap_config()
    data_dir = config.get("data_dir")
    have_dir = bool(data_dir) and Path(data_dir).is_dir()
    trace(f"data_dir={data_dir!r} exists={have_dir}")

    # Headless mode needs a data folder but never needs Qt, so it must not drag
    # a server operator through a GUI wizard or a 100 MB download.
    if HEADLESS_FLAG in sys.argv:
        chosen = Path(data_dir) if have_dir else default_data_parent() / APP_NAME
        chosen.mkdir(parents=True, exist_ok=True)
        os.environ[DATA_DIR_ENV] = str(chosen)
        if not have_dir:
            write_bootstrap_config(str(chosen))
        if install_looks_complete(chosen):
            _add_pyside6_to_path(chosen)
        return

    if not have_dir:
        trace("no usable data folder; opening the setup wizard")
        data_dir = _run_setup_wizard()
        if not data_dir:
            _abort("WheelHat needs a data folder to run.")
    elif not install_looks_complete(Path(data_dir)):
        root = Path(data_dir) / PYSIDE_SUBDIR
        partial = (root / "PySide6").is_dir() or (root / "shiboken6").is_dir()
        reason = "incomplete" if partial else "missing"
        trace(f"PySide6 install is {reason}; opening the repair dialog")
        data_dir = _run_recovery(data_dir, reason)
        if not data_dir:
            _abort("WheelHat needs PySide6 to show its window.")

    # Everything above only proves the files exist, never that they load. A
    # leftover install from another version, an interrupted removal, antivirus
    # quarantine or plain corruption all pass the checks above and still fail at
    # import. Catch it here, where there is a dialog to explain it, rather than
    # as an opaque crash later.
    #
    # Retried: freshly written unsigned DLLs can transiently fail to load while
    # an on-access scanner is still inspecting them, then succeed moments later.
    if not _pyside6_imports(data_dir, attempts=3):
        data_dir = _run_recovery(data_dir, "import_failed")
        if not data_dir or not _pyside6_imports(data_dir, attempts=1):
            _abort("PySide6 is installed but will not load on this PC.")

    trace(f"PySide6 ready in {data_dir}; handing over to the application")
    os.environ[DATA_DIR_ENV] = str(data_dir)
    _add_pyside6_to_path(Path(data_dir))


def _pyside6_imports(data_dir: str | Path, attempts: int = 3) -> bool:
    """Verify PySide6 actually imports, in a throwaway subprocess.

    This must not happen in this process. Proving importability means running
    the ctypes preload in _add_pyside6_to_path, and those DLLs then stay locked
    for the life of the process. If the check failed and recovery tried to wipe
    and re-extract that same folder, the wipe would silently fail on the locked
    files and the re-extract would hang. A subprocess's locks vanish when it
    exits, so the real process never touches a locked file.
    """
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                [sys.executable, SMOKE_TEST_FLAG, str(data_dir)],
                timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if result.returncode == 0:
                return True
        except Exception as exc:  # noqa: BLE001
            print(f"[bootstrap] smoke test could not run: {exc!r}", flush=True)
        print(f"[bootstrap] PySide6 import check {attempt}/{attempts} failed", flush=True)
        if attempt < attempts:
            time.sleep(1.5)
    return False


def _run_smoke_test(data_dir: str) -> None:
    """Subprocess entry point: import Qt and exit 0 or 1, releasing every lock."""
    _add_pyside6_to_path(Path(data_dir))
    try:
        import shiboken6  # noqa: F401
        from PySide6 import QtCore  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"[bootstrap] smoke test import failed: {exc!r}", flush=True)
        sys.exit(1)
    sys.exit(0)


def _add_pyside6_to_path(data_dir: Path) -> None:
    """Put the downloaded PySide6 on sys.path and make Windows able to load it."""
    root = Path(data_dir) / PYSIDE_SUBDIR
    pyside_pkg = root / "PySide6"
    shiboken_pkg = root / "shiboken6"

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    if sys.platform == "win32":
        # shiboken6 first: PySide6/__init__.py depends on it at import time.
        for dll_dir in (shiboken_pkg, pyside_pkg):
            if dll_dir.is_dir():
                with contextlib.suppress(AttributeError, OSError):
                    os.add_dll_directory(str(dll_dir))
        # add_dll_directory alone is not enough in a windowed PyInstaller build:
        # the console bootloader honours it, the windowed one does not, and Qt
        # fails with "DLL load failed while importing Shiboken". Loading each
        # DLL by full path puts it in the process by name, so every later
        # dependency lookup resolves against it regardless of search path.
        _preload_dlls(shiboken_pkg)
        _preload_dlls(pyside_pkg)

    _ensure_shiboken_init(shiboken_pkg)


def _preload_dlls(package_dir: Path) -> None:
    if not package_dir.is_dir():
        return
    import ctypes

    for dll in sorted(package_dir.glob("*.dll")):
        try:
            ctypes.WinDLL(str(dll))
        except OSError as exc:
            print(f"[bootstrap] could not preload {dll.name}: {exc}", flush=True)


def _ensure_shiboken_init(shiboken_pkg: Path) -> None:
    """Write the package init shim the cp-specific shiboken6 wheel omits.

    shiboken6 ships an abi3 wheel containing __init__.py and a cp-specific one
    containing only Shiboken.pyd. If the latter is chosen, shiboken6 becomes a
    namespace package whose __file__ is None, and Qt's signature bootstrap
    crashes on it.
    """
    init = shiboken_pkg / "__init__.py"
    if init.exists() or not (shiboken_pkg / "Shiboken.pyd").exists():
        return
    with contextlib.suppress(OSError):
        init.write_text(
            "# Written by the WheelHat bootstrap: the cp-specific shiboken6\n"
            "# wheel omits this file, so shiboken6 would otherwise be a\n"
            "# namespace package and Qt signature bootstrap would fail.\n"
            "from shiboken6.Shiboken import *  # noqa: F401,F403\n",
            encoding="utf-8",
        )


def _run_setup_wizard() -> str | None:
    try:
        from .ui import run_setup_wizard
    except Exception as exc:  # noqa: BLE001
        trace(f"could not load the setup wizard: {exc!r}")
        raise

    data_dir = run_setup_wizard(str(default_data_parent()))
    if data_dir:
        write_bootstrap_config(data_dir, installed_version(Path(data_dir)))
    return data_dir


def _run_recovery(data_dir: str, reason: str) -> str | None:
    try:
        from .ui import run_recovery_dialog
    except Exception as exc:  # noqa: BLE001 - the wizard is the last line of defence
        trace(f"could not load the repair dialog: {exc!r}")
        raise

    result = run_recovery_dialog(data_dir, reason=reason)
    if result:
        write_bootstrap_config(result, installed_version(Path(result)))
    return result


def _abort(message: str) -> None:
    """Explain why WheelHat is closing, then close. Never raises."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            f"{APP_NAME} cannot start",
            f"{message}\n\nStart {APP_NAME} again to try the setup once more.",
        )
        root.destroy()
    except Exception:  # noqa: BLE001 - a console message is better than nothing
        print(f"[bootstrap] {APP_NAME} cannot start: {message}", flush=True)
    sys.exit(1)
