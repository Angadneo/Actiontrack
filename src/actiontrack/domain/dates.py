"""Date primitives, vocabulary tables and the absolute date-literal parser (spec 9, M3).

Pure module: no I/O and no clock (BR-05: resolution is a pure function of
phrase + meeting date + ``date_order``). Nothing here may read the system time.

Sections (later M3 tasks append to the matching section, in this order)
-----------------------------------------------------------------------
1. Vocabulary tables (T-M3-01)      plain module-level data, NFR-MAINT-02
2. Enumerations (T-M3-01)           ``Confidence``, ``Reason``, ``Rule``
3. Calendar primitives (T-M3-01)    ISO-week (Mon-Sun) / month arithmetic
4. Literal parser (T-M3-02)         ``parse_literal``, ``match_literal_at``
5. [planned] Phrase recogniser      T-M3-03/04 (vague/range/relative shapes)
6. [planned] Resolver               T-M3-05..08 (relative rules, year-less rule)
7. [planned] Phrase locator         T-M3-09.. (introducers, multiple dates)

Public API (sections 1-4)
-------------------------
Tables: ``WEEKDAYS`` (name -> 0..6, Mon=0), ``MONTHS`` (name -> 1..12),
``ORDINAL_SUFFIXES``, ``TIME_OF_DAY_WORDS``, ``TIME_OF_DAY_PATTERN``,
``VAGUE_WORDS``, ``RANGE_WORDS``, ``DUE_INTRODUCERS``, ``EVENT_INTRODUCERS``,
``NOT_DUE_INTRODUCERS``, ``NUMBER_WORDS`` (one..twelve, a, an), ``SINGULAR_ONLY_NUMBER_WORDS``,
``VAGUE_QUANTITY_PHRASES``, ``PAST_ACCEPT_DAYS`` (= 60, year-less rule G-06),
``WEEKDAY_NAME_ROWS`` / ``MONTH_NAME_ROWS`` (the raw (name, value) rows, for duplicate checks).
Enums: ``Confidence`` (explicit/relative/inferred/unresolved/none), ``Reason``, ``Rule``
(StrEnums whose values are ``codes.DUE_REASONS`` / ``codes.DUE_RULES``).
Primitives: ``monday_of(d)``, ``iso_week_shift(d, n)``, ``weekday_in_week(d, weekday, week_offset)``,
``first_weekday_strictly_after(d, weekday)``, ``last_day_of_month(y, m)``, ``add_months(y, m, n) -> (y, m)``,
``is_valid_ymd(y, m, d)``, ``days_between(a, b)`` (= ``b - a`` in days).
Literals (all frozen dataclasses; ``raw`` is the matched source text):
``Literal(date, rule, weekday_claim, raw)``       full date; ``rule`` in iso_date/text_date/numeric_date
``YearlessDate(month, day, weekday_claim, raw)``  no year; resolved later (year-less rule);
                                                  ``Feb 29`` is valid here
``Invalid(reason, raw)``                          ``reason`` in {``invalid_date``, ``ambiguous_numeric``}
``LiteralMatch(start, end, result)``              span in the searched text + one of the three above
``parse_literal(text, date_order=None) -> Literal | YearlessDate | Invalid | None``
    whole-string parse (surrounding whitespace ignored); ``None`` = not a date literal.
``match_literal_at(text, pos, date_order=None) -> LiteralMatch | None``
    literal starting exactly at ``pos`` (word boundaries enforced); ``None`` = no literal there.
``weekday_claim`` is the 0..6 weekday written before the date (``Wed 16 Sep``) or ``None``;
the claim is only captured here, verified later (T-M3-06).
"""

from __future__ import annotations

import datetime as _dt
import enum
import re
from dataclasses import dataclass

from actiontrack import codes as _codes

__all__ = [
    "Confidence", "Reason", "Rule", "Literal", "YearlessDate", "Invalid", "LiteralMatch",
    "WEEKDAYS", "MONTHS", "ORDINAL_SUFFIXES", "TIME_OF_DAY_WORDS", "TIME_OF_DAY_PATTERN",
    "VAGUE_WORDS", "RANGE_WORDS", "DUE_INTRODUCERS", "EVENT_INTRODUCERS", "NOT_DUE_INTRODUCERS",
    "NUMBER_WORDS", "SINGULAR_ONLY_NUMBER_WORDS", "VAGUE_QUANTITY_PHRASES", "PAST_ACCEPT_DAYS",
    "WEEKDAY_NAME_ROWS", "MONTH_NAME_ROWS",
    "monday_of", "iso_week_shift", "weekday_in_week", "first_weekday_strictly_after",
    "last_day_of_month", "add_months", "is_valid_ymd", "days_between",
    "parse_literal", "match_literal_at",
]

# =========================================================================== 1. tables
WEEKDAY_NAME_ROWS: tuple[tuple[str, int], ...] = (
    ("monday", 0), ("tuesday", 1), ("wednesday", 2), ("thursday", 3),
    ("friday", 4), ("saturday", 5), ("sunday", 6),
    ("mon", 0), ("tue", 1), ("wed", 2), ("thu", 3), ("fri", 4), ("sat", 5), ("sun", 6),
    ("tues", 1), ("thur", 3), ("thurs", 3),
)

MONTH_NAME_ROWS: tuple[tuple[str, int], ...] = (
    ("january", 1), ("february", 2), ("march", 3), ("april", 4), ("may", 5), ("june", 6),
    ("july", 7), ("august", 8), ("september", 9), ("october", 10), ("november", 11),
    ("december", 12),
    ("jan", 1), ("feb", 2), ("mar", 3), ("apr", 4), ("jun", 6), ("jul", 7), ("aug", 8),
    ("sep", 9), ("oct", 10), ("nov", 11), ("dec", 12),
    ("sept", 9),
)


def _table(rows: tuple[tuple[str, int], ...]) -> dict[str, int]:
    out = dict(rows)
    assert len(out) == len(rows), "duplicate key in vocabulary table"
    return out


WEEKDAYS: dict[str, int] = _table(WEEKDAY_NAME_ROWS)  # lowercase name -> 0 (Mon) .. 6 (Sun)
MONTHS: dict[str, int] = _table(MONTH_NAME_ROWS)  # lowercase name -> 1..12

ORDINAL_SUFFIXES: tuple[str, ...] = ("st", "nd", "rd", "th")

TIME_OF_DAY_WORDS: tuple[str, ...] = ("eod", "cob", "noon", "midnight")
# e.g. 5pm, 5 pm, 10:30am (case-insensitive); retained in due_raw, ignored for the date.
TIME_OF_DAY_PATTERN: str = r"\b\d{1,2}(?::\d\d)?\s?(?:am|pm)\b"

VAGUE_WORDS: tuple[str, ...] = ("soon", "later", "asap", "tbd", "ongoing")
RANGE_WORDS: tuple[str, ...] = ("this week", "next week", "this month", "next month")
RANGE_PHRASE_WITH_DATE: str = "week of"  # ``week of <date>`` (date part found by the locator)

# Introducers (spec 9.2), lowercase. ``deadline:`` / ``(due:`` / ``[due:`` keep their punctuation.
DUE_INTRODUCERS: tuple[str, ...] = (
    "by", "due", "due on", "before", "until", "till", "on", "no later than",
    "deadline:", "(due:", "[due:", "\U0001F4C5",
)
EVENT_INTRODUCERS: tuple[str, ...] = ("on", "for")  # year-less date under these => event_date
NOT_DUE_INTRODUCERS: tuple[str, ...] = ("from", "since", "starting")  # start-of-effect, W-DATE-NOT-DUE

NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "a": 1, "an": 1,
}
SINGULAR_ONLY_NUMBER_WORDS: frozenset[str] = frozenset({"a", "an"})  # only before a singular unit (H-04)

# Quantity phrases that are ``vague`` (H-04); number words above twelve are NOT recognised.
VAGUE_QUANTITY_PHRASES: tuple[str, ...] = ("a couple of", "a few", "a fortnight")

PAST_ACCEPT_DAYS: int = 60  # year-less rule (G-06): earlier than M by <= 60 days => accept as past


# ===================================================================== 2. enumerations
class Confidence(enum.StrEnum):
    EXPLICIT = "explicit"
    RELATIVE = "relative"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"
    NONE = "none"


_REASON_ORDER = (
    "no_meeting_date", "vague", "range", "same_weekday", "this_weekday_past",
    "weekend_meeting", "ambiguous_numeric", "invalid_date", "weekday_date_mismatch",
    "multiple_dates",
)
_RULE_ORDER = (
    "iso_date", "text_date", "numeric_date", "yearless_date", "weekday_date", "event_date",
    "day_of_month", "today", "eod_alone", "tomorrow", "yesterday", "weekday_next",
    "weekday_this", "weekday_next_week", "monday_next_week", "end_of_week",
    "end_of_next_week", "end_of_month", "end_of_next_month", "in_n_days", "in_n_weeks",
)
assert set(_REASON_ORDER) == _codes.DUE_REASONS and len(_REASON_ORDER) == len(_codes.DUE_REASONS)
assert set(_RULE_ORDER) == _codes.DUE_RULES and len(_RULE_ORDER) == len(_codes.DUE_RULES)

# Thin enums (member NAME = upper-cased id, value = the id string, e.g. Rule.ISO_DATE == "iso_date").
Reason = enum.StrEnum("Reason", [(v.upper(), v) for v in _REASON_ORDER], module=__name__)
Rule = enum.StrEnum("Rule", [(v.upper(), v) for v in _RULE_ORDER], module=__name__)


# ============================================================== 3. calendar primitives
def monday_of(d: _dt.date) -> _dt.date:
    """Monday of the ISO week (Mon-Sun) containing ``d``."""
    return d - _dt.timedelta(days=d.weekday())


def iso_week_shift(d: _dt.date, n: int) -> _dt.date:
    """Same weekday ``n`` ISO weeks later (negative: earlier)."""
    return d + _dt.timedelta(days=7 * n)


def weekday_in_week(d: _dt.date, weekday: int, week_offset: int = 0) -> _dt.date:
    """Date of ``weekday`` (0=Mon..6=Sun) in the ISO week ``week_offset`` weeks after ``d``'s week."""
    if not 0 <= weekday <= 6:
        raise ValueError(f"weekday out of range: {weekday}")
    return monday_of(d) + _dt.timedelta(days=7 * week_offset + weekday)


def first_weekday_strictly_after(d: _dt.date, weekday: int) -> _dt.date:
    """First date with ``weekday`` strictly after ``d`` (same weekday => +7)."""
    if not 0 <= weekday <= 6:
        raise ValueError(f"weekday out of range: {weekday}")
    delta = (weekday - d.weekday()) % 7
    return d + _dt.timedelta(days=delta or 7)


def last_day_of_month(y: int, m: int) -> int:
    if not 1 <= m <= 12:
        raise ValueError(f"month out of range: {m}")
    if m == 12:
        return 31
    return (_dt.date(y, m + 1, 1) - _dt.timedelta(days=1)).day


def add_months(y: int, m: int, n: int) -> tuple[int, int]:
    """(year, month) ``n`` months after (y, m)."""
    idx = y * 12 + (m - 1) + n
    return idx // 12, idx % 12 + 1


def is_valid_ymd(y: int, m: int, d: int) -> bool:
    if not (_dt.MINYEAR <= y <= _dt.MAXYEAR and 1 <= m <= 12):
        return False
    return 1 <= d <= last_day_of_month(y, m)


def days_between(a: _dt.date, b: _dt.date) -> int:
    """Calendar days from ``a`` to ``b`` (``b - a``)."""
    return (b - a).days


# ================================================================== 4. literal parser
@dataclass(frozen=True)
class Literal:
    date: _dt.date
    rule: str  # "iso_date" | "text_date" | "numeric_date"
    weekday_claim: int | None
    raw: str


@dataclass(frozen=True)
class YearlessDate:
    month: int
    day: int
    weekday_claim: int | None
    raw: str


@dataclass(frozen=True)
class Invalid:
    reason: str  # "invalid_date" | "ambiguous_numeric" (codes.DUE_REASONS)
    raw: str


@dataclass(frozen=True)
class LiteralMatch:
    start: int
    end: int
    result: Literal | YearlessDate | Invalid


def _alt(names: dict[str, int]) -> str:
    return "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))


_WD = rf"(?P<wd>{_alt(WEEKDAYS)})"
_MON = rf"(?P<mon>{_alt(MONTHS)})"
_SUF = "|".join(ORDINAL_SUFFIXES)
_FLAGS = re.IGNORECASE

_ISO_RE = re.compile(
    r"(?P<y>\d{4})(?P<sep>[-/])(?P<m>\d{1,2})(?P=sep)(?P<d>\d{1,2})(?!\d)"
    r"(?:[T ]\d{1,2}:\d\d(?::\d\d(?:\.\d+)?)?(?:\s?(?:Z|[+-]\d\d(?::?\d\d)?))?)?",
    _FLAGS,
)
_WD_PREFIX = rf"(?:{_WD}\.?,?\s+)?"
_YEAR = r"(?:,?\s+(?P<y>\d{4})(?![\d]))?"
_DAY = rf"(?P<d>\d{{1,2}})(?!\d)(?:{_SUF})?"
_DMY_RE = re.compile(rf"{_WD_PREFIX}{_DAY}(?:\s+of)?\s+{_MON}(?![A-Za-z]){_YEAR}", _FLAGS)  # trailing '.' stays outside the span
_MDY_RE = re.compile(rf"{_WD_PREFIX}{_MON}\.?\s+{_DAY}(?![A-Za-z0-9]){_YEAR}", _FLAGS)
_NUM_RE = re.compile(r"(?P<a>\d{1,2})/(?P<b>\d{1,2})(?:/(?P<y>\d{4}(?!\d)|\d{2}(?!\d)))?")


def _boundary_ok(text: str, start: int, end: int) -> bool:
    if start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        return False
    if end < len(text):
        c = text[end]
        if c.isalnum() or c == "_":
            return False
        if c in "/-" and end + 1 < len(text) and text[end + 1].isdigit():
            return False
    return True


def _textual(m: re.Match[str], raw: str) -> Literal | YearlessDate | Invalid:
    wd = m.group("wd")
    claim = WEEKDAYS[wd.lower()] if wd else None
    month = MONTHS[m.group("mon").lower()]
    day = int(m.group("d"))
    ys = m.group("y")
    if ys is None:
        # Feb 29 is valid here (resolved / rejected by the year-less rule later)
        if 1 <= day <= last_day_of_month(2024, month):
            return YearlessDate(month, day, claim, raw)
        return Invalid("invalid_date", raw)
    year = int(ys)
    if is_valid_ymd(year, month, day):
        return Literal(_dt.date(year, month, day), "text_date", claim, raw)
    return Invalid("invalid_date", raw)


def _iso(m: re.Match[str], raw: str) -> Literal | Invalid:
    y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
    if is_valid_ymd(y, mo, d):
        return Literal(_dt.date(y, mo, d), "iso_date", None, raw)
    return Invalid("invalid_date", raw)


def _numeric(m: re.Match[str], raw: str, date_order: str | None) -> Literal | YearlessDate | Invalid:
    a, b = int(m.group("a")), int(m.group("b"))
    if a > 12 and b > 12 or a == 0 or b == 0:
        return Invalid("invalid_date", raw)
    if a > 12:
        day, month = a, b
    elif b > 12:
        month, day = a, b
    elif a == b:
        day = month = a
    elif date_order == "dmy":
        day, month = a, b
    elif date_order == "mdy":
        month, day = a, b
    else:
        return Invalid("ambiguous_numeric", raw)
    ys = m.group("y")
    if ys is None:
        if day <= last_day_of_month(2024, month):
            return YearlessDate(month, day, None, raw)
        return Invalid("invalid_date", raw)
    year = int(ys) if len(ys) == 4 else 2000 + int(ys)
    if is_valid_ymd(year, month, day):
        return Literal(_dt.date(year, month, day), "numeric_date", None, raw)
    return Invalid("invalid_date", raw)


def match_literal_at(text: str, pos: int, date_order: str | None = None) -> LiteralMatch | None:
    """Date literal starting exactly at ``pos`` or ``None``. Pure; word boundaries enforced."""
    if pos < 0 or pos >= len(text):
        return None
    candidates: list[LiteralMatch] = []
    for rx, kind in ((_ISO_RE, "iso"), (_DMY_RE, "txt"), (_MDY_RE, "txt"), (_NUM_RE, "num")):
        m = rx.match(text, pos)
        if m is None or not _boundary_ok(text, m.start(), m.end()):
            continue
        raw = m.group(0)
        if kind == "iso":
            res: Literal | YearlessDate | Invalid = _iso(m, raw)
        elif kind == "txt":
            res = _textual(m, raw)
        else:
            res = _numeric(m, raw, date_order)
        candidates.append(LiteralMatch(m.start(), m.end(), res))
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.end - c.start)


def parse_literal(text: str, date_order: str | None = None) -> Literal | YearlessDate | Invalid | None:
    """Parse a whole string as one date literal (surrounding whitespace ignored).

    ``None`` when the string is not (entirely) a date literal.
    """
    stripped = text.strip()
    if not stripped:
        return None
    m = match_literal_at(stripped, 0, date_order)
    if m is None or m.end != len(stripped):
        return None
    return m.result
