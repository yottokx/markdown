"""Real offline KaTeX/Mermaid rendering, asynchronous updates and scroll mapping."""

import json
from itertools import pairwise

import pytest
from PySide6.QtCore import QSettings

from md_editor.app import MainWindow
from md_editor.preview import PreviewPane
from md_editor.rendering import render_markdown


def evaluate(qtbot, pane, expression):
    values = []
    pane.page().runJavaScript(f"JSON.stringify({expression})", values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=10000)
    return json.loads(values[0])


@pytest.fixture
def pane(qtbot):
    widget = PreviewPane()
    qtbot.addWidget(widget)
    widget.resize(750, 540)
    widget.show()
    qtbot.waitUntil(lambda: widget._shell_ready, timeout=20000)
    return widget


def load(qtbot, pane, source, base, revision=1):
    document = render_markdown(source)
    with qtbot.waitSignal(pane.ready, timeout=20000):
        pane.set_document(document.html, document.line_count, base, revision)
    assert not evaluate(qtbot, pane, "window.previewApi.metrics().rendering")
    return document


def test_tex_is_typeset_with_local_fonts_and_code_remains_literal(qtbot, pane, tmp_path):
    source = r"""# 数式

Energy $E=mc^2$ and \(a^2+b^2=c^2\).

$$
\frac{1}{2} + \sum_{n=1}^{\infty}\frac{1}{n^2}
$$

\[
\begin{pmatrix}a & b \\ c & d\end{pmatrix}
\]

`$literal$` and a price of $20.

```text
$$not math$$
```
"""
    load(qtbot, pane, source, tmp_path)
    assert evaluate(qtbot, pane, "document.querySelectorAll('#content .katex').length") == 4
    assert evaluate(qtbot, pane, "document.querySelectorAll('#content math').length") == 4
    assert evaluate(qtbot, pane, "document.querySelectorAll('.render-error').length") == 0
    assert evaluate(qtbot, pane, "document.fonts.status") == "loaded"
    assert evaluate(qtbot, pane, "document.fonts.check('16px KaTeX_Main')")
    assert "$literal$" in evaluate(qtbot, pane, "document.querySelector('p code').textContent")
    assert "$$not math$$" in evaluate(qtbot, pane, "document.querySelector('pre code').textContent")
    assert evaluate(qtbot, pane, "Array.from(document.scripts).every(s => !/^https?:/.test(s.src))")
    assert evaluate(
        qtbot, pane, "Array.from(document.styleSheets).every(s => !/^https?:/.test(s.href || ''))"
    )


def test_mermaid_flowchart_and_sequence_have_real_svg_and_measured_anchors(qtbot, pane, tmp_path):
    source = """# 図

```mermaid
flowchart TD
    A[開始] --> B{確認}
    B -->|はい| C[完了]
    B -->|いいえ| A
```

```mermaid
sequenceDiagram
    participant A as 利用者
    participant B as アプリ
    A->>B: Markdownを編集
    B-->>A: プレビュー更新
```

## 最終行"""
    document = load(qtbot, pane, source, tmp_path)
    assert evaluate(qtbot, pane, "document.querySelectorAll('.mermaid-block > svg').length") == 2
    assert evaluate(
        qtbot,
        pane,
        "Array.from(document.querySelectorAll('.mermaid-block svg')).every(s => s.getBoundingClientRect().height > 80)",
    )
    assert "開始" in evaluate(qtbot, pane, "document.querySelector('.mermaid-block').textContent")
    assert evaluate(qtbot, pane, "document.querySelectorAll('.diagram-measuring-host').length") == 0
    info = evaluate(qtbot, pane, "window.previewApi.metrics()")
    assert all(a["source"] < b["source"] and a["y"] < b["y"] for a, b in pairwise(info["anchors"]))
    pane.scroll_to_source(document.line_count - 1, 1)
    qtbot.wait(100)
    final = evaluate(qtbot, pane, "window.previewApi.metrics()")
    assert final["source"] == pytest.approx(document.line_count - 1, abs=0.05)
    assert final["scrollY"] == pytest.approx(final["maxScroll"], abs=2)


def test_invalid_blocks_show_source_and_recover_on_edit(qtbot, pane, tmp_path):
    load(qtbot, pane, "$\\frac{$\n\n```mermaid\nflowchart TD\nA -->\n```", tmp_path)
    assert evaluate(qtbot, pane, "document.querySelectorAll('.render-error').length") == 2
    assert evaluate(qtbot, pane, "document.querySelectorAll('.render-error-source').length") == 2
    assert evaluate(qtbot, pane, "document.querySelectorAll('.diagram-measuring-host').length") == 0
    load(qtbot, pane, "$\\frac{1}{2}$\n\n```mermaid\nflowchart LR\nA --> B\n```", tmp_path, 2)
    assert evaluate(qtbot, pane, "document.querySelectorAll('.render-error').length") == 0
    assert evaluate(qtbot, pane, "document.querySelectorAll('.katex').length") == 1
    assert evaluate(qtbot, pane, "document.querySelectorAll('.mermaid-block > svg').length") == 1


def test_obsolete_diagram_does_not_replace_newer_document(qtbot, pane, tmp_path):
    older = render_markdown("```mermaid\nflowchart LR\nA[Obsolete] --> B\n```")
    newer = render_markdown("# Latest\n\n$E=mc^2$")
    pane.set_document(older.html, older.line_count, tmp_path, 1)
    with qtbot.waitSignal(
        pane.ready, timeout=20000, check_params_cb=lambda revision: revision == 2
    ):
        pane.set_document(newer.html, newer.line_count, tmp_path, 2)
    qtbot.wait(100)
    assert "Obsolete" not in evaluate(qtbot, pane, "document.getElementById('content').textContent")
    assert evaluate(qtbot, pane, "document.querySelectorAll('.mermaid-block').length") == 0
    assert evaluate(qtbot, pane, "document.querySelectorAll('.katex').length") == 1
    assert evaluate(qtbot, pane, "window.previewApi.metrics().revision") == 2


def test_theme_rerenders_diagrams_without_losing_source_position(qtbot, pane, tmp_path):
    source = (
        "# Start\n\n"
        + "paragraph\n\n" * 15
        + "```mermaid\nflowchart TD\nA --> B --> C\n```\n\n# Last"
    )
    load(qtbot, pane, source, tmp_path)
    pane.scroll_to_source(22, 1)
    qtbot.wait(100)
    light = evaluate(qtbot, pane, "document.querySelector('.mermaid-block').innerHTML")
    with qtbot.waitSignal(pane.ready, timeout=20000):
        pane.apply_theme(True)
    assert (
        evaluate(qtbot, pane, "document.querySelector('.mermaid-block').dataset.renderTheme")
        == "dark"
    )
    assert evaluate(qtbot, pane, "document.querySelector('.mermaid-block').innerHTML") != light
    assert evaluate(qtbot, pane, "window.previewApi.metrics().source") == pytest.approx(
        22, abs=0.05
    )
    with qtbot.waitSignal(pane.ready, timeout=20000):
        pane.apply_theme(False)
    assert (
        evaluate(qtbot, pane, "document.querySelector('.mermaid-block').dataset.renderTheme")
        == "light"
    )


def test_untrusted_render_input_cannot_execute_or_change_shell_style(qtbot, pane, tmp_path):
    source = r"""<span data-render-kind="mermaid">flowchart LR; A-->B</span>

$\href{javascript:alert(1)}{bad}$

```mermaid
%%{init: {"securityLevel": "loose", "themeCSS": "body { display: none !important; }"}}%%
flowchart LR
    A[Node] --> B[Other]
    click A "javascript:alert(1)"
```
"""
    load(qtbot, pane, source, tmp_path)
    assert (
        evaluate(qtbot, pane, "document.querySelectorAll('[data-render-kind=mermaid]').length") == 1
    )
    assert (
        evaluate(
            qtbot, pane, "document.querySelectorAll('#content a[href^=\"javascript:\"]').length"
        )
        == 0
    )
    assert evaluate(qtbot, pane, "getComputedStyle(document.body).display") != "none"
    assert evaluate(qtbot, pane, "document.querySelectorAll('#content script').length") == 0


def test_mainwindow_scroll_sync_after_diagram_layout_and_resize(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "rich.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window, before_close_func=lambda w: w.editor.document().setModified(False))
    window.resize(1180, 760)
    window.show()
    source = (
        "# Start\n\n"
        + "$a^2+b^2=c^2$\n\n" * 8
        + "```mermaid\nflowchart TD\nA --> B --> C --> D --> E\n```\n\n## End"
    )
    window.set_source(source, window.session.base_dir, update_session=False)
    qtbot.waitUntil(lambda: window._revision == window._rendered_revision, timeout=20000)
    for position in (2, 10, 20, window.editor.blockCount() - 1):
        window.editor.scroll_to_source(position)
        qtbot.wait(100)
        info = evaluate(qtbot, window.preview, "window.previewApi.metrics()")
        assert info["source"] == pytest.approx(position, abs=0.1)
    window.splitter.setSizes([420, 760])
    qtbot.wait(100)
    window.preview.page().runJavaScript("window.scrollTo(0, 160)")
    qtbot.wait(150)
    info = evaluate(qtbot, window.preview, "window.previewApi.metrics()")
    assert abs(window.editor.source_position() - info["source"]) < 1.05
    assert window.editor.toPlainText() == source


def test_inline_math_appears_after_typing_closer_and_updates_in_context(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "inline.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window, before_close_func=lambda w: w.editor.document().setModified(False))
    window.resize(1180, 760)
    window.show()
    window.set_source("", window.session.base_dir, update_session=False)
    qtbot.waitUntil(lambda: window._revision == window._rendered_revision, timeout=20000)
    window.editor.insertPlainText("本文 $ x_i + 1 ")
    qtbot.waitUntil(lambda: window._revision == window._rendered_revision, timeout=20000)
    assert evaluate(qtbot, window.preview, "document.querySelectorAll('.katex').length") == 0

    qtbot.keyClicks(window.editor, "$")
    qtbot.waitUntil(lambda: window._revision == window._rendered_revision, timeout=20000)
    assert evaluate(qtbot, window.preview, "document.querySelectorAll('.katex').length") == 1

    # Normal text, emphasis and table cells all use the same inline parser.
    tail = (
        "。通常の $E=mc^2$ と " + r"\(a^2 + b^2 = c^2\)" + "。\n\n"
        "**強調 $ x^2 $**\n\n"
        "| 数式 | 値 |\n| --- | --- |\n| $ x_i $ | $ 2 $ |\n\n"
        "コード `$ x $` と価格 $5、$10 はそのまま。"
    )
    window.editor.insertPlainText(tail)
    qtbot.waitUntil(lambda: window._revision == window._rendered_revision, timeout=20000)
    formulas = evaluate(
        qtbot,
        window.preview,
        """Array.from(document.querySelectorAll('[data-render-kind="math-inline"]')).map(node => {
            const glyphs = node.querySelector('.katex-html');
            const rect = glyphs?.getBoundingClientRect();
            return {state: node.dataset.renderState,
                    tex: node.querySelector('annotation')?.textContent,
                    visible: !!rect && rect.width > 0 && rect.height > 0
                             && getComputedStyle(glyphs).visibility === 'visible'};
        })""",
    )
    assert [formula["tex"] for formula in formulas] == [
        " x_i + 1 ",
        "E=mc^2",
        "a^2 + b^2 = c^2",
        " x^2 ",
        " x_i ",
        " 2 ",
    ]
    assert all(formula["state"] == "ready" and formula["visible"] for formula in formulas)
    assert evaluate(qtbot, window.preview, "document.querySelectorAll('.render-error').length") == 0
    assert window.editor.toPlainText() == "本文 $ x_i + 1 $" + tail
