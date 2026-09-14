"""Durable, note-scoped attachments without GUI or document mutation.

Callers publish the returned Markdown only after a copy finishes, and retain files
when the insertion is undone.  The library's mutation lock should cover calls when
they run alongside backup or external editing of an attachment.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .assets import Destination, local_path, markdown_destinations, rewrite_destinations

IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg", ".ico", ".avif"}
)


@dataclass(frozen=True, slots=True)
class ManagedAsset:
    asset_id: str
    relative_path: str
    original_name: str
    is_image: bool
    size: int
    markdown: str
    path: Path


@dataclass(frozen=True, slots=True)
class ImportError:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class ImportBatch:
    assets: tuple[ManagedAsset, ...]
    errors: tuple[ImportError, ...]

    @property
    def markdown(self) -> str:
        return "\n".join(asset.markdown for asset in self.assets)


@dataclass(frozen=True, slots=True)
class ImportedMarkdown:
    body: str
    assets: tuple[ManagedAsset, ...]
    warnings: tuple[str, ...]


def escape_link_label(label: str) -> str:
    """Keep filenames literal, including HTML, entities, brackets and backslashes."""
    label = " ".join(label.splitlines())
    return re.sub(r"([\\`*_{}\[\]()!#>+\-.|<&])", r"\\\1", label)


def _plain_directory(path: Path) -> Path:
    path = path.absolute()
    if path.is_symlink() or path.resolve() != path:
        raise ValueError(f"別の場所を参照するフォルダは使用できません: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"フォルダではありません: {path}")
    return path


def resolve_managed_asset(url: str, note_base: Path) -> Path | None:
    """Resolve only existing, direct files inside this note's assets directory.

    Absolute URLs, traversal components, and symbolic links/junctions cannot be
    managed attachments.  Query strings and fragments do not form part of a path.
    """
    try:
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            return None
        decoded = unquote(parsed.path).replace("\\", "/")
        parts = decoded.split("/")
        if len(parts) < 2 or parts[0] != "assets" or any(p in {"", ".", ".."} for p in parts):
            return None
        if any(":" in part or "\x00" in part for part in parts):
            return None
        base = _plain_directory(Path(note_base))
        directory = _plain_directory(base / "assets")
        candidate = base.joinpath(*parts)
        if candidate.is_symlink() or candidate.resolve() != candidate:
            return None
        if not candidate.is_relative_to(directory) or not candidate.is_file():
            return None
        return candidate
    except (OSError, ValueError):
        return None


class ManagedAssets:
    """Copy original bytes into a note, staging before publishing a unique name."""

    def __init__(self, note_base: Path):
        self.base_dir = _plain_directory(Path(note_base))

    @property
    def assets_dir(self) -> Path:
        _plain_directory(self.base_dir)
        directory = _plain_directory(self.base_dir / "assets")
        directory.mkdir(parents=True, exist_ok=True)
        return _plain_directory(directory)

    def _publish(
        self, source: BinaryIO, suffix: str, label: str, *, image: bool, name: str | None = None
    ) -> ManagedAsset:
        suffix = suffix.removeprefix(".").lower()
        if suffix and (
            len(suffix) > 100
            or re.search(r'[\x00-\x1f\\/:*?"<>|.]', suffix)
            or suffix.endswith(" ")
        ):
            raise ValueError("添付ファイルの拡張子が不正です。")
        directory = self.assets_dir
        staging = _plain_directory(self.base_dir / "staging")
        staging.mkdir(exist_ok=True)
        staged_path: Path | None = None
        destination: Path | None = None
        reserved = False
        try:
            with tempfile.NamedTemporaryFile(dir=staging, prefix="copy-", delete=False) as output:
                staged_path = Path(output.name)
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
                size = output.tell()
            # Reserve a fresh name exclusively. The momentary empty reservation
            # is not referenced by a document; replace publishes only complete bytes.
            for _attempt in range(100):
                asset_id = uuid.uuid4().hex
                generated = ("image-" if image else "attachment-") + asset_id
                if suffix:
                    generated += "." + suffix
                destination = directory / (name or generated)
                try:
                    with destination.open("xb"):
                        pass
                    reserved = True
                    break
                except FileExistsError:
                    if name is not None:
                        raise
                    continue
            else:
                raise OSError("添付ファイルの保存名を確保できませんでした。")
            os.replace(staged_path, destination)
            reserved = False
            relative = "assets/" + destination.name
            escaped_label = escape_link_label(label or ("画像" if image else "添付ファイル"))
            markdown = f"{'!' if image else ''}[{escaped_label}]({quote(relative, safe='/-._~')})"
            return ManagedAsset(asset_id, relative, label, image, size, markdown, destination)
        finally:
            if reserved and destination is not None:
                destination.unlink(missing_ok=True)
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)

    def save_bytes(
        self, data: bytes, suffix: str, label: str = "画像", *, image: bool = True
    ) -> ManagedAsset:
        from io import BytesIO

        if image and not data:
            raise ValueError("画像のデータがありません。")
        return self._publish(BytesIO(data), suffix, label, image=image)

    def import_file(self, path: Path, *, image: bool | None = None) -> ManagedAsset:
        path = Path(path)
        if path.is_dir():
            raise ValueError(f"フォルダの取り込みには対応していません: {path.name}")
        if not path.is_file():
            raise FileNotFoundError(f"添付元のファイルが見つかりません: {path}")
        if image is None:
            image = path.suffix.lower() in IMAGE_SUFFIXES
        with path.open("rb") as source:
            return self._publish(source, path.suffix, path.name, image=image)

    def copy_named(self, source: Path, name: str) -> ManagedAsset:
        """Publish a reviewed rename as a durable copy, retaining the original."""
        if (
            not name
            or name in {".", ".."}
            or name.endswith((".", " "))
            or re.search(r'[\x00-\x1f\\/:*?"<>|]', name)
        ):
            raise ValueError("添付ファイル名が不正です。")
        source = Path(source).absolute()
        try:
            relative = source.relative_to(self.base_dir).as_posix()
        except ValueError:
            raise ValueError("このノート内の添付ファイルではありません。") from None
        if resolve_managed_asset(quote(relative, safe="/-._~"), self.base_dir) != source:
            raise ValueError("このノート内の添付ファイルではありません。")
        if Path(name).suffix.lower() != source.suffix.lower():
            raise ValueError("改名時に添付の拡張子は変更できません。")
        with source.open("rb") as stream:
            return self._publish(
                stream,
                source.suffix,
                name,
                image=source.suffix.lower() in IMAGE_SUFFIXES,
                name=name,
            )

    def import_files(self, paths: Iterable[Path]) -> ImportBatch:
        assets: list[ManagedAsset] = []
        errors: list[ImportError] = []
        for path in paths:
            try:
                assets.append(self.import_file(path))
            except (OSError, ValueError) as error:
                errors.append(ImportError(Path(path), str(error)))
        return ImportBatch(tuple(assets), tuple(errors))


def import_markdown(source: str, source_base: Path, manager: ManagedAssets) -> ImportedMarkdown:
    """Copy relative local destinations and preserve all other Markdown bytes.

    Remote URLs, anchors and absolute/file URLs remain unchanged.  Missing local
    files also remain unchanged and are reported for the import UI to disclose.
    Repeated references share one copied asset; code samples are never rewritten.
    """
    imported: dict[Path, ManagedAsset] = {}
    failed: dict[Path, str] = {}

    def replacement(destination: Destination) -> str | None:
        try:
            path = local_path(destination.url, Path(source_base))
        except (OSError, ValueError) as error:
            failed[Path(destination.url)] = str(error)
            return None
        if path is None or path in failed:
            return None
        if path not in imported:
            try:
                imported[path] = manager.import_file(path, image=destination.is_image)
            except (OSError, ValueError) as error:
                failed[path] = str(error)
                return None
        parsed = urlsplit(destination.url)
        return urlunsplit(("", "", imported[path].relative_path, parsed.query, parsed.fragment))

    body = rewrite_destinations(source, replacement)
    return ImportedMarkdown(body, tuple(imported.values()), tuple(failed.values()))


def export_markdown(
    body: str, note_base: Path, destination_dir: Path, filename: str = "note.md"
) -> Path:
    """Publish a new export directory atomically, copying only referenced assets.

    The caller chooses an unused directory. Existing files are never overwritten;
    partial copies stay in a private temporary directory and are removed on failure.
    """
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise ValueError("書き出すMarkdownファイル名が不正です。")
    destination_dir = Path(destination_dir).absolute()
    parent = _plain_directory(destination_dir.parent)
    if destination_dir.exists() or destination_dir.is_symlink():
        raise FileExistsError(f"書き出し先は既に存在します: {destination_dir}")
    if not parent.is_dir():
        raise FileNotFoundError(f"書き出し先の親フォルダが見つかりません: {parent}")
    references: dict[str, Path] = {}
    for destination in markdown_destinations(body):
        parsed = urlsplit(destination.url)
        decoded = unquote(parsed.path).replace("\\", "/")
        if parsed.scheme or parsed.netloc or not decoded.startswith("assets/"):
            continue
        path = resolve_managed_asset(destination.url, note_base)
        if path is None:
            raise ValueError(f"添付ファイルを読み取れません: {destination.url}")
        references[path.relative_to(Path(note_base).absolute()).as_posix()] = path
    staged = Path(tempfile.mkdtemp(prefix=".marknotes-export-", dir=parent))
    try:
        for relative, path in references.items():
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
        with (staged / filename).open("x", encoding="utf-8", newline="") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        # os.rename refuses existing destinations on Windows. On POSIX, reserve
        # the name using an exclusive directory and replace only that empty one.
        if os.name != "nt":
            destination_dir.mkdir()
        try:
            os.rename(staged, destination_dir)
        except BaseException:
            if os.name != "nt":
                destination_dir.rmdir()
            raise
        return destination_dir / filename
    finally:
        if staged.exists():
            shutil.rmtree(staged)
