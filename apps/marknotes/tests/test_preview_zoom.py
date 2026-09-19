"""Browser zoom keeps content coordinates through asynchronous layout changes."""

import json

import pytest
from PySide6.QtCore import QSettings

from marknotes.app import MainWindow
from marknotes.preview import PreviewPane
from marknotes.rendering import render_markdown


def evaluate(qtbot, pane, expression):
    results = []
    pane.page().runJavaScript(f"JSON.stringify({expression})", results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=10000)
    return json.loads(results[0])


def metrics(qtbot, pane):
    return evaluate(qtbot, pane, "window.previewApi.metrics()")


def load_document(qtbot, pane, source, base_dir, revision=1):
    rendered = render_markdown(source)
    with qtbot.waitSignal(pane.ready, timeout=15000):
        pane.set_document(rendered.html, rendered.line_count, base_dir, revision)
    qtbot.wait(80)  # Let the initial ResizeObserver notification settle.
    return rendered


def wait_zoom(qtbot, pane, factor):
    qtbot.waitUntil(
        lambda: (
            pane.view.zoomFactor() == pytest.approx(factor) and pane._pending_zoom_token is None
        ),
        timeout=15000,
    )
    result = metrics(qtbot, pane)
    assert not result["zooming"]
    return result


@pytest.fixture
def preview(qtbot):
    pane = PreviewPane()
    qtbot.addWidget(pane)
    pane.resize(720, 440)
    pane.show()
    qtbot.waitUntil(lambda: pane._shell_ready, timeout=15000)
    return pane


@pytest.fixture
def long_source():
    return "\n\n".join(f"Paragraph {i} " + "wrapped content " * 35 for i in range(50)) + "\n\nEnd"


def test_zoom_preserves_fractional_source_and_eof(qtbot, preview, tmp_path, long_source):
    document = load_document(qtbot, preview, long_source, tmp_path)
    events = []
    preview.source_scrolled.connect(lambda *args: events.append(args))
    for position in (38.4, document.line_count - 1, 0):
        preview.scroll_to_source(position, 1)
        qtbot.waitUntil(
            lambda position=position: abs(metrics(qtbot, preview)["source"] - position) < 0.01
        )
        for factor in (0.5, 1.0, 1.5, 2.0, 0.25, 5.0):
            preview.set_zoom_factor(factor)
            result = wait_zoom(qtbot, preview, factor)
            assert result["source"] == pytest.approx(position, abs=0.01)
            assert result["measuredSource"] == pytest.approx(position, abs=0.08)
            if position == document.line_count - 1:
                assert result["scrollY"] == pytest.approx(result["maxScroll"], abs=2)
            assert not events


def test_latest_zoom_and_new_document_scroll_win(qtbot, preview, tmp_path, long_source):
    load_document(qtbot, preview, long_source, tmp_path)
    preview.scroll_to_source(70, 1)
    qtbot.waitUntil(lambda: metrics(qtbot, preview)["source"] == 70)
    updated = render_markdown(long_source[: len(long_source) // 2])
    with qtbot.waitSignal(preview.ready, timeout=15000):
        preview.set_zoom_factor(2)
        preview.set_zoom_factor(0.5)
        preview.set_document(updated.html, updated.line_count, tmp_path, 2)
        preview.scroll_to_source(12.3, 2)
        preview.set_zoom_factor(1.5)
    result = wait_zoom(qtbot, preview, 1.5)
    assert result["revision"] == 2
    assert result["source"] == pytest.approx(12.3, abs=0.01)
    assert result["measuredSource"] == pytest.approx(12.3, abs=0.08)
    assert evaluate(qtbot, preview, "window.previewApi.finishZoom(1)") is False
    assert preview.view.zoomFactor() == pytest.approx(1.5)


def test_hidden_zoom_uses_latest_display_restore(qtbot, preview, tmp_path, long_source):
    load_document(qtbot, preview, long_source, tmp_path)
    preview.scroll_to_source(60, 1)
    qtbot.waitUntil(lambda: metrics(qtbot, preview)["source"] == 60)
    preview.hide()
    preview.set_zoom_factor(0.5)
    preview.restore_view(30, 1, 1)
    with qtbot.waitSignal(preview.view_restored, timeout=15000) as restored:
        preview.restore_view(22.4, 1, 2)
        preview.show()
    assert restored.args == pytest.approx([22.4, 1, 2])
    result = wait_zoom(qtbot, preview, 0.5)
    assert result["source"] == pytest.approx(22.4, abs=0.01)
    assert result["measuredSource"] == pytest.approx(22.4, abs=0.08)


def test_zoom_with_sync_disabled_preserves_preview_position(qtbot, preview, tmp_path, long_source):
    load_document(qtbot, preview, long_source, tmp_path)
    preview.set_sync_enabled(False)
    events = []
    preview.source_scrolled.connect(lambda *args: events.append(args))
    evaluate(qtbot, preview, "(window.scrollTo(0, previewApi.metrics().maxScroll * 0.65), true)")
    qtbot.waitUntil(lambda: metrics(qtbot, preview)["source"] > 20)
    position = metrics(qtbot, preview)["source"]
    preview.set_zoom_factor(0.5)
    result = wait_zoom(qtbot, preview, 0.5)
    assert result["source"] == pytest.approx(position, abs=0.01)
    assert result["measuredSource"] == pytest.approx(position, abs=0.08)
    assert not events


def test_initial_zoom_noop_and_invalid_values(qtbot, tmp_path):
    pane = PreviewPane()
    qtbot.addWidget(pane)
    pane.set_zoom_factor(1.5)
    token = pane._zoom_token
    pane.set_zoom_factor(1.5)
    assert pane._zoom_token == token
    for value in (0, 0.24, 5.01, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            pane.set_zoom_factor(value)
    pane.resize(720, 440)
    pane.show()
    qtbot.waitUntil(lambda: pane._shell_ready, timeout=15000)
    load_document(qtbot, pane, "# First\n\nText", tmp_path)
    result = wait_zoom(qtbot, pane, 1.5)
    assert result["source"] == 0
    token = pane._zoom_token
    pane.set_zoom_factor(1.5)
    assert pane._zoom_token == token
    assert pane._pending_zoom_token is None


def test_both_scroll_directions_work_after_zoom(qtbot, tmp_path, long_source):
    window = MainWindow(QSettings(str(tmp_path / "zoom.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window, before_close_func=lambda w: w.editor.document().setModified(False))
    window.resize(1280, 800)
    window.show()
    window.set_source(long_source, tmp_path)
    qtbot.waitUntil(lambda: window._revision == window._rendered_revision, timeout=15000)
    window.editor.scroll_to_source(26)
    qtbot.waitUntil(lambda: metrics(qtbot, window.preview)["source"] == 26)
    window.preview.set_zoom_factor(2)
    result = wait_zoom(qtbot, window.preview, 2)
    assert result["source"] == pytest.approx(26, abs=0.05)
    assert window.editor.source_position() == pytest.approx(26, abs=0.05)
    window.editor.scroll_to_source(60)
    qtbot.waitUntil(lambda: abs(metrics(qtbot, window.preview)["source"] - 60) < 0.05)
    evaluate(
        qtbot,
        window.preview,
        "(scrollTo(0, previewApi.metrics().anchors.find(a => a.source === 40).y), true)",
    )
    qtbot.waitUntil(lambda: abs(window.editor.source_position() - 40) < 1.05)
    result = metrics(qtbot, window.preview)
    assert result["measuredSource"] == pytest.approx(40, abs=0.05)
    assert window.editor.toPlainText() == long_source


def test_zoom_reports_user_scroll_queued_just_before_it(qtbot, preview, tmp_path, long_source):
    load_document(qtbot, preview, long_source, tmp_path)
    events = []
    preview.source_scrolled.connect(lambda position, revision: events.append((position, revision)))
    evaluate(
        qtbot,
        preview,
        """(() => {
      const begin = previewApi.beginZoom;
      previewApi.beginZoom = token => {
        scrollTo(0, previewApi.metrics().anchors.find(a => a.source === 40).y);
        dispatchEvent(new Event('scroll'));
        return begin(token);
      };
      return true;
    })()""",
    )
    preview.set_zoom_factor(2)
    result = wait_zoom(qtbot, preview, 2)
    qtbot.waitUntil(lambda: bool(events))
    assert len(events) == 1
    assert events[0][0] == pytest.approx(40, abs=0.05)
    assert events[0][1] == 1
    assert result["source"] == pytest.approx(40, abs=0.05)
    assert result["measuredSource"] == pytest.approx(40, abs=0.05)
