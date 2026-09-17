"""Standalone export dependency resolution, rich DOM preservation and limits."""

import base64
import io
from pathlib import Path
from typing import ClassVar

import pytest
from bs4 import BeautifulSoup
from PySide6.QtGui import QColor, QImage

from marknotes import exporting
from marknotes.exporting import (
    ExportCancelledError,
    ExportDependencyError,
    build_export_html,
)


def png(path: Path, width=5, height=4) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#2468ab"))
    assert image.save(str(path), "PNG")
    return path.read_bytes()


def soup(html):
    return BeautifulSoup(html, "html.parser")


def decode_uri(uri):
    header, data = uri.split(",", 1)
    return header, base64.b64decode(data.split("#", 1)[0])


def test_output_is_utf8_self_contained_and_removes_preview_bridge(tmp_path):
    data = png(tmp_path / "img" / "日本語 画像.jpg")  # MIME comes from bytes, not suffix.
    fragment = (
        '<h1 data-source-line="0" onclick="evil()">タイトル</h1>'
        '<img src="img/日本語%20画像.jpg" loading="lazy" onerror="evil()">'
        '<span class="code-boundary" data-source-end="3"></span>'
        '<div id="eof-spacer" style="height:900px"></div>'
        '<script src="qrc:///qtwebchannel/qwebchannel.js">evil()</script>'
        '<iframe src="https://example.test/frame"></iframe>'
        '<a href="javascript:evil()">unsafe</a>'
    )
    output = build_export_html(fragment, tmp_path, '報告 <title> & "value"')
    doc = soup(output)
    assert doc.title.string == '報告 <title> & "value"'
    assert doc.find("meta", charset="utf-8")
    assert (
        "script-src 'none'"
        in doc.find("meta", attrs={"http-equiv": "Content-Security-Policy"})["content"]
    )
    assert not doc.find(["script", "iframe"])
    assert not doc.select(
        "[data-source-line], [data-source-end], [onclick], [onerror], #eof-spacer"
    )
    assert not doc.select(".code-boundary")
    assert "loading" not in doc.img.attrs
    assert "href" not in doc.a.attrs
    assert decode_uri(doc.img["src"]) == ("data:image/png;base64", data)
    assert output.encode("utf-8").decode("utf-8") == output


def test_custom_css_imports_images_fonts_and_media_conditions_use_their_own_directory(tmp_path):
    document = tmp_path / "document"
    document.mkdir()
    styles = tmp_path / "styles"
    (styles / "nested").mkdir(parents=True)
    data = png(styles / "img" / "a.png")
    font = (exporting.RESOURCE_DIR / "vendor/katex/fonts/KaTeX_Main-Regular.woff2").read_bytes()
    (styles / "font.woff2").write_bytes(font)
    (styles / "nested/base.css").write_text(
        '.base { background-image: url("../img/a.png"); }', encoding="utf-8"
    )
    custom = styles / "custom.css"
    custom.write_text(
        '@import "nested/base.css" layer(report) supports(display: grid) screen and (min-width: 300px);'
        '@font-face{font-family:Export;src:url("font.woff2") format("woff2")}'
        '.custom{color:#010203;background:image-set("img/a.png" 1x,url(img/a.png) 2x)}',
        encoding="utf-8",
    )
    output = build_export_html("<p>text</p>", document, "CSS", custom)
    css = soup(output).style.string
    assert "@import" not in css
    assert "@layer report" in css and "@supports (display: grid)" in css
    assert "@media screen and (min-width: 300px)" in css
    assert "data:image/png;base64," + base64.b64encode(data).decode() in css
    assert "data:font/woff2;base64," + base64.b64encode(font).decode() in css
    assert "url(img/a.png)" not in css
    assert css.index("#content") < css.index(".custom")


def test_picture_srcset_and_inline_style_are_embedded(tmp_path):
    data = png(tmp_path / "a.png")
    png(tmp_path / "b.png", 9, 7)
    embedded = "data:image/png;base64," + base64.b64encode(data).decode()
    output = build_export_html(
        '<picture><source srcset="a.png 1x, b.png 2x">'
        f'<img src="a.png" srcset="{embedded} 1x, b.png 2x" style="background:url(a.png)">'
        "</picture>",
        tmp_path,
        "srcset",
    )
    doc = soup(output)
    assert doc.source["srcset"].count("data:image/png;base64,") == 2
    assert doc.img["srcset"].count("data:image/png;base64,") == 2
    assert " 1x, " in doc.img["srcset"] and doc.img["srcset"].endswith(" 2x")
    assert "data:image/png;base64," in doc.img["style"]


def test_svg_file_dependencies_and_active_content_are_handled(tmp_path):
    png(tmp_path / "a.png")
    (tmp_path / "diagram.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
        '<script>alert(1)</script><g onclick="evil()"><image href="a.png" width="5" height="4"/>'
        '<path fill="url(#gradient)" d="M0 0h5v5z"/></g></svg>',
        encoding="utf-8",
    )
    output = build_export_html('<img src="diagram.svg">', tmp_path, "SVG")
    header, data = decode_uri(soup(output).img["src"])
    assert header == "data:image/svg+xml;base64"
    text = data.decode()
    assert "<script" not in text and "onclick" not in text
    assert "data:image/png;base64," in text
    assert "url(" in text and "#gradient" in text


def test_rendered_mermaid_svg_and_katex_mathml_survive_without_scripts(tmp_path):
    fragment = (
        '<div class="mermaid-block" data-render-state="ready"><svg viewBox="0 0 120 40" role="img">'
        '<defs><marker id="arrow"><path d="M0 0L5 5"/></marker></defs>'
        '<path marker-end="url(#arrow)" d="M0 0L100 0"/>'
        '<foreignObject width="100" height="30"><div xmlns="http://www.w3.org/1999/xhtml">図のラベル</div></foreignObject>'
        "</svg></div>"
        '<span class="katex"><span class="katex-mathml"><math xmlns="http://www.w3.org/1998/Math/MathML">'
        '<semantics><mrow><mi>x</mi></mrow><annotation encoding="application/x-tex">x_i</annotation>'
        '</semantics></math></span><span class="katex-html" aria-hidden="true">x</span></span>'
    )
    output = build_export_html(fragment, tmp_path, "Rich")
    doc = soup(output)
    assert doc.find("svg")["viewbox"] == "0 0 120 40"
    assert doc.find("foreignobject").get_text() == "図のラベル"
    assert doc.find("annotation").string == "x_i"
    assert doc.select_one(".katex-html")["aria-hidden"] == "true"
    assert "data:font/woff2;base64," in doc.style.string
    assert "fonts/KaTeX_" not in doc.style.string
    assert not doc.script
    assert len(output) > 100_000


def test_missing_dependencies_are_reported_together_instead_of_partial_output(tmp_path):
    (tmp_path / "custom.css").write_text(
        '@import "missing.css";.a{background:url(missing-background.png)}', encoding="utf-8"
    )
    with pytest.raises(ExportDependencyError) as caught:
        build_export_html(
            '<img src="missing-one.png"><picture><source srcset="missing-two.png 2x"></picture>',
            tmp_path,
            "Missing",
            tmp_path / "custom.css",
        )
    references = {issue.reference for issue in caught.value.issues}
    assert {
        "missing-one.png",
        "missing-two.png",
        "missing.css",
        "missing-background.png",
    } <= references
    assert "missing-one.png" in str(caught.value)


@pytest.mark.parametrize(
    "fragment",
    [
        '<svg><use href="external.svg#shape"/></svg>',
        '<svg><image href="missing.png"/></svg>',
        '<video src="movie.mp4"></video>',
        '<img srcset="missing.png invalid-descriptor">',
    ],
)
def test_unsupported_or_unresolved_html_dependencies_block_output(tmp_path, fragment):
    with pytest.raises(ExportDependencyError):
        build_export_html(fragment, tmp_path, "Unsupported")


def test_nested_svg_foreign_object_image_dependency_is_not_missed(tmp_path):
    (tmp_path / "external.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject>'
        '<img xmlns="http://www.w3.org/1999/xhtml" src="missing.png"/>'
        "</foreignObject></svg>",
        encoding="utf-8",
    )
    with pytest.raises(ExportDependencyError) as caught:
        build_export_html('<img src="external.svg">', tmp_path, "Nested")
    assert any(issue.reference == "missing.png" for issue in caught.value.issues)


@pytest.mark.parametrize("kind", ["css", "svg"])
def test_cyclic_dependencies_fail_without_recursing_forever(tmp_path, kind):
    if kind == "css":
        css = tmp_path / "cycle.css"
        css.write_text('@import "cycle.css";', encoding="utf-8")
        args = ("<p>text</p>", tmp_path, "Cycle", css)
    else:
        (tmp_path / "cycle.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><image href="cycle.svg"/></svg>',
            encoding="utf-8",
        )
        args = ('<img src="cycle.svg">', tmp_path, "Cycle")
    with pytest.raises(ExportDependencyError) as caught:
        build_export_html(*args)
    assert any("循環" in issue.reason for issue in caught.value.issues)


def test_actual_mime_is_checked_for_data_uris_and_corrupt_files(tmp_path):
    data = png(tmp_path / "a.png")
    wrong_label = "data:text/plain;base64," + base64.b64encode(data).decode()
    output = build_export_html(f'<img src="{wrong_label}">', tmp_path, "Actual MIME")
    assert decode_uri(soup(output).img["src"])[0] == "data:image/png;base64"
    (tmp_path / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
    with pytest.raises(ExportDependencyError):
        build_export_html('<img src="broken.png">', tmp_path, "Broken")


def test_svg_doctype_is_rejected(tmp_path):
    (tmp_path / "unsafe.svg").write_text(
        '<!DOCTYPE svg [<!ENTITY x "value">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
        encoding="utf-8",
    )
    with pytest.raises(ExportDependencyError):
        build_export_html('<img src="unsafe.svg">', tmp_path, "Unsafe")


def test_remote_fetch_is_opt_in_and_retains_real_mime(tmp_path, monkeypatch):
    data = png(tmp_path / "a.png")
    calls = []

    class Response(io.BytesIO):
        headers: ClassVar = {"Content-Length": str(len(data))}

        def geturl(self):
            return "https://example.test/final.png"

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return Response(data)

    monkeypatch.setattr(exporting.urllib.request, "build_opener", lambda *_: Opener())
    fragment = '<img src="https://example.test/a.png">'
    with pytest.raises(ExportDependencyError) as caught:
        build_export_html(fragment, tmp_path, "Remote")
    assert not calls
    assert any("無効" in issue.reason for issue in caught.value.issues)
    output = build_export_html(fragment, tmp_path, "Remote", fetch_remote=True)
    assert calls[0][0] == "https://example.test/a.png"
    assert 0 < calls[0][1] <= 10
    assert decode_uri(soup(output).img["src"])[1] == data


def test_remote_css_cannot_pull_local_file(tmp_path, monkeypatch):
    data = b".x{background:url(file:///C:/secret.png)}"

    class Response(io.BytesIO):
        headers: ClassVar = {}

        def geturl(self):
            return "https://example.test/main.css"

    class Opener:
        def open(self, request, timeout):
            return Response(data)

    monkeypatch.setattr(exporting.urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(ExportDependencyError) as caught:
        build_export_html(
            '<link rel="stylesheet" href="https://example.test/main.css">',
            tmp_path,
            "Remote CSS",
            fetch_remote=True,
        )
    assert any("外部コンテンツからローカル" in issue.reason for issue in caught.value.issues)


def test_limits_and_cancellation(tmp_path, monkeypatch):
    with pytest.raises(ExportCancelledError):
        build_export_html("<p>text</p>", tmp_path, "Cancel", cancelled=lambda: True)
    monkeypatch.setattr(exporting, "MAX_RESOURCES", 1)
    png(tmp_path / "a.png")
    png(tmp_path / "b.png")
    with pytest.raises(ExportDependencyError) as caught:
        build_export_html('<img src="a.png"><img src="b.png">', tmp_path, "Count")
    assert any("依存ファイル数" in issue.reason for issue in caught.value.issues)


def test_size_and_time_limits_report_structured_errors(tmp_path, monkeypatch):
    png(tmp_path / "a.png")
    monkeypatch.setattr(exporting, "MAX_RESOURCE_BYTES", 20)
    with pytest.raises(ExportDependencyError) as caught:
        build_export_html('<img src="a.png">', tmp_path, "Size")
    assert any("サイズ上限" in issue.reason for issue in caught.value.issues)
    monkeypatch.setattr(exporting, "MAX_SECONDS", -1)
    with pytest.raises(ExportDependencyError) as caught:
        build_export_html("<p>text</p>", tmp_path, "Timeout")
    assert any("制限時間" in issue.reason for issue in caught.value.issues)


def test_namespace_identifiers_and_alt_text_do_not_trigger_resource_fetches(tmp_path):
    css = tmp_path / "namespace.css"
    css.write_text(
        '@namespace svg url("http://www.w3.org/2000/svg");svg|svg{color:red}', encoding="utf-8"
    )
    png(tmp_path / "a.png")
    output = build_export_html(
        '<img src="a.png" alt="url(example.png)">', tmp_path, "Namespace", css
    )
    assert soup(output).img["alt"] == "url(example.png)"
    assert "http://www.w3.org/2000/svg" in soup(output).style.string


@pytest.mark.parametrize("location", ["custom", "link", "import"])
def test_css_cannot_close_style_element_and_inject_markup(tmp_path, location):
    css = tmp_path / "custom.css"
    css.write_text('.x::after{content:"</style><script>evil()</script>"}', encoding="utf-8")
    fragment = {
        "custom": "<p>text</p>",
        "link": '<link rel="stylesheet" href="custom.css"><p>text</p>',
        "import": '<style>@import "custom.css";</style><p>text</p>',
    }[location]
    output = build_export_html(fragment, tmp_path, "Safe", css if location == "custom" else None)
    doc = soup(output)
    assert not doc.script
    assert len(doc.find_all("style")) == (1 if location == "custom" else 2)


def test_snapshot_file_links_are_preserved_without_embedding_linked_documents(tmp_path):
    local = (tmp_path / "linked document.md").as_uri() + "#section"
    assert not (tmp_path / "linked document.md").exists()
    fragment = (
        f'<a href="{local}">Local</a>'
        '<a href="next.md">Relative</a>'
        '<a href="#section">Section</a>'
        '<a href="https://example.test/page">Web</a>'
        '<a href="mailto:test@example.test">Mail</a>'
        '<a href="custom-demo://item/123">Custom</a>'
        '<a href="vbscript:msgbox(1)">VBScript</a>'
        '<a href="javascript:evil()">Script</a>'
        '<a href="data:text/html,unsafe">Data</a>'
    )
    output = build_export_html(fragment, tmp_path, "Links")
    links = {a.get_text(): a.get("href") for a in soup(output).find_all("a")}
    assert links == {
        "Local": local,
        "Relative": "next.md",
        "Section": "#section",
        "Web": "https://example.test/page",
        "Mail": "mailto:test@example.test",
        "Custom": "custom-demo://item/123",
        "VBScript": None,
        "Script": None,
        "Data": None,
    }


def test_export_removes_copy_controls_and_wrapper_but_preserves_highlighted_code(tmp_path):
    fragment = (
        '<div class="code-block" style="padding-top:80px" data-preview-code-ui="block">'
        '<button class="code-copy-button" data-preview-code-ui="copy" aria-label="コードをコピー" onclick="copy()">'
        '<svg><path d="M0 0h5"/></svg><span>コピー済み</span></button>'
        '<pre><code class="language-python" data-code-source="if value &lt; 3:&#10;&#10;">'
        '<span class="code-boundary" data-source-line="0"></span>'
        '<span class="code-line" data-source-line="1"><span class="tok-keyword">if</span> '
        '<span class="tok-name">value</span> <span class="tok-operator">&lt;</span> '
        '<span class="tok-number">3</span>:</span>'
        '<span class="code-line" data-source-line="2"><br></span>'
        '<span class="code-line" data-source-line="3">\t<span class="tok-builtin">print</span>('
        '<span class="tok-string">&quot;  空白 &amp; &lt;tag&gt;  &quot;</span>)  </span>'
        '<span class="code-line" data-source-line="4"><br></span>'
        '<span class="code-boundary" data-source-line="5"></span>'
        "</code></pre></div>"
    )
    output = build_export_html(fragment, tmp_path, "Code export")
    doc = soup(output)
    assert not doc.select(".code-copy-button, .code-block, .code-boundary, button, svg")
    assert "コピー済み" not in doc.get_text()
    assert "padding-top:80px" not in output
    assert not doc.select("[data-preview-code-ui], [data-source-line], [data-code-source]")
    assert doc.pre.parent == doc.select_one("#content")
    assert doc.code["class"] == ["language-python"]
    lines = doc.select("pre .code-line")
    assert [line.get_text() for line in lines] == [
        "if value < 3:",
        "",
        '\tprint("  空白 & <tag>  ")  ',
        "",
    ]
    assert lines[1].find("br") and lines[3].find("br")
    assert doc.select_one(".code-line .tok-keyword").string == "if"
    assert doc.select_one(".code-line .tok-string").string == '"  空白 & <tag>  "'
    assert doc.html["data-theme"] == "light"
    assert not doc.select("script, link[rel=stylesheet]")


def test_code_highlight_styles_are_embedded_before_custom_overrides(tmp_path):
    custom = tmp_path / "custom.css"
    custom.write_text(".code-line .tok-keyword { color: #123456; }", encoding="utf-8")
    output = build_export_html(
        '<pre><code><span class="code-line"><span class="tok-keyword">return</span> 1</span></code></pre>',
        tmp_path,
        "Highlight CSS",
        custom,
    )
    css = soup(output).style.string
    highlight_css = (exporting.RESOURCE_DIR / "code-highlight.css").read_text(encoding="utf-8")
    import tinycss2

    selectors = [
        tinycss2.serialize(rule.prelude).strip()
        for rule in tinycss2.parse_stylesheet(
            highlight_css, skip_comments=True, skip_whitespace=True
        )
        if rule.type == "qualified-rule" and ".tok-keyword" in tinycss2.serialize(rule.prelude)
    ]
    assert selectors, "The bundled highlighter must define keyword colors"
    assert any(selector in css for selector in selectors)
    assert css.index(selectors[0]) < css.index("#123456")
    assert "url(" not in highlight_css and "@import" not in highlight_css


def test_export_keeps_user_html_that_uses_copy_ui_class_names(tmp_path):
    fragment = (
        '<p class="code-copy-button">残す本文</p>'
        '<div class="code-block" id="user-block" style="padding:7px">'
        "<pre><code>  unchanged &lt;code&gt;\n\n</code></pre>"
        '<button class="code-copy-button">ユーザーのボタン</button></div>'
        '<p data-preview-code-ui="copy">タグが異なる本文</p>'
    )
    doc = soup(build_export_html(fragment, tmp_path, "User HTML"))
    assert doc.select_one("p.code-copy-button").string == "残す本文"
    wrapper = doc.select_one("div.code-block#user-block")
    assert wrapper is not None
    assert "padding:7px" in wrapper["style"]
    assert wrapper.pre.parent is wrapper
    assert wrapper.code.string == "  unchanged <code>\n\n"
    assert wrapper.select_one("button.code-copy-button").string == "ユーザーのボタン"
    assert "タグが異なる本文" in doc.get_text()
    assert not doc.select("[data-preview-code-ui]")


def test_task_export_retains_checked_state_without_edit_coordinates(tmp_path):
    fragment = (
        '<input type="checkbox" data-task-line="0" data-task-column="3" checked>'
        '<input type="checkbox" data-task-line="1" data-task-column="3">'
    )
    doc = soup(build_export_html(fragment, tmp_path, "Tasks"))
    checkboxes = doc.select('input[type="checkbox"]')
    assert len(checkboxes) == 2
    assert checkboxes[0].has_attr("checked")
    assert not checkboxes[1].has_attr("checked")
    assert all(checkbox.has_attr("disabled") for checkbox in checkboxes)
    assert not doc.select("[data-task-line], [data-task-column]")
