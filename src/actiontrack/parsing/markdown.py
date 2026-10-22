"""Markdown structure scanning: front matter, ignored regions, list/checkbox lines, candidates.

Pure functions of text (no I/O, no clock). Lines are the 0-based list returned by
``reader.split_lines``; every "line number" in a result is 1-based and counts original file lines
(front matter included, BOM is not a line).

Public API (written against by item extraction, meeting metadata and assembly)
-----------------------------------------------------------------------------
Warnings carry a ``file`` label via the ``file=`` keyword of each function (default ``"<file>"``,
used only in message text). Structured ``detail`` payloads are JSON strings
(``json.loads(w.detail)``).

Front matter (T-M2-06)
    ``detect_front_matter(lines, file="") -> (FrontMatter | None, tuple[ParseWarning, ...])``
    ``FrontMatter(end_line, fields, key_lines)``; ``end_line`` is the 1-based closing ``---``/``...``
    line (region = lines 1..end_line); ``fields`` maps lower-cased keys to ``str`` or ``list[str]``
    (no coercion); ``key_lines`` maps each key to its 1-based line. Absent front matter returns
    ``None`` with no warning when line 1 is not ``---``. Unclosed or unsupported content returns
    ``None`` and one ``W-FRONTMATTER-INVALID`` at line 1; callers then parse the whole file as body.
    Built-in restricted YAML subset (plain/quoted scalars, ``[A, B]`` flow lists, ``- item`` block
    lists, blank lines, ``#`` comments, duplicate key = last wins, ``key:`` alone = empty string).
    Anchors/aliases, tags, nested maps, multi-line scalars, flow maps, ``: `` in plain scalars,
    unbalanced ``[``/quotes and any other line make the block invalid. A block-list item of the
    form ``[ ] text`` / ``[x] text`` is accepted as plain text (it stays inside the region).

Regions (T-M2-07)
    ``annotate(lines, front_matter_end=0, file="") -> Annotation(lines, warnings)``
    ``LineInfo(kind, visible_text, text)``: ``kind`` in ``KINDS`` = normal | front_matter | fence |
    comment | quote; ``text`` is the verbatim line; ``visible_text`` is the line with HTML-comment
    spans blanked by spaces (column preserving) for ``normal`` lines and ``""`` for every other
    kind. A line is ``comment`` only when nothing outside comment spans is left on it. Precedence:
    front matter > fence > comment > quote. Warnings: ``W-FENCE-UNCLOSED`` / ``W-HTML-COMMENT-UNCLOSED``
    at the opener line.
    ``scan_document(lines, file="") -> DocumentScan(lines, front_matter, annotation, warnings)`` runs
    front matter detection and annotation; ``warnings`` merges both (front matter first).

List lines (T-M2-08)
    ``classify_list_line(text) -> ListLine | None`` and ``classify_info(info) -> ListLine | None``
    (``normal`` lines only, uses ``visible_text``). ``ListLine(kind, indent_width, marker, box_raw,
    rest, rest_start, marker_col, nonstandard_char, warning_codes)``; ``kind`` in ``LIST_KINDS`` =
    checkbox_open | checkbox_done | checkbox_nonstandard | checkbox_empty | plain_bullet.
    ``rest`` is the text after the box (or after the marker for a plain bullet) with leading blanks
    removed; ``rest_start`` its column in the classified text; ``warning_codes`` holds the codes
    this line raises (``W-CHECKBOX-NOSPACE`` for ``[]``, ``-NONSTANDARD``, ``-EMPTY``).
    Only ``checkbox_open`` / ``checkbox_done`` are importable (``ListLine.importable``).
    ``list_line_warning(ll, text, line, file)`` builds the ``ParseWarning`` (detail = the raw line).
    A box directly followed by ``(`` or ``:`` is a link / reference, hence an ordinary bullet.

Ignored regions (T-M2-11) and candidates (T-M2-14)
    ``ignored_region_warnings(annotation, file="") -> list[ParseWarning]``
        ``W-CHECKBOX-IGNORED-REGION`` per checkbox line in a non-normal region; ``detail`` =
        JSON ``{"region", "raw_text"}``.
    ``find_candidates(annotation, file="") -> CandidateScan(candidates, warnings)``
        ``NonCheckboxCandidate(kind, line, text)`` with kind ``action_line`` | ``table_row`` |
        ``plain_bullet``; one ``W-NONCHECKBOX-CANDIDATES`` per candidate, ``detail`` = JSON
        ``{"kind", "text"}``. Tables are recognised only when rows start with ``|``.
    ``heading_text(visible_text) -> str | None``  ATX heading text (for ``section_heading``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from actiontrack import codes
from actiontrack.domain.models import NonCheckboxCandidate, ParseWarning, parse_message

KIND_NORMAL = "normal"
KIND_FRONT_MATTER = "front_matter"
KIND_FENCE = "fence"
KIND_COMMENT = "comment"
KIND_QUOTE = "quote"
KINDS: tuple[str, ...] = (KIND_NORMAL, KIND_FRONT_MATTER, KIND_FENCE, KIND_COMMENT, KIND_QUOTE)

LIST_CHECKBOX_OPEN = "checkbox_open"
LIST_CHECKBOX_DONE = "checkbox_done"
LIST_CHECKBOX_NONSTANDARD = "checkbox_nonstandard"
LIST_CHECKBOX_EMPTY = "checkbox_empty"
LIST_PLAIN_BULLET = "plain_bullet"
LIST_KINDS: tuple[str, ...] = (
    LIST_CHECKBOX_OPEN,
    LIST_CHECKBOX_DONE,
    LIST_CHECKBOX_NONSTANDARD,
    LIST_CHECKBOX_EMPTY,
    LIST_PLAIN_BULLET,
)

CAND_ACTION_LINE = "action_line"
CAND_TABLE_ROW = "table_row"
CAND_PLAIN_BULLET = "plain_bullet"

_NAME = "<file>"


def _json(**kw: str) -> str:
    return json.dumps(kw, ensure_ascii=False)


# ============================================================================ front matter
@dataclass(frozen=True, slots=True, eq=True)
class FrontMatter:
    end_line: int
    fields: dict[str, str | list[str]]
    key_lines: dict[str, int] = field(default_factory=dict)


class _Invalid(Exception):
    """The block uses syntax outside the supported YAML subset."""


_FM_KEY_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_ .\-]*?)[ \t]*:(?:[ \t]+(.*))?")
_FM_ITEM_RE = re.compile(r"[ \t]*-(?:[ \t]+(.*))?")
_FM_BOX_RE = re.compile(r"\[[ xX]?\](?=\s|$)")
_COMMENT_START_RE = re.compile(r"\s#")


def _read_quoted(text: str, start: int) -> tuple[str, int]:
    """Read a quoted scalar starting at ``text[start]``; return (value, index after the quote)."""
    quote = text[start]
    out: list[str] = []
    i = start + 1
    while i < len(text):
        ch = text[i]
        if quote == '"' and ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        if ch == quote:
            if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise _Invalid("unterminated quote")


def _only_comment_after(rest: str) -> None:
    if rest and not (rest[0].isspace() and (rest.strip() == "" or rest.strip().startswith("#"))):
        raise _Invalid("text after quoted scalar")


def _plain_scalar(value: str) -> str:
    m = _COMMENT_START_RE.search(value)
    if m:
        value = value[: m.start()]
    value = value.strip()
    if value.startswith("#") or not value:
        return ""
    if value[0] in "&*!|>{%@`":
        raise _Invalid("unsupported indicator")
    if ": " in value or value.endswith(":"):
        raise _Invalid("':' inside plain scalar (nested map)")
    if "{" in value and value.count("{") != value.count("}"):
        raise _Invalid("unbalanced brace")
    if value.count("[") != value.count("]"):
        raise _Invalid("unbalanced bracket")
    return value


def _scalar(value: str) -> str:
    value = value.strip()
    if value[:1] in ('"', "'"):
        text, end = _read_quoted(value, 0)
        _only_comment_after(value[end:])
        return text
    return _plain_scalar(value)


def _flow_list(value: str) -> list[str]:
    items: list[str] = []
    i = 1
    n = len(value)
    while True:
        while i < n and value[i].isspace():
            i += 1
        if i >= n:
            raise _Invalid("unclosed flow list")
        if value[i] == "]":
            i += 1
            break
        if value[i] in ('"', "'"):
            item, i = _read_quoted(value, i)
        else:
            j = i
            while j < n and value[j] not in ",]":
                j += 1
            if j >= n:
                raise _Invalid("unclosed flow list")
            raw = value[i:j].strip()
            if not raw or raw[0] in "&*!|>{[" or ": " in raw or "[" in raw or "{" in raw:
                raise _Invalid("unsupported flow item")
            item, i = raw, j
        items.append(item)
        while i < n and value[i].isspace():
            i += 1
        if i >= n:
            raise _Invalid("unclosed flow list")
        if value[i] == ",":
            i += 1
        elif value[i] != "]":
            raise _Invalid("unexpected character in flow list")
    _only_comment_after(value[i:])
    return items


def _value(raw: str) -> str | list[str]:
    value = raw.strip()
    if value.startswith("["):
        return _flow_list(value)
    return _scalar(value)


def _list_item(raw: str) -> str:
    item = raw.strip()
    if item[:1] in ('"', "'"):
        text, end = _read_quoted(item, 0)
        _only_comment_after(item[end:])
        return text
    m = _FM_BOX_RE.match(item)
    if m:
        body = _plain_scalar(item[m.end():])
        return f"{item[: m.end()]} {body}" if body else item[: m.end()]
    if item.startswith("["):
        raise _Invalid("nested flow list in block list")
    if not item or item.startswith("#"):
        raise _Invalid("empty list item")
    return _plain_scalar(item)


def _parse_block(block: Sequence[str], first_line: int) -> tuple[dict[str, str | list[str]], dict[str, int]]:
    fields: dict[str, str | list[str]] = {}
    key_lines: dict[str, int] = {}
    list_key: str | None = None  # key whose value is empty so far and may take block-list items
    for offset, line in enumerate(block):
        lineno = first_line + offset
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = _FM_ITEM_RE.fullmatch(line.rstrip())
        if item is not None:
            if list_key is None:
                raise _Invalid("list item without a key")
            current = fields[list_key]
            if not isinstance(current, list):
                current = []
                fields[list_key] = current
            current.append(_list_item(item.group(1) or ""))
            continue
        if line[0] in " \t":
            raise _Invalid("indented content (nested map or multi-line scalar)")
        m = _FM_KEY_RE.fullmatch(line.rstrip())
        if m is None:
            raise _Invalid("not a key: value line")
        key = m.group(1).strip().lower()
        raw = (m.group(2) or "").strip()
        if raw.startswith("#"):
            raw = ""
        value = _value(raw) if raw else ""
        fields[key] = value
        key_lines[key] = lineno
        list_key = key if value == "" else None
    return fields, key_lines


def detect_front_matter(
    lines: Sequence[str], file: str = ""
) -> tuple[FrontMatter | None, tuple[ParseWarning, ...]]:
    """Detect and parse front matter (see module docstring)."""
    if not lines or lines[0].lstrip("﻿").rstrip(" \t") != "---":
        return None, ()
    name = file or _NAME
    invalid = (
        ParseWarning(codes.W_FRONTMATTER_INVALID, 1, parse_message(codes.W_FRONTMATTER_INVALID, name, 1)),
    )
    close = None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip(" \t") in ("---", "..."):
            close = idx
            break
    if close is None:
        return None, invalid
    try:
        fields, key_lines = _parse_block(lines[1:close], 2)
    except _Invalid:
        return None, invalid
    return FrontMatter(close + 1, fields, key_lines), ()


# ================================================================================== regions
@dataclass(frozen=True, slots=True)
class LineInfo:
    kind: str
    visible_text: str
    text: str


@dataclass(frozen=True, slots=True)
class Annotation:
    lines: tuple[LineInfo, ...]
    warnings: tuple[ParseWarning, ...]


_FENCE_OPEN_RE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)")
_QUOTE_RE = re.compile(r" {0,3}>")


def _fence_opener(line: str) -> tuple[str, int] | None:
    m = _FENCE_OPEN_RE.fullmatch(line)
    if m is None:
        return None
    marker = m.group(1)
    if marker[0] == "`" and "`" in m.group(2):
        return None
    return marker[0], len(marker)


def _is_fence_close(line: str, char: str, length: int) -> bool:
    s = line.strip(" \t")
    return len(s) >= length and s == char * len(s)


def _scan_comments(line: str, in_comment: bool) -> tuple[str, bool, bool]:
    """Blank comment spans. Returns (visible_text, had_comment_content, still_open)."""
    out: list[str] = []
    pos = 0
    had = False
    if in_comment:
        had = True
        j = line.find("-->")
        if j < 0:
            return " " * len(line), True, True
        out.append(" " * (j + 3))
        pos = j + 3
    while True:
        i = line.find("<!--", pos)
        if i < 0:
            out.append(line[pos:])
            return "".join(out), had, False
        out.append(line[pos:i])
        had = True
        j = line.find("-->", i + 4)
        if j < 0:
            out.append(" " * (len(line) - i))
            return "".join(out), True, True
        out.append(" " * (j + 3 - i))
        pos = j + 3


def annotate(lines: Sequence[str], front_matter_end: int = 0, file: str = "") -> Annotation:
    """Classify every line (see module docstring)."""
    name = file or _NAME
    infos: list[LineInfo] = []
    fence: tuple[str, int, int] | None = None  # (char, length, open_line)
    comment_open: int | None = None
    for idx, line in enumerate(lines):
        number = idx + 1
        if number <= front_matter_end:
            infos.append(LineInfo(KIND_FRONT_MATTER, "", line))
            continue
        if fence is not None:
            infos.append(LineInfo(KIND_FENCE, "", line))
            if _is_fence_close(line, fence[0], fence[1]):
                fence = None
            continue
        if comment_open is not None:
            visible, _had, still_open = _scan_comments(line, True)
            if "-->" in line:  # the running comment closed here (it may reopen on the same line)
                comment_open = number if still_open else None
        elif _QUOTE_RE.match(line):
            infos.append(LineInfo(KIND_QUOTE, "", line))
            continue
        else:
            opener = _fence_opener(line)
            if opener is not None:
                fence = (opener[0], opener[1], number)
                infos.append(LineInfo(KIND_FENCE, "", line))
                continue
            visible, _had, still_open = _scan_comments(line, False)
            if still_open:
                comment_open = number
        kind = KIND_COMMENT if not visible.strip() and (_had or comment_open is not None) else KIND_NORMAL
        infos.append(LineInfo(kind, visible if kind == KIND_NORMAL else "", line))
    warnings: list[ParseWarning] = []
    if fence is not None:
        warnings.append(
            ParseWarning(codes.W_FENCE_UNCLOSED, fence[2], parse_message(codes.W_FENCE_UNCLOSED, name, fence[2]))
        )
    if comment_open is not None:
        warnings.append(
            ParseWarning(
                codes.W_HTML_COMMENT_UNCLOSED,
                comment_open,
                parse_message(codes.W_HTML_COMMENT_UNCLOSED, name, comment_open),
            )
        )
    return Annotation(tuple(infos), tuple(warnings))


@dataclass(frozen=True, slots=True)
class DocumentScan:
    lines: tuple[str, ...]
    front_matter: FrontMatter | None
    annotation: Annotation
    warnings: tuple[ParseWarning, ...]


def scan_document(lines: Sequence[str], file: str = "") -> DocumentScan:
    """Front matter detection plus region annotation in one call."""
    fm, fm_warnings = detect_front_matter(lines, file)
    ann = annotate(lines, fm.end_line if fm else 0, file)
    return DocumentScan(tuple(lines), fm, ann, fm_warnings + ann.warnings)


# ================================================================================ list lines
@dataclass(frozen=True, slots=True)
class ListLine:
    kind: str
    indent_width: int
    marker: str
    box_raw: str | None
    rest: str
    rest_start: int
    marker_col: int
    nonstandard_char: str | None = None
    warning_codes: tuple[str, ...] = ()

    @property
    def is_checkbox(self) -> bool:
        return self.kind != LIST_PLAIN_BULLET

    @property
    def importable(self) -> bool:
        return self.kind in (LIST_CHECKBOX_OPEN, LIST_CHECKBOX_DONE)


_LIST_RE = re.compile(r"([ \t]*)(?:([-*+])|(\d{1,9}[.)]))([ \t]+)(.*)")
_BOX_RE = re.compile(r"\[(.?)\]")


def _indent_width(prefix: str) -> int:
    width = 0
    for ch in prefix:
        width = (width // 4 + 1) * 4 if ch == "\t" else width + 1
    return width


def classify_list_line(text: str) -> ListLine | None:
    """Classify one line as a (checkbox) list item, or None (see module docstring)."""
    m = _LIST_RE.fullmatch(text)
    if m is None:
        return None
    prefix, bullet, number, gap, body = m.groups()
    marker = bullet or number
    indent = _indent_width(prefix)
    marker_col = len(prefix)
    body_start = marker_col + len(marker) + len(gap)

    def plain() -> ListLine:
        return ListLine(LIST_PLAIN_BULLET, indent, marker, None, body.lstrip(" \t"), body_start, marker_col)

    box = _BOX_RE.match(body)
    if box is None:
        return plain()
    inner = box.group(1)
    after = body[box.end():]
    if after.startswith(("(", ":")):
        return plain()  # link or reference definition
    rest = after.lstrip(" \t")
    rest_start = body_start + box.end() + (len(after) - len(rest))
    if inner == "":
        kind, warn = LIST_CHECKBOX_OPEN, (codes.W_CHECKBOX_NOSPACE,)
    elif inner == " ":
        kind, warn = LIST_CHECKBOX_OPEN, ()
    elif inner in ("x", "X"):
        kind, warn = LIST_CHECKBOX_DONE, ()
    elif inner.isalnum() or inner.isspace() or inner in "[]":
        return plain()  # letters/digits and the like are ordinary text
    else:  # exactly one other character: not-imported state marker
        if after and not after[0].isspace():
            return plain()
        return ListLine(
            LIST_CHECKBOX_NONSTANDARD,
            indent,
            marker,
            box.group(0),
            rest,
            rest_start,
            marker_col,
            nonstandard_char=inner,
            warning_codes=(codes.W_CHECKBOX_NONSTANDARD,),
        )
    if not rest.strip():
        return ListLine(
            LIST_CHECKBOX_EMPTY, indent, marker, box.group(0), "", rest_start, marker_col,
            warning_codes=(codes.W_CHECKBOX_EMPTY,),
        )
    return ListLine(kind, indent, marker, box.group(0), rest, rest_start, marker_col, warning_codes=warn)


def classify_info(info: LineInfo) -> ListLine | None:
    """Classify a ``normal`` annotated line (uses ``visible_text``); other kinds give None."""
    if info.kind != KIND_NORMAL:
        return None
    return classify_list_line(info.visible_text)


def list_line_warning(ll: ListLine, text: str, line: int, file: str = "") -> ParseWarning | None:
    """The checkbox warning (NOSPACE / NONSTANDARD / EMPTY) for a classified line, if any."""
    if not ll.warning_codes:
        return None
    code = ll.warning_codes[0]
    return ParseWarning(code, line, parse_message(code, file or _NAME, line), detail=text)


# ====================================================================== ignored-region reports
_QUOTE_PEEL_RE = re.compile(r" {0,3}(?:>[ \t]?)+")
_COMMENT_PEEL_RE = re.compile(r"[ \t]*(?:<!--[ \t]*)+")


def _peel(info: LineInfo) -> str:
    text = info.text
    if info.kind == KIND_QUOTE:
        m = _QUOTE_PEEL_RE.match(text)
        return text[m.end():] if m else text
    if info.kind == KIND_COMMENT:
        m = _COMMENT_PEEL_RE.match(text)
        return text[m.end():] if m else text
    return text


def ignored_region_warnings(annotation: Annotation, file: str = "") -> list[ParseWarning]:
    """``W-CHECKBOX-IGNORED-REGION`` for each checkbox line inside an ignored region."""
    name = file or _NAME
    out: list[ParseWarning] = []
    for idx, info in enumerate(annotation.lines):
        if info.kind == KIND_NORMAL:
            continue
        ll = classify_list_line(_peel(info))
        if ll is None or not ll.is_checkbox:
            continue
        out.append(
            ParseWarning(
                codes.W_CHECKBOX_IGNORED_REGION,
                idx + 1,
                parse_message(codes.W_CHECKBOX_IGNORED_REGION, name, idx + 1),
                detail=_json(region=info.kind, raw_text=info.text),
            )
        )
    return out


# ================================================================================ candidates
_HEADING_RE = re.compile(r" {0,3}#{1,6}[ \t]+(.*)")
_ACTION_HEADING_RE = re.compile(r"action|todo|follow[- ]?up|next steps", re.IGNORECASE)
_ACTION_PREFIX_RE = re.compile(r"[ \t]*(?:ACTION|Action|TODO):")
_TABLE_SEP_RE = re.compile(r"\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*")
_ACTION_WORD_RE = re.compile(r"\baction", re.IGNORECASE)
_CLOSING_HASHES_RE = re.compile(r"[ \t]+#+$")


def heading_text(visible_text: str) -> str | None:
    """Text of an ATX heading line (closing ``#`` run removed), else None."""
    m = _HEADING_RE.fullmatch(visible_text)
    if m is None:
        return None
    text = _CLOSING_HASHES_RE.sub("", m.group(1).strip()).strip()
    return "" if set(text) == {"#"} else text


def _is_row(visible: str) -> bool:
    return visible.lstrip().startswith("|")


def _is_separator(visible: str) -> bool:
    return "|" in visible and _TABLE_SEP_RE.fullmatch(visible) is not None


@dataclass(frozen=True, slots=True)
class CandidateScan:
    candidates: tuple[NonCheckboxCandidate, ...]
    warnings: tuple[ParseWarning, ...]


def find_candidates(annotation: Annotation, file: str = "") -> CandidateScan:
    """Non-checkbox action candidates (never items). See module docstring."""
    name = file or _NAME
    infos = annotation.lines
    found: list[NonCheckboxCandidate] = []
    heading: str | None = None
    in_table = False
    action_table = False
    stack: list[tuple[int, bool]] = []  # (indent, is_checkbox) of open list ancestors

    for idx, info in enumerate(infos):
        number = idx + 1
        if info.kind != KIND_NORMAL:
            in_table = False
            continue
        vis = info.visible_text
        if _is_row(vis):
            if in_table:
                if not _is_separator(vis) and action_table:
                    found.append(NonCheckboxCandidate(CAND_TABLE_ROW, number, vis.strip()))
                continue
            nxt = infos[idx + 1] if idx + 1 < len(infos) else None
            if nxt is not None and nxt.kind == KIND_NORMAL and _is_separator(nxt.visible_text):
                in_table = True
                action_table = _ACTION_WORD_RE.search(vis) is not None
                continue
        else:
            in_table = False
        h = heading_text(vis)
        if h is not None:
            heading = h
            stack.clear()
            continue
        if not vis.strip():
            continue
        ll = classify_list_line(vis)
        if ll is not None:
            while stack and stack[-1][0] >= ll.indent_width:
                stack.pop()
            if (
                ll.kind == LIST_PLAIN_BULLET
                and ll.rest.strip()
                and not any(is_cb for _w, is_cb in stack)
                and heading is not None
                and _ACTION_HEADING_RE.search(heading)
            ):
                found.append(NonCheckboxCandidate(CAND_PLAIN_BULLET, number, vis.strip()))
            stack.append((ll.indent_width, ll.is_checkbox))
            continue
        if not vis[0].isspace():
            stack.clear()
        if _ACTION_PREFIX_RE.match(vis) and not stack:
            found.append(NonCheckboxCandidate(CAND_ACTION_LINE, number, vis.strip()))
    warnings = tuple(
        ParseWarning(
            codes.W_NONCHECKBOX_CANDIDATES,
            c.line,
            parse_message(codes.W_NONCHECKBOX_CANDIDATES, name, c.line),
            detail=_json(kind=c.kind, text=c.text),
        )
        for c in found
    )
    return CandidateScan(tuple(found), warnings)
