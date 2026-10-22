"""Meeting metadata: date, title and attendees (spec 8.1; T-M2-12, T-M2-13).

Pure functions of the already-scanned document and the file's relative path: no I/O, no clock.
``W-MEETING-DATE-FUTURE`` is NOT produced here (it needs today; the ingest service computes it).

Public API (T-M2-15 assembly calls ``parse_meeting``)
-----------------------------------------------------
``parse_meeting(scan, rel_path, date_order=None, file=None) -> MeetingParse``
    ``scan`` is a ``markdown.DocumentScan``; ``MeetingParse(meta, warnings)`` where ``meta`` is a
    ``models.MeetingMeta`` and ``warnings`` a tuple of ``ParseWarning`` (date warnings, then
    ``W-ATTENDEES-MISSING``). ``file`` is the label used in message texts (default ``rel_path``).
    Front-matter warnings stay in ``scan.warnings`` and are not repeated.
``extract_meeting_date(lines, infos, front_matter, filename, date_order=None, file="") -> DateResult``
    ``lines`` is the original line list (1-based line numbers = index + 1), ``infos`` the
    ``LineInfo`` sequence (``scan.annotation.lines``), ``front_matter`` the accepted ``FrontMatter``
    or None (a rejected block is None, so tier 1 does not exist). ``DateResult(date, source, raw,
    warnings)`` with ``source`` in front_matter | date_line | title | filename | none.
    Tiers: front-matter ``date``/``meeting_date``; ``Date:`` / ``**Date:**`` / ``**Date**:`` line
    within the first 30 non-blank normal lines; first heading with an ISO or textual date (needs a
    year); filename prefix ``YYYY-MM-DD`` / ``YYYYMMDD``. A date *field* that is present but not
    parseable (including a year-less value) gives ``None`` + ``W-MEETING-DATE-INVALID`` only and
    stops the search (no filename fallback; spec 8.1, G-21). The filename is used only when no
    date field exists at all (``W-MEETING-DATE-FROM-FILENAME``); nothing found gives
    ``W-MEETING-DATE-MISSING`` only. A leading weekday that disagrees gives
    ``W-DATE-WEEKDAY-MISMATCH`` (date still used); a lower tier parsing to a different date gives
    one ``W-DATE-CONFLICT`` per disagreeing source (higher tier wins). Numeric ``NN/NN/YYYY`` is
    accepted only when unambiguous or ``date_order`` ('dmy'/'mdy') is given.
``extract_title(infos, front_matter, filename) -> str``
    front-matter ``title``; ``# Meeting: <title>`` (first ``Meeting:`` only); first H1; filename stem.
``extract_attendees(infos, front_matter) -> tuple[Attendee, ...]``
    front-matter ``attendees``/``participants``; else an ``Attendees:``/``Participants:``/
    ``Present:`` line (also bold) anywhere on a normal line (first non-empty wins, note B-8); else
    a heading with one of those names followed by a bullet list. Names split on ``, ; newline`` and
    the word ``and``; ``(role)`` removed; ``<email>`` becomes ``email_hint``; ``Absent:`` /
    ``Apologies:`` lines are never attendees; exact duplicates dropped. Names are kept as written
    (``Priya``-style incomplete names are the namesake logic's business, M4).
``split_names(text) -> tuple[Attendee, ...]``  the splitter on its own.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Sequence
from dataclasses import dataclass

from actiontrack import codes
from actiontrack.domain import dates as _dates
from actiontrack.domain.models import Attendee, MeetingMeta, ParseWarning, parse_message
from actiontrack.parsing.items import clean_description
from actiontrack.parsing.markdown import (
    KIND_NORMAL,
    DocumentScan,
    FrontMatter,
    LineInfo,
    heading_text,
)

DATE_LINE_WINDOW = 30  # non-blank normal lines searched for a Date: line

SRC_FRONT_MATTER = "front_matter"
SRC_DATE_LINE = "date_line"
SRC_TITLE = "title"
SRC_FILENAME = "filename"
SRC_NONE = "none"


@dataclass(frozen=True, slots=True)
class DateResult:
    date: _dt.date | None
    source: str
    raw: str | None
    warnings: tuple[ParseWarning, ...]


@dataclass(frozen=True, slots=True)
class MeetingParse:
    meta: MeetingMeta
    warnings: tuple[ParseWarning, ...]


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _rejected_block_end(infos: Sequence[LineInfo], front_matter: FrontMatter | None) -> int:
    """Number of leading lines of a *rejected* front-matter block (``---`` ... closing ``---``/``...``).

    Such a block is parsed as body (W-FRONTMATTER-INVALID) but its ``key: value`` lines are not
    meeting fields, so the date-line and attendee-line scans skip them. 0 when not applicable.
    """
    if front_matter is not None or not infos or infos[0].text.strip() != "---":
        return 0
    for i in range(1, len(infos)):
        if infos[i].text.strip() in ("---", "..."):
            return i + 1
    return 0


def _warn(code: str, file: str, line: int | None, **kw: object) -> ParseWarning:
    return ParseWarning(code, line, parse_message(code, file, line, **kw), None, file)


# ================================================================== date
_DATE_LINE_RE = re.compile(
    r"[ \t]*(?:\*\*|__)?date(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*(?P<v>.*)", re.IGNORECASE
)
_FILENAME_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?!\d)")
_FILENAME_COMPACT_RE = re.compile(r"(\d{4})(\d{2})(\d{2})(?!\d)")


def _strip_value(value: str) -> str:
    v = value.strip()
    while v.endswith(("**", "__")):
        v = v[:-2].rstrip()
    return clean_description(v) if v else v


def _parse_field(value: str, date_order: str | None) -> tuple[_dt.date | None, int | None]:
    """(date, weekday_claim) for a whole field value; (None, None) when not an accepted date."""
    lit = _dates.parse_literal(value, date_order)
    if isinstance(lit, _dates.Literal):
        return lit.date, lit.weekday_claim
    return None, None


def _heading_date(text: str) -> _dates.Literal | None:
    for i in range(len(text)):
        if i and (text[i - 1].isalnum()):
            continue
        m = _dates.match_literal_at(text, i)
        if m is not None and isinstance(m.result, _dates.Literal) and m.result.rule in (
            "iso_date",
            "text_date",
        ):
            return m.result
    return None


def _filename_date(filename: str) -> tuple[_dt.date, str] | None:
    name = _basename(filename)
    for rx in (_FILENAME_ISO_RE, _FILENAME_COMPACT_RE):
        m = rx.match(name)
        if m and _dates.is_valid_ymd(int(m[1]), int(m[2]), int(m[3])):
            return _dt.date(int(m[1]), int(m[2]), int(m[3])), m[0]
    return None


def extract_meeting_date(
    lines: Sequence[str],
    infos: Sequence[LineInfo],
    front_matter: FrontMatter | None,
    filename: str,
    date_order: str | None = None,
    file: str = "",
) -> DateResult:
    label = file or filename
    warnings: list[ParseWarning] = []

    # candidates per tier: (source, date|None-if-invalid-field, raw, line, claim)
    fm_field: tuple[str, int] | None = None
    if front_matter is not None:
        for key in ("date", "meeting_date"):
            if key in front_matter.fields:
                v = front_matter.fields[key]
                fm_field = (v if isinstance(v, str) else " ".join(v), front_matter.key_lines.get(key, 1))
                break

    line_field: tuple[str, int] | None = None
    seen = 0
    skip = _rejected_block_end(infos, front_matter)
    for idx, info in enumerate(infos):
        if idx < skip:
            continue
        if info.kind != KIND_NORMAL or not info.visible_text.strip():
            continue
        seen += 1
        if seen > DATE_LINE_WINDOW:
            break
        m = _DATE_LINE_RE.fullmatch(info.visible_text)
        if m:
            line_field = (_strip_value(m["v"]), idx + 1)
            break

    heading: tuple[_dates.Literal, int] | None = None
    for idx, info in enumerate(infos):
        if info.kind != KIND_NORMAL:
            continue
        ht = heading_text(info.visible_text)
        if ht:
            lit = _heading_date(ht)
            if lit is not None:
                heading = (lit, idx + 1)
                break

    fname = _filename_date(filename)

    # Resolve the winning tier.
    winner: tuple[str, _dt.date, str, int | None, int | None] | None = None
    for source, field in ((SRC_FRONT_MATTER, fm_field), (SRC_DATE_LINE, line_field)):
        if field is None:
            continue
        raw, line = field
        value = _strip_value(raw) if source == SRC_FRONT_MATTER else raw
        d, claim = _parse_field(value, date_order)
        if d is None:
            warnings.append(_warn(codes.W_MEETING_DATE_INVALID, label, line, value=value))
            return DateResult(None, SRC_NONE, value, tuple(warnings))
        winner = (source, d, value, line, claim)
        break
    if winner is None and heading is not None:
        lit, line = heading
        winner = (SRC_TITLE, lit.date, lit.raw, line, lit.weekday_claim)
    if winner is None and fname is not None:
        winner = (SRC_FILENAME, fname[0], fname[1], None, None)

    if winner is None:
        warnings.append(_warn(codes.W_MEETING_DATE_MISSING, label, None))
        return DateResult(None, SRC_NONE, None, tuple(warnings))

    source, date, raw, line, claim = winner
    if source == SRC_FILENAME:
        warnings.append(_warn(codes.W_MEETING_DATE_FROM_FILENAME, label, None, date=date.isoformat()))
    if claim is not None and claim != date.weekday():
        warnings.append(_warn(codes.W_DATE_WEEKDAY_MISMATCH, label, line, date=date.isoformat()))

    # Lower tiers that parse to a different date => conflict (highest tier wins).
    order = [SRC_FRONT_MATTER, SRC_DATE_LINE, SRC_TITLE, SRC_FILENAME]
    lower: list[tuple[str, _dt.date | None]] = []
    if line_field is not None:
        lower.append((SRC_DATE_LINE, _parse_field(_strip_value(line_field[0]), date_order)[0]))
    if heading is not None:
        lower.append((SRC_TITLE, heading[0].date))
    if fname is not None:
        lower.append((SRC_FILENAME, fname[0]))
    for src, d in lower:
        if order.index(src) > order.index(source) and d is not None and d != date:
            warnings.append(
                _warn(codes.W_DATE_CONFLICT, label, line, a=date.isoformat(), b=d.isoformat(), chosen=date.isoformat())
            )
    return DateResult(date, source, raw, tuple(warnings))


# ================================================================== title
_H1_RE = re.compile(r" {0,3}#(?!#)")
_MEETING_RE = re.compile(r"meeting\s*:\s*(?P<t>.*)", re.IGNORECASE)


def _stem(filename: str) -> str:
    name = _basename(filename)
    dot = name.rfind(".")
    return name[:dot] if dot > 0 else name


def extract_title(infos: Sequence[LineInfo], front_matter: FrontMatter | None, filename: str) -> str:
    if front_matter is not None:
        t = front_matter.fields.get("title")
        if isinstance(t, str) and clean_description(t):
            return clean_description(t)
    h1s: list[str] = []
    for info in infos:
        if info.kind != KIND_NORMAL or not _H1_RE.match(info.visible_text):
            continue
        ht = heading_text(info.visible_text)
        if ht:
            h1s.append(ht)
    for ht in h1s:
        m = _MEETING_RE.match(ht)
        if m:
            t = clean_description(m["t"])
            if t:
                return t
    for ht in h1s:
        m = _MEETING_RE.fullmatch(ht)
        if m and not m["t"].strip():
            continue
        t = clean_description(ht)
        if t:
            return t
    return _stem(filename)


# ================================================================== attendees
_ATT_LINE_RE = re.compile(
    r"[ \t]*(?:\*\*|__)?(?:attendees|participants|present)(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*(?P<v>.*)",
    re.IGNORECASE,
)
_ATT_NAMES = {"attendees", "participants", "present"}
_BULLET_RE = re.compile(r"[ \t]*[-*+][ \t]+(?P<v>.*)")
_EMAIL_RE = re.compile(r"<\s*([^<>\s@]+@[^<>\s]+)\s*>")
_PAREN_RE = re.compile(r"\([^()]*\)")


def _mask(text: str) -> str:
    """Same-length copy where everything inside ``()`` / ``<>`` is replaced by ``x``."""
    out: list[str] = []
    depth = 0
    angle = False
    for ch in text:
        if ch == "(":
            depth += 1
            out.append("x")
        elif ch == ")" and depth:
            depth -= 1
            out.append("x")
        elif ch == "<" and not depth:
            angle = True
            out.append("x")
        elif ch == ">" and angle:
            angle = False
            out.append("x")
        else:
            out.append("x" if depth or angle else ch)
    return "".join(out)


_SEP_RE = re.compile(r"[,;\n]|(?<=\s)and(?=\s)", re.IGNORECASE)


def split_names(text: str) -> tuple[Attendee, ...]:
    """Split an attendee string into ordered, de-duplicated ``Attendee`` values."""
    text = text.replace("\r", "")
    pieces: list[str] = []
    mask = _mask(text)
    pos = 0
    for m in _SEP_RE.finditer(mask):
        pieces.append(text[pos : m.start()])
        pos = m.end()
    pieces.append(text[pos:])
    result: dict[str, str | None] = {}
    for piece in pieces:
        email_m = _EMAIL_RE.search(piece)
        email = email_m[1] if email_m else None
        piece = _EMAIL_RE.sub(" ", piece)
        piece = _PAREN_RE.sub(" ", piece)
        name = clean_description(piece)
        if not name:
            continue
        if name not in result or (result[name] is None and email):
            result[name] = email if email else result.get(name)
    return tuple(Attendee(n, e) for n, e in result.items())


def _fm_attendees(front_matter: FrontMatter | None) -> tuple[Attendee, ...]:
    if front_matter is None:
        return ()
    for key in ("attendees", "participants"):
        v = front_matter.fields.get(key)
        if v is None:
            continue
        text = "\n".join(v) if isinstance(v, list) else v
        found = split_names(text)
        if found:
            return found
    return ()


def _bullets_after(infos: Sequence[LineInfo], start: int) -> list[str]:
    out: list[str] = []
    for info in infos[start:]:
        if info.kind != KIND_NORMAL or not info.visible_text.strip():
            continue  # blank, comment, fence, quote: skip
        if heading_text(info.visible_text) is not None:
            break
        m = _BULLET_RE.fullmatch(info.visible_text)
        if not m:
            break
        out.append(m["v"])
    return out


def extract_attendees(infos: Sequence[LineInfo], front_matter: FrontMatter | None) -> tuple[Attendee, ...]:
    found = _fm_attendees(front_matter)
    if found:
        return found
    skip = _rejected_block_end(infos, front_matter)
    for idx, info in enumerate(infos):
        if idx < skip or info.kind != KIND_NORMAL:
            continue
        m = _ATT_LINE_RE.fullmatch(info.visible_text)
        if not m:
            continue
        value = m["v"].strip()
        if not value:
            value = "\n".join(_bullets_after(infos, idx + 1))
        found = split_names(value)
        if found:
            return found
    for idx, info in enumerate(infos):
        if info.kind != KIND_NORMAL:
            continue
        ht = heading_text(info.visible_text)
        if ht and ht.strip().rstrip(":").strip().lower() in _ATT_NAMES:
            found = split_names("\n".join(_bullets_after(infos, idx + 1)))
            if found:
                return found
    return ()


# ================================================================== assembly
def parse_meeting(
    scan: DocumentScan, rel_path: str, date_order: str | None = None, file: str | None = None
) -> MeetingParse:
    label = file if file is not None else rel_path
    infos = scan.annotation.lines
    dr = extract_meeting_date(scan.lines, infos, scan.front_matter, rel_path, date_order, label)
    title = extract_title(infos, scan.front_matter, rel_path)
    attendees = extract_attendees(infos, scan.front_matter)
    warnings = list(dr.warnings)
    if not attendees:
        warnings.append(_warn(codes.W_ATTENDEES_MISSING, label, None))
    meta = MeetingMeta(dr.date, dr.source, dr.raw, title, attendees)
    return MeetingParse(meta, tuple(warnings))
