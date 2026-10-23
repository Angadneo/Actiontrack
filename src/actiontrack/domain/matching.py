"""Duplicate-matching primitives (spec 10.6). Pure: no I/O, no clock, no DB.

Sections (later tasks append their own, keeping this docstring's API list current):

1. Description core (T-M5-06, this section) -- implemented:
   ``description_core(text) -> str``      8.3 comparison form (NFKC, case-fold, quotes/dashes/ellipsis
                                          to ASCII, emoji removed, whitespace collapsed, edge punctuation
                                          stripped).
   ``core_tokens(core) -> tuple[str, ...]``  token splitting of 8.3 (apostrophes deleted, split on every
                                          non letter/digit, empties dropped), then stop-words removed.
   ``core_key(task_text, fallback=None) -> CoreResult``  ``(tokens, key)``; ``key`` = tokens joined by one
                                          space (the stored ``description_core_key``).
   ``has_recurrence_word(tokens) -> bool``, ``core_equal(a, b)``, ``jaccard(a, b)``.
2. Tier-1 merge (T-M5-08) -- implemented:
   ``NewView`` (the new source), ``ItemView`` (the existing item with its ``SourceView`` list),
   ``tier1_failures(new, item, *, window_days)`` -> tuple of failed condition numbers 1..6
   (empty = Tier-1), ``is_tier1(new, item, *, window_days) -> bool``, ``pick_tier1(new, items, *,
   window_days) -> ItemView | None`` (lowest item id), ``window_ok``, ``due_compatible``,
   ``due_condition_holds`` (condition 4 incl. the dateless-restatement extension), ``status_compatible``, ``owner_person_set``, ``all_resolved``.
3. Possible-duplicate links (T-M5-09) -- implemented:
   ``LINK_PRECEDENCE`` (the one ordered constant, G-27), ``link_reasons(new, item, ...)`` (every reason
   that applies to a pair, unordered), ``classify_pair(new, item, *, window_days, fuzzy_threshold)
   -> ItemLinkReason | None`` (exactly one stored reason or None; ``fuzzy`` for a non-identical core),
   ``fuzzy_match``.
4. Tier-1b completion match (T-M5-10) -- implemented: ``Tier1bMatch(holder, others)``, ``tier1b_applies(new, item,
   *, window_days)``, ``pick_tier1b(new, items, *, window_days) -> Tier1bMatch | None`` (holder = most recent
   matching item, ties by highest item id).
5. Changed-file pairing (T-M5-12, spec 10.7.2) -- implemented: ``OldSource`` / ``NewEntry`` (plain data),
   ``Pair`` (``old_id``, ``new_index``, ``rule`` = ``hash`` | ``moved`` | ``edited``, ``score``),
   ``PairingResult(pairs, new_only, old_only)`` (a 3-tuple), ``pair_sources(old, new) -> PairingResult`` (alias
   ``pair_entries``), ``EDIT_JACCARD`` (0.6). Rules (a) same ``block_hash`` + ``occurrence_index``, (b) same cleaned
   description, (c) same section heading and token Jaccard >= 0.6 greedy by highest score, then document
   order of the new entry, then document order of the old row. Rows with ``present=False`` take part; at every
   rule the ``present`` rows are tried first, so an identical present line is never stolen by a restored one.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from actiontrack import codes as _codes
from actiontrack.domain import rules as _rules
from actiontrack.domain.canonical import OwnerView, SourceView, latest_source

# =========================================================================== 1. Description core


class CoreResult(NamedTuple):
    """Comparison tokens (stop-words removed) and their space-joined key."""

    tokens: tuple[str, ...]
    key: str


_WS_RE = re.compile(r"\s+")


def _is_emoji_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F000 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x2B00 <= cp <= 0x2BFF
        or 0xE0020 <= cp <= 0xE007F
        or cp in (0xFE0E, 0xFE0F, 0x200D, 0x20E3)
        or unicodedata.category(ch) == "So"
    )


def _is_word_char(ch: str) -> bool:
    # Letters, digits, and combining marks (so a decomposed letter is never split from its accent).
    return unicodedata.category(ch)[0] in "LNM"


def description_core(text: str | None) -> str:
    """8.3 comparison form of ``text`` (normally an item's ``task_text``)."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text).casefold()
    s = s.translate(_rules.CORE_CHAR_FOLDS)
    s = "".join(" " if ch.isspace() else ch for ch in s if not _is_emoji_char(ch) or ch.isspace())
    s = _WS_RE.sub(" ", s).strip()
    start, end = 0, len(s)
    while start < end and not _is_word_char(s[start]):
        start += 1
    while end > start and not _is_word_char(s[end - 1]):
        end -= 1
    return s[start:end]


def _split_tokens(core: str) -> list[str]:
    s = core.replace("'", "")
    out: list[str] = []
    cur: list[str] = []
    for ch in s:
        if _is_word_char(ch):
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def core_tokens(core: str | None) -> tuple[str, ...]:
    """Token splitting of 8.3 (the single place the rule is encoded), then stop-word removal.

    Accepts an already-normalised core or raw text (it is re-normalised; idempotent).
    """
    if not core:
        return ()
    normalised = description_core(core)
    return tuple(t for t in _split_tokens(normalised) if t not in _rules.CORE_STOP_WORDS)


def core_key(task_text: str | None, fallback: str | None = None) -> CoreResult:
    """Core of an item. ``fallback`` (the full ``description``) is used only when ``task_text``
    yields no tokens (documented edge of 8.2: ``task_text`` fell back to ``description``)."""
    tokens = core_tokens(task_text)
    if not tokens and fallback:
        tokens = core_tokens(fallback)
    return CoreResult(tokens, " ".join(tokens))


def has_recurrence_word(tokens: Iterable[str]) -> bool:
    """True if any token is a recurrence word (10.6 condition 2; AC-07.7)."""
    return any(t in _rules.RECURRENCE_WORDS for t in tokens)


def core_equal(a: Sequence[str], b: Sequence[str]) -> bool:
    """Identical token *sequences* (10.6 condition 1)."""
    return tuple(a) == tuple(b)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Token-*set* Jaccard; 0.0 for two empty sets."""
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


# =========================================================================== 2. Tier-1 auto-merge (10.6)


@dataclass(frozen=True, slots=True)
class NewView:
    """The incoming source, as plain data (owners already resolved, due already resolved)."""

    source_file_id: int
    meeting_date: date | None
    core_tokens: tuple[str, ...]
    owners: tuple[OwnerView, ...]
    due_date: date | None = None
    due_confidence: str = "none"
    status: str = "open"  # "open" | "done" (checkbox)


@dataclass(frozen=True, slots=True)
class ItemView:
    """An existing action item as plain data. ``owners`` / ``due_*`` / ``status`` are the item's canonical
    values; ``sources`` are all its ``item_source`` rows (present or not)."""

    item_id: int
    core_tokens: tuple[str, ...]
    status: str
    owners: tuple[OwnerView, ...]
    sources: tuple[SourceView, ...]
    due_date: date | None = None
    due_confidence: str = "none"


def all_resolved(owners: Iterable[OwnerView]) -> bool:
    """Every owner row is ``resolved`` (and there is at least one)."""
    rows = list(owners)
    return bool(rows) and all(o.status == "resolved" and o.person_id for o in rows)


def owner_person_set(owners: Iterable[OwnerView]) -> frozenset[str]:
    """Person ids of all owner rows, ``with`` co-owners included (default adopted, G-C8 residual)."""
    return frozenset(o.person_id for o in owners if o.person_id)


def due_compatible(a_date: date | None, a_conf: str, b_date: date | None, b_conf: str) -> bool:
    """Condition 4: equal resolved ``due_date``, or both ``due_confidence=none``. An ``unresolved`` side
    (no date) is compatible with nothing, G-C22."""
    if a_date is not None and b_date is not None:
        return a_date == b_date
    return a_date is None and b_date is None and a_conf == "none" and b_conf == "none"


_RESOLVED_DUE_CONFIDENCES = frozenset({"explicit", "relative", "inferred"})


def due_condition_holds(new: NewView, item: ItemView) -> bool:
    """Condition 4 as applied to a new source and an existing item (spec 10.6, amends D-04).

    Holds when ``due_compatible`` does (equal resolved dates, or both ``none``), OR when the new source is a
    DATELESS RESTATEMENT of an item that already has a resolved due: the new source has ``due_confidence ==
    none`` (no date phrase at all; an ``unresolved`` due such as ``?(next week)`` stays incompatible, G-C22),
    the item's canonical due is a resolved date (explicit / relative / inferred) and the new source is a LATER
    restatement (its meeting date is not earlier than the item's latest source meeting date; an unknown date
    on either side fails). The item keeps its due (``canonical.select_canonical``).

    The REVERSE case is deliberately NOT extended (conservative): an existing dateless item and a new DATED
    source stay separate items with a ``due_differs`` link, as do two items dated differently."""
    if due_compatible(new.due_date, new.due_confidence, item.due_date, item.due_confidence):
        return True
    if new.due_confidence != "none" or new.due_date is not None:
        return False
    if item.due_date is None or item.due_confidence not in _RESOLVED_DUE_CONFIDENCES:
        return False
    latest = _latest_date(item)
    return new.meeting_date is not None and latest is not None and new.meeting_date >= latest


def status_compatible(existing: str, new: str) -> bool:
    """Condition 5: both open, both done, or existing open + new done."""
    return existing == new or (existing == "open" and new == "done")


def _latest_date(item: ItemView) -> date | None:
    return latest_source(item.sources).meeting_date if item.sources else None


def window_ok(new: NewView, item: ItemView, window_days: int) -> bool:
    """Condition 6b: both meeting dates known and ``|new - item's latest source| <= window_days``
    (absolute difference, boundary inclusive; an unknown date fails)."""
    latest = _latest_date(item)
    if new.meeting_date is None or latest is None:
        return False
    return abs((new.meeting_date - latest).days) <= window_days


def _same_file(new: NewView, item: ItemView) -> bool:
    return any(s.source_file_id == new.source_file_id for s in item.sources)


def _same_resolved_owners(new: NewView, item: ItemView) -> bool:
    return (
        all_resolved(new.owners)
        and all_resolved(item.owners)
        and owner_person_set(new.owners) == owner_person_set(item.owners)
    )


def tier1_failures(new: NewView, item: ItemView, *, window_days: int) -> tuple[int, ...]:
    """Numbers (1..6) of the Tier-1 conditions that do NOT hold; ``()`` means Tier-1 auto-merge."""
    failed: list[int] = []
    if not core_equal(new.core_tokens, item.core_tokens):
        failed.append(1)
    if len(new.core_tokens) < _rules.MIN_CORE_TOKENS or has_recurrence_word(new.core_tokens):
        failed.append(2)
    if not _same_resolved_owners(new, item):
        failed.append(3)
    if not due_condition_holds(new, item):
        failed.append(4)
    if not status_compatible(item.status, new.status):
        failed.append(5)
    if _same_file(new, item) or not window_ok(new, item, window_days):
        failed.append(6)
    return tuple(failed)


def is_tier1(new: NewView, item: ItemView, *, window_days: int) -> bool:
    return not tier1_failures(new, item, window_days=window_days)


def pick_tier1(new: NewView, items: Iterable[ItemView], *, window_days: int) -> ItemView | None:
    """The Tier-1 target among the candidates: the lowest item id (deterministic)."""
    for item in sorted(items, key=lambda i: i.item_id):
        if is_tier1(new, item, window_days=window_days):
            return item
    return None


# =========================================================================== 3. Possible-duplicate links (10.6)

_R = _codes.ItemLinkReason

#: Spec 10.6 precedence (G-27); exactly one reason is stored. ``owner_unresolved`` is placed after the
#: listed reasons (it only replaces ``fuzzy``, gap G-C22); ``fuzzy`` needs a resolved same owner so the
#: two never apply together.
LINK_PRECEDENCE: tuple[_codes.ItemLinkReason, ...] = (
    _R.OWNER_DIFFERS,
    _R.STATUS_DIFFERS,
    _R.DUE_DIFFERS,
    _R.SAME_FILE,
    _R.RECURRENCE,
    _R.OUTSIDE_WINDOW,
    _R.OWNER_UNRESOLVED,
    _R.FUZZY,
)


def fuzzy_match(new: NewView, item: ItemView, *, fuzzy_threshold: float) -> bool:
    """Token-set Jaccard >= threshold AND the same resolved owner set on both sides (10.6)."""
    return _same_resolved_owners(new, item) and jaccard(new.core_tokens, item.core_tokens) >= fuzzy_threshold


def link_reasons(
    new: NewView, item: ItemView, *, window_days: int, fuzzy_threshold: float
) -> frozenset[_codes.ItemLinkReason]:
    """Every possible-duplicate reason that applies to the pair (unordered; see ``classify_pair``)."""
    reasons: set[_codes.ItemLinkReason] = set()
    if not new.core_tokens or not item.core_tokens:
        return frozenset()
    if core_equal(new.core_tokens, item.core_tokens):
        new_res, item_res = all_resolved(new.owners), all_resolved(item.owners)
        if new_res and item_res and owner_person_set(new.owners) != owner_person_set(item.owners):
            reasons.add(_R.OWNER_DIFFERS)
        if not status_compatible(item.status, new.status):
            reasons.add(_R.STATUS_DIFFERS)
        if not due_condition_holds(new, item):
            reasons.add(_R.DUE_DIFFERS)
        if _same_file(new, item):
            reasons.add(_R.SAME_FILE)
        if has_recurrence_word(new.core_tokens):
            reasons.add(_R.RECURRENCE)
        if not window_ok(new, item, window_days):
            reasons.add(_R.OUTSIDE_WINDOW)
        if not (new_res and item_res):
            reasons.add(_R.OWNER_UNRESOLVED)
    if fuzzy_match(new, item, fuzzy_threshold=fuzzy_threshold):
        reasons.add(_R.FUZZY)
    return frozenset(reasons)


def classify_pair(
    new: NewView, item: ItemView, *, window_days: int, fuzzy_threshold: float
) -> _codes.ItemLinkReason | None:
    """The single stored link reason for a pair that is NOT Tier-1, or None for a non-match.

    Identical core: the highest-precedence applicable reason (``LINK_PRECEDENCE``); an identical core with
    nothing left to differ on (only a short core can get here) is ``fuzzy`` (Jaccard 1.0, same resolved
    owner). Non-identical core: ``fuzzy`` iff Jaccard >= threshold with the same resolved owner, else None
    (shared verb/topic, or similar text with different owners, are non-matches).
    A pair that IS Tier-1 gets None (it is merged, never linked)."""
    if is_tier1(new, item, window_days=window_days):
        return None
    applicable = link_reasons(new, item, window_days=window_days, fuzzy_threshold=fuzzy_threshold)
    for reason in LINK_PRECEDENCE:
        if reason in applicable:
            return reason
    return None


# =========================================================================== 4. Tier-1b completion match (10.6, G-31)


@dataclass(frozen=True, slots=True)
class Tier1bMatch:
    """Outcome of ``pick_tier1b``: the evidence holder and the other matching open items (ascending id)."""

    holder: ItemView
    others: tuple[ItemView, ...]

    @property
    def items(self) -> tuple[ItemView, ...]:
        return (*self.others, self.holder)


def _latest_sort_key(item: ItemView) -> tuple[bool, date, int]:
    """Ascending recency: dated before undated, then date, then item id (so ``max`` = the most recent)."""
    latest = _latest_date(item)
    return (latest is None, latest or date.min, item.item_id)


def tier1b_applies(new: NewView, item: ItemView, *, window_days: int) -> bool:
    """One open item is part of the Tier-1b obligation of a new ``[x]`` source (10.6 Tier-1b).

    Holds when: the new source is done and the item open; core identical, >= 3 tokens, no recurrence word,
    every owner resolved with identical person sets, different file and within ``window_days`` of the
    item's latest source (Tier-1 conditions 1, 2, 3, 6); the done source's meeting date is NOT earlier than
    that latest source date. The due date (condition 4) is ignored and condition 5 is replaced by
    "existing open + new done". An item with an unresolved owner never matches (AC-07.14)."""
    if new.status != "done" or item.status != "open":
        return False
    if set(tier1_failures(new, item, window_days=window_days)) - {4, 5}:
        return False
    latest = _latest_date(item)
    return new.meeting_date is not None and latest is not None and new.meeting_date >= latest


def pick_tier1b(new: NewView, items: Iterable[ItemView], *, window_days: int) -> Tier1bMatch | None:
    """Every matching open item, split into the evidence holder and the rest.

    The holder is the MOST RECENT matching item: latest source meeting date, ties by the highest item id.
    ``others`` (ascending id) are the items whose completion is only inferred (``completed_by`` -> holder,
    flag ``CI``). Returns None when nothing matches."""
    matching = [i for i in items if tier1b_applies(new, i, window_days=window_days)]
    if not matching:
        return None
    holder = max(matching, key=_latest_sort_key)
    others = tuple(sorted((i for i in matching if i is not holder), key=lambda i: i.item_id))
    return Tier1bMatch(holder, others)


# =========================================================================== 5. Changed-file pairing (10.7.2, G-24)

EDIT_JACCARD = 0.6
"""10.7.2 (c): minimum token Jaccard of an in-place edit."""


@dataclass(frozen=True, slots=True)
class OldSource:
    """An existing ``item_source`` row of the changed file (present or not), as plain data."""

    source_id: int
    block_hash: str
    occurrence_index: int
    description: str
    section_heading: str | None
    line_start: int
    present: bool = True


@dataclass(frozen=True, slots=True)
class NewEntry:
    """A freshly parsed item of the changed file (``index`` = position in document order)."""

    index: int
    block_hash: str
    occurrence_index: int
    description: str
    section_heading: str | None
    line_start: int


@dataclass(frozen=True, slots=True)
class Pair:
    old_id: int
    new_index: int
    rule: str  # "hash" | "moved" | "edited"
    score: float = 1.0


class PairingResult(NamedTuple):
    """``(pairs, new_only, old_only)``: ``pairs`` in new-entry order, ``new_only`` = new indexes without an old
    row (ascending), ``old_only`` = old source ids without a new entry (ascending)."""

    pairs: tuple[Pair, ...]
    new_only: tuple[int, ...]
    old_only: tuple[int, ...]


def _old_order(o: OldSource) -> tuple[int, int]:
    return (o.line_start, o.source_id)


def pair_sources(old: Iterable[OldSource], new: Iterable[NewEntry]) -> PairingResult:
    """Pair the parsed entries of a changed file with the file's existing sources (10.7.2 a-c, G-24).

    Deterministic and one-to-one. Every rule runs over the ``present`` old rows first and then over the
    ``present=False`` ones. (a) and (b) group by key and zip in document order (new entry order against the
    old line order, ties by id); (c) is greedy over all candidate pairs sorted by ``(-score, new index, old
    line, old id)``."""
    olds = sorted(old, key=_old_order)
    news = sorted(new, key=lambda e: e.index)
    free_old: dict[int, OldSource] = {o.source_id: o for o in olds}
    free_new: dict[int, NewEntry] = {e.index: e for e in news}
    found: dict[int, Pair] = {}

    def take(o: OldSource, e: NewEntry, rule: str, score: float) -> None:
        found[e.index] = Pair(o.source_id, e.index, rule, score)
        del free_old[o.source_id]
        del free_new[e.index]

    def keyed(rule: str, old_key, new_key) -> None:  # type: ignore[no-untyped-def]
        for want_present in (True, False):
            groups: dict[object, list[OldSource]] = {}
            for o in olds:
                if o.source_id in free_old and o.present is want_present:
                    groups.setdefault(old_key(o), []).append(o)
            for e in news:
                if e.index not in free_new:
                    continue
                bucket = groups.get(new_key(e))
                if bucket:
                    take(bucket.pop(0), e, rule, 1.0)

    keyed("hash", lambda o: (o.block_hash, o.occurrence_index), lambda e: (e.block_hash, e.occurrence_index))
    keyed("moved", lambda o: o.description, lambda e: e.description)

    old_tokens = {o.source_id: frozenset(core_tokens(o.description)) for o in olds}
    new_tokens = {e.index: frozenset(core_tokens(e.description)) for e in news}
    for want_present in (True, False):
        cands: list[tuple[float, int, int, int, OldSource, NewEntry]] = []
        for o in olds:
            if o.source_id not in free_old or o.present is not want_present:
                continue
            for e in news:
                if e.index in free_new and e.section_heading == o.section_heading:
                    score = jaccard(old_tokens[o.source_id], new_tokens[e.index])
                    if score >= EDIT_JACCARD:
                        cands.append((-score, e.index, o.line_start, o.source_id, o, e))
        cands.sort(key=lambda c: c[:4])
        for neg, _ei, _ol, _oid, o, e in cands:
            if o.source_id in free_old and e.index in free_new:
                take(o, e, "edited", -neg)

    pairs = tuple(found[i] for i in sorted(found))
    return PairingResult(pairs, tuple(sorted(free_new)), tuple(sorted(free_old)))


pair_entries = pair_sources
