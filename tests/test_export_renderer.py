import base64
import json
import re
import zlib

import pytest
from PySide6.QtCore import QMarginsF, Qt
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize
from PySide6.QtWebEngineWidgets import QWebEngineView

from md_editor.export_renderer import ExportPage, ExportRenderer, PdfRenderer, read_resource_state
from md_editor.preview import PreviewPane
from md_editor.rendering import render_markdown


@pytest.fixture
def exporter(qtbot):
    renderer = ExportRenderer(timeout_ms=20000)
    yield renderer
    renderer.close()
    renderer.deleteLater()
    qtbot.wait(20)


@pytest.fixture
def pdf_renderer(qtbot):
    renderer = PdfRenderer(timeout_ms=20000)
    yield renderer
    renderer.close()
    renderer.deleteLater()
    qtbot.wait(20)


def javascript(qtbot, page, expression):
    values = []
    page.runJavaScript("JSON.stringify(" + expression + ")", values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=5000)
    return json.loads(values[0])


def standalone(body, css=""):
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "script-src 'none'; style-src 'unsafe-inline'; img-src data: file:; font-src data: file:\">"
        f"<style>{css}</style></head><body>{body}</body></html>"
    )


def test_snapshot_waits_for_math_mermaid_fonts_and_has_real_offscreen_width(
    exporter, qtbot, tmp_path
):
    errors = []
    exporter.error.connect(errors.append)
    source = "# Snapshot\n\nInline $x^2$\n\n```mermaid\nflowchart LR\n A --> B\n```"
    with qtbot.waitSignal(exporter.rendered, timeout=20000) as result:
        exporter.render(source, tmp_path)
    fragment = result.args[0]
    assert 'class="katex"' in fragment
    assert "<svg" in fragment
    assert 'data-render-state="ready"' in fragment
    assert "render-error" not in fragment
    assert "eof-spacer" not in fragment
    assert not errors
    assert not exporter.busy
    assert exporter._preview.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    assert javascript(qtbot, exporter._preview.page(), "innerWidth") == 800


@pytest.mark.parametrize(
    "source", [r"$\definitelyUnknownCommand$", "```mermaid\ninvalid graph !!\n```"]
)
def test_snapshot_reports_rich_content_errors_before_output(exporter, qtbot, tmp_path, source):
    results = []
    exporter.rendered.connect(results.append)
    with qtbot.waitSignal(exporter.error, timeout=20000) as error:
        exporter.render(source, tmp_path)
    assert "修正" in error.args[0]
    assert not results
    assert not exporter.busy


def test_snapshot_replacement_cancel_and_close_ignore_obsolete_responses(exporter, qtbot, tmp_path):
    results, errors = [], []
    exporter.rendered.connect(results.append)
    exporter.error.connect(errors.append)
    with qtbot.waitSignal(exporter.rendered, timeout=20000):
        old = exporter.render("obsolete", tmp_path)
        new = exporter.render("current $x$", tmp_path)
    assert new > old
    assert len(results) == 1 and "current" in results[0] and "obsolete" not in results[0]
    exporter.render("cancelled", tmp_path)
    exporter.cancel()
    qtbot.wait(150)
    assert len(results) == 1 and not errors
    exporter.close()
    exporter._captured(old, {"ready": True, "html": "stale", "revision": old})
    assert len(results) == 1
    with pytest.raises(RuntimeError, match="closed"):
        exporter.render("closed", tmp_path)


def test_export_snapshot_does_not_replace_live_preview_or_change_its_theme(
    exporter, qtbot, tmp_path
):
    live = PreviewPane()
    qtbot.addWidget(live)
    live.resize(500, 400)
    live.show()
    live.apply_theme(True)
    content = render_markdown("# Live document\n\nkeep this content")
    with qtbot.waitSignal(live.ready, timeout=15000):
        live.set_document(content.html, content.line_count, tmp_path, 77)
    before = javascript(qtbot, live.page(), "document.querySelector('#content').innerHTML")
    with qtbot.waitSignal(exporter.rendered, timeout=20000):
        exporter.render("Different $y$", tmp_path)
    assert live._revision == 77
    assert javascript(qtbot, live.page(), "document.querySelector('#content').innerHTML") == before
    assert javascript(qtbot, live.page(), "document.documentElement.dataset.theme") == "dark"


def test_snapshot_timeout_is_one_error_and_late_result_is_ignored(
    exporter, qtbot, monkeypatch, tmp_path
):
    exporter._timeout_ms = 50
    monkeypatch.setattr(exporter, "_capture", lambda _revision: None)
    results, errors = [], []
    exporter.rendered.connect(results.append)
    exporter.error.connect(errors.append)
    with qtbot.waitSignal(exporter.error, timeout=1000):
        revision = exporter.render("source", tmp_path)
    exporter._captured(revision, {"ready": True, "html": "late", "revision": revision})
    assert len(errors) == 1 and "タイムアウト" in errors[0]
    assert not results


def test_export_page_readiness_works_under_script_none_csp_and_reports_broken_images(
    qtbot, tmp_path
):
    path = tmp_path / "standalone.html"
    path.write_text(
        standalone('<script>window.evil = true;</script><img src="missing.png">'), encoding="utf-8"
    )
    view = QWebEngineView()
    qtbot.addWidget(view)
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    view.resize(800, 600)
    page = ExportPage(view)
    view.setPage(page)
    view.show()
    with qtbot.waitSignal(page.loadFinished, timeout=10000) as loaded:
        page.load_html_file(path)
    assert loaded.args[0]
    values = []
    read_resource_state(page, values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=5000)
    assert values[0]["ready"]
    assert values[0]["errors"] and "画像" in values[0]["errors"][0]
    assert javascript(qtbot, page, "Boolean(window.evil)") is False
    assert not page.acceptNavigationRequest(
        page.url(), page.NavigationType.NavigationTypeOther, False
    )


def test_pdf_uses_file_loading_for_large_html_and_returns_pdf_bytes(pdf_renderer, qtbot):
    body = "<!--" + "large payload " * 180000 + "--><h1>PDF export</h1><p>Hello</p>"
    html = standalone(body)
    assert len(html.encode("utf-8")) > 2 * 1024 * 1024
    errors = []
    pdf_renderer.error.connect(errors.append)
    with qtbot.waitSignal(pdf_renderer.rendered, timeout=20000) as result:
        pdf_renderer.render(html)
        folder = pdf_renderer._job.directory.name
        assert pdf_renderer._job.page._document_path.endswith("document.html")
    assert result.args[0].startswith(b"%PDF-")
    assert not errors and not pdf_renderer.busy
    from pathlib import Path

    qtbot.waitUntil(lambda: not Path(folder).exists(), timeout=5000)


def test_pdf_explicit_layout_overrides_user_css_page_size(pdf_renderer, qtbot):
    layout = QPageLayout(
        QPageSize(QPageSize.PageSizeId.A5),
        QPageLayout.Orientation.Landscape,
        QMarginsF(12, 13, 14, 15),
        QPageLayout.Unit.Millimeter,
    )
    html = standalone(
        "<h1>Page choice</h1>",
        "@page {size: 100mm 100mm; margin: 40mm;} "
        "@page report {size: 80mm 80mm; margin: 30mm;} body {margin:0;page:report;}",
    )
    with qtbot.waitSignal(pdf_renderer.rendered, timeout=20000) as result:
        pdf_renderer.render(html, layout)
    boxes = re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", result.args[0])
    assert boxes
    width, height = map(float, boxes[0])
    assert width == pytest.approx(210 / 25.4 * 72, abs=1)
    assert height == pytest.approx(148 / 25.4 * 72, abs=1)
    # Chromium's printed content is clipped to the page's printable rectangle.
    # Inspect that real PDF geometry: CSS's 30/40mm margins must not survive.
    printable = None
    for stream in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", result.args[0], re.DOTALL):
        try:
            decoded = zlib.decompress(stream)
        except zlib.error:
            continue
        transform = re.search(rb"([-\d.]+) 0 0 ([-\d.]+) 0 [\d.]+ cm", decoded)
        clip = re.search(rb"([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re\r?\nW", decoded)
        if transform and clip:
            scale = abs(float(transform[1])) * 25.4 / 72
            printable = tuple(float(value) * scale for value in clip.groups())
            break
    assert printable is not None
    assert printable == pytest.approx((12, 13, 184, 120), abs=0.35)


def test_pdf_waits_for_embedded_image_and_reports_missing_resource(pdf_renderer, qtbot, tmp_path):
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor("#cc8855"))
    path = tmp_path / "image.png"
    assert image.save(str(path))
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    with qtbot.waitSignal(pdf_renderer.rendered, timeout=20000) as result:
        pdf_renderer.render(standalone(f'<img src="data:image/png;base64,{data}">'))
    assert b"/Subtype /Image" in result.args[0]
    with qtbot.waitSignal(pdf_renderer.error, timeout=10000) as error:
        pdf_renderer.render(standalone('<img src="file:///missing-export-image.png">'))
    assert "画像" in error.args[0]
    assert not pdf_renderer.busy


def test_pdf_cancel_and_replacement_ignore_previous_callbacks(pdf_renderer, qtbot):
    results, errors = [], []
    pdf_renderer.rendered.connect(results.append)
    pdf_renderer.error.connect(errors.append)
    pdf_renderer.render(standalone("cancelled"))
    old_job = pdf_renderer._job
    pdf_renderer.cancel()
    with qtbot.waitSignal(pdf_renderer.rendered, timeout=20000):
        pdf_renderer.render(standalone("replacement"))
        pdf_renderer._printed(old_job, b"%PDF-stale")
    assert len(results) == 1 and results[0] != b"%PDF-stale"
    assert not errors
    pdf_renderer.close()
    pdf_renderer._printed(old_job, b"%PDF-late")
    assert len(results) == 1


def test_pdf_timeout_cleans_temporary_file_and_ignores_late_callback(
    pdf_renderer, qtbot, monkeypatch
):
    from pathlib import Path

    pdf_renderer._timeout_ms = 60
    monkeypatch.setattr(pdf_renderer, "_check_resources", lambda: None)
    results, errors = [], []
    pdf_renderer.rendered.connect(results.append)
    pdf_renderer.error.connect(errors.append)
    with qtbot.waitSignal(pdf_renderer.error, timeout=2000):
        pdf_renderer.render(standalone("timed out"))
        job = pdf_renderer._job
        folder = Path(job.directory.name)
    pdf_renderer._resources(job, {"ready": True, "errors": []})
    pdf_renderer._printed(job, b"%PDF-late")
    qtbot.waitUntil(lambda: not folder.exists(), timeout=5000)
    assert len(errors) == 1 and "タイムアウト" in errors[0]
    assert not results


def test_highlighted_code_export_is_light_and_has_no_preview_copy_ui(
    exporter, pdf_renderer, qtbot, tmp_path
):
    from bs4 import BeautifulSoup

    from md_editor.exporting import build_export_html

    source = '```python\nif value < 3:\n\n    print("  日本語  ")  \n\n```\n'
    with qtbot.waitSignal(exporter.rendered, timeout=20000) as captured:
        exporter.render(source, tmp_path)
    html = build_export_html(captured.args[0], tmp_path, "Highlighted code")
    doc = BeautifulSoup(html, "html.parser")
    assert not doc.select(
        ".code-copy-button, .code-block, .code-boundary, [data-code-source], [data-preview-code-ui], script"
    )
    assert doc.html["data-theme"] == "light"
    assert [line.get_text() for line in doc.select(".code-line")] == [
        "if value < 3:",
        "",
        '    print("  日本語  ")  ',
        "",
    ]
    expected_color = javascript(
        qtbot,
        exporter._preview.page(),
        "getComputedStyle(document.querySelector('pre .tok-keyword')).color",
    )
    path = tmp_path / "highlighted-code.html"
    path.write_text(html, encoding="utf-8")
    view = QWebEngineView()
    qtbot.addWidget(view)
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    view.resize(800, 600)
    page = ExportPage(view)
    view.setPage(page)
    view.show()
    with qtbot.waitSignal(page.loadFinished, timeout=10000) as loaded:
        page.load_html_file(path)
    assert loaded.args[0]
    colors = javascript(
        qtbot,
        page,
        """({
        keyword: getComputedStyle(document.querySelector('pre .tok-keyword')).color,
        plain: getComputedStyle(document.querySelector('pre code')).color,
        lineHeight: document.querySelectorAll('pre .code-line')[1].getBoundingClientRect().height
    })""",
    )
    assert colors["keyword"] == expected_color
    assert colors["keyword"] != colors["plain"]
    assert colors["lineHeight"] > 0
    with qtbot.waitSignal(pdf_renderer.rendered, timeout=20000) as result:
        pdf_renderer.render(html)
    assert result.args[0].startswith(b"%PDF-")
