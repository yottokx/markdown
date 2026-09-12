"""Preferences commit/cancel behavior and real new/open/save default boundaries."""

import codecs
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from md_editor.app import MainWindow
from md_editor.settings_dialog import (
    DEFAULT_DIRECTORY_KEY,
    DEFAULT_ENCODING_KEY,
    DEFAULT_NEWLINE_KEY,
    IMAGE_EDITOR_KEY,
    SettingsDialog,
    new_document_format,
)


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)


def values(settings):
    return {key: settings.value(key) for key in settings.allKeys()}


def cleanup(window):
    window.editor.document().setModified(False)
    window._pending_encoding = window._pending_newline = None


def make_window(qtbot, settings, monkeypatch):
    window = MainWindow(settings)
    qtbot.addWidget(window, before_close_func=cleanup)

    def unexpected(*args):
        pytest.fail(f"Unexpected dialog: {args[1:3]}")

    monkeypatch.setattr(QMessageBox, "warning", unexpected)
    monkeypatch.setattr(QMessageBox, "question", unexpected)
    return window


def test_cancel_does_not_apply_any_settings(qtbot, settings, tmp_path):
    settings.setValue(IMAGE_EDITOR_KEY, sys.executable)
    settings.setValue(DEFAULT_ENCODING_KEY, "cp932")
    settings.setValue("theme", "dark")
    before = values(settings)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog.image_editor.text() == sys.executable
    dialog.image_editor.setText("mspaint.exe")
    dialog.default_folder.setText(str(tmp_path))
    dialog.encoding.setCurrentText("utf-16")
    dialog.newline.setCurrentText("CRLF")
    dialog.reject()
    settings.sync()
    reloaded = QSettings(settings.fileName(), QSettings.Format.IniFormat)
    assert values(reloaded) == before


def test_accept_validates_and_persists_all_choices(qtbot, settings, tmp_path):
    settings.setValue("theme", "dark")
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.image_editor.setText(sys.executable)
    dialog.default_folder.setText(str(tmp_path))
    dialog.encoding.setCurrentText("utf-16")
    dialog.newline.setCurrentText("CRLF")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    reloaded = QSettings(settings.fileName(), QSettings.Format.IniFormat)
    assert Path(reloaded.value(IMAGE_EDITOR_KEY)) == Path(sys.executable).resolve()
    assert Path(reloaded.value(DEFAULT_DIRECTORY_KEY)) == tmp_path
    assert new_document_format(reloaded) == ("utf-16", "CRLF")
    assert reloaded.value("theme") == "dark"
    clear = SettingsDialog(settings)
    qtbot.addWidget(clear)
    clear.default_folder.clear()
    clear.accept()
    assert settings.value(DEFAULT_DIRECTORY_KEY) == ""


@pytest.mark.parametrize("invalid_field", ["editor", "folder", "encoding", "newline"])
def test_invalid_preferences_keep_previous_values(qtbot, settings, tmp_path, invalid_field):
    settings.setValue(IMAGE_EDITOR_KEY, sys.executable)
    settings.setValue(DEFAULT_ENCODING_KEY, "cp932")
    before = values(settings)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.default_folder.setText(str(tmp_path))
    dialog.encoding.setCurrentText("utf-8")
    if invalid_field == "editor":
        dialog.image_editor.setText(str(tmp_path / "missing-editor.exe"))
    elif invalid_field == "folder":
        dialog.default_folder.setText(str(tmp_path / "missing-folder"))
    else:
        field = getattr(dialog, invalid_field)
        field.addItem("invalid")
        field.setCurrentText("invalid")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.message.text()
    assert values(settings) == before


def test_defaults_apply_on_startup_and_new_but_keep_opened_document_format(
    qtbot, settings, tmp_path, monkeypatch
):
    settings.setValue(DEFAULT_ENCODING_KEY, "cp932")
    settings.setValue(DEFAULT_NEWLINE_KEY, "CRLF")
    window = make_window(qtbot, settings, monkeypatch)
    assert (window.session.encoding, window.session.newline) == ("cp932", "CRLF")
    assert not window._has_unsaved_changes()
    window.insert_text("日本語\n次の行\n")
    first = tmp_path / "new.md"
    assert window.save_document(first)
    assert first.read_bytes() == "日本語\r\n次の行\r\n".encode("cp932")

    opened = tmp_path / "existing.md"
    original = codecs.BOM_UTF16_BE + "# 日本語\n本文\n".encode("utf-16-be")
    opened.write_bytes(original)
    assert window.open_path(opened)
    window.apply_new_document_defaults()
    assert (window.session.encoding, window.session.newline) == ("utf-16-be", "LF")
    assert window.save_document()
    assert opened.read_bytes() == original

    settings.setValue(DEFAULT_ENCODING_KEY, "utf-8-sig")
    settings.setValue(DEFAULT_NEWLINE_KEY, "CR")
    assert window.new_document()
    assert not window._has_unsaved_changes()
    window.insert_text("新規\n本文\n")
    target = tmp_path / "later.md"
    assert window.save_document(target)
    assert target.read_bytes() == codecs.BOM_UTF8 + "新規\r本文\r".encode()


def test_generic_utf16_default_writes_required_bom(qtbot, settings, tmp_path, monkeypatch):
    settings.setValue(DEFAULT_ENCODING_KEY, "utf-16")
    settings.setValue(DEFAULT_NEWLINE_KEY, "CRLF")
    window = make_window(qtbot, settings, monkeypatch)
    window.insert_text("日本語\n本文")
    path = tmp_path / "utf16.md"
    assert window.save_document(path)
    assert path.read_bytes() == codecs.BOM_UTF16_LE + "日本語\r\n本文".encode("utf-16-le")


def test_settings_menu_opens_dialog_and_leaves_current_new_document_unchanged(
    qtbot, settings, tmp_path, monkeypatch
):
    settings.setValue(IMAGE_EDITOR_KEY, sys.executable)
    window = make_window(qtbot, settings, monkeypatch)
    window.insert_text("現在の文書")
    window._pending_newline = "CRLF"
    file_menu = window.menuBar().actions()[0].menu()
    action = next(action for action in file_menu.actions() if action.text() == "設定…")
    seen = []

    def choose():
        dialog = QApplication.activeModalWidget()
        seen.append(isinstance(dialog, SettingsDialog))
        if isinstance(dialog, SettingsDialog):
            dialog.encoding.setCurrentText("cp932")
            dialog.newline.setCurrentText("CR")
            dialog.default_folder.setText(str(tmp_path))
            dialog.accept()
        elif dialog is not None:
            dialog.reject()

    # Always close a popup even if the action opens an unexpected dialog.
    QTimer.singleShot(20, choose)
    watchdog = QTimer(window)
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(
        lambda: QApplication.activeModalWidget() and QApplication.activeModalWidget().reject()
    )
    watchdog.start(2000)
    action.trigger()
    watchdog.stop()
    assert seen == [True]
    assert new_document_format(settings) == ("cp932", "CR")
    assert (window.session.encoding, window.session.newline) == ("utf-8", "LF")
    assert window._pending_newline == "CRLF"
    assert window.editor.toPlainText() == "現在の文書"
    assert window.editor.document().isUndoAvailable()


def test_file_dialog_folders_respect_setting_and_avoid_unsaved_temp_directory(
    qtbot, settings, tmp_path, monkeypatch
):
    window = make_window(qtbot, settings, monkeypatch)
    opened, saved = [], []
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args: (opened.append(Path(args[2])) or "", "")
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args: (saved.append(Path(args[2])) or "", "")
    )
    window.open_dialog()
    assert not window.save_as_dialog()
    assert opened[-1] == Path.home()
    assert saved[-1] == Path.home() / "無題.md"
    assert opened[-1] != window.session.base_dir

    preferred = tmp_path / "preferred"
    preferred.mkdir()
    settings.setValue(DEFAULT_DIRECTORY_KEY, str(preferred))
    window.open_dialog()
    window.save_as_dialog()
    assert opened[-1] == preferred
    assert saved[-1] == preferred / "無題.md"
    assert window.dialog_directory() == preferred

    actual = tmp_path / "document.md"
    actual.write_text("# Saved", encoding="utf-8")
    assert window.open_path(actual)
    window.open_dialog()
    window.save_as_dialog()
    assert opened[-1] == preferred
    assert saved[-1] == actual
    assert window.dialog_directory(prefer_configured=False) == actual.parent
    settings.setValue(DEFAULT_DIRECTORY_KEY, str(tmp_path / "missing"))
    window.open_dialog()
    assert opened[-1] == actual.parent


def test_invalid_stored_format_falls_back_to_valid_defaults(settings):
    settings.setValue(DEFAULT_ENCODING_KEY, "invalid-codec")
    settings.setValue(DEFAULT_NEWLINE_KEY, "invalid-newline")
    assert new_document_format(settings) == ("utf-8", "LF")


def test_failed_settings_write_restores_previous_values(qtbot, settings, tmp_path, monkeypatch):
    settings.setValue(IMAGE_EDITOR_KEY, sys.executable)
    settings.setValue(DEFAULT_ENCODING_KEY, "cp932")
    before = values(settings)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.default_folder.setText(str(tmp_path))
    dialog.encoding.setCurrentText("utf-16")
    monkeypatch.setattr(settings, "sync", lambda: None)
    monkeypatch.setattr(settings, "status", lambda: QSettings.Status.AccessError)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert values(settings) == before
    assert "保存できません" in dialog.message.text()
