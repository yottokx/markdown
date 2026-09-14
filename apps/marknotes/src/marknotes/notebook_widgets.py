"""Library navigation widgets, independent of storage and editor sessions.

All ranges are Python string offsets, with an exclusive end. Search snippets
carry a ``start`` offset into the note and relative ``matches`` within ``text``.
The caller owns persistence and supplies mappings, so these widgets never read
whole notes or run database queries on the GUI thread.
"""

from __future__ import annotations

import html
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QResizeEvent,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .ui_icons import outline_icon


def _navigation_colors(dark: bool) -> dict[str, str]:
    return {
        "chrome": "#20252d" if dark else "#f5f7fa",
        "base": "#171c24" if dark else "#ffffff",
        "text": "#e1e7ef" if dark else "#253047",
        "muted": "#a0adbf" if dark else "#67758a",
        "border": "#353e4b" if dark else "#dce2ea",
        "hover": "#2d3542" if dark else "#e8edf4",
        "selected": "#314968" if dark else "#e1ebfa",
        "accent": "#9fc2ff" if dark else "#315f9d",
    }


def _timestamp(value: object) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            pass
    return 0.0


def _local_date(value: object) -> tuple[str, str]:
    timestamp = _timestamp(value)
    if not timestamp:
        return "日付なし", ""
    try:
        local = datetime.fromtimestamp(timestamp, UTC).astimezone()
    except (OverflowError, OSError, ValueError):
        return "日付なし", ""
    return local.strftime("%Y年%m月%d日"), local.strftime("%H:%M")


def highlighted_text(text: str, ranges: Sequence[Sequence[int]] = ()) -> str:
    """Escape user text before adding markup; merge overlapping match ranges."""
    merged: list[list[int]] = []
    for start, end in sorted((max(0, int(a)), min(len(text), int(b))) for a, b in ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    pieces: list[str] = []
    position = 0
    for start, end in merged:
        pieces.append(html.escape(text[position:start]))
        pieces.append(
            '<span style="background-color:#f4cb62;color:#29220c;">'
            + html.escape(text[start:end])
            + "</span>"
        )
        position = end
    pieces.append(html.escape(text[position:]))
    return "".join(pieces).replace("\n", "<br>")


def _compact_excerpt(text: str, title: str) -> str:
    """Keep history summaries short while leaving stored Markdown untouched."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    first = re.sub(r"^#{1,6}\s*|\s+#+$", "", lines[0])
    first = re.sub(r"[*_`~]", "", first).strip()
    if first == title.strip():
        lines.pop(0)
    text = " ".join(lines)
    text = re.sub(r"!?\[([^]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= 120 else text[:119].rstrip() + "…"


class _NotebookDateCombo(QComboBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.arrow_color = QColor("#67758a")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # QSS-styled Windows combo boxes may omit the native drop arrow.
        # A tiny vector chevron stays legible in either theme and at any DPI.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(
                self.arrow_color,
                1.5,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        center_x, center_y = self.width() - 14, self.height() / 2
        path = QPainterPath()
        path.moveTo(center_x - 3, center_y - 1)
        path.lineTo(center_x, center_y + 2)
        path.lineTo(center_x + 3, center_y - 1)
        painter.drawPath(path)


class _NotebookTabBar(QTabBar):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._blank_press = False
        self.setDrawBase(False)

    # Let the titlebar receive clicks on its unused tab strip. Ignoring the
    # original event preserves Qt's coordinate mapping during parent delivery.
    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._blank_press = self.tabAt(event.position().toPoint()) < 0
        if self._blank_press:
            event.ignore()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._blank_press:
            event.ignore()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._blank_press:
            self._blank_press = False
            event.ignore()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self.tabAt(event.position().toPoint()) < 0:
            event.ignore()
            return
        super().mouseDoubleClickEvent(event)

    def tabSizeHint(self, index: int) -> QSize:
        size = super().tabSizeHint(index)
        return QSize(max(124, min(212, size.width())), 32)

    def minimumTabSizeHint(self, index: int) -> QSize:
        return self.tabSizeHint(index)

    def minimumSizeHint(self) -> QSize:
        # NotebookTabs selects overflow from its container's available width.
        # A minimum equal to all visible tabs would prevent the next shrink.
        return QSize(0, 32)


class NotebookTabs(QWidget):
    """Titlebar tabs with a stable logical order and least-recently-used overflow."""

    activated = Signal(str)
    close_requested = Signal(str)
    delete_requested = Signal(str)
    new_requested = Signal()
    close_others_requested = Signal(str)
    close_all_requested = Signal()
    reopen_requested = Signal()
    order_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._notes: dict[str, dict[str, Any]] = {}
        self._close_buttons: dict[str, QToolButton] = {}
        self._dark = False
        self.setObjectName("notebookTabs")
        self._active = ""
        self._can_reopen = False
        self._fitting = False
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.tab_bar = _NotebookTabBar(self)
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.setMovable(True)
        self.tab_bar.setObjectName("notebookTabBar")
        self.tab_bar.setTabsClosable(False)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setUsesScrollButtons(False)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.tab_bar.setMinimumWidth(0)
        self.tab_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.tab_bar.setAccessibleName("開いているノート")
        layout.addWidget(self.tab_bar)
        self.new_button = QToolButton(self)
        self.new_button.setObjectName("notebookNewTab")
        self.new_button.setText("+")
        self.new_button.setAutoRaise(True)
        self.new_button.setToolTip("新しいノート (Ctrl+T)")
        self.new_button.setAccessibleName("新しいノート")
        self.new_button.setFixedSize(30, 30)
        self.new_button.clicked.connect(self.new_requested)
        layout.addWidget(self.new_button)
        self.overflow_button = QToolButton(self)
        self.overflow_button.setObjectName("notebookOverflow")
        self.overflow_button.setText("…")
        self.overflow_button.setAutoRaise(True)
        self.overflow_button.setFixedHeight(30)
        self.overflow_button.setMinimumWidth(34)
        self.overflow_button.setToolTip("タブの一覧と操作")
        self.overflow_button.setAccessibleName("タブの一覧と操作")
        self.overflow_button.clicked.connect(self._show_overflow)
        layout.addWidget(self.overflow_button)
        # Keep both actions beside the last visible tab, with all spare titlebar
        # space after them available for dragging the window.
        layout.addStretch(1)
        self.tab_bar.currentChanged.connect(self._current_changed)
        self.tab_bar.tabCloseRequested.connect(self._close_index)
        self.tab_bar.tabMoved.connect(self._tab_moved)
        self.tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_bar.customContextMenuRequested.connect(self._show_tab_context)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_empty_context)
        self.apply_theme(False)

    def apply_theme(self, dark: bool) -> None:
        self._dark = dark
        c = _navigation_colors(dark)
        self.setStyleSheet(f"""
            QWidget#notebookTabs {{ background: transparent; }}
            QTabBar#notebookTabBar {{ background: transparent; border: none; }}
            QTabBar#notebookTabBar::tab {{
                background: transparent; color: {c["muted"]};
                border: 1px solid transparent; border-radius: 6px;
                padding: 0px 10px; margin-right: 3px;
            }}
            QTabBar#notebookTabBar::tab:hover {{
                background: {c["hover"]}; color: {c["text"]};
            }}
            QTabBar#notebookTabBar::tab:selected {{
                background: {c["base"]}; color: {c["text"]};
                border-color: {c["border"]};
            }}
            QWidget#notebookTabs QToolButton {{
                background: transparent; color: {c["text"]}; border: none;
                border-radius: 5px; padding: 0px;
            }}
            QToolButton#notebookNewTab {{ font-size: 18px; }}
            QToolButton#notebookOverflow {{ font-size: 16px; }}
            QWidget#notebookTabs QToolButton:hover {{ background: {c["hover"]}; }}
            QWidget#notebookTabs QToolButton:pressed {{ background: {c["selected"]}; }}
        """)
        for button in self._close_buttons.values():
            button.setIcon(outline_icon("close", c["muted"]))
        self._fit_tabs()

    def _make_close_button(self, note_id: str, index: int) -> None:
        button = QToolButton(self.tab_bar)
        button.setObjectName("notebookTabClose")
        button.setAutoRaise(True)
        button.setFixedSize(22, 22)
        button.setIconSize(QSize(14, 14))
        button.setIcon(outline_icon("close", _navigation_colors(self._dark)["muted"]))
        button.setToolTip("タブを閉じる (Ctrl+W)")
        button.setAccessibleName("タブを閉じる")
        button.clicked.connect(lambda _checked=False: self.close_requested.emit(note_id))
        self.tab_bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)
        self._close_buttons[note_id] = button

    @property
    def note_ids(self) -> list[str]:
        return [self.tab_bar.tabData(i) for i in range(self.tab_bar.count())]

    @property
    def visible_note_ids(self) -> list[str]:
        return [note_id for i, note_id in enumerate(self.note_ids) if self.tab_bar.isTabVisible(i)]

    @property
    def hidden_note_ids(self) -> list[str]:
        return [
            note_id for i, note_id in enumerate(self.note_ids) if not self.tab_bar.isTabVisible(i)
        ]

    def set_notes(self, notes: Sequence[Mapping[str, Any]]) -> None:
        """Replace metadata/order without emitting user-intent signals."""
        ids = [str(note["id"]) for note in notes]
        if len(set(ids)) != len(ids):
            raise ValueError("A note can only appear once in the tab bar")
        self._notes = {str(note["id"]): dict(note) for note in notes}
        with QSignalBlocker(self.tab_bar):
            if ids != self.note_ids:
                self._close_buttons.clear()
                while self.tab_bar.count():
                    self.tab_bar.removeTab(self.tab_bar.count() - 1)
                for note_id in ids:
                    index = self.tab_bar.addTab("")
                    self.tab_bar.setTabData(index, note_id)
                    self._make_close_button(note_id, index)
            for index, note_id in enumerate(ids):
                note = self._notes[note_id]
                title = str(note.get("title") or "新しいノート")
                self._close_buttons[note_id].setAccessibleName(f"{title} のタブを閉じる")
                self.tab_bar.setTabText(index, ("● " if note.get("pinned") else "") + title)
                date, clock = _local_date(note.get("content_updated_at", note.get("last_active")))
                self.tab_bar.setTabToolTip(index, f"{title}\n{date} {clock}".strip())
            if self._active not in ids:
                self._active = ids[0] if ids else ""
            if self._active:
                self.tab_bar.setCurrentIndex(ids.index(self._active))
        self._fit_tabs()

    def set_active(self, note_id: str) -> None:
        if note_id not in self._notes:
            return
        if note_id != self._active:
            self._notes[note_id]["last_active"] = time.time()
        self._active = note_id
        with QSignalBlocker(self.tab_bar):
            index = self.note_ids.index(note_id)
            self.tab_bar.setTabVisible(index, True)
            self.tab_bar.setCurrentIndex(index)
        self._fit_tabs()

    def set_can_reopen(self, enabled: bool) -> None:
        self._can_reopen = bool(enabled)

    def _current_changed(self, index: int) -> None:
        note_id = self.tab_bar.tabData(index) if index >= 0 else None
        if note_id in self._notes:
            self._active = note_id
            self._notes[note_id]["last_active"] = time.time()
            self._fit_tabs()
            self.activated.emit(note_id)

    def _close_index(self, index: int) -> None:
        if 0 <= index < self.tab_bar.count():
            self.close_requested.emit(self.tab_bar.tabData(index))

    def _tab_moved(self, _old: int, _new: int) -> None:
        self.order_changed.emit(self.note_ids)
        self._fit_tabs()

    def _fit_tabs(self) -> None:
        if self._fitting:
            return
        self._fitting = True
        try:
            ids = self.note_ids
            # Reserve enough for a count badge before choosing hidden tabs. A
            # changing button width must not oscillate the chosen visible set.
            overflow_width = max(
                34, self.overflow_button.fontMetrics().horizontalAdvance(f"… {len(ids)}") + 14
            )
            self.overflow_button.setFixedWidth(overflow_width)
            available = max(1, self.width() - 30 - overflow_width - 4)
            widths = {note_id: self.tab_bar.tabSizeHint(i).width() for i, note_id in enumerate(ids)}
            visible = set(ids)
            used = sum(widths.values())
            older = sorted(
                (note_id for note_id in ids if note_id != self._active),
                key=lambda note_id: _timestamp(self._notes[note_id].get("last_active")),
            )
            for note_id in older:
                if used <= available:
                    break
                visible.remove(note_id)
                used -= widths[note_id]
            with QSignalBlocker(self.tab_bar):
                for index, note_id in enumerate(ids):
                    self.tab_bar.setTabVisible(index, note_id in visible)
                if self._active in ids:
                    self.tab_bar.setCurrentIndex(ids.index(self._active))
                # Visibility changes from resizeEvent can retain cached positions
                # for the custom tab buttons. Rebuild the completed tab layout
                # once so visible tabs are compact before Qt paints them again.
                self.tab_bar.setExpanding(True)
                self.tab_bar.setExpanding(False)
            self.tab_bar.setMaximumWidth(min(used, available))
            self.tab_bar.updateGeometry()
            self.layout().activate()
            hidden_count = len(ids) - len(visible)
            self.overflow_button.setText(f"… {hidden_count}" if hidden_count else "…")
            self.overflow_button.setToolTip(
                f"非表示のタブ {hidden_count} 件 / タブの操作" if hidden_count else "タブの操作"
            )
        finally:
            self._fitting = False

    def build_context_menu(self, note_id: str | None = None) -> QMenu:
        menu = QMenu(self)
        if note_id in self._notes:
            action = menu.addAction("タブを閉じる")
            action.triggered.connect(lambda: self.close_requested.emit(note_id))
            action = menu.addAction("ほかのタブを閉じる")
            action.setEnabled(len(self._notes) > 1)
            action.triggered.connect(lambda: self.close_others_requested.emit(note_id))
        action = menu.addAction("すべてのタブを閉じる")
        action.setEnabled(bool(self._notes))
        action.triggered.connect(self.close_all_requested)
        menu.addSeparator()
        action = menu.addAction("閉じたタブを開く\tCtrl+Shift+T")
        action.setEnabled(self._can_reopen)
        action.triggered.connect(self.reopen_requested)
        if note_id in self._notes:
            menu.addSeparator()
            action = menu.addAction("ノートを削除…")
            action.triggered.connect(lambda: self._request_delete(note_id))
        return menu

    def _request_delete(self, note_id: str) -> None:
        if note_id in self._notes:
            self.delete_requested.emit(note_id)

    def _show_tab_context(self, point: QPoint) -> None:
        index = self.tab_bar.tabAt(point)
        note_id = self.tab_bar.tabData(index) if index >= 0 else None
        menu = self.build_context_menu(note_id)
        menu.exec(self.tab_bar.mapToGlobal(point))
        menu.deleteLater()

    def _show_empty_context(self, point: QPoint) -> None:
        menu = self.build_context_menu()
        menu.exec(self.mapToGlobal(point))
        menu.deleteLater()

    def _show_overflow(self) -> None:
        menu = self.build_context_menu(self._active or None)
        first_action = menu.actions()[0]
        for note_id in self.hidden_note_ids:
            note = self._notes[note_id]
            title = str(note.get("title") or "新しいノート").replace("&", "&&")
            action = menu.addAction(("● " if note.get("pinned") else "") + title)
            action.triggered.connect(
                lambda _checked=False, value=note_id: self.activated.emit(value)
            )
            menu.removeAction(action)
            menu.insertAction(first_action, action)
        if self.hidden_note_ids:
            menu.insertSeparator(first_action)
        menu.exec(self.overflow_button.mapToGlobal(QPoint(0, self.overflow_button.height())))
        menu.deleteLater()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_tabs()


class _ResultCard(QFrame):
    activated = Signal(str, object)
    more_requested = Signal(str)
    context_requested = Signal(str, QPoint)

    def __init__(
        self, result: Mapping[str, Any], parent: QWidget | None = None, *, dark: bool = False
    ):
        super().__init__(parent)
        self.result = dict(result)
        self.note_id = str(result["id"])
        snippets = list(result.get("snippets", []))
        self.match_start: int | None = None
        if snippets:
            first = snippets[0]
            matches = first.get("matches", [])
            self.match_start = int(first.get("start", 0)) + (int(matches[0][0]) if matches else 0)
        self.setObjectName("notebookResultCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(6)
        self.label = QLabel(self)
        self.label.setObjectName("notebookResultText")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.label.setOpenExternalLinks(False)
        self.label.linkActivated.connect(self._link_activated)
        title = str(result.get("title") or "新しいノート")
        self.label.setAccessibleName(title)
        layout.addWidget(self.label)
        self.excerpt_label = QLabel(self)
        self.excerpt_label.setObjectName("notebookResultExcerpt")
        self.excerpt_label.setTextFormat(Qt.TextFormat.PlainText)
        self.excerpt_label.setWordWrap(False)
        self._excerpt = (
            _compact_excerpt(str(result.get("excerpt", "")), title) if not snippets else ""
        )
        self.excerpt_label.setVisible(bool(self._excerpt))
        layout.addWidget(self.excerpt_label)
        self.more_button = QPushButton(self)
        self.more_button.setObjectName("notebookResultMore")
        covered = sum(len(snippet.get("matches", [])) for snippet in snippets)
        remaining = max(0, int(result.get("total_matches", 0)) - covered)
        self.more_button.setText(f"ほかの一致を表示（残り {remaining} 件）")
        self.more_button.setMinimumHeight(28)
        self.more_button.setVisible(remaining > 0)
        self.more_button.clicked.connect(self._request_more)
        layout.addWidget(self.more_button)
        self.setAccessibleName(title)
        for widget in (self, self.label, self.excerpt_label, self.more_button):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda point, target=widget: self.context_requested.emit(
                    self.note_id, target.mapToGlobal(point)
                )
            )
        self.apply_theme(dark)

    def apply_theme(self, dark: bool) -> None:
        c = _navigation_colors(dark)
        self.setStyleSheet(f"""
            QFrame#notebookResultCard {{
                background: transparent; border: none; border-radius: 6px;
            }}
            QFrame#notebookResultCard:hover {{ background: {c["hover"]}; }}
            QLabel#notebookResultText {{
                background: transparent; color: {c["text"]}; border: none;
            }}
            QLabel#notebookResultExcerpt {{
                background: transparent; color: {c["muted"]}; border: none;
            }}
            QPushButton#notebookResultMore {{
                background: transparent; color: {c["accent"]}; border: none;
                border-radius: 4px; padding: 3px 6px; text-align: left;
            }}
            QPushButton#notebookResultMore:hover {{ background: {c["selected"]}; }}
            QPushButton#notebookResultMore:disabled {{ color: {c["muted"]}; }}
        """)
        result = self.result
        title_html = highlighted_text(
            str(result.get("title") or "新しいノート"), result.get("title_matches", [])
        )
        if result.get("pinned"):
            title_html = "● " + title_html
        _, clock = _local_date(result.get("content_updated_at", result.get("created_at")))
        count = int(result.get("total_matches", 0))
        metadata = " · ".join(part for part in (clock, f"{count} 件一致" if count else "") if part)
        # Qt's rich-text parser does not reliably resolve CSS color:inherit on
        # links. Resolve every text color, including links, for the active theme.
        # User content remains escaped and never enters href or style values.
        title_style = f"text-decoration:none;color:{c['text']}"
        snippet_style = f"text-decoration:none;color:{c['muted']}"
        lines = [f'<a href="note" style="{title_style}"><b>{title_html}</b></a>']
        if metadata:
            lines.append(
                f'<span style="font-size:small;color:{c["muted"]}">{html.escape(metadata)}</span>'
            )
        snippets = result.get("snippets", [])
        if snippets:
            for snippet in snippets:
                text = str(snippet.get("text", ""))
                matches = snippet.get("matches", [])
                offset = int(snippet.get("start", 0)) + (int(matches[0][0]) if matches else 0)
                content = highlighted_text(text, matches)
                lines.append(f'<a href="match:{offset}" style="{snippet_style}">{content}</a>')
        self.label.setText("<br>".join(lines))

    def fit_excerpt(self, width: int) -> None:
        if not self._excerpt:
            return
        text_layout = QTextLayout(self._excerpt, self.excerpt_label.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        text_layout.setTextOption(option)
        text_layout.beginLayout()
        rendered = []
        for index in range(2):
            line = text_layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            start, length = line.textStart(), line.textLength()
            if index == 1:
                text = self.excerpt_label.fontMetrics().elidedText(
                    self._excerpt[start:], Qt.TextElideMode.ElideRight, width
                )
            else:
                text = self._excerpt[start : start + length].rstrip()
            rendered.append(text)
        text_layout.endLayout()
        self.excerpt_label.setText("\n".join(rendered))

    def _request_more(self) -> None:
        self.more_button.setEnabled(False)
        self.more_requested.emit(self.note_id)

    def _link_activated(self, link: str) -> None:
        if link == "note":
            self.activated.emit(self.note_id, self.match_start)
        elif link.startswith("match:"):
            try:
                offset = int(link[6:])
            except ValueError:
                return
            self.activated.emit(self.note_id, offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.note_id, self.match_start)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class NotebookSidebar(QWidget):
    """Overlay/fixed panel contents. The window owns positioning and dismissal."""

    note_requested = Signal(str, object)
    delete_requested = Signal(str)
    query_changed = Signal(str)
    mode_changed = Signal(str)
    date_order_changed = Signal(str)
    pinned_changed = Signal(bool)
    more_requested = Signal()
    more_matches_requested = Signal(str)
    MODES = ("history", "pinned", "search")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.mode = "history"
        self._dark = False
        self._last_group = ""
        self._has_more = False
        self._cards: list[tuple[QListWidgetItem, _ResultCard]] = []
        self.setObjectName("notebookSidebar")
        self.setMinimumWidth(250)
        self.resize(320, 500)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.mode_bar = QTabBar(self)
        self.mode_bar.setObjectName("notebookModeBar")
        self.mode_bar.setDrawBase(False)
        self.mode_bar.setFixedHeight(32)
        self.mode_bar.setExpanding(True)
        self.mode_bar.setDocumentMode(True)
        for title in ("履歴", "ピン", "検索"):
            self.mode_bar.addTab(title)
        self.mode_bar.setAccessibleName("ノートの探し方")
        self.mode_bar.currentChanged.connect(self._mode_selected)
        header.addWidget(self.mode_bar, 1)
        self.fixed_button = QToolButton(self)
        self.fixed_button.setObjectName("notebookFixedButton")
        self.fixed_button.setText("固定")
        self.fixed_button.setFixedSize(42, 32)
        self.fixed_button.setAutoRaise(True)
        self.fixed_button.setCheckable(True)
        self.fixed_button.setToolTip("サイドバーを固定表示")
        self.fixed_button.setAccessibleName("サイドバーを固定表示")
        self.fixed_button.toggled.connect(self.pinned_changed)
        header.addWidget(self.fixed_button)
        layout.addLayout(header)
        self.date_combo = _NotebookDateCombo(self)
        self.date_combo.setFixedHeight(32)
        self.date_combo.addItem("更新日ごと", "updated")
        self.date_combo.addItem("作成日ごと", "created")
        self.date_combo.setAccessibleName("日付の分類")
        self.date_combo.currentIndexChanged.connect(
            lambda _index: self.date_order_changed.emit(self.date_combo.currentData())
        )
        layout.addWidget(self.date_combo)
        self.search_edit = QLineEdit(self)
        self.search_edit.setFixedHeight(32)
        self.search_edit.setPlaceholderText("すべてのノートを検索")
        self.search_edit.setAccessibleName("すべてのノートを検索")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._query_edited)
        self.search_edit.returnPressed.connect(self._submit_query)
        self.search_edit.hide()
        layout.addWidget(self.search_edit)
        self.search_hint = QLabel("1〜2文字の検索は Enter で実行", self)
        self.search_hint.setObjectName("notebookSearchHint")
        self.search_hint.setWordWrap(True)
        self.search_hint.hide()
        layout.addWidget(self.search_hint)
        self.status_label = QLabel(self)
        self.status_label.setObjectName("notebookResultStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status_label)
        self.results = QListWidget(self)
        self.results.setObjectName("notebookResults")
        self.results.setFrameShape(QFrame.Shape.NoFrame)
        self._card_resize_timer = QTimer(self)
        self._card_resize_timer.setSingleShot(True)
        self._card_resize_timer.timeout.connect(self._resize_cards)
        self.results.viewport().installEventFilter(self)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results.setSpacing(2)
        self.results.setAccessibleName("ノート一覧")
        self.results.itemActivated.connect(self._item_activated)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._show_result_context)
        layout.addWidget(self.results, 1)
        self.more_button = QPushButton("さらに表示", self)
        self.more_button.setObjectName("notebookMoreResults")
        self.more_button.setMinimumHeight(30)
        self.more_button.clicked.connect(self.more_requested)
        self.more_button.hide()
        layout.addWidget(self.more_button)
        self._query_timer = QTimer(self)
        self._query_timer.setSingleShot(True)
        self._query_timer.setInterval(250)
        self._query_timer.timeout.connect(self._submit_query)
        self.apply_theme(False)

    def apply_theme(self, dark: bool) -> None:
        self._dark = dark
        c = _navigation_colors(dark)
        self.date_combo.arrow_color = QColor(c["muted"])
        self.date_combo.update()
        palette = QPalette(self.palette())
        for role, color in {
            QPalette.ColorRole.Window: c["chrome"],
            QPalette.ColorRole.Base: c["chrome"],
            QPalette.ColorRole.WindowText: c["text"],
            QPalette.ColorRole.Text: c["text"],
            QPalette.ColorRole.Button: c["chrome"],
            QPalette.ColorRole.ButtonText: c["text"],
            QPalette.ColorRole.PlaceholderText: c["muted"],
            QPalette.ColorRole.Highlight: c["selected"],
            QPalette.ColorRole.HighlightedText: c["text"],
        }.items():
            palette.setColor(role, QColor(color))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["muted"]))
        self.setPalette(palette)
        self.setStyleSheet(f"""
            QWidget#notebookSidebar {{
                background: {c["chrome"]}; color: {c["text"]};
                border: none; border-right: 1px solid {c["border"]};
            }}
            QTabBar#notebookModeBar {{
                background: {c["hover"]}; border: none; border-radius: 6px;
            }}
            QTabBar#notebookModeBar::tab {{
                background: transparent; color: {c["muted"]};
                border: none; border-radius: 4px; margin: 3px; padding: 4px 6px;
            }}
            QTabBar#notebookModeBar::tab:hover {{ color: {c["text"]}; }}
            QTabBar#notebookModeBar::tab:selected {{
                background: {c["base"]}; color: {c["text"]};
            }}
            QToolButton#notebookFixedButton {{
                background: transparent; color: {c["muted"]};
                border: 1px solid {c["border"]}; border-radius: 6px; padding: 0px;
            }}
            QToolButton#notebookFixedButton:hover {{ background: {c["hover"]}; }}
            QToolButton#notebookFixedButton:checked {{
                background: {c["selected"]}; color: {c["accent"]};
            }}
            QWidget#notebookSidebar QLineEdit, QWidget#notebookSidebar QComboBox {{
                background: {c["base"]}; color: {c["text"]};
                border: 1px solid {c["border"]}; border-radius: 6px;
                padding: 0px 8px; selection-background-color: {c["selected"]};
                selection-color: {c["text"]};
            }}
            QWidget#notebookSidebar QLineEdit:focus,
            QWidget#notebookSidebar QComboBox:focus {{ border-color: {c["accent"]}; }}
            QWidget#notebookSidebar QComboBox::drop-down {{
                background: transparent; border: none; width: 24px;
            }}
            QWidget#notebookSidebar QComboBox::down-arrow {{ image: none; width: 0px; }}
            QWidget#notebookSidebar QComboBox QAbstractItemView {{
                background: {c["base"]}; color: {c["text"]};
                border: 1px solid {c["border"]}; outline: none;
                selection-background-color: {c["selected"]}; selection-color: {c["text"]};
            }}
            QLabel#notebookSearchHint, QLabel#notebookResultStatus {{
                background: transparent; color: {c["muted"]}; border: none;
            }}
            QListWidget#notebookResults {{
                background: {c["chrome"]}; color: {c["muted"]};
                border: none; outline: none; padding: 0px;
            }}
            QListWidget#notebookResults::item {{
                background: transparent; border: none; border-radius: 6px;
            }}
            QListWidget#notebookResults::item:selected {{ background: {c["selected"]}; }}
            QPushButton#notebookMoreResults {{
                background: transparent; color: {c["accent"]};
                border: 1px solid {c["border"]}; border-radius: 6px; padding: 4px 8px;
            }}
            QPushButton#notebookMoreResults:hover {{ background: {c["hover"]}; }}
        """)
        for index in range(self.results.count()):
            item = self.results.item(index)
            if item.data(Qt.ItemDataRole.UserRole) is None:
                item.setForeground(QColor(c["muted"]))
        for _item, card in self._cards:
            card.apply_theme(dark)
        self._resize_cards()

    def set_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError(f"Unknown sidebar mode: {mode}")
        self.mode_bar.setCurrentIndex(self.MODES.index(mode))
        self._update_mode_widgets()

    def _mode_selected(self, index: int) -> None:
        self.mode = self.MODES[index]
        self._query_timer.stop()
        self._update_mode_widgets()
        self.mode_changed.emit(self.mode)

    def _update_mode_widgets(self) -> None:
        self.date_combo.setVisible(self.mode == "history")
        self.search_edit.setVisible(self.mode == "search")
        self.search_hint.setVisible(self.mode == "search")
        if self.mode == "search":
            self.search_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_query(self, query: str, *, emit: bool = False) -> None:
        self._query_timer.stop()
        with QSignalBlocker(self.search_edit):
            self.search_edit.setText(query)
        if emit:
            self._submit_query()

    def set_fixed(self, fixed: bool) -> None:
        with QSignalBlocker(self.fixed_button):
            self.fixed_button.setChecked(fixed)

    def _query_edited(self, query: str) -> None:
        self._query_timer.stop()
        if not query:
            self.query_changed.emit("")
        elif len(unicodedata.normalize("NFKC", query).casefold()) >= 3:
            self._query_timer.start()
        else:
            self.status_label.setText("Enter で検索")

    def _submit_query(self) -> None:
        self._query_timer.stop()
        self.query_changed.emit(self.search_edit.text())

    def set_results(
        self,
        results: Sequence[Mapping[str, Any]],
        *,
        append: bool = False,
        has_more: bool = False,
    ) -> None:
        if not append:
            self.results.clear()
            self._cards.clear()
            self._last_group = ""
        for result in results:
            if self.mode == "history":
                field = (
                    "created_at"
                    if self.date_combo.currentData() == "created"
                    else "content_updated_at"
                )
                date, _clock = _local_date(result.get(field))
                if date != self._last_group:
                    header = QListWidgetItem(date, self.results)
                    header.setFlags(Qt.ItemFlag.NoItemFlags)
                    font = header.font()
                    font.setBold(True)
                    header.setFont(font)
                    header.setSizeHint(QSize(100, 30))
                    header.setForeground(QColor(_navigation_colors(self._dark)["muted"]))
                    self._last_group = date
            item = QListWidgetItem(self.results)
            item.setData(Qt.ItemDataRole.UserRole, str(result["id"]))
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole, str(result.get("title") or "新しいノート")
            )
            card = _ResultCard(result, self.results, dark=self._dark)
            card.activated.connect(self.note_requested)
            card.more_requested.connect(self.more_matches_requested)
            card.context_requested.connect(self._show_note_context)
            self.results.setItemWidget(item, card)
            self._cards.append((item, card))
        self._has_more = has_more
        self.more_button.setVisible(has_more)
        self.more_button.setEnabled(True)
        self.results.setEnabled(True)
        self.status_label.setText(
            f"{len(self._cards)} 件{'以上' if has_more else ''}"
            if self._cards
            else ("一致するノートはありません" if self.mode == "search" else "ノートはありません")
        )
        self._resize_cards()

    def set_note_snippets(
        self, note_id: str, snippets: Sequence[Mapping[str, Any]], total_matches: int
    ) -> None:
        """Replace one note's displayed snippets without resetting result order.

        The caller supplies all snippets to keep visible, including those already
        loaded. A stale response for a note outside the current result set is
        ignored; the caller still owns query-generation validation.
        """
        for index, (item, card) in enumerate(self._cards):
            if card.note_id != note_id:
                continue
            position = self.results.verticalScrollBar().value()
            result = {**card.result, "snippets": list(snippets), "total_matches": total_matches}
            replacement = _ResultCard(result, self.results, dark=self._dark)
            replacement.activated.connect(self.note_requested)
            replacement.more_requested.connect(self.more_matches_requested)
            replacement.context_requested.connect(self._show_note_context)
            self.results.setItemWidget(item, replacement)
            self._cards[index] = (item, replacement)
            self._resize_cards()
            self.results.verticalScrollBar().setValue(position)
            break

    def set_busy(self, busy: bool) -> None:
        self.more_button.setEnabled(not busy)
        if busy:
            self.status_label.setText("検索中…" if self.mode == "search" else "読み込み中…")

    def set_error(self, message: str) -> None:
        self.status_label.setText(message)
        self.more_button.setEnabled(True)

    def build_context_menu(self, note_id: str | None = None) -> QMenu:
        menu = QMenu(self)
        if any(card.note_id == note_id for _item, card in self._cards):
            action = menu.addAction("ノートを削除…")
            action.triggered.connect(lambda: self._request_delete(note_id))
        return menu

    def _request_delete(self, note_id: str) -> None:
        if any(card.note_id == note_id for _item, card in self._cards):
            self.delete_requested.emit(note_id)

    def _show_note_context(self, note_id: str, global_point: QPoint) -> None:
        for item, card in self._cards:
            if card.note_id == note_id:
                self.results.setCurrentItem(item)
                menu = self.build_context_menu(note_id)
                menu.exec(global_point)
                menu.deleteLater()
                break

    def _show_result_context(self, point: QPoint) -> None:
        item = self.results.itemAt(point)
        if item is not None:
            note_id = item.data(Qt.ItemDataRole.UserRole)
            if note_id is not None:
                self._show_note_context(note_id, self.results.viewport().mapToGlobal(point))

    def _item_activated(self, item: QListWidgetItem) -> None:
        for target, card in self._cards:
            if target is item:
                self.note_requested.emit(card.note_id, card.match_start)
                break

    def _resize_cards(self) -> None:
        width = max(100, self.results.viewport().width() - 8)
        for item, card in self._cards:
            card.setFixedWidth(width)
            inner_width = max(50, width - 20)
            height = max(48, card.label.heightForWidth(inner_width) + 18)
            if not card.excerpt_label.isHidden():
                card.fit_excerpt(inner_width)
                height += card.excerpt_label.sizeHint().height() + card.layout().spacing()
            if not card.more_button.isHidden():
                height += card.more_button.sizeHint().height() + card.layout().spacing()
            item.setSizeHint(QSize(width, height))

    def eventFilter(self, watched, event) -> bool:
        if watched is self.results.viewport() and event.type() == QEvent.Type.Resize:
            self._card_resize_timer.start(0)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "results"):
            self._resize_cards()


Sidebar = NotebookSidebar
