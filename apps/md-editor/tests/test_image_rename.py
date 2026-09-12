"""Image rename transactions preserve document bytes, history and shared assets."""

import codecs
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage

import md_editor.image_rename as renames
from md_editor.assets import local_path, markdown_destinations
from md_editor.document import DocumentSession, ExternalFileChangedError
from md_editor.image_rename import (
    bulk_rename_plan,
    current_images,
    execute_rename,
    finalize_rename,
    image_at,
    make_rename_plan,
    recovery_records,
    validate_image_name,
)


def png(path, width=16, color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, 12, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG")
    return path


@pytest.fixture
def session(tmp_path):
    instance = DocumentSession(recovery_root=tmp_path / "recovery")
    instance.adopt(base_dir=tmp_path / "workspace")
    instance.base_dir.mkdir(parents=True, exist_ok=True)
    yield instance
    instance.close()


def saved(session, text, encoding="utf-8", newline="LF"):
    path = session.base_dir / "note.md"
    path.write_bytes(
        (codecs.BOM_UTF8 if encoding == "utf-8-sig" else b"")
        + text.replace("\n", {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}[newline]).encode(
            "utf-8" if encoding == "utf-8-sig" else encoding
        )
    )
    return session.open(path)


def test_rename_all_actual_refs_preserves_nonimage_source(session):
    old = png(session.base_dir / "img" / "old.png")
    source = """![first](img/old.png "title") and [download](img/old.png)

![second][same] and [shared download][same]

[same]: img/old.png 'reference title'

<img data-src="img/fake.png" alt='src="img/fake2.png"' src="img/old.png?x=1&amp;y=2">

`![code](img/old.png)`

$$
![math](img/old.png)
$$
"""
    saved(session, source)
    assert len(current_images(session, source)) == 1
    assert image_at(session, source, source.index("first")) is not None
    assert image_at(session, source, source.index("second")) is not None
    assert image_at(session, source, source.index("![code]")) is None
    result = session.rename_images(source, {old: "日本語 (新).png"})
    refs = markdown_destinations(result.text)
    assert len(refs) == 4
    assert all("old.png" not in d.url for d in refs)
    assert 'data-src="img/fake.png"' in result.text
    assert "alt='src=\"img/fake2.png\"'" in result.text
    assert "?x=1&amp;y=2" in result.text
    assert "`![code](img/old.png)`" in result.text
    assert "![math](img/old.png)" in result.text
    assert session.path.read_text("utf-8") == result.text
    assert old.exists()
    target = old.with_name("日本語 (新).png")
    assert target.read_bytes() == old.read_bytes()
    session.close()
    assert not old.exists()
    assert target.exists()


def test_only_current_managed_actual_images_and_svg(session):
    base = session.base_dir
    inside = png(base / "img" / "valid.png")
    png(base / "outside.png")
    (base / "img" / "fake.png").write_text("not an image")
    svg = base / "img" / "vector.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20"/></svg>'
    )
    source = "\n".join(
        f"![image]({url})"
        for url in [
            "img/valid.png",
            "outside.png",
            "img/fake.png",
            "img/missing.png",
            "https://example.com/img/a.png",
            "img/../outside.png",
            "img/vector.svg",
        ]
    )
    images = current_images(session, source)
    assert [item.path for item in images] == [inside, svg]
    assert [item.raster for item in images] == [True, False]
    with pytest.raises(ValueError):
        make_rename_plan(session, source, {base / "outside.png": "other.png"})


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../a.png",
        "sub/a.png",
        "bad?.png",
        "bad .png ",
        "a.png.",
        "CON.png",
        "lPt9.png",
        "a.jpg",
    ],
)
def test_bad_names_rejected(name):
    with pytest.raises(ValueError):
        validate_image_name(name, Path("old.png"))


def test_bulk_first_occurrence_only_preserves_type_and_skips_collisions(session):
    base = session.base_dir
    a, b = png(base / "img" / "a.png"), png(base / "img" / "b.png", 20)
    unused = png(base / "img" / "note_00001.png", 24)
    source = "![b](img/b.png)\n![a](img/a.png)\n![again](img/b.png)"
    saved(session, source)
    plan = bulk_rename_plan(session, source)
    assert [entry.source for entry in plan.entries] == [b, a]
    assert [entry.target.name for entry in plan.entries] == ["note_00002.png", "note_00003.png"]
    result = execute_rename(session, plan)
    assert unused.exists() and "note_00001" not in result.text
    assert len(list((base / "img").iterdir())) == 5
    session.close()
    assert sorted(p.name for p in (base / "img").iterdir()) == [
        "note_00001.png",
        "note_00002.png",
        "note_00003.png",
    ]


def test_existing_and_history_names_never_overwritten(session):
    first = png(session.base_dir / "img" / "first.png")
    taken = png(first.with_name("taken.png"), 23)
    source = "![one](img/first.png)"
    session.remember_references(source + "\n![two](img/taken.png)", history=False, history_key=1)
    session.remember_references(source, history=False, history_key=2)
    with pytest.raises(ValueError):
        make_rename_plan(session, source, {first: taken.name})
    taken.unlink()
    with pytest.raises(ValueError):
        make_rename_plan(session, source, {first: taken.name})
    assert first.exists()


@pytest.mark.parametrize(
    "encoding,newline", [("utf-8-sig", "CRLF"), ("cp932", "CR"), ("utf-8", "LF")]
)
def test_saved_format_and_modified_body_preserved(session, encoding, newline):
    old = png(session.base_dir / "img" / "old.png")
    source = "# 日本語\n\n![画像](img/old.png)\n末尾"
    saved(session, source, encoding, newline)
    edited = source + "追加"
    result = session.rename_images(edited, {old: "new.png"})
    expected = result.text.replace("\n", {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}[newline])
    assert session.path.read_bytes() == expected.encode(encoding)
    assert session.newline == newline
    assert not session.has_external_change()


def test_undo_source_remains_valid_and_can_be_saved(session):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    session.remember_references(source, history=False, history_key=0)
    result = session.rename_images(source, {old: "new.png"})
    session.remember_references(result.text, history=False, history_key=1)
    session.remember_references(source, history=True, history_key=0)
    assert session.normalize_references(source) == source
    assert old.exists()
    session.save(source)
    session.close()
    assert old.exists()
    assert session.path.read_text("utf-8") == source


@pytest.mark.parametrize("style", ["inline", "reference", "html", "file_uri"])
def test_shared_sibling_document_retains_old_asset(session, style):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    other = session.base_dir / "other.md"
    shared = {
        "inline": "![x](img/old.png)",
        "reference": "![x][id]\n\n[id]: img/old.png",
        "html": '<img src="img/old.png">',
        "file_uri": f"![x]({QUrl.fromLocalFile(str(old)).toString()})",
    }[style]
    other.write_text(shared, "utf-8")
    plan = make_rename_plan(session, source, {old: "new.png"})
    assert plan.entries[0].shared_with == (other,)
    execute_rename(session, plan)
    session.close()
    assert old.exists()
    assert old.with_name("new.png").exists()
    assert other.read_text("utf-8") == shared


def test_disk_save_failure_restores_everything(session, monkeypatch):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    before = session.path.read_bytes()
    original_write = renames.atomic_write

    def fail_document(path, data):
        if path == session.path:
            raise PermissionError("read-only Markdown")
        return original_write(path, data)

    monkeypatch.setattr(renames, "atomic_write", fail_document)
    with pytest.raises(PermissionError):
        session.rename_images(source, {old: "new.png"})
    assert old.exists() and not old.with_name("new.png").exists()
    assert session.path.read_bytes() == before
    assert not session.has_external_change()
    assert not session._rename_journals
    assert recovery_records(session.recovery_root)[0][1]["state"] == "rolled_back"


def test_external_markdown_change_refused_without_assets_mutation(session):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    session.path.write_text("outside edit", "utf-8")
    with pytest.raises(ExternalFileChangedError):
        session.rename_images(source, {old: "new.png"})
    assert not old.with_name("new.png").exists()
    assert session.path.read_text("utf-8") == "outside edit"


def test_case_only_rename_updates_source_and_real_spelling(session):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    result = session.rename_images(source, {old: "OLD.PNG"})
    assert result.text == "![one](img/OLD.PNG)"
    assert "OLD.PNG" in [p.name for p in old.parent.iterdir()]
    assert "old.png" not in [p.name for p in old.parent.iterdir()]
    assert old.exists()  # Windows accepts Undo's spelling without another file.


def test_recovery_after_commit_restores_missing_new_image(session):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    result = session.rename_images(source, {old: "new.png"})
    directory = session._rename_journals[-1]
    new = old.with_name("new.png")
    new.unlink()
    assert finalize_rename(directory, recovering=True)
    assert new.exists() and not old.exists()
    assert session.path.read_text("utf-8") == result.text


def test_prepared_recovery_rolls_back_only_owned_files(session):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    session.rename_images(source, {old: "new.png"})
    directory = session._rename_journals[-1]
    record_path = directory / "transaction.json"
    record = json.loads(record_path.read_text("utf-8"))
    record["state"] = "prepared"
    record_path.write_text(json.dumps(record), "utf-8")
    session.path.write_bytes((directory / "document.before").read_bytes())
    assert finalize_rename(directory, recovering=True)
    assert old.exists() and not old.with_name("new.png").exists()
    assert session.path.read_text("utf-8") == source


def test_recovery_never_overwrites_external_edits(session):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    session.rename_images(source, {old: "new.png"})
    directory = session._rename_journals[-1]
    session.path.write_text("external MD change", "utf-8")
    png(old, 30, "blue")
    old_data = old.read_bytes()
    new = png(old.with_name("new.png"), 40, "yellow")
    new_data = new.read_bytes()
    finalize_rename(directory, recovering=True)
    assert session.path.read_text("utf-8") == "external MD change"
    assert old.read_bytes() == old_data
    assert new.read_bytes() == new_data


def test_unsaved_recovery_copy_contains_all_referenced_images(session):
    old = png(session.base_dir / "img" / "old.png")
    other = png(old.with_name("other.png"), 28)
    source = "![one](img/old.png)\n![two](img/other.png)"
    result = session.rename_images(source, {old: "new.png"})
    assert result.path is None
    directory = session._rename_journals[-1]
    recovered = (directory / "recovered.md").read_text("utf-8")
    assert "img/new.png" in recovered and "img/other.png" in recovered
    for destination in markdown_destinations(recovered):
        assert local_path(destination.url, directory).is_file()
    assert (directory / "img" / "other.png").read_bytes() == other.read_bytes()


def test_srcset_images_rename_and_shared_sibling_remains_valid(session):
    old = png(session.base_dir / "img" / "old.png")
    large = png(old.with_name("large.png"), 32)
    source = '<picture><source srcset="img/old.png?x=1&amp;y=2&#32;1x, img/large.png 2x"><img src="img/old.png"></picture>'
    saved(session, source)
    other = session.base_dir / "other.md"
    other.write_text(
        '<picture><source srcset="img/old.png 1x"><img src="fallback.png"></picture>', "utf-8"
    )
    plan = make_rename_plan(session, source, {old: "new.png"})
    assert plan.entries[0].shared_with == (other,)
    result = execute_rename(session, plan)
    assert "img/new.png?x=1&amp;y=2&#32;1x" in result.text
    assert '<img src="img/new.png">' in result.text
    assert "img/large.png 2x" in result.text
    assert [item.path for item in current_images(session, source)] == [old, large]
    session.close()
    assert old.exists()


def test_reference_image_usage_determines_bulk_order(session):
    first = png(session.base_dir / "img" / "first.png")
    second = png(first.with_name("second.png"), 23)
    source = "![first][id]\n\n![second](img/second.png)\n\n[id]: img/first.png"
    saved(session, source)
    assert [entry.source for entry in bulk_rename_plan(session, source).entries] == [first, second]


def test_journal_failure_and_external_md_change_retains_new_reference(session, monkeypatch):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    original_write = renames._record_write

    def competing_writer(directory, record):
        if record["state"] == "committed":
            session.path.write_text("![one](img/new.png)\nexternal edit\n", "utf-8")
            raise OSError("journal unavailable")
        return original_write(directory, record)

    monkeypatch.setattr(renames, "_record_write", competing_writer)
    with pytest.raises(OSError):
        session.rename_images(source, {old: "new.png"})
    assert old.exists() and old.with_name("new.png").exists()
    assert session.path.read_text("utf-8").endswith("external edit\n")
    directory, record = recovery_records(session.recovery_root)[0]
    assert record["state"] == "conflict"
    finalize_rename(directory, recovering=True)
    assert old.with_name("new.png").exists()


def test_image_modified_while_preparing_rename_is_preserved(session, monkeypatch):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    original_write = renames._write_exclusive

    def edit_original(path, data):
        original_write(path, data)
        if path == old.with_name("new.png"):
            png(old, 40, "blue")

    monkeypatch.setattr(renames, "_write_exclusive", edit_original)
    with pytest.raises(OSError, match="画像が操作中"):
        session.rename_images(source, {old: "new.png"})
    assert QImage(str(old)).width() == 40
    assert not old.with_name("new.png").exists()
    assert session.path.read_text("utf-8") == source


def test_corrupt_recovery_json_does_not_prevent_document_open(session):
    source = "ordinary document"
    document = session.base_dir / "note.md"
    document.write_text(source, "utf-8")
    for index, value in enumerate(
        [
            [],
            None,
            {"schema": 1, "entries": ["invalid"]},
            {
                "schema": 1,
                "base_dir": str(session.base_dir),
                "document": str(document),
                "state": "prepared",
                "before_sha256": "x",
                "after_sha256": "y",
                "entries": [{"old": []}],
            },
        ]
    ):
        directory = session.recovery_root / str(index)
        directory.mkdir(parents=True)
        (directory / "transaction.json").write_text(json.dumps(value), "utf-8")
    assert session.open(document) == source


def test_uncertain_sibling_codec_keeps_old_image(session):
    old = png(session.base_dir / "img" / "old.png")
    source = "![one](img/old.png)"
    saved(session, source)
    other = session.base_dir / "other.md"
    other.write_bytes("# 日本語文書\n![共用画像](img/old.png)".encode("cp932"))
    session.rename_images(source, {old: "new.png"})
    session.close()
    assert old.exists()


def test_custom_bulk_prefix_skips_existing_and_historical_names(session):
    first = png(session.base_dir / "img" / "first.png")
    second = png(first.with_name("second.png"), 25)
    existing = png(first.with_name("figure_007.png"), 30)
    historical = png(first.with_name("figure_009.png"), 35)
    source = "![first](img/first.png)\n\n![second](img/second.png)"
    saved(session, source)
    session.remember_references(
        source + "\n![deleted](img/figure_009.png)", history=False, history_key=1
    )
    session.remember_references(source, history=False, history_key=2)
    historical.unlink()
    before = session.path.read_bytes()
    plan = bulk_rename_plan(session, source, prefix="figure_", start_number=7, digits=3)
    assert [entry.source for entry in plan.entries] == [first, second]
    assert [entry.target.name for entry in plan.entries] == ["figure_008.png", "figure_010.png"]
    assert session.path.read_bytes() == before
    assert existing.exists() and not historical.exists()
    assert not first.with_name("figure_008.png").exists()


def test_custom_bulk_number_width_grows_and_empty_prefix_is_valid(session):
    first = png(session.base_dir / "img" / "a.png")
    png(first.with_name("b.png"), 25)
    source = "![a](img/a.png)\n![b](img/b.png)"
    plan = bulk_rename_plan(session, source, prefix="", start_number=99, digits=2)
    assert [entry.target.name for entry in plan.entries] == ["99.png", "100.png"]
    plan = bulk_rename_plan(session, source, prefix="図-", start_number=0, digits=4)
    assert [entry.target.name for entry in plan.entries] == ["図-0000.png", "図-0001.png"]


@pytest.mark.parametrize(
    "options",
    [
        {"prefix": "../outside_"},
        {"prefix": "bad?"},
        {"prefix": " leading"},
        {"start_number": -1},
        {"start_number": 1.5},
        {"digits": 0},
        {"digits": 13},
    ],
)
def test_invalid_bulk_options_do_not_mutate_files(session, options):
    original = png(session.base_dir / "img" / "old.png")
    source = "![old](img/old.png)"
    saved(session, source)
    before = session.path.read_bytes()
    with pytest.raises(ValueError):
        bulk_rename_plan(session, source, **options)
    assert session.path.read_bytes() == before
    assert list(original.parent.iterdir()) == [original]
