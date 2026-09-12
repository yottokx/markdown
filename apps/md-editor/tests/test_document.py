"""Document saves must preserve bytes, image references and user data on error."""

import codecs

import pytest
from PySide6.QtGui import QImage

from md_editor.assets import managed_image_path, markdown_destinations
from md_editor.document import (
    DocumentSession,
    ExternalFileChangedError,
    NeedsNewlineSelection,
    decode_document,
    encode_document,
)


@pytest.fixture
def session():
    document = DocumentSession()
    yield document
    document.close()


def picture():
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0xFFAABBCC)
    return image


@pytest.mark.parametrize(
    "encoding,bom",
    [
        ("utf-8", b""),
        ("utf-8-sig", codecs.BOM_UTF8),
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
        ("cp932", b""),
        ("euc_jp", b""),
    ],
)
@pytest.mark.parametrize("newline", ["LF", "CRLF", "CR"])
def test_format_roundtrip_is_byte_identical(tmp_path, session, encoding, bom, newline):
    text = "# 日本語の文書\n文字コードと改行を確認します。\n末尾改行なし"
    original = encode_document(text, encoding, newline, bom)
    path = tmp_path / "文書.md"
    path.write_bytes(original)
    loaded = session.open(path)
    assert loaded == text
    assert session.newline == newline
    session.save(loaded)
    assert path.read_bytes() == original


def test_preserves_trailing_newline_and_empty_document(tmp_path, session):
    for index, text in enumerate(("", "a\n", "a\n\n")):
        session.new()
        result = session.save(text, tmp_path / f"{index}.md")
        assert result.path.read_bytes() == text.encode()


def test_mixed_newlines_require_explicit_save_choice(tmp_path, session):
    path = tmp_path / "mixed.md"
    original = b"a\r\nb\nc\r"
    path.write_bytes(original)
    text = session.open(path)
    assert session.mixed_newlines
    with pytest.raises(NeedsNewlineSelection):
        session.save(text)
    assert path.read_bytes() == original
    session.save(text, newline="CRLF")
    assert path.read_bytes() == b"a\r\nb\r\nc\r\n"
    assert not session.mixed_newlines


def test_unrepresentable_text_does_not_replace_existing_file(tmp_path, session):
    path = tmp_path / "legacy.md"
    original = "日本語".encode("cp932")
    path.write_bytes(original)
    session.open(path, encoding="cp932")
    with pytest.raises(UnicodeEncodeError):
        session.save("日本語😀")
    assert path.read_bytes() == original
    assert session.encoding == "cp932"
    session.save("日本語😀", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == "日本語😀"


def test_open_failure_preserves_unsaved_image_and_state(tmp_path, session):
    md = session.add_image(picture())
    base = session.base_dir
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        session.open(bad, encoding="utf-8")
    assert session.path is None
    assert session.base_dir == base
    assert managed_image_path(markdown_destinations(md)[0].url, base).is_file()


def test_first_save_copies_referenced_images_and_keeps_history(tmp_path, session):
    first = session.add_image(picture())
    unused = session.add_image(picture())
    old_base = session.base_dir
    text = first + "\n`" + unused + "`"
    result = session.save(text, tmp_path / "初回.md")
    assert result.rewritten
    assert result.path.read_text(encoding="utf-8") == result.text
    assert len(list((tmp_path / "img").iterdir())) == 1
    assert "初回_00001.png" in next((tmp_path / "img").iterdir()).name
    assert old_base.exists()
    assert len(list((old_base / "img").iterdir())) == 2
    assert session.normalize_references(text) == result.text
    assert unused in result.text  # code sample remains unchanged


def test_save_as_copies_shared_asset_once_rebases_links_and_retains_original(tmp_path, session):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    session.save("", one / "first.md")
    image = session.add_image(picture())
    image_url = markdown_destinations(image)[0].url
    source = image + "\n[download](" + image_url + ")\n[doc](docs/readme.md#part)"
    first_result = session.save(source)
    original = (one / "first.md").read_bytes()
    result = session.save(source, two / "second.md")
    assert len(list((two / "img").iterdir())) == 1
    assert result.text.count("img/second_00001.png") == 2
    assert "../one/docs/readme.md#part" in result.text
    assert (one / "first.md").read_bytes() == original
    assert session.normalize_references(first_result.text) == result.text
    assert managed_image_path(image_url, one).is_file()


def test_same_directory_save_as_keeps_existing_image_name(tmp_path, session):
    session.save("", tmp_path / "old.md")
    source = session.add_image(picture())
    session.save(source)
    result = session.save(source, tmp_path / "new.md")
    assert result.text == source
    assert "new_00001.png" in session.add_image(picture())


def test_repeated_save_as_normalizes_undo_source(tmp_path, session):
    original = session.add_image(picture())
    histories = [original]
    for name in ("one", "two", "three"):
        folder = tmp_path / name
        folder.mkdir()
        result = session.save(histories[-1], folder / f"{name}.md")
        histories.append(result.text)
    for old_text in histories:
        assert session.normalize_references(old_text) == histories[-1]
    # Saving a pre-first-save undo snapshot writes the current valid reference.
    assert session.save(original).text == histories[-1]


def test_failed_atomic_save_rolls_back_new_assets_and_preserves_temp(
    tmp_path, session, monkeypatch
):
    import md_editor.document as module

    source = session.add_image(picture())
    old_base = session.base_dir
    target = tmp_path / "doc.md"
    target.write_text("untouched", encoding="utf-8")

    def fail(*args):
        raise OSError("simulated full disk")

    monkeypatch.setattr(module, "atomic_write", fail)
    with pytest.raises(OSError, match="full disk"):
        session.save(source, target)
    assert target.read_text() == "untouched"
    assert session.path is None
    assert session.base_dir == old_base
    assert session.normalize_references(source) == source
    assert len(list((old_base / "img").iterdir())) == 1
    assert not list((tmp_path / "img").iterdir())


def test_external_change_is_not_overwritten_without_explicit_override(tmp_path, session):
    path = tmp_path / "doc.md"
    path.write_text("original", encoding="utf-8")
    session.open(path)
    path.write_text("external", encoding="utf-8")
    assert session.has_external_change()
    with pytest.raises(ExternalFileChangedError):
        session.save("mine")
    assert path.read_text() == "external"
    session.save("mine", overwrite_external=True)
    assert path.read_text() == "mine"


def test_strict_manual_encoding_and_bom_retention():
    source = codecs.BOM_UTF8 + "日本語".encode()
    decoded = decode_document(source, "utf-8")
    assert decoded.text == "日本語"
    assert decoded.encoding == "utf-8-sig"
    assert encode_document(decoded.text, decoded.encoding, decoded.newline, decoded.bom) == source


def test_redo_unused_temp_image_then_save_remains_valid_after_close(tmp_path, session):
    image = session.add_image(picture())
    temporary = session.base_dir
    session.save("", tmp_path / "restored.md")
    assert not (tmp_path / "img").exists()
    restored_preview = session.normalize_references(image)
    assert restored_preview != image
    # Native Redo has restored the pre-save source, so the next save must promote it.
    result = session.save(image)
    assert "img/restored_00001.png" in result.text
    session.close()
    assert not temporary.exists()
    reopened = DocumentSession()
    try:
        text = reopened.open(result.path)
        url = markdown_destinations(text)[0].url
        assert managed_image_path(url, reopened.base_dir).is_file()
    finally:
        reopened.close()


def test_loaded_then_deleted_image_can_be_restored_after_save_as(tmp_path, session):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    session.save("", old / "old.md")
    image = session.add_image(picture())
    session.save(image)
    session.open(old / "old.md")
    session.save("", new / "new.md")
    assert not (new / "img").exists()
    result = session.save(image)
    assert "img/new_00001.png" in result.text
    assert (new / "img/new_00001.png").is_file()
    assert (old / "img/old_00001.png").is_file()


def test_initial_save_in_adopted_directory_resolves_temporary_names(tmp_path, session):
    session.new(base_dir=tmp_path)
    source = session.add_image(picture())
    result = session.save(source, tmp_path / "named.md")
    assert "img/named_00001.png" in result.text
    assert (tmp_path / "img/untitled_00001.png").is_file()


def test_remember_manual_link_before_deletion_preserves_undo_context(tmp_path, session):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    session.save("", old / "doc.md")
    history = "[manual link](docs/readme.md)"
    session.remember_references(history)
    session.save("", new / "doc.md")
    assert session.normalize_references(history) == "[manual link](../old/docs/readme.md)"


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_bomless_utf16_with_ascii_markdown_is_detected_and_preserved(tmp_path, session, encoding):
    text = "# Title\n日本語の本文\n"
    path = tmp_path / "bomless.md"
    original = text.encode(encoding)
    path.write_bytes(original)
    assert session.open(path) == text
    assert session.encoding == encoding
    assert session.bom == b""
    assert session.encoding_warning
    session.save(text)
    assert path.read_bytes() == original


def test_generic_utf16_manual_open_obeys_bom_endianness():
    data = codecs.BOM_UTF16_BE + "日本語".encode("utf-16-be")
    result = decode_document(data, "utf-16")
    assert result.text == "日本語"
    assert result.encoding == "utf-16-be"


def test_new_reference_after_save_as_uses_destination_directory_not_historical_alias(
    tmp_path, session
):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (new / "img").mkdir()
    assert picture().save(str(new / "img/doc_00001.png"))
    session.save("", old / "doc.md")
    historical = session.add_image(picture())
    session.save(historical)
    result = session.save(historical, new / "doc.md")
    assert "doc_00002.png" in result.text
    # The UI applies the canonical save result as a normal undoable edit.
    session.remember_references(result.text)
    fresh = "![different local image](img/doc_00001.png)"
    session.remember_references(fresh)
    assert session.normalize_references(fresh) == fresh
    assert session.save(fresh).text == fresh
    assert "doc_00002.png" in session.normalize_references(historical)


def test_old_and_new_occurrences_of_same_url_keep_distinct_origins(tmp_path, session):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (new / "img").mkdir()
    assert picture().save(str(new / "img/doc_00001.png"))
    session.save("", old / "doc.md")
    historical = session.add_image(picture())
    session.save(historical)
    result = session.save(historical, new / "doc.md")
    session.remember_references(result.text)
    # Undo the save's URL rewrite, then append a NEW reference to an existing
    # image in the new directory, using exactly the same URL as the old source.
    session.remember_references(historical)
    mixed = historical + "\n![new local](img/doc_00001.png)"
    session.remember_references(mixed)
    normalized = session.normalize_references(mixed)
    assert normalized.splitlines()[0].endswith("img/doc_00002.png)")
    assert normalized.splitlines()[1] == "![new local](img/doc_00001.png)"
    assert session.save(mixed).text == normalized


def test_editing_alt_text_after_undo_retains_original_image_origin(tmp_path, session):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    session.save("", old / "doc.md")
    historical = session.add_image(picture())
    result = session.save(historical, new / "new.md")
    session.remember_references(result.text)
    session.remember_references(historical)
    edited = historical.replace("画像", "alt text changed")
    session.remember_references(edited)
    assert session.normalize_references(edited) == result.text.replace("画像", "alt text changed")


def test_cross_drive_historical_temp_image_renders_and_promotes_on_save(
    tmp_path, session, monkeypatch
):
    import md_editor.document as module

    historical = session.add_image(picture())
    temporary = session.base_dir
    image_path = managed_image_path(markdown_destinations(historical)[0].url, temporary)
    session.save("", tmp_path / "restored.md")

    def different_drives(*args):
        raise ValueError("path is on mount C:, start on mount D:")

    monkeypatch.setattr(module.os.path, "relpath", different_drives)
    normalized = session.normalize_references(historical)
    assert markdown_destinations(normalized)[0].url == image_path.as_uri()
    result = session.save(historical)
    assert markdown_destinations(result.text)[0].url == "img/restored_00001.png"
    assert (tmp_path / "img/restored_00001.png").is_file()
    session.close()
    assert not temporary.exists()
    assert (tmp_path / "img/restored_00001.png").is_file()


def test_cross_drive_unmanaged_links_use_encoded_file_uri(tmp_path, session, monkeypatch):
    import md_editor.document as module

    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    original = old / "doc.md"
    original.write_text("[link](docs/a%20b.md?view=1#part)", encoding="utf-8")
    text = session.open(original)

    def different_drives(*args):
        raise ValueError("different drives")

    monkeypatch.setattr(module.os.path, "relpath", different_drives)
    result = session.save(text, new / "doc.md")
    url = markdown_destinations(result.text)[0].url
    assert url == (old / "docs/a b.md").as_uri() + "?view=1#part"
    assert session.normalize_references(text) == result.text
    assert original.read_text(encoding="utf-8") == text


def test_save_as_preserves_math_body_and_copies_only_real_image_reference(tmp_path, session):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    session.save("", old / "doc.md")
    actual = session.add_image(picture())
    math_image_text = session.add_image(picture())
    math = "$$\n" + r"\text{" + math_image_text + "}\n$$"
    source = math + "\n\n" + actual
    session.save(source)
    result = session.save(source, new / "new.md")
    assert math in result.text
    assert "img/new_00001.png" in result.text
    assert len(list((new / "img").iterdir())) == 1
    assert len(list((old / "img").iterdir())) == 2
