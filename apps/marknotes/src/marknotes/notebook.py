"""MarkNotes application shell: managed notes, tabs, autosave and library navigation."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QProcess, QSize, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .app import MainWindow
from .clipboard import choose_paste
from .document import SaveResult, decode_document
from .image_actions import ImageRenameDialog
from .image_rename import current_images
from .local_links import LocalLinkDialog
from .managed_assets import ManagedAssets, export_markdown, import_markdown
from .notebook_runtime import BackgroundJobs, NoteDocument, NoteSession
from .notebook_store import NotebookStore, find_match_ranges
from .notebook_theme import notebook_stylesheet
from .notebook_widgets import NotebookSidebar, NotebookTabs
from .search import qt_position
from .ui_icons import outline_icon


def default_library_root():
    return (
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        / "library"
    )


class NotebookWindow(MainWindow):
    def __init__(self, library_root=None, settings=None):
        self._notebook_ready = False
        self._sessions = {}
        self._summaries = {}
        self._order = []
        self._active = None
        self._visited = {}
        self._busy = False
        self._pending_creations = 0
        self._close_after_busy = False
        self._closing = False
        self._shutdown_done = False
        self._save_inflight = set()
        self._delete_prompt_pending = False
        self._cleanup_issues = ()
        self._cleanup_running = False
        self._after_assets = []
        self._asset_signatures = {}
        self._sidebar_composing = False
        self._search_generation = 0
        self._highlight_generation = 0
        self._search_cancel = threading.Event()
        self._library_query = ""
        self._sidebar_mode = "history"
        self._date_order = "updated"
        self._result_offset = 0
        self._snippet_limits = {}
        self._current_match = 0
        self._load_generation = 0
        self.store = NotebookStore(Path(library_root) if library_root else default_library_root())
        super().__init__(settings)
        self.session.close()
        self.session = NoteDocument(self.store.root / "notes")
        self._empty_document = self.editor.document()
        self._empty_document.setParent(self)
        self._empty_context = self.editor.markdown_context
        self._empty_highlighter = self.editor.highlighter
        self.writer = BackgroundJobs(self)
        self.reader = BackgroundJobs(self, name="marknotes-search")
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(700)
        self._autosave.timeout.connect(self.save_pending)
        self._max_save = QTimer(self)
        self._max_save.setInterval(5000)
        self._max_save.timeout.connect(self.save_pending)
        self._max_save.start()
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(1000)
        self._view_timer.timeout.connect(self._persist_view)
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.setInterval(130)
        self._highlight_timer.timeout.connect(self._apply_library_highlights)
        self.editor.cursorPositionChanged.connect(self._schedule_view)
        self.editor.source_position_changed.connect(self._schedule_view)
        self.preview.source_scrolled.connect(self._schedule_view)
        self.splitter.splitterMoved.connect(self._schedule_view)
        self.search.refreshed.connect(self._apply_library_highlights)
        self.search.closed.connect(self._apply_library_highlights)
        self._build_notebook_layout()
        self._notebook_ready = True
        self.format_label.hide()
        self.title_bar.set_file_actions([self.undo_action, self.redo_action])
        self.title_bar.title_label.hide()
        self.apply_theme(self.theme_mode, persist=False)
        QApplication.instance().installEventFilter(self)
        state = self.store.get_session()
        self._order = list(state.order)
        self._visited = dict(state.visited)
        for note_id in self._order:
            # Only tab labels and identifiers are needed at startup.
            self._summaries[note_id] = {"id": note_id, "title": "読み込み中…"}
        self._show_empty()
        self._refresh_tabs()
        self._load_tab_summaries()
        if self._order:
            self.open_note(state.active if state.active in self._order else self._order[0])
        self.refresh_library()
        self.statusBar().showMessage("ノートは自動保存されます")
        self.retry_deleted_attachments(notify=False)

    def _build_actions(self):
        super()._build_actions()
        self.note_menu = self.menuBar().actions()[0].menu()
        self.note_menu.setTitle("ノート(&N)")
        for action in list(self.note_menu.actions()):
            if action.text() in {
                "保存",
                "名前を付けて保存…",
                "文字コード・改行コード…",
                "文字コードを指定して開き直す…",
            }:
                self.note_menu.removeAction(action)
                if action is not self.save_action:
                    action.setShortcut(QKeySequence())
        self.addAction(self.save_action)
        self.new_action.setText("新しいノート")
        self.new_action.setShortcut(QKeySequence("Ctrl+T"))
        self.open_action.setText("Markdownを取り込む…")

        def add(menu, text, callback, shortcut=None):
            action = QAction(text, self)
            action.triggered.connect(callback)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            menu.addAction(action)
            return action

        self.note_menu.addSeparator()
        self.close_tab_action = add(self.note_menu, "タブを閉じる", self.close_current, "Ctrl+W")
        self.close_other_action = add(self.note_menu, "ほかのタブを閉じる", self.close_others)
        self.close_all_action = add(self.note_menu, "すべてのタブを閉じる", self.close_all)
        self.reopen_action = add(
            self.note_menu, "閉じたタブを開く", self.reopen_closed, "Ctrl+Shift+T"
        )
        self.pin_action = add(self.note_menu, "ノートをピン止め", self.toggle_pin)
        self.pin_action.setCheckable(True)
        self.delete_note_action = add(self.note_menu, "ノートを削除…", self.delete_note)
        self.markdown_export_action = add(
            self.note_menu, "Markdownと添付を書き出す…", self.export_note_dialog
        )
        add(self.edit_menu, "すべてのノートを検索…", self.show_library_search, "Ctrl+Shift+F")
        add(self.view_menu, "次のタブ", lambda: self.cycle_tab(1), "Ctrl+Tab")
        add(self.view_menu, "前のタブ", lambda: self.cycle_tab(-1), "Ctrl+Shift+Tab")
        add(self.view_menu, "サイドバー", self.toggle_sidebar, "Ctrl+B")
        add(self.insert_menu, "添付ファイル…", self.insert_attachment_dialog)
        add(
            self.insert_menu,
            "ローカルパスへのリンク…",
            lambda: self._with_source_visible(self.insert_local_link_dialog),
        )
        tools = self.menuBar().actions()[4].menu()
        for action in list(tools.actions()):
            if action.text() == "中断した画像操作を復旧…":
                tools.removeAction(action)
        tools.addSeparator()
        add(tools, "ライブラリを開く…", self.open_library_dialog)
        add(tools, "ライブラリをバックアップ…", self.backup_dialog)
        add(tools, "バックアップから復元…", self.restore_dialog)
        add(tools, "検索インデックスを再構築", self.rebuild_search)
        add(tools, "保存を再試行", self.save_pending)
        self.retry_cleanup_action = add(
            tools, "添付ファイルの削除を再試行", self.retry_deleted_attachments
        )
        self.retry_cleanup_action.setEnabled(False)
        add(tools, "現在のノートの復旧コピーを書き出す…", self.export_note_dialog)

    def _build_notebook_layout(self):
        self.tabs = NotebookTabs(self.title_bar)
        self.title_bar.set_center_widget(self.tabs, drag_width=112)
        self.tabs.activated.connect(self.open_note)
        self.tabs.new_requested.connect(self.new_document)
        self.tabs.close_requested.connect(lambda note_id: self.close_notes([note_id]))
        self.tabs.close_others_requested.connect(self.close_others)
        self.tabs.close_all_requested.connect(self.close_all)
        self.tabs.delete_requested.connect(self.delete_note)
        self.tabs.reopen_requested.connect(self.reopen_closed)
        self.tabs.order_changed.connect(self._tab_order_changed)
        self.sidebar_button = QToolButton(self.chrome_header)
        self.sidebar_button.setObjectName("notebookSidebarToggle")
        self.sidebar_button.setCheckable(True)
        self.sidebar_button.setToolTip("サイドバーを開く / 閉じる")
        self.sidebar_button.setAccessibleName("サイドバー")
        self.sidebar_button.setFixedSize(32, 30)
        self.sidebar_button.setIconSize(QSize(18, 18))
        self.sidebar_button.clicked.connect(self.toggle_sidebar)
        self.set_menu_leading_widget(self.sidebar_button)
        self.splitter.setObjectName("noteSplitter")
        self.splitter.setHandleWidth(1)
        self.search.setObjectName("noteSearchBar")
        note_area = self.takeCentralWidget()
        self.library_match_bar = QWidget()
        self.library_match_bar.setObjectName("libraryMatchBar")
        match_layout = QHBoxLayout(self.library_match_bar)
        match_layout.setContentsMargins(8, 4, 8, 4)
        self.library_match_label = QLabel()
        self.library_match_label.setTextFormat(Qt.TextFormat.PlainText)
        match_layout.addWidget(self.library_match_label, 1)
        for text, callback in (
            ("前の一致", lambda: self.navigate_library_match(-1)),
            ("次の一致", lambda: self.navigate_library_match(1)),
            ("一致をソースで表示", self.show_match_source),
            ("検索解除", lambda: self.sidebar.set_query("", emit=True)),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            match_layout.addWidget(button)
        self.library_match_bar.hide()
        note_area.layout().insertWidget(0, self.library_match_bar)
        self.workspace = QWidget(self)
        self.workspace.setObjectName("notebookWorkspace")
        layout = QHBoxLayout(self.workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._sidebar_space = QWidget()
        self._sidebar_space.setFixedWidth(0)
        layout.addWidget(self._sidebar_space)
        self.pages = QStackedWidget()
        self.pages.setObjectName("notebookPages")
        self.pages.addWidget(note_area)
        empty = QWidget()
        empty.setObjectName("notebookEmpty")
        empty_layout = QVBoxLayout(empty)
        empty_layout.setSpacing(10)
        empty_layout.addStretch()
        label = QLabel("MarkNotes")
        label.setObjectName("notebookEmptyTitle")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(label)
        hint = QLabel("思いついたことを、そのままノートに。")
        hint.setObjectName("notebookEmptyHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(hint)
        for text, callback in (
            ("新しいノート", self.new_document),
            ("過去のノートを開く", self.show_history),
            ("閉じたタブを開く", self.reopen_closed),
        ):
            button = QPushButton(text)
            button.setMaximumWidth(260)
            button.clicked.connect(callback)
            empty_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            if callback == self.reopen_closed:
                self.empty_reopen_button = button
        empty_layout.addStretch()
        self.pages.addWidget(empty)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(self.workspace)
        self.cleanup_button = QToolButton(self.statusBar())
        self.cleanup_button.setText("添付の削除待ち")
        self.cleanup_button.setAutoRaise(True)
        self.cleanup_button.setToolTip(
            "削除できていない添付があります。ファイルを閉じて、クリックして再試行してください。"
        )
        self.cleanup_button.clicked.connect(self.retry_deleted_attachments)
        self.statusBar().addPermanentWidget(self.cleanup_button)
        self.cleanup_button.hide()
        self.sidebar = NotebookSidebar(self.workspace)
        self.sidebar.hide()
        self._sidebar_fixed = self.settings.value("sidebar/fixed", False, type=bool)
        self.sidebar.set_fixed(self._sidebar_fixed)
        self.sidebar.note_requested.connect(self._result_selected)
        self.sidebar.delete_requested.connect(self.delete_note)
        self.sidebar.note_pin_requested.connect(self.set_note_pinned)
        self.sidebar.query_changed.connect(self._query_changed)
        self.sidebar.mode_changed.connect(self._sidebar_mode_changed)
        self.sidebar.date_order_changed.connect(self._date_order_changed)
        self.sidebar.pinned_changed.connect(self._sidebar_fixed_changed)
        self.sidebar.more_requested.connect(lambda: self.refresh_library(append=True))
        self.sidebar.more_matches_requested.connect(self._load_more_matches)
        if self._sidebar_fixed:
            self.sidebar.show()

    def apply_theme(self, mode: str, persist: bool = True) -> None:
        if self._notebook_ready:
            dark = mode == "dark" or (
                mode not in {"light", "dark"}
                and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
            )
            # Replacing a window stylesheet can restore Qt's saved old palette.
            # Apply it first so the base theme's palette is the final one used by
            # controls, popup menus and subsequently created dialogs.
            self.setStyleSheet(notebook_stylesheet(dark))
        super().apply_theme(mode, persist=persist)
        if not self._notebook_ready:
            return
        dark = self.preview._dark
        self.tabs.apply_theme(dark)
        self.sidebar.apply_theme(dark)
        self._apply_source_highlights()
        self.sidebar_button.setIcon(outline_icon("menu", "#e1e7ef" if dark else "#253047"))
        self._layout_sidebar()

    def _load_tab_summaries(self):
        order = list(self._order)

        def load():
            notes = []
            for note_id in order:
                try:
                    notes.append((note_id, self.store.get_summary(note_id)))
                except KeyError:
                    # A note may have been deleted while this read was queued.
                    continue
            return notes

        def done(notes, error):
            if error:
                self._storage_error(error)
                return
            for note_id, note in notes:
                if note_id in self._order and note_id not in self._sessions:
                    self._summaries[note_id] = {
                        "id": note_id,
                        "title": note.title,
                        "pinned": note.pinned,
                    }
            self._refresh_tabs()

        self.reader.submit(load, done)

    def _refresh_tabs(self):
        if not hasattr(self, "tabs"):
            return
        self.tabs.set_notes(
            [
                {
                    **self._summaries.get(note_id, {"id": note_id, "title": "ノート"}),
                    "last_active": self._visited.get(note_id, 0),
                }
                for note_id in self._order
            ]
        )
        if self._active:
            self.tabs.set_active(self._active)
        enabled = bool(self._active) and not self._busy
        for action in (
            self.close_tab_action,
            self.delete_note_action,
            self.pin_action,
            self.markdown_export_action,
            self.export_preview_action,
            self.export_html_action,
            self.export_pdf_action,
        ):
            action.setEnabled(enabled)
        self.close_all_action.setEnabled(bool(self._order) and not self._busy)
        self.close_other_action.setEnabled(len(self._order) > 1 and not self._busy)
        self.pin_action.setChecked(bool(self._summaries.get(self._active, {}).get("pinned")))
        if hasattr(self, "writer"):
            self.writer.submit(self.store.closed_history_count, self._history_count_loaded)

    def _history_count_loaded(self, count, error):
        if error:
            self._storage_error(error)
            return
        self.reopen_action.setEnabled(bool(count) and not self._busy)
        self.tabs.set_can_reopen(bool(count))
        self.empty_reopen_button.setVisible(bool(count))

    def _new_session(self, note, view):
        state = NoteSession.from_note(note, self.store.note_dir(note.id), view, self)
        self._sessions[note.id] = state
        state.document.contentsChanged.connect(lambda: self._note_changed(state))
        self._summaries[note.id] = {"id": note.id, "title": note.title, "pinned": note.pinned}
        return state

    def open_note(self, note_id, match_start=None):
        if self._busy or self._closing:
            return
        self._load_generation += 1
        generation = self._load_generation
        if note_id in self._sessions:
            self._activate(note_id, match_start)
            return
        self.save_pending()

        def load():
            return self.store.get(note_id), self.store.get_view_state(note_id)

        def done(value, error):
            if generation != self._load_generation or self._closing or self._busy:
                return
            if error:
                self._storage_error(error)
                return
            if note_id not in self._sessions:
                self._new_session(*value)
            self._activate(note_id, match_start)

        self.writer.submit(load, done)

    def _capture_view(self):
        state = self._sessions.get(self._active)
        if state is None:
            return
        cursor = self.editor.textCursor()
        state.view = {
            "mode": self.display_mode,
            "cursor": cursor.position(),
            "anchor": cursor.anchor(),
            "source": self.editor.source_position(),
            "preview": self._preview_position,
            "split": self.splitter.sizes() if self.display_mode == "split" else self._split_sizes,
        }

    def _activate(self, note_id, match_start=None):
        self._capture_view()
        self.save_pending()
        self._persist_view()
        state = self._sessions[note_id]
        self._loading = True
        self._render_timer.stop()
        self._image_timer.stop()
        self.close_image_actions()
        self._display_change_token += 1
        self._ignore_editor_position = None
        self._changing_display_mode = False
        self._active = note_id
        if note_id not in self._order:
            self._order.append(note_id)
        self._visited[note_id] = time.time()
        self.session = state.adapter
        self.base_dir = state.adapter.base_dir
        self.preview.page().managed_assets_base = self.base_dir
        self.preview.page().allow_local_links = True
        self.path = None
        self.editor.markdown_context = state.context
        self.editor.highlighter = state.highlighter
        self.editor._removed_list = None
        self.editor.setDocument(state.document)
        self.editor.apply_theme(
            self.theme_mode == "dark"
            or (
                self.theme_mode == "system"
                and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
            )
        )
        self.editor.setReadOnly(False)
        view = state.view
        self._split_sizes = view.get("split", [690, 690])
        self._preview_position = view.get("preview", 0.0)
        self._revision += 1
        self._rendered_revision = -1
        self.set_display_mode(view.get("mode", "split"), persist=False)
        self._preview_position = view.get("preview", 0.0)
        cursor = QTextCursor(state.document)
        maximum = state.document.characterCount() - 1
        cursor.setPosition(min(maximum, max(0, view.get("anchor", 0))))
        cursor.setPosition(
            min(maximum, max(0, view.get("cursor", 0))), QTextCursor.MoveMode.KeepAnchor
        )
        self.editor.setTextCursor(cursor)
        self.pages.setCurrentIndex(0)
        self._loading = False
        self._render()
        self.editor.scroll_to_source(view.get("source", 0.0))
        token = self._display_change_token
        QTimer.singleShot(0, self, lambda: self._restore_note_view(note_id, token, match_start))
        self.undo_action.setEnabled(state.document.isUndoAvailable())
        self.redo_action.setEnabled(state.document.isRedoAvailable())
        self._refresh_tabs()
        self._persist_session()
        self._update_title()
        self._update_positions()
        self.search.refresh()
        self._apply_source_highlights()

    def _restore_note_view(self, note_id, token, match_start):
        if self._active != note_id or self._display_change_token != token:
            return
        view = self._sessions[note_id].view
        self.editor.scroll_to_source(view.get("source", 0.0))
        if self.display_mode == "split":
            self.splitter.setSizes(view.get("split", [690, 690]))
        if match_start is not None:
            source = self.editor.toPlainText()
            matches = find_match_ranges(source, self._library_query)
            self._current_match = next(
                (i for i, (start, _end) in enumerate(matches) if start >= match_start), 0
            )
            line = source[:match_start].count("\n")
            if self.display_mode != "preview":
                cursor = self.editor.textCursor()
                cursor.setPosition(qt_position(source, min(match_start, len(source))))
                self.editor.setTextCursor(cursor)
                self.editor.scroll_to_source(line)
            self._preview_position = float(line)
            self._scroll_preview_to(float(line))

    def _show_empty(self):
        self._loading = True
        self._active = None
        self._revision += 1
        self._render_timer.stop()
        self.close_image_actions()
        self.editor.setDocument(self._empty_document)
        self.editor.markdown_context = self._empty_context
        self.editor.highlighter = self._empty_highlighter
        self.editor.setReadOnly(True)
        self.session = NoteDocument(self.store.root / "notes")
        self.base_dir = self.session.base_dir
        self.preview.page().managed_assets_base = None
        self.preview.page().allow_local_links = False
        self.search.close_bar()
        self.pages.setCurrentIndex(1)
        self._loading = False
        self.undo_action.setEnabled(False)
        self.redo_action.setEnabled(False)
        self._update_title()

    def new_document(self, *_args):
        if not self._notebook_ready or self._busy or self._closing:
            return
        self._load_generation += 1
        self._pending_creations += 1

        def done(note, error):
            self._pending_creations -= 1
            if error:
                self._storage_error(error)
            else:
                self._new_session(note, {})
                self._order.append(note.id)
                if not self._busy and not self._closing:
                    self._activate(note.id)
                else:
                    self._refresh_tabs()
                self.refresh_library()
            self._drain_deferred_operation()

        self.writer.submit(self.store.create, done)

    def cycle_tab(self, direction):
        if self._order and not self._busy:
            index = self._order.index(self._active) if self._active in self._order else 0
            self.open_note(self._order[(index + direction) % len(self._order)])

    def _tab_order_changed(self, order):
        if set(order) == set(self._order):
            self._order = list(order)
            self._persist_session()

    def _source_changed(self):
        if self._notebook_ready and not self._loading and self._active in self._sessions:
            self._note_changed(self._sessions[self._active])

    def _note_changed(self, state):
        if self._loading:
            return
        text = state.document.toPlainText()
        if text == state.last_text:
            return
        state.last_text = text
        state.revision += 1
        from .notebook_store import note_title

        self._summaries[state.id]["title"] = note_title(text)
        self._autosave.start()
        if state.id == self._active:
            self._revision += 1
            self._render_timer.start()
            self._highlight_timer.start()
            self._update_title()
        self.tabs.set_notes(
            [
                {**self._summaries[note_id], "last_active": self._visited.get(note_id, 0)}
                for note_id in self._order
            ]
        )
        self.statusBar().showMessage("保存待ち")

    def _update_title(self, *_args):
        title = self._summaries.get(self._active, {}).get("title", "")
        self.setWindowTitle(f"{title} — MarkNotes" if title else "MarkNotes")

    def _snapshots(self):
        return [
            (state.id, state.last_text, state.revision)
            for state in self._sessions.values()
            if state.dirty
        ]

    def save_pending(self, *_args):
        if not self._notebook_ready or self._shutdown_done or self._busy or self._closing:
            return
        self._autosave.stop()
        for note_id, text, revision in self._snapshots():
            if note_id in self._save_inflight:
                continue
            self._save_inflight.add(note_id)
            self.statusBar().showMessage("保存中…")

            def done(_value, error, note_id=note_id, revision=revision):
                self._save_inflight.discard(note_id)
                if error:
                    self._storage_error(error)
                    return
                state = self._sessions.get(note_id)
                if state:
                    state.saved_revision = max(state.saved_revision, revision)
                    if not state.dirty:
                        state.document.setModified(False)
                    else:
                        self._autosave.start()
                self._saved_status()

            self.writer.submit(
                lambda note_id=note_id, text=text, revision=revision: self.store.save(
                    note_id, text, revision
                ),
                done,
            )

    def save_document(self, *_args, **_kwargs):
        self.save_pending()
        return True

    def _saved_status(self):
        if not any(state.dirty for state in self._sessions.values()):
            self.statusBar().showMessage("保存済み")

    def _storage_error(self, error):
        self.statusBar().showMessage(
            f"保存・読み込みができません: {error}（ツールから再試行・復旧コピー）"
        )

    def _schedule_view(self, *_args):
        if self._notebook_ready and not self._loading and not self._busy:
            self._view_timer.start()

    def _persist_view(self):
        if not self._notebook_ready or self._shutdown_done or self._busy or self._closing:
            return
        self._capture_view()
        state = self._sessions.get(self._active)
        if state:
            note_id, view = state.id, dict(state.view)
            self.writer.submit(
                lambda: self.store.set_view_state(note_id, view),
                lambda _v, error: self._storage_error(error) if error else None,
            )

    def _persist_session(self):
        order, active, visited = list(self._order), self._active, dict(self._visited)
        self.writer.submit(
            lambda: self.store.set_session(order, active, visited),
            lambda _v, error: self._storage_error(error) if error else None,
        )

    def set_display_mode(self, mode, *, persist=True):
        super().set_display_mode(mode, persist=False)
        if self._notebook_ready and not self._loading:
            self._schedule_view()

    def _set_operation_busy(self, busy):
        self._busy = busy
        if busy:
            self._view_timer.stop()
        self.editor.setReadOnly(busy or not bool(self._active))
        self.tabs.setEnabled(not busy)
        self.sidebar.setEnabled(not busy)
        self.note_menu.setEnabled(not busy)
        self.edit_menu.setEnabled(not busy)
        self.insert_menu.setEnabled(not busy and bool(self._active))
        self.undo_action.setEnabled(not busy and self.editor.document().isUndoAvailable())
        self.redo_action.setEnabled(not busy and self.editor.document().isRedoAvailable())

    def _drain_deferred_operation(self):
        if self._pending_creations or any(
            state.pending_assets for state in self._sessions.values()
        ):
            return
        callbacks, self._after_assets = self._after_assets, []
        for callback in callbacks:
            callback()

    def _flush(self, operation, callback, *, session_operation=False, discard_ids=()):
        if self._busy:
            return
        self._load_generation += 1
        self._set_operation_busy(True)

        def start():
            # Freeze commands immediately, but capture only after pending imports
            # have published their text into the original GUI-owned documents.
            self._capture_view()
            snapshots = [row for row in self._snapshots() if row[0] not in discard_ids]
            views = [
                (state.id, dict(state.view))
                for state in self._sessions.values()
                if state.id not in discard_ids
            ]
            session = (list(self._order), self._active, dict(self._visited))
            self.statusBar().showMessage("保存中…")

            def work():
                for note_id, text, revision in snapshots:
                    self.store.save(note_id, text, revision)
                for note_id, view in views:
                    self.store.set_view_state(note_id, view)
                return operation(*session) if session_operation else operation()

            def done(value, error):
                self._set_operation_busy(False)
                if error:
                    self._closing = False
                    self._storage_error(error)
                    callback(None, error)
                    return
                for note_id, _text, revision in snapshots:
                    state = self._sessions.get(note_id)
                    if state:
                        state.saved_revision = max(state.saved_revision, revision)
                self._saved_status()
                callback(value, None)
                if self._close_after_busy and not self._shutdown_done:
                    self._close_after_busy = False
                    self.close()

            self.writer.submit(work, done)

        if self._pending_creations or any(
            state.pending_assets for state in self._sessions.values()
        ):
            self.statusBar().showMessage("取り込みの完了後に保存して続行します…")
            self._after_assets = [start]
        else:
            start()

    def close_current(self):
        if self._active:
            self.close_notes([self._active])

    def close_all(self):
        self.close_notes(list(self._order))

    def close_others(self, note_id=None):
        keep = note_id if isinstance(note_id, str) else self._active
        self.close_notes([value for value in self._order if value != keep])

    def close_notes(self, ids):
        if self._busy or self._closing or not ids:
            return
        ids = [value for value in ids if value in self._order]
        self._load_generation += 1

        def done(state, error):
            if error:
                return
            # Detach the active QTextDocument before releasing any closed one.
            self._show_empty()
            for note_id in ids:
                self._summaries.pop(note_id, None)
                old = self._sessions.pop(note_id, None)
                if old:
                    old.document.deleteLater()
            self._order = list(state.order)
            self._visited = dict(state.visited)
            self._active = None
            self._refresh_tabs()
            if self._order:
                self.open_note(state.active or self._order[0])
            self.refresh_library()

        self._flush(
            lambda order, active, _visited: self.store.close_tabs(ids, order, active),
            done,
            session_operation=True,
        )

    def delete_note(self, note_id=None):
        """Ask about the selected note without opening a closed library result."""
        if self._busy or self._closing or self._delete_prompt_pending:
            return
        note_id = note_id if isinstance(note_id, str) else self._active
        if not note_id:
            return
        self._delete_prompt_pending = True

        def loaded(note, error):
            try:
                if self._busy or self._closing or self._shutdown_done:
                    return
                if error:
                    self._storage_error(error)
                    self.refresh_library()
                    return
                title = self._summaries.get(note_id, {}).get("title", note.title)
                if self._confirm_note_deletion(title):
                    self._delete_notes([note_id])
            finally:
                self._delete_prompt_pending = False

        self.writer.submit(lambda: self.store.get_summary(note_id), loaded)

    def _confirm_note_deletion(self, title):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("ノートを削除")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"「{title}」を削除しますか？")
        dialog.setInformativeText("本文と添付ファイルを削除します。この操作は取り消せません。")
        delete = dialog.addButton("削除", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        try:
            dialog.exec()
            return dialog.clickedButton() is delete
        finally:
            dialog.deleteLater()

    def _delete_notes(self, ids):
        if self._busy or self._closing or not ids:
            return
        ids = tuple(dict.fromkeys(ids))
        self._search_generation += 1
        self._search_cancel.set()
        self.close_image_actions()

        def done(result, error):
            if error:
                self.sync_image_watches()
                self.refresh_library()
                return
            deleted = set(result.deleted_ids)
            active_deleted = self._active in deleted
            if active_deleted:
                self._show_empty()
                self.preview.set_document("", 1, self.base_dir, self._revision)
            for note_id in deleted:
                self._summaries.pop(note_id, None)
                self._snippet_limits.pop(note_id, None)
                self._save_inflight.discard(note_id)
                old = self._sessions.pop(note_id, None)
                if old:
                    old.document.clearUndoRedoStacks()
                    old.document.deleteLater()
            self._asset_signatures = {
                path: signature
                for path, signature in self._asset_signatures.items()
                if signature[0] not in deleted
            }
            self._order = list(result.session.order)
            self._visited = dict(result.session.visited)
            self._image_timer.stop()
            self._refresh_tabs()
            if active_deleted and self._order:
                self.open_note(result.session.active or self._order[0])
            self.sync_image_watches()
            self.sidebar.set_results([])
            self.refresh_library()
            self._cleanup_finished(result.pending_cleanup, None, notify=False)
            self.statusBar().showMessage(
                "ノートを削除しました。添付の削除待ちがあります。"
                if result.pending_cleanup
                else "ノートを削除しました"
            )

        self._flush(
            lambda order, active, visited: self.store.delete_notes(ids, order, active, visited),
            done,
            session_operation=True,
            discard_ids=ids,
        )

    def retry_deleted_attachments(self, *_args, notify=True):
        if self._busy or self._closing or self._cleanup_running:
            return
        self._cleanup_running = True
        self.cleanup_button.setEnabled(False)
        self.retry_cleanup_action.setEnabled(False)
        self.writer.submit(
            self.store.retry_cleanup,
            lambda issues, error: self._cleanup_finished(issues, error, notify=notify),
        )

    def _cleanup_finished(self, issues, error, *, notify):
        self._cleanup_running = False
        if error:
            self._storage_error(error)
        else:
            self._cleanup_issues = tuple(issues)
            if notify or issues:
                self.statusBar().showMessage(
                    "添付を削除できませんでした。ファイルを閉じて再試行してください。"
                    if issues
                    else "添付ファイルの削除が完了しました"
                )
        pending = bool(self._cleanup_issues) or bool(error)
        self.cleanup_button.setVisible(pending)
        self.cleanup_button.setEnabled(pending)
        self.retry_cleanup_action.setEnabled(pending)

    def reopen_closed(self):
        if self._busy or self._closing:
            return

        def operation(order, active, _visited):
            # Validate every referenced note in the store transaction before consuming history.
            return self.store.reopen_closed(order, active)

        def done(state, error):
            if error or state is None:
                return
            self._order = list(state.order)
            for note_id in self._order:
                self._summaries.setdefault(note_id, {"id": note_id, "title": "ノート"})
            self._refresh_tabs()
            self._load_tab_summaries()
            if state.active:
                self.open_note(state.active)

        self._flush(operation, done, session_operation=True)

    def toggle_pin(self, *_args):
        if not self._active or self._busy:
            return
        note_id = self._active
        pinned = not bool(self._summaries[note_id].get("pinned"))
        self.set_note_pinned(note_id, pinned)

    def set_note_pinned(self, note_id: str, pinned: bool):
        if self._busy or self._shutdown_done or self._closing:
            return

        def done(_value, error):
            if error:
                self._storage_error(error)
                return
            if note_id in self._summaries:
                self._summaries[note_id]["pinned"] = pinned
            self._refresh_tabs()
            self.refresh_library()

        self.writer.submit(lambda: self.store.set_pinned(note_id, pinned), done)

    def toggle_sidebar(self):
        if not hasattr(self, "sidebar"):
            return
        self.sidebar.setVisible(not self.sidebar.isVisible())
        self._layout_sidebar()
        if self.sidebar.isVisible():
            self.refresh_library()

    def show_history(self):
        self.sidebar.set_mode("history")
        self.sidebar.show()
        self._layout_sidebar()
        self.refresh_library()

    def show_library_search(self):
        self.sidebar.set_mode("search")
        self.sidebar.show()
        self._layout_sidebar()
        self.sidebar.search_edit.setFocus()

    def _sidebar_mode_changed(self, mode):
        self._sidebar_mode = mode
        self.refresh_library()

    def _date_order_changed(self, order):
        self._date_order = order
        self.refresh_library()

    def _sidebar_fixed_changed(self, fixed):
        self._sidebar_fixed = fixed
        self.settings.setValue("sidebar/fixed", fixed)
        self._layout_sidebar()

    def _layout_sidebar(self):
        if not hasattr(self, "sidebar"):
            return
        width = min(340, max(200, self.workspace.width() - 80))
        fixed = self._sidebar_fixed and self.workspace.width() >= width + 700
        self._sidebar_space.setFixedWidth(width if fixed and self.sidebar.isVisible() else 0)
        self.sidebar.setGeometry(0, 0, width, self.workspace.height())
        self.sidebar_button.setChecked(self.sidebar.isVisible())
        self.sidebar.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_sidebar()

    def eventFilter(self, watched, event):
        if not self._notebook_ready or self._shutdown_done or self._busy or self._closing:
            return False
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self.save_pending()
            self._persist_view()
        if event.type() == QEvent.Type.InputMethod and watched is self.sidebar.search_edit:
            self._sidebar_composing = bool(event.preeditString())
        if self.sidebar.isVisible():
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                composing = self.editor._composing or self._sidebar_composing
                if not composing and self.isActiveWindow():
                    self.sidebar.hide()
                    self._layout_sidebar()
                    self.editor.setFocus()
                    return True
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and not self._sidebar_space.width()
                and hasattr(event, "globalPosition")
            ):
                point = event.globalPosition().toPoint()
                inside = self.sidebar.rect().contains(self.sidebar.mapFromGlobal(point))
                toggle = self.sidebar_button.rect().contains(
                    self.sidebar_button.mapFromGlobal(point)
                )
                if not inside and not toggle and self.isActiveWindow():
                    self.sidebar.hide()
                    self._layout_sidebar()
        return super().eventFilter(watched, event)

    def _query_changed(self, query):
        self._library_query = query
        self._snippet_limits.clear()
        self._current_match = 0
        self.library_match_bar.setVisible(bool(query))
        self._apply_library_highlights()
        self.refresh_library()

    def refresh_library(self, *, append=False):
        if not self._notebook_ready or self._shutdown_done or self._busy or self._closing:
            return
        self._search_generation += 1
        generation = self._search_generation
        self._search_cancel.set()
        cancel = self._search_cancel = threading.Event()
        self._result_offset = self._result_offset if append else 0
        offset = self._result_offset
        mode, query, order = self._sidebar_mode, self._library_query, self._date_order
        self.sidebar.set_busy(True)
        snapshots = self._snapshots() if mode == "search" else []

        def run_query():
            if mode == "search":
                return self.store.search(query, limit=50, offset=offset, cancel=cancel.is_set)
            return self.store.list_notes(
                order=order, pinned=mode == "pinned", limit=50, offset=offset
            )

        def done(results, error):
            if generation != self._search_generation or self._shutdown_done:
                return
            self.sidebar.set_busy(False)
            if error:
                self.sidebar.set_error(str(error))
                return
            rows = []
            for result in results:
                note = result.note if mode == "search" else result
                row = asdict(note)
                row["content_updated_at"] = note.updated_at
                if mode == "search":
                    row["title_matches"] = find_match_ranges(note.title, query)
                    row["snippets"] = [
                        {"text": item.text, "start": item.start, "matches": item.ranges}
                        for item in result.snippets
                    ]
                    row["total_matches"] = result.match_count
                rows.append(row)
            self._result_offset = offset + len(results)
            self.sidebar.set_results(rows, append=append, has_more=len(results) == 50)

        def after_saved(_value, error):
            if generation != self._search_generation:
                return
            if error:
                done(None, error)
            else:
                self.reader.submit(run_query, done)

        if snapshots:

            def save_first():
                for note_id, text, revision in snapshots:
                    self.store.save(note_id, text, revision)

            self.writer.submit(save_first, after_saved)
        else:
            after_saved(None, None)

    def _load_more_matches(self, note_id):
        query, generation = self._library_query, self._search_generation
        limit = self._snippet_limits.get(note_id, 3) + 10

        def work():
            from .notebook_store import make_snippets

            body = self.store.get(note_id).body
            matches = find_match_ranges(body, query)
            return make_snippets(body, matches, limit=limit), len(matches)

        def done(value, error):
            if generation != self._search_generation:
                return
            if error:
                self.sidebar.set_error(str(error))
                return
            snippets, count = value
            self._snippet_limits[note_id] = limit
            self.sidebar.set_note_snippets(
                note_id,
                [
                    {"text": item.text, "start": item.start, "matches": item.ranges}
                    for item in snippets
                ],
                count,
            )

        self.reader.submit(work, done)

    def navigate_library_match(self, direction):
        if not self._active or not self._library_query:
            return
        matches = find_match_ranges(self.editor.toPlainText(), self._library_query)
        if matches:
            self._current_match = (self._current_match + direction) % len(matches)
            self._restore_note_view(
                self._active, self._display_change_token, matches[self._current_match][0]
            )

    def show_match_source(self):
        if not self._active or not self._library_query:
            return
        matches = find_match_ranges(self.editor.toPlainText(), self._library_query)
        if matches:
            self.set_display_mode("source")
            self._restore_note_view(
                self._active,
                self._display_change_token,
                matches[min(self._current_match, len(matches) - 1)][0],
            )

    def _result_selected(self, note_id, match_start):
        self.open_note(note_id, match_start)
        if not self._sidebar_space.width():
            self.sidebar.hide()
            self._layout_sidebar()

    def _apply_source_highlights(self):
        if not self._notebook_ready or self.search.isVisible():
            return
        selections = []
        if self._active and self._library_query:
            text = self.editor.toPlainText()
            for start, end in find_match_ranges(text, self._library_query)[:2000]:
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(self.editor.document())
                selection.cursor.setPosition(qt_position(text, start))
                selection.cursor.setPosition(
                    qt_position(text, end), QTextCursor.MoveMode.KeepAnchor
                )
                selection.format.setBackground(QColor("#f5d97a"))
                selection.format.setForeground(QColor("#252018"))
                selections.append(selection)
        self.editor.setExtraSelections(selections)

    def _apply_library_highlights(self):
        """Use note-local search in both panes while its bar is open, otherwise library search."""
        if not self._notebook_ready or self._shutdown_done or self._closing:
            return
        self._apply_source_highlights()
        matches = (
            find_match_ranges(self.editor.toPlainText(), self._library_query)
            if self._active
            else ()
        )
        self.library_match_label.setText(f"検索: {self._library_query} · {len(matches)} 件一致")
        from .notebook_highlight import preview_highlight_script, preview_search_ranges_script

        self._highlight_generation += 1
        generation = self._highlight_generation
        revision = self._revision
        note_id = self._active or ""
        local_search = bool(self._active and self.search.isVisible())
        self.preview.page().runJavaScript(
            preview_highlight_script(
                "" if local_search else self._library_query,
                revision,
                generation=generation,
                note_id=note_id,
            )
        )
        self.preview.page().runJavaScript(
            preview_search_ranges_script([], revision, generation=generation, note_id=note_id)
        )
        if local_search:
            self._apply_note_search_highlights(revision, generation, note_id)

    def _apply_note_search_highlights(self, revision, generation, note_id):
        from .notebook_highlight import (
            preview_search_match_ranges,
            preview_search_ranges_script,
            preview_search_text_script,
        )

        try:
            pattern = self.search.pattern()
        except re.error:
            return
        if pattern is None:
            return

        def collected(raw):
            if (
                self._shutdown_done
                or self._closing
                or generation != self._highlight_generation
                or revision != self._revision
                or note_id != self._active
                or not self.search.isVisible()
            ):
                return
            try:
                result = json.loads(raw) if isinstance(raw, str) else None
            except (TypeError, ValueError):
                return
            if not isinstance(result, dict) or not result.get("applied"):
                return
            ranges = preview_search_match_ranges(pattern, result.get("runs", []))
            self.preview.page().runJavaScript(
                preview_search_ranges_script(
                    ranges, revision, generation=generation, note_id=note_id
                )
            )

        script = (
            preview_search_text_script(revision, generation=generation, note_id=note_id)
            .strip()
            .removesuffix(";")
        )
        self.preview.page().runJavaScript(f"JSON.stringify({script})", collected)

    def _preview_ready(self, revision):
        super()._preview_ready(revision)
        if self._notebook_ready and revision == self._revision:
            self._apply_library_highlights()

    def paste_mime(self, mime):
        if not self._active or self._busy:
            return
        if self.editor.in_code_block() and mime.hasText():
            self.insert_text(mime.text())
            return
        if mime.hasUrls() and any(url.isLocalFile() for url in mime.urls()):
            remote = [url.toString() for url in mime.urls() if not url.isLocalFile()]
            self.attach_files([Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()])
            if remote:
                self.insert_text("\n".join(remote) + "\n")
            return
        decision = choose_paste(mime, in_code=self.editor.in_code_block())
        if decision.kind == "image":
            data = mime.imageData()
            image = data.toImage() if hasattr(data, "toImage") else QImage(data)
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "PNG"):
                self.statusBar().showMessage("画像を読み取れませんでした")
                return
            data = bytes(buffer.data())
            self._attach(lambda manager: manager.save_bytes(data, ".png", "画像"))
        else:
            self.insert_text(decision.text)

    def insert_local_link_dialog(self):
        if not self._active or self._busy or self.editor.isReadOnly():
            return
        note_id = self._active
        cursor = QTextCursor(self.editor.textCursor())
        dialog = LocalLinkDialog(
            self,
            label=cursor.selectedText().replace("\u2029", " "),
            directory=self.dialog_directory(),
        )
        try:
            if (
                dialog.exec() == QDialog.DialogCode.Accepted
                and self._active == note_id
                and not self._busy
                and not self._closing
                and not self.editor.isReadOnly()
            ):
                self.editor.setTextCursor(cursor)
                self.insert_text(dialog.markdown)
        finally:
            dialog.deleteLater()

    def insert_attachment_dialog(self):
        if not self._active:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "ファイルを添付", str(self.dialog_directory())
        )
        if paths:
            self.attach_files([Path(value) for value in paths])

    def insert_image_file(self):
        self.insert_attachment_dialog()

    def attach_files(self, paths):
        self._attach(lambda manager: manager.import_files(paths))

    def _attach(self, operation):
        if not self._active or self._busy:
            return
        state = self._sessions[self._active]
        cursor = QTextCursor(self.editor.textCursor())
        state.pending_assets += 1
        manager = ManagedAssets(state.adapter.base_dir)
        self.statusBar().showMessage("添付を取り込み中…")

        def work():
            with self.store.mutation_lock:
                result = operation(manager)
                self._register_assets(state.id, getattr(result, "assets", (result,)))
                return result

        def done(result, error):
            state.pending_assets -= 1
            if error:
                self._storage_error(error)
            else:
                if result.markdown:
                    cursor.beginEditBlock()
                    cursor.insertText(result.markdown)
                    cursor.endEditBlock()
                    if self._active == state.id:
                        self.editor.setTextCursor(cursor)
                errors = getattr(result, "errors", ())
                if errors:
                    self.statusBar().showMessage(
                        "一部の添付を取り込めませんでした: "
                        + "; ".join(f"{item.path.name}: {item.message}" for item in errors)
                    )
                self.save_pending()
            self.sync_image_watches()
            self._drain_deferred_operation()

        self.writer.submit(work, done)

    def _register_assets(self, note_id, assets):
        for asset in assets:
            self.store.register_attachment(
                note_id,
                asset.relative_path,
                asset.original_name,
                "image" if asset.is_image else "file",
                asset.size,
                asset.asset_id,
            )

    def sync_image_watches(self):
        if not self._notebook_ready:
            return
        paths = set()
        for state in self._sessions.values():
            assets = state.adapter.base_dir / "assets"
            if assets.is_dir():
                paths.add(str(assets))
            for destination, path, _managed in state.adapter.image_reference_states(
                state.last_text
            ):
                if destination.is_image and path and path.is_file():
                    paths.add(str(path))
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    self._asset_signatures.setdefault(
                        path, (state.id, stat.st_mtime_ns, stat.st_size)
                    )
        previous = set(self._image_watcher.files() + self._image_watcher.directories())
        if previous - paths:
            self._image_watcher.removePaths(list(previous - paths))
        if paths - previous:
            self._image_watcher.addPaths(list(paths - previous))

    def _images_changed(self):
        if not self._notebook_ready or self._busy or self._closing or self._shutdown_done:
            return
        changed = set()
        for path, (note_id, old_time, old_size) in list(self._asset_signatures.items()):
            if not path.is_file():
                continue  # Atomic replacement may temporarily remove the path.
            try:
                stat = path.stat()
            except OSError:
                continue
            if (old_time, old_size) != (stat.st_mtime_ns, stat.st_size):
                changed.add(note_id)
                self._asset_signatures[path] = (note_id, stat.st_mtime_ns, stat.st_size)
        for note_id in changed:
            self.writer.submit(
                lambda note_id=note_id: self.store.touch(note_id),
                lambda _v, error: self._storage_error(error) if error else self.refresh_library(),
            )
        self._image_generation += 1
        self._revision += 1
        self._render_timer.start()
        self.sync_image_watches()

    def _review_image_rename(self, image=None):
        if not self._active or self._busy:
            return False
        if not current_images(self.session, self.editor.toPlainText()):
            self.statusBar().showMessage("このノートに参照中の画像がありません")
            return False
        dialog = ImageRenameDialog(self.session, self.editor.toPlainText(), self, image)
        if not dialog.exec():
            return False
        plan = dialog.plan
        state = self._sessions[self._active]
        if state.last_text != plan.source_text:
            self.statusBar().showMessage("ノートが更新されました。改名一覧を開き直してください")
            return False

        def operation():
            with self.store.mutation_lock:
                for entry in plan.entries:
                    if entry.changed:
                        # Keep original assets for Undo and interrupted saves.
                        asset = ManagedAssets(state.adapter.base_dir).copy_named(
                            entry.source, entry.target.name
                        )
                        self._register_assets(state.id, (asset,))
                return SaveResult(plan.text, None, True)

        def done(result, error):
            if error:
                self._storage_error(error)
            elif self._active == state.id and state.last_text == plan.source_text:
                self._apply_document_result(result, saved=False)
                self.save_pending()
            else:
                self.statusBar().showMessage("ノートが更新されたため、参照変更を中止しました")

        self.writer.submit(operation, done)
        return True

    def open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Markdownを取り込む",
            str(self.dialog_directory()),
            "Markdown (*.md *.markdown);;すべて (*)",
        )
        for path in paths:
            self.open_path(Path(path))

    def open_path(self, path, encoding=None):
        if self._busy or self._closing:
            return False
        self._pending_creations += 1
        path = Path(path)

        def work():
            source = decode_document(path.read_bytes(), encoding).text
            note = self.store.create()
            with self.store.mutation_lock:
                imported = import_markdown(
                    source, path.parent, ManagedAssets(self.store.note_dir(note.id))
                )
                self._register_assets(note.id, imported.assets)
                self.store.save(note.id, imported.body, note.revision + 1)
            return self.store.get(note.id), imported.warnings

        def done(result, error):
            self._pending_creations -= 1
            if error:
                self._storage_error(error)
            else:
                note, warnings = result
                self._new_session(note, {})
                self._order.append(note.id)
                if not self._busy and not self._closing:
                    self._activate(note.id)
                self.refresh_library()
                if warnings:
                    self.statusBar().showMessage("取り込み時の確認事項: " + "; ".join(warnings))
            self._drain_deferred_operation()

        self.writer.submit(work, done)
        return True

    def export_note_dialog(self):
        if not self._active:
            return
        parent = QFileDialog.getExistingDirectory(
            self, "書き出し先フォルダ", str(self.dialog_directory())
        )
        if not parent:
            return
        state = self._sessions[self._active]
        target = Path(parent) / f"MarkNotes-{state.id[:8]}-{time.strftime('%Y%m%d-%H%M%S')}"
        body, base = state.last_text, state.adapter.base_dir
        self.writer.submit(lambda: export_markdown(body, base, target), self._export_done)

    def _export_done(self, result, error):
        if error:
            self._storage_error(error)
        else:
            self.statusBar().showMessage(f"書き出しました: {result}")

    def backup_dialog(self):
        parent = QFileDialog.getExistingDirectory(
            self, "バックアップ先フォルダ", str(self.dialog_directory())
        )
        if parent:
            target = Path(parent) / time.strftime("MarkNotes-backup-%Y%m%d-%H%M%S")
            self._flush(lambda: self.store.backup(target), self._export_done)

    def restore_dialog(self):
        backup = QFileDialog.getExistingDirectory(self, "復元するバックアップ")
        if not backup:
            return
        target = default_library_root().parent / time.strftime("restored-%Y%m%d-%H%M%S")

        def done(_result, error):
            if error:
                self._storage_error(error)
                return
            self._switch_library(target)

        self.writer.submit(lambda: NotebookStore.restore_backup(Path(backup), target), done)

    def open_library_dialog(self):
        target = QFileDialog.getExistingDirectory(
            self, "ライブラリを開く", str(self.store.root.parent)
        )
        if not target:
            return
        path = Path(target)
        if not (path / "library.sqlite3").is_file():
            QMessageBox.warning(
                self, "ライブラリを開けません", "MarkNotesのライブラリを選択してください。"
            )
            return
        self._switch_library(path)

    def _switch_library(self, target):
        if target.resolve() == self.store.root or self._busy or self._closing:
            return

        def done(_value, error):
            if error:
                return
            arguments = ["--library", str(target)]
            if not getattr(sys, "frozen", False):
                arguments = ["-m", "marknotes", *arguments]
            started, _pid = QProcess.startDetached(sys.executable, arguments)
            if not started:
                self.statusBar().showMessage("ライブラリを開けませんでした")
                return
            self.settings.setValue("library/path", str(target))
            self.settings.sync()
            self.close()

        self._flush(lambda: None, done)

    def configure_settings(self):
        from .notebook_settings import NotebookSettingsDialog

        dialog = NotebookSettingsDialog(self.settings, self.store.root, self)
        result = dialog.exec()
        dialog.deleteLater()
        if result:
            self.statusBar().showMessage("設定を保存しました", 3500)
        return bool(result)

    def rebuild_search(self):
        self.writer.submit(
            self.store.rebuild_index,
            lambda _v, error: self._storage_error(error) if error else self.refresh_library(),
        )

    def closeEvent(self, event):
        if not self._notebook_ready:
            return super().closeEvent(event)
        if self._shutdown_done:
            event.accept()
            return
        event.ignore()
        if self._closing:
            return
        if self._busy:
            self._close_after_busy = True
            return
        self._closing = True

        def done(_value, error):
            if error:
                return
            self._shutdown_done = True
            self._search_cancel.set()
            for timer in (
                self._autosave,
                self._max_save,
                self._view_timer,
                self._highlight_timer,
                self._render_timer,
                self.search._timer,
            ):
                timer.stop()
            self.close_image_actions()
            QApplication.instance().removeEventFilter(self)
            self.writer.shutdown()
            self.reader.shutdown()
            self.close()

        self._flush(
            lambda order, active, visited: self.store.finish_session(order, active, visited),
            done,
            session_operation=True,
        )
