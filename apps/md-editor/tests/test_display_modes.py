"""Visible panes retain their content, selection and logical scroll positions."""

import json

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QToolBar

from md_editor.app import RESOURCE_DIR, MainWindow


@pytest.fixture
def window(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "display.ini"), QSettings.Format.IniFormat)
    widget = MainWindow(settings)
    qtbot.addWidget(widget, before_close_func=lambda w: w.editor.document().setModified(False))
    widget.resize(1150, 780)
    widget.show()
    widget.set_source((RESOURCE_DIR / "scroll-check.md").read_text(encoding="utf-8"), RESOURCE_DIR)
    ready(qtbot, widget)
    return widget


def ready(qtbot, window):
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    qtbot.waitUntil(lambda: not window._changing_display_mode)
    qtbot.wait(120)


def js(qtbot, window, script):
    results = []
    window.preview.page().runJavaScript(script, results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=10000)
    return results[0]


def metrics(qtbot, window):
    return json.loads(js(qtbot, window, "JSON.stringify(window.previewApi.metrics())"))


def scroll_preview(qtbot, window, fraction):
    old = window._preview_position
    js(qtbot, window, f"window.scrollTo(0, window.previewApi.metrics().maxScroll * {fraction})")
    qtbot.waitUntil(lambda: abs(window._preview_position - old) > 1, timeout=5000)
    qtbot.wait(100)
    return window._preview_position


def test_menu_and_buttons_share_modes_without_destroying_editor_state(qtbot, window):
    assert not window.findChildren(QToolBar)
    assert window.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is window.display_toolbar
    original = window.editor.toPlainText()
    cursor = window.editor.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    window.splitter.setSizes([390, 760])
    qtbot.wait(100)
    sizes = window.splitter.sizes()
    for mode in ("source", "preview", "split"):
        button = window.display_buttons[mode]
        assert button.defaultAction() is window.display_actions[mode]
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: not window._changing_display_mode)
        assert window.display_actions[mode].isChecked()
        assert sum(a.isChecked() for a in window.display_actions.values()) == 1
        assert window.editor.isVisible() is (mode != "preview")
        assert window.preview.isVisible() is (mode != "source")
        assert window.editor.toPlainText() == original
        assert window.editor.textCursor().selectedText() == original[3:8]
    assert window.splitter.sizes() == pytest.approx(sizes, abs=2)
    window.display_actions["source"].trigger()
    assert window.display_mode == "source"
    assert window.settings.value("display/mode") == "source"
    assert window.display_buttons["source"].isChecked()


def test_hidden_preview_displays_latest_edits_and_restores_source_position(qtbot, window):
    window.set_display_mode("source")
    qtbot.waitUntil(lambda: not window._changing_display_mode)
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)
    window.insert_text("\n\n## added while hidden\n\nnew content")
    window.editor.scroll_to_source(65)
    qtbot.wait(250)
    assert window.preview.isHidden()
    window.set_display_mode("preview")
    ready(qtbot, window)
    assert "added while hidden" in js(qtbot, window, "document.body.innerText")
    assert metrics(qtbot, window)["source"] == pytest.approx(65, abs=0.2)
    window.set_display_mode("split")
    ready(qtbot, window)
    assert window.editor.source_position() == pytest.approx(65, abs=0.2)


def test_preview_navigation_is_authoritative_when_source_is_hidden(qtbot, window):
    window.editor.scroll_to_source(20)
    qtbot.wait(100)
    window.set_display_mode("preview")
    ready(qtbot, window)
    position = scroll_preview(qtbot, window, 0.61)
    window.apply_theme("dark", persist=False)
    qtbot.wait(180)
    assert metrics(qtbot, window)["source"] == pytest.approx(position, abs=0.2)
    window.set_display_mode("split")
    ready(qtbot, window)
    assert window.editor.source_position() == pytest.approx(position, abs=1.1)
    assert metrics(qtbot, window)["source"] == pytest.approx(position, abs=1.1)


def test_disabled_sync_keeps_independent_positions_across_modes(qtbot, window):
    window.sync_action.setChecked(False)
    window.editor.scroll_to_source(25)
    qtbot.wait(100)
    position = scroll_preview(qtbot, window, 0.75)
    for mode in ("source", "preview", "split"):
        window.set_display_mode(mode)
        qtbot.waitUntil(lambda: not window._changing_display_mode)
        qtbot.wait(120)
    assert window.editor.source_position() == pytest.approx(25, abs=0.2)
    assert metrics(qtbot, window)["source"] == pytest.approx(position, abs=0.2)


def test_preview_only_search_reveals_source_and_finds_visible_match(qtbot, window):
    window.set_display_mode("preview")
    ready(qtbot, window)
    window.show_search(True)
    qtbot.waitUntil(lambda: window.search.query.hasFocus())
    assert window.display_mode == "split"
    assert window.search.replace_row.isVisible()
    window.search.query.setText("最終")
    window.set_display_mode("preview")
    ready(qtbot, window)
    window.navigate_search()
    qtbot.waitUntil(lambda: window.editor.textCursor().selectedText() == "最終")
    assert window.display_mode == "split"
    qtbot.wait(100)
    assert window.editor.cursorRect().intersects(window.editor.viewport().rect())


def test_preview_only_copy_uses_preview_selection_not_hidden_source(qtbot, window):
    window.set_source("# Heading\n\nVisible paragraph", window.base_dir)
    ready(qtbot, window)
    window.editor.selectAll()
    window.set_display_mode("preview")
    ready(qtbot, window)
    assert not window.cut_action.isEnabled()
    assert not window.delete_action.isEnabled()
    # Entering preview preserves the mirrored source selection.
    qtbot.waitUntil(lambda: window.copy_action.isEnabled())
    assert "Heading" in window.preview.page().selectedText()
    js(qtbot, window, "window.getSelection().removeAllRanges()")
    qtbot.waitUntil(lambda: not window.copy_action.isEnabled())
    js(
        qtbot,
        window,
        "const r = document.createRange(); r.selectNodeContents(document.querySelector('p')); const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);",
    )
    qtbot.waitUntil(lambda: window.copy_action.isEnabled())
    window.copy_action.trigger()
    qtbot.waitUntil(lambda: QApplication.clipboard().text() == "Visible paragraph")
    window.set_display_mode("split")
    ready(qtbot, window)
    assert window.cut_action.isEnabled()
    assert window.delete_action.isEnabled()
    assert window.editor.toPlainText() == "# Heading\n\nVisible paragraph"


def test_boundary_commands_and_new_file_work_in_preview_only(qtbot, window):
    window.set_display_mode("preview")
    ready(qtbot, window)
    window.scroll_to_boundary(True)
    qtbot.wait(120)
    result = metrics(qtbot, window)
    assert result["source"] == window.editor.blockCount() - 1
    assert result["scrollY"] == pytest.approx(result["maxScroll"], abs=2)
    window.scroll_to_boundary(False)
    qtbot.wait(80)
    assert metrics(qtbot, window)["source"] == 0
    window.set_source("# New\n\nFresh document", window.base_dir)
    ready(qtbot, window)
    assert metrics(qtbot, window)["source"] == 0
    assert window.windowTitle() == "無題"
    window.insert_text("changed")
    assert window.windowTitle() == "無題 *"
    window.editor.undo()
    assert window.windowTitle() == "無題"


@pytest.mark.parametrize("mode", ["source", "preview"])
def test_saved_display_mode_and_title_actions_on_startup(qtbot, tmp_path, mode, monkeypatch):
    settings = QSettings(str(tmp_path / "startup.ini"), QSettings.Format.IniFormat)
    settings.setValue("display/mode", mode)
    widget = MainWindow(settings)
    qtbot.addWidget(widget, before_close_func=lambda w: w.editor.document().setModified(False))
    widget.show()
    widget.set_source("# Startup", widget.session.base_dir, update_session=False)
    assert widget.display_mode == mode
    assert widget.editor.isVisible() is (mode == "source")
    assert widget.preview.isVisible() is (mode == "preview")
    if mode == "preview":
        ready(qtbot, widget)
    assert [b.defaultAction() for b in widget.title_bar.file_buttons] == [
        widget.new_action,
        widget.open_action,
        widget.save_action,
        widget.undo_action,
        widget.redo_action,
    ]
    calls = []
    monkeypatch.setattr(widget, "save_document", lambda: calls.append("save"))
    qtbot.mouseClick(widget.title_bar.file_buttons[2], Qt.MouseButton.LeftButton)
    assert calls == ["save"]
    undo, redo = widget.title_bar.file_buttons[3:]
    assert not undo.isEnabled() and not redo.isEnabled()
    widget.insert_text("modified")
    modified = widget.editor.toPlainText()
    assert widget.title_bar.title_label.text() == "無題 *"
    assert "Markdown Editor" not in widget.windowTitle()
    assert undo.isEnabled() and not redo.isEnabled()
    qtbot.mouseClick(undo, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: widget.editor.toPlainText() == "# Startup", timeout=5000)
    assert widget.editor.isVisible()
    assert not undo.isEnabled() and redo.isEnabled()
    assert widget.title_bar.title_label.text() == "無題"
    qtbot.mouseClick(redo, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: widget.editor.toPlainText() == modified, timeout=5000)
    assert undo.isEnabled() and not redo.isEnabled()
    assert widget.title_bar.title_label.text() == "無題 *"


def test_quick_mode_switches_keep_last_choice_and_scroll_target(qtbot, window):
    window.editor.scroll_to_source(65)
    qtbot.wait(100)
    for mode in ("preview", "source", "preview", "split"):
        window.set_display_mode(mode)
    ready(qtbot, window)
    assert window.display_mode == "split"
    assert window.editor.source_position() == pytest.approx(65, abs=0.2)
    assert metrics(qtbot, window)["source"] == pytest.approx(65, abs=0.2)


@pytest.mark.parametrize("supersede", ["document", "mode"])
def test_queued_edit_does_not_affect_replaced_or_hidden_document(qtbot, window, supersede):
    window.set_display_mode("preview")
    ready(qtbot, window)
    window._with_source_visible(lambda: window.insert_text("stale paste"))
    if supersede == "document":
        window.set_source("# Another document", window.base_dir)
    else:
        window.set_display_mode("preview")
    expected = window.editor.toPlainText()
    ready(qtbot, window)
    assert window.editor.toPlainText() == expected
    assert not window.editor.document().isModified()
