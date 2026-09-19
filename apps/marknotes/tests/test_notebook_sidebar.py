"""Current-note sidebar panels stay bound to the visible notebook document."""

import threading

import pytest
import test_notebook_integration as notebook_integration
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QFrame, QWidget
from test_notebook_integration import activate, close_window, javascript, new_note, saved

from marknotes.search import qt_position

make_notebook = notebook_integration.make_notebook


def heading_titles(window):
    return [heading.title for heading in window.outline_panel.headings]


def wait_render(qtbot, window):
    qtbot.waitUntil(
        lambda: (
            window._rendered_revision == window._revision
            and not window._render_timer.isActive()
            and not window._changing_display_mode
        ),
        timeout=15000,
    )


def preview_source(qtbot, window):
    return javascript(qtbot, window, "window.previewApi?.metrics().source")


def test_outline_follows_edits_undo_tabs_and_empty_window(qtbot, make_notebook):
    window = make_notebook()
    window.right_sidebar.set_mode("outline")
    assert window.outline_panel.note_id == ""
    assert heading_titles(window) == []

    first = new_note(qtbot, window, "# 😀最初のノート\n本文")
    qtbot.waitUntil(lambda: heading_titles(window) == ["😀最初のノート"])
    window.editor.insertPlainText("\n## 追加した見出し")
    qtbot.waitUntil(lambda: heading_titles(window) == ["😀最初のノート", "追加した見出し"])
    window.editor.undo()
    qtbot.waitUntil(lambda: heading_titles(window) == ["😀最初のノート"])
    window.editor.redo()
    qtbot.waitUntil(lambda: heading_titles(window) == ["😀最初のノート", "追加した見出し"])

    second = new_note(qtbot, window, "見出しのないノート")
    qtbot.waitUntil(lambda: heading_titles(window) == [])
    assert window.outline_panel.note_id == second
    activate(qtbot, window, first)
    assert window.outline_panel.note_id == first
    assert heading_titles(window) == ["😀最初のノート", "追加した見出し"]

    window.close_all()
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    assert window.outline_panel.note_id == ""
    assert heading_titles(window) == []
    assert window.outline_panel.tree.topLevelItemCount() == 0


def test_outline_ignores_heading_clicks_from_previous_text_or_note(qtbot, make_notebook):
    window = make_notebook()
    first = new_note(qtbot, window, "# 最初\n\n## 移動先")
    window.right_sidebar.set_mode("outline")
    old_line = window.outline_panel.headings[-1].line
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    window.editor.setTextCursor(cursor)
    window.editor.insertPlainText("追加した段落\n\n")
    position = window.editor.textCursor().position()
    window.outline_panel.heading_requested.emit(first, old_line)
    assert window.editor.textCursor().position() == position
    assert window.outline_panel.headings[-1].line == old_line + 2

    second = new_note(qtbot, window, "# 次のノート\n\n## 別の移動先")
    position = window.editor.textCursor().position()
    window.outline_panel.heading_requested.emit(first, old_line)
    assert window._active == second
    assert window.editor.textCursor().position() == position


@pytest.mark.parametrize("mode", ["split", "source", "preview"])
def test_outline_jump_moves_visible_panes_with_sync_disabled_without_editing(
    qtbot, make_notebook, mode
):
    window = make_notebook()
    prefix = "# 😀ノート\n\n" + "日本語と🌕の段落。\n\n" * 30
    heading = "## 🌕目的の見出し"
    body = prefix + heading + "\n\n" + "続く段落。\n\n" * 30
    note_id = new_note(qtbot, window, body)
    window.right_sidebar.set_mode("outline")
    window.sync_action.setChecked(False)
    saved(qtbot, window)
    wait_render(qtbot, window)
    window.set_display_mode(mode)
    qtbot.waitUntil(lambda: not window._changing_display_mode, timeout=15000)
    before = window.store.get(note_id)
    undo_steps = window.editor.document().availableUndoSteps()
    item = window.outline_panel.tree.topLevelItem(0).child(0)
    line = prefix.count("\n")

    window.outline_panel.tree.itemClicked.emit(item, 0)

    assert window.editor.textCursor().position() == qt_position(body, len(prefix))
    assert window.editor.textCursor().block().text() == heading
    if mode != "preview":
        assert window.editor.source_position() == pytest.approx(line, abs=0.05)
    if mode != "source":
        qtbot.waitUntil(
            lambda: preview_source(qtbot, window) == pytest.approx(line, abs=0.05),
            timeout=10000,
        )
    assert window.display_mode == mode
    assert not window.sync_action.isChecked()
    assert window.editor.toPlainText() == body
    assert window.editor.document().availableUndoSteps() == undo_steps
    saved(qtbot, window)
    after = window.store.get(note_id)
    assert after.body == before.body
    assert after.revision == before.revision
    assert after.updated_at == before.updated_at


def test_outline_jump_waits_for_pending_preview_render(qtbot, make_notebook):
    window = make_notebook()
    prefix = "# 開始\n\n" + "段落。\n\n" * 25
    note_id = new_note(qtbot, window, prefix + "## 目的地\n\n" + "続き。\n\n" * 25)
    window.right_sidebar.set_mode("outline")
    window.sync_action.setChecked(False)
    wait_render(qtbot, window)
    window.editor.insertPlainText("再描画を待っている変更")
    window._refresh_outline()
    assert window._rendered_revision != window._revision
    window.outline_panel.heading_requested.emit(note_id, prefix.count("\n"))
    wait_render(qtbot, window)
    qtbot.waitUntil(
        lambda: preview_source(qtbot, window) == pytest.approx(prefix.count("\n"), abs=0.05),
        timeout=10000,
    )


@pytest.mark.parametrize("mode", ["assets", "outline"])
def test_right_sidebar_modes_leave_left_library_queries_independent(
    qtbot, make_notebook, monkeypatch, mode
):
    window = make_notebook()
    new_note(qtbot, window, "# 現在のノート")
    saved(qtbot, window)
    qtbot.waitUntil(lambda: not window.reader._callbacks)
    queries = []

    def unexpected_query(*args, **kwargs):
        queries.append((args, kwargs))
        return []

    monkeypatch.setattr(window.store, "list_notes", unexpected_query)
    monkeypatch.setattr(window.store, "search", unexpected_query)
    window.right_sidebar.set_mode(mode)
    assert queries == []
    assert not window.reader._callbacks
    assert window.sidebar.mode == "history"
    assert not window.sidebar.results.isHidden()
    assert not window.sidebar.status_label.isHidden()
    window.refresh_library()
    qtbot.waitUntil(lambda: not window.reader._callbacks)
    assert len(queries) == 1


@pytest.mark.parametrize("mode", ["assets", "outline"])
def test_right_sidebar_modes_preserve_pending_left_library_results(
    qtbot, make_notebook, monkeypatch, mode
):
    window = make_notebook()
    note_id = new_note(qtbot, window, "# 現在のノート")
    saved(qtbot, window)
    qtbot.waitUntil(lambda: not window.reader._callbacks)
    started, release = threading.Event(), threading.Event()
    original = window.store.list_notes
    received = []

    def delayed(*args, **kwargs):
        started.set()
        if not release.wait(10):
            raise TimeoutError("test did not release the library query")
        return original(*args, **kwargs)

    monkeypatch.setattr(window.store, "list_notes", delayed)
    monkeypatch.setattr(
        window.sidebar, "set_results", lambda *args, **kwargs: received.append(args)
    )
    try:
        window.refresh_library()
        qtbot.waitUntil(started.is_set)
        window.right_sidebar.set_mode(mode)
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.reader._callbacks)
    assert len(received) == 1
    assert window._active == note_id
    assert window.right_sidebar.mode == mode
    assert window.sidebar.mode == "history"
    assert not window.sidebar.results.isHidden()


def test_assets_panel_tracks_the_current_note_and_clears_after_last_tab(qtbot, make_notebook):
    window = make_notebook()
    window.right_sidebar.set_mode("assets")
    assert not window.assets_panel.isEnabled()
    first = new_note(qtbot, window, "# 添付があるノート")
    first_root = window.store.note_dir(first)
    (first_root / "assets").mkdir(parents=True, exist_ok=True)
    (first_root / "assets" / "最初の資料.txt").write_text("first", encoding="utf-8")
    window.assets_controller.refresh()
    qtbot.waitUntil(lambda: window.assets_panel.files.count() == 1)
    assert "最初の資料.txt" in window.assets_panel.files.item(0).text()
    window.assets_panel.files.item(0).setSelected(True)

    second = new_note(qtbot, window, "# 別の添付があるノート")
    second_root = window.store.note_dir(second)
    (second_root / "assets").mkdir(parents=True, exist_ok=True)
    (second_root / "assets" / "次の資料.txt").write_text("second", encoding="utf-8")
    window.assets_controller.refresh()
    qtbot.waitUntil(lambda: window.assets_panel.files.count() == 1)
    assert "次の資料.txt" in window.assets_panel.files.item(0).text()
    assert window.assets_controller.note_id == second
    assert window.assets_controller.base_dir == second_root
    assert window.assets_panel.selected_entries() == []

    activate(qtbot, window, first)
    qtbot.waitUntil(lambda: window.assets_panel.files.count() == 1)
    assert "最初の資料.txt" in window.assets_panel.files.item(0).text()
    assert window.assets_controller.note_id == first
    assert window.assets_controller.base_dir == first_root
    window.close_all()
    qtbot.waitUntil(lambda: not window._busy and not window._order)
    assert window.assets_controller.note_id is None
    assert not window.assets_panel.isEnabled()
    assert window.assets_panel.files.count() == 0


@pytest.mark.parametrize("transient", ["activePopupWidget", "activeModalWidget"])
@pytest.mark.parametrize("event_kind", ["escape", "outside_click"])
def test_overlay_sidebar_stays_open_for_file_menus_and_dialogs(
    qtbot, make_notebook, monkeypatch, transient, event_kind
):
    window = make_notebook()
    new_note(qtbot, window, "# 操作するノート")
    window.right_sidebar.set_mode("assets")
    window._right_sidebar_fixed = False
    window.right_sidebar.show()
    window._layout_sidebar()
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    popup = QWidget(window)
    monkeypatch.setattr(QApplication, "activePopupWidget", lambda: None)
    monkeypatch.setattr(QApplication, "activeModalWidget", lambda: None)
    monkeypatch.setattr(QApplication, transient, lambda: popup)
    if event_kind == "escape":
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    else:
        point = window.rect().bottomRight()
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(point),
            QPointF(window.mapToGlobal(point)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    window.eventFilter(window, event)
    assert window.right_sidebar.isVisible()
    monkeypatch.setattr(QApplication, transient, lambda: None)
    window.eventFilter(window, event)
    assert window.right_sidebar.isHidden()


def test_right_sidebar_toggle_follows_split_button_and_separator(qtbot, make_notebook):
    window = make_notebook()
    new_note(qtbot, window, "# 右サイドバー")
    row = window.display_toolbar.layout()
    controls = [row.itemAt(index).widget() for index in range(row.count())]
    controls = [widget for widget in controls if widget is not None]
    split_index = controls.index(window.display_buttons["split"])
    assert controls[split_index + 1] is window.right_sidebar_separator
    assert window.right_sidebar_separator.frameShape() == QFrame.Shape.VLine
    assert controls[split_index + 2] is window.right_sidebar_button
    assert not window.right_sidebar_button.icon().isNull()
    assert window.right_sidebar_button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert window.right_sidebar_button.accessibleName()
    assert window.assets_panel.parentWidget() is window.right_sidebar
    assert window.outline_panel.parentWidget() is window.right_sidebar

    window.show_history()
    for mode in ("source", "preview", "split"):
        window.set_display_mode(mode)
        qtbot.waitUntil(lambda: not window._changing_display_mode, timeout=15000)
        qtbot.mouseClick(window.right_sidebar_button, Qt.MouseButton.LeftButton)
        assert window.right_sidebar.isVisible()
        assert window.right_sidebar_button.isChecked()
        assert window.sidebar.isVisible()
        assert window.display_mode == mode
        assert window.display_actions[mode].isChecked()
        qtbot.mouseClick(window.right_sidebar_button, Qt.MouseButton.LeftButton)
        assert window.right_sidebar.isHidden()
        assert not window.right_sidebar_button.isChecked()
        assert window.sidebar.isVisible()
        assert window.display_mode == mode


def test_right_sidebar_overlay_and_both_fixed_sidebars_keep_room_for_content(qtbot, make_notebook):
    window = make_notebook()
    new_note(qtbot, window, "# 左右のサイドバー")
    window.resize(1500, 760)
    qtbot.waitUntil(lambda: window.workspace.width() >= 1380)
    original_width = window.pages.width()
    window.toggle_right_sidebar()
    assert window.right_sidebar.isVisible()
    assert window._right_sidebar_space.width() == 0
    assert window.pages.width() == original_width
    assert window.right_sidebar.geometry().right() == window.workspace.rect().right()
    window._right_sidebar_fixed_changed(True)
    qtbot.waitUntil(lambda: window._right_sidebar_space.width() > 0)
    qtbot.waitUntil(lambda: window.pages.width() < original_width)

    window.show_history()
    window._sidebar_fixed_changed(True)
    qtbot.waitUntil(lambda: window._sidebar_space.width() > 0)
    assert window._right_sidebar_space.width() > 0
    qtbot.waitUntil(lambda: window.pages.width() >= 700)
    qtbot.waitUntil(lambda: window.sidebar.geometry().right() < window.pages.geometry().left())
    qtbot.waitUntil(
        lambda: window.pages.geometry().right() < window.right_sidebar.geometry().left()
    )

    window.resize(1200, 760)
    qtbot.waitUntil(lambda: window._right_sidebar_space.width() == 0)
    assert window._sidebar_space.width() > 0
    qtbot.waitUntil(lambda: window.pages.width() >= 700)
    assert window._sidebar_fixed and window._right_sidebar_fixed
    window.resize(760, 650)
    qtbot.waitUntil(lambda: window._sidebar_space.width() == 0)
    assert window._right_sidebar_space.width() == 0
    assert window.sidebar.isVisible() and window.right_sidebar.isVisible()
    qtbot.waitUntil(lambda: window.pages.width() == window.workspace.width())
    assert window.right_sidebar.geometry().right() == window.workspace.rect().right()
    window.resize(1500, 760)
    qtbot.waitUntil(
        lambda: window._sidebar_space.width() > 0 and window._right_sidebar_space.width() > 0
    )


@pytest.mark.parametrize("left_fixed,right_fixed", [(True, False), (False, True)])
def test_sidebar_fixed_settings_are_independent_and_restore(
    qtbot, make_notebook, left_fixed, right_fixed
):
    window = make_notebook()
    assert not window._sidebar_fixed
    assert not window._right_sidebar_fixed
    window._sidebar_fixed_changed(left_fixed)
    window._right_sidebar_fixed_changed(right_fixed)
    close_window(qtbot, window)

    restored = make_notebook(window.store.root)
    assert restored._sidebar_fixed is left_fixed
    assert restored._right_sidebar_fixed is right_fixed
    assert restored.sidebar.isVisible() is left_fixed
    assert restored.right_sidebar.isVisible() is right_fixed
    qtbot.waitUntil(lambda: restored.right_sidebar_button.isChecked() is right_fixed)
    if right_fixed:
        qtbot.waitUntil(lambda: restored._right_sidebar_space.width() > 0)
    if left_fixed:
        qtbot.waitUntil(lambda: restored._sidebar_space.width() > 0)


@pytest.mark.parametrize("focused_side", ["left", "right", "editor"])
def test_escape_closes_focused_sidebar_or_right_sidebar_first(
    qtbot, make_notebook, monkeypatch, focused_side
):
    window = make_notebook()
    new_note(qtbot, window, "# 両方を開く")
    window.show_history()
    window.toggle_right_sidebar()
    focus = {
        "left": window.sidebar.results,
        "right": window.assets_panel.files,
        "editor": window.editor,
    }[focused_side]
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    monkeypatch.setattr(window, "focusWidget", lambda: focus)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    window.eventFilter(focus, event)
    if focused_side == "left":
        assert window.sidebar.isHidden()
        assert window.right_sidebar.isVisible()
    else:
        assert window.right_sidebar.isHidden()
        assert window.sidebar.isVisible()


def test_outside_click_dismisses_right_overlay_and_preserves_fixed_left(
    qtbot, make_notebook, monkeypatch
):
    window = make_notebook()
    new_note(qtbot, window, "# 独立した固定")
    window.show_history()
    window._sidebar_fixed_changed(True)
    window.toggle_right_sidebar()
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    point = window.pages.rect().center()
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(point),
        QPointF(window.pages.mapToGlobal(point)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.eventFilter(window.pages, event)
    assert window.right_sidebar.isHidden()
    assert window.sidebar.isVisible()
    assert window._sidebar_space.width() > 0
