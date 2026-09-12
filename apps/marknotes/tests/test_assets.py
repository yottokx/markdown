"""Verify real Markdown references and collision-safe local image creation."""

import base64
from urllib.parse import unquote

import pytest
from PySide6.QtGui import QImage

from marknotes.assets import (
    AssetManager,
    managed_image_path,
    markdown_destinations,
    rebase_url,
    rewrite_destinations,
)


def picture():
    image = QImage(8, 5, QImage.Format.Format_RGB32)
    image.fill(0xFF33AA66)
    return image


def test_image_names_share_sequence_and_do_not_overwrite(tmp_path):
    manager = AssetManager(tmp_path, "議事録")
    first = manager.add_image(picture())
    first_path = managed_image_path(markdown_destinations(first)[0].url, tmp_path)
    original = first_path.read_bytes()
    jpg = tmp_path / "写真.dat"
    assert picture().save(str(jpg), "JPEG")
    second = manager.import_image_file(jpg)
    assert unquote(markdown_destinations(second)[0].url) == "img/議事録_00002.jpg"
    assert (tmp_path / "img/議事録_00002.jpg").read_bytes() == jpg.read_bytes()
    assert first_path.read_bytes() == original


def test_import_gif_preserves_original_bytes(tmp_path):
    gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
    source = tmp_path / "source.gif"
    source.write_bytes(gif)
    markdown = AssetManager(tmp_path, "doc").import_image_file(source)
    dest = managed_image_path(markdown_destinations(markdown)[0].url, tmp_path)
    assert dest.suffix == ".gif"
    assert dest.read_bytes() == gif


def test_null_or_invalid_image_creates_no_assets(tmp_path):
    manager = AssetManager(tmp_path)
    with pytest.raises(ValueError):
        manager.add_image(QImage())
    bad = tmp_path / "fake.png"
    bad.write_text("not an image")
    with pytest.raises(ValueError):
        manager.import_image_file(bad)
    assert not (tmp_path / "img").exists()


def test_only_parsed_destinations_are_rewritten():
    text = (
        "![x](img/a.png) and [download](img/a.png)\n\n"
        "`![sample](img/a.png)`\n\n"
        "```md\n![sample](img/a.png)\n```\n\n"
        "    ![indented code](img/a.png)\n\n"
        r"\![escaped](img/a.png)"
        "\n\n"
        '![ref][photo]\n\n[photo]: <img/a.png> "caption"\n'
        "[unused]: img/a.png\n"
    )
    result = rewrite_destinations(text, lambda d: "img/new.png")
    assert result.count("img/new.png") == 4  # escaped ! still leaves a real ordinary link
    assert "`![sample](img/a.png)`" in result
    assert "```md\n![sample](img/a.png)" in result
    assert "    ![indented code](img/a.png)" in result
    assert "[unused]: img/a.png" in result
    assert '[photo]: <img/new.png> "caption"' in result


@pytest.mark.parametrize(
    "text,count",
    [
        ("![x](img/a(b).png)", 1),
        (r"![x](img/a\(b\).png)", 1),
        ("[![x](img/a.png)](docs/page.md)", 2),
        ("> - ![x](img/a.png)\n>   ![y](img/b.png)", 2),
        ("| a | b |\n| - | - |\n| ![x](img/a.png) | ![y](img/b.png) |", 2),
        (r"\[fake](img/a.png)", 0),
        ("![x](<img/a%20b.png>)", 1),
    ],
)
def test_markdown_position_mapping(text, count):
    found = markdown_destinations(text)
    assert len(found) == count
    for d in found:
        assert text[d.start : d.end]


def test_managed_path_rejects_escape_absolute_and_remote(tmp_path):
    manager = AssetManager(tmp_path)
    md = manager.add_image(picture())
    url = markdown_destinations(md)[0].url
    assert managed_image_path(url, tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    for bad in (
        "img/../outside.png",
        "img/%2e%2e/outside.png",
        str(outside),
        "https://example.com/img/a.png",
        "img/../../outside.png",
        "image/a.png",
    ):
        assert managed_image_path(bad, tmp_path) is None


def test_managed_path_rejects_symbolic_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.png").write_bytes(b"x")
    base = tmp_path / "doc"
    base.mkdir()
    try:
        (base / "img").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    assert managed_image_path("img/x.png", base) is None
    with pytest.raises(ValueError):
        AssetManager(base).add_image(picture())


def test_rebase_preserves_query_fragment_and_nonlocal_urls(tmp_path):
    old, new = tmp_path / "one", tmp_path / "two"
    assert rebase_url("docs/a%20b.md?q=1#section", old, new) == "../one/docs/a%20b.md?q=1#section"
    for url in ("#local", "https://example.org/a", "data:image/png;base64,abc", "/absolute.png"):
        assert rebase_url(url, old, new) is None


def test_table_escaped_pipe_before_image_does_not_hide_asset():
    source = "| a | b |\n| - | - |\n| a \\| ![x](img/a.png) | y |"
    result = rewrite_destinations(source, lambda d: "img/new.png")
    assert "a \\| ![x](img/new.png)" in result


def test_quoted_multiline_reference_definition_is_rewritten():
    source = "> [a]:\n>   img/a.png\n>\n> ![x][a]"
    result = rewrite_destinations(source, lambda d: "img/new.png")
    assert result == "> [a]:\n>   img/new.png\n>\n> ![x][a]"


@pytest.mark.parametrize(
    "math",
    [
        r"$\text{![fake](img/fake.png) [link](docs/fake.md)}$",
        r"\(\text{![fake](img/fake.png) [link](docs/fake.md)}\)",
        "$$\n" + r"\text{![fake](img/fake.png) [link](docs/fake.md)}" + "\n$$",
        "\\[\n" + r"\text{![fake](img/fake.png) [link](docs/fake.md)}" + "\n\\]",
    ],
)
def test_tex_bodies_are_opaque_to_image_and_link_rewriting(math):
    source = "[before](docs/before.md)\n\n" + math + "\n\n![after](img/after.png)"
    destinations = markdown_destinations(source)
    assert [destination.url for destination in destinations] == ["docs/before.md", "img/after.png"]
    rewritten = rewrite_destinations(source, lambda destination: "new/" + destination.url)
    assert math in rewritten
    assert "[before](new/docs/before.md)" in rewritten
    assert "![after](new/img/after.png)" in rewritten
