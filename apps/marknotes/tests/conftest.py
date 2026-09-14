"""Keep automated MarkNotes checks off the user's desktop by default."""

import os

# Configure Qt before pytest-qt creates QApplication. An explicit override is
# reserved for user-requested interactive UI verification.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
