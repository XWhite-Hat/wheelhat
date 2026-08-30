"""First-run wizard and repair dialogs, shown before Qt exists.

Built on customtkinter (MIT), which is bundled into the executable. tkinter
itself is part of the standard library, so the abort path always has something
to show even if customtkinter is unavailable.

Public surface:

    run_setup_wizard(default_parent) -> str | None
        Welcome, folder choice, download. Returns the data folder, or None if
        the user cancelled.

    run_recovery_dialog(data_dir, reason) -> str | None
        Shown when the folder exists but PySide6 is missing, incomplete or
        will not import. Returns a usable data folder, or None to give up.
"""

from __future__ import annotations

import contextlib
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from .check import trace
from .downloader import download_pyside6

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

APP_NAME = "WheelHat"
_VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

_TITLE_FONT = ("Segoe UI", 19, "bold")
_BODY_FONT = ("Segoe UI", 12)
_SMALL_FONT = ("Segoe UI", 11)



def _present(window, label: str) -> None:
    """Map the window and put it in front of whatever is already open."""
    window.update_idletasks()
    window.deiconify()
    window.lift()
    window.attributes("-topmost", True)
    window.after(200, lambda: window.attributes("-topmost", False))
    # Focus is best effort; some window managers refuse it.
    with contextlib.suppress(Exception):
        window.focus_force()
    trace(f"{label} is on screen")


def _centre(window, width: int, height: int) -> None:
    window.update_idletasks()
    x = (window.winfo_screenwidth() - width) // 2
    y = (window.winfo_screenheight() - height) // 2
    window.geometry(f"{width}x{height}+{x}+{max(0, y - 40)}")


def _human(size: int) -> str:
    megabytes = size / (1024 * 1024)
    return f"{megabytes:.1f} MB" if megabytes >= 0.1 else f"{size} B"


class _DownloadMixin:
    """Shared download-with-progress behaviour for the wizard and repair dialog."""

    def _start_download(self, target: Path, on_done) -> None:
        """Run the download off the UI thread and marshal updates back to it."""
        self._cancelled = False

        def progress(done: int, total: int, message: str) -> None:
            # Tk is not thread-safe; hop back to the UI thread.
            self.after(0, lambda: self._on_progress(done, total, message))

        def worker() -> None:
            ok, detail = download_pyside6(target, progress)
            self.after(0, lambda: on_done(ok, detail))

        threading.Thread(target=worker, daemon=True, name="pyside6-download").start()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if getattr(self, "_status", None) is not None:
            self._status.configure(text=message)
        bar = getattr(self, "_bar", None)
        if bar is None:
            return
        if total > 0:
            bar.set(min(1.0, done / total))
            if getattr(self, "_detail", None) is not None:
                self._detail.configure(text=f"{_human(done)} of {_human(total)}")
        else:
            # Unknown length: keep it moving so it does not look stuck.
            bar.configure(mode="indeterminate")
            bar.start()


class _SetupWizard(ctk.CTk, _DownloadMixin):
    """Welcome -> choose a folder -> download Qt."""

    def __init__(self, default_parent: str) -> None:
        super().__init__()
        self.title(f"{APP_NAME} setup")
        self.resizable(False, False)
        _centre(self, 560, 430)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.result: str | None = None
        self._parent_dir = ctk.StringVar(value=default_parent)
        self._make_subfolder = ctk.BooleanVar(value=True)
        self._status = None
        self._bar = None
        self._detail = None

        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.pack(fill="both", expand=True, padx=30, pady=26)
        self._show_welcome()
        _present(self, "setup wizard")

    # ---------------------------------------------------------------- screens

    def _clear(self) -> None:
        for child in self._body.winfo_children():
            child.destroy()

    def _show_welcome(self) -> None:
        self._clear()
        ctk.CTkLabel(self._body, text=f"Welcome to {APP_NAME}", font=_TITLE_FONT).pack(
            anchor="w"
        )
        ctk.CTkLabel(
            self._body,
            justify="left",
            font=_BODY_FONT,
            text=(
                f"\n{APP_NAME} needs two things before it can start.\n\n"
                "1.  A folder for your wheels, images and settings.\n"
                "     Everything it saves lives there, and nowhere else.\n\n"
                "2.  The Qt interface library (PySide6), about 100 MB.\n"
                "     It is downloaded from PyPI and checked against the\n"
                "     official SHA-256 before anything is installed.\n\n"
                "Qt is kept beside your data rather than inside the\n"
                "application so you are free to replace it, which is what\n"
                "its licence asks for.\n"
            ),
        ).pack(anchor="w")

        row = ctk.CTkFrame(self._body, fg_color="transparent")
        row.pack(side="bottom", fill="x", pady=(10, 0))
        ctk.CTkButton(row, text="Cancel", width=110, fg_color="gray30", command=self._cancel).pack(
            side="left"
        )
        ctk.CTkButton(row, text="Continue", width=140, command=self._show_folder).pack(side="right")

    def _show_folder(self) -> None:
        self._clear()
        ctk.CTkLabel(self._body, text="Where should it save?", font=_TITLE_FONT).pack(anchor="w")
        ctk.CTkLabel(
            self._body,
            justify="left",
            font=_BODY_FONT,
            text=(
                "\nPick somewhere you can find again, and that is not\n"
                "inside Program Files.\n"
            ),
        ).pack(anchor="w")

        picker = ctk.CTkFrame(self._body, fg_color="transparent")
        picker.pack(fill="x", pady=(6, 4))
        entry = ctk.CTkEntry(picker, textvariable=self._parent_dir, height=34)
        entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(picker, text="Browse", width=90, command=self._browse).pack(
            side="left", padx=(8, 0)
        )

        ctk.CTkCheckBox(
            self._body,
            text=f"Create a {APP_NAME} folder inside it",
            variable=self._make_subfolder,
            font=_SMALL_FONT,
            command=self._refresh_preview,
        ).pack(anchor="w", pady=(10, 4))

        self._preview = ctk.CTkLabel(
            self._body, text="", font=_SMALL_FONT, text_color="gray60", justify="left"
        )
        self._preview.pack(anchor="w")
        self._error = ctk.CTkLabel(self._body, text="", font=_SMALL_FONT, text_color="#f0616d")
        self._error.pack(anchor="w", pady=(6, 0))
        self._parent_dir.trace_add("write", lambda *_: self._refresh_preview())
        self._refresh_preview()

        row = ctk.CTkFrame(self._body, fg_color="transparent")
        row.pack(side="bottom", fill="x", pady=(10, 0))
        ctk.CTkButton(row, text="Back", width=110, fg_color="gray30", command=self._show_welcome).pack(
            side="left"
        )
        ctk.CTkButton(row, text="Install", width=140, command=self._begin_install).pack(side="right")

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self._parent_dir.get() or str(Path.home()))
        if chosen:
            self._parent_dir.set(chosen)

    def _target_dir(self) -> Path:
        base = Path(self._parent_dir.get().strip() or str(Path.home()))
        return base / APP_NAME if self._make_subfolder.get() else base

    def _refresh_preview(self) -> None:
        self._preview.configure(text=f"Everything will be saved in:\n{self._target_dir()}")

    def _begin_install(self) -> None:
        target = self._target_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".wheelhat-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            self._error.configure(text=f"That folder cannot be written to: {exc.strerror or exc}")
            return
        self._show_download(target)

    def _show_download(self, target: Path) -> None:
        self._clear()
        ctk.CTkLabel(self._body, text="Setting up", font=_TITLE_FONT).pack(anchor="w")
        ctk.CTkLabel(
            self._body,
            justify="left",
            font=_BODY_FONT,
            text=f"\nDownloading the Qt interface library into\n{target}\n",
        ).pack(anchor="w")

        self._bar = ctk.CTkProgressBar(self._body, height=14)
        self._bar.set(0)
        self._bar.pack(fill="x", pady=(14, 8))
        self._status = ctk.CTkLabel(self._body, text="Starting…", font=_SMALL_FONT, wraplength=480)
        self._status.pack(anchor="w")
        self._detail = ctk.CTkLabel(self._body, text="", font=_SMALL_FONT, text_color="gray60")
        self._detail.pack(anchor="w")

        self._cancel_button = ctk.CTkButton(
            self._body, text="Cancel", width=110, fg_color="gray30", command=self._cancel
        )
        self._cancel_button.pack(side="bottom", anchor="w", pady=(10, 0))

        self._start_download(target / "pyside6", lambda ok, detail: self._finished(ok, detail, target))

    def _finished(self, ok: bool, detail: str, target: Path) -> None:
        if self._bar is not None:
            self._bar.stop()
            self._bar.configure(mode="determinate")
            self._bar.set(1.0 if ok else 0.0)
        if ok:
            self.result = str(target)
            self.destroy()
            return
        self._status.configure(text=f"Setup failed.\n\n{detail}", text_color="#f0616d")
        self._detail.configure(text="")
        self._cancel_button.configure(text="Close")

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


_REASON_TEXT = {
    "missing": (
        "The Qt interface library is not in your data folder.",
        "It was probably never finished downloading, or it has been deleted.",
        "Download it",
    ),
    "incomplete": (
        "The Qt interface library is only partly installed.",
        "A download was interrupted, so some of the files are missing.",
        "Repair it",
    ),
    "import_failed": (
        "The Qt interface library will not load.",
        "The files are there but Windows refuses to load them. This is usually a\n"
        "leftover install from another version, an interrupted removal, or a\n"
        "download that was damaged. Less often, the Microsoft Visual C++\n"
        "Redistributable is missing from this PC.",
        "Repair it",
    ),
}


class _RecoveryDialog(ctk.CTk, _DownloadMixin):
    """Offers repair, a different folder, or giving up.

    ``result`` is ``"repaired"``, a new folder path, or None.
    """

    def __init__(self, data_dir: str, reason: str) -> None:
        super().__init__()
        self.title(f"{APP_NAME} needs repairing")
        self.resizable(False, False)
        _centre(self, 560, 400)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.result: str | None = None
        self._data_dir = Path(data_dir)
        self._status = None
        self._bar = None
        self._detail = None

        heading, body, action = _REASON_TEXT.get(reason, _REASON_TEXT["missing"])
        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.pack(fill="both", expand=True, padx=30, pady=26)

        ctk.CTkLabel(self._body, text=heading, font=_TITLE_FONT, wraplength=500,
                     justify="left").pack(anchor="w")
        ctk.CTkLabel(
            self._body,
            justify="left",
            font=_BODY_FONT,
            wraplength=500,
            text=f"\nYour data folder:\n{data_dir}\n\n{body}\n",
        ).pack(anchor="w")

        if reason == "import_failed":
            ctk.CTkButton(
                self._body,
                text="Get the Visual C++ Redistributable",
                fg_color="gray30",
                height=30,
                command=lambda: webbrowser.open(_VC_REDIST_URL),
            ).pack(anchor="w", pady=(0, 6))

        row = ctk.CTkFrame(self._body, fg_color="transparent")
        row.pack(side="bottom", fill="x", pady=(10, 0))
        ctk.CTkButton(row, text="Quit", width=90, fg_color="gray30", command=self._close).pack(
            side="left"
        )
        ctk.CTkButton(
            row, text="Change folder", width=130, fg_color="gray30", command=self._change_folder
        ).pack(side="left", padx=(8, 0))
        self._action_button = ctk.CTkButton(row, text=action, width=130, command=self._repair)
        self._action_button.pack(side="right")
        _present(self, "repair dialog")

    def _repair(self) -> None:
        self._action_button.configure(state="disabled", text="Working…")
        self._bar = ctk.CTkProgressBar(self._body, height=14)
        self._bar.set(0)
        self._bar.pack(fill="x", pady=(14, 8))
        self._status = ctk.CTkLabel(self._body, text="Starting…", font=_SMALL_FONT, wraplength=480)
        self._status.pack(anchor="w")
        self._detail = ctk.CTkLabel(self._body, text="", font=_SMALL_FONT, text_color="gray60")
        self._detail.pack(anchor="w")
        self._start_download(self._data_dir / "pyside6", self._finished)

    def _finished(self, ok: bool, detail: str) -> None:
        if self._bar is not None:
            self._bar.stop()
            self._bar.configure(mode="determinate")
            self._bar.set(1.0 if ok else 0.0)
        if ok:
            self.result = str(self._data_dir)
            self.destroy()
            return
        self._status.configure(text=f"Repair failed.\n\n{detail}", text_color="#f0616d")
        self._action_button.configure(state="normal", text="Try again")

    def _change_folder(self) -> None:
        self.result = "__change__"
        self.destroy()

    def _close(self) -> None:
        self.result = None
        self.destroy()


def run_setup_wizard(default_parent: str) -> str | None:
    trace("building the setup wizard")
    wizard = _SetupWizard(default_parent)
    wizard.mainloop()
    return wizard.result


def run_recovery_dialog(data_dir: str, reason: str = "missing") -> str | None:
    """Loop until the user has a working folder or gives up."""
    while True:
        trace(f"building the repair dialog ({reason})")
        dialog = _RecoveryDialog(data_dir, reason=reason)
        dialog.mainloop()
        outcome = dialog.result

        if outcome is None:
            return None
        if outcome == "__change__":
            chosen = run_setup_wizard(str(Path(data_dir).parent))
            if chosen:
                return chosen
            # Cancelled the wizard: fall back to the repair choice again.
            continue
        return outcome
