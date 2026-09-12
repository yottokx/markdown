"""Document commands and format selection for the main window."""

from __future__ import annotations

import shutil
from difflib import SequenceMatcher
from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from md_editor.document import ExternalFileChangedError, NeedsNewlineSelection
from md_editor.search import qt_position
from md_editor.settings_dialog import (
    ENCODINGS,
    SettingsDialog,
    configured_directory,
    new_document_format,
)


class FormatDialog(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        if parent is not None:
            self.setPalette(parent.palette())
        self.setWindowTitle("文字コード・改行コード")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.encoding = QComboBox()
        self.encoding.addItems(ENCODINGS)
        if session.encoding not in ENCODINGS:
            self.encoding.addItem(session.encoding)
        self.encoding.setCurrentText(session.encoding)
        self.newline = QComboBox()
        self.newline.addItems(["LF", "CRLF", "CR"])
        self.newline.setCurrentText(session.newline)
        form.addRow("保存時の文字コード", self.encoding)
        form.addRow("保存時の改行コード", self.newline)
        layout.addLayout(form)
        layout.addWidget(
            QLabel(
                "変更は次の保存時に適用します。読み直す場合は「文字コードを指定して開き直す」を使います。"
            )
        )
        if session.mixed_newlines:
            layout.addWidget(
                QLabel(
                    "この文書には複数の改行コードが混在しています。保存時に選択した形式へ統一します。"
                )
            )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class FileActions:
    def configure_settings(self):
        dialog = SettingsDialog(self.settings, self)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            dialog.deleteLater()
        if accepted:
            self.statusBar().showMessage(
                "設定を保存しました。新規文書の形式は次の新規作成から適用します。", 4000
            )
        return accepted

    def apply_new_document_defaults(self):
        """Call after creating a new session, including initial window setup."""
        if self.session.path is not None:
            return
        self.session.encoding, self.session.newline = new_document_format(self.settings)
        self.session.bom = b""
        self.session.mixed_newlines = False
        self.session.encoding_warning = ""
        self._pending_encoding = self._pending_newline = None

    def dialog_directory(self, *, prefer_configured: bool = True) -> Path:
        """Choose a user folder, never the unsaved document's temporary asset area."""
        if prefer_configured:
            configured = configured_directory(self.settings)
            if configured is not None:
                return configured
        if self.path is not None:
            return self.path.parent
        return Path.home()

    def new_document(self):
        if not self._confirm_discard():
            return False
        self.session.new()
        self.apply_new_document_defaults()
        self.set_source("", self.session.base_dir, update_session=False)
        self._watch_current_file()
        return True

    def open_path(self, path: Path, encoding: str | None = None) -> bool:
        try:
            text = self.session.open(path, encoding=encoding)
        except (OSError, UnicodeError, ValueError, LookupError) as exc:
            QMessageBox.warning(self, "ファイルを開けません", f"{path}\n\n{exc}")
            return False
        self._pending_encoding = None
        self._pending_newline = None
        self.set_source(text, self.session.base_dir, self.session.path, update_session=False)
        self._watch_current_file()
        warning = " ".join(self.session.recovery_messages) or self.session.encoding_warning
        if warning:
            self.statusBar().showMessage(warning)
        elif self.session.mixed_newlines:
            self.statusBar().showMessage(
                "改行コードが混在しています。保存時に統一先を選択できます。"
            )
        else:
            self.statusBar().showMessage("ファイルを開きました", 2500)
        return True

    def open_dialog(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Markdownを開く",
            str(self.dialog_directory()),
            "Markdown (*.md *.markdown);;すべてのファイル (*)",
        )
        if filename and self._confirm_discard():
            self.open_path(Path(filename))

    def open_sample(self):
        if not self._confirm_discard():
            return
        from md_editor.app import RESOURCE_DIR

        self.session.new()
        (self.session.base_dir / "img").mkdir(exist_ok=True)
        shutil.copyfile(
            RESOURCE_DIR / "img" / "scroll-check.svg",
            self.session.base_dir / "img" / "scroll-check.svg",
        )
        text = (RESOURCE_DIR / "scroll-check.md").read_text(encoding="utf-8")
        self._pending_encoding = None
        self._pending_newline = None
        self.set_source(text, self.session.base_dir, update_session=False)
        self._watch_current_file()
        self.statusBar().showMessage(
            "サンプルを開きました。保存時に新しいファイル名を指定できます。", 4000
        )

    def save_as_dialog(self):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "名前を付けて保存",
            str(self.path or (self.dialog_directory() / "無題.md")),
            "Markdown (*.md);;すべてのファイル (*)",
        )
        if not filename:
            return False
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".md")
        return self.save_document(path)

    def save_document(
        self, path: Path | None = None, *, encoding=None, newline=None, overwrite_external=False
    ):
        if path is None and self.session.path is None:
            return self.save_as_dialog()
        encoding = encoding or self._pending_encoding
        newline = newline or self._pending_newline
        try:
            result = self.session.save(
                self.editor.toPlainText(),
                path,
                encoding=encoding,
                newline=newline,
                overwrite_external=overwrite_external,
            )
        except NeedsNewlineSelection:
            value, ok = QInputDialog.getItem(
                self,
                "改行コードが混在しています",
                "保存時に統一する改行コード",
                ["LF", "CRLF", "CR"],
                ["LF", "CRLF", "CR"].index(self.session.newline),
                False,
            )
            return (
                self.save_document(
                    path, encoding=encoding, newline=value, overwrite_external=overwrite_external
                )
                if ok
                else False
            )
        except UnicodeEncodeError:
            answer = QMessageBox.question(
                self,
                "この文字コードでは保存できません",
                "現在の文字コードで表せない文字があります。UTF-8へ変更して保存しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            return (
                self.save_document(
                    path, encoding="utf-8", newline=newline, overwrite_external=overwrite_external
                )
                if answer == QMessageBox.StandardButton.Yes
                else False
            )
        except ExternalFileChangedError:
            answer = QMessageBox.question(
                self,
                "ファイルが外部で更新されています",
                "現在の内容で上書きしますか？「いいえ」で別名保存できます。",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Yes:
                return self.save_document(
                    path, encoding=encoding, newline=newline, overwrite_external=True
                )
            return self.save_as_dialog() if answer == QMessageBox.StandardButton.No else False
        except (OSError, UnicodeError, ValueError, LookupError) as exc:
            QMessageBox.warning(self, "保存できません", str(exc))
            return False
        self._apply_document_result(result, saved=True)
        self.statusBar().showMessage(f"保存しました: {self.path.name}", 3500)
        return True

    def _apply_document_result(self, result, *, saved: bool = True):
        source_position = self.editor.source_position()
        source = self.editor.toPlainText()
        if result.text != source:
            saved_cursor = QTextCursor(self.editor.textCursor())
            cursor = QTextCursor(self.editor.document())
            cursor.beginEditBlock()
            for tag, start, end, new_start, new_end in reversed(
                SequenceMatcher(None, source, result.text).get_opcodes()
            ):
                if tag == "equal":
                    continue
                cursor.setPosition(qt_position(source, start))
                cursor.setPosition(qt_position(source, end), QTextCursor.MoveMode.KeepAnchor)
                cursor.insertText(result.text[new_start:new_end])
            cursor.endEditBlock()
            self.editor.setTextCursor(saved_cursor)
        self.path = self.session.path
        self.base_dir = self.session.base_dir
        if saved:
            self._pending_encoding = None
            self._pending_newline = None
        self.editor.document().setModified(not saved)
        self.editor.scroll_to_source(source_position)
        self._revision += 1
        self._render_timer.stop()
        self._render()
        self._watch_current_file()
        self._update_title()
        self._update_positions()

    def _has_unsaved_changes(self) -> bool:
        return (
            self.editor.document().isModified()
            or bool(self._pending_encoding and self._pending_encoding != self.session.encoding)
            or bool(self._pending_newline and self._pending_newline != self.session.newline)
        )

    def _confirm_discard(self) -> bool:
        if not self._has_unsaved_changes():
            return True
        answer = QMessageBox.question(
            self,
            "変更を保存しますか？",
            "この文書には未保存の変更があります。",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_document()
        return answer == QMessageBox.StandardButton.Discard

    def change_format(self):
        dialog = FormatDialog(self.session, self)
        if self._pending_encoding:
            dialog.encoding.setCurrentText(self._pending_encoding)
        if self._pending_newline:
            dialog.newline.setCurrentText(self._pending_newline)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._pending_encoding = dialog.encoding.currentText()
            self._pending_newline = dialog.newline.currentText()
            self._update_title()
            self._update_positions()

    def reopen_encoding(self):
        if self.path is None:
            return
        value, ok = QInputDialog.getItem(
            self, "文字コードを指定して開き直す", "文字コード", ENCODINGS, 0, True
        )
        if ok and self._confirm_discard():
            self.open_path(self.path, encoding=value)

    def _watch_current_file(self):
        paths = self._file_watcher.files() + self._file_watcher.directories()
        if paths:
            self._file_watcher.removePaths(paths)
        if self.path is not None:
            self._file_watcher.addPath(str(self.path.parent))
            if self.path.exists():
                self._file_watcher.addPath(str(self.path))

    def _external_file_changed(self, *_args):
        if self.path is None or not self.session.has_external_change():
            return
        if self._has_unsaved_changes() or not self.path.exists():
            self.statusBar().showMessage(
                "ファイルが外部で変更・削除されています。保存前に内容を確認してください。"
            )
        else:
            position = self.editor.source_position()
            cursor_position = self.editor.textCursor().position()
            if self.open_path(self.path):
                cursor = self.editor.textCursor()
                cursor.setPosition(
                    min(cursor_position, self.editor.document().characterCount() - 1)
                )
                self.editor.setTextCursor(cursor)
                self.editor.scroll_to_source(position)
                self.statusBar().showMessage("外部の変更を再読み込みしました", 3500)
