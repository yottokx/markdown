"""Real-window saves, cancellation, formats, themes and image Undo history."""

import codecs

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage, QPalette, QTextCursor
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

from marknotes.app import MainWindow
from marknotes.file_actions import FormatDialog
from marknotes.search import qt_position


def cleanup(window):
    window.editor.document().setModified(False)
    window._pending_encoding = window._pending_newline = None


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "ui-test.ini"), QSettings.Format.IniFormat)
    widget = MainWindow(settings)
    qtbot.addWidget(widget, before_close_func=cleanup)
    widget.resize(1180, 760)
    widget.show()
    widget.set_source("", widget.session.base_dir, update_session=False)

    def unexpected(*args):
        pytest.fail(f"Unexpected dialog: {args[1:3]}")

    monkeypatch.setattr(QMessageBox, "question", unexpected)
    monkeypatch.setattr(QMessageBox, "warning", unexpected)
    return widget


def rendered(qtbot, window):
    qtbot.waitUntil(lambda: window._revision == window._rendered_revision, timeout=15000)
    # Preview ready means first measured layout. Local image loading is async.
    qtbot.waitUntil(
        lambda: bool(
            javascript(qtbot, window, "Array.from(document.images).every(image => image.complete)")
        ),
        timeout=10000,
    )


def javascript(qtbot, window, source):
    values = []
    window.preview.page().runJavaScript(source, values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=10000)
    return values[0]


def image():
    result = QImage(32, 21, QImage.Format.Format_RGB32)
    result.fill(QColor("#3b78a1"))
    return result


@pytest.mark.parametrize(
    "encoding,bom,newline",
    [
        ("utf-8", b"", "\n"),
        ("utf-8", codecs.BOM_UTF8, "\r\n"),
        ("utf-16-le", codecs.BOM_UTF16_LE, "\r\n"),
        ("cp932", b"", "\r"),
    ],
)
def test_open_edit_save_preserves_format_and_eof(window, tmp_path, encoding, bom, newline):
    path = tmp_path / "日本語 文書.md"
    original = "# 見出し\n\n日本語の本文です。\n最終行"
    path.write_bytes(bom + original.replace("\n", newline).encode(encoding))
    assert window.open_path(path)
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)
    window.insert_text("追加")
    assert window._has_unsaved_changes()
    assert window.save_document()
    assert path.read_bytes() == bom + (original + "追加").replace("\n", newline).encode(encoding)
    assert not window._has_unsaved_changes()
    window.editor.undo()
    assert window.editor.toPlainText() == original
    assert window._has_unsaved_changes()


def test_failed_open_unknown_codec_preserves_dirty_document(window, tmp_path, monkeypatch):
    window.insert_text("keep 😀")
    path = tmp_path / "test.md"
    path.write_text("new", encoding="utf-8")
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))
    assert not window.open_path(path, encoding="not-a-codec")
    assert window.editor.toPlainText() == "keep 😀"
    assert window.path is None
    assert window._has_unsaved_changes()
    assert messages


def test_save_as_cancel_preserves_temporary_images_and_source(window, monkeypatch):
    window.insert_text(window.session.add_image(image()))
    before = window.editor.toPlainText()
    base = window.base_dir
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    assert not window.save_document()
    assert window.editor.toPlainText() == before
    assert window.base_dir == base and window.path is None
    assert len(list((base / "img").glob("*.png"))) == 1
    window.editor.undo()
    assert window.editor.toPlainText() == ""


def test_initial_image_save_preserves_cursor_scroll_and_undo(qtbot, window, tmp_path):
    source = "😀 prefix\n" + "paragraph\n\n" * 35
    window.set_source(source, window.session.base_dir, update_session=False)
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)
    window.insert_text(window.session.add_image(image()))
    old = window.editor.toPlainText()
    cursor.setPosition(qt_position(old, 4))
    window.editor.setTextCursor(cursor)
    window.editor.scroll_to_source(12)
    assert window.save_document(tmp_path / "保存した文書.md")
    assert window.editor.textCursor().position() == qt_position(old, 4)
    assert abs(window.editor.source_position() - 12) < 1
    assert "untitled" not in window.editor.toPlainText()
    assert list((tmp_path / "img").glob("保存した文書_*.png"))
    rendered(qtbot, window)
    assert javascript(qtbot, window, "document.images[0].naturalWidth") == 32
    window.editor.undo()
    assert window.editor.toPlainText() == old
    rendered(qtbot, window)
    assert javascript(qtbot, window, "document.images[0].naturalWidth") == 32
    window.editor.redo()
    assert "untitled" not in window.editor.toPlainText()


def test_undone_image_redo_after_first_save_becomes_permanent(qtbot, window, tmp_path):
    temporary = window.base_dir
    window.insert_text(window.session.add_image(image()))
    old = window.editor.toPlainText()
    window.editor.undo()
    assert window.save_document(tmp_path / "restored.md")
    assert not (tmp_path / "img").exists()
    window.editor.redo()
    assert window.editor.toPlainText() == old
    rendered(qtbot, window)
    assert javascript(qtbot, window, "document.images[0].naturalWidth") == 32
    assert window.save_document()
    assert "restored_00001.png" in window.editor.toPlainText()
    assert (tmp_path / "img" / "restored_00001.png").exists()
    assert window.new_document()
    assert not temporary.exists()
    assert window.open_path(tmp_path / "restored.md")
    rendered(qtbot, window)
    assert javascript(qtbot, window, "document.images[0].naturalWidth") == 32


def test_pending_format_remains_dirty_after_text_undo(window, tmp_path, monkeypatch):
    assert window.save_document(tmp_path / "format.md")

    def choose(dialog):
        dialog.newline.setCurrentText("CRLF")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormatDialog, "exec", choose)
    window.change_format()
    window.insert_text("x")
    window.editor.undo()
    assert not window.editor.document().isModified()
    assert window._has_unsaved_changes()
    assert "*" in window.windowTitle()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    assert not window.new_document()
    assert window._pending_newline == "CRLF"
    assert window.save_document()
    assert window.session.newline == "CRLF"
    assert not window._has_unsaved_changes()


def test_mixed_newline_save_cancel_and_explicit_choice(window, tmp_path, monkeypatch):
    path = tmp_path / "mixed.md"
    original = b"one\r\ntwo\nthree\r"
    path.write_bytes(original)
    assert window.open_path(path)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("", False))
    assert not window.save_document()
    assert path.read_bytes() == original
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("CRLF", True))
    assert window.save_document()
    assert path.read_bytes() == b"one\r\ntwo\r\nthree\r\n"


def test_external_change_clean_reload_dirty_save_cancel(window, tmp_path, monkeypatch):
    path = tmp_path / "external.md"
    path.write_text("original", encoding="utf-8")
    window.open_path(path)
    path.write_text("external clean", encoding="utf-8")
    window._external_file_changed()
    assert window.editor.toPlainText() == "external clean"
    window.insert_text("local ")
    local = window.editor.toPlainText()
    path.write_text("external dirty", encoding="utf-8")
    window._external_file_changed()
    assert window.editor.toPlainText() == local
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    assert not window.save_document()
    assert path.read_text(encoding="utf-8") == "external dirty"
    assert window.editor.toPlainText() == local


def test_unencodable_text_cancel_then_utf8_is_lossless(window, tmp_path, monkeypatch):
    path = tmp_path / "legacy.md"
    path.write_bytes("日本語".encode("cp932"))
    window.open_path(path, encoding="cp932")
    window.insert_text("😀")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    assert not window.save_document()
    assert path.read_bytes() == "日本語".encode("cp932")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    assert window.save_document()
    assert path.read_text(encoding="utf-8") == window.editor.toPlainText()
    assert window.session.encoding == "utf-8"


def test_theme_does_not_modify_document_or_scroll_and_is_persisted(qtbot, window):
    window.set_source(
        "# Theme\n\n" + "text **bold**\n\n" * 80, window.base_dir, update_session=False
    )
    window.insert_text("😀 ")
    source = window.editor.toPlainText()
    window.editor.document().setModified(False)
    rendered(qtbot, window)
    window.editor.scroll_to_source(45)
    qtbot.wait(100)
    window.apply_theme("dark")
    qtbot.wait(100)
    assert javascript(qtbot, window, "document.documentElement.dataset.theme") == "dark"
    assert window.palette().color(QPalette.ColorRole.Base).lightness() < 100
    assert window.search.query.palette().color(QPalette.ColorRole.Base).lightness() < 100
    assert window.settings.value("theme") == "dark"
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isModified()
    assert abs(window.editor.source_position() - 45) < 1
    assert abs(javascript(qtbot, window, "window.previewApi.metrics().source") - 45) < 1
    dialog = FormatDialog(window.session, window)
    qtbot.addWidget(dialog)
    assert dialog.palette().color(QPalette.ColorRole.Base).lightness() < 100
    window.apply_theme("light")
    assert window.palette().color(QPalette.ColorRole.Base).lightness() > 230
    window.apply_theme("system")
    assert window.theme_actions["system"].isChecked()
    window.editor.undo()
    assert window.editor.toPlainText() != source


def loaded_image_width(qtbot, window):
    qtbot.waitUntil(
        lambda: bool(
            javascript(qtbot, window, "document.images.length > 0 && document.images[0].complete")
        ),
        timeout=10000,
    )
    return javascript(qtbot, window, "document.images[0].naturalWidth")


def test_identical_source_new_paste_and_native_undo_keep_distinct_image_origins(
    qtbot, window, tmp_path
):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (new / "img").mkdir()
    other = QImage(17, 9, QImage.Format.Format_RGB32)
    other.fill(QColor("red"))
    assert other.save(str(new / "img/doc_00001.png"))
    assert window.save_document(old / "doc.md")
    window.insert_text(window.session.add_image(image()))
    historical = window.editor.toPlainText()
    assert window.save_document()
    assert window.save_document(new / "doc.md")
    canonical = window.editor.toPlainText()
    assert "doc_00002.png" in canonical
    # New whole-document paste matches the old source byte-for-byte. It must
    # resolve to B's pre-existing image, rather than masquerading as Undo.
    window.editor.selectAll()
    window.insert_text(historical)
    rendered(qtbot, window)
    assert window.session.normalize_references(historical) == historical
    assert loaded_image_width(qtbot, window) == 17
    window.editor.undo()
    assert window.editor.toPlainText() == canonical
    rendered(qtbot, window)
    assert loaded_image_width(qtbot, window) == 32
    window.editor.undo()
    assert window.editor.toPlainText() == historical
    rendered(qtbot, window)
    assert loaded_image_width(qtbot, window) == 32
    window.editor.redo()
    window.editor.redo()
    assert window.editor.toPlainText() == historical
    rendered(qtbot, window)
    assert loaded_image_width(qtbot, window) == 17
    assert window.save_document()
    assert window.editor.toPlainText() == historical


def test_new_pasted_image_reusing_old_filename_after_empty_save_as_is_new_asset(
    qtbot, window, tmp_path
):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    assert window.save_document(old / "doc.md")
    window.insert_text(window.session.add_image(image()))
    historical = window.editor.toPlainText()
    window.editor.undo()
    assert window.save_document(new / "doc.md")
    fresh = QImage(19, 11, QImage.Format.Format_RGB32)
    fresh.fill(QColor("green"))
    window.insert_text(window.session.add_image(fresh))
    assert window.editor.toPlainText() == historical
    rendered(qtbot, window)
    assert loaded_image_width(qtbot, window) == 19
    assert window.save_document()
    assert QImage(str(new / "img/doc_00001.png")).width() == 19
    assert not (new / "img/doc_00002.png").exists()


def test_cross_drive_redo_temporary_image_preview_and_permanent_save(
    qtbot, window, tmp_path, monkeypatch
):
    import marknotes.document as module

    window.insert_text(window.session.add_image(image()))
    historical = window.editor.toPlainText()
    window.editor.undo()
    assert window.save_document(tmp_path / "cross-drive.md")

    def different_drives(*args):
        raise ValueError("different drive letters")

    monkeypatch.setattr(module.os.path, "relpath", different_drives)
    window.editor.redo()
    assert window.editor.toPlainText() == historical
    assert "file:///" in window.session.normalize_references(historical)
    rendered(qtbot, window)
    assert loaded_image_width(qtbot, window) == 32
    assert window.save_document()
    assert "img/cross-drive_00001.png" in window.editor.toPlainText()
    assert "file:///" not in window.editor.toPlainText()
    assert (tmp_path / "img/cross-drive_00001.png").is_file()
