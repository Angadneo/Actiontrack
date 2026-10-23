"""T-M5-11: pure status derivation and the reopen test (domain.status)."""

from __future__ import annotations

from datetime import date

import pytest

from actiontrack.domain.canonical import SourceView
from actiontrack.domain.status import (
    EvidenceChange,
    ItemStatus,
    conflicting_manual_done,
    derive_status,
    later_dated,
    reopens,
)

D = date.fromisoformat


def sv(sid: int, day: str | None, checkbox: str = "open", present: bool = True) -> SourceView:
    return SourceView(sid, f"f{sid}.md", 1, D(day) if day else None, sid, present, checkbox_state=checkbox)


OPEN = ItemStatus("open")
NOTE_DONE = ItemStatus("done", "note", 2)
MANUAL = ItemStatus("done", "manual", None)


# ------------------------------------------------------------------------------------ derive_status
@pytest.mark.parametrize(
    ("item", "sources", "expected"),
    [
        (OPEN, [sv(1, "2026-09-01", "done")], ("done", "note", 1)),
        (OPEN, [sv(1, "2026-09-01")], ("open", None, None)),
        (OPEN, [], ("open", None, None)),
        # note-derived status follows the LATEST present source
        (OPEN, [sv(1, "2026-09-01", "done"), sv(2, "2026-09-08")], ("open", None, None)),
        (OPEN, [sv(1, "2026-09-01"), sv(2, "2026-09-08", "done")], ("done", "note", 2)),
        # an absent latest source is ignored while a present one exists
        (OPEN, [sv(1, "2026-09-01", "done"), sv(2, "2026-09-08", present=False)], ("done", "note", 1)),
        # a note completion stays done whatever the sources say (only the reopen rule undoes it)
        (NOTE_DONE, [sv(1, "2026-09-01"), sv(2, "2026-09-08")], ("done", "note", 2)),
        # manual done is sticky and always wins
        (MANUAL, [sv(1, "2026-09-01")], ("done", "manual", None)),
        (MANUAL, [], ("done", "manual", None)),
    ],
)
def test_derive_status_table(item, sources, expected):
    d = derive_status(item, sources)
    assert (d.status, d.origin, d.evidence_source_id) == expected
    assert d.completed_at is None


@pytest.mark.ac("AC-01.3")
def test_note_checkbox_done_is_origin_note_with_null_completed_at():
    d = derive_status(OPEN, [sv(1, "2026-09-01", "done")])
    assert (d.status, d.origin, d.completed_at) == ("done", "note", None)


# ------------------------------------------------------------------------------------- reopen rule
def change(src_id=2, now="open", old="2026-09-15", new="2026-09-15", present=True):
    return EvidenceChange(src_id, now, D(old) if old else None, D(new) if new else None, present)


@pytest.mark.ac("AC-07.16")
def test_evidence_line_edited_in_place_to_open_reopens_equal_date():
    assert reopens(NOTE_DONE, change())


@pytest.mark.ac("AC-07.16")
def test_later_meeting_date_reopens_earlier_does_not():
    assert reopens(NOTE_DONE, change(new="2026-09-20"))
    assert not reopens(NOTE_DONE, change(new="2026-09-10"))  # literal 10.7.3 reading (G-C24)


@pytest.mark.ac("AC-07.16")
def test_other_source_or_still_done_or_removed_never_reopens():
    assert not reopens(NOTE_DONE, change(src_id=1))  # unrelated source row
    assert not reopens(NOTE_DONE, change(now="done"))  # evidence still [x]
    assert not reopens(NOTE_DONE, change(present=False))  # line removal is not a [ ] (G-C24)


@pytest.mark.ac("AC-07.16")
def test_manual_done_is_never_reopened():
    for c in (change(), change(new="2026-12-01"), change(src_id=1)):
        assert not reopens(MANUAL, c)


def test_open_item_or_item_without_evidence_has_nothing_to_reopen():
    assert not reopens(OPEN, change())
    assert not reopens(ItemStatus("done", "note", None), change())


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [("2026-09-15", "2026-09-15", True), ("2026-09-15", "2026-09-16", True), ("2026-09-15", "2026-09-14", False),
     (None, None, True), (None, "2026-09-15", False), ("2026-09-15", None, False)],
)
def test_later_dated(old, new, expected):
    assert later_dated(D(old) if old else None, D(new) if new else None) is expected


# --------------------------------------------------------------------------- W-STATUS-CONFLICT test
@pytest.mark.ac("AC-03.6")
def test_conflict_only_for_manual_done_with_open_latest_present_source():
    assert conflicting_manual_done(MANUAL, [sv(1, "2026-09-01", "done"), sv(2, "2026-09-08")])
    assert not conflicting_manual_done(MANUAL, [sv(1, "2026-09-01"), sv(2, "2026-09-08", "done")])
    assert not conflicting_manual_done(MANUAL, [sv(1, "2026-09-01", present=False)])  # nothing present
    assert not conflicting_manual_done(MANUAL, [])
    assert not conflicting_manual_done(NOTE_DONE, [sv(1, "2026-09-01")])
    assert not conflicting_manual_done(OPEN, [sv(1, "2026-09-01")])
    # a later-ordered absent [ ] does not count while a present [x] exists
    assert not conflicting_manual_done(MANUAL, [sv(1, "2026-09-01", "done"), sv(2, "2026-09-08", present=False)])
