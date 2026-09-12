"""Exercise paste/context actions through the real editor and live WebEngine preview."""

import json

import pytest
from markdown_it import MarkdownIt
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QMenu,
    QMessageBox,
    QTableWidgetItem,
)
from shiboken6 import isValid

from md_editor.app import MainWindow
from md_editor.search import qt_position
from md_editor.tables import TableDialog, find_table


@pytest.fixture
def paste_window(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "paste-test.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    qtbot.addWidget(window, before_close_func=lambda w: w.editor.document().setModified(False))
    window.resize(1150, 750)
    window.show()
    window.set_source("", window.session.base_dir)
    wait_rendered(qtbot, window)
    return window


def wait_rendered(qtbot, window):
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)


def javascript(qtbot, window, source):
    values = []
    window.preview.page().runJavaScript(source, values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=10000)
    return values[0]


def set_cursor(window, position, end=None):
    source = window.editor.toPlainText()
    cursor = window.editor.textCursor()
    cursor.setPosition(qt_position(source, position))
    if end is not None:
        cursor.setPosition(qt_position(source, end), QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)


def test_standard_paste_preserves_code_plain_despite_html(qtbot, paste_window):
    window = paste_window
    plain = "def some_function(value_name):\n    return value_name * 2\n"
    mime = QMimeData()
    mime.setText(plain)
    mime.setHtml(
        "<pre><code>def some_function(value_name):\n    return value_name * 2\n</code></pre>"
    )
    QApplication.clipboard().setMimeData(mime)
    window.editor.paste()
    assert window.editor.toPlainText() == plain
    assert "\\_" not in window.editor.toPlainText()
    window.editor.undo()
    assert window.editor.toPlainText() == ""
    window.editor.redo()
    assert window.editor.toPlainText() == plain
    wait_rendered(qtbot, window)


def test_standard_paste_converts_html_article(qtbot, paste_window):
    window = paste_window
    mime = QMimeData()
    mime.setText("記事タイトル\n本文とリンク")
    mime.setHtml(
        '<h1>記事タイトル</h1><p><strong>本文</strong>と<a href="https://example.test/page">リンク</a></p>'
    )
    QApplication.clipboard().setMimeData(mime)
    window.editor.paste()
    assert (
        window.editor.toPlainText()
        == "# 記事タイトル\n\n**本文**と[リンク](https://example.test/page)"
    )
    wait_rendered(qtbot, window)
    assert javascript(qtbot, window, "document.querySelector('h1').textContent") == "記事タイトル"
    assert javascript(qtbot, window, "document.querySelector('strong').textContent") == "本文"


def test_standard_paste_inside_fence_uses_plain(qtbot, paste_window):
    window = paste_window
    original = "```python\n\n```"
    window.set_source(original, window.base_dir)
    set_cursor(window, original.index("\n") + 1)
    mime = QMimeData()
    mime.setText("some_name * literal")
    mime.setHtml("<b>some_name * literal</b>")
    QApplication.clipboard().setMimeData(mime)
    window.editor.paste()
    assert window.editor.toPlainText() == "```python\nsome_name * literal\n```"
    wait_rendered(qtbot, window)


def test_clipboard_image_creates_temporary_png_and_loads_live(qtbot, paste_window):
    window = paste_window
    temporary_base = window.session.base_dir
    assert window.path is None
    image = QImage(43, 27, QImage.Format.Format_ARGB32)
    image.fill(QColor("#2878d0"))
    mime = QMimeData()
    mime.setImageData(image)
    QApplication.clipboard().setMimeData(mime)
    window.editor.paste()
    created = list((temporary_base / "img").glob("*.png"))
    assert len(created) == 1
    assert created[0].name == "untitled_00001.png"
    assert QImage(str(created[0])).size() == image.size()
    assert "img/untitled_00001.png" in window.editor.toPlainText()
    wait_rendered(qtbot, window)
    result = json.loads(
        javascript(
            qtbot,
            window,
            "JSON.stringify(Array.from(document.images).map(i => ({complete:i.complete,w:i.naturalWidth,h:i.naturalHeight,src:i.src})))",
        )
    )
    assert len(result) == 1
    assert result[0]["complete"] and result[0]["w"] == 43 and result[0]["h"] == 27
    assert "untitled_00001.png" in result[0]["src"]
    window.editor.undo()
    assert window.editor.toPlainText() == ""
    assert created[0].exists()
    window.editor.redo()
    assert "img/untitled_00001.png" in window.editor.toPlainText()


def test_image_file_clipboard_copies_source_and_displays(qtbot, paste_window, tmp_path):
    window = paste_window
    original = tmp_path / "日本語 元画像.png"
    image = QImage(31, 19, QImage.Format.Format_RGB32)
    image.fill(QColor("#12ab34"))
    assert image.save(str(original))
    original_bytes = original.read_bytes()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(original))])
    QApplication.clipboard().setMimeData(mime)
    window.editor.paste()
    copies = list((window.session.base_dir / "img").glob("*.png"))
    assert len(copies) == 1
    assert copies[0].read_bytes() == original_bytes
    assert original.read_bytes() == original_bytes
    wait_rendered(qtbot, window)
    assert javascript(
        qtbot, window, "document.images.length === 1 && document.images[0].naturalWidth === 31"
    )


def test_paste_table_accepts_added_header_and_undoes_once(paste_window, monkeypatch):
    window = paste_window
    source = "😀 before\n\nafter"
    window.set_source(source, window.base_dir)
    set_cursor(window, source.index("after"))
    QApplication.clipboard().setText("001\tfirst\n002\tsecond")

    def accepted(dialog):
        assert dialog.rows() == [["001", "first"], ["002", "second"]]
        dialog.header_mode.setCurrentIndex(1)
        dialog.grid.setItem(0, 0, QTableWidgetItem("番号"))
        dialog.grid.setItem(0, 1, QTableWidgetItem("値"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TableDialog, "exec", accepted)
    window.paste_format_menu.aboutToShow.emit()
    assert window.table_paste_action.isEnabled()
    window.table_paste_action.trigger()
    result = window.editor.toPlainText()
    assert result.startswith("😀 before\n\n| 番号 | 値 |\n")
    assert "| 001 | first |" in result
    assert result.endswith("\nafter")
    region = find_table(result, result.index("| 001 |"))
    assert region.rows[-1] == ["002", "second"]  # suffix remains outside the table
    window.editor.undo()
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()


@pytest.mark.parametrize("operation", ["paste", "create"])
def test_insert_table_cancel_preserves_selection_source_and_undo(
    paste_window, monkeypatch, operation
):
    window = paste_window
    source = "😀 chosen suffix"
    window.set_source(source, window.base_dir)
    set_cursor(window, 2, 8)
    cursor = window.editor.textCursor()
    selection = (cursor.selectionStart(), cursor.selectionEnd())
    QApplication.clipboard().setText("A,B\n1,2")
    monkeypatch.setattr(TableDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    getattr(window, f"{operation}_table")()
    assert window.editor.toPlainText() == source
    assert (
        window.editor.textCursor().selectionStart(),
        window.editor.textCursor().selectionEnd(),
    ) == selection
    assert not window.editor.document().isUndoAvailable()


def test_edit_table_preserves_nested_context_surroundings_and_undo(paste_window, monkeypatch):
    window = paste_window
    source = "😀 before\n\n> - |A|B|\n>   |:---|---:|\n>   |001|old|\n\nafter"
    window.set_source(source, window.base_dir)
    set_cursor(window, source.index("old"))

    def accepted(dialog):
        assert dialog.rows() == [["A", "B"], ["001", "old"]]
        assert dialog.alignments() == ["left", "right"]
        dialog.grid.setItem(1, 1, QTableWidgetItem("new\nline"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TableDialog, "exec", accepted)
    window.edit_table()
    result = window.editor.toPlainText()
    assert result.startswith("😀 before\n\n> - | A | B |\n>   | :--- | ---: |\n")
    assert ">   | 001 | new<br>line |" in result
    assert result.endswith("\n\nafter")
    window.editor.undo()
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()


def test_edit_table_cancel_preserves_original_formatting(paste_window, monkeypatch):
    window = paste_window
    source = "before\n\nA   |  B\n:---|---:\n001 | old\n\nafter"
    window.set_source(source, window.base_dir)
    set_cursor(window, source.index("old"))
    monkeypatch.setattr(TableDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    window.edit_table()
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()


@pytest.mark.parametrize("operation", ["paste", "edit", "create"])
def test_table_dialog_rejects_stale_document_revision(qtbot, paste_window, monkeypatch, operation):
    window = paste_window
    source = "|A|B|\n|-|-|\n|001|old|"
    window.set_source(source, window.base_dir)
    set_cursor(window, source.index("old"))
    QApplication.clipboard().setText("X,Y\n2,3")
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))

    dialogs = []

    def accepted_after_external_edit(dialog):
        dialogs.append(dialog)
        set_cursor(window, len(window.editor.toPlainText()))
        window.insert_text("\n\nexternal edit")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TableDialog, "exec", accepted_after_external_edit)
    getattr(window, f"{operation}_table")()
    assert window.editor.toPlainText() == source + "\n\nexternal edit"
    assert len(messages) == 1
    qtbot.waitUntil(lambda: not isValid(dialogs[0]))


@pytest.mark.parametrize(
    "source,table_present",
    [
        ("😀 before\n\n|A|B|\n|-|-|\n|1|2|", True),
        ("😀 ordinary | inline | text", False),
        ("```\n|A|B|\n|-|-|\n|1|2|\n```", False),
    ],
)
def test_context_menu_detects_only_real_table(paste_window, monkeypatch, source, table_present):
    window = paste_window
    window.set_source(source, window.base_dir)
    index = source.index("|1|") if "|1|" in source else source.index("inline")
    set_cursor(window, index)
    clicked_position = window.editor.cursorRect().center()
    set_cursor(window, 0)  # table actions must use the right-click target, not the caret
    QApplication.clipboard().setText("A\tB\n1\t2")
    menus = []
    targets = []
    monkeypatch.setattr(window, "edit_table", targets.append)

    def capture_menu():
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            try:
                actions = [action for action in menu.actions() if not action.isSeparator()]
                submenu = next(
                    action.menu() for action in actions if action.menu() is window.paste_format_menu
                )
                insertion = next(
                    action.menu() for action in actions if action.menu() is window.insert_menu
                )
                menus.append(
                    {
                        "actions": {action.text(): action.isEnabled() for action in actions},
                        "shared_submenu": submenu is window.paste_format_menu,
                        "shared_insert": insertion is window.insert_menu,
                        "insert_actions": insertion.actions(),
                        "formats": {
                            action.text(): action.isEnabled() for action in submenu.actions()
                        },
                    }
                )
                if table_present:
                    next(action for action in actions if action.text() == "表を編集…").trigger()
            finally:
                menu.close()

    QTimer.singleShot(0, capture_menu)
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse,
        clicked_position,
        window.editor.mapToGlobal(clicked_position),
    )
    window.editor.contextMenuEvent(event)
    assert menus
    # The application and context menus share the actual submenu and actions.
    expected = [
        action.text() for action in window.edit_menu.actions() if action.property("sourceContext")
    ]
    assert list(menus[0]["actions"]) == (
        expected
        + [window.insert_menu.title(), "文字の書式", "行・ブロックの書式"]
        + (["表を編集…"] if table_present else [])
    )
    assert menus[0]["shared_insert"]
    assert menus[0]["insert_actions"] == window.insert_menu.actions()
    assert menus[0]["shared_submenu"]
    assert menus[0]["formats"] == {
        "プレーンテキストとして貼り付け": True,
        "Markdownとして貼り付け": True,
        "コードブロックとして貼り付け": True,
        "引用として貼り付け": True,
        "表として貼り付け…": True,
    }
    actions = menus[0]["actions"]
    assert not actions["コピー"]
    assert not actions["切り取り"]
    assert actions["すべて選択"]
    assert list(actions).index("形式を指定して貼り付け") == list(actions).index("貼り付け") + 1
    assert all(label not in actions for label in menus[0]["formats"])
    assert "表を編集…" not in [action.text() for action in window.edit_menu.actions()]
    assert "表として貼り付け…" not in [action.text() for action in window.insert_menu.actions()]
    if table_present:
        assert len(targets) == 1
        assert targets[0].rows == [["A", "B"], ["1", "2"]]
        assert targets[0].start <= index < targets[0].end
    else:
        assert not targets
    assert (find_table(source, index) is not None) is table_present


def test_explicit_markdown_submenu_action_converts_html_and_preserves_plain(qtbot, paste_window):
    window = paste_window
    mime = QMimeData()
    mime.setText("# original plain Markdown")
    mime.setHtml("<h2>HTML heading</h2><p><strong>HTML body</strong></p>")
    QApplication.clipboard().setMimeData(mime)
    window.paste_format_menu.aboutToShow.emit()
    markdown = next(
        action
        for action in window.paste_format_menu.actions()
        if action.text() == "Markdownとして貼り付け"
    )
    assert markdown.isEnabled()
    markdown.trigger()
    assert window.editor.toPlainText() == "## HTML heading\n\n**HTML body**"
    window.editor.undo()
    assert window.editor.toPlainText() == ""
    # The existing plain shortcut still reaches the shared action in the submenu.
    window.activateWindow()
    window.editor.setFocus()
    qtbot.wait(30)
    qtbot.keyClick(
        window.editor,
        Qt.Key.Key_V,
        modifier=Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert window.editor.toPlainText() == "# original plain Markdown"
    window.editor.undo()
    QApplication.clipboard().setText("some_name * literal\n  indentation")
    window.paste_format_menu.aboutToShow.emit()
    assert markdown.isEnabled()
    markdown.trigger()
    assert window.editor.toPlainText() == "some_name * literal\n  indentation"


def test_format_paste_submenu_disables_unavailable_formats(paste_window):
    window = paste_window
    mime = QMimeData()
    mime.setHtml("<b>HTML only</b>")
    QApplication.clipboard().setMimeData(mime)
    window.refresh_clipboard_actions()
    assert not window.plain_paste_action.isEnabled()
    assert window.html_paste_action.isEnabled()
    assert not window.table_paste_action.isEnabled()
    assert not window.code_paste_action.isEnabled()
    assert window.quote_paste_action.isEnabled()
    assert window.paste_format_menu.isEnabled()
    window.editor.setReadOnly(True)
    window.refresh_clipboard_actions()
    assert not window.paste_format_menu.isEnabled()
    assert not any(action.isEnabled() for action in window.paste_format_menu.actions())
    window.editor.setReadOnly(False)
    QApplication.clipboard().clear()
    window.refresh_clipboard_actions()
    assert not window.paste_format_menu.isEnabled()
    QApplication.clipboard().setText("A\tB\n1\t2")
    window.refresh_clipboard_actions()
    assert window.paste_format_menu.isEnabled()
    assert all(action.isEnabled() for action in window.paste_format_menu.actions())


def test_standard_paste_preserves_and_renders_inline_tex(qtbot, paste_window):
    window = paste_window
    plain = r"式 $ x_i $ と \(\frac{a_i}{b}\) です。"
    mime = QMimeData()
    mime.setText(plain)
    mime.setHtml(f"<p><strong>{plain}</strong></p>")
    QApplication.clipboard().setMimeData(mime)
    window.editor.paste()
    wait_rendered(qtbot, window)
    assert window.editor.toPlainText() == plain
    assert json.loads(
        javascript(
            qtbot,
            window,
            "JSON.stringify(Array.from(document.querySelectorAll('.katex annotation')).map(n => n.textContent))",
        )
    ) == [" x_i ", r"\frac{a_i}{b}"]
    assert javascript(qtbot, window, "document.querySelectorAll('.render-error').length") == 0
    window.editor.undo()
    assert window.editor.toPlainText() == ""
    window.editor.redo()
    wait_rendered(qtbot, window)
    assert window.editor.toPlainText() == plain
    assert javascript(qtbot, window, "document.querySelectorAll('.katex').length") == 2


@pytest.mark.parametrize("source,start,end", [("", 0, 0), ("😀 before selected after", 9, 18)])
def test_create_table_replaces_captured_selection_and_undoes_once(
    paste_window, monkeypatch, source, start, end
):
    window = paste_window
    window.set_source(source, window.base_dir)
    set_cursor(window, start, end)

    def accepted(dialog):
        assert dialog.windowTitle() == "表を作成"
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text() == "OK"
        assert dialog.rows() == [["", ""], ["", ""], ["", ""]]
        assert dialog.header_mode.isHidden() and dialog.delimiter_combo.isHidden()
        dialog.grid.setItem(0, 0, QTableWidgetItem("番号"))
        dialog.grid.setItem(0, 1, QTableWidgetItem("内容"))
        dialog.grid.setItem(1, 0, QTableWidgetItem("001"))
        dialog.grid.setItem(1, 1, QTableWidgetItem("first"))
        # A programmatic caret move while the modal dialog is open must not
        # redirect the insertion away from the user's original selection.
        set_cursor(window, 0)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TableDialog, "exec", accepted)
    window.create_table_action.trigger()
    result = window.editor.toPlainText()
    region = find_table(result, result.index("| 001 |"))
    assert region is not None
    assert region.rows == [["番号", "内容"], ["001", "first"], ["", ""]]
    assert result[: region.start] == (source[:start] + "\n\n" if start else "")
    assert result[region.end :] == ("\nafter" if end < len(source) else "")
    window.editor.undo()
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()


def test_context_insertion_uses_application_table_callback(paste_window, monkeypatch):
    window = paste_window
    source = "before after"
    window.set_source(source, window.base_dir)
    set_cursor(window, 7, 12)
    clicked_position = window.editor.cursorRect().center()
    created = []

    def accepted(dialog):
        created.append(dialog.windowTitle())
        dialog.grid.setItem(0, 0, QTableWidgetItem("Header"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TableDialog, "exec", accepted)

    def activate_insertion():
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            try:
                shared = next(a.menu() for a in menu.actions() if a.menu() is window.insert_menu)
                action = next(a for a in shared.actions() if a is window.create_table_action)
                action.trigger()
            finally:
                menu.close()

    QTimer.singleShot(0, activate_insertion)
    window.editor.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            clicked_position,
            window.editor.mapToGlobal(clicked_position),
        )
    )
    assert created == ["表を作成"]
    assert window.editor.toPlainText().startswith("before \n\n| Header |")
    window.editor.undo()
    assert window.editor.toPlainText() == source


@pytest.mark.parametrize("operation", ["paste", "create"])
def test_table_insertion_readonly_does_not_open_dialog(paste_window, monkeypatch, operation):
    window = paste_window
    window.editor.setReadOnly(True)
    window.refresh_edit_actions()
    QApplication.clipboard().setText("A,B\n1,2")
    opened = []
    monkeypatch.setattr(TableDialog, "exec", lambda _: opened.append(True))
    getattr(window, f"{operation}_table")()
    assert not opened
    assert not window.insert_menu.isEnabled()
    assert window.editor.toPlainText() == ""


def test_repeated_context_menus_preserve_shared_submenus_after_deletion(paste_window, monkeypatch):
    window = paste_window
    window.set_source("before", window.base_dir)
    set_cursor(window, len("before"))
    QApplication.clipboard().setText("A,B\n1,2")
    created = []

    def accept_table(dialog):
        created.append(dialog.windowTitle())
        dialog.grid.setItem(0, 0, QTableWidgetItem("Header"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TableDialog, "exec", accept_table)
    shared = (window.insert_menu, window.paste_format_menu)
    for attempt in range(3):
        popups = []

        def close_popup(popups=popups, attempt=attempt):
            menu = QApplication.activePopupWidget()
            if isinstance(menu, QMenu):
                popups.append(menu)
                try:
                    assert all(submenu.menuAction() in menu.actions() for submenu in shared)
                    if attempt:
                        window.create_table_action.trigger()
                finally:
                    menu.close()

        position = window.editor.cursorRect().center()
        QTimer.singleShot(0, close_popup)
        window.editor.contextMenuEvent(
            QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse,
                position,
                window.editor.mapToGlobal(position),
            )
        )
        assert len(popups) == 1
        # Closing the popup only schedules deletion. Flush it before checking
        # the persistent menus, as happens before the user's next right-click.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(popups[0])
        assert all(isValid(submenu) for submenu in shared)
        assert window.insert_menu.menuAction() in window.menuBar().actions()
        assert window.paste_format_menu.menuAction() in window.edit_menu.actions()
        window.refresh_edit_actions()
        window.refresh_clipboard_actions()
        if attempt:
            assert "| Header |" in window.editor.toPlainText()
            window.editor.undo()
            assert window.editor.toPlainText() == "before"

    # The application menu must still invoke the same command afterwards.
    window.create_table_action.trigger()
    assert created == ["表を作成"] * 3
    assert "| Header |" in window.editor.toPlainText()
    window.editor.undo()
    assert window.editor.toPlainText() == "before"


def test_code_paste_detects_python_preserves_plain_selection_and_single_undo(qtbot, paste_window):
    window = paste_window
    source = "😀 before selected after"
    window.set_source(source, window.base_dir)
    set_cursor(window, source.index("selected"), source.index("selected") + len("selected"))
    plain = "def some_function(value_name):\n    return value_name * 2"
    mime = QMimeData()
    mime.setText(plain)
    mime.setHtml("<p><strong>wrong HTML text</strong></p>")
    QApplication.clipboard().setMimeData(mime)
    window.refresh_clipboard_actions()
    window.code_paste_action.trigger()
    result = window.editor.toPlainText()
    assert result.startswith("😀 before \n\n") and result.endswith("\n\n after")
    blocks = [token for token in MarkdownIt("commonmark").parse(result) if token.type == "fence"]
    assert len(blocks) == 1
    assert blocks[0].info == "python" and blocks[0].content == plain + "\n"
    wait_rendered(qtbot, window)
    assert (
        javascript(qtbot, window, "document.querySelector('pre code').className")
        == "language-python"
    )
    assert json.loads(
        javascript(
            qtbot,
            window,
            "JSON.stringify(Array.from(document.querySelectorAll('pre .code-line'), n => n.textContent))",
        )
    ) == plain.split("\n")
    window.editor.undo()
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()
    window.editor.redo()
    assert window.editor.toPlainText() == result


def test_code_paste_in_existing_code_does_not_add_fences_or_change_language(paste_window):
    window = paste_window
    source = "```javascript\n\n```"
    window.set_source(source, window.base_dir)
    set_cursor(window, source.index("\n") + 1)
    QApplication.clipboard().setText("const value = 42;")
    window.refresh_clipboard_actions()
    window.code_paste_action.trigger()
    assert window.editor.toPlainText() == "```javascript\nconst value = 42;\n```"
    window.editor.undo()
    assert window.editor.toPlainText() == source


def test_quote_paste_keeps_paragraphs_and_nested_quote_separate_from_surroundings(
    qtbot, paste_window
):
    window = paste_window
    window.set_source("beforeafter", window.base_dir)
    set_cursor(window, len("before"))
    mime = QMimeData()
    mime.setText("引用の一段落\r\n\r\n**二段落**\r\n> nested")
    mime.setHtml("<p>wrong HTML</p>")
    QApplication.clipboard().setMimeData(mime)
    window.refresh_clipboard_actions()
    window.quote_paste_action.trigger()
    result = "before\n\n> 引用の一段落\n>\n> **二段落**\n> > nested\n\nafter"
    assert window.editor.toPlainText() == result
    wait_rendered(qtbot, window)
    assert (
        javascript(qtbot, window, "document.querySelectorAll('#content > blockquote').length") == 1
    )
    assert (
        javascript(qtbot, window, "document.querySelector('blockquote strong').textContent")
        == "二段落"
    )
    assert (
        javascript(
            qtbot, window, "document.querySelector('blockquote blockquote').textContent.trim()"
        )
        == "nested"
    )
    assert (
        javascript(qtbot, window, "document.querySelector('#content > :last-child').textContent")
        == "after"
    )
    window.editor.undo()
    assert window.editor.toPlainText() == "beforeafter"
    assert not window.editor.document().isUndoAvailable()


def test_quote_paste_converts_html_when_plain_is_absent(qtbot, paste_window):
    window = paste_window
    mime = QMimeData()
    mime.setHtml("<p><strong>HTML only</strong></p>")
    QApplication.clipboard().setMimeData(mime)
    window.refresh_clipboard_actions()
    assert not window.code_paste_action.isEnabled()
    assert window.quote_paste_action.isEnabled()
    window.refresh_clipboard_actions()
    window.quote_paste_action.trigger()
    assert window.editor.toPlainText() == "> **HTML only**"
    wait_rendered(qtbot, window)
    assert (
        javascript(qtbot, window, "document.querySelector('blockquote strong').textContent")
        == "HTML only"
    )


@pytest.mark.parametrize("kind", ["code", "quote"])
def test_block_paste_reveals_source_from_preview_and_respects_readonly(qtbot, paste_window, kind):
    window = paste_window
    raw = "単なる文章です。"
    QApplication.clipboard().setText(raw)
    window.set_display_mode("preview")
    qtbot.waitUntil(lambda: not window._changing_display_mode)
    window.refresh_clipboard_actions()
    getattr(window, f"{kind}_paste_action").trigger()
    qtbot.waitUntil(lambda: window.editor.toPlainText() != "")
    assert window.display_mode == "split"
    assert window.editor.isVisible()
    tokens = MarkdownIt("commonmark").parse(window.editor.toPlainText())
    if kind == "code":
        assert tokens[0].type == "fence" and tokens[0].info == ""
        assert tokens[0].content == raw + "\n"
    else:
        assert tokens[0].type == "blockquote_open"
    window.editor.undo()
    assert window.editor.toPlainText() == ""
    window.editor.setReadOnly(True)
    window.refresh_clipboard_actions()
    assert not getattr(window, f"{kind}_paste_action").isEnabled()
    getattr(window, "paste_code_block" if kind == "code" else "paste_quote")()
    assert window.editor.toPlainText() == ""


@pytest.mark.parametrize("kind", ["code", "quote"])
def test_block_paste_empty_or_image_clipboard_does_not_remove_selection(paste_window, kind):
    window = paste_window
    window.set_source("keep this", window.base_dir)
    window.editor.selectAll()
    for text in (None, ""):
        mime = QMimeData()
        if text is not None:
            mime.setText(text)
        QApplication.clipboard().setMimeData(mime)
        getattr(window, "paste_code_block" if kind == "code" else "paste_quote")()
        assert window.editor.toPlainText() == "keep this"
    image = QMimeData()
    image.setImageData(QImage(2, 2, QImage.Format.Format_ARGB32))
    QApplication.clipboard().setMimeData(image)
    window.refresh_clipboard_actions()
    assert not getattr(window, f"{kind}_paste_action").isEnabled()
    getattr(window, "paste_code_block" if kind == "code" else "paste_quote")()
    assert window.editor.toPlainText() == "keep this"


@pytest.mark.parametrize("backwards", [False, True])
def test_code_body_selection_retains_closing_fence_for_both_selection_directions(
    paste_window, backwards
):
    window = paste_window
    source = "```python\nold\n```\n\nafter"
    window.set_source(source, window.base_dir)
    start, end = source.index("old"), source.index("old") + len("old\n")
    set_cursor(window, end if backwards else start, start if backwards else end)
    QApplication.clipboard().setText("new\n```\nstill code")
    window.refresh_clipboard_actions()
    window.code_paste_action.trigger()
    result = window.editor.toPlainText()
    tokens = MarkdownIt("commonmark").parse(result)
    assert tokens[0].type == "fence" and tokens[0].info == "python"
    assert tokens[0].content == "new\n```\nstill code\n"
    assert [token.content for token in tokens if token.type == "inline"] == ["after"]
    assert result.count("````") == 2
    window.editor.undo()
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()


@pytest.mark.parametrize("source", ["    old", "```python\nold\n```"])
def test_replacing_entire_code_container_creates_a_new_detected_fence(paste_window, source):
    window = paste_window
    window.set_source(source, window.base_dir)
    window.editor.selectAll()
    QApplication.clipboard().setText('{"value": 42}')
    window.refresh_clipboard_actions()
    window.code_paste_action.trigger()
    tokens = MarkdownIt("commonmark").parse(window.editor.toPlainText())
    assert len(tokens) == 1 and tokens[0].type == "fence"
    assert tokens[0].info == "json" and tokens[0].content == '{"value": 42}\n'
    window.editor.undo()
    assert window.editor.toPlainText() == source
