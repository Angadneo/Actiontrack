"""T-M1-17: CLI-level --quiet / --log-file / --format contract (AC-09.9, AC-09.11)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from actiontrack import cli, codes, io
from actiontrack.errors import EnvError
from tests.conftest import REPO_ROOT

SUMMARY = "Files: discovered 1 | ingested 1 | unchanged 0 | empty 0 | failed 0"


def run(capsys, *argv: str):
    rc = cli.main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


@pytest.fixture
def chatty(monkeypatch):
    """Test handler on `list` that emits every kind of output."""
    calls = []

    def handler(ctx):
        calls.append(ctx)
        ctx.diag.warn(codes.W_EMPTY_FILE, "WARN-TEXT", file="a.md", line=2)
        ctx.diag.info("INFO-TEXT")
        ctx.diag.debug("DEBUG-TEXT")
        io.emit("RESULT-TEXT")
        ctx.diag.summary(SUMMARY)
        raise EnvError("ERROR-TEXT")

    monkeypatch.setitem(cli.HANDLERS, "list", handler)
    return calls


@pytest.mark.ac("AC-09.9")
def test_quiet_cli_level_keeps_summary_and_errors(capsys, tmp_workdir, chatty):
    cfg = tmp_workdir / "c.toml"
    cfg.write_text('timezone = "Asia/Kolkata"\n', encoding="utf-8")
    base = ["--config", str(cfg)]

    rc, normal_out, normal_err = run(capsys, *base, "list")
    assert rc == 4
    for t in ("WARN-TEXT", "INFO-TEXT", SUMMARY, "ERROR-TEXT"):
        assert t in normal_err
    assert "DEBUG-TEXT" not in normal_err

    for argv in ([*base, "--quiet", "list"], [*base, "list", "--quiet"]):
        rc, out, err = run(capsys, *argv)
        assert rc == 4
        assert "WARN-TEXT" not in err and "INFO-TEXT" not in err and "DEBUG-TEXT" not in err
        assert SUMMARY in err and "ERROR-TEXT" in err
        assert out == normal_out == "RESULT-TEXT\n"  # --quiet never changes result data


@pytest.mark.ac("AC-09.9")
def test_verbose_shows_debug(capsys, tmp_workdir, chatty):
    rc, out, err = run(capsys, "--verbose", "list")
    assert "DEBUG-TEXT" in err and out == "RESULT-TEXT\n"


@pytest.mark.ac("AC-09.9")
def test_quiet_does_not_hide_result_data_of_a_clean_run(monkeypatch, capsys, tmp_workdir):
    monkeypatch.setitem(cli.HANDLERS, "list", lambda ctx: io.emit("ONLY-RESULT"))
    rc, out, err = run(capsys, "--quiet", "list")
    assert (rc, out, err) == (0, "ONLY-RESULT\n", "")


@pytest.mark.ac("AC-09.9")
def test_log_file_appends_same_lines_as_stderr(capsys, tmp_workdir, chatty):
    log = tmp_workdir / "run.log"
    log.write_bytes(b"previous run\n")
    rc, out, err = run(capsys, "list", "--log-file", str(log))
    text = log.read_bytes().decode("utf-8")
    assert text.startswith("previous run\n")
    assert "\r" not in text
    for t in ("WARN-TEXT", "INFO-TEXT", SUMMARY, "ERROR-TEXT"):
        assert t in text
    assert "RESULT-TEXT" not in text  # result data is stdout only
    # a second run appends again
    run(capsys, "--log-file", str(log), "list")
    assert log.read_text(encoding="utf-8").count("ERROR-TEXT") == 2


@pytest.mark.ac("AC-09.9")
def test_log_file_unopenable_exit4_before_work(capsys, tmp_workdir, chatty):
    for bad in (tmp_workdir, tmp_workdir / "missing-dir" / "x.log"):
        rc, out, err = run(capsys, "--log-file", str(bad), "list")
        assert rc == 4 and out == ""
        assert "log file" in err
    assert chatty == []  # handler never invoked


@pytest.mark.ac("AC-09.9")
def test_log_file_checked_before_config_is_read(capsys, tmp_workdir, chatty):
    bad_cfg = tmp_workdir / "bad.toml"
    bad_cfg.write_text("nope = [", encoding="utf-8")
    rc, _, err = run(capsys, "--config", str(bad_cfg), "--log-file", str(tmp_workdir), "list")
    assert rc == 4 and "log file" in err and "toml" not in err.lower()


@pytest.mark.ac("AC-09.9")
def test_quiet_verbose_together_nothing_executed(capsys, tmp_workdir, chatty):
    log = tmp_workdir / "never.log"
    rc, out, err = run(capsys, "--quiet", "--verbose", "--log-file", str(log), "list")
    assert rc == 2 and out == ""
    assert "--quiet" in err and "--verbose" in err
    assert chatty == [] and not log.exists()


def test_log_file_via_subprocess_is_utf8(tmp_workdir):
    log = tmp_workdir / "u.log"
    env = {
        "PYTHONPATH": str(REPO_ROOT / "src"),
        **{k: os.environ[k] for k in ("SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE") if k in os.environ},
        "PYTHONIOENCODING": "cp1252",
    }
    r = subprocess.run(
        [sys.executable, "-m", "actiontrack", "list", "--log-file", str(log)],
        capture_output=True, env=env, cwd=tmp_workdir, timeout=60,
    )
    assert r.returncode == 3  # M6: list is real; there is no database in the empty work dir
    assert "database not found" in log.read_text(encoding="utf-8")


@pytest.mark.ac("AC-09.11")
def test_format_before_and_after_identical_via_subprocess(tmp_workdir):
    env = {"PYTHONPATH": str(REPO_ROOT / "src"), **{k: os.environ[k] for k in ("SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE") if k in os.environ}}

    def go(*args: str):
        r = subprocess.run(
            [sys.executable, "-m", "actiontrack", *args],
            capture_output=True, env=env, cwd=tmp_workdir, timeout=60,
        )
        return r.returncode, r.stdout, r.stderr

    for tail in (["gmail-login"], ["list"], ["ingest", "notes"], ["done", "1"], ["followup", "A B"]):
        before = go("--format", "json", *tail)
        after = go(*tail, "--format", "json")
        assert before == after, tail
    assert go("--format", "json", "gmail-login", "--format", "table") == go("gmail-login")
    assert list(Path(tmp_workdir).iterdir()) == []
