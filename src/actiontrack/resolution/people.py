"""In-memory people registry (T-M4-05; spec 10.2a, 10.3 known people K, BR-06, BR-23).

The registry is K of spec 10.3: roster persons, attendee-origin persons and provisional
(note-owner) persons, plus the alias table and the attendee/owner spellings that drive display
names. It is **pure**: no I/O, no clock, no randomness; every query result is sorted (BR-18), so
nothing depends on dict insertion order.

State and rebuild from the database
-----------------------------------
Everything is derived from four inputs, so M5 can rebuild it at the start of each run (or after a
rolled-back file transaction) from DB rows:

1. the roster (``add_roster``; persons keep the config-computed ``RosterPerson.person_id``);
2. the stored ``person`` rows (``load_person``: attendee-origin, provisional and historical
   roster rows, exactly as stored);
3. the stored ``person_alias`` rows (``load_alias``; the table carries an ``auto`` column so the
   flag round-trips; ``Repo.load_registry`` performs the whole rebuild);
4. optionally the attendee spellings (``note_spelling``) when display names must be recomputed.

Mutation model: explicit mutators (documented below) change the registry in place;
``snapshot()`` / ``restore()`` give M5 a rollback point per file transaction and ``copy()`` gives a
cheap independent clone (the whole-database re-resolution pass works on a copy, so it stays a pure
function of its inputs).

Public API
----------
``PeopleRegistry``
  ``add_roster(roster) -> tuple[Person, ...]``            roster persons (emails come ONLY from here)
  ``load_person(person)`` / ``load_alias(person_id, alias, alias_key=None, auto=False)``   DB rows
  ``add_attendee(raw_name, *, ordinal=None) -> Person``   find-or-create an attendee-origin person
                                    by *identity form only* (no cross-form matching: the steps of
                                    10.3 live in ``owners.resolve_attendees``)
  ``add_provisional(raw_name, *, ordinal=None) -> Person``  find-or-create a ``note_owner`` person;
                                    identical ``provisional_key`` shares one person (10.3 step 5)
  ``add_alias(person_id, alias, *, auto=False) -> AliasRecord | None``
  ``note_spelling(person_id, raw, *, ordinal=None, attendee=True)``
  ``merge_provisional(provisional_id, into_person_id) -> MergeRecord``   (10.2a(d), G-30)
  ``get(id)``, ``people()``, ``ids()``, ``aliases(person_id=None)``, ``display_name(id)``,
  ``full_name(id)``, ``is_namesake(id)``, ``namesake_groups()``
  lookups used by the resolver: ``lookup_identity``, ``lookup_alias``, ``lookup_first_name``,
  ``lookup_initial``, ``lookup_non_full``
  ``snapshot() -> RegistrySnapshot`` / ``restore(snapshot)`` / ``copy()`` / ``==``
``known_name_predicate(registry) -> (raw) -> bool``   parser hook ``is_known_name`` (T-M5-07)

Person kinds: a *full* person has ``last_name`` (First+Last identity); a *non-full* person
(``last_name is None``) is a single-token or initial-form attendee/provisional person keyed by
``provisional_key`` (``zed``, ``zed q``). Non-full persons are never returned by the identity
index and never counted in first-name groups (10.2a(a)/(b)); they are only found by exact
``provisional_key`` (step 5) and are merged away by the re-resolution pass once exactly one known
person matches their form.

Defaults adopted (see report): roster ``display_name`` is derived as ``First Last (qualifier)``;
a weak auto-alias (bare first name or initial form, ``auto=True``) is recorded but ignored by
step 1 while that form is shared by >= 2 known people (G-C28).
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Iterable, Iterator
from typing import Any

from actiontrack.config import Roster, RosterPerson
from actiontrack.domain.models import Person, PersonOrigin
from actiontrack.errors import AmbiguousOwnerError, NotFoundError
from actiontrack.domain.names import (
    QUALIFIER_SEP,
    classify_key_kind,
    fold_key,
    name_key,
    person_id_for,
    provisional_key,
)

_STRIP = " \t.,;:()[]{}\"'"


def _clean_token(token: str) -> str:
    return token.strip(_STRIP)


def _raw_tokens(raw: str) -> list[str]:
    """Written tokens that survive honorific/punctuation cleaning (original case)."""
    return [c for t in raw.split() if name_key(t) and (c := _clean_token(t))]


def base_key(identity_key: str) -> str:
    """Identity key without its qualifier part."""
    return identity_key.split(QUALIFIER_SEP, 1)[0]


def is_full(person: Person) -> bool:
    """True for a First+Last person (the only kind that carries an identity, 10.2a)."""
    return person.last_name is not None


def _first_key(person: Person, fold: bool) -> str:
    key = fold_key if fold else name_key
    toks = key(person.first_name).split()
    return toks[0] if toks else ""


def _initial_key(person: Person, fold: bool) -> str:
    if person.last_name is None:
        return ""
    key = fold_key if fold else name_key
    toks = key(person.last_name).split()
    return toks[-1][:1] if toks else ""


@dataclasses.dataclass(frozen=True, slots=True)
class AliasRecord:
    person_id: str
    alias: str
    alias_key: str
    auto: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class MergeRecord:
    """Outcome of ``merge_provisional``: the provisional row is gone, ``alias`` is kept (auto)."""

    provisional_id: str
    into_person_id: str
    alias: str
    alias_key: str


@dataclasses.dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """Opaque rollback point (``PeopleRegistry.snapshot`` / ``restore``)."""

    people: dict[str, Person]
    aliases: dict[tuple[str, str], AliasRecord]
    spellings: dict[str, dict[str, tuple[int, bool]]]
    counter: int


class _Index:
    """Derived lookup tables (rebuilt lazily after any mutation); all id tuples are sorted."""

    __slots__ = (
        "alias",
        "alias_fold",
        "first",
        "first_fold",
        "identity",
        "identity_fold",
        "initial",
        "initial_fold",
        "non_full",
    )

    def __init__(self, people: dict[str, Person], aliases: dict[tuple[str, str], AliasRecord]) -> None:
        identity: dict[str, list[str]] = {}
        identity_fold: dict[str, list[str]] = {}
        first: dict[str, list[str]] = {}
        first_fold: dict[str, list[str]] = {}
        initial: dict[tuple[str, str], list[str]] = {}
        initial_fold: dict[tuple[str, str], list[str]] = {}
        non_full: dict[str, str] = {}
        for pid in sorted(people):
            p = people[pid]
            if not is_full(p):
                non_full[p.identity_key] = pid
                continue
            base = base_key(p.identity_key)
            identity.setdefault(base, []).append(pid)
            identity_fold.setdefault(fold_key(base), []).append(pid)
            first.setdefault(_first_key(p, False), []).append(pid)
            first_fold.setdefault(_first_key(p, True), []).append(pid)
            initial.setdefault((_first_key(p, False), _initial_key(p, False)), []).append(pid)
            initial_fold.setdefault((_first_key(p, True), _initial_key(p, True)), []).append(pid)
        self.identity = {k: tuple(v) for k, v in identity.items()}
        self.identity_fold = {k: tuple(v) for k, v in identity_fold.items()}
        self.first = {k: tuple(v) for k, v in first.items()}
        self.first_fold = {k: tuple(v) for k, v in first_fold.items()}
        self.initial = {k: tuple(v) for k, v in initial.items()}
        self.initial_fold = {k: tuple(v) for k, v in initial_fold.items()}
        self.non_full = non_full
        alias: dict[str, list[AliasRecord]] = {}
        alias_fold: dict[str, list[AliasRecord]] = {}
        for k in sorted(aliases):
            rec = aliases[k]
            if rec.person_id not in people:
                continue
            alias.setdefault(rec.alias_key, []).append(rec)
            alias_fold.setdefault(fold_key(rec.alias_key), []).append(rec)
        self.alias = alias
        self.alias_fold = alias_fold


class PeopleRegistry:
    """Known people K (spec 10.3) with sorted, deterministic indexes. See module docstring."""

    def __init__(self) -> None:
        self._people: dict[str, Person] = {}
        self._aliases: dict[tuple[str, str], AliasRecord] = {}
        self._spellings: dict[str, dict[str, tuple[int, bool]]] = {}
        self._counter = 0
        self._idx: _Index | None = None

    # ------------------------------------------------------------------ plumbing
    def _index(self) -> _Index:
        if self._idx is None:
            self._idx = _Index(self._people, self._aliases)
        return self._idx

    def _dirty(self) -> None:
        self._idx = None

    def _next_ordinal(self, ordinal: int | None) -> int:
        if ordinal is not None:
            return ordinal
        self._counter += 1
        return self._counter

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PeopleRegistry):
            return NotImplemented
        return self._people == other._people and self._aliases == other._aliases

    __hash__ = None  # type: ignore[assignment]

    def __len__(self) -> int:
        return len(self._people)

    def __contains__(self, person_id: object) -> bool:
        return person_id in self._people

    def __repr__(self) -> str:
        return f"PeopleRegistry({len(self._people)} people, {len(self._aliases)} aliases)"

    # ------------------------------------------------------------------ snapshot / copy
    def snapshot(self) -> RegistrySnapshot:
        return RegistrySnapshot(
            people=dict(self._people),
            aliases=dict(self._aliases),
            spellings={k: dict(v) for k, v in self._spellings.items()},
            counter=self._counter,
        )

    def restore(self, snapshot: RegistrySnapshot) -> None:
        self._people = dict(snapshot.people)
        self._aliases = dict(snapshot.aliases)
        self._spellings = {k: dict(v) for k, v in snapshot.spellings.items()}
        self._counter = snapshot.counter
        self._dirty()

    def copy(self) -> PeopleRegistry:
        other = PeopleRegistry()
        other.restore(self.snapshot())
        return other

    # ------------------------------------------------------------------ loading
    def add_roster(self, roster: Roster | Iterable[RosterPerson]) -> tuple[Person, ...]:
        """Add (or refresh) roster persons; the only source of ``Person.email`` (FR-05.3).

        An existing person with the same identity key (attendee/provisional origin) is upgraded in
        place (id kept, origin ``roster``, verified). Re-adding a roster person replaces its
        non-auto aliases with the roster's current ones; auto aliases are kept.
        """
        out: list[Person] = []
        for rp in roster:
            ident = rp.qualified_key
            existing_id = next((i for i, p in self._people.items() if p.identity_key == ident), None)
            if existing_id is not None:
                pid = existing_id
            elif rp.person_id in self._people:
                pid = person_id_for(ident, taken=self._people.keys())
            else:
                pid = rp.person_id
            display = rp.full_name + (f" ({rp.qualifier})" if rp.qualifier else "")
            person = Person(
                id=pid,
                first_name=rp.first_name,
                last_name=rp.last_name,
                identity_key=ident,
                origin=PersonOrigin.ROSTER,
                verified=True,
                qualifier=rp.qualifier,
                display_name=display,
                name_key=base_key(ident),
                email=rp.email,
            )
            self._people[pid] = person
            for k in [k for k, a in self._aliases.items() if k[0] == pid and not a.auto]:
                del self._aliases[k]
            for alias in rp.aliases:
                self._put_alias(pid, alias, auto=False)
            out.append(person)
        self._dirty()
        return tuple(out)

    def load_person(self, person: Person) -> None:
        """Insert a stored ``person`` row as is (DB rebuild)."""
        self._people[person.id] = person
        self._dirty()

    def load_alias(
        self, person_id: str, alias: str, alias_key: str | None = None, auto: bool = False
    ) -> AliasRecord | None:
        """Insert a stored ``person_alias`` row (DB rebuild)."""
        key = alias_key if alias_key is not None else name_key(alias)
        if not key:
            return None
        rec = AliasRecord(person_id, alias, key, auto)
        self._aliases[(person_id, key)] = rec
        self._dirty()
        return rec

    # ------------------------------------------------------------------ mutators
    def _put_alias(self, person_id: str, alias: str, *, auto: bool) -> AliasRecord | None:
        key = name_key(alias)
        if not key:
            return None
        old = self._aliases.get((person_id, key))
        if old is not None and (not old.auto or auto):
            return old  # a roster alias is never downgraded to auto; duplicates are no-ops
        rec = AliasRecord(person_id, alias.strip(), key, auto)
        self._aliases[(person_id, key)] = rec
        return rec

    def add_alias(self, person_id: str, alias: str, *, auto: bool = False) -> AliasRecord | None:
        if person_id not in self._people:
            raise KeyError(person_id)
        rec = self._put_alias(person_id, alias, auto=auto)
        self._dirty()
        return rec

    def _create(self, raw: str, origin: PersonOrigin, ordinal: int | None) -> Person:
        key = provisional_key(raw)
        if key is None:
            raise ValueError(f"cannot create a person from a blank name: {raw!r}")
        kind = classify_key_kind(raw)
        toks = _raw_tokens(raw)
        person = Person(
            id=person_id_for(key, taken=self._people.keys()),
            first_name=toks[0],
            last_name=toks[-1] if kind == "full" else None,
            identity_key=key,
            origin=origin,
            verified=False,
            qualifier=None,
            display_name=" ".join(raw.split()),
            name_key=key,
            email=None,
        )
        self._people[person.id] = person
        self._dirty()
        return self._record_spelling(person.id, raw, ordinal, attendee=origin is PersonOrigin.ATTENDEE)

    def _find_exact(self, raw: str) -> Person | None:
        """Person whose identity form equals ``raw`` (full -> identity index; else provisional key)."""
        key = provisional_key(raw)
        if key is None:
            return None
        idx = self._index()
        if classify_key_kind(raw) == "full":
            ids = idx.identity.get(key, ())
            return self._people[ids[0]] if len(ids) == 1 else None
        pid = idx.non_full.get(key)
        return self._people[pid] if pid else None

    def add_attendee(self, raw_name: str, *, ordinal: int | None = None) -> Person:
        """Find-or-create the attendee-origin person with this exact identity form (never mutates a
        roster person; records the spelling for the display-name rule)."""
        found = self._find_exact(raw_name)
        if found is not None:
            return self._record_spelling(found.id, raw_name, ordinal, attendee=True)
        return self._create(raw_name, PersonOrigin.ATTENDEE, ordinal)

    def add_provisional(self, raw_name: str, *, ordinal: int | None = None) -> Person:
        """Find-or-create the provisional person (``note_owner``, unverified); identical
        ``provisional_key`` shares one person (10.3 step 5, G-30)."""
        found = self._find_exact(raw_name)
        if found is not None:
            return self._record_spelling(found.id, raw_name, ordinal, attendee=False)
        return self._create(raw_name, PersonOrigin.NOTE_OWNER, ordinal)

    def _record_spelling(self, person_id: str, raw: str, ordinal: int | None, *, attendee: bool) -> Person:
        raw = " ".join(raw.split())
        o = self._next_ordinal(ordinal)
        sp = self._spellings.setdefault(person_id, {})
        old = sp.get(raw)
        sp[raw] = (o, attendee) if old is None else (min(old[0], o), old[1] or attendee)
        person = self._people[person_id]
        display = self.display_name(person_id)
        if display != person.display_name:
            person = dataclasses.replace(person, display_name=display)
            self._people[person_id] = person
            self._dirty()
        return person

    def note_spelling(
        self, person_id: str, raw: str, *, ordinal: int | None = None, attendee: bool = True
    ) -> Person:
        """Record a spelling seen for an existing person (display-name rule, 10.3 last paragraph)."""
        if person_id not in self._people:
            raise KeyError(person_id)
        return self._record_spelling(person_id, raw, ordinal, attendee=attendee)

    def merge_provisional(self, provisional_id: str, into_person_id: str) -> MergeRecord:
        """Merge a non-roster person into a known one (10.2a(d), G-30).

        Aliases of the provisional person move to the target; its name key is kept as an
        ``auto=True`` alias; the provisional person leaves K. (Repointing ItemOwner /
        MeetingAttendee rows is the caller's job, T-M4-07/15.)
        """
        prov = self._people.get(provisional_id)
        target = self._people.get(into_person_id)
        if prov is None or target is None:
            raise KeyError(provisional_id if prov is None else into_person_id)
        if provisional_id == into_person_id:
            raise ValueError("cannot merge a person into itself")
        if prov.origin is PersonOrigin.ROSTER:
            raise ValueError("a roster person is never merged away")
        for k, a in [(k, a) for k, a in self._aliases.items() if k[0] == provisional_id]:
            del self._aliases[k]
            self._put_alias(into_person_id, a.alias, auto=True)
        alias_text = prov.display_name or prov.first_name
        key = base_key(prov.identity_key)
        old = self._aliases.get((into_person_id, key))
        if old is None:
            self._aliases[(into_person_id, key)] = AliasRecord(into_person_id, alias_text, key, True)
        del self._people[provisional_id]
        self._spellings.pop(provisional_id, None)
        self._dirty()
        return MergeRecord(provisional_id, into_person_id, alias_text, key)

    # ------------------------------------------------------------------ queries
    def get(self, person_id: str) -> Person | None:
        return self._people.get(person_id)

    def people(self) -> tuple[Person, ...]:
        return tuple(self._people[i] for i in sorted(self._people))

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._people))

    def aliases(self, person_id: str | None = None) -> tuple[AliasRecord, ...]:
        recs = [a for k, a in self._aliases.items() if person_id is None or k[0] == person_id]
        return tuple(sorted(recs, key=lambda a: (a.person_id, a.alias_key)))

    def full_name(self, person_id: str) -> str:
        """``First Last`` (+ ``(qualifier)``) for a full person; the display name otherwise."""
        p = self._people[person_id]
        if p.last_name is None:
            return self.display_name(person_id)
        name = f"{p.first_name} {p.last_name}"
        return f"{name} ({p.qualifier})" if p.qualifier else name

    def display_name(self, person_id: str) -> str:
        """Roster display name, else the most complete attendee spelling, else the first-seen raw
        spelling (ties by ordinal, then text) -- recomputed from stored spellings, so it never
        depends on the order in which they arrived."""
        p = self._people[person_id]
        if p.origin is PersonOrigin.ROSTER:
            return p.display_name
        sp = self._spellings.get(person_id)
        if not sp:
            return p.display_name
        attended = [(raw, o) for raw, (o, att) in sp.items() if att]
        if attended:
            best = min(attended, key=lambda t: (-len(t[0].split()), -len(t[0]), t[1], t[0]))
        else:
            best = min(((raw, o) for raw, (o, _a) in sp.items()), key=lambda t: (t[1], t[0]))
        return best[0]

    def namesake_groups(self) -> dict[str, tuple[str, ...]]:
        """First-name key -> sorted person ids, only groups of >= 2 full persons (10.2a)."""
        return {k: v for k, v in sorted(self._index().first.items()) if len(v) >= 2}

    def is_namesake(self, person_id: str) -> bool:
        p = self._people.get(person_id)
        if p is None or not is_full(p):
            return False
        return len(self._index().first.get(_first_key(p, False), ())) >= 2

    # ------------------------------------------------------------------ resolver lookups
    def lookup_identity(self, base: str, *, fold: bool = False) -> tuple[str, ...]:
        """Full persons whose identity key (qualifier ignored) equals ``base`` (a name key; for
        ``fold=True`` a folded key)."""
        idx = self._index()
        return (idx.identity_fold if fold else idx.identity).get(base, ())

    def lookup_alias(self, alias_key: str, *, fold: bool = False) -> tuple[str, ...]:
        """Persons an alias key maps to (sorted, distinct). Weak auto-aliases (bare first name or
        initial form, ``auto=True``) are skipped while their form is shared by >= 2 people (G-C28)."""
        idx = self._index()
        recs = (idx.alias_fold if fold else idx.alias).get(alias_key, ())
        out: set[str] = set()
        for rec in recs:
            if rec.auto and self._is_weak_shared(rec.alias_key):
                continue
            out.add(rec.person_id)
        return tuple(sorted(out))

    def _is_weak_shared(self, key: str) -> bool:
        kind = classify_key_kind(key)
        toks = key.split()
        idx = self._index()
        if kind == "single_token":
            return len(idx.first.get(toks[0], ())) >= 2
        if kind == "initial_form":
            return len(idx.initial.get((toks[0], toks[-1][:1]), ())) >= 2
        return False

    def lookup_first_name(self, first: str, *, fold: bool = False) -> tuple[str, ...]:
        """C of 10.3 step 3: full persons whose first-name key equals ``first``."""
        idx = self._index()
        return (idx.first_fold if fold else idx.first).get(first, ())

    def lookup_initial(self, first: str, initial: str, *, fold: bool = False) -> tuple[str, ...]:
        idx = self._index()
        return (idx.initial_fold if fold else idx.initial).get((first, initial), ())

    def lookup_non_full(self, key: str) -> str | None:
        """Single-token / initial-form person with this ``provisional_key`` (step 5 reuse)."""
        return self._index().non_full.get(key)

    def non_full_people(self) -> tuple[Person, ...]:
        return tuple(p for p in self.people() if not is_full(p))


# ====================================================================================== T-M4-07
# Transactional glue between the registry and the per-file database transaction (NFR-REL-01).
# All SQL lives in ``store/repo.py`` (``Repo.upsert_roster`` / ``persist_registry`` /
# ``load_registry`` / ``merge_person``); this module only keeps the registry rollback point.


@contextlib.contextmanager
def file_transaction(db: Any, registry: PeopleRegistry) -> Iterator[None]:
    """Per-file ingest transaction: ``db.transaction()`` plus a registry rollback point.

    ``registry.snapshot()`` is taken before ``BEGIN``; on ANY exception (including one raised by
    COMMIT / the ``before_commit`` fault hook) the registry is ``restore()``d and the error is
    re-raised, so a failed file leaves neither DB rows nor registry state behind. On success
    nothing is undone. Usage (M5)::

        with file_transaction(db, registry):
            ...registry mutations + repo.persist_registry(registry) + item writes...
    """
    snap = registry.snapshot()
    try:
        with db.transaction():
            yield
    except BaseException:
        registry.restore(snap)
        raise


# ====================================================================================== T-M4-13
# Person query resolution for ``list --owner`` and ``followup`` (spec 10.5, FR-02.6, FR-04.1).
#
# ``resolve_person_query(query, registry) -> Person`` or raises
#   ``PersonNotFound``  (a ``NotFoundError``, exit 3): ``.query``, ``.near`` (<= 3 ``Person``,
#                       sorted by (edit distance, identity key); NEVER auto-used) and a
#                       "did you mean" hint.
#   ``PersonAmbiguous`` (an ``AmbiguousOwnerError``, exit 6): ``.query``, ``.candidates`` (ids,
#                       sorted), ``.candidate_lines`` (``Full Name <email>`` / ``Full Name (no email)``)
#                       and the hint "re-run with the full name"; ``format()`` prints every candidate.
# Order: person id -> email (case-insensitive) -> exact identity key / full name / alias key
# (union; exactly one chosen, >= 2 ambiguous) -> exact provisional key -> bare first name (one
# full person chosen, >= 2 ambiguous) -> not found. Provisional / attendee-only people are
# matchable by their exact key (``list`` may use them). ``followup`` must additionally check
# ``has_recipient(person)`` (FR-04.7).


def _person_label(p: Person) -> str:
    return p.display_name or f"{p.first_name} {p.last_name or ''}".strip()


class PersonNotFound(NotFoundError):
    def __init__(self, query: str, near: Iterable[Person] = ()) -> None:
        self.query = query
        self.near: tuple[Person, ...] = tuple(near)
        self.near_names: tuple[str, ...] = tuple(_person_label(p) for p in self.near)
        names = ", ".join(self.near_names)
        super().__init__(
            f"no person matches {query!r}",
            next_step=(f"did you mean: {names}?" if names else "check the name, email or id"),
        )


class PersonAmbiguous(AmbiguousOwnerError):
    def __init__(self, query: str, candidates: Iterable[str], lines: Iterable[str]) -> None:
        self.query = query
        self.candidates: tuple[str, ...] = tuple(sorted(candidates))
        self.candidate_lines: tuple[str, ...] = tuple(lines)
        super().__init__(f"{query!r} matches more than one person", next_step="re-run with the full name")

    def format(self) -> str:
        body = "\n".join(f"  {line}" for line in self.candidate_lines)
        return f"{self.what}:\n{body}\nNext: {self.next_step}"


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (insert / delete / substitute), deterministic, no dependency."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def has_recipient(person: Person) -> bool:
    """FR-04.7 precondition helper: a person can receive a follow-up only with an email."""
    return bool(person.email)


def _choose(query: str, ids: Iterable[str], registry: PeopleRegistry) -> Person | None:
    ordered = sorted(set(ids))
    if not ordered:
        return None
    if len(ordered) > 1:
        from actiontrack.resolution.owners import candidate_lines  # lazy: owners imports this module

        raise PersonAmbiguous(query, ordered, candidate_lines(ordered, registry))
    return registry.get(ordered[0])


def _near_matches(key: str, registry: PeopleRegistry) -> tuple[Person, ...]:
    forms: list[tuple[str, str, str]] = [(base_key(p.identity_key), p.identity_key, p.id) for p in registry.people()]
    for rec in registry.aliases():
        p = registry.get(rec.person_id)
        if p is not None:
            forms.append((rec.alias_key, p.identity_key, p.id))
    best: dict[str, tuple[int, str]] = {}
    for form, ident, pid in forms:
        d = edit_distance(key, form)
        if d <= 2 and (pid not in best or (d, ident) < best[pid]):
            best[pid] = (d, ident)
    ordered = sorted(best, key=lambda pid: (best[pid][0], best[pid][1], pid))[:3]
    return tuple(registry.get(pid) for pid in ordered)  # type: ignore[misc]


def resolve_person_query(query: str, registry: PeopleRegistry) -> Person:
    """Resolve an ``--owner`` / ``followup`` argument to exactly one known person (spec 10.5)."""
    text = (query or "").strip()
    if not text:
        raise PersonNotFound(query or "")
    # 1. person id (exact, then case-insensitive)
    if (p := registry.get(text)) is not None:
        return p
    low = text.casefold()
    by_id = [p for p in registry.people() if p.id.casefold() == low]
    if len(by_id) == 1:
        return by_id[0]
    # 2. email, case-insensitive
    by_email = [p.id for p in registry.people() if p.email and p.email.strip().casefold() == low]
    if (p := _choose(text, by_email, registry)) is not None:
        return p
    # 3. exact identity key / full name / alias key (union), then exact provisional key
    key = name_key(text)
    if not key:
        raise PersonNotFound(text)
    ids = set(registry.lookup_identity(key)) | set(registry.lookup_alias(key))
    ids |= {p.id for p in registry.people() if is_full(p) and name_key(registry.full_name(p.id)) == key}
    if (p := _choose(text, ids, registry)) is not None:
        return p
    pkey = provisional_key(text)
    if pkey is not None and (pid := registry.lookup_non_full(pkey)) is not None:
        return registry.get(pid)  # type: ignore[return-value]
    # 4. first-name-only query
    if len(key.split()) == 1 and (p := _choose(text, registry.lookup_first_name(key), registry)) is not None:
        return p
    raise PersonNotFound(text, _near_matches(key, registry))


# ====================================================================================== T-M5-07
# Parser hook: ``is_known_name`` for ``parsing.owner_phrase.OwnerAnalyzer`` (spec 10.1 rule 1,
# 10.3). The registry has no ``is_known`` method; this predicate is built from its lookups.


def known_name_predicate(registry: PeopleRegistry) -> Any:
    """Return ``is_known_name(raw) -> bool``: True when ``raw`` matches a known person by any
    spec 10.3 route (roster alias, full identity, first name + initial, bare first name), on the
    plain keys or on the diacritic-folded keys (step 4). Evaluated lazily against the registry
    object, so it sees every person added before the call. Provisional (``note_owner``) people
    count as known (they are in K)."""
    from actiontrack.domain.models import NameFormKind
    from actiontrack.domain.names import classify_name, identity_key

    def known(raw: str) -> bool:
        form = classify_name(raw)
        if form is None:
            return False
        for fold in (False, True):
            fk = fold_key if fold else (lambda t: t)
            if registry.lookup_alias(fold_key(raw) if fold else name_key(raw), fold=fold):
                return True
            if form.kind is NameFormKind.FULL:
                base = identity_key(raw)
                if base and registry.lookup_identity(fk(base), fold=fold):
                    return True
            elif form.kind is NameFormKind.FIRST_INITIAL:
                if registry.lookup_initial(fk(form.first), fk(form.initial or ""), fold=fold):
                    return True
            elif registry.lookup_first_name(fk(form.first), fold=fold):
                return True
        return False

    return known
