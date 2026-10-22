"""T-M4-01 / T-M4-02: name keys, identity, slugs, classifier, models, rule tables."""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from pathlib import Path

import pytest

from actiontrack.domain import models, names, rules
from actiontrack.domain.models import (
    NameFormKind,
    NameMention,
    OwnerPhrase,
    OwnerRole,
    OwnerStatus,
    Person,
    PersonOrigin,
    Provenance,
    ResolvedOwner,
    TextSpan,
)
from actiontrack.domain.names import (
    classify_key_kind,
    classify_name,
    fold_key,
    identity_key,
    initial_matches,
    name_key,
    person_slug,
    provisional_key,
)


@pytest.mark.ac("AC-05.10")
@pytest.mark.parametrize(
    "raw",
    [
        "Priya Nair",
        "priya nair",
        "  PRIYA   Nair ",
        "Dr. Priya Nair",
        "Dr Priya Nair",
        "Priya R. Nair",
        "Mrs. Priya Nair",
        "PRIYA\tNAIR",
        "Ms Priya Q. Nair",
    ],
)
def test_identity_key_variants_equal(raw):
    assert identity_key(raw) == "priya nair"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Priya Nair-Rao", "priya nair-rao"),
        ("O’Brien", "o'brien"),
        ("O'Brien", "o'brien"),
        ("Anne O’Brien", "anne o'brien"),
        ("李雷", "李雷"),
        ("Zoë Åström", "zoë åström"),
        ("José Alvarez", "josé alvarez"),
        ("Jean-Luc Picard", "jean-luc picard"),
    ],
)
def test_name_key_table(raw, expected):
    assert name_key(raw) == expected


@pytest.mark.ac("AC-05.6")
def test_fold_key_vs_name_key():
    assert name_key("José") != name_key("Jose")
    assert fold_key("José") == fold_key("Jose") == "jose"
    assert fold_key("Zoë Åström") == "zoe astrom"
    assert name_key("Zoë Åström") == "zoë åström"
    assert fold_key("李雷") == "李雷"
    # non-decomposable letters are not folded in v1
    assert fold_key("Søren") == "søren"


@pytest.mark.ac("AC-05.6")
def test_fold_key_applied_identically_but_identity_differs():
    roster, attendee, owner = "José Alvarez", "Jose Alvarez", "Jose Alvarez"
    assert fold_key(roster) == fold_key(attendee) == fold_key(owner)
    assert identity_key(roster) != identity_key(attendee)
    assert fold_key(identity_key(roster)) == fold_key(identity_key(attendee))


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("Priya Nair", "full"),
        ("Priya R. Nair", "full"),
        ("Priya N.", "initial_form"),
        ("Priya N", "initial_form"),
        ("Zed", "single_token"),
        ("", "empty"),
        ("Dr.", "empty"),
        ("李雷 王", "full"),
    ],
)
def test_classify_key_kind(raw, kind):
    assert classify_key_kind(raw) == kind


def test_identity_and_provisional_corner_cases():
    assert identity_key("Priya N.") is None
    assert identity_key("Zed") == "zed" and classify_key_kind("Zed") == "single_token"
    assert provisional_key("Priya N.") == "priya n"
    assert provisional_key("Zed Q.") == "zed q"
    assert provisional_key("Zed") == "zed"
    assert provisional_key("Priya Nair") == "priya nair"
    assert provisional_key("  ") is None


def test_split_first_last_tokens():
    assert names.split_name("Priya R. Nair-Rao") == ["priya", "r", "nair-rao"]
    assert names.first_token("Priya R. Nair-Rao") == "priya"
    assert names.last_token("Priya R. Nair-Rao") == "nair-rao"
    assert names.first_token("") is None and names.last_token("") is None


def test_qualifier_is_part_of_identity():
    assert identity_key("Priya Nair", "Sales") == "priya nair|sales"
    assert identity_key("Priya Nair", "  ") == "priya nair"


@pytest.mark.ac("AC-09.6")
def test_slugs():
    assert person_slug("priya nair") == "priya-nair"
    assert person_slug("priya nair|sales") == "priya-nair-sales"
    assert person_slug("李雷 王") == "李雷-王"
    assert person_slug("zoë åström") == "zoë-åström"
    assert person_slug("priya nair-rao") == "priya-nair-rao"
    assert unicodedata.is_normalized("NFC", person_slug("zoë a"))


def test_slug_collisions_deterministic():
    first = person_slug("priya nair")
    second = person_slug("priya, nair", taken=[first])
    third = person_slug("priya.nair", taken=[first, second])
    assert (first, second, third) == ("priya-nair", "priya-nair-2", "priya-nair-3")


@pytest.mark.parametrize("raw", ["Dr. Priya  Nair", "Zoë Åström", "O’Brien", "!!!", "", "Mr. X Y"])
def test_name_key_idempotent_and_normalisation_independent(raw):
    k = name_key(raw)
    assert name_key(k) == k
    assert name_key(unicodedata.normalize("NFD", raw)) == name_key(unicodedata.normalize("NFC", raw))


def test_names_module_is_pure():
    src = Path(names.__file__).read_text(encoding="utf-8")
    for banned in ("import locale", "import sqlite3", "datetime", "open(", "import os"):
        assert banned not in src


# ---- T-M4-02 ---------------------------------------------------------------------------


@pytest.mark.ac("AC-05.3")
@pytest.mark.parametrize(
    ("raw", "kind", "first", "last", "initial"),
    [
        ("Priya N.", NameFormKind.FIRST_INITIAL, "priya", None, "n"),
        ("Priya S.", NameFormKind.FIRST_INITIAL, "priya", None, "s"),
        ("Priya N", NameFormKind.FIRST_INITIAL, "priya", None, "n"),
        ("Priya Nair", NameFormKind.FULL, "priya", "nair", "n"),
        ("Priya", NameFormKind.FIRST_ONLY, "priya", None, None),
        ("Priya R. Nair", NameFormKind.FULL, "priya", "nair", "n"),
        ("Zoë Åström", NameFormKind.FULL, "zoë", "åström", "å"),
        ("Dr. Priya Nair", NameFormKind.FULL, "priya", "nair", "n"),
        ("Priya Nair-Rao", NameFormKind.FULL, "priya", "nair-rao", "n"),
        ("Ab Bc Cd Dd", NameFormKind.FULL, "ab", "dd", "d"),
    ],
)
def test_classify_name_table(raw, kind, first, last, initial):
    form = classify_name(raw)
    assert (form.kind, form.first, form.last, form.initial) == (kind, first, last, initial)


@pytest.mark.parametrize("raw", ["a b c d e", "", "   ", "Anita's", "James’", "Priya Nair's"])
def test_classify_name_rejects(raw):
    assert classify_name(raw) is None


def test_classify_name_qualifier_and_consistency():
    assert classify_name("Priya Nair", "Sales").qualifier == "sales"
    expected = {
        "initial_form": NameFormKind.FIRST_INITIAL,
        "full": NameFormKind.FULL,
        "single_token": NameFormKind.FIRST_ONLY,
    }
    for raw in ["Priya N.", "Priya Nair", "Zed", "Priya R. Nair"]:
        assert expected[classify_key_kind(raw)] is classify_name(raw).kind


def _person(first, last):
    return Person("x", first, last, identity_key=f"{first} {last}".lower(), origin=PersonOrigin.ROSTER)


@pytest.mark.ac("AC-05.5")
def test_initial_matches_and_first_name_key():
    nair, shah = _person("Priya", "Nair"), _person("Priya", "Shah")
    form = classify_name("Priya N.")
    assert initial_matches(nair, form) and not initial_matches(shah, form)
    assert not initial_matches(nair, classify_name("Priya Nair"))
    assert not initial_matches(_person("Priya", None), form)
    assert initial_matches(_person("José", "Nair"), classify_name("José N."))
    assert not initial_matches(_person("José", "Nair"), classify_name("Jose N."))
    assert initial_matches(_person("José", "Nair"), classify_name("Jose N."), fold=True)
    assert names.first_name_key(nair) == names.first_name_key("Priya Q. Other") == "priya"


# ---- rule tables ------------------------------------------------------------------------


@pytest.mark.ac("AC-05.18")
@pytest.mark.parametrize(
    "word",
    ["everyone", "TBD", "Team", "All", "Someone", "EVERYBODY", "Anyone", "Nobody", "We", "I", "Me", "TBD:"],
)
def test_nonperson_words(word):
    assert rules.is_nonperson_word(word)


@pytest.mark.parametrize("word", ["Friday", "Oct", "By", "due", "She", "May", "Until", "sept"])
def test_not_a_name_words(word):
    assert rules.is_not_a_name_word(word)
    assert not rules.is_nonperson_word(word)


@pytest.mark.parametrize("word", ["Priya", "Anita", "Rahul", "", None])
def test_names_are_not_listed(word):
    assert not rules.is_nonperson_word(word) and not rules.is_not_a_name_word(word)


def test_rule_tables_contents():
    assert rules.MAX_NAME_TOKENS == 4
    assert rules.OWNER_LABEL_WORDS == {"owner", "assignee", "dri"}
    assert set(rules.OWNER_VERBS) >= {"to", "will", "should", "needs to", "is going to", "can", "must"}
    assert rules.HONORIFICS == {"dr", "mr", "ms", "mrs"}
    assert rules.is_honorific("Dr.") and not rules.is_honorific("Priya")
    for table in (rules.NONPERSON_WORDS, rules.NOT_A_NAME_WORDS, rules.HONORIFICS):
        assert all(w == w.casefold() for w in table)


def test_rule_tables_are_the_only_place_word_lists_live():
    src = Path(names.__file__).read_text(encoding="utf-8")
    assert "everyone" not in src.lower() and '"friday"' not in src.lower()
    assert names._HONORIFICS is rules.HONORIFICS


# ---- models -----------------------------------------------------------------------------

_SPEC_PROVENANCE = [
    "roster",
    "alias",
    "attendee",
    "attendee_in_meeting",
    "unique_first_name",
    "initial_form",
    "diacritic_fold",
    "provisional",
    "none",
]


def test_provenance_matches_spec_7_2_exactly():
    assert [p.value for p in Provenance] == _SPEC_PROVENANCE


def test_enums_match_schema_check_constraints():
    sql = (Path(models.__file__).parents[1] / "store" / "migrations" / "0001_initial.sql").read_text(
        encoding="utf-8"
    )
    assert {s.value for s in OwnerStatus} == {"resolved", "provisional", "ambiguous", "unassigned"}
    assert {o.value for o in PersonOrigin} == {"roster", "attendee", "note_owner"}
    assert {r.value for r in OwnerRole} == {"owner", "with"}
    for p in Provenance:
        assert f"'{p.value}'" in sql
    assert re.search(r"origin IN \('roster', 'attendee', 'note_owner'\)", sql)


def test_owner_models_frozen_and_validated():
    phrase = OwnerPhrase((NameMention("Anita", TextSpan(0, 5)),), 2, TextSpan(0, 12))
    assert phrase.mentions[0].role is OwnerRole.OWNER and phrase.nonperson_raw is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        phrase.rule_id = 3  # type: ignore[misc]
    ok = ResolvedOwner(0, "Priya", "priya-nair", OwnerStatus.RESOLVED, provenance=Provenance.ALIAS)
    assert ok.role is OwnerRole.OWNER and ok.candidates == ()
    amb = ResolvedOwner(
        0, "Priya", None, OwnerStatus.AMBIGUOUS, ("a", "b"), Provenance.NONE, warnings=("W-OWNER-NOW-AMBIGUOUS",)
    )
    assert amb.candidates == ("a", "b")
    with pytest.raises(ValueError):
        ResolvedOwner(0, "x", "id", OwnerStatus.AMBIGUOUS)
    with pytest.raises(ValueError):
        ResolvedOwner(0, "x", None, OwnerStatus.RESOLVED)
    with pytest.raises(ValueError):
        ResolvedOwner(0, "x", None, OwnerStatus.UNASSIGNED, warnings=("nope",))
    un = ResolvedOwner(0, "Everyone", None, OwnerStatus.UNASSIGNED)
    assert un.provenance is Provenance.NONE


def test_person_fields_mirror_schema():
    cols = {f.name for f in dataclasses.fields(Person)}
    assert cols == {
        "id",
        "first_name",
        "last_name",
        "qualifier",
        "display_name",
        "name_key",
        "identity_key",
        "email",
        "origin",
        "verified",
    }
