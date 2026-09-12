"""Clipboard and contextual editing, separate from source key/scroll behavior."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage, QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu, QMessageBox

from marknotes.clipboard import (
    as_code_block,
    as_quote,
    choose_paste,
    existing_fence_paste,
    html_to_markdown,
)
from marknotes.code_language import detect_code_language
from marknotes.editor import SourceEditor
from marknotes.formatting_menu import add_format_menus
from marknotes.image_actions import ImageActions
from marknotes.image_rename import image_at
from marknotes.search import python_position, qt_position
from marknotes.tables import TableDialog, find_table, parse_delimited


class InteractiveEditor(SourceEditor):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def canInsertFromMimeData(self, source):
        return source.hasText() or source.hasHtml() or source.hasImage() or source.hasUrls()

    def insertFromMimeData(self, source):
        self.owner.paste_mime(source)

    def contextMenuEvent(self, event):
        # Share labels, shortcuts and command state with the application menu.
        # Keep the caret/selection in place; only table editing uses the click.
        self.setFocus()
        self.owner.refresh_edit_actions()
        self.owner.refresh_clipboard_actions()
        menu = QMenu(self)
        clicked = self.cursorForPosition(event.pos())
        source = self.toPlainText()
        region = find_table(source, python_position(source, clicked.position()))
        separator = False
        for action in self.owner.edit_menu.actions():
            if action.isSeparator():
                separator = bool(menu.actions())
                continue
            if not action.property("sourceContext"):
                continue
            if separator:
                menu.addSeparator()
                separator = False
            menu.addAction(action)
        menu.addSeparator()
        # Share the persistent menu action. PySide6 addMenu(existing_menu)
        # invalidates its wrapper when this temporary popup is deleted.
        menu.addAction(self.owner.insert_menu.menuAction())
        add_format_menus(menu, self)
        if region is not None:
            # Table editing belongs only to the clicked table's context menu.
            # The menu click can target a different table from the caret.
            menu.addSeparator()
            item = menu.addAction("表を編集…")
            item.setEnabled(not self.isReadOnly())
            item.triggered.connect(lambda: self.owner.edit_table(region))
        selected_image = image_at(
            self.owner.session, source, python_position(source, clicked.position())
        )
        if selected_image is not None:
            menu.addSeparator()
            edit_image = menu.addAction("画像を編集")
            edit_image.setEnabled(selected_image.raster and not self.isReadOnly())
            edit_image.triggered.connect(lambda: self.owner.edit_image(selected_image))
            rename_image = menu.addAction("ファイル名を変更…")
            rename_image.setEnabled(not self.isReadOnly())
            rename_image.triggered.connect(lambda: self.owner.rename_current_image(selected_image))
        try:
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()


class EditingActions(ImageActions):
    def refresh_edit_actions(self, *_args):
        selected = self.editor.textCursor().hasSelection()
        editable = not self.editor.isReadOnly()
        preview_only = getattr(self, "display_mode", "split") == "preview"
        copy_available = bool(self.preview.page().selectedText()) if preview_only else selected
        self.insert_menu.setEnabled(editable)
        self.cut_action.setEnabled(selected and editable and not preview_only)
        self.copy_action.setEnabled(copy_available)
        self.delete_action.setEnabled(selected and editable and not preview_only)
        self.select_all_action.setEnabled(not self.editor.document().isEmpty())

    def refresh_clipboard_actions(self, *_args):
        editable = not self.editor.isReadOnly()
        mime = QApplication.clipboard().mimeData()
        has_text = mime is not None and mime.hasText()
        # Derive availability from the payload: child actions report disabled
        # while their QMenu is disabled, even after setEnabled(True).
        self.paste_format_menu.setEnabled(
            editable and mime is not None and (has_text or mime.hasHtml())
        )
        self.paste_action.setEnabled(editable and self.editor.canPaste())
        self.plain_paste_action.setEnabled(editable and has_text)
        self.html_paste_action.setEnabled(
            editable and mime is not None and (mime.hasHtml() or has_text)
        )
        self.code_paste_action.setEnabled(editable and has_text)
        self.quote_paste_action.setEnabled(
            editable and mime is not None and (has_text or mime.hasHtml())
        )
        self.table_paste_action.setEnabled(
            editable and has_text and bool(parse_delimited(mime.text()))
        )

    def delete_selection(self):
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.beginEditBlock()
            cursor.removeSelectedText()
            cursor.endEditBlock()
            self.editor.setTextCursor(cursor)

    def insert_text(self, text: str):
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(text)
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def paste_mime(self, mime):
        try:
            decision = choose_paste(mime, in_code=self.editor.in_code_block())
            if decision.kind == "image":
                data = mime.imageData()
                image = data.toImage() if hasattr(data, "toImage") else QImage(data)
                self.insert_text(self.session.add_image(image))
            elif decision.kind == "files":
                snippets = [self.session.import_image_file(path) for path in decision.files]
                self.insert_text("\n".join(snippets))
            else:
                self.insert_text(decision.text)
            self.statusBar().showMessage(decision.reason or "貼り付けました", 3500)
        except (ValueError, OSError, TypeError) as exc:
            QMessageBox.warning(self, "貼り付けできません", str(exc))

    def paste_plain(self):
        self.insert_text(QApplication.clipboard().text())

    def paste_code_block(self):
        mime = QApplication.clipboard().mimeData()
        if self.editor.isReadOnly() or mime is None or not mime.hasText():
            return
        text = mime.text()
        if not text:
            return
        source = self.editor.toPlainText()
        cursor = self.editor.textCursor()
        replacement = existing_fence_paste(
            source,
            python_position(source, cursor.selectionStart()),
            python_position(source, cursor.selectionEnd()),
            text,
        )
        if replacement is not None:
            start, end, snippet, caret = replacement
            cursor.beginEditBlock()
            cursor.setPosition(qt_position(source, start))
            cursor.setPosition(qt_position(source, end), QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(snippet)
            cursor.endEditBlock()
            updated = self.editor.toPlainText()
            cursor.setPosition(qt_position(updated, start + caret))
            self.editor.setTextCursor(cursor)
            self.editor.setFocus()
            self.statusBar().showMessage("既存のコードブロックに貼り付けました", 3500)
            return
        language = detect_code_language(text)
        self.insert_markdown_block(as_code_block(text, language))
        label = f"言語: {language}" if language else "言語指定なし"
        self.statusBar().showMessage(f"コードブロックとして貼り付けました（{label}）", 3500)

    def paste_quote(self):
        mime = QApplication.clipboard().mimeData()
        if self.editor.isReadOnly() or mime is None:
            return
        try:
            if mime.hasText():
                text = mime.text()
            elif mime.hasHtml():
                text = html_to_markdown(mime.html())
            else:
                return
            if text:
                self.insert_markdown_block(as_quote(text))
                self.statusBar().showMessage("引用として貼り付けました", 3500)
        except (ValueError, OSError, TypeError) as exc:
            QMessageBox.warning(self, "引用として貼り付けできません", str(exc))

    def insert_markdown_block(self, text: str):
        # Blank lines keep the new block and following prose independent,
        # including when replacing a selection in the middle of a paragraph.
        source = self.editor.toPlainText()
        cursor = self.editor.textCursor()
        start = python_position(source, cursor.selectionStart())
        end = python_position(source, cursor.selectionEnd())
        left, right = source[:start], source[end:]
        before = "\n" * max(0, 2 - (len(left) - len(left.rstrip("\n")))) if left else ""
        after = "\n" * max(0, 2 - (len(right) - len(right.lstrip("\n")))) if right else ""
        self.insert_text(before + text + after)

    def paste_html(self):
        mime = QApplication.clipboard().mimeData()
        if mime is None:
            return
        if mime.hasHtml():
            try:
                self.insert_text(html_to_markdown(mime.html()))
            except (ValueError, OSError, TypeError) as exc:
                QMessageBox.warning(self, "HTMLを変換できません", str(exc))
        elif mime.hasText():
            self.insert_text(mime.text())

    def insert_image_file(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "画像を挿入",
            str(self.dialog_directory()),
            "画像 (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.tif *.tiff *.svg);;すべて (*)",
        )
        if not paths:
            return
        try:
            snippets = [self.session.import_image_file(Path(path)) for path in paths]
            self.insert_text("\n".join(snippets))
        except (ValueError, OSError, TypeError) as exc:
            QMessageBox.warning(self, "画像を挿入できません", str(exc))

    def create_table(self):
        if self.editor.isReadOnly():
            return
        self._insert_table_dialog(TableDialog(self, create_mode=True))

    def paste_table(self):
        if self.editor.isReadOnly():
            return
        raw = QApplication.clipboard().text()
        rows = parse_delimited(raw)
        if not rows:
            self.statusBar().showMessage("クリップボードからCSV/TSVの表を読み取れません", 4000)
            return
        self._insert_table_dialog(TableDialog(self, rows=rows, paste_mode=True, raw_text=raw))

    def _insert_table_dialog(self, dialog):
        try:
            revision = self._revision
            source = self.editor.toPlainText()
            cursor = self.editor.textCursor()
            start = python_position(source, cursor.selectionStart())
            end = python_position(source, cursor.selectionEnd())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if revision != self._revision or source != self.editor.toPlainText():
                QMessageBox.information(
                    self, "文書が更新されました", "表の挿入位置を確認して再度実行してください。"
                )
                return
            if self.editor.isReadOnly():
                return
            text = dialog.markdown()
            # A plain line immediately following a GFM table becomes another
            # table row. Keep inserted tables separate from surrounding prose.
            left, right = source[:start], source[end:]
            before = "\n" * max(0, 2 - (len(left) - len(left.rstrip("\n")))) if left else ""
            after = "\n" * max(0, 2 - (len(right) - len(right.lstrip("\n")))) if right else ""
            # Preserve the insertion target captured before the modal dialog opened.
            self.editor.setTextCursor(cursor)
            self.insert_text(before + text + after)
        finally:
            dialog.deleteLater()

    def edit_table(self, region=None):
        source = self.editor.toPlainText()
        if region is None:
            region = find_table(
                source, python_position(source, self.editor.textCursor().position())
            )
        if region is None:
            self.statusBar().showMessage("カーソルをMarkdownの表に置いてください", 3500)
            return
        revision = self._revision
        dialog = TableDialog(self, rows=region.rows, alignments=region.alignments, paste_mode=False)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if revision != self._revision or source != self.editor.toPlainText():
                QMessageBox.information(
                    self, "文書が更新されました", "表を再度開いて編集してください。"
                )
                return
            replacement = region.wrap(dialog.markdown())
            cursor = QTextCursor(self.editor.document())
            cursor.beginEditBlock()
            cursor.setPosition(qt_position(source, region.start))
            cursor.setPosition(qt_position(source, region.end), QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(replacement)
            cursor.endEditBlock()
            self.editor.setTextCursor(cursor)
            self.editor.setFocus()
        finally:
            dialog.deleteLater()
