"""Diagnostics: levels, --quiet/--verbose, --log-file (T-M1-09)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from actiontrack import codes, io
from actiontrack.errors import EnvError, ExitCode, UsageError
from actiontrack.logging_setup import Diagnostics, redact

WARN = codes.W_EMPTY_FILE
SUMMARY = "Files: discovered 1 | ingested 0 | unchanged 0 | empty 1 | failed 0"


def _emit_all(d: Diagnostics) -> None:
    d.error("boom")
    d.warn(WARN, "empty", file="a.md", line=3)
    d.info("hello info")
    d.debug("deep detail")
    d.summary(SUMMARY)


@pytest.mark.ac("AC-09.9")
def test_quiet_suppresses_info_and_warn_keeps_error(capsys):
    _emit_all(Diagnostics(quiet=True))
    err = capsys.readouterr().err
    assert "boom" in err
    assert "hello info" not in err and WARN not in err and "deep detail" not in err


@pytest.mark.ac("AC-09.9")
def test_quiet_keeps_summary_line_and_errors(capsys):
    _emit_all(Diagnostics(quiet=True))
    err = capsys.readouterr().err
    assert SUMMARY in err and "error: boom" in err


@pytest.mark.ac("AC-09.9")
def test_log_level_never_touches_stdout_result_data(capsys):
    seen = []
    for kw in ({}, {"quiet": True}, {"verbose": True}):
        d = Diagnostics(**kw)
        io.emit("AT-0001  open  Zoë")
        _emit_all(d)
        seen.append(capsys.readouterr().out)
    assert seen[0] == seen[1] == seen[2] == "AT-0001  open  Zoë\n"


@pytest.mark.ac("AC-09.9")
def test_default_shows_warn_info_not_debug(capsys):
    _emit_all(Diagnostics())
    err = capsys.readouterr().err
    assert f"{WARN} a.md:3 empty" in err and "hello info" in err and "deep detail" not in err


def test_verbose_shows_debug(capsys):
    _emit_all(Diagnostics(verbose=True))
    err = capsys.readouterr().err
    assert "debug: deep detail" in err and "hello info" in err


@pytest.mark.ac("AC-09.9")
def test_quiet_and_verbose_conflict_exit2():
    with pytest.raises(UsageError) as ei:
        Diagnostics(quiet=True, verbose=True)
    assert ei.value.exit_code == ExitCode.USAGE == 2


def test_warning_requires_registered_code():
    d = Diagnostics()
    with pytest.raises(AssertionError):
        d.warn("W-TYPO-NOPE", "x")
    with pytest.raises(AssertionError):
        d.warn(codes.E_BINARY, "errors are not warnings")  # E-* is not a warning code
    # non-strict mode emits instead of failing
    Diagnostics(strict_codes=False).warn("W-TYPO-NOPE", "x")


def test_warn_format_variants(capsys):
    d = Diagnostics()
    d.warn(WARN, "m")
    d.warn(WARN, "m", file="f.md")
    d.warn(WARN, "m", file="f.md", line=7)
    assert capsys.readouterr().err.splitlines() == [f"{WARN} m", f"{WARN} f.md m", f"{WARN} f.md:7 m"]
    assert d.warning_count == 3


def test_warning_count_counts_hidden_warnings(capsys):
    d = Diagnostics(quiet=True)
    d.warn(WARN, "m")
    assert d.warning_count == 1 and capsys.readouterr().err == ""


def test_traceback_only_in_verbose(capsys):
    def boom():
        raise RuntimeError("kaput")

    try:
        boom()
    except RuntimeError as exc:
        Diagnostics().exception(exc)
        plain = capsys.readouterr().err
        Diagnostics(verbose=True).exception(exc)
        verbose = capsys.readouterr().err
    assert "Traceback" not in plain and "re-run with --verbose for details" in plain
    assert "kaput" in plain and len(plain.strip().splitlines()) == 2
    assert "Traceback (most recent call last)" in verbose and "in boom" in verbose


def test_actiontrack_error_reported_without_traceback(capsys):
    Diagnostics().exception(EnvError("folder missing", where="C:/x", next_step="check the path"))
    err = capsys.readouterr().err
    assert "folder missing (C:/x)" in err and "Next: check the path" in err and "Traceback" not in err


def test_log_file_written_utf8_and_sanitised(tmp_path, capsys):
    log = tmp_path / "run.log"
    with Diagnostics(log_file=log) as d:
        d.warn(WARN, "Zoë 李雷 \x1b[31mred\x9b")
        d.summary(SUMMARY)
    raw = log.read_bytes()
    assert b"\r" not in raw and b"\x1b" not in raw
    text = raw.decode("utf-8")
    assert "Zoë 李雷 [31mred" in text and SUMMARY in text
    assert text.endswith("\n")


def test_log_file_same_verbosity_as_stderr(tmp_path):
    log = tmp_path / "q.log"
    with Diagnostics(quiet=True, log_file=log) as d:
        _emit_all(d)
    text = log.read_text(encoding="utf-8")
    assert "boom" in text and SUMMARY in text and "hello info" not in text and WARN not in text


def test_log_file_appends_not_truncates(tmp_path):
    log = tmp_path / "a.log"
    for word in ("first", "second"):
        with Diagnostics(log_file=log) as d:
            d.info(word)
    assert log.read_text(encoding="utf-8").splitlines() == ["first", "second"]


@pytest.mark.ac("AC-09.9")
def test_log_file_unwritable_exit4(tmp_path):
    ro = tmp_path / "ro.log"
    ro.write_text("x", encoding="utf-8")
    os.chmod(ro, stat.S_IREAD)
    try:
        for bad in (tmp_path, ro, tmp_path / "missing" / "x.log"):
            with pytest.raises(EnvError) as ei:
                Diagnostics(log_file=bad)
            assert ei.value.exit_code == ExitCode.ENVIRONMENT == 4
            assert str(bad) in str(ei.value)
    finally:
        os.chmod(ro, stat.S_IWRITE | stat.S_IREAD)


def test_log_file_failure_happens_before_any_output(tmp_path, capsys):
    with pytest.raises(EnvError):
        Diagnostics(log_file=tmp_path / "nope" / "x.log")
    assert capsys.readouterr().err == ""


def test_log_file_never_contains_body_text(tmp_path, capsys):
    log = tmp_path / "b.log"
    body = "Dear Priya, SECRET-BODY-TEXT regards"
    with Diagnostics(verbose=True, log_file=log) as d:
        d.debug_body("email body", body)
    out = log.read_text(encoding="utf-8") + capsys.readouterr().err
    assert "SECRET-BODY-TEXT" not in out and f"{len(body)} characters omitted" in out


def test_person_names_only_at_debug(capsys):
    Diagnostics().debug_person("Priya Shah")
    Diagnostics(quiet=True).debug_person("Priya Shah")
    assert capsys.readouterr().err == ""
    Diagnostics(verbose=True).debug_person("Priya Shah")
    assert "Priya Shah" in capsys.readouterr().err


def test_redact_token_like_strings(capsys):
    text = "Authorization: Bearer abcdefgh12345678 token=ya29.A0abc-def password: hunter2 ok"
    out = redact(text)
    for secret in ("abcdefgh12345678", "ya29.A0abc-def", "hunter2"):
        assert secret not in out
    assert out.endswith("ok") and "[REDACTED]" in out
    Diagnostics().info("got access_token=zzzzzzzz1234")
    assert "zzzzzzzz1234" not in capsys.readouterr().err
    assert redact("2026-10-14-sprint-planning.md:18 Zoë") == "2026-10-14-sprint-planning.md:18 Zoë"


@pytest.mark.ac("AC-09.8")
def test_control_chars_stripped_in_diag(capsys):
    d = Diagnostics()
    d.info("a \x1b[31mred\x1b[0m \x9b \x7f")
    d.warn(WARN, "m \x1b[2J", file="f\x1b.md")
    d.error("e\x07")
    err = capsys.readouterr().err
    assert not any(c in err for c in ("\x1b", "\x9b", "\x7f", "\x07"))


def test_summary_printed_unless_quiet_semantics(capsys):
    """NFR-LOG-02: the summary is always printed, including with --quiet."""
    for kw in ({}, {"quiet": True}, {"verbose": True}):
        Diagnostics(**kw).summary(SUMMARY)
        assert SUMMARY in capsys.readouterr().err


def test_only_logging_setup_and_io_write_to_sys_stderr():
    src = Path(__file__).resolve().parent.parent.parent / "src"
    offenders = []
    for path in src.rglob("*.py"):
        if path.name in {"io.py", "logging_setup.py"} and path.parent.name == "actiontrack":
            continue
        text = path.read_text(encoding="utf-8")
        if "sys.stderr" in text:
            offenders.append(str(path.relative_to(src)))
    assert not offenders, offenders
