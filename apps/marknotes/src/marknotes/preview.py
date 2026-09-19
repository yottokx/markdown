"""Local WebEngine preview with revision-aware source-coordinate scrolling.

``PreviewPane.set_document`` takes a sanitized HTML fragment whose blocks carry
``data-source-line`` and exclusive ``data-source-end`` attributes. The public
scroll coordinate is a zero-based logical source line plus fractional progress.
``source_scrolled`` is emitted for user navigation only; programmatic positioning
and layout corrections do not feed back into the source editor.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QContextMenuEvent, QDesktopServices
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEngineContextMenuRequest,
    QWebEnginePage,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMenu, QVBoxLayout, QWidget
from shiboken6 import isValid

from .image_sources import resolve_srcsets
from .link_schemes import is_external_link
from .managed_assets import resolve_managed_asset


class _PreviewPage(QWebEnginePage):
    console_error = Signal(str)

    def __init__(self, shell_url: QUrl, parent: QObject) -> None:
        super().__init__(parent)
        self._shell_url = shell_url
        # Enabled only by the notebook shell for the currently displayed note.
        self.managed_assets_base: Path | None = None
        self.allow_local_links = False

    def _managed_link(self, url: QUrl) -> Path | None:
        if self.managed_assets_base is None or not url.isLocalFile():
            return None
        try:
            base = Path(self.managed_assets_base).absolute()
            relative = Path(url.toLocalFile()).absolute().relative_to(base).as_posix()
            return resolve_managed_asset(quote(relative, safe="/-._~"), base)
        except (OSError, ValueError):
            return None

    def _local_link(self, url: QUrl) -> Path | None:
        if not self.allow_local_links or not url.isValid() or not url.isLocalFile():
            return None
        try:
            path = Path(url.toLocalFile())
            if path.is_absolute() and (path.is_file() or path.is_dir()):
                return path
        except (OSError, ValueError):
            pass
        return None

    def _is_shell_url(self, url: QUrl) -> bool:
        return url.isLocalFile() and os.path.normcase(url.toLocalFile()) == os.path.normcase(
            self._shell_url.toLocalFile()
        )

    def can_open_context_link(self, url: QUrl) -> bool:
        return (
            self._is_shell_url(url)
            or (url.isValid() and is_external_link(url.toString()))
            or self._managed_link(url) is not None
            or self._local_link(url) is not None
        )

    def acceptNavigationRequest(
        self, url: QUrl, navigation_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        if not is_main_frame:
            return False
        if self._is_shell_url(url):
            return True
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if url.isValid() and is_external_link(url.toString()):
                QDesktopServices.openUrl(url)
            elif (asset := self._managed_link(url) or self._local_link(url)) is not None:
                # Only an explicit click opens a local file or folder.
                # Keep it out of the embedded browser and discard URL parameters.
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(asset)))
        return False

    def open_context_link(self, url: QUrl) -> None:
        # Explicit context-menu navigation follows exactly the ordinary link
        # policy. Native OpenLinkInThisWindow is inert for external links in
        # this restricted local preview, so use its existing navigation gate.
        if self.acceptNavigationRequest(
            url, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True
        ):
            self.setUrl(url)

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.console_error.emit(f"Preview: {message} ({line_number})")


class _PreviewView(QWebEngineView):
    """A document context menu using the page's current native editing actions."""

    def _create_context_menu(self, request: QWebEngineContextMenuRequest | None) -> QMenu:
        menu = QMenu(self)
        menu.setObjectName("previewContextMenu")

        def add_action(action_type: QWebEnginePage.WebAction, label: str):
            action = self.pageAction(action_type)
            action.setText(label)
            menu.addAction(action)
            return action

        # Keep the same relative order as the application's Edit menu.
        add_action(QWebEnginePage.WebAction.Copy, "コピー")
        add_action(QWebEnginePage.WebAction.SelectAll, "すべて選択")
        if request is None:
            return menu
        if not request.linkUrl().isEmpty():
            menu.addSeparator()
            open_link = menu.addAction("リンクを開く")
            target = QUrl(request.linkUrl())
            open_link.setEnabled(self.page().can_open_context_link(target))
            open_link.triggered.connect(
                lambda checked=False, url=target: self.page().open_context_link(url)
            )
            add_action(QWebEnginePage.WebAction.CopyLinkToClipboard, "リンクURLをコピー")
        if request.mediaType() in {
            QWebEngineContextMenuRequest.MediaType.MediaTypeImage,
            QWebEngineContextMenuRequest.MediaType.MediaTypeCanvas,
        }:
            menu.addSeparator()
            add_action(QWebEnginePage.WebAction.CopyImageToClipboard, "画像をコピー")
            if not request.mediaUrl().isEmpty():
                add_action(QWebEnginePage.WebAction.CopyImageUrlToClipboard, "画像URLをコピー")
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        request = self.lastContextMenuRequest()
        if request is not None:
            request.setAccepted(True)
        menu = self._create_context_menu(request)
        # WebAction dispatch keeps Copy tied to the preview selection and
        # link navigation subject to _PreviewPage.acceptNavigationRequest.
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(event.globalPos())
        event.accept()


class _PreviewBridge(QObject):
    def __init__(self, preview: PreviewPane) -> None:
        super().__init__(preview)
        self._preview = preview

    @Slot()
    def shellReady(self) -> None:
        self._preview._on_shell_ready()

    @Slot(int)
    def documentReady(self, revision: int) -> None:
        if revision == self._preview._revision:
            self._preview._send_source_selection()
            self._preview.ready.emit(revision)

    @Slot(int, int, int, int)
    def selectionChanged(self, anchor: int, position: int, revision: int, sequence: int) -> None:
        preview = self._preview
        cleared = anchor == position == -1
        if (
            revision == preview._revision
            and sequence == preview._selection_sequence
            and (cleared or 0 <= anchor <= preview._source_length)
            and (cleared or 0 <= position <= preview._source_length)
        ):
            preview.selection_changed.emit(anchor, position, revision, sequence)

    @Slot(str, int, result=bool)
    def copyCode(self, text: str, revision: int) -> bool:
        # Keep browser clipboard access disabled. Only the current document's
        # explicit copy-button action passes its original, unformatted source.
        if revision != self._preview._revision or not text:
            return False
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        return True

    @Slot(int, int, bool, int, result=bool)
    def toggleTask(self, line: int, column: int, checked: bool, revision: int) -> bool:
        preview = self._preview
        handler = preview._task_toggle_handler
        if (
            revision != preview._revision
            or not 0 <= line < preview._line_count
            or column < 0
            or handler is None
        ):
            return False
        return bool(handler(line, column, checked, revision))

    @Slot(float, int)
    def sourceScrolled(self, position: float, revision: int) -> None:
        preview = self._preview
        if (
            revision == preview._revision
            and math.isfinite(position)
            and preview._sync_enabled
            and preview._pending_view_restore is None
        ):
            preview.source_scrolled.emit(
                max(0.0, min(position, float(preview._line_count) - 1e-6)), revision
            )

    @Slot(float, int, int)
    def viewRestored(self, position: float, revision: int, token: int) -> None:
        preview = self._preview
        pending = preview._pending_view_restore
        if (
            pending is not None
            and pending[1:] == (revision, token)
            and revision == preview._revision
            and math.isfinite(position)
        ):
            preview._pending_view_restore = None
            preview.view_restored.emit(
                max(0.0, min(position, float(preview._line_count) - 1e-6)), revision, token
            )

    @Slot(int)
    def zoomRestored(self, token: int) -> None:
        if token == self._preview._pending_zoom_token:
            self._preview._pending_zoom_token = None


class PreviewPane(QWidget):
    """Preview widget; ``view`` exposes the QWebEngineView for diagnostics.

    ``ready(revision)`` fires after a new fragment's first measured layout.
    Images, fonts and resizes subsequently rebuild the map while preserving its
    source position. For integration tests, ``window.previewApi.metrics()``
    returns revision, anchors, source, measuredSource, maxSource, scrollY,
    maxScroll and viewportHeight through ``view.page().runJavaScript(...)``.
    """

    source_scrolled = Signal(float, int)
    selection_changed = Signal(int, int, int, int)
    view_restored = Signal(float, int, int)
    ready = Signal(int)
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dark = False
        self._task_toggle_handler: Callable[[int, int, bool, int], bool] | None = None
        self._revision = -1
        self._line_count = 1
        self._source_length = 0
        self._selection_sequence = 0
        self._pending_source_selection: tuple[int, int, int, int, bool] | None = None
        self._shell_ready = False
        self._sync_enabled = True
        self._pending_document: dict[str, object] | None = None
        self._pending_scroll: tuple[float, int] | None = None
        self._pending_view_restore: tuple[float, int, int] | None = None
        self._view_restore_token = -1
        self._zoom_factor = 1.0
        self._zoom_token = 0
        self._pending_zoom_token: int | None = None

        shell_path = Path(__file__).resolve().parent / "resources" / "preview.html"
        self._shell_url = QUrl.fromLocalFile(str(shell_path))
        self.view = _PreviewView(self)
        self.web_view = self.view
        self._page = _PreviewPage(self._shell_url, self.view)
        self.view.setPage(self._page)
        self._page.console_error.connect(self.error)

        settings = self._page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)

        self._channel = QWebChannel(self._page)
        self._bridge = _PreviewBridge(self)
        self._channel.registerObject("previewBridge", self._bridge)
        self._page.setWebChannel(self._channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view)
        self.view.loadFinished.connect(self._on_load_finished)
        self.view.load(self._shell_url)

    def page(self) -> QWebEnginePage:
        return self._page

    def set_zoom_factor(self, factor: float) -> None:
        """Zoom the browser after preserving its own logical source position."""
        factor = float(factor)
        if not math.isfinite(factor) or not 0.25 <= factor <= 5.0:
            raise ValueError("Preview zoom must be between 0.25 and 5.0")
        if factor == self._zoom_factor:
            return
        self._zoom_factor = factor
        self._zoom_token += 1
        token = self._zoom_token
        if not self._shell_ready:
            self.view.setZoomFactor(factor)
            return
        self._pending_zoom_token = token

        def apply_zoom(started: bool) -> None:
            # JavaScript callbacks can arrive after another request or deletion.
            if not isValid(self) or token != self._zoom_token:
                return
            if not started:
                self._pending_zoom_token = None
                return
            self.view.setZoomFactor(factor)
            self._page.runJavaScript(f"window.previewApi.finishZoom({token});")

        self._page.runJavaScript(f"window.previewApi.beginZoom({token});", apply_zoom)

    def apply_theme(self, dark: bool) -> None:
        self._dark = bool(dark)
        self._page.setBackgroundColor(QColor("#171c24" if self._dark else "#ffffff"))
        if self._shell_ready:
            self._page.runJavaScript(
                "window.previewApi.setTheme(" + ("true" if self._dark else "false") + ");"
            )

    def set_task_toggle_handler(
        self, handler: Callable[[int, int, bool, int], bool] | None
    ) -> None:
        """Allow task edits only when the source owner can validate and apply them."""
        self._task_toggle_handler = handler
        editable = handler is not None
        if self._pending_document is not None:
            self._pending_document["tasksEditable"] = editable
        if self._shell_ready:
            self._page.runJavaScript(
                "window.previewApi.setTasksEditable(" + ("true" if editable else "false") + ");"
            )

    def set_sync_enabled(self, enabled: bool) -> None:
        self._sync_enabled = bool(enabled)

    def set_document(
        self,
        html: str,
        line_count: int,
        base_dir: Path,
        revision: int,
        *,
        selection_map: Iterable[dict[str, object]] = (),
        source_length: int = 0,
    ) -> None:
        """Replace the fragment without reloading the WebEngine shell.

        Documents older than the latest revision are ignored. Relative images
        resolve against ``base_dir``, which is the Markdown file's directory.
        """
        revision = int(revision)
        if revision < self._revision:
            return
        self._revision = revision
        self._line_count = max(1, int(line_count))
        self._source_length = max(0, int(source_length))
        base_path = str(Path(base_dir).resolve()) + os.sep
        self._pending_document = {
            "html": resolve_srcsets(html, QUrl.fromLocalFile(base_path).toString()),
            "lineCount": self._line_count,
            "tasksEditable": self._task_toggle_handler is not None,
            "baseUrl": QUrl.fromLocalFile(base_path).toString(),
            "revision": revision,
            "selectionMap": list(selection_map),
            "sourceLength": self._source_length,
        }
        if self._pending_source_selection and self._pending_source_selection[2] < revision:
            self._pending_source_selection = None
        if self._pending_scroll and self._pending_scroll[1] != revision:
            self._pending_scroll = None
        if self._pending_view_restore and self._pending_view_restore[1] < revision:
            self._pending_view_restore = None
        if self._shell_ready:
            self._send_document()

    def set_source_selection(
        self,
        anchor: int,
        position: int,
        revision: int,
        sequence: int,
        scroll: bool = False,
    ) -> None:
        """Mirror source UTF-16 positions without moving keyboard focus.

        Repeated requests carry an increasing sequence. Browser notifications
        echo that sequence so delayed responses cannot replace a newer choice.
        A collapsed source selection clears the preview's native selection.
        """
        anchor, position = int(anchor), int(position)
        revision, sequence = int(revision), int(sequence)
        if (
            revision < self._revision
            or sequence < self._selection_sequence
            or min(anchor, position) < 0
            or (revision == self._revision and max(anchor, position) > self._source_length)
        ):
            return
        self._selection_sequence = sequence
        self._pending_source_selection = (anchor, position, revision, sequence, bool(scroll))
        self._send_source_selection()

    def _send_source_selection(self) -> None:
        pending = self._pending_source_selection
        if not self._shell_ready or pending is None or pending[2] != self._revision:
            return
        self._pending_source_selection = None
        anchor, position, revision, sequence, scroll = pending
        if max(anchor, position) > self._source_length:
            return
        payload = json.dumps([anchor, position, revision, sequence, scroll])
        self._page.runJavaScript(f"window.previewApi.setSourceSelection(...{payload});")

    def restore_view(self, position: float, revision: int, token: int) -> None:
        """Restore after layout and acknowledge only the latest request.

        A future document revision waits for set_document; an unready shell
        waits for its bridge. Tokens increase for this PreviewPane lifetime.
        """
        revision, token = int(revision), int(token)
        if (
            not math.isfinite(position)
            or revision < self._revision
            or token < self._view_restore_token
        ):
            return
        self._view_restore_token = token
        self._pending_view_restore = (max(0.0, float(position)), revision, token)
        self._send_view_restore()

    def _send_view_restore(self) -> None:
        pending = self._pending_view_restore
        if not self._shell_ready or pending is None or pending[1] != self._revision:
            return
        position, revision, token = pending
        position = min(position, float(self._line_count) - 1e-6)
        self._page.runJavaScript(
            f"window.previewApi.restoreView({position!r}, {revision}, {token});"
        )

    def scroll_to_source(self, position: float, revision: int) -> None:
        if revision != self._revision or not math.isfinite(position):
            return
        position = max(0.0, min(float(position), float(self._line_count) - 1e-6))
        if not self._shell_ready:
            self._pending_scroll = (position, revision)
            return
        self._page.runJavaScript(
            f"window.previewApi && window.previewApi.scrollToSource({position!r}, {int(revision)});"
        )

    @Slot(bool)
    def _on_load_finished(self, success: bool) -> None:
        if not success:
            self.error.emit(
                "プレビュー画面を読み込めませんでした。配布リソースを確認してください。"
            )

    def _on_shell_ready(self) -> None:
        self._shell_ready = True
        self.apply_theme(self._dark)
        self._send_document()

    def _send_document(self) -> None:
        if self._pending_document is None:
            self._send_view_restore()
            return
        payload = json.dumps(self._pending_document, ensure_ascii=True, separators=(",", ":"))
        self._pending_document = None
        self._page.runJavaScript(f"window.previewApi.setDocument({payload});")
        self._send_source_selection()
        if self._pending_scroll:
            position, revision = self._pending_scroll
            self._pending_scroll = None
            self.scroll_to_source(position, revision)
        self._send_view_restore()
