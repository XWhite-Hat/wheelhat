"""Main window, menus and tray icon."""

from __future__ import annotations

import logging
import webbrowser

from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, config
from .server import ServerThread
from .webview import ControlPanelView

log = logging.getLogger("wheelhat.desktop.window")


def app_icon() -> QIcon:
    for name in ("icon.ico", "icon.png", "favicon.svg"):
        candidate = config.WEB_DIR / "static" / "img" / name
        if candidate.exists():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon
    return QIcon()


class MainWindow(QMainWindow):
    def __init__(self, server: ServerThread) -> None:
        super().__init__()
        self.server = server
        self.settings = QSettings("WheelHat", "WheelHat")
        self._really_quitting = False
        self._warned_about_tray = bool(self.settings.value("warned_about_tray", False))

        self.setWindowTitle("WheelHat")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(980, 640)

        self.view = ControlPanelView(server.base_url, self)
        self.view.loadFailed.connect(self._on_load_failed)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.setCentralWidget(container)

        self._build_menus()
        self._build_tray()
        self._restore_geometry()

        self.statusBar().showMessage(f"Serving on {server.base_url}")
        self.view.load_panel()

    # ------------------------------------------------------------------ menus

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_browser = QAction("Open in &browser", self)
        open_browser.setStatusTip("Open the control panel in your normal web browser")
        open_browser.triggered.connect(lambda: webbrowser.open(self.server.base_url))
        file_menu.addAction(open_browser)

        data_folder = QAction("Open &data folder", self)
        data_folder.setStatusTip("Wheels, tokens and uploaded images live here")
        data_folder.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.DATA_DIR)))
        )
        file_menu.addAction(data_folder)

        assets_folder = QAction("Open &assets folder", self)
        assets_folder.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.ASSETS_DIR)))
        )
        file_menu.addAction(assets_folder)

        file_menu.addSeparator()
        hide = QAction("&Hide to tray", self)
        hide.setShortcut(QKeySequence("Ctrl+W"))
        hide.triggered.connect(self.hide)
        file_menu.addAction(hide)

        quit_action = QAction("&Quit WheelHat", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.quit_application)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")
        reload_action = QAction("&Reload", self)
        reload_action.setShortcut(QKeySequence.StandardKey.Refresh)
        reload_action.triggered.connect(self.view.reload)
        view_menu.addAction(reload_action)

        for label, shortcut, delta in (
            ("Zoom &in", "Ctrl++", 0.1),
            ("Zoom &out", "Ctrl+-", -0.1),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _=False, d=delta: self._zoom(d))
            view_menu.addAction(action)

        reset_zoom = QAction("&Reset zoom", self)
        reset_zoom.setShortcut(QKeySequence("Ctrl+0"))
        reset_zoom.triggered.connect(lambda: self._set_zoom(1.0))
        view_menu.addAction(reset_zoom)

        help_menu = self.menuBar().addMenu("&Help")
        copy_url = QAction("&Copy server address", self)
        copy_url.setStatusTip("The address OBS browser sources connect to")
        copy_url.triggered.connect(self._copy_base_url)
        help_menu.addAction(copy_url)

        open_log = QAction("Open the &log file", self)
        open_log.setStatusTip("Useful when reporting a problem")
        open_log.triggered.connect(self._open_log)
        help_menu.addAction(open_log)

        about = QAction("&About WheelHat", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _zoom(self, delta: float) -> None:
        self._set_zoom(max(0.5, min(2.5, self.view.zoomFactor() + delta)))

    def _set_zoom(self, factor: float) -> None:
        self.view.setZoomFactor(factor)
        self.settings.setValue("zoom", factor)

    def _copy_base_url(self) -> None:
        QGuiApplication.clipboard().setText(self.server.base_url)
        self.statusBar().showMessage(f"Copied {self.server.base_url}", 4000)

    def _open_log(self) -> None:
        from .logs import log_path

        target = log_path()
        if not target.exists():
            self.statusBar().showMessage("Nothing has been logged yet.", 4000)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About WheelHat",
            f"<h3>WheelHat {__version__}</h3>"
            "<p>Spinner wheels for Twitch streamers.</p>"
            f"<p>Serving on <code>{self.server.base_url}</code><br>"
            f"Data folder: <code>{config.DATA_DIR}</code></p>"
            "<p>MIT licensed. Qt for Python (PySide6) is used under the LGPL.</p>",
        )

    # ------------------------------------------------------------------- tray

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(f"WheelHat - {self.server.base_url}")

        menu = QMenu()
        show = QAction("Show WheelHat", self)
        show.triggered.connect(self.show_and_raise)
        menu.addAction(show)

        browser = QAction("Open in browser", self)
        browser.triggered.connect(lambda: webbrowser.open(self.server.base_url))
        menu.addAction(browser)

        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_application)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_and_raise()

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # -------------------------------------------------------------- lifecycle

    def _restore_geometry(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1280, 820)
        zoom = self.settings.value("zoom")
        if zoom:
            self.view.setZoomFactor(float(zoom))

    def _on_load_failed(self) -> None:
        self.statusBar().showMessage("Could not load the control panel - retrying…", 5000)
        self.view.load_panel()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Closing the window keeps WheelHat running in the tray.

        Triggers have to keep firing mid-stream, so an accidental close must not
        take the wheels offline. Quitting explicitly is what actually stops it.
        """
        if self._really_quitting or not self.tray.isVisible():
            self._save_state()
            event.accept()
            return

        event.ignore()
        self.hide()
        if not self._warned_about_tray:
            self._warned_about_tray = True
            self.settings.setValue("warned_about_tray", True)
            self.tray.showMessage(
                "WheelHat is still running",
                "Your wheels stay live and triggers keep firing. "
                "Quit from the tray icon to stop.",
                app_icon(),
                6000,
            )

    def _save_state(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())

    def quit_application(self) -> None:
        self._really_quitting = True
        self._save_state()
        self.tray.hide()
        QApplication.instance().quit()
