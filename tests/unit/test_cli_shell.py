"""T-M1-14: CLI shell (argparse, precedence, exit-code mapping), in-process through main()."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from actiontrack import cli
from actiontrack.errors import (
    AmbiguousOwnerError,
    CancelledByUser,
    DraftNotCreatedError,
    EnvError,
    ExitCode,
    NotFoundError,
    PreconditionError,
    UsageError,
    EXIT_HANDOFF_PREPARED,
)

COMMANDS = ["ingest", "list", "done", "followup", "gmail-login"]
RESULT_COMMANDS = ["ingest", "list", "done", "followup"]
MINIMAL = {
    "ingest": ["ingest", "notes"],
    "list": ["list"],
    "done": ["done", "AT-0001"],
    "followup": ["followup", "Priya Shah"],
    "gmail-login": ["gmail-login"],
}


def run(capsys, *argv: str) -> tuple[int, str, str]:
    rc = cli.main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def set_handler(monkeypatch, command, fn):
    monkeypatch.setitem(cli.HANDLERS, command, fn)


@pytest.fixture
def seen(monkeypatch):
    """Install a recording handler on `list`; returns the list of contexts."""
    contexts: list[cli.CliContext] = []

    def handler(ctx):
        contexts.append(ctx)

    set_handler(monkeypatch, "list", handler)
    return contexts


def write_config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------ registration / stubs
def test_all_five_commands_registered():
    assert set(cli.HANDLERS) == set(COMMANDS)
    assert tuple(COMMANDS) == cli.COMMANDS


# All four real commands have handlers now; followup without an ingested database exits 3 (like list/done).
def test_followup_without_database_exits_3(capsys, tmp_workdir):
    rc, out, err = run(capsys, *MINIMAL["followup"])
    assert rc == int(ExitCode.NOT_FOUND)
    assert out == ""
    assert "database not found" in err and "actiontrack ingest" in err
    assert "Traceback" not in err


def test_gmail_login_handoff_channel_is_a_noop_exit_0(capsys, tmp_workdir):
    rc, out, err = run(capsys, "gmail-login")
    assert rc == 0
    assert "not needed for the handoff channel" in out
    assert "not implemented" not in err


def test_gmail_login_google_channel_is_still_a_stub(capsys, tmp_workdir):
    cfg = write_config(tmp_workdir / "g.toml", '[gmail]\nchannel = "google"\n')
    rc, out, err = run(capsys, "--config", str(cfg), "gmail-login")
    assert rc == 1 and out == ""
    assert "gmail-login: not implemented in this build" in err


def test_stub_contract_is_replaceable():
    """A later milestone replaces one HANDLERS entry without touching the shell."""
    handler = cli.not_implemented_handler("ingest")
    with pytest.raises(cli.NotImplementedYet) as info:
        handler(None)  # type: ignore[arg-type]
    assert info.value.exit_code == ExitCode.INTERNAL_ERROR
    assert info.value.command == "ingest"


# ---------------------------------------------------------------------- exit code 2
@pytest.mark.ac("AC-09.2")
@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["ingest"],
        ["done"],
        ["followup"],
        ["list", "--bogus"],
        ["--bogus", "list"],
        ["nosuchcommand"],
        ["list", "--status", "weird"],
        ["list", "--format", "xml"],
        ["followup", "A B", "--status", "maybe"],
        ["ingest", "a", "b"],
    ],
)
def test_exit_code_2_unknown_option_and_missing_args(capsys, tmp_workdir, argv):
    rc, out, err = run(capsys, *argv)
    assert rc == int(ExitCode.USAGE)
    assert out == ""
    assert "error:" in err and "--help" in err
    assert "Traceback" not in err
    assert not (tmp_workdir / ".actiontrack").exists()


def test_usage_error_message_goes_through_sanitiser(capsys, tmp_workdir):
    rc, out, err = run(capsys, "list", "--bogus\x1b[31m")
    assert rc == 2
    assert "\x1b" not in err


def test_quiet_verbose_mutually_exclusive_exit2(capsys, tmp_workdir, seen):
    for argv in (["--quiet", "--verbose", "list"], ["--verbose", "list", "--quiet"]):
        rc, out, err = run(capsys, *argv)
        assert rc == 2
        assert "--quiet" in err and "--verbose" in err
    assert seen == []


# ------------------------------------------------------------------ followup grammar
def test_followup_grammar_accepts_record_draft_without_owner(monkeypatch, capsys, tmp_workdir):
    captured = []
    set_handler(monkeypatch, "followup", lambda ctx: captured.append(ctx.args))
    rc, _, _ = run(
        capsys, "followup", "--record-draft", "f.json", "--draft-id", "r-1", "--status", "created"
    )
    assert rc == 0
    args = captured[0]
    assert args.owner is None and args.record_draft == "f.json" and args.draft_id == "r-1"
    assert args.status == "created"


def test_followup_accepts_force_and_format(monkeypatch, capsys, tmp_workdir):
    captured = []
    set_handler(monkeypatch, "followup", lambda ctx: captured.append(ctx))
    rc, _, _ = run(
        capsys, "followup", "Priya Shah", "--to", "p@example.com", "--include-ambiguous",
        "--dry-run", "--yes", "--force", "--format", "json",
    )
    assert rc == 0
    ctx = captured[0]
    assert ctx.args.force and ctx.args.yes and ctx.args.dry_run and ctx.args.include_ambiguous
    assert ctx.args.to == "p@example.com" and ctx.format == "json"


def test_list_options_parse(monkeypatch, capsys, tmp_workdir, seen):
    rc, _, _ = run(
        capsys, "list", "--owner", "Priya Shah", "--status", "all", "--due-before", "2026-10-30",
        "--include-ambiguous", "--wide",
    )
    assert rc == 0
    a = seen[0].args
    assert a.owner == "Priya Shah" and a.status == "all" and a.due_before == "2026-10-30"
    assert a.include_ambiguous and not a.ambiguous and a.wide


def test_list_option_conflicts_are_usage_errors(capsys, tmp_workdir, seen):
    """M6 registers the AC-02.9 / AC-02.11 conflict rules in ``VALIDATORS`` (exit 2, handler never called)."""
    for argv in (["--owner", "X", "--ambiguous"], ["--include-ambiguous"], ["--ambiguous", "--include-ambiguous"]):
        rc, out, err = run(capsys, "list", *argv)
        assert rc == 2 and out == "" and "--" in err
    assert seen == []


def test_list_status_default_open(seen, capsys, tmp_workdir):
    run(capsys, "list")
    assert seen[0].args.status == "open"


def test_ingest_and_done_options(monkeypatch, capsys, tmp_workdir):
    got = {}
    set_handler(monkeypatch, "ingest", lambda ctx: got.update(i=ctx.args))
    set_handler(monkeypatch, "done", lambda ctx: got.update(d=ctx.args))
    run(capsys, "ingest", "notes dir", "--no-recursive", "--dry-run")
    run(capsys, "done", "at-12")
    assert got["i"].folder == "notes dir" and got["i"].no_recursive and got["i"].dry_run
    assert got["d"].id == "at-12"


def test_validate_args_extension_point_is_called(monkeypatch, capsys, tmp_workdir, seen):
    def boom(args):
        raise UsageError("custom conflict")

    monkeypatch.setitem(cli.VALIDATORS, "list", boom)
    rc, out, err = run(capsys, "list")
    assert rc == 2 and "custom conflict" in err and seen == []


# ------------------------------------------------------------------ exit code mapping
@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (NotFoundError("no such item"), 3),
        (EnvError("db unusable"), 4),
        (AmbiguousOwnerError("two Priyas"), 6),
        (PreconditionError("no recipient email"), 7),
        (DraftNotCreatedError("draft unknown"), 8),
        (UsageError("bad combination"), 2),
    ],
)
def test_error_codes_reachable_via_test_handler(monkeypatch, capsys, tmp_workdir, exc, code):
    def handler(ctx):
        raise exc

    set_handler(monkeypatch, "list", handler)
    rc, out, err = run(capsys, "list")
    assert rc == code and out == ""
    assert exc.what in err


def test_exit_5_via_returned_code(monkeypatch, capsys, tmp_workdir):
    set_handler(monkeypatch, "ingest", lambda ctx: ExitCode.INGEST_FILE_FAILURES)
    rc, _, _ = run(capsys, "ingest", "notes")
    assert rc == 5


def test_exit_9_reachable_via_test_handler(monkeypatch, capsys, tmp_workdir):
    def cancel(ctx):
        raise CancelledByUser()

    set_handler(monkeypatch, "followup", cancel)
    rc, out, err = run(capsys, "followup", "Priya Shah")
    assert rc == 9 and out == ""
    assert "Cancelled. Nothing created." in err

    set_handler(monkeypatch, "followup", lambda ctx: EXIT_HANDOFF_PREPARED)
    assert run(capsys, "followup", "Priya Shah")[0] == 0


def test_exit_9_still_shown_with_quiet(monkeypatch, capsys, tmp_workdir):
    def cancel(ctx):
        raise CancelledByUser()

    set_handler(monkeypatch, "followup", cancel)
    rc, _, err = run(capsys, "--quiet", "followup", "Priya Shah")
    assert rc == 9 and "Cancelled. Nothing created." in err


def test_none_return_is_success(monkeypatch, capsys, tmp_workdir):
    set_handler(monkeypatch, "list", lambda ctx: None)
    assert run(capsys, "list")[0] == 0


def test_exit_code_1_internal_error_no_traceback_unless_verbose(monkeypatch, capsys, tmp_workdir):
    def crash(ctx):
        raise RuntimeError("kaboom")

    set_handler(monkeypatch, "list", crash)
    rc, out, err = run(capsys, "list")
    assert rc == 1 and out == ""
    assert "kaboom" in err and "--verbose" in err
    assert "Traceback" not in err

    rc, out, err = run(capsys, "--verbose", "list")
    assert rc == 1
    assert "Traceback (most recent call last)" in err and "kaboom" in err


def test_keyboard_interrupt_is_exit_1_one_line(monkeypatch, capsys, tmp_workdir):
    def interrupt(ctx):
        raise KeyboardInterrupt

    set_handler(monkeypatch, "list", interrupt)
    rc, out, err = run(capsys, "--quiet", "list")  # --quiet hides the timezone hint
    assert rc == 1 and out == ""
    assert len(err.strip().splitlines()) == 1 and "interrupted" in err


# ---------------------------------------------------------------- config / env precedence
@pytest.mark.ac("AC-09.1")
def test_exit_code_4_invalid_config_before_any_write(capsys, tmp_workdir, tmp_db_path):
    bad = write_config(tmp_workdir / "bad.toml", 'timezone = "Asia/Kolkata"\nbogus_key = 1\n')
    for command in RESULT_COMMANDS:
        argv = MINIMAL[command]
        rc, out, err = run(capsys, "--config", str(bad), "--db", str(tmp_db_path), *argv)
        assert rc == 4, command
        assert "bogus_key" in err
    assert not tmp_db_path.exists()
    assert not (tmp_workdir / ".actiontrack").exists()


def test_missing_explicit_config_is_exit_4(capsys, tmp_workdir):
    rc, _, err = run(capsys, "--config", "nope.toml", "list")
    assert rc == 4 and "nope.toml" in err


def test_flag_beats_env_for_db_config_today(monkeypatch, capsys, tmp_workdir, seen):
    cfg_flag = write_config(tmp_workdir / "flag.toml", 'timezone = "Asia/Kolkata"\n')
    cfg_env = write_config(tmp_workdir / "env.toml", 'timezone = "Europe/Paris"\n')
    monkeypatch.setenv("ACTIONTRACK_DB", str(tmp_workdir / "env.db"))
    monkeypatch.setenv("ACTIONTRACK_CONFIG", str(cfg_env))
    monkeypatch.setenv("ACTIONTRACK_TODAY", "2026-01-02")

    run(capsys, "list")
    env_ctx = seen[-1]
    assert env_ctx.db_path == tmp_workdir / "env.db"
    assert env_ctx.config.timezone == "Europe/Paris"
    assert env_ctx.run.today == date(2026, 1, 2)

    run(capsys, "--db", "flag.db", "--config", str(cfg_flag), "--today", "2026-03-04", "list")
    flag_ctx = seen[-1]
    assert flag_ctx.db_path == tmp_workdir / "flag.db"
    assert flag_ctx.config.timezone == "Asia/Kolkata"
    assert flag_ctx.run.today == date(2026, 3, 4)


def test_defaults_and_empty_env_is_unset(monkeypatch, capsys, tmp_workdir, seen):
    monkeypatch.setenv("ACTIONTRACK_DB", "")
    monkeypatch.setenv("ACTIONTRACK_CONFIG", "")
    monkeypatch.setenv("ACTIONTRACK_TODAY", "")
    monkeypatch.setenv("NO_COLOR", "")
    rc, _, _ = run(capsys, "list")
    assert rc == 0
    ctx = seen[0]
    assert ctx.db_path == (tmp_workdir / ".actiontrack" / "actiontrack.db")
    assert ctx.config.source is None
    assert ctx.format == "table" and ctx.no_color is False
    assert not ctx.db_path.exists()


def test_default_config_file_in_cwd_is_used(capsys, tmp_workdir, seen):
    write_config(tmp_workdir / "actiontrack.toml", 'timezone = "Asia/Kolkata"\n')
    run(capsys, "list")
    assert seen[0].config.timezone == "Asia/Kolkata"


def test_invalid_today_flag_and_env_exit_2_before_handler(monkeypatch, capsys, tmp_workdir, seen):
    rc, _, err = run(capsys, "--today", "2026-13-40", "list")
    assert rc == 2 and "2026-13-40" in err
    monkeypatch.setenv("ACTIONTRACK_TODAY", "yesterday")
    rc, _, err = run(capsys, "list")
    assert rc == 2 and "ACTIONTRACK_TODAY" in err
    assert seen == []


def test_actiontrack_now_fixes_clock(monkeypatch, capsys, tmp_workdir, seen):
    cfg = write_config(tmp_workdir / "c.toml", 'timezone = "Asia/Kolkata"\n')
    monkeypatch.setenv("ACTIONTRACK_NOW", "2026-10-22T20:00:00Z")
    run(capsys, "--config", str(cfg), "list")
    ctx = seen[0]
    assert ctx.run.today == date(2026, 10, 23)
    assert ctx.run.clock.now_utc().isoformat() == "2026-10-22T20:00:00+00:00"


def test_no_color_flag_and_env(monkeypatch, capsys, tmp_workdir, seen):
    run(capsys, "list", "--no-color")
    assert seen[-1].no_color is True
    monkeypatch.setenv("NO_COLOR", "1")
    run(capsys, "list")
    assert seen[-1].no_color is True


# ------------------------------------------------------------ help/version don't read anything
@pytest.mark.ac("AC-09.2")
def test_help_and_version_work_with_broken_config_and_invalid_today_env(
    monkeypatch, capsys, tmp_workdir, seen
):
    bad = write_config(tmp_workdir / "bad.toml", "this is = not [toml")
    monkeypatch.setenv("ACTIONTRACK_CONFIG", str(bad))
    monkeypatch.setenv("ACTIONTRACK_TODAY", "not-a-date")
    monkeypatch.setenv("ACTIONTRACK_NOW", "garbage")
    for argv in (["--version"], ["--help"], ["list", "--help"], ["followup", "--version"], ["-h"]):
        rc, out, err = run(capsys, *argv)
        assert rc == 0 and out and err == "", argv
    assert seen == []


def test_version_output(capsys):
    from actiontrack import __version__

    rc, out, err = run(capsys, "--version")
    assert (rc, out, err) == (0, f"actiontrack {__version__}\n", "")


def test_help_goes_to_stdout_and_creates_nothing(capsys, tmp_workdir):
    rc, out, err = run(capsys, "--help")
    assert rc == 0 and out == cli.HELP["program"] and err == ""
    assert list(tmp_workdir.iterdir()) == []


# ------------------------------------------------------------------ stream discipline
def test_stdout_vs_stderr_split(monkeypatch, capsys, tmp_workdir):
    def handler(ctx):
        from actiontrack import codes, io

        io.emit("RESULT-LINE")
        ctx.diag.warn(codes.W_EMPTY_FILE, "WARN-LINE", file="a.md", line=1)
        ctx.diag.info("INFO-LINE")
        raise EnvError("ERROR-LINE")

    set_handler(monkeypatch, "list", handler)
    rc, out, err = run(capsys, "list")
    assert rc == 4
    assert out == "RESULT-LINE\n"
    for text in ("WARN-LINE", "INFO-LINE", "ERROR-LINE"):
        assert text in err and text not in out


def test_result_text_is_sanitised(monkeypatch, capsys, tmp_workdir):
    def handler(ctx):
        from actiontrack import io

        io.emit("a\x1b[31mred")

    set_handler(monkeypatch, "list", handler)
    rc, out, _ = run(capsys, "list")
    assert "\x1b" not in out


def test_timezone_fallback_hint_goes_to_stderr(capsys, tmp_workdir, seen):
    rc, out, err = run(capsys, "list")
    assert out == "" and "no \"timezone\" configured" in err
    cfg = write_config(tmp_workdir / "c.toml", 'timezone = "Asia/Kolkata"\n')
    rc, out, err = run(capsys, "--config", str(cfg), "list")
    assert err == ""


def test_diag_options_are_closed_after_main(capsys, tmp_workdir, seen):
    log = tmp_workdir / "log.txt"
    run(capsys, "--log-file", str(log), "list")
    log.unlink()  # would fail on Windows if the handle were still open
