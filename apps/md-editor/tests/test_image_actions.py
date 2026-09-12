"""Real image rename dialogs, native Undo, editor launching and file-watch refresh."""

import os

import pytest
from PySide6.QtCore import QProcess, QSettings
from PySide6.QtGui import QColor, QImage, QTextCursor
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from md_editor.app import MainWindow
from md_editor.document import DocumentSession
from md_editor.image_actions import ImageRenameDialog
from md_editor.image_rename import current_images


def png(path, width=20, color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, 12, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG")
    return path


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "image-tests.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    window.session._recovery_root_override = tmp_path / "recovery"
    qtbot.addWidget(
        window, before_close_func=lambda widget: widget.editor.document().setModified(False)
    )
    window.resize(1100, 750)
    window.show()
    window.set_source("", window.session.base_dir, update_session=False)

    def unexpected(*args):
        pytest.fail(f"Unexpected dialog: {args[1:3]}")

    monkeypatch.setattr(QMessageBox, "question", unexpected)
    monkeypatch.setattr(QMessageBox, "warning", unexpected)
    return window


def javascript(qtbot, window, source):
    values = []
    window.preview.page().runJavaScript(source, values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=10000)
    return values[0]


def width(qtbot, window):
    return javascript(qtbot, window, "document.images[0]?.naturalWidth || 0")


def rendered(qtbot, window):
    qtbot.waitUntil(lambda: window._revision == window._rendered_revision, timeout=15000)
    qtbot.waitUntil(
        lambda: bool(
            javascript(
                qtbot,
                window,
                "document.images.length && [...document.images].every(i => i.complete && i.naturalWidth)",
            )
        ),
        timeout=10000,
    )


def open_image(window, tmp_path):
    image = png(tmp_path / "img" / "old.png")
    source = "# title\n\n![caption](img/old.png)\n"
    document = tmp_path / "note.md"
    document.write_text(source, "utf-8")
    assert window.open_path(document)
    return image, source


def approve_rename(monkeypatch, name="new.png"):
    def accept(dialog):
        dialog.table.item(0, 1).setText(name)
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ImageRenameDialog, "exec", accept)


def test_individual_rename_is_one_native_undo_and_redo(window, qtbot, tmp_path, monkeypatch):
    old, original = open_image(window, tmp_path)
    rendered(qtbot, window)
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)
    window.insert_text("edited text")
    source = window.editor.toPlainText()
    approve_rename(monkeypatch)
    assert window.rename_current_image(current_images(window.session, source)[0])
    result = window.editor.toPlainText()
    assert "img/new.png" in result and result.endswith("edited text")
    assert not window._has_unsaved_changes()
    assert window.path.read_text("utf-8") == result
    rendered(qtbot, window)
    assert width(qtbot, window) == 20
    window.editor.undo()
    assert window.editor.toPlainText() == source
    assert window._has_unsaved_changes()
    rendered(qtbot, window)
    assert width(qtbot, window) == 20
    window.editor.redo()
    assert window.editor.toPlainText() == result
    rendered(qtbot, window)
    assert width(qtbot, window) == 20
    window.editor.undo()
    window.editor.undo()
    assert window.editor.toPlainText() == original
    window.editor.redo()
    assert window.editor.toPlainText() == source
    assert old.exists() and old.with_name("new.png").exists()


def test_bulk_cancel_and_collision_have_no_side_effects(window, tmp_path, monkeypatch):
    old, source = open_image(window, tmp_path)
    assert current_images(window.session, source)
    taken = png(old.with_name("taken.png"), 35)

    def cancel(dialog):
        dialog.table.item(0, 1).setText(taken.name)
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        assert "既存" in dialog.message.text()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(ImageRenameDialog, "exec", cancel)
    assert not window.rename_images_bulk()
    assert window.editor.toPlainText() == source
    assert window.path.read_text("utf-8") == source
    assert len(list(old.parent.iterdir())) == 2


def test_unsaved_rename_remains_dirty_and_first_save_promotes(window, qtbot, tmp_path, monkeypatch):
    image = QImage(22, 14, QImage.Format.Format_RGB32)
    image.fill(QColor("blue"))
    source = window.session.add_image(image)
    window.insert_text(source)
    approve_rename(monkeypatch)
    assert window.rename_images_bulk()
    assert window.path is None and window._has_unsaved_changes()
    renamed = window.editor.toPlainText()
    assert "img/new.png" in renamed
    window.editor.undo()
    assert window.editor.toPlainText() == source
    rendered(qtbot, window)
    assert width(qtbot, window) == 22
    window.editor.redo()
    assert window.editor.toPlainText() == renamed
    from PySide6.QtWidgets import QFileDialog

    target = tmp_path / "saved" / "document.md"
    target.parent.mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    assert window.save_document()
    assert "img/document_00001.png" in window.editor.toPlainText()
    assert (target.parent / "img" / "document_00001.png").is_file()
    rendered(qtbot, window)
    assert width(qtbot, window) == 22


def test_external_editor_receives_one_path_argument_and_svg_is_disabled(
    window, tmp_path, monkeypatch
):
    old, source = open_image(window, tmp_path)
    executable = "C:/Program Files/Example Editor/editor.exe"
    window.settings.setValue("image_editor", executable)
    calls = []
    monkeypatch.setattr(
        QProcess,
        "startDetached",
        lambda program, args: calls.append((program, args)) or (True, 123),
    )
    assert window.edit_image(current_images(window.session, source)[0])
    assert calls == [(executable, [str(old)])]
    assert not window._has_unsaved_changes()
    vector = old.with_name("vector.svg")
    vector.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>', "utf-8"
    )
    window.set_source("![v](img/vector.svg)", tmp_path)
    selected = current_images(window.session, window.editor.toPlainText())[0]
    assert not selected.raster
    assert not window.edit_image(selected)
    assert len(calls) == 1


def test_external_atomic_image_replacement_refreshes_without_text_change(window, qtbot, tmp_path):
    old, source = open_image(window, tmp_path)
    rendered(qtbot, window)
    assert width(qtbot, window) == 20
    assert str(old) in window._image_watcher.files()
    replacement = png(tmp_path / "replacement.png", 48, "blue")
    generation = window._image_generation
    os.replace(replacement, old)
    qtbot.waitUntil(lambda: window._image_generation > generation, timeout=10000)
    rendered(qtbot, window)
    qtbot.waitUntil(lambda: width(qtbot, window) == 48, timeout=10000)
    assert str(old) in window._image_watcher.files()
    assert window.editor.toPlainText() == source
    assert window.path.read_text("utf-8") == source
    assert not window._has_unsaved_changes()
    assert "_mdedit=" in window.image_preview_source(source)
    assert "_mdedit=" not in window.session.normalize_references(source)


def test_image_delete_then_recreate_remains_watched(window, qtbot, tmp_path):
    old, source = open_image(window, tmp_path)
    rendered(qtbot, window)
    generation = window._image_generation
    old.unlink()
    qtbot.waitUntil(lambda: window._image_generation > generation, timeout=10000)
    assert str(old.parent) in window._image_watcher.directories()
    generation = window._image_generation
    png(old, 60, "yellow")
    qtbot.waitUntil(lambda: window._image_generation > generation, timeout=10000)
    rendered(qtbot, window)
    qtbot.waitUntil(lambda: width(qtbot, window) == 60, timeout=10000)
    assert window.editor.toPlainText() == source
    assert not window._has_unsaved_changes()


def test_bulk_dialog_recalculates_preview_from_prefix_and_numbering(tmp_path, qtbot):
    old = png(tmp_path / "img" / "old.png")
    source = "![image](img/old.png)"
    document = tmp_path / "note.md"
    document.write_text(source, "utf-8")
    session = DocumentSession(recovery_root=tmp_path / "recovery")
    session.open(document)
    taken = png(old.with_name("図-0010.png"), 35)
    dialog = ImageRenameDialog(session, source)
    qtbot.addWidget(dialog)
    assert dialog.prefix.text() == "note_"
    assert dialog.start_number.value() == 1
    assert dialog.digits.value() == 5
    dialog.prefix.setText("図-")
    dialog.start_number.setValue(10)
    dialog.digits.setValue(4)
    assert dialog.table.item(0, 1).text() == "図-0011.png"
    assert dialog.plan.entries[0].target.name == "図-0011.png"
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert document.read_text("utf-8") == source
    assert not old.with_name("図-0011.png").exists()
    dialog.prefix.setText("../escape_")
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    # Editing an old preview row cannot accidentally confirm invalid options.
    dialog.table.item(0, 1).setText("manual.png")
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.prefix.setText("figure-")
    assert dialog.table.item(0, 1).text() == "figure-0010.png"
    dialog.table.item(0, 1).setText("manual.png")
    assert dialog.plan.entries[0].target.name == "manual.png"
    dialog.reject()
    assert old.exists() and taken.exists()
    assert len(list(old.parent.iterdir())) == 2
    session.close()


def test_custom_bulk_rename_saves_references_and_preserves_native_undo(
    window, tmp_path, qtbot, monkeypatch
):
    old, source = open_image(window, tmp_path)
    rendered(qtbot, window)
    shared = tmp_path / "shared.md"
    shared.write_text(source, "utf-8")

    def accept(dialog):
        dialog.prefix.setText("figure-")
        dialog.start_number.setValue(7)
        dialog.digits.setValue(3)
        assert dialog.table.item(0, 1).text() == "figure-007.png"
        assert "shared.md" in dialog.table.item(0, 2).text()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ImageRenameDialog, "exec", accept)
    assert window.rename_images_bulk()
    renamed = source.replace("img/old.png", "img/figure-007.png")
    assert window.editor.toPlainText() == renamed
    assert window.path.read_text("utf-8") == renamed
    assert old.with_name("figure-007.png").read_bytes() == old.read_bytes()
    assert not window._has_unsaved_changes()
    window.editor.undo()
    assert window.editor.toPlainText() == source
    rendered(qtbot, window)
    assert width(qtbot, window) == 20
    window.editor.redo()
    assert window.editor.toPlainText() == renamed
    rendered(qtbot, window)
    assert width(qtbot, window) == 20
    assert shared.read_text("utf-8") == source
