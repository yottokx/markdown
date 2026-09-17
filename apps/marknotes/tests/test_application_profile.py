from pathlib import Path

from marknotes.app import _parse_arguments
from marknotes.application_profile import (
    DEVELOPMENT_PROFILE,
    STABLE_PROFILE,
    application_profile,
)


def test_normal_launch_keeps_the_existing_stable_identity():
    args = _parse_arguments([])

    assert args.dev is False
    assert application_profile(args.dev) == STABLE_PROFILE
    assert STABLE_PROFILE.application_name == "MarkNotes"
    assert STABLE_PROFILE.windows_app_user_model_id == "Yotto.MarkNotes"


def test_dev_launch_uses_a_distinct_qt_and_windows_identity():
    args = _parse_arguments(["--dev"])

    assert args.dev is True
    assert application_profile(args.dev) == DEVELOPMENT_PROFILE
    assert DEVELOPMENT_PROFILE.application_name == "MarkNotes.dev"
    assert DEVELOPMENT_PROFILE.windows_app_user_model_id == "Yotto.MarkNotes.dev"
    assert DEVELOPMENT_PROFILE != STABLE_PROFILE


def test_dev_flag_can_be_combined_with_an_explicit_library(tmp_path):
    root = tmp_path / "library"

    args = _parse_arguments(["--dev", "--library", str(root), "memo.md"])

    assert args.dev is True
    assert args.library == root
    assert args.files == [Path("memo.md")]
