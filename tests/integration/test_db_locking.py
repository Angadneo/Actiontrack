"""Process-level writer lock <db>.lock with REAL separate processes (T-M1-11; AC-08.9, SC-67)."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from actiontrack.errors import EnvError, ExitCode
from actiontrack.store.db import Database
from actiontrack.store.lock import LOCK_MESSAGE, WriteLock, can_mark_interrupted, lock_path_for

from tests.conftest import REPO_ROOT

HOLDER = """
import sys
from actiontrack.store.lock import WriteLock
lock = WriteLock(sys.argv[1]).acquire()
print("READY", flush=True)
sys.stdin.readline()
"""

CONTENDER = """
import sys
from actiontrack.errors import ActionTrackError
from actiontrack.store.db import Database
try:
    Database.open(sys.argv[1], lock=True).close()
except ActionTrackError as exc:
    print(int(exc.exit_code), exc.what, flush=True)
    sys.exit(int(exc.exit_code))
print("OPENED", flush=True)
"""


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return env


def start_holder(db_path: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(db_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=_env(),
    )
    assert proc.stdout.readline().strip() == "READY"
    return proc


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)
    for stream in (proc.stdin, proc.stdout):
        if stream:
            stream.close()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def existing_db(tmp_db_path):
    Database.open(tmp_db_path).close()
    return tmp_db_path


@pytest.mark.ac("AC-08.9")
@pytest.mark.sc("SC-67")
def test_second_writer_exit4_while_ingest_holds_lock(existing_db):
    before = sha(existing_db)
    holder = start_holder(existing_db)
    try:
        # in-process second writer (an `ingest` or `done`-style write open)
        with pytest.raises(EnvError) as info:
            Database.open(existing_db, lock=True)
        assert info.value.exit_code == ExitCode.ENVIRONMENT == 4
        assert LOCK_MESSAGE in info.value.what
        assert "another actiontrack process is using this database" in info.value.what
        # real second process
        out = subprocess.run(
            [sys.executable, "-c", CONTENDER, str(existing_db)],
            capture_output=True, text=True, env=_env(), timeout=60,
        )
        assert out.returncode == 4, out.stdout + out.stderr
        assert "another actiontrack process" in out.stdout
        assert sha(existing_db) == before
        assert holder.poll() is None  # first unaffected
    finally:
        stop(holder)


@pytest.mark.ac("AC-08.9")
@pytest.mark.sc("SC-67")
def test_lock_released_after_crash(existing_db):
    holder = start_holder(existing_db)
    try:
        assert can_mark_interrupted(existing_db) is False  # live owner: never mark interrupted
        holder.kill()
        holder.wait(timeout=10)
        deadline = time.time() + 10
        while True:
            try:
                lock = WriteLock(existing_db).acquire()
                break
            except EnvError:
                assert time.time() < deadline, "lock not released after the holder died"
                time.sleep(0.1)
        lock.release()
        assert lock_path_for(existing_db).exists()  # stale lock file is left alone, harmless
        assert can_mark_interrupted(existing_db) is True
        with Database.open(existing_db, lock=True) as db:
            assert db.can_mark_interrupted() is True  # own lock counts
    finally:
        stop(holder)


def test_can_mark_interrupted_true_when_this_process_holds_lock(existing_db):
    with Database.open(existing_db, lock=True) as db:
        assert db.write_lock.held
        assert can_mark_interrupted(existing_db) is False  # a second handle is a "second process"
        assert can_mark_interrupted(existing_db, own_lock=db.write_lock) is True
        assert db.can_mark_interrupted() is True
    assert not db.write_lock.held


def test_dry_run_and_readonly_take_no_lock(existing_db):
    lock_file = lock_path_for(existing_db)
    assert not lock_file.exists()
    # no lock file exists and none is created by non-writers
    Database.open(existing_db, readonly=True).close()
    Database.open_dry_run(existing_db).close()
    assert not lock_file.exists()
    # while another process holds the lock they still work and do not touch the lock file
    holder = start_holder(existing_db)
    try:
        stat = lock_file.stat()
        ro = Database.open(existing_db, readonly=True)
        assert ro.conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 0
        ro.close()
        dry = Database.open_dry_run(existing_db)
        dry.close()
        after = lock_file.stat()
        assert (after.st_size, after.st_mtime_ns) == (stat.st_size, stat.st_mtime_ns)
    finally:
        stop(holder)


def test_lock_released_on_close_and_context_exit(existing_db):
    db = Database.open(existing_db, lock=True)
    db.close()
    WriteLock(existing_db).acquire().release()
    with WriteLock(existing_db) as lock:
        assert lock.held
    assert not lock.held


def test_failed_open_releases_lock(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(os.urandom(4096))
    with pytest.raises(EnvError):
        Database.open(bad, lock=True)
    WriteLock(bad).acquire().release()  # lock was released after the failed open
