"""Shared text-cleaning helpers used by multiple services.

Kept in a separate module so both `ingestion` and `snapshots` can import
it without creating an import cycle (ingestion already imports snapshots).
"""
from __future__ import annotations

import html as _html
import re

_TAG_STRIP = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def strip_html_to_text(text: str | None) -> str:
    """Remove HTML tags, decode entities, collapse whitespace.

    Safe to call on already-clean Unicode text — entity decoding is a no-op
    when there are no entities, and the tag regex matches nothing in plain text.
    Used as a final filter before storing or displaying text that may have
    originated from an HTML source.
    """
    if not text:
        return ""
    decoded = _html.unescape(text)
    stripped = _TAG_STRIP.sub(" ", decoded)
    return _WHITESPACE.sub(" ", stripped).strip()
