"""Owner and attendee resolution (T-M4-06, 08..12; spec 10.2a, 10.3, 10.4, BR-06, BR-23).

Everything here is PURE and deterministic: no I/O, no clock. The only mutation is of the
``PeopleRegistry`` passed in, and only where the spec creates people (attendee names, step 5
provisional people); the whole-database re-resolution pass works on a copy of the registry.

Resolution (``resolve_name`` / ``resolve_owner``), one owner token, spec 10.3 in order:

1. exact full name (identity key) or roster alias -> ``resolved`` (provenance ``roster`` /
   ``attendee`` by how the person entered K, ``alias`` for an alias hit); alias to >= 2 people ->
   ``ambiguous``. A single-token owner is only ever matched by a roster alias here (10.2a(a));
   an unmatched *known* provisional person of origin ``note_owner`` yields ``provisional``.
2. first name + last initial -> unique ``resolved`` (provenance ``initial_form``), several
   ``ambiguous``. A roster alias written as an initial form (``Priya N.``) keeps provenance
   ``initial_form`` when it agrees with the initial match (AC-05.18); an alias that pins a
   different person (or disambiguates an ambiguous initial) wins and is ``alias``.
3. first name only: C = full persons with that first name; 3a (only |C| >= 2, a meeting WITH an
   attendee list, ``attendee_disambiguation`` on): A = C within the meeting's attendee ids ->
   |A| = 1 ``attendee_in_meeting``, |A| >= 2 ambiguous, |A| = 0 falls to 3b; 3b: |C| = 1
   ``unique_first_name`` (even with an attendee list), |C| >= 2 ambiguous, |C| = 0 falls on.
4. the same steps on folded keys (``fold_key``); unique -> ``diacritic_fold`` + ``W-NAME-DIACRITIC``.
5. find-or-create a provisional person (``origin=note_owner``), status ``provisional``.

Status rule for matched people: a person of origin ``note_owner`` (unverified) is reported as
``provisional``/``provisional`` on every re-run, so repeated passes are idempotent. Ambiguous
results carry provenance ``none`` and a sorted ``candidates`` tuple.

Public API
----------
``MeetingContext(attendee_ids)``       ids of the meeting's attendees, ``None`` = note has no list
``resolve_name(raw, ctx, registry, cfg=None, *, ordinal=0, role=OWNER) -> ResolvedOwner``
``resolve_owner(mention, ctx, registry, cfg=None, *, ordinal=0) -> ResolvedOwner``
``resolve_attendees(attendees, registry, *, ordinal=None) -> AttendeeResolution``
``re_resolve(snapshot, registry, cfg=None) -> ReResolutionResult``       whole-DB pass (step 6)
``apply_to_snapshot(snapshot, result) -> ReResolutionSnapshot``           (tests / M5 loops)
ambiguity hooks: ``candidate_key``, ``owner_display``, ``group_ambiguous``,
``ambiguous_report_records``, ``owner_matches_person``, ``candidate_lines``.

``cfg`` may be a ``Config`` (``cfg.owners.attendee_disambiguation``), an ``Owners`` or ``None``
(default: attendance disambiguation on).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from typing import Any

from actiontrack import codes as _codes
from actiontrack.domain.models import (
    FLAG_OWNER_AMBIGUOUS,
    FLAG_OWNER_UNASSIGNED,
    AmbiguousGroup,
    AmbiguousRecord,
    Attendee,
    NameForm,
    NameFormKind,
    NameMention,
    OwnerDisplay,
    OwnerRole,
    OwnerStatus,
    Person,
    PersonOrigin,
    Provenance,
    ResolvedOwner,
)
from actiontrack.domain.names import (
    classify_name,
    fold_key,
    identity_key,
    name_key,
)
from actiontrack.resolution.people import MergeRecord, PeopleRegistry, is_full

# =============================================================================
# Meeting context, outcomes, steps 1-5
# =============================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class MeetingContext:
    """``attendee_ids``: persons in the meeting's attendee list (the ``A`` source of step 3a);
    ``None`` when the note has no attendee list (``W-ATTENDEES-MISSING``)."""

    attendee_ids: frozenset[str] | None = None


NO_ATTENDEE_LIST = MeetingContext(None)


def attendance_enabled(cfg: Any) -> bool:
    """Read ``attendee_disambiguation`` from a Config, an Owners table or None (default True)."""
    if cfg is None:
        return True
    owners = getattr(cfg, "owners", cfg)
    return bool(getattr(owners, "attendee_disambiguation", True))


@dataclasses.dataclass(frozen=True, slots=True)
class _Outcome:
    ids: tuple[str, ...]  # sorted; 1 = resolved, >= 2 = ambiguous
    provenance: Provenance


def _origin_provenance(person: Person) -> Provenance:
    if person.origin is PersonOrigin.ROSTER:
        return Provenance.ROSTER
    if person.origin is PersonOrigin.ATTENDEE:
        return Provenance.ATTENDEE
    return Provenance.PROVISIONAL


def _alias_key(raw: str, fold: bool) -> str:
    return fold_key(raw) if fold else name_key(raw)


def _fk(text: str, fold: bool) -> str:
    return fold_key(text) if fold else text


def _step2_ids(form: NameForm, reg: PeopleRegistry, fold: bool) -> tuple[str, ...]:
    return reg.lookup_initial(_fk(form.first, fold), _fk(form.initial or "", fold), fold=fold)


def _step1(raw: str, form: NameForm, reg: PeopleRegistry, fold: bool) -> _Outcome | None:
    """Steps 1 (and 2 for initial forms, see module docstring)."""
    alias = reg.lookup_alias(_alias_key(raw, fold), fold=fold)
    if form.kind is NameFormKind.FULL:
        base = identity_key(raw)
        ident = reg.lookup_identity(_fk(base, fold), fold=fold) if base else ()
        if alias:  # a roster alias pins the name; an unverified provisional person must not compete
            ident = tuple(i for i in ident if reg.get(i).origin is not PersonOrigin.NOTE_OWNER)  # type: ignore[union-attr]
        ids = tuple(sorted(set(ident) | set(alias)))
        if not ids:
            return None
        if len(ids) == 1 and ident:
            return _Outcome(ids, _origin_provenance(reg.get(ids[0])))  # type: ignore[arg-type]
        return _Outcome(ids, Provenance.ALIAS)
    if form.kind is NameFormKind.FIRST_ONLY:
        return _Outcome(alias, Provenance.ALIAS) if alias else None
    # first name + last initial (step 2), with the alias agreement rule
    initial = _step2_ids(form, reg, fold)
    if alias:
        if len(alias) == 1 and initial == alias:
            return _Outcome(alias, Provenance.INITIAL_FORM)
        return _Outcome(alias, Provenance.ALIAS)
    return _Outcome(initial, Provenance.INITIAL_FORM) if initial else None


def _step3(
    form: NameForm, ctx: MeetingContext, reg: PeopleRegistry, attendance: bool, fold: bool
) -> _Outcome | None:
    c = reg.lookup_first_name(_fk(form.first, fold), fold=fold)
    if not c:
        return None
    if len(c) >= 2 and attendance and ctx.attendee_ids is not None:  # 3a
        a = tuple(i for i in c if i in ctx.attendee_ids)
        if len(a) == 1:
            return _Outcome(a, Provenance.ATTENDEE_IN_MEETING)
        if len(a) >= 2:
            return _Outcome(a, Provenance.NONE)
    if len(c) == 1:  # 3b
        return _Outcome(c, Provenance.UNIQUE_FIRST_NAME)
    return _Outcome(c, Provenance.NONE)


def _steps_1_to_3(
    raw: str, form: NameForm, ctx: MeetingContext, reg: PeopleRegistry, attendance: bool, fold: bool
) -> _Outcome | None:
    out = _step1(raw, form, reg, fold)
    if out is not None:
        return out
    if form.kind is NameFormKind.FIRST_ONLY:
        return _step3(form, ctx, reg, attendance, fold)
    return None


def _finalize(
    out: _Outcome, reg: PeopleRegistry, raw: str, ordinal: int, role: OwnerRole, fold: bool
) -> ResolvedOwner:
    if len(out.ids) >= 2:
        return ResolvedOwner(
            ordinal, raw, None, OwnerStatus.AMBIGUOUS, tuple(sorted(out.ids)), Provenance.NONE, role
        )
    person = reg.get(out.ids[0])
    warnings = (_codes.W_NAME_DIACRITIC,) if fold else ()
    provenance = Provenance.DIACRITIC_FOLD if fold else out.provenance
    status = OwnerStatus.RESOLVED
    if person is not None and person.origin is PersonOrigin.NOTE_OWNER:
        status, provenance = OwnerStatus.PROVISIONAL, Provenance.PROVISIONAL
    return ResolvedOwner(ordinal, raw, out.ids[0], status, (), provenance, role, warnings)


def resolve_name(
    raw: str | None,
    ctx: MeetingContext,
    registry: PeopleRegistry,
    cfg: Any = None,
    *,
    ordinal: int = 0,
    role: OwnerRole = OwnerRole.OWNER,
) -> ResolvedOwner:
    """Resolve one owner token (spec 10.3 steps 1-5). Step 5 may add a provisional person."""
    form = classify_name(raw)
    if form is None or raw is None:
        return ResolvedOwner(ordinal, raw, None, OwnerStatus.UNASSIGNED, (), Provenance.NONE, role)
    attendance = attendance_enabled(cfg)
    for fold in (False, True):  # step 4 re-runs 1-3 on folded keys
        out = _steps_1_to_3(raw, form, ctx, registry, attendance, fold)
        if out is not None:
            return _finalize(out, registry, raw, ordinal, role, fold)
    person = registry.add_provisional(raw)
    return ResolvedOwner(
        ordinal, raw, person.id, OwnerStatus.PROVISIONAL, (), Provenance.PROVISIONAL, role
    )


def resolve_owner(
    mention: NameMention,
    meeting_ctx: MeetingContext,
    registry: PeopleRegistry,
    cfg: Any = None,
    *,
    ordinal: int = 0,
) -> ResolvedOwner:
    """``resolve_name`` for an extracted ``NameMention`` (keeps its role)."""
    return resolve_name(mention.raw, meeting_ctx, registry, cfg, ordinal=ordinal, role=mention.role)


# =============================================================================
# Attendees (T-M4-06)
# =============================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class MeetingAttendeeRef:
    """One attendee entry: ``person_id`` is None when not attributable (incomplete name while
    namesakes exist, or ambiguous). ``email_hint`` stays here, never copied to ``Person.email``
    (BR-13). ``warnings`` holds W-* codes; the caller adds file/line context."""

    raw_name: str
    person_id: str | None
    email_hint: str | None = None
    warnings: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class AttendeeResolution:
    refs: tuple[MeetingAttendeeRef, ...]
    person_ids: frozenset[str]

    def context(self) -> MeetingContext:
        """``MeetingContext`` for step 3a (``None`` ids when the list was empty)."""
        return MeetingContext(self.person_ids if self.refs else None)


_INCOMPLETE = "incomplete"


def _attendee_lookup(
    raw: str, form: NameForm, reg: PeopleRegistry, fold: bool
) -> tuple[str, ...] | str | None:
    out = _step1(raw, form, reg, fold)
    if out is not None:
        return out.ids
    if form.kind is NameFormKind.FIRST_ONLY:
        c = reg.lookup_first_name(_fk(form.first, fold), fold=fold)
        if len(c) >= 2:
            return _INCOMPLETE
        if len(c) == 1:
            return c
    return None


def _resolve_attendee(att: Attendee, reg: PeopleRegistry, ordinal: int | None) -> MeetingAttendeeRef:
    raw = att.raw_name
    form = classify_name(raw)
    if form is None:
        return MeetingAttendeeRef(raw, None, att.email_hint)
    for fold in (False, True):  # steps 1, 2 (and the first-name rule), then 4, before creation
        found = _attendee_lookup(raw, form, reg, fold)
        if found == _INCOMPLETE:
            return MeetingAttendeeRef(
                raw, None, att.email_hint, (_codes.W_ATTENDEE_NAME_INCOMPLETE,)
            )
        if found:
            if len(found) != 1:
                return MeetingAttendeeRef(raw, None, att.email_hint)  # ambiguous: not attributed
            reg.note_spelling(found[0], raw, ordinal=ordinal, attendee=True)  # type: ignore[index]
            return MeetingAttendeeRef(raw, found[0], att.email_hint)  # type: ignore[index]
    person = reg.add_attendee(raw, ordinal=ordinal)
    return MeetingAttendeeRef(raw, person.id, att.email_hint)


def resolve_attendees(
    attendees: Iterable[Attendee | str], registry: PeopleRegistry, *, ordinal: int | None = None
) -> AttendeeResolution:
    """Resolve a meeting's attendee names (spec 10.2a, 10.3 step 4, AC-05.13).

    Full names are processed before initial forms and bare first names, so the outcome does not
    depend on the order of the list. Returns the refs (input order) and the set of person ids
    present (the ``A`` set of step 3a), nulls excluded.
    """
    items = [a if isinstance(a, Attendee) else Attendee(str(a)) for a in attendees]
    refs: list[MeetingAttendeeRef | None] = [None] * len(items)
    forms = [classify_name(a.raw_name) for a in items]
    phases = (
        [i for i, f in enumerate(forms) if f is not None and f.kind is NameFormKind.FULL],
        [i for i, f in enumerate(forms) if f is None or f.kind is not NameFormKind.FULL],
    )
    for phase in phases:
        for i in phase:
            refs[i] = _resolve_attendee(items[i], registry, ordinal)
    final = tuple(r for r in refs if r is not None)
    return AttendeeResolution(final, frozenset(r.person_id for r in final if r.person_id))


# =============================================================================
# Whole-database re-resolution pass (T-M4-11; spec 10.3 step 6, 10.7.9, H-05)
# =============================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class StoredAttendee:
    raw_name: str
    person_id: str | None = None
    email_hint: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class StoredMeeting:
    """A meeting's stored attendee RAW names (the pass rebuilds ``A`` from them, G-C20)."""

    meeting_id: int | str
    attendees: tuple[StoredAttendee, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class StoredOwner:
    """One stored ItemOwner row plus the meeting its owner phrase came from."""

    item_id: int
    ordinal: int
    meeting_id: int | str | None
    owner_raw: str | None
    status: OwnerStatus
    person_id: str | None = None
    candidates: tuple[str, ...] = ()
    provenance: Provenance = Provenance.NONE
    role: OwnerRole = OwnerRole.OWNER


@dataclasses.dataclass(frozen=True, slots=True)
class ReResolutionSnapshot:
    meetings: tuple[StoredMeeting, ...] = ()
    owners: tuple[StoredOwner, ...] = ()


OWNER_CHANGED = "owner_changed"
KIND_MERGE_PROVISIONAL = "merge_provisional"
KIND_DOWNGRADED = "downgraded_to_ambiguous"
KIND_RESOLVED = "resolved_from_ambiguous"
KIND_CHANGED = "changed"


@dataclasses.dataclass(frozen=True, slots=True)
class OwnerChange:
    """New owner fields of one ItemOwner row. Only owner fields are carried (H-05: never any
    change to sources, merges, links, completion or status). ``event`` is the ItemEvent kind to
    write; ``merged_or_completed_under_earlier_resolution`` is filled by the caller (T-M4-15)."""

    item_id: int
    ordinal: int
    owner_raw: str | None
    role: OwnerRole
    kind: str
    old_status: OwnerStatus
    old_person_id: str | None
    old_candidates: tuple[str, ...]
    old_provenance: Provenance
    new_status: OwnerStatus
    new_person_id: str | None
    new_candidates: tuple[str, ...]
    new_provenance: Provenance
    warnings: tuple[str, ...] = ()
    event: str = OWNER_CHANGED
    merged_or_completed_under_earlier_resolution: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class AttendeeChange:
    meeting_id: int | str
    raw_name: str
    old_person_id: str | None
    new_person_id: str | None
    warnings: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class ReResolutionResult:
    """Proposed changes. ``merges`` (provisional -> known, alias kept) must be applied first, then
    ``attendee_changes``, then ``changes``. ``registry`` is the resulting registry state (a copy;
    the input registry is never mutated); ``created_people`` are persons the pass had to add."""

    merges: tuple[MergeRecord, ...]
    attendee_changes: tuple[AttendeeChange, ...]
    changes: tuple[OwnerChange, ...]
    created_people: tuple[Person, ...]
    registry: PeopleRegistry

    @property
    def warnings(self) -> tuple[tuple[str, int, int, str | None], ...]:
        """Flat ``(code, item_id, ordinal, owner_raw)`` rows, in change order."""
        return tuple((w, c.item_id, c.ordinal, c.owner_raw) for c in self.changes for w in c.warnings)

    @property
    def is_empty(self) -> bool:
        return not (self.merges or self.attendee_changes or self.changes)


def merge_target(reg: PeopleRegistry, person: Person) -> str | None:
    """Known person a provisional/attendee person should be merged into (10.2a(d), 10.3 step 6):
    the person a roster alias pins an unverified (``note_owner``) name to, else -- for a non-full
    person -- the ONLY full person whose first name (single token) or first name + last initial
    (initial form) matches."""
    if person.origin is PersonOrigin.ROSTER:
        return None
    if person.origin is PersonOrigin.NOTE_OWNER:  # a roster alias pins an unverified name
        pinned = tuple(i for i in reg.lookup_alias(person.identity_key.split("|")[0]) if i != person.id)
        if len(pinned) == 1:
            return pinned[0]
    if is_full(person):
        return None
    toks = person.identity_key.split()
    if len(toks) == 1:
        cands = reg.lookup_first_name(toks[0])
    elif len(toks) == 2:
        cands = reg.lookup_initial(toks[0], toks[1][:1])
    else:
        return None
    cands = tuple(c for c in cands if c != person.id)
    return cands[0] if len(cands) == 1 else None


def _owner_key(o: StoredOwner | ResolvedOwner) -> tuple[Any, ...]:
    return (o.status, o.person_id, tuple(o.candidates), o.provenance)


def re_resolve(
    snapshot: ReResolutionSnapshot, registry: PeopleRegistry, cfg: Any = None
) -> ReResolutionResult:
    """Pure whole-database re-resolution (step 6, run on EVERY ingest, G-08).

    A) merge provisional/attendee-origin single-token or initial-form persons that now match
    exactly one known person; B) rebuild each meeting's attendee set from stored raw names against
    the current K (G-C20); C) re-run every stored owner through steps 1-5. Idempotent: applying
    the result (``apply_to_snapshot``) and running again yields no changes. Ordered by item id,
    ordinal (changes), meeting id (attendees), person id (merges).
    """
    reg = registry.copy()
    before_ids = set(reg.ids())

    merges: list[MergeRecord] = []
    for person in reg.people():
        target = merge_target(reg, person)
        if target is not None:
            merges.append(reg.merge_provisional(person.id, target))
    merged_ids = {m.provisional_id for m in merges}

    contexts: dict[int | str, MeetingContext] = {}
    attendee_changes: list[AttendeeChange] = []
    meetings = sorted(snapshot.meetings, key=lambda m: str(m.meeting_id).rjust(24))
    for n, meeting in enumerate(meetings):
        if not meeting.attendees:
            contexts[meeting.meeting_id] = NO_ATTENDEE_LIST
            continue
        res = resolve_attendees(
            [Attendee(a.raw_name, a.email_hint) for a in meeting.attendees], reg, ordinal=n
        )
        contexts[meeting.meeting_id] = res.context()
        for stored, ref in zip(meeting.attendees, res.refs, strict=True):
            if stored.person_id != ref.person_id:
                attendee_changes.append(
                    AttendeeChange(
                        meeting.meeting_id, stored.raw_name, stored.person_id, ref.person_id, ref.warnings
                    )
                )

    cache: dict[tuple[str, int | str | None], ResolvedOwner] = {}
    changes: list[OwnerChange] = []
    for owner in sorted(snapshot.owners, key=lambda o: (o.item_id, o.ordinal)):
        if owner.status is OwnerStatus.UNASSIGNED or not (owner.owner_raw or "").strip():
            continue
        ctx = contexts.get(owner.meeting_id, NO_ATTENDEE_LIST)
        ck = (owner.owner_raw or "", owner.meeting_id)
        base = cache.get(ck)
        if base is None:
            base = resolve_name(owner.owner_raw, ctx, reg, cfg)
            cache[ck] = base
        if _owner_key(base) == _owner_key(owner):
            continue
        if owner.person_id in merged_ids:
            kind = KIND_MERGE_PROVISIONAL
        elif owner.status is OwnerStatus.RESOLVED and base.status is OwnerStatus.AMBIGUOUS:
            kind = KIND_DOWNGRADED
        elif owner.status is OwnerStatus.AMBIGUOUS and base.status is OwnerStatus.RESOLVED:
            kind = KIND_RESOLVED
        else:
            kind = KIND_CHANGED
        warnings = (
            (_codes.W_OWNER_NOW_AMBIGUOUS,)
            if owner.status is OwnerStatus.RESOLVED and base.status is OwnerStatus.AMBIGUOUS
            else ()
        )
        changes.append(
            OwnerChange(
                item_id=owner.item_id,
                ordinal=owner.ordinal,
                owner_raw=owner.owner_raw,
                role=owner.role,
                kind=kind,
                old_status=owner.status,
                old_person_id=owner.person_id,
                old_candidates=tuple(owner.candidates),
                old_provenance=owner.provenance,
                new_status=base.status,
                new_person_id=base.person_id,
                new_candidates=base.candidates,
                new_provenance=base.provenance,
                warnings=warnings,
            )
        )
    created = tuple(p for p in reg.people() if p.id not in before_ids)
    return ReResolutionResult(
        tuple(merges), tuple(attendee_changes), tuple(changes), created, reg
    )


def apply_to_snapshot(snapshot: ReResolutionSnapshot, result: ReResolutionResult) -> ReResolutionSnapshot:
    """Snapshot after the result's attendee and owner changes (pure; merges are in
    ``result.registry``). Lets callers and tests check idempotence."""
    att = {(c.meeting_id, c.raw_name): c.new_person_id for c in result.attendee_changes}
    meetings = tuple(
        dataclasses.replace(
            m,
            attendees=tuple(
                dataclasses.replace(a, person_id=att.get((m.meeting_id, a.raw_name), a.person_id))
                for a in m.attendees
            ),
        )
        for m in snapshot.meetings
    )
    ch = {(c.item_id, c.ordinal): c for c in result.changes}
    owners = []
    for o in snapshot.owners:
        c = ch.get((o.item_id, o.ordinal))
        if c is None:
            owners.append(o)
        else:
            owners.append(
                dataclasses.replace(
                    o,
                    status=c.new_status,
                    person_id=c.new_person_id,
                    candidates=c.new_candidates,
                    provenance=c.new_provenance,
                )
            )
    return ReResolutionSnapshot(meetings, tuple(owners))


# =============================================================================
# Ambiguity model and surfacing hooks (T-M4-12; spec 10.2a rule 1, 10.4, FR-02.9)
# =============================================================================


def candidate_key(candidates: Iterable[str]) -> tuple[str, ...]:
    """Canonical sorted, de-duplicated tuple of person ids (grouping and DB storage)."""
    return tuple(sorted(set(candidates)))


def _short_label(reg: PeopleRegistry, person_id: str, lead_first: str) -> str:
    p = reg.get(person_id)
    if p is None:
        return person_id
    if p.last_name is not None and name_key(p.first_name).split()[:1] == [lead_first]:
        return p.last_name + (f" ({p.qualifier})" if p.qualifier else "")
    return reg.full_name(person_id)


def owner_display(owner: Any, registry: PeopleRegistry) -> OwnerDisplay:
    """Structured display of one owner: ``(text, flags)`` (spec 10.2a rule 1, 10.4, AC-05.11).

    * resolved namesake: the full name; resolved non-namesake: the display name;
    * ambiguous: ``?Priya (Nair | Shah)`` (candidates with another first name are shown as full
      names), flag ``OA``;
    * provisional / unassigned: the raw text as written, flag ``OU``.
    """
    status = OwnerStatus(owner.status)
    raw = getattr(owner, "owner_raw", None)
    if status is OwnerStatus.AMBIGUOUS:
        cands = candidate_key(owner.candidates)
        ordered = sorted(cands, key=lambda c: (registry.full_name(c).casefold() if c in registry else c, c))
        lead_tokens = (raw or "").split()
        lead = lead_tokens[0] if lead_tokens else ""
        if not lead and ordered and (p := registry.get(ordered[0])) is not None:
            lead = p.first_name
        lead_key = (name_key(lead).split() or [""])[0]
        labels = " | ".join(_short_label(registry, c, lead_key) for c in ordered)
        return OwnerDisplay(f"?{lead} ({labels})", (FLAG_OWNER_AMBIGUOUS,))
    if status in (OwnerStatus.RESOLVED, OwnerStatus.PROVISIONAL) and owner.person_id in registry:
        pid = owner.person_id
        p = registry.get(pid)
        if status is OwnerStatus.PROVISIONAL:
            return OwnerDisplay(raw or registry.display_name(pid), (FLAG_OWNER_UNASSIGNED,))
        if p is not None and is_full(p) and registry.is_namesake(pid):
            return OwnerDisplay(registry.full_name(pid))
        return OwnerDisplay(registry.display_name(pid))
    return OwnerDisplay(raw or "", (FLAG_OWNER_UNASSIGNED,))


def candidate_lines(candidates: Iterable[str], registry: PeopleRegistry) -> tuple[str, ...]:
    """``Full Name <email>`` lines, sorted (exit-6 messages, AC-04.6, and report rows). A person
    without an email is shown as ``Full Name (no email)``."""
    lines = []
    for pid in candidate_key(candidates):
        if pid not in registry:
            lines.append(pid)
            continue
        email = registry.get(pid).email  # type: ignore[union-attr]
        name = registry.full_name(pid)
        lines.append(f"{name} <{email}>" if email else f"{name} (no email)")
    return tuple(sorted(lines, key=str.casefold))


def _item_matches_status(item: Any, status: str) -> bool:
    if status == "all":
        return True
    return str(item.status) == status


def _ambiguous_owners(item: Any) -> list[Any]:
    return [o for o in item.owners if OwnerStatus(o.status) is OwnerStatus.AMBIGUOUS]


def group_ambiguous(
    items: Iterable[Any], registry: PeopleRegistry, status: str = "open"
) -> tuple[AmbiguousGroup, ...]:
    """``list --ambiguous`` grouping (FR-02.9, AC-02.11): items with an ambiguous owner, filtered by
    item status (default ``open``; ``done``; ``all``), grouped by candidate set; groups sorted by
    candidate full names, items by id. Items are objects with ``item_id``, ``status``, ``owners``."""
    if status not in ("open", "done", "all"):
        raise ValueError(f"status must be open, done or all, got {status!r}")
    groups: dict[tuple[str, ...], dict[int, Any]] = {}
    for item in items:
        if not _item_matches_status(item, status):
            continue
        for o in _ambiguous_owners(item):
            groups.setdefault(candidate_key(o.candidates), {})[item.item_id] = item

    def names(c: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(registry.full_name(i) if i in registry else i for i in c)

    out = [
        AmbiguousGroup(c, names(c), tuple(v[k] for k in sorted(v))) for c, v in groups.items()
    ]
    out.sort(key=lambda g: (tuple(n.casefold() for n in g.names), g.candidates))
    return tuple(out)


def ambiguous_report_records(items: Iterable[Any], registry: PeopleRegistry) -> tuple[AmbiguousRecord, ...]:
    """Ingest-report hook: one record per ambiguous owner of an OPEN item (the report section is
    open-scoped, 4.2): ``(item_id, file:line, raw, candidate full names)``, by item id, ordinal."""
    rows: list[tuple[int, int, AmbiguousRecord]] = []
    for item in items:
        if not _item_matches_status(item, "open"):
            continue
        for o in _ambiguous_owners(item):
            cands = candidate_key(o.candidates)
            names = tuple(
                sorted((registry.full_name(c) if c in registry else c for c in cands), key=str.casefold)
            )
            rows.append(
                (
                    item.item_id,
                    getattr(o, "ordinal", 0),
                    AmbiguousRecord(item.item_id, getattr(item, "location", None), o.owner_raw, names),
                )
            )
    rows.sort(key=lambda r: (r[0], r[1]))
    return tuple(r[2] for r in rows)


def owner_matches_person(item_owners: Sequence[Any], person_id: str, include_ambiguous: bool) -> bool:
    """True when a resolved owner is ``person_id``, or (``include_ambiguous``) an ambiguous owner's
    candidates include it. Ambiguous items never match without the flag (BR-14)."""
    for o in item_owners:
        status = OwnerStatus(o.status)
        if status is OwnerStatus.AMBIGUOUS:
            if include_ambiguous and person_id in o.candidates:
                return True
        elif o.person_id == person_id:
            return True
    return False
