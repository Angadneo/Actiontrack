"""PeopleRegistry (T-M4-05; spec 10.2a, 10.3, BR-06, BR-23, AC-05.10, AC-05.11, SC-52)."""

from __future__ import annotations

from pathlib import Path

import pytest

from actiontrack.config import load_config, parse_roster
from actiontrack.domain.models import Person, PersonOrigin
from actiontrack.domain.names import identity_key, provisional_key
from actiontrack.resolution.people import PeopleRegistry, base_key, is_full

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "config"


def appendix_c_roster():
    """The 9-person roster of the sample config (both Priyas, both Rahuls)."""
    return load_config(FIX / "valid_full.toml").roster


def roster_registry(extra=()):
    reg = PeopleRegistry()
    reg.add_roster(appendix_c_roster())
    if extra:
        reg.add_roster(parse_roster(list(extra)))
    return reg


def custom_roster(*entries):
    """Registry from (first, last[, aliases]) tuples."""
    reg = PeopleRegistry()
    rows = []
    for e in entries:
        first, last, *rest = e
        rows.append({"first_name": first, "last_name": last, "aliases": list(rest[0]) if rest else []})
    reg.add_roster(parse_roster(rows))
    return reg


def test_appendix_c_namesake_groups_and_flags():
    reg = roster_registry()
    assert len(reg) == 9
    assert reg.namesake_groups() == {
        "priya": ("priya-nair", "priya-shah"),
        "rahul": ("rahul-mehta", "rahul-verma"),
    }
    assert reg.is_namesake("priya-nair") and reg.is_namesake("rahul-verma")
    assert not reg.is_namesake("anita-desai")
    assert not reg.is_namesake("nobody")


def test_roster_persons_carry_email_and_are_verified():
    reg = roster_registry()
    nair = reg.get("priya-nair")
    assert nair.origin is PersonOrigin.ROSTER and nair.verified is True
    assert nair.email == "angad.kulkarni@coditas.com"
    assert reg.get("rahul-mehta").email is None
    assert nair.display_name == "Priya Nair" and nair.identity_key == "priya nair"
    assert [a.alias_key for a in reg.aliases("priya-nair")] == ["priya n"]
    assert all(not a.auto for a in reg.aliases())


def test_qualified_roster_people_share_base_identity_but_have_distinct_ids():
    reg = PeopleRegistry()
    reg.add_roster(
        parse_roster(
            [
                {"first_name": "Priya", "last_name": "Nair", "qualifier": "Sales"},
                {"first_name": "Priya", "last_name": "Nair", "qualifier": "Eng"},
            ]
        )
    )
    assert reg.ids() == ("priya-nair-eng", "priya-nair-sales")
    assert reg.lookup_identity("priya nair") == ("priya-nair-eng", "priya-nair-sales")
    assert reg.full_name("priya-nair-sales") == "Priya Nair (Sales)"
    assert base_key(reg.get("priya-nair-sales").identity_key) == "priya nair"


@pytest.mark.ac("AC-05.10")
@pytest.mark.sc("SC-52")
def test_add_attendee_same_identity_never_creates_second_person():
    reg = PeopleRegistry()
    a = reg.add_attendee("Priya Nair")
    b = reg.add_attendee("priya nair")
    c = reg.add_attendee("Dr Priya R. Nair")
    assert a.id == b.id == c.id == "priya-nair"
    assert len(reg) == 1
    assert reg.get("priya-nair").origin is PersonOrigin.ATTENDEE
    assert reg.get("priya-nair").verified is False


def test_attendee_does_not_mutate_roster_person():
    reg = roster_registry()
    before = reg.get("priya-nair")
    reg.add_attendee("Priya R. Nair", ordinal=5)
    reg.note_spelling("priya-nair", "Priya R. Nair")
    assert reg.get("priya-nair") == before
    assert len(reg) == 9


def test_provisional_identical_keys_share_one_person():
    reg = PeopleRegistry()
    a = reg.add_provisional("Zed Zed")
    b = reg.add_provisional("zed  zed")
    assert a.id == b.id == "zed-zed"
    assert a.origin is PersonOrigin.NOTE_OWNER and a.verified is False
    assert is_full(a)


def test_single_token_and_initial_form_provisional_keys():
    reg = PeopleRegistry()
    z = reg.add_provisional("Zed")
    zq = reg.add_provisional("Zed Q.")
    assert (z.identity_key, zq.identity_key) == ("zed", "zed q")
    assert z.last_name is None and zq.last_name is None
    assert reg.lookup_non_full("zed") == z.id and reg.lookup_non_full("zed q") == zq.id
    assert provisional_key("Zed Q.") == "zed q"
    # never found through the identity index of a First+Last person (10.2a(a)/(b))
    reg.add_provisional("Zed Zed")
    assert reg.lookup_identity(identity_key("Zed Zed")) == ("zed-zed",)
    assert reg.lookup_identity("zed") == () and reg.lookup_identity("zed q") == ()
    assert reg.lookup_first_name("zed") == ("zed-zed",)  # non-full people are not in C


def test_lookups_are_sorted_and_independent_of_insertion_order():
    names = ["Rahul Verma", "Rahul Mehta", "Rahul Abel"]
    a, b = PeopleRegistry(), PeopleRegistry()
    for n in names:
        a.add_attendee(n)
    for n in reversed(names):
        b.add_attendee(n)
    assert a.lookup_first_name("rahul") == b.lookup_first_name("rahul") == (
        "rahul-abel",
        "rahul-mehta",
        "rahul-verma",
    )
    assert a == b
    assert a.lookup_initial("rahul", "m") == ("rahul-mehta",)


def test_merge_provisional_into_known_keeps_alias_and_removes_provisional():
    reg = custom_roster(("Priya", "Nair"), ("Anita", "Desai"))
    prov = reg.add_provisional("Priya N.")
    rec = reg.merge_provisional(prov.id, "priya-nair")
    assert prov.id not in reg and reg.get(prov.id) is None
    assert (rec.provisional_id, rec.into_person_id, rec.alias_key) == (prov.id, "priya-nair", "priya n")
    alias = reg.aliases("priya-nair")[0]
    assert alias.alias_key == "priya n" and alias.auto is True
    assert reg.lookup_alias("priya n") == ("priya-nair",)
    assert reg.lookup_non_full("priya n") is None


def test_merge_keeps_roster_alias_non_auto():
    reg = roster_registry()
    prov = reg.add_provisional("Priya N.")
    reg.merge_provisional(prov.id, "priya-nair")
    (alias,) = reg.aliases("priya-nair")
    assert alias.alias_key == "priya n" and alias.auto is False


def test_merge_never_removes_a_roster_person_or_self():
    reg = roster_registry()
    with pytest.raises(ValueError):
        reg.merge_provisional("priya-nair", "priya-shah")
    prov = reg.add_provisional("Zed")
    with pytest.raises(ValueError):
        reg.merge_provisional(prov.id, prov.id)
    with pytest.raises(KeyError):
        reg.merge_provisional("missing", "priya-nair")


def test_auto_alias_of_bare_first_name_is_not_pinned_once_namesake_arrives():
    """G-C28 default: the recorded auto-alias ``priya`` is ignored while shared by >= 2 people."""
    reg = custom_roster(("Priya", "Nair"))
    prov = reg.add_provisional("Priya")
    reg.merge_provisional(prov.id, "priya-nair")
    assert reg.lookup_alias("priya") == ("priya-nair",)  # lone Priya: consulted
    reg.add_roster(parse_roster([{"first_name": "Priya", "last_name": "Nair"}, {"first_name": "Priya", "last_name": "Shah"}]))
    assert reg.get("priya-shah") is not None
    assert reg.lookup_alias("priya") == ()  # shared -> falls to steps 2-3
    assert reg.aliases("priya-nair")[0].alias_key == "priya" and reg.aliases("priya-nair")[0].auto


def test_auto_alias_of_initial_form_not_pinned_when_shared():
    reg = custom_roster(("Priya", "Nair"))
    prov = reg.add_provisional("Priya N.")
    reg.merge_provisional(prov.id, "priya-nair")
    assert reg.lookup_alias("priya n") == ("priya-nair",)
    reg.add_roster(parse_roster([{"first_name": "Priya", "last_name": "Nair"}, {"first_name": "Priya", "last_name": "Narang"}]))
    assert reg.lookup_alias("priya n") == ()


def test_auto_alias_of_full_name_resolves_directly():
    reg = custom_roster(("Priya", "Nair"))
    prov = reg.add_provisional("Priya Nayar")
    reg.merge_provisional(prov.id, "priya-nair")  # (policy decides; the primitive allows it)
    assert reg.lookup_alias("priya nayar") == ("priya-nair",)


def test_explicit_roster_alias_of_bare_first_name_is_consulted_even_when_shared():
    reg = custom_roster(("Priya", "Nair"), ("Priya", "Shah", ["Priya"]))
    assert reg.lookup_alias("priya") == ("priya-shah",)


def test_alias_mapping_to_two_people():
    reg = custom_roster(("Priya", "Nair", ["Priya (Sales)"]), ("Priya", "Shah", ["Priya (Sales)"]))
    assert reg.lookup_alias("priya sales") == ("priya-nair", "priya-shah")


def test_display_name_roster_wins_then_most_complete_attendee_then_first_seen():
    reg = roster_registry()
    reg.note_spelling("priya-nair", "P. Nair", ordinal=1)
    assert reg.display_name("priya-nair") == "Priya Nair"
    assert reg.full_name("priya-nair") == "Priya Nair"

    r2 = PeopleRegistry()
    r2.add_attendee("Priya N.", ordinal=1)  # non-full person 'priya n'
    p = r2.add_attendee("Priya Nair", ordinal=2)
    r2.note_spelling(p.id, "Priya N.", ordinal=3)
    assert r2.display_name(p.id) == "Priya Nair"  # longer spelling beats the initial form

    r3 = PeopleRegistry()
    q = r3.add_attendee("priya nair", ordinal=4)
    r3.note_spelling(q.id, "Priya Nair", ordinal=2)  # same token count and length: lower ordinal wins
    assert r3.display_name(q.id) == "Priya Nair"
    r3.note_spelling(q.id, "Priya R. Nair", ordinal=9)  # more tokens = more complete
    assert r3.display_name(q.id) == "Priya R. Nair"
    assert r3.get(q.id).display_name == "Priya R. Nair"  # stored Person follows


def test_display_name_is_order_independent():
    spell = [("Priya N. Nair", 3), ("Priya Nair", 1), ("Priya R. Nair", 2)]
    results = set()
    import itertools

    for perm in itertools.permutations(spell):
        reg = PeopleRegistry()
        pid = reg.add_attendee("Priya Nair", ordinal=10).id
        for raw, o in perm:
            reg.note_spelling(pid, raw, ordinal=o)
        results.add(reg.display_name(pid))
    assert len(results) == 1


def test_display_name_provisional_first_seen_spelling():
    reg = PeopleRegistry()
    p = reg.add_provisional("ZED ZED", ordinal=2)
    reg.note_spelling(p.id, "Zed Zed", ordinal=1, attendee=False)
    assert reg.display_name(p.id) == "Zed Zed"


def test_roster_upgrade_keeps_id_of_existing_attendee_person():
    reg = PeopleRegistry()
    reg.add_attendee("Priya Nair")
    reg.add_roster(parse_roster([{"first_name": "Priya", "last_name": "Nair", "email": "p@x.org"}]))
    p = reg.get("priya-nair")
    assert len(reg) == 1 and p.origin is PersonOrigin.ROSTER and p.email == "p@x.org" and p.verified


def test_readding_roster_replaces_non_auto_aliases_only():
    reg = custom_roster(("Priya", "Nair", ["Priya (Sales)"]))
    reg.add_alias("priya-nair", "Pri", auto=True)
    reg.add_roster(parse_roster([{"first_name": "Priya", "last_name": "Nair", "aliases": ["PN"]}]))
    assert {(a.alias_key, a.auto) for a in reg.aliases("priya-nair")} == {("pn", False), ("pri", True)}


def test_snapshot_restore_round_trip_leaves_indexes_identical():
    reg = roster_registry()
    snap = reg.snapshot()
    before = (reg.people(), reg.aliases(), reg.namesake_groups(), reg.lookup_first_name("priya"))
    reg.add_attendee("Zed Zed")
    prov = reg.add_provisional("Priya N.")
    reg.merge_provisional(prov.id, "priya-nair")
    reg.add_alias("anita-desai", "AD", auto=True)
    reg.note_spelling("anita-desai", "Anita D.")
    assert reg.lookup_first_name("zed") == ("zed-zed",)
    reg.restore(snap)
    after = (reg.people(), reg.aliases(), reg.namesake_groups(), reg.lookup_first_name("priya"))
    assert before == after
    assert reg.lookup_first_name("zed") == () and reg == roster_registry()
    # the snapshot itself is not aliased to live state
    reg.add_attendee("Another Person")
    reg.restore(snap)
    assert len(reg) == 9


def test_copy_is_independent():
    reg = roster_registry()
    clone = reg.copy()
    assert clone == reg
    clone.add_attendee("Zed Zed")
    assert len(reg) == 9 and len(clone) == 10 and clone != reg


def test_rebuild_from_rows_equals_live_registry():
    live = roster_registry()
    live.add_attendee("Zed Zed")
    prov = live.add_provisional("Priya N.")
    live.merge_provisional(prov.id, "priya-nair")
    live.add_provisional("Zed Q.")

    rebuilt = PeopleRegistry()
    rebuilt.add_roster(appendix_c_roster())
    roster_alias_keys = {(a.person_id, a.alias_key) for a in rebuilt.aliases()}
    for p in live.people():
        if p.origin is not PersonOrigin.ROSTER:
            rebuilt.load_person(p)
    for a in live.aliases():
        if (a.person_id, a.alias_key) not in roster_alias_keys:
            rebuilt.load_alias(a.person_id, a.alias, a.alias_key, auto=True)
    assert rebuilt == live


def test_people_returns_person_objects_sorted_by_id():
    reg = roster_registry()
    assert [p.id for p in reg.people()] == sorted(p.id for p in reg.people())
    assert all(isinstance(p, Person) for p in reg.people())
