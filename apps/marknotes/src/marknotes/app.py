"""Native Markdown editing, managed paste, file formats and synchronized preview."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from marknotes.application_icon import application_icon, set_windows_app_user_model_id
from marknotes.display_modes import DisplayModes
from marknotes.document import DocumentSession
from marknotes.export_dialog import ExportActions
from marknotes.file_actions import FileActions
from marknotes.interactions import EditingActions, InteractiveEditor
from marknotes.localization import install_japanese_translation
from marknotes.menu_theme import apply_window_menu_theme
from marknotes.preview import PreviewPane
from marknotes.rendering import render_markdown
from marknotes.search import SearchBar
from marknotes.theme import palette
from marknotes.ui_icons import outline_icon
from marknotes.window_chrome import ChromeMainWindow

RESOURCE_DIR = Path(__file__).resolve().parent / "resources"


class MainWindow(DisplayModes, FileActions, EditingActions, ExportActions, ChromeMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        set_windows_app_user_model_id()
        super().__init__()
        # Fusion honors palettes consistently; Windows' native control style
        # otherwise paints light text fields even under a dark window palette.
        application = QApplication.instance()
        application.setWindowIcon(application_icon())
        self.setWindowIcon(application.windowIcon())
        install_japanese_translation(application)
        if application.style().objectName().lower() != "fusion":
            application.setStyle("Fusion")
        self.resize(1380, 880)
        self.session = DocumentSession()
        self.path: Path | None = None
        self.base_dir = self.session.base_dir
        self.settings = settings or QSettings("MarkNotes", "MarkNotes")
        self.theme_mode = str(self.settings.value("theme", "system"))
        self._pending_encoding = None
        self._pending_newline = None
        self.apply_new_document_defaults()
        self._file_watcher = QFileSystemWatcher(self)
        self._file_watcher.fileChanged.connect(self._external_file_changed)
        self._file_watcher.directoryChanged.connect(self._external_file_changed)
        self._revision = 0
        self._rendered_revision = -1
        self._syncing_editor = False
        self._ignore_editor_position: float | None = None
        self._loading = False
        self.editor = InteractiveEditor(self)
        self.preview = PreviewPane()
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.editor.setMinimumWidth(180)
        self.preview.setMinimumWidth(180)
        self.splitter.addWidget(self.editor)
        self.splitter.addWidget(self.preview)
        self.splitter.setSizes([690, 690])
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.search = SearchBar(self.editor, self)
        self.search.navigated.connect(lambda: self._editor_scrolled(self.editor.source_position()))
        central_layout.addWidget(self.search)
        central_layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(180)
        self._render_timer.timeout.connect(self._render)
        self._new_undo_command = False
        self._undo_steps = self.editor.document().availableUndoSteps()
        self.editor.document().undoCommandAdded.connect(self._record_new_undo_command)
        self.editor.textChanged.connect(self._source_changed)
        self.editor.document().modificationChanged.connect(self._update_title)
        self.editor.cursorPositionChanged.connect(self._update_positions)
        self.editor.source_position_changed.connect(self._editor_scrolled)
        self.preview.source_scrolled.connect(self._preview_scrolled)
        self.preview.ready.connect(self._preview_ready)
        self.preview.view_restored.connect(self._display_view_restored)
        self.preview.error.connect(self._preview_error)
        self.init_image_actions()
        self._build_actions()
        self.title_bar.set_file_actions(
            [
                self.new_action,
                self.open_action,
                self.save_action,
                self.undo_action,
                self.redo_action,
            ]
        )
        self.position_label = QLabel()
        self.statusBar().addPermanentWidget(self.position_label)
        self.format_label = QLabel()
        self.format_label.setToolTip("ファイルメニューから文字コード・改行コードを変更できます")
        self.statusBar().addPermanentWidget(self.format_label)
        self.statusBar().showMessage("準備完了")
        QApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        self.apply_theme(self.theme_mode, persist=False)
        self.set_display_mode(str(self.settings.value("display/mode", "split")), persist=False)
        self._update_title()
        self._update_positions()

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("ファイル(&F)")
        self.edit_menu = edit_menu = self.menuBar().addMenu("編集(&E)")
        self.insert_menu = insert_menu = self.menuBar().addMenu("挿入(&I)")
        self.view_menu = view_menu = self.menuBar().addMenu("表示(&V)")
        tools_menu = self.menuBar().addMenu("ツール(&T)")
        self.init_display_modes(view_menu)
        view_menu.addSeparator()

        def action(menu, text, callback, shortcut=None, source_context=False):
            result = QAction(text, self)
            result.setProperty("sourceContext", source_context)
            result.triggered.connect(callback)
            if shortcut:
                result.setShortcut(shortcut)
            menu.addAction(result)
            return result

        self.new_action = action(file_menu, "新規", self.new_document, QKeySequence.StandardKey.New)
        self.open_action = action(
            file_menu, "開く…", self.open_dialog, QKeySequence.StandardKey.Open
        )
        self.save_action = action(
            file_menu, "保存", lambda: self.save_document(), QKeySequence.StandardKey.Save
        )
        action(file_menu, "名前を付けて保存…", self.save_as_dialog, QKeySequence.StandardKey.SaveAs)
        file_menu.addSeparator()
        self.export_preview_action = action(
            file_menu, "出力プレビュー…", lambda: self.show_export_dialog()
        )
        self.export_html_action = action(
            file_menu, "HTMLを出力…", lambda: self.show_export_dialog("html")
        )
        self.export_pdf_action = action(
            file_menu, "PDFを出力…", lambda: self.show_export_dialog("pdf")
        )
        file_menu.addSeparator()
        action(file_menu, "文字コード・改行コード…", self.change_format)
        action(file_menu, "文字コードを指定して開き直す…", self.reopen_encoding)
        file_menu.addSeparator()
        self.settings_action = action(file_menu, "設定…", self.configure_settings)
        file_menu.addSeparator()
        action(file_menu, "終了", self.close)
        self.undo_action = action(
            edit_menu,
            "元に戻す",
            lambda: self._with_source_visible(self.editor.undo),
            QKeySequence.StandardKey.Undo,
            source_context=True,
        )
        self.redo_action = action(
            edit_menu,
            "やり直す",
            lambda: self._with_source_visible(self.editor.redo),
            QKeySequence.StandardKey.Redo,
            source_context=True,
        )
        self.undo_action.setEnabled(False)
        self.redo_action.setEnabled(False)
        self.editor.undoAvailable.connect(self.undo_action.setEnabled)
        self.editor.redoAvailable.connect(self.redo_action.setEnabled)
        edit_menu.addSeparator()
        self.cut_action = action(
            edit_menu,
            "切り取り",
            self.editor.cut,
            QKeySequence.StandardKey.Cut,
            source_context=True,
        )
        self.copy_action = action(
            edit_menu,
            "コピー",
            self.copy_active,
            QKeySequence.StandardKey.Copy,
            source_context=True,
        )
        self.paste_action = action(
            edit_menu,
            "貼り付け",
            lambda: self._with_source_visible(self.editor.paste),
            QKeySequence.StandardKey.Paste,
            source_context=True,
        )
        self.paste_format_menu = edit_menu.addMenu("形式を指定して貼り付け")
        self.paste_format_menu.menuAction().setProperty("sourceContext", True)
        self.plain_paste_action = action(
            self.paste_format_menu,
            "プレーンテキストとして貼り付け",
            lambda: self._with_source_visible(self.paste_plain),
            "Ctrl+Shift+V",
        )
        self.html_paste_action = action(
            self.paste_format_menu,
            "Markdownとして貼り付け",
            lambda: self._with_source_visible(self.paste_html),
        )
        self.code_paste_action = action(
            self.paste_format_menu,
            "コードブロックとして貼り付け",
            lambda: self._with_source_visible(self.paste_code_block),
        )
        self.quote_paste_action = action(
            self.paste_format_menu,
            "引用として貼り付け",
            lambda: self._with_source_visible(self.paste_quote),
        )
        self.table_paste_action = action(
            self.paste_format_menu,
            "表として貼り付け…",
            lambda: self._with_source_visible(self.paste_table),
        )
        self.delete_action = action(edit_menu, "削除", self.delete_selection, source_context=True)
        edit_menu.addSeparator()
        self.select_all_action = action(
            edit_menu,
            "すべて選択",
            self.select_all_active,
            QKeySequence.StandardKey.SelectAll,
            source_context=True,
        )
        edit_menu.addSeparator()
        action(edit_menu, "検索…", lambda: self.show_search(False), QKeySequence.StandardKey.Find)
        action(edit_menu, "置換…", lambda: self.show_search(True), "Ctrl+H")
        action(edit_menu, "次を検索", lambda: self.navigate_search(False), "F3")
        action(edit_menu, "前を検索", lambda: self.navigate_search(True), "Shift+F3")
        action(
            insert_menu, "画像ファイル…", lambda: self._with_source_visible(self.insert_image_file)
        )
        self.create_table_action = action(
            insert_menu, "表…", lambda: self._with_source_visible(self.create_table)
        )
        insert_menu.aboutToShow.connect(self.refresh_edit_actions)
        self.rename_images_action = action(
            tools_menu, "画像ファイル名を一括変更…", self.rename_images_bulk
        )
        action(tools_menu, "中断した画像操作を復旧…", self.recover_image_operations_dialog)
        edit_menu.aboutToShow.connect(self.refresh_edit_actions)
        edit_menu.aboutToShow.connect(self.refresh_clipboard_actions)
        self.paste_format_menu.aboutToShow.connect(self.refresh_clipboard_actions)
        self.editor.copyAvailable.connect(self.refresh_edit_actions)
        self.preview.page().selectionChanged.connect(self.refresh_edit_actions)
        self.editor.textChanged.connect(self.refresh_edit_actions)
        QApplication.clipboard().dataChanged.connect(self.refresh_clipboard_actions)
        self.refresh_edit_actions()
        self.refresh_clipboard_actions()
        self.sync_action = QAction("スクロール同期", self)
        self.sync_action.setCheckable(True)
        self.sync_action.setChecked(True)
        self.sync_action.toggled.connect(self._sync_toggled)
        self.wrap_action = QAction("ソースを折り返す", self)
        self.wrap_action.setCheckable(True)
        self.wrap_action.setChecked(True)
        self.wrap_action.toggled.connect(self.editor.set_wrapping)
        for item in (self.sync_action, self.wrap_action):
            view_menu.addAction(item)
        action(view_menu, "先頭へ", lambda: self.scroll_to_boundary(False))
        action(view_menu, "最終行を上端へ", lambda: self.scroll_to_boundary(True))
        theme_menu = view_menu.addMenu("テーマ")
        self.theme_actions = {}
        self._theme_group = QActionGroup(self)
        for key, label in (("light", "ライト"), ("dark", "ダーク"), ("system", "システムと同じ")):
            item = QAction(label, self)
            item.setCheckable(True)
            item.triggered.connect(lambda checked=False, mode=key: self.apply_theme(mode))
            self._theme_group.addAction(item)
            theme_menu.addAction(item)
            self.theme_actions[key] = item
        self._search_escape = QShortcut(QKeySequence("Escape"), self.search)
        self._search_escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._search_escape.activated.connect(self.search.close_bar)

    def apply_theme(self, mode: str, persist: bool = True) -> None:
        self.theme_mode = mode if mode in {"light", "dark", "system"} else "system"
        dark = self.theme_mode == "dark" or (
            self.theme_mode == "system"
            and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        )
        self.setPalette(palette(dark))
        self.editor.apply_theme(dark)
        self.preview.apply_theme(dark)
        self.search.apply_theme(dark)
        self.update_display_icons(dark)
        self.apply_chrome_theme(dark)
        apply_window_menu_theme(self)
        for name, item in (
            ("new", self.new_action),
            ("open", self.open_action),
            ("save", self.save_action),
            ("undo", self.undo_action),
            ("redo", self.redo_action),
        ):
            item.setIcon(outline_icon(name, self.palette().windowText().color()))
        self.theme_actions[self.theme_mode].setChecked(True)
        if persist:
            self.settings.setValue("theme", self.theme_mode)

    def _system_theme_changed(self, *_args):
        if self.theme_mode == "system":
            self.apply_theme("system", persist=False)

    def set_source(
        self, text: str, base_dir: Path, path: Path | None = None, *, update_session: bool = True
    ) -> None:
        self._loading = True
        self._display_change_token += 1
        self._changing_display_mode = False
        self._preview_position = 0.0
        self._render_timer.stop()
        if update_session:
            self.session.adopt(path=path, base_dir=base_dir)
            self._pending_encoding = None
            self._pending_newline = None
        self.path = path
        self.base_dir = base_dir.resolve()
        self._revision += 1
        self.editor.replace_source(text)
        self._loading = False
        self._update_title()
        self._render()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard():
            self._render_timer.stop()
            self.search._timer.stop()
            self.close_image_actions()
            self.session.close()
            event.accept()
        else:
            event.ignore()

    def _update_title(self, *_args) -> None:
        name = self.path.name if self.path else "無題"
        marker = " *" if self._has_unsaved_changes() else ""
        self.setWindowTitle(f"{name}{marker}")

    def _record_new_undo_command(self) -> None:
        self._new_undo_command = True

    def _source_changed(self) -> None:
        steps = self.editor.document().availableUndoSteps()
        history_navigation = not self._new_undo_command and steps != self._undo_steps
        self._new_undo_command = False
        self._undo_steps = steps
        if not self._loading:
            self.session.remember_references(
                self.editor.toPlainText(), history=history_navigation, history_key=steps
            )
            self._revision += 1
            self._render_timer.start()

    def _render(self) -> None:
        source = self.editor.toPlainText()
        self.session.remember_references(
            source, history_key=self.editor.document().availableUndoSteps()
        )
        self.sync_image_watches()
        rendered = render_markdown(
            self.image_preview_source(self.session.normalize_references(source))
        )
        self.preview.set_document(rendered.html, rendered.line_count, self.base_dir, self._revision)

    def _preview_ready(self, revision: int) -> None:
        if revision != self._revision:
            return
        self._rendered_revision = revision
        if self._changing_display_mode:
            if self.display_mode != "source" and self._display_restore_revision != revision:
                self._display_restore_revision = revision
                self.preview.restore_view(
                    self._preview_position, revision, self._display_change_token
                )
        elif self.display_mode == "preview":
            self._scroll_preview_to(self._preview_position)
        elif (
            self.display_mode == "split"
            and self.sync_action.isChecked()
            and not self._changing_display_mode
        ):
            self._scroll_preview_to(self.editor.source_position())
        self._update_positions()

    def _editor_scrolled(self, position: float) -> None:
        self._update_positions()
        if (
            self._syncing_editor
            or self._loading
            or self._changing_display_mode
            or self.display_mode == "preview"
        ):
            return
        if self._ignore_editor_position is not None:
            expected, self._ignore_editor_position = self._ignore_editor_position, None
            if abs(expected - position) < 0.001:
                return
        if (
            self.display_mode == "split"
            and self.sync_action.isChecked()
            and self._rendered_revision == self._revision
        ):
            self._scroll_preview_to(position)

    def _preview_scrolled(self, position: float, revision: int) -> None:
        if (
            revision != self._revision
            or self._changing_display_mode
            or self.display_mode == "source"
        ):
            return
        self._preview_position = position
        if self.sync_action.isChecked() and self.display_mode == "split":
            self._syncing_editor = True
            try:
                self.editor.scroll_to_source(position)
                self._ignore_editor_position = self.editor.source_position()
            finally:
                self._syncing_editor = False
        self._update_positions()

    def _sync_toggled(self, enabled: bool) -> None:
        if enabled and self._rendered_revision == self._revision and self.display_mode == "split":
            self._scroll_preview_to(self.editor.source_position())
        self._update_positions()

    def _preview_error(self, message: str) -> None:
        self.statusBar().showMessage(f"プレビュー: {message}")

    def _update_positions(self) -> None:
        if not hasattr(self, "position_label"):
            return
        if hasattr(self, "format_label"):
            encoding = self._pending_encoding or self.session.encoding
            newline = self._pending_newline or self.session.newline
            mixed = "（混在）" if self.session.mixed_newlines and not self._pending_newline else ""
            bom = " + BOM" if self.session.bom and encoding not in {"utf-8-sig", "utf-16"} else ""
            self.format_label.setText(f"{encoding}{bom}  ·  {newline}{mixed}")
        cursor = self.editor.textCursor()
        self.position_label.setText(
            f"{cursor.blockNumber() + 1} 行 {cursor.positionInBlock() + 1} 列  ·  表示位置: {int(self._preview_position if self.display_mode == 'preview' else self.editor.source_position()) + 1} 行 / {self.editor.blockCount()} 行"
            + ("  ·  同期 ON" if self.sync_action.isChecked() else "  ·  同期 OFF")
        )


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QMessageBox

    from .notebook import NotebookWindow, default_library_root
    from .notebook_instance import NotebookInstance

    parser = argparse.ArgumentParser(description="MarkNotes: 自動保存のMarkdownノート")
    parser.add_argument("files", nargs="*", type=Path, help="ノートに取り込むMarkdownファイル")
    parser.add_argument("--library", type=Path, help="ライブラリの保存フォルダ")
    parser.add_argument("--sample", action="store_true", help="検証サンプルをノートに取り込む")
    parser.add_argument("--smoke-report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    set_windows_app_user_model_id()
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setWindowIcon(application_icon())
    app.setApplicationName("MarkNotes")
    app.setOrganizationName("MarkNotes")
    settings = QSettings("MarkNotes", "MarkNotes")
    root = args.library or Path(str(settings.value("library/path", str(default_library_root()))))
    paths = list(args.files)
    if args.sample:
        paths.append(RESOURCE_DIR / "scroll-check.md")
    instance = NotebookInstance(root, app)
    pending_requests = []
    instance.requested.connect(pending_requests.append)
    try:
        if not instance.acquire(paths):
            return 0
        window = NotebookWindow(root, settings)
    except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
        instance.close()
        QMessageBox.critical(None, "MarkNotesを開けません", str(exc))
        return 1

    def activate(files):
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()
        for path in files:
            window.open_path(Path(path))

    instance.requested.disconnect(pending_requests.append)
    instance.requested.connect(activate)
    for request in pending_requests:
        activate(request)
    app.aboutToQuit.connect(instance.close)
    window.show()
    for path in paths:
        window.open_path(path)
    if args.smoke_report:
        from marknotes.smoke import start_smoke

        def start_when_ready():
            if window._active and window._rendered_revision == window._revision:
                start_smoke(window, args.smoke_report)
            else:
                QTimer.singleShot(100, window, start_when_ready)

        if not paths and not window._order:
            window.new_document()
        QTimer.singleShot(100, window, start_when_ready)
    return app.exec()
