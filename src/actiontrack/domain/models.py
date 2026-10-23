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


# ===========================================================================
# Parse-result model (T-M2-01, T-M2-16)
# ===========================================================================
#
# Immutable, pure-Python value objects returned by the parsing layer. No I/O, no clock, no
# randomness; every collection is a tuple. Codes are validated against ``codes.py`` (this module
# defines no code list; it only adds the parse-time message templates keyed by code).
#
# Public API
# ----------
# ``ParseWarning(code, line, message, detail=None, file=None)``  warning code (W-*), 1-based line or
#                       None; ``file`` is set by layers that have no enclosing ParsedFile (discovery)
# ``FileFailure(code, message, byte_offset=None, size_bytes=None, os_reason=None, traceback=None)``
#                       E-* code
# ``Attendee(raw_name, email_hint=None)``
# ``MeetingMeta(date, date_source, date_raw, title, attendees)``
# ``TextSpan(start, end)``          half-open offsets into ``ParsedItem.description``
# ``ParsedItem(...)``               see class docstring; ``owner_hook`` / ``due_hook`` stay None in M2
# ``NonCheckboxCandidate(kind, line, text)``
# ``ParsedFile(rel_path, status, failure, content_sha256, size_bytes, mtime_ns, meeting, items,
#                candidates, warnings)`` plus ``to_dict()``
# ``PARSE_MESSAGE_TEMPLATES``       code -> template (placeholders ``{file}`` + code-specific ones)
# ``PARSER_EMITTED_CODES``          codes that must have a template
# ``parse_message(code, file, line=None, **kw) -> str``
# ``ingest_order_key(parsed_file) -> tuple``   persistence order (T-M2-16)

import dataclasses  # noqa: E402
import datetime as _dt  # noqa: E402

from actiontrack import codes as _codes  # noqa: E402

PARSE_STATUSES: frozenset[str] = frozenset({"ok", "empty", "failed"})
DATE_SOURCES: frozenset[str] = frozenset({"front_matter", "date_line", "title", "filename", "none"})
CHECKBOX_STATES: frozenset[str] = frozenset({"open", "done"})

# Parse-time message templates. ``{file}`` is the location text ("rel/path.md" or
# "rel/path.md:12"); each states what happened and what to do next (spec 11, last paragraph).
_T = _codes
PARSE_MESSAGE_TEMPLATES: dict[str, str] = {
    _T.E_UNREADABLE: "{file}: cannot read file ({reason}). Next: check permissions or close the program locking it.",
    _T.E_ENCODING: "{file}: not valid UTF-8 at byte {offset}; file skipped. Next: re-save the file as UTF-8.",
    _T.E_BINARY: "{file}: contains binary data (NUL bytes or binary header); file skipped. Next: remove it from the notes folder or replace it with a text note.",
    _T.E_TOO_LARGE: "{file}: file is {size} bytes, over the {limit}-byte limit; file skipped. Next: split the file or raise limits.max_file_bytes.",
    _T.E_INTERNAL_PARSE: "{file}: unexpected internal error while parsing; the file's changes were rolled back. Next: re-run with --verbose and report the trace.",
    _T.W_NO_FILES: "{file}: no .md files found. Next: check the folder path.",
    _T.W_EMPTY_FILE: "{file}: file is empty; nothing to ingest. Next: add notes or delete the file.",
    _T.W_NO_ITEMS: "{file}: no action items found. Next: add '- [ ] Owner to do something' lines if the meeting had actions.",
    _T.W_SYMLINK_SKIPPED: "{file}: symbolic link skipped (links are never followed). Next: copy the real file into the notes folder.",
    _T.W_FRONTMATTER_INVALID: "{file}: front matter is unclosed or unsupported; treated as absent. Next: close it with a '---' line.",
    _T.W_FENCE_UNCLOSED: "{file}: code fence is never closed; the rest of the file is ignored. Next: add the closing fence.",
    _T.W_MEETING_DATE_MISSING: "{file}: no meeting date found; relative due dates stay unresolved. Next: add a 'Date: YYYY-MM-DD' line.",
    _T.W_MEETING_DATE_INVALID: "{file}: meeting date {value!r} is not a valid date; date left empty. Next: write it as YYYY-MM-DD.",
    _T.W_MEETING_DATE_FROM_FILENAME: "{file}: meeting date {date} taken from the file name. Next: add a 'Date:' line to confirm it.",
    _T.W_DATE_WEEKDAY_MISMATCH: "{file}: weekday does not match date {date}; date used as written. Next: fix the weekday or the date.",
    _T.W_DATE_NOT_DUE: "{file}: phrase {phrase!r} is not a due date; kept as other date. Next: write 'due <date>' if it is one.",
    _T.W_CHECKBOX_IGNORED_REGION: "{file}: checkbox inside an ignored region (code fence or comment); not imported. Next: move it out of the region.",
    _T.W_DATE_CONFLICT: "{file}: meeting date sources disagree ({a} vs {b}); using {chosen}. Next: make the date fields agree.",
    _T.W_ATTENDEES_MISSING: "{file}: no attendees listed. Next: add an 'Attendees:' line so owners can be resolved.",
    _T.W_CHECKBOX_NOSPACE: "{file}: checkbox without a space after the bracket. Next: write '- [ ] text'.",
    _T.W_CHECKBOX_NONSTANDARD: "{file}: non-standard checkbox marker. Next: use '[ ]' or '[x]'.",
    _T.W_NONCHECKBOX_CANDIDATES: "{file}: possible action line is not a checkbox and was not imported. Next: turn it into '- [ ] ...' if it is an action.",
    _T.W_DUE_BEFORE_MEETING: "{file}: due date {due} is before the meeting date {meeting}. Next: check the date.",
    _T.W_LONG_LINE: "{file}: line is very long ({length} characters). Next: split it if it holds several actions.",
    _T.W_HTML_COMMENT_UNCLOSED: "{file}: HTML comment is never closed; the rest of the file is ignored. Next: add '-->'.",
    _T.W_CHECKBOX_EMPTY: "{file}: checkbox with no text; not imported. Next: add the task text.",
    _T.W_DIR_UNREADABLE: "{file}: directory cannot be read ({reason}); skipped. Next: fix its permissions and re-run.",
}

#: Codes the parsers emit: all E-* plus W-* whose emitter is "parsers" (22 W + 5 E).
PARSER_EMITTED_CODES: frozenset[str] = _codes.ERROR_CODES | frozenset(
    c for c in _codes.WARNING_CODES if _codes.EMITTERS[c] == "parsers"
)


def parse_message(code: str, file: str, line: int | None = None, **kw: object) -> str:
    """Render ``PARSE_MESSAGE_TEMPLATES[code]``; ``file`` gets ``:line`` appended when given.

    Unknown placeholders are left as ``{key}`` rather than raising.
    """
    where = f"{file}:{line}" if line is not None else file

    class _Keep(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return PARSE_MESSAGE_TEMPLATES[code].format_map(_Keep(file=where, **kw))


@dataclasses.dataclass(frozen=True, slots=True)
class ParseWarning:
    """A non-fatal parse finding. ``code`` must be a ``W-*`` code; ``line`` is 1-based or None."""

    code: str
    line: int | None
    message: str
    detail: str | None = None
    file: str | None = None

    def __post_init__(self) -> None:
        if self.code not in _codes.WARNING_CODES:
            raise ValueError(f"not a warning code: {self.code!r}")

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "line": self.line, "message": self.message, "detail": self.detail}


@dataclasses.dataclass(frozen=True, slots=True)
class FileFailure:
    """A whole-file failure. ``code`` must be an ``E-*`` code (exit 5 at end of run)."""

    code: str
    message: str
    byte_offset: int | None = None
    size_bytes: int | None = None
    os_reason: str | None = None
    traceback: str | None = None

    def __post_init__(self) -> None:
        if self.code not in _codes.ERROR_CODES:
            raise ValueError(f"not an error code: {self.code!r}")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "byte_offset": self.byte_offset,
            "size_bytes": self.size_bytes,
            "os_reason": self.os_reason,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Attendee:
    raw_name: str
    email_hint: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class MeetingMeta:
    """``date`` is a ``datetime.date`` or None; ``date_source`` in DATE_SOURCES."""

    date: _dt.date | None
    date_source: str
    date_raw: str | None
    title: str | None
    attendees: tuple[Attendee, ...] = ()

    def __post_init__(self) -> None:
        if self.date_source not in DATE_SOURCES:
            raise ValueError(f"invalid date_source: {self.date_source!r}")


@dataclasses.dataclass(frozen=True, slots=True)
class TextSpan:
    """Half-open ``[start, end)`` character offsets into ``ParsedItem.description``."""

    start: int
    end: int


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedItem:
    """One checkbox action item.

    ``raw_text`` is verbatim source text (never NFC-normalised); ``description`` the full cleaned
    NFC text (owner/due phrases still present); ``task_text`` the derived display text.
    ``owner_hook`` / ``due_hook`` are placeholders (None in M2) for M3/M4 types.
    """

    line_start: int
    line_end: int
    raw_text: str
    description: str
    task_text: str
    checkbox_state: str
    section_heading: str | None
    occurrence_index: int
    block_hash: str
    owner_hook: object | None = None
    due_hook: object | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "line_start": self.line_start,
            "line_end": self.line_end,
            "raw_text": self.raw_text,
            "description": self.description,
            "task_text": self.task_text,
            "checkbox_state": self.checkbox_state,
            "section_heading": self.section_heading,
            "occurrence_index": self.occurrence_index,
            "block_hash": self.block_hash,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class NonCheckboxCandidate:
    kind: str
    line: int
    text: str


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedFile:
    """Parse result of one file. ``status``: ``ok`` | ``empty`` | ``failed`` (then ``failure`` set)."""

    rel_path: str
    status: str
    failure: FileFailure | None = None
    content_sha256: str | None = None
    size_bytes: int | None = None
    mtime_ns: int | None = None
    meeting: MeetingMeta | None = None
    items: tuple[ParsedItem, ...] = ()
    candidates: tuple[NonCheckboxCandidate, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in PARSE_STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")
        if (self.status == "failed") != (self.failure is not None):
            raise ValueError("status 'failed' requires a failure, and only then")

    def to_dict(self) -> dict[str, object]:
        """Stable, JSON-serialisable form (fixed key order, ISO dates, warnings by (line, code))."""
        meeting = None
        if self.meeting is not None:
            m = self.meeting
            meeting = {
                "date": m.date.isoformat() if m.date else None,
                "date_source": m.date_source,
                "date_raw": m.date_raw,
                "title": m.title,
                "attendees": [{"raw_name": a.raw_name, "email_hint": a.email_hint} for a in m.attendees],
            }
        warnings = sorted(self.warnings, key=lambda w: (w.line or 0, w.code))
        return {
            "rel_path": self.rel_path,
            "status": self.status,
            "failure": self.failure.to_dict() if self.failure else None,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "meeting": meeting,
            "items": [i.to_dict() for i in self.items],
            "candidates": [{"kind": c.kind, "line": c.line, "text": c.text} for c in self.candidates],
            "warnings": [w.to_dict() for w in warnings],
        }


def ingest_order_key(parsed_file: ParsedFile) -> tuple[bool, _dt.date, str]:
    """Persistence order: dated files by meeting date, then ordinal ``rel_path``; undated last.

    ``(meeting_date is None, meeting_date, rel_path)`` with ``date.min`` standing in for None so
    keys always compare. Failed/empty files carry no meeting and land in the null group.
    """
    meeting_date = parsed_file.meeting.date if parsed_file.meeting is not None else None
    return (meeting_date is None, meeting_date or _dt.date.min, parsed_file.rel_path)


# ---------------------------------------------------------------------------------------------
# Owner / person models (T-M4-02; spec 7.2 ItemOwner/Person, 10.1, 10.2a, 10.3)
#
# Public API appended for M4/M5 (all frozen, pure data):
#   ``OwnerStatus``     StrEnum resolved|provisional|ambiguous|unassigned     (item_owner.owner_status)
#   ``Provenance``      StrEnum roster|alias|attendee|attendee_in_meeting|unique_first_name|
#                       initial_form|diacritic_fold|provisional|none          (item_owner.provenance)
#   ``OwnerRole``       StrEnum owner|with                                     (item_owner.role)
#   ``PersonOrigin``    StrEnum roster|attendee|note_owner                     (person.origin)
#   ``NameForm(kind, first, last, initial, qualifier)``  kind: ``NameFormKind`` full|first_initial|
#                       first_only. ``first``/``last``/``initial`` are ``name_key`` text (last is None
#                       unless ``full``; initial is the single letter for ``first_initial`` and the
#                       first letter of ``last`` for ``full``; None for ``first_only``).
#   ``NameMention(raw, span, role=OwnerRole.OWNER)``  one name found in an owner clause; ``raw`` is the
#                       text as written, ``span`` a ``TextSpan`` into ``ParsedItem.description``.
#   ``OwnerPhrase(mentions, rule_id, clause_span, nonperson_raw=None, with_spans=())``
#                       result of owner-phrase extraction (T-M4-03..04). ``rule_id`` is 1..3 (the rule
#                       that located the clause), ``clause_span`` the whole owner clause (removed from
#                       ``task_text``), ``nonperson_raw`` the preserved text of a non-person owner
#                       such as ``Everyone`` (then ``mentions`` is empty and the owner is unassigned),
#                       ``with_spans`` the spans of the ``(with Other)`` collaborator parts.
#   ``ResolvedOwner(ordinal, owner_raw, person_id, status, candidates, provenance, role, warnings)``
#                       one resolved item owner row; ``candidates`` a sorted tuple of person ids (only
#                       for ``ambiguous``), ``warnings`` a tuple of W-* codes (str) the resolver wants
#                       emitted (the caller adds file/line context).
#   ``Person(id, first_name, last_name, identity_key, origin, verified=False, qualifier=None,
#            display_name='', name_key='', email=None)``  mirrors the ``person`` table (``last_name``
#                       None for a provisional single-token/initial-form person).
# ---------------------------------------------------------------------------------------------
import enum as _enum  # noqa: E402


class OwnerStatus(_enum.StrEnum):
    RESOLVED = "resolved"
    PROVISIONAL = "provisional"
    AMBIGUOUS = "ambiguous"
    UNASSIGNED = "unassigned"


class Provenance(_enum.StrEnum):
    ROSTER = "roster"
    ALIAS = "alias"
    ATTENDEE = "attendee"
    ATTENDEE_IN_MEETING = "attendee_in_meeting"
    UNIQUE_FIRST_NAME = "unique_first_name"
    INITIAL_FORM = "initial_form"
    DIACRITIC_FOLD = "diacritic_fold"
    PROVISIONAL = "provisional"
    NONE = "none"


class OwnerRole(_enum.StrEnum):
    OWNER = "owner"
    WITH = "with"


class PersonOrigin(_enum.StrEnum):
    ROSTER = "roster"
    ATTENDEE = "attendee"
    NOTE_OWNER = "note_owner"


class NameFormKind(_enum.StrEnum):
    FULL = "full"
    FIRST_INITIAL = "first_initial"
    FIRST_ONLY = "first_only"


@dataclasses.dataclass(frozen=True, slots=True)
class NameForm:
    """Shape of a written name (see ``names.classify_name``); keys are ``name_key`` text."""

    kind: NameFormKind
    first: str
    last: str | None = None
    initial: str | None = None
    qualifier: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class NameMention:
    """One name inside an owner clause (``span`` is into ``ParsedItem.description``)."""

    raw: str
    span: TextSpan
    role: OwnerRole = OwnerRole.OWNER


@dataclasses.dataclass(frozen=True, slots=True)
class OwnerPhrase:
    """Extracted owner clause (spec 10.1); ``mentions`` empty when ``nonperson_raw`` is set."""

    mentions: tuple[NameMention, ...]
    rule_id: int
    clause_span: TextSpan
    nonperson_raw: str | None = None
    with_spans: tuple[TextSpan, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedOwner:
    """One resolved ItemOwner row (spec 7.2, 10.3)."""

    ordinal: int
    owner_raw: str | None
    person_id: str | None
    status: OwnerStatus
    candidates: tuple[str, ...] = ()
    provenance: Provenance = Provenance.NONE
    role: OwnerRole = OwnerRole.OWNER
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for code in self.warnings:
            if code not in _codes.WARNING_CODES:
                raise ValueError(f"not a warning code: {code!r}")
        if self.status is OwnerStatus.AMBIGUOUS and self.person_id is not None:
            raise ValueError("an ambiguous owner has no person_id")
        if self.status in (OwnerStatus.RESOLVED, OwnerStatus.PROVISIONAL) and self.person_id is None:
            raise ValueError(f"a {self.status.value} owner needs a person_id")


@dataclasses.dataclass(frozen=True, slots=True)
class Person:
    """Person record (spec 7.2); identity is ``identity_key`` (First+Last, spec 10.2a)."""

    id: str
    first_name: str
    last_name: str | None
    identity_key: str
    origin: PersonOrigin
    verified: bool = False
    qualifier: str | None = None
    display_name: str = ""
    name_key: str = ""
    email: str | None = None


# ---------------------------------------------------------------------------------------------
# Ambiguity surfacing models (T-M4-12; spec 10.2a rule 1, 10.4, FR-02.9, AC-05.11, AC-05.14)
#
#   ``OwnerDisplay(text, flags)``       how one owner is shown: ``flags`` is a tuple of list flags
#                       (``OA`` owner ambiguous, ``OU`` owner unassigned/provisional).
#   ``AmbiguousGroup(candidates, names, items)``   one candidate set of ``list --ambiguous``.
#   ``AmbiguousRecord(item_id, location, raw, candidate_names)``   ingest-report hook row.
# ---------------------------------------------------------------------------------------------
FLAG_OWNER_AMBIGUOUS = "OA"
FLAG_OWNER_UNASSIGNED = "OU"


@dataclasses.dataclass(frozen=True, slots=True)
class OwnerDisplay:
    text: str
    flags: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class AmbiguousGroup:
    candidates: tuple[str, ...]
    names: tuple[str, ...]
    items: tuple[object, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class AmbiguousRecord:
    item_id: int
    location: str | None
    raw: str | None
    candidate_names: tuple[str, ...]
