"""Image editing, rename review dialogs, and live file-change preview updates."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PySide6.QtCore import QFileSystemWatcher, QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .assets import local_path, rewrite_destinations
from .document import ExternalFileChangedError, NeedsNewlineSelection
from .image_rename import (
    ManagedImage,
    _record_write,
    bulk_rename_plan,
    current_images,
    execute_rename,
    image_at,
    make_rename_plan,
    recovery_records,
)
from .search import python_position


class ImageRenameDialog(QDialog):
    def __init__(self, session, source: str, parent=None, image: ManagedImage | None = None):
        super().__init__(parent)
        self.setWindowTitle("画像のファイル名を変更" if image else "画像ファイル名を一括変更")
        self.resize(850, 420 if image else 550)
        if parent is not None:
            self.setPalette(parent.palette())
        self.session, self.source = session, source
        self._managed_note = bool(getattr(session, "is_managed_note", False))
        self._bulk_error = ""
        self.plan = (
            make_rename_plan(session, source, {image.path: image.path.name})
            if image
            else bulk_rename_plan(session, source, prefix="image_" if self._managed_note else None)
        )
        layout = QVBoxLayout(self)
        summary = (
            "確定すると、画像の改名と同時に現在のMarkdown本文を保存します。"
            if session.path
            else "未保存文書の一時画像を改名します。初回保存時に文書名に合わせて採番します。"
        )
        note = QLabel(
            "このノートの assets にある画像のファイル名を変更します。"
            "\n本文中の参照を更新し、自動保存します。"
            "変更前のファイルは元に戻す操作のため保持します。"
            if self._managed_note
            else summary + "\n対象はこの文書が参照するimg配下の画像です。"
            "\n旧名はUndo履歴用に保持します。同じフォルダの別Markdownからの共有参照があれば、終了後も残します。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        if image is None:
            options = QFormLayout()
            prefix = (
                "image_"
                if self._managed_note
                else f"{session.path.stem if session.path else 'untitled'}_"
            )
            self.prefix = QLineEdit(prefix)
            self.prefix.setObjectName("imageRenamePrefix")
            self.prefix.setPlaceholderText("空欄なら連番のみ")
            self.start_number = QSpinBox()
            self.start_number.setObjectName("imageRenameStartNumber")
            self.start_number.setRange(0, 2_147_483_647)
            self.start_number.setValue(1)
            self.digits = QSpinBox()
            self.digits.setObjectName("imageRenameDigits")
            self.digits.setRange(1, 12)
            self.digits.setValue(5)
            self.digits.setToolTip("番号が指定桁数を超えた場合は、桁数を増やして続けます。")
            options.addRow("プレフィックス", self.prefix)
            options.addRow("開始番号", self.start_number)
            options.addRow("連番の最小桁数", self.digits)
            layout.addLayout(options)
            naming_note = QLabel(
                "プレフィックスには区切り文字（_など）も含めて入力してください。"
                "\n設定変更で候補を更新し、既存名・履歴で使用中の番号は飛ばします。"
            )
            naming_note.setWordWrap(True)
            layout.addWidget(naming_note)
        self.table = QTableWidget(len(self.plan.entries), 3)
        self.table.setHorizontalHeaderLabels(
            [
                "現在の名前",
                "変更後の名前",
                "添付の範囲" if self._managed_note else "共有参照／確認範囲",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._sources = [entry.source for entry in self.plan.entries]
        for row, entry in enumerate(self.plan.entries):
            old = QTableWidgetItem(entry.source.relative_to(session.base_dir).as_posix())
            old.setFlags(old.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, old)
            self.table.setItem(row, 1, QTableWidgetItem(entry.target.name))
            shared = QTableWidgetItem(self._shared_description(entry))
            shared.setFlags(shared.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, shared)
        layout.addWidget(self.table)
        if image is not None:
            preview = QLabel()
            pixmap = QPixmap(str(image.path))
            if not pixmap.isNull():
                preview.setPixmap(
                    pixmap.scaled(
                        260,
                        120,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                layout.addWidget(preview)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "改名して自動保存" if self._managed_note else "改名して保存" if session.path else "改名"
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.table.itemChanged.connect(self._validate)
        if image is None:
            self.prefix.textChanged.connect(self._regenerate_bulk_names)
            self.start_number.valueChanged.connect(self._regenerate_bulk_names)
            self.digits.valueChanged.connect(self._regenerate_bulk_names)
        self._validate()

    def _shared_description(self, entry):
        if self._managed_note:
            return "このノートの添付"
        return ", ".join(path.name for path in entry.shared_with) or "同じフォルダ内に共有参照なし"

    def _regenerate_bulk_names(self, *_args):
        try:
            generated = bulk_rename_plan(
                self.session,
                self.source,
                prefix=self.prefix.text(),
                start_number=self.start_number.value(),
                digits=self.digits.value(),
            )
        except (ValueError, OSError) as exc:
            self._bulk_error = str(exc)
            self._validate()
            return
        self._bulk_error = ""
        previous = self.table.blockSignals(True)
        try:
            for row, entry in enumerate(generated.entries):
                self.table.item(row, 1).setText(entry.target.name)
                self.table.item(row, 2).setText(self._shared_description(entry))
        finally:
            self.table.blockSignals(previous)
        self._validate()

    def _validate(self, *_args):
        if self._bulk_error:
            self.message.setText(self._bulk_error)
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        try:
            names = {path: self.table.item(row, 1).text() for row, path in enumerate(self._sources)}
            self.plan = make_rename_plan(self.session, self.source, names)
            if self._managed_note and any(entry.case_only for entry in self.plan.entries):
                raise ValueError(
                    "大文字・小文字だけの変更は同じ名前として扱われます。異なる名前を指定してください。"
                )
            self.message.setText(
                "拡張子と画像形式は維持します。変更前の添付もこのノートに残します。"
                if self._managed_note
                else "拡張子と画像形式は維持します。別フォルダの文書からの参照は確認対象外です。"
            )
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.plan.changed)
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)


class ImageActions:
    def init_image_actions(self):
        self._image_generation = 0
        self._image_watcher = QFileSystemWatcher(self)
        self._image_timer = QTimer(self)
        self._image_timer.setSingleShot(True)
        self._image_timer.setInterval(100)
        self._image_timer.timeout.connect(self._images_changed)
        self._image_watcher.fileChanged.connect(lambda *_: self._image_timer.start())
        self._image_watcher.directoryChanged.connect(lambda *_: self._image_timer.start())

    def close_image_actions(self):
        self._image_timer.stop()
        paths = self._image_watcher.files() + self._image_watcher.directories()
        if paths:
            self._image_watcher.removePaths(paths)

    def sync_image_watches(self):
        if not hasattr(self, "_image_watcher"):
            return
        try:
            paths = set()
            image_root = self.base_dir / "img"
            for destination, origin, _managed in self.session.image_reference_states(
                self.editor.toPlainText()
            ):
                if (
                    not destination.is_image
                    or origin is None
                    or not origin.is_relative_to(image_root)
                ):
                    continue
                if origin.is_file():
                    paths.add(str(origin))
                # Atomic replacement may remove a file for a moment. Keep its
                # directory under observation so recreating it resumes updates.
                directory = origin.parent
                while directory.is_relative_to(image_root):
                    if directory.is_dir():
                        paths.add(str(directory))
                    directory = directory.parent
            previous = set(self._image_watcher.files() + self._image_watcher.directories())
            removed, added = previous - paths, paths - previous
            if removed:
                self._image_watcher.removePaths(list(removed))
            if added:
                self._image_watcher.addPaths(list(added))
        except (OSError, ValueError):
            pass

    def _images_changed(self):
        self._image_generation += 1
        self.sync_image_watches()
        self._revision += 1
        self._render_timer.start()

    def image_preview_source(self, source: str) -> str:
        def cache_url(destination):
            if not destination.is_image:
                return None
            parsed = urlsplit(destination.url)
            path = (
                Path(QUrl(destination.url).toLocalFile())
                if parsed.scheme.lower() == "file"
                else local_path(destination.url, self.base_dir)
            )
            if path is None:
                return None
            try:
                stat = path.stat()
            except OSError:
                return None
            query = [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key != "_mdedit"
            ]
            query.append(("_mdedit", f"{self._image_generation}-{stat.st_mtime_ns}-{stat.st_size}"))
            return urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
            )

        return rewrite_destinations(source, cache_url)

    def edit_image(self, image: ManagedImage):
        source = self.editor.toPlainText()
        current = next(
            (item for item in current_images(self.session, source) if item.path == image.path), None
        )
        if current is None or not current.raster:
            self.statusBar().showMessage("img配下の参照中ラスター画像を選択してください。", 4000)
            return False
        executable = str(self.settings.value("image_editor", "mspaint.exe"))
        success, _pid = QProcess.startDetached(executable, [str(current.path)])
        if not success:
            QMessageBox.warning(
                self,
                "画像を編集できません",
                "画像編集アプリを起動できません。設定から実行ファイルを確認してください。",
            )
            return False
        self.sync_image_watches()
        return True

    def edit_current_image(self):
        source = self.editor.toPlainText()
        selected = image_at(
            self.session, source, python_position(source, self.editor.textCursor().position())
        )
        return self.edit_image(selected) if selected else False

    def rename_current_image(self, image: ManagedImage | None = None):
        source = self.editor.toPlainText()
        selected = image or image_at(
            self.session, source, python_position(source, self.editor.textCursor().position())
        )
        if selected is None:
            self.statusBar().showMessage("img配下の参照中画像を選択してください。", 4000)
            return False
        return self._review_image_rename(selected)

    def rename_images_bulk(self):
        return self._review_image_rename()

    def _review_image_rename(self, image=None):
        source = self.editor.toPlainText()
        if not current_images(self.session, source):
            self.statusBar().showMessage("この文書から参照するimg配下の画像がありません。", 4000)
            return False
        revision = self._revision
        try:
            dialog = ImageRenameDialog(self.session, source, self, image)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "改名できません", str(exc))
            return False
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if revision != self._revision or source != self.editor.toPlainText():
            QMessageBox.information(
                self, "文書が更新されました", "画像の改名一覧を開き直してください。"
            )
            return False
        encoding, newline = self._pending_encoding, self._pending_newline
        while True:
            try:
                result = execute_rename(
                    self.session, dialog.plan, encoding=encoding, newline=newline
                )
                break
            except NeedsNewlineSelection:
                newline, accepted = QInputDialog.getItem(
                    self,
                    "改行コード",
                    "保存する改行コード",
                    ["LF", "CRLF", "CR"],
                    ["LF", "CRLF", "CR"].index(self.session.newline),
                    False,
                )
                if not accepted:
                    return False
            except UnicodeEncodeError:
                answer = QMessageBox.question(
                    self,
                    "文字コード",
                    "現在の文字コードでは保存できません。UTF-8で保存しますか？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False
                encoding = "utf-8"
            except (
                OSError,
                ValueError,
                UnicodeError,
                LookupError,
                ExternalFileChangedError,
            ) as exc:
                QMessageBox.warning(self, "改名できません", str(exc))
                return False
        self._apply_document_result(result, saved=self.session.path is not None)
        self.statusBar().showMessage("画像名と文書内の参照を更新しました。", 4000)
        return True

    def recover_image_operations_dialog(self):
        active = set(self.session._rename_journals)
        records = [
            (directory, record)
            for directory, record in recovery_records(self.session.recovery_root)
            if directory not in active
            and record.get("state") not in {"complete", "rolled_back", "recovered"}
        ]
        if not records:
            QMessageBox.information(self, "画像操作の復旧", "復旧が必要な画像操作はありません。")
            return False
        dialog = QDialog(self)
        dialog.setPalette(self.palette())
        dialog.setWindowTitle("中断した画像操作を復旧")
        dialog.resize(780, 300)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("元の文書を上書きせず、画像を含む復旧コピーを開きます。"))
        table = QTableWidget(len(records), 2)
        table.setHorizontalHeaderLabels(["文書", "状態"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, (_directory, record) in enumerate(records):
            table.setItem(row, 0, QTableWidgetItem(record.get("document") or "未保存文書"))
            labels = {
                "prepared": "準備中",
                "committed": "改名済み",
                "conflict": "外部更新・復旧の確認が必要",
            }
            table.setItem(
                row, 1, QTableWidgetItem(labels.get(record.get("state"), "復旧の確認が必要"))
            )
        table.selectRow(0)
        layout.addWidget(table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).setText("復旧コピーを開く")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not self._confirm_discard():
            return False
        directory, record = records[table.currentRow()]
        if self.open_path(directory / "recovered.md"):
            record["state"] = "recovered"
            _record_write(directory, record)
            return True
        return False
