import json
from pathlib import Path

import pytest

from md_editor.app import RESOURCE_DIR, MainWindow


def javascript(qtbot, window, source):
    values = []
    window.preview.page().runJavaScript(source, values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=10000)
    return values[0]


def metrics(qtbot, window):
    return json.loads(javascript(qtbot, window, "JSON.stringify(window.previewApi.metrics())"))


@pytest.fixture
def window(qtbot):
    widget = MainWindow()
    qtbot.addWidget(widget, before_close_func=lambda w: w.editor.document().setModified(False))
    widget.resize(1280, 800)
    widget.show()
    text = (RESOURCE_DIR / "scroll-check.md").read_text(encoding="utf-8")
    widget.set_source(text, RESOURCE_DIR)
    qtbot.waitUntil(lambda: widget._rendered_revision == widget._revision, timeout=15000)
    qtbot.wait(150)
    return widget


def test_left_to_right_content_sync_and_eof(qtbot, window):
    text = window.editor.toPlainText()
    targets = [i for i, line in enumerate(text.split("\n")) if line.startswith("## ")]
    targets.extend([window.editor.blockCount() - 1, 0])
    for target in targets:
        window.editor.scroll_to_source(target)
        qtbot.wait(80)
        result = metrics(qtbot, window)
        assert result["source"] == pytest.approx(target, abs=0.1)
        assert result["measuredSource"] == pytest.approx(target, abs=0.1)
        if target == window.editor.blockCount() - 1:
            assert result["scrollY"] == pytest.approx(result["maxScroll"], abs=2)
    assert window.editor.toPlainText() == text


def test_right_scroll_moves_left_without_bounce(qtbot, window):
    result = metrics(qtbot, window)
    candidates = [a for a in result["anchors"] if 15 <= a["source"] <= 100]
    for anchor in candidates[:: max(1, len(candidates) // 7)]:
        javascript(qtbot, window, f"window.scrollTo(0, {anchor['y'] + 4})")
        qtbot.wait(120)
        first = metrics(qtbot, window)
        qtbot.wait(120)
        settled = metrics(qtbot, window)
        assert abs(window.editor.source_position() - settled["source"]) < 1.05
        assert settled["scrollY"] == pytest.approx(first["scrollY"], abs=1)


def test_image_loaded_and_splitter_resize_keeps_content(qtbot, window):
    assert javascript(
        qtbot, window, "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
    )
    window.editor.scroll_to_source(65)
    qtbot.wait(80)
    window.splitter.setSizes([430, 850])
    qtbot.wait(150)
    result = metrics(qtbot, window)
    assert abs(window.editor.source_position() - 65) < 0.2
    assert abs(result["source"] - 65) < 0.2


def test_live_edit_and_disabled_sync(qtbot, window):
    window.editor.scroll_to_source(30)
    qtbot.wait(100)
    window.sync_action.setChecked(False)
    initial = metrics(qtbot, window)["scrollY"]
    window.editor.scroll_to_source(80)
    qtbot.wait(80)
    assert metrics(qtbot, window)["scrollY"] == pytest.approx(initial, abs=1)
    window.sync_action.setChecked(True)
    qtbot.wait(100)
    assert metrics(qtbot, window)["source"] == pytest.approx(80, abs=0.1)
    cursor = window.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    cursor.insertText("\n\n## 追加した見出し\n\n更新の確認")
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=10000)
    assert "追加した見出し" in javascript(qtbot, window, "document.body.innerText")
    window.editor.show_end()
    qtbot.wait(100)
    assert metrics(qtbot, window)["source"] == window.editor.blockCount() - 1


def test_capture_sample_for_visual_review(qtbot, window):
    output = Path(__file__).resolve().parents[1] / "artifacts"
    output.mkdir(exist_ok=True)
    window.editor.scroll_to_source(0)
    qtbot.wait(120)
    assert window.grab().save(str(output / "scroll-start.png"))
    image_line = next(
        i for i, s in enumerate(window.editor.toPlainText().split("\n")) if s.startswith("![")
    )
    window.editor.scroll_to_source(image_line)
    qtbot.wait(120)
    assert window.grab().save(str(output / "scroll-image.png"))
    window.editor.show_end()
    qtbot.wait(120)
    assert window.grab().save(str(output / "scroll-end.png"))
