"""Exercise durable managed copies, parsed rewriting and export failure boundaries."""

from types import SimpleNamespace

import pytest
from markdown_it import MarkdownIt

from marknotes.assets import markdown_destinations
from marknotes.managed_assets import (
    ManagedAssets,
    escape_link_label,
    export_markdown,
    import_markdown,
    resolve_managed_asset,
)


def test_mixed_batch_preserves_bytes_labels_and_rejects_directories(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    picture = source / "日本語 [写真].GIF"
    picture.write_bytes(b"GIF89a original frames")
    attachment = source / "資料 (1) [a] & b.pdf"
    attachment.write_bytes(b"%PDF-test")
    manager = ManagedAssets(tmp_path / "note")

    batch = manager.import_files([picture, attachment, source, source / "missing.pdf"])

    assert len(batch.assets) == 2
    assert len(batch.errors) == 2
    assert "フォルダ" in batch.errors[0].message
    for result, original in zip(batch.assets, [picture, attachment], strict=True):
        assert result.path.read_bytes() == original.read_bytes()
        assert result.size == len(original.read_bytes())
        assert result.original_name == original.name
        assert result.relative_path.startswith("assets/")
        assert resolve_managed_asset(result.relative_path, manager.base_dir) == result.path
    assert batch.assets[0].is_image
    assert not batch.assets[1].is_image
    html = MarkdownIt("commonmark").render(batch.markdown)
    assert '<img src="assets/image-' in html
    assert "日本語" in html and "写真" in html
    assert "資料 (1) [a] &amp; b.pdf</a>" in html
    assert not list((manager.base_dir / "staging").iterdir())


def test_filename_markdown_is_always_literal():
    label = r"a\b![x](_hi_)`code`&amp;<img> **bold**"
    html = MarkdownIt("commonmark", {"html": True}).render(f"[{escape_link_label(label)}](asset)")
    assert "<img>" not in html
    assert "<strong>" not in html
    assert "<code>" not in html
    assert "&amp;amp;" in html
    assert "&lt;img&gt;" in html


def test_unique_names_never_overwrite_existing_asset(tmp_path, monkeypatch):
    manager = ManagedAssets(tmp_path / "note")
    ids = iter(["same", "same", "different"])
    monkeypatch.setattr(
        "marknotes.managed_assets.uuid.uuid4", lambda: SimpleNamespace(hex=next(ids))
    )
    first = manager.save_bytes(b"first", ".png")
    second = manager.save_bytes(b"second", "png")
    assert first.path.read_bytes() == b"first"
    assert second.path.read_bytes() == b"second"
    assert first.path != second.path


def test_publish_failure_returns_no_link_and_removes_partial_files(tmp_path, monkeypatch):
    manager = ManagedAssets(tmp_path / "note")

    def fail_replace(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("marknotes.managed_assets.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        manager.save_bytes(b"content", "png")
    assert not list(manager.assets_dir.iterdir())
    assert not list((manager.base_dir / "staging").iterdir())


def test_copy_failure_never_creates_public_asset(tmp_path, monkeypatch):
    manager = ManagedAssets(tmp_path / "note")

    def fail_copy(_source, output, **_kwargs):
        output.write(b"partial")
        raise OSError("source disappeared")

    monkeypatch.setattr("marknotes.managed_assets.shutil.copyfileobj", fail_copy)
    with pytest.raises(OSError, match="source disappeared"):
        manager.save_bytes(b"content", "png")
    assert not list(manager.assets_dir.iterdir())
    assert not list((manager.base_dir / "staging").iterdir())


@pytest.mark.parametrize(
    "url",
    [
        "../secret.txt",
        "assets/../secret.txt",
        "assets/%2e%2e/secret.txt",
        "assets/../../secret.txt",
        "assets/./a.png",
        "assets//a.png",
        "assets/a.png:stream",
        "assets/%00.png",
        "file:///C:/a.png",
        "https://example.com/assets/a.png",
        "//server/assets/a.png",
        "/assets/a.png",
        "C:/assets/a.png",
        "#assets/a.png",
    ],
)
def test_managed_resolution_rejects_external_and_indirect_paths(tmp_path, url):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.png").write_bytes(b"a")
    (tmp_path / "secret.txt").write_bytes(b"secret")
    assert resolve_managed_asset(url, tmp_path) is None


def test_query_fragment_and_backslash_are_resolved_without_leaving_assets(tmp_path):
    manager = ManagedAssets(tmp_path / "note")
    asset = manager.save_bytes(b"document", "pdf", "資料.pdf", image=False)
    assert (
        resolve_managed_asset(asset.relative_path + "?download#page=2", manager.base_dir)
        == asset.path
    )
    assert (
        resolve_managed_asset(asset.relative_path.replace("/", "\\"), manager.base_dir)
        == asset.path
    )


def test_linked_assets_directory_is_rejected(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    base = tmp_path / "note"
    base.mkdir()
    try:
        (base / "assets").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links unavailable on this Windows account")
    with pytest.raises(ValueError):
        ManagedAssets(base).save_bytes(b"image", "png")
    assert not list(external.iterdir())


def test_import_rewrites_actual_local_links_and_reuses_references(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "写真 1.png").write_bytes(b"picture")
    (source / "資料.pdf").write_bytes(b"pdf")
    body = (
        '![画像](<写真 1.png> "title")\n\n'
        "[資料](資料.pdf#page=2)\n\n"
        "![same][ref]\n\n[ref]: <写真 1.png>\n\n"
        "`[example](資料.pdf)`\n\n"
        "```md\n![example](写真.png)\n```\n\n"
        '<a href="資料.pdf">HTML link</a>\n\n'
        "[web](https://example.com/a.pdf) [anchor](#section)\n"
        "[missing](missing.pdf)"
    )
    manager = ManagedAssets(tmp_path / "note")
    imported = import_markdown(body, source, manager)
    assert len(imported.assets) == 2
    assert len(imported.warnings) == 1
    assert imported.body.count(imported.assets[0].relative_path) == 2
    assert imported.body.count(imported.assets[1].relative_path) == 2
    assert "#page=2)" in imported.body
    assert "`[example](資料.pdf)`" in imported.body
    assert "```md\n![example](写真.png)\n```" in imported.body
    assert "[missing](missing.pdf)" in imported.body
    assert "[web](https://example.com/a.pdf) [anchor](#section)" in imported.body


def test_export_round_trip_copies_references_without_unreferenced_files(tmp_path):
    manager = ManagedAssets(tmp_path / "note")
    image = manager.save_bytes(b"picture", "png")
    pdf = manager.save_bytes(b"pdf", "pdf", "資料.pdf", image=False)
    orphan = manager.save_bytes(b"retained for undo", "png")
    body = "# ノート\n" + image.markdown + "\n" + pdf.markdown + "\n"
    result = export_markdown(body, manager.base_dir, tmp_path / "export")
    assert result.read_text(encoding="utf-8") == body
    for asset in (image, pdf):
        assert (result.parent / asset.relative_path).read_bytes() == asset.path.read_bytes()
    assert not (result.parent / orphan.relative_path).exists()
    assert orphan.path.exists()
    assert len(markdown_destinations(result.read_text(encoding="utf-8"))) == 2


def test_export_failure_preserves_existing_destination_and_source(tmp_path, monkeypatch):
    manager = ManagedAssets(tmp_path / "note")
    image = manager.save_bytes(b"image", "png")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep").write_text("existing")
    with pytest.raises(FileExistsError):
        export_markdown(image.markdown, manager.base_dir, occupied)
    assert (occupied / "keep").read_text() == "existing"

    def fail_copy(*_args, **_kwargs):
        raise OSError("no space")

    monkeypatch.setattr("marknotes.managed_assets.shutil.copyfileobj", fail_copy)
    with pytest.raises(OSError, match="no space"):
        export_markdown(image.markdown, manager.base_dir, tmp_path / "export")
    assert not (tmp_path / "export").exists()
    assert not list(tmp_path.glob(".marknotes-export-*"))
    assert image.path.read_bytes() == b"image"


def test_missing_or_traversing_asset_blocks_broken_export(tmp_path):
    manager = ManagedAssets(tmp_path / "note")
    for path in ("assets/missing.pdf", "assets/../../outside.txt"):
        with pytest.raises(ValueError, match="添付ファイル"):
            export_markdown(f"[broken]({path})", manager.base_dir, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_export_rejects_path_filename_and_does_not_mutate_note(tmp_path):
    manager = ManagedAssets(tmp_path / "note")
    with pytest.raises(ValueError):
        export_markdown("body", manager.base_dir, tmp_path / "export", "../outside.md")
    assert not manager.base_dir.exists()


def test_empty_general_attachment_and_no_extension_are_supported(tmp_path):
    source = tmp_path / "README"
    source.touch()
    asset = ManagedAssets(tmp_path / "note").import_file(source)
    assert asset.size == 0
    assert not asset.is_image
    assert asset.path.suffix == ""
    with pytest.raises(ValueError, match="画像"):
        ManagedAssets(tmp_path / "note").save_bytes(b"", "png")


def test_named_copy_preserves_original_and_publishes_unicode_name(tmp_path):
    manager = ManagedAssets(tmp_path / "note")
    original = manager.save_bytes(b"original-image", "png")
    copied = manager.copy_named(original.path, "新しい 写真 [1].png")
    assert copied.path.read_bytes() == original.path.read_bytes() == b"original-image"
    assert copied.path.name == "新しい 写真 [1].png"
    assert (
        resolve_managed_asset(markdown_destinations(copied.markdown)[0].url, manager.base_dir)
        == copied.path
    )
    with pytest.raises(FileExistsError):
        manager.copy_named(original.path, copied.path.name)
    assert copied.path.read_bytes() == b"original-image"


def test_named_copy_failure_and_invalid_target_leave_original_intact(tmp_path, monkeypatch):
    manager = ManagedAssets(tmp_path / "note")
    original = manager.save_bytes(b"image", "png")
    for name in ["../escape.png", "other.jpg", "folder/a.png", "bad:stream.png"]:
        with pytest.raises(ValueError):
            manager.copy_named(original.path, name)

    def fail(*_args):
        raise OSError("disk error")

    monkeypatch.setattr("marknotes.managed_assets.os.replace", fail)
    with pytest.raises(OSError):
        manager.copy_named(original.path, "new.png")
    assert not (manager.assets_dir / "new.png").exists()
    assert original.path.read_bytes() == b"image"


def test_unicode_file_extension_is_retained_and_url_encoded(tmp_path):
    source = tmp_path / "独自形式.資料"
    source.write_bytes(b"application-data")
    manager = ManagedAssets(tmp_path / "note")
    asset = manager.import_file(source)
    assert asset.path.suffix == ".資料"
    assert "%" in asset.markdown
    assert (
        resolve_managed_asset(markdown_destinations(asset.markdown)[0].url, manager.base_dir)
        == asset.path
    )
