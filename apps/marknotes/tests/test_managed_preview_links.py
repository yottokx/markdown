"""Only explicit clicks on the current note's attachments may leave the preview."""

from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage

from marknotes.managed_assets import ManagedAssets
from marknotes.preview import PreviewPane, _PreviewPage


@pytest.fixture
def page(qapp, tmp_path):
    return _PreviewPage(QUrl.fromLocalFile(str(tmp_path / "shell.html")), qapp)


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr("marknotes.preview.QDesktopServices.openUrl", lambda url: urls.append(url))
    return urls


def test_managed_file_click_opens_in_os_but_never_navigates_preview(page, opened, tmp_path):
    manager = ManagedAssets(tmp_path / "note")
    asset = manager.save_bytes(b"pdf", "pdf", "資料.pdf", image=False)
    page.managed_assets_base = manager.base_dir
    url = QUrl.fromLocalFile(str(asset.path))
    url.setQuery("download")
    url.setFragment("page=2")
    assert page.can_open_context_link(url)
    assert not page.acceptNavigationRequest(
        url, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True
    )
    assert [Path(url.toLocalFile()) for url in opened] == [asset.path]
    assert not opened[0].hasQuery() and not opened[0].hasFragment()
    page.open_context_link(url)
    assert len(opened) == 2


@pytest.mark.parametrize(
    "navigation",
    [
        QWebEnginePage.NavigationType.NavigationTypeTyped,
        QWebEnginePage.NavigationType.NavigationTypeFormSubmitted,
        QWebEnginePage.NavigationType.NavigationTypeOther,
        QWebEnginePage.NavigationType.NavigationTypeRedirect,
    ],
)
def test_automatic_navigation_never_launches_attachment(page, opened, tmp_path, navigation):
    manager = ManagedAssets(tmp_path / "note")
    asset = manager.save_bytes(b"pdf", "pdf", image=False)
    page.managed_assets_base = manager.base_dir
    assert not page.acceptNavigationRequest(QUrl.fromLocalFile(str(asset.path)), navigation, True)
    assert not opened


def test_local_files_are_blocked_by_default_and_between_notes(page, opened, tmp_path):
    first = ManagedAssets(tmp_path / "first")
    second = ManagedAssets(tmp_path / "second")
    first_asset = first.save_bytes(b"a", "txt", image=False)
    second_asset = second.save_bytes(b"b", "txt", image=False)
    url = QUrl.fromLocalFile(str(first_asset.path))
    assert not page.can_open_context_link(url)
    page.open_context_link(url)
    assert not opened
    page.managed_assets_base = second.base_dir
    assert not page.can_open_context_link(url)
    page.open_context_link(url)
    assert not opened
    assert page.can_open_context_link(QUrl.fromLocalFile(str(second_asset.path)))
    page.managed_assets_base = None
    assert not page.can_open_context_link(QUrl.fromLocalFile(str(second_asset.path)))


def test_outside_missing_directory_and_subframe_links_are_rejected(page, opened, tmp_path):
    manager = ManagedAssets(tmp_path / "note")
    asset = manager.save_bytes(b"pdf", "pdf", image=False)
    outside = tmp_path / "private.txt"
    outside.write_text("outside")
    page.managed_assets_base = manager.base_dir
    for path in [outside, manager.assets_dir, manager.assets_dir / "missing.txt"]:
        url = QUrl.fromLocalFile(str(path))
        assert not page.can_open_context_link(url)
        page.open_context_link(url)
    assert not page.acceptNavigationRequest(
        QUrl.fromLocalFile(str(asset.path)),
        QWebEnginePage.NavigationType.NavigationTypeLinkClicked,
        False,
    )
    assert not opened


def test_existing_external_link_and_shell_rules_remain(page, opened):
    url = QUrl("https://example.com/document")
    assert page.can_open_context_link(url)
    assert not page.acceptNavigationRequest(
        url, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True
    )
    assert opened == [url]
    assert page.acceptNavigationRequest(
        page._shell_url, QWebEnginePage.NavigationType.NavigationTypeOther, True
    )
    assert not page.can_open_context_link(QUrl("javascript:alert(1)"))


def test_setting_a_preview_document_does_not_enable_managed_links(qtbot, tmp_path):
    pane = PreviewPane()
    qtbot.addWidget(pane)
    pane.set_document("<p>text</p>", 1, tmp_path, 1)
    assert pane.page().managed_assets_base is None


@pytest.mark.parametrize("kind", ["file", "folder"])
def test_notebook_local_links_open_existing_targets_only_on_explicit_click(
    page, opened, tmp_path, kind
):
    path = tmp_path / "外部 [資料] #100%.txt"
    if kind == "file":
        path.write_text("unchanged", encoding="utf-8")
    else:
        path.mkdir()
    url = QUrl.fromLocalFile(str(path))
    page.allow_local_links = True
    assert page.can_open_context_link(url)
    for navigation in (
        QWebEnginePage.NavigationType.NavigationTypeOther,
        QWebEnginePage.NavigationType.NavigationTypeRedirect,
        QWebEnginePage.NavigationType.NavigationTypeFormSubmitted,
    ):
        assert not page.acceptNavigationRequest(url, navigation, True)
    assert not page.acceptNavigationRequest(
        url, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, False
    )
    assert not opened
    assert not page.acceptNavigationRequest(
        url, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True
    )
    page.open_context_link(url)
    assert [Path(item.toLocalFile()) for item in opened] == [path, path]
    missing = QUrl.fromLocalFile(str(tmp_path / "missing"))
    assert not page.can_open_context_link(missing)
    page.open_context_link(missing)
    page.allow_local_links = False
    page.open_context_link(url)
    assert len(opened) == 2
