# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the all-in-one WheelHat executable.

Run from the repo root:
    pyinstaller build/wheelhat.spec --noconfirm --clean --distpath dist

PySide6 IS DELIBERATELY NOT BUNDLED.
It is downloaded into the user's data folder on first run by
wheelhat/bootstrap. That keeps this executable small, and it keeps the LGPL
obligation simple: Qt stays a separate, user-replaceable set of files rather
than something linked into a single binary.

customtkinter IS bundled - it draws the first-run wizard, which has to work
before Qt exists.
"""

import sys
from pathlib import Path

HERE = Path(SPECPATH).parent  # spec lives in build/, so the parent is the repo root

# customtkinter needs its theme JSON at runtime; darkdetect is its dependency.
try:
    from PyInstaller.utils.hooks import collect_all

    ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
    dd_datas, dd_binaries, dd_hidden = collect_all("darkdetect")
    ctk_datas += dd_datas
    ctk_binaries += dd_binaries
    ctk_hidden += dd_hidden
except Exception:
    ctk_datas, ctk_binaries, ctk_hidden = [], [], []

# python3.dll is the stable-ABI forwarder that PySide6's abi3 wheels link
# against. PyInstaller only bundles it as a side effect of some other
# dependency needing it, so whether it lands in the build has silently depended
# on what else happened to be installed. Bundle it deliberately - without it the
# downloaded PySide6 cannot load.
_python3_dll = Path(sys.base_prefix) / "python3.dll"
_extra_binaries = [(str(_python3_dll), ".")] if _python3_dll.is_file() else []

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(HERE)],
    binaries=[*ctk_binaries, *_extra_binaries],
    datas=[
        (str(HERE / "wheelhat" / "web"), "wheelhat/web"),
        *ctk_datas,
    ],
    hiddenimports=[
        # Reached only through a string or at runtime, so not seen by analysis.
        "wheelhat.app",
        "wheelhat.actions.handlers",
        "wheelhat.desktop",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "websockets",
        "anyio",
        "anyio._backends._asyncio",
        # Starlette reaches for this only when a multipart upload arrives.
        "python_multipart",
        "multipart",
        # The first-run wizard.
        "tkinter",
        "tkinter.filedialog",
        "tkinter.messagebox",
        *ctk_hidden,
    ],
    excludes=[
        # Downloaded at runtime, never bundled. This is the LGPL mechanism.
        "PySide6",
        "shiboken6",
        "PyQt5",
        "PyQt6",
        # Pillow is only used by tools/make_icon.py to regenerate the icon.
        # customtkinter picks it up opportunistically when present, which adds
        # ~7 MB to the build for a feature the wizard does not use.
        "PIL",
        "Pillow",
        # Never used, and large.
        "matplotlib",
        "numpy",
        "pytest",
        "IPython",
        "notebook",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WheelHat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=str(HERE / "wheelhat" / "web" / "static" / "img" / "icon.ico"),
    onefile=True,
)
