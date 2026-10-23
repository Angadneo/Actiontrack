"""Duplicate-matching primitives (spec 10.6). Pure: no I/O, no clock, no DB.

Sections (later tasks append their own, keeping this docstring's API list current):

1. Description core (T-M5-06, this section) -- implemented:
   ``description_core(text) -> str``      8.3 comparison form (NFKC, case-fold, quotes/dashes/ellipsis
                                          to ASCII, emoji removed, whitespace collapsed, edge punctuation
                                          stripped).
   ``core_tokens(core) -> tuple[str, ...]``  token splitting of 8.3 (apostrophes deleted, split on every
                                          non letter/digit, empties dropped), then stop-words removed.
   ``core_key(task_text, fallback=None) -> CoreResult``  ``(tokens, key)``; ``key`` = tokens joined by one
                                          space (the stored ``description_core_key``).
   ``has_recurrence_word(tokens) -> bool``, ``core_equal(a, b)``, ``jaccard(a, b)``.
2. Tier-1 merge (T-M5-08), 3. possible-duplicate links (T-M5-09), 4. Tier-1b (T-M5-10),
   5. edited-in-place pairing (T-M5-12): planned, appended below.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import NamedTuple

from actiontrack.domain import rules as _rules

# =========================================================================== 1. Description core


class CoreResult(NamedTuple):
    """Comparison tokens (stop-words removed) and their space-joined key."""

    tokens: tuple[str, ...]
    key: str


_WS_RE = re.compile(r"\s+")


def _is_emoji_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F000 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x2B00 <= cp <= 0x2BFF
        or 0xE0020 <= cp <= 0xE007F
        or cp in (0xFE0E, 0xFE0F, 0x200D, 0x20E3)
        or unicodedata.category(ch) == "So"
    )


def _is_word_char(ch: str) -> bool:
    # Letters, digits, and combining marks (so a decomposed letter is never split from its accent).
    return unicodedata.category(ch)[0] in "LNM"


def description_core(text: str | None) -> str:
    """8.3 comparison form of ``text`` (normally an item's ``task_text``)."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text).casefold()
    s = s.translate(_rules.CORE_CHAR_FOLDS)
    s = "".join(" " if ch.isspace() else ch for ch in s if not _is_emoji_char(ch) or ch.isspace())
    s = _WS_RE.sub(" ", s).strip()
    start, end = 0, len(s)
    while start < end and not _is_word_char(s[start]):
        start += 1
    while end > start and not _is_word_char(s[end - 1]):
        end -= 1
    return s[start:end]


def _split_tokens(core: str) -> list[str]:
    s = core.replace("'", "")
    out: list[str] = []
    cur: list[str] = []
    for ch in s:
        if _is_word_char(ch):
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def core_tokens(core: str | None) -> tuple[str, ...]:
    """Token splitting of 8.3 (the single place the rule is encoded), then stop-word removal.

    Accepts an already-normalised core or raw text (it is re-normalised; idempotent).
    """
    if not core:
        return ()
    normalised = description_core(core)
    return tuple(t for t in _split_tokens(normalised) if t not in _rules.CORE_STOP_WORDS)


def core_key(task_text: str | None, fallback: str | None = None) -> CoreResult:
    """Core of an item. ``fallback`` (the full ``description``) is used only when ``task_text``
    yields no tokens (documented edge of 8.2: ``task_text`` fell back to ``description``)."""
    tokens = core_tokens(task_text)
    if not tokens and fallback:
        tokens = core_tokens(fallback)
    return CoreResult(tokens, " ".join(tokens))


def has_recurrence_word(tokens: Iterable[str]) -> bool:
    """True if any token is a recurrence word (10.6 condition 2; AC-07.7)."""
    return any(t in _rules.RECURRENCE_WORDS for t in tokens)


def core_equal(a: Sequence[str], b: Sequence[str]) -> bool:
    """Identical token *sequences* (10.6 condition 1)."""
    return tuple(a) == tuple(b)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Token-*set* Jaccard; 0.0 for two empty sets."""
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)
