"""HTML image candidates, including the commas inside data URLs."""

from __future__ import annotations

import re
from html import escape, unescape
from urllib.parse import urljoin


def parse_srcset(value: str) -> list[tuple[str, str]]:
    candidates = []
    position = 0
    while position < len(value):
        while position < len(value) and (value[position].isspace() or value[position] == ","):
            position += 1
        start = position
        while position < len(value) and not value[position].isspace():
            position += 1
        url = value[start:position]
        if not url:
            break
        descriptor = ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            start = position
            while position < len(value) and value[position] != ",":
                position += 1
            descriptor = value[start:position].strip()
            position += 1
        candidates.append((url, descriptor))
    return candidates


def resolve_srcsets(fragment: str, base_url: str) -> str:
    """Rewrite candidates in nh3's quoted attributes without reserializing HTML."""

    def replace(match):
        values = [
            urljoin(base_url, url) + (" " + descriptor if descriptor else "")
            for url, descriptor in parse_srcset(unescape(match[2]))
        ]
        return match[1] + escape(", ".join(values), quote=True) + match[3]

    return re.sub(r'(<(?:img|source)\b[^>]*?\bsrcset=")([^"]*)(")', replace, fragment)
