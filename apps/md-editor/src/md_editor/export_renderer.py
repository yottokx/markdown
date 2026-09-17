"""Independent Qt rendering jobs for static HTML snapshots and PDF output."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPageLayout
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .link_schemes import is_external_link
from .preview import PreviewPane
from .rendering import render_markdown

# Return a value synchronously; WebEngine callbacks do not await JS Promises.
# PySide6 needs JSON.stringify around object results. read_resource_state below
# provides that conversion and runs in the isolated ApplicationWorld under CSP.
RESOURCE_READINESS_JS = r"""(() => {
  const images = Array.from(document.images);
  const errors = images.filter(img => img.complete && img.naturalWidth === 0)
    .map(img => `画像を読み込めません: ${(img.currentSrc || img.src).slice(0, 180)}`);
  if (document.fonts) {
    for (const font of document.fonts) {
      if (font.status === 'error') errors.push(`フォントを読み込めません: ${font.family}`);
    }
  }
  return {
    ready: document.readyState === 'complete' && images.every(img => img.complete)
      && (!document.fonts || document.fonts.status === 'loaded'),
    errors
  };
})()"""


_SNAPSHOT_JS = r"""(() => {
  const root = document.getElementById('content');
  if (!root || !window.previewApi) return {ready: false};
  const errors = Array.from(root.querySelectorAll('[data-render-state="error"]')).map(node => {
    const line = node.closest('[data-source-line]')?.dataset.sourceLine;
    const location = line === undefined ? '' : `${Number(line) + 1}行目: `;
    return location + (node.getAttribute('title') || '数式・図を描画できません');
  });
  const ready = !root.querySelector('[data-render-state="pending"]')
    && Array.from(root.querySelectorAll('img')).every(img => img.complete)
    && (!document.fonts || document.fonts.status === 'loaded');
  return {revision: window.previewApi.metrics().revision, ready, errors,
    html: ready ? root.innerHTML : null};
})()"""


def _read_json(page: QWebEnginePage, script: str, callback, *, isolated: bool = True) -> None:
    def received(value) -> None:
        try:
            result = json.loads(value) if isinstance(value, str) and value else None
        except (TypeError, ValueError):
            result = None
        callback(result if isinstance(result, dict) else None)

    world = (
        QWebEngineScript.ScriptWorldId.ApplicationWorld
        if isolated
        else QWebEngineScript.ScriptWorldId.MainWorld
    )
    page.runJavaScript("JSON.stringify(" + script + ")", world, received)


def read_resource_state(page: QWebEnginePage, callback: Callable[[dict | None], None]) -> None:
    """Read completed HTML's image/font state without depending on page scripts."""
    _read_json(page, RESOURCE_READINESS_JS, callback)


class ExportPage(QWebEnginePage):
    """Local output HTML; explicit links open externally without replacing it."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._document_path: str | None = None
        self.setBackgroundColor(QColor("white"))
        settings = self.settings()
        for attribute in (
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
            QWebEngineSettings.WebAttribute.PluginsEnabled,
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,
        ):
            settings.setAttribute(attribute, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

    def load_html_file(self, path: Path | str) -> None:
        path = Path(path).resolve()
        self._document_path = os.path.normcase(str(path))
        self.load(QUrl.fromLocalFile(str(path)))

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame) -> bool:
        if not is_main_frame:
            return False
        if url.isLocalFile() and os.path.normcase(url.toLocalFile()) == self._document_path:
            return True
        if (
            navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            and url.isValid()
            and is_external_link(url.toString())
        ):
            QDesktopServices.openUrl(url)
        return False


class ExportRenderer(QObject):
    """Render a source snapshot with a private, light, 800px preview surface."""

    rendered = Signal(str)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None, *, timeout_ms: int = 30000) -> None:
        super().__init__(parent)
        self._timeout_ms = timeout_ms
        self._revision = 0
        self._busy = False
        self._closed = False
        self._preview: PreviewPane | None = None
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(
            lambda: self._fail(self._revision, "出力用の描画がタイムアウトしました。")
        )
        self._poll = QTimer(self)
        self._poll.setSingleShot(True)
        self._poll.setInterval(80)
        self._poll.timeout.connect(lambda: self._capture(self._revision))

    @property
    def busy(self) -> bool:
        return self._busy

    def _ensure_preview(self) -> PreviewPane:
        if self._preview is None:
            preview = self._preview = PreviewPane()
            # A merely hidden QWebEngineView has a zero-width viewport. This
            # attribute gives it real layout without mapping an on-screen window.
            preview.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            preview.resize(800, 600)
            preview.set_sync_enabled(False)
            preview.apply_theme(False)
            preview.ready.connect(self._capture)
            preview.view.loadFinished.connect(self._shell_loaded)
            preview.page().renderProcessTerminated.connect(
                lambda *_args: self._fail(self._revision, "出力用の描画プロセスが終了しました。")
            )
            self.destroyed.connect(preview.deleteLater)
            preview.show()
        return self._preview

    def render(self, source: str, base_dir: Path | str) -> int:
        if self._closed:
            raise RuntimeError("ExportRenderer is closed")
        self.cancel()
        revision = self._revision
        self._busy = True
        self._deadline.start(self._timeout_ms)
        try:
            rendered = render_markdown(source)
            self._ensure_preview().set_document(
                rendered.html, rendered.line_count, Path(base_dir), revision
            )
        except (OSError, ValueError, TypeError) as exc:
            self._fail(revision, f"出力用の本文を準備できません: {exc}")
        return revision

    def _shell_loaded(self, success: bool) -> None:
        if not success:
            self._fail(self._revision, "出力用プレビューを読み込めませんでした。")

    def _is_current(self, revision: int) -> bool:
        return not self._closed and self._busy and revision == self._revision

    def _capture(self, revision: int) -> None:
        if not self._is_current(revision) or self._preview is None:
            return
        _read_json(
            self._preview.page(),
            _SNAPSHOT_JS,
            lambda result: self._captured(revision, result),
            isolated=False,
        )

    def _captured(self, revision: int, result: dict | None) -> None:
        if not self._is_current(revision):
            return
        if not result or result.get("revision") != revision:
            self._poll.start()
            return
        if result.get("errors"):
            self._fail(
                revision,
                "数式・図のエラーを修正してから出力してください。\n"
                + "\n".join(result["errors"][:5]),
            )
            return
        if not result.get("ready"):
            self._poll.start()
            return
        self._busy = False
        self._deadline.stop()
        self._poll.stop()
        self.rendered.emit(result["html"])

    def _fail(self, revision: int, message: str) -> None:
        if self._is_current(revision):
            self._busy = False
            self._deadline.stop()
            self._poll.stop()
            self.error.emit(message)

    def cancel(self) -> None:
        self._revision += 1
        self._busy = False
        self._deadline.stop()
        self._poll.stop()

    def close(self) -> None:
        if self._closed:
            return
        self.cancel()
        self._closed = True
        if self._preview is not None:
            self._preview.view.stop()
            self._preview.close()
            self._preview.deleteLater()
            self._preview = None


@dataclass
class _PdfJob:
    revision: int
    directory: TemporaryDirectory
    view: QWebEngineView
    page: ExportPage
    layout: QPageLayout | None
    loaded: bool = False
    printing: bool = False


def _page_layout_script(layout: QPageLayout) -> str:
    rectangle = layout.fullRect(QPageLayout.Unit.Millimeter)
    margins = layout.margins(QPageLayout.Unit.Millimeter)
    size = f"{rectangle.width():.6f}mm {rectangle.height():.6f}mm"
    margin = " ".join(
        f"{value:.6f}mm"
        for value in (margins.top(), margins.right(), margins.bottom(), margins.left())
    )
    # Apply the explicit print dialog choice to named and pseudo-page rules as
    # well as the default page; other user print CSS stays intact.
    return (
        "(() => { const size = "
        + json.dumps(size)
        + ", margin = "
        + json.dumps(margin)
        + ";\n"
        + """
      function update(rules) {
        for (const rule of rules) {
          if (rule.type === CSSRule.PAGE_RULE) {
            rule.style.setProperty('size', size, 'important');
            rule.style.setProperty('margin', margin, 'important');
          }
          if (rule.cssRules) update(rule.cssRules);
        }
      }
      for (const sheet of document.styleSheets) {
        try { update(sheet.cssRules); } catch (_) { /* Cross-origin CSS remains isolated. */ }
      }
      const style = document.createElement('style');
      style.textContent = `@page { size: ${size} !important; margin: ${margin} !important; }`;
      document.head.append(style);
      return true;
    })()"""
    )


class PdfRenderer(QObject):
    """Print completed standalone HTML from a temporary file, never setHtml()."""

    rendered = Signal(bytes)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None, *, timeout_ms: int = 60000) -> None:
        super().__init__(parent)
        self._timeout_ms = timeout_ms
        self._revision = 0
        self._closed = False
        self._job: _PdfJob | None = None
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(
            lambda: self._fail(self._revision, "PDF出力がタイムアウトしました。")
        )
        self._poll = QTimer(self)
        self._poll.setSingleShot(True)
        self._poll.setInterval(80)
        self._poll.timeout.connect(self._check_resources)

    @property
    def busy(self) -> bool:
        return self._job is not None

    def render(self, html: str, page_layout: QPageLayout | None = None) -> int:
        if self._closed:
            raise RuntimeError("PdfRenderer is closed")
        self.cancel()
        revision = self._revision
        directory = TemporaryDirectory(prefix="md-editor-pdf-")
        try:
            path = Path(directory.name) / "document.html"
            path.write_text(html, encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            directory.cleanup()
            self.error.emit(f"PDF出力用の一時ファイルを作成できません: {exc}")
            return revision
        view = QWebEngineView()
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        view.resize(800, 1100)
        page = ExportPage(view)
        view.setPage(page)
        job = self._job = _PdfJob(
            revision,
            directory,
            view,
            page,
            QPageLayout(page_layout) if page_layout is not None else None,
        )
        view.destroyed.connect(lambda *_args, folder=directory: folder.cleanup())
        self.destroyed.connect(view.deleteLater)
        page.loadFinished.connect(lambda success: self._loaded(job, success))
        page.renderProcessTerminated.connect(
            lambda *_args: self._fail(revision, "PDF描画プロセスが終了しました。")
        )
        self._deadline.start(self._timeout_ms)
        view.show()
        page.load_html_file(path)
        return revision

    def _current(self, job: _PdfJob) -> bool:
        return not self._closed and self._job is job and job.revision == self._revision

    def _loaded(self, job: _PdfJob, success: bool) -> None:
        if not self._current(job):
            return
        if not success:
            self._fail(job.revision, "PDF出力用のHTMLを読み込めませんでした。")
            return
        job.loaded = True
        if job.layout is not None:
            job.page.runJavaScript(
                _page_layout_script(job.layout),
                QWebEngineScript.ScriptWorldId.ApplicationWorld,
                lambda _result: self._check_resources(),
            )
        else:
            self._check_resources()

    def _check_resources(self) -> None:
        job = self._job
        if job is not None and job.loaded and not job.printing:
            read_resource_state(job.page, lambda result: self._resources(job, result))

    def _resources(self, job: _PdfJob, result: dict | None) -> None:
        if not self._current(job) or job.printing:
            return
        if result and result.get("errors"):
            self._fail(job.revision, "\n".join(result["errors"][:5]))
            return
        if not result or not result.get("ready"):
            self._poll.start()
            return
        job.printing = True
        callback = lambda data: self._printed(job, bytes(data) if data else b"")
        if job.layout is None:
            job.page.printToPdf(callback)
        else:
            job.page.printToPdf(callback, job.layout)

    def _printed(self, job: _PdfJob, data: bytes) -> None:
        if not self._current(job):
            return
        if not data.startswith(b"%PDF-"):
            self._fail(job.revision, "PDFを生成できませんでした。")
            return
        self._finish_job()
        self.rendered.emit(data)

    def _fail(self, revision: int, message: str) -> None:
        if self._job is not None and revision == self._revision and not self._closed:
            self._finish_job()
            self.error.emit(message)

    def _finish_job(self) -> None:
        self._deadline.stop()
        self._poll.stop()
        job, self._job = self._job, None
        if job is not None:
            job.view.stop()
            job.view.close()
            job.view.deleteLater()

    def cancel(self) -> None:
        self._revision += 1
        self._finish_job()

    def close(self) -> None:
        if not self._closed:
            self.cancel()
            self._closed = True
