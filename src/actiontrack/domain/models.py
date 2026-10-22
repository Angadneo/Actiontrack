"""Domain data models (filled in by later milestones) and the ``ItemId`` helpers (T-M1-13).

Public API (ItemId helpers, spec FR-03.1 / 7.2 / BR-11)
-------------------------------------------------------
``format_item_id(n) -> str``   ``4 -> "AT-0004"``; zero-padded to at least 4 digits, wider
                               numbers print in full (``12345 -> "AT-12345"``), never truncated.
``parse_item_id(text) -> int`` accepts ``AT-0012``, ``12``, ``at-12`` (case-insensitive prefix,
                               ASCII digits only, value >= 1); anything else raises
                               ``UsageError`` (exit 2): ``abc``, ``AT-``, ``-5``, ``0``, ``1.5``,
                               ``+3``, blank, surrounding whitespace, non-ASCII digits.
"""

from __future__ import annotations

import re

from actiontrack.errors import UsageError

_ITEM_ID_RE = re.compile(r"(?:[Aa][Tt]-)?([0-9]+)", re.ASCII)


def format_item_id(n: int) -> str:
    """Display form of an item id: ``AT-`` plus at least four zero-padded digits."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"item id must be a positive integer, got {n!r}")
    return f"AT-{n:04d}"


def parse_item_id(text: str) -> int:
    """Parse a user-typed item id (``AT-0012`` / ``12`` / ``at-12``) into its integer."""
    match = _ITEM_ID_RE.fullmatch(text) if isinstance(text, str) else None
    value = int(match.group(1)) if match else 0
    if value < 1:
        raise UsageError(
            f"invalid item id {text!r}",
            next_step="use the form AT-0012, at-12 or 12 (ids start at 1)",
        )
    return value
