"""Synchronize native selections without moving focus or changing note text."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication


class SelectionSync(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.sequence = 0
        self._sent_sequence = 0
        self._receiving = False
        self._active_pane = "source"
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._send_source)
        window.editor.selectionChanged.connect(self._source_changed)
        window.editor.cursorPositionChanged.connect(self._source_changed)
        window.preview.selection_changed.connect(self._preview_changed)
        window.preview.ready.connect(self._preview_ready)
        QApplication.instance().focusChanged.connect(self._focus_changed)

    def _in_preview(self, widget):
        view = self.window.preview.view
        return widget is not None and (widget is view or view.isAncestorOf(widget))

    def _focus_changed(self, _previous, current):
        if self._in_preview(current):
            self._active_pane = "preview"
            if self._timer.isActive():
                # Transfer pending source selection on focus-only navigation.
                # JS keeps any newer native preview gesture authoritative.
                self._timer.stop()
                self._send_source()
        elif current is self.window.editor or (
            current is not None and self.window.editor.isAncestorOf(current)
        ):
            self._active_pane = "source"
        self.window.refresh_edit_actions()

    def preview_is_active(self):
        if self.window.display_mode != "split":
            return self.window.display_mode == "preview"
        return self._active_pane == "preview"

    def _source_changed(self):
        if self._receiving:
            return
        self.sequence += 1
        self._timer.stop()
        self._active_pane = "source"
        if self.window._loading:
            return
        self._timer.start()

    def _send_source(self):
        window = self.window
        if (
            window._loading
            or getattr(window, "_closing", False)
            or window._changing_display_mode
            or window.display_mode == "source"
            or window._rendered_revision != window._revision
        ):
            return
        cursor = window.editor.textCursor()
        self._sent_sequence = self.sequence
        window.preview.set_source_selection(
            cursor.anchor(), cursor.position(), window._revision, self.sequence
        )

    def _preview_ready(self, revision):
        if revision == self.window._revision:
            self._timer.stop()
            self._send_source()

    def restore(self):
        """Reapply the current source selection after restoring a visible pane."""
        self.sequence += 1
        self._timer.stop()
        self._send_source()

    def _preview_changed(self, anchor, position, revision, sequence):
        window = self.window
        if (
            self._receiving
            or window._loading
            or getattr(window, "_closing", False)
            or window._changing_display_mode
            or window.display_mode == "source"
            or revision != window._revision
            or revision != window._rendered_revision
            or sequence
            != (self._sent_sequence if self._active_pane == "preview" else self.sequence)
            or not self._in_preview(QApplication.focusWidget())
        ):
            return
        cursor = window.editor.textCursor()
        maximum = window.editor.document().characterCount() - 1
        if anchor == position == -1:
            anchor = position = cursor.position()
        elif not (0 <= anchor <= maximum and 0 <= position <= maximum):
            return
        self._timer.stop()
        self._active_pane = "preview"
        self._receiving = True
        syncing = window._syncing_editor
        window._syncing_editor = True
        try:
            cursor.setPosition(anchor)
            cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
            window.editor.setTextCursor(cursor)
            if window.display_mode == "split":
                window.editor.ensureCursorVisible()
                window._ignore_editor_position = window.editor.source_position()
        finally:
            window._syncing_editor = syncing
            self._receiving = False
        window.refresh_edit_actions()
