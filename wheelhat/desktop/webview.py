"""The embedded control panel.

Three behaviours matter for this to feel like an application rather than a
browser in a costume:

* Off-origin links (the Twitch developer console, integration docs) open in the
  real browser, where the streamer is already signed in.
* ``target="_blank"`` links - the overlay previews - do the same, instead of
  silently doing nothing as they would by default.
* Downloads (Export everything) go through a native save dialog, because
  QtWebEngine otherwise discards them without a word.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFileDialog, QMessageBox

log = logging.getLogger("wheelhat.desktop.webview")


class ControlPanelPage(QWebEnginePage):
    """Keeps the window on WheelHat and sends everything else outside."""

    def __init__(self, profile, parent, base_url: str) -> None:
        super().__init__(profile, parent)
        self._base = QUrl(base_url)

    def _is_ours(self, url: QUrl) -> bool:
        return url.host() in {self._base.host(), "127.0.0.1", "localhost"} and (
            url.port() in (self._base.port(), -1)
        )

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:  # noqa: N802
        if is_main_frame and url.scheme() in {"http", "https"} and not self._is_ours(url):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def createWindow(self, _window_type):  # noqa: N802
        """A target=_blank link. Hand it to the system browser."""
        # Qt gives us no URL here, so route it through a throwaway page whose
        # navigation request carries the destination.
        opener = QWebEnginePage(self.profile(), self)

        def hand_off(url: QUrl) -> None:
            QDesktopServices.openUrl(url)
            opener.deleteLater()

        opener.urlChanged.connect(hand_off)
        return opener

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
        # These enum members are not ordered in PySide6, so match them exactly
        # rather than comparing with >=.
        levels = QWebEnginePage.JavaScriptConsoleMessageLevel
        if level in (levels.WarningMessageLevel, levels.ErrorMessageLevel):
            log.warning("control panel: %s (%s:%s)", message, source, line)


class ControlPanelView(QWebEngineView):
    loadFailed = Signal()

    def __init__(self, base_url: str, parent=None) -> None:
        super().__init__(parent)
        self._base_url = base_url

        page = ControlPanelPage(self.page().profile(), self, base_url)
        self.setPage(page)

        settings = self.settings()
        # The control panel copies overlay URLs to the clipboard; without this
        # the copy button silently does nothing.
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)

        page.profile().downloadRequested.connect(self._on_download)
        self.loadFinished.connect(self._on_load_finished)

    def load_panel(self) -> None:
        self.load(QUrl(self._base_url))

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self.loadFailed.emit()

    def _on_download(self, download: QWebEngineDownloadRequest) -> None:
        """Backups and wheel exports land wherever the streamer chooses."""
        suggested = download.downloadFileName() or "wheelhat-export.json"
        target, _ = QFileDialog.getSaveFileName(
            self, "Save file", str(Path.home() / suggested), "All files (*.*)"
        )
        if not target:
            download.cancel()
            return

        chosen = Path(target)
        download.setDownloadDirectory(str(chosen.parent))
        download.setDownloadFileName(chosen.name)
        download.accept()
        download.isFinishedChanged.connect(lambda: self._download_finished(download, chosen))

    def _download_finished(self, download: QWebEngineDownloadRequest, target: Path) -> None:
        if download.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            log.info("Saved %s", target)
        else:
            QMessageBox.warning(self, "WheelHat", f"Could not save {target.name}.")
