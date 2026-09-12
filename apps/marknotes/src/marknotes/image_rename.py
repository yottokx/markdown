"""Reviewed image rename plans and recoverable filesystem transactions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QStandardPaths, QUrl
from PySide6.QtGui import QImageReader

from .assets import local_path, managed_image_path, markdown_destinations
from .document import (
    ExternalFileChangedError,
    NeedsNewlineSelection,
    SaveResult,
    _canonical_encoding,
    atomic_write,
    decode_document,
    encode_document,
)


@dataclass(frozen=True, slots=True)
class ManagedImage:
    path: Path
    spans: tuple[tuple[int, int], ...]
    urls: tuple[str, ...]
    raster: bool


@dataclass(frozen=True, slots=True)
class RenameEntry:
    source: Path
    target: Path
    shared_with: tuple[Path, ...] = ()

    @property
    def changed(self):
        return self.source.name != self.target.name

    @property
    def case_only(self):
        return self.changed and self.source.name.casefold() == self.target.name.casefold()


@dataclass(frozen=True, slots=True)
class RenamePlan:
    source_text: str
    text: str
    entries: tuple[RenameEntry, ...]
    document: Path | None
    base_dir: Path

    @property
    def changed(self):
        return any(entry.changed for entry in self.entries)


def image_recovery_root() -> Path:
    return (
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        / "image-recovery"
    )


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_hash(path: Path) -> str | None:
    try:
        return _hash(path.read_bytes())
    except OSError:
        return None


def _write_exclusive(path: Path, data: bytes) -> None:
    opened = False
    try:
        with path.open("xb") as stream:
            opened = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if opened:
            path.unlink(missing_ok=True)
        raise


def _image_format(path: Path) -> str:
    reader = QImageReader(str(path))
    reader.setDecideFormatFromContent(True)
    return bytes(reader.format()).decode("ascii").lower() if reader.canRead() else ""


def current_images(session, text: str) -> tuple[ManagedImage, ...]:
    grouped = {}
    for destination, origin, _managed in session.image_reference_states(text):
        if not destination.is_image or origin is None:
            continue
        try:
            relative = origin.relative_to(session.base_dir).as_posix()
        except ValueError:
            continue
        path = managed_image_path(quote(relative, safe="/-._~"), session.base_dir)
        if path is None:
            continue
        image_format = _image_format(path)
        if not image_format:
            continue
        item = grouped.setdefault(
            path,
            {
                "spans": [],
                "urls": [],
                "raster": image_format not in {"svg", "svgz"},
                "first": destination.image_spans[0][0]
                if destination.image_spans
                else destination.start,
            },
        )
        item["first"] = min(
            item["first"],
            destination.image_spans[0][0] if destination.image_spans else destination.start,
        )
        item["spans"].extend(destination.image_spans or ((destination.start, destination.end),))
        item["urls"].append(destination.url)
    return tuple(
        ManagedImage(path, tuple(item["spans"]), tuple(item["urls"]), item["raster"])
        for path, item in sorted(grouped.items(), key=lambda item: item[1]["first"])
    )


def image_at(session, text: str, position: int) -> ManagedImage | None:
    return next(
        (
            image
            for image in current_images(session, text)
            if any(start <= position < end for start, end in image.spans)
        ),
        None,
    )


def validate_image_name(name: str, source: Path) -> str:
    if not name or name in {".", ".."} or name != name.strip() or name.endswith((".", " ")):
        raise ValueError("ファイル名の前後の空白、末尾のピリオド、空の名前は使用できません。")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError("ファイル名に使用できない文字が含まれています。")
    if re.match(r"(?i)^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name):
        raise ValueError("Windowsの予約名は使用できません。")
    if Path(name).suffix.lower() != source.suffix.lower():
        raise ValueError(f"拡張子 {source.suffix} は変更できません。")
    if len(name.encode("utf-16-le")) // 2 > 255:
        raise ValueError("ファイル名が長すぎます。")
    return name


def _referenced_files(path: Path) -> set[Path] | None:
    try:
        decoded = decode_document(path.read_bytes())
        if decoded.encoding_warning:
            return None  # An uncertain codec cannot prove that an old name is unused.
        text = decoded.text
        references = set()
        for destination in markdown_destinations(text):
            url = QUrl(destination.url)
            target = (
                Path(url.toLocalFile()).resolve()
                if url.isLocalFile()
                else local_path(destination.url, path.parent)
            )
            if target is not None:
                references.add(target)
        return references
    except (OSError, UnicodeError, ValueError, LookupError):
        return None


def _other_documents(base: Path, current: Path | None):
    return sorted(
        {*base.glob("*.md"), *base.glob("*.markdown")} - ({current} if current else set())
    )


def shared_documents(path: Path, base: Path, current: Path | None) -> tuple[Path, ...]:
    shared = []
    for document in _other_documents(base, current):
        references = _referenced_files(document)
        if references is None or path in references:
            shared.append(document)
    return tuple(shared)


def make_rename_plan(session, text: str, names: dict[Path, str]) -> RenamePlan:
    available = {image.path: image for image in current_images(session, text)}
    reserved = session.reserved_image_paths()
    entries, targets = [], set()
    for source, name in names.items():
        source = Path(source).resolve()
        if source not in available:
            raise ValueError(f"現在の文書が参照するimg配下の画像ではありません: {source.name}")
        target = source.parent / validate_image_name(name, source)
        same = source.name.casefold() == target.name.casefold()
        identity = str(target).casefold()
        if identity in targets:
            raise ValueError(f"変更後の名前が重複しています: {target.name}")
        targets.add(identity)
        if not same and (target.exists() or target in reserved):
            raise ValueError(f"既存ファイルまたは編集履歴で使用中の名前です: {target.name}")
        entries.append(
            RenameEntry(source, target, shared_documents(source, session.base_dir, session.path))
        )
    changes = {entry.source: entry.target for entry in entries if entry.changed}
    updated = session.rewrite_image_paths(text, changes)
    return RenamePlan(text, updated, tuple(entries), session.path, session.base_dir)


def bulk_rename_plan(
    session,
    text: str,
    *,
    prefix: str | None = None,
    start_number: int = 1,
    digits: int = 5,
) -> RenamePlan:
    """Generate reviewed names in image order; prefix includes any separator.

    Digits is a minimum width. Numbers grow beyond it without wrapping, and
    names already occupied by files or editing history are skipped.
    """
    if prefix is None:
        prefix = f"{session.path.stem if session.path else 'untitled'}_"
    if not isinstance(prefix, str):
        raise TypeError("プレフィックスは文字列で指定してください。")
    if type(start_number) is not int or start_number < 0:
        raise ValueError("開始番号は0以上の整数で指定してください。")
    if type(digits) is not int or not 1 <= digits <= 12:
        raise ValueError("連番の最小桁数は1〜12で指定してください。")
    occupied = session.reserved_image_paths()
    names, sequence = {}, start_number
    for image in current_images(session, text):
        while True:
            name = validate_image_name(
                f"{prefix}{sequence:0{digits}d}{image.path.suffix}", image.path
            )
            sequence += 1
            target = image.path.parent / name
            if target == image.path or target not in occupied and not target.exists():
                break
        names[image.path] = name
        occupied.add(target)
    return make_rename_plan(session, text, names)


def _record_write(directory: Path, record: dict) -> None:
    atomic_write(
        directory / "transaction.json",
        (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode(),
    )


def _case_move(source: Path, target: Path, temporary: Path) -> None:
    os.replace(source, temporary)
    try:
        os.replace(temporary, target)
    except BaseException:
        os.replace(temporary, source)
        raise


def _snapshot_recovery_document(
    session, plan: RenamePlan, directory: Path, image_data: dict[Path, bytes]
):
    replacements = {}
    renamed = {entry.source: entry.target for entry in plan.entries}
    for destination, origin, _managed in session.image_reference_states(plan.source_text):
        if origin is None:
            continue
        target = renamed.get(origin, origin)
        if destination.is_image and origin in image_data:
            relative = target.relative_to(plan.base_dir)
            recovery_target = directory / relative
            recovery_target.parent.mkdir(parents=True, exist_ok=True)
            if not recovery_target.exists():
                _write_exclusive(recovery_target, image_data[origin])
            target = recovery_target
        replacements[origin] = target
    recovered = session.rewrite_image_paths(plan.source_text, replacements, base_dir=directory)
    atomic_write(directory / "recovered.md", recovered.encode("utf-8"))


def execute_rename(session, plan: RenamePlan, *, encoding=None, newline=None) -> SaveResult:
    if plan.document != session.path or plan.base_dir != session.base_dir:
        raise ValueError("文書の保存先が変更されました。改名一覧を開き直してください。")
    # Revalidate names/collisions and shared references immediately before writing.
    verified = make_rename_plan(
        session, plan.source_text, {entry.source: entry.target.name for entry in plan.entries}
    )
    if not verified.changed:
        return SaveResult(plan.source_text, session.path, False)
    if session.path is not None and session.has_external_change():
        raise ExternalFileChangedError(
            "Markdownが外部で更新されています。開き直してから改名してください。"
        )
    if session.path is not None and session.mixed_newlines and newline is None:
        raise NeedsNewlineSelection(
            "改行コードが混在しています。保存形式を選択してから改名してください。"
        )
    selected_encoding, selected_newline = encoding or session.encoding, newline or session.newline
    selected_bom = (
        session.bom
        if encoding is None
        or _canonical_encoding(encoding) == _canonical_encoding(session.encoding)
        else b""
    )
    after = encode_document(verified.text, selected_encoding, selected_newline, selected_bom)
    before = session.path.read_bytes() if session.path is not None else plan.source_text.encode()
    directory = session.recovery_root / uuid.uuid4().hex
    directory.mkdir(parents=True)
    image_data = {
        image.path: image.path.read_bytes() for image in current_images(session, plan.source_text)
    }
    record = {
        "schema": 1,
        "state": "prepared",
        "document": str(session.path) if session.path else None,
        "base_dir": str(session.base_dir),
        "before_sha256": _hash(before),
        "after_sha256": _hash(after),
        "entries": [],
    }
    atomic_write(directory / "document.before", before)
    atomic_write(directory / "document.after", after)
    for index, entry in enumerate(verified.entries):
        if not entry.changed:
            continue
        backup = f"image-{index:04d}{entry.source.suffix}"
        data = image_data[entry.source]
        atomic_write(directory / backup, data)
        record["entries"].append(
            {
                "old": entry.source.relative_to(session.base_dir).as_posix(),
                "new": entry.target.relative_to(session.base_dir).as_posix(),
                "backup": backup,
                "sha256": _hash(data),
                "case_only": entry.case_only,
                "temporary": (entry.source.parent / f".marknotes-case-{uuid.uuid4().hex}.tmp")
                .relative_to(session.base_dir)
                .as_posix(),
            }
        )
    _snapshot_recovery_document(session, verified, directory, image_data)
    _record_write(directory, record)
    created, case_changes, markdown_written = [], [], False
    try:
        for entry in verified.entries:
            if entry.changed and not entry.case_only:
                _write_exclusive(entry.target, image_data[entry.source])
                created.append(entry.target)
        if any(_read_hash(path) != _hash(data) for path, data in image_data.items()):
            raise OSError("画像が操作中に更新されました。改名一覧を開き直してください。")
        if session.path is not None:
            if session.has_external_change():
                raise ExternalFileChangedError("Markdownが操作中に外部更新されました。")
            atomic_write(session.path, after)
            markdown_written = True
        for entry, saved in zip(
            (e for e in verified.entries if e.changed), record["entries"], strict=True
        ):
            if entry.case_only:
                _case_move(entry.source, entry.target, session.base_dir / saved["temporary"])
                case_changes.append((entry, saved))
        record["state"] = "committed"
        _record_write(directory, record)
    except BaseException:
        # A competing writer or a failed rollback may leave the MD referring to
        # the newly prepared names. Keep both names whenever that is uncertain.
        rollback_clean = not markdown_written
        if markdown_written:
            if _read_hash(session.path) == _hash(after):
                try:
                    atomic_write(session.path, before)
                except OSError:
                    pass
            rollback_clean = _read_hash(session.path) == _hash(before)
        if rollback_clean:
            for entry, saved in reversed(case_changes):
                try:
                    _case_move(entry.target, entry.source, session.base_dir / saved["temporary"])
                except OSError:
                    rollback_clean = False
            for saved in record["entries"]:
                original = session.base_dir / saved["old"]
                if not original.exists():
                    try:
                        _write_exclusive(original, (directory / saved["backup"]).read_bytes())
                    except OSError:
                        rollback_clean = False
        if rollback_clean:
            references = _referenced_files(session.path) if session.path else set()
            for target in created:
                expected = image_data[
                    next(e.source for e in verified.entries if e.target == target)
                ]
                if (
                    _read_hash(target) == _hash(expected)
                    and references is not None
                    and target not in references
                    and not shared_documents(target, session.base_dir, session.path)
                ):
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        rollback_clean = False
        record["state"] = "rolled_back" if rollback_clean else "conflict"
        try:
            _record_write(directory, record)
        except OSError:
            pass  # The prepared journal already contains every recovery backup.
        raise
    session._rename_journals.append(directory)
    for saved in record["entries"]:
        session._rename_backups[session.base_dir / saved["old"]] = directory / saved["backup"]
    if session.path is not None:
        session.accept_saved_bytes(after, selected_encoding, selected_newline)
    return SaveResult(verified.text, session.path, verified.text != plan.source_text)


def recovery_records(root: Path) -> list[tuple[Path, dict]]:
    if not root.exists():
        return []
    records = []
    for path in sorted(root.glob("*/transaction.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                isinstance(record, dict)
                and record.get("schema") == 1
                and isinstance(record.get("base_dir"), str)
                and (record.get("document") is None or isinstance(record.get("document"), str))
                and isinstance(record.get("entries"), list)
                and all(isinstance(entry, dict) for entry in record["entries"])
            ):
                records.append((path.parent, record))
        except (OSError, ValueError):
            continue
    return records


def _validated_entries(directory: Path, record: dict):
    base = Path(record["base_dir"]).resolve()
    if record.get("document") and Path(record["document"]).resolve().parent != base:
        raise ValueError("画像操作の復旧記録の保存先が不正です。")
    for entry in record["entries"]:
        if any(
            not isinstance(entry.get(key), str)
            for key in ("old", "new", "temporary", "backup", "sha256")
        ):
            raise ValueError("画像操作の復旧記録の形式が不正です。")
        old, new = base / entry["old"], base / entry["new"]
        temporary = base / entry["temporary"]
        backup = directory / entry["backup"]
        if (
            not old.resolve().is_relative_to(base / "img")
            or not new.resolve().is_relative_to(base / "img")
            or old.parent != new.parent
            or not temporary.resolve().is_relative_to(base / "img")
            or not backup.resolve().is_relative_to(directory.resolve())
            or _read_hash(backup) != entry["sha256"]
        ):
            raise ValueError("画像操作の復旧記録が不正または破損しています。")
        yield entry, old, new, temporary, backup


def finalize_rename(directory: Path, *, recovering: bool = False) -> str | None:
    record = json.loads((directory / "transaction.json").read_text(encoding="utf-8"))
    if record.get("state") in {"complete", "rolled_back", "recovered"}:
        return None
    document = Path(record["document"]) if record.get("document") else None
    if document is None:
        return "未保存文書の画像操作を復旧できます。" if recovering else None
    current_hash = _read_hash(document)
    if record["state"] == "prepared" and current_hash == record["before_sha256"]:
        mode = "rollback"
    elif record["state"] == "committed" or current_hash == record["after_sha256"]:
        mode = "committed"
    else:
        mode = "conflict"
    references = _referenced_files(document)
    for entry, old, new, temporary, backup in _validated_entries(directory, record):
        data = backup.read_bytes()
        if temporary.exists() and _read_hash(temporary) == entry["sha256"]:
            desired = old if mode == "rollback" else new
            if not desired.exists():
                os.replace(temporary, desired)
        for desired in (
            (old,) if mode == "rollback" else (old, new) if mode == "conflict" else (new,)
        ):
            if not desired.exists():
                desired.parent.mkdir(parents=True, exist_ok=True)
                _write_exclusive(desired, data)
        if (
            mode == "committed"
            and not entry["case_only"]
            and references is not None
            and old not in references
        ):
            if (
                not shared_documents(old, document.parent, document)
                and _read_hash(old) == entry["sha256"]
            ):
                old.unlink()
        elif (
            mode == "rollback"
            and not entry["case_only"]
            and _read_hash(new) == entry["sha256"]
            and not shared_documents(new, document.parent, document)
            and references is not None
            and new not in references
        ):
            new.unlink()
    record["state"] = (
        "complete" if mode == "committed" else "rolled_back" if mode == "rollback" else "conflict"
    )
    _record_write(directory, record)
    return "中断した画像操作の整合性を確認・復旧しました。" if recovering else None


def recover_for_document(path: Path, root: Path) -> list[str]:
    messages = []
    for directory, record in recovery_records(root):
        if record.get("document") and Path(record["document"]).resolve() == path.resolve():
            try:
                message = finalize_rename(directory, recovering=True)
                if message:
                    messages.append(message)
            except (OSError, ValueError, KeyError):
                messages.append(
                    "画像操作の復旧記録があります。ツールメニューから確認してください。"
                )
    return messages
