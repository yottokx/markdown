"""Preferences for app-managed notes, without external-document save defaults."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .settings_dialog import (
    DEFAULT_DIRECTORY_KEY,
    DEFAULT_IMAGE_EDITOR,
    IMAGE_EDITOR_KEY,
    SettingsDialog,
)


class NotebookSettingsDialog(SettingsDialog):
    """Reuse validated path pickers and the existing transactional settings save."""

    def __init__(self, settings: QSettings, library_path: Path, parent=None):
        QDialog.__init__(self, parent)
        self.settings = settings
        if parent is not None:
            self.setPalette(parent.palette())
        self.setWindowTitle("MarkNotes の設定")
        self.resize(720, 270)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.image_editor = QLineEdit(
            str(settings.value(IMAGE_EDITOR_KEY, DEFAULT_IMAGE_EDITOR) or DEFAULT_IMAGE_EDITOR)
        )
        self.image_editor.setObjectName("notebookImageEditor")
        self.image_editor.setPlaceholderText(DEFAULT_IMAGE_EDITOR)
        image_row = self._path_row(self.image_editor, self._choose_editor)
        reset_editor = QPushButton("ペイントに戻す")
        reset_editor.clicked.connect(lambda: self.image_editor.setText(DEFAULT_IMAGE_EDITOR))
        image_row.layout().addWidget(reset_editor)
        form.addRow("画像編集アプリ", image_row)

        self.default_folder = QLineEdit(str(settings.value(DEFAULT_DIRECTORY_KEY, "") or ""))
        self.default_folder.setObjectName("notebookDefaultFolder")
        self.default_folder.setPlaceholderText("未設定：ホームフォルダ")
        folder_row = self._path_row(self.default_folder, self._choose_folder)
        clear_folder = QPushButton("クリア")
        clear_folder.clicked.connect(self.default_folder.clear)
        folder_row.layout().addWidget(clear_folder)
        form.addRow("取り込み・書き出しの既定フォルダ", folder_row)

        self.library_path = QLineEdit(str(Path(library_path).resolve()))
        self.library_path.setObjectName("notebookLibraryPath")
        self.library_path.setReadOnly(True)
        self.library_path.setCursorPosition(0)
        form.addRow("現在のノートライブラリ", self.library_path)
        layout.addLayout(form)

        note = QLabel(
            "ノートはライブラリへ自動保存します。"
            "取り込み・書き出しの既定フォルダを変更しても、ノートの保存先は変わりません。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _values(self) -> dict[str, str]:
        executable = self.image_editor.text().strip() or DEFAULT_IMAGE_EDITOR
        editor_path = Path(executable).expanduser()
        if editor_path.is_file():
            executable = str(editor_path.resolve())
        elif not QStandardPaths.findExecutable(executable):
            raise ValueError("画像編集アプリの実行ファイルが見つかりません。")
        folder = self.default_folder.text().strip()
        if folder:
            directory = Path(folder).expanduser().resolve()
            if not directory.is_dir():
                raise ValueError("取り込み・書き出しの既定フォルダが見つかりません。")
            folder = str(directory)
        return {IMAGE_EDITOR_KEY: executable, DEFAULT_DIRECTORY_KEY: folder}
