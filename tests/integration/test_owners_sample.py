"""T-M4-14: M4 acceptance suite for owner resolution, grounded in the sample notes.

Drives the real pure pipeline (no DB) over ``tests/fixtures/notes`` in ingest order
(``ingest_order_key``): parse each file with ``OwnerAnalyzer``, resolve the meeting's attendees into
one shared ``PeopleRegistry`` (roster = ``tests/fixtures/config/valid_full.toml``, 9 people), resolve
every owner mention with that meeting's context, and compare with ``tests/golden/expected_owners.csv``.

The CSV was authored BY HAND from spec 10.1-10.4 (attendee lists per meeting, roster, AC-05.x, Appendix
A.3), one row per ItemOwner row (117 items, 119 owner rows: ``Anita & Rahul`` has two owners and
``Lena (with Priya)`` has owner + ``with``). It is NOT a dump of implementation output.

Verify:      ``pytest tests/integration/test_owners_sample.py``
Regenerate:  never paste output of ``actiontrack`` into the CSV. To change a row, re-derive it from the
             note's attendee list and spec 10.3: bare namesake first name + exactly one namesake attending
             -> ``attendee_in_meeting``; both/none attending or no attendee list -> ``ambiguous``;
             ``First L.`` -> ``initial_form``; unique first name -> ``unique_first_name``; non-person word
             -> ``unassigned``. Columns: file,line,ordinal,owner_raw,role,status,persons ('|' joined
             candidates for ambiguous),provenance,display.

Verified totals (equal to the task block / spec A.3): 117 items, 119 owner rows = 107 resolved
(36 attendee_in_meeting, 4 initial_form, 67 unique_first_name) + 7 ambiguous + 5 unassigned;
0 provisional, 0 diacritic_fold, 9 Person rows (all roster).

Known gap: ``PeopleRegistry`` has no ``is_known`` predicate (the parser's ``is_known_name`` hook is fed by
``_make_known`` below, built from the ``lookup_*`` queries); M5 must provide the same predicate.
"""

from __future__ import annotations

import csv
import dataclasses
import random
from collections import Counter
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.config import Config, Owners, load_config, parse_roster
from actiontrack.domain.models import (
    NameFormKind,
    OwnerRole,
    OwnerStatus,
    ParsedFile,
    ParsedItem,
    PersonOrigin,
    Provenance,
    ResolvedOwner,
    ingest_order_key,
)
from actiontrack.domain.names import classify_name, identity_key, name_key
from actiontrack.parsing import parse_file
from actiontrack.parsing.discovery import discover
from actiontrack.parsing.owner_phrase import OwnerAnalyzer
from actiontrack.resolution import owners as O
from actiontrack.resolution.people import PeopleRegistry
from tests.conftest import FIXTURES_NOTES, REPO_ROOT

GOLDEN = REPO_ROOT / "tests" / "golden" / "expected_owners.csv"
ROSTER_TOML = REPO_ROOT / "tests" / "fixtures" / "config" / "valid_full.toml"

ROSTER_IDS = {
    "priya-nair", "priya-shah", "rahul-mehta", "rahul-verma", "anita-desai",
    "sam-okafor", "meera-iyer", "karan-joshi", "lena-fischer",
}  # fmt: skip
AMBIGUOUS_ITEMS = {  # AC-05.14 / Appendix A.3 (file, line)
    ("2026-09-17-billing-migration-sync.md", 9),
    ("2026-09-23-standup.md", 8),
    ("2026-09-30-q4-offsite.md", 16),
    ("2026-09-30-q4-offsite.md", 17),
    ("2026-10-07-standup.md", 9),
    ("2026-10-08-billing-go-no-go.md", 9),
    ("untitled.md", 2),
}
UNASSIGNED_ITEMS = {  # spec A.3 / task block
    ("2026-09-10-acme-renewal-prep.md", 12): "Someone",
    ("2026-09-18-design-review.md", 14): "Everyone",
    ("2026-09-18-design-review.md", 15): "TBD",
    ("2026-09-30-q4-offsite.md", 18): "Everyone",
    ("2026-10-01-quote-portal-weekly.md", 12): "Team",
}
Key = tuple[str, int, int]


# ------------------------------------------------------------------ the hand-written table
@dataclasses.dataclass(frozen=True)
class Row:
    file: str
    line: int
    ordinal: int
    owner_raw: str
    role: str
    status: str
    persons: str
    provenance: str
    display: str

    @property
    def key(self) -> Key:
        return (self.file, self.line, self.ordinal)


def _golden() -> dict[Key, Row]:
    with open(GOLDEN, encoding="utf-8", newline="") as fh:
        rows = [Row(r["file"], int(r["line"]), int(r["ordinal"]), r["owner_raw"], r["role"], r["status"],
                    r["persons"], r["provenance"], r["display"]) for r in csv.DictReader(fh)]  # fmt: skip
    table = {r.key: r for r in rows}
    assert len(table) == len(rows), "duplicate key in expected_owners.csv"
    return table


# ------------------------------------------------------------------ pipeline under test
def _roster_config(attendance: bool = True) -> Config:
    cfg = load_config(ROSTER_TOML)
    return dataclasses.replace(cfg, owners=Owners(attendee_disambiguation=attendance))


def _make_known(reg: PeopleRegistry):
    """``is_known_name`` predicate for the parser, from the registry's lookups (no ``is_known`` exists)."""

    def known(raw: str) -> bool:
        form = classify_name(raw)
        if form is None:
            return False
        if reg.lookup_alias(name_key(raw)):
            return True
        if form.kind is NameFormKind.FULL:
            return bool(reg.lookup_identity(identity_key(raw)))
        if form.kind is NameFormKind.FIRST_INITIAL:
            return bool(reg.lookup_initial(form.first, form.initial or ""))
        return bool(reg.lookup_first_name(form.first))

    return known


def _roster_registry(cfg: Config | None) -> PeopleRegistry:
    reg = PeopleRegistry()
    if cfg is not None:
        reg.add_roster(cfg.roster)
    return reg


def _parse_all(cfg: Config | None, order: list[int] | None = None) -> list[ParsedFile]:
    """Parse every discovered file (optionally in a permuted order); the parser sees the roster only."""
    files = list(discover(FIXTURES_NOTES).files)
    if order is not None:
        files = [files[i] for i in order]
    analyzer = OwnerAnalyzer(is_known_name=_make_known(_roster_registry(cfg)))
    return [parse_file(f, analyzer=analyzer) for f in files]


@dataclasses.dataclass
class Run:
    owners: dict[Key, ResolvedOwner]
    items: dict[tuple[str, int], ParsedItem]
    contexts: dict[str, O.MeetingContext]
    parsed: dict[str, ParsedFile]
    registry: PeopleRegistry
    cfg: Config
    processed: list[str]
    snapshot: O.ReResolutionSnapshot

    def display(self, key: Key):
        return O.owner_display(self.owners[key], self.registry)


def _process(parsed: list[ParsedFile], cfg: Config, reg: PeopleRegistry) -> Run:
    """What ingest does per file, in ingest order: attendees into the registry, then every owner."""
    owners: dict[Key, ResolvedOwner] = {}
    items: dict[tuple[str, int], ParsedItem] = {}
    contexts: dict[str, O.MeetingContext] = {}
    stored_meetings: list[O.StoredMeeting] = []
    stored_owners: list[O.StoredOwner] = []
    processed: list[str] = []
    item_id = 0
    for n, pf in enumerate(sorted(parsed, key=ingest_order_key)):
        if pf.status != "ok":
            continue
        processed.append(pf.rel_path)
        att = O.resolve_attendees(pf.meeting.attendees, reg, ordinal=n)
        ctx = att.context()
        contexts[pf.rel_path] = ctx
        stored_meetings.append(
            O.StoredMeeting(
                pf.rel_path, tuple(O.StoredAttendee(r.raw_name, r.person_id, r.email_hint) for r in att.refs)
            )
        )
        for item in pf.items:
            items[(pf.rel_path, item.line_start)] = item
            item_id += 1
            phrase = item.owner_hook
            if phrase is None:  # no owner phrase at all (W-OWNER-MISSING); the sample has none
                resolved = [ResolvedOwner(0, None, None, OwnerStatus.UNASSIGNED)]
            elif phrase.nonperson_raw is not None:
                resolved = [ResolvedOwner(0, phrase.nonperson_raw, None, OwnerStatus.UNASSIGNED)]
            else:
                resolved = [
                    O.resolve_owner(m, ctx, reg, cfg, ordinal=k) for k, m in enumerate(phrase.mentions)
                ]
            for r in resolved:
                owners[(pf.rel_path, item.line_start, r.ordinal)] = r
                stored_owners.append(
                    O.StoredOwner(item_id, r.ordinal, pf.rel_path, r.owner_raw, r.status, r.person_id,
                                  tuple(r.candidates), r.provenance, r.role)  # fmt: skip
                )
    return Run(owners, items, contexts, {p.rel_path: p for p in parsed}, reg, cfg, processed,
               O.ReResolutionSnapshot(tuple(stored_meetings), tuple(stored_owners)))  # fmt: skip


def run_pipeline(attendance: bool = True, order: list[int] | None = None, roster: bool = True) -> Run:
    cfg = _roster_config(attendance)
    parsed = _parse_all(cfg if roster else None, order)
    return _process(parsed, cfg, _roster_registry(cfg if roster else None))


@pytest.fixture(scope="module")
def run() -> Run:
    return run_pipeline()


@pytest.fixture(scope="module")
def golden() -> dict[Key, Row]:
    return _golden()


def _persons(r: ResolvedOwner) -> str:
    return "|".join(sorted(r.candidates)) if r.status is OwnerStatus.AMBIGUOUS else (r.person_id or "")


def _actual(run_: Run, key: Key) -> tuple:
    r = run_.owners[key]
    return (r.owner_raw, r.role.value, r.status.value, _persons(r), r.provenance.value, run_.display(key).text)


def _expected(row: Row) -> tuple:
    return (row.owner_raw, row.role, row.status, row.persons, row.provenance, row.display)


def _rows_where(run_: Run, **want: str) -> set[Key]:
    out = set()
    for k, r in run_.owners.items():
        got = {"status": r.status.value, "provenance": r.provenance.value, "role": r.role.value}
        if all(got[a] == b for a, b in want.items()):
            out.add(k)
    return out


# ------------------------------------------------------------------ corpus shape
@pytest.mark.ac("AC-05.14")
def test_corpus_shape(run: Run, golden: dict[Key, Row]) -> None:
    files = discover(FIXTURES_NOTES).files
    assert len(files) == 35 and not any(f.rel_path.endswith(".txt") for f in files)
    assert sum(len(p.items) for p in run.parsed.values()) == 117
    assert run.parsed["scratch.md"].status == "failed"  # binary leftover: no items
    assert run.parsed["2026-09-09-notes-final-v2.md"].status == "empty"
    assert len(run.processed) == 33  # spec A.3: untitled.md is one of the 33 ingested files
    assert run.processed[-2:] == ["2026-10-03-sales-pipeline.md", "untitled.md"]  # null dates last
    assert len(golden) == 119
    assert {k[:2] for k in golden} == set(run.items)  # one CSV group per sample item, none extra
    assert len(run.items) == 117


# ------------------------------------------------------------------ every row against the CSV
@pytest.mark.ac("AC-05.1", "AC-05.2", "AC-05.3", "AC-05.4", "AC-05.5", "AC-05.9", "AC-05.18")
@pytest.mark.sc("SC-05", "SC-07", "SC-44", "SC-45", "SC-46", "SC-48", "SC-50", "SC-51")
def test_every_owner_row_matches_hand_derived_csv(run: Run, golden: dict[Key, Row]) -> None:
    assert set(run.owners) == set(golden), (set(run.owners) ^ set(golden))
    bad = {k: (_expected(golden[k]), _actual(run, k)) for k in golden if _actual(run, k) != _expected(golden[k])}
    assert bad == {}


# ------------------------------------------------------------------ aggregate facts
@pytest.mark.ac("AC-05.14", "AC-05.18")
def test_aggregate_status_and_provenance_counts(run: Run) -> None:
    assert len(run.owners) == 119
    by_status = Counter(r.status.value for r in run.owners.values())
    assert by_status == {"resolved": 107, "ambiguous": 7, "unassigned": 5}
    by_prov = Counter(r.provenance.value for r in run.owners.values() if r.status is OwnerStatus.RESOLVED)
    assert by_prov == {"attendee_in_meeting": 36, "initial_form": 4, "unique_first_name": 67}
    for none_of in ("alias", "roster", "provisional", "attendee", "diacritic_fold"):
        assert none_of not in by_prov
    assert not any(r.warnings for r in run.owners.values())  # no W-NAME-DIACRITIC etc.
    assert not any(r.status is OwnerStatus.PROVISIONAL for r in run.owners.values())  # 0 provisional


@pytest.mark.ac("AC-05.18")
def test_attendance_rows_are_exactly_the_namesake_items(run: Run) -> None:
    am = _rows_where(run, provenance="attendee_in_meeting")
    assert len(am) == 36
    assert {run.owners[k].owner_raw for k in am} == {"Priya", "Rahul"}
    # every first-name-only Sam/Anita/Meera/Karan/Lena row is unique_first_name (even with an attendee list)
    for k, r in run.owners.items():
        if (r.owner_raw or "").casefold() in {"sam", "anita", "meera", "karan", "lena"}:
            assert r.provenance is Provenance.UNIQUE_FIRST_NAME, k
    # Priya N./S. rows are initial_form (AC-05.18)
    initial = _rows_where(run, provenance="initial_form")
    assert {run.owners[k].owner_raw for k in initial} == {"Priya N.", "Priya S."}
    assert len(initial) == 4


@pytest.mark.ac("AC-05.14")
@pytest.mark.sc("SC-53")
def test_exactly_the_seven_ambiguous_items_of_appendix_a3(run: Run) -> None:
    amb = _rows_where(run, status="ambiguous")
    assert {k[:2] for k in amb} == AMBIGUOUS_ITEMS and len(amb) == 7
    for k in amb:
        r = run.owners[k]
        assert r.provenance is Provenance.NONE and r.person_id is None
        assert len(r.candidates) == 2
        assert run.items[k[:2]].checkbox_state == "open"  # AC-05.14 default (open) scope == all scope here
    # open-scoped hooks agree with the all-items set (no item is done)
    states = {k[:2]: it.checkbox_state for k, it in run.items.items()}
    open_amb = {k[:2] for k in amb if states[k[:2]] == "open"}
    assert open_amb == AMBIGUOUS_ITEMS
    # which of the sample items are done at all (recorded, review before changing): 13 per spec A.3
    assert Counter(states.values()) == {"open": 104, "done": 13}


@pytest.mark.ac("AC-05.5")
@pytest.mark.sc("SC-05")
def test_unassigned_non_person_owners(run: Run) -> None:
    un = _rows_where(run, status="unassigned")
    assert {k[:2]: run.owners[k].owner_raw for k in un} == UNASSIGNED_ITEMS
    for k in un:
        assert run.owners[k].person_id is None and run.owners[k].candidates == ()
        assert run.items[k[:2]].checkbox_state == "open"
        d = run.display(k)
        assert d.text == run.owners[k].owner_raw and d.flags == ("OU",)
    # every sample item has an owner phrase of some kind (W-OWNER-MISSING never needed on the sample)
    assert all(it.owner_hook is not None for it in run.items.values())


@pytest.mark.ac("AC-05.4", "AC-05.9")
@pytest.mark.sc("SC-51")
def test_multi_owner_and_special_phrasings(run: Run) -> None:
    f = "2026-09-18-design-review.md"
    multi = {k[:2] for k in run.owners if k[2] > 0}
    assert multi == {(f, 12), (f, 13)}
    # @anita: -> Anita Desai
    r = run.owners[(f, 11, 0)]
    assert (r.owner_raw, r.person_id, r.status) == ("anita", "anita-desai", OwnerStatus.RESOLVED)
    # Anita & Rahul -> Anita Desai + Rahul Mehta (the only Rahul attending), both role owner
    a, b = run.owners[(f, 12, 0)], run.owners[(f, 12, 1)]
    assert (a.person_id, a.role) == ("anita-desai", OwnerRole.OWNER)
    assert (b.person_id, b.role, b.provenance) == ("rahul-mehta", OwnerRole.OWNER, Provenance.ATTENDEE_IN_MEETING)
    # Lena (with Priya) -> Lena Fischer owner + Priya Nair with
    lena, priya = run.owners[(f, 13, 0)], run.owners[(f, 13, 1)]
    assert (lena.person_id, lena.role) == ("lena-fischer", OwnerRole.OWNER)
    assert (priya.person_id, priya.role) == ("priya-nair", OwnerRole.WITH)
    # Rahul to review Anita's PR -> Rahul only (ambiguous, both Rahuls attend); Anita is not an owner
    g = "2026-10-07-standup.md"
    owners_of_item = [k for k in run.owners if k[:2] == (g, 9)]
    assert len(owners_of_item) == 1
    r = run.owners[(g, 9, 0)]
    assert r.owner_raw == "Rahul" and r.status is OwnerStatus.AMBIGUOUS
    assert r.candidates == ("rahul-mehta", "rahul-verma")
    assert "Anita" not in {run.owners[k].owner_raw for k in owners_of_item}


@pytest.mark.ac("AC-05.2")
@pytest.mark.sc("SC-46", "SC-50")
def test_notes_without_attendee_list(run: Run) -> None:
    for name in ("untitled.md", "2026-09-23-standup.md"):
        assert run.contexts[name].attendee_ids is None
        codes_ = {w.code for w in run.parsed[name].warnings}
        assert codes.W_ATTENDEES_MISSING in codes_
    assert run.owners[("untitled.md", 1, 0)].provenance is Provenance.UNIQUE_FIRST_NAME  # Sam
    assert run.owners[("untitled.md", 1, 0)].person_id == "sam-okafor"
    assert run.owners[("untitled.md", 2, 0)].status is OwnerStatus.AMBIGUOUS  # Priya
    assert run.owners[("2026-09-23-standup.md", 8, 0)].status is OwnerStatus.AMBIGUOUS  # Rahul
    # null meeting dates do not change owner resolution
    assert run.parsed["untitled.md"].meeting.date is None
    assert run.parsed["2026-10-03-sales-pipeline.md"].meeting.date is None
    assert run.owners[("2026-10-03-sales-pipeline.md", 6, 0)].person_id == "meera-iyer"


@pytest.mark.ac("AC-05.1", "AC-05.3")
@pytest.mark.sc("SC-07", "SC-08", "SC-44")
def test_both_namesakes_attend_bare_ambiguous_but_initials_resolve(run: Run) -> None:
    both_priya = {"2026-09-17-billing-migration-sync.md", "2026-09-29-billing-cutover-planning.md",
                  "2026-10-08-billing-go-no-go.md", "2026-09-30-q4-offsite.md"}  # fmt: skip
    for name in both_priya:
        assert {"priya-nair", "priya-shah"} <= run.contexts[name].attendee_ids, name
    for k in (("2026-09-17-billing-migration-sync.md", 9, 0), ("2026-10-08-billing-go-no-go.md", 9, 0)):
        assert run.owners[k].status is OwnerStatus.AMBIGUOUS
        assert run.owners[k].candidates == ("priya-nair", "priya-shah")
    s = run.owners[("2026-09-29-billing-cutover-planning.md", 9, 0)]
    n = run.owners[("2026-09-29-billing-cutover-planning.md", 10, 0)]
    n2 = run.owners[("2026-10-08-billing-go-no-go.md", 12, 0)]
    assert (s.person_id, s.provenance) == ("priya-shah", Provenance.INITIAL_FORM)
    assert (n.person_id, n.provenance) == ("priya-nair", Provenance.INITIAL_FORM)
    assert (n2.person_id, n2.provenance) == ("priya-nair", Provenance.INITIAL_FORM)


@pytest.mark.ac("AC-05.1")
@pytest.mark.sc("SC-48")
def test_offsite_all_four_namesakes_attend(run: Run) -> None:
    ctx = run.contexts["2026-09-30-q4-offsite.md"]
    assert {"priya-nair", "priya-shah", "rahul-mehta", "rahul-verma"} <= ctx.attendee_ids
    assert len(ctx.attendee_ids) == 9
    assert run.owners[("2026-09-30-q4-offsite.md", 16, 0)].candidates == ("priya-nair", "priya-shah")
    assert run.owners[("2026-09-30-q4-offsite.md", 17, 0)].candidates == ("rahul-mehta", "rahul-verma")
    # nested child, '*' and '+' items are owned like any other
    assert run.owners[("2026-09-30-q4-offsite.md", 10, 0)].person_id == "karan-joshi"
    assert run.owners[("2026-09-30-q4-offsite.md", 11, 0)].person_id == "lena-fischer"
    assert run.owners[("2026-09-30-q4-offsite.md", 12, 0)].person_id == "sam-okafor"


def test_no_label_text_becomes_an_owner(run: Run) -> None:
    allowed = {"Priya", "Rahul", "Anita", "anita", "Sam", "Meera", "Karan", "Lena", "Priya N.", "Priya S.",
               "Someone", "Everyone", "TBD", "Team"}  # fmt: skip
    assert {r.owner_raw for r in run.owners.values()} <= allowed
    # ACTION: / table-row lines are not checkbox items, hence no owner rows
    assert ("2026-09-30-q4-offsite.md", 24) not in run.items
    assert ("2026-09-30-q4-offsite.md", 22) not in run.items


# ------------------------------------------------------------------ displays and identities
@pytest.mark.ac("AC-05.11")
@pytest.mark.sc("SC-52")
def test_displays_never_show_bare_namesake_first_name_for_resolved(run: Run) -> None:
    for k, r in run.owners.items():
        d = run.display(k)
        if r.status is OwnerStatus.RESOLVED:
            assert d.text == run.registry.full_name(r.person_id), k
            assert d.text not in {"Priya", "Rahul"}
            assert d.flags == ()
        elif r.status is OwnerStatus.AMBIGUOUS:
            assert d.flags == ("OA",)
            assert d.text in {"?Priya (Nair | Shah)", "?Rahul (Mehta | Verma)"}, d.text
        else:
            assert d.flags == ("OU",)
    # a bare namesake first name appears only as the lead of an ambiguous display
    bare = [run.display(k).text for k, r in run.owners.items()
            if run.display(k).text.split(" ")[0].lstrip("?") in {"Priya", "Rahul"}
            and run.display(k).text in {"Priya", "Rahul"}]  # fmt: skip
    assert bare == []


@pytest.mark.ac("AC-05.10")
@pytest.mark.sc("SC-52")
def test_one_person_row_per_identity_no_provisional(run: Run) -> None:
    people = run.registry.people()
    assert {p.id for p in people} == ROSTER_IDS and len(people) == 9
    assert {p.origin for p in people} == {PersonOrigin.ROSTER}
    assert {p.identity_key for p in people} == {p.id.replace("-", " ") for p in people}
    assert not [p for p in people if p.last_name is None]  # no provisional / first-name-only Priya or Rahul
    assert run.registry.namesake_groups().keys() == {"priya", "rahul"}
    # every resolved id is a roster person and every candidate too
    used = {r.person_id for r in run.owners.values() if r.person_id} | {
        c for r in run.owners.values() for c in r.candidates
    }
    assert used <= ROSTER_IDS
    # every attendee of every meeting was attributed (no new people from attendee lists)
    for pf in run.parsed.values():
        if pf.status == "ok":
            assert len(run.contexts[pf.rel_path].attendee_ids or ()) == len(pf.meeting.attendees)


# ------------------------------------------------------------------ attendee_disambiguation=false
@pytest.mark.ac("AC-05.8")
@pytest.mark.sc("SC-65")
def test_attendance_disabled_makes_every_shared_first_name_ambiguous(
    run: Run, golden: dict[Key, Row]
) -> None:
    off = run_pipeline(attendance=False)
    assert set(off.owners) == set(golden)
    for key, row in golden.items():
        r = off.owners[key]
        if row.provenance == "attendee_in_meeting" or (row.status == "ambiguous"):
            assert r.status is OwnerStatus.AMBIGUOUS, key
            assert r.provenance is Provenance.NONE and r.person_id is None
            assert len(r.candidates) == 2
        else:  # initial forms, unique first names, unassigned: unchanged
            assert _actual(off, key) == _expected(row), key
    assert len(_rows_where(off, status="ambiguous")) == 7 + 36
    assert not _rows_where(off, provenance="attendee_in_meeting")
    # the attendee-resolved "with" co-owner is ambiguous too (role kept)
    w = off.owners[("2026-09-18-design-review.md", 13, 1)]
    assert w.status is OwnerStatus.AMBIGUOUS and w.role is OwnerRole.WITH
    # persisted rows of the default run re-resolved with the switch off: exactly those 36 downgrade
    res = O.re_resolve(run.snapshot, run.registry, off.cfg)
    assert len(res.changes) == 36
    assert {c.kind for c in res.changes} == {O.KIND_DOWNGRADED}
    assert all(c.warnings == (codes.W_OWNER_NOW_AMBIGUOUS,) for c in res.changes)
    assert {c.old_provenance for c in res.changes} == {Provenance.ATTENDEE_IN_MEETING}
    assert not res.merges and not res.attendee_changes


# ------------------------------------------------------------------ order independence
@pytest.mark.ac("AC-05.2")
def test_parse_order_does_not_matter_when_processing_in_ingest_order(run: Run) -> None:
    n = len(discover(FIXTURES_NOTES).files)
    for seed in (1, 2, 3):
        order = list(range(n))
        random.Random(seed).shuffle(order)
        assert order != list(range(n))
        shuffled = run_pipeline(order=order)
        assert shuffled.processed == run.processed
        assert shuffled.owners == run.owners
        assert shuffled.registry == run.registry


# ------------------------------------------------------------------ re-resolution (step 6)
@pytest.mark.ac("AC-05.8", "AC-05.10")
def test_re_resolution_of_final_state_is_idempotent(run: Run) -> None:
    res = O.re_resolve(run.snapshot, run.registry, run.cfg)
    assert res.is_empty, (res.merges, res.attendee_changes, res.changes)
    assert not res.created_people
    again = O.re_resolve(O.apply_to_snapshot(run.snapshot, res), res.registry, run.cfg)
    assert again.is_empty
    assert res.registry == run.registry  # the pass never mutated or grew K


def _roster_entries_with_second_sam() -> list[dict]:
    cfg = load_config(ROSTER_TOML)
    entries = [
        {"first_name": p.first_name, "last_name": p.last_name, "email": p.email or "", "aliases": list(p.aliases)}
        for p in cfg.roster
    ]
    entries.append({"first_name": "Sam", "last_name": "Brown", "email": "", "aliases": []})
    return entries


@pytest.mark.ac("AC-05.2")
@pytest.mark.sc("SC-50")
def test_second_sam_downgrades_where_no_attendance_evidence(run: Run) -> None:
    """Spec 10.3 3a: with a second Sam the namesake rule applies to `Sam` too. Where the note lists Sam Okafor
    among its attendees the item stays resolved (now by attendance); where the note has no attendee list
    (09-23, untitled) it is downgraded with W-OWNER-NOW-AMBIGUOUS. Attendance-resolved Priya/Rahul rows
    are untouched."""
    reg = run.registry.copy()
    reg.add_roster(parse_roster(_roster_entries_with_second_sam()))
    res = O.re_resolve(run.snapshot, reg, run.cfg)
    sam_rows = {k for k, r in run.owners.items() if r.owner_raw == "Sam"}
    assert len(sam_rows) == 16
    by_item = {(o.item_id, o.ordinal): k for k, o in zip(run.owners, run.snapshot.owners, strict=True)}
    changed = {by_item[(c.item_id, c.ordinal)]: c for c in res.changes}
    assert set(changed) == sam_rows  # nothing but Sam rows changes
    downgraded = {k for k, c in changed.items() if c.new_status is OwnerStatus.AMBIGUOUS}
    assert {k[:2] for k in downgraded} == {("2026-09-23-standup.md", 9), ("untitled.md", 1)}
    for k in downgraded:
        c = changed[k]
        assert c.warnings == (codes.W_OWNER_NOW_AMBIGUOUS,) and c.kind == O.KIND_DOWNGRADED
        assert c.new_candidates == ("sam-brown", "sam-okafor")
        assert c.old_provenance is Provenance.UNIQUE_FIRST_NAME
    for k in set(changed) - downgraded:  # attendance now decides: still Sam Okafor, no warning
        c = changed[k]
        assert (c.new_status, c.new_person_id, c.new_provenance) == (
            OwnerStatus.RESOLVED, "sam-okafor", Provenance.ATTENDEE_IN_MEETING)
        assert c.warnings == ()  # fmt: skip


@pytest.mark.ac("AC-05.2", "AC-05.8")
def test_second_sam_without_attendance_downgrades_every_unique_first_name_sam(run: Run) -> None:
    reg = run.registry.copy()
    reg.add_roster(parse_roster(_roster_entries_with_second_sam()))
    off = dataclasses.replace(run.cfg, owners=Owners(attendee_disambiguation=False))
    res = O.re_resolve(run.snapshot, reg, off)
    by_item = {(o.item_id, o.ordinal): k for k, o in zip(run.owners, run.snapshot.owners, strict=True)}
    changed = {by_item[(c.item_id, c.ordinal)]: c for c in res.changes}
    unique_sam = {k for k, r in run.owners.items()
                  if r.owner_raw == "Sam" and r.provenance is Provenance.UNIQUE_FIRST_NAME}  # fmt: skip
    attendance_rows = _rows_where(run, provenance="attendee_in_meeting")
    # exactly the unique_first_name Sam items and (attendance switched off) the attendance rows change
    assert {k for k, c in changed.items() if c.old_provenance is Provenance.UNIQUE_FIRST_NAME} == unique_sam
    assert set(changed) == unique_sam | attendance_rows
    assert all(changed[k].new_status is OwnerStatus.AMBIGUOUS for k in unique_sam)
    assert all(changed[k].warnings == (codes.W_OWNER_NOW_AMBIGUOUS,) for k in unique_sam)
    assert all(changed[k].new_candidates == ("sam-brown", "sam-okafor") for k in unique_sam)
    # attendance-resolved items are untouched when the switch stays ON (checked above) and the sources of
    # the downgrade are only owner fields (H-05): the change objects carry no item/source data
    names = {f.name for f in dataclasses.fields(O.OwnerChange)}
    assert not names & {"sources", "status_item", "links", "completed_by"}


# ------------------------------------------------------------------ no roster
@pytest.mark.ac("AC-05.10", "AC-05.2")
def test_no_roster_run_gives_same_people_and_status_as_roster_run(run: Run, golden: dict[Key, Row]) -> None:
    """Without a roster K is built from attendee lists in ingest order. Statuses, persons and displays agree
    with the roster run; only provenance can differ, and only where the namesake is not yet known (the first
    quote-portal / standup meetings, before the other Priya/Rahul has attended anywhere)."""
    nr = run_pipeline(roster=False)
    assert set(nr.owners) == set(golden)
    diff_prov = {}
    for key, row in golden.items():
        a = _actual(nr, key)
        assert a[2] == row.status and a[3] == row.persons and a[5] == row.display, (key, a, _expected(row))
        if a[4] != row.provenance:
            diff_prov[key] = (row.provenance, a[4])
    assert set(diff_prov.values()) <= {("attendee_in_meeting", "unique_first_name")}
    assert nr.registry.namesake_groups().keys() == {"priya", "rahul"}
    assert {p.origin for p in nr.registry.people()} == {PersonOrigin.ATTENDEE}
    assert {p.id for p in nr.registry.people()} == ROSTER_IDS
    assert all(p.email is None for p in nr.registry.people())  # emails only ever come from the roster
    # AC-05.18: not byte-identical provenance, and that is correct (K grows with ingested attendees)
    assert diff_prov  # first-seen meetings: Priya/Rahul with a single known namesake
