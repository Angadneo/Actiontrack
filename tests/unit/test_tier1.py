"""T-M5-08: pure Tier-1 predicate (spec 10.6 conditions 1-6)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from actiontrack.domain import matching as m
from actiontrack.domain.canonical import OwnerView, SourceView

PRIYA_N = OwnerView("Priya Nair", "owner", "priya-nair", "resolved", "roster")
PRIYA_S = OwnerView("Priya Shah", "owner", "priya-shah", "resolved", "roster")
RAHUL = OwnerView("Rahul", "owner", "rahul-mehta", "resolved", "roster")
AMBIG = OwnerView("Priya", "owner", None, "ambiguous", "none", ("priya-nair", "priya-shah"))
NOBODY = OwnerView(None, "owner", None, "unassigned", "none")
D0 = date(2026, 10, 1)
CORE = ("freeze", "invoice", "edits")
WIN = 60


def item(*, core=CORE, status="open", owners=(RAHUL,), due=None, conf="none", file_id=1, d=D0, item_id=1, sources=None):
    srcs = sources if sources is not None else (SourceView(1, "a.md", 1, d, source_file_id=file_id),)
    return m.ItemView(item_id, core, status, owners, srcs, due, conf)


def new(*, core=CORE, status="open", owners=(RAHUL,), due=None, conf="none", file_id=2, d=D0 + timedelta(days=7)):
    return m.NewView(file_id, d, core, owners, due, conf, status)


@pytest.mark.ac("AC-07.1")
@pytest.mark.sc("SC-03")
def test_all_six_hold_is_tier1():
    assert m.tier1_failures(new(), item(), window_days=WIN) == ()
    assert m.is_tier1(new(due=date(2026, 10, 9), conf="explicit"), item(due=date(2026, 10, 9), conf="relative"), window_days=WIN)


def test_negative_table():
    base_new, base_item = new(), item()
    cases = {
        "core differs": (new(core=("freeze", "invoice", "edit")), base_item, 1),
        "core under 3 tokens": (new(core=("send", "quote")), item(core=("send", "quote")), 2),
        "recurrence": (
            new(core=("send", "weekly", "status", "report")),
            item(core=("send", "weekly", "status", "report")),
            2,
        ),
        "owner differs": (new(owners=(PRIYA_N,)), base_item, 3),
        "new owner ambiguous": (new(owners=(AMBIG,)), base_item, 3),
        "existing owner ambiguous": (base_new, item(owners=(AMBIG,)), 3),
        "unassigned": (new(owners=(NOBODY,)), item(owners=(NOBODY,)), 3),
        "due differs": (new(due=date(2026, 10, 9), conf="explicit"), item(due=date(2026, 10, 8), conf="explicit"), 4),
        "one due null": (new(due=date(2026, 10, 9), conf="explicit"), base_item, 4),
        "unresolved vs unresolved": (new(conf="unresolved"), item(conf="unresolved"), 4),
        "existing done new open": (base_new, item(status="done"), 5),
        "same file": (new(file_id=1), base_item, 6),
        "meeting date null": (new(d=None), base_item, 6),
        "existing latest date null": (
            base_new,
            item(sources=(SourceView(1, "a.md", 1, None, source_file_id=1),)),
            6,
        ),
    }
    for label, (n, i, cond) in cases.items():
        assert cond in m.tier1_failures(n, i, window_days=WIN), label
        assert not m.is_tier1(n, i, window_days=WIN), label


def test_window_boundary_60_inclusive_both_directions():
    it = item()
    assert m.is_tier1(new(d=D0 + timedelta(days=60)), it, window_days=WIN)
    assert m.tier1_failures(new(d=D0 + timedelta(days=61)), it, window_days=WIN) == (6,)
    assert m.is_tier1(new(d=D0 - timedelta(days=60)), it, window_days=WIN)  # absolute difference
    assert m.tier1_failures(new(d=D0 - timedelta(days=61)), it, window_days=WIN) == (6,)
    assert m.is_tier1(new(d=D0 + timedelta(days=10)), it, window_days=10)
    assert not m.is_tier1(new(d=D0 + timedelta(days=11)), it, window_days=10)


def test_window_measured_from_latest_source_and_file_rule_covers_absent_sources():
    srcs = (
        SourceView(1, "a.md", 1, D0, source_file_id=1),
        SourceView(2, "b.md", 1, D0 + timedelta(days=50), source_file_id=3),
    )
    it = item(sources=srcs)
    assert m.is_tier1(new(d=D0 + timedelta(days=100)), it, window_days=WIN)  # 50 from the latest source
    gone = (SourceView(1, "a.md", 1, D0, source_file_id=1, present=False),)
    # same file rule counts non-present sources too
    assert 6 in m.tier1_failures(new(file_id=1), item(sources=gone), window_days=WIN)


def test_status_table():
    assert m.is_tier1(new(status="done"), item(status="open"), window_days=WIN)  # existing open + new done
    assert m.is_tier1(new(status="done"), item(status="done"), window_days=WIN)
    assert m.is_tier1(new(status="open"), item(status="open"), window_days=WIN)
    assert not m.is_tier1(new(status="open"), item(status="done"), window_days=WIN)


def test_with_coowner_included_in_person_set():
    with_rahul = OwnerView("Priya", "with", "rahul-mehta", "resolved", "roster")
    two = (PRIYA_N, OwnerView("Rahul", "with", "rahul-mehta", "resolved", "roster"))
    assert m.owner_person_set(two) == {"priya-nair", "rahul-mehta"}
    assert not m.is_tier1(new(owners=two), item(owners=(PRIYA_N,)), window_days=WIN)
    assert m.is_tier1(new(owners=(PRIYA_N, with_rahul)), item(owners=two), window_days=WIN)


@pytest.mark.ac("AC-05.16")
def test_namesakes_never_tier1():
    assert not m.is_tier1(new(owners=(PRIYA_N,)), item(owners=(PRIYA_S,)), window_days=WIN)


def test_pick_lowest_item_id_and_stop_words_do_not_matter_here():
    a = item(item_id=5)
    b = item(item_id=3)
    c = item(item_id=1, owners=(PRIYA_N,))
    assert m.pick_tier1(new(), [a, b, c], window_days=WIN).item_id == 3
    assert m.pick_tier1(new(owners=(PRIYA_S,)), [a, b, c], window_days=WIN) is None
