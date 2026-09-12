"""Application preferences; defaults apply to subsequently created documents."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ENCODINGS = [
    "utf-8",
    "utf-8-sig",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "cp932",
    "shift_jis",
    "euc_jp",
    "iso2022_jp",
]
DEFAULT_DIRECTORY_KEY = "files/defaultDirectory"
DEFAULT_ENCODING_KEY = "files/defaultEncoding"
DEFAULT_NEWLINE_KEY = "files/defaultNewline"
IMAGE_EDITOR_KEY = "image_editor"
DEFAULT_IMAGE_EDITOR = "mspaint.exe"


def new_document_format(settings: QSettings) -> tuple[str, str]:
    """Ignore unavailable or malformed stored choices without affecting file decoding."""
    encoding = str(settings.value(DEFAULT_ENCODING_KEY, "utf-8"))
    newline = str(settings.value(DEFAULT_NEWLINE_KEY, "LF"))
    return (
        encoding if encoding in ENCODINGS else "utf-8",
        newline if newline in {"LF", "CRLF", "CR"} else "LF",
    )


def configured_directory(settings: QSettings) -> Path | None:
    value = str(settings.value(DEFAULT_DIRECTORY_KEY, "") or "").strip()
    if value:
        try:
            path = Path(value).expanduser().resolve()
            if path.is_dir():
                return path
        except (OSError, ValueError):
            pass
    return None


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        if parent is not None:
            self.setPalette(parent.palette())
        self.setWindowTitle("設定")
        self.resize(650, 300)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.image_editor = QLineEdit(
            str(settings.value(IMAGE_EDITOR_KEY, DEFAULT_IMAGE_EDITOR) or DEFAULT_IMAGE_EDITOR)
        )
        self.image_editor.setPlaceholderText(DEFAULT_IMAGE_EDITOR)
        image_row = self._path_row(self.image_editor, self._choose_editor)
        reset_editor = QPushButton("ペイントに戻す")
        reset_editor.clicked.connect(lambda: self.image_editor.setText(DEFAULT_IMAGE_EDITOR))
        image_row.layout().addWidget(reset_editor)
        form.addRow("画像編集アプリ", image_row)
        self.default_folder = QLineEdit(str(settings.value(DEFAULT_DIRECTORY_KEY, "") or ""))
        self.default_folder.setPlaceholderText("未設定：現在の文書のフォルダ、またはホーム")
        folder_row = self._path_row(self.default_folder, self._choose_folder)
        clear_folder = QPushButton("クリア")
        clear_folder.clicked.connect(self.default_folder.clear)
        folder_row.layout().addWidget(clear_folder)
        form.addRow("デフォルトで開くフォルダ", folder_row)
        encoding, newline = new_document_format(settings)
        self.encoding = QComboBox()
        self.encoding.addItems(ENCODINGS)
        self.encoding.setCurrentText(encoding)
        self.newline = QComboBox()
        self.newline.addItems(["LF", "CRLF", "CR"])
        self.newline.setCurrentText(newline)
        form.addRow("新規文書の文字コード", self.encoding)
        form.addRow("新規文書の改行コード", self.newline)
        layout.addLayout(form)
        note = QLabel(
            "文字コード・改行コードは、次に作る新規文書から適用します。"
            "開いた文書では自動認識した形式を維持します。"
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

    @staticmethod
    def _path_row(field, browse):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field, 1)
        button = QPushButton("参照…")
        button.clicked.connect(browse)
        layout.addWidget(button)
        return row

    def _choose_editor(self):
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "画像編集アプリを選択",
            self.image_editor.text(),
            "実行ファイル (*.exe);;すべてのファイル (*)",
        )
        if selected:
            self.image_editor.setText(selected)

    def _choose_folder(self):
        directory = configured_directory(self.settings) or Path.home()
        candidate = Path(self.default_folder.text()).expanduser()
        if self.default_folder.text().strip() and candidate.is_dir():
            directory = candidate
        selected = QFileDialog.getExistingDirectory(self, "既定のフォルダを選択", str(directory))
        if selected:
            self.default_folder.setText(selected)

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
                raise ValueError("デフォルトで開くフォルダが見つかりません。")
            folder = str(directory)
        encoding, newline = self.encoding.currentText(), self.newline.currentText()
        if encoding not in ENCODINGS or newline not in {"LF", "CRLF", "CR"}:
            raise ValueError("文字コード・改行コードを一覧から選択してください。")
        return {
            IMAGE_EDITOR_KEY: executable,
            DEFAULT_DIRECTORY_KEY: folder,
            DEFAULT_ENCODING_KEY: encoding,
            DEFAULT_NEWLINE_KEY: newline,
        }

    def accept(self):
        try:
            values = self._values()
        except (OSError, ValueError) as exc:
            self.message.setText(str(exc))
            self.message.show()
            return
        previous = {key: self.settings.value(key) for key in values}
        for key, value in values.items():
            self.settings.setValue(key, value)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            for key, value in previous.items():
                if value is None:
                    self.settings.remove(key)
                else:
                    self.settings.setValue(key, value)
            self.settings.sync()
            self.message.setText("設定を保存できません。設定ファイルの保存先を確認してください。")
            self.message.show()
            return
        super().accept()
