"""Search uses matching settings in both panes and preserves rendered content."""

import json
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QTextCursor

from md_editor import app as app_module
from md_editor.app import MainWindow
from md_editor.search import qt_position


@pytest.fixture
def window(qtbot, tmp_path):
    widget = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(widget, before_close_func=lambda w: w.editor.document().setModified(False))
    widget.resize(1000, 600)
    widget.show()
    return widget


def evaluate(qtbot, window, expression):
    values = []
    window.preview.page().runJavaScript(f"JSON.stringify({expression})", values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=10000)
    return json.loads(values[0])


def prepare(qtbot, window, tmp_path, source, query="needle"):
    window.set_source(source, tmp_path)
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    window.show_search(replace=True)
    window.search.query.setText(query)


def wait_highlights(qtbot, window, expected, *, source=None):
    source = expected if source is None else source
    qtbot.waitUntil(
        lambda: [item.cursor.selectedText() for item in window.editor.extraSelections()] == source,
        timeout=15000,
    )
    qtbot.waitUntil(
        lambda: (
            evaluate(
                qtbot,
                window,
                "Array.from(CSS.highlights.get('md-editor-search') || [], range => range.toString())",
            )
            == expected
        ),
        timeout=15000,
    )


def test_search_options_empty_invalid_and_close_update_both_panes(qtbot, window, tmp_path):
    source = "# 😀Regex\nAlpha alpha ALPHA\nNamed42 named7\n\n**Split**Text"
    prepare(qtbot, window, tmp_path, source, "alpha")
    wait_highlights(qtbot, window, ["Alpha", "alpha", "ALPHA"])
    undo_steps = window.editor.document().availableUndoSteps()
    original = evaluate(qtbot, window, "document.getElementById('content').innerHTML")
    window.search.case_sensitive.setChecked(True)
    wait_highlights(qtbot, window, ["alpha"])
    window.search.case_sensitive.setChecked(False)
    window.search.regex.setChecked(True)
    window.search.query.setText(r"(?P<word>named)\d+")
    wait_highlights(qtbot, window, ["Named42", "named7"])
    window.search.query.setText("[")
    wait_highlights(qtbot, window, [])
    assert "正規表現エラー" in window.search.status.text()
    window.search.regex.setChecked(False)
    window.search.query.setText("SplitText")
    wait_highlights(qtbot, window, ["SplitText"], source=[])
    window.search.query.clear()
    wait_highlights(qtbot, window, [])
    window.search.query.setText("alpha")
    wait_highlights(qtbot, window, ["Alpha", "alpha", "ALPHA"])
    window.search.close_bar()
    wait_highlights(qtbot, window, [])
    assert window.editor.toPlainText() == source
    assert window.editor.document().availableUndoSteps() == undo_steps
    assert not window.editor.document().isModified()
    assert evaluate(qtbot, window, "document.getElementById('content').innerHTML") == original


def test_search_tracks_edits_replace_undo_new_document_theme_and_rerender(qtbot, window, tmp_path):
    prepare(qtbot, window, tmp_path, "# First\nneedle needle")
    wait_highlights(qtbot, window, ["needle"] * 2)
    window.editor.moveCursor(QTextCursor.MoveOperation.End)
    window.editor.insertPlainText(" needle")
    wait_highlights(qtbot, window, ["needle"] * 3)
    window.search.replacement.setText("changed")
    window.search.replace_all()
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    wait_highlights(qtbot, window, [])
    window.editor.undo()
    wait_highlights(qtbot, window, ["needle"] * 3)
    window.apply_theme("dark", persist=False)
    wait_highlights(qtbot, window, ["needle"] * 3)
    with qtbot.waitSignal(window.preview.ready, timeout=15000):
        window._render()
    wait_highlights(qtbot, window, ["needle"] * 3)
    window.set_source("# Different document\nneedle once", tmp_path)
    wait_highlights(qtbot, window, ["needle"])


def test_search_navigation_keeps_native_highlight_objects_and_preview_nodes(
    qtbot, window, tmp_path, monkeypatch
):
    source = "# 😀Search\nneedle first\n\nneedle second\n\nneedle third"
    prepare(qtbot, window, tmp_path, source)
    window.editor.moveCursor(QTextCursor.MoveOperation.Start)
    qtbot.waitUntil(
        lambda: not window._render_timer.isActive() and not window.search._timer.isActive()
    )
    wait_highlights(qtbot, window, ["needle"] * 3)
    generation = window.search_highlights.generation
    assert (
        evaluate(
            qtbot,
            window,
            """(() => {
      const content = document.getElementById('content');
      const highlight = CSS.highlights.get('md-editor-search');
      window.searchBaseline = {highlight, ranges: Array.from(highlight),
        html: content.innerHTML, nodes: Array.from(content.childNodes)};
      return highlight.size;
    })()""",
        )
        == 3
    )
    rendered = Mock(wraps=app_module.render_markdown)
    set_document = Mock(wraps=window.preview.set_document)
    monkeypatch.setattr(app_module, "render_markdown", rendered)
    monkeypatch.setattr(window.preview, "set_document", set_document)
    offsets = [
        qt_position(source, source.index(f"needle {name}")) for name in ("first", "second", "third")
    ]
    for navigate, index in (
        (window.search.next, 0),
        (window.search.next, 1),
        (window.search.previous, 0),
        (window.search.previous, 2),
        (window.search.next, 0),
    ):
        navigate()
        assert window.editor.textCursor().selectionStart() == offsets[index]
        qtbot.waitUntil(
            lambda index=index: (
                evaluate(
                    qtbot,
                    window,
                    """(() => {
                  const selection = window.getSelection();
                  const block = selection.anchorNode?.parentElement?.closest('p');
                  return [selection.toString(),
                    Array.from(document.querySelectorAll('#content p')).indexOf(block)];
                })()""",
                )
                == ["needle", index]
            ),
            timeout=5000,
        )
        assert window.search_highlights.generation == generation
        assert evaluate(
            qtbot,
            window,
            """(() => {
          const before = window.searchBaseline;
          const content = document.getElementById('content');
          const highlight = CSS.highlights.get('md-editor-search');
          const ranges = Array.from(highlight || []);
          return highlight === before.highlight && ranges.length === before.ranges.length &&
            ranges.every((range, i) => range === before.ranges[i]) &&
            content.innerHTML === before.html && content.childNodes.length === before.nodes.length &&
            Array.from(content.childNodes).every((node, i) => node === before.nodes[i]);
        })()""",
        )
    qtbot.wait(max(window._render_timer.interval(), window.search._timer.interval()) + 30)
    assert window.search_highlights.generation == generation
    rendered.assert_not_called()
    set_document.assert_not_called()
    assert window.editor.toPlainText() == source


def test_search_highlights_at_most_first_thousand_matches(qtbot, window, tmp_path):
    prepare(qtbot, window, tmp_path, "needle " * 1005)
    wait_highlights(qtbot, window, ["needle"] * 1000)
    assert len(window.search._matches) == 1005


def test_stale_collected_text_cannot_restore_an_old_query(qtbot, window, tmp_path, monkeypatch):
    prepare(qtbot, window, tmp_path, "needle other")
    wait_highlights(qtbot, window, ["needle"])
    page = window.preview.page()
    run = page.runJavaScript
    pending = []

    def defer(script, *args):
        if args and callable(args[-1]) and "runs.map" in script:
            pending.append((script, args[-1]))
        else:
            return run(script, *args)

    monkeypatch.setattr(page, "runJavaScript", defer)
    window.search_highlights.refresh()
    assert len(pending) == 1
    old_script, old_callback = pending.pop()
    values = []
    run(old_script, values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=10000)
    window.search.query.clear()
    wait_highlights(qtbot, window, [])
    old_callback(values[0])
    assert evaluate(qtbot, window, "CSS.highlights.has('md-editor-search')") is False
