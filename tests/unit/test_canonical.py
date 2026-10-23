"""T-M5-22: pure canonical field selection (spec 10.6.1, AC-07.15)."""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from actiontrack.domain.canonical import (
    OwnerView,
    SourceView,
    earliest_source,
    latest_source,
    select_canonical,
    source_order_key,
)

KARAN = OwnerView("Karan", "owner", "karan-joshi", "resolved", "roster")
ANITA = OwnerView("Anita", "owner", "anita-desai", "resolved", "roster")


def sv(sid, rel, line, d, **kw):
    base = dict(
        source_id=sid,
        rel_path=rel,
        line_start=line,
        meeting_date=date.fromisoformat(d) if d else None,
        description=f"desc-{sid}",
        task_text=f"task-{sid}",
        core_key=f"core{sid}",
    )
    base.update(kw)
    return SourceView(**base)


@pytest.mark.ac("AC-07.15")
def test_description_earliest_due_owner_status_latest_and_present_toggle():
    s1 = sv(1, "2026-09-29-a.md", 12, "2026-09-29", owners=(KARAN,))
    s2 = sv(
        2, "2026-10-08-b.md", 11, "2026-10-08", owners=(ANITA,), due_raw="9 Oct", due_date=date(2026, 10, 9),
        due_confidence="explicit", due_rule="text_date", checkbox_state="done",
    )
    c = select_canonical([s2, s1])  # input order never matters
    assert c.description == "desc-1" and c.task_text == "task-1" and c.core_key == "core1"
    assert c.due_date == date(2026, 10, 9) and c.due_confidence == "explicit" and c.due_rule == "text_date"
    assert c.due_raw == "9 Oct" and c.owners == (ANITA,) and c.note_status == "done"
    assert (c.description_source_id, c.driver_source_id) == (1, 2)
    # the 10-08 source goes absent: everything follows 09-29
    gone = dataclasses.replace(s2, present=False)
    c = select_canonical([s1, gone])
    assert c.due_date is None and c.due_confidence == "none" and c.owners == (KARAN,) and c.note_status == "open"
    assert c.description == "desc-1"
    # restored: switches back
    assert select_canonical([s1, s2]).due_date == date(2026, 10, 9)


def test_same_date_orders_by_ordinal_path_then_line():
    b = sv(1, "B.md", 5, "2026-10-01")
    a = sv(2, "a.md", 1, "2026-10-01")
    assert earliest_source([a, b]).source_id == 1  # 'B' (0x42) < 'a' (0x61), ordinal not locale
    assert latest_source([a, b]).source_id == 2
    x = sv(3, "a.md", 9, "2026-10-01")
    assert latest_source([a, x]).source_id == 3
    assert source_order_key(a) < source_order_key(x)


def test_undated_source_sorts_as_latest():
    dated = sv(1, "z.md", 1, "2026-10-01")
    undated = sv(2, "a.md", 1, None, due_date=date(2026, 12, 1), due_confidence="relative")
    assert latest_source([dated, undated]).source_id == 2
    assert earliest_source([dated, undated]).source_id == 1
    assert select_canonical([dated, undated]).due_date == date(2026, 12, 1)


def test_all_absent_follows_ordering_over_all_sources():
    s1 = sv(1, "a.md", 1, "2026-09-01", present=False, owners=(KARAN,))
    s2 = sv(2, "b.md", 1, "2026-09-02", present=False, owners=(ANITA,))
    c = select_canonical([s1, s2])
    assert c.description == "desc-1" and c.owners == (ANITA,)


def test_single_source_and_empty():
    s = sv(7, "a.md", 3, "2026-09-01", owners=(KARAN,), due_raw="Fri")
    c = select_canonical([s])
    assert (c.description_source_id, c.driver_source_id) == (7, 7) and c.due_raw == "Fri"
    with pytest.raises(ValueError):
        select_canonical([])


def test_task_text_falls_back_to_description_and_columns_never_include_status():
    s = sv(1, "a.md", 1, "2026-09-01", task_text="")
    c = select_canonical([s])
    assert c.task_text == "desc-1"
    cols = c.item_columns()
    assert "status" not in cols and set(cols) >= {"description", "task_text", "description_core_key", "due_date"}
