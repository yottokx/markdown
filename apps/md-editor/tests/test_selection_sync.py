"""Native source/preview selections stay aligned without editing the document."""

import json

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QImage, QTextCursor
from PySide6.QtWidgets import QApplication

from md_editor.app import MainWindow
from md_editor.search import qt_position


@pytest.fixture
def window(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "selection.ini"), QSettings.Format.IniFormat)
    widget = MainWindow(settings)
    qtbot.addWidget(widget, before_close_func=lambda w: w.editor.document().setModified(False))
    widget.resize(1150, 780)
    widget.show()
    return widget


def javascript(qtbot, window, script):
    results = []
    window.preview.page().runJavaScript(script, results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=10000)
    return results[0]


def wait_render(qtbot, window):
    qtbot.waitUntil(
        lambda: (
            window._rendered_revision == window._revision
            and not window._render_timer.isActive()
            and not window._changing_display_mode
        ),
        timeout=15000,
    )
    qtbot.waitUntil(
        lambda: javascript(
            qtbot, window, "Boolean(document.querySelector('#content [data-selection-id]'))"
        ),
        timeout=15000,
    )


def load_source(qtbot, window, body):
    window.set_source(body, window.session.base_dir, window.session.path, update_session=False)
    wait_render(qtbot, window)


def focus_source(qtbot, window):
    window.activateWindow()
    window.editor.setFocus()
    qtbot.waitUntil(window.editor.hasFocus)


def focus_preview(qtbot, window):
    window.activateWindow()
    window.preview.view.setFocus()
    qtbot.waitUntil(lambda: window.selection_sync._in_preview(QApplication.focusWidget()))


def select_source(window, body, anchor, position):
    cursor = window.editor.textCursor()
    cursor.setPosition(qt_position(body, anchor))
    cursor.setPosition(qt_position(body, position), QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)


def preview_selection(qtbot, window, selector="#content p"):
    return json.loads(
        javascript(
            qtbot,
            window,
            """(() => {
              const selection = window.getSelection();
              const scope = document.querySelector(SELECTOR);
              const offset = (node, position) => {
                if (!node || !scope || !scope.contains(node)) return -1;
                const range = document.createRange();
                range.selectNodeContents(scope);
                range.setEnd(node, position);
                return range.toString().length;
              };
              return JSON.stringify({text: selection.toString(), count: selection.rangeCount,
                anchor: offset(selection.anchorNode, selection.anchorOffset),
                position: offset(selection.focusNode, selection.focusOffset)});
            })()""".replace("SELECTOR", json.dumps(selector)),
        )
    )


def wait_preview_selection(qtbot, window, text, selector="#content p"):
    qtbot.waitUntil(
        lambda: preview_selection(qtbot, window, selector)["text"] == text, timeout=10000
    )
    return preview_selection(qtbot, window, selector)


def select_preview(qtbot, window, anchor, position, selector="#content p"):
    # This is the same native Selection that browser drags and Shift+Arrow modify.
    script = """(() => {
      const scope = document.querySelector(SELECTOR);
      const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
      const parts = [];
      let node;
      let count = 0;
      while ((node = walker.nextNode())) {
        parts.push({node, start: count, end: count + node.length});
        count += node.length;
      }
      const point = offset => {
        const part = parts.find(item => item.end >= offset);
        return [part.node, offset - part.start];
      };
      window.getSelection().setBaseAndExtent(...point(ANCHOR), ...point(POSITION));
      return window.getSelection().toString();
    })()"""
    return javascript(
        qtbot,
        window,
        script.replace("SELECTOR", json.dumps(selector))
        .replace("ANCHOR", str(anchor))
        .replace("POSITION", str(position)),
    )


def wait_source_selection(qtbot, window, body, anchor, position):
    expected = (qt_position(body, anchor), qt_position(body, position))
    qtbot.waitUntil(
        lambda: (
            (window.editor.textCursor().anchor(), window.editor.textCursor().position()) == expected
        ),
        timeout=10000,
    )


def test_source_selection_clips_syntax_and_preserves_direction(qtbot, window):
    body = "**alpha** and [beta](custom:item) plus `code`."
    load_source(qtbot, window, body)
    focus_source(qtbot, window)
    cases = (
        (1, body.index("a**") + 2, "alpha"),
        (body.index("["), body.index("](custom:") + 7, "beta"),
        (body.index("`"), body.rindex("`") + 1, "code"),
        (body.index("beta") + 2, body.index("alpha") + 2, "pha and be"),
    )
    for anchor, position, text in cases:
        select_source(window, body, anchor, position)
        selected = wait_preview_selection(qtbot, window, text)
        assert (selected["anchor"] > selected["position"]) == (anchor > position)
        assert window.editor.hasFocus()
        wait_source_selection(qtbot, window, body, anchor, position)

    # Syntax by itself must not borrow a visible character outside the source range.
    select_source(window, body, 0, 2)
    assert wait_preview_selection(qtbot, window, "")["count"] == 0
    wait_source_selection(qtbot, window, body, 0, 2)
    select_source(window, body, 2, 5)
    wait_preview_selection(qtbot, window, "alp")
    select_source(window, body, 5, 5)
    assert wait_preview_selection(qtbot, window, "")["count"] == 0


def test_unicode_entities_and_repeated_words_use_exact_positions(qtbot, window):
    body = "😀 同じ **同じ** と同じ &amp; 終"
    rendered = "😀 同じ 同じ と同じ & 終"
    load_source(qtbot, window, body)
    focus_source(qtbot, window)
    second_source = body.index("同じ", body.index("同じ") + 1)
    second_preview = rendered.index("同じ", rendered.index("同じ") + 1)
    select_source(window, body, second_source, second_source + 2)
    selected = wait_preview_selection(qtbot, window, "同じ")
    assert selected["anchor"] == qt_position(rendered, second_preview)
    assert selected["position"] == qt_position(rendered, second_preview + 2)

    focus_preview(qtbot, window)
    last_source = body.rindex("同じ")
    last_preview = rendered.rindex("同じ")
    select_preview(
        qtbot,
        window,
        qt_position(rendered, last_preview),
        qt_position(rendered, last_preview + 2),
    )
    wait_source_selection(qtbot, window, body, last_source, last_source + 2)
    entity_preview = qt_position(rendered, rendered.index("&"))
    select_preview(qtbot, window, entity_preview, entity_preview + 1)
    entity_source = body.index("&amp;")
    wait_source_selection(qtbot, window, body, entity_source, entity_source + 5)
    assert window.editor.textCursor().selectedText() == "&amp;"
    assert window.selection_sync._in_preview(QApplication.focusWidget())


def test_preview_selection_maps_contiguous_source_and_copy_target(qtbot, window):
    body = "前 **bold *inner* end** と [label](custom:item) 後"
    rendered = "前 bold inner end と label 後"
    load_source(qtbot, window, body)
    focus_source(qtbot, window)
    select_source(window, body, 0, 1)
    wait_preview_selection(qtbot, window, "前")
    focus_preview(qtbot, window)

    start = rendered.index("bold") + 1
    end = rendered.index("inner") + 4
    source_start = body.index("bold") + 1
    source_end = body.index("inner") + 4
    for anchor, position, expected_anchor, expected_position in (
        (start, end, source_start, source_end),
        (end, start, source_end, source_start),
    ):
        assert select_preview(qtbot, window, anchor, position) == "old inne"
        wait_source_selection(qtbot, window, body, expected_anchor, expected_position)
        assert window.editor.textCursor().selectedText() == "old *inne"
        assert window.selection_sync._in_preview(QApplication.focusWidget())
        assert window.preview_edit_target()
        window.copy_active()
        qtbot.waitUntil(lambda: QApplication.clipboard().text() == "old inne")

    focus_source(qtbot, window)
    window.copy_active()
    assert QApplication.clipboard().text() == "old *inne"
    focus_preview(qtbot, window)
    javascript(qtbot, window, "window.getSelection().removeAllRanges()")
    qtbot.waitUntil(lambda: not window.editor.textCursor().hasSelection())
    assert window.selection_sync._in_preview(QApplication.focusWidget())


def test_selection_keeps_document_undo_dirty_state_and_render_dom(qtbot, window, tmp_path):
    body = "# Selection\n\nfirst **bold** paragraph\n\nsecond paragraph"
    load_source(qtbot, window, body)
    path = tmp_path / "selection.md"
    assert window.save_document(path)
    wait_render(qtbot, window)
    document = window.editor.document()
    baseline = (
        document.availableUndoSteps(),
        document.availableRedoSteps(),
        document.isModified(),
        window._revision,
        path.read_bytes(),
    )
    javascript(
        qtbot,
        window,
        "window.selectionBaseline = Array.from(document.getElementById('content').childNodes)",
    )
    focus_source(qtbot, window)
    select_source(window, body, body.index("bold"), body.index("second") + len("second"))
    qtbot.waitUntil(
        lambda: "second" in preview_selection(qtbot, window, "#content")["text"], timeout=10000
    )
    focus_preview(qtbot, window)
    assert select_preview(qtbot, window, 0, 5, "#content p:last-child") == "secon"
    wait_source_selection(qtbot, window, body, body.index("second"), body.index("second") + 5)
    qtbot.wait(100)
    assert window.editor.toPlainText() == body
    assert (
        document.availableUndoSteps(),
        document.availableRedoSteps(),
        document.isModified(),
        window._revision,
        path.read_bytes(),
    ) == baseline
    assert javascript(
        qtbot,
        window,
        """(() => {
          const nodes = Array.from(document.getElementById('content').childNodes);
          return nodes.length === window.selectionBaseline.length &&
            nodes.every((node, index) => node === window.selectionBaseline[index]);
        })()""",
    )


def test_previous_file_and_older_source_selection_events_are_ignored(qtbot, window, tmp_path):
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    first.write_text("first **file**", encoding="utf-8")
    body = "second **file**"
    second.write_text(body, encoding="utf-8")
    assert window.open_path(first)
    wait_render(qtbot, window)
    old_revision = window._revision
    assert window.open_path(second)
    wait_render(qtbot, window)
    focus_source(qtbot, window)
    select_source(window, body, 0, 6)
    wait_preview_selection(qtbot, window, "second")
    sequence = window.selection_sync.sequence
    focus_preview(qtbot, window)
    for anchor, position, revision, event_sequence in (
        (1, 3, old_revision, sequence),
        (1, 3, window._revision, sequence - 1),
        (-2, 3, window._revision, sequence),
        (0, 10000, window._revision, sequence),
    ):
        window.preview.selection_changed.emit(anchor, position, revision, event_sequence)
        wait_source_selection(qtbot, window, body, 0, 6)
    assert window.path == second
    assert window.open_path(first)
    wait_render(qtbot, window)
    focus_source(qtbot, window)
    first_body = window.editor.toPlainText()
    start = first_body.index("file")
    select_source(window, first_body, start, start + 4)
    wait_preview_selection(qtbot, window, "file")
    focus_preview(qtbot, window)
    select_preview(qtbot, window, 0, 5)
    wait_source_selection(qtbot, window, first_body, 0, 5)


@pytest.mark.parametrize(
    "restore_deleted", [False, True], ids=["save-as-undo", "deleted-image-undo"]
)
def test_normalized_image_urls_do_not_shift_original_source_mapping(
    qtbot, window, tmp_path, restore_deleted
):
    old, new = tmp_path / "old", tmp_path / "new-folder"
    old.mkdir()
    new.mkdir()
    assert window.save_document(old / "a.md")
    picture = QImage(8, 8, QImage.Format.Format_ARGB32)
    picture.fill(QColor("#4477aa"))
    body = window.session.add_image(picture) + "\n\n😀 画像の後の **選択対象** です"
    load_source(qtbot, window, body)
    assert window.save_document()
    if restore_deleted:
        # Preserve a reference in undo history even though Save As does not copy it.
        cursor = window.editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(
            qt_position(body, body.index("\n\n") + 2), QTextCursor.MoveMode.KeepAnchor
        )
        cursor.removeSelectedText()
    assert window.save_document(new / "longer-destination-name.md")
    window.editor.undo()
    assert window.editor.toPlainText() == body
    normalized = window.session.normalize_references(body)
    assert normalized != body
    assert len(normalized) != len(body)
    wait_render(qtbot, window)
    qtbot.waitUntil(
        lambda: javascript(
            qtbot,
            window,
            "document.querySelector('#content img').src.includes('_mdedit=') && "
            "document.querySelector('#content img').naturalWidth === 8",
        ),
        timeout=10000,
    )
    focus_source(qtbot, window)
    start = body.index("選択対象")
    select_source(window, body, start, start + 4)
    wait_preview_selection(qtbot, window, "選択対象", "#content p:last-child")
    focus_preview(qtbot, window)
    assert select_preview(qtbot, window, 3, 8, "#content p:last-child") == "画像の後の"
    start = body.index("画像の後の")
    wait_source_selection(qtbot, window, body, start, start + 5)


def test_native_keyboard_selection_syncs_from_both_panes(qtbot, window):
    body = "**alpha** beta"
    load_source(qtbot, window, body)
    focus_source(qtbot, window)
    select_source(window, body, 2, 7)
    wait_preview_selection(qtbot, window, "alpha")
    focus_preview(qtbot, window)
    qtbot.keyClick(
        window.preview.view.focusProxy(),
        Qt.Key.Key_Right,
        modifier=Qt.KeyboardModifier.ShiftModifier,
    )
    wait_source_selection(qtbot, window, body, 2, 10)
    assert preview_selection(qtbot, window)["text"] == "alpha "
    focus_source(qtbot, window)
    qtbot.keyClick(window.editor, Qt.Key.Key_Right, modifier=Qt.KeyboardModifier.ShiftModifier)
    wait_preview_selection(qtbot, window, "alpha b")
    assert window.editor.toPlainText() == body


def test_preview_choice_supersedes_pending_source_debounce(qtbot, window):
    body = "alpha beta gamma"
    load_source(qtbot, window, body)
    focus_source(qtbot, window)
    select_source(window, body, 0, 5)
    wait_preview_selection(qtbot, window, "alpha")
    window.selection_sync._timer.setInterval(150)
    select_source(window, body, 6, 10)
    assert window.selection_sync._timer.isActive()
    focus_preview(qtbot, window)
    select_preview(qtbot, window, 11, 16)
    wait_source_selection(qtbot, window, body, 11, 16)
    qtbot.wait(200)
    assert preview_selection(qtbot, window)["text"] == "gamma"
    wait_source_selection(qtbot, window, body, 11, 16)


def test_selection_survives_hidden_preview_and_render_completion(qtbot, window):
    body = "first **bold** and second"
    load_source(qtbot, window, body)
    focus_source(qtbot, window)
    select_source(window, body, body.index("bold"), body.index("bold") + 4)
    wait_preview_selection(qtbot, window, "bold")
    window.set_display_mode("source")
    qtbot.waitUntil(lambda: not window._changing_display_mode)
    start = body.index("second")
    select_source(window, body, start + 6, start)
    window.set_display_mode("split")
    qtbot.waitUntil(lambda: not window._changing_display_mode, timeout=10000)
    selected = wait_preview_selection(qtbot, window, "second")
    assert selected["anchor"] > selected["position"]
    focus_preview(qtbot, window)
    select_preview(qtbot, window, 0, 5)
    wait_source_selection(qtbot, window, body, 0, 5)
    window.preview.ready.emit(window._revision)
    qtbot.wait(100)
    assert preview_selection(qtbot, window)["text"] == "first"
    wait_source_selection(qtbot, window, body, 0, 5)
    assert window.selection_sync._in_preview(QApplication.focusWidget())


def test_focus_without_new_selection_keeps_pending_source_range(qtbot, window):
    body = "alpha beta gamma"
    load_source(qtbot, window, body)
    focus_source(qtbot, window)
    select_source(window, body, 0, 5)
    wait_preview_selection(qtbot, window, "alpha")
    window.selection_sync._timer.setInterval(150)
    select_source(window, body, 6, 10)
    assert window.selection_sync._timer.isActive()
    focus_preview(qtbot, window)
    wait_preview_selection(qtbot, window, "beta")
    wait_source_selection(qtbot, window, body, 6, 10)
    assert window.selection_sync._in_preview(QApplication.focusWidget())
