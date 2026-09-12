"""Lossless text format handling and transactional document/image saves."""

from __future__ import annotations

import codecs
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from PySide6.QtGui import QImage

from .assets import (
    AssetManager,
    Destination,
    local_path,
    managed_image_path,
    markdown_destinations,
    rewrite_destinations,
)

NEWLINES = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}
ENCODINGS = ("utf-8", "utf-8-sig", "utf-16-le", "utf-16-be", "cp932", "shift_jis", "euc_jp")
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


class NeedsNewlineSelection(ValueError):
    """Mixed input line endings require an explicit normalization choice."""


class ExternalFileChangedError(OSError):
    """A loaded document changed on disk before saving."""


@dataclass(frozen=True, slots=True)
class DecodedDocument:
    text: str
    encoding: str
    newline: str
    mixed_newlines: bool
    bom: bytes = b""
    encoding_warning: str = ""


@dataclass(frozen=True, slots=True)
class SaveResult:
    text: str
    path: Path | None
    rewritten: bool
    reference_map: dict[str, str] = field(default_factory=dict)


def _canonical_encoding(encoding: str) -> str:
    name = codecs.lookup(encoding).name
    return {"utf-16": "utf-16-le", "utf-32": "utf-32-le"}.get(name, name)


def _newline_information(text: str) -> tuple[str, bool]:
    matches = re.findall(r"\r\n|\r|\n", text)
    if not matches:
        return "LF", False
    reverse = {value: key for key, value in NEWLINES.items()}
    counts = {value: matches.count(value) for value in set(matches)}
    # Prefer the first encountered kind when counts tie.
    chosen = max(dict.fromkeys(matches), key=lambda value: counts[value])
    return reverse[chosen], len(counts) > 1


def decode_document(data: bytes, encoding: str | None = None) -> DecodedDocument:
    """Detect BOM, strict UTF-8, then legacy encodings without replacement chars."""
    detected_bom = b""
    bom_encoding = ""
    for candidate_bom, candidate_encoding in _BOMS:
        if data.startswith(candidate_bom):
            detected_bom, bom_encoding = candidate_bom, candidate_encoding
            break
    warning = ""
    utf16_guess = None
    if encoding is None and not detected_bom and len(data) >= 4 and len(data) % 2 == 0:
        even_zeros, odd_zeros = data[::2].count(0), data[1::2].count(0)
        minimum = len(data) / 20
        if odd_zeros >= minimum and odd_zeros > 3 * even_zeros:
            utf16_guess = "utf-16-le"
        elif even_zeros >= minimum and even_zeros > 3 * odd_zeros:
            utf16_guess = "utf-16-be"
    if encoding is not None:
        chosen = _canonical_encoding(encoding)
        if codecs.lookup(encoding).name in {"utf-16", "utf-32"} and bom_encoding.startswith(
            codecs.lookup(encoding).name
        ):
            chosen = bom_encoding
        compatible = bom_encoding and (
            chosen == _canonical_encoding(bom_encoding)
            or {chosen, bom_encoding} <= {"utf-8", "utf-8-sig"}
        )
        bom = detected_bom if compatible else b""
        payload = data[len(bom) :]
        text = payload.decode("utf-8" if chosen == "utf-8-sig" else chosen, errors="strict")
        if bom and chosen == "utf-8":
            chosen = "utf-8-sig"
    elif detected_bom:
        chosen, bom = bom_encoding, detected_bom
        text = data[len(bom) :].decode(
            "utf-8" if chosen == "utf-8-sig" else chosen, errors="strict"
        )
    elif utf16_guess:
        chosen, bom = utf16_guess, b""
        text = data.decode(chosen, errors="strict")
        warning = f"文字コードは推定です: {chosen} (BOMなし)"
    elif data.startswith((b"\x1b$", b"\x1b(")):
        chosen, bom = "iso2022_jp", b""
        text = data.decode(chosen, errors="strict")
        warning = "文字コードは推定です: iso2022_jp"
    else:
        bom = b""
        try:
            text = data.decode("utf-8", errors="strict")
            chosen = "utf-8"
        except UnicodeDecodeError:
            from charset_normalizer import from_bytes

            guesses = from_bytes(data)
            best = guesses.best()
            valid_japanese: dict[str, str] = {}
            for candidate in ("cp932", "shift_jis", "euc_jp"):
                try:
                    valid_japanese[candidate] = data.decode(candidate, errors="strict")
                except UnicodeDecodeError:
                    pass
            preferred = _canonical_encoding(best.encoding) if best else None
            if preferred in valid_japanese:
                chosen = preferred
            elif valid_japanese:
                # Short Japanese snippets are frequently guessed as European
                # encodings. Prefer full kana/kanji over half-width noise.
                def japanese_score(candidate):
                    decoded = valid_japanese[candidate]
                    return sum(
                        2 if "\u3040" <= c <= "\u9fff" else -1 if "\uff61" <= c <= "\uff9f" else 0
                        for c in decoded
                    )

                chosen = max(valid_japanese, key=japanese_score)
            elif best:
                chosen = preferred
            else:
                raise UnicodeError(
                    "文字コードを判定できません。文字コードを指定して開いてください。"
                )
            text = data.decode(chosen, errors="strict")
            alternatives = ", ".join(valid_japanese)
            warning = f"文字コードは推定です: {chosen}"
            if len(valid_japanese) > 1:
                warning += f" (候補: {alternatives})"
    if "\x00" in text:
        raise UnicodeError(
            "NUL文字が含まれています。UTF-16等の文字コードを指定して開いてください。"
        )
    newline, mixed = _newline_information(text)
    return DecodedDocument(
        text.replace("\r\n", "\n").replace("\r", "\n"), chosen, newline, mixed, bom, warning
    )


def encode_document(text: str, encoding: str, newline: str, bom: bytes = b"") -> bytes:
    """Preserve EOF while encoding strictly; callers choose how to handle errors."""
    if newline not in NEWLINES:
        raise ValueError(f"対応していない改行コードです: {newline}")
    chosen = _canonical_encoding(encoding)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\n", NEWLINES[newline])
    if chosen == "utf-8-sig":
        return codecs.BOM_UTF8 + normalized.encode("utf-8", errors="strict")
    payload = normalized.encode(chosen, errors="strict")
    compatible_bom = next(
        (prefix for prefix, enc in _BOMS if prefix == bom and _canonical_encoding(enc) == chosen),
        b"",
    )
    if encoding.lower().replace("_", "-") in {"utf-16", "utf-32"}:
        compatible_bom = codecs.BOM_UTF16_LE if chosen == "utf-16-le" else codecs.BOM_UTF32_LE
    return compatible_bom + payload


def atomic_write(path: Path, data: bytes) -> None:
    """Replace one Markdown file only after its complete bytes have reached disk."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary_path, path.stat().st_mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class _ReferenceState:
    destination: Destination
    origin: Path | None
    managed: bool


def _unchanged_ranges(old: str, new: str) -> list[tuple[int, int, int]]:
    """Return old/new offsets and lengths for unchanged spans of one edit.

    Most keystrokes are one insertion, so trim common edges before asking
    SequenceMatcher to compare the small changed region.
    """
    prefix = 0
    limit = min(len(old), len(new))
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[len(old) - suffix - 1] == new[len(new) - suffix - 1]:
        suffix += 1
    result = [(0, 0, prefix)] if prefix else []
    old_end, new_end = len(old) - suffix, len(new) - suffix
    old_middle, new_middle = old[prefix:old_end], new[prefix:new_end]
    if old_middle and new_middle and max(len(old_middle), len(new_middle)) <= 16000:
        for block in SequenceMatcher(
            None, old_middle, new_middle, autojunk=False
        ).get_matching_blocks():
            if block.size:
                result.append((prefix + block.a, prefix + block.b, block.size))
    if suffix:
        result.append((old_end, new_end, suffix))
    return result


class DocumentSession:
    """Text format, local images, and provenance-aware references across Undo.

    Reference provenance belongs to source snapshots, not URL strings: a newly
    typed img/a.png after Save As must not inherit an old image's URL alias.
    The editor calls remember_references after changes, and renders/saves using
    normalize_references without modifying its native QTextDocument undo stack.
    """

    def __init__(self, recovery_root: Path | None = None):
        self._recovery_root_override = recovery_root
        self._rename_journals: list[Path] = []
        self._rename_backups: dict[Path, Path] = {}
        self.recovery_messages: list[str] = []
        self._temporary = tempfile.TemporaryDirectory(prefix="marknotes-")
        self.path: Path | None = None
        self._base_override: Path | None = None
        self.encoding = "utf-8"
        self.newline = "LF"
        self.mixed_newlines = False
        self.bom = b""
        self.encoding_warning = ""
        self._snapshots: dict[tuple[str, int | None], tuple[_ReferenceState, ...]] = {}
        self._active_source = ""
        self._active_references: tuple[_ReferenceState, ...] = ()
        self._asset_redirects: dict[Path, Path] = {}
        self._disk_bytes: bytes | None = None

    @property
    def base_dir(self) -> Path:
        return (
            self.path.parent if self.path else (self._base_override or Path(self._temporary.name))
        )

    @property
    def assets(self) -> AssetManager:
        return AssetManager(self.base_dir, self.path.stem if self.path else "untitled")

    def new(self, base_dir: Path | None = None) -> None:
        self._finalize_image_renames()
        self._rename_backups.clear()
        self.recovery_messages = []
        replacement = tempfile.TemporaryDirectory(prefix="marknotes-")
        self._temporary.cleanup()
        self._temporary = replacement
        self.path = None
        self._base_override = Path(base_dir).resolve() if base_dir is not None else None
        self.encoding, self.newline = "utf-8", "LF"
        self.mixed_newlines = False
        self.bom, self.encoding_warning = b"", ""
        self._snapshots.clear()
        self._active_source, self._active_references = "", ()
        self._asset_redirects.clear()
        self._disk_bytes = None

    def adopt(self, path: Path | None = None, base_dir: Path | None = None) -> None:
        """Configure programmatic/sample text without reading it as the document."""
        self.new(base_dir)
        if path is not None:
            self.path = Path(path).resolve()
            self._disk_bytes = self.path.read_bytes() if self.path.is_file() else None

    def open(self, path: Path, encoding: str | None = None) -> str:
        resolved = Path(path).resolve()
        from .image_rename import recover_for_document

        recovery_messages = recover_for_document(resolved, self.recovery_root)
        data = resolved.read_bytes()
        decoded = decode_document(data, encoding)
        self.new()
        self.path = resolved
        self.encoding, self.newline = decoded.encoding, decoded.newline
        self.mixed_newlines, self.bom = decoded.mixed_newlines, decoded.bom
        self.encoding_warning = decoded.encoding_warning
        self._disk_bytes = data
        self.recovery_messages = recovery_messages
        self.remember_references(decoded.text)
        return decoded.text

    @staticmethod
    def _source_key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()

    def remember_references(
        self, text: str, *, history: bool | None = None, history_key: int | None = None
    ) -> None:
        """Remember source provenance; unchanged spans carry original targets.

        The UI supplies Qt's undo-step count as history_key and distinguishes
        new edits from Undo/Redo with undoCommandAdded. Identical source text in
        different undo states can then retain different image origins.
        """
        if history is None and history_key is None and text == self._active_source:
            return
        key = (self._source_key(text), history_key)
        previous = self._snapshots.get(key)
        if previous is not None and history is not False:
            self._active_source, self._active_references = text, previous
            return
        old_by_span = {
            (ref.destination.start, ref.destination.end): ref for ref in self._active_references
        }
        unchanged = _unchanged_ranges(self._active_source, text)
        references = []
        for destination in markdown_destinations(text):
            inherited = None
            for old_start, new_start, length in unchanged:
                if new_start <= destination.start and destination.end <= new_start + length:
                    offset = old_start - new_start
                    inherited = old_by_span.get(
                        (destination.start + offset, destination.end + offset)
                    )
                    if inherited and inherited.destination.url != destination.url:
                        inherited = None
                    break
            if inherited is not None:
                reference = _ReferenceState(destination, inherited.origin, inherited.managed)
            else:
                origin = local_path(destination.url, self.base_dir)
                managed = (
                    destination.is_image
                    and managed_image_path(destination.url, self.base_dir) is not None
                )
                reference = _ReferenceState(destination, origin, managed)
            references.append(reference)
        self._active_source, self._active_references = text, tuple(references)
        self._snapshots[key] = self._active_references

    def _register_image(self, snippet: str) -> str:
        # Register the freshly-created file, even if the same filename occurred
        # in another directory earlier. Native UI histories use separate keys.
        references = tuple(
            _ReferenceState(destination, local_path(destination.url, self.base_dir), True)
            for destination in markdown_destinations(snippet)
        )
        self._snapshots[(self._source_key(snippet), None)] = references
        return snippet

    def add_image(self, image: QImage) -> str:
        return self._register_image(self.assets.add_image(image))

    def import_image_file(self, path: Path) -> str:
        return self._register_image(self.assets.import_image_file(path))

    def _references_for(self, text: str) -> tuple[_ReferenceState, ...]:
        if text == self._active_source:
            return self._active_references
        key = (self._source_key(text), None)
        if key not in self._snapshots:
            self.remember_references(text)
        return self._snapshots[key]

    def _origin(self, reference: _ReferenceState) -> Path | None:
        origin = reference.origin
        resolved = self._asset_redirects.get(origin, origin) if origin is not None else None
        backup = self._rename_backups.get(resolved)
        if (
            resolved is not None
            and not resolved.exists()
            and backup is not None
            and backup.is_file()
        ):
            from .image_rename import _write_exclusive

            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                _write_exclusive(resolved, backup.read_bytes())
            except OSError:
                pass  # Keep the retained backup; a later save/recovery can retry.
        return resolved

    @staticmethod
    def _url_for_origin(
        reference: _ReferenceState,
        origin: Path | None,
        base: Path,
        *,
        preserve_spelling: bool = True,
    ) -> str:
        url = reference.destination.url
        if origin is None or preserve_spelling and local_path(url, base) == origin:
            return url
        parsed = urlsplit(url)
        if origin.is_relative_to(base):
            relative = origin.relative_to(base).as_posix()
        else:
            try:
                relative = os.path.relpath(origin, base).replace("\\", "/")
            except ValueError:
                # Windows cannot express a path across drive letters relatively.
                # An encoded file URI keeps historical/ordinary local links usable;
                # restored owned images are promoted into img on their next save.
                absolute = urlsplit(origin.as_uri())
                return urlunsplit(
                    (absolute.scheme, absolute.netloc, absolute.path, parsed.query, parsed.fragment)
                )
        return urlunsplit(("", "", quote(relative, safe="/-._~"), parsed.query, parsed.fragment))

    def normalize_references(self, text: str) -> str:
        references = self._references_for(text)
        changes = {
            ref.destination.start: self._url_for_origin(ref, self._origin(ref), self.base_dir)
            for ref in references
        }
        return rewrite_destinations(text, lambda d: changes.get(d.start))

    def has_external_change(self) -> bool:
        if self.path is None or self._disk_bytes is None:
            return False
        try:
            return self.path.read_bytes() != self._disk_bytes
        except OSError:
            return True

    def save(
        self,
        text: str,
        path: Path | None = None,
        encoding: str | None = None,
        newline: str | None = None,
        *,
        overwrite_external: bool = False,
    ) -> SaveResult:
        target = Path(path).resolve() if path is not None else self.path
        if target is None:
            raise ValueError("保存先を指定してください。")
        if self.mixed_newlines and newline is None:
            raise NeedsNewlineSelection(
                "改行コードが混在しています。保存する改行コードを選んでください。"
            )
        if target == self.path and not overwrite_external and self.has_external_change():
            raise ExternalFileChangedError(
                "ファイルが外部で変更されています。保存先を変更するか開き直してください。"
            )
        selected_encoding, selected_newline = encoding or self.encoding, newline or self.newline
        selected_bom = (
            self.bom
            if encoding is None
            or (_canonical_encoding(encoding) == _canonical_encoding(self.encoding))
            else b""
        )
        self.remember_references(text)
        references = self._references_for(text)
        normalized = self.normalize_references(text)
        encode_document(normalized, selected_encoding, selected_newline, selected_bom)
        old_base, new_base = self.base_dir, target.parent
        mapping: dict[str, str] = {}
        created: list[Path] = []
        copied: dict[Path, Path] = {}
        try:
            manager = AssetManager(new_base, target.stem)
            relocate = old_base != new_base or self.path is None
            for reference in references:
                image_path = self._origin(reference)
                if not reference.managed or image_path is None or not image_path.is_file():
                    continue
                needs_copy = relocate or not image_path.is_relative_to(new_base / "img")
                if needs_copy and image_path not in copied:
                    new_image = manager.copy_managed(image_path)
                    created.append(new_image)
                    copied[image_path] = new_image
            changes = {}
            for reference in references:
                origin = self._origin(reference)
                resolved = copied.get(origin, origin)
                url = self._url_for_origin(reference, resolved, new_base)
                changes[reference.destination.start] = url
                if url != reference.destination.url:
                    mapping[reference.destination.url] = url
            rewritten = rewrite_destinations(text, lambda d: changes.get(d.start))
            data = encode_document(rewritten, selected_encoding, selected_newline, selected_bom)
            atomic_write(target, data)
        except BaseException:
            for image_path in created:
                image_path.unlink(missing_ok=True)
            raise
        self._asset_redirects = {
            old: copied.get(current, current) for old, current in self._asset_redirects.items()
        }
        self._asset_redirects.update(copied)
        self.path, self._base_override = target, None
        self.accept_saved_bytes(data, selected_encoding, selected_newline)
        return SaveResult(rewritten, target, rewritten != text, mapping)

    @property
    def recovery_root(self) -> Path:
        if self._recovery_root_override is not None:
            return Path(self._recovery_root_override)
        from .image_rename import image_recovery_root

        return image_recovery_root()

    def image_reference_states(self, text: str):
        return tuple(
            (ref.destination, self._origin(ref), ref.managed) for ref in self._references_for(text)
        )

    def reserved_image_paths(self) -> set[Path]:
        image_dir = self.base_dir / "img"
        paths = {
            ref.origin
            for references in self._snapshots.values()
            for ref in references
            if ref.destination.is_image and ref.origin is not None
        }
        paths.update(self._asset_redirects.keys())
        paths.update(self._asset_redirects.values())
        return {path for path in paths if path.is_relative_to(image_dir)}

    def rewrite_image_paths(
        self, text: str, replacements: dict[Path, Path], *, base_dir: Path | None = None
    ) -> str:
        base = base_dir or self.base_dir
        updates = {}
        for ref in self._references_for(text):
            origin = self._origin(ref)
            target = replacements.get(origin, origin)
            updates[ref.destination.start] = self._url_for_origin(
                ref, target, base, preserve_spelling=origin not in replacements
            )
        return rewrite_destinations(text, lambda destination: updates.get(destination.start))

    def accept_saved_bytes(self, data: bytes, encoding: str, newline: str) -> None:
        self.encoding = _canonical_encoding(encoding)
        self.newline, self.mixed_newlines = newline, False
        self.bom = next((prefix for prefix, _ in _BOMS if data.startswith(prefix)), b"")
        if self.bom == codecs.BOM_UTF8:
            self.encoding = "utf-8-sig"
        self.encoding_warning = ""
        self._disk_bytes = data

    def rename_images(self, text: str, names: dict[Path, str], *, encoding=None, newline=None):
        from .image_rename import execute_rename, make_rename_plan

        return execute_rename(
            self, make_rename_plan(self, text, names), encoding=encoding, newline=newline
        )

    def _finalize_image_renames(self) -> None:
        if not self._rename_journals:
            return
        import json

        from .image_rename import _record_write, finalize_rename

        for directory in self._rename_journals:
            try:
                record = json.loads((directory / "transaction.json").read_text(encoding="utf-8"))
                if not record.get("document"):
                    record["state"] = "complete"
                    _record_write(directory, record)
                else:
                    finalize_rename(directory)
            except (OSError, ValueError, KeyError):
                pass  # Preserve originals, backups and journal for next-start recovery.
        self._rename_journals.clear()

    def close(self) -> None:
        self._finalize_image_renames()
        self._temporary.cleanup()
