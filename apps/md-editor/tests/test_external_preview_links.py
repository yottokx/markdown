"""Custom links survive sanitation and leave the preview only on explicit navigation."""

import json
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtWebEngineCore import QWebEngineContextMenuRequest, QWebEnginePage

from md_editor.export_renderer import ExportPage
from md_editor.preview import PreviewPane, _PreviewPage
from md_editor.rendering import render_markdown


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hrefs = []
        self.images = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "href" in attrs:
            self.hrefs.append(attrs["href"])
        if tag == "img" and "src" in attrs:
            self.images.append(attrs["src"])


@pytest.mark.parametrize(
    "url",
    [
        "obsidian://open?vault=Notes&file=hello",
        "custom+demo.v1://item/123?mode=open#part",
        "vscode://file/C:/work/note.md",
        "mailto:hello@example.com",
        "https://example.com/page",
    ],
)
@pytest.mark.parametrize("markup", ["[link]({url})", "<{url}>", '<a href="{url}">link</a>'])
def test_custom_link_schemes_survive_markdown_and_html_sanitation(url, markup):
    assert Links(render_markdown(markup.format(url=url)).html).hrefs == [url]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "vbscript:msgbox(1)",
        "data:text/html,test",
        "blob:https://example.com/123",
        "filesystem:file:///tmp/test",
        "about:blank",
        "qrc:///qtwebchannel/qwebchannel.js",
        "view-source:https://example.com",
        "JaVaScRiPt:alert(1)",
        "java&#x09;script:alert(1)",
    ],
)
def test_execution_and_internal_schemes_are_removed_even_with_custom_links(url):
    html = render_markdown(f'<a href="{url}">bad</a> [good](custom-demo://item)').html
    assert Links(html).hrefs == ["custom-demo://item"]


def test_custom_scheme_is_allowed_for_links_only_and_preserves_relative_and_image_urls():
    result = Links(
        render_markdown(
            "[custom](custom-demo://item) [relative](other.md) [anchor](#part) "
            "[local](file:///C:/notes/note.md) "
            '<img src="custom-demo://item"> <img src="data:image/png;base64,AA=="> '
            "![relative](images/sample.png)"
        ).html
    )
    assert result.hrefs == ["custom-demo://item", "other.md", "#part", "file:///C:/notes/note.md"]
    assert result.images == ["data:image/png;base64,AA==", "images/sample.png"]


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr("md_editor.preview.QDesktopServices.openUrl", lambda url: urls.append(url))
    return urls


@pytest.mark.parametrize("url", ["custom-demo://item/123?x=1#part", "mailto:a@example.com"])
def test_custom_navigation_requires_explicit_main_frame_click(qapp, tmp_path, opened, url):
    page = _PreviewPage(QUrl.fromLocalFile(str(tmp_path / "shell.html")), qapp)
    target = QUrl(url)
    assert page.can_open_context_link(target)
    for navigation in (
        QWebEnginePage.NavigationType.NavigationTypeOther,
        QWebEnginePage.NavigationType.NavigationTypeRedirect,
        QWebEnginePage.NavigationType.NavigationTypeFormSubmitted,
        QWebEnginePage.NavigationType.NavigationTypeTyped,
    ):
        assert not page.acceptNavigationRequest(target, navigation, True)
    assert not page.acceptNavigationRequest(
        target, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, False
    )
    assert opened == []
    assert not page.acceptNavigationRequest(
        target, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True
    )
    page.open_context_link(target)
    assert opened == [target, target]
    page.deleteLater()


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "vbscript:test", "data:text/plain,test", "about:blank", "", "a.md"],
)
def test_unsafe_and_relative_navigation_cannot_leave_preview(qapp, tmp_path, opened, url):
    page = _PreviewPage(QUrl.fromLocalFile(str(tmp_path / "shell.html")), qapp)
    target = QUrl(url)
    assert not page.can_open_context_link(target)
    page.open_context_link(target)
    assert opened == []
    page.deleteLater()


def evaluate(qtbot, pane, expression):
    values = []
    pane.page().runJavaScript(f"JSON.stringify({expression})", values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=5000)
    return json.loads(values[0])


def test_real_webengine_click_on_custom_scheme_uses_external_handler(qtbot, tmp_path, opened):
    pane = PreviewPane()
    qtbot.addWidget(pane)
    pane.resize(600, 350)
    pane.show()
    qtbot.waitUntil(lambda: pane._shell_ready, timeout=15000)
    url = "custom-demo://item/123?mode=open#part"
    rendered = render_markdown(f"[Open custom link]({url})")
    with qtbot.waitSignal(pane.ready, timeout=10000):
        pane.set_document(rendered.html, rendered.line_count, tmp_path, 1)
    assert evaluate(qtbot, pane, "document.querySelector('#content a').href") == url
    point = evaluate(
        qtbot,
        pane,
        "(() => {const r=document.querySelector('#content a').getBoundingClientRect();"
        "return [Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)];})()",
    )
    target = pane.view.focusProxy()
    assert target is not None
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=QPoint(*point))
    qtbot.waitUntil(lambda: len(opened) == 1, timeout=5000)
    assert opened == [QUrl(url)]
    assert pane.view.url() == pane._shell_url
    # Context navigation shares the same policy and never replaces the shell.
    menu = pane.view._create_context_menu(
        SimpleNamespace(
            linkUrl=lambda: QUrl(url),
            mediaType=lambda: QWebEngineContextMenuRequest.MediaType.MediaTypeNone,
        )
    )
    try:
        open_action = next(action for action in menu.actions() if action.text() == "リンクを開く")
        assert open_action.isEnabled()
        open_action.trigger()
    finally:
        menu.deleteLater()
    assert opened == [QUrl(url), QUrl(url)]
    assert pane.view.url() == pane._shell_url


def test_export_page_accepts_custom_clicks_and_rejects_automatic_or_unsafe_navigation(
    qapp, monkeypatch
):
    opened = []
    monkeypatch.setattr("md_editor.export_renderer.QDesktopServices.openUrl", opened.append)
    page = ExportPage(qapp)
    target = QUrl("custom-demo://item/123")
    assert not page.acceptNavigationRequest(
        target, QWebEnginePage.NavigationType.NavigationTypeOther, True
    )
    assert not page.acceptNavigationRequest(
        target, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, False
    )
    assert not opened
    assert not page.acceptNavigationRequest(
        target, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True
    )
    for value in ("javascript:alert(1)", "data:text/html,test", "file:///outside.html"):
        assert not page.acceptNavigationRequest(
            QUrl(value), QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True
        )
    assert opened == [target]
    page.deleteLater()
