"""Current-note attachment browsing and explicit, serialized file operations."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from PySide6.QtCore import QFileSystemWatcher, QMimeData, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QPalette, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .assets import rewrite_destinations
from .managed_assets import (
    IMAGE_SUFFIXES,
    ManagedAssets,
    _plain_directory,
    escape_link_label,
    resolve_managed_asset,
)
from .ui_icons import outline_icon


@dataclass(frozen=True, slots=True)
class AssetEntry:
    relative_path: str
    name: str
    size: int

    @property
    def markdown(self):
        image = Path(self.relative_path).suffix.lower() in IMAGE_SUFFIXES
        return (
            f"{'!' if image else ''}[{escape_link_label(self.name)}]"
            f"({quote(self.relative_path, safe='/-._~')})"
        )


@dataclass(frozen=True, slots=True)
class AssetListing:
    entries: tuple[AssetEntry, ...]
    directories: tuple[str, ...]


class NotebookAssetFiles:
    """Validated note-scoped operations, suitable for notebook background jobs."""

    def __init__(self, store, note_id, base_dir):
        self.store, self.note_id = store, note_id
        self.base_dir = Path(base_dir).absolute()
        if self.base_dir != store.note_dir(note_id):
            raise ValueError("このノートの添付フォルダではありません。")

    def _manager(self):
        self.store.get(self.note_id)
        return ManagedAssets(self.base_dir)

    def path(self, relative):
        self._manager()
        path = resolve_managed_asset(quote(relative, safe="/-._~"), self.base_dir)
        if path is None:
            raise ValueError(f"添付ファイルが見つからないか、安全に参照できません: {relative}")
        return path

    def listing(self):
        with self.store.mutation_lock:
            root = self._manager().assets_dir
            names = {
                row["relative_path"]: row["original_name"]
                for row in self.store.list_attachments(self.note_id)
            }
            entries, directories = [], []
            for directory, folders, filenames in os.walk(root, followlinks=False):
                current = _plain_directory(Path(directory))
                directories.append(str(current))
                safe_folders = []
                for name in folders:
                    try:
                        _plain_directory(current / name)
                    except (OSError, ValueError):
                        continue
                    safe_folders.append(name)
                folders[:] = safe_folders
                for name in filenames:
                    relative = (current / name).relative_to(self.base_dir).as_posix()
                    try:
                        path = resolve_managed_asset(quote(relative, safe="/-._~"), self.base_dir)
                        if path is None:
                            continue
                        size = path.stat().st_size
                    except (OSError, ValueError):
                        continue
                    entries.append(AssetEntry(relative, names.get(relative) or name, size))
            entries.sort(key=lambda item: item.relative_path.casefold())
            return AssetListing(tuple(entries), tuple(directories))

    def rename(self, relative, name):
        with self.store.mutation_lock:
            asset = self._manager().copy_named(self.path(relative), name)
            try:
                self.store.register_attachment(
                    self.note_id,
                    asset.relative_path,
                    asset.original_name,
                    "image" if asset.is_image else "file",
                    asset.size,
                    asset.asset_id,
                )
            except Exception:
                asset.path.unlink(missing_ok=True)
                raise
            return asset

    def delete(self, relative):
        with self.store.mutation_lock:
            source = self.path(relative)
            metadata = next(
                (
                    row
                    for row in self.store.list_attachments(self.note_id)
                    if row["relative_path"] == relative
                ),
                None,
            )
            staging = _plain_directory(self.base_dir / "staging")
            staging.mkdir(exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix="delete-", dir=staging))
            staged = temporary / source.name
            try:
                source.rename(staged)
                try:
                    self.store.remove_attachment(self.note_id, relative)
                except Exception:
                    # Never overwrite a file an external application created.
                    if source.exists():
                        raise OSError(f"削除の復元先が既に存在します: {source}") from None
                    staged.rename(source)
                    raise
                try:
                    staged.unlink()
                except OSError:
                    if source.exists():
                        raise OSError(f"削除の復元先が既に存在します: {source}") from None
                    staged.rename(source)
                    if metadata is not None:
                        self.store.register_attachment(
                            self.note_id,
                            relative,
                            metadata["original_name"],
                            metadata["kind"],
                            metadata["size"],
                            metadata["asset_id"],
                        )
                    raise
            finally:
                if not staged.exists():
                    temporary.rmdir()

    def export(self, relative, directory):
        with self.store.mutation_lock:
            source = self.path(relative)
            directory = _plain_directory(Path(directory))
            if directory.is_relative_to(self.store.root):
                raise ValueError("コピー先はライブラリの外を選んでください。")
            target = directory / source.name
            created = False
            try:
                with target.open("xb") as output:
                    created = True
                    with source.open("rb") as stream:
                        shutil.copyfileobj(stream, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                if created:
                    target.unlink(missing_ok=True)
                raise
            return target


def renamed_asset_markdown(body, old_path, asset, base_dir):
    def replacement(destination):
        if resolve_managed_asset(destination.url, base_dir) != old_path:
            return None
        parsed = urlsplit(destination.url)
        return urlunsplit(
            ("", "", quote(asset.relative_path, safe="/-._~"), parsed.query, parsed.fragment)
        )

    return rewrite_destinations(body, replacement)


class NotebookAssetsPanel(QWidget):
    action_requested = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notebookAssetsPanel")
        self._entries = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        toolbar = QHBoxLayout()
        self.summary = QLabel("ノートを開くと添付ファイルを表示します", self)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.buttons = {}
        for action, title, icon in (
            ("add", "ファイルを追加…", QStyle.StandardPixmap.SP_FileDialogNewFolder),
            ("folder", "添付フォルダを開く", QStyle.StandardPixmap.SP_DirOpenIcon),
            ("refresh", "一覧を更新", QStyle.StandardPixmap.SP_BrowserReload),
            ("menu", "ファイル操作", QStyle.StandardPixmap.SP_FileDialogDetailedView),
        ):
            button = QToolButton(self)
            button.setIcon(self.style().standardIcon(icon))
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(30, 30)
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.setAutoRaise(True)
            button.clicked.connect(lambda _checked=False, key=action: self._request(key))
            toolbar.addWidget(button)
            self.buttons[action] = button
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.files = QListWidget(self)
        self.files.setAccessibleName("現在のノートの添付ファイル")
        self.files.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._show_context)
        self.files.itemDoubleClicked.connect(lambda _item: self._request("open"))
        layout.addWidget(self.files, 1)
        self._shortcuts = []
        for key, action in (
            (QKeySequence.StandardKey.Copy, "copy"),
            ("Delete", "delete"),
            ("F2", "rename"),
        ):
            shortcut = QShortcut(QKeySequence(key), self.files)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda key=action: self._request(key))
            self._shortcuts.append(shortcut)
        self.setEnabled(False)
        self.apply_theme(False)

    def apply_theme(self, dark):
        chrome = "#20252d" if dark else "#f5f7fa"
        text = "#e1e7ef" if dark else "#253047"
        muted = "#a0adbf" if dark else "#67758a"
        hover = "#2d3542" if dark else "#e8edf4"
        selected = "#314968" if dark else "#e1ebfa"
        palette = QPalette(self.palette())
        for role, color in (
            (QPalette.ColorRole.Window, chrome),
            (QPalette.ColorRole.Base, chrome),
            (QPalette.ColorRole.WindowText, text),
            (QPalette.ColorRole.Text, text),
            (QPalette.ColorRole.Highlight, selected),
            (QPalette.ColorRole.HighlightedText, text),
        ):
            palette.setColor(role, QColor(color))
        self.setPalette(palette)
        for key, glyph in (
            ("add", "new"),
            ("folder", "open"),
            ("refresh", "refresh"),
            ("menu", "menu"),
        ):
            self.buttons[key].setIcon(outline_icon(glyph, muted))
        self.setStyleSheet(f"""
            QWidget#notebookAssetsPanel {{ background: {chrome}; color: {text}; }}
            QWidget#notebookAssetsPanel QLabel {{ color: {muted}; background: transparent; }}
            QWidget#notebookAssetsPanel QListWidget {{
                background: {chrome}; color: {text}; border: none; outline: none;
            }}
            QWidget#notebookAssetsPanel QListWidget::item {{ padding: 8px 4px; border-radius: 4px; }}
            QWidget#notebookAssetsPanel QListWidget::item:hover {{ background: {hover}; }}
            QWidget#notebookAssetsPanel QListWidget::item:selected {{ background: {selected}; color: {text}; }}
            QWidget#notebookAssetsPanel QToolButton {{ border: none; border-radius: 4px; background: transparent; }}
            QWidget#notebookAssetsPanel QToolButton:hover {{ background: {hover}; }}
        """)

    def selected_entries(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.files.selectedItems()]

    def _request(self, action):
        if action == "menu":
            menu = self.context_menu()
            try:
                menu.exec(
                    self.buttons["menu"].mapToGlobal(self.buttons["menu"].rect().bottomLeft())
                )
            finally:
                menu.deleteLater()
        else:
            self.action_requested.emit(action, self.selected_entries())

    def context_menu(self):
        menu = QMenu(self)
        selected = self.selected_entries()
        for action, label in (
            ("open", "開く"),
            ("reveal", "保存場所を開く"),
            ("copy", "ファイルをコピー"),
            ("path", "パスをコピー"),
            ("link", "Markdownリンクをコピー"),
            ("insert", "ノートにリンクを挿入"),
            ("export", "指定フォルダにコピー…"),
            ("rename", "名前を変更…"),
            ("delete", "削除…"),
        ):
            item = menu.addAction(label)
            item.setData(action)
            item.setEnabled(bool(selected) and (action != "rename" or len(selected) == 1))
            item.triggered.connect(lambda _checked=False, key=action: self._request(key))
        menu.addSeparator()
        menu.addAction("ファイルを追加…", lambda: self._request("add"))
        menu.addAction("添付フォルダを開く", lambda: self._request("folder"))
        menu.addAction("一覧を更新", lambda: self._request("refresh"))
        return menu

    def _show_context(self, point):
        item = self.files.itemAt(point)
        if item is not None and not item.isSelected():
            self.files.setCurrentItem(item)
        menu = self.context_menu()
        try:
            menu.exec(self.files.viewport().mapToGlobal(point))
        finally:
            menu.deleteLater()

    def set_entries(self, entries):
        entries = tuple(entries)
        if entries == self._entries:
            self.summary.setText(
                f"添付ファイル {len(entries)} 件" if entries else "添付ファイルはありません"
            )
            return
        self._entries = entries
        selected = {entry.relative_path for entry in self.selected_entries()}
        current = self.files.currentItem()
        current_path = current.data(Qt.ItemDataRole.UserRole).relative_path if current else None
        scroll = self.files.verticalScrollBar().value()
        self.files.clear()
        for entry in entries:
            size = f"{entry.size:,} B" if entry.size < 1024 else f"{entry.size / 1024:,.1f} KB"
            relative = entry.relative_path.removeprefix("assets/")
            item = QListWidgetItem(f"{entry.name}\n{relative} · {size}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(entry.relative_path)
            self.files.addItem(item)
            if entry.relative_path == current_path:
                self.files.setCurrentItem(item)
            item.setSelected(entry.relative_path in selected)
        self.files.verticalScrollBar().setValue(scroll)
        self.summary.setText(
            f"添付ファイル {len(entries)} 件" if entries else "添付ファイルはありません"
        )

    def clear(self):
        self._entries = ()
        self.files.clear()
        self.summary.setText("ノートを開くと添付ファイルを表示します")
        self.setEnabled(False)


class NotebookAssetsController(QObject):
    def __init__(self, window, panel):
        super().__init__(window)
        self.window, self.panel = window, panel
        self.note_id, self.base_dir = None, None
        self._generation, self._pending = 0, 0
        self._busy = False
        self.watcher = QFileSystemWatcher(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.refresh)
        self.watcher.directoryChanged.connect(lambda _path: self.timer.start())
        panel.action_requested.connect(self.handle_action)

    def set_note(self, note_id, base_dir=None):
        self._generation += 1
        self.timer.stop()
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.note_id = note_id
        self.base_dir = Path(base_dir) if note_id and base_dir else None
        self.panel.clear()
        if self.note_id and self.base_dir:
            self.panel.summary.setText("添付ファイルを読み込み中…")
            self.set_busy(self._busy)
            self.refresh()

    def clear(self):
        self.set_note(None)

    def set_busy(self, busy):
        self._busy = busy
        self.panel.setEnabled(bool(self.note_id) and not busy and not self._pending)

    def _valid(self, note_id):
        return (
            note_id == self.note_id == self.window._active
            and not self.window._busy
            and not self.window._closing
            and not self.window._shutdown_done
        )

    def _files(self):
        return NotebookAssetFiles(self.window.store, self.note_id, self.base_dir)

    def refresh(self):
        if (
            not self.note_id
            or not self.base_dir
            or self.window._closing
            or self.window._shutdown_done
        ):
            return
        self._generation += 1
        generation, note_id, service = self._generation, self.note_id, self._files()

        def done(listing, error):
            if (
                generation != self._generation
                or note_id != self.note_id
                or self.window._closing
                or self.window._shutdown_done
            ):
                return
            if error:
                self.panel.summary.setText(f"添付を読み込めません: {error}")
                return
            self.panel.set_entries(listing.entries)
            old, new = set(self.watcher.directories()), set(listing.directories)
            if old - new:
                self.watcher.removePaths(list(old - new))
            if new - old:
                self.watcher.addPaths(list(new - old))

        self.window.writer.submit(service.listing, done)

    def _operation(self, work, success=None):
        state = self.window._sessions[self.note_id]
        self._pending += 1
        state.pending_assets += 1
        self.set_busy(self._busy)

        def done(result, error):
            self._pending -= 1
            state.pending_assets -= 1
            self.set_busy(self._busy)
            if error:
                self.window.statusBar().showMessage(f"ファイル操作に失敗しました: {error}")
            elif success:
                success(result)
            self.refresh()
            self.window.sync_image_watches()
            self.window._drain_deferred_operation()

        self.window.writer.submit(work, done)

    def handle_action(self, action, entries):
        note_id = self.note_id
        if not self._valid(note_id) or not note_id or self._pending:
            return
        service = self._files()
        try:
            if action == "refresh":
                self.refresh()
            elif action == "add":
                self.window.insert_attachment_dialog()
            elif action == "folder":
                self._open_folder(service, note_id)
            elif entries:
                self._selected_action(action, entries, service, note_id)
        except (OSError, ValueError) as error:
            self.window.statusBar().showMessage(f"ファイル操作に失敗しました: {error}")

    def _open_folder(self, service, note_id):
        # An ordinary attachment import can already hold the mutation lock.
        # Queue behind it instead of waiting for that lock on the GUI thread.
        def work():
            with self.window.store.mutation_lock:
                return service._manager().assets_dir

        def done(folder):
            if not self._valid(note_id):
                return
            try:
                self._open(folder)
            except (OSError, ValueError) as error:
                self.window.statusBar().showMessage(f"フォルダを開けません: {error}")

        self._operation(work, done)

    def _open(self, path):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            raise OSError(f"開くためのアプリが見つかりません: {path.name}")

    def _selected_action(self, action, entries, service, note_id):
        paths = [service.path(entry.relative_path) for entry in entries]
        if action in {"open", "reveal"}:
            targets = dict.fromkeys(path.parent if action == "reveal" else path for path in paths)
            for path in targets:
                self._open(path)
        elif action == "copy":
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
            mime.setText("\n".join(map(str, paths)))
            QApplication.clipboard().setMimeData(mime)
        elif action in {"path", "link"}:
            value = (
                "\n".join(map(str, paths))
                if action == "path"
                else "\n".join(entry.markdown for entry in entries)
            )
            QApplication.clipboard().setText(value)
        elif action == "insert":
            self.window.insert_text("\n".join(entry.markdown for entry in entries))
        elif action == "rename" and len(entries) == 1:
            self._rename(entries[0], paths[0], service, note_id)
        elif action == "delete":
            names = "\n".join(entry.name for entry in entries[:10])
            answer = QMessageBox.question(
                self.window,
                "添付ファイルを削除",
                f"{len(entries)} 件の添付ファイルを削除しますか？\n\n{names}\n\n"
                "元に戻せません。本文のリンクは残り、削除したファイルを開けなくなります。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes and self._valid(note_id):
                self._batch(entries, lambda entry: service.delete(entry.relative_path), "削除")
        elif action == "export":
            directory = QFileDialog.getExistingDirectory(self.window, "コピー先フォルダ")
            if directory and self._valid(note_id):
                self._batch(
                    entries, lambda entry: service.export(entry.relative_path, directory), "コピー"
                )

    def _batch(self, entries, operation, label):
        def work():
            failures, count = [], 0
            for entry in entries:
                try:
                    operation(entry)
                    count += 1
                except (OSError, ValueError) as error:
                    failures.append(f"{entry.name}: {error}")
            return count, failures

        def done(result):
            count, failures = result
            message = f"{count} 件のファイルを{label}しました"
            if failures:
                message += "（失敗: " + "; ".join(failures) + "）"
            self.window.statusBar().showMessage(message)

        self._operation(work, done)

    def _rename(self, entry, path, service, note_id):
        state = self.window._sessions[note_id]
        name, accepted = QInputDialog.getText(
            self.window,
            "添付ファイルの名前を変更",
            "新しいファイル名（拡張子は変更できません）\n"
            "本文の参照も更新します。元に戻せるよう元ファイルは保持します。\n"
            "変更後のファイルは添付フォルダの直下に保存します。",
            text=path.name,
        )
        if not accepted or not name or name == path.name or not self._valid(note_id):
            return
        body = state.document.toPlainText()

        def done(asset):
            if state.document.toPlainText() == body:
                text = renamed_asset_markdown(body, path, asset, service.base_dir)
                if text != body:
                    cursor = QTextCursor(state.document)
                    cursor.beginEditBlock()
                    cursor.select(QTextCursor.SelectionType.Document)
                    cursor.insertText(text)
                    cursor.endEditBlock()
                    self.window.save_pending()
                self.window.statusBar().showMessage(
                    "名前を変更しました（元ファイルは保持しています）"
                )
            else:
                self.window.statusBar().showMessage(
                    "変更後のファイルを保存しました。本文が更新されたため参照変更を中止しました"
                )

        self._operation(lambda: service.rename(entry.relative_path, name), done)
