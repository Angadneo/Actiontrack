"""Action-item text handling for the parser (pure; no I/O).

Public API (grows task by task; keep each section self-contained):

Section 1 - description cleaning (T-M2-10, spec §8.2 / §8.3, G-12)
    ``clean_description(text) -> str``
        Full cleaned NFC text of one item: inline markup removed, whitespace
        collapsed.  The owner phrase, due phrase, separators and trailing
        sentence punctuation all stay (that is ``task_text``'s job, T-M2-19).
    ``clean_item_lines(lines) -> str``
        Joins the item's content pieces (first line after the checkbox marker
        plus indented continuation lines) with single spaces *before* cleaning,
        so markup spanning two lines is still recognised.

Section 2 - extraction (T-M2-09, spec §8.2, §7.2, FR-01.3/01.4/01.6)
    ``extract_items(scan, file="", task_text_fn=None) -> ItemScan``
        Builds ``ParsedItem`` objects (document order) from a ``DocumentScan``:
        only importable checkboxes (``[ ]``, ``[]``, ``[x]``/``[X]`` with text) on
        ``normal`` lines, at any nesting depth.  Multiline continuation = following
        non-blank ``normal`` lines that are not list items / headings / table rows
        and are indented deeper than the item's marker.  ``raw_text`` is the
        verbatim original lines joined with ``\\n`` (never normalised);
        ``block_hash`` = SHA-256 hex of ``raw_text`` (UTF-8); ``occurrence_index``
        = zero-based rank among items of the file with equal hash;
        ``section_heading`` = nearest preceding ATX heading (markup stripped) or
        None.  ``ItemScan.warnings`` holds the NOSPACE / NONSTANDARD / EMPTY
        checkbox warnings for ``normal`` lines (ignored regions are reported by
        ``markdown.ignored_region_warnings``).  ``task_text_fn`` maps a
        description to its ``task_text`` (default: first-letter rule only, i.e. no
        spans; T-M2-15 passes ``make_task_text_fn(...)``).  The owner/due hooks
        stay ``None`` (filled by T-M2-15).

Section 3 - ``task_text`` (T-M2-19, spec §8.2, G-12, J-01, AC-01.19)
    ``derive_task_text(description, spans) -> str``                (pure)
    ``due_spans(description, date_order=None) -> list[TextSpan]``  (via ``dates.locate``)
    ``OwnerSpanProvider`` protocol / ``NullOwnerSpans`` default    (M4 supplies the real one)
    ``collect_spans`` / ``compute_task_text`` / ``make_task_text_fn`` glue.
    Capitalisation rule (task text is silent beyond "upper-case only the first
    character"): after deletion the FIRST character of the result is upper-cased
    with ``str.upper``; every other character is untouched.

Section 4 - assembly (T-M2-15), Section 5 - analyzer adapter (T-M3-09):
    added by later tasks.

Design notes for section 1: everything is a single left-to-right scan or a
linear-time regex (no nested quantifiers), so pathological one-million-character
lines stay fast (T-M2-18).  Markup that cannot be matched is left as written
(conservative: never discard text we do not understand).  Code spans and
backslash escapes are first swapped for private-use sentinels so that later
stages cannot see markup inside them; the sentinels are swapped back before
whitespace collapsing.  Control characters are NOT touched here (output
sanitising is the renderers' job, spec SEC-08).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import islice
from typing import Protocol

from actiontrack.domain import dates as _dates
from actiontrack.domain.models import ParsedItem, ParseWarning, TextSpan
from actiontrack.parsing import markdown as _md

# --------------------------------------------------------------------------
# Section 1 - description cleaning
# --------------------------------------------------------------------------

_SENT_OPEN = ""
_SENT_CLOSE = ""
_SENT_RE = re.compile(_SENT_OPEN + r"(\d+)" + _SENT_CLOSE)

_ASCII_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

_AUTOLINK_RE = re.compile(r"<([A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\s<>]*)>")
_EMAIL_AUTOLINK_RE = re.compile(r"<([^\s<>@]+@[^\s<>@]+)>")
# Only well-known inline HTML tags are stripped; placeholders such as
# ``<date>`` are user text and are preserved.
_HTML_TAG_RE = re.compile(
    r"</?(?:a|abbr|b|big|br|cite|code|del|em|i|ins|kbd|mark|q|s|small|span|"
    r"strike|strong|sub|sup|tt|u)(?:\s[^<>]*)?/?>",
    re.IGNORECASE,
)
_MAX_EMPH_RUNS = 20_000  # more delimiter runs than any real item has
_EMPH_RUN_RE = re.compile(r"\*+|_+|~+")
_ESC_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_TICK_OR_ESC_RE = re.compile(r"\\[!-/:-@\[-`{-~]|`+")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _stash(store: list[str], value: str) -> str:
    store.append(value)
    return f"{_SENT_OPEN}{len(store) - 1}{_SENT_CLOSE}"


def _protect(text: str, store: list[str]) -> str:
    """Replace code spans and backslash escapes by sentinels."""
    if "\\" not in text and "`" not in text:
        return text
    # Backtick runs, skipping escaped characters.
    runs: list[tuple[int, int]] = [
        (m.start(), m.end() - m.start()) for m in _TICK_OR_ESC_RE.finditer(text) if m.group()[0] == "`"
    ]
    nxt: list[int] = [-1] * len(runs)
    last_seen: dict[int, int] = {}
    for k in range(len(runs) - 1, -1, -1):
        nxt[k] = last_seen.get(runs[k][1], -1)
        last_seen[runs[k][1]] = k
    code: list[tuple[int, int, int, int]] = []  # run_start, content_start, content_end, run_end
    k = 0
    while k < len(runs):
        if nxt[k] != -1:
            s, ln = runs[k]
            cs, cl = runs[nxt[k]]
            code.append((s, s + ln, cs, cs + cl))
            k = nxt[k] + 1
        else:
            k += 1

    out: list[str] = []
    pos = 0

    def plain(seg: str) -> str:
        return _ESC_RE.sub(lambda m: _stash(store, m.group(1)), seg)

    for run_start, c_start, c_end, run_end in code:
        out.append(plain(text[pos:run_start]))
        content = text[c_start:c_end]
        content = content.replace("\r", " ").replace("\n", " ")
        if len(content) > 2 and content[0] == " " and content[-1] == " " and content.strip():
            content = content[1:-1]
        out.append(_stash(store, content))
        pos = run_end
    out.append(plain(text[pos:]))
    return "".join(out)


def _remove_comments(text: str) -> str:
    """Drop closed ``<!-- ... -->`` spans; an unclosed one is left as written."""
    if "<!--" not in text:
        return text
    out: list[str] = []
    pos = 0
    while True:
        s = text.find("<!--", pos)
        if s == -1:
            break
        e = text.find("-->", s + 4)
        if e == -1:
            break
        out.append(text[pos:s])
        pos = e + 3
    out.append(text[pos:])
    return "".join(out)


def _scan_dest(text: str, i: int) -> int:
    """End index (exclusive) of an inline ``(dest "title")`` starting at ``text[i] == '('``; -1 if none."""
    n = len(text)
    j = i + 1
    depth = 0
    while j < n:
        c = text[j]
        if c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                return j + 1
            depth -= 1
        elif c in "[]" or c.isspace():
            break
        j += 1
    # Optional title after whitespace: (url "title")
    if j < n and text[j].isspace():
        while j < n and text[j].isspace():
            j += 1
        if j < n and text[j] in "\"'":
            q = text[j]
            e = text.find(q, j + 1, j + 2001)
            if e != -1:
                e += 1
                while e < n and text[e].isspace():
                    e += 1
                if e < n and text[e] == ")":
                    return e + 1
        elif j < n and text[j] == ")":
            return j + 1
    return -1


def _scan_ref(text: str, i: int) -> int:
    """End index of ``[ref]`` / ``[]`` starting at ``text[i] == '['``; -1 if none."""
    e = text.find("]", i + 1, i + 202)
    if e == -1 or "[" in text[i + 1 : e]:
        return -1
    return e + 1


def _strip_links(text: str) -> str:
    """``[t](u)`` / ``[t][r]`` -> ``t``; ``![a](u)`` -> ``a``; unmatched brackets stay."""
    if "[" not in text:
        return text
    n = len(text)
    drop: set[int] = set()  # indices removed outright
    skip: dict[int, int] = {}  # index of ']' -> index after the tail
    stack: list[int] = []
    i = 0
    while i < n:
        c = text[i]
        if c == "[":
            stack.append(i)
        elif c == "]" and stack:
            opener = stack.pop()
            end = -1
            if i + 1 < n:
                if text[i + 1] == "(":
                    end = _scan_dest(text, i + 1)
                elif text[i + 1] == "[":
                    end = _scan_ref(text, i + 1)
            if end != -1:
                drop.add(opener)
                if opener > 0 and text[opener - 1] == "!":
                    drop.add(opener - 1)
                skip[i] = end
                i = end
                continue
        i += 1
    if not drop:
        return text
    out: list[str] = []
    i = 0
    while i < n:
        if i in drop:
            i += 1
        elif i in skip:
            i = skip[i]
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _strip_emphasis(text: str) -> str:
    """Remove ``**``/``__``/``*``/``_``/``~~`` pairs under simplified flanking rules."""
    if not any(ch in text for ch in "*_~"):
        return text
    n = len(text)
    spans = list(islice(_EMPH_RUN_RE.finditer(text), _MAX_EMPH_RUNS + 1))
    if len(spans) > _MAX_EMPH_RUNS:
        return text  # pathological input: leave markers as written (keeps parsing fast)
    runs = [(m.group()[0], m.start(), m.end() - m.start()) for m in spans]
    # Per-run bookkeeping: chars taken from an opener's tail / a closer's head.
    open_stacks: dict[str, list[int]] = {"*": [], "_": [], "~": []}
    rem_open: dict[int, int] = {}
    take_head: dict[int, int] = {}
    take_tail: dict[int, int] = {}
    for idx, (c, start, ln) in enumerate(runs):
        if c == "~" and ln != 2:
            continue
        end = start + ln
        prev = text[start - 1] if start else " "
        nxt = text[end] if end < n else " "
        can_open = not nxt.isspace()
        can_close = not prev.isspace()
        if c == "_":
            if prev.isalnum():
                can_open = False
            if nxt.isalnum():
                can_close = False
        stack = open_stacks[c]
        if can_close and stack:
            o = stack[-1]
            avail = rem_open[o]
            k = min(avail, ln) if c != "~" else 2
            if k > 0:
                take_tail[o] = take_tail.get(o, 0) + k
                take_head[idx] = k
                rem_open[o] = avail - k
                if rem_open[o] == 0:
                    stack.pop()
                if ln - k > 0 and can_open:
                    stack.append(idx)
                    rem_open[idx] = ln - k
                continue
        if can_open:
            stack.append(idx)
            rem_open[idx] = ln
    if not take_head:
        return text
    out: list[str] = []
    pos = 0
    for idx, (c, start, ln) in enumerate(runs):
        if idx in take_head:
            out.append(text[pos:start])
            pos = start + take_head[idx]
        if idx in take_tail:
            end = start + ln
            out.append(text[pos : end - take_tail[idx]])
            pos = end
    out.append(text[pos:])
    return "".join(out)


def clean_description(text: str) -> str:
    """Return the full cleaned NFC description for item text ``text``.

    Strips emphasis/strike/code markers, ``[t](u)``/``[t][r]`` links (-> ``t``),
    images (-> alt), autolinks (-> URL), inline HTML comments and common inline
    HTML tags, and unescapes ``\\*`` style escapes; then collapses whitespace
    to single spaces and trims.  Owner/due phrases and punctuation are kept.
    Linear time; unmatched markup is left as written.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace(_SENT_OPEN, "").replace(_SENT_CLOSE, "")
    store: list[str] = []
    text = _protect(text, store)
    text = _remove_comments(text)
    text = _BR_RE.sub(" ", text)
    text = _AUTOLINK_RE.sub(lambda m: m.group(1), text)
    text = _EMAIL_AUTOLINK_RE.sub(lambda m: m.group(1), text)
    text = _HTML_TAG_RE.sub("", text)
    text = _strip_links(text)
    text = _strip_emphasis(text)
    text = _SENT_RE.sub(lambda m: store[int(m.group(1))], text)
    text = " ".join(text.split())
    return unicodedata.normalize("NFC", text)


def clean_item_lines(lines: Iterable[str]) -> str:
    """Join an item's content pieces with single spaces, then clean them.

    ``lines`` are the text after the checkbox marker on the first line plus the
    indented continuation lines (T-M2-09); joining happens before cleaning.
    """
    return clean_description(" ".join(piece.strip() for piece in lines))


# --------------------------------------------------------------------------
# Section 2 - item extraction (T-M2-09)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ItemScan:
    """Result of ``extract_items``: items in document order plus checkbox warnings."""

    items: tuple[ParsedItem, ...]
    warnings: tuple[ParseWarning, ...]


def block_hash(raw_text: str) -> str:
    """SHA-256 hex digest of the verbatim item text (UTF-8, line endings already ``\\n``)."""
    return hashlib.sha256(raw_text.encode("utf-8", errors="surrogatepass")).hexdigest()


def _default_task_text(description: str) -> str:
    return derive_task_text(description, ())


def extract_items(
    scan: _md.DocumentScan,
    file: str = "",
    task_text_fn: Callable[[str], str] | None = None,
) -> ItemScan:
    """Extract the checkbox action items of one scanned document (see module docstring)."""
    to_task_text = task_text_fn or _default_task_text
    infos = scan.annotation.lines
    n = len(infos)
    items: list[ParsedItem] = []
    warnings: list[ParseWarning] = []
    heading: str | None = None
    i = 0
    while i < n:
        info = infos[i]
        if info.kind != _md.KIND_NORMAL:
            i += 1
            continue
        vis = info.visible_text
        h = _md.heading_text(vis)
        if h is not None:
            heading = clean_description(h) or None
            i += 1
            continue
        ll = _md.classify_list_line(vis)
        if ll is None:
            i += 1
            continue
        warning = _md.list_line_warning(ll, info.text, i + 1, file)
        if warning is not None:
            warnings.append(warning)
        if not ll.importable:
            i += 1
            continue
        pieces = [ll.rest]
        last = i
        j = i + 1
        while j < n:
            nxt = infos[j]
            if nxt.kind != _md.KIND_NORMAL:
                break
            text = nxt.visible_text
            if not text.strip():
                break
            if _md.line_indent_width(text) <= ll.indent_width:
                break
            if (
                _md.classify_list_line(text) is not None
                or _md.heading_text(text) is not None
                or text.lstrip().startswith("|")
            ):
                break
            pieces.append(text)
            last = j
            j += 1
        raw_text = "\n".join(scan.lines[i : last + 1])
        description = clean_item_lines(pieces)
        items.append(
            ParsedItem(
                line_start=i + 1,
                line_end=last + 1,
                raw_text=raw_text,
                description=description,
                task_text=to_task_text(description),
                checkbox_state="done" if ll.kind == _md.LIST_CHECKBOX_DONE else "open",
                section_heading=heading,
                occurrence_index=0,  # ranked below
                block_hash=block_hash(raw_text),
            )
        )
        i = last + 1
    seen: Counter[str] = Counter()
    ranked: list[ParsedItem] = []
    for item in items:
        ranked.append(_with_occurrence(item, seen[item.block_hash]))
        seen[item.block_hash] += 1
    return ItemScan(tuple(ranked), tuple(warnings))


def _with_occurrence(item: ParsedItem, index: int) -> ParsedItem:
    if index == 0:
        return item
    return ParsedItem(
        item.line_start,
        item.line_end,
        item.raw_text,
        item.description,
        item.task_text,
        item.checkbox_state,
        item.section_heading,
        index,
        item.block_hash,
        item.owner_hook,
        item.due_hook,
    )


# --------------------------------------------------------------------------
# Section 3 - task_text derivation (T-M2-19)
# --------------------------------------------------------------------------

_SEPARATORS = " \t:-–—,"  # whitespace plus the spec's orphaned separators
_LEFT_RUN = _SEPARATORS + ";"  # runs eaten on the left of a removed span
_RIGHT_RUN = _SEPARATORS + ".;"  # runs eaten on the right (sentence-final . ; included, J-01)
_SEP_CHARS = ":-–—,"
_PAIRS = {"(": ")", "[": "]"}


class OwnerSpanProvider(Protocol):
    """Locates owner / co-owner phrases in a cleaned description (M4 implements the real one).

    Returned spans are half-open offsets into the *description* and include the trailing
    `` to `` / ``(with Other)`` parts that belong to the owner clause (spec §10.1).
    """

    def owner_spans(self, description: str) -> Sequence[TextSpan]: ...


class NullOwnerSpans:
    """Default provider: no owner spans (until M4's owner-phrase locator is wired in)."""

    def owner_spans(self, description: str) -> Sequence[TextSpan]:
        return ()


def due_spans(description: str, date_order: str | None = None) -> list[TextSpan]:
    """Spans of every located date phrase: introducer, separator after it, phrase (incl. time words).

    Includes ``not_due`` phrases such as ``from 9 Oct`` (spec §8.2).
    """
    out: list[TextSpan] = []
    for found in _dates.locate(description, date_order):
        if found.introducer_span is not None:
            out.append(found.introducer_span)
        if found.separator_span is not None:
            out.append(found.separator_span)
        out.append(TextSpan(found.start, found.end))
    return out


def collect_spans(
    description: str, owner_spans: OwnerSpanProvider | None = None, date_order: str | None = None
) -> list[TextSpan]:
    """Owner spans (from the provider) plus due/date spans for ``description``."""
    provider = owner_spans if owner_spans is not None else NullOwnerSpans()
    return [*provider.owner_spans(description), *due_spans(description, date_order)]


def compute_task_text(
    description: str, owner_spans: OwnerSpanProvider | None = None, date_order: str | None = None
) -> str:
    """``derive_task_text`` over the owner and due spans of ``description``."""
    return derive_task_text(description, collect_spans(description, owner_spans, date_order))


def make_task_text_fn(
    owner_spans: OwnerSpanProvider | None = None, date_order: str | None = None
) -> Callable[[str], str]:
    """Callable ``description -> task_text`` for ``extract_items(task_text_fn=...)``."""

    def fn(description: str) -> str:
        return compute_task_text(description, owner_spans, date_order)

    return fn


def _merge_spans(text: str, spans: Iterable[TextSpan]) -> list[tuple[int, int]]:
    """Clamp, sort and merge spans; spans separated only by separators/whitespace merge too."""
    n = len(text)
    ivs = sorted(
        (max(0, min(s.start, n)), max(0, min(s.end, n))) for s in spans if min(s.end, n) > max(s.start, 0)
    )
    merged: list[tuple[int, int]] = []
    for s, e in ivs:
        if merged and (s <= merged[-1][1] or all(c in _SEPARATORS for c in text[merged[-1][1] : s])):
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def derive_task_text(description: str, spans: Iterable[TextSpan] = ()) -> str:
    """Display text of an item: ``description`` minus owner/due ``spans`` (spec §8.2, AC-01.19).

    ``spans`` are half-open offsets into ``description``.  They are merged and deleted, then
    orphaned separators (``-``, en/em dash, ``,``, ``:``) next to a deleted span are removed
    (doubled ones collapse to one), a sentence-final ``.``/``;`` adjacent to a deleted span is
    dropped (J-01; a ``.`` ending a text with no adjacent deleted span stays), empty or
    unmatched brackets left by ``(due: ...)`` / ``(by Friday)`` are cleaned, whitespace is
    collapsed, ONLY the first character is upper-cased and the result is NFC.  An empty
    result falls back to ``description``.  Pure and deterministic.
    """
    text = description
    n = len(text)
    out: list[str] = []
    emitted = False  # any non-blank text emitted so far
    paren = bracket = 0  # open-bracket balance of the emitted text
    cursor = 0
    merged = _merge_spans(text, spans)
    for idx, (s, e) in enumerate(merged):
        next_limit = merged[idx + 1][0] if idx + 1 < len(merged) else n
        ls, re_end = s, e
        while True:
            while ls > cursor and text[ls - 1] in _LEFT_RUN:
                ls -= 1
            start_mode = not emitted and not text[cursor:ls].strip()
            run = _SEPARATORS if start_mode else _RIGHT_RUN
            while re_end < next_limit and text[re_end] in run:
                re_end += 1
            if ls > cursor and re_end < next_limit and text[ls - 1] in _PAIRS and text[re_end] == _PAIRS[text[ls - 1]]:
                ls -= 1
                re_end += 1  # empty brackets: "(by Friday)" -> nothing
                continue
            if re_end < next_limit and text[re_end] in ")]":
                left = text[cursor:ls]
                opener, closer = ("(", ")") if text[re_end] == ")" else ("[", "]")
                balance = (paren if opener == "(" else bracket) + left.count(opener) - left.count(closer)
                if balance <= 0:
                    re_end += 1  # orphan closing bracket left by "(due: ...)"
                    continue
            break
        left = text[cursor:ls]
        out.append(left)
        emitted = emitted or bool(left.strip())
        paren += left.count("(") - left.count(")")
        bracket += left.count("[") - left.count("]")
        cursor = re_end
        if re_end >= n:
            break  # everything to the end was removed
        if start_mode:
            continue
        left_run = "".join(c for c in text[ls:s] if c not in "([")
        right_run = "".join(c for c in text[e:re_end] if c not in ")]").lstrip(" \t")
        if right_run[:1] in (".", ";"):
            out.append(right_run[0] + " ")  # sentence punctuation moves next to the kept text
        else:
            sep = next((c for c in right_run if c in _SEP_CHARS), None) or next(
                (c for c in left_run if c in _SEP_CHARS + ";"), None
            )
            out.append(sep + " " if sep else " ")
    out.append(text[cursor:])
    result = " ".join("".join(out).split())
    if not result:
        return description
    result = result[0].upper() + result[1:]
    return unicodedata.normalize("NFC", result)
