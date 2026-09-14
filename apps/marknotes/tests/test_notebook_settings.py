"""Managed-note preferences and image rename wording preserve document independence."""

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel

from marknotes.image_actions import ImageRenameDialog
from marknotes.image_rename import current_images
from marknotes.managed_assets import ManagedAssets
from marknotes.notebook_runtime import NoteDocument
from marknotes.notebook_settings import NotebookSettingsDialog
from marknotes.settings_dialog import (
    DEFAULT_DIRECTORY_KEY,
    DEFAULT_ENCODING_KEY,
    DEFAULT_NEWLINE_KEY,
    IMAGE_EDITOR_KEY,
)


@pytest.fixture
def settings(tmp_path):
    result = QSettings(str(tmp_path / "notebook-settings.ini"), QSettings.Format.IniFormat)
    result.setValue(IMAGE_EDITOR_KEY, sys.executable)
    result.setValue(DEFAULT_ENCODING_KEY, "cp932")
    result.setValue(DEFAULT_NEWLINE_KEY, "CRLF")
    result.setValue("theme", "dark")
    return result


def stored(settings):
    return {key: settings.value(key) for key in settings.allKeys()}


def test_notebook_settings_only_offer_note_relevant_fields(qtbot, settings, tmp_path):
    dialog = NotebookSettingsDialog(settings, tmp_path / "library")
    qtbot.addWidget(dialog)
    assert not dialog.findChildren(QComboBox)
    assert not hasattr(dialog, "encoding")
    assert not hasattr(dialog, "newline")
    assert dialog.library_path.isReadOnly()
    assert Path(dialog.library_path.text()) == tmp_path / "library"
    labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "取り込み・書き出し" in labels
    assert "自動保存" in labels
    assert "文字コード" not in labels
    assert "改行コード" not in labels
    assert "初回保存" not in labels


def test_notebook_settings_save_only_common_paths_and_preserve_other_defaults(
    qtbot, settings, tmp_path
):
    before = stored(settings)
    dialog = NotebookSettingsDialog(settings, tmp_path / "library")
    qtbot.addWidget(dialog)
    dialog.default_folder.setText(str(tmp_path))
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    reloaded = QSettings(settings.fileName(), QSettings.Format.IniFormat)
    assert Path(reloaded.value(DEFAULT_DIRECTORY_KEY)) == tmp_path
    assert Path(reloaded.value(IMAGE_EDITOR_KEY)) == Path(sys.executable).resolve()
    for key in (DEFAULT_ENCODING_KEY, DEFAULT_NEWLINE_KEY, "theme"):
        assert reloaded.value(key) == before[key]
    assert not any("library" in key.lower() for key in reloaded.allKeys())


def test_cancel_and_clear_default_folder(qtbot, settings, tmp_path):
    settings.setValue(DEFAULT_DIRECTORY_KEY, str(tmp_path))
    before = stored(settings)
    dialog = NotebookSettingsDialog(settings, tmp_path / "library")
    qtbot.addWidget(dialog)
    dialog.default_folder.clear()
    dialog.image_editor.setText("not-existing-app.exe")
    dialog.reject()
    assert stored(settings) == before
    saved = NotebookSettingsDialog(settings, tmp_path / "library")
    qtbot.addWidget(saved)
    saved.default_folder.clear()
    saved.accept()
    assert saved.result() == QDialog.DialogCode.Accepted
    assert settings.value(DEFAULT_DIRECTORY_KEY) == ""


@pytest.mark.parametrize("invalid", ["image_editor", "default_folder"])
def test_invalid_path_keeps_settings_and_dialog_open(qtbot, settings, tmp_path, invalid):
    before = stored(settings)
    dialog = NotebookSettingsDialog(settings, tmp_path / "library")
    qtbot.addWidget(dialog)
    getattr(dialog, invalid).setText(str(tmp_path / "does-not-exist"))
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.message.text()
    assert stored(settings) == before


def test_failed_write_rolls_back_only_edited_keys(qtbot, settings, tmp_path, monkeypatch):
    before = stored(settings)
    dialog = NotebookSettingsDialog(settings, tmp_path / "library")
    qtbot.addWidget(dialog)
    dialog.default_folder.setText(str(tmp_path))
    monkeypatch.setattr(settings, "sync", lambda: None)
    monkeypatch.setattr(settings, "status", lambda: QSettings.Status.AccessError)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert "保存できません" in dialog.message.text()
    assert stored(settings) == before


def managed_picture(tmp_path):
    source = tmp_path / "source.png"
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0xFF3388AA)
    assert image.save(str(source))
    manager = ManagedAssets(tmp_path / "note")
    asset = manager.import_file(source)
    session = NoteDocument(manager.base_dir)
    return session, asset.markdown


def test_managed_image_rename_uses_note_scoped_description_and_prefix(qtbot, tmp_path):
    session, body = managed_picture(tmp_path)
    dialog = ImageRenameDialog(session, body)
    qtbot.addWidget(dialog)
    labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "assets" in labels
    assert "自動保存" in labels
    assert "初回保存" not in labels
    assert "未保存文書" not in labels
    assert "img配下" not in labels
    assert dialog.prefix.text() == "image_"
    assert dialog.plan.entries[0].target.name == "image_00001.png"
    assert dialog.table.horizontalHeaderItem(2).text() == "添付の範囲"
    assert dialog.table.item(0, 2).text() == "このノートの添付"
    dialog.prefix.setText("写真_")
    assert dialog.table.item(0, 1).text() == "写真_00001.png"
    assert dialog.table.item(0, 2).text() == "このノートの添付"
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text() == "改名して自動保存"


def test_managed_case_only_image_rename_is_rejected_before_copy(qtbot, tmp_path):
    session, body = managed_picture(tmp_path)
    image = current_images(session, body)[0]
    dialog = ImageRenameDialog(session, body, image=image)
    qtbot.addWidget(dialog)
    dialog.table.item(0, 1).setText(image.path.name.upper())
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert "大文字・小文字" in dialog.message.text()
    dialog.table.item(0, 1).setText("new-picture.png")
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert image.path.exists()
