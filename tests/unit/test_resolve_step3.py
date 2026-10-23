"""Resolution step 3: first name only, attendance in namesake groups (T-M4-09).

Spec 10.3 steps 3, 3a, 3b, 10.4, BR-06, AC-05.1, 05.2, 05.8, 05.18, SC-44..46, 48, 50, 55.
Attendee lists are copied by hand from the sample meetings (tests/fixtures/notes).
"""

from __future__ import annotations

import dataclasses

import pytest

from actiontrack.config import Owners
from actiontrack.domain.models import OwnerStatus, Provenance
from actiontrack.resolution.owners import (
    NO_ATTENDEE_LIST,
    MeetingContext,
    resolve_attendees,
    resolve_name,
)
from actiontrack.resolution.people import PeopleRegistry
from tests.unit.test_people_registry import custom_roster, roster_registry

ATT = {
    "0901": ["Priya Nair", "Rahul Mehta", "Anita Desai", "Sam Okafor"],
    "0903": ["Karan Joshi", "Sam Okafor", "Rahul Verma", "Priya Shah"],
    "0917": ["Karan Joshi", "Sam Okafor", "Rahul Verma", "Priya Shah", "Priya Nair"],
    "0918": ["Anita Desai", "Rahul Mehta", "Priya Nair", "Lena Fischer"],
    "0930": [
        "Priya Nair", "Rahul Mehta", "Anita Desai", "Sam Okafor", "Meera Iyer",
        "Karan Joshi", "Lena Fischer", "Priya Shah", "Rahul Verma",
    ],
    "1007": ["Rahul Mehta", "Rahul Verma", "Anita Desai"],
    "1008": ["Karan Joshi", "Sam Okafor", "Rahul Verma", "Priya Shah", "Priya Nair"],
}
NOTHING = None  # notes without attendee list: 2026-09-23-standup.md, untitled.md

NAIR_SHAH = ("priya-nair", "priya-shah")
MEHTA_VERMA = ("rahul-mehta", "rahul-verma")
AIM = Provenance.ATTENDEE_IN_MEETING
UFN = Provenance.UNIQUE_FIRST_NAME


def ctx(reg, key):
    if key is None:
        return NO_ATTENDEE_LIST
    return resolve_attendees(ATT[key], reg).context()


def resolved(r, pid, prov):
    assert (r.status, r.person_id, r.provenance, r.candidates) == (OwnerStatus.RESOLVED, pid, prov, ())


def ambiguous(r, cands):
    assert (r.status, r.person_id, r.provenance, r.candidates) == (
        OwnerStatus.AMBIGUOUS, None, Provenance.NONE, cands)


AMBIGUOUS_ROWS = [
    # (file:line, raw, attendee key, candidates)
    ("2026-09-17-billing-migration-sync.md:9", "Priya", "0917", NAIR_SHAH),
    ("2026-10-08-billing-go-no-go.md:9", "Priya", "1008", NAIR_SHAH),
    ("2026-09-30-q4-offsite.md:16", "Priya", "0930", NAIR_SHAH),
    ("2026-09-30-q4-offsite.md:17", "Rahul", "0930", MEHTA_VERMA),
    ("2026-10-07-standup.md:9", "Rahul", "1007", MEHTA_VERMA),
    ("2026-09-23-standup.md:8", "Rahul", NOTHING, MEHTA_VERMA),
    ("untitled.md:2", "Priya", NOTHING, NAIR_SHAH),
]


@pytest.mark.ac("AC-05.1", "AC-05.2")
@pytest.mark.sc("SC-44", "SC-46", "SC-48")
@pytest.mark.parametrize(("where", "raw", "att", "cands"), AMBIGUOUS_ROWS, ids=[r[0] for r in AMBIGUOUS_ROWS])
def test_sample_ambiguous_rows(where, raw, att, cands):
    reg = roster_registry()
    ambiguous(resolve_name(raw, ctx(reg, att), reg), cands)


ATTENDANCE_ROWS = [
    ("2026-09-01-quote-portal-kickoff.md:9", "Priya", "0901", "priya-nair"),
    ("2026-09-03-billing-migration-sync.md:11", "Priya", "0903", "priya-shah"),
    ("2026-09-17-billing-migration-sync.md:12", "Rahul", "0917", "rahul-verma"),
    ("2026-09-18-design-review.md:12 (Anita & Rahul)", "Rahul", "0918", "rahul-mehta"),
    ("2026-09-18-design-review.md:13 (Lena with Priya)", "Priya", "0918", "priya-nair"),
]


@pytest.mark.ac("AC-05.2", "AC-05.9", "AC-05.18")
@pytest.mark.sc("SC-45")
@pytest.mark.parametrize(("where", "raw", "att", "pid"), ATTENDANCE_ROWS, ids=[r[0] for r in ATTENDANCE_ROWS])
def test_sample_resolved_by_attendance(where, raw, att, pid):
    reg = roster_registry()
    resolved(resolve_name(raw, ctx(reg, att), reg), pid, AIM)


@pytest.mark.ac("AC-05.18")
@pytest.mark.parametrize("first", ["Sam", "Anita", "Meera", "Karan", "Lena"])
def test_non_namesake_first_name_in_noted_meeting_is_unique_first_name(first):
    reg = roster_registry()
    r = resolve_name(first, ctx(reg, "0930"), reg)
    assert r.provenance is UFN and r.status is OwnerStatus.RESOLVED


@pytest.mark.sc("SC-50")
def test_unique_first_name_without_attendee_list():
    reg = roster_registry()
    resolved(resolve_name("Sam", NO_ATTENDEE_LIST, reg), "sam-okafor", UFN)  # untitled.md:1, 09-23:9


def test_namesake_present_but_nobody_of_that_name_attended():
    reg = roster_registry()
    ctx_ = resolve_attendees(["Anita Desai", "Sam Okafor"], reg).context()
    ambiguous(resolve_name("Priya", ctx_, reg), NAIR_SHAH)  # |A| = 0 -> 3b -> |C| = 2


@pytest.mark.ac("AC-05.8")
def test_config_off_makes_attendance_case_ambiguous():
    reg = roster_registry()
    c = ctx(reg, "0901")
    resolved(resolve_name("Priya", c, reg), "priya-nair", AIM)
    for cfg in (Owners(attendee_disambiguation=False),):
        ambiguous(resolve_name("Priya", c, reg, cfg), NAIR_SHAH)
    from actiontrack.config import default_config

    cfg = default_config()
    assert cfg.owners.attendee_disambiguation is True
    cfg_off = dataclasses.replace(cfg, owners=Owners(attendee_disambiguation=False))
    ambiguous(resolve_name("Priya", c, reg, cfg_off), NAIR_SHAH)
    # |C| = 1 never needed attendance, the switch does not touch it
    resolved(resolve_name("Sam", c, reg, cfg_off), "sam-okafor", UFN)


@pytest.mark.ac("AC-05.13")
@pytest.mark.sc("SC-55")
def test_attendees_all_incomplete_leave_owner_ambiguous():
    reg = roster_registry()
    c = resolve_attendees(["Priya", "Rahul"], reg).context()
    assert c.attendee_ids == frozenset()
    ambiguous(resolve_name("Priya", c, reg), NAIR_SHAH)
    ambiguous(resolve_name("Rahul", c, reg), MEHTA_VERMA)


def test_three_way_namesake_attendance_partial():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Shah"), ("Priya", "Rao"))
    c = MeetingContext(frozenset({"priya-shah", "priya-rao"}))
    ambiguous(resolve_name("Priya", c, reg), ("priya-rao", "priya-shah"))  # candidates = A
    c1 = MeetingContext(frozenset({"priya-rao", "anita"}))
    resolved(resolve_name("Priya", c1, reg), "priya-rao", AIM)


def test_pure_function_independent_of_call_order():
    reg = roster_registry()
    c1, c2 = ctx(reg, "0901"), ctx(reg, "0903")
    first = [resolve_name("Priya", c, reg) for c in (c1, c2, c1)]
    second = [resolve_name("Priya", c, reg) for c in (c1, c1, c2)]
    assert first[0] == second[0] == second[1] and first[1] == second[2]


def test_provenance_counts_over_namesake_attendance_rows_only():
    """Attendance provenance appears only inside namesake groups (G-26)."""
    reg = roster_registry()
    c = ctx(reg, "0930")
    rows = [resolve_name(n, c, reg) for n in ["Sam", "Anita", "Meera", "Karan", "Lena", "Priya", "Rahul"]]
    assert [r.provenance for r in rows].count(AIM) == 0  # all four namesakes attend: ambiguous
    assert [r.status for r in rows].count(OwnerStatus.AMBIGUOUS) == 2


# ---- no-roster mode (attendees only, chronological) ------------------------------------------


def test_no_roster_mode_final_state_matches_roster_mode():
    reg = PeopleRegistry()
    for key in ("0901", "0903", "0917", "0918", "0930", "1007", "1008"):
        resolve_attendees(ATT[key], reg)
    assert reg.namesake_groups() == {
        "priya": ("priya-nair", "priya-shah"),
        "rahul": ("rahul-mehta", "rahul-verma"),
    }
    for _w, raw, att, cands in AMBIGUOUS_ROWS:
        c = resolve_attendees(ATT[att], reg).context() if att else NO_ATTENDEE_LIST
        ambiguous(resolve_name(raw, c, reg), cands)
    for _w, raw, att, pid in ATTENDANCE_ROWS:
        resolved(resolve_name(raw, resolve_attendees(ATT[att], reg).context(), reg), pid, AIM)
    assert len(reg) == 9  # nobody provisional


def test_no_roster_first_ingest_sees_only_one_priya_then_attendance_rules_later():
    reg = PeopleRegistry()
    c = resolve_attendees(ATT["0901"], reg).context()
    resolved(resolve_name("Priya", c, reg), "priya-nair", UFN)  # only one Priya known so far
    resolve_attendees(ATT["0903"], reg)
    resolved(resolve_name("Priya", c, reg), "priya-nair", AIM)  # re-evaluated: now a namesake group


def test_first_name_nobody_known_falls_through_to_provisional():
    reg = roster_registry()
    r = resolve_name("Zed", NO_ATTENDEE_LIST, reg)
    assert r.status is OwnerStatus.PROVISIONAL and r.provenance is Provenance.PROVISIONAL
