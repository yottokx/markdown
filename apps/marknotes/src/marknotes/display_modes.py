"""Source/preview visibility without losing document or scroll state."""

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QPalette
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from .ui_icons import outline_icon

MODES = (("source", "ソースのみ"), ("preview", "プレビューのみ"), ("split", "ソース+プレビュー"))


class DisplayModes:
    def init_display_modes(self, view_menu):
        self.display_mode = "split"
        self._split_sizes = [690, 690]
        self._preview_position = 0.0
        self._changing_display_mode = False
        self._display_change_token = 0
        self._display_restore_revision = -1
        self.display_menu = view_menu.addMenu("表示対象")
        self.display_actions = {}
        self._display_group = QActionGroup(self)
        self._display_group.setExclusive(True)
        self.display_toolbar = QWidget(self.menuBar())
        self.display_toolbar.setObjectName("displayToolbar")
        self.display_toolbar.setFixedHeight(34)
        row = QHBoxLayout(self.display_toolbar)
        row.setContentsMargins(4, 0, 8, 0)
        row.setSpacing(2)
        self.display_buttons = {}
        for mode, title in MODES:
            action = QAction(title, self)
            action.setCheckable(True)
            action.setIconVisibleInMenu(False)
            action.triggered.connect(lambda checked=False, value=mode: self.set_display_mode(value))
            self._display_group.addAction(action)
            self.display_menu.addAction(action)
            self.display_actions[mode] = action
            button = QToolButton(self.display_toolbar)
            button.setObjectName(f"display_{mode}")
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(32, 28)
            button.setAutoRaise(True)
            button.setAccessibleName(title)
            row.addWidget(button)
            self.display_buttons[mode] = button
        self.display_actions["split"].setChecked(True)
        self.menuBar().setCornerWidget(self.display_toolbar, Qt.Corner.TopRightCorner)
        self.menuBar().setMinimumHeight(34)

    def update_display_icons(self, dark):
        color = self.palette().color(QPalette.ColorRole.WindowText)
        for mode, action in self.display_actions.items():
            action.setIcon(outline_icon(mode, color))
        hover, active = ("#303947", "#345480") if dark else ("#e3eaf4", "#c6dcff")
        self.display_toolbar.setStyleSheet(
            "QToolButton { border: none; border-radius: 4px; background: transparent; }"
            f"QToolButton:hover {{ background: {hover}; }}"
            f"QToolButton:checked {{ background: {active}; }}"
        )

    def set_display_mode(self, mode, *, persist=True):
        mode = mode if mode in self.display_actions else "split"
        self.display_actions[mode].setChecked(True)
        if persist:
            self.settings.setValue("display/mode", mode)
        if mode == self.display_mode:
            return
        old_mode = self.display_mode
        if old_mode == "split" and all(self.splitter.sizes()):
            self._split_sizes = self.splitter.sizes()
        source_position = self.editor.source_position()
        preview_position = self._preview_position
        if self.sync_action.isChecked():
            source_position = preview_position = (
                preview_position if old_mode == "preview" else source_position
            )
        self._display_change_token += 1
        token = self._display_change_token
        self._changing_display_mode = True
        self.display_mode = mode
        self._preview_position = preview_position
        self.editor.setVisible(mode != "preview")
        self.preview.setVisible(mode != "source")
        if mode == "split":
            self.splitter.setSizes(self._split_sizes)
        if mode == "preview":
            self.search.close_bar()
            self.preview.view.setFocus()
        else:
            self.editor.setFocus()
        self.refresh_edit_actions()
        # Show WebEngine first: its animation-frame layout/ready callbacks are
        # suspended while hidden. Restore after Qt has resized both viewports.
        QTimer.singleShot(
            0,
            self,
            lambda: self._restore_display_positions(token, source_position, preview_position),
        )

    def _restore_display_positions(self, token, source_position, preview_position):
        if token != self._display_change_token:
            return
        if self.display_mode != "preview":
            self.editor.scroll_to_source(source_position)
        if self.display_mode != "source":
            self._display_restore_revision = self._revision
            self.preview.restore_view(preview_position, self._revision, token)
        else:
            QTimer.singleShot(0, self, lambda: self._finish_display_change(token))

    def _display_view_restored(self, position, revision, token):
        if (
            token != self._display_change_token
            or revision != self._revision
            or not self._changing_display_mode
            or self.display_mode == "source"
        ):
            return
        self._preview_position = position
        QTimer.singleShot(0, self, lambda: self._finish_display_change(token))

    def _finish_display_change(self, token):
        if token == self._display_change_token:
            self._changing_display_mode = False
            if self.display_mode == "split" and self.sync_action.isChecked():
                self._scroll_preview_to(self.editor.source_position())
            if hasattr(self, "selection_sync"):
                self.selection_sync.restore()
            self._update_positions()

    def _scroll_preview_to(self, position):
        self._preview_position = max(0.0, min(position, self.editor.blockCount() - 1e-7))
        self.preview.scroll_to_source(self._preview_position, self._revision)

    def scroll_to_boundary(self, end):
        position = self.editor.blockCount() - 1 if end else 0
        if self.display_mode != "preview":
            self.editor.scroll_to_source(position)
        if self.display_mode == "preview" or (
            self.display_mode == "split" and self.sync_action.isChecked()
        ):
            self._scroll_preview_to(position)

    def _with_source_visible(self, callback):
        if self.display_mode == "preview":
            self.set_display_mode("split", persist=False)
            token = self._display_change_token

            def invoke_if_current():
                # Opening a different document or changing panes supersedes the
                # queued operation; never edit a new or now-hidden document.
                if token == self._display_change_token and self.display_mode != "preview":
                    callback()

            QTimer.singleShot(0, self, invoke_if_current)
        else:
            callback()

    def show_search(self, replace=False):
        self._with_source_visible(lambda: self.search.show_bar(replace))

    def navigate_search(self, backwards=False):
        self._with_source_visible(self.search.previous if backwards else self.search.next)

    def copy_active(self):
        if self.preview_edit_target():
            self.preview.page().triggerAction(QWebEnginePage.WebAction.Copy)
        else:
            self.editor.copy()

    def select_all_active(self):
        if self.preview_edit_target():
            self.preview.page().triggerAction(QWebEnginePage.WebAction.SelectAll)
        else:
            self.editor.selectAll()

    def preview_edit_target(self):
        sync = getattr(self, "selection_sync", None)
        return sync.preview_is_active() if sync is not None else self.display_mode == "preview"
