"""Asset listing, confinement, metadata consistency, and stale GUI requests."""

from types import SimpleNamespace

import pytest
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from marknotes.managed_assets import ManagedAssets
from marknotes.notebook_assets import (
    AssetEntry,
    AssetListing,
    NotebookAssetFiles,
    NotebookAssetsController,
    NotebookAssetsPanel,
    renamed_asset_markdown,
)
from marknotes.notebook_store import NotebookStore


@pytest.fixture
def library(tmp_path):
    store = NotebookStore(tmp_path / "library")
    note = store.create("# Note")
    manager = ManagedAssets(store.note_dir(note.id))
    asset = manager.save_bytes(b"attachment bytes", "pdf", "日本語 [資料].pdf", image=False)
    store.register_attachment(
        note.id, asset.relative_path, asset.original_name, "file", asset.size, asset.asset_id
    )
    return store, note, asset, NotebookAssetFiles(store, note.id, manager.base_dir)


def test_listing_includes_nested_and_unregistered_files_but_only_current_note(library):
    store, note, asset, service = library
    nested = service.base_dir / "assets" / "nested"
    nested.mkdir()
    (nested / "new.txt").write_text("new", encoding="utf-8")
    other = store.create()
    ManagedAssets(store.note_dir(other.id)).save_bytes(b"other", "txt", image=False)
    result = service.listing()
    assert len(result.entries) == 2
    assert {entry.relative_path for entry in result.entries} == {
        asset.relative_path,
        "assets/nested/new.txt",
    }
    assert (
        next(entry.name for entry in result.entries if entry.relative_path == asset.relative_path)
        == asset.original_name
    )
    assert str(nested) in result.directories
    assert store.get(note.id) == note


@pytest.mark.parametrize(
    "relative",
    [
        "../library.sqlite3",
        "assets/../file.txt",
        "assets/../../file.txt",
        "file:///test",
        "/other/file",
        "assets/missing.txt",
    ],
)
def test_file_actions_reject_escape_and_missing_paths(library, tmp_path, relative):
    _store, _note, _asset, service = library
    with pytest.raises(ValueError):
        service.delete(relative)
    with pytest.raises(ValueError):
        service.export(relative, tmp_path)


def test_rejects_another_note_base_directory(library):
    store, note, _asset, _service = library
    other = store.create()
    with pytest.raises(ValueError):
        NotebookAssetFiles(store, note.id, store.note_dir(other.id))


def test_listing_and_operations_reject_symlinks(library, tmp_path):
    _store, _note, _asset, service = library
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    link = service.base_dir / "assets" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks requires Windows developer mode")
    assert all("linked" not in entry.relative_path for entry in service.listing().entries)
    with pytest.raises(ValueError):
        service.delete("assets/linked/secret.txt")
    assert secret.read_text(encoding="utf-8") == "secret"


def test_delete_removes_only_selected_file_and_its_metadata(library):
    store, note, asset, service = library
    other = ManagedAssets(service.base_dir).save_bytes(b"keep", "txt", image=False)
    service.delete(asset.relative_path)
    assert not asset.path.exists()
    assert store.list_attachments(note.id) == []
    assert other.path.read_bytes() == b"keep"
    assert store.get(note.id) == note
    assert not list((service.base_dir / "staging").iterdir())


def test_delete_database_failure_restores_file(library, monkeypatch):
    store, note, asset, service = library

    def fail(*_args):
        raise OSError("simulated database failure")

    monkeypatch.setattr(store, "remove_attachment", fail)
    with pytest.raises(OSError, match="database failure"):
        service.delete(asset.relative_path)
    assert asset.path.read_bytes() == b"attachment bytes"
    assert len(store.list_attachments(note.id)) == 1
    assert not list((service.base_dir / "staging").iterdir())


def test_rename_preserves_undo_original_and_updates_only_real_references(library):
    store, note, asset, service = library
    renamed = service.rename(asset.relative_path, "renamed.pdf")
    body = f"[File]({asset.relative_path}?download#page=2)\n`[code]({asset.relative_path})`"
    text = renamed_asset_markdown(body, asset.path, renamed, service.base_dir)
    assert "[File](assets/renamed.pdf?download#page=2)" in text
    assert f"`[code]({asset.relative_path})`" in text
    assert asset.path.read_bytes() == renamed.path.read_bytes()
    assert len(store.list_attachments(note.id)) == 2
    assert store.get(note.id) == note
    with pytest.raises(FileExistsError):
        service.rename(asset.relative_path, "renamed.pdf")
    with pytest.raises(ValueError):
        service.rename(asset.relative_path, "renamed.exe")


def test_rename_database_failure_discards_only_new_copy(library, monkeypatch):
    store, _note, asset, service = library

    def fail(*_args):
        raise OSError("registration failed")

    monkeypatch.setattr(store, "register_attachment", fail)
    with pytest.raises(OSError, match="registration failed"):
        service.rename(asset.relative_path, "new.pdf")
    assert asset.path.exists()
    assert not (service.base_dir / "assets" / "new.pdf").exists()


def test_export_never_overwrites_and_disallows_copy_into_library(library, tmp_path):
    _store, _note, asset, service = library
    destination = tmp_path / "exports"
    destination.mkdir()
    target = service.export(asset.relative_path, destination)
    assert target.read_bytes() == asset.path.read_bytes()
    target.write_bytes(b"keep existing")
    with pytest.raises(FileExistsError):
        service.export(asset.relative_path, destination)
    assert target.read_bytes() == b"keep existing"
    with pytest.raises(ValueError):
        service.export(asset.relative_path, service.base_dir)


def test_toolbar_uses_theme_colored_outline_icons(qtbot):
    panel = NotebookAssetsPanel()
    qtbot.addWidget(panel)
    assert {key: button.icon().name() for key, button in panel.buttons.items()} == {
        "add": "new",
        "folder": "open",
        "refresh": "refresh",
        "menu": "menu",
    }
    light = panel.buttons["refresh"].icon().pixmap(18, 18).toImage()
    panel.apply_theme(True)
    assert panel.buttons["refresh"].icon().name() == "refresh"
    assert panel.buttons["refresh"].icon().pixmap(18, 18).toImage() != light


class DeferredWriter:
    def __init__(self):
        self.jobs = []

    def submit(self, work, callback):
        self.jobs.append((work, callback))

    def finish(self, index=0):
        work, callback = self.jobs.pop(index)
        try:
            value, error = work(), None
        except Exception as exc:  # noqa: BLE001 -- emulate background worker error delivery
            value, error = None, exc
        callback(value, error)


@pytest.fixture
def controller(qtbot, library):
    store, note, _asset, _service = library
    window = QMainWindow()
    qtbot.addWidget(window)
    window.store = store
    window.writer = DeferredWriter()
    window._active = note.id
    window._busy = window._closing = window._shutdown_done = False
    document = QTextDocument(window)
    document.setPlainText(note.body)
    window._sessions = {note.id: SimpleNamespace(document=document, pending_assets=0)}
    window.sync_image_watches = lambda: None
    window._drain_deferred_operation = lambda: None
    window.save_pending = lambda: None
    panel = NotebookAssetsPanel(window)
    control = NotebookAssetsController(window, panel)
    control.set_note(note.id, store.note_dir(note.id))
    window.writer.finish()
    yield control
    control.timer.stop()


def test_switching_note_discards_late_listing(controller, library):
    store, _note, _asset, _service = library
    controller.refresh()
    stale = controller.window.writer.jobs.pop()[1]
    other = store.create()
    controller.window._active = other.id
    controller.set_note(other.id, store.note_dir(other.id))
    controller.window.writer.finish()
    stale(AssetListing((AssetEntry("assets/stale.txt", "stale", 1),), ()), None)
    assert controller.panel.files.count() == 0
    controller.clear()
    assert not controller.panel.isEnabled()
    assert not controller.watcher.directories()


def test_copy_open_link_insert_and_context_selection(controller, library, monkeypatch):
    _store, _note, asset, _service = library
    panel = controller.panel
    panel.files.item(0).setSelected(True)
    entries = panel.selected_entries()
    opened, inserted = [], []
    monkeypatch.setattr(controller, "_open", opened.append)
    controller.window.insert_text = inserted.append
    controller.handle_action("open", entries)
    assert opened == [asset.path]
    controller.handle_action("copy", entries)
    assert QApplication.clipboard().mimeData().urls()[0].toLocalFile() == str(asset.path).replace(
        "\\", "/"
    )
    controller.handle_action("link", entries)
    assert QApplication.clipboard().text() == entries[0].markdown
    controller.handle_action("insert", entries)
    assert inserted == [entries[0].markdown]
    menu = panel.context_menu()
    assert all(action.isEnabled() for action in menu.actions() if action.data())
    menu.deleteLater()


def test_delete_confirmation_and_note_switch_reject_old_action(controller, library, monkeypatch):
    store, note, asset, _service = library
    entries = [controller.panel.files.item(0).data(256)]
    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.No)
    controller.handle_action("delete", entries)
    assert not controller.window.writer.jobs
    assert asset.path.exists()

    def switch(*_args):
        other = store.create()
        controller.window._active = other.id
        controller.set_note(other.id, store.note_dir(other.id))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", switch)
    controller.handle_action("delete", entries)
    assert asset.path.exists()
    assert store.list_attachments(note.id)


def test_delete_job_updates_files_and_pending_counter(controller, library, monkeypatch):
    _store, note, asset, _service = library
    entries = [controller.panel.files.item(0).data(256)]
    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)
    controller.handle_action("delete", entries)
    assert controller.window._sessions[note.id].pending_assets == 1
    assert not controller.panel.isEnabled()
    controller.window.writer.finish()
    assert controller.window._sessions[note.id].pending_assets == 0
    assert not asset.path.exists()
    controller.window.writer.finish()
    assert controller.panel.files.count() == 0


def test_delete_unlink_failure_restores_file_and_metadata(library, monkeypatch):
    store, note, asset, service = library
    original = store.list_attachments(note.id)
    path_type = type(asset.path)
    real_unlink = path_type.unlink

    def fail_staged(path, *args, **kwargs):
        if path.parent.name.startswith("delete-"):
            raise PermissionError("file is in use")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "unlink", fail_staged)
    with pytest.raises(PermissionError, match="in use"):
        service.delete(asset.relative_path)
    assert asset.path.read_bytes() == b"attachment bytes"
    assert store.list_attachments(note.id) == original
    assert not list((service.base_dir / "staging").iterdir())


def test_unchanged_listing_preserves_current_item_and_selection(controller):
    panel = controller.panel
    item = panel.files.item(0)
    panel.files.setCurrentItem(item)
    panel.set_entries((item.data(256),))
    assert panel.files.currentItem() is item
    assert item.isSelected()


@pytest.mark.parametrize("open_fails", [False, True])
def test_folder_open_waits_for_writer_without_locking_gui(
    controller, library, monkeypatch, open_fails
):
    store, note, _asset, service = library
    entered, opened = [], []
    original_lock = store.mutation_lock

    class TrackedLock:
        def __enter__(self):
            entered.append(True)
            return original_lock.__enter__()

        def __exit__(self, *args):
            return original_lock.__exit__(*args)

    def open_folder(path):
        opened.append(path)
        if open_fails:
            raise OSError("handler unavailable")

    monkeypatch.setattr(store, "mutation_lock", TrackedLock())
    monkeypatch.setattr(controller, "_open", open_folder)
    state = controller.window._sessions[note.id]
    state.pending_assets = 1  # A regular attachment import is still pending.
    controller.handle_action("folder", [])
    assert entered == []
    assert opened == []
    assert state.pending_assets == 2
    controller.window.writer.finish()
    assert entered == [True]
    assert opened == [service.base_dir / "assets"]
    assert state.pending_assets == 1
    if open_fails:
        assert "handler unavailable" in controller.window.statusBar().currentMessage()


def test_queued_folder_open_does_not_open_after_switching_note(controller, library, monkeypatch):
    store, _note, _asset, _service = library
    opened = []
    monkeypatch.setattr(controller, "_open", opened.append)
    controller.handle_action("folder", [])
    other = store.create()
    controller.window._active = other.id
    controller.set_note(other.id, store.note_dir(other.id))
    controller.window.writer.finish()
    assert opened == []
