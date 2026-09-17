"""Insert references to local files and folders without copying their contents."""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .managed_assets import escape_link_label


def local_path_link(value: str, label: str = "") -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not value or any(ord(char) < 32 for char in value):
        raise ValueError("ファイルまたはフォルダの絶対パスを入力してください。")
    if value.lower().startswith("file:"):
        url = QUrl(value)
        if not url.isValid() or not url.isLocalFile() or url.hasQuery() or url.hasFragment():
            raise ValueError("有効なファイルURLを入力してください。")
        value = url.toLocalFile()
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("ファイルまたはフォルダの絶対パスを入力してください。")
    url = QUrl.fromLocalFile(str(path)).toString(QUrl.ComponentFormattingOption.FullyEncoded)
    text = escape_link_label(label.strip() or path.name or str(path))
    return f"[{text}](<{url}>)"


class LocalLinkDialog(QDialog):
    def __init__(self, parent=None, *, label: str = "", directory: Path | None = None):
        super().__init__(parent)
        # Dialog windows need explicit propagation of the owning window's palette.
        self.setAttribute(Qt.WidgetAttribute.WA_WindowPropagation, True)
        if parent is not None:
            self.setPalette(parent.palette())
        placeholder = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        self.setStyleSheet(f"QLineEdit {{ placeholder-text-color: {placeholder}; }}")
        self.setWindowTitle("ローカルパスへのリンク")
        self.setMinimumWidth(480)
        self.directory = str(directory or Path.home())
        self.markdown = ""
        layout = QVBoxLayout(self)
        hint = QLabel("ファイルをコピーせず、元の場所へのリンクを挿入します。", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText(r"C:\Users\…\資料.pdf または file:///C:/…")
        self.path_edit.setAccessibleName("ファイルまたはフォルダのパス")
        form.addRow("パス", self.path_edit)
        browse = QHBoxLayout()
        self.file_button = QPushButton("ファイルを選択…", self)
        self.folder_button = QPushButton("フォルダを選択…", self)
        for button in (self.file_button, self.folder_button):
            button.setAutoDefault(False)
            browse.addWidget(button)
        self.file_button.clicked.connect(self._choose_file)
        self.folder_button.clicked.connect(self._choose_folder)
        form.addRow("", browse)
        self.label_edit = QLineEdit(label, self)
        self.label_edit.setPlaceholderText("省略時はファイル・フォルダ名")
        self.label_edit.setAccessibleName("リンクの表示名")
        form.addRow("表示名", self.label_edit)
        layout.addLayout(form)
        self.error_label = QLabel(self)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("挿入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.path_edit.setFocus()

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "リンクするファイル", self.directory)
        if path:
            self.path_edit.setText(path)
            self.directory = str(Path(path).parent)

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "リンクするフォルダ", self.directory)
        if path:
            self.path_edit.setText(path)
            self.directory = path

    def accept(self):
        try:
            self.markdown = local_path_link(self.path_edit.text(), self.label_edit.text())
        except (ValueError, OSError) as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            self.path_edit.setFocus()
            return
        super().accept()
