"""A single writer window per local library, with acknowledged command handoff."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QLockFile, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

MAX_HANDOFF_BYTES = 1024 * 1024
HANDOFF_TIMEOUT_MS = 2000


class NotebookInstance(QObject):
    requested = Signal(list)

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.root = root.resolve()
        self.name = (
            "MarkNotes-" + hashlib.sha256(str(self.root).casefold().encode()).hexdigest()[:24]
        )
        self.lock = None
        self.server = None
        self._clients = set()

    @staticmethod
    def _validate_paths(paths):
        if not isinstance(paths, list) or not all(
            isinstance(path, str) and path and "\x00" not in path and Path(path).is_absolute()
            for path in paths
        ):
            raise ValueError("Invalid handoff: paths must be absolute file paths")

    def acquire(self, paths=()):
        """False means the existing window acknowledged receipt of this request."""
        paths = [str(Path(path).resolve()) for path in paths]
        self._validate_paths(paths)
        payload = json.dumps(paths).encode("utf-8") + b"\n"
        if len(payload) > MAX_HANDOFF_BYTES:
            raise ValueError("一度に引き継ぐファイル数が多すぎます。分けて開いてください。")
        socket = QLocalSocket()
        try:
            socket.connectToServer(self.name)
            if socket.waitForConnected(300):
                if socket.write(payload) != len(payload) or (
                    socket.bytesToWrite() and not socket.waitForBytesWritten(HANDOFF_TIMEOUT_MS)
                ):
                    raise OSError("起動中のMarkNotesへファイルを引き継げませんでした。")
                timeout = QElapsedTimer()
                timeout.start()
                while not socket.canReadLine():
                    remaining = HANDOFF_TIMEOUT_MS - timeout.elapsed()
                    if remaining <= 0 or not socket.waitForReadyRead(remaining):
                        raise OSError(
                            "起動中のMarkNotesが応答しません。しばらくしてから開いてください。"
                        )
                if bytes(socket.readLine(32)) != b"OK\n":
                    raise OSError("起動中のMarkNotesが引き継ぎを受け付けませんでした。")
                return False
        finally:
            socket.abort()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(self.root / "instance.lock"))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            raise OSError(
                "このライブラリは別のMarkNotesで使用中です。しばらくしてから開いてください。"
            )
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(self.name):
            self.lock.unlock()
            raise OSError(self.server.errorString())
        self.server.newConnection.connect(self._connect)
        return True

    def _connect(self):
        while self.server.hasPendingConnections():
            client = self.server.nextPendingConnection()
            self._clients.add(client)
            client.setReadBufferSize(MAX_HANDOFF_BYTES + 1)
            client.setProperty("requestData", b"")
            client.setProperty("requestHandled", False)
            client.readyRead.connect(lambda client=client: self._read(client))
            client.disconnected.connect(lambda client=client: self._release(client))
            # Incomplete requests must not keep a named-pipe connection forever.
            QTimer.singleShot(HANDOFF_TIMEOUT_MS * 2, client, client.abort)
            self._read(client)

    def _read(self, client):
        if client.property("requestHandled"):
            return
        previous = client.property("requestData")
        data = previous + bytes(client.read(MAX_HANDOFF_BYTES + 1 - len(previous)))
        if len(data) > MAX_HANDOFF_BYTES:
            client.abort()
            return
        client.setProperty("requestData", data)
        if b"\n" not in data:
            return
        client.setProperty("requestHandled", True)
        try:
            paths = json.loads(data.split(b"\n", 1)[0])
            self._validate_paths(paths)
        except (ValueError, UnicodeError):
            client.abort()
            return
        self.requested.emit(paths)
        client.write(b"OK\n")
        client.disconnectFromServer()

    def _release(self, client):
        self._clients.discard(client)
        client.deleteLater()

    def close(self):
        for client in list(self._clients):
            client.abort()
        if self.server:
            self.server.close()
        if self.lock:
            self.lock.unlock()
