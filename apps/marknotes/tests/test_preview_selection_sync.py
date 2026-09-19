"""Native preview selections follow source positions without taking keyboard focus."""

import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QWidget

from marknotes.preview import PreviewPane

SOURCE = "same 😀 **same** end\n\nsame"
HTML = (
    '<p data-source-line="0" data-source-end="1">'
    '<span data-selection-id="s0">same 😀 </span>'
    '<strong data-selection-id="s1">same</strong>'
    '<span data-selection-id="s2"> end</span></p>'
    '<p data-source-line="2" data-source-end="3"><span data-selection-id="s3">same</span></p>'
)
MAP = (
    {"id": "s0", "segments": [[0, 8, 0, 8]]},
    {"id": "s1", "segments": [[0, 4, 10, 14]]},
    {"id": "s2", "segments": [[0, 4, 16, 20]]},
    {"id": "s3", "segments": [[0, 4, 22, 26]]},
)


@pytest.fixture
def panes(qtbot):
    host = QWidget()
    row = QHBoxLayout(host)
    editor = QPlainTextEdit()
    preview = PreviewPane()
    row.addWidget(editor)
    row.addWidget(preview)
    host.resize(1000, 500)
    qtbot.addWidget(host)
    host.show()
    host.activateWindow()
    qtbot.waitUntil(lambda: preview._shell_ready, timeout=15000)
    yield editor, preview


def evaluate(qtbot, preview, expression):
    results = []
    expression = expression.strip().removesuffix(";")
    preview.page().runJavaScript(f"JSON.stringify({expression})", results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    return json.loads(results[0])


def load(qtbot, preview, tmp_path, *, html=HTML, mapping=MAP, length=26, revision=1):
    with qtbot.waitSignal(preview.ready, timeout=15000):
        preview.set_document(
            html, 3, tmp_path, revision, selection_map=mapping, source_length=length
        )


def selection(qtbot, preview):
    return evaluate(
        qtbot,
        preview,
        "(() => {const s=getSelection(); return {text:s.toString(), count:s.rangeCount,"
        "anchor:s.anchorOffset, focus:s.focusOffset,"
        "anchorId:s.anchorNode?.parentElement?.dataset.selectionId,"
        "focusId:s.focusNode?.parentElement?.dataset.selectionId};})()",
    )


@pytest.mark.parametrize("backwards", [False, True])
def test_source_selection_spans_blocks_preserves_direction_and_focus(
    qtbot, panes, tmp_path, backwards
):
    editor, preview = panes
    load(qtbot, preview, tmp_path)
    editor.setPlainText(SOURCE)
    editor.setFocus()
    qtbot.waitUntil(editor.hasFocus)
    reports = []
    preview.selection_changed.connect(lambda *args: reports.append(args))
    preview.set_source_selection(26 if backwards else 10, 10 if backwards else 26, 1, 4)
    state = selection(qtbot, preview)
    assert state["text"].startswith("same end")
    assert state["text"].endswith("same")
    assert state["anchorId"] == ("s3" if backwards else "s1")
    assert state["focusId"] == ("s1" if backwards else "s3")
    assert editor.hasFocus()
    qtbot.wait(80)
    assert reports == []


def test_source_boundaries_skip_markdown_syntax_and_keep_emoji_offsets(qtbot, panes, tmp_path):
    _, preview = panes
    load(qtbot, preview, tmp_path)
    preview.set_source_selection(8, 12, 1, 1)
    assert selection(qtbot, preview)["text"] == "sa"
    preview.set_source_selection(5, 7, 1, 2)
    assert selection(qtbot, preview)["text"] == "😀"
    preview.set_source_selection(8, 10, 1, 3)
    assert selection(qtbot, preview)["count"] == 0
    preview.set_source_selection(10, 14, 1, 4)
    assert selection(qtbot, preview)["text"] == "same"
    preview.set_source_selection(12, 12, 1, 5)
    assert selection(qtbot, preview)["count"] == 0


@pytest.mark.parametrize("backwards", [False, True])
def test_browser_range_reports_exact_repeated_occurrence_and_reverse_direction(
    qtbot, panes, tmp_path, backwards
):
    _, preview = panes
    load(qtbot, preview, tmp_path)
    preview.set_source_selection(0, 0, 1, 7)
    reports = []
    preview.selection_changed.connect(lambda *args: reports.append(args))
    evaluate(
        qtbot,
        preview,
        "(() => {const a=document.querySelector('[data-selection-id=s1]').firstChild;"
        "const b=document.querySelector('[data-selection-id=s3]').firstChild;"
        + (
            "getSelection().setBaseAndExtent(b,4,a,1);"
            if backwards
            else "getSelection().setBaseAndExtent(a,1,b,4);"
        )
        + "return true;})()",
    )
    expected = (26, 11, 1, 7) if backwards else (11, 26, 1, 7)
    qtbot.waitUntil(lambda: expected in reports, timeout=5000)


def test_actual_browser_drag_reports_multi_block_selection_without_focus_jump(
    qtbot, panes, tmp_path
):
    editor, preview = panes
    load(qtbot, preview, tmp_path)
    preview.set_source_selection(0, 0, 1, 9)
    points = evaluate(
        qtbot,
        preview,
        "(() => {const a=document.querySelector('[data-selection-id=s0]').getBoundingClientRect();"
        "const b=document.querySelector('[data-selection-id=s3]').getBoundingClientRect();"
        "return [[Math.round(a.left+1),Math.round(a.top+a.height/2)],"
        "[Math.round(b.right+2),Math.round(b.top+b.height/2)]];})()",
    )
    reports = []
    preview.selection_changed.connect(lambda *args: reports.append(args))
    target = preview.view.focusProxy()
    assert target is not None
    start, end = QPoint(*points[0]), QPoint(*points[1])
    qtbot.mousePress(target, Qt.MouseButton.LeftButton, pos=start)
    for step in range(1, 6):
        position = QPoint(
            start.x() + (end.x() - start.x()) * step // 5,
            start.y() + (end.y() - start.y()) * step // 5,
        )
        qtbot.mouseMove(target, pos=position, delay=15)
    qtbot.mouseRelease(target, Qt.MouseButton.LeftButton, pos=end)
    qtbot.waitUntil(
        lambda: any(anchor <= 1 and position >= 22 for anchor, position, _, _ in reports),
        timeout=5000,
    )
    assert not editor.hasFocus()
    assert target.hasFocus() or preview.view.hasFocus()
    assert selection(qtbot, preview)["text"].count("same") == 3
    reports.clear()
    evaluate(
        qtbot,
        preview,
        "(() => {getSelection().selectAllChildren(document.body);return true;})()",
    )
    qtbot.waitUntil(lambda: (0, 26, 1, 9) in reports)


def test_atomic_conversion_maps_visible_selection_to_full_source_span(qtbot, panes, tmp_path):
    _, preview = panes
    html = '<p><span data-selection-id="e">&amp;</span> <span data-selection-id="m">x²</span></p>'
    mapping = (
        {"id": "e", "segments": [[0, 1, 0, 5]]},
        {"id": "m", "start": 6, "end": 13, "atomic": True},
    )
    load(qtbot, preview, tmp_path, html=html, mapping=mapping, length=13)
    preview.set_source_selection(2, 3, 1, 1)
    assert selection(qtbot, preview)["text"] == "&"
    preview.set_source_selection(8, 9, 1, 2)
    assert selection(qtbot, preview)["text"] == "x²"
    reports = []
    preview.selection_changed.connect(lambda *args: reports.append(args))
    evaluate(
        qtbot,
        preview,
        "(() => {const n=document.querySelector('[data-selection-id=m]').firstChild;"
        "getSelection().setBaseAndExtent(n,1,n,2);return true;})()",
    )
    qtbot.waitUntil(lambda: (6, 13, 1, 2) in reports)


def test_stale_requests_and_notifications_cannot_change_current_selection(qtbot, panes, tmp_path):
    _, preview = panes
    load(qtbot, preview, tmp_path, revision=3)
    preview.set_source_selection(10, 14, 3, 5)
    assert selection(qtbot, preview)["text"] == "same"
    preview.set_source_selection(5, 7, 3, 4)
    preview.set_source_selection(5, 7, 2, 6)
    assert selection(qtbot, preview)["text"] == "same"
    reports = []
    preview.selection_changed.connect(lambda *args: reports.append(args))
    for arguments in [(0, 4, 2, 5), (0, 4, 3, 4), (-1, 4, 3, 5), (0, 99, 3, 5)]:
        preview._bridge.selectionChanged(*arguments)
    assert reports == []
    preview._bridge.selectionChanged(-1, -1, 3, 5)
    assert reports == [(-1, -1, 3, 5)]
    assert evaluate(qtbot, preview, "window.previewApi.setSourceSelection(0,4,3,4)") is False


def test_pending_selection_and_theme_rebuild_keep_native_range(qtbot, panes, tmp_path):
    _, preview = panes
    preview.set_source_selection(10, 14, 2, 3)
    load(qtbot, preview, tmp_path, revision=2)
    assert selection(qtbot, preview)["text"] == "same"
    preview.apply_theme(True)
    qtbot.waitUntil(lambda: not evaluate(qtbot, preview, "window.previewApi.metrics().rendering"))
    assert selection(qtbot, preview)["text"] == "same"
    load(qtbot, preview, tmp_path, revision=3)
    assert selection(qtbot, preview)["count"] == 0


def test_invalid_and_hidden_mapping_is_ignored(qtbot, panes, tmp_path):
    _, preview = panes
    html = '<p><span data-selection-id="a">shown</span><span hidden data-selection-id="b">hide</span></p>'
    mapping = (
        {"id": "a", "segments": [[0, 999, 0, 5], [0, 5, -1, 4]]},
        {"id": "b", "segments": [[0, 4, 5, 9]]},
    )
    load(qtbot, preview, tmp_path, html=html, mapping=mapping, length=9)
    preview.set_source_selection(0, 9, 1, 1)
    assert selection(qtbot, preview)["count"] == 0


@pytest.mark.parametrize("already_reported", [False, True])
def test_new_browser_selection_wins_over_a_delayed_source_request(
    qtbot, panes, tmp_path, already_reported
):
    editor, preview = panes
    load(qtbot, preview, tmp_path)
    preview.set_source_selection(10, 14, 1, 1)
    assert selection(qtbot, preview)["anchorId"] == "s1"
    preview.view.setFocus()
    qtbot.waitUntil(lambda: evaluate(qtbot, preview, "document.hasFocus()"))
    reports = []
    preview.selection_changed.connect(lambda *args: reports.append(args))
    select_last = (
        "const node=document.querySelector('[data-selection-id=s3]').firstChild;"
        "getSelection().setBaseAndExtent(node,4,node,0);"
    )
    if already_reported:
        evaluate(qtbot, preview, "(() => {" + select_last + "return true;})()")
        qtbot.waitUntil(lambda: (26, 22, 1, 1) in reports)
    # The Python sender has advanced its sequence, while its asynchronous JS
    # command is still behind the browser's newer selection operation.
    preview._selection_sequence = 2
    evaluate(
        qtbot,
        preview,
        "(() => {"
        + ("" if already_reported else select_last)
        + "return window.previewApi.setSourceSelection(0,4,1,2);})()",
    )
    qtbot.waitUntil(lambda: (26, 22, 1, 2) in reports)
    state = selection(qtbot, preview)
    assert state["anchorId"] == "s3"
    assert (state["anchor"], state["focus"]) == (4, 0)

    # Focus returning to source ends browser ownership. New source selections
    # and selection clearing must be accepted again.
    editor.setFocus()
    qtbot.waitUntil(lambda: not evaluate(qtbot, preview, "document.hasFocus()"))
    preview.set_source_selection(0, 4, 1, 3)
    assert selection(qtbot, preview)["anchorId"] == "s0"
    preview.set_source_selection(4, 4, 1, 4)
    assert selection(qtbot, preview)["count"] == 0


def test_focus_without_new_browser_selection_accepts_pending_source_range(qtbot, panes, tmp_path):
    _, preview = panes
    load(qtbot, preview, tmp_path)
    preview.set_source_selection(10, 14, 1, 1)
    assert selection(qtbot, preview)["anchorId"] == "s1"
    preview.view.setFocus()
    qtbot.waitUntil(lambda: evaluate(qtbot, preview, "document.hasFocus()"))
    preview.set_source_selection(0, 4, 1, 2)
    assert selection(qtbot, preview)["anchorId"] == "s0"
