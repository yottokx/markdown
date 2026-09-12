"""Output settings and a preview of the exact standalone HTML being saved."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QMarginsF,
    QObject,
    QRunnable,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QCloseEvent, QPageLayout, QPageSize
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .document import atomic_write
from .export_renderer import (
    RESOURCE_READINESS_JS,
    ExportPage,
    ExportRenderer,
    PdfRenderer,
)
from .exporting import ExportCancelledError, build_export_html


class _BuildSignals(QObject):
    succeeded = Signal(int, str)
    failed = Signal(int, str)
    finished = Signal(int)


class _BuildJob(QRunnable):
    def __init__(self, revision, fragment, base_dir, title, css_path, fetch_remote):
        super().__init__()
        self.revision = revision
        self.arguments = (fragment, base_dir, title, css_path, fetch_remote)
        self.cancelled = Event()
        self.signals = _BuildSignals()

    def run(self):
        try:
            html = build_export_html(*self.arguments, cancelled=self.cancelled.is_set)
            if not self.cancelled.is_set():
                self.signals.succeeded.emit(self.revision, html)
        except ExportCancelledError:
            pass
        except Exception as exc:
            logging.getLogger(__name__).exception("HTML export failed")
            if not self.cancelled.is_set():
                self.signals.failed.emit(self.revision, str(exc))
        finally:
            self.signals.finished.emit(self.revision)


class ExportDialog(QDialog):
    """A document snapshot; source edits and the live preview remain independent."""

    ready = Signal()
    saved = Signal(str)

    def __init__(
        self,
        source: str,
        base_dir: Path,
        source_path: Path | None = None,
        parent=None,
        *,
        settings: QSettings | None = None,
        preferred_format: str = "html",
    ):
        super().__init__(parent)
        if parent is not None:
            self.setPalette(parent.palette())
        self.setWindowTitle("出力プレビュー")
        self.resize(1120, 880)
        self.source = source
        self.base_dir = Path(base_dir).resolve()
        self.source_path = Path(source_path).resolve() if source_path else None
        self.title = source_path.stem if source_path else "無題"
        self.settings = settings or QSettings("MdEditor", "Markdown Editor")
        self.html = ""
        self._closed = False
        self._busy = False
        self._revision = 0
        self._jobs: dict[int, _BuildJob] = {}
        self._pdf_target: Path | None = None
        self._temporary = tempfile.TemporaryDirectory(prefix="md-editor-output-")
        self._preview_path = Path(self._temporary.name) / "preview.html"
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._check_resources)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(30000)
        self._timeout.timeout.connect(
            lambda: self._fail("出力プレビューの読み込みがタイムアウトしました。")
        )
        self.renderer = ExportRenderer(self)
        self.renderer.rendered.connect(self._fragment_ready)
        self.renderer.error.connect(self._fail)
        self.pdf_renderer = PdfRenderer(self)
        self.pdf_renderer.rendered.connect(self._pdf_ready)
        self.pdf_renderer.error.connect(self._fail)
        self._build_ui(preferred_format)
        QTimer.singleShot(0, self.refresh)

    def _build_ui(self, preferred_format):
        layout = QVBoxLayout(self)
        css_row = QHBoxLayout()
        self.custom_css = QCheckBox("出力用CSSを追加")
        self.custom_css.setChecked(self.settings.value("export/customCss", False, type=bool))
        self.css_path = QLineEdit(str(self.settings.value("export/cssPath", "")))
        self.css_path.setPlaceholderText("標準スタイルに追加するCSSファイル")
        self.browse_css = QPushButton("参照…")
        self.browse_css.clicked.connect(self._choose_css)
        self.refresh_button = QPushButton("プレビューを更新")
        self.refresh_button.clicked.connect(self.refresh)
        css_row.addWidget(self.custom_css)
        css_row.addWidget(self.css_path, 1)
        css_row.addWidget(self.browse_css)
        css_row.addWidget(self.refresh_button)
        layout.addLayout(css_row)
        self.fetch_remote = QCheckBox("外部URLの画像・CSSを取得して埋め込む")
        layout.addWidget(self.fetch_remote)

        self.page_options = QGroupBox("PDFの用紙設定")
        form = QHBoxLayout(self.page_options)
        self.paper = QComboBox()
        for name, identifier in (
            ("A4", QPageSize.PageSizeId.A4),
            ("A3", QPageSize.PageSizeId.A3),
            ("A5", QPageSize.PageSizeId.A5),
            ("Letter", QPageSize.PageSizeId.Letter),
        ):
            self.paper.addItem(name, identifier)
        self.paper.setCurrentText(str(self.settings.value("export/paper", "A4")))
        self.orientation = QComboBox()
        self.orientation.addItem("縦", QPageLayout.Orientation.Portrait)
        self.orientation.addItem("横", QPageLayout.Orientation.Landscape)
        self.orientation.setCurrentIndex(int(self.settings.value("export/orientation", 0)))
        form.addWidget(QLabel("用紙"))
        form.addWidget(self.paper)
        form.addWidget(self.orientation)
        self.margins = {}
        for key, label in (("top", "上"), ("bottom", "下"), ("left", "左"), ("right", "右")):
            control = QDoubleSpinBox()
            control.setRange(0, 80)
            control.setSingleStep(0.5)
            control.setSuffix(" mm")
            control.setValue(float(self.settings.value(f"export/margin/{key}", 15)))
            self.margins[key] = control
            form.addWidget(QLabel(label))
            form.addWidget(control)
        form.addStretch()
        layout.addWidget(self.page_options)
        note = QLabel(
            "画面は保存するHTMLと同じ内容です。PDFの改ページは用紙設定と印刷用CSSで決まります。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.view = QWebEngineView(self)
        self.page = ExportPage(self)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        layout.addWidget(self.view, 1)
        self.errors = QPlainTextEdit()
        self.errors.setReadOnly(True)
        self.errors.setMaximumHeight(125)
        self.errors.hide()
        layout.addWidget(self.errors)
        footer = QHBoxLayout()
        self.status = QLabel("出力を準備しています…")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.html_button = QPushButton("HTMLを保存…")
        self.html_button.clicked.connect(lambda: self.save_html())
        self.pdf_button = QPushButton("PDFを保存…")
        self.pdf_button.clicked.connect(lambda: self.save_pdf())
        self.html_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        (self.pdf_button if preferred_format == "pdf" else self.html_button).setDefault(True)
        footer.addWidget(self.html_button)
        footer.addWidget(self.pdf_button)
        close = QPushButton("閉じる")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        layout.addLayout(footer)
        self.custom_css.toggled.connect(self._settings_changed)
        self.css_path.textChanged.connect(self._settings_changed)
        self.fetch_remote.toggled.connect(self._settings_changed)
        self.css_path.setEnabled(self.custom_css.isChecked())

    def _choose_css(self):
        selected, _ = QFileDialog.getOpenFileName(
            self, "出力用CSSを選択", self.css_path.text() or str(self.base_dir), "CSS (*.css)"
        )
        if selected:
            self.css_path.setText(selected)
            self.custom_css.setChecked(True)

    def _settings_changed(self, *_args):
        self.css_path.setEnabled(self.custom_css.isChecked())
        self._invalidate()
        self.status.setText("設定を変更しました。「プレビューを更新」で反映してください。")

    def _invalidate(self):
        self._revision += 1
        self.html = ""
        self.renderer.cancel()
        self.pdf_renderer.cancel()
        self._pdf_target = None
        self._poll.stop()
        self._timeout.stop()
        for job in self._jobs.values():
            job.cancelled.set()
        self._set_busy(False)
        self.refresh_button.setEnabled(True)
        self.html_button.setEnabled(False)
        self.pdf_button.setEnabled(False)

    def _set_busy(self, busy):
        self._busy = busy
        for widget in (
            self.custom_css,
            self.css_path,
            self.browse_css,
            self.fetch_remote,
            self.page_options,
        ):
            widget.setEnabled(not busy)
        if not busy:
            self.css_path.setEnabled(self.custom_css.isChecked())
        self.html_button.setEnabled(not busy and bool(self.html))
        self.pdf_button.setEnabled(not busy and bool(self.html))

    @Slot()
    def refresh(self):
        if self._closed:
            return
        self._invalidate()
        self.errors.hide()
        if self.custom_css.isChecked() and not self.css_path.text().strip():
            self._fail("追加するCSSファイルを選択してください。")
            return
        self._set_busy(True)
        self.status.setText("数式・Mermaidを描画しています…")
        self.renderer.render(self.source, self.base_dir)

    @Slot(str)
    def _fragment_ready(self, fragment):
        if self._closed:
            return
        self.status.setText("画像・CSS・フォントを埋め込んでいます…")
        css_path = (
            Path(self.css_path.text().strip()).resolve() if self.custom_css.isChecked() else None
        )
        job = _BuildJob(
            self._revision,
            fragment,
            self.base_dir,
            self.title,
            css_path,
            self.fetch_remote.isChecked(),
        )
        self._jobs[job.revision] = job
        job.signals.succeeded.connect(self._html_ready)
        job.signals.failed.connect(self._build_failed)
        job.signals.finished.connect(self._job_finished)
        QThreadPool.globalInstance().start(job)

    @Slot(int)
    def _job_finished(self, revision):
        self._jobs.pop(revision, None)

    @Slot(int, str)
    def _build_failed(self, revision, message):
        if revision == self._revision and not self._closed:
            self._fail(message)

    @Slot(int, str)
    def _html_ready(self, revision, html):
        if revision != self._revision or self._closed:
            return
        try:
            atomic_write(self._preview_path, html.encode("utf-8"))
        except OSError as exc:
            self._fail(str(exc))
            return
        self.html = html
        self.status.setText("出力プレビューを読み込んでいます…")
        self._timeout.start()
        previous = self.page
        self.page = page = ExportPage(self)
        self.view.setPage(page)
        page.loadFinished.connect(lambda success: self._loaded(revision, page, success))
        previous.deleteLater()
        page.load_html_file(self._preview_path)

    def _loaded(self, revision, page, success):
        if (
            self._closed
            or revision != self._revision
            or page is not self.page
            or not self.html
            or not self._busy
        ):
            return
        if not success:
            self._fail("出力プレビューを読み込めませんでした。")
            return
        self._poll.start()
        self._check_resources()

    def _check_resources(self):
        revision = self._revision
        self.page.runJavaScript(
            "JSON.stringify(" + RESOURCE_READINESS_JS + ")",
            QWebEngineScript.ScriptWorldId.ApplicationWorld,
            lambda value: self._resources_checked(revision, value),
        )

    def _resources_checked(self, revision, value):
        if self._closed or revision != self._revision or not self._poll.isActive():
            return
        try:
            result = json.loads(value)
        except (TypeError, ValueError):
            return
        if result.get("errors"):
            self._fail("\n".join(result["errors"]))
        elif result.get("ready"):
            self._poll.stop()
            self._timeout.stop()
            self._set_busy(False)
            self.status.setText("出力の準備ができました。画像・フォントはHTMLに埋め込み済みです。")
            self._persist_settings()
            self.ready.emit()

    @Slot(str)
    def _fail(self, message):
        if self._closed:
            return
        self._poll.stop()
        self._timeout.stop()
        self._pdf_target = None
        self._set_busy(False)
        self.refresh_button.setEnabled(True)
        self.html_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        self.status.setText("出力できません。以下の内容を確認して、再度更新してください。")
        self.errors.setPlainText(message)
        self.errors.show()

    def _persist_settings(self):
        self.settings.setValue("export/customCss", self.custom_css.isChecked())
        self.settings.setValue("export/cssPath", self.css_path.text())
        self.settings.setValue("export/paper", self.paper.currentText())
        self.settings.setValue("export/orientation", self.orientation.currentIndex())
        for key, control in self.margins.items():
            self.settings.setValue(f"export/margin/{key}", control.value())

    def page_layout(self):
        size = QPageSize(self.paper.currentData())
        orientation = self.orientation.currentData()
        dimensions = size.size(QPageSize.Unit.Millimeter)
        width, height = dimensions.width(), dimensions.height()
        if orientation == QPageLayout.Orientation.Landscape:
            width, height = height, width
        margins = {key: control.value() for key, control in self.margins.items()}
        if (
            margins["left"] + margins["right"] >= width
            or margins["top"] + margins["bottom"] >= height
        ):
            raise ValueError("余白が用紙サイズを超えています。余白を小さくしてください。")
        return QPageLayout(
            size,
            orientation,
            QMarginsF(margins["left"], margins["top"], margins["right"], margins["bottom"]),
            QPageLayout.Unit.Millimeter,
        )

    def _target_path(self, path, suffix):
        if path is None:
            directory = self.source_path.parent if self.source_path else Path.home()
            selected, _ = QFileDialog.getSaveFileName(
                self,
                f"{suffix.upper()}を保存",
                str(directory / (self.title + "." + suffix)),
                "HTML (*.html *.htm)" if suffix == "html" else "PDF (*.pdf)",
            )
            if not selected:
                return None
            path = Path(selected)
        path = Path(path).resolve()
        if not path.suffix:
            path = path.with_suffix("." + suffix)
        if path == self.source_path:
            raise ValueError("編集中のMarkdownファイルは出力先に指定できません。")
        if path.suffix.lower() not in ({".html", ".htm"} if suffix == "html" else {".pdf"}):
            raise ValueError(f"出力先の拡張子を .{suffix} にしてください。")
        return path

    def save_html(self, path=None):
        if self._busy or not self.html_button.isEnabled():
            return False
        try:
            target = self._target_path(path, "html")
            if target is None:
                return False
            atomic_write(target, self.html.encode("utf-8"))
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "HTMLを保存できません", str(exc))
            return False
        self.status.setText(f"HTMLを保存しました: {target}")
        self.saved.emit(str(target))
        return True

    def save_pdf(self, path=None):
        if self._busy or not self.pdf_button.isEnabled():
            return False
        try:
            page_layout = self.page_layout()
            target = self._target_path(path, "pdf")
            if target is None:
                return False
        except ValueError as exc:
            QMessageBox.warning(self, "PDFを保存できません", str(exc))
            return False
        self._persist_settings()
        self._pdf_target = target
        self._set_busy(True)
        self.refresh_button.setEnabled(False)
        self.status.setText("PDFを生成しています…")
        self.pdf_renderer.render(self.html, page_layout)
        return True

    @Slot(bytes)
    def _pdf_ready(self, data):
        target = self._pdf_target
        if self._closed or target is None:
            return
        self._pdf_target = None
        self._set_busy(False)
        self.refresh_button.setEnabled(True)
        try:
            atomic_write(target, data)
        except OSError as exc:
            QMessageBox.warning(self, "PDFを保存できません", str(exc))
            return
        self.status.setText(f"PDFを保存しました: {target}")
        self.saved.emit(str(target))

    def _cleanup(self):
        if self._closed:
            return
        self._closed = True
        self._invalidate()
        self.renderer.close()
        self.pdf_renderer.close()
        self.view.stop()
        self._temporary.cleanup()

    def done(self, result):
        self._cleanup()
        super().done(result)

    def closeEvent(self, event: QCloseEvent):
        self._cleanup()
        super().closeEvent(event)


class ExportActions:
    def show_export_dialog(self, preferred_format="html"):
        dialog = ExportDialog(
            self.session.normalize_references(self.editor.toPlainText()),
            self.base_dir,
            self.path,
            self,
            settings=self.settings,
            preferred_format=preferred_format,
        )
        dialog.exec()
        dialog.deleteLater()
