"""Native WebEngine checks for source mapping and scroll feedback suppression."""

import json
from itertools import pairwise

import pytest
from PySide6.QtGui import QColor, QImage

from marknotes.preview import PreviewPane
from marknotes.rendering import render_markdown


@pytest.fixture
def preview(qtbot):
    pane = PreviewPane()
    pane.resize(720, 440)
    qtbot.addWidget(pane)
    pane.show()
    qtbot.waitUntil(lambda: pane._shell_ready, timeout=15000)
    return pane


def evaluate(qtbot, pane, expression):
    results = []
    pane.page().runJavaScript(f"JSON.stringify({expression})", results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    return json.loads(results[0])


def metrics(qtbot, pane):
    return evaluate(qtbot, pane, "window.previewApi.metrics()")


def load_document(qtbot, pane, source, base_dir, revision=1):
    rendered = render_markdown(source)
    with qtbot.waitSignal(pane.ready, timeout=10000):
        pane.set_document(rendered.html, rendered.line_count, base_dir, revision)
    return rendered


def wait_position(qtbot, pane, position):
    qtbot.waitUntil(
        lambda: abs(metrics(qtbot, pane)["source"] - position) < 0.01,
        timeout=5000,
    )
    return metrics(qtbot, pane)


def test_measured_content_map_and_last_line_reaches_top(qtbot, preview, tmp_path):
    source = "\n".join(
        [
            "# Heading",
            "",
            "A wrapped paragraph " * 35,
            "",
            "- First item",
            "- Second item",
            "",
            "```python",
            "x = 1",
            "",
            "print(x)",
            "```",
            "",
            "| Name | Value |",
            "| --- | --- |",
            "| A | 1 |",
            "| B | 2 |",
            "",
            "## Another heading",
            "",
            "Last paragraph",
            "",
            "",
        ]
    )
    rendered = load_document(qtbot, preview, source, tmp_path)
    info = metrics(qtbot, preview)
    anchors = info["anchors"]
    assert all(a["source"] < b["source"] for a, b in pairwise(anchors))
    assert all(a["y"] < b["y"] for a, b in pairwise(anchors))
    # The lengthy paragraph makes the measured map different from a simple
    # scrollbar percentage or a fixed-height logical-line approximation.
    intervals = [(b["y"] - a["y"]) / (b["source"] - a["source"]) for a, b in pairwise(anchors)]
    assert max(intervals) > min(intervals) * 5
    for position in (0, 4, 9.5, rendered.line_count - 1):
        preview.scroll_to_source(position, 1)
        current = wait_position(qtbot, preview, position)
        assert abs(current["measuredSource"] - position) <= 0.55
    assert abs(current["scrollY"] - current["maxScroll"]) <= 1
    assert current["anchors"][-1]["source"] == rendered.line_count - 1


@pytest.mark.parametrize("source", ["", "final", "\n\n\n", "# Heading\n\n\n\n"])
def test_empty_and_trailing_blank_lines_have_scrollable_eof(qtbot, preview, tmp_path, source):
    rendered = load_document(qtbot, preview, source, tmp_path)
    last_line = rendered.line_count - 1
    preview.scroll_to_source(last_line, 1)
    info = wait_position(qtbot, preview, last_line)
    assert abs(info["scrollY"] - info["maxScroll"]) <= 1
    assert info["measuredSource"] == pytest.approx(last_line, abs=0.03)


def test_programmatic_scroll_is_silent_but_browser_scroll_reports(qtbot, preview, tmp_path):
    source = "\n\n".join(f"Paragraph {index}" for index in range(50))
    load_document(qtbot, preview, source, tmp_path)
    events = []
    preview.source_scrolled.connect(lambda position, revision: events.append((position, revision)))
    preview.scroll_to_source(30, 1)
    wait_position(qtbot, preview, 30)
    qtbot.wait(100)  # Deliver the browser's asynchronous scroll event.
    assert not events
    preview.page().runJavaScript("window.scrollTo(0, 200)")
    qtbot.waitUntil(lambda: bool(events), timeout=5000)
    assert events[-1][1] == 1
    assert events[-1][0] != pytest.approx(30)
    assert events[-1][0] == pytest.approx(metrics(qtbot, preview)["measuredSource"], abs=0.05)


def test_old_revisions_cannot_replace_or_scroll_new_document(qtbot, preview, tmp_path):
    source = "\n\n".join(f"Paragraph {index}" for index in range(30))
    rendered = load_document(qtbot, preview, source, tmp_path, revision=7)
    preview.scroll_to_source(20, 7)
    wait_position(qtbot, preview, 20)
    preview.set_document("old", 1, tmp_path, 6)
    preview.scroll_to_source(0, 6)
    assert evaluate(qtbot, preview, "window.previewApi.scrollToSource(0, 6)") is False
    assert (
        evaluate(
            qtbot, preview, "window.previewApi.setDocument({html:'old',lineCount:1,revision:6})"
        )
        is False
    )
    info = metrics(qtbot, preview)
    assert info["revision"] == 7
    assert info["lineCount"] == rendered.line_count
    assert info["source"] == pytest.approx(20)


def test_local_image_and_resize_preserve_source_position(qtbot, preview, tmp_path):
    image = QImage(640, 320, QImage.Format.Format_RGB32)
    image.fill(QColor("#7595c9"))
    assert image.save(str(tmp_path / "sample image.png"))
    source = "\n".join(
        [
            "# Images",
            "",
            "![local image](sample%20image.png)",
            "",
            "Wrapped content " * 60,
            "",
            "## Target",
            "",
            "After image",
            "",
            "",
        ]
    )
    load_document(qtbot, preview, source, tmp_path)
    qtbot.waitUntil(
        lambda: evaluate(qtbot, preview, "document.querySelector('img').naturalWidth") == 640,
        timeout=5000,
    )
    preview.scroll_to_source(6, 1)
    before = wait_position(qtbot, preview, 6)
    events = []
    preview.source_scrolled.connect(lambda *args: events.append(args))
    tall_image = QImage(640, 900, QImage.Format.Format_RGB32)
    tall_image.fill(QColor("#c98675"))
    assert tall_image.save(str(tmp_path / "tall.png"))
    preview.page().runJavaScript(
        "document.querySelector('img').src = " + json.dumps((tmp_path / "tall.png").as_uri())
    )
    qtbot.waitUntil(
        lambda: evaluate(qtbot, preview, "document.querySelector('img').naturalHeight") == 900,
        timeout=5000,
    )
    qtbot.waitUntil(
        lambda: metrics(qtbot, preview)["anchors"][-1]["y"] > before["anchors"][-1]["y"] + 100,
        timeout=5000,
    )
    loaded = metrics(qtbot, preview)
    assert loaded["source"] == pytest.approx(6, abs=0.01)
    assert loaded["measuredSource"] == pytest.approx(6, abs=0.05)
    assert not events
    before = loaded
    preview.resize(410, 540)
    qtbot.waitUntil(
        lambda: abs(metrics(qtbot, preview)["anchors"][-1]["y"] - before["anchors"][-1]["y"]) > 10,
        timeout=5000,
    )
    after = metrics(qtbot, preview)
    assert after["source"] == pytest.approx(6, abs=0.01)
    assert after["measuredSource"] == pytest.approx(6, abs=0.05)
    assert not events


def test_viewport_height_change_at_eof_keeps_last_line(qtbot, preview, tmp_path):
    rendered = load_document(
        qtbot, preview, "\n\n".join(f"Paragraph {index}" for index in range(40)), tmp_path
    )
    last = rendered.line_count - 1
    preview.scroll_to_source(last, 1)
    before = wait_position(qtbot, preview, last)
    preview.resize(720, 720)
    qtbot.waitUntil(
        lambda: metrics(qtbot, preview)["viewportHeight"] != before["viewportHeight"],
        timeout=5000,
    )
    after = metrics(qtbot, preview)
    assert after["source"] == pytest.approx(last, abs=0.01)
    assert abs(after["scrollY"] - after["maxScroll"]) <= 1


@pytest.mark.parametrize("source", ["final", "# Final heading", "- Final item", "```\ncode\n```"])
def test_short_final_line_keeps_its_top_as_scroll_limit(qtbot, preview, tmp_path, source):
    rendered = load_document(qtbot, preview, source, tmp_path)
    info = metrics(qtbot, preview)
    assert info["maxSource"] == pytest.approx(rendered.line_count - 1, abs=0.001)
    preview.scroll_to_source(rendered.line_count - 1, 1)
    info = wait_position(qtbot, preview, rendered.line_count - 1)
    assert abs(info["scrollY"] - info["maxScroll"]) <= 1


@pytest.mark.parametrize("prefix", ["", "# Heading\n\nShort paragraph\n\n"])
def test_wrapped_final_line_supports_fractional_scroll(qtbot, preview, tmp_path, prefix):
    rendered = load_document(qtbot, preview, prefix + "long wrapped content " * 200, tmp_path)
    info = metrics(qtbot, preview)
    last = rendered.line_count - 1
    assert last + 0.5 < info["maxSource"] < rendered.line_count
    assert info["maxScroll"] > info["viewportHeight"]
    preview.scroll_to_source(last + 0.5, 1)
    middle = wait_position(qtbot, preview, last + 0.5)
    assert middle["measuredSource"] == pytest.approx(last + 0.5, abs=0.003)
    assert middle["scrollY"] > 100
    events = []
    preview.source_scrolled.connect(lambda position, revision: events.append((position, revision)))
    preview.page().runJavaScript("window.scrollTo(0, document.documentElement.scrollHeight)")
    qtbot.waitUntil(lambda: bool(events), timeout=5000)
    bottom = metrics(qtbot, preview)
    assert events[-1][0] == pytest.approx(bottom["maxSource"], abs=0.003)
    assert bottom["measuredSource"] == pytest.approx(bottom["maxSource"], abs=0.003)


def test_tall_image_on_final_line_can_scroll_inside_the_image(qtbot, preview, tmp_path):
    image = QImage(320, 1600, QImage.Format.Format_RGB32)
    image.fill(QColor("#7595c9"))
    assert image.save(str(tmp_path / "tall-final.png"))
    load_document(qtbot, preview, "![tall](tall-final.png)", tmp_path)
    qtbot.waitUntil(
        lambda: evaluate(qtbot, preview, "document.querySelector('img').naturalHeight") == 1600,
        timeout=5000,
    )
    qtbot.waitUntil(lambda: metrics(qtbot, preview)["maxSource"] > 0.8, timeout=5000)
    preview.scroll_to_source(0.5, 1)
    middle = wait_position(qtbot, preview, 0.5)
    assert middle["scrollY"] > 600
    assert middle["measuredSource"] == pytest.approx(0.5, abs=0.003)
    preview.scroll_to_source(0, 1)
    assert wait_position(qtbot, preview, 0)["scrollY"] == 0


def test_rendered_table_column_alignment(qtbot, preview, tmp_path):
    load_document(qtbot, preview, "| A | B | C |\n| :--- | :---: | ---: |\n| 1 | 2 | 3 |", tmp_path)
    assert evaluate(
        qtbot,
        preview,
        "Array.from(document.querySelectorAll('th')).map(el => getComputedStyle(el).textAlign)",
    ) == ["left", "center", "right"]


def test_qt_standard_text_menu_translation_is_installed_only_once(qtbot, qapp):
    from PySide6.QtCore import QTranslator
    from PySide6.QtWidgets import QLineEdit

    from marknotes.localization import install_japanese_translation

    assert install_japanese_translation(qapp)
    first = qapp.findChild(QTranslator, "mdEditorQtJapaneseTranslator")
    assert first is not None
    assert install_japanese_translation(qapp)
    assert qapp.findChildren(QTranslator, "mdEditorQtJapaneseTranslator") == [first]
    field = QLineEdit("text")
    qtbot.addWidget(field)
    menu = field.createStandardContextMenu()
    try:
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        assert any("コピー" in label for label in labels)
        assert any("貼り付け" in label for label in labels)
        assert all("Copy" not in label and "Paste" not in label for label in labels)
    finally:
        menu.deleteLater()


def _invoke_preview_context(qtbot, preview, monkeypatch, selector, trigger=None):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QApplication, QMenu

    point = evaluate(
        qtbot,
        preview,
        "(() => { const r=document.querySelector("
        + json.dumps(selector)
        + ").getBoundingClientRect();return [Math.round(r.left+8),Math.round(r.top+8)];})()",
    )
    target = preview.view.focusProxy()
    assert target is not None
    position = QPoint(*point)
    qtbot.mouseClick(target, Qt.MouseButton.RightButton, pos=position)
    QApplication.sendEvent(
        target,
        QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position, target.mapToGlobal(position)),
    )
    qtbot.waitUntil(
        lambda: bool(preview.view.findChildren(QMenu, "previewContextMenu")), timeout=5000
    )
    menu = preview.view.findChild(QMenu, "previewContextMenu")
    try:
        actions = [action for action in menu.actions() if not action.isSeparator()]
        observed = [(action.text(), action.isEnabled()) for action in actions]
        if trigger:
            action = next(action for action in actions if action.text() == trigger)
            assert action.isEnabled()
            action.trigger()
    finally:
        menu.close()
    qtbot.waitUntil(
        lambda: not preview.view.findChildren(QMenu, "previewContextMenu"), timeout=5000
    )
    return observed


def test_preview_context_menu_uses_preview_selection_and_japanese_edit_order(
    qtbot, preview, tmp_path, monkeypatch, qapp
):
    load_document(qtbot, preview, "Preview selected text", tmp_path)
    preview.page().runJavaScript(
        "(() => {const r=document.createRange();r.selectNodeContents(document.querySelector('p'));"
        "const s=window.getSelection();s.removeAllRanges();s.addRange(r);})()"
    )
    qtbot.waitUntil(lambda: preview.page().selectedText() == "Preview selected text", timeout=5000)
    qapp.clipboard().clear()
    actions = _invoke_preview_context(qtbot, preview, monkeypatch, "p", "コピー")
    assert [label for label, _ in actions] == ["コピー", "すべて選択"]
    qtbot.waitUntil(lambda: qapp.clipboard().text() == "Preview selected text", timeout=5000)
    assert preview.view.url() == preview._shell_url


def test_preview_context_keeps_link_copy_and_existing_navigation_policy(
    qtbot, preview, tmp_path, monkeypatch, qapp
):
    from types import SimpleNamespace

    import marknotes.preview as preview_module

    opened = []
    monkeypatch.setattr(
        preview_module,
        "QDesktopServices",
        SimpleNamespace(
            openUrl=lambda url: opened.append(url.toString()) or True,
        ),
    )
    load_document(qtbot, preview, "[Example](https://example.com/context-test)", tmp_path)
    actions = _invoke_preview_context(qtbot, preview, monkeypatch, "a", "リンクURLをコピー")
    assert [label for label, _ in actions] == [
        "コピー",
        "すべて選択",
        "リンクを開く",
        "リンクURLをコピー",
    ]
    qtbot.waitUntil(
        lambda: qapp.clipboard().text() == "https://example.com/context-test", timeout=5000
    )
    _invoke_preview_context(qtbot, preview, monkeypatch, "a", "リンクを開く")
    qtbot.waitUntil(lambda: bool(opened), timeout=5000)
    assert opened == ["https://example.com/context-test"]
    from PySide6.QtCore import QUrl

    preview.page().open_context_link(QUrl("file:///outside-preview.html"))
    preview.page().open_context_link(QUrl("javascript:alert(1)"))
    evaluate(qtbot, preview, "document.querySelector('a').href = 'file:///outside-preview.html'")
    blocked_actions = _invoke_preview_context(qtbot, preview, monkeypatch, "a")
    assert ("リンクを開く", False) in blocked_actions
    assert ("リンクURLをコピー", True) in blocked_actions
    assert opened == ["https://example.com/context-test"]
    assert preview.view.url() == preview._shell_url


def test_preview_context_keeps_image_copy(qtbot, preview, tmp_path, monkeypatch, qapp):
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor("#337799"))
    assert image.save(str(tmp_path / "context.png"))
    load_document(qtbot, preview, "![Picture](context.png)", tmp_path)
    qtbot.waitUntil(
        lambda: evaluate(qtbot, preview, "document.querySelector('img').naturalWidth") == 64,
        timeout=5000,
    )
    qapp.clipboard().clear()
    actions = _invoke_preview_context(qtbot, preview, monkeypatch, "img", "画像をコピー")
    assert [label for label, _ in actions] == [
        "コピー",
        "すべて選択",
        "画像をコピー",
        "画像URLをコピー",
    ]
    qtbot.waitUntil(lambda: qapp.clipboard().mimeData().hasImage(), timeout=5000)
    assert qapp.clipboard().image().size() == image.size()


def test_code_copy_button_preserves_source_and_supports_mouse_and_keyboard(
    qtbot, preview, tmp_path, qapp
):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWebEngineCore import QWebEngineSettings

    code = 'if True:\n\tprint("日本語 & <tag>")  \n\n# 最後のコメント\n\n'
    load_document(qtbot, preview, "```python\n" + code + "```", tmp_path)
    assert (
        not preview.page()
        .settings()
        .testAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard)
    )
    assert evaluate(qtbot, preview, "document.querySelectorAll('.code-copy-button').length") == 1
    assert evaluate(qtbot, preview, "document.querySelector('code').dataset.codeSource") == code
    point = evaluate(
        qtbot,
        preview,
        "(() => {const r=document.querySelector('.code-copy-button').getBoundingClientRect();"
        "return [Math.round(r.left+r.width/2), Math.round(r.top+r.height/2)];})()",
    )
    qapp.clipboard().setText("before copy")
    target = preview.view.focusProxy()
    assert target is not None
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=QPoint(*point))
    qtbot.waitUntil(lambda: qapp.clipboard().text() == code, timeout=5000)
    qtbot.waitUntil(
        lambda: (
            evaluate(
                qtbot,
                preview,
                "document.querySelector('.code-copy-button').getAttribute('aria-label')",
            )
            == "コピーしました"
        ),
        timeout=5000,
    )
    assert not qapp.clipboard().mimeData().hasHtml()
    qapp.clipboard().setText("before keyboard copy")
    evaluate(qtbot, preview, "document.querySelector('.code-copy-button').focus() || true")
    qtbot.keyClick(target, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: qapp.clipboard().text() == code, timeout=5000)


def test_code_copy_controls_follow_current_document_and_skip_non_code(
    qtbot, preview, tmp_path, qapp
):
    load_document(qtbot, preview, "```text\nold code\n```", tmp_path)
    evaluate(qtbot, preview, "(window.oldCopy = document.querySelector('.code-copy-button'), true)")
    source = (
        "`inline`\n\n```unknown-language\nnew code\n```\n\n```\n```\n\n"
        '<pre><code data-code-source="untrusted">raw HTML</code></pre>\n\n'
        "```mermaid\nflowchart LR\nA --> B\n```"
    )
    load_document(qtbot, preview, source, tmp_path, revision=2)
    assert evaluate(qtbot, preview, "document.querySelectorAll('.code-copy-button').length") == 2
    assert (
        evaluate(qtbot, preview, "document.querySelectorAll('.code-copy-button:disabled').length")
        == 1
    )
    qapp.clipboard().setText("unchanged")
    qtbot.waitUntil(lambda: qapp.clipboard().text() == "unchanged")
    assert not preview._bridge.copyCode("old code", 1)
    evaluate(qtbot, preview, "window.oldCopy.click() || true")
    qtbot.wait(100)
    assert qapp.clipboard().text() == "unchanged"
    evaluate(qtbot, preview, "document.querySelector('.code-copy-button').click() || true")
    qtbot.waitUntil(lambda: qapp.clipboard().text() == "new code\n", timeout=5000)
    assert evaluate(qtbot, preview, "document.querySelectorAll('.mermaid-block svg').length") == 1
    assert (
        evaluate(qtbot, preview, "document.querySelectorAll('.code-block [class^=tok-]').length")
        == 0
    )


def test_highlight_and_copy_button_keep_code_geometry_during_theme_and_scroll(
    qtbot, preview, tmp_path
):
    source = (
        "# Code\n\n```python\n"
        + "\n".join(
            ["def example(value):", "    # comment", '    return "' + "wide " * 80 + '"'] * 12
        )
        + "\n```\n\nLast line"
    )
    load_document(qtbot, preview, source, tmp_path)
    expression = """(() => {
      const token = document.querySelector('.tok-keyword');
      const row = document.querySelector('.code-line');
      const button = document.querySelector('.code-copy-button');
      const pre = document.querySelector('.code-block > pre');
      const r = row.getBoundingClientRect(), b = button.getBoundingClientRect();
      return {color: getComputedStyle(token).color, rowHeight: r.height,
        rowTop: r.top, buttonBottom: b.bottom, buttonRight: b.right,
        weight: getComputedStyle(token).fontWeight, rowWeight: getComputedStyle(row).fontWeight,
        buttonBackground: getComputedStyle(button).backgroundColor,
        blockBackground: getComputedStyle(pre).backgroundColor};
    })()"""
    light = evaluate(qtbot, preview, expression)
    assert light["buttonBottom"] <= light["rowTop"]
    assert light["weight"] == light["rowWeight"]
    assert light["buttonBackground"] == light["blockBackground"]
    evaluate(qtbot, preview, "(document.querySelector('pre').scrollLeft = 300, true)")
    assert evaluate(qtbot, preview, expression)["buttonRight"] == light["buttonRight"]
    preview.scroll_to_source(17.5, 1)
    before = wait_position(qtbot, preview, 17.5)
    preview.apply_theme(True)
    qtbot.waitUntil(
        lambda: evaluate(qtbot, preview, expression)["color"] != light["color"], timeout=5000
    )
    dark = evaluate(qtbot, preview, expression)
    after = metrics(qtbot, preview)
    assert dark["rowHeight"] == light["rowHeight"]
    assert dark["weight"] == light["weight"]
    assert dark["buttonBackground"] == dark["blockBackground"]
    assert after["source"] == pytest.approx(before["source"], abs=0.02)
    assert after["measuredSource"] == pytest.approx(before["measuredSource"], abs=0.05)
    preview.scroll_to_source(len(source.split("\n")) - 1, 1)
    end = wait_position(qtbot, preview, len(source.split("\n")) - 1)
    assert abs(end["scrollY"] - end["maxScroll"]) <= 1
