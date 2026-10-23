"""T-M1-14: exit codes and stream separation through a real process (``python -m actiontrack``).

Runs with a cp1252 console encoding (PYTHONIOENCODING) and Unicode arguments to prove the
shell forces UTF-8 on its streams and never crashes on characters the console cannot encode.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from actiontrack import __version__
from tests.conftest import REPO_ROOT

UNICODE = "Zoë 日本語 ✓"


def at(*args: str, cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {
        "PYTHONPATH": str(REPO_ROOT / "src"),
        **{k: os.environ[k] for k in ("SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE") if k in os.environ},
        "PYTHONIOENCODING": "cp1252",
        "TEMP": str(cwd),
        "TMP": str(cwd),
    }
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "actiontrack", *args],
        capture_output=True,
        env=env,
        cwd=cwd,
        timeout=60,
    )


def text(data: bytes) -> str:
    return data.decode("utf-8")  # strict: proves the streams really are UTF-8


def test_exit_0_version_and_help_create_nothing(tmp_workdir):
    r = at("--version", cwd=tmp_workdir)
    assert r.returncode == 0 and text(r.stdout) == f"actiontrack {__version__}\n" and r.stderr == b""
    for argv in (["--help"], ["ingest", "--help"], ["followup", "--help"], ["list", "--help"]):
        r = at(*argv, cwd=tmp_workdir)
        assert r.returncode == 0 and r.stderr == b"" and b"Synopsis:" in r.stdout
        assert b"\r\n" not in r.stdout
    assert list(tmp_workdir.iterdir()) == []


def test_exit_0_gmail_login_handoff_noop(tmp_workdir):
    r = at("gmail-login", cwd=tmp_workdir)
    assert r.returncode == 0
    assert "not needed for the handoff channel" in text(r.stdout)
    assert list(tmp_workdir.iterdir()) == []


# M5 (T-M5-18): ``ingest`` is a real command now; only list / done / followup are still dispatch stubs.
@pytest.mark.parametrize("command", ["list", "done", "followup"])
def test_exit_1_stub_commands(tmp_workdir, command):
    args = {"list": ["list"], "done": ["done", "1"], "followup": ["followup", "A B"]}
    r = at(*args[command], cwd=tmp_workdir)
    assert r.returncode == 1
    assert r.stdout == b""
    err = text(r.stderr)
    assert f"{command}: not implemented in this build" in err and "Traceback" not in err
    assert not (tmp_workdir / ".actiontrack").exists()


def test_exit_2_usage_unicode_argument_cp1252(tmp_workdir):
    r = at("--today", UNICODE, "list", cwd=tmp_workdir)
    assert r.returncode == 2
    assert r.stdout == b""
    err = text(r.stderr)
    assert UNICODE in err and "Traceback" not in err


def test_exit_2_unknown_option_unicode(tmp_workdir):
    r = at("list", f"--{UNICODE}", cwd=tmp_workdir)
    assert r.returncode == 2 and r.stdout == b""
    err = text(r.stderr)
    assert "error:" in err and "--help" in err and "Traceback" not in err


def test_exit_2_missing_arguments(tmp_workdir):
    for argv in ([], ["ingest"], ["done"], ["followup"], ["--quiet", "--verbose", "list"]):
        r = at(*argv, cwd=tmp_workdir)
        assert r.returncode == 2 and r.stdout == b"", argv
        assert b"Traceback" not in r.stderr
    assert list(tmp_workdir.iterdir()) == []


def test_exit_2_invalid_today_env_before_any_db_access(tmp_workdir):
    r = at("list", cwd=tmp_workdir, env_extra={"ACTIONTRACK_TODAY": "2026-02-30"})
    assert r.returncode == 2 and "ACTIONTRACK_TODAY" in text(r.stderr)
    assert list(tmp_workdir.iterdir()) == []


def test_exit_4_invalid_config_before_any_write(tmp_workdir):
    cfg = tmp_workdir / "bad.toml"
    cfg.write_text('timezone = "Asia/Kolkata"\nbogus_key = 1\n', encoding="utf-8")
    db = tmp_workdir / "x.db"
    r = at("--config", str(cfg), "--db", str(db), "list", cwd=tmp_workdir)
    assert r.returncode == 4 and r.stdout == b""
    err = text(r.stderr)
    assert "_key" in err and "Traceback" not in err
    assert not db.exists() and not (tmp_workdir / ".actiontrack").exists()


def test_exit_4_unopenable_log_file(tmp_workdir):
    r = at("--log-file", str(tmp_workdir), "list", cwd=tmp_workdir)
    assert r.returncode == 4 and r.stdout == b""
    assert "log file" in text(r.stderr)


def test_help_and_version_ignore_broken_environment(tmp_workdir):
    bad = tmp_workdir / "bad.toml"
    bad.write_text("not = [toml", encoding="utf-8")
    env = {
        "ACTIONTRACK_CONFIG": str(bad),
        "ACTIONTRACK_TODAY": "nonsense",
        "ACTIONTRACK_NOW": "nonsense",
        "ACTIONTRACK_DB": str(tmp_workdir / "nope" / "x.db"),
    }
    for argv in (["--version"], ["--help"], ["list", "--help"], ["gmail-login", "--help"]):
        r = at(*argv, cwd=tmp_workdir, env_extra=env)
        assert r.returncode == 0 and r.stderr == b"", argv


def test_stdout_vs_stderr_split_in_a_real_process(tmp_workdir):
    """A command that fails writes nothing to stdout; help writes nothing to stderr."""
    failing = at("done", cwd=tmp_workdir)
    assert failing.stdout == b"" and failing.stderr
    ok = at("done", "--help", cwd=tmp_workdir)
    assert ok.stderr == b"" and ok.stdout


def test_gmail_login_unicode_config_path_and_utf8_output(tmp_workdir):
    sub = tmp_workdir / "dossier-é"
    sub.mkdir()
    cfg = sub / "c.toml"
    cfg.write_text('timezone = "Asia/Kolkata"\n', encoding="utf-8")
    r = at("gmail-login", "--config", str(cfg), cwd=tmp_workdir)
    assert r.returncode == 0 and r.stderr == b""
    missing = at("gmail-login", "--config", str(sub / "ü.toml"), cwd=tmp_workdir)
    assert missing.returncode == 4 and "ü.toml" in text(missing.stderr)


def test_only_cli_and_main_module_call_sys_exit():
    src = REPO_ROOT / "src" / "actiontrack"
    users = {p.name for p in src.rglob("*.py") if "sys.exit(" in p.read_text(encoding="utf-8")}
    assert users == {"__main__.py"}
