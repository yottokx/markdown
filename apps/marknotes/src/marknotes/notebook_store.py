"""Durable, Qt-independent storage for the managed MarkNotes notebook library."""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

SCHEMA_VERSION = 2
MAX_CLOSED_OPERATIONS = 100


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def normalize_text(text: str) -> str:
    """The exact same literal normalization is used by indexing and highlighting."""
    return unicodedata.normalize("NFKC", text).casefold()


def _normalized_spans(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Map normalized code points to original grapheme-sized source ranges.

    Combining sequences and half-width kana voice marks must normalize together.
    Hangul jamo composition also crosses starter boundaries. Mapping the whole
    composing cluster avoids selecting half a visible character in the editor.
    """
    chunks: list[str] = []
    spans: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(text) + 1):
        boundary = index == len(text)
        if not boundary:
            decomposition = unicodedata.normalize("NFKD", text[index])
            mark = bool(decomposition) and unicodedata.category(decomposition[0]).startswith("M")
            previous = unicodedata.normalize("NFKC", text[index - 1])
            current = unicodedata.normalize("NFKC", text[index])
            hangul = bool(previous and current) and (
                (0x1100 <= ord(previous[-1]) <= 0x1112 and 0x1161 <= ord(current[0]) <= 0x1175)
                or (0x1161 <= ord(previous[-1]) <= 0x1175 and 0x11A8 <= ord(current[0]) <= 0x11C2)
                or (
                    0xAC00 <= ord(previous[-1]) <= 0xD7A3
                    and (ord(previous[-1]) - 0xAC00) % 28 == 0
                    and 0x11A8 <= ord(current[0]) <= 0x11C2
                )
            )
            boundary = not mark and not hangul
        if boundary:
            normalized = normalize_text(text[start:index])
            chunks.append(normalized)
            spans.extend([(start, index)] * len(normalized))
            start = index
    return "".join(chunks), spans


def find_match_ranges(text: str, query: str) -> tuple[tuple[int, int], ...]:
    """Return non-overlapping, original Python-string ranges for literal hits."""
    needle = normalize_text(query)
    if not needle:
        return ()
    haystack, spans = _normalized_spans(text)
    found: list[tuple[int, int]] = []
    offset = 0
    while (offset := haystack.find(needle, offset)) >= 0:
        match = (spans[offset][0], spans[offset + len(needle) - 1][1])
        if found and match[0] < found[-1][1]:
            found[-1] = (found[-1][0], max(found[-1][1], match[1]))
        else:
            found.append(match)
        offset += len(needle)
    return tuple(found)


def note_title(body: str) -> str:
    """Derive a compact label; titles never act as identities or file names."""
    line = next((line.strip() for line in body.splitlines() if line.strip()), "")
    if not line:
        return "新しいノート"
    image_only = bool(re.fullmatch(r"!\[[^]]*\]\([^)]*\)", line))
    line = re.sub(r"^\s{0,3}(?:#{1,6}\s*|>\s*|[-+*]\s+|\d+[.)]\s+)", "", line)
    line = re.sub(r"!?\[([^]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"<[^>]*>", "", line)
    line = re.sub(r"[*_`~]", "", line)
    line = html.unescape(line).strip()
    return line[:240] or ("画像のノート" if image_only else "新しいノート")


@dataclass(frozen=True)
class NoteSummary:
    id: str
    title: str
    excerpt: str
    created_at: str
    updated_at: str
    revision: int
    pinned: bool
    pinned_at: str | None = None

    @property
    def content_updated_at(self) -> str:
        return self.updated_at


@dataclass(frozen=True)
class Note(NoteSummary):
    body: str = ""


@dataclass(frozen=True)
class SessionState:
    order: list[str] = field(default_factory=list)
    active: str | None = None
    visited: dict[str, float] = field(default_factory=dict)

    @property
    def ids(self) -> list[str]:
        return self.order


@dataclass(frozen=True)
class CleanupIssue:
    note_id: str
    error: str


@dataclass(frozen=True)
class DeleteResult:
    session: SessionState
    deleted_ids: tuple[str, ...]
    pending_cleanup: tuple[CleanupIssue, ...] = ()


@dataclass(frozen=True)
class SearchSnippet:
    text: str
    start: int
    ranges: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class SearchResult:
    note: NoteSummary
    matches: tuple[tuple[int, int], ...]
    snippets: tuple[SearchSnippet, ...]

    @property
    def match_count(self) -> int:
        return len(self.matches)

    @property
    def id(self) -> str:
        return self.note.id

    @property
    def title(self) -> str:
        return self.note.title


def make_snippets(
    body: str, matches: tuple[tuple[int, int], ...], context: int = 45, limit: int = 3
) -> tuple[SearchSnippet, ...]:
    groups: list[list[int]] = []
    for start, end in matches:
        left, right = max(0, start - context), min(len(body), end + context)
        if (
            groups
            and left <= groups[-1][1]
            and right - groups[-1][0] <= max(240, end - start + context * 2)
        ):
            groups[-1][1] = max(right, groups[-1][1])
        elif not groups or end > groups[-1][1]:
            groups.append([left, right])
    return tuple(
        SearchSnippet(
            body[start:end],
            start,
            tuple((a - start, b - start) for a, b in matches if a >= start and b <= end),
        )
        for start, end in groups[:limit]
    )


_SUMMARY_COLUMNS = "id, title, excerpt, created_at, updated_at, revision, pinned_at"


class NotebookStore:
    """Short-lived connections permit UI reads and background saves/searches.

    ``mutation_lock`` also coordinates attachment publication with backups. The
    application owns one store per library and prevents a second editing process.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "library.sqlite3"
        self.mutation_lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        # The lock is re-entrant because attachment transactions may call save.
        lock = self.mutation_lock if write else None
        if lock:
            lock.acquire()
        connection = None
        try:
            connection = sqlite3.connect(self.db_path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()
            if lock:
                lock.release()

    def _initialize(self) -> None:
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("このライブラリは新しいMarkNotesで作成されています。")
            if version == 1:
                # This additive migration does not rewrite existing note data.
                # A single transaction either adds the queue completely or leaves
                # the original schema usable, without making extra body copies.
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE note_file_cleanup (
                        note_id TEXT PRIMARY KEY,
                        requested_at TEXT NOT NULL,
                        last_error TEXT NOT NULL DEFAULT ''
                    );
                    UPDATE schema_version SET version=2;
                    PRAGMA user_version=2;
                    COMMIT;
                    """
                )
                version = SCHEMA_VERSION
            if version == SCHEMA_VERSION:
                self.fts_available = bool(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='note_fts'"
                    ).fetchone()
                )
                return
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notes'"
            ).fetchone():
                raise RuntimeError("不明なライブラリ形式です。バックアップから復旧してください。")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE notes (
                    rowid INTEGER PRIMARY KEY,
                    id TEXT NOT NULL UNIQUE,
                    body TEXT NOT NULL,
                    title TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 0),
                    pinned_at TEXT
                );
                CREATE INDEX notes_updated ON notes(updated_at DESC, id);
                CREATE INDEX notes_created ON notes(created_at DESC, id);
                CREATE INDEX notes_pinned ON notes(pinned_at DESC, id) WHERE pinned_at IS NOT NULL;
                CREATE TABLE note_view_state (
                    note_id TEXT PRIMARY KEY REFERENCES notes(id),
                    state TEXT NOT NULL
                );
                CREATE TABLE app_session (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    state TEXT NOT NULL
                );
                INSERT INTO app_session VALUES (1, '{"order":[],"active":null,"visited":{}}');
                CREATE TABLE closed_tab_history (
                    operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    closed_at TEXT NOT NULL,
                    state TEXT NOT NULL
                );
                CREATE TABLE attachments (
                    asset_id TEXT PRIMARY KEY,
                    note_id TEXT NOT NULL REFERENCES notes(id),
                    relative_path TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'ready',
                    UNIQUE(note_id, relative_path)
                );
                CREATE TABLE note_search_content (
                    rowid INTEGER PRIMARY KEY REFERENCES notes(rowid),
                    search_text TEXT NOT NULL
                );
                CREATE TABLE note_file_cleanup (
                    note_id TEXT PRIMARY KEY,
                    requested_at TEXT NOT NULL,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE schema_version (version INTEGER NOT NULL);
                INSERT INTO schema_version VALUES (2);
                PRAGMA user_version=2;
                COMMIT;
                """
            )
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE temp.fts_probe USING fts5(text, tokenize='trigram')"
                )
            except sqlite3.OperationalError:
                self.fts_available = False
            else:
                self.fts_available = True
                connection.execute("DROP TABLE temp.fts_probe")
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE VIRTUAL TABLE note_fts USING fts5(
                        search_text, content='note_search_content', content_rowid='rowid',
                        tokenize='trigram case_sensitive 1'
                    );
                    CREATE TRIGGER search_insert AFTER INSERT ON note_search_content BEGIN
                        INSERT INTO note_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
                    END;
                    CREATE TRIGGER search_delete AFTER DELETE ON note_search_content BEGIN
                        INSERT INTO note_fts(note_fts, rowid, search_text)
                            VALUES ('delete', old.rowid, old.search_text);
                    END;
                    CREATE TRIGGER search_update AFTER UPDATE ON note_search_content BEGIN
                        INSERT INTO note_fts(note_fts, rowid, search_text)
                            VALUES ('delete', old.rowid, old.search_text);
                        INSERT INTO note_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
                    END;
                    COMMIT;
                    """
                )

    @staticmethod
    def _summary(row: sqlite3.Row) -> NoteSummary:
        return NoteSummary(
            id=row["id"],
            title=row["title"],
            excerpt=row["excerpt"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            revision=row["revision"],
            pinned=row["pinned_at"] is not None,
            pinned_at=row["pinned_at"],
        )

    @classmethod
    def _note(cls, row: sqlite3.Row) -> Note:
        summary = cls._summary(row)
        return Note(**vars(summary), body=row["body"])

    @staticmethod
    def _require(connection: sqlite3.Connection, note_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            raise KeyError(note_id)
        return row

    def create(self, body: str = "") -> Note:
        note_id, timestamp = str(uuid4()), _now()
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                "INSERT INTO notes(id, body, title, excerpt, created_at, updated_at, revision) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (note_id, body, note_title(body), body[:240], timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO note_search_content(rowid, search_text) VALUES (?, ?)",
                (cursor.lastrowid, normalize_text(body)),
            )
            return self._note(self._require(connection, note_id))

    def get_summary(self, note_id: str) -> NoteSummary:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM notes WHERE id=?", (note_id,)
            ).fetchone()
            if row is None:
                raise KeyError(note_id)
            return self._summary(row)

    def get(self, note_id: str) -> Note:
        with self._connection() as connection:
            return self._note(self._require(connection, note_id))

    def save(self, note_id: str, body: str, revision: int) -> bool:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("revision must be a non-negative integer")
        with self._connection(write=True) as connection:
            previous = self._require(connection, note_id)
            if revision <= previous["revision"]:
                return False
            if body == previous["body"]:
                connection.execute("UPDATE notes SET revision=? WHERE id=?", (revision, note_id))
            else:
                connection.execute(
                    "UPDATE notes SET body=?, title=?, excerpt=?, updated_at=?, revision=? WHERE id=?",
                    (body, note_title(body), body[:240], _now(), revision, note_id),
                )
                connection.execute(
                    "UPDATE note_search_content SET search_text=? WHERE rowid=?",
                    (normalize_text(body), previous["rowid"]),
                )
            return True

    def touch(self, note_id: str) -> None:
        """An external attachment edit changes the content date, not body revision."""
        with self._connection(write=True) as connection:
            self._require(connection, note_id)
            connection.execute("UPDATE notes SET updated_at=? WHERE id=?", (_now(), note_id))

    def list_notes(
        self, order: str = "updated", pinned: bool = False, limit: int = 50, offset: int = 0
    ) -> list[NoteSummary]:
        columns = {"updated": "updated_at", "created": "created_at"}
        if order not in columns:
            raise ValueError("order must be 'updated' or 'created'")
        limit, offset = self._page(limit, offset)
        sorting = "pinned_at" if pinned else columns[order]
        predicate = "WHERE pinned_at IS NOT NULL" if pinned else ""
        with self._connection() as connection:
            return [
                self._summary(row)
                for row in connection.execute(
                    f"SELECT {_SUMMARY_COLUMNS} FROM notes {predicate} "
                    f"ORDER BY {sorting} DESC, id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    @staticmethod
    def _page(limit: int, offset: int) -> tuple[int, int]:
        if limit < 1 or limit > 1000 or offset < 0:
            raise ValueError("limit must be 1..1000 and offset must be non-negative")
        return limit, offset

    def set_pinned(self, note_id: str, pinned: bool) -> None:
        with self._connection(write=True) as connection:
            row = self._require(connection, note_id)
            value = (row["pinned_at"] or _now()) if pinned else None
            connection.execute("UPDATE notes SET pinned_at=? WHERE id=?", (value, note_id))

    def get_view_state(self, note_id: str) -> dict:
        with self._connection() as connection:
            self._require(connection, note_id)
            row = connection.execute(
                "SELECT state FROM note_view_state WHERE note_id=?", (note_id,)
            ).fetchone()
            state = json.loads(row[0]) if row else {"mode": "split"}
            self._validate_view_state(state)
            return state

    @staticmethod
    def _validate_view_state(state: dict) -> None:
        if not isinstance(state, dict):
            raise TypeError("invalid note view state")
        if "mode" in state and state["mode"] not in ("source", "preview", "split"):
            raise ValueError("invalid note display mode")
        for key in ("cursor", "anchor"):
            if key in state and (type(state[key]) is not int or state[key] < 0):
                raise ValueError(f"invalid note {key} position")
        for key in ("source", "preview"):
            if key in state and (
                not isinstance(state[key], (float, int))
                or not math.isfinite(state[key])
                or state[key] < 0
            ):
                raise ValueError(f"invalid note {key} position")
        if "split" in state and (
            not isinstance(state["split"], list)
            or len(state["split"]) != 2
            or any(type(size) is not int or size < 0 for size in state["split"])
        ):
            raise ValueError("invalid note splitter sizes")

    def set_view_state(self, note_id: str, state: dict) -> None:
        self._validate_view_state(state)
        serialized = json.dumps(state, ensure_ascii=False, allow_nan=False)
        with self._connection(write=True) as connection:
            self._require(connection, note_id)
            connection.execute(
                "INSERT INTO note_view_state VALUES (?, ?) ON CONFLICT(note_id) "
                "DO UPDATE SET state=excluded.state",
                (note_id, serialized),
            )

    @staticmethod
    def _read_session(connection: sqlite3.Connection) -> SessionState:
        data = json.loads(
            connection.execute("SELECT state FROM app_session WHERE singleton=1").fetchone()[0]
        )
        return SessionState(data["order"], data["active"], data.get("visited", {}))

    @staticmethod
    def _write_session(connection: sqlite3.Connection, state: SessionState) -> None:
        connection.execute(
            "UPDATE app_session SET state=? WHERE singleton=1",
            (json.dumps(vars(state), ensure_ascii=False, allow_nan=False),),
        )

    @classmethod
    def _session(
        cls,
        connection: sqlite3.Connection,
        order: list[str],
        active: str | None,
        visited: dict[str, float] | None = None,
    ) -> SessionState:
        unique = list(dict.fromkeys(order))
        for note_id in unique:
            # Only identity is needed; avoid loading every open note's Markdown.
            if connection.execute("SELECT 1 FROM notes WHERE id=?", (note_id,)).fetchone() is None:
                raise KeyError(note_id)
        active = active if active in unique else (unique[-1] if unique else None)
        return SessionState(
            unique, active, {key: value for key, value in (visited or {}).items() if key in unique}
        )

    def get_session(self) -> SessionState:
        with self._connection() as connection:
            return self._read_session(connection)

    def set_session(
        self, order: list[str], active: str | None, visited: dict[str, float] | None = None
    ) -> SessionState:
        with self._connection(write=True) as connection:
            if visited is None:
                visited = self._read_session(connection).visited
            state = self._session(connection, order, active, visited)
            self._write_session(connection, state)
            return state

    def _is_untouched_draft(self, connection: sqlite3.Connection, note_id: str) -> bool:
        note = self._require(connection, note_id)
        if (
            note["body"] != ""
            or note["revision"] != 0
            or note["pinned_at"] is not None
            or note["created_at"] != note["updated_at"]
            or connection.execute(
                "SELECT 1 FROM attachments WHERE note_id=? LIMIT 1", (note_id,)
            ).fetchone()
        ):
            return False
        directory = self.note_dir(note_id)
        try:
            if directory.is_symlink() or directory.resolve() != directory:
                return False
            if not directory.exists():
                return True
            if not directory.is_dir():
                return False

            def unreadable(error):
                raise error

            # Keep incomplete copies, unregistered files and link targets. Empty
            # directories left by a cancelled attachment do not represent content.
            for parent, children, files in os.walk(directory, onerror=unreadable):
                if files:
                    return False
                for child in children:
                    child_path = Path(parent) / child
                    if child_path.is_symlink() or child_path.resolve() != child_path:
                        return False
        except OSError:
            return False  # An unreadable directory is not evidence of emptiness.
        return True

    def _discard_untouched_drafts(self, connection: sqlite3.Connection, ids: set[str]) -> set[str]:
        discarded = {note_id for note_id in ids if self._is_untouched_draft(connection, note_id)}
        self._delete_note_records(connection, discarded)
        return discarded

    @staticmethod
    def _delete_note_records(connection: sqlite3.Connection, discarded: set[str]) -> None:
        if not discarded:
            return
        for row in connection.execute(
            "SELECT operation_id, state FROM closed_tab_history"
        ).fetchall():
            history = json.loads(row["state"])
            retained = [tab for tab in history["tabs"] if tab["id"] not in discarded]
            if (
                len(retained) == len(history["tabs"])
                and history["active"] not in discarded
                and not discarded.intersection(history.get("visited", {}))
            ):
                continue
            if not retained:
                connection.execute(
                    "DELETE FROM closed_tab_history WHERE operation_id=?", (row["operation_id"],)
                )
            else:
                removed_positions = [
                    tab["position"] for tab in history["tabs"] if tab["id"] in discarded
                ]
                history["tabs"] = [
                    {
                        **tab,
                        "position": tab["position"]
                        - sum(position < tab["position"] for position in removed_positions),
                    }
                    for tab in retained
                ]
                if history["active"] in discarded:
                    history["active"] = None
                history["visited"] = {
                    key: value
                    for key, value in history.get("visited", {}).items()
                    if key not in discarded
                }
                connection.execute(
                    "UPDATE closed_tab_history SET state=? WHERE operation_id=?",
                    (json.dumps(history, ensure_ascii=False, allow_nan=False), row["operation_id"]),
                )
        for note_id in discarded:
            # Foreign keys and FTS triggers participate in this same transaction.
            # File cleanup starts only after these changes and its queue commit.
            connection.execute("DELETE FROM attachments WHERE note_id=?", (note_id,))
            connection.execute("DELETE FROM note_view_state WHERE note_id=?", (note_id,))
            connection.execute(
                "DELETE FROM note_search_content WHERE rowid=(SELECT rowid FROM notes WHERE id=?)",
                (note_id,),
            )
            connection.execute("DELETE FROM notes WHERE id=?", (note_id,))

    def delete_notes(
        self,
        ids: list[str],
        existing_order: list[str] | None = None,
        active: str | None = None,
        visited: dict[str, float] | None = None,
    ) -> DeleteResult:
        """Delete authoritative records, then retryably remove each owned directory.

        The caller must finish pending edits and attachment publications first.
        A failed DB transaction never removes files. After commit, a cleanup
        failure is a warning, not a failed note deletion; the persisted queue is
        available to ``retry_cleanup`` after restart. Unknown IDs never authorize
        deleting arbitrary orphan directories.
        """
        targets = list(dict.fromkeys(ids))
        for note_id in targets:
            self.note_dir(note_id)  # Validate the canonical UUID before any write.
        with self.mutation_lock:
            with self._connection(write=True) as connection:
                previous = self._read_session(connection)
                current = SessionState(
                    list(
                        dict.fromkeys(previous.order if existing_order is None else existing_order)
                    ),
                    previous.active if existing_order is None else active,
                    previous.visited if visited is None else dict(visited),
                )
                deleted = tuple(
                    note_id
                    for note_id in targets
                    if connection.execute("SELECT 1 FROM notes WHERE id=?", (note_id,)).fetchone()
                )
                for note_id in deleted:
                    connection.execute(
                        "INSERT INTO note_file_cleanup(note_id, requested_at) VALUES (?, ?) "
                        "ON CONFLICT(note_id) DO NOTHING",
                        (note_id, _now()),
                    )
                self._delete_note_records(connection, set(targets))
                state = self._without_tabs(connection, current, set(targets))
                self._write_session(connection, state)
                queued_before_cleanup = tuple(
                    CleanupIssue(row["note_id"], row["last_error"])
                    for row in connection.execute(
                        "SELECT note_id, last_error FROM note_file_cleanup ORDER BY requested_at, note_id"
                    )
                )
            try:
                issues = self._cleanup_queued_files(set(targets))
            except sqlite3.Error as error:
                # The deletion has committed; never leave the UI believing the
                # old records survived merely because queue bookkeeping failed.
                issues = tuple(
                    CleanupIssue(item.note_id, item.error or str(error))
                    for item in queued_before_cleanup
                )
            return DeleteResult(state, deleted, issues)

    def pending_cleanup(self) -> tuple[CleanupIssue, ...]:
        with self._connection() as connection:
            return tuple(
                CleanupIssue(row["note_id"], row["last_error"])
                for row in connection.execute(
                    "SELECT note_id, last_error FROM note_file_cleanup ORDER BY requested_at, note_id"
                )
            )

    def retry_cleanup(self) -> tuple[CleanupIssue, ...]:
        """Run on the application's worker; return only work still pending."""
        return self._cleanup_queued_files()

    def _cleanup_queued_files(self, ids: set[str] | None = None) -> tuple[CleanupIssue, ...]:
        issues = []
        with self.mutation_lock:
            queued = self.pending_cleanup()
            for pending in queued:
                note_id = pending.note_id
                if ids is not None and note_id not in ids:
                    issues.append(pending)
                    continue
                error = None
                try:
                    with self._connection() as connection:
                        if connection.execute(
                            "SELECT 1 FROM notes WHERE id=?", (note_id,)
                        ).fetchone():
                            raise ValueError(
                                "An existing note cannot have its attachments cleaned up"
                            )
                    self._remove_note_directory(note_id)
                except (OSError, ValueError, sqlite3.Error) as caught:
                    error = str(caught)
                try:
                    with self._connection(write=True) as connection:
                        if error is None:
                            connection.execute(
                                "DELETE FROM note_file_cleanup WHERE note_id=?", (note_id,)
                            )
                        else:
                            connection.execute(
                                "UPDATE note_file_cleanup SET last_error=? WHERE note_id=?",
                                (error, note_id),
                            )
                except sqlite3.Error as caught:
                    error = error or str(caught)
                if error is not None:
                    issues.append(CleanupIssue(note_id, error))
        return tuple(issues)

    def _remove_note_directory(self, note_id: str) -> None:
        target = self.note_dir(note_id)
        notes = self.root / "notes"
        # Never interpret the queue as a filesystem path, and never follow a
        # substituted library/notes root. A link at the UUID itself is removed
        # as a link; its external target remains untouched.
        if self.root.resolve() != self.root or notes.resolve() != notes:
            raise ValueError("The notes directory points outside its managed location")
        if notes.is_symlink() or notes.is_junction():
            raise ValueError("The notes directory must not be a link")
        if target.parent != notes:
            raise ValueError("The cleanup target is not an owned note directory")
        if target.is_symlink():
            target.unlink()
        elif target.is_junction():
            target.rmdir()
        elif target.exists():
            if target.resolve() != target:
                raise ValueError("The cleanup target points outside its managed location")
            if target.is_dir():
                # Python 3.13 rmtree treats nested symlinks and Windows junctions
                # as files, removing the links without traversing their targets.
                shutil.rmtree(target)
            else:
                target.unlink()

    @classmethod
    def _without_tabs(
        cls, connection: sqlite3.Connection, current: SessionState, removed: set[str]
    ) -> SessionState:
        remaining = [note_id for note_id in current.order if note_id not in removed]
        if current.active in removed:
            old_index = current.order.index(current.active)
            active = remaining[min(old_index, len(remaining) - 1)] if remaining else None
        else:
            active = current.active
        return cls._session(connection, remaining, active, current.visited)

    def finish_session(
        self, order: list[str], active: str | None, visited: dict[str, float] | None = None
    ) -> SessionState:
        """Save exit state after flushing edits, discarding only untouched drafts.

        Notes with any edited revision, pin or attachment survive even when their
        body is empty. Exiting never creates a close operation for retained notes.
        """
        with self._connection(write=True) as connection:
            if visited is None:
                visited = self._read_session(connection).visited
            current = self._session(connection, order, active, visited)
            discarded = self._discard_untouched_drafts(connection, set(current.order))
            state = self._without_tabs(connection, current, discarded)
            self._write_session(connection, state)
            return state

    def close_tabs(
        self, ids: list[str], existing_order: list[str], active: str | None
    ) -> SessionState:
        """Call only after the UI's current body revisions have been saved."""
        with self._connection(write=True) as connection:
            previous = self._read_session(connection)
            current = self._session(connection, existing_order, active, previous.visited)
            closing = set(ids).intersection(current.order)
            if not closing:
                return current
            discarded = self._discard_untouched_drafts(connection, closing)
            retained = closing - discarded
            history = {
                "tabs": [
                    {"id": note_id, "position": index}
                    for index, note_id in enumerate(
                        note_id for note_id in current.order if note_id not in discarded
                    )
                    if note_id in retained
                ],
                "active": current.active if current.active not in discarded else None,
                "visited": {
                    key: value for key, value in current.visited.items() if key in retained
                },
            }
            state = self._without_tabs(connection, current, closing)
            self._write_session(connection, state)
            if retained:
                connection.execute(
                    "INSERT INTO closed_tab_history(closed_at, state) VALUES (?, ?)",
                    (_now(), json.dumps(history, ensure_ascii=False, allow_nan=False)),
                )
                connection.execute(
                    "DELETE FROM closed_tab_history WHERE operation_id NOT IN "
                    "(SELECT operation_id FROM closed_tab_history ORDER BY operation_id DESC LIMIT ?)",
                    (MAX_CLOSED_OPERATIONS,),
                )
            return state

    def closed_history_count(self) -> int:
        with self._connection() as connection:
            return connection.execute("SELECT COUNT(*) FROM closed_tab_history").fetchone()[0]

    def reopen_closed(
        self, existing_order: list[str] | None = None, active: str | None = None
    ) -> SessionState | None:
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT operation_id, state FROM closed_tab_history ORDER BY operation_id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            current = self._read_session(connection)
            if existing_order is not None:
                current = self._session(connection, existing_order, active, current.visited)
            history = json.loads(row["state"])
            order = list(current.order)
            targets = [tab["id"] for tab in history["tabs"]]
            for tab in history["tabs"]:
                # Validate all notes before committing or consuming the operation.
                note = self._require(connection, tab["id"])
                if not isinstance(note["body"], str):
                    raise TypeError("invalid note body")
                view = connection.execute(
                    "SELECT state FROM note_view_state WHERE note_id=?", (tab["id"],)
                ).fetchone()
                if view:
                    self._validate_view_state(json.loads(view[0]))
                if tab["id"] not in order:
                    order.insert(min(tab["position"], len(order)), tab["id"])
            selected = history["active"] if history["active"] in targets else targets[0]
            visited = dict(history.get("visited", {})) | current.visited
            state = self._session(connection, order, selected, visited)
            self._write_session(connection, state)
            connection.execute(
                "DELETE FROM closed_tab_history WHERE operation_id=?", (row["operation_id"],)
            )
            return state

    def note_dir(self, note_id: str) -> Path:
        if str(UUID(note_id)) != note_id:
            raise ValueError("invalid canonical note UUID")
        return self.root / "notes" / note_id

    def assets_dir(self, note_id: str) -> Path:
        path = self.note_dir(note_id) / "assets"
        self.get(note_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def search(
        self,
        query: str,
        limit: int = 50,
        offset: int = 0,
        cancel: Callable[[], bool] | None = None,
    ) -> list[SearchResult]:
        limit, offset = self._page(limit, offset)
        needle = normalize_text(query)
        if not needle or (cancel and cancel()):
            return []
        with self._connection() as connection:
            if cancel:
                connection.set_progress_handler(lambda: int(cancel()), 1000)
            parameters: list = [needle]
            if self.fts_available and len(needle) >= 3 and "\x00" not in needle:
                # Quotes make FTS operators and punctuation literal; instr guards
                # against tokenizer differences without scanning unrelated notes.
                phrase = '"' + needle.replace('"', '""') + '"'
                predicate = "n.rowid IN (SELECT rowid FROM note_fts WHERE note_fts MATCH ?) AND instr(s.search_text, ?) > 0"
                parameters.insert(0, phrase)
            else:
                predicate = "instr(s.search_text, ?) > 0"
            try:
                rows = connection.execute(
                    "SELECT n.* FROM notes n JOIN note_search_content s ON s.rowid=n.rowid "
                    f"WHERE {predicate} ORDER BY n.updated_at DESC, n.id LIMIT ? OFFSET ?",
                    (*parameters, limit, offset),
                ).fetchall()
            except sqlite3.OperationalError:
                if cancel and cancel():
                    return []
                raise
            results = []
            for row in rows:
                if cancel and cancel():
                    return []
                matches = find_match_ranges(row["body"], query)
                results.append(
                    SearchResult(self._summary(row), matches, make_snippets(row["body"], matches))
                )
            return results

    def rebuild_index(self) -> None:
        with self._connection(write=True) as connection:
            # Do not use the old index's deletion records while repairing it.
            # Disabling triggers also lets a corrupt derived index be replaced
            # directly from the authoritative body, in one transaction.
            for action in ("insert", "delete", "update"):
                connection.execute(f"DROP TRIGGER IF EXISTS search_{action}")
            connection.execute("DELETE FROM note_search_content")
            for row in connection.execute("SELECT rowid, body FROM notes"):
                connection.execute(
                    "INSERT INTO note_search_content VALUES (?, ?)",
                    (row["rowid"], normalize_text(row["body"])),
                )
            if self.fts_available:
                connection.execute("INSERT INTO note_fts(note_fts) VALUES ('rebuild')")
                connection.execute(
                    "CREATE TRIGGER search_insert AFTER INSERT ON note_search_content BEGIN "
                    "INSERT INTO note_fts(rowid, search_text) VALUES (new.rowid, new.search_text); END"
                )
                connection.execute(
                    "CREATE TRIGGER search_delete AFTER DELETE ON note_search_content BEGIN "
                    "INSERT INTO note_fts(note_fts, rowid, search_text) "
                    "VALUES ('delete', old.rowid, old.search_text); END"
                )
                connection.execute(
                    "CREATE TRIGGER search_update AFTER UPDATE ON note_search_content BEGIN "
                    "INSERT INTO note_fts(note_fts, rowid, search_text) "
                    "VALUES ('delete', old.rowid, old.search_text); "
                    "INSERT INTO note_fts(rowid, search_text) VALUES (new.rowid, new.search_text); END"
                )

    def register_attachment(
        self,
        note_id: str,
        relative_path: str,
        original_name: str,
        kind: str,
        size: int,
        asset_id: str | None = None,
    ) -> str:
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "assets":
            raise ValueError("attachment must be a relative assets path")
        asset_id = asset_id or str(uuid4())
        with self._connection(write=True) as connection:
            self._require(connection, note_id)
            connection.execute(
                "INSERT INTO attachments(asset_id, note_id, relative_path, original_name, kind, size) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(note_id, relative_path) DO UPDATE SET "
                "original_name=excluded.original_name, kind=excluded.kind, size=excluded.size",
                (asset_id, note_id, path.as_posix(), original_name, kind, size),
            )
        return asset_id

    def list_attachments(self, note_id: str) -> list[dict]:
        """Metadata complements the asset browser's actual filesystem listing."""
        with self._connection() as connection:
            self._require(connection, note_id)
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM attachments WHERE note_id=? ORDER BY relative_path", (note_id,)
                )
            ]

    def remove_attachment(self, note_id: str, relative_path: str) -> None:
        """Remove metadata after the caller stages a validated asset for deletion."""
        with self._connection(write=True) as connection:
            self._require(connection, note_id)
            connection.execute(
                "DELETE FROM attachments WHERE note_id=? AND relative_path=?",
                (note_id, relative_path),
            )

    def backup(self, destination: Path | str) -> Path:
        """Publish a complete directory snapshot only after every file is verified."""
        destination = Path(destination).resolve()
        if destination == self.root or destination.is_relative_to(self.root):
            raise ValueError("バックアップ先はライブラリの外を選んでください。")
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name(f".{destination.name}-{uuid4().hex}.tmp")
        staging.mkdir()
        try:
            with self.mutation_lock:
                with (
                    self._connection() as source,
                    closing(sqlite3.connect(staging / "library.sqlite3")) as target,
                ):
                    source.backup(target)
                    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("バックアップDBの整合性検証に失敗しました。")
                    pending_ids = {
                        row[0] for row in target.execute("SELECT note_id FROM note_file_cleanup")
                    }
                notes = self.root / "notes"
                if notes.exists():
                    source_paths = []
                    for note_path in notes.iterdir():
                        if note_path.name in pending_ids:
                            continue  # Deleted attachments must not enter a new backup.
                        if note_path.is_symlink() or note_path.is_junction():
                            raise ValueError("Linked note folders cannot be backed up")
                        source_paths.append(note_path)
                        if note_path.is_dir():
                            source_paths.extend(note_path.rglob("*"))
                    for source_path in source_paths:
                        if source_path.is_symlink() or source_path.is_junction():
                            raise ValueError(
                                "ライブラリ内のシンボリックリンクはバックアップできません。"
                            )
                        if source_path.is_file():
                            target_path = staging / source_path.relative_to(self.root)
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            before = source_path.stat()
                            shutil.copy2(source_path, target_path)
                            after = source_path.stat()
                            if (before.st_size, before.st_mtime_ns) != (
                                after.st_size,
                                after.st_mtime_ns,
                            ) or _digest(source_path) != _digest(target_path):
                                raise RuntimeError(
                                    f"バックアップ中に添付が変更されました: {source_path.name}"
                                )
                files = {
                    path.relative_to(staging).as_posix(): {
                        "sha256": _digest(path),
                        "size": path.stat().st_size,
                    }
                    for path in staging.rglob("*")
                    if path.is_file()
                }
                manifest = {
                    "format": "MarkNotes backup",
                    "version": 1,
                    "created_at": _now(),
                    "files": files,
                }
                (staging / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.rename(staging, destination)
            return destination
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @staticmethod
    def restore_backup(backup_dir: Path | str, destination_root: Path | str) -> Path:
        """Restore to a new directory; an existing library is never overwritten."""
        backup_dir = Path(backup_dir).resolve()
        destination = Path(destination_root).resolve()
        if destination.exists():
            raise FileExistsError(destination)
        manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != "MarkNotes backup" or manifest.get("version") != 1:
            raise ValueError("対応していないバックアップ形式です。")
        files = manifest["files"]
        if "library.sqlite3" not in files:
            raise ValueError("バックアップにDBがありません。")
        sources = []
        for relative, metadata in files.items():
            path = Path(relative)
            source = backup_dir / path
            if (
                path.is_absolute()
                or ".." in path.parts
                or not source.resolve().is_relative_to(backup_dir)
                or source.is_symlink()
            ):
                raise ValueError("バックアップに不正なファイルパスがあります。")
            if (
                not source.is_file()
                or source.stat().st_size != metadata["size"]
                or _digest(source) != metadata["sha256"]
            ):
                raise ValueError(f"バックアップの検証に失敗しました: {relative}")
            sources.append((source, path))
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name(f".{destination.name}-{uuid4().hex}.tmp")
        staging.mkdir()
        try:
            for source, relative in sources:
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if _digest(target) != files[relative.as_posix()]["sha256"]:
                    raise ValueError("復元中にバックアップが変更されました。")
            with closing(sqlite3.connect(staging / "library.sqlite3")) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("復元したDBの整合性検証に失敗しました。")
            os.rename(staging, destination)
            return destination
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
