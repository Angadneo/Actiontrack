"""Resolution steps 4-5: diacritic variant, provisional people (T-M4-10).

Spec 10.3 steps 4-5, 10.2a(a)-(c), AC-05.6, AC-05.7, W-NAME-DIACRITIC, G-30.
"""

from __future__ import annotations

import pytest

from actiontrack import codes
from actiontrack.domain.models import OwnerStatus, PersonOrigin, Provenance
from actiontrack.resolution.owners import NO_ATTENDEE_LIST, MeetingContext, resolve_name
from actiontrack.resolution.people import PeopleRegistry
from tests.unit.test_people_registry import custom_roster, roster_registry

DIA = (codes.W_NAME_DIACRITIC,)


@pytest.mark.ac("AC-05.6")
def test_first_name_diacritic_variant_unique_resolves_with_warning():
    reg = custom_roster(("José", "Alvarez"))
    r = resolve_name("Jose", NO_ATTENDEE_LIST, reg)
    assert (r.status, r.person_id, r.provenance, r.warnings) == (
        OwnerStatus.RESOLVED, "josé-alvarez", Provenance.DIACRITIC_FOLD, DIA)
    assert len(reg) == 1  # no provisional person


@pytest.mark.ac("AC-05.6")
def test_two_folded_matches_are_ambiguous_no_resolution():
    reg = custom_roster(("José", "Alvarez"), ("Jose", "Alvarez", ["Joe"]))  # identities differ by diacritics
    r = resolve_name("Josè", NO_ATTENDEE_LIST, reg)
    assert r.status is OwnerStatus.AMBIGUOUS and r.warnings == ()
    assert len(r.candidates) == 2
    r2 = resolve_name("Jose", NO_ATTENDEE_LIST, reg)  # exact first-name key matches only one? both fold equal
    # exact name_key "jose" equals only the second person's first name -> |C| = 1 exact, no fold needed
    assert r2.status is OwnerStatus.RESOLVED and r2.provenance is Provenance.UNIQUE_FIRST_NAME


def test_full_name_diacritic_variant():
    reg = custom_roster(("José", "Alvarez"))
    r = resolve_name("Jose Alvarez", NO_ATTENDEE_LIST, reg)
    assert (r.person_id, r.provenance, r.warnings) == ("josé-alvarez", Provenance.DIACRITIC_FOLD, DIA)
    r = resolve_name("JOSÉ ALVAREZ", NO_ATTENDEE_LIST, reg)  # exact: no warning
    assert (r.provenance, r.warnings) == (Provenance.ROSTER, ())


def test_initial_form_diacritic_variant():
    reg = custom_roster(("José", "Álvarez"))
    r = resolve_name("Jose A.", NO_ATTENDEE_LIST, reg)
    assert (r.person_id, r.provenance, r.warnings) == ("josé-álvarez", Provenance.DIACRITIC_FOLD, DIA)


def test_folded_first_name_uses_attendance_like_3a():
    reg = custom_roster(("José", "Alvarez"), ("Josè", "Brown"))
    ctx = MeetingContext(frozenset({"josé-alvarez"}))
    r = resolve_name("Jose", ctx, reg)
    assert (r.person_id, r.provenance, r.warnings) == ("josé-alvarez", Provenance.DIACRITIC_FOLD, DIA)
    r = resolve_name("Jose", NO_ATTENDEE_LIST, reg)  # |C| = 2 without attendance: ambiguous
    assert r.status is OwnerStatus.AMBIGUOUS


def test_diacritic_variant_never_creates_provisional_when_unique_fold_match():
    reg = custom_roster(("José", "Alvarez"))
    for raw in ["Jose", "Jose Alvarez", "José"]:
        resolve_name(raw, NO_ATTENDEE_LIST, reg)
    assert len(reg) == 1 and not reg.non_full_people()


@pytest.mark.ac("AC-05.7")
def test_unknown_first_name_becomes_single_provisional_person():
    reg = roster_registry()
    r = resolve_name("Zed", NO_ATTENDEE_LIST, reg)
    assert (r.status, r.person_id, r.provenance, r.candidates) == (
        OwnerStatus.PROVISIONAL, "zed", Provenance.PROVISIONAL, ())
    p = reg.get("zed")
    assert p.origin is PersonOrigin.NOTE_OWNER and p.verified is False and p.last_name is None
    # second occurrence (another file) reuses the same person
    r2 = resolve_name("zed", MeetingContext(frozenset({"anita-desai"})), reg)
    assert r2.person_id == "zed" and len(reg) == 10


def test_initial_form_provisional_keyed_first_plus_initial():
    reg = roster_registry()
    r = resolve_name("Zed Q.", NO_ATTENDEE_LIST, reg)
    assert r.status is OwnerStatus.PROVISIONAL and r.person_id == "zed-q"
    assert reg.get("zed-q").identity_key == "zed q"
    assert resolve_name("zed q", NO_ATTENDEE_LIST, reg).person_id == "zed-q"


def test_full_unmatched_name_is_provisional_keyed_by_identity_and_shared():
    reg = roster_registry()
    r1 = resolve_name("Zed Zed", NO_ATTENDEE_LIST, reg)
    r2 = resolve_name("ZED  zed", NO_ATTENDEE_LIST, reg)
    assert r1.person_id == r2.person_id == "zed-zed"
    assert r1.status is r2.status is OwnerStatus.PROVISIONAL
    assert reg.get("zed-zed").last_name == "Zed"
    # status stays provisional on re-run (idempotent), person is known in K afterwards
    assert resolve_name("Zed Zed", NO_ATTENDEE_LIST, reg).provenance is Provenance.PROVISIONAL


def test_provisional_single_token_is_not_found_by_identity_of_full_name():
    reg = roster_registry()
    resolve_name("Zed", NO_ATTENDEE_LIST, reg)
    r = resolve_name("Zed Zed", NO_ATTENDEE_LIST, reg)
    assert r.person_id == "zed-zed" and r.person_id != "zed"


def test_sample_style_roster_names_never_create_provisional_people():
    reg = roster_registry()
    n = len(reg)
    for raw in ["Priya Nair", "Priya N.", "Priya S.", "Rahul V.", "Anita", "Sam", "Meera Iyer", "Karan", "Lena"]:
        resolve_name(raw, NO_ATTENDEE_LIST, reg)
    assert len(reg) == n


def test_provisional_people_are_in_known_set_for_later_files():
    reg = PeopleRegistry()
    resolve_name("Zed Zed", NO_ATTENDEE_LIST, reg)
    assert reg.lookup_first_name("zed") == ("zed-zed",)
    r = resolve_name("Zed", NO_ATTENDEE_LIST, reg)  # |C| = 1 -> the provisional full person
    assert r.person_id == "zed-zed" and r.status is OwnerStatus.PROVISIONAL
