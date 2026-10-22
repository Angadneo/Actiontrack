"""Tests for roster validation in config.py (T-M1-05; AC-05.12, spec 10.2a)."""

from __future__ import annotations

from pathlib import Path

import pytest

from actiontrack.config import Roster, load_config, parse_roster
from actiontrack.errors import EnvError, ExitCode

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "config"


def person(first="Priya", last="Nair", **kw):
    return {"first_name": first, "last_name": last, **kw}


def err(entries) -> str:
    with pytest.raises(EnvError) as ei:
        parse_roster(entries)
    assert ei.value.exit_code == ExitCode.ENVIRONMENT
    return str(ei.value)


@pytest.mark.ac("AC-05.12")
def test_duplicate_roster_entries_exit4_names_both():
    msg = err([person(), person("Rahul", "Mehta"), person("Anita", "Desai"), person("priya", "NAIR")])
    assert 'person[0] "Priya Nair"' in msg and 'person[3] "priya NAIR"' in msg
    msg = str(_file_err(FIX / "bad_duplicate_person.toml"))
    assert "person[0]" in msg and "person[1]" in msg and "bad_duplicate_person.toml" in msg


def _file_err(path):
    with pytest.raises(EnvError) as ei:
        load_config(path)
    return ei.value


def test_middle_initial_duplicate_detected():
    msg = err([person(), person("Priya R.", "Nair")])
    assert "person[0]" in msg and "person[1]" in msg


@pytest.mark.ac("AC-05.12")
def test_distinct_qualifiers_accepted_as_two_people():
    r = parse_roster([person(qualifier="Sales"), person(qualifier="Ops")])
    assert len(r) == 2
    a, b = r.people
    assert a.person_id == "priya-nair-sales" and b.person_id == "priya-nair-ops"
    assert a.person_id != b.person_id
    assert a.identity_key == b.identity_key == "priya nair"
    assert a.qualified_key != b.qualified_key


def test_same_qualifier_still_duplicate():
    msg = err([person(qualifier="Sales"), person(qualifier="sales")])
    assert "person[0]" in msg and "person[1]" in msg


def test_one_qualifier_only_is_still_duplicate():
    err([person(qualifier="Sales"), person()])


def test_three_way_duplicate_names_pairs():
    msg = err([person(), person(), person()])
    assert msg.count("duplicate identity") == 3


@pytest.mark.parametrize(
    "entry",
    [
        {"last_name": "Nair"},
        {"first_name": "Priya"},
        {"first_name": "  ", "last_name": "Nair"},
        {"first_name": "Priya", "last_name": ""},
        {"first_name": 3, "last_name": "Nair"},
        {"first_name": "Priya", "last_name": "N."},
    ],
)
def test_first_or_last_name_missing_exit4(entry):
    msg = err([person("Anita", "Desai"), entry])
    assert "person[1]." in msg


def test_missing_key_names_entry_index_and_key():
    msg = err([person(), {"first_name": "Rahul"}])
    assert 'key "person[1].last_name"' in msg


@pytest.mark.parametrize(
    "email",
    ["no-at-sign", "a@b@c.com", "a b@c.com", "a@b.com, c@d.com", "<a@b.com>", "@b.com", "a@", "Name <a@b.com>"],
)
def test_invalid_email_exit4(email):
    assert "person[0].email" in err([person(email=email)])


def test_valid_and_blank_email():
    r = parse_roster([person(email="Priya.Nair@Example.com"), person("Rahul", "Mehta", email=""), person("Sam", "Okafor")])
    assert r.people[0].email == "Priya.Nair@Example.com"
    assert r.people[1].email is None and r.people[2].email is None
    assert r.find_by_email("priya.nair@example.COM") is r.people[0]
    assert r.find_by_email("x@y.com") is None


def test_duplicate_email_exit4():
    msg = err([person(email="a@b.com"), person("Rahul", "Mehta", email="A@B.com")])
    assert "person[0]" in msg and "person[1]" in msg and "duplicate email" in msg


def test_aliases_normalised_and_shared_alias_allowed():
    r = parse_roster(
        [
            person(aliases=["Priya N.", " P. Nair "]),
            person("Priya", "Shah", aliases=["Priya N."]),
        ]
    )
    assert r.people[0].alias_keys == ("priya n", "p nair")
    assert r.people[1].alias_keys == ("priya n",)
    assert err([person(aliases="Priya N.")]).count("aliases") >= 1


def test_unknown_person_key_and_non_array():
    assert 'key "person[0].nickname": unknown key' in err([person(nickname="x")])
    with pytest.raises(EnvError):
        parse_roster({"first_name": "x"})


def test_empty_roster():
    assert len(parse_roster(None)) == 0 and len(parse_roster([])) == 0
    assert isinstance(Roster(), Roster)


@pytest.mark.ac("AC-05.12")
def test_appendix_c_roster_has_nine_people_and_two_namesake_groups():
    r = load_config(FIX / "valid_full.toml").roster
    assert len(r) == 9
    groups = r.namesake_groups()
    assert set(groups) == {"priya", "rahul"}
    assert [p.full_name for p in groups["priya"]] == ["Priya Nair", "Priya Shah"]
    assert [p.full_name for p in groups["rahul"]] == ["Rahul Mehta", "Rahul Verma"]
    ids = [p.person_id for p in r]
    assert len(set(ids)) == 9 and "priya-nair" in ids and "lena-fischer" in ids
    assert r.by_person_id("priya-shah").email == "pranay.pathak@coditas.com"
    assert r.people[0].identity_key == "priya nair"


def test_roster_errors_collected_with_config_errors(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('bogus = 1\n[[person]]\nfirst_name = "A"\n', encoding="utf-8")
    msg = str(_file_err(p))
    assert '"bogus"' in msg and '"person[0].last_name"' in msg


def test_person_ids_unique_after_collision():
    r = parse_roster([person("Zoë", "Åström"), person("李雷", "王"), person("Anita", "Desai")])
    ids = [p.person_id for p in r]
    assert len(set(ids)) == 3 and all(ids)
