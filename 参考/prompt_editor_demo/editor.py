from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QRectF, QThread, Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QFont,
    QInputMethodEvent,
    QKeyEvent,
    QKeySequence,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from desktop_llm.models import ModelProfile
from desktop_llm.secrets import ApiKeyStore, SecretStoreError
from desktop_llm.storage import SettingsStore

from .completion import (
    CompletionError,
    CompletionProvider,
    CompletionRequest,
    CompletionValue,
    MockCompletionProvider,
    OpenAIProfileCompletionProvider,
)
from .directives import (
    Directive,
    DirectiveKind,
    directives_for_selection,
    line_end_completion_directive,
    parse_directives,
)
from .markdown_highlighter import MarkdownHighlighter


class PromptTextEdit(QPlainTextEdit):
    tab_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._composing = False
        self.setTabChangesFocus(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        font = QFont("Cascadia Mono", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)
        if not event.preeditString():
            self._composing = False

    def inputMethodQuery(self, query: Qt.InputMethodQuery):  # type: ignore[override]
        value = super().inputMethodQuery(query)
        if query == Qt.InputMethodQuery.ImCursorRectangle and isinstance(value, QRectF):
            return value.translated(0.0, float(self._ime_candidate_vertical_offset()))
        return value

    def _ime_candidate_vertical_offset(self) -> int:
        return self.fontMetrics().height() + 4

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if self._composing:
                super().keyPressEvent(event)
                return
            if event.key() == Qt.Key.Key_Backtab or bool(
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self.outdent_list()
            else:
                self.tab_requested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            if self._insert_list_newline():
                event.accept()
                return
        if event.key() == Qt.Key.Key_Backspace and not event.modifiers():
            if self._handle_structural_backspace():
                event.accept()
                return
        super().keyPressEvent(event)

    def indent_list(self) -> bool:
        return self._change_list_indent(outdent=False)

    def indent_list_for_tab(self) -> bool:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return self.indent_list()
        block = cursor.block()
        if (
            cursor.position() == block.position() + len(block.text())
            and re.fullmatch(r" *- ", block.text())
        ):
            return self.indent_list()
        return False

    def outdent_list(self) -> bool:
        return self._change_list_indent(outdent=True)

    def _change_list_indent(self, *, outdent: bool) -> bool:
        cursor = self.textCursor()
        start_block = self.document().findBlock(cursor.selectionStart())
        end_position = max(cursor.selectionStart(), cursor.selectionEnd() - 1)
        end_block = self.document().findBlock(end_position)
        blocks = []
        block = start_block
        while block.isValid():
            blocks.append(block)
            if block == end_block:
                break
            block = block.next()
        if not blocks or any(re.match(r"^ *- ", block.text()) is None for block in blocks):
            return False

        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for block in reversed(blocks):
            line = block.text()
            edit.setPosition(block.position())
            if outdent:
                remove = min(2, len(line) - len(line.lstrip(" ")))
                if remove:
                    edit.setPosition(block.position() + remove, QTextCursor.MoveMode.KeepAnchor)
                    edit.removeSelectedText()
            else:
                edit.insertText("  ")
        edit.endEditBlock()
        return True

    def _insert_list_newline(self) -> bool:
        cursor = self.textCursor()
        block = cursor.block()
        if cursor.position() != block.position() + len(block.text()):
            return False
        match = re.match(r"^(?P<indent> *)- (?P<body>.*)$", block.text())
        if match is not None:
            if match.group("body") or match.group("indent"):
                cursor.insertText(f"\n{match.group('indent')}- ")
            else:
                cursor.setPosition(block.position())
                cursor.setPosition(
                    block.position() + len(block.text()),
                    QTextCursor.MoveMode.KeepAnchor,
                )
                cursor.insertText(match.group("indent"))
                cursor.insertBlock()
            self.setTextCursor(cursor)
            return True

        continuation_indent = self._continuation_list_indent(block, block.text())
        if continuation_indent is not None:
            cursor.insertText(f"\n{continuation_indent}- ")
            self.setTextCursor(cursor)
            return True
        return False

    @staticmethod
    def _continuation_list_indent(block, line: str) -> str | None:  # type: ignore[no-untyped-def]
        previous = block.previous()
        if not previous.isValid():
            return None
        previous_match = re.match(r"^(?P<indent> *)- ", previous.text())
        if previous_match is None:
            return None
        list_indent = previous_match.group("indent")
        content_indent = list_indent + "  "
        if not line.startswith(content_indent):
            return None
        return list_indent

    def _handle_structural_backspace(self) -> bool:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        block = cursor.block()
        line = block.text()
        if cursor.position() != block.position() + len(line):
            return False

        if re.fullmatch(r" *- ", line):
            cursor.setPosition(block.position())
            cursor.setPosition(
                block.position() + len(line), QTextCursor.MoveMode.KeepAnchor
            )
            cursor.insertText(" " * len(line))
            self.setTextCursor(cursor)
            return True

        if line and not line.strip():
            previous = block.previous()
            previous_match = (
                re.match(r"^(?P<indent> *)- ", previous.text())
                if previous.isValid()
                else None
            )
            if (
                previous_match is not None
                and len(line) >= 2
                and (len(line) - 2) % 2 == 0
                and len(previous_match.group("indent")) <= len(line) - 2
            ):
                shallower_indent_width = len(line) - 4
                replacement = (
                    f"{' ' * shallower_indent_width}- "
                    if shallower_indent_width >= 0
                    else ""
                )
                cursor.setPosition(block.position())
                cursor.setPosition(
                    block.position() + len(line), QTextCursor.MoveMode.KeepAnchor
                )
                cursor.insertText(replacement)
            else:
                remove = min(2, len(line))
                cursor.setPosition(
                    cursor.position() - remove, QTextCursor.MoveMode.KeepAnchor
                )
                cursor.removeSelectedText()
            self.setTextCursor(cursor)
            return True
        return False


class DragBar(QFrame):
    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


class CompletionWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        provider: CompletionProvider,
        request: CompletionRequest,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.provider = provider
        self.request = request

    def run(self) -> None:
        try:
            self.completed.emit(self.provider.complete(self.request))
        except CompletionError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"補完に失敗しました: {exc}")

    def cancel(self) -> None:
        self.provider.cancel()


class PromptEditorWindow(QMainWindow):
    def __init__(
        self,
        store: SettingsStore,
        keys: ApiKeyStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.keys = keys
        self._profiles: list[ModelProfile] = []
        self._worker: CompletionWorker | None = None
        self._request: CompletionRequest | None = None
        self._request_selection = (0, 0)
        self._applying = False

        self.setWindowTitle("Prompt Editor Demo")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(680, 460)
        self.setMinimumSize(480, 300)

        outer = QWidget()
        outer.setObjectName("promptOuter")
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(0)

        self.surface = QFrame()
        self.surface.setObjectName("promptSurface")
        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setContentsMargins(9, 7, 9, 9)
        surface_layout.setSpacing(6)

        title_bar = DragBar()
        title_bar.setObjectName("promptTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(2, 0, 0, 0)
        title_layout.setSpacing(6)
        title = QLabel("Prompt Editor")
        title.setObjectName("promptTitle")
        self.activity_label = QLabel("")
        self.activity_label.setObjectName("promptActivity")
        close_button = QToolButton()
        close_button.setObjectName("promptClose")
        close_button.setText("×")
        close_button.setToolTip("閉じる")
        close_button.setFixedSize(27, 27)
        close_button.clicked.connect(self.close)
        title_layout.addWidget(title)
        title_layout.addStretch()
        title_layout.addWidget(self.activity_label)
        title_layout.addWidget(close_button)

        self.editor = PromptTextEdit()
        self.editor.setObjectName("promptTextEdit")
        self.editor.setPlaceholderText(
            "Markdownでプロンプトを入力…\n\n私は ?人名 です。\n? 注意点を説明する\n- ?注意点を3つ..."
        )
        self.highlighter = MarkdownHighlighter(self.editor.document())

        controls = QFrame()
        controls.setObjectName("promptControls")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(6, 5, 5, 5)
        controls_layout.setSpacing(5)

        self.ai_toggle = QToolButton()
        self.ai_toggle.setObjectName("aiToggle")
        self.ai_toggle.setCheckable(True)
        self.ai_toggle.setChecked(True)
        self.ai_toggle.setText("AI ON")
        self.ai_toggle.setToolTip("AI補完をON/OFF")
        self.ai_toggle.toggled.connect(self._ai_toggled)

        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("promptCombo")
        self.provider_combo.addItem("疑似AI", "mock")
        self.provider_combo.addItem("プロファイルAPI", "profile")
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)

        self.profile_combo = QComboBox()
        self.profile_combo.setObjectName("promptCombo")
        self.profile_combo.setMinimumWidth(130)
        self.profile_combo.setMaximumWidth(220)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)

        self.reasoning_combo = QComboBox()
        self.reasoning_combo.setObjectName("promptCombo")
        self.reasoning_combo.setMinimumWidth(85)
        self.reasoning_combo.setMaximumWidth(120)

        refresh_button = QToolButton()
        refresh_button.setObjectName("promptToolButton")
        refresh_button.setText("↻")
        refresh_button.setToolTip("Desktop LLMのプロファイルを再読込")
        refresh_button.clicked.connect(self.reload_profiles)

        self.unresolved_label = QLabel("? 0")
        self.unresolved_label.setObjectName("unresolvedLabel")
        self.unresolved_label.setToolTip("未補完ブロック数")

        clear_button = QToolButton()
        clear_button.setObjectName("promptToolButton")
        clear_button.setText("新規")
        clear_button.setToolTip("内容をクリア")
        clear_button.clicked.connect(self.editor.clear)

        copy_button = QToolButton()
        copy_button.setObjectName("promptToolButton")
        copy_button.setText("⧉")
        copy_button.setToolTip("全文をコピー (Ctrl+Enter)")
        copy_button.clicked.connect(self.copy_all)

        controls_layout.addWidget(self.ai_toggle)
        controls_layout.addWidget(self.provider_combo)
        controls_layout.addWidget(self.profile_combo)
        controls_layout.addWidget(self.reasoning_combo)
        controls_layout.addWidget(refresh_button)
        controls_layout.addStretch()
        controls_layout.addWidget(self.unresolved_label)
        controls_layout.addWidget(clear_button)
        controls_layout.addWidget(copy_button)

        surface_layout.addWidget(title_bar)
        surface_layout.addWidget(self.editor, 1)
        surface_layout.addWidget(controls)
        outer_layout.addWidget(self.surface)
        self.setCentralWidget(outer)

        self.editor.tab_requested.connect(self._tab_requested)
        self.editor.cancel_requested.connect(self.cancel_completion)
        self.editor.textChanged.connect(self._document_changed)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.copy_all)

        self._apply_styles()
        self.reload_profiles()
        self._provider_changed()
        self._update_unresolved_count()

    def reload_profiles(self) -> None:
        selected = self.profile_combo.currentData()
        if selected is None:
            selected = self.store.load_app_settings().last_profile_id
        self._profiles = self.store.list_profiles()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self._profiles:
            self.profile_combo.addItem(profile.name, profile.id)
        index = self.profile_combo.findData(selected)
        if index < 0 and self.profile_combo.count():
            index = 0
        if index >= 0:
            self.profile_combo.setCurrentIndex(index)
        self.profile_combo.blockSignals(False)
        self._profile_changed()
        if not self._profiles:
            self._show_status("Desktop LLMでプロファイルを登録してください", error=True)

    def copy_all(self) -> None:
        self.editor.selectAll()
        self.editor.copy()
        cursor = self.editor.textCursor()
        cursor.clearSelection()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)
        self._show_status("コピーしました")

    def cancel_completion(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        self._show_status("キャンセル中…")

    def _tab_requested(self) -> None:
        cursor = self.editor.textCursor()
        text = self.editor.toPlainText()
        directives = parse_directives(text)
        targets = directives_for_selection(
            directives, cursor.selectionStart(), cursor.selectionEnd()
        )
        if targets:
            if not self.ai_toggle.isChecked():
                self._show_status("AIがOFFです", error=True)
                return
            self._start_completion(targets)
            return
        if self.editor.indent_list_for_tab():
            return
        if cursor.hasSelection():
            return
        block_start = cursor.block().position()
        if any(directive.line_start == block_start for directive in directives):
            return
        line_end_target = line_end_completion_directive(text, cursor.position())
        if line_end_target is None:
            return
        if not self.ai_toggle.isChecked():
            self._show_status("AIがOFFです", error=True)
            return
        self._start_completion([line_end_target])

    def _start_completion(self, directives: list[Directive]) -> None:
        if self._worker is not None:
            self._show_status("補完中です")
            return
        text = self.editor.toPlainText()
        request = CompletionRequest(
            document=text,
            revision=self.editor.document().revision(),
            directives=tuple(directives),
        )
        try:
            provider = self._make_provider()
        except (CompletionError, SecretStoreError) as exc:
            self._show_status(str(exc), error=True)
            return

        cursor = self.editor.textCursor()
        self._request = request
        self._request_selection = (cursor.selectionStart(), cursor.selectionEnd())
        self._worker = CompletionWorker(provider, request, self)
        self._worker.completed.connect(self._completion_succeeded)
        self._worker.failed.connect(self._completion_failed)
        self._worker.finished.connect(self._worker_finished)
        self._set_busy(True)
        count = len(directives)
        self._show_status("補完中…" if count == 1 else f"{count}か所を補完中…")
        self._worker.start()

    def _make_provider(self) -> CompletionProvider:
        if self.provider_combo.currentData() == "mock":
            return MockCompletionProvider()
        profile = self._selected_profile()
        if profile is None:
            raise CompletionError("モデルプロファイルがありません。")
        endpoint = self.store.get_endpoint(profile.endpoint_id)
        if endpoint is None:
            raise CompletionError("APIエンドポイントが見つかりません。")
        api_key = self.keys.get(endpoint.id)
        return OpenAIProfileCompletionProvider(
            endpoint,
            profile,
            api_key,
            self.reasoning_combo.currentData(),
        )

    def _completion_succeeded(self, value: object) -> None:
        if not isinstance(value, dict) or self._request is None:
            self._show_status("補完応答の形式が不正です", error=True)
            return
        cursor = self.editor.textCursor()
        selection = (cursor.selectionStart(), cursor.selectionEnd())
        if (
            self.editor.document().revision() != self._request.revision
            or selection != self._request_selection
        ):
            self._show_status("編集中に届いた古い結果を破棄しました")
            return
        try:
            self._apply_replacements(self._request, value)
        except CompletionError as exc:
            self._show_status(str(exc), error=True)
            return
        self._show_status("補完しました")

    def _completion_failed(self, message: str) -> None:
        self._show_status(message, error="キャンセル" not in message)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        self._request = None
        self._set_busy(False)
        if worker is not None:
            worker.deleteLater()

    def _apply_replacements(
        self,
        request: CompletionRequest,
        values: dict[str, CompletionValue],
    ) -> None:
        if set(values) != {directive.id for directive in request.directives}:
            raise CompletionError("補完結果に不足または余分な項目があります。")
        current = self.editor.toPlainText()
        for directive in request.directives:
            if current[directive.start : directive.end] != request.document[
                directive.start : directive.end
            ]:
                raise CompletionError("補完対象が変更されたため結果を破棄しました。")

        edit = QTextCursor(self.editor.document())
        self._applying = True
        edit.beginEditBlock()
        try:
            for directive in sorted(request.directives, key=lambda item: item.start, reverse=True):
                replacement = _replacement_text(directive, values[directive.id])
                edit.setPosition(directive.start)
                edit.setPosition(directive.end, QTextCursor.MoveMode.KeepAnchor)
                edit.insertText(replacement)
        finally:
            edit.endEditBlock()
            self._applying = False
        cursor = self.editor.textCursor()
        cursor.clearSelection()
        self.editor.setTextCursor(cursor)

    def _document_changed(self) -> None:
        self._update_unresolved_count()
        if self._worker is not None and not self._applying:
            self._worker.cancel()

    def _update_unresolved_count(self) -> None:
        count = len(parse_directives(self.editor.toPlainText()))
        self.unresolved_label.setText(f"? {count}")
        self.unresolved_label.setProperty("active", count > 0)
        self.unresolved_label.style().unpolish(self.unresolved_label)
        self.unresolved_label.style().polish(self.unresolved_label)

    def _provider_changed(self) -> None:
        profile_mode = self.provider_combo.currentData() == "profile"
        self.profile_combo.setEnabled(profile_mode and bool(self._profiles))
        self.reasoning_combo.setEnabled(profile_mode and bool(self._profiles))

    def _profile_changed(self) -> None:
        profile = self._selected_profile()
        self.reasoning_combo.clear()
        if profile is None or not profile.reasoning_efforts:
            self.reasoning_combo.addItem("推論指定なし", None)
        else:
            for effort in profile.reasoning_efforts:
                self.reasoning_combo.addItem(effort, effort)
            previous = self.store.load_app_settings().last_reasoning_effort
            index = self.reasoning_combo.findData(previous)
            if index >= 0:
                self.reasoning_combo.setCurrentIndex(index)
        self._provider_changed()

    def _selected_profile(self) -> ModelProfile | None:
        profile_id = self.profile_combo.currentData()
        return next((profile for profile in self._profiles if profile.id == profile_id), None)

    def _ai_toggled(self, checked: bool) -> None:
        self.ai_toggle.setText("AI ON" if checked else "AI OFF")
        if not checked:
            self.cancel_completion()

    def _set_busy(self, busy: bool) -> None:
        self.provider_combo.setEnabled(not busy)
        self.profile_combo.setEnabled(
            not busy and self.provider_combo.currentData() == "profile" and bool(self._profiles)
        )
        self.reasoning_combo.setEnabled(
            not busy and self.provider_combo.currentData() == "profile" and bool(self._profiles)
        )

    def _show_status(self, text: str, *, error: bool = False) -> None:
        self.activity_label.setText(text)
        self.activity_label.setProperty("error", error)
        self.activity_label.style().unpolish(self.activity_label)
        self.activity_label.style().polish(self.activity_label)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(1500)
        super().closeEvent(event)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget#promptOuter { background: transparent; }
            QFrame#promptSurface {
                background-color: rgba(10, 15, 22, 244);
                border: 1px solid #324252;
                border-radius: 0;
            }
            QFrame#promptTitleBar { background: transparent; border: none; }
            QLabel#promptTitle {
                color: #e8f0f6;
                font-size: 9.5pt;
                font-weight: 600;
            }
            QLabel#promptActivity { color: #8eddf6; font-size: 8pt; }
            QLabel#promptActivity[error="true"] { color: #f1a19a; }
            QToolButton#promptClose {
                color: #8493a3;
                background: transparent;
                border: none;
                border-radius: 0;
                font-size: 17px;
            }
            QToolButton#promptClose:hover {
                color: #f3f6f9;
                background-color: rgba(255, 255, 255, 18);
            }
            QPlainTextEdit#promptTextEdit {
                color: #e4edf4;
                background-color: rgba(14, 21, 29, 215);
                border: 1px solid #2b3a4b;
                border-radius: 0;
                padding: 10px;
                selection-background-color: #246681;
            }
            QPlainTextEdit#promptTextEdit:focus { border-color: #4fbadd; }
            QFrame#promptControls {
                background-color: rgba(20, 29, 40, 245);
                border: 1px solid #2b3a4b;
                border-radius: 0;
            }
            QComboBox#promptCombo {
                color: #aebdca;
                background-color: rgba(255, 255, 255, 7);
                border: 1px solid rgba(130, 158, 181, 42);
                border-radius: 0;
                padding: 4px 20px 4px 7px;
                font-size: 8pt;
            }
            QComboBox#promptCombo:hover { border-color: rgba(126, 218, 247, 90); }
            QComboBox#promptCombo:disabled { color: #566574; }
            QToolButton#aiToggle, QToolButton#promptToolButton {
                color: #aebdca;
                background: transparent;
                border: 1px solid rgba(130, 158, 181, 42);
                border-radius: 0;
                padding: 5px 8px;
                font-size: 8pt;
            }
            QToolButton#aiToggle:hover, QToolButton#promptToolButton:hover {
                color: #8eddf6;
                border-color: rgba(126, 218, 247, 90);
                background-color: rgba(255, 255, 255, 10);
            }
            QToolButton#aiToggle:checked {
                color: #8eddf6;
                background-color: rgba(39, 116, 146, 75);
                border-color: rgba(126, 218, 247, 110);
            }
            QLabel#unresolvedLabel { color: #6e8192; font-size: 8pt; padding: 0 5px; }
            QLabel#unresolvedLabel[active="true"] { color: #f0bd72; }
            """
        )


def _replacement_text(directive: Directive, value: CompletionValue) -> str:
    if directive.kind in {DirectiveKind.INLINE, DirectiveKind.LINE_END}:
        if not value.text:
            label = "文中補完" if directive.kind == DirectiveKind.INLINE else "行末補完"
            raise CompletionError(f"{label}が空です。")
        return " ".join(value.text.splitlines()).strip()
    if directive.kind == DirectiveKind.PARAGRAPH:
        if not value.text.strip():
            raise CompletionError("段落補完が空です。")
        lines = value.text.strip().splitlines()
        return "\n".join(directive.indent + line.strip() for line in lines if line.strip())
    if directive.kind == DirectiveKind.LIST_SINGLE:
        if not value.text:
            raise CompletionError("リスト補完が空です。")
        return f"{directive.indent}- {' '.join(value.text.splitlines()).strip()}"
    if not value.items:
        raise CompletionError("複数リスト補完が空です。")
    return "\n".join(f"{directive.indent}- {item}" for item in value.items)
