"""Owner-phrase extraction (pure; spec §10.1 rules 1-6, BR-19, BR-20, G-23; T-M4-03/T-M4-04).

Public API
----------
``extract_owner_phrase(description, is_known_name=None) -> OwnerPhrase | None``
    Locates the owner clause of one *cleaned* item description (spans index it exactly) and
    returns an ``OwnerPhrase`` (``mentions`` in document order, ``rule_id`` 1/2/3,
    ``clause_span``, ``nonperson_raw``, ``with_spans``) or ``None`` (no owner; the ingest layer
    raises ``W-OWNER-MISSING``).  ``extract_owner`` is an alias.
``OwnerPhraseProvider(is_known_name=None)``
    ``OwnerSpanProvider`` for ``make_task_text_fn`` / ``DueAnalyzer``: clause span + ``with`` spans.
``OwnerAnalyzer(is_known_name=None)``
    ``ItemAnalyzer`` = ``DueAnalyzer`` (due + due spans) with ``ItemAnalysis.owner`` set to the
    ``OwnerPhrase`` and ``owner_spans`` set from it.  NOT the default analyzer of ``parse_document``.

``is_known_name(raw) -> bool`` is injected by the caller (the people registry lives in
``resolution/``): true when ``raw`` matches a known person by any §10.3 step.  It makes caseless or
lower-case names (``李雷``) tokens and lets a leading ``Name:`` / ``Name -`` be an owner (rule 1).
Default: nobody is known.

Order (first match wins, G-23): rule 1 (leading ``@name:`` any case; ``Name:`` / ``Name -`` only for
known or ``@`` names, so ``Note:`` / ``Update:`` / ``Blocked -`` / ``Everyone:`` are plain text),
rule 2 (``Name to|will|should|needs to|is going to|can|must <verb>``, at the start of the text or
after a clause boundary such as ``Note: ``), rule 3 (trailing ``(Name)``, ``— Name``,
``[owner: Name]``, ``Owner:|Assignee:|DRI: Name``).  Rules 4-6 work inside the located clause:
lists joined by ``and`` ``&`` ``,`` ``/`` (at most 8 names), ``(with Other)`` (role ``with``, span in
``with_spans``), possessives never owners.

Decisions (documented deviations / defaults)
--------------------------------------------
* ``@anita`` -> mention ``raw="anita"`` (span excludes the ``@``); the clause span includes it.
* ``clause_span`` for rule 2 ends right after the verb phrase (``Priya N. to``); ``task_text``
  derivation eats the surrounding blanks.
* Non-person words (BR-20) -> ``nonperson_raw`` and no mentions.  A list that mixes persons and
  non-person words (``Anita and Team to``) is unassigned as a whole: ``nonperson_raw`` is the list
  text, never a guess.  Lower-case non-person words (``we will``, ``everyone to``) count in rule 2.
* Rule-2 guards against false owners (a capitalised sentence-start word is not a person): after
  ``to`` the next word must start lower-case and not be a determiner (``Upgrade to Python``,
  ``Move to the cloud`` are not owners); ``be`` after any verb (``Proposal will be sent``) is passive;
  a multi-token run led by an imperative verb (``Ask Acme to ...``) is rejected; leading fillers
  (``Also Priya to``) are skipped.  Rule 3 additionally rejects acronyms (``(ASAP)``) unless known.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from actiontrack.domain import rules as _rules
from actiontrack.domain.models import NameMention, OwnerPhrase, OwnerRole, TextSpan
from actiontrack.domain.names import is_possessive
from actiontrack.parsing.items import AnalyzerContext, DueAnalyzer, ItemAnalysis

KnownPredicate = Callable[[str], bool]

MAX_MENTIONS = 8

_VERB_KEYWORDS = frozenset({"to", "should", "can", "must", "needs", "is", "going"})  # "will" may be a name
_FILLERS = frozenset(
    {"also", "then", "and", "but", "plus", "so", "please", "maybe", "finally", "now", "ok", "okay", "thanks"}
)
_IMPERATIVE_LEAD = frozenset(
    {"ask", "tell", "remind", "let", "have", "get", "ping", "email", "call", "invite", "help", "make", "need",
     "want", "require", "allow", "send", "give", "show"}
)  # fmt: skip
_DETERMINERS = frozenset(
    {"the", "a", "an", "this", "that", "these", "those", "our", "my", "your", "their", "his", "her", "its",
     "all", "each", "every", "any", "some", "no", "one", "another", "new", "next", "last"}
)  # fmt: skip

_NEVER_NAME_TOKEN = _VERB_KEYWORDS


def _no_one_known(raw: str) -> bool:
    return False


@dataclass(frozen=True, slots=True)
class _Entry:
    raw: str
    start: int  # of the name text (after any '@')
    end: int
    at: bool

    @property
    def clause_start(self) -> int:
        return self.start - 1 if self.at else self.start


# --------------------------------------------------------------------------
# tokens and names
# --------------------------------------------------------------------------


def _is_mark(ch: str) -> bool:
    return unicodedata.category(ch)[0] == "M"


def _read_token(s: str, i: int) -> int | None:
    """End index of a name-shaped token starting at ``s[i]`` (letters, internal ``'``/``-``, initial dot)."""
    n = len(s)
    if i >= n or not s[i].isalpha():
        return None
    j = i + 1
    while j < n:
        c = s[j]
        if c.isalpha() or _is_mark(c):
            j += 1
        elif c in "'’-" and j + 1 < n and s[j + 1].isalpha():
            j += 1
        else:
            break
    text = s[i:j]
    if j < n and s[j] == "." and (len(text) == 1 or _rules.is_honorific(text)):
        j += 1
    if j < n and (s[j].isdigit() or s[j] == "_"):
        return None
    return j


def _starts_upper(text: str) -> bool:
    return text[:1].isupper() or text[:1].istitle()


def _entry_candidates(
    s: str,
    pos: int,
    known: KnownPredicate,
    *,
    allow_acronym: bool = True,
    lower_nonperson: bool = False,
) -> Iterator[_Entry]:
    """Name entries starting at ``pos`` (optional ``@``), longest first."""
    n = len(s)
    at = pos < n and s[pos] == "@"
    p = pos + 1 if at else pos
    toks: list[tuple[int, int]] = []
    q = p
    while len(toks) < _rules.MAX_NAME_TOKENS:
        r = _read_token(s, q)
        if r is None:
            break
        text = s[q:r]
        low = text.casefold().rstrip(".")
        i = len(toks)
        if _rules.is_not_a_name_word(text) and not (at and i == 0):
            break
        if low in _NEVER_NAME_TOKEN or (low == "will" and not _starts_upper(text)):
            break
        if is_possessive(text) or (r < n and s[r] in "'’"):
            break
        prefix = s[p:r]
        if at and i == 0:
            ok = True
        elif _starts_upper(text):
            ok = True
        elif lower_nonperson and i == 0 and _rules.is_nonperson_word(text):
            ok = True
        else:
            ok = known(text) or known(prefix)
        if not ok:
            break
        if not allow_acronym and len(text) >= 2 and text.isupper() and not _rules.is_nonperson_word(text) and not known(text):
            break
        toks.append((q, r))
        if r + 1 < n and s[r] == " " and s[r + 1].isalpha():
            q = r + 1
        else:
            break
    first_low = s[toks[0][0] : toks[0][1]].casefold() if toks else ""
    for k in range(len(toks), 0, -1):
        if k > 1 and first_low in _IMPERATIVE_LEAD and not known(s[toks[0][0] : toks[0][1]]):
            continue
        start, end = toks[0][0], toks[k - 1][1]
        yield _Entry(s[start:end], start, end, at)


_SEP_RE = re.compile(r"\s*,\s*(?:and\s+|&\s*)?|\s+and\s+|\s*&\s*|\s*/\s*", re.IGNORECASE)


def _lists(
    s: str, pos: int, known: KnownPredicate, depth: int = 1, **kw: bool
) -> Iterator[tuple[list[_Entry], int]]:
    """All ways to read a name list at ``pos``: ``(entries, end)``; shorter lists first."""
    for entry in _entry_candidates(s, pos, known, **kw):
        yield [entry], entry.end
        if depth >= MAX_MENTIONS:
            continue
        m = _SEP_RE.match(s, entry.end)
        if m is None or m.end() == entry.end:
            continue
        for rest, end in _lists(s, m.end(), known, depth + 1, **kw):
            yield [entry, *rest], end


# --------------------------------------------------------------------------
# building the result
# --------------------------------------------------------------------------


def _build(
    s: str,
    rule_id: int,
    entries: Sequence[_Entry],
    clause_start: int,
    clause_end: int,
    with_entries: Sequence[_Entry] = (),
    with_spans: Sequence[TextSpan] = (),
) -> OwnerPhrase:
    clause = TextSpan(clause_start, clause_end)
    persons = [e for e in entries if not _rules.is_nonperson_word(e.raw)]
    if len(persons) != len(entries):
        raw = entries[0].raw if len(entries) == 1 else s[entries[0].start : entries[-1].end]
        return OwnerPhrase((), rule_id, clause, nonperson_raw=raw, with_spans=tuple(with_spans))
    mentions = [NameMention(e.raw, TextSpan(e.start, e.end)) for e in entries]
    mentions += [
        NameMention(e.raw, TextSpan(e.start, e.end), OwnerRole.WITH)
        for e in with_entries
        if not _rules.is_nonperson_word(e.raw)
    ]
    return OwnerPhrase(tuple(mentions), rule_id, clause, with_spans=tuple(with_spans))


# --------------------------------------------------------------------------
# rule 1
# --------------------------------------------------------------------------

_R1_TAIL_RE = re.compile(r"\s*:\s*(?=\S)|\s+[-–—]\s+(?=\S)")


def _rule1(s: str, known: KnownPredicate) -> OwnerPhrase | None:
    for entries, end in _lists(s, 0, known):
        m = _R1_TAIL_RE.match(s, end)
        if m is None:
            continue
        if not entries[0].at and not all(known(e.raw) for e in entries):
            continue  # unprefixed `Name:` needs every name known (a list led by '@' extends to its members)
        return _build(s, 1, entries, entries[0].clause_start, m.end())
    return None


# --------------------------------------------------------------------------
# rule 2 (+ rules 4-6 inside the clause)
# --------------------------------------------------------------------------

_VERB_RE = re.compile(
    r"\s+(" + "|".join(v.replace(" ", r"\s+") for v in _rules.OWNER_VERBS) + r")\s+(\S+)", re.IGNORECASE
)
_WITH_RE = re.compile(r"\s*\(\s*with\s+", re.IGNORECASE)
_CLOSE_RE = re.compile(r"\s*\)")
_BOUNDARY_RE = re.compile(r"(?:[:;.,!?(\[]\s*|\s[-–—]\s+)")


def _verb_end(s: str, pos: int) -> int | None:
    m = _VERB_RE.match(s, pos)
    if m is None:
        return None
    verb = " ".join(m.group(1).casefold().split())
    nxt = m.group(2)
    nxt_clean = nxt.strip("\"'“”‘’(")
    low = nxt_clean.casefold()
    if low in ("be", "been", "being"):
        return None
    if verb == "to":
        if not nxt_clean or not nxt_clean[0].isalpha() or nxt_clean[0].isupper() or low in _DETERMINERS:
            return None
    return m.end(1)


def _skip_fillers(s: str, pos: int) -> int:
    while True:
        r = _read_token(s, pos)
        if r is None or s[pos:r].casefold() not in _FILLERS or r + 1 >= len(s) or s[r] != " ":
            return pos
        pos = r + 1


def _match_clause(s: str, pos: int, known: KnownPredicate) -> OwnerPhrase | None:
    pos = _skip_fillers(s, pos)
    for entries, end in _lists(s, pos, known, lower_nonperson=True):
        # (with Other) group, then the verb
        wm = _WITH_RE.match(s, end)
        if wm is not None:
            paren = s.index("(", end)
            for wents, wend in _lists(s, wm.end(), known):
                cm = _CLOSE_RE.match(s, wend)
                if cm is None:
                    continue
                v_end = _verb_end(s, cm.end())
                if v_end is not None:
                    return _build(
                        s, 2, entries, entries[0].clause_start, v_end, wents, [TextSpan(paren, cm.end())]
                    )
        v_end = _verb_end(s, end)
        if v_end is not None:
            return _build(s, 2, entries, entries[0].clause_start, v_end)
    return None


def _prev_word_is_capitalised(s: str, end: int) -> bool:
    i = end
    while i > 0 and s[i - 1].isalpha():
        i -= 1
    return i < end and s[i].isupper()


def _rule2(s: str, known: KnownPredicate) -> OwnerPhrase | None:
    starts = [0]
    for m in _BOUNDARY_RE.finditer(s):
        if s[m.start()] == "," and _prev_word_is_capitalised(s, m.start()):
            continue  # inside a name list (`Aa, Bb to`): never start a clause mid-list (keeps the 8-name cap honest)
        starts.append(m.end())
    seen: set[int] = set()
    for start in starts:
        if start in seen or start >= len(s):
            continue
        seen.add(start)
        found = _match_clause(s, start, known)
        if found is not None:
            return found
    return None


# --------------------------------------------------------------------------
# rule 3 (trailing forms; only when rules 1 and 2 found nothing)
# --------------------------------------------------------------------------

_LABEL = r"(?:owner|assignee|dri)"
_PAREN_OPEN_RE = re.compile(r"\(\s*(?:" + _LABEL + r"\s*:\s*)?", re.IGNORECASE)
_PAREN_CLOSE_END_RE = re.compile(r"\s*\)\s*[.;!]?\s*$")
_DASH_RE = re.compile(r"\s[–—-]\s+")
_TAIL_END_RE = re.compile(r"\s*[.;!]?\s*$")
_BRACKET_RE = re.compile(r"\[\s*" + _LABEL + r"\s*:\s*", re.IGNORECASE)
_BRACKET_CLOSE_RE = re.compile(r"\s*\]")
_LABEL_RE = re.compile(r"(?<![\w\[(])" + _LABEL + r"\s*:\s*", re.IGNORECASE)
_LABEL_END_RE = re.compile(r"\s*(?:$|[.;,)\]]|(?=\s*(?:by|due|before|until)\b))", re.IGNORECASE)


def _rule3(s: str, known: KnownPredicate) -> OwnerPhrase | None:
    kw = {"allow_acronym": False}
    # (Name) / (owner: Name) -- rightmost first
    for m in reversed(list(re.finditer(r"\(", s))):
        if not s[: m.start()].strip():
            continue
        om = _PAREN_OPEN_RE.match(s, m.start())
        assert om is not None
        for entries, end in _lists(s, om.end(), known, **kw):
            cm = _PAREN_CLOSE_END_RE.match(s, end)
            if cm is not None:
                close = s.index(")", end)
                return _build(s, 3, entries, m.start(), close + 1)
    # -- Name / – Name / - Name at the end
    for m in reversed(list(_DASH_RE.finditer(s))):
        if not s[: m.start()].strip():
            continue
        for entries, end in _lists(s, m.end(), known, **kw):
            if _TAIL_END_RE.match(s, end) is not None:
                return _build(s, 3, entries, m.start() + 1, end)
    # [owner: Name]
    for m in _BRACKET_RE.finditer(s):
        for entries, end in _lists(s, m.end(), known, **kw):
            cm = _BRACKET_CLOSE_RE.match(s, end)
            if cm is not None:
                return _build(s, 3, entries, m.start(), cm.end())
    # Owner: Name / Assignee: Name / DRI: Name
    for m in _LABEL_RE.finditer(s):
        for entries, end in _lists(s, m.end(), known, **kw):
            if _LABEL_END_RE.match(s, end) is not None:
                return _build(s, 3, entries, m.start(), end)
    return None


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------


def extract_owner_phrase(description: str, is_known_name: KnownPredicate | None = None) -> OwnerPhrase | None:
    """Owner phrase of a cleaned ``description`` (see module docstring); ``None`` when there is none."""
    known = is_known_name or _no_one_known
    if not description or not description.strip():
        return None
    return _rule1(description, known) or _rule2(description, known) or _rule3(description, known)


extract_owner = extract_owner_phrase


def phrase_spans(phrase: OwnerPhrase | None) -> tuple[TextSpan, ...]:
    """Spans of ``phrase`` to delete for ``task_text``: the clause plus every ``(with Other)``."""
    if phrase is None:
        return ()
    return (phrase.clause_span, *phrase.with_spans)


class OwnerPhraseProvider:
    """``OwnerSpanProvider`` backed by ``extract_owner_phrase`` (spans index the description)."""

    def __init__(self, is_known_name: KnownPredicate | None = None) -> None:
        self._known = is_known_name

    def owner_spans(self, description: str) -> Sequence[TextSpan]:
        return phrase_spans(extract_owner_phrase(description, self._known))


class OwnerAnalyzer:
    """``ItemAnalyzer``: ``DueAnalyzer`` plus the owner phrase (``ItemAnalysis.owner``) and owner spans."""

    def __init__(self, is_known_name: KnownPredicate | None = None) -> None:
        self._known = is_known_name
        self._due = DueAnalyzer(OwnerPhraseProvider(is_known_name))

    def analyze(self, description: str, ctx: AnalyzerContext) -> ItemAnalysis:
        base = self._due.analyze(description, ctx)
        return dataclasses.replace(base, owner=extract_owner_phrase(description, self._known))
