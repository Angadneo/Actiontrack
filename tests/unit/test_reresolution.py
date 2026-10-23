"""Whole-database re-resolution pass (T-M4-11; spec 10.3 step 6, 10.7.9, H-05, G-C20).

AC-05.2 (last sentence), AC-05.8, AC-05.17, AC-05.19 (no-undo contract), W-6.
"""

from __future__ import annotations

import dataclasses
import time

import pytest

from actiontrack import codes
from actiontrack.config import Owners, default_config
from actiontrack.domain.models import OwnerRole, OwnerStatus, Provenance
from actiontrack.resolution.owners import (
    KIND_DOWNGRADED,
    KIND_MERGE_PROVISIONAL,
    KIND_RESOLVED,
    NO_ATTENDEE_LIST,
    MeetingContext,
    OwnerChange,
    ReResolutionSnapshot,
    StoredAttendee,
    StoredMeeting,
    StoredOwner,
    apply_to_snapshot,
    re_resolve,
    resolve_attendees,
    resolve_name,
)
from actiontrack.resolution.people import PeopleRegistry
from tests.unit.test_people_registry import custom_roster, roster_registry

AMB = OwnerStatus.AMBIGUOUS
RES = OwnerStatus.RESOLVED
NAIR_SHAH = ("priya-nair", "priya-shah")
UFN = Provenance.UNIQUE_FIRST_NAME
AIM = Provenance.ATTENDEE_IN_MEETING


def owner(item, raw, status, pid=None, prov=Provenance.NONE, cands=(), meeting="A", ordinal=0):
    return StoredOwner(item, ordinal, meeting, raw, status, pid, tuple(cands), prov)


def snap(owners, meetings=()):
    return ReResolutionSnapshot(tuple(meetings), tuple(owners))


def cfg_off():
    return dataclasses.replace(default_config(), owners=Owners(attendee_disambiguation=False))


@pytest.mark.ac("AC-05.2")
def test_unique_first_name_downgraded_when_second_namesake_appears():
    reg = PeopleRegistry()
    resolve_attendees(["Priya Nair"], reg)  # file A has no attendee list
    first = resolve_name("Priya", NO_ATTENDEE_LIST, reg)
    assert first.provenance is UFN and first.person_id == "priya-nair"
    stored = owner(1, "Priya", RES, "priya-nair", UFN)
    assert re_resolve(snap([stored]), reg).is_empty  # nothing new yet

    resolve_attendees(["Priya Shah"], reg)  # file B: Priya Shah attends
    res = re_resolve(snap([stored]), reg)
    assert len(res.changes) == 1
    (ch,) = res.changes
    assert ch.kind == KIND_DOWNGRADED and ch.event == "owner_changed"
    assert (ch.new_status, ch.new_person_id, ch.new_candidates) == (AMB, None, NAIR_SHAH)
    assert ch.warnings == (codes.W_OWNER_NOW_AMBIGUOUS,)
    assert res.warnings == ((codes.W_OWNER_NOW_AMBIGUOUS, 1, 0, "Priya"),)
    assert not res.merges and not res.attendee_changes


def test_result_carries_only_owner_fields_no_undo_contract():
    names = {f.name for f in dataclasses.fields(OwnerChange)}
    forbidden = {"description", "due", "sources", "status_item", "item_status", "links", "completed", "merged"}
    assert not (names & forbidden)
    assert "merged_or_completed_under_earlier_resolution" in names
    ch = OwnerChange(1, 0, "Priya", OwnerRole.OWNER, "k", RES, "a", (), UFN, AMB, None, NAIR_SHAH, Provenance.NONE)
    assert ch.merged_or_completed_under_earlier_resolution is False
    assert dataclasses.replace(ch, merged_or_completed_under_earlier_resolution=True).new_status is AMB


def test_provisional_initial_form_created_before_person_known_is_merged():
    reg = PeopleRegistry()
    r = resolve_name("Priya N.", NO_ATTENDEE_LIST, reg)
    assert r.status is OwnerStatus.PROVISIONAL and r.person_id == "priya-n"
    stored = owner(7, "Priya N.", OwnerStatus.PROVISIONAL, "priya-n", Provenance.PROVISIONAL)

    resolve_attendees(["Priya Nair"], reg)
    before = reg.copy()
    res = re_resolve(snap([stored]), reg)
    assert reg == before  # the input registry is never mutated
    assert [(m.provisional_id, m.into_person_id, m.alias_key) for m in res.merges] == [
        ("priya-n", "priya-nair", "priya n")
    ]
    (ch,) = res.changes
    assert ch.kind == KIND_MERGE_PROVISIONAL and ch.event == "owner_changed"
    assert (ch.new_status, ch.new_person_id, ch.new_provenance) == (RES, "priya-nair", Provenance.INITIAL_FORM)
    assert ch.warnings == ()
    assert "priya-n" not in res.registry and res.registry.lookup_alias("priya n") == ("priya-nair",)
    assert res.registry.get("priya-nair") is not None
    # idempotent
    assert re_resolve(apply_to_snapshot(snap([stored]), res), res.registry).is_empty


def test_bare_first_name_provisional_merged_when_exactly_one_known_person():
    reg = PeopleRegistry()
    resolve_name("Priya", NO_ATTENDEE_LIST, reg)  # nobody known: provisional 'priya'
    resolve_attendees(["Priya Nair"], reg)
    stored = owner(1, "Priya", OwnerStatus.PROVISIONAL, "priya", Provenance.PROVISIONAL)
    res = re_resolve(snap([stored]), reg)
    assert [m.into_person_id for m in res.merges] == ["priya-nair"]
    (ch,) = res.changes
    assert ch.kind == KIND_MERGE_PROVISIONAL and ch.new_person_id == "priya-nair" and ch.new_status is RES


def test_bare_first_name_provisional_not_merged_into_namesakes():
    reg = roster_registry()
    reg.add_provisional("Priya")
    stored = owner(1, "Priya", OwnerStatus.PROVISIONAL, "priya", Provenance.PROVISIONAL)
    res = re_resolve(snap([stored]), reg)
    assert not res.merges
    (ch,) = res.changes
    assert (ch.new_status, ch.new_candidates) == (AMB, NAIR_SHAH)
    assert ch.warnings == ()  # provisional -> ambiguous is not a resolved -> ambiguous downgrade


@pytest.mark.ac("AC-05.17")
def test_roster_alias_pins_ambiguous_owner_with_no_file_input():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Shah"))
    stored = owner(3, "Priya", AMB, None, Provenance.NONE, NAIR_SHAH, meeting=None)
    assert re_resolve(snap([stored]), reg).is_empty

    reg.add_alias("priya-shah", "Priya")  # roster alias "Priya" -> Priya Shah
    res = re_resolve(snap([stored]), reg)
    (ch,) = res.changes
    assert (ch.kind, ch.new_status, ch.new_person_id, ch.new_provenance) == (
        KIND_RESOLVED, RES, "priya-shah", Provenance.ALIAS)
    assert ch.warnings == ()

    pinned = apply_to_snapshot(snap([stored]), res)
    reg2 = custom_roster(("Priya", "Nair"), ("Priya", "Shah"))  # alias removed again
    back = re_resolve(pinned, reg2)
    (ch2,) = back.changes
    assert (ch2.new_status, ch2.new_candidates) == (AMB, NAIR_SHAH)
    assert ch2.warnings == (codes.W_OWNER_NOW_AMBIGUOUS,)


@pytest.mark.ac("AC-05.17")
def test_alias_variant_priya_sales_with_edited_note():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Shah"))
    prov = resolve_name("Priya (Sales)", NO_ATTENDEE_LIST, reg)
    assert prov.status is OwnerStatus.PROVISIONAL
    stored = owner(4, "Priya (Sales)", OwnerStatus.PROVISIONAL, prov.person_id, Provenance.PROVISIONAL)
    reg.add_alias("priya-shah", "Priya (Sales)")
    res = re_resolve(snap([stored]), reg)
    assert [(m.provisional_id, m.into_person_id) for m in res.merges] == [("priya-sales", "priya-shah")]
    (ch,) = res.changes
    assert ch.kind == KIND_MERGE_PROVISIONAL
    assert (ch.new_status, ch.new_person_id, ch.new_provenance) == (RES, "priya-shah", Provenance.ALIAS)
    assert re_resolve(apply_to_snapshot(snap([stored]), res), res.registry).is_empty


@pytest.mark.ac("AC-05.8")
def test_attendance_rows_become_ambiguous_when_switch_off_and_back():
    reg = roster_registry()
    ctx_n = resolve_attendees(["Priya Nair", "Rahul Mehta", "Anita Desai"], reg)
    ctx_s = resolve_attendees(["Priya Shah", "Rahul Verma"], reg)
    meetings = [
        StoredMeeting("A", tuple(StoredAttendee(r.raw_name, r.person_id) for r in ctx_n.refs)),
        StoredMeeting("B", tuple(StoredAttendee(r.raw_name, r.person_id) for r in ctx_s.refs)),
    ]
    owners = [
        owner(1, "Priya", RES, "priya-nair", AIM, meeting="A"),
        owner(2, "Rahul", RES, "rahul-mehta", AIM, meeting="A"),
        owner(3, "Priya", RES, "priya-shah", AIM, meeting="B"),
        owner(4, "Anita", RES, "anita-desai", UFN, meeting="A"),
    ]
    s0 = snap(owners, meetings)
    assert re_resolve(s0, reg).is_empty  # config on: nothing changes

    off = re_resolve(s0, reg, cfg_off())
    attendance_rows = sum(1 for o in owners if o.provenance is AIM)
    assert len(off.changes) == attendance_rows == 3  # Anita (|C| = 1) is untouched
    assert all(c.new_status is AMB and c.warnings == (codes.W_OWNER_NOW_AMBIGUOUS,) for c in off.changes)
    assert {c.item_id for c in off.changes} == {1, 2, 3}

    s1 = apply_to_snapshot(s0, off)
    assert re_resolve(s1, reg, cfg_off()).is_empty  # idempotent
    on = re_resolve(s1, reg)  # switching back restores them
    assert {(c.item_id, c.new_person_id, c.new_provenance) for c in on.changes} == {
        (1, "priya-nair", AIM), (2, "rahul-mehta", AIM), (3, "priya-shah", AIM)}
    assert all(c.kind == KIND_RESOLVED and c.warnings == () for c in on.changes)


def test_stored_attendee_first_name_becomes_incomplete_when_second_namesake_joins():
    reg = custom_roster(("Priya", "Nair"))
    att = resolve_attendees(["Priya"], reg)  # only one Priya known: accepted
    assert att.refs[0].person_id == "priya-nair"
    meeting = StoredMeeting("M", (StoredAttendee("Priya", "priya-nair"),))
    stored = owner(1, "Priya", RES, "priya-nair", UFN, meeting="M")
    assert re_resolve(snap([stored], [meeting]), reg).is_empty

    reg.add_roster(__import__("actiontrack.config", fromlist=["parse_roster"]).parse_roster(
        [{"first_name": "Priya", "last_name": "Nair"}, {"first_name": "Priya", "last_name": "Shah"}]))
    res = re_resolve(snap([stored], [meeting]), reg)
    (ac,) = res.attendee_changes
    assert (ac.meeting_id, ac.raw_name, ac.old_person_id, ac.new_person_id) == ("M", "Priya", "priya-nair", None)
    assert ac.warnings == (codes.W_ATTENDEE_NAME_INCOMPLETE,)
    (ch,) = res.changes
    assert (ch.new_status, ch.new_candidates, ch.warnings) == (AMB, NAIR_SHAH, (codes.W_OWNER_NOW_AMBIGUOUS,))


def test_attendee_single_token_person_is_merged_and_attendee_row_repointed():
    reg = PeopleRegistry()
    resolve_attendees(["Priya"], reg)  # nobody known -> attendee-origin 'priya'
    resolve_attendees(["Priya Nair"], reg)
    meeting = StoredMeeting("M", (StoredAttendee("Priya", "priya"),))
    res = re_resolve(snap([], [meeting]), reg)
    assert [(m.provisional_id, m.into_person_id) for m in res.merges] == [("priya", "priya-nair")]
    (ac,) = res.attendee_changes
    assert ac.new_person_id == "priya-nair"


def test_second_pass_yields_zero_changes_and_manual_done_owner_has_no_status_fields():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Shah"))
    stored = owner(9, "Priya", RES, "priya-nair", UFN, meeting=None)  # e.g. a manually done item
    s0 = snap([stored])
    r1 = re_resolve(s0, reg)
    assert len(r1.changes) == 1 and r1.changes[0].new_status is AMB
    assert re_resolve(apply_to_snapshot(s0, r1), r1.registry).is_empty


def test_deterministic_order_by_item_then_ordinal():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Shah"))
    owners = [
        owner(5, "Priya", RES, "priya-nair", UFN, ordinal=1),
        owner(2, "Priya", RES, "priya-nair", UFN, ordinal=0),
        owner(5, "Priya", RES, "priya-nair", UFN, ordinal=0),
    ]
    r = re_resolve(snap(owners), reg)
    assert [(c.item_id, c.ordinal) for c in r.changes] == [(2, 0), (5, 0), (5, 1)]
    r2 = re_resolve(snap(list(reversed(owners))), reg)
    assert r == r2 or [(c.item_id, c.ordinal) for c in r2.changes] == [(2, 0), (5, 0), (5, 1)]


def test_unassigned_and_blank_owners_are_left_alone():
    reg = roster_registry()
    owners = [
        owner(1, "Everyone", OwnerStatus.UNASSIGNED),
        owner(2, None, OwnerStatus.UNASSIGNED),
    ]
    assert re_resolve(snap(owners), reg).is_empty


def test_provisional_with_unmatched_name_stays_and_is_not_duplicated():
    reg = roster_registry()
    zed = resolve_name("Zed", NO_ATTENDEE_LIST, reg)
    stored = owner(1, "Zed", OwnerStatus.PROVISIONAL, zed.person_id, Provenance.PROVISIONAL)
    res = re_resolve(snap([stored]), reg)
    assert res.is_empty and not res.created_people


def test_performance_ten_thousand_owner_rows_under_one_second():
    reg = roster_registry()
    names = ["Priya", "Rahul", "Anita", "Sam", "Priya N.", "Rahul V.", "Lena Fischer", "Meera", "Karan"]
    meetings = [StoredMeeting(m, (StoredAttendee("Priya Nair"), StoredAttendee("Rahul Verma"))) for m in range(50)]
    owners = [
        owner(i, names[i % len(names)], RES, "anita-desai", UFN, meeting=i % 50) for i in range(10_000)
    ]
    t0 = time.perf_counter()
    res = re_resolve(snap(owners, meetings), reg)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, elapsed
    assert len(res.changes) > 0


def test_module_does_not_import_store():
    import actiontrack.resolution.owners as mod

    src = open(mod.__file__, encoding="utf-8").read()
    assert "actiontrack.store" not in src and "sqlite3" not in src
    assert isinstance(MeetingContext(None).attendee_ids, type(None))
