"""Date primitives, vocabulary tables and the absolute date-literal parser (spec 9, M3).

Pure module: no I/O and no clock (BR-05: resolution is a pure function of
phrase + meeting date + ``date_order``). Nothing here may read the system time.

Sections (later M3 tasks append to the matching section, in this order)
-----------------------------------------------------------------------
1. Vocabulary tables (T-M3-01)      plain module-level data, NFR-MAINT-02
2. Enumerations (T-M3-01)           ``Confidence``, ``Reason``, ``Rule``
3. Calendar primitives (T-M3-01)    ISO-week (Mon-Sun) / month arithmetic
4. Literal parser (T-M3-02)         ``parse_literal``, ``match_literal_at``
5. Phrase recogniser (T-M3-03)      ``classify_phrase`` -> ``Phrase`` AST (no date arithmetic)
6. Resolver (T-M3-06/07/08)         ``resolve_phrase`` -> ``Resolution`` (pure; needs meeting date M)
7. Phrase locator (T-M3-04/05)      ``locate`` -> ``Located`` spans over the cleaned ``description``
8. Item due (T-M3-09)               ``resolve_due`` -> ``DueResult``; ``format_due`` (display, spec 9.5)

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
the claim is only captured here, verified by the resolver (section 6).

Public API (sections 5-7)
-------------------------
``Phrase(kind, raw, time_words, weekday, n, unit, day, literal_text, yearless, reason)`` frozen dataclass;
    ``kind`` in ``PHRASE_KINDS`` (absolute, yearless, weekday_date, day_of_month, today, eod_alone,
    tomorrow, yesterday, weekday, this_weekday, next_weekday, monday_next_week, end_of_week,
    end_of_next_week, end_of_month, end_of_next_month, in_n, range, vague, invalid).
``classify_phrase(text, date_order=None) -> Phrase | None``   ``None`` = not a date phrase.
``Resolution(date, confidence, rule, reason, warnings)``      ``warnings`` = tuple of ``W-*`` codes;
    unresolved => ``date``/``rule`` are ``None`` and ``reason`` is a ``Reason`` value.
``resolve_phrase(phrase, meeting_date, date_order=None, *, event=False) -> Resolution``
``resolve_text(text, meeting_date, date_order=None, *, event=False) -> Resolution | None``
``Located(raw, start, end, introducer, introducer_span, time_words, role, phrase, separator_span)``
    spans are offsets into the cleaned ``description`` (``TextSpan``); ``role`` in due/event/not_due.
``locate(description, date_order=None) -> list[Located]``     sorted by ``start``.
``distinct_due_phrases(located)`` (due/event roles, one per normalised raw, in order),
``not_due_phrases(located)``, ``has_multiple_dates(located)``.

Public API (section 8)
----------------------
``resolve_due(description, meeting_date, date_order=None) -> DueResult(due_raw, due_date, due_confidence,
due_rule, due_reason, due_raw_other, raws, spans, warnings)``  (see ``DueResult``);
``format_due(result) -> str``  ``2026-09-04`` / ``?(raw excerpt)`` / ``-``.
"""

from __future__ import annotations

import datetime as _dt
import enum
import re
from dataclasses import dataclass

from actiontrack import codes as _codes
from actiontrack.domain.models import TextSpan

__all__ = [
    "Confidence", "Reason", "Rule", "Literal", "YearlessDate", "Invalid", "LiteralMatch",
    "WEEKDAYS", "MONTHS", "ORDINAL_SUFFIXES", "TIME_OF_DAY_WORDS", "TIME_OF_DAY_PATTERN",
    "VAGUE_WORDS", "RANGE_WORDS", "DUE_INTRODUCERS", "EVENT_INTRODUCERS", "NOT_DUE_INTRODUCERS",
    "NUMBER_WORDS", "SINGULAR_ONLY_NUMBER_WORDS", "VAGUE_QUANTITY_PHRASES", "PAST_ACCEPT_DAYS",
    "WEEKDAY_NAME_ROWS", "MONTH_NAME_ROWS",
    "monday_of", "iso_week_shift", "weekday_in_week", "first_weekday_strictly_after",
    "last_day_of_month", "add_months", "is_valid_ymd", "days_between",
    "parse_literal", "match_literal_at",
    "PHRASE_KINDS", "Phrase", "classify_phrase", "Resolution", "resolve_phrase", "resolve_text",
    "Located", "locate", "distinct_due_phrases", "not_due_phrases", "has_multiple_dates",
    "DueResult", "resolve_due", "format_due", "MeetingDate",
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


# ============================================================ 5. phrase recogniser
PHRASE_KINDS: tuple[str, ...] = (
    "absolute", "yearless", "weekday_date", "day_of_month", "today", "eod_alone", "tomorrow",
    "yesterday", "weekday", "this_weekday", "next_weekday", "monday_next_week", "end_of_week",
    "end_of_next_week", "end_of_month", "end_of_next_month", "in_n", "range", "vague", "invalid",
)


@dataclass(frozen=True)
class Phrase:
    """Parsed shape of a date phrase (no date arithmetic).

    ``time_words`` are split-off ``EOD``/``5pm``-style words (kept for ``due_raw``). Literal-based
    kinds (``absolute``, ``yearless``, ``weekday_date``, ``invalid``; ``range`` for ``week of <date>``)
    carry ``literal_text`` which the resolver re-parses with the configured ``date_order``.
    ``kind == "invalid"`` marks an impossible / ambiguous literal (``reason`` is the T-M3-02 reason).
    """

    kind: str
    raw: str
    time_words: tuple[str, ...] = ()
    weekday: int | None = None
    n: int | None = None
    unit: str | None = None  # "days" | "weeks" (kind in_n)
    day: int | None = None  # day of month (kind day_of_month)
    literal_text: str | None = None
    yearless: bool = False  # literal-based kinds: the literal carries no year
    reason: str | None = None


_TIME_TOKEN = r"(?:eod|cob|noon|midnight|\d{1,2}(?::\d\d)?\s?(?:am|pm))"
_LEAD_TIME_RE = re.compile(rf"(?P<t>{_TIME_TOKEN})(?![A-Za-z0-9])[\s,]*", _FLAGS)
_TRAIL_TIME_RE = re.compile(rf"[\s,]*(?:at\s+)?(?<![A-Za-z0-9])(?P<t>{_TIME_TOKEN})$", _FLAGS)
_WD_ONLY = rf"(?P<wd>{_alt(WEEKDAYS)})"
_MON_ONLY = rf"(?P<mon>{_alt(MONTHS)})"
_NUMW = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))

_RX_WEEKDAY = re.compile(rf"{_WD_ONLY}", _FLAGS)
_RX_THIS_WD = re.compile(rf"this\s+{_WD_ONLY}", _FLAGS)
_RX_NEXT_WD = re.compile(rf"next\s+{_WD_ONLY}", _FLAGS)
_RX_WD_NEXT_WEEK = re.compile(rf"{_WD_ONLY}\s+next\s+week", _FLAGS)
_RX_DAY_OF_MONTH = re.compile(r"(?P<d>\d{1,2})(?:st|nd|rd|th)", _FLAGS)
_RX_IN_N = re.compile(
    rf"(?:in|within)\s+(?P<n>\d{{1,3}}|{_NUMW})\s+(?P<u>days?|weeks?)", _FLAGS)
_RX_IN_VAGUE = re.compile(
    r"(?:in|within)\s+(?:(?:a\s+couple\s+of|a\s+few)\s+(?:days?|weeks?|months?)|a\s+fortnight)", _FLAGS)
_RX_WEEK_OF = re.compile(r"week\s+of\s+(?P<lit>.+)", _FLAGS)
_RX_QUARTER = re.compile(r"(?:end\s+of\s+)?q[1-4]", _FLAGS)
_RX_MID = re.compile(rf"mid[-\s](?:month|{_alt(MONTHS)})", _FLAGS)
_RX_MONTH_ONLY = re.compile(rf"(?:for\s+)?{_MON_ONLY}", _FLAGS)
_RX_LEADING_THE = re.compile(r"^the\s+", _FLAGS)
_RX_AFTER_LAUNCH = re.compile(r"after\s+the\s+launch", _FLAGS)
_RX_END_OF = re.compile(
    r"(?:(?P<eow>eow)|(?P<eom>eom)|end\s+of\s+(?:(?P<nxt>next)\s+|(?:the\s+)?)(?P<per>week|month))", _FLAGS)
_FIXED_KINDS: dict[str, str] = {
    "today": "today", "tonight": "today", "this morning": "today", "this afternoon": "today",
    "tomorrow": "tomorrow", "yesterday": "yesterday",
    "this week": "range", "next week": "range", "this month": "range", "next month": "range",
}


def _split_time_words(text: str) -> tuple[str, tuple[str, ...]]:
    t = text.strip()
    lead: list[str] = []
    trail: list[str] = []
    while True:
        m = _LEAD_TIME_RE.match(t)
        if not m:
            break
        lead.append(m.group("t"))
        t = t[m.end():]
    while t:
        m = _TRAIL_TIME_RE.search(t)
        if not m:
            break
        trail.insert(0, m.group("t"))
        t = t[: m.start()]
    return t.strip(), tuple(lead + trail)


def _literal_phrase(core: str, raw: str, tw: tuple[str, ...], date_order: str | None) -> Phrase | None:
    res = parse_literal(core, date_order)
    if res is None:
        return None
    if isinstance(res, Invalid):
        return Phrase("invalid", raw, tw, literal_text=core, reason=res.reason)
    claim = res.weekday_claim
    yearless = isinstance(res, YearlessDate)
    if claim is not None:
        return Phrase("weekday_date", raw, tw, weekday=claim, literal_text=core, yearless=yearless)
    kind = "yearless" if yearless else "absolute"
    return Phrase(kind, raw, tw, literal_text=core, yearless=yearless)


def classify_phrase(text: str, date_order: str | None = None) -> Phrase | None:
    """Classify a text already believed to be a date phrase; ``None`` if it is not one.

    Case-insensitive. Leading/trailing time words (``EOD``, ``COB``, ``noon``, ``5pm``) are split off
    into ``time_words``. Does no date arithmetic; ``date_order`` only decides whether a numeric
    literal such as ``03/04/2026`` is ``absolute`` or ``invalid`` (ambiguous).
    """
    raw = text.strip()
    if not raw:
        return None
    core, tw = _split_time_words(raw)
    if not core:
        if any(w.lower() in ("eod", "cob") for w in tw):
            return Phrase("eod_alone", raw, tw)
        return None
    core = _RX_LEADING_THE.sub("", core, count=1)
    if not core:
        return None
    flat = " ".join(core.lower().split())

    lit = _literal_phrase(core, raw, tw, date_order)
    if lit is not None:
        return lit
    if flat in _FIXED_KINDS:
        return Phrase(_FIXED_KINDS[flat], raw, tw)
    m = _RX_DAY_OF_MONTH.fullmatch(flat)
    if m:
        d = int(m.group("d"))
        return Phrase("day_of_month", raw, tw, day=d) if 1 <= d <= 31 else None
    m = _RX_WEEKDAY.fullmatch(flat)
    if m:
        return Phrase("weekday", raw, tw, weekday=WEEKDAYS[m.group("wd")])
    m = _RX_THIS_WD.fullmatch(flat)
    if m:
        return Phrase("this_weekday", raw, tw, weekday=WEEKDAYS[m.group("wd")])
    m = _RX_NEXT_WD.fullmatch(flat)
    if m:
        return Phrase("next_weekday", raw, tw, weekday=WEEKDAYS[m.group("wd")])
    m = _RX_WD_NEXT_WEEK.fullmatch(flat)
    if m:
        return Phrase("monday_next_week", raw, tw, weekday=WEEKDAYS[m.group("wd")])
    m = _RX_END_OF.fullmatch(flat)
    if m:
        if m.group("eow") or m.group("per") == "week":
            return Phrase("end_of_next_week" if m.group("nxt") else "end_of_week", raw, tw)
        return Phrase("end_of_next_month" if m.group("nxt") else "end_of_month", raw, tw)
    m = _RX_IN_N.fullmatch(flat)
    if m:
        word = m.group("n")
        n = int(word) if word.isdigit() else NUMBER_WORDS[word]
        unit = "weeks" if m.group("u").startswith("week") else "days"
        plural = m.group("u").endswith("s")
        if n < 1 or (word in SINGULAR_ONLY_NUMBER_WORDS and plural):
            return None
        return Phrase("in_n", raw, tw, n=n, unit=unit)
    if _RX_IN_VAGUE.fullmatch(flat):
        return Phrase("vague", raw, tw)
    m = _RX_WEEK_OF.fullmatch(" ".join(core.split()))
    if m:
        res = parse_literal(m.group("lit"), date_order)
        if isinstance(res, (Literal, YearlessDate)):
            return Phrase("range", raw, tw, literal_text=m.group("lit"), yearless=isinstance(res, YearlessDate))
        return None
    if (flat in VAGUE_WORDS or _RX_QUARTER.fullmatch(flat) or _RX_MID.fullmatch(flat)
            or _RX_MONTH_ONLY.fullmatch(flat) or _RX_AFTER_LAUNCH.fullmatch(flat)):
        return Phrase("vague", raw, tw)
    return None


# ================================================================== 6. resolver
@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving one phrase against a meeting date (spec 9.3-9.5).

    Resolved: ``date`` and ``rule`` set, ``reason`` ``None``. Unresolved: ``confidence`` is
    ``Confidence.UNRESOLVED``, ``date``/``rule`` are ``None`` (AC-06.6) and ``reason`` is set.
    """

    date: _dt.date | None
    confidence: Confidence
    rule: Rule | None
    reason: Reason | None
    warnings: tuple[str, ...] = ()


_W_BEFORE = _codes.W_DUE_BEFORE_MEETING


def _unresolved(reason: str, warnings: tuple[str, ...] = ()) -> Resolution:
    return Resolution(None, Confidence.UNRESOLVED, None, Reason(reason), warnings)


def _resolved(d: _dt.date, confidence: Confidence, rule: str, m: _dt.date | None = None,
              *, warn_if_before: bool = False) -> Resolution:
    warnings: tuple[str, ...] = ()
    if warn_if_before and m is not None and d < m:
        warnings = (_W_BEFORE,)
    return Resolution(d, confidence, Rule(rule), None, warnings)


def _resolve_literal(res: Literal | YearlessDate | Invalid, m: _dt.date | None, event: bool) -> Resolution:
    if isinstance(res, Invalid):
        return _unresolved(res.reason)
    if isinstance(res, Literal):
        # year-bearing: always explicit with its own rule, whatever the introducer (G-07)
        if res.weekday_claim is not None and res.date.weekday() != res.weekday_claim:
            return _unresolved("weekday_date_mismatch")
        return _resolved(res.date, Confidence.EXPLICIT, res.rule, m, warn_if_before=True)
    if m is None:
        return _unresolved("no_meeting_date")
    claim = res.weekday_claim
    if claim is not None:
        rule = "event_date" if event else "weekday_date"
        for year in (m.year, m.year + 1, m.year - 1):
            if is_valid_ymd(year, res.month, res.day):
                d = _dt.date(year, res.month, res.day)
                if d.weekday() == claim:
                    return _resolved(d, Confidence.INFERRED, rule, m, warn_if_before=True)
        return _unresolved("weekday_date_mismatch")
    rule = "event_date" if event else "yearless_date"
    if not is_valid_ymd(m.year, res.month, res.day):
        return _unresolved("invalid_date")
    d = _dt.date(m.year, res.month, res.day)
    if d >= m:
        return _resolved(d, Confidence.INFERRED, rule)
    if days_between(d, m) <= PAST_ACCEPT_DAYS:
        return _resolved(d, Confidence.INFERRED, rule, m, warn_if_before=True)
    if not is_valid_ymd(m.year + 1, res.month, res.day):
        return _unresolved("invalid_date")
    return _resolved(_dt.date(m.year + 1, res.month, res.day), Confidence.INFERRED, rule)


def _resolve_relative(p: Phrase, m: _dt.date) -> Resolution:
    k = p.kind
    rel = Confidence.RELATIVE
    try:
        if k == "today":
            return _resolved(m, rel, "today")
        if k == "eod_alone":
            return _resolved(m, rel, "eod_alone")
        if k == "tomorrow":
            return _resolved(m + _dt.timedelta(days=1), rel, "tomorrow")
        if k == "yesterday":
            return _resolved(m - _dt.timedelta(days=1), rel, "yesterday", m, warn_if_before=True)
        if k == "weekday":
            assert p.weekday is not None
            if p.weekday == m.weekday():
                return _unresolved("same_weekday")
            return _resolved(first_weekday_strictly_after(m, p.weekday), rel, "weekday_next")
        if k == "this_weekday":
            assert p.weekday is not None
            d = weekday_in_week(m, p.weekday, 0)
            if d < m:
                return _unresolved("this_weekday_past")
            return _resolved(d, rel, "weekday_this")
        if k == "next_weekday":
            assert p.weekday is not None
            return _resolved(weekday_in_week(m, p.weekday, 1), rel, "weekday_next_week")
        if k == "monday_next_week":
            assert p.weekday is not None
            return _resolved(weekday_in_week(m, p.weekday, 1), rel, "monday_next_week")
        if k == "end_of_week":
            if m.weekday() >= 5:
                return _unresolved("weekend_meeting")
            return _resolved(weekday_in_week(m, 4, 0), rel, "end_of_week")
        if k == "end_of_next_week":
            return _resolved(weekday_in_week(m, 4, 1), rel, "end_of_next_week")
        if k == "end_of_month":
            return _resolved(_dt.date(m.year, m.month, last_day_of_month(m.year, m.month)), rel, "end_of_month")
        if k == "end_of_next_month":
            y, mo = add_months(m.year, m.month, 1)
            return _resolved(_dt.date(y, mo, last_day_of_month(y, mo)), rel, "end_of_next_month")
        if k == "in_n":
            assert p.n is not None
            if p.unit == "weeks":
                return _resolved(m + _dt.timedelta(days=7 * p.n), rel, "in_n_weeks")
            return _resolved(m + _dt.timedelta(days=p.n), rel, "in_n_days")
        if k == "day_of_month":
            assert p.day is not None
            if m.day <= p.day:
                y, mo = m.year, m.month
            else:
                y, mo = add_months(m.year, m.month, 1)
            if p.day > last_day_of_month(y, mo):
                return _unresolved("invalid_date")  # never skip ahead to a later month
            return _resolved(_dt.date(y, mo, p.day), Confidence.INFERRED, "day_of_month")
    except (OverflowError, ValueError):  # date arithmetic past year 9999 / before year 1
        return _unresolved("invalid_date")
    raise ValueError(f"unknown phrase kind: {k!r}")


def resolve_phrase(phrase: Phrase, meeting_date: _dt.date | None, date_order: str | None = None,
                   *, event: bool = False) -> Resolution:
    """Resolve a classified phrase against meeting date ``meeting_date`` (a pure function; no clock).

    ``event=True`` (set by the caller for a ``role == "event"`` phrase) only replaces the rule id of a
    year-less date with ``event_date``; a year-bearing date is always ``explicit`` (G-07).
    """
    k = phrase.kind
    if k == "range":
        return _unresolved("range")
    if k == "vague":
        return _unresolved("vague")
    if k in ("absolute", "yearless", "weekday_date", "invalid"):
        assert phrase.literal_text is not None
        lit = parse_literal(phrase.literal_text, date_order)
        if lit is None:  # cannot happen for a phrase built by classify_phrase
            return _unresolved("invalid_date")
        return _resolve_literal(lit, meeting_date, event)
    if meeting_date is None:
        return _unresolved("no_meeting_date")
    return _resolve_relative(phrase, meeting_date)


def resolve_text(text: str, meeting_date: _dt.date | None, date_order: str | None = None,
                 *, event: bool = False) -> Resolution | None:
    """``classify_phrase`` + ``resolve_phrase``; ``None`` when ``text`` is not a date phrase."""
    p = classify_phrase(text, date_order)
    return None if p is None else resolve_phrase(p, meeting_date, date_order, event=event)


# ================================================================== 7. phrase locator
@dataclass(frozen=True)
class Located:
    """One date phrase found in a cleaned ``description`` (all spans index that string, G-12).

    ``raw`` is the phrase including time words but excluding the introducer
    (``description[start:end] == raw``). ``introducer`` is the introducer text as written
    (``None`` for a trailing standalone phrase); ``introducer_span`` its ``TextSpan``.
    ``separator_span`` covers punctuation between introducer and phrase (``due: Oct 20``), else ``None``.
    ``role``: ``"due"``, ``"event"`` (year-less date under ``on``/``for``) or ``"not_due"``
    (``from``/``since``/``starting``).
    """

    raw: str
    start: int
    end: int
    introducer: str | None
    introducer_span: TextSpan | None
    time_words: tuple[str, ...]
    role: str
    phrase: Phrase
    separator_span: TextSpan | None = None


_INTRO_RE = re.compile(
    r"(?P<nd>(?<!\w)(?:from|since|starting)(?:\s+on)?(?!\w))"
    r"|(?P<d>(?<!\w)(?:no\s+later\s+than|due\s+on|due|by|before|until|till|on|for)(?!\w)"
    r"|\(\s*due\s*:|\[\s*due\s*:|(?<!\w)deadline\s*:|\U0001F4C5)",
    _FLAGS,
)
_SEP_CHARS = " \t:-\u2013\u2014"  # space, tab, colon, hyphen, en dash, em dash
_TRAIL_PUNCT = ".,;:!?)]"
_MAX_PHRASE_TOKENS = 8
_TOKEN_RE = re.compile(r"\S+")
_MONTH_ONLY_NEXT_OK = re.compile(r"\s*(?:$|[.,;:!?)\]\-\u2013\u2014])")


def _is_month_only(p: Phrase, text: str) -> bool:
    return p.kind == "vague" and _RX_MONTH_ONLY.fullmatch(" ".join(text.lower().split())) is not None


def _effective_end(text: str) -> int:
    end = len(text)
    while end > 0 and (text[end - 1].isspace() or text[end - 1] in _TRAIL_PUNCT):
        end -= 1
    return end


def _try_phrase(text: str, a: int, b: int, date_order: str | None) -> Phrase | None:
    """Classify ``text[a:b]`` with trailing punctuation trimmed; ``None`` when not a phrase."""
    while b > a and text[b - 1] in _TRAIL_PUNCT:
        b -= 1
    if b <= a:
        return None
    return classify_phrase(text[a:b], date_order)


def _trim_end(text: str, a: int, b: int) -> int:
    while b > a and text[b - 1] in _TRAIL_PUNCT:
        b -= 1
    return b


def _after_introducer(text: str, pos: int, eff_end: int, date_order: str | None,
                      *, anchored: bool) -> tuple[Phrase, int, int, TextSpan | None] | None:
    """Longest date phrase directly after an introducer ending at ``pos``.

    Returns ``(phrase, start, end, separator_span)``. ``anchored`` (the ``for`` form) demands that
    the phrase runs to the end of the text.
    """
    p = pos
    while p < len(text) and text[p] in _SEP_CHARS:
        p += 1
    sep = TextSpan(pos, p) if text[pos:p].strip() else None
    if p >= eff_end:
        return None
    toks = [(p + m.start(), p + m.end()) for m in _TOKEN_RE.finditer(text[p:eff_end])]
    if not toks:
        return None
    ntok = len(toks)
    if anchored:
        if ntok > _MAX_PHRASE_TOKENS:
            return None
        ph = classify_phrase(text[p:eff_end], date_order)
        return None if ph is None else (ph, p, eff_end, sep)
    for j in range(min(ntok, _MAX_PHRASE_TOKENS) - 1, -1, -1):
        b = _trim_end(text, p, toks[j][1])
        ph = classify_phrase(text[p:b], date_order) if b > p else None
        if ph is None:
            continue
        if ph.kind == "vague" and _RX_AFTER_LAUNCH.fullmatch(" ".join(text[p:b].lower().split())):
            continue  # found only by the trailing-standalone rule (B-3)
        if _is_month_only(ph, text[p:b]) and not _MONTH_ONLY_NEXT_OK.match(text, b):
            continue
        return ph, p, b, sep
    return None


def _role(introducer_key: str, phrase: Phrase) -> str:
    if introducer_key in ("on", "for") and phrase.yearless and phrase.kind in ("yearless", "weekday_date"):
        return "event"
    return "due"


def locate(description: str, date_order: str | None = None) -> list[Located]:
    """Find every date phrase in the cleaned ``description`` (spans index ``description``).

    Candidates come from introducers (``by``, ``due``, ``before``, ``until``, ``till``, ``on``, ``for``,
    ``no later than``, ``deadline:``, ``(due:``, ``[due:``, a calendar emoji; ``from``/``since``/
    ``starting`` give ``role="not_due"``) and from a trailing standalone phrase anchored to the end of the
    text. An introducer without a date phrase after it is skipped silently. ``Located`` entries are
    returned in text order; identical raw phrases are all returned (use ``distinct_due_phrases``).
    """
    text = description
    eff_end = _effective_end(text)
    found: list[Located] = []
    consumed = 0
    for m in _INTRO_RE.finditer(text):
        if m.start() < consumed:
            continue
        key = " ".join(m.group(0).lower().split())
        is_not_due = m.group("nd") is not None
        hit = _after_introducer(text, m.end(), eff_end, date_order, anchored=(key == "for"))
        if hit is None:
            continue
        ph, a, b, sep = hit
        role = "not_due" if is_not_due else _role(key, ph)
        found.append(Located(text[a:b], a, b, m.group(0), TextSpan(m.start(), m.end()),
                             ph.time_words, role, ph, sep))
        consumed = b
    # trailing standalone phrase (no introducer): longest suffix of the text that is a date phrase
    toks = [(mm.start(), mm.end()) for mm in _TOKEN_RE.finditer(text[:eff_end])]
    for i in range(max(0, len(toks) - _MAX_PHRASE_TOKENS), len(toks)):
        a = toks[i][0]
        while a < eff_end and text[a] in "([":
            a += 1
        b = eff_end
        if a >= b or any(a < f.end and f.start < b for f in found):
            continue
        ph = classify_phrase(text[a:b], date_order)
        if ph is None or _is_month_only(ph, text[a:b]):
            continue
        found.append(Located(text[a:b], a, b, None, None, ph.time_words, "due", ph))
        break
    found.sort(key=lambda f: f.start)
    return found


def _norm_raw(raw: str) -> str:
    return " ".join(raw.casefold().split())


def distinct_due_phrases(located: list[Located]) -> list[Located]:
    """Due/event phrases, first occurrence of each normalised raw, in text order (``not_due`` excluded)."""
    seen: set[str] = set()
    out: list[Located] = []
    for f in located:
        if f.role == "not_due":
            continue
        key = _norm_raw(f.raw)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def not_due_phrases(located: list[Located]) -> list[Located]:
    return [f for f in located if f.role == "not_due"]


def has_multiple_dates(located: list[Located]) -> bool:
    """Two or more *different* due/event phrases (different normalised raw), spec 9.2 (B-3 default)."""
    return len(distinct_due_phrases(located)) >= 2


# ================================================================== 8. resolve_due (T-M3-09)
MeetingDate = _dt.date  # alias so pure parsing modules can annotate without importing ``datetime``


@dataclass(frozen=True)
class DueResult:
    """Due provenance of one item description (spec 9.5, 7.2). Pure function result.

    ``due_raw``: the single due/event phrase without introducer (time words kept); for
    ``multiple_dates`` the distinct raws joined by ``"; "``; ``None`` when there is no due phrase.
    ``due_confidence``: explicit/relative/inferred/unresolved/none. ``due_rule`` is a
    ``codes.DUE_RULES`` member for a resolved date, ``None`` otherwise. ``due_reason`` is set only
    when unresolved. ``due_raw_other``: non-due phrases (``from 9 Oct``) joined by ``"; "``.
    ``raws``: every distinct due/event raw in text order. ``spans``: introducer, separator and
    phrase spans of every located phrase (due, event, not-due), offsets into ``description``.
    ``warnings``: ``W-*`` codes (``W-DATE-NOT-DUE``, ``W-DUE-BEFORE-MEETING``), sorted.
    """

    due_raw: str | None
    due_date: _dt.date | None
    due_confidence: Confidence
    due_rule: Rule | None
    due_reason: Reason | None
    due_raw_other: str | None = None
    raws: tuple[str, ...] = ()
    spans: tuple[TextSpan, ...] = ()
    warnings: tuple[str, ...] = ()


def resolve_due(description: str, meeting_date: _dt.date | None,
                date_order: str | None = None) -> DueResult:
    """Locate and resolve the due date of a cleaned ``description`` (pure; no clock, BR-05).

    ``locate`` -> ``distinct_due_phrases``: two or more different phrases give unresolved
    ``multiple_dates`` (all raws kept); none gives ``none`` (``due_raw`` ``None``); one is resolved
    with ``resolve_phrase`` (``event=True`` for an ``event`` role). Not-due phrases only fill
    ``due_raw_other`` and add ``W-DATE-NOT-DUE`` (also next to a due phrase).
    """
    located = locate(description, date_order)
    distinct = distinct_due_phrases(located)
    others = not_due_phrases(located)
    spans: list[TextSpan] = []
    for f in located:
        if f.introducer_span is not None:
            spans.append(f.introducer_span)
        if f.separator_span is not None:
            spans.append(f.separator_span)
        spans.append(TextSpan(f.start, f.end))
    raws = tuple(f.raw for f in distinct)
    other = "; ".join(f.raw for f in others) or None
    extra = (_codes.W_DATE_NOT_DUE,) if others else ()
    common = {"due_raw_other": other, "raws": raws, "spans": tuple(spans)}
    if len(distinct) >= 2:
        return DueResult("; ".join(raws), None, Confidence.UNRESOLVED, None, Reason("multiple_dates"),
                         warnings=tuple(sorted(extra)), **common)
    if not distinct:
        return DueResult(None, None, Confidence.NONE, None, None, warnings=tuple(sorted(extra)), **common)
    loc = distinct[0]
    res = resolve_phrase(loc.phrase, meeting_date, date_order, event=(loc.role == "event"))
    return DueResult(loc.raw, res.date, res.confidence, res.rule, res.reason,
                     warnings=tuple(sorted((*res.warnings, *extra))), **common)


def format_due(result: DueResult, max_excerpt: int = 24) -> str:
    """Table display of a due result (spec 9.5): date, ``?(raw excerpt)`` if unresolved, ``-`` if none."""
    if result.due_date is not None:
        return result.due_date.isoformat()
    if result.due_confidence == Confidence.UNRESOLVED:
        raw = result.due_raw or ""
        if len(raw) > max_excerpt:
            raw = raw[: max_excerpt - 1] + "\u2026"
        return f"?({raw})"
    return "-"
