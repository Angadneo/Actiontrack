"""T-M5-04: ingest run lifecycle, process lock use, interrupted-run detection (in-process half).

The real two-process half (AC-08.9 / SC-67) is ``test_ingest_lock.py``.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3

import pytest

from actiontrack.clock import FixedClock
from actiontrack.errors import EnvError, ExitCode
from actiontrack.services import ingest as ing
from actiontrack.store.db import DbLocked, Database
from actiontrack.store.lock import WriteLock, lock_path_for
from actiontrack.store.repo import Repo

from tests.integration.ingest_support import FIXED_NOW


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    return root


def test_begin_run_creates_committed_in_progress_run(tmp_db_path, folder):
    with ing.ingest_session(tmp_db_path, folder, clock=FixedClock(FIXED_NOW)) as s:
        assert not s.db.conn.in_transaction  # the run row is committed on its own
        row = s.repo.get_ingest_run(s.run.run_id)
        assert row.status == "in_progress"
        assert row.dry_run is False
        assert row.folder_root == os.path.realpath(folder)  # canonical absolute path
        assert row.started_at == "2026-10-22T09:30:00Z"  # injected clock
        assert row.finished_at is None
        assert s.run.interrupted == ()
        assert s.db.write_lock is not None and s.db.write_lock.held
    # the lock is gone after the context exits
    WriteLock(tmp_db_path).acquire().release()


def test_finish_run_status_complete_and_partial(tmp_db_path, folder):
    with ing.ingest_session(tmp_db_path, folder, clock=FixedClock(FIXED_NOW)) as s:
        counters = ing.RunCounters(files_discovered=3, files_ingested=2, files_unchanged=1)
        row = ing.finish_run(s.db, s.repo, s.run.run_id, counters)
        assert row.status == "complete"
        assert row.finished_at == "2026-10-22T09:30:00Z"
        assert (row.files_discovered, row.files_ingested, row.files_unchanged) == (3, 2, 1)
    with ing.ingest_session(tmp_db_path, folder, clock=FixedClock(FIXED_NOW)) as s:
        counters = ing.RunCounters(files_discovered=2, files_ingested=1, files_failed=1)
        row = ing.finish_run(s.db, s.repo, s.run.run_id, counters)
        assert row.status == "partial"
        assert row.files_failed == 1


def test_run_counters_invariant():
    ok = ing.RunCounters(files_discovered=5, files_ingested=1, files_unchanged=2, files_empty=1, files_failed=1)
    assert ok.invariant_holds()
    bad = ing.RunCounters(files_discovered=5, files_ingested=1)
    assert not bad.invariant_holds()


@pytest.mark.ac("AC-08.5", "AC-08.9")
def test_stale_in_progress_run_is_marked_interrupted_and_reported(tmp_db_path, folder):
    with Database.open(tmp_db_path, clock=FixedClock(FIXED_NOW)) as db:
        repo = Repo(db)
        with db.transaction():
            stale = repo.insert_ingest_run("/somewhere", started_at="2026-10-20T08:00:00Z")
    with ing.ingest_session(tmp_db_path, folder, clock=FixedClock(FIXED_NOW)) as s:
        assert [r.run_id for r in s.run.interrupted] == [stale]
        notice = s.run.interrupted[0]
        assert f"#{stale}" in notice.message and "interrupted" in notice.message
        assert notice.files_committed == 0 and notice.last_file is None
        assert s.repo.get_ingest_run(stale).status == "interrupted"
        assert s.repo.get_ingest_run(s.run.run_id).status == "in_progress"
        assert s.run.in_progress_elsewhere == ()


def test_notice_reports_files_the_dead_run_committed(tmp_db_path, folder):
    with Database.open(tmp_db_path, clock=FixedClock(FIXED_NOW)) as db:
        repo = Repo(db)
        with db.transaction():
            run = repo.insert_ingest_run("/x", started_at="2026-10-20T08:00:00Z")
            repo.insert_source_file("/x", "a.md", status="ok", content_sha256="aa", run_id=run, ingested=True)
            repo.insert_source_file("/x", "b.md", status="ok", content_sha256="bb", run_id=run, ingested=True)
    with ing.ingest_session(tmp_db_path, folder, clock=FixedClock(FIXED_NOW)) as s:
        (notice,) = s.run.interrupted
        assert notice.files_committed == 2
        assert notice.last_file == "b.md"
        assert "2 committed file" in notice.message and "b.md" in notice.message


@pytest.mark.ac("AC-08.9")
def test_live_in_progress_run_is_never_marked_interrupted(tmp_db_path, folder):
    """A writer without the lock (e.g. a concurrent process) must not mark a live run."""
    with Database.open(tmp_db_path, clock=FixedClock(FIXED_NOW)) as db:
        repo = Repo(db)
        with db.transaction():
            live = repo.insert_ingest_run("/x", started_at="2026-10-20T08:00:00Z")
    holder = WriteLock(tmp_db_path).acquire()  # "another live process"
    try:
        db = Database.open(tmp_db_path, clock=FixedClock(FIXED_NOW))  # no own lock
        repo = Repo(db)
        handle = ing.begin_run(db, repo, str(folder))
        assert handle.interrupted == ()
        assert handle.in_progress_elsewhere == (live,)
        assert repo.get_ingest_run(live).status == "in_progress"
        db.close()
    finally:
        holder.release()


def test_second_ingest_session_exit4_without_touching_db(tmp_db_path, folder):
    with ing.ingest_session(tmp_db_path, folder, clock=FixedClock(FIXED_NOW)):
        before = _sha(tmp_db_path)
        with pytest.raises(EnvError) as info:
            with ing.ingest_session(tmp_db_path, folder, clock=FixedClock(FIXED_NOW)):
                pytest.fail("second writer must not get in")
        assert info.value.exit_code == ExitCode.ENVIRONMENT
        assert "another actiontrack process is using this database" in info.value.what
        assert _sha(tmp_db_path) == before


def test_lock_released_on_exceptional_exit_and_run_left_in_progress(tmp_db_path, folder):
    with pytest.raises(RuntimeError):
        with ing.ingest_session(tmp_db_path, folder, clock=FixedClock(FIXED_NOW)) as s:
            run_id = s.run.run_id
            raise RuntimeError("boom")
    WriteLock(tmp_db_path).acquire().release()  # lock free again
    assert lock_path_for(tmp_db_path).exists()  # a stale lock file is harmless and kept
    # the abandoned run is cleaned up by the NEXT run, which reports it
    with ing.ingest_session(tmp_db_path, folder, clock=FixedClock(FIXED_NOW)) as s2:
        assert [r.run_id for r in s2.run.interrupted] == [run_id]


def test_dry_run_takes_no_lock_creates_no_file_and_never_marks(tmp_db_path, tmp_path, folder):
    # no database yet: nothing is created at all
    with ing.ingest_session(tmp_db_path, folder, dry_run=True, clock=FixedClock(FIXED_NOW)) as s:
        assert s.db.in_memory and s.db.write_lock is None
        assert s.repo.get_ingest_run(s.run.run_id).dry_run is True
    assert not tmp_db_path.exists()
    assert not lock_path_for(tmp_db_path).exists()
    # existing db with a (possibly live) in-progress run: dry-run neither marks nor persists anything
    with Database.open(tmp_db_path, clock=FixedClock(FIXED_NOW)) as db:
        repo = Repo(db)
        with db.transaction():
            live = repo.insert_ingest_run("/x", started_at="2026-10-20T08:00:00Z")
    before = _sha(tmp_db_path)
    holder = WriteLock(tmp_db_path).acquire()
    try:
        with ing.ingest_session(tmp_db_path, folder, dry_run=True, clock=FixedClock(FIXED_NOW)) as s:
            assert s.run.interrupted == ()
            assert s.run.in_progress_elsewhere == (live,)
    finally:
        holder.release()
    assert _sha(tmp_db_path) == before
    conn = sqlite3.connect(tmp_db_path)
    try:
        assert conn.execute("SELECT status FROM ingest_run WHERE id = ?", (live,)).fetchone()[0] == "in_progress"
    finally:
        conn.close()


def test_sqlite_busy_maps_to_typed_dblocked(tmp_db_path):
    """SQLite 'database is locked' after the busy timeout is the typed ``DbLocked`` (exit 4)."""
    db1 = Database.open(tmp_db_path)
    db2 = Database.open(tmp_db_path)
    db1.conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(DbLocked) as info:
            with db2.transaction():
                pytest.fail("must not get the write lock")
        assert info.value.exit_code == ExitCode.ENVIRONMENT
        assert isinstance(info.value, EnvError)
    finally:
        db1.conn.execute("ROLLBACK")
        db1.close()
        db2.close()
