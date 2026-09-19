"""Display preferences preserve document state and content-based scrolling."""

import json

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QFontDialog, QInputDialog

from md_editor.app import RESOURCE_DIR, MainWindow


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "display-preferences.ini"), QSettings.Format.IniFormat)


def make_window(qtbot, settings):
    window = MainWindow(settings)
    qtbot.addWidget(window, before_close_func=lambda w: w.editor.document().setModified(False))
    window.resize(1150, 780)
    return window


@pytest.fixture
def window(qtbot, settings):
    widget = make_window(qtbot, settings)
    widget.show()
    paragraph = "折り返して表示される長い段落で、行内の表示位置も維持します。 " * 40
    widget.set_source(
        "# Display preferences\n\n"
        + "\n\n".join(f"{number}: {paragraph}" for number in range(70))
        + "\n\n",
        RESOURCE_DIR,
    )
    ready(qtbot, widget)
    return widget


def ready(qtbot, window):
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    qtbot.waitUntil(lambda: not window._changing_display_mode, timeout=10000)
    # Let WebEngine's deferred layout measurements and scroll events settle.
    qtbot.wait(200)


def js(qtbot, window, script):
    results = []
    window.preview.page().runJavaScript(script, results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=10000)
    return results[0]


def metrics(qtbot, window):
    return json.loads(js(qtbot, window, "JSON.stringify(window.previewApi.metrics())"))


def scroll_preview(qtbot, window, fraction):
    previous = window._preview_position
    js(qtbot, window, f"window.scrollTo(0, window.previewApi.metrics().maxScroll * {fraction})")
    qtbot.waitUntil(lambda: abs(window._preview_position - previous) > 1, timeout=5000)
    qtbot.wait(150)
    return metrics(qtbot, window)["measuredSource"]


def assert_position(qtbot, window, source, preview=None):
    if preview is None:
        preview = source
    # QPlainTextEdit scrolls by visual lines, so a fractional source position
    # can differ by less than one wrapped visual line after a font change.
    assert window.editor.source_position() == pytest.approx(source, abs=0.15)
    result = metrics(qtbot, window)
    assert result["source"] == pytest.approx(preview, abs=0.15)
    assert result["measuredSource"] == pytest.approx(preview, abs=0.15)


def test_font_menu_accept_cancel_and_reset(qtbot, settings, monkeypatch):
    window = make_window(qtbot, settings)
    assert window.source_font_action in window.view_menu.actions()
    chosen = QFont("Yu Gothic", 18)
    chosen.setItalic(True)
    monkeypatch.setattr(QFontDialog, "getFont", lambda *args, **kwargs: (True, chosen))
    window.source_font_action.trigger()
    assert window.editor.font().family() == chosen.family()
    assert window.editor.font().pointSize() == 18
    assert window.editor.font().italic()
    stored = settings.value("display/sourceFont")
    restored = QFont()
    assert restored.fromString(stored)
    assert restored.pointSize() == 18

    monkeypatch.setattr(QFontDialog, "getFont", lambda *args, **kwargs: (False, QFont("Arial", 30)))
    window.source_font_action.trigger()
    assert window.editor.font().pointSize() == 18
    assert settings.value("display/sourceFont") == stored

    window.reset_source_font_action.trigger()
    assert window.editor.font().family() == "Cascadia Mono"
    assert window.editor.font().pointSize() == 11


def test_zoom_menu_accept_and_cancel(qtbot, settings, monkeypatch):
    window = make_window(qtbot, settings)
    assert window.preview_zoom_menu.menuAction() in window.view_menu.actions()
    window.preview_zoom_actions[1.5].trigger()
    assert window.preview.view.zoomFactor() == pytest.approx(1.5)
    assert float(settings.value("display/previewZoom")) == pytest.approx(1.5)
    assert window.preview_zoom_actions[1.5].isChecked()

    monkeypatch.setattr(QInputDialog, "getInt", lambda *args, **kwargs: (135, True))
    window.custom_preview_zoom_action.trigger()
    assert window.preview.view.zoomFactor() == pytest.approx(1.35)
    assert not any(action.isChecked() for action in window.preview_zoom_actions.values())
    assert float(settings.value("display/previewZoom")) == pytest.approx(1.35)

    monkeypatch.setattr(QInputDialog, "getInt", lambda *args, **kwargs: (250, False))
    window.custom_preview_zoom_action.trigger()
    assert window.preview.view.zoomFactor() == pytest.approx(1.35)
    assert float(settings.value("display/previewZoom")) == pytest.approx(1.35)


def test_saved_preferences_restore_and_transient_changes_do_not_persist(qtbot, settings):
    first = make_window(qtbot, settings)
    first.set_source_font(QFont("Yu Gothic", 17))
    first.set_preview_zoom(1.75)
    settings.sync()
    reopened = QSettings(settings.fileName(), QSettings.Format.IniFormat)
    second = make_window(qtbot, reopened)
    assert second.editor.font().family() == "Yu Gothic"
    assert second.editor.font().pointSize() == 17
    assert second.preview.view.zoomFactor() == pytest.approx(1.75)
    assert second.preview_zoom_actions[1.75].isChecked()
    saved_font = reopened.value("display/sourceFont")
    second.set_source_font(QFont("Arial", 22), persist=False)
    second.set_preview_zoom(0.75, persist=False)
    assert reopened.value("display/sourceFont") == saved_font
    assert float(reopened.value("display/previewZoom")) == pytest.approx(1.75)


@pytest.mark.parametrize("zoom", ["invalid", "nan", "inf", 0.1, 6.0])
def test_invalid_saved_preferences_fall_back(qtbot, settings, zoom):
    settings.setValue("display/sourceFont", "invalid-font-data")
    settings.setValue("display/previewZoom", zoom)
    window = make_window(qtbot, settings)
    assert window.editor.font().family() == "Cascadia Mono"
    assert window.editor.font().pointSize() == 11
    assert window.preview.view.zoomFactor() == pytest.approx(1.0)
    assert window.preview_zoom_actions[1.0].isChecked()


def test_changed_font_and_zoom_preserve_position_and_bidirectional_sync(qtbot, window):
    window.editor.scroll_to_source(34.375)
    qtbot.wait(150)
    position = window.editor.source_position()
    assert position > 34
    window.set_source_font(QFont("Yu Gothic", 19))
    ready(qtbot, window)
    assert_position(qtbot, window, position)
    position = window.editor.source_position()
    window.set_preview_zoom(1.75)
    ready(qtbot, window)
    assert_position(qtbot, window, position)

    # Exercise both directions after the layout changes, including a second
    # layout change while the preview's user scroll is authoritative.
    window.editor.scroll_to_source(60.4)
    qtbot.wait(150)
    assert_position(qtbot, window, window.editor.source_position())
    position = scroll_preview(qtbot, window, 0.62)
    assert_position(qtbot, window, position)
    window.set_preview_zoom(0.75)
    ready(qtbot, window)
    assert_position(qtbot, window, position)


def test_disabled_sync_keeps_independent_positions_after_display_changes(qtbot, window):
    window.sync_action.setChecked(False)
    window.editor.scroll_to_source(24.375)
    qtbot.wait(100)
    source = window.editor.source_position()
    preview = scroll_preview(qtbot, window, 0.72)
    assert abs(source - preview) > 20
    window.set_source_font(QFont("Yu Gothic", 19))
    window.set_preview_zoom(1.5)
    ready(qtbot, window)
    assert_position(qtbot, window, source, preview)

    window.editor.scroll_to_source(40.25)
    qtbot.wait(150)
    assert_position(qtbot, window, window.editor.source_position(), preview)
    source = window.editor.source_position()
    preview = scroll_preview(qtbot, window, 0.45)
    assert_position(qtbot, window, source, preview)


@pytest.mark.parametrize("mode", ["source", "preview"])
def test_single_pane_changes_restore_same_content_in_split_mode(qtbot, window, mode):
    window.editor.scroll_to_source(34.375)
    qtbot.wait(150)
    window.set_display_mode(mode)
    ready(qtbot, window)
    position = (
        scroll_preview(qtbot, window, 0.55)
        if mode == "preview"
        else window.editor.source_position()
    )
    window.set_source_font(QFont("Yu Gothic", 19))
    window.set_preview_zoom(1.75)
    qtbot.wait(200)
    window.set_display_mode("split")
    ready(qtbot, window)
    assert_position(qtbot, window, position)


@pytest.mark.parametrize("end", [False, True])
def test_document_boundaries_survive_font_and_zoom_changes(qtbot, window, end):
    window.scroll_to_boundary(end)
    qtbot.wait(150)
    window.set_source_font(QFont("Yu Gothic", 19))
    window.set_preview_zoom(2.0)
    ready(qtbot, window)
    target = window.editor.blockCount() - 1 if end else 0
    assert_position(qtbot, window, target)
    result = metrics(qtbot, window)
    assert result["scrollY"] == pytest.approx(result["maxScroll"] if end else 0, abs=2)


def test_font_and_zoom_leave_selection_text_and_undo_history_intact(qtbot, window):
    original = window.editor.toPlainText()
    window.insert_text("Added text. ")
    cursor = window.editor.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    ready(qtbot, window)
    text = window.editor.toPlainText()
    selection = window.editor.textCursor().selectedText()
    revision = window._revision
    undo_steps = window.editor.document().availableUndoSteps()
    window.set_source_font(QFont("Yu Gothic", 19))
    window.set_preview_zoom(1.75)
    ready(qtbot, window)
    assert window.editor.toPlainText() == text
    assert window.editor.textCursor().selectedText() == selection
    assert window._revision == revision
    assert window.editor.document().availableUndoSteps() == undo_steps
    window.editor.undo()
    assert window.editor.toPlainText() == original
    window.editor.redo()
    assert window.editor.toPlainText() == text
