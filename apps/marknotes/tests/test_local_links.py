import html
import re
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QDialog, QLabel, QWidget

from marknotes.local_links import LocalLinkDialog, local_path_link
from marknotes.notebook_theme import notebook_stylesheet
from marknotes.rendering import render_markdown
from marknotes.theme import palette


@pytest.mark.parametrize("kind", ["file", "folder"])
@pytest.mark.parametrize("input_type", ["path", "quoted", "url"])
def test_local_path_link_renders_encoded_file_and_folder_paths(tmp_path, kind, input_type):
    path = tmp_path / "日本語 [最終] #100%(資料)&.txt"
    if kind == "file":
        path.write_text("unchanged", encoding="utf-8")
    else:
        path.mkdir()
    value = str(path)
    if input_type == "quoted":
        value = f'"{value}"'
    elif input_type == "url":
        value = QUrl.fromLocalFile(value).toString(QUrl.ComponentFormattingOption.FullyEncoded)
    markdown = local_path_link(value, "表示 [資料] & <確認>")
    rendered = render_markdown(markdown).html
    href = html.unescape(re.search(r'href="([^"]+)"', rendered)[1])
    assert Path(QUrl(href).toLocalFile()) == path
    assert "<確認>" not in rendered
    assert "&lt;確認&gt;" in rendered
    assert "<img" not in rendered
    assert path.exists()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "relative.txt",
        "C:relative.txt",
        "https://example.com",
        "javascript:alert(1)",
        "C:\\line\nfile",
    ],
)
def test_local_path_link_rejects_non_absolute_paths(value):
    with pytest.raises(ValueError):
        local_path_link(value)


def test_unc_and_missing_paths_can_be_inserted_without_reading_them(tmp_path):
    markdown = local_path_link(r"\\server\share\資料 フォルダ")
    assert "file://server/share/" in markdown
    missing = tmp_path / "not-created.txt"
    assert "file:///" in local_path_link(str(missing))
    assert not missing.exists()


def test_dialog_browses_files_and_folders_and_cancel_keeps_path(qtbot, tmp_path, monkeypatch):
    dialog = LocalLinkDialog(directory=tmp_path)
    qtbot.addWidget(dialog)
    path = tmp_path / "document.txt"
    monkeypatch.setattr(
        "marknotes.local_links.QFileDialog.getOpenFileName", lambda *_: (str(path), "")
    )
    qtbot.mouseClick(dialog.file_button, Qt.MouseButton.LeftButton)
    assert dialog.path_edit.text() == str(path)
    monkeypatch.setattr(
        "marknotes.local_links.QFileDialog.getExistingDirectory", lambda *_: str(tmp_path)
    )
    qtbot.mouseClick(dialog.folder_button, Qt.MouseButton.LeftButton)
    assert dialog.path_edit.text() == str(tmp_path)
    monkeypatch.setattr("marknotes.local_links.QFileDialog.getExistingDirectory", lambda *_: "")
    qtbot.mouseClick(dialog.folder_button, Qt.MouseButton.LeftButton)
    assert dialog.path_edit.text() == str(tmp_path)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.markdown == local_path_link(str(tmp_path))


def test_dialog_validation_keeps_invalid_input_open_and_cancel_does_not_insert(qtbot):
    dialog = LocalLinkDialog()
    qtbot.addWidget(dialog)
    dialog.path_edit.setText("relative.txt")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.error_label.text()
    assert dialog.markdown == ""
    dialog.reject()
    assert dialog.markdown == ""


@pytest.mark.parametrize("dark", [True, False])
def test_dialog_background_and_controls_use_the_owning_window_theme(qtbot, qapp, dark):
    application_palette = QPalette(qapp.palette())
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.setPalette(palette(dark))
    parent.setStyleSheet(notebook_stylesheet(dark))
    dialog = LocalLinkDialog(parent)
    qtbot.addWidget(dialog)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dialog.show()
    dialog.ensurePolished()
    expected = palette(dark)
    # Check the painted surface, not just inherited text styles: the regression
    # had light dialog backgrounds with dark-theme labels and input fields.
    assert dialog.grab().toImage().pixelColor(1, 1) == expected.color(QPalette.ColorRole.Window)
    for label in dialog.findChildren(QLabel):
        assert label.palette().color(QPalette.ColorRole.WindowText) == expected.color(
            QPalette.ColorRole.WindowText
        )
    assert dialog.path_edit.palette().color(QPalette.ColorRole.Base) == expected.color(
        QPalette.ColorRole.Base
    )
    assert dialog.path_edit.palette().color(QPalette.ColorRole.PlaceholderText) == expected.color(
        QPalette.ColorRole.PlaceholderText
    )
    assert dialog.file_button.palette().color(QPalette.ColorRole.ButtonText) == expected.color(
        QPalette.ColorRole.ButtonText
    )
    assert qapp.palette() == application_palette
