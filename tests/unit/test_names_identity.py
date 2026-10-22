"""Tests for domain/names.py minimum (T-M1-05; spec 10.2, 10.2a)."""

from __future__ import annotations

import pytest

from actiontrack.domain.names import (
    folded_key,
    identity_key,
    is_known_identity,
    name_key,
    person_id_for,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Priya  Nair", "priya nair"),
        ("  PRIYA\tnair ", "priya nair"),
        ("Dr. Priya Nair", "priya nair"),
        ("Mrs Priya Nair", "priya nair"),
        ("Priya, Nair!", "priya nair"),
        ("Nair-Rao", "nair-rao"),
        ("O’Brien", "o'brien"),
        ("-Priya-", "priya"),
        ("José", "josé"),
        ("", ""),
    ],
)
def test_name_key_table(raw, expected):
    assert name_key(raw) == expected


@pytest.mark.ac("AC-05.12")
def test_identity_key_cases():
    base = identity_key("Priya Nair")
    assert base == "priya nair"
    assert identity_key("Priya R. Nair") == base
    assert identity_key("Dr Priya Nair") == base
    assert identity_key("PRIYA  nair") == base
    assert identity_key("@Priya Nair".lstrip("@")) == base
    assert identity_key("Priya Nair-Rao") == "priya nair-rao"
    assert identity_key("José Alvarez") != identity_key("Jose Alvarez")


def test_identity_key_corner_cases():
    assert identity_key("Zed") == "zed" and not is_known_identity("Zed")
    assert identity_key("Priya N.") is None
    assert identity_key("Priya N") is None
    assert identity_key("") is None and identity_key("   ") is None
    assert is_known_identity("Priya Nair") and not is_known_identity("Priya N.")
    assert folded_key("José Alvarez") == folded_key("Jose Alvarez") == "jose alvarez"
    assert identity_key("José Alvarez") != identity_key("Jose Alvarez")


def test_identity_key_non_latin_last_token_is_not_initial():
    assert identity_key("李雷 王") == "李雷 王"
    assert is_known_identity("李雷 王")


def test_identity_key_with_qualifier():
    assert identity_key("Priya Nair", "Sales") == "priya nair|sales"
    assert identity_key("Priya Nair", "  ") == "priya nair"
    assert identity_key("Priya Nair", "Sales") != identity_key("Priya Nair", "Ops")


def test_person_id_basics():
    assert person_id_for("priya nair") == "priya-nair"
    assert person_id_for("priya nair|sales") == "priya-nair-sales"
    assert person_id_for("priya nair-rao") == "priya-nair-rao"
    assert person_id_for("o'brien x") == "o-brien-x"


@pytest.mark.ac("AC-09.6")
def test_person_id_unicode():
    a = person_id_for(identity_key("Zoë Åström"))
    b = person_id_for(identity_key("李雷 王"))
    assert a == person_id_for(identity_key("Zoë Åström")) == "zoë-åström"
    assert b == "李雷-王"
    for pid in (a, b):
        assert pid and not any(c in pid for c in '<>:"/\\|?* ')


def test_person_id_collision_suffix_and_fallback():
    assert person_id_for("priya nair", taken={"priya-nair"}) == "priya-nair-2"
    assert person_id_for("priya nair", taken={"priya-nair", "priya-nair-2"}) == "priya-nair-3"
    fb = person_id_for("!!!")
    assert fb.startswith("p-") and fb == person_id_for("!!!")
