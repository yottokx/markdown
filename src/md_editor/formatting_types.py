"""Pure source edits shared by formatting engines and the Qt context menu."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FormatEdit:
    """Replace source[start:end]; selection offsets are relative to replacement text."""

    start: int
    end: int
    text: str
    selection_start: int
    selection_end: int


@dataclass(frozen=True, slots=True)
class FormatAction:
    """An unavailable operation has edit=None. Keys are stable and non-localized."""

    key: str
    label: str
    edit: FormatEdit | None = None
    checked: bool = False
    group: str = ""
