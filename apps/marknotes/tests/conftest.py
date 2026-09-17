"""Keep automated MarkNotes checks off the user's desktop by default."""

import os

import pytest

# Configure Qt before pytest-qt creates QApplication. An explicit override is
# reserved for user-requested interactive UI verification.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_addoption(parser):
    parser.addoption("--dev", action="store_true", help="Use the MarkNotes development profile")


@pytest.fixture(scope="session")
def qapp(qapp, pytestconfig):
    if pytestconfig.getoption("--dev"):
        from marknotes.app import _parse_arguments
        from marknotes.application_icon import set_windows_app_user_model_id
        from marknotes.application_profile import application_profile

        profile = application_profile(_parse_arguments(["--dev"]).dev)
        set_windows_app_user_model_id(profile.windows_app_user_model_id)
        qapp.setApplicationName(profile.application_name)
        qapp.setApplicationDisplayName(profile.display_name)
        qapp.setOrganizationName("MarkNotes")
    return qapp
