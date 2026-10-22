"""Warning/error code registry (spec Appendix B v0.6, spec 11, 9.5; T-M1-02).

Single place to import ``E-*`` / ``W-*`` codes, due-unresolved reasons, ``due_rule``
ids and ItemLink reasons from. Data only, no business logic.

Public API
----------
Code constants        ``E_BINARY = "E-BINARY"``, ``W_EMPTY_FILE = "W-EMPTY-FILE"``, ... (one per code)
``Severity``          enum ``ERROR`` / ``WARNING``
``CodeInfo``          frozen dataclass ``(code, severity, template, emitter)``; ``.format(**kw)``
                      fills ``template`` (missing keys are left as ``{key}``)
``REGISTRY``          ``dict[str, CodeInfo]`` for every code
``ERROR_CODES``, ``WARNING_CODES``, ``DUE_REASONS``, ``DUE_RULES``   frozensets of strings
``ItemLinkReason``    StrEnum of ItemLink reasons (NOT warning codes)
``EMITTERS``          ``dict[str, str]`` code -> emitting component
                      (``"parsers"`` | ``"ingest"`` | ``"resolution"``), used by T-M9-09
``is_file_failure(code) -> bool``   E-* => True (exit 5); W-* => False (exit 0)
``message(code, **kw) -> str``      ``f"{code}: ..."`` rendered from the template

Emitters (Appendix B note): parse-time warnings and all ``E-*`` raised while reading are
"parsers" (discovery/reader/markdown/meeting/items); ``W-MEETING-DATE-FUTURE`` and
``E-INTERNAL-PARSE`` come from the "ingest" service; ``W-OWNER-*``,
``W-ATTENDEE-NAME-INCOMPLETE``, ``W-NAME-DIACRITIC``, ``W-STATUS-*``, ``W-FILE-MISSING``
from the "resolution" layer (ingest/resolution).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Severity(enum.Enum):
    ERROR = "error"
    WARNING = "warning"


# ----------------------------------------------------------------- error codes
E_UNREADABLE = "E-UNREADABLE"
E_ENCODING = "E-ENCODING"
E_BINARY = "E-BINARY"
E_TOO_LARGE = "E-TOO-LARGE"
E_INTERNAL_PARSE = "E-INTERNAL-PARSE"

# --------------------------------------------------------------- warning codes
W_NO_FILES = "W-NO-FILES"
W_EMPTY_FILE = "W-EMPTY-FILE"
W_NO_ITEMS = "W-NO-ITEMS"
W_SYMLINK_SKIPPED = "W-SYMLINK-SKIPPED"
W_FRONTMATTER_INVALID = "W-FRONTMATTER-INVALID"
W_FENCE_UNCLOSED = "W-FENCE-UNCLOSED"
W_MEETING_DATE_MISSING = "W-MEETING-DATE-MISSING"
W_MEETING_DATE_INVALID = "W-MEETING-DATE-INVALID"
W_MEETING_DATE_FROM_FILENAME = "W-MEETING-DATE-FROM-FILENAME"
W_DATE_WEEKDAY_MISMATCH = "W-DATE-WEEKDAY-MISMATCH"
W_DATE_NOT_DUE = "W-DATE-NOT-DUE"
W_CHECKBOX_IGNORED_REGION = "W-CHECKBOX-IGNORED-REGION"
W_MEETING_DATE_FUTURE = "W-MEETING-DATE-FUTURE"
W_DATE_CONFLICT = "W-DATE-CONFLICT"
W_ATTENDEES_MISSING = "W-ATTENDEES-MISSING"
W_CHECKBOX_NOSPACE = "W-CHECKBOX-NOSPACE"
W_CHECKBOX_NONSTANDARD = "W-CHECKBOX-NONSTANDARD"
W_NONCHECKBOX_CANDIDATES = "W-NONCHECKBOX-CANDIDATES"
W_OWNER_MISSING = "W-OWNER-MISSING"
W_OWNER_NOW_AMBIGUOUS = "W-OWNER-NOW-AMBIGUOUS"
W_NAME_DIACRITIC = "W-NAME-DIACRITIC"
W_DUE_BEFORE_MEETING = "W-DUE-BEFORE-MEETING"
W_STATUS_REOPENED = "W-STATUS-REOPENED"
W_STATUS_CONFLICT = "W-STATUS-CONFLICT"
W_FILE_MISSING = "W-FILE-MISSING"
W_LONG_LINE = "W-LONG-LINE"
W_ATTENDEE_NAME_INCOMPLETE = "W-ATTENDEE-NAME-INCOMPLETE"
W_HTML_COMMENT_UNCLOSED = "W-HTML-COMMENT-UNCLOSED"
W_CHECKBOX_EMPTY = "W-CHECKBOX-EMPTY"
W_DIR_UNREADABLE = "W-DIR-UNREADABLE"

# ------------------------------------------------------- due-unresolved reasons
DUE_NO_MEETING_DATE = "no_meeting_date"
DUE_VAGUE = "vague"
DUE_RANGE = "range"
DUE_SAME_WEEKDAY = "same_weekday"
DUE_THIS_WEEKDAY_PAST = "this_weekday_past"
DUE_WEEKEND_MEETING = "weekend_meeting"
DUE_AMBIGUOUS_NUMERIC = "ambiguous_numeric"
DUE_INVALID_DATE = "invalid_date"
DUE_WEEKDAY_DATE_MISMATCH = "weekday_date_mismatch"
DUE_MULTIPLE_DATES = "multiple_dates"

DUE_REASONS: frozenset[str] = frozenset(
    {
        DUE_NO_MEETING_DATE,
        DUE_VAGUE,
        DUE_RANGE,
        DUE_SAME_WEEKDAY,
        DUE_THIS_WEEKDAY_PAST,
        DUE_WEEKEND_MEETING,
        DUE_AMBIGUOUS_NUMERIC,
        DUE_INVALID_DATE,
        DUE_WEEKDAY_DATE_MISMATCH,
        DUE_MULTIPLE_DATES,
    }
)

# ------------------------------------------------ due_rule catalogue (spec 9.5)
DUE_RULES: frozenset[str] = frozenset(
    {
        "iso_date",
        "text_date",
        "numeric_date",
        "yearless_date",
        "weekday_date",
        "event_date",
        "day_of_month",
        "today",
        "eod_alone",
        "tomorrow",
        "yesterday",
        "weekday_next",
        "weekday_this",
        "weekday_next_week",
        "monday_next_week",
        "end_of_week",
        "end_of_next_week",
        "end_of_month",
        "end_of_next_month",
        "in_n_days",
        "in_n_weeks",
    }
)


class ItemLinkReason(enum.StrEnum):
    """ItemLink.reason (spec 7.2); separate from warning codes."""

    DUE_DIFFERS = "due_differs"
    OWNER_DIFFERS = "owner_differs"
    STATUS_DIFFERS = "status_differs"
    SAME_FILE = "same_file"
    FUZZY = "fuzzy"
    RECURRENCE = "recurrence"
    OUTSIDE_WINDOW = "outside_window"
    OWNER_UNRESOLVED = "owner_unresolved"


# ------------------------------------------------------------------- registry
@dataclass(frozen=True)
class CodeInfo:
    code: str
    severity: Severity
    template: str
    emitter: str

    def format(self, **kw: object) -> str:
        class _Keep(dict):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        return self.template.format_map(_Keep(kw))


_P, _I, _R = "parsers", "ingest", "resolution"

_E = Severity.ERROR
_W = Severity.WARNING

_DEFS: tuple[tuple[str, Severity, str, str], ...] = (
    (E_UNREADABLE, _E, "cannot read file: {reason}; skipped. Check permissions or close the program locking it", _P),
    (E_ENCODING, _E, "not valid UTF-8 at byte {offset}; skipped. Re-save the file as UTF-8", _P),
    (E_BINARY, _E, "contains binary data (NUL bytes or binary header); skipped", _P),
    (E_TOO_LARGE, _E, "file is {size} bytes, over the {limit}-byte limit; skipped. Raise limits.max_file_bytes or split the file", _P),
    (E_INTERNAL_PARSE, _E, "unexpected internal error while processing this file; its changes were rolled back. Re-run with --verbose for the trace", _I),
    (W_NO_FILES, _W, "no .md files found in {folder}", _P),
    (W_EMPTY_FILE, _W, "file is empty; nothing to ingest", _P),
    (W_NO_ITEMS, _W, "no action items found", _P),
    (W_SYMLINK_SKIPPED, _W, "symbolic link skipped: {path}", _P),
    (W_FRONTMATTER_INVALID, _W, "front matter is unclosed or unsupported; treated as absent", _P),
    (W_FENCE_UNCLOSED, _W, "code fence opened at line {line} is never closed", _P),
    (W_MEETING_DATE_MISSING, _W, "no meeting date found; relative due dates stay unresolved", _P),
    (W_MEETING_DATE_INVALID, _W, "meeting date {value!r} is not a valid date", _P),
    (W_MEETING_DATE_FROM_FILENAME, _W, "meeting date taken from the file name: {date}", _P),
    (W_DATE_WEEKDAY_MISMATCH, _W, "weekday does not match the date {date}; date used as written", _P),
    (W_DATE_NOT_DUE, _W, "phrase {phrase!r} is not a due date; kept as other date", _P),
    (W_CHECKBOX_IGNORED_REGION, _W, "checkbox inside an ignored region (code fence/comment) at line {line}", _P),
    (W_MEETING_DATE_FUTURE, _W, "meeting date {date} is after today ({today})", _I),
    (W_DATE_CONFLICT, _W, "meeting date sources disagree ({a} vs {b}); using {chosen}", _P),
    (W_ATTENDEES_MISSING, _W, "no attendees listed", _P),
    (W_CHECKBOX_NOSPACE, _W, "checkbox without a space after the bracket at line {line}", _P),
    (W_CHECKBOX_NONSTANDARD, _W, "non-standard checkbox marker at line {line}", _P),
    (W_NONCHECKBOX_CANDIDATES, _W, "{count} possible action lines are not checkboxes and were not imported", _P),
    (W_OWNER_MISSING, _W, "no owner found for {task!r}", _R),
    (W_OWNER_NOW_AMBIGUOUS, _W, "owner {name!r} now matches several people", _R),
    (W_NAME_DIACRITIC, _W, "name {name!r} differs from {other!r} only by diacritics", _R),
    (W_DUE_BEFORE_MEETING, _W, "due date {due} is before the meeting date {meeting}", _P),
    (W_STATUS_REOPENED, _W, "item {item} reopened by a later note", _R),
    (W_STATUS_CONFLICT, _W, "item {item} has conflicting status across sources", _R),
    (W_FILE_MISSING, _W, "previously ingested file no longer exists: {path}", _R),
    (W_LONG_LINE, _W, "line {line} is very long ({length} characters)", _P),
    (W_ATTENDEE_NAME_INCOMPLETE, _W, "attendee {name!r} has no last name while namesakes exist", _R),
    (W_HTML_COMMENT_UNCLOSED, _W, "HTML comment opened at line {line} is never closed", _P),
    (W_CHECKBOX_EMPTY, _W, "checkbox with no text at line {line}; not imported", _P),
    (W_DIR_UNREADABLE, _W, "directory cannot be read: {path}; skipped", _P),
)

REGISTRY: dict[str, CodeInfo] = {c: CodeInfo(c, s, t, e) for c, s, t, e in _DEFS}
assert len(REGISTRY) == len(_DEFS), "duplicate code in registry"

ERROR_CODES: frozenset[str] = frozenset(c for c, i in REGISTRY.items() if i.severity is _E)
WARNING_CODES: frozenset[str] = frozenset(c for c, i in REGISTRY.items() if i.severity is _W)
EMITTERS: dict[str, str] = {c: i.emitter for c, i in REGISTRY.items()}


def is_file_failure(code: str) -> bool:
    """True for E-* (file failure, exit 5); False for W-* (warning, exit 0). Unknown codes raise KeyError."""
    return REGISTRY[code].severity is Severity.ERROR


def message(code: str, **kw: object) -> str:
    return f"{code}: {REGISTRY[code].format(**kw)}"
