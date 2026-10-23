"""T-M5-04 / AC-08.9 / SC-67: the ingest process lock with REAL separate processes.

Process A runs a real ``run_ingest`` and blocks (in a fault hook) after file 2; B / C are a second ``ingest``
and a ``done``-style writer (the shared ``Database.open(lock=True)`` helper); D is the next ``ingest`` after A
was killed. The in-process half is ``test_run_lifecycle.py``; the raw lock module is ``test_db_locking.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from actiontrack.services import ingest as ing

from tests.conftest import REPO_ROOT
from tests.integration.ingest_support import roster_cfg, run_ctx, simple_note, write_note

SCRIPT = r'''
import json, sys, time
from datetime import UTC, date, datetime, timezone
from pathlib import Path

from actiontrack.clock import FixedClock, ResolvedZone, RunContext
from actiontrack.config import load_config
from actiontrack.errors import ActionTrackError
from actiontrack.services import ingest as ing
from actiontrack.store.db import Database

mode, db_path, folder, roster, ready, go = sys.argv[1:7]
ctx = RunContext(FixedClock(datetime(2026, 10, 22, 9, 30, tzinfo=UTC)), date(2026, 10, 22), "UTC",
                 ResolvedZone("UTC", timezone.utc, False))

def hook(name):
    if name == "after_file:2":
        Path(ready).write_text("ready")
        if mode == "hold":
            while not Path(go).exists():
                time.sleep(0.05)

try:
    if mode == "done":  # what `done` does: the same helper, a short write open
        Database.open(db_path, lock=True).close()
        print(json.dumps({"opened": True}), flush=True)
        sys.exit(0)
    res = ing.run_ingest(db_path, folder, cfg=load_config(roster), run_ctx=ctx,
                         fault_hook=hook if mode == "hold" else None)
except ActionTrackError as exc:
    print(json.dumps({"exit": int(exc.exit_code), "what": exc.what}), flush=True)
    sys.exit(int(exc.exit_code))
print(json.dumps({"exit": res.exit_code, "status": res.run_row.status,
                  "interrupted": [r.run_id for r in res.run.interrupted],
                  "notice": [r.message for r in res.run.interrupted],
                  "ingested": res.counters.files_ingested, "unchanged": res.counters.files_unchanged}), flush=True)
'''


def _env() -> dict[str, str]:
    env = dict(os.environ)  # keeps APPDATA / LOCALAPPDATA / USERPROFILE / SYSTEMROOT for the interpreter
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return env


class Procs:
    def __init__(self, tmp_path: Path, db: Path, folder: Path):
        self.dir, self.db, self.folder = tmp_path, db, folder
        self.script = tmp_path / "ingest_proc.py"
        self.script.write_text(SCRIPT, encoding="utf-8", newline="\n")
        self.ready = tmp_path / "ready.flag"
        self.go = tmp_path / "go.flag"
        self.live: list[subprocess.Popen] = []

    def argv(self, mode: str) -> list[str]:
        roster = str(REPO_ROOT / "tests" / "fixtures" / "config" / "valid_full.toml")
        return [sys.executable, str(self.script), mode, str(self.db), str(self.folder), roster, str(self.ready), str(self.go)]

    def start_holder(self) -> subprocess.Popen:
        proc = subprocess.Popen(
            self.argv("hold"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env()
        )
        self.live.append(proc)
        deadline = time.time() + 60
        while not self.ready.exists():
            assert proc.poll() is None, proc.stderr.read()
            assert time.time() < deadline, "holder never reached file 2"
            time.sleep(0.05)
        return proc

    def run(self, mode: str, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            self.argv(mode), capture_output=True, text=True, env=_env(), timeout=timeout
        )

    def cleanup(self) -> None:
        for proc in self.live:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()


@pytest.fixture
def procs(tmp_path, tmp_db_path):
    folder = tmp_path / "notes"
    for i in (1, 2, 3):
        write_note(folder, f"2026-09-0{i}-n.md", simple_note(f"2026-09-0{i}", f"N{i}"))
    p = Procs(tmp_path, tmp_db_path, folder)
    yield p
    p.cleanup()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runs(db: Path) -> list[tuple[int, str]]:
    conn = sqlite3.connect(db)
    try:
        return [(r[0], r[1]) for r in conn.execute("SELECT id, status FROM ingest_run ORDER BY id")]
    finally:
        conn.close()


@pytest.mark.ac("AC-08.9")
@pytest.mark.sc("SC-67")
def test_second_ingest_and_done_exit4_without_touching_the_database(procs):
    holder = procs.start_holder()
    before = _sha(procs.db)
    b = procs.run("ingest")
    assert b.returncode == 4, b.stdout + b.stderr
    assert "another actiontrack process is using this database" in json.loads(b.stdout.splitlines()[-1])["what"]
    c = procs.run("done")  # `done` goes through the same Database.open(lock=True) helper
    assert c.returncode == 4, c.stdout + c.stderr
    assert _sha(procs.db) == before  # not one byte written by the second writers
    assert holder.poll() is None
    # a live in_progress run is never marked interrupted by a concurrent process
    assert _runs(procs.db) == [(1, "in_progress")]
    # let the first ingest finish normally: it completes untouched by B and C
    procs.go.write_text("go")
    out, err = holder.communicate(timeout=120)
    assert holder.returncode == 0, err
    result = json.loads(out.splitlines()[-1])
    assert result["status"] == "complete" and result["ingested"] == 3
    assert _runs(procs.db) == [(1, "complete")]


@pytest.mark.ac("AC-08.9", "AC-08.5")
@pytest.mark.sc("SC-67", "SC-28")
def test_killed_ingest_is_marked_interrupted_by_the_next_run_and_completes(procs):
    holder = procs.start_holder()
    holder.kill()  # the OS releases the lock; a stale <db>.lock stays behind and is harmless
    holder.wait(timeout=10)
    assert _runs(procs.db) == [(1, "in_progress")]
    deadline = time.time() + 20
    while True:  # the OS may need a moment to release the byte-range lock of the dead process
        d = procs.run("ingest")
        if d.returncode != 4 or time.time() > deadline:
            break
        time.sleep(0.2)
    assert d.returncode == 0, d.stdout + d.stderr
    result = json.loads(d.stdout.splitlines()[-1])
    assert result["interrupted"] == [1]  # the abandoned run is reported ...
    assert "previous run #1" in result["notice"][0] and "2 committed file" in result["notice"][0]
    assert result["status"] == "complete"
    assert (result["unchanged"], result["ingested"]) == (2, 1)  # A's committed files are unchanged
    assert _runs(procs.db) == [(1, "interrupted"), (2, "complete")]  # ... and marked


@pytest.mark.ac("AC-08.9")
def test_dry_run_works_while_another_process_holds_the_lock(procs):
    holder = procs.start_holder()
    before = _sha(procs.db)
    res = ing.run_ingest(procs.db, procs.folder, cfg=roster_cfg(), run_ctx=run_ctx(), dry_run=True)
    assert res.counters.files_unchanged == 2 and res.counters.files_ingested == 1  # computed on the copy
    assert res.run.interrupted == () and res.run.in_progress_elsewhere == (1,)  # live run: not touched
    assert _sha(procs.db) == before
    assert _runs(procs.db) == [(1, "in_progress")]
    assert holder.poll() is None
