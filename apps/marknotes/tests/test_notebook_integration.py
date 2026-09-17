"""Real notebook windows against isolated libraries, including asynchronous saves."""

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QInputMethodEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from marknotes import app as app_module
from marknotes import notebook as notebook_module
from marknotes.managed_assets import ManagedAssets
from marknotes.notebook import NotebookWindow
from marknotes.notebook_store import NotebookStore, find_match_ranges
from marknotes.search import qt_position


@pytest.fixture
def make_notebook(qtbot, tmp_path):
    windows = []

    def make(root=None):
        settings = QSettings(str(tmp_path / "notebook.ini"), QSettings.Format.IniFormat)
        settings.setValue("display/mode", "split")
        window = NotebookWindow(root or tmp_path / "library", settings=settings)
        windows.append(window)
        # Own cleanup: pytest-qt deletes registered widgets before asynchronous close completes.
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        window.resize(1200, 760)
        window.show()
        return window

    yield make

    for window in reversed(windows):
        if not window._shutdown_done:
            qtbot.waitUntil(lambda window=window: not window._busy, timeout=15000)
            window.close()
            qtbot.waitUntil(lambda window=window: window._shutdown_done, timeout=15000)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()


def new_note(qtbot, window, text=""):
    previous = set(window._order)
    window.new_document()
    qtbot.waitUntil(lambda: window._active is not None and window._active not in previous)
    note_id = window._active
    if text:
        window.editor.insertPlainText(text)
    return note_id


def activate(qtbot, window, note_id):
    window.open_note(note_id)
    qtbot.waitUntil(lambda: window._active == note_id)
    # View restoration is queued independently from the document swap.
    qtbot.wait(1)


def saved(qtbot, window):
    window.save_pending()
    qtbot.waitUntil(
        lambda: not window._save_inflight and not any(s.dirty for s in window._sessions.values()),
        timeout=10000,
    )


def close_window(qtbot, window):
    qtbot.waitUntil(lambda: not window._busy)
    window.close()
    qtbot.waitUntil(lambda: window._shutdown_done, timeout=15000)


def javascript(qtbot, window, script):
    values = []
    window.preview.page().runJavaScript(script, values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=10000)
    return values[0]


def test_autosave_uses_managed_storage_and_keeps_undo_per_tab(qtbot, make_notebook):
    window = make_notebook()
    assert window._order == []
    first = new_note(qtbot, window, "# 最初のメモ\n本文 A")
    qtbot.waitUntil(lambda: not window._sessions[first].dirty, timeout=5000)
    assert window.store.get(first).body == "# 最初のメモ\n本文 A"
    assert window.tabs.tab_bar.tabText(0) == "最初のメモ"
    assert window.path is None
    second = new_note(qtbot, window, "# 次のメモ\n本文 B")
    activate(qtbot, window, first)
    first_document = window.editor.document()
    assert window.editor.toPlainText().endswith("本文 A")
    assert window.editor.document().isUndoAvailable()
    activate(qtbot, window, second)
    window.editor.undo()
    assert window.editor.toPlainText() == ""
    assert first_document.toPlainText() == "# 最初のメモ\n本文 A"
    activate(qtbot, window, first)
    assert window.editor.document() is first_document
    assert window.editor.toPlainText() == "# 最初のメモ\n本文 A"
    activate(qtbot, window, second)
    window.editor.redo()
    saved(qtbot, window)
    assert window.store.get(second).body == "# 次のメモ\n本文 B"


def test_single_and_bulk_close_reopen_preserves_notes_order_and_selection(qtbot, make_notebook):
    window = make_notebook()
    ids = [new_note(qtbot, window, f"# Note {index}") for index in range(3)]
    activate(qtbot, window, ids[1])
    window.close_current()
    qtbot.waitUntil(lambda: not window._busy and ids[1] not in window._order)
    assert window.store.get(ids[1]).body == "# Note 1"
    assert window.store.closed_history_count() == 1
    window.reopen_closed()
    qtbot.waitUntil(lambda: not window._busy and window._active == ids[1])
    assert window._order == ids
    assert not window.editor.document().isUndoAvailable()
    window.close_all()
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    assert window._active is None
    assert window.pages.currentIndex() == 1
    assert len(window.store.list_notes()) == 3
    assert window.store.closed_history_count() == 1
    window.reopen_closed()
    qtbot.waitUntil(lambda: not window._busy and window._active == ids[1])
    assert window._order == ids
    assert window.store.closed_history_count() == 0


def test_restart_restores_note_view_and_can_reopen_closed_group(qtbot, make_notebook):
    window = make_notebook()
    first = new_note(qtbot, window, "# 完成済み\nプレビューで読む")
    window.set_display_mode("preview")
    second = new_note(qtbot, window, "# 下書き\nまだ書く")
    window.set_display_mode("source")
    close_window(qtbot, window)
    restored = make_notebook(window.store.root)
    qtbot.waitUntil(lambda: restored._active == second)
    assert restored._order == [first, second]
    assert restored.display_mode == "source"
    activate(qtbot, restored, first)
    assert restored.display_mode == "preview"
    assert restored.editor.toPlainText() == "# 完成済み\nプレビューで読む"
    restored.close_all()
    qtbot.waitUntil(lambda: not restored._busy and restored._order == [])
    close_window(qtbot, restored)
    reopened = make_notebook(window.store.root)
    assert reopened._order == []
    reopened.reopen_closed()
    qtbot.waitUntil(lambda: not reopened._busy and reopened._active == first)
    assert reopened._order == [first, second]
    assert reopened.display_mode == "preview"
    assert reopened.store.closed_history_count() == 0


def test_pin_reading_and_display_changes_keep_body_update_date(qtbot, make_notebook):
    window = make_notebook()
    first = new_note(qtbot, window, "# 読み返すノート")
    saved(qtbot, window)
    original = window.store.get(first)
    window.toggle_pin()
    qtbot.waitUntil(lambda: window.store.get(first).pinned)
    window.set_display_mode("preview")
    second = new_note(qtbot, window, "# 別のノート")
    activate(qtbot, window, first)
    window.show_history()
    saved(qtbot, window)
    result = window.store.get(first)
    assert result.updated_at == original.updated_at
    assert result.created_at == original.created_at
    assert result.revision == original.revision
    assert result.pinned
    window.sidebar.set_mode("pinned")
    qtbot.waitUntil(lambda: [card.note_id for _item, card in window.sidebar._cards] == [first])
    assert second not in [card.note_id for _item, card in window.sidebar._cards]


def test_mixed_clipboard_attachments_finish_in_origin_note_after_tab_switch(
    qtbot, make_notebook, tmp_path, monkeypatch
):
    window = make_notebook()
    first = new_note(qtbot, window, "# 添付元\n")
    second = new_note(qtbot, window, "# 作業中\n")
    activate(qtbot, window, first)
    image_path = tmp_path / "画像.png"
    image = QImage(8, 8, QImage.Format.Format_ARGB32)
    image.fill(QColor("#3377aa"))
    assert image.save(str(image_path))
    document_path = tmp_path / "議事録 [最終].pdf"
    document_path.write_bytes(b"%PDF-1.4\nattachment fixture")
    started, release = threading.Event(), threading.Event()
    original = ManagedAssets.import_files

    def delayed(manager, paths):
        started.set()
        if not release.wait(10):
            raise TimeoutError("test did not release asset copy")
        return original(manager, paths)

    monkeypatch.setattr(ManagedAssets, "import_files", delayed)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(image_path)), QUrl.fromLocalFile(str(document_path))])
    try:
        window.paste_mime(mime)
        qtbot.waitUntil(started.is_set)
        activate(qtbot, window, second)
        assert window.editor.toPlainText() == "# 作業中\n"
    finally:
        release.set()
    qtbot.waitUntil(lambda: window._sessions[first].pending_assets == 0, timeout=10000)
    saved(qtbot, window)
    first_body = window.store.get(first).body
    assert "![" in first_body
    assert ".pdf)" in first_body
    assert "assets/" in first_body
    assert window.store.get(second).body == "# 作業中\n"
    assert len(list((window.store.note_dir(first) / "assets").iterdir())) == 2
    image_path.unlink()
    document_path.unlink()
    assert all(path.is_file() for path in (window.store.note_dir(first) / "assets").iterdir())


def test_library_search_highlights_each_hit_without_modifying_body_undo_or_view(
    qtbot, make_notebook
):
    window = make_notebook()
    first = new_note(qtbot, window, "# 😀メモ\n最初のメモと二つ目のメモ")
    new_note(qtbot, window, "# 買い物\nメモを確認")
    activate(qtbot, window, first)
    window.set_display_mode("preview")
    saved(qtbot, window)
    before = window.store.get(first)
    undo_steps = window.editor.document().availableUndoSteps()
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    preview_before = javascript(qtbot, window, "document.getElementById('content').innerHTML")
    window.show_library_search()
    window.sidebar.set_query("メモ", emit=True)
    qtbot.waitUntil(
        lambda: (
            len(window.sidebar._cards) == 2
            and all(
                "background-color:" in card.label.text() for _item, card in window.sidebar._cards
            )
        ),
        timeout=10000,
    )
    assert [s.cursor.selectedText() for s in window.editor.extraSelections()] == ["メモ"] * 3
    for _item, card in window.sidebar._cards:
        assert "background-color:" in card.label.text()
    assert window.display_mode == "preview"
    assert (
        javascript(qtbot, window, "CSS.highlights.get('marknotes-library-search')?.size || 0") == 3
    )
    assert (
        javascript(qtbot, window, "document.getElementById('content').innerHTML") == preview_before
    )
    assert window.editor.document().availableUndoSteps() == undo_steps
    assert window.editor.toPlainText() == before.body
    after = window.store.get(first)
    assert after.updated_at == before.updated_at
    assert after.revision == before.revision
    window.sidebar.set_query("", emit=True)
    assert window.editor.extraSelections() == []
    assert (
        javascript(qtbot, window, "CSS.highlights.get('marknotes-library-search')?.size || 0") == 0
    )
    assert window.editor.document().availableUndoSteps() == undo_steps


def wait_note_search_highlights(qtbot, window, expected, *, source=None):
    expected_source = expected if source is None else source
    qtbot.waitUntil(
        lambda: (
            [s.cursor.selectedText() for s in window.editor.extraSelections()] == expected_source
        ),
        timeout=15000,
    )
    qtbot.waitUntil(
        lambda: (
            json.loads(
                javascript(
                    qtbot,
                    window,
                    "JSON.stringify(Array.from(CSS.highlights.get('marknotes-note-search') || [], "
                    "range => range.toString()))",
                )
            )
            == expected
        ),
        timeout=15000,
    )


def test_note_search_highlights_both_panes_without_changing_note_or_preview(qtbot, make_notebook):
    window = make_notebook()
    note_id = new_note(
        qtbot,
        window,
        "# 😀メモ\n最初の**メモ**と二つ目のメモ\n\n[メモ](https://example.com/hidden)",
    )
    saved(qtbot, window)
    before = window.store.get(note_id)
    undo_steps = window.editor.document().availableUndoSteps()
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    preview_before = javascript(qtbot, window, "document.getElementById('content').innerHTML")

    window.show_search()
    window.search.query.setText("メモ")
    wait_note_search_highlights(qtbot, window, ["メモ"] * 4)

    assert window.display_mode == "split"
    assert (
        javascript(qtbot, window, "document.getElementById('content').innerHTML") == preview_before
    )
    assert window.editor.toPlainText() == before.body
    assert window.editor.document().availableUndoSteps() == undo_steps
    assert not window._sessions[note_id].dirty
    after = window.store.get(note_id)
    assert after.updated_at == before.updated_at
    assert after.revision == before.revision


def test_note_search_options_and_invalid_query_update_both_panes(qtbot, make_notebook):
    window = make_notebook()
    new_note(qtbot, window, "# 😀Regex\nAlpha alpha ALPHA\nNamed42 named7\n\n**Split**Text")
    window.show_search()
    window.search.query.setText("alpha")
    wait_note_search_highlights(qtbot, window, ["Alpha", "alpha", "ALPHA"])

    window.search.case_sensitive.setChecked(True)
    wait_note_search_highlights(qtbot, window, ["alpha"])
    window.search.case_sensitive.setChecked(False)
    window.search.regex.setChecked(True)
    window.search.query.setText(r"(?P<word>named)\d+")
    wait_note_search_highlights(qtbot, window, ["Named42", "named7"])

    window.search.query.setText("[")
    wait_note_search_highlights(qtbot, window, [])
    assert "正規表現エラー" in window.search.status.text()

    window.search.regex.setChecked(False)
    window.search.query.setText("SplitText")
    wait_note_search_highlights(qtbot, window, ["SplitText"], source=[])
    window.search.query.clear()
    wait_note_search_highlights(qtbot, window, [])


def test_note_search_regex_uses_document_boundaries_in_both_panes(qtbot, make_notebook):
    window = make_notebook()
    new_note(qtbot, window, "needle\n\nneedle")
    window.show_search()
    window.search.regex.setChecked(True)

    for query, source_start in (
        (r"^needle", 0),
        (r"\Aneedle", 0),
        (r"needle$", 8),
        (r"needle\Z", 8),
        (r"(?<=\n)needle", 8),
    ):
        window.search.query.clear()
        wait_note_search_highlights(qtbot, window, [])
        window.search.query.setText(query)
        wait_note_search_highlights(qtbot, window, ["needle"])
        assert window.editor.extraSelections()[0].cursor.selectionStart() == source_start
        highlighted_block = javascript(
            qtbot,
            window,
            "(() => {"
            "const range = Array.from(CSS.highlights.get('marknotes-note-search'))[0];"
            "const block = range.startContainer.parentElement.closest('p');"
            "return Array.from(document.querySelectorAll('#content p')).indexOf(block);"
            "})()",
        )
        assert highlighted_block == (0 if source_start == 0 else 1), query


def test_note_search_temporarily_overrides_and_restores_library_highlights(qtbot, make_notebook):
    window = make_notebook()
    new_note(qtbot, window, "# global global\nlocal local")
    saved(qtbot, window)
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    window.show_library_search()
    window.sidebar.set_query("global", emit=True)
    qtbot.waitUntil(
        lambda: [s.cursor.selectedText() for s in window.editor.extraSelections()] == ["global"] * 2
    )
    qtbot.waitUntil(
        lambda: (
            javascript(qtbot, window, "CSS.highlights.get('marknotes-library-search')?.size || 0")
            == 2
        )
    )

    window.show_search()
    window.search.query.setText("local")
    wait_note_search_highlights(qtbot, window, ["local"] * 2)
    assert (
        javascript(qtbot, window, "CSS.highlights.get('marknotes-library-search')?.size || 0") == 0
    )
    window.sidebar.set_query("GLOBAL", emit=True)
    wait_note_search_highlights(qtbot, window, ["local"] * 2)
    assert (
        javascript(qtbot, window, "CSS.highlights.get('marknotes-library-search')?.size || 0") == 0
    )

    window.search.query.clear()
    wait_note_search_highlights(qtbot, window, [])
    assert (
        javascript(qtbot, window, "CSS.highlights.get('marknotes-library-search')?.size || 0") == 0
    )
    window.search.close_bar()
    qtbot.waitUntil(
        lambda: [s.cursor.selectedText() for s in window.editor.extraSelections()] == ["global"] * 2
    )
    qtbot.waitUntil(
        lambda: (
            javascript(qtbot, window, "CSS.highlights.get('marknotes-library-search')?.size || 0")
            == 2
        )
    )
    assert javascript(qtbot, window, "CSS.highlights.get('marknotes-note-search')?.size || 0") == 0

    # A hidden SearchBar's delayed editor refresh must not erase restored highlights.
    window.editor.moveCursor(QTextCursor.MoveOperation.End)
    window.editor.insertPlainText(" global")
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    qtbot.waitUntil(lambda: not window.search._timer.isActive())
    assert [s.cursor.selectedText() for s in window.editor.extraSelections()] == ["global"] * 3
    qtbot.waitUntil(
        lambda: (
            javascript(qtbot, window, "CSS.highlights.get('marknotes-library-search')?.size || 0")
            == 3
        )
    )


def test_note_search_tracks_edits_replacements_undo_tabs_and_rerender(qtbot, make_notebook):
    window = make_notebook()
    first = new_note(qtbot, window, "# First\nneedle needle")
    second = new_note(qtbot, window, "# Second\nneedle once")
    activate(qtbot, window, first)
    window.show_search(replace=True)
    window.search.query.setText("needle")
    wait_note_search_highlights(qtbot, window, ["needle"] * 2)

    window.editor.moveCursor(QTextCursor.MoveOperation.End)
    window.editor.insertPlainText(" needle")
    wait_note_search_highlights(qtbot, window, ["needle"] * 3)
    window.search.replacement.setText("changed")
    window.search.replace_all()
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    wait_note_search_highlights(qtbot, window, [])
    assert window.editor.toPlainText() == "# First\nchanged changed changed"
    window.editor.undo()
    wait_note_search_highlights(qtbot, window, ["needle"] * 3)

    activate(qtbot, window, second)
    wait_note_search_highlights(qtbot, window, ["needle"])
    activate(qtbot, window, first)
    wait_note_search_highlights(qtbot, window, ["needle"] * 3)
    with qtbot.waitSignal(window.preview.ready, timeout=15000):
        window._render()
    wait_note_search_highlights(qtbot, window, ["needle"] * 3)


def test_note_search_navigation_preserves_preview_and_highlights(qtbot, make_notebook, monkeypatch):
    window = make_notebook()
    body = "# 😀Search\nneedle first\n\nneedle second\n\nneedle third"
    new_note(qtbot, window, body)
    saved(qtbot, window)
    window.show_search()
    window.search.query.setText("needle")
    window.editor.moveCursor(QTextCursor.MoveOperation.Start)
    qtbot.waitUntil(
        lambda: (
            window._rendered_revision == window._revision
            and not window._render_timer.isActive()
            and not window._highlight_timer.isActive()
            and not window.search._timer.isActive()
        ),
        timeout=15000,
    )
    wait_note_search_highlights(qtbot, window, ["needle"] * 3)
    generation = window._highlight_generation
    assert (
        javascript(
            qtbot,
            window,
            "(() => {"
            "const content = document.getElementById('content');"
            "const highlight = CSS.highlights.get('marknotes-note-search');"
            "window.noteSearchNavigationBaseline = {"
            "highlight, ranges: Array.from(highlight),"
            "texts: Array.from(highlight, range => range.toString()),"
            "html: content.innerHTML, nodes: Array.from(content.childNodes)};"
            "return highlight.size;"
            "})()",
        )
        == 3
    )
    rendered = Mock(wraps=app_module.render_markdown)
    set_document = Mock(wraps=window.preview.set_document)
    monkeypatch.setattr(app_module, "render_markdown", rendered)
    monkeypatch.setattr(window.preview, "set_document", set_document)

    offsets = [
        qt_position(body, body.index(f"needle {name}")) for name in ("first", "second", "third")
    ]
    for navigate, index in (
        (window.search.next, 0),
        (window.search.next, 1),
        (window.search.previous, 0),
        (window.search.previous, 2),
        (window.search.next, 0),
        (window.search.next, 1),
        (window.search.next, 2),
        (window.search.next, 0),
    ):
        navigate()
        cursor = window.editor.textCursor()
        assert cursor.selectionStart() == offsets[index]
        assert cursor.selectedText() == "needle"
        assert window.search.status.text() == f"{index + 1} / 3 件"
        assert window._highlight_generation == generation
        assert javascript(
            qtbot,
            window,
            "(() => {"
            "const before = window.noteSearchNavigationBaseline;"
            "const content = document.getElementById('content');"
            "const highlight = CSS.highlights.get('marknotes-note-search');"
            "const ranges = Array.from(highlight || []);"
            "return highlight === before.highlight && ranges.length === before.ranges.length && "
            "ranges.every((range, i) => range === before.ranges[i] && "
            "range.toString() === before.texts[i]) && content.innerHTML === before.html && "
            "content.childNodes.length === before.nodes.length && "
            "Array.from(content.childNodes).every((node, i) => node === before.nodes[i]);"
            "})()",
        )

    # Let any accidentally queued debounce finish before checking for redraws.
    qtbot.wait(
        max(
            window._render_timer.interval(),
            window._highlight_timer.interval(),
            window.search._timer.interval(),
        )
        + 30
    )
    assert window._highlight_generation == generation
    rendered.assert_not_called()
    set_document.assert_not_called()
    assert window.editor.toPlainText() == body


def test_failed_save_keeps_tab_body_and_reopen_history_until_retry(
    qtbot, make_notebook, monkeypatch
):
    window = make_notebook()
    note_id = new_note(qtbot, window, "消えてはいけない本文")
    original_save = NotebookStore.save

    def fail(*_args, **_kwargs):
        raise OSError("disk full test")

    with monkeypatch.context() as patch:
        patch.setattr(NotebookStore, "save", fail)
        window.close_current()
        qtbot.waitUntil(lambda: not window._busy)
        assert window._order == [note_id]
        assert window._active == note_id
        assert window.editor.toPlainText() == "消えてはいけない本文"
        assert window.store.closed_history_count() == 0
        assert window._sessions[note_id].dirty
        assert "disk full test" in window.statusBar().currentMessage()
    assert NotebookStore.save is original_save
    window.close_current()
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    assert window.store.get(note_id).body == "消えてはいけない本文"
    assert window.store.closed_history_count() == 1


def test_sidebar_overlay_keeps_editor_width_and_fixed_falls_back_when_narrow(qtbot, make_notebook):
    window = make_notebook()
    new_note(qtbot, window, "# サイドバー")
    original_width = window.pages.width()
    window.show_history()
    assert window._sidebar_space.width() == 0
    assert window.pages.width() == original_width
    window._sidebar_fixed_changed(True)
    qtbot.waitUntil(lambda: window._sidebar_space.width() > 0)
    qtbot.waitUntil(lambda: window.pages.width() < original_width)
    window.resize(760, 650)
    qtbot.waitUntil(lambda: window._sidebar_space.width() == 0)
    assert window._sidebar_fixed
    window.resize(1200, 760)
    qtbot.waitUntil(lambda: window._sidebar_space.width() > 0)


def test_source_cursor_selection_remains_independent_between_notes(qtbot, make_notebook):
    window = make_notebook()
    first = new_note(qtbot, window, "# first\n0123456789")
    second = new_note(qtbot, window, "# second\nabcdefghij")
    activate(qtbot, window, first)
    cursor = window.editor.textCursor()
    cursor.setPosition(8)
    cursor.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    activate(qtbot, window, second)
    second_cursor = window.editor.textCursor()
    second_cursor.setPosition(3)
    window.editor.setTextCursor(second_cursor)
    activate(qtbot, window, first)
    assert window.editor.textCursor().selectionStart() == 8
    assert window.editor.textCursor().selectionEnd() == 13
    activate(qtbot, window, second)
    assert window.editor.textCursor().position() == 3


@pytest.mark.parametrize("operation", ["new", "import"])
def test_delayed_creation_finishes_durably_without_activating_during_shutdown(
    qtbot, make_notebook, tmp_path, monkeypatch, operation
):
    window = make_notebook()
    existing = new_note(qtbot, window, "# 保存中のノート")
    saved(qtbot, window)
    started, release = threading.Event(), threading.Event()
    activations = []
    original_activate = window._activate

    def observe_activation(note_id, match_start=None):
        activations.append((note_id, window._busy, window._closing))
        return original_activate(note_id, match_start)

    monkeypatch.setattr(window, "_activate", observe_activation)
    if operation == "new":
        original = window.store.create

        def delayed():
            started.set()
            if not release.wait(10):
                raise TimeoutError("creation gate was not released")
            return original()

        monkeypatch.setattr(window.store, "create", delayed)
        request = window.new_document
    else:
        original = notebook_module.import_markdown

        def delayed(*args, **kwargs):
            started.set()
            if not release.wait(10):
                raise TimeoutError("import gate was not released")
            return original(*args, **kwargs)

        monkeypatch.setattr(notebook_module, "import_markdown", delayed)
        imported_file = tmp_path / "取り込むノート.md"
        imported_file.write_text("# 取り込み中\n保存する本文", encoding="utf-8")
        request = lambda: window.open_path(imported_file)
    try:
        request()
        qtbot.waitUntil(started.is_set)
        window.close()
        assert not window._shutdown_done
    finally:
        release.set()
    qtbot.waitUntil(lambda: window._shutdown_done, timeout=15000)
    assert activations == []
    assert window.store.get(existing).body == "# 保存中のノート"
    notes = window.store.list_notes()
    assert len(notes) == (2 if operation == "import" else 1)
    if operation == "import":
        imported = next(note for note in notes if note.id != existing)
        assert window.store.get(imported.id).body == "# 取り込み中\n保存する本文"


@pytest.mark.parametrize("initial_text", ["", "# 添付先\n"])
def test_repeated_close_all_while_attachment_pending_records_only_one_operation(
    qtbot, make_notebook, tmp_path, monkeypatch, initial_text
):
    window = make_notebook()
    ids = [new_note(qtbot, window, "# 既存\n"), new_note(qtbot, window, initial_text)]
    path = tmp_path / "資料.txt"
    path.write_text("preserved attachment", encoding="utf-8")
    started, release = threading.Event(), threading.Event()
    original = ManagedAssets.import_files

    def delayed(manager, paths):
        started.set()
        if not release.wait(10):
            raise TimeoutError("asset gate was not released")
        return original(manager, paths)

    monkeypatch.setattr(ManagedAssets, "import_files", delayed)
    try:
        window.attach_files([path])
        qtbot.waitUntil(started.is_set)
        window.close_all()
        window.close_all()
        window.close_all()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window._busy and window._order == [], timeout=15000)
    barrier = []
    window.writer.submit(lambda: None, lambda _value, _error: barrier.append(True))
    qtbot.waitUntil(lambda: bool(barrier))
    assert window.store.closed_history_count() == 1
    assert "assets/" in window.store.get(ids[-1]).body
    window.reopen_closed()
    qtbot.waitUntil(lambda: not window._busy and window._active == ids[-1])
    assert window._order == ids
    assert window.store.closed_history_count() == 0


def test_late_note_load_cannot_override_close_others_or_reopen_selection(
    qtbot, make_notebook, monkeypatch
):
    window = make_notebook()
    first, middle, last = [new_note(qtbot, window, f"# {index}") for index in range(3)]
    window.close_notes([middle])
    qtbot.waitUntil(lambda: not window._busy and middle not in window._order)
    activate(qtbot, window, first)
    started, release = threading.Event(), threading.Event()
    original = window.store.get
    blocked_once = False

    def delayed(note_id):
        nonlocal blocked_once
        # Only the explicit load is delayed; title metadata reads may run separately.
        if note_id == middle and not blocked_once:
            blocked_once = True
            started.set()
            if not release.wait(10):
                raise TimeoutError("load gate was not released")
        return original(note_id)

    monkeypatch.setattr(window.store, "get", delayed)
    try:
        window.open_note(middle)
        qtbot.waitUntil(started.is_set)
        window.close_others(first)
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window._busy and window._order == [first], timeout=15000)
    qtbot.waitUntil(lambda: window._active == first)
    assert middle not in window._sessions
    window.reopen_closed()
    qtbot.waitUntil(lambda: not window._busy and window._active == last)
    assert window._order == [first, last]
    window.reopen_closed()
    qtbot.waitUntil(lambda: not window._busy and window._active == middle)
    assert window._order == [first, middle, last]


def test_search_shortcut_focus_and_ime_escape_precedence(qtbot, make_notebook):
    window = make_notebook()
    new_note(qtbot, window, "# 入力を継続")
    window.activateWindow()
    window.editor.setFocus(Qt.FocusReason.OtherFocusReason)
    # Window activation and the platform keyboard-focus window are separate
    # events on Windows; do not send the chord while only activation is ready.
    qtbot.waitUntil(
        lambda: (
            QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
            and QApplication.activeWindow() is window
            and QApplication.focusWindow() is window.windowHandle()
            and QApplication.focusWidget() is window.editor
        ),
        timeout=5000,
    )
    search_action = next(
        action
        for action in window.edit_menu.actions()
        if action.shortcut().toString() == "Ctrl+Shift+F"
    )
    with qtbot.waitSignal(search_action.triggered, timeout=5000):
        qtbot.keyClick(
            window.editor,
            Qt.Key.Key_F,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
    qtbot.waitUntil(lambda: window.sidebar.isVisible())
    assert window.sidebar.mode == "search"
    assert window.focusWidget() is window.sidebar.search_edit
    QApplication.sendEvent(window.sidebar.search_edit, QInputMethodEvent("へんかん", []))
    qtbot.keyClick(window.sidebar.search_edit, Qt.Key.Key_Escape)
    assert window.sidebar.isVisible()
    assert window.sidebar.search_edit.text() == ""
    QApplication.sendEvent(window.sidebar.search_edit, QInputMethodEvent("", []))
    qtbot.keyClick(window.sidebar.search_edit, Qt.Key.Key_Escape)
    assert not window.sidebar.isVisible()
    assert window.focusWidget() is window.editor


def test_more_match_button_loads_later_snippets_for_the_same_note(qtbot, make_notebook):
    window = make_notebook()
    body = "# 検索の抜粋\n\n" + "\n\n".join(
        f"段落 {index} の確認語 " + "離れた文章です。" * 40 for index in range(7)
    )
    note_id = new_note(qtbot, window, body)
    saved(qtbot, window)
    before = window.store.get(note_id)
    window.show_library_search()
    window.sidebar.set_query("確認語", emit=True)
    qtbot.waitUntil(
        lambda: (
            len(window.sidebar._cards) == 1
            and len(window.sidebar._cards[0][1].result.get("snippets", [])) == 3
        ),
        timeout=10000,
    )
    item, card = window.sidebar._cards[0]
    assert card.result["total_matches"] == 7
    assert not card.more_button.isHidden()
    qtbot.mouseClick(card.more_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: len(window.sidebar._cards[0][1].result.get("snippets", [])) == 7,
        timeout=10000,
    )
    updated_item, updated_card = window.sidebar._cards[0]
    assert updated_item is item
    assert updated_card.note_id == note_id
    assert updated_card.more_button.isHidden()
    assert updated_card.label.text().count("background-color:") == 7
    assert window.store.get(note_id).revision == before.revision
    assert window.store.get(note_id).updated_at == before.updated_at


def test_match_navigation_can_reveal_hidden_markdown_hit_then_clear_search(qtbot, make_notebook):
    window = make_notebook()
    body = "# 検索移動\n😀 記録 その1\n\n- 記録 その2\n\n[添付](assets/記録.txt)"
    note_id = new_note(qtbot, window, body)
    window.set_display_mode("preview")
    saved(qtbot, window)
    before = window.store.get(note_id)
    undo_steps = window.editor.document().availableUndoSteps()
    window.show_library_search()
    window.sidebar.set_query("記録", emit=True)
    qtbot.waitUntil(lambda: "3 件一致" in window.library_match_label.text())
    controls = {
        button.text(): button for button in window.library_match_bar.findChildren(QPushButton)
    }
    qtbot.mouseClick(controls["次の一致"], Qt.MouseButton.LeftButton)
    assert window._current_match == 1
    assert window.display_mode == "preview"
    qtbot.mouseClick(controls["前の一致"], Qt.MouseButton.LeftButton)
    assert window._current_match == 0
    qtbot.mouseClick(controls["前の一致"], Qt.MouseButton.LeftButton)
    assert window._current_match == 2
    qtbot.mouseClick(controls["一致をソースで表示"], Qt.MouseButton.LeftButton)
    target = qt_position(body, find_match_ranges(body, "記録")[2][0])
    qtbot.waitUntil(
        lambda: window.display_mode == "source" and window.editor.textCursor().position() == target
    )
    assert window.editor.toPlainText() == body
    assert window.editor.document().availableUndoSteps() == undo_steps
    qtbot.mouseClick(controls["検索解除"], Qt.MouseButton.LeftButton)
    assert window.sidebar.search_edit.text() == ""
    assert window._library_query == ""
    assert window.library_match_bar.isHidden()
    assert window.editor.extraSelections() == []
    after = window.store.get(note_id)
    assert after.body == before.body
    assert after.updated_at == before.updated_at
    assert after.revision == before.revision


def test_attachment_rows_and_external_image_change_target_the_owning_note(
    qtbot, make_notebook, tmp_path
):
    window = make_notebook()
    first = new_note(qtbot, window, "# 添付のあるノート\n")
    second = new_note(qtbot, window, "# 別のノート\n")
    activate(qtbot, window, first)
    image_path = tmp_path / "元の画像.png"
    image = QImage(8, 8, QImage.Format.Format_ARGB32)
    image.fill(QColor("#2468ac"))
    assert image.save(str(image_path))
    pdf_path = tmp_path / "議事録.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nmetadata check")
    window.attach_files([image_path, pdf_path])
    qtbot.waitUntil(lambda: window._sessions[first].pending_assets == 0, timeout=10000)
    saved(qtbot, window)
    with closing(sqlite3.connect(window.store.db_path)) as connection:
        rows = connection.execute(
            "SELECT note_id, relative_path, original_name, kind, size FROM attachments ORDER BY kind"
        ).fetchall()
    assert len(rows) == 2
    assert {row[0] for row in rows} == {first}
    assert {row[2] for row in rows} == {image_path.name, pdf_path.name}
    assert {row[3] for row in rows} == {"file", "image"}
    for _note_id, relative_path, _name, _kind, size in rows:
        assert (window.store.note_dir(first) / relative_path).stat().st_size == size
    image_row = next(row for row in rows if row[3] == "image")
    managed_image = window.store.note_dir(first) / image_row[1]
    qtbot.waitUntil(lambda: managed_image in window._asset_signatures)
    activate(qtbot, window, second)
    saved(qtbot, window)
    first_before, second_before = window.store.get(first), window.store.get(second)
    replacement = QImage(24, 16, QImage.Format.Format_ARGB32)
    replacement.fill(QColor("#bb4411"))
    assert replacement.save(str(managed_image))
    # Real QFileSystemWatcher delivery must attribute this edit to the inactive
    # owner, without changing its body/Undo revision or the displayed note.
    qtbot.waitUntil(
        lambda: window.store.get(first).updated_at != first_before.updated_at,
        timeout=10000,
    )
    after = window.store.get(first)
    assert after.body == first_before.body
    assert after.revision == first_before.revision
    assert window.store.get(second).updated_at == second_before.updated_at
    assert window._active == second
    assert window.editor.toPlainText() == second_before.body


def test_theme_switch_keeps_library_matches_without_a_visible_preview(qtbot, make_notebook):
    window = make_notebook()
    note_id = new_note(qtbot, window, "# メモ\n大切なメモを残す")
    window.set_display_mode("source")
    window.sidebar.set_query("メモ", emit=True)
    assert len(window.editor.extraSelections()) == 2
    for mode in ("dark", "dark", "light", "dark", "light"):
        window.apply_theme(mode, persist=False)
        assert len(window.editor.extraSelections()) == 2
        assert window.display_mode == "source"
        assert window._active == note_id
        assert (window.palette().window().color().lightness() < 128) == (mode == "dark")
        # The actual SVG glyph must remain readable after repeated QSS repolish.
        pixels = window.sidebar_button.icon().pixmap(18, 18).toImage()
        lightness = [
            pixels.pixelColor(x, y).lightness()
            for y in range(pixels.height())
            for x in range(pixels.width())
            if pixels.pixelColor(x, y).alpha() > 128
        ]
        assert lightness
        mean = sum(lightness) / len(lightness)
        assert mean > 160 if mode == "dark" else mean < 100
    assert window.editor.toPlainText() == "# メモ\n大切なメモを残す"


def test_untouched_tab_disappears_without_history_or_delayed_view_errors(
    qtbot, make_notebook, monkeypatch
):
    window = make_notebook()
    note_id = new_note(qtbot, window)
    window.set_display_mode("preview")
    started, release = threading.Event(), threading.Event()
    original = window.store.close_tabs
    errors = []
    monkeypatch.setattr(window, "_storage_error", errors.append)

    def delayed(*args, **kwargs):
        started.set()
        if not release.wait(10):
            raise TimeoutError("close gate was not released")
        return original(*args, **kwargs)

    monkeypatch.setattr(window.store, "close_tabs", delayed)
    try:
        window.close_current()
        qtbot.waitUntil(started.is_set)
        # A previously scheduled view callback must not enqueue a write after deletion.
        window._persist_view()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    barrier = []
    window.writer.submit(lambda: None, lambda _value, _error: barrier.append(True))
    qtbot.waitUntil(lambda: bool(barrier))
    assert errors == []
    assert window.store.list_notes() == []
    assert window.store.get_session().order == []
    assert window.store.closed_history_count() == 0
    assert note_id not in window._sessions
    assert note_id not in window._summaries
    assert note_id not in window._visited
    with pytest.raises(KeyError):
        window.store.get(note_id)
    qtbot.waitUntil(lambda: not window.reopen_action.isEnabled())


def test_bulk_close_skips_untouched_tabs_but_keeps_pinned_and_edited_empty_notes(
    qtbot, make_notebook
):
    window = make_notebook()
    first = new_note(qtbot, window, "# 残す本文")
    new_note(qtbot, window)
    pinned = new_note(qtbot, window)
    window.toggle_pin()
    qtbot.waitUntil(lambda: window.store.get(pinned).pinned)
    cleared = new_note(qtbot, window, "一度書いた本文")
    window.editor.selectAll()
    window.editor.insertPlainText("")
    assert window.editor.toPlainText() == ""
    new_note(qtbot, window)
    window.close_all()
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    kept = [first, pinned, cleared]
    assert {note.id for note in window.store.list_notes()} == set(kept)
    assert window.store.get(cleared).body == ""
    assert window.store.get(cleared).revision > 0
    assert window.store.closed_history_count() == 1
    window.reopen_closed()
    qtbot.waitUntil(
        lambda: not window._busy and len(window._order) == len(kept) and window._active in kept
    )
    assert window._order == kept
    assert window._active in kept


def test_exit_discards_untouched_tabs_and_restores_only_retained_notes(qtbot, make_notebook):
    window = make_notebook()
    new_note(qtbot, window)
    retained = new_note(qtbot, window, "終了時に保存する本文")
    new_note(qtbot, window)
    close_window(qtbot, window)
    assert [note.id for note in window.store.list_notes()] == [retained]
    assert window.store.closed_history_count() == 0
    restored = make_notebook(window.store.root)
    qtbot.waitUntil(lambda: restored._active == retained)
    assert restored._order == [retained]
    assert restored.editor.toPlainText() == "終了時に保存する本文"


def test_deletion_removes_body_index_assets_and_close_history(qtbot, make_notebook, tmp_path):
    window = make_notebook()
    retained = new_note(qtbot, window, "# 残すノート")
    target = new_note(qtbot, window, "# 削除するノート\ndelete-needle")
    source = tmp_path / "資料.txt"
    source.write_text("original attachment", encoding="utf-8")
    window.attach_files([source])
    qtbot.waitUntil(lambda: window._sessions[target].pending_assets == 0)
    saved(qtbot, window)
    window.toggle_pin()
    qtbot.waitUntil(lambda: window.store.get(target).pinned)
    note_directory = window.store.note_dir(target)
    assert note_directory.is_dir()
    window.close_current()
    qtbot.waitUntil(lambda: not window._busy and target not in window._order)
    activate(qtbot, window, target)
    assert window.store.closed_history_count() == 1
    window._delete_notes([target])
    qtbot.waitUntil(lambda: not window._busy and window._active == retained)
    assert window.store.search("delete-needle") == []
    assert window.store.closed_history_count() == 0
    assert [note.id for note in window.store.list_notes()] == [retained]
    assert not note_directory.exists()
    assert source.read_text(encoding="utf-8") == "original attachment"
    assert target not in window._sessions
    assert target not in window._summaries
    assert all(signature[0] != target for signature in window._asset_signatures.values())
    assert window.store.pending_cleanup() == ()


@pytest.mark.parametrize("mode", ["history", "pinned", "search"])
def test_deletion_from_sidebar_targets_closed_note_without_switching_active(
    qtbot, make_notebook, monkeypatch, mode
):
    window = make_notebook()
    target = new_note(qtbot, window, "# 削除対象\nneedle")
    saved(qtbot, window)
    window.toggle_pin()
    qtbot.waitUntil(lambda: window.store.get(target).pinned)
    window.close_current()
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    retained = new_note(qtbot, window, "# 編集を続ける\nneedle")
    document = window.editor.document()
    undo_steps = document.availableUndoSteps()
    confirmations = []
    monkeypatch.setattr(
        window, "_confirm_note_deletion", lambda title: confirmations.append(title) or True
    )
    window.sidebar.set_mode(mode)
    if mode == "search":
        window.sidebar.set_query("needle", emit=True)
    window.refresh_library()
    qtbot.waitUntil(lambda: target in [card.note_id for _, card in window.sidebar._cards])
    window.sidebar.delete_requested.emit(target)
    qtbot.waitUntil(lambda: not window._busy and not window._delete_prompt_pending)
    qtbot.waitUntil(lambda: target not in [note.id for note in window.store.list_notes()])
    assert confirmations == ["削除対象"]
    assert window._active == retained
    assert window.editor.document() is document
    assert document.availableUndoSteps() == undo_steps
    assert window._order == [retained]
    assert window.store.closed_history_count() == 0
    assert target not in [card.note_id for _, card in window.sidebar._cards]


def test_deletion_cancel_keeps_note_and_confirmation_defaults_to_cancel(
    qtbot, make_notebook, monkeypatch
):
    window = make_notebook()
    target = new_note(qtbot, window, "# <b>削除しない</b>")
    seen = []

    def inspect_dialog(dialog):
        seen.append(dialog.text())
        assert dialog.textFormat() == Qt.TextFormat.PlainText
        assert dialog.defaultButton().text() == "キャンセル"
        assert dialog.escapeButton() is dialog.defaultButton()
        assert "取り消せません" in dialog.informativeText()
        return 0  # Inspect the dialog without displaying or executing its event loop.

    monkeypatch.setattr(QMessageBox, "exec", inspect_dialog)
    window.delete_note_action.trigger()
    qtbot.waitUntil(lambda: bool(seen) and not window._delete_prompt_pending)
    assert window._order == [target]
    assert window._active == target
    assert window.editor.toPlainText() == "# <b>削除しない</b>"
    assert window.store.closed_history_count() == 0


def test_deletion_skips_saving_discarded_edits_and_failure_keeps_them_for_retry(
    qtbot, make_notebook, monkeypatch
):
    window = make_notebook()
    target = new_note(qtbot, window, "消す予定の未保存本文")
    window._autosave.stop()
    original_delete = window.store.delete_notes

    def fail_delete(*_args, **_kwargs):
        raise OSError("delete denied")

    def unexpected_save(*_args, **_kwargs):
        raise AssertionError("Deleted edits should not require another body save")

    monkeypatch.setattr(window.store, "save", unexpected_save)
    monkeypatch.setattr(window.store, "delete_notes", fail_delete)
    window._delete_notes([target])
    qtbot.waitUntil(lambda: not window._busy)
    assert window._active == target
    assert window.editor.toPlainText() == "消す予定の未保存本文"
    assert window._sessions[target].dirty
    assert window.store.get(target).body == ""
    assert "delete denied" in window.statusBar().currentMessage()
    monkeypatch.setattr(window.store, "delete_notes", original_delete)
    window._delete_notes([target])
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    assert window.store.list_notes() == []
    assert not window.delete_note_action.isEnabled()
    assert window._active is None
    assert window.editor.toPlainText() == ""


def test_deletion_waits_for_attachment_copy_and_ignores_repeated_requests(
    qtbot, make_notebook, tmp_path, monkeypatch
):
    window = make_notebook()
    target = new_note(qtbot, window, "# 添付コピー中")
    source = tmp_path / "attachment.txt"
    source.write_text("copied before delete", encoding="utf-8")
    started, release = threading.Event(), threading.Event()
    original = ManagedAssets.import_files
    errors = []
    monkeypatch.setattr(window, "_storage_error", errors.append)

    def delayed(manager, paths):
        started.set()
        if not release.wait(10):
            raise TimeoutError("copy gate was not released")
        return original(manager, paths)

    monkeypatch.setattr(ManagedAssets, "import_files", delayed)
    try:
        window.attach_files([source])
        qtbot.waitUntil(started.is_set)
        window._delete_notes([target])
        window._delete_notes([target])
        window._images_changed()
        assert window._busy
        assert target in window._order
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window._busy and not window._order, timeout=15000)
    assert errors == []
    assert window.store.list_notes() == []
    assert not window.store.note_dir(target).exists()
    assert source.is_file()
    assert window.store.closed_history_count() == 0


def test_deletion_discards_search_results_that_complete_after_removal(
    qtbot, make_notebook, monkeypatch
):
    window = make_notebook()
    target = new_note(qtbot, window, "# needle delete")
    saved(qtbot, window)
    started, release = threading.Event(), threading.Event()
    original = window.store.search
    blocked = False

    def delayed(*args, **kwargs):
        nonlocal blocked
        results = original(*args, **kwargs)
        if not blocked and args[0] == "needle":
            blocked = True
            started.set()
            if not release.wait(10):
                raise TimeoutError("search gate was not released")
        return results

    monkeypatch.setattr(window.store, "search", delayed)
    try:
        window.sidebar.set_mode("search")
        window.sidebar.set_query("needle", emit=True)
        qtbot.waitUntil(started.is_set)
        window._delete_notes([target])
        qtbot.waitUntil(lambda: not window._busy and not window._order)
        assert window.sidebar._cards == []
    finally:
        release.set()
    barrier = []
    window.reader.submit(lambda: None, lambda _result, _error: barrier.append(True))
    qtbot.waitUntil(lambda: bool(barrier))
    assert window.sidebar._cards == []
    assert window._sessions == {}


def test_deletion_pending_files_show_retry_and_retry_again_after_restart(
    qtbot, make_notebook, tmp_path, monkeypatch
):
    window = make_notebook()
    target = new_note(qtbot, window, "# 添付の削除待ち")
    directory = window.store.assets_dir(target)
    (directory / "locked.txt").write_text("locked fixture", encoding="utf-8")
    original_remove = NotebookStore._remove_note_directory

    def locked(_store, note_id):
        if note_id == target:
            raise PermissionError("attachment is in use")
        return original_remove(_store, note_id)

    monkeypatch.setattr(NotebookStore, "_remove_note_directory", locked)
    window._delete_notes([target])
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    assert window.store.list_notes() == []
    assert directory.exists()
    assert not window.cleanup_button.isHidden()
    assert window.retry_cleanup_action.isEnabled()
    close_window(qtbot, window)
    restored = make_notebook(window.store.root)
    qtbot.waitUntil(lambda: not restored._cleanup_running)
    assert not restored.cleanup_button.isHidden()
    assert restored.retry_cleanup_action.isEnabled()
    monkeypatch.setattr(NotebookStore, "_remove_note_directory", original_remove)
    restored.retry_cleanup_action.trigger()
    qtbot.waitUntil(lambda: not restored._cleanup_running)
    assert not directory.exists()
    assert restored.store.pending_cleanup() == ()
    assert restored.cleanup_button.isHidden()
    assert not restored.retry_cleanup_action.isEnabled()


@pytest.mark.parametrize("target_state", ["active", "background", "closed", "all_closed"])
def test_history_pin_persists_without_opening_or_editing_note(qtbot, make_notebook, target_state):
    window = make_notebook()
    target = new_note(qtbot, window, "# 履歴からピン止め")
    if target_state in {"background", "closed"}:
        new_note(qtbot, window, "# 作業中のノート")
    saved(qtbot, window)
    if target_state in {"closed", "all_closed"}:
        window.close_notes([target])
        qtbot.waitUntil(lambda: not window._busy and target not in window._order)
    original = window.store.get(target)
    active, order = window._active, list(window._order)
    history_count = window.store.closed_history_count()
    window.show_history()

    def target_card():
        return next((card for _, card in window.sidebar._cards if card.note_id == target), None)

    qtbot.waitUntil(lambda: target_card() is not None)
    qtbot.mouseClick(
        next(card for _, card in window.sidebar._cards if card.note_id == target).pin_button,
        Qt.MouseButton.LeftButton,
    )
    qtbot.waitUntil(lambda: target_card() is not None and bool(target_card().result["pinned"]))
    assert window.store.get(target).pinned
    assert next(
        card for _, card in window.sidebar._cards if card.note_id == target
    ).pin_button.isChecked()
    assert window._active == active
    assert window._order == order
    assert window.pin_action.isChecked() == (active == target)
    if target in order:
        assert window.tabs.tab_bar.tabText(order.index(target)) == original.title
    window.sidebar.set_mode("pinned")
    qtbot.waitUntil(lambda: [card.note_id for _, card in window.sidebar._cards] == [target])
    qtbot.mouseClick(
        next(card for _, card in window.sidebar._cards if card.note_id == target).pin_button,
        Qt.MouseButton.LeftButton,
    )
    qtbot.waitUntil(lambda: not window.sidebar._cards)
    result = window.store.get(target)
    assert not result.pinned
    assert (result.body, result.updated_at, result.created_at, result.revision) == (
        original.body,
        original.updated_at,
        original.created_at,
        original.revision,
    )
    assert window._active == active
    assert window._order == order
    assert not window.pin_action.isChecked()
    assert window.store.closed_history_count() == history_count


def test_history_pin_save_failure_keeps_state_and_allows_retry(qtbot, make_notebook, monkeypatch):
    window = make_notebook()
    target = new_note(qtbot, window, "# 保存失敗")
    saved(qtbot, window)
    window.show_history()
    qtbot.waitUntil(lambda: bool(window.sidebar._cards))
    original = window.store.set_pinned

    def fail(*_args):
        raise OSError("pin write failed")

    monkeypatch.setattr(window.store, "set_pinned", fail)
    qtbot.mouseClick(
        next(card for _, card in window.sidebar._cards if card.note_id == target).pin_button,
        Qt.MouseButton.LeftButton,
    )
    qtbot.waitUntil(lambda: "pin write failed" in window.statusBar().currentMessage())
    assert not window.store.get(target).pinned
    assert not window._summaries[target]["pinned"]
    assert not window.sidebar._cards[0][1].result["pinned"]
    assert not window.sidebar._cards[0][1].pin_button.isChecked()
    monkeypatch.setattr(window.store, "set_pinned", original)
    qtbot.mouseClick(
        next(card for _, card in window.sidebar._cards if card.note_id == target).pin_button,
        Qt.MouseButton.LeftButton,
    )
    qtbot.waitUntil(lambda: bool(window.sidebar._cards[0][1].result["pinned"]))
    assert window.store.get(target).pinned
    assert next(
        card for _, card in window.sidebar._cards if card.note_id == target
    ).pin_button.isChecked()


@pytest.mark.parametrize("kind", ["file", "folder"])
def test_insert_local_link_replaces_selection_saves_and_opens_without_copying(
    qtbot, make_notebook, tmp_path, monkeypatch, kind
):
    from marknotes.local_links import LocalLinkDialog, local_path_link

    path = tmp_path / "資料 [最終] #100%.txt"
    if kind == "file":
        path.write_text("original", encoding="utf-8")
    else:
        path.mkdir()
    window = make_notebook()
    note_id = new_note(qtbot, window, "表示名")
    window.editor.selectAll()

    def accept_dialog(dialog):
        assert dialog.label_edit.text() == "表示名"
        dialog.path_edit.setText(str(path))
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(LocalLinkDialog, "exec", accept_dialog)
    opened = []
    monkeypatch.setattr(
        "marknotes.preview.QDesktopServices.openUrl", lambda url: opened.append(url)
    )
    next(
        action
        for action in window.insert_menu.actions()
        if action.text() == "ローカルパスへのリンク…"
    ).trigger()
    expected = local_path_link(str(path), "表示名")
    assert window.editor.toPlainText() == expected
    saved(qtbot, window)
    assert window.store.get(note_id).body == expected
    assert not list((window.base_dir / "assets").glob("*"))
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision)
    qtbot.waitUntil(lambda: bool(javascript(qtbot, window, "document.querySelector('a')?.href")))
    href = javascript(qtbot, window, "document.querySelector('a').href")
    assert Path(QUrl(href).toLocalFile()) == path
    window.preview.page().open_context_link(QUrl(href))
    assert [Path(url.toLocalFile()) for url in opened] == [path]
    window.editor.undo()
    assert window.editor.toPlainText() == "表示名"
    assert path.exists()


def test_insert_timestamp_and_link_paste_require_an_active_note(qtbot, make_notebook):
    window = make_notebook()
    QApplication.clipboard().setText("custom+demo.v1://item/123")

    def assert_commands_unavailable():
        window.refresh_edit_actions()
        window.refresh_clipboard_actions()
        assert window.editor.isReadOnly()
        assert not window.insert_timestamp_action.isEnabled()
        assert not window.link_paste_action.isEnabled()
        window.insert_timestamp()
        window.paste_link()
        assert window.editor.toPlainText() == ""
        assert window._active is None

    assert_commands_unavailable()
    note_id = new_note(qtbot, window, "保存する本文")
    window.refresh_edit_actions()
    window.refresh_clipboard_actions()
    assert window.insert_timestamp_action.isEnabled()
    assert window.link_paste_action.isEnabled()
    window.close_current()
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    assert_commands_unavailable()
    assert window.store.get(note_id).body == "保存する本文"


def test_timestamp_and_custom_link_autosave_keep_undo_and_preview_across_tabs(
    qtbot, make_notebook, monkeypatch
):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    fixed_time = datetime(2026, 9, 16, 14, 5, 6, tzinfo=UTC)
    timestamp = fixed_time.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr("marknotes.interactions.datetime", SimpleNamespace(now=lambda: fixed_time))
    window = make_notebook()
    original = "# 操作を記録\n参照先\n"
    first = new_note(qtbot, window, original)
    document = window.editor.document()
    window.editor.moveCursor(QTextCursor.MoveOperation.End)
    window.refresh_edit_actions()
    window.insert_timestamp_action.trigger()
    timestamp_body = original + timestamp
    assert window.editor.toPlainText() == timestamp_body
    qtbot.waitUntil(lambda: not window._sessions[first].dirty, timeout=5000)
    assert window.store.get(first).body == timestamp_body
    window.editor.undo()
    assert window.editor.toPlainText() == original
    window.editor.redo()
    assert window.editor.toPlainText() == timestamp_body

    url = "custom+demo.v1://item/123?mode=open#part"
    QApplication.clipboard().setText(url)
    cursor = window.editor.textCursor()
    start = timestamp_body.index("参照先")
    cursor.setPosition(qt_position(timestamp_body, start))
    cursor.setPosition(
        qt_position(timestamp_body, start + len("参照先")), QTextCursor.MoveMode.KeepAnchor
    )
    window.editor.setTextCursor(cursor)
    window.refresh_clipboard_actions()
    assert window.link_paste_action.isEnabled()
    window.link_paste_action.trigger()
    linked_body = timestamp_body.replace("参照先", f"[参照先]({url})")
    assert window.editor.toPlainText() == linked_body
    qtbot.waitUntil(lambda: not window._sessions[first].dirty, timeout=5000)
    assert window.store.get(first).body == linked_body

    second_body = "# 別のノート\nこの本文は変えない"
    second = new_note(qtbot, window, second_body)
    activate(qtbot, window, first)
    assert window.editor.document() is document
    assert window.editor.toPlainText() == linked_body
    window.editor.undo()
    assert window.editor.toPlainText() == timestamp_body
    window.editor.redo()
    assert window.editor.toPlainText() == linked_body
    saved(qtbot, window)
    assert window.store.get(first).body == linked_body
    assert window.store.get(second).body == second_body
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    qtbot.waitUntil(
        lambda: javascript(qtbot, window, "document.querySelector('#content a')?.href") == url,
        timeout=10000,
    )
    assert javascript(qtbot, window, "document.querySelector('#content a').textContent") == "参照先"
    preview_text = javascript(qtbot, window, "document.getElementById('content').textContent")
    assert timestamp in preview_text
    activate(qtbot, window, second)
    assert window.editor.toPlainText() == second_body


def test_preview_task_click_autosaves_and_keeps_selection_and_undo_across_tabs(
    qtbot, make_notebook
):
    import json

    from PySide6.QtCore import QPoint

    window = make_notebook()
    original = "# 😀作業の記録\n- [ ] 確認する\n- [x] 完了した項目"
    first = new_note(qtbot, window, original)
    saved(qtbot, window)
    document = window.editor.document()
    cursor = window.editor.textCursor()
    cursor.setPosition(qt_position(original, 2))
    cursor.setPosition(qt_position(original, 5), QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    selection = (cursor.anchor(), cursor.position(), cursor.selectedText())
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    qtbot.waitUntil(
        lambda: (
            javascript(qtbot, window, "window.previewApi?.metrics().revision") == window._revision
        ),
        timeout=10000,
    )
    window.preview.scroll_to_source(0, window._revision)
    qtbot.waitUntil(
        lambda: javascript(qtbot, window, "window.previewApi.metrics().scrollY") == 0,
        timeout=5000,
    )
    point = json.loads(
        javascript(
            qtbot,
            window,
            "JSON.stringify((() => {const checkbox=document.querySelector("
            + "'#content input[type=checkbox][data-task-line]');"
            + "const r=checkbox.getBoundingClientRect();"
            + "return [Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)];})())",
        )
    )
    target = window.preview.view.focusProxy()
    assert target is not None
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=QPoint(*point))
    checked = original.replace("- [ ]", "- [x]", 1)
    qtbot.waitUntil(lambda: window.editor.toPlainText() == checked, timeout=5000)
    cursor = window.editor.textCursor()
    assert (cursor.anchor(), cursor.position(), cursor.selectedText()) == selection
    qtbot.waitUntil(lambda: not window._sessions[first].dirty, timeout=5000)
    assert window.store.get(first).body == checked
    window.editor.undo()
    assert window.editor.toPlainText() == original
    window.editor.redo()
    assert window.editor.toPlainText() == checked

    second_body = "# 別のノート\n- [ ] こちらは変更しない"
    second = new_note(qtbot, window, second_body)
    activate(qtbot, window, first)
    assert window.editor.document() is document
    window.editor.undo()
    assert window.editor.toPlainText() == original
    window.editor.redo()
    assert window.editor.toPlainText() == checked
    saved(qtbot, window)
    assert window.store.get(first).body == checked
    assert window.store.get(second).body == second_body


def test_preview_task_rejects_previous_tab_and_closed_note_revisions(qtbot, make_notebook):
    window = make_notebook()
    original = "- [ ] 同じタスク"
    first = new_note(qtbot, window, original)
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    first_revision = window._rendered_revision
    second = new_note(qtbot, window, original)
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    second_revision = window._rendered_revision
    assert second_revision != first_revision
    assert not window._toggle_preview_task(0, 3, True, first_revision)
    assert window.editor.toPlainText() == original
    assert window._sessions[first].document.toPlainText() == original

    assert window._toggle_preview_task(0, 3, True, second_revision)
    checked = "- [x] 同じタスク"
    assert window.editor.toPlainText() == checked
    saved(qtbot, window)
    assert window.store.get(first).body == original
    assert window.store.get(second).body == checked
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    closed_revision = window._rendered_revision
    window.close_current()
    qtbot.waitUntil(lambda: not window._busy and window._active == first)
    assert not window._toggle_preview_task(0, 3, False, closed_revision)
    assert window.editor.toPlainText() == original
    assert window.store.get(second).body == checked

    window.close_current()
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    assert window._active is None
    assert window.editor.isReadOnly()
    assert not window._toggle_preview_task(0, 3, True, first_revision)
    assert not window._toggle_preview_task(0, 3, True, window._revision)
    assert window.editor.toPlainText() == ""
    assert window.store.get(first).body == original
    assert window.store.get(second).body == checked
