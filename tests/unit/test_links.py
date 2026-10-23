"""T-M5-09: pure link classification (spec 10.6 'otherwise' clause, G-27 precedence, non-matches)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from actiontrack.codes import ItemLinkReason as R
from actiontrack.domain import matching as m
from actiontrack.domain.canonical import OwnerView, SourceView

PRIYA_N = OwnerView("Priya Nair", "owner", "priya-nair", "resolved", "roster")
PRIYA_S = OwnerView("Priya Shah", "owner", "priya-shah", "resolved", "roster")
RAHUL = OwnerView("Rahul", "owner", "rahul-mehta", "resolved", "roster")
AMBIG = OwnerView("Priya", "owner", None, "ambiguous", "none", ("priya-nair", "priya-shah"))
D0 = date(2026, 10, 1)
CORE = ("set", "up", "staging", "environment")
KW = dict(window_days=60, fuzzy_threshold=0.85)


def item(*, core=CORE, status="open", owners=(RAHUL,), due=None, conf="none", file_id=1, d=D0, item_id=1):
    return m.ItemView(item_id, core, status, owners, (SourceView(1, "a.md", 1, d, source_file_id=file_id),), due, conf)


def new(*, core=CORE, status="open", owners=(RAHUL,), due=None, conf="none", file_id=2, d=D0 + timedelta(days=7)):
    return m.NewView(file_id, d, core, owners, due, conf, status)


def cls(n, i):
    return m.classify_pair(n, i, **KW)


EXPLICIT = {"due": date(2026, 10, 9), "conf": "explicit"}


def test_precedence_constant_is_the_spec_order():
    assert m.LINK_PRECEDENCE == (
        R.OWNER_DIFFERS, R.STATUS_DIFFERS, R.DUE_DIFFERS, R.SAME_FILE, R.RECURRENCE, R.OUTSIDE_WINDOW,
        R.OWNER_UNRESOLVED, R.FUZZY,
    )  # fmt: skip


@pytest.mark.ac("AC-07.2", "AC-07.13")
def test_due_differs_variants():
    assert cls(new(**EXPLICIT), item(due=date(2026, 10, 8), conf="explicit")) is R.DUE_DIFFERS
    assert cls(new(**EXPLICIT), item()) is R.DUE_DIFFERS  # one null
    assert cls(new(), item(**EXPLICIT)) is R.DUE_DIFFERS
    assert cls(new(conf="unresolved"), item(**EXPLICIT)) is R.DUE_DIFFERS  # unresolved vs date
    assert cls(new(conf="unresolved"), item(conf="unresolved")) is R.DUE_DIFFERS


@pytest.mark.ac("AC-07.3", "AC-05.16")
@pytest.mark.sc("SC-39")
def test_owner_differs_and_namesakes():
    assert cls(new(owners=(PRIYA_N,)), item(owners=(RAHUL,))) is R.OWNER_DIFFERS
    assert cls(new(owners=(PRIYA_N,)), item(owners=(PRIYA_S,))) is R.OWNER_DIFFERS


@pytest.mark.ac("AC-07.4")
@pytest.mark.sc("SC-32")
def test_status_differs_only_existing_done_new_open():
    assert cls(new(), item(status="done")) is R.STATUS_DIFFERS
    # existing open + new done is status-compatible (Tier-1 row / Tier-1b): no status reason
    assert cls(new(status="done", **EXPLICIT), item(status="open")) is R.DUE_DIFFERS


@pytest.mark.ac("AC-07.6")
def test_same_file():
    assert cls(new(file_id=1), item()) is R.SAME_FILE


@pytest.mark.ac("AC-07.7")
def test_recurrence_even_when_everything_else_matches():
    core = ("send", "weekly", "status", "report")
    assert cls(new(core=core), item(core=core)) is R.RECURRENCE


def test_outside_window_and_unknown_date():
    assert cls(new(d=D0 + timedelta(days=61)), item()) is R.OUTSIDE_WINDOW
    assert cls(new(d=None), item()) is R.OUTSIDE_WINDOW
    assert cls(new(), item(d=None)) is R.OUTSIDE_WINDOW


@pytest.mark.ac("AC-07.8")
def test_owner_unresolved_last_resort():
    assert cls(new(owners=(AMBIG,)), item(owners=(AMBIG,))) is R.OWNER_UNRESOLVED
    assert cls(new(owners=(AMBIG,)), item(owners=(RAHUL,))) is R.OWNER_UNRESOLVED  # not owner_differs


def test_short_core_identical_falls_to_fuzzy():
    core = ("send", "quote")
    assert cls(new(core=core), item(core=core)) is R.FUZZY


def test_precedence_one_pair_per_adjacent_step():
    far = D0 + timedelta(days=200)
    rec = ("send", "weekly", "status", "report")
    # owner_differs > status_differs
    assert cls(new(owners=(PRIYA_N,)), item(status="done")) is R.OWNER_DIFFERS
    # status_differs > due_differs
    assert cls(new(**EXPLICIT), item(status="done")) is R.STATUS_DIFFERS
    # due_differs > same_file
    assert cls(new(file_id=1, **EXPLICIT), item()) is R.DUE_DIFFERS
    # same_file > recurrence
    assert cls(new(file_id=1, core=rec), item(core=rec)) is R.SAME_FILE
    # recurrence > outside_window
    assert cls(new(core=rec, d=far), item(core=rec)) is R.RECURRENCE
    # outside_window > owner_unresolved
    assert cls(new(owners=(AMBIG,), d=far), item(owners=(AMBIG,))) is R.OUTSIDE_WINDOW
    # unknown date -> outside_window even with unresolved owner
    assert cls(new(owners=(AMBIG,), d=None), item(owners=(AMBIG,))) is R.OUTSIDE_WINDOW
    # owner_unresolved > fuzzy never both: unresolved owners cannot fuzzy-match
    assert m.link_reasons(new(owners=(AMBIG,)), item(owners=(AMBIG,)), **KW) == {R.OWNER_UNRESOLVED}


def test_link_reasons_lists_every_applicable_reason():
    n = new(owners=(PRIYA_N,), file_id=1, **EXPLICIT, d=D0 + timedelta(days=400))
    got = m.link_reasons(n, item(status="done"), **KW)
    assert got == {R.OWNER_DIFFERS, R.STATUS_DIFFERS, R.DUE_DIFFERS, R.SAME_FILE, R.OUTSIDE_WINDOW}


def test_tier1_pair_is_not_classified():
    assert cls(new(), item()) is None


@pytest.mark.ac("AC-07.10")
def test_non_matches_and_fuzzy():
    a = ("share", "first", "wireframes")
    b = ("share", "updated", "wireframes")
    assert m.jaccard(a, b) == 0.5
    assert cls(new(core=a), item(core=b)) is None
    assert cls(new(core=("ask", "acme", "procurement", "contact")), item(core=("send", "procurement", "form"))) is None
    # Jaccard 0.9 (9 of 10 tokens shared... 9/10) same resolved owner -> fuzzy
    nine = tuple(f"w{i}" for i in range(9))
    ten = (*nine, "extra")
    assert round(m.jaccard(nine, ten), 2) == 0.9
    assert cls(new(core=ten), item(core=nine)) is R.FUZZY
    # same similarity, different owners -> no link
    assert cls(new(core=ten, owners=(PRIYA_N,)), item(core=nine)) is None
    # fuzzy with a differing due and the same owner is still fuzzy (only different-owner pairs are non-matches)
    assert cls(new(core=ten, **EXPLICIT), item(core=nine)) is R.FUZZY
    # unresolved owners never fuzzy
    assert cls(new(core=ten, owners=(AMBIG,)), item(core=nine, owners=(AMBIG,))) is None
    # below threshold
    assert cls(new(core=ten), item(core=("w0", "w1", "zzz"))) is None
    # same token set, different order: not identical core -> fuzzy
    assert cls(new(core=("b", "a", "c")), item(core=("a", "b", "c"))) is R.FUZZY


def test_empty_core_never_links():
    assert cls(new(core=()), item(core=())) is None
