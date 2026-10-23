"""T-M5-17: ``--dry-run`` = the whole pipeline against an in-memory copy; no writes of any kind, no lock."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from actiontrack.clock import FixedClock
from actiontrack.store.db import Database

from tests.integration.ingest_support import FIXED_NOW, write_note
from tests.integration.whole_db_support import copy_sample, dump, ingest, make_cfg, q


def snapshot_tree(root: Path) -> dict[str, str | None]:
    """Every file and directory under ``root`` (hidden ones included) with a content hash for files."""
    out: dict[str, str | None] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            out[str((Path(dirpath) / d).relative_to(root))] = None
        for f in filenames:
            p = Path(dirpath) / f
            try:
                out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
            except PermissionError:  # a byte-range locked lock file: its existence is what matters
                out[str(p.relative_to(root))] = "locked"
    return out


def note(date: str, *items: str) -> str:
    return f"# Meeting: Sync\n\nDate: {date}\n\n## Action items\n\n" + "\n".join(items) + "\n"


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Priya to send the quote by 2026-09-05",
                                               "- [ ] Anita to send the deck by 2026-09-05"))
    write_note(folder, "2026-09-02-b.md", note("2026-09-02", "- [ ] Karan to fix login by 2026-09-09"))
    dbdir = tmp_path / "data"
    dbdir.mkdir()
    return dbdir / "t.db", folder, make_cfg(tmp_path), tmp_path


@pytest.mark.ac("AC-01.10")
@pytest.mark.sc("SC-40")
def test_dry_run_on_an_absent_database_writes_nothing(work):
    db, folder, cfg, tmp = work
    before = snapshot_tree(tmp)
    res = ingest(db, folder, cfg, dry_run=True)
    assert not db.exists() and not Path(f"{db}.lock").exists()
    assert snapshot_tree(tmp) == before
    assert res.dry_run and res.counters.files_ingested == 2 and res.counters.items_new == 3
    assert res.run_row.status == "complete" and res.run.run_id == 1


@pytest.mark.ac("AC-01.10")
@pytest.mark.sc("SC-40")
def test_dry_run_on_an_existing_database_is_byte_identical_and_lists_the_same_files(work):
    db, folder, cfg, tmp = work
    ingest(db, folder, cfg)
    write_note(folder, "2026-09-03-c.md", note("2026-09-03", "- [ ] Meera to plan offsite by 2026-09-12"))
    before = snapshot_tree(tmp)
    res = ingest(db, folder, cfg, dry_run=True)
    assert snapshot_tree(tmp) == before  # DB file, hidden files, lock, WAL side files: all equal
    assert res.counters.files_ingested == 1 and res.counters.files_unchanged == 2
    assert q(db, "SELECT count(*) FROM ingest_run")[0][0] == 1  # no run row in the real database
    assert q(db, "SELECT count(*) FROM action_item")[0][0] == 3


def test_dry_run_leaves_a_stale_in_progress_run_in_progress(work):
    db, folder, cfg, tmp = work
    ingest(db, folder, cfg)
    d = Database.open(db, clock=FixedClock(FIXED_NOW))
    try:
        with d.transaction():
            d.conn.execute("INSERT INTO ingest_run (started_at, folder_root, status, dry_run) VALUES "
                           "('2026-10-01T00:00:00Z', 'x', 'in_progress', 0)")
    finally:
        d.close()
    before = snapshot_tree(tmp)
    res = ingest(db, folder, cfg, dry_run=True)
    assert snapshot_tree(tmp) == before
    assert [r["status"] for r in q(db, "SELECT status FROM ingest_run ORDER BY id")] == ["complete", "in_progress"]
    assert res.run.interrupted == ()  # shown as "would", never marked in the real file


def test_dry_run_works_while_another_writer_holds_the_lock_and_creates_no_lock_file(work):
    db, folder, cfg, tmp = work
    ingest(db, folder, cfg)
    holder = Database.open(db, lock=True, clock=FixedClock(FIXED_NOW))
    try:
        before = snapshot_tree(tmp)
        res = ingest(db, folder, cfg, dry_run=True)  # no EnvError / exit 4
        assert res.counters.files_unchanged == 2
        assert snapshot_tree(tmp) == before
    finally:
        holder.close()
    # while the lock holder is gone, the lock file is still just what the real run left (not created by the dry run)
    lock = Path(f"{db}.lock")
    existed = lock.exists()
    ingest(db, folder, cfg, dry_run=True)
    assert lock.exists() == existed


@pytest.mark.ac("AC-01.10", "AC-05.17")
def test_dry_run_after_a_roster_alias_change_reports_the_re_resolution_it_would_do(work):
    db, folder, cfg, tmp = work
    ingest(db, folder, cfg)
    pinned = make_cfg(tmp, aliases={"Priya Shah": ("Priya",)}, name="pinned.toml")
    before = snapshot_tree(tmp)
    res = ingest(db, folder, pinned, dry_run=True)
    assert snapshot_tree(tmp) == before
    assert [(c.item_id, c.kind) for c in res.reresolution.changes] == [(1, "resolved_from_ambiguous")]
    assert q(db, "SELECT owner_status FROM item_owner WHERE item_id = 1")[0][0] == "ambiguous"
    assert q(db, "SELECT count(*) FROM item_event WHERE kind = 'owner_changed'")[0][0] == 0
    real = ingest(db, folder, pinned)
    assert [(c.item_id, c.kind) for c in real.reresolution.changes] == [(1, "resolved_from_ambiguous")]


def test_dry_run_reports_what_the_real_run_then_does(work):
    db, folder, cfg, tmp = work
    ingest(db, folder, cfg)
    write_note(folder, "2026-09-03-c.md", note("2026-09-03", "- [ ] Meera to plan offsite by 2026-09-12"))
    (folder / "2026-09-02-b.md").unlink()
    dry = ingest(db, folder, cfg, dry_run=True)
    real = ingest(db, folder, cfg)
    assert dry.counters == real.counters
    assert [m.rel_path for m in dry.missing] == [m.rel_path for m in real.missing] == ["2026-09-02-b.md"]
    assert dry.reresolution == real.reresolution
    assert dry.run_row.status == real.run_row.status
    assert dry.run.run_id == real.run.run_id  # the copy's "next id" (G-C13 residual)


def test_dry_run_then_real_run_gives_the_same_state_as_a_real_run_alone(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    cfg = make_cfg(tmp_path)
    folder_a, folder_b = copy_sample(a), copy_sample(b)
    ingest(a / "t.db", folder_a, cfg, dry_run=True)
    ra = ingest(a / "t.db", folder_a, cfg)
    rb = ingest(b / "t.db", folder_b, cfg)
    assert ra.counters == rb.counters
    assert dump(a / "t.db")["action_item"] == dump(b / "t.db")["action_item"]
    assert q(a / "t.db", "SELECT count(*) FROM ingest_run")[0][0] == 1


@pytest.mark.ac("AC-01.10")
@pytest.mark.sc("SC-40")
def test_sample_folder_dry_run_exits_5_and_writes_nothing(tmp_path):
    folder = copy_sample(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    db = data / "t.db"
    cfg = make_cfg(tmp_path)
    before = snapshot_tree(tmp_path)
    res = ingest(db, folder, cfg, dry_run=True)
    assert res.exit_code == 5
    assert res.counters.files_failed == 1 and res.counters.files_empty == 1
    assert snapshot_tree(tmp_path) == before
    assert not db.exists() and not (data / ".actiontrack").exists()
