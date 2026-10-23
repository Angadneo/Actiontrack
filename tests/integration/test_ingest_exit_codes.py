"""T-M5-18: the real ``ingest`` command through the CLI: exit codes, environment errors, streams, report output.

Spec 4.1 (exit codes 0/4/5, streams, --quiet/--verbose), 4.2, 11, AC-01.7, AC-01.8, AC-01.12, AC-01.13, AC-09.4,
AC-09.9, SC-09, SC-40, SC-41, SC-69. In-process through ``cli.main`` (capsys) and, for the sample, in a real
``python -m actiontrack`` subprocess.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from actiontrack import cli
from actiontrack.report.ingest_report import mask_volatile_json, mask_volatile_table
from actiontrack.services import ingest as ing
from actiontrack.store.lock import WriteLock

from tests.conftest import REPO_ROOT, snapshot_tree
from tests.integration.ingest_support import ROSTER_TOML, write_note
from tests.integration.whole_db_support import copy_sample

TODAY = "2026-10-22"
VALID = "# Meeting\n\nDate: 2026-09-01\nAttendees: Anita Desai, Karan Joshi\n\n- [ ] Karan Joshi to ship the {n} by 2026-09-04\n"


def run(capsys, folder, *extra: str, db: Path, quiet: bool = False, fmt: str | None = None, dry: bool = False,
        no_recursive: bool = False, verbose: bool = False):
    argv = ["--db", str(db), "--config", str(ROSTER_TOML), "--today", TODAY]
    if quiet:
        argv.append("--quiet")
    if verbose:
        argv.append("--verbose")
    if fmt:
        argv += ["--format", fmt]
    argv += ["ingest", str(folder)]
    if dry:
        argv.append("--dry-run")
    if no_recursive:
        argv.append("--no-recursive")
    argv += list(extra)
    capsys.readouterr()
    rc = cli.main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def small(tmp_path):
    folder = tmp_path / "notes"
    for n in ("a", "b", "c"):
        write_note(folder, f"2026-09-0{ord(n) - 96}-{n}.md", VALID.format(n=n))
    return folder


# ----------------------------------------------------------------------------- exit 4: environment errors
@pytest.mark.ac("AC-01.12")
@pytest.mark.sc("SC-09")
def test_nonexistent_folder_exits_4_and_writes_nothing(capsys, tmp_path):
    db = tmp_path / "sub" / "actiontrack.db"
    rc, out, err = run(capsys, tmp_path / "missing", db=db)
    assert rc == 4 and out == ""
    assert "cannot scan folder: no such file or directory" in err and "missing" in err and "Next:" in err
    assert "Traceback" not in err
    assert [p.name for p in tmp_path.iterdir()] == []  # no DB, no directory, no lock file


@pytest.mark.ac("AC-01.12")
def test_folder_that_is_a_file_exits_4(capsys, tmp_path):
    f = tmp_path / "file.md"
    f.write_text("x", encoding="utf-8")
    rc, out, err = run(capsys, f, db=tmp_path / "t.db")
    assert rc == 4 and out == "" and "not a directory" in err
    assert not (tmp_path / "t.db").exists()


@pytest.mark.ac("AC-01.12")
def test_unusable_database_exits_4_before_anything_is_reported(capsys, small, tmp_path):
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is not a sqlite database at all" * 20)
    before = sha(garbage)
    rc, out, err = run(capsys, small, db=garbage)
    assert rc == 4 and out == "" and "Traceback" not in err
    assert sha(garbage) == before
    folder_db = tmp_path / "adir"
    folder_db.mkdir()
    rc, out, err = run(capsys, small, db=folder_db)
    assert rc == 4 and out == ""


@pytest.mark.sc("SC-67")
def test_lock_held_by_another_writer_exits_4_without_a_run_row(capsys, small, tmp_path):
    db = tmp_path / "t.db"
    assert run(capsys, small, db=db)[0] == 0
    rows_before = sqlite3.connect(db).execute("SELECT count(*) FROM ingest_run").fetchone()[0]
    holder = WriteLock(db)
    holder.acquire()
    try:
        rc, out, err = run(capsys, small, db=db)
    finally:
        holder.release()
    assert rc == 4 and out == "" and "another actiontrack process is using this database" in err
    assert sqlite3.connect(db).execute("SELECT count(*) FROM ingest_run").fetchone()[0] == rows_before
    # a dry run takes no lock and works while it is held
    holder.acquire()
    try:
        assert run(capsys, small, db=db, dry=True)[0] == 0
    finally:
        holder.release()


def test_invalid_config_exits_4_and_usage_errors_exit_2(capsys, small, tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not toml", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["--config", str(bad), "--db", str(tmp_path / "t.db"), "ingest", str(small)]) == 4
    assert not (tmp_path / "t.db").exists()
    for argv in (["ingest"], ["ingest", "  "], ["--quiet", "--verbose", "ingest", str(small)], ["ingest", "a", "b"]):
        assert cli.main(argv) == 2, argv


# ----------------------------------------------------------------------------- exit 5 / 0
@pytest.mark.ac("AC-01.8")
@pytest.mark.sc("SC-09")
def test_three_valid_one_binary_one_empty_exit_5(capsys, small, tmp_path):
    (small / "scratch.md").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    (small / "empty.md").write_bytes(b"")
    rc, out, err = run(capsys, small, db=tmp_path / "t.db")
    assert rc == 5
    assert "Files: discovered 5 | ingested 3 | unchanged 0 | empty 1 | failed 1" in out
    assert "Parsing failures (1):" in out and "scratch.md  E-BINARY" in out
    assert out.splitlines()[-1] == "Exit code 5 (1 file failed)"
    assert "error: E-BINARY scratch.md:" in err and "Next:" in err  # what / where / next, on stderr
    assert "W-EMPTY-FILE" in err  # the empty file is a warning, not a failure
    # the three valid files are in the database although the run exits 5 (partial)
    conn = sqlite3.connect(tmp_path / "t.db")
    assert conn.execute("SELECT status FROM ingest_run").fetchone()[0] == "partial"
    assert conn.execute("SELECT count(*) FROM action_item").fetchone()[0] == 3


@pytest.mark.ac("AC-01.8")
def test_second_run_re_reports_failures_every_run_and_keeps_exit_5(capsys, small, tmp_path):
    (small / "scratch.md").write_bytes(b"\x00\x00binary")
    db = tmp_path / "t.db"
    assert run(capsys, small, db=db)[0] == 5
    rc, out, _ = run(capsys, small, db=db)
    assert rc == 5
    assert "Files: discovered 4 | ingested 0 | unchanged 3 | empty 0 | failed 1" in out
    assert "Items: new 0 | updated 0 | unchanged 0 | merged-duplicates 0 | possible-duplicates 0" in out


def test_clean_run_exits_0_with_the_report(capsys, small, tmp_path):
    rc, out, err = run(capsys, small, db=tmp_path / "t.db")
    assert rc == 0 and out.splitlines()[-1] == "Exit code 0"
    assert "Files: discovered 3 | ingested 3 | unchanged 0 | empty 0 | failed 0" in out
    assert err.count("Ingest run #1:") == 1  # the summary line
    assert "exit 0" in err


@pytest.mark.ac("AC-01.13")
@pytest.mark.sc("SC-41")
def test_sample_one_level_down_no_recursive_then_recursive(capsys, tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    copy_sample(parent, "notes")
    db = tmp_path / "t.db"
    rc, out, err = run(capsys, parent, db=db, no_recursive=True)
    assert rc == 0
    assert "Files: discovered 0 | ingested 0 | unchanged 0 | empty 0 | failed 0" in out
    assert "Hint: 0 .md files at the top level; 35 .md files exist in subfolders. Re-run without --no-recursive" in out
    assert "W-NO-FILES" in err and "35 .md files exist in subfolders" in err
    for target in (parent, parent / "notes"):
        rc, out, err = run(capsys, target, db=tmp_path / f"{target.name}.db")
        assert rc == 5
        assert "Files: discovered 35 | ingested 33 | unchanged 0 | empty 1 | failed 1" in out
        assert "scratch.md  E-BINARY" in out and "Hint:" not in out
        assert out.splitlines()[-1] == "Exit code 5 (1 file failed)"


@pytest.mark.ac("AC-01.7", "AC-01.13")
@pytest.mark.sc("SC-41")
def test_sample_figures_verified_against_the_database(capsys, tmp_path):
    folder = copy_sample(tmp_path)
    db = tmp_path / "t.db"
    rc, out, err = run(capsys, folder, db=db, fmt="json")
    assert rc == 5
    report = json.loads(out)
    conn = sqlite3.connect(db)
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    assert report["files"]["discovered"] == 35 and report["files"]["empty"] == 1 and report["files"]["failed"] == 1
    assert report["items"]["new"] == one("SELECT count(*) FROM action_item") == 103
    assert report["items"]["merged_duplicates"] == 14 and report["items"]["sources_imported"] == one(
        "SELECT count(*) FROM item_source"
    ) == 117
    assert report["items"]["possible_duplicates"] == one("SELECT count(*) FROM item_link WHERE kind = 'possible_duplicate'") == 9
    assert report["items"]["already_done_in_note"] == one("SELECT count(*) FROM item_source WHERE checkbox_state = 'done'") == 13
    assert report["unresolved_owners"]["ambiguous"] == 7 and report["unresolved_owners"]["unassigned"] == 5
    assert report["missing_due_dates"]["count"] == one(
        "SELECT count(*) FROM action_item WHERE status = 'open' AND due_date IS NULL"
    )
    assert report["resolved_via_attendance"]["count"] == one(
        "SELECT count(*) FROM item_owner WHERE provenance = 'attendee_in_meeting'"
    )
    assert report["exit_code"] == 5 and report["parsing_failures"]["rows"][0]["path"] == "scratch.md"


@pytest.mark.ac("AC-01.7")
@pytest.mark.sc("SC-69")
def test_sc69_style_folder_exits_0_with_warnings_only(capsys, tmp_path, monkeypatch):
    folder = tmp_path / "n"
    write_note(folder, ".draft.md", VALID.format(n="dot"))  # dot-file: skipped silently
    write_note(folder, ".git/x.md", VALID.format(n="git"))
    write_note(folder, "a.md", VALID.format(n="a") + "\n<!-- never closed\n- [ ] Karan Joshi to hide this\n")
    write_note(folder, "b.md", VALID.format(n="b") + "\n- [ ]\n")  # empty checkbox
    (folder / "locked").mkdir()
    real = os.scandir

    def scandir(path):
        if os.path.basename(os.fspath(path)) == "locked":
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    rc, out, err = run(capsys, folder, db=tmp_path / "t.db")
    assert rc == 0, err
    assert "Files: discovered 2 | ingested 2 | unchanged 0 | empty 0 | failed 0" in out
    codes_seen = {line.split()[0] for line in err.splitlines() if line.startswith("W-")}
    assert {"W-HTML-COMMENT-UNCLOSED", "W-CHECKBOX-EMPTY", "W-DIR-UNREADABLE"} <= codes_seen
    assert not any(line.startswith("error:") for line in err.splitlines())
    assert out.splitlines()[-1] == "Exit code 0"


def test_exit_code_table_for_codes_0_4_5(capsys, small, tmp_path):
    """(situation, exit code) equals spec 4.1 for codes 0, 4 and 5."""
    bin_dir = tmp_path / "with_binary"
    write_note(bin_dir, "a.md", VALID.format(n="a"))
    (bin_dir / "x.md").write_bytes(b"\x00")
    cases = [
        ("clean folder", run(capsys, small, db=tmp_path / "1.db")[0], 0),
        ("empty folder (W-NO-FILES only)", run(capsys, tmp_path / "emptydir_probe", db=tmp_path / "2.db")[0], 4),
        ("one binary file", run(capsys, bin_dir, db=tmp_path / "3.db")[0], 5),
        ("missing folder", run(capsys, tmp_path / "nope", db=tmp_path / "4.db")[0], 4),
    ]
    (tmp_path / "emptydir").mkdir()
    cases.append(("folder without .md files", run(capsys, tmp_path / "emptydir", db=tmp_path / "5.db")[0], 0))
    for situation, got, want in cases:
        assert got == want, situation


# ----------------------------------------------------------------------------- streams / options
@pytest.mark.ac("AC-09.9")
def test_quiet_hides_warnings_but_prints_summary_errors_and_the_report(capsys, small, tmp_path):
    (small / "scratch.md").write_bytes(b"\x00bin")
    (small / "empty.md").write_bytes(b"")
    rc, out, err = run(capsys, small, db=tmp_path / "t.db", quiet=True)
    assert rc == 5
    assert "Parsing failures (1):" in out and "Files: discovered 5" in out  # result data never suppressed
    assert "W-EMPTY-FILE" not in err  # warnings hidden
    assert "error: E-BINARY" in err  # errors never hidden
    assert err.strip().splitlines()[-1].startswith("Ingest run #1: 5 files")  # the summary line is printed
    assert "see --verbose" in out  # the report itself still counts them


def test_default_shows_warnings_on_stderr_and_verbose_expands_them_in_the_report(capsys, small, tmp_path):
    (small / "empty.md").write_bytes(b"")
    rc, out, err = run(capsys, small, db=tmp_path / "t.db")
    assert rc == 0 and "W-EMPTY-FILE" in err and "Warnings (3): see --verbose" in out
    assert "W-EMPTY-FILE" not in out
    rc, out, err = run(capsys, small, db=tmp_path / "t.db", verbose=True)
    assert "Warnings (1):\n  W-EMPTY-FILE  empty.md: file is empty" in out


@pytest.mark.ac("AC-01.7")
def test_json_format_is_valid_json_on_stdout_only(capsys, small, tmp_path):
    (small / "scratch.md").write_bytes(b"\x00bin")
    rc, out, err = run(capsys, small, db=tmp_path / "t.db", fmt="json")
    assert rc == 5
    report = json.loads(out)
    assert report["exit_code"] == 5 and report["files"]["failed"] == 1 and report["run"]["dry_run"] is False
    assert "Ingest run" not in out and "error: E-BINARY" in err
    before = run(capsys, small, db=tmp_path / "t.db", fmt="json", quiet=True)[1]
    assert json.loads(before)["files"]["unchanged"] == 3


@pytest.mark.ac("AC-09.4")
def test_report_is_byte_identical_across_runs_after_masking(capsys, small, tmp_path):
    a = run(capsys, small, db=tmp_path / "a.db")[1]
    b = run(capsys, small, db=tmp_path / "b" / "b.db")[1]
    assert mask_volatile_table(a) == mask_volatile_table(b)
    ja = run(capsys, small, db=tmp_path / "c.db", fmt="json")[1]
    jb = run(capsys, small, db=tmp_path / "d.db", fmt="json")[1]
    assert mask_volatile_json(ja) == mask_volatile_json(jb)


@pytest.mark.ac("AC-01.10")
@pytest.mark.sc("SC-40")
def test_dry_run_writes_nothing_and_reports_the_same_counts(capsys, tmp_path):
    folder = copy_sample(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    db = out_dir / "t.db"
    sample_before = snapshot_tree(folder)
    rc, out, err = run(capsys, folder, db=db, dry=True)
    assert rc == 5  # exit code as a real run would give
    assert list(out_dir.iterdir()) == [] and not (out_dir / ".actiontrack").exists()
    assert "Dry run: nothing was written; #1 is the number a real run would get" in out
    assert "Files: discovered 35 | ingested 33 | unchanged 0 | empty 1 | failed 1" in out
    assert out.splitlines()[-1] == "Exit code 5 (1 file failed)" and "Dry run: Ingest run #1" in err
    assert snapshot_tree(folder) == sample_before
    real_rc, real_out, _ = run(capsys, folder, db=db)
    assert real_rc == 5
    body = lambda text: [x for x in text.splitlines() if not x.startswith(("Dry run", "Ingest run"))]  # noqa: E731
    assert body(real_out) == body(out)  # dry run and real run report the same facts


# ----------------------------------------------------------------------------- finalisation failure
@pytest.mark.ac("AC-08.7")
def test_finalisation_failure_exits_4_with_the_report_still_printed(capsys, small, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ing, "reresolve_database", boom)
    rc, out, err = run(capsys, small, db=tmp_path / "t.db")
    assert rc == 4
    assert "Run finalisation failed:" in out and out.splitlines()[-1].startswith("Exit code 4 (run finalisation failed")
    assert "error: " in err and "boom" in err
    conn = sqlite3.connect(tmp_path / "t.db")
    assert conn.execute("SELECT count(*) FROM action_item").fetchone()[0] == 3  # files already ingested are kept
    assert conn.execute("SELECT status FROM ingest_run").fetchone()[0] == "partial"


# ----------------------------------------------------------------------------- a real process
@pytest.mark.ac("AC-01.13", "AC-01.12")
@pytest.mark.sc("SC-41")
def test_python_dash_m_sample_run_and_missing_folder(tmp_path):
    folder = copy_sample(tmp_path)
    db = tmp_path / "t.db"
    env = {
        "PYTHONPATH": str(REPO_ROOT / "src"),
        **{k: os.environ[k] for k in ("SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE") if k in os.environ},
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    }

    def at(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "actiontrack", "--db", str(db), "--config", str(ROSTER_TOML), "--today", TODAY, *args],
            capture_output=True, env=env, cwd=tmp_path, timeout=120,
        )

    r = at("ingest", str(folder))
    out, err = r.stdout.decode("utf-8"), r.stderr.decode("utf-8")
    assert r.returncode == 5
    assert "Files: discovered 35 | ingested 33 | unchanged 0 | empty 1 | failed 1" in out
    assert "Unresolved owners (12):" in out and out.splitlines()[-1] == "Exit code 5 (1 file failed)"
    assert "error: E-BINARY scratch.md" in err and "Traceback" not in err
    second = at("ingest", str(folder))
    assert second.returncode == 5
    assert "ingested 0 | unchanged 33 | empty 1 | failed 1" in second.stdout.decode("utf-8")
    quiet = at("--quiet", "ingest", str(folder), "--format", "json")
    assert json.loads(quiet.stdout.decode("utf-8"))["exit_code"] == 5
    assert "W-" not in quiet.stderr.decode("utf-8")
    missing = at("ingest", str(tmp_path / "does-not-exist"))
    assert missing.returncode == 4 and missing.stdout == b""
