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

Section 2 - extraction (T-M2-09), Section 3 - ``task_text`` (T-M2-19),
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

import re
import unicodedata
from collections.abc import Iterable
from itertools import islice

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
