"""T-M5-14: the end-of-run finalisation transaction (whole-database passes, state counters, run status)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.clock import FixedClock
from actiontrack.services import ingest as ing
from actiontrack.store.db import Database
from actiontrack.store.repo import Repo

from tests.integration.ingest_support import FIXED_NOW, write_note
from tests.integration.whole_db_support import copy_sample, dump, ingest, make_cfg, owners_of, q

ATT = "Priya Nair, Priya Shah, Rahul Mehta, Anita Desai, Karan Joshi"


def note(date: str, *items: str, attendees: str | None = None) -> str:
    head = f"# Meeting: Sync\n\nDate: {date}\n"
    if attendees:
        head += f"Attendees: {attendees}\n"
    return head + "\n## Action items\n\n" + "\n".join(items) + "\n"


def logical(db_path: Path) -> dict[str, list[tuple]]:
    """Business tables without run-bookkeeping columns (so a crash + re-run can be compared to a clean run)."""
    conn = sqlite3.connect(db_path)
    try:
        out: dict[str, list[tuple]] = {}
        for table in ("person", "person_alias", "meeting", "meeting_attendee", "action_item", "item_source",
                      "item_source_owner", "item_owner", "item_link"):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")') if "run" not in r[1]]
            sel = ", ".join(f'"{c}"' for c in cols)
            out[table] = [tuple(r) for r in conn.execute(f'SELECT {sel} FROM "{table}" ORDER BY {sel}')]
        out["events"] = [
            tuple(r) for r in conn.execute('SELECT item_id, kind, "before", "after" FROM item_event ORDER BY 1, 2, 3, 4')
        ]
        return out
    finally:
        conn.close()


@pytest.fixture
def small(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Priya to send the quote by 2026-09-05",
                                               "- [ ] Anita to send the deck by 2026-09-05"))
    write_note(folder, "2026-09-02-b.md", note("2026-09-02", "- [ ] Karan to fix login by 2026-09-09",
                                               "- [ ] Rahul Mehta to book room by 2026-09-09", attendees=ATT))
    return tmp_path / "t.db", folder, make_cfg(tmp_path)


def test_two_runs_back_to_back_second_changes_nothing(small):
    db, folder, cfg = small
    first = ingest(db, folder, cfg)
    assert first.run_row.status == "complete"
    assert first.finalisation_error is None
    snap = dump(db)
    second = ingest(db, folder, cfg)
    assert second.counters.files_unchanged == 2 and second.counters.files_ingested == 0
    assert second.reresolution.is_empty and second.conflicts == ()
    assert dump(db) == snap  # business tables byte-identical (AC-08.1)


@pytest.mark.ac("AC-08.5")
@pytest.mark.sc("SC-28")
def test_crash_after_file_k_then_rerun_equals_uninterrupted_run(tmp_path):
    folder = copy_sample(tmp_path)
    cfg = make_cfg(tmp_path)
    clean = tmp_path / "clean.db"
    ingest(clean, folder, cfg)

    class Crash(BaseException):
        pass

    def hook(name: str) -> None:
        if name == "after_file:12":
            raise Crash

    crashed = tmp_path / "crashed.db"
    with pytest.raises(Crash):
        ingest(crashed, folder, cfg, fault_hook=hook)
    assert q(crashed, "SELECT status FROM ingest_run")[0]["status"] == "in_progress"  # never finalised
    ingest(crashed, folder, cfg)
    assert logical(crashed) == logical(clean)


@pytest.mark.ac("AC-05.17")
@pytest.mark.sc("SC-65")
def test_roster_alias_resolves_owner_on_a_folder_where_every_file_is_unchanged(small):
    db, folder, cfg = small
    ingest(db, folder, cfg)
    assert {o["owner_status"] for o in owners_of(db, 1)} == {"ambiguous"}
    pinned = make_cfg(small[0].parent, aliases={"Priya Shah": ("Priya",)}, name="pinned.toml")
    res = ingest(db, folder, pinned)
    assert res.counters.files_unchanged == 2 and res.counters.files_ingested == 0  # report still says unchanged
    (o,) = owners_of(db, 1)
    assert (o["owner_status"], o["person_id"], o["provenance"]) == ("resolved", "priya-shah", "alias")
    evs = q(db, "SELECT * FROM item_event WHERE item_id = 1 AND kind = 'owner_changed' AND run_id = ?", (res.run.run_id,))
    assert len(evs) == 1
    assert [c.kind for c in res.reresolution.changes if c.item_id == 1] == ["resolved_from_ambiguous"]


@pytest.mark.ac("AC-05.8")
def test_attendee_disambiguation_off_downgrades_attendance_rows_of_an_existing_db(small):
    db, folder, cfg = small
    ingest(db, folder, cfg)
    ok = q(db, "SELECT item_id FROM item_owner WHERE provenance = 'attendee_in_meeting'")
    assert len(ok) == 0  # 'Rahul Mehta' is a full name: no attendance needed; make a namesake case below
    folder2 = small[1]
    write_note(folder2, "2026-09-03-c.md", note("2026-09-03", "- [ ] Priya to review specs by 2026-09-10",
                                                attendees="Priya Nair, Anita Desai"))
    res = ingest(db, folder2, cfg)
    assert res.counters.resolved_via_attendance == 1
    assert res.run_row.resolved_via_attendance == 1
    off = make_cfg(small[0].parent, attendance=False, name="off.toml")
    res2 = ingest(db, folder2, off)
    assert res2.counters.files_unchanged == 3
    assert [c.kind for c in res2.reresolution.changes] == ["downgraded_to_ambiguous"]
    assert res2.counters.resolved_via_attendance == 0
    (w,) = [w for w in q(db, "SELECT * FROM ingest_warning WHERE run_id = ?", (res2.run.run_id,))
            if w["code"] == codes.W_OWNER_NOW_AMBIGUOUS]
    assert "2026-09-03-c.md" in w["message"]
    back = ingest(db, folder2, cfg)  # switching back restores them
    assert back.counters.resolved_via_attendance == 1


def test_downgrade_end_to_end_keeps_merged_sources_and_notes_it(tmp_path):
    folder = tmp_path / "notes"
    item = "- [ ] Sam to freeze invoice edits by 2026-10-30"
    write_note(folder, "2026-10-14-a.md", note("2026-10-14", item))
    write_note(folder, "2026-10-15-b.md", note("2026-10-15", item))
    db = tmp_path / "t.db"
    ingest(db, folder, make_cfg(tmp_path))
    res = ingest(db, folder, make_cfg(tmp_path, add=(("Sam", "Lee", ()),), name="two.toml"))
    assert res.counters.files_unchanged == 2
    assert q(db, "SELECT count(*) FROM item_source")[0][0] == 2
    (c,) = res.reresolution.changes
    assert c.merged_or_completed_under_earlier_resolution
    (w,) = [w for w in res.reresolution.warnings if w[0] == codes.W_OWNER_NOW_AMBIGUOUS]
    assert "merged/completed under an earlier owner resolution" in w[2]


@pytest.mark.ac("AC-03.6")
def test_status_conflict_is_emitted_on_an_all_unchanged_run(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Anita to send the deck by 2026-09-05"))
    db = tmp_path / "t.db"
    cfg = make_cfg(tmp_path)
    ingest(db, folder, cfg)
    d = Database.open(db, clock=FixedClock(FIXED_NOW))
    try:
        with d.transaction():
            Repo(d).set_manual_done(1)
    finally:
        d.close()
    res = ingest(db, folder, cfg)
    assert res.counters.files_unchanged == 1
    assert res.conflicts == (1,)
    warns = [w for w in q(db, "SELECT * FROM ingest_warning WHERE run_id = ?", (res.run.run_id,))
             if w["code"] == codes.W_STATUS_CONFLICT]
    assert len(warns) == 1
    assert q(db, "SELECT status, completion_origin FROM action_item")[0]["status"] == "done"


def test_partial_when_a_file_failed_complete_otherwise_and_counters_match_the_row(small):
    db, folder, cfg = small
    (folder / "bad.md").write_bytes(b"\x00\x01\x02\xff" * 40)
    res = ingest(db, folder, cfg)
    assert res.counters.files_failed == 1 and res.run_row.status == "partial"
    assert res.run_row.finished_at is not None
    for name, value in res.counters.as_columns().items():
        assert getattr(res.run_row, name) == value
    (folder / "bad.md").unlink()
    ok = ingest(db, folder, cfg)
    assert ok.run_row.status == "complete" and ok.exit_code == 0


def test_failure_in_finalisation_rolls_back_only_the_finalisation(small):
    db, folder, cfg = small
    ingest(db, folder, cfg)
    pinned = make_cfg(small[0].parent, aliases={"Priya Shah": ("Priya",)}, name="pinned.toml")

    def hook(name: str) -> None:
        if name == "finalise_written":
            raise RuntimeError("boom")

    before = dump(db)
    res = ingest(db, folder, pinned, fault_hook=hook)
    assert res.finalisation_error and "boom" in res.finalisation_error
    assert res.run_row.status == "partial"
    assert {o["owner_status"] for o in owners_of(db, 1)} == {"ambiguous"}  # re-resolution rolled back
    assert dump(db)["item_owner"] == before["item_owner"]
    codes_in_run = [w["code"] for w in q(db, "SELECT code FROM ingest_warning WHERE run_id = ?", (res.run.run_id,))]
    assert codes.E_INTERNAL_PARSE in codes_in_run
    # the next run repairs it
    fixed = ingest(db, folder, pinned)
    assert fixed.finalisation_error is None
    assert {o["owner_status"] for o in owners_of(db, 1)} == {"resolved"}


def test_source_state_is_recomputed_for_the_whole_database_in_finalisation(small):
    db, folder, cfg = small
    ingest(db, folder, cfg)
    (folder / "2026-09-02-b.md").unlink()
    res = ingest(db, folder, cfg)
    assert {r["source_state"] for r in q(db, "SELECT source_state FROM action_item WHERE id IN (3, 4)")} == {"missing"}
    assert res.run_row.status == "complete"
    assert json.dumps(sorted(r["status"] for r in q(db, "SELECT status FROM action_item"))) == '["open", "open", "open", "open"]'
