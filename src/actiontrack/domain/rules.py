"""Rule tables for owner/name handling, as plain data (NFR-MAINT-02; spec 10.1, BR-20).

This module is the ONLY place these word lists live. All entries are stored case-folded;
use the helper predicates (they casefold their input). No imports from other actiontrack modules.

Public API
----------
``NONPERSON_WORDS``        BR-20 / 10.1: everyone someone team tbd everybody anyone nobody all we i me
                           (``all`` added to the 10.1 set because BR-20 lists it, gap G-C26).
``HONORIFICS``             dr mr ms mrs (dropped by ``name_key``).
``WEEKDAY_WORDS``/``MONTH_WORDS``  full and abbreviated English names (never names).
``PRONOUN_WORDS``          he she they it you him her them his hers their theirs its your yours
                           (``we``/``i``/``me`` are non-person words, not pronoun-only entries).
``DATE_INTRO_WORDS``       by due before until (introduce due phrases, never names).
``NOT_A_NAME_WORDS``       union of weekday, month, pronoun and date-intro words.
``OWNER_VERBS``            rule-2 verb phrases: to will should needs to is going to can must.
``OWNER_LABEL_WORDS``      trailing-form labels: owner assignee dri.
``MAX_NAME_TOKENS``        a name has 1..4 tokens (10.1).
``is_nonperson_word(text)`` / ``is_not_a_name_word(text)`` / ``is_honorific(text)``
"""

from __future__ import annotations

MAX_NAME_TOKENS = 4

NONPERSON_WORDS: frozenset[str] = frozenset(
    {"everyone", "someone", "team", "tbd", "everybody", "anyone", "nobody", "all", "we", "i", "me"}
)

HONORIFICS: frozenset[str] = frozenset({"dr", "mr", "ms", "mrs"})

WEEKDAY_WORDS: frozenset[str] = frozenset(
    {
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    }
)  # fmt: skip

MONTH_WORDS: frozenset[str] = frozenset(
    {
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    }
)  # fmt: skip

PRONOUN_WORDS: frozenset[str] = frozenset(
    {"he", "she", "they", "it", "you", "him", "her", "them", "his", "hers", "their", "theirs",
     "its", "your", "yours", "us", "our", "ours", "my", "mine"}
)  # fmt: skip

DATE_INTRO_WORDS: frozenset[str] = frozenset({"by", "due", "before", "until"})

NOT_A_NAME_WORDS: frozenset[str] = WEEKDAY_WORDS | MONTH_WORDS | PRONOUN_WORDS | DATE_INTRO_WORDS

#: Verb phrases that follow the owner in rule 2 (``Name to|will|... <verb>``); longest first.
OWNER_VERBS: tuple[str, ...] = ("is going to", "needs to", "should", "will", "can", "must", "to")

OWNER_LABEL_WORDS: frozenset[str] = frozenset({"owner", "assignee", "dri"})


def is_nonperson_word(text: str | None) -> bool:
    """True for a BR-20 non-person owner word (``Everyone``, ``TBD``, ``All`` ...), any case."""
    return bool(text) and text.strip().strip(".:,;").casefold() in NONPERSON_WORDS  # type: ignore[union-attr]


def is_not_a_name_word(text: str | None) -> bool:
    """True for weekday/month/pronoun/``by``-``due`` words, which are never names (10.1)."""
    return bool(text) and text.strip().strip(".:,;").casefold() in NOT_A_NAME_WORDS  # type: ignore[union-attr]


def is_honorific(text: str | None) -> bool:
    return bool(text) and text.strip().rstrip(".").casefold() in HONORIFICS  # type: ignore[union-attr]


# --------------------------------------------------------------------------- description core (10.6)
# Stop-words removed from the *comparison tokens only* (spec 10.6).
CORE_STOP_WORDS: frozenset[str] = frozenset({"the", "a", "an", "to", "and", "of", "for", "please"})

# A core containing one of these is never auto-merged (Tier-1 condition 2; AC-07.7).
RECURRENCE_WORDS: frozenset[str] = frozenset(
    {"weekly", "daily", "monthly", "every", "each", "recurring", "standing", "regular"}
)

# Tier-1 needs a core of at least this many tokens (10.6 condition 2).
MIN_CORE_TOKENS = 3

# Default token-set Jaccard threshold for the `fuzzy` possible-duplicate reason (10.6; config overrides).
FUZZY_JACCARD_THRESHOLD = 0.85

# Comparison-form character folds (8.3): curly quotes / dashes / ellipsis to ASCII.
CORE_CHAR_FOLDS: dict[int, str] = {
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x2032: "'", 0x02BC: "'",
    0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"', 0x2033: '"',
    0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-", 0x2014: "-", 0x2015: "-", 0x2212: "-",
    0x2026: "...",
}  # fmt: skip
