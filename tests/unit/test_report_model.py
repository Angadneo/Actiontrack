"""T-M5-15: the ingest report model and its counting rules (spec 4.2 "Report definitions", G-25, FR-01.7).

The model is built from a real (small or sample) database by ``services.ingest.build_ingest_report``; these tests
assert the definitions: run-scoped Files/Items counts partition exactly, the sections are STATE-scoped over OPEN
items (all folder roots), headings equal state counts, ordering is deterministic and the H-05 note rides on the
downgrade warning, the unresolved-owner row and the re-resolution row.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from actiontrack import codes
from actiontrack.domain.models import IngestReport
from actiontrack.resolution.owners import group_ambiguous
from actiontrack.services import ingest as ing
from actiontrack.store.db import Database
from actiontrack.store.repo import Repo

from tests.integration.ingest_support import roster_cfg, run_ctx, write_note
from tests.integration.whole_db_support import copy_sample, make_cfg


def ingest(db_path: Path, folder: Path, cfg=None, **kw) -> ing.IngestResult:
    return ing.run_ingest(db_path, folder, cfg=cfg or roster_cfg(), run_ctx=run_ctx(), **kw)


def q(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("report_model")
    folder = copy_sample(tmp)
    db = tmp / "t.db"
    first = ingest(db, folder)
    second = ingest(db, folder)
    return SimpleNamespace(tmp=tmp, folder=folder, db=db, first=first, second=second)


# ----------------------------------------------------------------------------- run-scoped counts
@pytest.mark.ac("AC-01.7", "AC-01.13")
@pytest.mark.sc("SC-41")
def test_files_line_partitions_and_matches_the_sample(sample):
    r = sample.first.report
    f = r.files
    assert (f.discovered, f.ingested, f.unchanged, f.empty, f.failed) == (35, 33, 0, 1, 1)
    assert f.discovered == f.ingested + f.unchanged + f.empty + f.failed
    assert (f.missing, f.symlinks_skipped) == (0, 0)
    assert (r.status, r.exit_code, r.exit_reason) == ("partial", 5, "1 file failed")


@pytest.mark.ac("AC-01.7")
def test_items_line_partitions_every_source_exactly_once(sample):
    it = sample.first.report.items
    assert (it.new, it.merged_duplicates, it.updated, it.unchanged) == (105, 12, 0, 0)
    assert it.sources_imported == 117 == it.new + it.merged_duplicates + it.updated + it.unchanged
    assert it.possible_duplicates == 11
    assert it.already_done_in_note == 13  # orthogonal to the four classes
    # counts equal the database deltas
    assert q(sample.db, "SELECT count(*) FROM item_source")[0][0] == 117
    assert q(sample.db, "SELECT count(*) FROM action_item")[0][0] == it.new
    assert q(sample.db, "SELECT count(*) FROM item_link WHERE kind = 'possible_duplicate'")[0][0] == 11
    assert q(sample.db, "SELECT count(*) FROM item_source WHERE checkbox_state = 'done'")[0][0] == 13


@pytest.mark.ac("AC-01.7")
def test_second_run_run_scoped_counts_are_zero_but_empty_and_failed_are_counted_every_run(sample):
    r = sample.second.report
    f, it = r.files, r.items
    assert (f.discovered, f.ingested, f.unchanged, f.empty, f.failed) == (35, 0, 33, 1, 1)
    assert (it.new, it.updated, it.unchanged, it.merged_duplicates, it.possible_duplicates) == (0, 0, 0, 0, 0)
    assert it.already_done_in_note == 0
    assert [(x.path, x.code) for x in r.failures] == [("scratch.md", "E-BINARY")]
    assert r.exit_code == 5


# ----------------------------------------------------------------------------- state-scoped sections
@pytest.mark.ac("AC-01.7", "AC-05.14")
def test_unresolved_owner_section_lists_the_seven_ambiguous_and_five_unassigned(sample):
    rows = sample.first.report.unresolved_owners
    assert len(rows) == 12
    amb = [o for o in rows if o.kind == "ambiguous"]
    assert len(amb) == 7
    assert sorted({o.candidates for o in amb}) == [
        ("Priya Nair", "Priya Shah"),
        ("Rahul Mehta", "Rahul Verma"),
    ]
    assert all(o.location and ":" in o.location for o in rows)
    assert [o.kind for o in rows].count("unassigned") == 5
    assert not any(o.kind == "unverified" for o in rows)
    assert [o.item_id for o in rows] == sorted(o.item_id for o in rows)  # deterministic order
    # the unassigned rows carry the raw source line, untruncated
    assert any(o.raw == "- [ ] Someone to update the CRM notes" for o in rows)


@pytest.mark.ac("AC-05.14")
def test_ambiguous_rows_equal_the_open_group_ambiguous_count(sample):
    conn = sqlite3.connect(sample.db)
    conn.row_factory = sqlite3.Row
    db = Database.open(sample.db, readonly=True)
    try:
        repo = Repo(db)
        registry = repo.load_registry()
        items = [
            SimpleNamespace(
                item_id=i.id,
                status=i.status,
                owners=[
                    SimpleNamespace(status=o.owner_status, candidates=o.candidates, owner_raw=o.owner_raw)
                    for o in repo.list_item_owners(i.id)
                ],
            )
            for i in repo.list_action_items()
        ]
    finally:
        db.close()
        conn.close()
    groups = group_ambiguous(items, registry, "open")
    assert sum(len(g.items) for g in groups) == 7
    assert sum(1 for o in sample.first.report.unresolved_owners if o.kind == "ambiguous") == 7


@pytest.mark.ac("AC-01.7")
def test_due_section_is_open_items_without_a_date_with_reasons(sample):
    rows = sample.first.report.missing_due_dates
    expected = q(sample.db, "SELECT count(*) FROM action_item WHERE status = 'open' AND due_date IS NULL")[0][0]
    assert len(rows) == expected == 28
    unresolved = [d for d in rows if d.kind == "unresolved"]
    assert sorted((d.due_raw, d.reason) for d in unresolved) == [
        ("Friday", "no_meeting_date"),
        ("Friday", "no_meeting_date"),
        ("November", "vague"),
        ("mid-October", "vague"),
    ]
    assert all(d.kind == "no_date" and d.due_raw is None for d in rows if d not in unresolved)
    assert [d.item_id for d in rows] == sorted(d.item_id for d in rows)


@pytest.mark.ac("AC-01.7")
def test_duplicate_section_lists_pairs_with_an_open_item_later_id_first(sample):
    pairs = sample.first.report.possible_duplicate_pairs
    status = {r[0]: r[1] for r in q(sample.db, "SELECT id, status FROM action_item")}
    links = q(sample.db, "SELECT * FROM item_link WHERE kind = 'possible_duplicate'")
    expected = {(r["to_item_id"], r["from_item_id"]) for r in links if "open" in (status[r["from_item_id"]], status[r["to_item_id"]])}
    assert {(p.item_id, p.other_item_id) for p in pairs} == expected and len(expected) == 6
    assert all(p.item_id > p.other_item_id and p.reason == "due_differs" for p in pairs)
    assert all(p.reason_text.startswith("same task+owner, due differs (") for p in pairs)
    assert [(p.other_item_id, p.item_id) for p in pairs] == sorted((p.other_item_id, p.item_id) for p in pairs)


@pytest.mark.ac("AC-01.7")
def test_resolved_via_attendance_is_a_state_count(sample):
    assert sample.first.report.resolved_via_attendance == 33 == sample.second.report.resolved_via_attendance


@pytest.mark.ac("AC-01.7")
def test_second_run_sections_still_list_the_same_open_rows(sample):
    a, b = sample.first.report, sample.second.report
    assert b.unresolved_owners == a.unresolved_owners
    assert b.missing_due_dates == a.missing_due_dates
    assert b.possible_duplicate_pairs == a.possible_duplicate_pairs


def test_failures_and_warnings_are_run_scoped_and_ordered(sample):
    r = sample.first.report
    [fail] = r.failures
    assert (fail.path, fail.code) == ("scratch.md", "E-BINARY")
    assert fail.message.startswith("contains binary data") and fail.next_step.startswith("remove it")
    assert r.warning_count == 12 == len(r.warnings)
    assert all(w.code.startswith("W-") for w in r.warnings)  # the E- row is the failure, not a warning
    keys = [(w.path or "", w.line or 0, w.code, w.message) for w in r.warnings]
    assert keys == sorted(keys)
    assert r.finalisation_error is None and r.notices == () and r.hint is None


# ----------------------------------------------------------------------------- open-only / all roots
@pytest.mark.ac("AC-01.7", "AC-05.14")
def test_done_ambiguous_item_is_not_in_the_sections_and_second_root_is(tmp_path):
    db = tmp_path / "t.db"
    a = tmp_path / "root_a"
    write_note(a, "2026-09-01-a.md", "# A\n\nDate: 2026-09-01\n\n- [ ] Priya to ring the vendor by 2026-09-04\n"
                                     "- [ ] Rahul to check the logs\n")
    first = ingest(db, a)
    assert [o.kind for o in first.report.unresolved_owners] == ["ambiguous", "ambiguous"]
    # complete the Priya item by hand: done items are not chased
    conn = Database.open(db, lock=True, clock=run_ctx().clock)
    try:
        Repo(conn).set_manual_done(1)
    finally:
        conn.close()
    b = tmp_path / "root_b"
    write_note(b, "2026-09-02-b.md", "# B\n\nDate: 2026-09-02\n\n- [ ] Priya to draft the memo\n- [ ] Karan Joshi to ship it\n")
    second = ingest(db, b)  # another folder root, same database
    rows = second.report.unresolved_owners
    assert [(o.item_id, o.kind, o.location) for o in rows] == [
        (2, "ambiguous", "2026-09-01-a.md:6"),  # root_a's Rahul: all folder roots, open only
        (3, "ambiguous", "2026-09-02-b.md:5"),
    ]
    # the done item is absent; counts are state counts of OPEN items equal to what list would show
    assert q(db, "SELECT count(*) FROM action_item WHERE status = 'open'")[0][0] == 3
    assert second.report.items.new == 2  # run-scoped: only root_b's two items


@pytest.mark.ac("AC-01.7")
def test_provisional_owner_is_listed_as_unverified(tmp_path):
    db = tmp_path / "t.db"
    folder = tmp_path / "n"
    write_note(folder, "2026-09-01-a.md", "# A\n\nDate: 2026-09-01\nAttendees: Anita Desai\n\n- [ ] Zed Quux to file the report\n")
    rows = ingest(db, folder).report.unresolved_owners
    # a never-seen full name is a provisional person: resolved to a person but unverified
    assert [(o.kind, o.owner_raw) for o in rows] == [("unverified", "Zed Quux")]


# ----------------------------------------------------------------------------- H-05
@pytest.mark.ac("AC-05.19")
@pytest.mark.sc("SC-72")
def test_h05_note_rides_on_the_downgrade_warning_and_rows(tmp_path):
    folder = copy_sample(tmp_path)
    db = tmp_path / "t.db"
    ingest(db, folder, make_cfg(tmp_path))
    res = ingest(db, folder, make_cfg(tmp_path, attendance=False, name="off.toml"))
    r: IngestReport = res.report
    flagged = {c.item_id for c in res.reresolution.changes if c.merged_or_completed_under_earlier_resolution}
    assert len(flagged) == 6
    noted = [w for w in r.warnings if w.notes]
    assert len(noted) == 6 and all(w.code == codes.W_OWNER_NOW_AMBIGUOUS for w in noted)
    assert all(ing.H05_NOTE in w.message and w.notes == (ing.H05_NOTE,) for w in noted)
    # the re-resolution section carries it too, and the items keep their status (done retained, H-05)
    assert {x.item_id for x in r.reresolution if x.notes} == flagged
    for item_id in flagged:
        assert q(db, "SELECT status FROM action_item WHERE id = ?", (item_id,))[0][0] == "done"
    # a flagged item that is still open also shows the note in its unresolved-owner row
    for row in r.unresolved_owners:
        assert bool(row.notes) == (row.item_id in flagged)
    # 33 more ambiguous owners than before (AC-05.18 downgrades), in the state-scoped section
    assert len([o for o in r.unresolved_owners if o.kind == "ambiguous"]) > 7


# ----------------------------------------------------------------------------- exit status / notices
@pytest.mark.ac("AC-01.8")
def test_exit_status_table():
    c = ing.RunCounters()
    assert ing.ingest_exit_status(c, None) == (0, None)
    c.files_failed = 1
    assert ing.ingest_exit_status(c, None) == (5, "1 file failed")
    c.files_failed = 3
    assert ing.ingest_exit_status(c, None) == (5, "3 files failed")
    assert ing.ingest_exit_status(c, "boom")[0] == 4  # finalisation failure takes precedence (environment class)


@pytest.mark.ac("AC-01.13")
@pytest.mark.sc("SC-41")
def test_no_recursive_hint_counts_subfolder_files(tmp_path):
    parent = tmp_path / "parent"
    inner = copy_sample(parent.parent, "parent_src")  # 36 files in a folder
    parent.mkdir()
    nested = parent / "notes"
    inner.rename(nested)
    res = ingest(tmp_path / "t.db", parent, recursive=False)
    r = res.report
    assert r.files.discovered == 0 and r.exit_code == 0 and res.subfolder_md_count == 35
    assert r.hint == "0 .md files at the top level; 35 .md files exist in subfolders. Re-run without --no-recursive"
    assert any(w.code == codes.W_NO_FILES for w in r.warnings)
    full = ingest(tmp_path / "t.db", parent)
    assert full.report.hint is None and full.report.files.discovered == 35


def test_dry_run_notice_and_interrupted_notice(tmp_path):
    folder = tmp_path / "n"
    write_note(folder, "2026-09-01-a.md", "# A\n\nDate: 2026-09-01\n\n- [ ] Karan Joshi to ship it\n")
    dry = ingest(tmp_path / "t.db", folder, dry_run=True)
    assert dry.report.dry_run and dry.report.notices[0].startswith("Dry run: nothing was written; #1 ")
    # a stale in_progress run is reported by the next real run
    db = Database.open(tmp_path / "t.db", clock=run_ctx().clock)
    try:
        Repo(db).insert_ingest_run(str(folder), started_at="2026-10-01T00:00:00Z")
    finally:
        db.close()
    real = ingest(tmp_path / "t.db", folder)
    assert any("was interrupted" in n for n in real.report.notices)
