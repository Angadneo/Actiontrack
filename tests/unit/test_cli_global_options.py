"""T-M1-17: global option placement (H-03), last-wins merge, AC-09.11."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from actiontrack import cli, io
from actiontrack.cli import merge_global_options

MINIMAL = {
    "ingest": ["ingest", "notes"],
    "list": ["list"],
    "done": ["done", "AT-0001"],
    "followup": ["followup", "Priya Shah"],
}


def run(capsys, *argv: str) -> tuple[int, str, str]:
    rc = cli.main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


@pytest.fixture
def echo(monkeypatch):
    """Handler on every result command that echoes the resolved globals to stdout."""
    contexts: list[cli.CliContext] = []

    def handler(ctx):
        contexts.append(ctx)
        io.emit(
            f"cmd={ctx.command} format={ctx.format} today={ctx.run.today.isoformat()} "
            f"db={ctx.db_path.name} tz={ctx.config.timezone}"
        )
        ctx.diag.info("diag-line")

    for name in MINIMAL:
        monkeypatch.setitem(cli.HANDLERS, name, handler)
    return contexts


def cfg(path: Path, tz: str) -> Path:
    path.write_text(f'timezone = "{tz}"\n', encoding="utf-8")
    return path


# ------------------------------------------------------------------ merge (pure, dict level)
@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ({}, {}, {}),
        ({"format": "json"}, {}, {"format": "json"}),
        ({}, {"format": "json"}, {"format": "json"}),
        ({"format": "json"}, {"format": "table"}, {"format": "table"}),
        ({"format": "table"}, {"format": "json"}, {"format": "json"}),
        ({"today": "2026-10-01"}, {"today": "2026-10-22"}, {"today": "2026-10-22"}),
        ({"db": "a.db", "config": "a.toml"}, {"db": "b.db"}, {"db": "b.db", "config": "a.toml"}),
        ({"log_file": "x"}, {"log_file": None}, {"log_file": "x"}),
        ({"quiet": True}, {}, {"quiet": True}),
        ({}, {"verbose": True}, {"verbose": True}),
        ({"quiet": True}, {"verbose": True}, {"quiet": True, "verbose": True}),
        ({"no_color": False}, {"no_color": True}, {"no_color": True}),
        ({"unknown_extra": 1}, {"other": 2}, {}),
    ],
)
def test_merge_global_options_table(before, after, expected):
    merged = merge_global_options(before, after)
    defaults = {k: None for k in cli.VALUE_OPTIONS} | {k: False for k in cli.FLAG_OPTIONS}
    assert merged == defaults | expected
    assert set(merged) == set(cli.GLOBAL_OPTION_NAMES)


def test_merge_global_options_does_not_mutate_inputs():
    before, after = {"format": "json"}, {"format": "table"}
    merge_global_options(before, after)
    assert before == {"format": "json"} and after == {"format": "table"}


def test_last_wins_rule_lives_in_one_function():
    import inspect

    source = inspect.getsource(cli)
    assert source.count("def merge_global_options") == 1
    assert source.count("= merge_global_options(") == 1  # the single call, in _run


# ------------------------------------------------------------------ AC-09.11 end to end
@pytest.mark.ac("AC-09.11")
@pytest.mark.parametrize("command", list(MINIMAL))
def test_format_json_before_and_after_subcommand_identical(capsys, tmp_workdir, echo, command):
    argv = MINIMAL[command]
    before = run(capsys, "--format", "json", *argv)
    after = run(capsys, *argv, "--format", "json")
    assert before == after
    assert before[0] == 0 and "format=json" in before[1]
    other = run(capsys, *argv)
    assert "format=table" in other[1]


@pytest.mark.ac("AC-09.11")
def test_repeated_option_last_wins(capsys, tmp_workdir, echo):
    cases = [
        (["--format", "json", "list", "--format", "table"], "format=table"),
        (["--format", "table", "list", "--format", "json"], "format=json"),
        (["--format", "table", "--format", "json", "list"], "format=json"),
        (["list", "--format", "json", "--format", "table"], "format=table"),
        (["--today", "2026-10-01", "list", "--today", "2026-10-22"], "today=2026-10-22"),
        (["--today", "2026-10-22", "--today", "2026-10-01", "list"], "today=2026-10-01"),
        (["--db", "one.db", "list", "--db", "two.db"], "db=two.db"),
        (["--db", "a.db", "--db", "b.db", "list"], "db=b.db"),
    ]
    for argv, expected in cases:
        rc, out, _ = run(capsys, *argv)
        assert rc == 0 and expected in out, argv

    a = cfg(tmp_workdir / "a.toml", "Asia/Kolkata")
    b = cfg(tmp_workdir / "b.toml", "Europe/Paris")
    rc, out, _ = run(capsys, "--config", str(a), "list", "--config", str(b))
    assert "tz=Europe/Paris" in out
    rc, out, _ = run(capsys, "--config", str(b), "--config", str(a), "list")
    assert "tz=Asia/Kolkata" in out


@pytest.mark.ac("AC-09.11")
def test_all_globals_after_subcommand(capsys, tmp_workdir, echo):
    c = cfg(tmp_workdir / "c.toml", "Asia/Kolkata")
    log = tmp_workdir / "l.txt"
    rc, out, err = run(
        capsys, "list", "--db", "x.db", "--config", str(c), "--today", "2026-05-06",
        "--format", "json", "--log-file", str(log), "--no-color", "--verbose",
    )
    assert rc == 0
    ctx = echo[0]
    assert ctx.db_path.name == "x.db" and ctx.format == "json" and ctx.no_color
    assert ctx.run.today == date(2026, 5, 6) and ctx.diag.verbose
    assert log.read_text(encoding="utf-8").strip().splitlines()[-1].endswith("diag-line")


def test_global_options_after_subcommand(capsys, tmp_workdir, echo):
    rc, out, _ = run(capsys, "ingest", "notes", "--format", "json", "--today", "2026-01-01")
    assert rc == 0 and "format=json today=2026-01-01" in out


@pytest.mark.ac("AC-09.11")
def test_unused_option_accepted_and_ignored_on_gmail_login(capsys, tmp_workdir):
    results = [
        run(capsys, "gmail-login", "--format", "json"),
        run(capsys, "--format", "json", "gmail-login"),
        run(capsys, "gmail-login", "--today", "2026-10-22", "--format", "json"),
        run(capsys, "gmail-login"),
    ]
    for rc, out, err in results:
        assert rc == 0
        assert "not needed for the handoff channel" in out
        assert "warning" not in err.lower()
    assert results[0] == results[1]
    assert results[0][1] == results[3][1]


@pytest.mark.ac("AC-09.9")
@pytest.mark.parametrize("command", list(MINIMAL))
def test_format_accepted_by_all_four_result_commands(capsys, tmp_workdir, echo, command):
    for fmt in ("table", "json"):
        rc, out, _ = run(capsys, *MINIMAL[command], "--format", fmt)
        assert rc == 0 and f"format={fmt}" in out


def test_format_value_validated_in_both_positions(capsys, tmp_workdir, echo):
    assert run(capsys, "--format", "yaml", "list")[0] == 2
    assert run(capsys, "list", "--format", "yaml")[0] == 2
    assert run(capsys, "--format", "json", "list", "--format", "yaml")[0] == 2


@pytest.mark.ac("AC-09.9")
@pytest.mark.parametrize("argv", [["--quiet", "--verbose"], ["--verbose", "--quiet"]])
def test_quiet_verbose_not_subject_to_last_wins(capsys, tmp_workdir, echo, argv):
    for rc_argv in (["list", *argv], [argv[0], "list", argv[1]], [*argv, "list"]):
        rc, out, err = run(capsys, *rc_argv)
        assert rc == 2 and out == "" and "--quiet" in err and "--verbose" in err
    assert echo == []


@pytest.mark.ac("AC-09.11")
def test_help_after_subcommand_wins(capsys, tmp_workdir, echo):
    for argv in (
        ["list", "--help"],
        ["list", "--format", "json", "--help"],
        ["list", "--help", "--format", "json"],
        ["--format", "json", "list", "--help"],
        ["list", "--format", "json", "--version"],
        ["done", "--help"],
    ):
        rc, out, err = run(capsys, *argv)
        assert rc == 0 and err == "" and out, argv
        if "--version" in argv:
            assert out.startswith("actiontrack ") and "Synopsis" not in out
        else:
            command = next(a for a in argv if a in cli.COMMANDS)
            assert out == cli.HELP[command]
    assert echo == []


def test_help_after_command_is_that_commands_help(capsys):
    for command in cli.COMMANDS:
        rc, out, _ = run(capsys, command, "--format", "json", "--help")
        assert rc == 0 and out == cli.HELP[command]
    assert run(capsys, "--help")[1] == cli.HELP["program"]


def test_option_abbreviations_are_not_accepted(capsys, tmp_workdir, echo):
    assert run(capsys, "list", "--form", "json")[0] == 2
    assert run(capsys, "--db", "x.db", "list", "--dry")[0] == 2
