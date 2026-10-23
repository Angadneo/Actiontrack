"""Resolution steps 1-2 (T-M4-08; spec 10.3 steps 1-2, 10.2a, AC-05.3, AC-05.10, AC-05.18, SC-08, SC-52)."""

from __future__ import annotations

import pytest

from actiontrack.domain.models import (
    NameMention,
    OwnerRole,
    OwnerStatus,
    Provenance,
    TextSpan,
)
from actiontrack.resolution.owners import MeetingContext, resolve_name, resolve_owner
from tests.unit.test_people_registry import custom_roster, roster_registry

BOTH_PRIYAS = MeetingContext(frozenset({"priya-nair", "priya-shah", "karan-joshi"}))
NO_LIST = MeetingContext(None)


def test_full_names_resolve_to_roster_without_provisional_rows():
    reg = roster_registry()
    n = len(reg)
    for raw in ["Priya Nair", "priya nair", "@priya nair", "Dr Priya R. Nair", "PRIYA  NAIR"]:
        r = resolve_name(raw.lstrip("@"), NO_LIST, reg)
        assert (r.status, r.person_id, r.provenance) == (OwnerStatus.RESOLVED, "priya-nair", Provenance.ROSTER), raw
        assert r.candidates == () and r.warnings == ()
    assert len(reg) == n  # AC-05.10: zero provisional rows


@pytest.mark.ac("AC-05.18", "AC-05.3")
@pytest.mark.sc("SC-08")
def test_initial_forms_resolve_with_provenance_initial_form():
    reg = roster_registry()
    # 2026-09-08-quote-portal-weekly.md:9, 2026-09-29-billing-cutover-planning.md:10 ("Priya N.")
    r = resolve_name("Priya N.", MeetingContext(frozenset({"priya-nair", "priya-shah"})), reg)
    assert (r.status, r.person_id, r.provenance) == (OwnerStatus.RESOLVED, "priya-nair", Provenance.INITIAL_FORM)
    # 2026-09-29-billing-cutover-planning.md:9 ("Priya S.")
    r = resolve_name("Priya S.", BOTH_PRIYAS, reg)
    assert (r.person_id, r.provenance) == ("priya-shah", Provenance.INITIAL_FORM)
    assert resolve_name("Rahul M.", NO_LIST, reg).person_id == "rahul-mehta"


@pytest.mark.ac("AC-05.18")
def test_initial_form_works_without_roster_aliases():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Shah"))
    r = resolve_name("Priya N.", NO_LIST, reg)
    assert (r.person_id, r.provenance) == ("priya-nair", Provenance.INITIAL_FORM)


def test_initial_form_two_matches_is_ambiguous_with_sorted_candidates():
    reg = custom_roster(("Priya", "Narang"), ("Priya", "Nair"), ("Anita", "Desai"))
    r = resolve_name("Priya N.", NO_LIST, reg)
    assert r.status is OwnerStatus.AMBIGUOUS and r.person_id is None
    assert r.candidates == ("priya-nair", "priya-narang") and r.provenance is Provenance.NONE


def test_initial_form_creates_no_identity_and_no_person_when_matched():
    reg = roster_registry()
    n = len(reg)
    resolve_name("Priya N.", NO_LIST, reg)
    assert len(reg) == n


def test_alias_to_one_person_resolves_with_provenance_alias():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Shah", ["Priya (Sales)"]))
    r = resolve_name("Priya (Sales)", NO_LIST, reg)
    assert (r.status, r.person_id, r.provenance) == (OwnerStatus.RESOLVED, "priya-shah", Provenance.ALIAS)
    assert resolve_name("priya (SALES)", NO_LIST, reg).person_id == "priya-shah"


def test_alias_mapped_to_two_people_is_ambiguous():
    reg = custom_roster(("Priya", "Nair", ["Priya (Sales)"]), ("Priya", "Shah", ["Priya (Sales)"]))
    r = resolve_name("Priya (Sales)", NO_LIST, reg)
    assert r.status is OwnerStatus.AMBIGUOUS and r.candidates == ("priya-nair", "priya-shah")


def test_roster_alias_of_bare_first_name_is_a_step1_hit():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Shah", ["Priya"]))
    r = resolve_name("Priya", NO_LIST, reg)
    assert (r.status, r.person_id, r.provenance) == (OwnerStatus.RESOLVED, "priya-shah", Provenance.ALIAS)
    # also inside a meeting where the other Priya attended: the alias is explicit user intent
    r = resolve_name("Priya", MeetingContext(frozenset({"priya-nair"})), reg)
    assert r.person_id == "priya-shah"


def test_initial_form_alias_pinning_another_person_wins():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Narang", ["Priya N."]))
    r = resolve_name("Priya N.", NO_LIST, reg)  # initial matches both; alias pins Narang
    assert (r.status, r.person_id, r.provenance) == (OwnerStatus.RESOLVED, "priya-narang", Provenance.ALIAS)


def test_single_token_never_matches_identity_of_known_full_person():
    reg = custom_roster(("Priya", "Nair"))
    # 'priya' is not the identity key of anyone: step 1 does not hit; step 3 handles it
    r = resolve_name("Priya", NO_LIST, reg)
    assert r.provenance is Provenance.UNIQUE_FIRST_NAME


def test_attendance_does_not_change_step12_outcomes():
    """``Priya N.`` in the 09-17 meeting (both Priyas attend) still resolves to Nair."""
    reg = roster_registry()
    ctx = MeetingContext(
        frozenset({"karan-joshi", "sam-okafor", "rahul-verma", "priya-shah", "priya-nair"})
    )
    assert resolve_name("Priya N.", ctx, reg).person_id == "priya-nair"
    assert resolve_name("Priya Shah", ctx, reg).person_id == "priya-shah"
    only_shah = MeetingContext(frozenset({"priya-shah"}))
    assert resolve_name("Priya Nair", only_shah, reg).person_id == "priya-nair"


def test_qualified_namesakes_same_base_identity_are_ambiguous():
    from actiontrack.config import parse_roster
    from actiontrack.resolution.people import PeopleRegistry

    reg = PeopleRegistry()
    reg.add_roster(
        parse_roster(
            [
                {"first_name": "Priya", "last_name": "Nair", "qualifier": "Sales"},
                {"first_name": "Priya", "last_name": "Nair", "qualifier": "Eng"},
            ]
        )
    )
    r = resolve_name("Priya Nair", NO_LIST, reg)
    assert r.status is OwnerStatus.AMBIGUOUS and r.candidates == ("priya-nair-eng", "priya-nair-sales")


def test_attendee_origin_person_provenance_attendee():
    from actiontrack.resolution.owners import resolve_attendees
    from actiontrack.resolution.people import PeopleRegistry

    reg = PeopleRegistry()
    resolve_attendees(["Priya Nair"], reg)
    r = resolve_name("Priya Nair", NO_LIST, reg)
    assert (r.status, r.person_id, r.provenance) == (OwnerStatus.RESOLVED, "priya-nair", Provenance.ATTENDEE)


def test_resolve_owner_wraps_mention_and_keeps_role_and_ordinal():
    reg = roster_registry()
    m = NameMention("Lena Fischer", TextSpan(0, 12), OwnerRole.WITH)
    r = resolve_owner(m, NO_LIST, reg, ordinal=2)
    assert (r.ordinal, r.role, r.person_id, r.owner_raw) == (2, OwnerRole.WITH, "lena-fischer", "Lena Fischer")


def test_unusable_text_is_unassigned():
    reg = roster_registry()
    for raw in ["", "   ", "Anita's", None]:
        r = resolve_name(raw, NO_LIST, reg)
        assert r.status is OwnerStatus.UNASSIGNED and r.person_id is None
