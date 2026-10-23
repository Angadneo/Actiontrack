"""resolve_attendees (T-M4-06; spec 10.2a incomplete names, 10.3 step 4, AC-05.13, SC-55)."""

from __future__ import annotations

import pytest

from actiontrack import codes
from actiontrack.domain.models import Attendee, PersonOrigin
from actiontrack.resolution.owners import MeetingContext, resolve_attendees
from actiontrack.resolution.people import PeopleRegistry
from tests.unit.test_people_registry import custom_roster, roster_registry

MIGRATION_SYNC_0917 = ["Karan Joshi", "Sam Okafor", "Rahul Verma", "Priya Shah", "Priya Nair"]


def ids(res):
    return sorted(res.person_ids)


def test_sample_0917_attendee_set():
    reg = roster_registry()
    res = resolve_attendees([Attendee(n) for n in MIGRATION_SYNC_0917], reg)
    assert ids(res) == ["karan-joshi", "priya-nair", "priya-shah", "rahul-verma", "sam-okafor"]
    assert [r.person_id for r in res.refs] == [
        "karan-joshi",
        "sam-okafor",
        "rahul-verma",
        "priya-shah",
        "priya-nair",
    ]
    assert all(not r.warnings for r in res.refs)
    assert len(reg) == 9  # no attendee-origin people for known roster names


@pytest.mark.ac("AC-05.13")
@pytest.mark.sc("SC-55")
def test_incomplete_first_names_with_namesakes_are_not_attributed():
    reg = roster_registry()
    res = resolve_attendees(["Priya", "Rahul"], reg)
    assert [r.person_id for r in res.refs] == [None, None]
    assert [r.warnings for r in res.refs] == [(codes.W_ATTENDEE_NAME_INCOMPLETE,)] * 2
    assert res.person_ids == frozenset()
    assert len(reg) == 9  # nothing created
    ctx = res.context()
    assert ctx == MeetingContext(frozenset())  # the list exists but A is empty


def test_unique_first_names_are_accepted():
    reg = roster_registry()
    res = resolve_attendees(["Anita", "Sam"], reg)
    assert ids(res) == ["anita-desai", "sam-okafor"]
    assert all(not r.warnings for r in res.refs)


def test_initial_forms_resolve_to_nair_and_verma():
    reg = roster_registry()
    res = resolve_attendees(["Priya N.", "Rahul V."], reg)
    assert ids(res) == ["priya-nair", "rahul-verma"]
    assert len(reg) == 9


def test_initial_form_matching_two_people_is_not_attributed():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Narang"))
    res = resolve_attendees(["Priya N."], reg)
    assert res.refs[0].person_id is None and res.refs[0].warnings == ()


def test_unknown_initial_form_creates_non_full_attendee_person():
    reg = roster_registry()
    res = resolve_attendees(["Zed Q."], reg)
    p = reg.get(res.refs[0].person_id)
    assert p.identity_key == "zed q" and p.last_name is None and p.origin is PersonOrigin.ATTENDEE


def test_diacritic_attendee_matches_roster_person_without_second_person():
    reg = custom_roster(("José", "Alvarez"))
    res = resolve_attendees(["Jose Alvarez"], reg)
    assert res.refs[0].person_id == "josé-alvarez"
    assert len(reg) == 1


def test_diacritic_first_name_only_attendee_with_unique_fold_match():
    reg = custom_roster(("José", "Alvarez"))
    assert resolve_attendees(["Jose"], reg).refs[0].person_id == "josé-alvarez"
    reg2 = custom_roster(("José", "Alvarez"), ("José", "Brown"))
    r = resolve_attendees(["Jose"], reg2).refs[0]
    assert r.person_id is None and r.warnings == (codes.W_ATTENDEE_NAME_INCOMPLETE,)


def test_email_hint_kept_on_ref_and_person_email_unchanged():
    reg = roster_registry()
    res = resolve_attendees([Attendee("Priya Nair", "angad.kulkarni@coditas.com"), Attendee("Anita Desai", "x@y.org")], reg)
    assert res.refs[0].email_hint == "angad.kulkarni@coditas.com"
    assert reg.get("priya-nair").email == "angad.kulkarni@coditas.com"
    assert reg.get("anita-desai").email is None  # hint never copied (BR-13)


def test_new_attendee_person_is_unverified_attendee_origin():
    reg = PeopleRegistry()
    res = resolve_attendees([Attendee("Priya Nair", "p@x.org")], reg)
    p = reg.get(res.refs[0].person_id)
    assert p.origin is PersonOrigin.ATTENDEE and p.verified is False and p.email is None


def test_same_identity_in_any_spelling_is_one_person_without_roster():
    reg = PeopleRegistry()
    res = resolve_attendees(["Priya Nair", "Priya R. Nair", "Priya N.", "priya nair"], reg)
    assert len(reg) == 1 and ids(res) == ["priya-nair"]
    assert reg.display_name("priya-nair") == "Priya R. Nair"  # most complete spelling


def test_order_within_list_does_not_matter_for_first_names():
    a, b = PeopleRegistry(), PeopleRegistry()
    ra = resolve_attendees(["Priya", "Priya Nair"], a)
    rb = resolve_attendees(["Priya Nair", "Priya"], b)
    assert ra.person_ids == rb.person_ids == frozenset({"priya-nair"})
    assert len(a) == len(b) == 1


def test_first_name_nobody_known_creates_single_token_person_once():
    reg = roster_registry()
    r1 = resolve_attendees(["Zed"], reg).refs[0]
    r2 = resolve_attendees(["zed"], reg).refs[0]
    assert r1.person_id == r2.person_id == "zed"
    assert reg.get("zed").last_name is None
    assert len(reg) == 10


def test_no_attendees_gives_no_list_context():
    res = resolve_attendees([], roster_registry())
    assert res.context() == MeetingContext(None)


def test_invalid_names_are_skipped_not_created():
    reg = roster_registry()
    res = resolve_attendees(["Anita's", "a b c d e f"], reg)
    assert [r.person_id for r in res.refs] == [None, None]
    assert len(reg) == 9
