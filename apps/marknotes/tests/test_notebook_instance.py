"""Real local-socket and lock tests without creating a notebook window."""

import json
import os
import subprocess
import sys

import pytest
from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalSocket

import marknotes.notebook_instance as instance_module
from marknotes.notebook_instance import MAX_HANDOFF_BYTES, NotebookInstance


@pytest.fixture
def instances(qapp, tmp_path):
    created = []

    def create(name="library"):
        instance = NotebookInstance(tmp_path / name)
        created.append(instance)
        return instance

    yield create
    for instance in reversed(created):
        instance.close()
    qapp.processEvents()


def _handoff(qtbot, root, paths=()):
    # Use a real second process: PySide's blocking Windows socket calls may
    # retain the GIL, so two instances on Python threads do not model launch.
    code = """
import json, sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
import marknotes.notebook_instance as module
app = QCoreApplication([])
module.HANDOFF_TIMEOUT_MS = int(sys.argv[3])
instance = module.NotebookInstance(Path(sys.argv[1]))
try:
    result = instance.acquire(json.loads(sys.argv[2]))
    print(json.dumps({"result": result}))
except (OSError, ValueError) as exc:
    print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
finally:
    instance.close()
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(root),
            json.dumps([str(path) for path in paths]),
            str(instance_module.HANDOFF_TIMEOUT_MS),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        qtbot.waitUntil(lambda: process.poll() is not None, timeout=10000)
        output, errors = process.communicate(timeout=1)
        assert process.returncode == 0, errors
        result = json.loads(output)
        if result.get("error") == "ValueError":
            raise ValueError(result["message"])
        if "error" in result:
            raise OSError(result["message"])
        return result["result"]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def _connect(instance, qtbot):
    client = QLocalSocket()
    client.connectToServer(instance.name)
    assert client.waitForConnected(1000)
    qtbot.waitUntil(lambda: bool(instance._clients))
    return client


def _send(client, payload):
    assert client.write(payload) == len(payload)
    if client.bytesToWrite():
        assert client.waitForBytesWritten(1000)


def test_second_launch_hands_absolute_unicode_paths_to_first_window(instances, qtbot, tmp_path):
    first = instances()
    assert first.acquire()
    received = []
    first.requested.connect(received.append)
    paths = [tmp_path / "日本語 (1).md", tmp_path / "folder" / ".." / "other.md"]
    assert _handoff(qtbot, first.root, paths) is False
    assert received == [[str(path.resolve()) for path in paths]]
    assert first.lock.isLocked()
    assert first.server.isListening()


def test_empty_handoff_raises_existing_window_without_importing_a_file(instances, qtbot):
    first = instances()
    first.acquire()
    received = []
    first.requested.connect(received.append)
    assert _handoff(qtbot, first.root) is False
    assert received == [[]]


def test_separate_libraries_have_independent_locks_and_server_names(instances):
    first, second = instances("one"), instances("two")
    assert first.acquire()
    assert second.acquire()
    assert first.name != second.name
    assert first.lock.isLocked() and second.lock.isLocked()


def test_canonical_root_aliases_use_the_same_instance_name(instances):
    first = instances()
    alias = NotebookInstance(first.root / "child" / "..")
    assert first.name == alias.name
    assert first.root == alias.root


def test_lock_blocks_second_writer_even_when_server_is_not_ready(instances):
    instance = instances()
    instance.root.mkdir(parents=True)
    lock = QLockFile(str(instance.root / "instance.lock"))
    assert lock.tryLock(0)
    try:
        with pytest.raises(OSError, match="使用中"):
            instance.acquire()
        assert lock.isLocked()
    finally:
        lock.unlock()


def test_close_releases_library_for_next_process_and_is_idempotent(instances):
    first = instances()
    assert first.acquire()
    first.close()
    first.close()
    second = instances()
    assert second.acquire()
    assert second.lock.isLocked()


def test_fragmented_frame_is_emitted_only_after_complete_request(instances, qtbot, tmp_path):
    first = instances()
    first.acquire()
    received = []
    first.requested.connect(received.append)
    client = _connect(first, qtbot)
    payload = json.dumps([str(tmp_path / "memo.md")]).encode()
    _send(client, payload[:6])
    qtbot.waitUntil(lambda: any(peer.property("requestData") for peer in first._clients))
    assert received == []
    _send(client, payload[6:] + b"\n")
    qtbot.waitUntil(lambda: len(received) == 1)
    qtbot.waitUntil(lambda: client.bytesAvailable() > 0)
    assert bytes(client.readAll()) == b"OK\n"
    assert received == [[str(tmp_path / "memo.md")]]
    client.abort()


@pytest.mark.parametrize(
    "payload",
    [
        b"{}\n",
        b"null\n",
        b'"string"\n',
        b"[42]\n",
        b"[null]\n",
        b"[[]]\n",
        b'["relative.md"]\n',
        b'[""]\n',
        b'["C:\\\\path\\u0000.md"]\n',
        b"not JSON\n",
        b"\xff\xfe\n",
    ],
)
def test_malformed_and_nonlist_payloads_never_reach_import(instances, qtbot, payload):
    first = instances()
    first.acquire()
    received = []
    first.requested.connect(received.append)
    client = _connect(first, qtbot)
    _send(client, payload)
    qtbot.waitUntil(lambda: client.state() == QLocalSocket.LocalSocketState.UnconnectedState)
    assert received == []
    assert not first._clients
    client.abort()


def test_oversized_receive_is_rejected_before_json_decoding(instances, qtbot):
    first = instances()
    first.acquire()
    received = []
    first.requested.connect(received.append)
    client = _connect(first, qtbot)
    _send(client, b" " * (MAX_HANDOFF_BYTES + 1))
    qtbot.waitUntil(lambda: client.state() == QLocalSocket.LocalSocketState.UnconnectedState)
    assert received == []
    assert not first._clients


def test_oversized_outgoing_payload_fails_without_claiming_delivery(instances, qtbot, tmp_path):
    first = instances()
    first.acquire()
    received = []
    first.requested.connect(received.append)
    with pytest.raises(ValueError, match="多すぎ"):
        second = instances()
        second.acquire([tmp_path / ("a" * 200)] * 10000)
    assert received == []
    assert first.lock.isLocked()


def test_multiple_frames_cannot_duplicate_imports_on_one_connection(instances, qtbot):
    first = instances()
    first.acquire()
    received = []
    first.requested.connect(received.append)
    client = _connect(first, qtbot)
    _send(client, b"[]\n[]\n")
    qtbot.waitUntil(lambda: client.state() == QLocalSocket.LocalSocketState.UnconnectedState)
    assert received == [[]]


def test_disconnect_before_newline_discards_incomplete_request(instances, qtbot):
    first = instances()
    first.acquire()
    received = []
    first.requested.connect(received.append)
    client = _connect(first, qtbot)
    _send(client, b"[")
    qtbot.waitUntil(lambda: any(peer.property("requestData") for peer in first._clients))
    client.abort()
    qtbot.waitUntil(lambda: not first._clients)
    assert received == []


def test_unresponsive_existing_server_does_not_report_handoff_success(
    instances, qtbot, monkeypatch
):
    first = instances()
    first.acquire()
    monkeypatch.setattr(instance_module, "HANDOFF_TIMEOUT_MS", 150)
    monkeypatch.setattr(first, "_read", lambda client: None)
    with pytest.raises(OSError, match="応答"):
        _handoff(qtbot, first.root)
    assert first.lock.isLocked()


def test_write_failure_is_reported_instead_of_silently_losing_handoff(instances, monkeypatch):
    class FailedSocket:
        def connectToServer(self, name):
            pass

        def waitForConnected(self, timeout):
            return True

        def write(self, payload):
            return -1

        def abort(self):
            pass

    monkeypatch.setattr(instance_module, "QLocalSocket", FailedSocket)
    instance = instances()
    with pytest.raises(OSError, match="引き継げません"):
        instance.acquire()
    assert instance.lock is None


def test_listener_failure_releases_acquired_library_lock(instances, monkeypatch):
    original = instance_module.QLocalServer

    class FailedServer:
        SocketOption = original.SocketOption
        removeServer = staticmethod(original.removeServer)

        def __init__(self, parent):
            pass

        def setSocketOptions(self, options):
            pass

        def listen(self, name):
            return False

        def errorString(self):
            return "simulated listener error"

        def close(self):
            pass

    instance = instances()
    monkeypatch.setattr(instance_module, "QLocalServer", FailedServer)
    with pytest.raises(OSError, match="simulated listener error"):
        instance.acquire()
    assert not instance.lock.isLocked()
