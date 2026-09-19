"""Source view with source-line coordinates independent of line wrapping."""

from __future__ import annotations

import math

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPainter, QTextCursor, QTextDocument
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from .highlighter import MarkdownHighlighter
from .indentation import MarkdownContext, MarkdownEditingMixin


class LineNumbers(QWidget):
    def __init__(self, editor: SourceEditor) -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_line_numbers(event)


class SourceEditor(MarkdownEditingMixin, QPlainTextEdit):
    source_position_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sourceEditor")
        self.setTabChangesFocus(False)
        self.setCenterOnScroll(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        font = QFont("Cascadia Mono", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 2)
        self.gutter = LineNumbers(self)
        self._composing = False
        self._removed_list = None
        self.markdown_context = MarkdownContext(self.document())
        self.highlighter = MarkdownHighlighter(self.document(), self.markdown_context)
        self.apply_theme(False)
        self._position_timer = QTimer(self)
        self._position_timer.setSingleShot(True)
        self._position_timer.timeout.connect(self._emit_position)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.verticalScrollBar().valueChanged.connect(self._schedule_position)
        self._update_gutter_width()

    def gutter_width(self) -> int:
        return 18 + self.fontMetrics().horizontalAdvance("9") * len(str(self.blockCount()))

    def _update_gutter_width(self, *_args) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event) -> None:
        position = self.source_position()
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.gutter.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))
        self.scroll_to_source(position)
        self._schedule_position()

    def set_wrapping(self, enabled: bool) -> None:
        position = self.source_position()
        self.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if enabled
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.scroll_to_source(position)
        self._schedule_position()

    def set_source_font(self, font: QFont) -> None:
        """Change presentation while preserving the source position and document."""
        if font == self.font():
            return
        position = self.source_position()
        self.setFont(font)
        self._update_font_metrics()
        self.scroll_to_source(position)
        self._schedule_position()

    def setDocument(self, document: QTextDocument) -> None:
        # QPlainTextEdit keeps the widget font but adopts the new document's
        # default font and tab stops unless they are applied explicitly.
        document.setDefaultFont(self.font())
        super().setDocument(document)
        self._update_font_metrics()

    def _update_font_metrics(self) -> None:
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 2)
        if hasattr(self, "gutter"):
            self.gutter.setFont(self.font())
            self._update_gutter_width()
            rect = self.contentsRect()
            self.gutter.setGeometry(
                QRect(rect.left(), rect.top(), self.gutter_width(), rect.height())
            )
            self.gutter.update()

    def source_position(self) -> float:
        block = self.firstVisibleBlock()
        if not block.isValid():
            return 0.0
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        height = max(1.0, block.layout().boundingRect().height())
        fraction = min(0.999999, max(0.0, -top / height))
        return min(self.blockCount() - 1e-7, block.blockNumber() + fraction)

    def scroll_to_source(self, position: float) -> None:
        if not math.isfinite(position):
            return
        position = max(0.0, min(self.blockCount() - 1e-7, position))
        block = self.document().findBlockByNumber(int(position))
        if not block.isValid():
            return
        self.document().documentLayout().blockBoundingRect(block)
        count = max(1, block.layout().lineCount())
        offset = min(count - 1, int((position - int(position)) * count + 1e-7))
        self.verticalScrollBar().setValue(block.firstLineNumber() + offset)

    def show_start(self) -> None:
        self.scroll_to_source(0)

    def show_end(self) -> None:
        self.scroll_to_source(self.blockCount() - 1)

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), self._gutter_background)
        painter.setPen(self._gutter_foreground)
        block = self.firstVisibleBlock()
        while block.isValid():
            top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            height = self.blockBoundingRect(block).height()
            if top > event.rect().bottom():
                break
            if block.isVisible() and top + height >= event.rect().top():
                painter.drawText(
                    0,
                    round(top),
                    self.gutter.width() - 8,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(block.blockNumber() + 1),
                )
            block = block.next()

    def _schedule_position(self, *_args) -> None:
        self._position_timer.start(0)

    def _emit_position(self) -> None:
        self.source_position_changed.emit(self.source_position())

    def replace_source(self, text: str) -> None:
        self._removed_list = None
        self.setPlainText(text)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.setTextCursor(cursor)
        self.document().setModified(False)
        self.show_start()
