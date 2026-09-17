"""URL scheme policy shared by rendered links and external navigation."""

import re
from urllib.parse import urlsplit

_BLOCKED_SCHEMES = {
    "about",
    "blob",
    "chrome",
    "chrome-extension",
    "data",
    "devtools",
    "filesystem",
    "javascript",
    "qrc",
    "resource",
    "vbscript",
    "view-source",
}


def link_scheme(value: str) -> str | None:
    """Return a normalized scheme, or None for a malformed URL."""
    try:
        return urlsplit(re.sub(r"[\x00-\x20]", "", value)).scheme.lower()
    except ValueError:
        return None


def is_safe_link(value: str) -> bool:
    """Permit ordinary links, including relative/file and custom scheme URLs."""
    scheme = link_scheme(value)
    return scheme is not None and scheme not in _BLOCKED_SCHEMES


def is_external_link(value: str) -> bool:
    """Local files have their own app-specific navigation policy."""
    scheme = link_scheme(value)
    return bool(scheme) and scheme != "file" and scheme not in _BLOCKED_SCHEMES
