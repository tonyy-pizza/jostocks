r"""jos/instance.py - one window, and a way to talk to it.

QLocalServer/QLocalSocket, which works on both platforms (a named pipe on
Windows, a unix socket elsewhere) and needs nothing installed. Two jobs:

  - A second launch does not open a second window. It hands its message to
    the running one and exits, so double-clicking the launcher twice raises
    the window that is already there.
  - The autorun can tell a running app that a refresh has finished, or ask it
    to run one, instead of starting a second copy. Section 9 requires both.

The stale-socket case is handled rather than ignored: if the app was killed,
the server name is still taken on unix and a fresh listen fails. Connecting
to it is the test - a name nobody answers is a leftover and is removed.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

#: Per user, so two accounts on one machine each get their own window.
SERVER_NAME = "jostocks-single-instance"

CONNECT_TIMEOUT_MS = 400
WRITE_TIMEOUT_MS = 1000


def send_to_running(message="show", name=SERVER_NAME) -> bool:
    """Hand a message to an already-running app. False if none is listening."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
        return False
    socket.write(str(message).encode("utf-8"))
    socket.flush()
    socket.waitForBytesWritten(WRITE_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


class InstanceServer(QObject):
    """Listens for messages from later launches and from the autorun."""

    message = pyqtSignal(str)

    def __init__(self, name=SERVER_NAME, parent=None):
        super().__init__(parent)
        self._name = name
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        """Start listening. Clears a leftover socket from a killed process."""
        if self._server.listen(self._name):
            return True
        # Nobody answered on that name (checked by the caller before us), so
        # it is a leftover rather than a live instance.
        QLocalServer.removeServer(self._name)
        return self._server.listen(self._name)

    def _on_connection(self):
        socket = self._server.nextPendingConnection()
        if socket is None:
            return

        def _read():
            payload = bytes(socket.readAll()).decode("utf-8", "replace").strip()
            if payload:
                self.message.emit(payload)
            socket.disconnectFromServer()

        socket.readyRead.connect(_read)
        socket.disconnected.connect(socket.deleteLater)

    def close(self):
        self._server.close()
