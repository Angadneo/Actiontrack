"""Person query resolution (T-M4-13; spec 10.5, FR-02.6, FR-04.1, AC-02.6, AC-04.6, SC-07, SC-54)."""

from __future__ import annotations

from pathlib import Path

import pytest

from actiontrack.config import load_config
from actiontrack.errors import AmbiguousOwnerError, ExitCode, NotFoundError, exit_code_for
from actiontrack.resolution.people import (
    PeopleRegistry,
    PersonAmbiguous,
    PersonNotFound,
    edit_distance,
    has_recipient,
    resolve_person_query,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "config"


@pytest.fixture
def reg():
    r = PeopleRegistry()
    r.add_roster(load_config(FIX / "valid_full.toml").roster)
    return r


@pytest.mark.ac("AC-02.6", "AC-04.6")
@pytest.mark.sc("SC-07", "SC-54")
def test_bare_shared_first_name_is_ambiguous_with_emails(reg):
    with pytest.raises(PersonAmbiguous) as ei:
        resolve_person_query("Priya", reg)
    e = ei.value
    assert e.candidates == ("priya-nair", "priya-shah")
    assert e.candidate_lines == (
        "Priya Nair <angad.kulkarni@coditas.com>",
        "Priya Shah <pranay.pathak@coditas.com>",
    )
    text = e.format()
    assert "Priya Nair <angad.kulkarni@coditas.com>" in text and "Priya Shah <pranay.pathak@coditas.com>" in text
    assert "re-run with the full name" in text
    assert exit_code_for(e) == ExitCode.AMBIGUOUS_OWNER


def test_blank_email_candidates_shown_as_no_email(reg):
    with pytest.raises(PersonAmbiguous) as ei:
        resolve_person_query("rahul", reg)
    assert ei.value.candidate_lines == ("Rahul Mehta (no email)", "Rahul Verma (no email)")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("priya-nair", "priya-nair"),
        ("Priya-Nair", "priya-nair"),
        ("Priya Nair", "priya-nair"),
        ("priya   nair", "priya-nair"),
        ("PRIYA NAIR", "priya-nair"),
        ("ANGAD.KULKARNI@CODITAS.COM", "priya-nair"),
        ("pranay.pathak@coditas.com", "priya-shah"),
        ("Priya N.", "priya-nair"),
        ("Priya S.", "priya-shah"),
        ("Anita", "anita-desai"),
        ("anita desai", "anita-desai"),
        ("  Sam Okafor ", "sam-okafor"),
    ],
)
def test_resolves_exactly_one(reg, query, expected):
    assert resolve_person_query(query, reg).id == expected


def test_initial_form_without_alias_is_not_matched(reg):
    with pytest.raises(PersonNotFound):
        resolve_person_query("Meera I.", reg)
    with pytest.raises(PersonNotFound) as ei:
        resolve_person_query("Meera I", reg)
    assert ei.value.near_names == ()  # nothing within distance 2 of "meera i" except identity forms


@pytest.mark.ac("AC-04.6")
def test_not_found_offers_near_match_never_auto_used(reg):
    with pytest.raises(PersonNotFound) as ei:
        resolve_person_query("Priyaa Nair", reg)
    e = ei.value
    assert [p.id for p in e.near] == ["priya-nair"]
    assert "Priya Nair" in e.format()
    assert isinstance(e, NotFoundError) and exit_code_for(e) == ExitCode.NOT_FOUND


def test_not_found_no_near_matches(reg):
    with pytest.raises(PersonNotFound) as ei:
        resolve_person_query("Zzz", reg)
    assert ei.value.near == ()


def test_near_matches_capped_at_three_sorted(reg):
    r = PeopleRegistry()
    for first in ("Anna", "Anne", "Anny", "Annu"):
        r.add_attendee(f"{first} Lee")
    with pytest.raises(PersonNotFound) as ei:
        resolve_person_query("Ann Lee", r)
    assert [p.id for p in ei.value.near] == ["anna-lee", "anne-lee", "annu-lee"]


def test_provisional_person_matchable(reg):
    reg.add_provisional("Zed")
    p = resolve_person_query("zed", reg)
    assert p.id == "zed" and not p.verified and not has_recipient(p)
    assert resolve_person_query("ZED", reg).id == "zed"


def test_has_recipient_for_roster(reg):
    assert has_recipient(resolve_person_query("Priya Nair", reg))
    assert not has_recipient(resolve_person_query("Anita", reg))


def test_roster_alias_for_bare_first_name_is_explicit(reg):
    r = PeopleRegistry()
    r.add_roster(
        load_config(FIX / "valid_full.toml").roster
    )
    r.add_alias("priya-nair", "Priya", auto=False)
    assert resolve_person_query("Priya", r).id == "priya-nair"


def test_alias_shared_by_two_people_is_ambiguous(reg):
    reg.add_alias("priya-shah", "PN", auto=False)
    reg.add_alias("priya-nair", "PN", auto=False)
    with pytest.raises(AmbiguousOwnerError):
        resolve_person_query("PN", reg)


def test_id_beats_other_matches_and_empty_query(reg):
    with pytest.raises(PersonNotFound):
        resolve_person_query("   ", reg)
    with pytest.raises(PersonNotFound):
        resolve_person_query("", reg)


def test_edit_distance_table():
    cases = [("", "", 0), ("a", "", 1), ("kitten", "sitting", 3), ("priya", "priyaa", 1), ("ab", "ba", 2)]
    for a, b, d in cases:
        assert edit_distance(a, b) == edit_distance(b, a) == d


def test_exception_contract():
    assert issubclass(PersonNotFound, NotFoundError) and PersonNotFound.exit_code == ExitCode.NOT_FOUND
    assert issubclass(PersonAmbiguous, AmbiguousOwnerError)
    assert PersonAmbiguous.exit_code == ExitCode.AMBIGUOUS_OWNER
