"""Explicit URL pasting and timestamp insertion preserve selection and undo."""

from datetime import datetime

import pytest
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from PySide6.QtCore import QMimeData, QSettings, QUrl
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from marknotes import interactions
from marknotes.app import MainWindow
from marknotes.clipboard import as_link, clipboard_url
from marknotes.search import qt_position


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/a?b=c#d",
        "http://localhost:8000/",
        "obsidian://open?vault=Notes&file=日本語",
        "vscode://file/C:/notes/readme.md",
        "mailto:person@example.test",
        "custom+notes:page/123",
        "file:///C:/notes/readme.md",
        "https://example.test/a(b))?x=%26copy;&y=1",
    ],
)
def test_clipboard_url_accepts_single_absolute_url(value):
    mime = QMimeData()
    mime.setText(" \t" + value + "\r\n")
    assert clipboard_url(mime) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not a URL",
        "relative/page.md",
        "www.example.test",
        "https://",
        "https:///path",
        "custom:",
        "https://example.test/\nhttps://other.test/",
        "https://example.test/a b",
        "https://example.test/\x00a",
        r"C:\notes\readme.md",
        "C:/notes/readme.md",
        "javascript:alert(1)",
        "data:text/html,test",
        "vbscript:msgbox(1)",
        "about:blank",
        "chrome://settings",
    ],
)
def test_clipboard_url_rejects_non_urls_multiple_urls_and_executable_schemes(value):
    mime = QMimeData()
    mime.setText(value)
    assert clipboard_url(mime) is None


def test_clipboard_url_accepts_one_uri_list_and_rejects_more():
    mime = QMimeData()
    mime.setUrls([QUrl("custom://open/item")])
    assert clipboard_url(mime) == "custom://open/item"
    mime.setUrls([QUrl("https://example.test/a"), QUrl("https://example.test/b")])
    assert clipboard_url(mime) is None
    assert clipboard_url(None) is None
    assert clipboard_url(QMimeData()) is None


@pytest.mark.parametrize(
    "label", ["", "日本語😀 [name] *literal* `code` &copy; <tag> $x$", "line 1\nline 2"]
)
@pytest.mark.parametrize(
    "url",
    ["https://example.test/a(b))?x=%26copy;&copy;=1", "custom://note/123?x=%2F&y=2"],
)
def test_as_link_round_trips_literal_label_and_destination(url, label):
    source = as_link(url, label)
    soup = BeautifulSoup(MarkdownIt().render(source), "html.parser")
    links = soup.find_all("a")
    assert len(links) == 1
    assert links[0]["href"] == url
    assert links[0].get_text() == " ".join((label or url).splitlines())
    assert links[0].find() is None


@pytest.fixture
def window(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "link-timestamp.ini"), QSettings.Format.IniFormat)
    widget = MainWindow(settings)
    qtbot.addWidget(widget, before_close_func=lambda w: w.editor.document().setModified(False))
    widget.show()
    widget.set_source("", widget.base_dir)
    qtbot.waitUntil(lambda: widget._rendered_revision == widget._revision, timeout=15000)
    yield widget
    QApplication.clipboard().clear()


def select(window, source, start, end=None):
    window.set_source(source, window.base_dir)
    cursor = window.editor.textCursor()
    cursor.setPosition(qt_position(source, start))
    if end is not None:
        cursor.setPosition(qt_position(source, end), QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)


@pytest.mark.parametrize("with_selection", [False, True])
def test_timestamp_action_uses_local_time_and_one_undo(window, monkeypatch, with_selection):
    class FixedDatetime:
        @staticmethod
        def now():
            return datetime.fromisoformat("2026-09-16T09:08:07").astimezone()

    monkeypatch.setattr(interactions, "datetime", FixedDatetime)
    source = "前😀 chosen 後"
    start = source.index("chosen")
    end = start + len("chosen") if with_selection else start
    select(window, source, start, end)
    assert window.insert_timestamp_action in window.insert_menu.actions()
    window.insert_timestamp_action.trigger()
    result = source[:start] + "2026-09-16 09:08:07" + source[end:]
    assert window.editor.toPlainText() == result
    window.editor.undo()
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()
    window.editor.redo()
    assert window.editor.toPlainText() == result


@pytest.mark.parametrize("with_selection", [False, True])
def test_paste_link_uses_selection_or_url_and_one_undo(window, with_selection):
    source = "前😀 selected [名前] 後"
    start, end = source.index("selected"), source.index(" 後")
    if not with_selection:
        end = start
    select(window, source, start, end)
    url = "custom://open/item?x=%2F&y=1"
    QApplication.clipboard().setText(url)
    window.refresh_clipboard_actions()
    assert window.link_paste_action in window.paste_format_menu.actions()
    assert window.link_paste_action.isEnabled()
    window.link_paste_action.trigger()
    snippet = as_link(url, source[start:end])
    result = source[:start] + snippet + source[end:]
    assert window.editor.toPlainText() == result
    assert QApplication.clipboard().text() == url
    window.editor.undo()
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()
    window.editor.redo()
    assert window.editor.toPlainText() == result


def test_invalid_clipboard_and_readonly_actions_do_not_change_selection(window):
    select(window, "元の本文", 1, 3)
    cursor = window.editor.textCursor()
    original_selection = (cursor.anchor(), cursor.position())
    QApplication.clipboard().setText("a plain sentence")
    window.refresh_clipboard_actions()
    assert not window.link_paste_action.isEnabled()
    window.paste_link()
    QApplication.clipboard().setText("custom://open/1")
    window.editor.setReadOnly(True)
    window.refresh_edit_actions()
    window.refresh_clipboard_actions()
    assert not window.insert_timestamp_action.isEnabled()
    assert not window.link_paste_action.isEnabled()
    window.insert_timestamp()
    window.paste_link()
    assert window.editor.toPlainText() == "元の本文"
    assert (
        window.editor.textCursor().anchor(),
        window.editor.textCursor().position(),
    ) == original_selection
    assert not window.editor.document().isUndoAvailable()


def test_link_action_tracks_clipboard_and_source_visibility(window, qtbot):
    select(window, "", 0)
    QApplication.clipboard().clear()
    window.refresh_clipboard_actions()
    assert not window.link_paste_action.isEnabled()
    mime = QMimeData()
    mime.setUrls([QUrl("custom://open/item")])
    QApplication.clipboard().setMimeData(mime)
    window.refresh_clipboard_actions()
    assert window.paste_format_menu.isEnabled()
    assert window.link_paste_action.isEnabled()
    window.set_display_mode("preview", persist=False)
    window.link_paste_action.trigger()
    assert window.display_mode == "split"
    qtbot.waitUntil(
        lambda: window.editor.toPlainText() == "[custom://open/item](custom://open/item)"
    )
