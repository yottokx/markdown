"""Stable and development identities for isolated MarkNotes data."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApplicationProfile:
    application_name: str
    display_name: str
    windows_app_user_model_id: str


STABLE_PROFILE = ApplicationProfile(
    application_name="MarkNotes",
    display_name="MarkNotes",
    windows_app_user_model_id="Yotto.MarkNotes",
)

DEVELOPMENT_PROFILE = ApplicationProfile(
    application_name="MarkNotes.dev",
    display_name="MarkNotes Dev",
    windows_app_user_model_id="Yotto.MarkNotes.dev",
)


def application_profile(development: bool) -> ApplicationProfile:
    return DEVELOPMENT_PROFILE if development else STABLE_PROFILE
