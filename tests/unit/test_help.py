"""T-M1-15: --help content for the program and every command (FR-09.3, AC-09.2)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from actiontrack import cli
from actiontrack.errors import ExitCode

ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = ROOT / "tests" / "golden"
SPEC = ROOT / "specs" / "actiontrack-spec.md"
KEYS = ["program", *cli.COMMANDS]


def help_out(capsys, monkeypatch, *argv: str) -> str:
    monkeypatch.setenv("COLUMNS", "100")
    assert cli.main(list(argv)) == 0
    cap = capsys.readouterr()
    assert cap.err == ""
    return cap.out


def argv_for(key: str) -> list[str]:
    return ["--help"] if key == "program" else [key, "--help"]


def section(text: str, label: str) -> list[str]:
    """Lines of a labelled section: from ``Label:`` to the next blank line / unindented label."""
    lines = text.splitlines()
    start = lines.index(f"{label}:")
    body = []
    for line in lines[start + 1 :]:
        if not line.strip() and body and label in ("Exit codes",):
            break
        if line and not line.startswith(" "):
            break
        body.append(line)
    return body


def exit_codes_in(text: str) -> list[int]:
    return [int(m.group(1)) for line in section(text, "Exit codes") if (m := re.match(r"^ {2}(\d) {2}\S", line))]


@pytest.mark.ac("AC-09.2")
@pytest.mark.parametrize("key", KEYS)
def test_help_has_four_parts_for_each_command(capsys, monkeypatch, key):
    out = help_out(capsys, monkeypatch, *argv_for(key))
    for label in ("Synopsis", "Options", "Exit codes", "Example"):
        assert f"\n{label}:\n" in "\n" + out, (key, label)
    assert section(out, "Synopsis") and section(out, "Options")
    assert exit_codes_in(out)
    ex = section(out, "Example")
    assert any(line.strip().startswith("actiontrack ") for line in ex)
    assert out == cli.HELP[key]


@pytest.mark.ac("AC-09.2")
def test_command_exit_codes_subset_of_table(capsys, monkeypatch):
    table = set(range(10))
    assert set(cli.EXIT_CODE_TABLE) == table == {int(c) for c in ExitCode}
    expected = {
        "ingest": [0, 2, 4, 5],
        "list": [0, 2, 3, 4, 6],
        "done": [0, 2, 3, 4],
        "followup": [0, 2, 3, 4, 6, 7, 8, 9],
        "gmail-login": [0, 4, 8],
    }
    for command, codes_ in expected.items():
        out = help_out(capsys, monkeypatch, command, "--help")
        assert exit_codes_in(out) == codes_ == list(cli.COMMAND_EXIT_CODES[command])
        assert set(codes_) <= table
    assert exit_codes_in(help_out(capsys, monkeypatch, "--help")) == list(range(10))


@pytest.mark.ac("AC-09.2")
def test_exit_code_9_listed_for_followup_and_program(capsys, monkeypatch):
    for argv in (["--help"], ["followup", "--help"]):
        out = help_out(capsys, monkeypatch, *argv)
        assert re.search(r"^ {2}9 {2}Cancelled by user", out, re.M)
    assert 9 not in exit_codes_in(help_out(capsys, monkeypatch, "list", "--help"))


def test_program_help_lists_all_global_options_and_placement_rule(capsys, monkeypatch):
    out = help_out(capsys, monkeypatch, "--help")
    for flag in ("--db", "--config", "--today", "--format", "--quiet", "--verbose",
                 "--log-file", "--no-color", "--help", "--version"):
        assert flag in out
    assert "table|json" in out and "ACTIONTRACK_DB" in out and "NO_COLOR" in out
    assert "before or after the command" in out and "last occurrence wins" in out
    for command in cli.COMMANDS:
        assert re.search(rf"^ {{2}}{re.escape(command)}\s+\S", out, re.M)
    assert 'Draft NOT yet created in Gmail' in out
    assert "prepared followup handoff exits 0" in out


def test_actiontrack_now_not_in_help(capsys, monkeypatch):
    for key in KEYS:
        assert "ACTIONTRACK_NOW" not in help_out(capsys, monkeypatch, *argv_for(key))
        assert "ACTIONTRACK_NOW" not in cli.HELP[key]


def test_followup_help_content(capsys, monkeypatch):
    out = help_out(capsys, monkeypatch, "followup", "--help")
    assert "never sent" in out
    assert "needed together with `--yes` to proceed past the repeat-draft warning" in out
    assert "(FR-04.9)" in out
    for text in ("--format", "--include-ambiguous", "--record-draft <file>", "--draft-id",
                 "--status created|failed", "co-owner names are omitted", "20 items",
                 "Draft NOT yet created in Gmail"):
        assert text in out
    assert "actiontrack followup --record-draft <handoff-file> [--draft-id <id>]" in out


def test_examples_use_double_quotes_only(capsys, monkeypatch):
    for key in KEYS:
        for line in section(cli.HELP[key], "Example"):
            assert "'" not in line, line


def test_no_help_line_is_a_bare_send_capable_tool_name(capsys, monkeypatch):
    forbidden = {
        "send_message", "reply", "forward", "update_draft", "delete_draft",
        "trash_message", "trash_thread", "mark_message_spam", "mark_thread_spam",
    }
    for text in cli.HELP.values():
        for line in text.splitlines():
            assert line.strip() not in forbidden


def _spec_option_sets() -> dict[str, set[str]]:
    """``--flag`` names from the first fenced synopsis block of each spec 4.x section."""
    text = SPEC.read_text(encoding="utf-8")
    sections = re.split(r"^### 4\.(\d) ", text, flags=re.M)
    by_number = {sections[i]: sections[i + 1] for i in range(1, len(sections) - 1, 2)}
    names = {"1": "program", "2": "ingest", "3": "list", "4": "done", "5": "followup", "6": "gmail-login"}
    result = {}
    for number, key in names.items():
        block = re.search(r"```\n(.*?)```", by_number[number], re.S).group(1)
        result[key] = set(re.findall(r"--[a-z][a-z-]*", block))
    return result


@pytest.mark.parametrize("key", KEYS)
def test_every_option_in_spec_appears_in_help(key):
    expected = _spec_option_sets()[key]
    assert expected, key
    for flag in sorted(expected):
        assert re.search(rf"{re.escape(flag)}(?![a-z-])", cli.HELP[key]), (key, flag)


def test_every_registered_parser_option_is_documented():
    """No option exists in the parser that its help does not mention (and vice versa for commands)."""
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.choices and "ingest" in a.choices)
    for name, p in sub.choices.items():
        for action in p._actions:
            for flag in action.option_strings:
                if flag.startswith("--") and flag != "--help":
                    assert flag in cli.HELP[name] or flag in cli.HELP["program"], (name, flag)


# ------------------------------------------------------------------ goldens
@pytest.mark.ac("AC-09.2")
@pytest.mark.parametrize("key", KEYS)
def test_golden_help_screens(capsys, monkeypatch, key):
    out = help_out(capsys, monkeypatch, *argv_for(key))
    golden = GOLDEN / f"help_{key}.txt"
    assert golden.read_bytes().decode("utf-8") == out
    assert "\r" not in out
    assert max(len(line) for line in out.splitlines()) <= 100
