"""T-M5-10: pure Tier-1b predicate and holder selection (domain.matching section 4)."""

from __future__ import annotations

from datetime import date

import pytest

from actiontrack.domain.canonical import OwnerView, SourceView
from actiontrack.domain.matching import (
    ItemView,
    NewView,
    Tier1bMatch,
    pick_tier1b,
    tier1b_applies,
)

TOK = ("send", "revised", "quote", "acme")
WIN = 60


def owner(pid: str = "priya-nair", status: str = "resolved") -> tuple[OwnerView, ...]:
    return (OwnerView("Priya Nair", "owner", pid if status == "resolved" else None, status),)


def src(sid: int, day: str | None, file_id: int = 1, checkbox: str = "open") -> SourceView:
    return SourceView(sid, f"f{file_id}.md", 3, date.fromisoformat(day) if day else None, file_id, True, checkbox_state=checkbox)


def item(iid: int, day: str | None = "2026-09-01", *, status="open", tokens=TOK, owners=None, file_id=None, due=None):
    return ItemView(
        iid,
        tokens,
        status,
        owner() if owners is None else owners,
        (src(iid * 10, day, file_id if file_id is not None else iid),),
        due,
        "explicit" if due else "none",
    )


def new(day: str | None = "2026-09-15", *, status="done", tokens=TOK, owners=None, file_id=99, due=None):
    return NewView(
        file_id, date.fromisoformat(day) if day else None, tokens, owner() if owners is None else owners, due,
        "explicit" if due else "none", status,
    )


@pytest.mark.ac("AC-07.11")
def test_due_is_ignored_and_dated_open_item_matches_dateless_done_source():
    assert tier1b_applies(new(), item(1, due=date(2026, 9, 4)), window_days=WIN)
    assert tier1b_applies(new(due=date(2026, 9, 20)), item(1, due=date(2026, 9, 4)), window_days=WIN)


@pytest.mark.ac("AC-07.11")
def test_holder_is_most_recent_others_are_the_rest():
    a, b, c = item(1, "2026-09-01"), item(2, "2026-09-08"), item(3, "2026-09-03")
    m = pick_tier1b(new(), [a, b, c], window_days=WIN)
    assert isinstance(m, Tier1bMatch)
    assert m.holder is b and m.others == (a, c)  # others ascending id
    assert [i.item_id for i in m.items] == [1, 3, 2]


def test_tie_on_latest_meeting_date_goes_to_highest_item_id():
    a, b = item(7, "2026-09-08"), item(4, "2026-09-08")
    m = pick_tier1b(new(), [a, b], window_days=WIN)
    assert m is not None and m.holder.item_id == 7 and [o.item_id for o in m.others] == [4]


def test_single_match_is_the_holder_with_no_others():
    m = pick_tier1b(new(), [item(5)], window_days=WIN)
    assert m is not None and m.holder.item_id == 5 and m.others == ()


def test_no_match_returns_none():
    assert pick_tier1b(new(), [], window_days=WIN) is None
    assert pick_tier1b(new(), [item(1, tokens=("something", "else", "entirely"))], window_days=WIN) is None


# ----------------------------------------------------------------------------- AC-07.14 negatives
NEG = {
    "new source not done": dict(n=dict(status="open")),
    "existing item done": dict(i=dict(status="done")),
    "done note dated before the open item": dict(n=dict(day="2026-08-30")),
    "different owners": dict(n=dict(owners=owner("priya-shah"))),
    "new owner ambiguous": dict(n=dict(owners=owner(status="ambiguous"))),
    "item owner ambiguous": dict(i=dict(owners=owner(status="ambiguous"))),
    "item owner unassigned": dict(i=dict(owners=(OwnerView(None, "owner", None, "unassigned"),))),
    "item owner provisional": dict(i=dict(owners=(OwnerView("Zed", "owner", "zed", "provisional"),))),
    "recurrence word": dict(n=dict(tokens=("send", "weekly", "status", "report")), i=dict(tokens=("send", "weekly", "status", "report"))),
    "core under three tokens": dict(n=dict(tokens=("send", "quote")), i=dict(tokens=("send", "quote"))),
    "core differs": dict(n=dict(tokens=("send", "quote", "acme", "now"))),
    "window 61 days": dict(n=dict(day="2026-11-01")),
    "same file": dict(i=dict(file_id=99)),
    "unknown done date": dict(n=dict(day=None)),
    "unknown item date": dict(i=dict(day=None)),
}


@pytest.mark.parametrize("label", sorted(NEG))
@pytest.mark.ac("AC-07.14")
def test_negatives_never_match(label):
    spec = NEG[label]
    assert not tier1b_applies(new(**spec.get("n", {})), item(1, **spec.get("i", {})), window_days=WIN), label
    assert pick_tier1b(new(**spec.get("n", {})), [item(1, **spec.get("i", {}))], window_days=WIN) is None


def test_boundaries_equal_date_and_sixty_days_match():
    assert tier1b_applies(new("2026-09-01"), item(1, "2026-09-01"), window_days=WIN)  # same date is not earlier
    assert tier1b_applies(new("2026-10-31"), item(1, "2026-09-01"), window_days=WIN)  # 60 days inclusive


def test_window_is_measured_from_the_items_latest_source():
    two = ItemView(1, TOK, "open", owner(), (src(10, "2026-08-01", 1), src(11, "2026-10-20", 2)))
    assert tier1b_applies(new("2026-10-30"), two, window_days=WIN)  # 10 days from the latest, not 90 from the first
    assert not tier1b_applies(new("2026-10-10"), two, window_days=WIN)  # earlier than the latest source


def test_only_open_items_participate_in_a_mixed_candidate_list():
    done = item(1, status="done")
    open_ = item(2)
    m = pick_tier1b(new(), [done, open_], window_days=WIN)
    assert m is not None and m.holder.item_id == 2 and m.others == ()
