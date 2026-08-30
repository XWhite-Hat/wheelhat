"""Boots the desktop shell: single-instance guard, server, window."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import config
from .logs import configure as configure_logging
from .server import ServerThread, find_free_port

log = logging.getLogger("wheelhat.desktop")

#: Named pipe / unix socket used to spot an instance that is already running.
INSTANCE_KEY = "wheelhat-single-instance"


def _existing_instance_raised() -> bool:
    """Ask a running WheelHat to show itself. True if one answered."""
    probe = QLocalSocket()
    probe.connectToServer(INSTANCE_KEY)
    if not probe.waitForConnected(300):
        return False
    probe.write(b"show")
    probe.waitForBytesWritten(300)
    probe.disconnectFromServer()
    return True


def _claim_instance(window) -> QLocalServer:
    """Listen for later launches so they raise this window instead of starting
    a second copy - two servers would fight over the port and the database."""
    QLocalServer.removeServer(INSTANCE_KEY)
    server = QLocalServer()
    server.listen(INSTANCE_KEY)

    def on_connection() -> None:
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.readyRead.connect(window.show_and_raise)
            connection.disconnected.connect(connection.deleteLater)
        window.show_and_raise()

    server.newConnection.connect(on_connection)
    return server


def run_desktop(host: str = "", port: int = 0, log_level: str = "info") -> int:
    """Run WheelHat as a windowed application. Returns a process exit code."""
    host = host or config.HOST
    preferred = port or config.PORT
    # Without a console there is nowhere for a message to go, so keep a file.
    configure_logging(log_level)

    # Must be set before the QApplication exists or QtWebEngine may not share
    # its GL context with the widget hierarchy.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("WheelHat")
    qt_app.setOrganizationName("WheelHat")
    qt_app.setApplicationDisplayName("WheelHat")
    # Closing the last window hides to tray; the app quits explicitly.
    qt_app.setQuitOnLastWindowClosed(False)

    if _existing_instance_raised():
        log.info("WheelHat is already running; raised the existing window")
        return 0

    chosen = find_free_port(preferred, host if host != "0.0.0.0" else "127.0.0.1")
    if chosen != preferred:
        log.warning("Port %s was busy, using %s instead", preferred, chosen)
    # Overlay URLs are built from these, so set them before the server starts.
    config.HOST, config.PORT = host, chosen

    server = ServerThread(host=host, port=chosen, log_level=log_level)
    server.start()

    if not server.wait_until_ready():
        detail = str(server.error) if server.error else "The server did not start in time."
        QMessageBox.critical(
            None,
            "WheelHat could not start",
            f"The WheelHat server failed to start.\n\n{detail}",
        )
        server.stop()
        return 1

    from .window import MainWindow

    window = MainWindow(server)
    instance_guard = _claim_instance(window)
    window.show()

    if chosen != preferred:
        window.statusBar().showMessage(
            f"Port {preferred} was busy - serving on {chosen} instead. "
            "Update your OBS browser sources.",
            15000,
        )

    try:
        code = qt_app.exec()
    finally:
        instance_guard.close()
        QLocalServer.removeServer(INSTANCE_KEY)
        # Let uvicorn unwind its lifespan so OBS/VTS connections close tidily.
        server.stop()
    return int(code)
