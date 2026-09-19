"""Navigation behavior that does not require an editor or live note library."""

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu, QTabBar, QVBoxLayout, QWidget

from marknotes import notebook_widgets
from marknotes.notebook_widgets import (
    NotebookNoteSidebar,
    NotebookSidebar,
    NotebookTabs,
    _compact_excerpt,
    highlighted_text,
)


@pytest.fixture
def tabs(qtbot):
    widget = NotebookTabs()
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    qtbot.addWidget(widget)
    widget.resize(330, 34)
    widget.show()
    return widget


@pytest.fixture
def sidebar(qtbot):
    widget = NotebookSidebar()
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    qtbot.addWidget(widget)
    widget.resize(320, 500)
    widget.show()
    return widget


@pytest.fixture
def note_sidebar(qtbot):
    widget = NotebookNoteSidebar()
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    qtbot.addWidget(widget)
    widget.resize(320, 500)
    widget.show()
    return widget


def sample_notes():
    return [
        {"id": "a", "title": "first", "last_active": 1},
        {"id": "b", "title": "second", "last_active": 2, "pinned": True},
        {"id": "c", "title": "third", "last_active": 3},
        {"id": "d", "title": "active", "last_active": 0},
    ]


def test_overflow_preserves_logical_order_and_keeps_active_even_when_oldest(tabs):
    tabs.set_notes(sample_notes())
    tabs.set_active("d")
    # Offscreen and native platforms can choose different fallback fonts. Size
    # the host for exactly these two tabs instead of assuming a native font.
    two_tab_width = sum(tabs.tab_bar.tabSizeHint(index).width() for index in (2, 3))
    tabs.resize(two_tab_width + tabs.new_button.width() + tabs.overflow_button.width() + 4, 34)
    assert tabs.visible_note_ids == ["c", "d"]
    assert tabs.hidden_note_ids == ["a", "b"]
    assert tabs.note_ids == ["a", "b", "c", "d"]
    tabs.set_active("a")
    assert "a" in tabs.visible_note_ids
    assert tabs.note_ids == ["a", "b", "c", "d"]
    tabs.resize(1100, 34)
    assert tabs.visible_note_ids == ["a", "b", "c", "d"]
    assert tabs.hidden_note_ids == []


def test_selection_and_drag_emit_user_intent_but_metadata_refresh_does_not(tabs):
    selected = []
    order = []
    tabs.activated.connect(selected.append)
    tabs.order_changed.connect(order.append)
    tabs.set_notes(sample_notes())
    tabs.set_active("c")
    assert selected == []
    tabs.tab_bar.setCurrentIndex(3)
    assert selected == ["d"]
    tabs.tab_bar.moveTab(0, 2)
    assert order == [["b", "c", "a", "d"]]
    assert tabs.note_ids == ["b", "c", "a", "d"]
    notes = {note["id"]: note for note in sample_notes()}
    tabs.set_notes([notes[note_id] for note_id in tabs.note_ids])
    assert selected == ["d"]
    assert len(order) == 1


def test_empty_bar_can_reopen_and_context_actions_never_delete_local_metadata(tabs):
    requests = []
    tabs.reopen_requested.connect(lambda: requests.append("reopen"))
    tabs.close_all_requested.connect(lambda: requests.append("all"))
    tabs.close_others_requested.connect(lambda note_id: requests.append(note_id))
    menu = tabs.build_context_menu()
    assert not menu.actions()[-1].isEnabled()
    tabs.set_can_reopen(True)
    menu = tabs.build_context_menu()
    assert menu.actions()[-1].isEnabled()
    menu.actions()[-1].trigger()
    assert requests == ["reopen"]
    tabs.set_notes(sample_notes())
    menu = tabs.build_context_menu("b")
    menu.actions()[1].trigger()
    menu.actions()[2].trigger()
    assert requests == ["reopen", "b", "all"]
    assert tabs.note_ids == ["a", "b", "c", "d"]


def test_plus_and_tab_close_forward_the_target(qtbot, tabs):
    requests = []
    tabs.new_requested.connect(lambda: requests.append("new"))
    tabs.close_requested.connect(requests.append)
    tabs.set_notes(sample_notes())
    qtbot.mouseClick(tabs.new_button, Qt.MouseButton.LeftButton)
    tabs.tab_bar.tabCloseRequested.emit(1)
    assert requests == ["new", "b"]


def test_duplicate_note_ids_are_rejected_without_mutating_tabs(tabs):
    tabs.set_notes(sample_notes())
    with pytest.raises(ValueError, match="once"):
        tabs.set_notes([{"id": "same"}, {"id": "same"}])
    assert tabs.note_ids == ["a", "b", "c", "d"]


def test_highlights_escape_html_and_merge_ranges_without_losing_text():
    text = '<script>alert("x")</script> メモ'
    marked = highlighted_text(text, [(0, 4), (3, 8), (200, 300), (-4, 2)])
    assert "<script>" not in marked
    assert "&lt;script&gt;" in marked
    assert "&quot;x&quot;" in marked
    assert marked.count("<span") == 1
    assert marked.endswith(" メモ")


def test_short_query_waits_for_enter_long_query_debounces_and_clear_cancels(qtbot, sidebar):
    requests = []
    sidebar.query_changed.connect(requests.append)
    sidebar.set_mode("search")
    sidebar.search_edit.setText("メモ")
    qtbot.wait(300)
    assert requests == []
    qtbot.keyClick(sidebar.search_edit, Qt.Key.Key_Return)
    assert requests == ["メモ"]
    sidebar.search_edit.setText("メモ帳")
    sidebar.search_edit.setText("メモ帳の")
    qtbot.waitUntil(lambda: requests == ["メモ", "メモ帳の"])
    sidebar.search_edit.setText("キャンセル")
    sidebar.search_edit.clear()
    qtbot.wait(300)
    assert requests == ["メモ", "メモ帳の", ""]


def test_mode_change_stops_pending_search_and_fixed_is_separate_from_note_pin(qtbot, sidebar):
    queries = []
    fixed = []
    modes = []
    sidebar.query_changed.connect(queries.append)
    sidebar.pinned_changed.connect(fixed.append)
    sidebar.mode_changed.connect(modes.append)
    sidebar.set_mode("search")
    sidebar.search_edit.setText("pending")
    sidebar.set_mode("pinned")
    qtbot.wait(300)
    assert queries == []
    assert modes == ["search", "pinned"]
    assert sidebar.date_combo.isHidden()
    assert sidebar.search_edit.isHidden()
    sidebar.set_fixed(True)
    assert fixed == []
    qtbot.mouseClick(sidebar.fixed_button, Qt.MouseButton.LeftButton)
    assert fixed == [False]


def test_history_groups_across_pages_and_more_button_requests_another_page(qtbot, sidebar):
    next_pages = []
    sidebar.more_requested.connect(lambda: next_pages.append(True))
    sidebar.set_results(
        [{"id": "a", "title": "one", "content_updated_at": "2026-09-13T12:00:00+09:00"}],
        has_more=True,
    )
    qtbot.mouseClick(sidebar.more_button, Qt.MouseButton.LeftButton)
    assert next_pages == [True]
    sidebar.set_results(
        [
            {"id": "b", "title": "two", "content_updated_at": "2026-09-13T11:00:00+09:00"},
            {"id": "c", "title": "three", "content_updated_at": "2026-09-12T12:00:00+09:00"},
        ],
        append=True,
    )
    assert sidebar.results.count() == 5  # Three notes, two local-date headings.
    assert len(sidebar._cards) == 3
    assert sidebar.more_button.isHidden()


def test_search_groups_escaped_snippets_and_click_uses_original_note_offset(qtbot, sidebar):
    opened = []
    sidebar.note_requested.connect(lambda note_id, offset: opened.append((note_id, offset)))
    sidebar.set_mode("search")
    sidebar.set_results(
        [
            {
                "id": "note",
                "title": "<img>メモ",
                "title_matches": [(5, 7)],
                "total_matches": 2,
                "snippets": [
                    {"text": "<script>メモ", "start": 100, "matches": [(8, 10)]},
                    {"text": "次のメモ", "start": 250, "matches": [(2, 4)]},
                ],
            }
        ]
    )
    assert sidebar.results.count() == 1
    item, card = sidebar._cards[0]
    rendered = card.label.text()
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<img>" not in rendered
    assert "2 件一致" in rendered
    assert 'href="match:252"' in rendered
    assert rendered.count("background-color:") == 3
    qtbot.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert opened == [("note", 108)]
    sidebar.results.setCurrentItem(item)
    qtbot.keyClick(sidebar.results, Qt.Key.Key_Return)
    assert opened == [("note", 108), ("note", 108)]
    old_height = item.sizeHint().height()
    sidebar.resize(500, 500)
    assert item.sizeHint().height() <= old_height


def test_programmatic_query_and_fixed_restore_do_not_launch_requests(sidebar):
    queries = []
    sidebar.query_changed.connect(queries.append)
    sidebar.set_query("保存した検索")
    assert sidebar.search_edit.text() == "保存した検索"
    assert queries == []
    sidebar.set_query("明示的な検索", emit=True)
    assert queries == ["明示的な検索"]


def test_more_matches_loads_only_the_selected_note_without_resetting_results(qtbot, sidebar):
    sidebar.set_mode("search")
    requested = []
    sidebar.more_matches_requested.connect(requested.append)
    first_three = [
        {"text": f"メモ {index}", "start": index * 100, "matches": [(0, 2)]} for index in range(3)
    ]
    sidebar.set_results(
        [
            {"id": "first", "title": "対象", "snippets": first_three, "total_matches": 5},
            {"id": "second", "title": "別ノート", "snippets": [], "total_matches": 0},
        ]
    )
    first_item, first_card = sidebar._cards[0]
    second_item, second_card = sidebar._cards[1]
    sidebar.results.setCurrentItem(second_item)
    first_height = first_item.sizeHint().height()
    assert not first_card.more_button.isHidden()
    assert "残り 2 件" in first_card.more_button.text()
    qtbot.mouseClick(first_card.more_button, Qt.MouseButton.LeftButton)
    assert requested == ["first"]
    assert not first_card.more_button.isEnabled()
    all_snippets = first_three + [
        {"text": f"メモ {index}", "start": index * 100, "matches": [(0, 2)]}
        for index in range(3, 5)
    ]
    sidebar.set_note_snippets("first", all_snippets, 5)
    assert sidebar.results.count() == 2
    assert sidebar.results.currentItem() is second_item
    assert sidebar._cards[1] == (second_item, second_card)
    updated_item, updated_card = sidebar._cards[0]
    assert updated_item is first_item
    assert updated_card.more_button.isHidden()
    assert updated_card.label.text().count("background-color:") == 5
    assert updated_item.sizeHint().height() >= first_height
    sidebar.set_note_snippets("stale-result", [], 0)
    assert sidebar.results.count() == 2


def test_custom_tab_close_keeps_note_identity_after_reorder_and_theme_switch(qtbot, tabs):
    requests = []
    tabs.close_requested.connect(requests.append)
    tabs.set_notes(sample_notes())
    close_button = tabs.tab_bar.tabButton(1, QTabBar.ButtonPosition.RightSide)
    assert not close_button.icon().isNull()
    light_icon = close_button.icon().cacheKey()
    tabs.tab_bar.moveTab(1, 0)
    tabs.apply_theme(True)
    assert close_button.icon().cacheKey() != light_icon
    assert tabs.tab_bar.tabButton(0, QTabBar.ButtonPosition.RightSide) is close_button
    close_button.click()
    assert requests == ["b"]
    assert tabs.note_ids == ["b", "a", "c", "d"]
    assert not tabs.tab_bar.drawBase()


def test_space_after_tab_actions_propagates_window_drag_and_double_click(qtbot):
    class TitlebarHost(QWidget):
        def __init__(self):
            super().__init__()
            self.pressed = []
            self.double_clicked = []

        def mousePressEvent(self, event):
            self.pressed.append(event.position().toPoint())
            event.accept()

        def mouseDoubleClickEvent(self, event):
            self.double_clicked.append(event.position().toPoint())
            event.accept()

    host = TitlebarHost()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    tabs = NotebookTabs(host)
    layout.addWidget(tabs)
    tabs.set_notes([{"id": "one", "title": "one"}])
    host.resize(650, 90)
    host.show()
    blank = QPoint(tabs.overflow_button.geometry().right() + 30, tabs.height() // 2)
    assert tabs.rect().contains(blank)
    expected = tabs.mapTo(host, blank)
    qtbot.mouseClick(tabs, Qt.MouseButton.LeftButton, pos=blank)
    assert host.pressed == [expected]
    qtbot.mouseDClick(tabs, Qt.MouseButton.LeftButton, pos=blank)
    assert host.double_clicked == [expected]


def test_sidebar_theme_switch_preserves_results_and_explicit_rich_text_colors(sidebar):
    sidebar.set_mode("search")
    sidebar.set_results(
        [
            {
                "id": "note",
                "title": "<note> メモ",
                "total_matches": 2,
                "snippets": [{"text": "メモ内容", "start": 10, "matches": [(0, 2)]}],
            }
        ]
    )
    item, card = sidebar._cards[0]
    sidebar.results.setCurrentItem(item)
    card.more_button.setEnabled(False)
    sidebar.apply_theme(True)
    assert sidebar._cards[0] == (item, card)
    assert sidebar.results.currentItem() is item
    assert not card.more_button.isEnabled()
    assert "color:#e1e7ef" in card.label.text()
    assert "color:#a0adbf" in card.label.text()
    assert "color:inherit" not in card.label.text()
    assert "&lt;note&gt;" in card.label.text()
    assert card.label.text().count("background-color:") == 1
    sidebar.set_note_snippets("note", [{"text": "メモ内容", "start": 10, "matches": [(0, 2)]}], 2)
    _, replacement = sidebar._cards[0]
    assert "color:#e1e7ef" in replacement.label.text()
    sidebar.apply_theme(False)
    assert "color:#253047" in replacement.label.text()
    assert "color:#67758a" in replacement.label.text()
    assert sidebar.search_edit.height() == sidebar.date_combo.height() == 32


def test_history_excerpt_removes_duplicate_heading_and_limits_whitespace_and_length():
    text = "# **見出し**\n\n短い本文。\n  次の行。\t次の文。"
    assert _compact_excerpt(text, "見出し") == "短い本文。 次の行。 次の文。"
    assert _compact_excerpt("見出し\n", "見出し") == ""
    assert len(_compact_excerpt("長い本文" * 100, "タイトル")) <= 120


def test_history_card_has_two_line_excerpt_that_refits_viewport(qtbot, sidebar):
    sidebar.set_results(
        [
            {"id": str(index), "title": "見出し", "excerpt": "# 見出し\n" + "長い本文。\n" * 60}
            for index in range(8)
        ]
    )
    qtbot.waitUntil(lambda: sidebar.results.verticalScrollBar().maximum() > 0)
    qtbot.waitUntil(
        lambda: all(
            card.width() == sidebar.results.viewport().width() - 8 for _, card in sidebar._cards
        )
    )
    item, card = sidebar._cards[0]
    assert len(card.excerpt_label.text().splitlines()) <= 2
    assert "見出し" not in card.excerpt_label.text()
    assert item.sizeHint().height() < 130
    sidebar.resize(500, 500)
    qtbot.waitUntil(lambda: card.width() == sidebar.results.viewport().width() - 8)
    assert len(card.excerpt_label.text().splitlines()) <= 2


def test_resize_compacts_visible_tab_rectangles_and_keeps_close_buttons_inside(qtbot, tabs):
    tabs.set_notes(
        [{"id": str(index), "title": f"note {index}", "last_active": index} for index in range(8)]
    )
    tabs.set_active("0")
    for width in (1020, 540, 330, 1020, 540):
        tabs.resize(width, 34)
        qtbot.waitUntil(lambda target=width: tabs.width() == target)
        previous_right = -1
        for index in range(tabs.tab_bar.count()):
            if not tabs.tab_bar.isTabVisible(index):
                continue
            rect = tabs.tab_bar.tabRect(index)
            assert not rect.isEmpty()
            assert tabs.tab_bar.rect().contains(rect)
            assert tabs.tab_bar.tabAt(rect.center()) == index
            assert rect.left() == previous_right + 1
            button = tabs.tab_bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
            assert rect.contains(button.geometry())
            previous_right = rect.right()


@pytest.mark.parametrize("count", [0, 1, 2, 8, 100])
def test_tab_actions_follow_last_visible_tab_with_spare_space_after(qtbot, tabs, count):
    notes = [
        {"id": str(index), "title": f"note {index}", "last_active": index} for index in range(count)
    ]
    tabs.set_notes(notes)
    if count:
        tabs.set_active("0")
    for width, dark in [(1020, False), (330, False), (1020, True), (330, True)]:
        tabs.resize(width, 34)
        tabs.apply_theme(dark)
        qtbot.waitUntil(lambda target=width: tabs.width() == target)
        visible_rects = [
            tabs.tab_bar.tabRect(index)
            for index in range(count)
            if tabs.tab_bar.isTabVisible(index)
        ]
        used_width = max((rect.right() + 1 for rect in visible_rects), default=0)
        assert tabs.tab_bar.width() == used_width
        assert tabs.new_button.x() == tabs.tab_bar.x() + used_width + 2
        assert tabs.overflow_button.x() == tabs.new_button.geometry().right() + 3
        assert tabs.overflow_button.geometry().right() < tabs.width()
        if count <= 2 and width == 1020:
            assert tabs.width() - tabs.overflow_button.geometry().right() > 600
        if count:
            assert "0" in tabs.visible_note_ids
        for rect in visible_rects:
            assert tabs.tab_bar.rect().contains(rect)
    if count > 1:
        tabs.tab_bar.moveTab(count - 1, 0)
        remaining = [note for note in notes if note["id"] != str(count - 1)]
        tabs.set_notes(remaining)
        visible_rects = [
            tabs.tab_bar.tabRect(index)
            for index in range(tabs.tab_bar.count())
            if tabs.tab_bar.isTabVisible(index)
        ]
        assert tabs.new_button.x() == max(rect.right() for rect in visible_rects) + 3


@pytest.fixture
def constructed_menus(monkeypatch):
    menus = []

    class MenuWithoutPopup(QMenu):
        def exec(self, *_args):
            menus.append(self)

        def popup(self, *_args):
            raise AssertionError("Tests must not display popup menus")

    # PySide's QMenu method descriptors do not reliably accept class-level
    # monkeypatching; substitute a Python subclass before a menu is constructed.
    monkeypatch.setattr(notebook_widgets, "QMenu", MenuWithoutPopup)
    return menus


def trigger_delete(menu):
    next(action for action in menu.actions() if action.text() == "ノートを削除…").trigger()


def test_tab_delete_targets_context_note_without_closing_or_mutating_tabs(tabs, constructed_menus):
    deleted = []
    closed = []
    tabs.delete_requested.connect(deleted.append)
    tabs.close_requested.connect(closed.append)
    tabs.set_notes(sample_notes())
    tabs.set_active("a")
    tabs.resize(1100, 34)
    tabs._show_tab_context(tabs.tab_bar.tabRect(1).center())
    assert len(constructed_menus) == 1
    assert deleted == []
    trigger_delete(constructed_menus[-1])
    assert deleted == ["b"]
    assert closed == []
    assert tabs._active == "a"
    assert tabs.note_ids == ["a", "b", "c", "d"]
    tabs._show_overflow()
    assert len(constructed_menus) == 2
    trigger_delete(constructed_menus[-1])
    assert deleted == ["b", "a"]


def test_tab_delete_is_unavailable_for_empty_space_and_stale_notes(tabs):
    deleted = []
    tabs.delete_requested.connect(deleted.append)
    tabs.set_notes(sample_notes())
    for note_id in (None, "missing"):
        assert "ノートを削除…" not in [
            action.text() for action in tabs.build_context_menu(note_id).actions()
        ]
    menu = tabs.build_context_menu("b")
    tabs.set_notes([note for note in sample_notes() if note["id"] != "b"])
    trigger_delete(menu)
    assert deleted == []


@pytest.mark.parametrize("mode", ["history", "pinned", "search"])
@pytest.mark.parametrize("target_name", ["card", "label"])
def test_sidebar_delete_uses_right_clicked_card_in_every_mode(
    sidebar, constructed_menus, mode, target_name
):
    deleted = []
    opened = []
    sidebar.delete_requested.connect(deleted.append)
    sidebar.note_requested.connect(lambda *args: opened.append(args))
    sidebar.set_mode(mode)
    sidebar.set_results(
        [{"id": "a", "title": "first"}, {"id": "b", "title": "second", "pinned": True}]
    )
    sidebar.results.setCurrentItem(sidebar._cards[0][0])
    item, card = sidebar._cards[1]
    target = card if target_name == "card" else card.label
    point = QPoint(5, 5)
    QApplication.sendEvent(
        target,
        QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, target.mapToGlobal(point)),
    )
    assert len(constructed_menus) == 1
    assert deleted == []
    menu = constructed_menus[-1]
    assert [action.text() for action in menu.actions()] == ["ピン止めを解除", "", "ノートを削除…"]
    trigger_delete(menu)
    assert deleted == ["b"]
    assert opened == []
    assert sidebar.results.currentItem() is item
    assert [card.note_id for _, card in sidebar._cards] == ["a", "b"]


def test_sidebar_delete_does_not_use_selection_on_date_heading_or_blank_space(
    sidebar, constructed_menus
):
    sidebar.set_results([{"id": "note", "title": "one"}])
    sidebar.results.setCurrentItem(sidebar._cards[0][0])
    header = sidebar.results.item(0)
    sidebar._show_result_context(sidebar.results.visualItemRect(header).center())
    sidebar._show_result_context(QPoint(10, sidebar.results.viewport().height() - 1))
    assert constructed_menus == []
    assert sidebar.build_context_menu().isEmpty()
    assert sidebar.build_context_menu("missing").isEmpty()


def test_replaced_search_card_keeps_delete_menu_and_stale_action_is_ignored(
    sidebar, constructed_menus
):
    deleted = []
    sidebar.delete_requested.connect(deleted.append)
    sidebar.set_mode("search")
    sidebar.set_results([{"id": "note", "title": "one"}])
    sidebar.set_note_snippets("note", [{"text": "match", "matches": [(0, 5)]}], 3)
    card = sidebar._cards[0][1]
    assert not card.more_button.isHidden()
    card.more_button.customContextMenuRequested.emit(QPoint(2, 2))
    assert len(constructed_menus) == 1
    trigger_delete(constructed_menus[-1])
    assert deleted == ["note"]
    # A query refresh while the menu is open must not delete a formerly selected note.
    menu = sidebar.build_context_menu("note")
    sidebar.set_results([{"id": "another", "title": "another"}])
    trigger_delete(menu)
    assert deleted == ["note"]


@pytest.mark.parametrize("mode", ["history", "pinned", "search"])
@pytest.mark.parametrize("pinned", [False, True])
def test_sidebar_pin_targets_context_note_without_opening(sidebar, constructed_menus, mode, pinned):
    requested, opened = [], []
    sidebar.note_pin_requested.connect(lambda *args: requested.append(args))
    sidebar.note_requested.connect(lambda *args: opened.append(args))
    sidebar.set_mode(mode)
    sidebar.set_results(
        [
            {"id": "a", "title": "first"},
            {"id": "b", "title": "second", "pinned": pinned},
        ]
    )
    sidebar.results.setCurrentItem(sidebar._cards[0][0])
    card = sidebar._cards[1][1]
    point = QPoint(5, 5)
    QApplication.sendEvent(
        card.label,
        QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, card.label.mapToGlobal(point)),
    )
    action = constructed_menus[-1].actions()[0]
    assert action.text() == ("ピン止めを解除" if pinned else "ノートをピン止め")
    action.trigger()
    assert requested == [("b", not pinned)]
    assert opened == []
    assert card.result["pinned"] is pinned  # Persistence belongs to the window.
    sidebar.set_results([{"id": "a", "title": "first"}])
    action.trigger()
    assert requested == [("b", not pinned)]


@pytest.mark.parametrize("mode", ["history", "pinned", "search"])
@pytest.mark.parametrize("pinned", [False, True])
def test_card_pin_button_emits_target_without_opening(qtbot, sidebar, mode, pinned):
    requested, opened = [], []
    sidebar.note_pin_requested.connect(lambda *args: requested.append(args))
    sidebar.note_requested.connect(lambda *args: opened.append(args))
    sidebar.set_mode(mode)
    sidebar.set_results(
        [
            {"id": "a", "title": "first"},
            {"id": "b", "title": "長いノート名" * 12, "pinned": pinned},
        ]
    )
    sidebar.results.setCurrentItem(sidebar._cards[0][0])
    for width, dark in [(320, False), (500, True), (250, False)]:
        sidebar.resize(width, 500)
        sidebar.apply_theme(dark)
        card = sidebar._cards[1][1]
        button = card.pin_button
        qtbot.waitUntil(
            lambda button=button, card=card: button.x() > card.content.geometry().right()
        )
        assert card.rect().contains(button.geometry())
        assert button.y() == card.label.mapTo(card, QPoint()).y()
        assert card.label.height() >= card.label.heightForWidth(card.label.width())
        assert button.isChecked() is pinned
        assert button.text() == ""
        assert not button.icon().isNull()
        assert button.toolTip() == ("ピン止めを解除" if pinned else "ノートをピン止め")
        assert "長いノート名" in button.accessibleName()
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        assert requested[-1] == ("b", not pinned)
        assert button.isChecked() is pinned
    assert opened == []
    assert sidebar.results.currentItem() is sidebar._cards[0][0]


def test_pin_button_still_works_after_search_snippets_are_replaced(qtbot, sidebar):
    requested = []
    sidebar.note_pin_requested.connect(lambda *args: requested.append(args))
    sidebar.set_mode("search")
    sidebar.set_results([{"id": "a", "title": "title", "pinned": True}])
    sidebar.set_note_snippets("a", [{"text": "match", "matches": [(0, 5)]}], 1)
    button = sidebar._cards[0][1].pin_button
    button.setFocus()
    qtbot.keyClick(button, Qt.Key.Key_Space)
    assert requested == [("a", False)]
    assert button.isChecked()


def test_pinned_tab_and_overflow_show_only_the_note_title(tabs, constructed_menus):
    notes = sample_notes()
    tabs.set_notes(notes)
    for index, note in enumerate(notes):
        assert tabs.tab_bar.tabText(index) == note["title"]
    tabs.set_active("a")
    tabs.resize(100, 34)
    assert "b" in tabs.hidden_note_ids
    tabs._show_overflow()
    labels = [action.text() for action in constructed_menus[-1].actions()]
    assert "second" in labels
    assert "● second" not in labels


@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize("mode", ["history", "pinned", "search"])
def test_note_hover_and_pin_hover_have_separate_backgrounds(qtbot, sidebar, dark, mode):
    sidebar.set_mode(mode)
    sidebar.apply_theme(dark)
    sidebar.set_results([{"id": "a", "title": "ノート", "excerpt": "本文の抜粋"}])
    card = sidebar._cards[0][1]
    QApplication.processEvents()
    card.layout().activate()
    assert card.content.geometry().right() < card.pin_button.x()
    hover = notebook_widgets._navigation_colors(dark)["hover"]
    content_point = QPoint(2, card.content.height() // 2)
    pin_point = QPoint(3, card.pin_button.height() // 2)

    def content_color():
        return card.content.grab().toImage().pixelColor(content_point).name()

    def pin_color():
        return card.pin_button.grab().toImage().pixelColor(pin_point).name()

    def set_hover(widget, hovered):
        # Offscreen windows do not reliably receive native cursor enter/leave events.
        widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, hovered)
        QApplication.sendEvent(widget, QEvent(QEvent.Type.Enter if hovered else QEvent.Type.Leave))
        widget.update()

    set_hover(card, True)
    set_hover(card.pin_button, False)
    set_hover(card.content, True)
    assert content_color() == hover
    assert pin_color() != hover
    set_hover(card.content, False)
    set_hover(card.pin_button, True)
    assert pin_color() == hover
    assert content_color() != hover


@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize(
    "fixture_name, labels",
    [("sidebar", ("履歴", "ピン", "検索")), ("note_sidebar", ("添付", "アウトライン"))],
)
def test_sidebar_text_tabs_are_centered_accessible_and_fit_narrow_panel(
    qtbot, request, fixture_name, labels, dark
):
    sidebar = request.getfixturevalue(fixture_name)
    sidebar.apply_theme(dark)
    sidebar.resize(250, 500)
    QApplication.processEvents()
    assert sidebar.mode_bar.count() == len(labels)
    for index, mode in enumerate(sidebar.MODES):
        assert sidebar.mode_bar.tabText(index) == labels[index]
        assert sidebar.mode_bar.tabIcon(index).isNull()
        assert sidebar.mode_bar.tabToolTip(index)
        assert sidebar.mode_bar.accessibleTabName(index) == sidebar.mode_bar.tabToolTip(index)
        rect = sidebar.mode_bar.tabRect(index)
        assert sidebar.mode_bar.rect().contains(rect)
        assert rect.width() >= sidebar.mode_bar.fontMetrics().horizontalAdvance(labels[index]) + 12
        qtbot.mouseClick(sidebar.mode_bar, Qt.MouseButton.LeftButton, pos=rect.center())
        assert sidebar.mode == mode
    assert sidebar.fixed_button.text() == ""
    assert not sidebar.fixed_button.icon().isNull()


@pytest.mark.parametrize("fixture_name, prefix", [("sidebar", ""), ("note_sidebar", "右")])
def test_sidebar_fixed_icon_tracks_programmatic_state_theme_and_user_toggle(
    qtbot, request, fixture_name, prefix
):
    sidebar = request.getfixturevalue(fixture_name)
    requested = []
    sidebar.pinned_changed.connect(requested.append)
    released_icon = sidebar.fixed_button.icon().pixmap(24, 24).toImage()
    sidebar.set_fixed(True)
    assert requested == []
    assert sidebar.fixed_button.toolTip() == f"{prefix}サイドバーの固定を解除"
    assert sidebar.fixed_button.accessibleName() == sidebar.fixed_button.toolTip()
    fixed_icon = sidebar.fixed_button.icon().pixmap(24, 24).toImage()
    assert fixed_icon != released_icon
    sidebar.apply_theme(True)
    assert sidebar.fixed_button.icon().pixmap(24, 24).toImage() != fixed_icon
    qtbot.mouseClick(sidebar.fixed_button, Qt.MouseButton.LeftButton)
    assert requested == [False]
    assert sidebar.fixed_button.toolTip() == f"{prefix}サイドバーを固定表示"


def test_current_note_tools_have_independent_sidebar_and_preserve_library_results(
    sidebar, note_sidebar
):
    assert sidebar.MODES == ("history", "pinned", "search")
    assert note_sidebar.MODES == ("assets", "outline")
    assert note_sidebar.mode == "assets"
    assert not hasattr(note_sidebar, "search_edit")
    assets, outline = QWidget(), QWidget()
    note_sidebar.set_mode_panel("assets", assets)
    note_sidebar.set_mode_panel("outline", outline)
    sidebar.set_results([{"id": "a", "title": "title"}], has_more=True)
    result_item, result_card = sidebar._cards[0]
    sidebar.results.setCurrentItem(result_item)
    modes = []
    note_sidebar.mode_changed.connect(modes.append)
    for mode, selected, hidden in [("outline", outline, assets), ("assets", assets, outline)]:
        note_sidebar.set_mode(mode)
        assert selected.isVisible()
        assert hidden.isHidden()
        assert sidebar.mode == "history"
        assert sidebar._cards == [(result_item, result_card)]
        assert sidebar.results.currentItem() is result_item
        assert sidebar.results.isVisible()
        assert sidebar.date_combo.isVisible()
        assert sidebar.status_label.isVisible()
        assert sidebar.more_button.isVisible()
    assert modes == ["outline", "assets"]
    sidebar.set_mode("search")
    assert note_sidebar.mode == "assets"
    assert assets.isVisible()
    sidebar.set_fixed(True)
    assert not note_sidebar.fixed_button.isChecked()
    note_sidebar.set_fixed(True)
    sidebar.set_fixed(False)
    assert note_sidebar.fixed_button.isChecked()
    for mode in note_sidebar.MODES:
        with pytest.raises(ValueError):
            sidebar.set_mode(mode)
    for mode in (*sidebar.MODES, "invalid"):
        with pytest.raises(ValueError):
            note_sidebar.set_mode(mode)


def test_library_results_can_update_while_note_tools_are_visible(sidebar, note_sidebar):
    assets = QWidget()
    note_sidebar.set_mode_panel("assets", assets)
    sidebar.set_results([{"id": "a", "title": "title"}], has_more=True)
    assert sidebar.more_button.isVisible()
    assert sidebar.results.isVisible()
    assert note_sidebar.mode == "assets"
    assert assets.isVisible()


def test_note_panel_installation_inherits_theme_and_replaces_visible_panel(qtbot, note_sidebar):
    sidebar = note_sidebar

    class ThemedPanel(QWidget):
        def __init__(self):
            super().__init__()
            self.themes = []

        def apply_theme(self, dark):
            self.themes.append(dark)

    first, second = ThemedPanel(), ThemedPanel()
    qtbot.addWidget(first)
    qtbot.addWidget(second)
    sidebar.apply_theme(True)
    sidebar.set_mode("assets")
    sidebar.set_mode_panel("assets", first)
    assert first.isVisible()
    assert first.themes == [True]
    sidebar.apply_theme(False)
    assert first.themes == [True, False]
    sidebar.set_mode_panel("assets", second)
    assert first.isHidden()
    assert second.isVisible()
    assert second.themes == [False]
    for mode in ("history", "invalid"):
        with pytest.raises(ValueError):
            sidebar.set_mode_panel(mode, second)
    with pytest.raises(ValueError):
        sidebar.set_mode_panel("outline", second)
