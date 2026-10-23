"""Ambiguity model and surfacing hooks (T-M4-12).

Spec 10.2a rule 1, 10.4, BR-06, BR-14, BR-23, FR-02.6, FR-02.9, AC-05.11, 05.14, 05.15, 05.16,
AC-02.11, SC-53.
"""

from __future__ import annotations

import dataclasses

import pytest

from actiontrack.config import parse_roster
from actiontrack.domain.models import OwnerRole, OwnerStatus, Provenance, ResolvedOwner
from actiontrack.resolution.owners import (
    ambiguous_report_records,
    candidate_key,
    candidate_lines,
    group_ambiguous,
    owner_display,
    owner_matches_person,
)
from actiontrack.resolution.people import PeopleRegistry
from tests.unit.test_people_registry import roster_registry

NAIR_SHAH = ("priya-nair", "priya-shah")
MEHTA_VERMA = ("rahul-mehta", "rahul-verma")


@dataclasses.dataclass
class Item:
    item_id: int
    status: str
    owners: tuple
    location: str | None = None


def amb(raw, cands, ordinal=0):
    return ResolvedOwner(ordinal, raw, None, OwnerStatus.AMBIGUOUS, tuple(cands))


def res(raw, pid, prov=Provenance.ROSTER, ordinal=0):
    return ResolvedOwner(ordinal, raw, pid, OwnerStatus.RESOLVED, (), prov)


def sample_items():
    """The 7 ambiguous sample items (ids hand-assigned) plus resolved ones."""
    return [
        Item(1, "open", (amb("Priya", NAIR_SHAH),), "2026-09-17-billing-migration-sync.md:9"),
        Item(2, "open", (amb("Rahul", MEHTA_VERMA),), "2026-09-23-standup.md:8"),
        Item(3, "open", (amb("Priya", NAIR_SHAH),), "2026-09-30-q4-offsite.md:16"),
        Item(4, "open", (amb("Rahul", MEHTA_VERMA),), "2026-09-30-q4-offsite.md:17"),
        Item(5, "open", (amb("Rahul", MEHTA_VERMA),), "2026-10-07-standup.md:9"),
        Item(6, "open", (amb("Priya", NAIR_SHAH),), "2026-10-08-billing-go-no-go.md:9"),
        Item(7, "open", (amb("Priya", NAIR_SHAH),), "untitled.md:2"),
        Item(8, "open", (res("Priya Nair", "priya-nair"),), "x.md:1"),
        Item(9, "open", (res("Priya Shah", "priya-shah"),), "x.md:2"),
        Item(10, "done", (amb("Rahul", MEHTA_VERMA),), "2026-09-01-x.md:3"),  # done ambiguous fixture
    ]


@pytest.mark.ac("AC-05.11")
def test_display_names_full_name_for_namesakes_and_question_form_for_ambiguous():
    reg = roster_registry()
    d = owner_display(res("Priya", "priya-nair"), reg)
    assert (d.text, d.flags) == ("Priya Nair", ())
    assert owner_display(res("Priya N.", "priya-shah"), reg).text == "Priya Shah"
    d = owner_display(amb("Priya", NAIR_SHAH), reg)
    assert (d.text, d.flags) == ("?Priya (Nair | Shah)", ("OA",))
    assert owner_display(amb("Rahul", MEHTA_VERMA), reg).text == "?Rahul (Mehta | Verma)"
    assert owner_display(amb("Rahul", reversed(MEHTA_VERMA)), reg).text == "?Rahul (Mehta | Verma)"
    # the bare first name never stands alone for a resolved namesake
    assert owner_display(res("Rahul", "rahul-verma"), reg).text == "Rahul Verma"


def test_non_namesake_shows_display_name():
    reg = roster_registry()
    assert owner_display(res("Sam", "sam-okafor", Provenance.UNIQUE_FIRST_NAME), reg).text == "Sam Okafor"


def test_ambiguous_candidates_with_different_first_names_shown_as_full_names():
    reg = roster_registry()
    d = owner_display(amb("Priya (Sales)", ("anita-desai", "priya-nair", "priya-shah")), reg)
    assert d.text == "?Priya (Anita Desai | Nair | Shah)" and d.flags == ("OA",)
    d = owner_display(amb("PM", ("anita-desai", "sam-okafor")), reg)
    assert d.text == "?PM (Anita Desai | Sam Okafor)"


def test_qualified_candidates_show_qualifier():
    reg = PeopleRegistry()
    reg.add_roster(
        parse_roster(
            [
                {"first_name": "Priya", "last_name": "Nair", "qualifier": "Sales"},
                {"first_name": "Priya", "last_name": "Nair", "qualifier": "Eng"},
            ]
        )
    )
    d = owner_display(amb("Priya Nair", ("priya-nair-eng", "priya-nair-sales")), reg)
    assert d.text == "?Priya (Nair (Eng) | Nair (Sales))"
    assert owner_display(res("Priya Nair", "priya-nair-sales"), reg).text == "Priya Nair (Sales)"


def test_provisional_and_unassigned_show_raw_text_with_ou_flag():
    reg = roster_registry()
    zed = reg.add_provisional("Zed")
    p = ResolvedOwner(0, "zed", zed.id, OwnerStatus.PROVISIONAL, (), Provenance.PROVISIONAL)
    assert owner_display(p, reg) == dataclasses.replace(owner_display(p, reg), text="zed", flags=("OU",))
    u = ResolvedOwner(0, "Everyone", None, OwnerStatus.UNASSIGNED)
    d = owner_display(u, reg)
    assert (d.text, d.flags) == ("Everyone", ("OU",))
    assert owner_display(ResolvedOwner(0, None, None, OwnerStatus.UNASSIGNED), reg).text == ""


def test_candidate_key_sorted_unique():
    assert candidate_key(["b", "a", "b"]) == ("a", "b")
    assert candidate_key(()) == ()


@pytest.mark.ac("AC-05.14", "AC-02.11")
@pytest.mark.sc("SC-53")
def test_group_ambiguous_all_and_open_default():
    reg = roster_registry()
    groups = group_ambiguous(sample_items(), reg, "all")
    assert [g.names for g in groups] == [
        ("Priya Nair", "Priya Shah"),
        ("Rahul Mehta", "Rahul Verma"),
    ]
    assert [g.candidates for g in groups] == [NAIR_SHAH, MEHTA_VERMA]
    assert [len(g.items) for g in groups] == [4, 4]  # 4 Priya, 3 Rahul + the done fixture
    assert [i.item_id for i in groups[0].items] == [1, 3, 6, 7]  # by id

    default = group_ambiguous(sample_items(), reg)  # open
    assert [len(g.items) for g in default] == [4, 3]
    assert 10 not in {i.item_id for g in default for i in g.items}
    assert [i.item_id for i in group_ambiguous(sample_items(), reg, "done")[0].items] == [10]
    with pytest.raises(ValueError):
        group_ambiguous(sample_items(), reg, "closed")


def test_default_count_equals_report_hook_count():
    reg = roster_registry()
    items = sample_items()
    default_count = sum(len(g.items) for g in group_ambiguous(items, reg))
    records = ambiguous_report_records(items, reg)
    assert default_count == len(records) == 7
    assert [r.item_id for r in records] == [1, 2, 3, 4, 5, 6, 7]
    assert records[0].location == "2026-09-17-billing-migration-sync.md:9"
    assert records[0].raw == "Priya"
    assert records[0].candidate_names == ("Priya Nair", "Priya Shah")


def test_group_sorting_by_candidate_full_names():
    reg = roster_registry()
    items = [
        Item(1, "open", (amb("Rahul", MEHTA_VERMA),)),
        Item(2, "open", (amb("Anita", ("anita-desai", "sam-okafor")),)),
        Item(3, "open", (amb("Priya", NAIR_SHAH),)),
    ]
    assert [g.names[0] for g in group_ambiguous(items, reg)] == ["Anita Desai", "Priya Nair", "Rahul Mehta"]


def test_item_with_two_ambiguous_owners_appears_in_each_group():
    reg = roster_registry()
    items = [Item(1, "open", (amb("Priya", NAIR_SHAH), amb("Rahul", MEHTA_VERMA, 1)))]
    assert [len(g.items) for g in group_ambiguous(items, reg)] == [1, 1]
    assert len(ambiguous_report_records(items, reg)) == 2


@pytest.mark.ac("AC-05.15")
def test_owner_matches_person_with_and_without_flag():
    rows = {
        "nair_resolved": (res("Priya Nair", "priya-nair"),),
        "shah_resolved": (res("Priya Shah", "priya-shah"),),
        "amb_ns": (amb("Priya", NAIR_SHAH),),
        "amb_rahul": (amb("Rahul", MEHTA_VERMA),),
        "co_owner": (res("Anita", "anita-desai"), res("Priya Nair", "priya-nair", ordinal=1)),
    }
    pid = "priya-nair"
    with_flag = sorted(k for k, v in rows.items() if owner_matches_person(v, pid, True))
    without = sorted(k for k, v in rows.items() if owner_matches_person(v, pid, False))
    assert with_flag == ["amb_ns", "co_owner", "nair_resolved"]
    assert without == ["co_owner", "nair_resolved"]  # BR-14: ambiguous never match by default
    assert not owner_matches_person(rows["amb_rahul"], pid, True)
    assert not owner_matches_person(rows["shah_resolved"], pid, True)


@pytest.mark.ac("AC-05.16")
def test_identical_text_items_keep_distinct_person_ids():
    a = res("Priya Nair", "priya-nair")
    b = res("Priya Shah", "priya-shah")
    assert a.person_id != b.person_id
    assert owner_matches_person((a,), "priya-shah", True) is False
    assert owner_matches_person((b,), "priya-shah", False) is True


def test_candidate_lines_full_name_and_email_sorted():
    reg = roster_registry()
    assert candidate_lines(["priya-shah", "priya-nair"], reg) == (
        "Priya Nair <angad.kulkarni@coditas.com>",
        "Priya Shah <pranay.pathak@coditas.com>",
    )
    assert candidate_lines(MEHTA_VERMA, reg) == ("Rahul Mehta (no email)", "Rahul Verma (no email)")
    assert candidate_lines((), reg) == ()


def test_display_works_for_role_with_owner():
    reg = roster_registry()
    w = ResolvedOwner(1, "Priya", "priya-shah", OwnerStatus.RESOLVED, (), Provenance.ALIAS, OwnerRole.WITH)
    assert owner_display(w, reg).text == "Priya Shah"
