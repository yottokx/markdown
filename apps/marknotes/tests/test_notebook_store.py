"""Library durability, Unicode search and tab restoration regression coverage."""

import json
import sqlite3
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import pytest

from marknotes.notebook_store import (
    MAX_CLOSED_OPERATIONS,
    NotebookStore,
    _normalized_spans,
    find_match_ranges,
    normalize_text,
    note_title,
)


@pytest.fixture
def store(tmp_path):
    return NotebookStore(tmp_path / "library")


def test_body_state_and_sessions_survive_restart_without_changing_content_date(store):
    note = store.create("# 日本語のノート\n最初のメモ")
    assert note.title == "日本語のノート"
    assert store.save(note.id, "# 日本語のノート\n更新したメモ", 1)
    updated = store.get(note.id)
    assert updated.updated_at > note.updated_at
    store.set_pinned(note.id, True)
    store.set_view_state(note.id, {"mode": "preview", "cursor": 3, "source_scroll": 42})
    store.set_session([note.id], note.id, {note.id: 123.0})
    reopened = NotebookStore(store.root)
    assert reopened.get(note.id).body == updated.body
    assert reopened.get(note.id).pinned
    assert reopened.get(note.id).updated_at == updated.updated_at
    assert reopened.get_view_state(note.id)["mode"] == "preview"
    assert reopened.get_session().order == [note.id]
    assert reopened.get_session().visited == {note.id: 123.0}


def test_outdated_saves_cannot_replace_new_body_or_index(store):
    note = store.create("old body")
    assert store.save(note.id, "new 日本語本文", 10)
    assert not store.save(note.id, "stale older content", 9)
    assert not store.save(note.id, "conflicting content", 10)
    assert store.get(note.id).revision == 10
    assert store.get(note.id).body == "new 日本語本文"
    assert store.search("日本語")
    assert not store.search("stale")
    before = store.get(note.id).updated_at
    assert store.save(note.id, "new 日本語本文", 11)
    assert store.get(note.id).updated_at == before


def test_saving_body_and_index_rolls_back_together_on_index_failure(store):
    note = store.create("before")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_search BEFORE UPDATE ON note_search_content "
            "BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="simulated disk failure"):
        store.save(note.id, "after", 1)
    assert store.get(note.id) == note
    assert store.search("before")
    assert not store.search("after")


def test_worker_connections_handle_reverse_revision_order(store):
    note = store.create()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda revision: store.save(note.id, f"body {revision}", revision),
                [3, 1, 6, 2, 8, 5, 7, 4],
            )
        )
    assert store.get(note.id).body == "body 8"
    assert store.get(note.id).revision == 8


@pytest.mark.parametrize(
    "body,title",
    [
        ("", "新しいノート"),
        ("\n# 出張の**持ち物**\nbody", "出張の持ち物"),
        ("![旅行](assets/image.png)", "旅行"),
        ("![](assets/image.png)", "画像のノート"),
        ("[議事録.pdf](assets/file.pdf)", "議事録.pdf"),
        ("<script>危険</script>", "危険"),
    ],
)
def test_titles_are_optional_and_derived_from_first_nonempty_line(body, title):
    assert note_title(body) == title


def test_list_is_paged_without_loading_bodies_and_pin_date_is_independent(store):
    first = store.create("first")
    second = store.create("second")
    third = store.create("third")
    assert [n.id for n in store.list_notes(limit=2)] == [third.id, second.id]
    assert [n.id for n in store.list_notes(limit=2, offset=2)] == [first.id]
    assert not hasattr(store.list_notes()[0], "body")
    store.set_pinned(third.id, True)
    store.set_pinned(first.id, True)
    assert [n.id for n in store.list_notes(pinned=True)] == [first.id, third.id]
    store.save(first.id, "first updated", 1)
    assert store.list_notes(order="updated")[0].id == first.id
    assert store.list_notes(order="created")[0].id == third.id
    store.set_pinned(first.id, False)
    assert [n.id for n in store.list_notes(pinned=True)] == [third.id]


def test_close_all_and_reopen_after_restart_preserve_group_order_and_selected_tab(store):
    notes = [store.create(str(index)) for index in range(4)]
    ids = [note.id for note in notes]
    store.set_view_state(ids[2], {"mode": "preview", "scroll": 20})
    store.set_session(ids, ids[2])
    assert store.close_tabs(ids, ids, ids[2]).order == []
    assert store.closed_history_count() == 1
    reopened = NotebookStore(store.root)
    state = reopened.reopen_closed()
    assert state.order == ids
    assert state.active == ids[2]
    assert reopened.closed_history_count() == 0
    assert reopened.get_view_state(ids[2])["mode"] == "preview"
    assert reopened.get(ids[2]).updated_at == notes[2].updated_at


def test_reopen_uses_latest_note_without_duplicate_or_reordering_existing_tabs(store):
    ids = [store.create(str(index)).id for index in range(5)]
    store.set_session(ids, ids[1])
    state = store.close_tabs([ids[1], ids[3]], ids, ids[1])
    assert state.order == [ids[0], ids[2], ids[4]]
    store.save(ids[1], "edited after closing", 1)
    current = [ids[4], ids[1], ids[2], ids[0]]
    store.set_session(current, ids[2])
    state = store.reopen_closed()
    assert [note_id for note_id in state.order if note_id in current] == current
    assert len(state.order) == 5
    assert state.active == ids[1]
    assert store.get(ids[1]).body == "edited after closing"


def test_close_others_reopen_selects_first_restored_note_and_history_is_lifo(store):
    a, b, c = [store.create(letter).id for letter in "abc"]
    store.set_session([a, b, c], b)
    store.close_tabs([a, c], [a, b, c], b)
    store.close_tabs([b], [b], b)
    assert store.reopen_closed().order == [b]
    result = store.reopen_closed()
    assert result.order == [a, b, c]
    assert result.active == a
    assert store.reopen_closed() is None


def test_closed_history_limit_keeps_notes_and_does_not_record_exit_or_overflow(store):
    note = store.create("retained content")
    for _ in range(MAX_CLOSED_OPERATIONS + 2):
        store.set_session([note.id], note.id)
        store.close_tabs([note.id], [note.id], note.id)
    assert store.closed_history_count() == MAX_CLOSED_OPERATIONS
    assert store.get(note.id).body == "retained content"
    store.set_session([note.id], note.id)
    store.set_session([], None)
    assert store.closed_history_count() == MAX_CLOSED_OPERATIONS


def test_close_state_and_history_are_one_transaction(store):
    note = store.create("keep me")
    store.set_session([note.id], note.id)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_history BEFORE INSERT ON closed_tab_history "
            "BEGIN SELECT RAISE(ABORT, 'close failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="close failed"):
        store.close_tabs([note.id], [note.id], note.id)
    assert store.get_session().order == [note.id]
    assert store.closed_history_count() == 0


def test_reopen_failure_preserves_history_for_retry(store):
    note = store.create("retained content")
    store.set_session([note.id], note.id)
    store.close_tabs([note.id], [note.id], note.id)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_session BEFORE UPDATE ON app_session "
            "BEGIN SELECT RAISE(ABORT, 'restore failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="restore failed"):
        store.reopen_closed()
    assert store.closed_history_count() == 1
    assert store.get_session().order == []


@pytest.mark.parametrize(
    "body,query,selected",
    [
        ("😀日本語の会議メモ", "会議", "会議"),
        ("😀日本語の会議メモ", "議", "議"),
        ("😀日本語の会議メモ", "日本語", "日本語"),
        ("ＳＴＲＡＳＳＥ Straße", "strasse", "ＳＴＲＡＳＳＥ"),
        ("cafe\u0301", "CAFÉ", "cafe\u0301"),
        ("半角ｶﾞラス", "ガラ", "ｶﾞラ"),
        ("\u1100\u1161\u11a8", "각", "\u1100\u1161\u11a8"),
        ("x\ufb03y", "FFI", "\ufb03"),
        ("C++ and OR NOT", "C++", "C++"),
        ('A "quoted" text', '"quoted"', '"quoted"'),
        ("x * OR * y", "* OR *", "* OR *"),
    ],
)
def test_literal_search_maps_normalized_hits_back_to_original(body, query, selected, store):
    note = store.create(body)
    results = store.search(query)
    assert len(results) == 1
    result = results[0]
    assert result.id == note.id
    start, end = result.matches[0]
    assert body[start:end] == selected
    snippet = result.snippets[0]
    start, end = snippet.ranges[0]
    assert snippet.text[start:end] == selected


def test_normalized_mapping_agrees_with_whole_string_nfkc_for_unicode_sequences():
    samples = [
        "a\u0315\u0300",
        "ｶﾞ",
        "\u1100\u1161\u11a8",
        "\u3131\u314f",
        "\u212b\u0301",
        "ﬃ Straße",
        "👩‍💻 Cafe\u0301",
    ]
    samples += [chr(codepoint) + "\u0301" for codepoint in range(0x20, 0x300)]
    for sample in samples:
        actual, spans = _normalized_spans(sample)
        assert actual == unicodedata.normalize("NFKC", sample).casefold()
        assert len(spans) == len(actual)


def test_casefold_expansion_is_one_visible_match_and_no_injected_html(store):
    assert find_match_ranges("ß", "s") == ((0, 1),)
    body = "<img src=x>メモ " * 2 + "." * 200 + "遠いメモ" + "." * 200 + "最後のメモ"
    store.create(body)
    result = store.search("メモ")[0]
    assert result.match_count == 4
    assert len(result.snippets) == 3
    assert len(result.snippets[0].ranges) == 2
    # Snippets are plain source text. HTML escaping belongs to the view layer.
    assert "<img src=x>" in result.snippets[0].text


def test_search_paging_cancellation_and_empty_query(store):
    ids = [store.create(f"common {index}").id for index in range(5)]
    assert [result.id for result in store.search("common", limit=2, offset=2)] == list(
        reversed(ids)
    )[2:4]
    assert not store.search("")
    assert not store.search("common", cancel=lambda: True)
    assert normalize_text("Ａ") == "a"


def test_search_falls_back_when_trigram_is_unavailable(store):
    note = store.create("日本語本文")
    store.fts_available = False
    assert store.search("日本語")[0].id == note.id


def test_rebuild_index_uses_authoritative_notes(store):
    note = store.create("rebuild 日本語検索")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE note_search_content SET search_text='wrong'")
    assert not store.search("日本語検索")
    store.rebuild_index()
    assert store.search("日本語検索")[0].id == note.id
    assert store.get(note.id) == note


def test_backup_roundtrip_includes_db_tabs_views_and_unreferenced_redo_assets(store, tmp_path):
    note = store.create("# バックアップ\n![画像](assets/picture.png)")
    asset_dir = store.assets_dir(note.id)
    (asset_dir / "picture.png").write_bytes(b"image contents")
    (asset_dir / "redo.pdf").write_bytes(b"temporarily unreferenced")
    store.register_attachment(note.id, "assets/picture.png", "元の画像.png", "image", 14)
    store.set_pinned(note.id, True)
    store.set_view_state(note.id, {"mode": "preview"})
    store.set_session([note.id], note.id)
    store.close_tabs([note.id], [note.id], note.id)
    backup = store.backup(tmp_path / "snapshot")
    store.save(note.id, "later changes", 1)
    restored_root = NotebookStore.restore_backup(backup, tmp_path / "restored")
    restored = NotebookStore(restored_root)
    assert restored.get(note.id).body == note.body
    assert restored.get(note.id).pinned
    assert restored.get_view_state(note.id)["mode"] == "preview"
    assert restored.reopen_closed().order == [note.id]
    assert (restored.assets_dir(note.id) / "redo.pdf").read_bytes() == b"temporarily unreferenced"
    assert restored.search("バックアップ")[0].id == note.id


def test_backup_and_restore_never_overwrite_an_existing_library(store, tmp_path):
    store.create("keep")
    with pytest.raises(ValueError):
        store.backup(store.root / "nested")
    backup = store.backup(tmp_path / "snapshot")
    with pytest.raises(FileExistsError):
        store.backup(backup)
    with pytest.raises(FileExistsError):
        NotebookStore.restore_backup(backup, store.root)
    assert store.list_notes()[0].title == "keep"


def test_tampered_backup_fails_before_publishing_restore_directory(store, tmp_path):
    store.create("backup")
    backup = store.backup(tmp_path / "snapshot")
    (backup / "library.sqlite3").write_bytes(b"corrupted")
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="検証"):
        NotebookStore.restore_backup(backup, destination)
    assert not destination.exists()


def test_manifest_traversal_rejected_without_writing_outside_destination(store, tmp_path):
    backup = store.backup(tmp_path / "snapshot")
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["../outside.txt"] = {"size": 0, "sha256": "ignored"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="不正"):
        NotebookStore.restore_backup(backup, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_unknown_newer_schema_is_not_downgraded(store):
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="新しい"):
        NotebookStore(store.root)


def test_invalid_ids_and_view_modes_are_rejected(store):
    with pytest.raises(ValueError):
        store.assets_dir("../../outside")
    with pytest.raises(KeyError):
        store.set_session(["missing"], "missing")
    note = store.create()
    with pytest.raises(ValueError):
        store.set_view_state(note.id, {"mode": "unknown"})
    assert store.get_view_state(note.id) == {"mode": "split"}


def test_rebuild_repairs_broken_fts_segments_and_restores_update_triggers(store):
    note = store.create("indexed marker")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE note_fts_data SET block=x'00000000' WHERE id > 10")
    # The damaged segment can return no candidates or raise SQLITE_CORRUPT,
    # depending on the bundled SQLite build. Either must be repairable.
    try:
        assert not store.search("marker")
    except sqlite3.DatabaseError:
        pass
    store.rebuild_index()
    assert store.search("marker")[0].id == note.id
    store.save(note.id, "new marker", 1)
    assert not store.search("indexed")
    assert store.search("new marker")[0].id == note.id


def test_dense_matches_do_not_transfer_the_entire_note_as_one_snippet(store):
    store.create("word " * 10000)
    result = store.search("word")[0]
    assert result.match_count == 10000
    assert len(result.snippets) == 3
    assert all(len(snippet.text) <= 240 for snippet in result.snippets)


def test_summary_is_body_free(store):
    note = store.create("a large note" * 1000)
    summary = store.get_summary(note.id)
    assert summary.id == note.id
    assert not hasattr(summary, "body")
    with pytest.raises(KeyError):
        store.get_summary("missing")


@pytest.mark.parametrize(
    "state", ["broken JSON", "[]", '{"mode":"unknown"}', '{"cursor":"wrong"}', '{"split":[1]}']
)
def test_corrupt_view_state_does_not_consume_closed_history(store, state):
    note = store.create("retained content")
    store.set_view_state(note.id, {"mode": "split"})
    store.set_session([note.id], note.id)
    store.close_tabs([note.id], [note.id], note.id)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE note_view_state SET state=? WHERE note_id=?", (state, note.id))
    with pytest.raises((ValueError, TypeError)):
        store.reopen_closed()
    assert store.closed_history_count() == 1
    assert not store.get_session().order


def test_attachment_changes_abort_backup_without_publishing_partial_snapshot(
    store, tmp_path, monkeypatch
):
    import marknotes.notebook_store as module

    note = store.create("linked attachment")
    source = store.assets_dir(note.id) / "file.pdf"
    source.write_bytes(b"before")
    original_copy = module.shutil.copy2

    def copy_and_mutate(source_path, target_path):
        result = original_copy(source_path, target_path)
        source_path.write_bytes(b"changed while copying")
        return result

    monkeypatch.setattr(module.shutil, "copy2", copy_and_mutate)
    destination = tmp_path / "snapshot"
    with pytest.raises(RuntimeError):
        store.backup(destination)
    assert not destination.exists()
    assert store.get(note.id).body == "linked attachment"
    assert not list(tmp_path.glob(".snapshot-*.tmp"))


def test_closing_untouched_note_discards_body_view_index_and_history(store):
    note = store.create()
    store.set_view_state(note.id, {"mode": "preview", "cursor": 0})
    store.set_session([note.id], note.id)
    state = store.close_tabs([note.id], [note.id], note.id)
    assert state.order == []
    assert state.active is None
    assert store.list_notes() == []
    assert store.closed_history_count() == 0
    assert store.reopen_closed() is None
    with pytest.raises(KeyError):
        store.get(note.id)
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM note_search_content").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM note_view_state").fetchone()[0] == 0
    reopened = NotebookStore(store.root)
    assert reopened.list_notes() == []
    assert reopened.get_session().order == []


def test_bulk_close_restores_only_content_bearing_notes(store):
    first = store.create("first")
    blank = store.create()
    last = store.create("last")
    ids = [first.id, blank.id, last.id]
    store.set_session(ids, blank.id)
    store.close_tabs(ids, ids, blank.id)
    assert store.closed_history_count() == 1
    state = store.reopen_closed()
    assert state.order == [first.id, last.id]
    assert state.active == first.id
    assert {note.id for note in store.list_notes()} == {first.id, last.id}


@pytest.mark.parametrize("body,revision", [("", 1), ("", 2), (" ", 0), ("\n", 0)])
def test_edited_empty_and_whitespace_notes_survive_close(store, body, revision):
    note = store.create(body)
    if revision:
        # Includes typing then deleting before the first autosave and empty MD imports.
        store.save(note.id, body, revision)
    store.set_session([note.id], note.id)
    store.close_tabs([note.id], [note.id], note.id)
    assert store.get(note.id).body == body
    assert store.reopen_closed().order == [note.id]


def test_note_with_previously_saved_content_survives_after_body_is_cleared(store):
    note = store.create("keep the identity")
    store.save(note.id, "", 1)
    store.set_session([note.id], note.id)
    store.close_tabs([note.id], [note.id], note.id)
    assert store.get(note.id).body == ""
    assert store.reopen_closed().order == [note.id]


@pytest.mark.parametrize(
    "protection", ["pin", "metadata", "asset", "staging", "unknown_file", "touched"]
)
def test_empty_note_with_pins_or_asset_evidence_is_not_discarded(store, protection):
    note = store.create()
    if protection == "pin":
        store.set_pinned(note.id, True)
    elif protection == "metadata":
        store.register_attachment(note.id, "assets/missing.pdf", "missing.pdf", "file", 10)
    elif protection == "touched":
        store.touch(note.id)
    else:
        relative = {
            "asset": "assets/image.png",
            "staging": "staging/copy-pending",
            "unknown_file": "recovery.txt",
        }[protection]
        path = store.note_dir(note.id) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data to retain")
    store.set_session([note.id], note.id)
    store.close_tabs([note.id], [note.id], note.id)
    assert store.get(note.id).body == ""
    assert store.reopen_closed().order == [note.id]
    if protection in {"asset", "staging", "unknown_file"}:
        assert path.read_bytes() == b"data to retain"


def test_empty_attachment_directories_do_not_keep_an_untouched_note(store):
    note = store.create()
    directory = store.assets_dir(note.id)
    store.set_session([note.id], note.id)
    store.close_tabs([note.id], [note.id], note.id)
    with pytest.raises(KeyError):
        store.get(note.id)
    # The storage cleanup never recursively deletes directories or user files.
    assert directory.is_dir()


def test_discard_only_applies_to_requested_tabs(store):
    closed, remaining = store.create(), store.create()
    ids = [closed.id, remaining.id]
    store.set_session(ids, closed.id)
    state = store.close_tabs([closed.id], ids, closed.id)
    assert state.order == [remaining.id]
    assert state.active == remaining.id
    assert store.get(remaining.id).body == ""
    assert store.closed_history_count() == 0


def _insert_old_close_operation(store, ids, active):
    history = {
        "tabs": [{"id": note_id, "position": index} for index, note_id in enumerate(ids)],
        "active": active,
        "visited": {note_id: 1.0 for note_id in ids},
    }
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "INSERT INTO closed_tab_history(closed_at, state) VALUES (?, ?)",
            ("2026-09-13T00:00:00Z", json.dumps(history)),
        )


def test_discard_removes_old_empty_history_entries_and_prunes_mixed_groups(store):
    blank = store.create()
    retained = store.create("old closed content")
    _insert_old_close_operation(store, [blank.id], blank.id)
    _insert_old_close_operation(store, [blank.id, retained.id], blank.id)
    store.set_session([blank.id], blank.id)
    store.close_tabs([blank.id], [blank.id], blank.id)
    assert store.closed_history_count() == 1
    state = store.reopen_closed()
    assert state.order == [retained.id]
    assert state.active == retained.id
    assert store.closed_history_count() == 0


def test_discard_clears_legacy_active_reference_without_removing_other_tabs(store):
    blank = store.create()
    retained = store.create("old closed content")
    _insert_old_close_operation(store, [retained.id], blank.id)
    store.set_session([blank.id], blank.id)
    store.close_tabs([blank.id], [blank.id], blank.id)
    with sqlite3.connect(store.db_path) as connection:
        history = json.loads(
            connection.execute("SELECT state FROM closed_tab_history").fetchone()[0]
        )
    assert history["active"] is None
    assert store.reopen_closed().order == [retained.id]


def test_discard_is_atomic_with_session_and_previous_close_history(store):
    blank = store.create()
    retained = store.create("existing")
    store.set_view_state(blank.id, {"mode": "preview"})
    ids = [blank.id, retained.id]
    store.set_session(ids, blank.id)
    _insert_old_close_operation(store, [blank.id], blank.id)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_discard BEFORE DELETE ON notes BEGIN SELECT RAISE(ABORT, 'discard failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="discard failed"):
        store.close_tabs(ids, ids, blank.id)
    assert store.get(blank.id).body == ""
    assert store.get_view_state(blank.id)["mode"] == "preview"
    assert store.get_session().order == ids
    assert store.closed_history_count() == 1
    assert store.search("existing")[0].id == retained.id


def test_finish_session_discards_only_untouched_open_notes_without_close_history(store):
    untouched = store.create()
    existing = store.create("existing")
    edited_empty = store.create()
    store.save(edited_empty.id, "", 2)
    closed_untouched = store.create()
    ids = [untouched.id, existing.id, edited_empty.id]
    store.set_session(ids, untouched.id)
    state = store.finish_session(ids, untouched.id, {note_id: 1.0 for note_id in ids})
    assert state.order == [existing.id, edited_empty.id]
    assert state.active == existing.id
    assert untouched.id not in state.visited
    assert store.closed_history_count() == 0
    reopened = NotebookStore(store.root)
    assert reopened.get_session() == state
    assert reopened.get(closed_untouched.id).body == ""
    with pytest.raises(KeyError):
        reopened.get(untouched.id)


def test_finish_session_failure_does_not_remove_untouched_note(store):
    note = store.create()
    store.set_session([note.id], note.id)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_exit BEFORE UPDATE ON app_session BEGIN SELECT RAISE(ABORT, 'exit failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="exit failed"):
        store.finish_session([note.id], note.id)
    assert store.get(note.id).body == ""
    assert store.get_session().order == [note.id]


def test_reopen_mixed_close_preserves_position_relative_to_still_open_tabs(store):
    blank = store.create()
    authored = store.create("authored")
    still_open = store.create("still open")
    ids = [blank.id, authored.id, still_open.id]
    store.set_session(ids, authored.id)
    state = store.close_tabs([blank.id, authored.id], ids, authored.id)
    assert state.order == [still_open.id]
    restored = store.reopen_closed()
    assert restored.order == [authored.id, still_open.id]
    assert restored.active == authored.id


def test_legacy_history_pruning_compacts_discarded_positions_before_restoring(store):
    first_blank = store.create()
    first_authored = store.create("first")
    second_blank = store.create()
    second_authored = store.create("second")
    still_open = store.create("still open")
    _insert_old_close_operation(
        store,
        [first_blank.id, first_authored.id, second_blank.id, second_authored.id],
        second_authored.id,
    )
    current = [first_blank.id, second_blank.id, still_open.id]
    store.set_session(current, still_open.id)
    store.close_tabs([first_blank.id, second_blank.id], current, still_open.id)
    restored = store.reopen_closed()
    assert restored.order == [first_authored.id, second_authored.id, still_open.id]
    assert restored.active == second_authored.id


def test_delete_authored_note_removes_records_search_history_and_only_its_files(store):
    victim = store.create("private marker")
    survivor = store.create("survivor")
    store.set_pinned(victim.id, True)
    store.set_view_state(victim.id, {"mode": "preview"})
    directory = store.assets_dir(victim.id)
    (directory / "file.pdf").write_bytes(b"victim attachment")
    stage = store.note_dir(victim.id) / "staging"
    stage.mkdir()
    (stage / "unfinished").write_bytes(b"unfinished")
    store.register_attachment(victim.id, "assets/file.pdf", "file.pdf", "file", 17)
    other_file = store.assets_dir(survivor.id) / "keep.pdf"
    other_file.write_bytes(b"keep")
    _insert_old_close_operation(store, [victim.id, survivor.id], victim.id)
    store.set_session([victim.id, survivor.id], victim.id)
    result = store.delete_notes([victim.id])
    assert result.deleted_ids == (victim.id,)
    assert result.session.order == [survivor.id]
    assert result.session.active == survivor.id
    assert result.pending_cleanup == ()
    assert not store.note_dir(victim.id).exists()
    assert other_file.read_bytes() == b"keep"
    assert store.search("private marker") == []
    assert store.list_notes(pinned=True) == []
    assert store.reopen_closed().order == [survivor.id]
    with pytest.raises(KeyError):
        store.get(victim.id)
    with sqlite3.connect(store.db_path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM attachments WHERE note_id=?", (victim.id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM note_view_state WHERE note_id=?", (victim.id,)
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.execute(
                "SELECT count(*) FROM note_fts WHERE note_fts MATCH ?", ('"private marker"',)
            ).fetchone()[0]
            == 0
        )


def test_delete_closed_note_keeps_current_tab_and_existing_relative_order(store):
    first, victim, last = [store.create(text) for text in ("first", "victim", "last")]
    store.set_session([first.id, last.id], first.id)
    _insert_old_close_operation(store, [victim.id], victim.id)
    result = store.delete_notes([victim.id])
    assert result.session.order == [first.id, last.id]
    assert result.session.active == first.id
    assert store.closed_history_count() == 0


def test_delete_is_atomic_before_any_file_cleanup(store):
    note = store.create("retained search")
    asset = store.assets_dir(note.id) / "file.txt"
    asset.write_text("retained", encoding="utf-8")
    store.set_session([note.id], note.id)
    _insert_old_close_operation(store, [note.id], note.id)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "CREATE TRIGGER deny_delete BEFORE DELETE ON notes BEGIN SELECT RAISE(ABORT, 'blocked deletion'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="blocked deletion"):
        store.delete_notes([note.id])
    assert store.get(note.id).body == "retained search"
    assert store.search("retained")[0].id == note.id
    assert store.get_session().order == [note.id]
    assert store.closed_history_count() == 1
    assert asset.read_text(encoding="utf-8") == "retained"
    assert store.pending_cleanup() == ()


def test_file_cleanup_failure_commits_delete_and_retries_after_restart(store, monkeypatch):
    note = store.create("delete with retry")
    asset = store.assets_dir(note.id) / "locked.txt"
    asset.write_text("locked", encoding="utf-8")
    store.set_session([note.id], note.id)

    def locked(note_id):
        raise PermissionError("attachment is in use")

    monkeypatch.setattr(store, "_remove_note_directory", locked)
    result = store.delete_notes([note.id])
    assert result.deleted_ids == (note.id,)
    assert result.session.order == []
    assert result.pending_cleanup[0].note_id == note.id
    assert "in use" in result.pending_cleanup[0].error
    assert asset.exists()
    with pytest.raises(KeyError):
        store.get(note.id)
    reopened = NotebookStore(store.root)
    assert asset.exists()  # Startup does not run potentially slow filesystem work.
    assert reopened.pending_cleanup()[0].note_id == note.id
    assert reopened.retry_cleanup() == ()
    assert not asset.exists()
    assert reopened.pending_cleanup() == ()


def test_successful_second_delete_does_not_hide_earlier_cleanup_failure(store, monkeypatch):
    first = store.create("first")
    second = store.create("second")
    (store.assets_dir(first.id) / "file").write_bytes(b"keep until retry")
    original = store._remove_note_directory

    def fail_first(note_id):
        if note_id == first.id:
            raise PermissionError("first attachment locked")
        original(note_id)

    monkeypatch.setattr(store, "_remove_note_directory", fail_first)
    store.delete_notes([first.id])
    result = store.delete_notes([second.id])
    assert result.deleted_ids == (second.id,)
    assert [issue.note_id for issue in result.pending_cleanup] == [first.id]
    assert "locked" in result.pending_cleanup[0].error


def test_postcommit_cleanup_query_failure_keeps_all_known_pending_warnings(store, monkeypatch):
    first, second = store.create("first"), store.create("second")
    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "_remove_note_directory",
            lambda note_id: (_ for _ in ()).throw(PermissionError("locked")),
        )
        store.delete_notes([first.id])
    monkeypatch.setattr(
        store,
        "pending_cleanup",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database unavailable")),
    )
    result = store.delete_notes([second.id])
    assert {issue.note_id for issue in result.pending_cleanup} == {first.id, second.id}
    assert all(issue.error for issue in result.pending_cleanup)
    with pytest.raises(KeyError):
        store.get(second.id)


def test_interruption_after_database_commit_leaves_durable_cleanup_work(store, monkeypatch):
    note = store.create("interrupt")
    asset = store.assets_dir(note.id) / "file"
    asset.write_bytes(b"pending")

    def interrupt(note_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(store, "_remove_note_directory", interrupt)
    with pytest.raises(KeyboardInterrupt):
        store.delete_notes([note.id])
    reopened = NotebookStore(store.root)
    with pytest.raises(KeyError):
        reopened.get(note.id)
    assert reopened.pending_cleanup()[0].note_id == note.id
    assert reopened.retry_cleanup() == ()
    assert not asset.exists()


def test_cleanup_bookkeeping_failure_is_a_warning_and_can_be_retried(store):
    note = store.create("remove")
    asset = store.assets_dir(note.id) / "file"
    asset.write_bytes(b"remove")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "CREATE TRIGGER deny_queue_removal BEFORE DELETE ON note_file_cleanup BEGIN SELECT RAISE(ABORT, 'queue update failed'); END"
        )
    result = store.delete_notes([note.id])
    assert result.deleted_ids == (note.id,)
    assert "queue update failed" in result.pending_cleanup[0].error
    assert not asset.exists()
    assert store.pending_cleanup()[0].note_id == note.id
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("DROP TRIGGER deny_queue_removal")
    assert store.retry_cleanup() == ()


def test_backup_excludes_pending_deleted_note_files_and_restore_cleans_empty_queue(
    store, tmp_path, monkeypatch
):
    deleted, retained = store.create("deleted"), store.create("retained")
    victim_file = store.assets_dir(deleted.id) / "secret.pdf"
    victim_file.write_bytes(b"must not enter a new backup")
    keep_file = store.assets_dir(retained.id) / "keep.pdf"
    keep_file.write_bytes(b"keep")
    monkeypatch.setattr(
        store,
        "_remove_note_directory",
        lambda note_id: (_ for _ in ()).throw(PermissionError("locked")),
    )
    store.delete_notes([deleted.id])
    backup = store.backup(tmp_path / "snapshot-after-delete")
    assert victim_file.exists()
    assert not (backup / "notes" / deleted.id).exists()
    assert (backup / "notes" / retained.id / "assets" / "keep.pdf").read_bytes() == b"keep"
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert all(deleted.id not in name for name in manifest["files"])
    destination = store.restore_backup(backup, tmp_path / "restored-after-delete")
    restored = NotebookStore(destination)
    assert [note.id for note in restored.list_notes()] == [retained.id]
    assert restored.pending_cleanup()[0].note_id == deleted.id
    assert restored.retry_cleanup() == ()


def _directory_link(link, target):
    import os

    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("at_note_root", [False, True])
def test_delete_unlinks_directory_links_without_touching_external_targets(
    store, tmp_path, at_note_root
):
    note = store.create("linked")
    external = tmp_path / "outside-library"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_bytes(b"external original")
    if at_note_root:
        store.note_dir(note.id).parent.mkdir(parents=True)
        link = store.note_dir(note.id)
    else:
        link = store.assets_dir(note.id) / "external-junction"
    _directory_link(link, external)
    result = store.delete_notes([note.id])
    assert result.pending_cleanup == ()
    assert not store.note_dir(note.id).exists()
    assert marker.read_bytes() == b"external original"


def test_substituted_notes_parent_is_never_traversed_by_cleanup(store, tmp_path):
    note = store.create("linked parent")
    external = tmp_path / "outside-library"
    victim = external / note.id
    victim.mkdir(parents=True)
    marker = victim / "keep.txt"
    marker.write_bytes(b"external original")
    _directory_link(store.root / "notes", external)
    result = store.delete_notes([note.id])
    assert result.pending_cleanup[0].note_id == note.id
    assert marker.read_bytes() == b"external original"


def test_unknown_and_invalid_note_ids_cannot_delete_arbitrary_directories(store):
    from uuid import uuid4

    orphan_id = str(uuid4())
    orphan = store.note_dir(orphan_id)
    orphan.mkdir(parents=True)
    marker = orphan / "keep.txt"
    marker.write_bytes(b"not authorized by a note record")
    result = store.delete_notes([orphan_id])
    assert result.deleted_ids == ()
    assert result.pending_cleanup == ()
    assert marker.exists()
    with pytest.raises(ValueError):
        store.delete_notes(["../outside"])
    assert marker.exists()


def test_invalid_or_live_note_cleanup_queue_does_not_delete_files(store):
    note = store.create("must survive")
    marker = store.assets_dir(note.id) / "keep.txt"
    marker.write_bytes(b"keep")
    with sqlite3.connect(store.db_path) as connection:
        connection.executemany(
            "INSERT INTO note_file_cleanup(note_id, requested_at) VALUES (?, ?)",
            [(note.id, "now"), ("../outside", "now")],
        )
    issues = store.retry_cleanup()
    assert {issue.note_id for issue in issues} == {note.id, "../outside"}
    assert marker.exists()
    assert store.get(note.id).body == "must survive"


def _downgrade_schema_to_v1(path):
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE note_file_cleanup")
        connection.execute("UPDATE schema_version SET version=1")
        connection.execute("PRAGMA user_version=1")


def test_additive_v1_migration_preserves_existing_notes_indexes_and_views(store):
    note = store.create("migration marker")
    store.set_pinned(note.id, True)
    store.set_view_state(note.id, {"mode": "preview"})
    store.set_session([note.id], note.id)
    _downgrade_schema_to_v1(store.db_path)
    reopened = NotebookStore(store.root)
    assert reopened.get(note.id).body == note.body
    assert reopened.get(note.id).pinned
    assert reopened.search("migration")[0].id == note.id
    assert reopened.get_view_state(note.id)["mode"] == "preview"
    assert reopened.get_session().order == [note.id]
    assert reopened.pending_cleanup() == ()
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 2


def test_v1_migration_failure_rolls_back_new_table_and_version(store):
    note = store.create("safe during migration")
    _downgrade_schema_to_v1(store.db_path)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_schema_version BEFORE UPDATE ON schema_version BEGIN SELECT RAISE(ABORT, 'migration failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="migration failed"):
        NotebookStore(store.root)
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='note_file_cleanup'"
        ).fetchone()
        assert (
            connection.execute("SELECT body FROM notes WHERE id=?", (note.id,)).fetchone()[0]
            == note.body
        )
        connection.execute("DROP TRIGGER reject_schema_version")
    assert NotebookStore(store.root).get(note.id).body == note.body


def test_restore_of_v1_backup_migrates_when_library_is_opened(store, tmp_path):
    import hashlib

    note = store.create("v1 backup marker")
    backup = store.backup(tmp_path / "old-backup")
    database = backup / "library.sqlite3"
    _downgrade_schema_to_v1(database)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["library.sqlite3"] = {
        "size": database.stat().st_size,
        "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    destination = store.restore_backup(backup, tmp_path / "v1-restored")
    restored = NotebookStore(destination)
    assert restored.get(note.id).body == note.body
    assert restored.search("backup")[0].id == note.id
    assert restored.pending_cleanup() == ()
