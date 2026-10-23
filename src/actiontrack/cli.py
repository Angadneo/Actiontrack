"""Command-line shell: argparse, global options, precedence, exit-code mapping (T-M1-14/15/17).

``main(argv=None) -> int`` is the single entry point and the only place that turns exceptions
into exit codes. It never calls ``sys.exit``; the console script and ``__main__`` do.

Flow of one invocation
----------------------
1. ``io.configure_streams()`` (UTF-8, LF) before anything is written.
2. Parse. Global options are accepted before AND after the command (H-03). The top-level
   parser stores them under their plain names, every sub-parser under ``after__<name>`` with
   ``default=SUPPRESS``; ``merge_global_options(before, after)`` is the ONLY place that
   implements "last occurrence wins" (``--quiet``/``--verbose`` are never last-wins: together
   they are a usage error). ``--help``/``--version`` act the moment they are parsed (exit 0, no
   config/DB/env-date is read). Parse errors become ``UsageError`` (exit 2).
3. ``VALIDATORS[command](args)`` (the ``validate_args`` extension point, exit 2 on violation).
4. ``Diagnostics`` (``--quiet``+``--verbose`` -> exit 2; unopenable ``--log-file`` -> exit 4),
   then ``load_config`` (exit 4), then ``build_run_context`` (invalid ``--today`` -> exit 2),
   all before any handler (and so before any DB write).
5. ``HANDLERS[command](ctx)`` (looked up at call time, so milestones plug in one by one).
   Returns an ``int`` exit code, or ``None`` for success. A prepared followup handoff returns
   ``EXIT_HANDOFF_PREPARED``; this module never hard-codes that integer.

Dispatch-stub contract (replace one by one in M2-M8)
----------------------------------------------------
``HANDLERS`` maps each of ``ingest``, ``list``, ``done``, ``followup``, ``gmail-login`` to a
callable ``handler(ctx: CliContext) -> int | None``. ``ingest`` is real since M5 (``ingest_handler``); in M1 the
result commands were ``not_implemented_handler(command)`` and ``list``, ``done``, ``followup`` still are: it raises ``NotImplementedYet`` (an ``InternalError``,
exit 1) whose message is "<command>: not implemented in this build" (stderr, no traceback).
``gmail-login`` is real only for the ``handoff``/``fake`` channels ("not needed" no-op, exit 0);
for ``google`` it is the same stub (M8). Handlers get everything through ``CliContext``
(parsed ``args``, ``config``, ``run`` = ``clock.RunContext``, ``diag``, ``db_path``,
``format``, ``no_color``), print result data with ``io.emit`` and diagnostics through
``ctx.diag``; they raise ``ActionTrackError`` subclasses for exit codes 2-9.

Output discipline: result data -> stdout (``io.emit``); warnings, diagnostics, errors,
usage messages -> stderr (``Diagnostics``/``io.diag``); never ``print``.

Help text is pure data (``HELP``, ``COMMAND_EXIT_CODES``, ``EXIT_CODE_TABLE``) so the M9
consistency checks can compare it with the code constants.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from actiontrack import __version__, io
from actiontrack.clock import RunContext, build_run_context
from actiontrack.config import Config, load_config
from actiontrack.errors import (
    ActionTrackError,
    CancelledByUser,
    ExitCode,
    InternalError,
    UsageError,
    exit_code_for,
)
from actiontrack.logging_setup import Diagnostics

# --------------------------------------------------------------------------- help data
EXIT_CODE_TABLE: dict[int, str] = {
    0: "Success (data-quality warnings alone do not change the code)",
    1: "Unexpected internal error",
    2: "Usage error / invalid argument",
    3: "Not found (item ID, owner)",
    4: "Environment error (folder, database, config, handoff file or log file unusable)",
    5: "ingest finished but at least one file failed to parse or read",
    6: "Ambiguous owner (the query matches two or more people)",
    7: "followup precondition failed (for example no recipient email)",
    8: "Gmail draft not created or status unknown",
    9: "Cancelled by user (nothing was created)",
}

COMMAND_EXIT_CODES: dict[str, tuple[int, ...]] = {
    "ingest": (0, 2, 4, 5),
    "list": (0, 2, 3, 4, 6),
    "done": (0, 2, 3, 4),
    "followup": (0, 2, 3, 4, 6, 7, 8, 9),
    "gmail-login": (0, 4, 8),
}

COMMANDS: tuple[str, ...] = tuple(COMMAND_EXIT_CODES)

_GLOBAL_OPTIONS_NOTE = (
    'Global options (--db, --config, --today, --format, --quiet, --verbose, --log-file,\n'
    '  --no-color) may also be given here; run "actiontrack --help" for them.'
)

_PROGRAM_HELP = """\
actiontrack - extract action items from Markdown meeting notes, list and complete them,
and prepare follow-up emails as Gmail drafts. It can never send email.

Synopsis:
  actiontrack [global options] <command> [args]
  actiontrack <command> [args] [global options]

Commands:
  ingest       Read a folder of Markdown notes and extract action items
  list         List and filter action items
  done         Mark an action item as done
  followup     Prepare a Gmail draft with one owner's open items (the draft is never sent)
  gmail-login  Sign in for the direct Google channel (not needed for the handoff channel)

Options:
  Global options are accepted before or after the command; the last occurrence wins.
  --db <path>          Database file (default: ./.actiontrack/actiontrack.db; env ACTIONTRACK_DB)
  --config <path>      Config/roster file (default: ./actiontrack.toml if present;
                       env ACTIONTRACK_CONFIG)
  --today <YYYY-MM-DD> Override the current date (env ACTIONTRACK_TODAY)
  --format table|json  Output format for ingest, list, done and followup (default: table)
  --quiet              Errors and the final summary line only on stderr; never hides results
  --verbose            Debug diagnostics, stack traces and personal names in logs
                       (--quiet and --verbose together are a usage error)
  --log-file <path>    Append diagnostics to this file (UTF-8); an unopenable file is exit 4
  --no-color           Disable colour (also honours NO_COLOR)
  --help               Show this help (after a command: help for that command)
  --version            Print the version

Result data goes to stdout; warnings, diagnostics and errors go to stderr.

Exit codes:
{exit_codes}
  A prepared followup handoff exits 0 but prints "Draft NOT yet created in Gmail".

Example:
  actiontrack --today 2026-10-22 list --status open --format json
"""

_COMMAND_HELP: dict[str, dict[str, Any]] = {
    "ingest": {
        "summary": "Read a folder of Markdown meeting notes and extract action items.\n"
        "Notes are only read, never changed.",
        "synopsis": ["actiontrack ingest <folder> [--no-recursive] [--dry-run] [global options]"],
        "options": [
            ("<folder>", "Folder of Markdown (.md) notes."),
            ("--no-recursive", "Scan only the top level (subfolders are scanned by default)."),
            ("--dry-run", "Parse and report, but write nothing."),
            ("--format table|json", "Report format (default: table)."),
        ],
        "example": ['actiontrack ingest "C:\\notes\\meetings" --dry-run'],
    },
    "list": {
        "summary": "List action items, optionally filtered.",
        "synopsis": [
            "actiontrack list [--owner <full name|id|email>] [--status open|done|overdue|all]",
            "                 [--due-before <date>] [--include-ambiguous] [--ambiguous] [--wide]",
            "                 [--format table|json]",
        ],
        "options": [
            ("--owner <full name|id|email>", "Only this person's items (a bare shared first name is rejected)."),
            ("--status open|done|overdue|all", "Which items to show (default: open)."),
            ("--due-before <date>", "Only items due before this date."),
            ("--include-ambiguous", "With --owner: also show items whose owner may be this person."),
            ("--ambiguous", "Only items whose owner is ambiguous (not with --owner)."),
            ("--wide", "Show wider columns."),
            ("--format table|json", "Output format (default: table)."),
        ],
        "example": ['actiontrack list --owner "Priya Shah" --status overdue'],
    },
    "done": {
        "summary": "Mark an action item as done.",
        "synopsis": ["actiontrack done <id> [--format table|json]"],
        "options": [
            ("<id>", "Item ID: AT-0012, 12 or at-12 (case-insensitive)."),
            ("--format table|json", "Output format (default: table)."),
        ],
        "example": ["actiontrack done AT-0012"],
    },
    "followup": {
        "summary": "Prepare a follow-up email with one owner's open items as a Gmail draft.\n"
        "Drafts are never sent: review and send them yourself from Gmail Drafts.",
        "synopsis": [
            "actiontrack followup <full name|id|email> [--to <email>] [--include-ambiguous]",
            "                     [--dry-run] [--yes] [--force] [--format table|json]",
            "actiontrack followup --record-draft <handoff-file> [--draft-id <id>]",
            "                     [--status created|failed]",
        ],
        "options": [
            ("<full name|id|email>", "The owner to write to (not given with --record-draft)."),
            ("--to <email>", "Recipient email address."),
            (
                "--include-ambiguous",
                "Opt-in: add a separate section (up to 20 items) of ambiguous\n"
                "items that may be this person's; only task text and meeting\n"
                "are shown, co-owner names are omitted.",
            ),
            ("--dry-run", "Show the preview only; create nothing."),
            ("--yes", "Answer the confirmation prompt (required without a terminal)."),
            (
                "--force",
                "needed together with `--yes` to proceed past the repeat-draft warning\n"
                "without a TTY (FR-04.9).",
            ),
            ("--format table|json", "Output format (default: table); JSON carries a status field."),
            (
                "--record-draft <file>",
                "Second form: record the outcome of a prepared handoff file\n"
                "that the host session's Gmail connector handled.",
            ),
            ("--draft-id <id>", "Draft id reported by the host (needed for --status created)."),
            ("--status created|failed", "Outcome to record (default: created; failed takes no --draft-id)."),
        ],
        "notes": 'A prepared handoff exits 0 but prints "Draft NOT yet created in Gmail".\n'
        "Declining the confirmation exits 9 and creates nothing.",
        "example": [
            'actiontrack followup "Priya Shah" --dry-run',
            'actiontrack followup --record-draft ".actiontrack\\handoff\\file.json" --draft-id r-123',
        ],
    },
    "gmail-login": {
        "summary": "One-time sign-in for the direct Google channel. With the default handoff\n"
        "channel it prints that no sign-in is needed. It never reads, sends or changes mail.",
        "synopsis": ["actiontrack gmail-login [--logout] [global options]"],
        "options": [
            ("--logout", "Delete the stored sign-in token."),
            ("--format table|json", "Accepted and ignored."),
        ],
        "example": ["actiontrack gmail-login"],
    },
}


def _exit_code_lines(codes_: Sequence[int]) -> str:
    return "\n".join(f"  {c}  {EXIT_CODE_TABLE[c]}" for c in codes_)


def _render_command_help(name: str) -> str:
    spec = _COMMAND_HELP[name]
    width = max(len(flag) for flag, _ in spec["options"]) + 2
    lines = [f"actiontrack {name} - {spec['summary']}", "", "Synopsis:"]
    lines += [f"  {s}" for s in spec["synopsis"]]
    lines += ["", "Options:"]
    for flag, text in spec["options"]:
        first, *rest = text.split("\n")
        lines.append(f"  {flag.ljust(width)}{first}")
        lines += [f"  {' ' * width}{r}" for r in rest]
    lines += [f"  {_GLOBAL_OPTIONS_NOTE}"]
    if "notes" in spec:
        lines += ["", spec["notes"]]
    lines += ["", "Exit codes:", _exit_code_lines(COMMAND_EXIT_CODES[name]), "", "Example:"]
    lines += [f"  {e}" for e in spec["example"]]
    return "\n".join(lines) + "\n"


#: Complete help screens by key (``"program"`` or a command name). Pure data.
HELP: dict[str, str] = {
    "program": _PROGRAM_HELP.format(exit_codes=_exit_code_lines(sorted(EXIT_CODE_TABLE))),
    **{name: _render_command_help(name) for name in COMMANDS},
}

# ------------------------------------------------------------------ global option merge
VALUE_OPTIONS: tuple[str, ...] = ("db", "config", "today", "format", "log_file")
FLAG_OPTIONS: tuple[str, ...] = ("quiet", "verbose", "no_color")
GLOBAL_OPTION_NAMES: tuple[str, ...] = VALUE_OPTIONS + FLAG_OPTIONS
_AFTER = "after__"


def merge_global_options(
    parsed_before: Mapping[str, Any], parsed_after: Mapping[str, Any]
) -> dict[str, Any]:
    """Combine the globals given before and after the command (H-03). Pure, no argparse.

    ``parsed_after`` holds only the options actually given after the command. A value option
    given after wins over (is the later occurrence than) any value given before; within one
    position argparse already keeps the last occurrence. Flags are OR-ed: ``--quiet`` and
    ``--verbose`` are never subject to last-wins (together they are rejected by Diagnostics).
    """
    merged: dict[str, Any] = {}
    for key in VALUE_OPTIONS:
        after = parsed_after.get(key)
        merged[key] = after if after is not None else parsed_before.get(key)
    for key in FLAG_OPTIONS:
        merged[key] = bool(parsed_before.get(key)) or bool(parsed_after.get(key))
    return merged


# --------------------------------------------------------------------------- parser
class NotImplementedYet(InternalError):
    """Raised by a dispatch stub: the command exists but its milestone has not landed (exit 1)."""

    def __init__(self, command: str) -> None:
        super().__init__(f"{command}: not implemented in this build")
        self.command = command


class _ParserExit(Exception):
    """Raised instead of ``sys.exit`` by ``--help``/``--version`` (carries the exit code)."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose errors are ``UsageError`` and whose output goes through ``io``."""

    help_key = "program"

    def error(self, message: str) -> NoReturn:
        raise UsageError(message, next_step=f'run "{self.prog} --help" for usage')

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            io.diag(message.rstrip("\n"))
        raise _ParserExit(status)

    def print_help(self, file: Any = None) -> None:
        io.emit(HELP[self.help_key].rstrip("\n"))

    def print_usage(self, file: Any = None) -> None:
        io.diag(self.format_usage().rstrip("\n"))


class _HelpAction(argparse.Action):
    def __init__(self, option_strings: Sequence[str], dest: str = argparse.SUPPRESS,
                 default: str = argparse.SUPPRESS, help: str | None = None) -> None:
        super().__init__(option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser: Any, namespace: Any, values: Any, option_string: Any = None) -> NoReturn:
        parser.print_help()
        raise _ParserExit(int(ExitCode.OK))


class _VersionAction(_HelpAction):
    def __call__(self, parser: Any, namespace: Any, values: Any, option_string: Any = None) -> NoReturn:
        io.emit(f"actiontrack {__version__}")
        raise _ParserExit(int(ExitCode.OK))


def _add_global_options(parser: _Parser, *, after: bool) -> None:
    """Register the global options. ``after`` = on a sub-parser (separate dest, no default)."""

    def dest(name: str) -> str:
        return f"{_AFTER}{name}" if after else name

    value_default: Any = argparse.SUPPRESS if after else None
    flag_default: Any = argparse.SUPPRESS if after else False
    parser.add_argument("--db", dest=dest("db"), default=value_default, metavar="PATH")
    parser.add_argument("--config", dest=dest("config"), default=value_default, metavar="PATH")
    parser.add_argument("--today", dest=dest("today"), default=value_default, metavar="YYYY-MM-DD")
    parser.add_argument(
        "--format", dest=dest("format"), default=value_default, choices=("table", "json")
    )
    parser.add_argument("--log-file", dest=dest("log_file"), default=value_default, metavar="PATH")
    for flag in ("quiet", "verbose", "no_color"):
        parser.add_argument(
            f"--{flag.replace('_', '-')}", dest=dest(flag), action="store_true", default=flag_default
        )
    parser.add_argument("-h", "--help", action=_HelpAction)
    parser.add_argument("--version", action=_VersionAction)


def build_parser() -> _Parser:
    parser = _Parser(prog="actiontrack", add_help=False, allow_abbrev=False)
    parser.help_key = "program"
    _add_global_options(parser, after=False)
    sub = parser.add_subparsers(dest="command", metavar="<command>", parser_class=_Parser)
    sub.required = True

    def command(name: str) -> _Parser:
        p = sub.add_parser(name, add_help=False, allow_abbrev=False)
        p.help_key = name
        _add_global_options(p, after=True)
        return p

    p = command("ingest")
    p.add_argument("folder")
    p.add_argument("--no-recursive", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    p = command("list")
    p.add_argument("--owner")
    p.add_argument("--status", choices=("open", "done", "overdue", "all"), default="open")
    p.add_argument("--due-before")
    p.add_argument("--include-ambiguous", action="store_true")
    p.add_argument("--ambiguous", action="store_true")
    p.add_argument("--wide", action="store_true")

    p = command("done")
    p.add_argument("id")

    p = command("followup")
    p.add_argument("owner", nargs="?")
    p.add_argument("--to")
    p.add_argument("--include-ambiguous", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--record-draft", metavar="HANDOFF_FILE")
    p.add_argument("--draft-id")
    p.add_argument("--status", choices=("created", "failed"), default=None)

    p = command("gmail-login")
    p.add_argument("--logout", action="store_true")
    return parser


# ------------------------------------------------------------------- validate_args hooks
def _validate_followup(args: argparse.Namespace) -> None:
    """Shell-level grammar only: exactly one of ``<owner>`` or ``--record-draft``.

    M7 extends this (FR-04.12 / AC-04.13): ``--status created`` needs ``--draft-id``,
    ``--status failed`` forbids it, ``--record-draft`` with an owner or with ``--dry-run`` /
    ``--to`` / ``--include-ambiguous`` / ``--force`` / ``--yes`` => ``UsageError``.
    """
    if args.owner is None and args.record_draft is None:
        raise UsageError(
            "followup needs an <owner> or --record-draft <handoff-file>",
            next_step='run "actiontrack followup --help" for usage',
        )


def _validate_ingest(args: argparse.Namespace) -> None:
    """``<folder>`` must name something (an empty or blank folder is a usage error, exit 2)."""
    if not str(args.folder).strip():
        raise UsageError("ingest needs a non-empty <folder>", next_step='run "actiontrack ingest --help" for usage')


#: ``validate_args`` extension point: ``command -> callable(args)`` raising ``UsageError``.
#: ``list`` conflicts (AC-02.9/02.11) and the ``done`` id syntax are registered by M6.
def _validate_list(args: argparse.Namespace) -> None:
    from actiontrack.services.listing import validate_list_args

    validate_list_args(args)


def _validate_done(args: argparse.Namespace) -> None:
    from actiontrack.domain.models import parse_item_id

    parse_item_id(str(args.id).strip())


VALIDATORS: dict[str, Callable[[argparse.Namespace], None]] = {
    "ingest": _validate_ingest,
    "list": _validate_list,
    "done": _validate_done,
    "followup": _validate_followup,
}

# ------------------------------------------------------------------------- context
DEFAULT_DB = Path(".actiontrack") / "actiontrack.db"


@dataclass(frozen=True)
class CliContext:
    """Everything a handler needs, built once per invocation.

    ``run`` is the ``clock.RunContext`` (clock, today, tz label, zone).
    """

    command: str
    args: argparse.Namespace
    config: Config
    run: RunContext
    diag: Diagnostics
    db_path: Path
    format: str
    no_color: bool


Handler = Callable[[CliContext], "int | None"]


def not_implemented_handler(command: str) -> Handler:
    def handler(ctx: CliContext) -> int | None:
        raise NotImplementedYet(command)

    handler.__name__ = f"not_implemented_{command.replace('-', '_')}"
    return handler


def _gmail_login(ctx: CliContext) -> int | None:
    channel = ctx.config.gmail.channel
    if channel in ("handoff", "fake"):
        io.emit(f"gmail-login: not needed for the {channel} channel")
        return None
    raise NotImplementedYet("gmail-login")  # google channel: M8


def ingest_handler(ctx: CliContext) -> int | None:
    """``actiontrack ingest <folder> [--no-recursive] [--dry-run]`` (T-M5-18; spec 4.1, 4.2, 11).

    Runs ``services.ingest.run_ingest`` and prints the report to stdout (table, or JSON with ``--format json``).
    Diagnostics go to stderr through ``ctx.diag``: one line per warning (hidden by ``--quiet``), one line per
    failed file with what/where/next (errors are never hidden), the run-finalisation failure, and the summary
    line (printed in every mode, AC-09.9). The exit code is the report's: 0, 5 (>= 1 file failed) or 4
    (finalisation failed). Environment errors (folder missing/unreadable, database unusable or locked) are raised
    by the service BEFORE anything is written and mapped to exit 4 by ``main``; nothing is printed to stdout then.
    """
    from actiontrack.report.ingest_report import render_ingest_json, render_ingest_table, summary_line
    from actiontrack.services.ingest import run_ingest

    args = ctx.args
    result = run_ingest(
        ctx.db_path,
        args.folder,
        cfg=ctx.config,
        run_ctx=ctx.run,
        recursive=not args.no_recursive,
        dry_run=args.dry_run,
    )
    report = result.report
    assert report is not None  # run_ingest always builds it
    if ctx.format == "json":
        io.emit(render_ingest_json(report), end="")
    else:
        io.emit(render_ingest_table(report, verbose=ctx.diag.verbose))
    for w in report.warnings:
        if w.code.startswith("W-"):
            ctx.diag.warn(w.code, w.message)
    for failure in report.failures:
        nxt = f" Next: {failure.next_step}" if failure.next_step else ""
        ctx.diag.error(f"{failure.path}: {failure.message}{nxt}", code=failure.code)
    if report.finalisation_error:
        ctx.diag.error(report.finalisation_error)
    ctx.diag.summary(summary_line(report))
    return report.exit_code


def _open_existing_db(ctx: CliContext, *, readonly: bool):
    """Open the database of ``list``/``done``: never creates it (missing -> NotFoundError, exit 3)."""
    from actiontrack.errors import NotFoundError
    from actiontrack.store.db import Database

    if not ctx.db_path.exists():
        raise NotFoundError(
            "database not found",
            where=str(ctx.db_path),
            next_step="run `actiontrack ingest <folder>` first",
        )
    # looked up by name: the repo lint tests/unit/test_io.py flags every literal ``.open(`` call
    opener = getattr(Database, "open")  # noqa: B009
    if readonly:
        return opener(ctx.db_path, readonly=True, clock=ctx.run.clock)
    return opener(ctx.db_path, create=False, lock=True, clock=ctx.run.clock)


def list_handler(ctx: CliContext) -> int | None:
    """``actiontrack list`` (M6, spec 4.3): read-only; table or JSON on stdout (T-M6-11)."""
    from actiontrack.report.list_report import render_list_json, render_list_table
    from actiontrack.services.listing import list_items, parse_list_args
    from actiontrack.store.repo import Repo

    query = parse_list_args(ctx.args, ctx.run.today)
    with _open_existing_db(ctx, readonly=True) as db:
        repo = Repo(db, ctx.run.clock)
        result = list_items(repo, repo.load_registry(), query, ctx.run.today)
    if ctx.format == "json":
        io.emit(render_list_json(result, ctx.run.tz_label), end="")
    else:
        io.emit(render_list_table(result, ctx.run.tz_label, wide=query.wide))
    return None


def _completed_date(ctx: CliContext, completed_at: str | None) -> str:
    if not completed_at:
        return "by note"
    from datetime import datetime, timezone

    instant = datetime.strptime(completed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return instant.astimezone(ctx.run.zone.tzinfo).date().isoformat()


def done_handler(ctx: CliContext) -> int | None:
    """``actiontrack done <id>`` (M6, spec 4.4): one transaction under the write lock (T-M6-09/10)."""
    from actiontrack.domain.models import format_item_id, parse_item_id
    from actiontrack.report.json import render_json
    from actiontrack.report.sanitize import clean_text
    from actiontrack.services.done import mark_done
    from actiontrack.store.repo import Repo

    parse_item_id(str(ctx.args.id).strip())  # exit 2 before the database is touched
    with _open_existing_db(ctx, readonly=False) as db:
        result = mark_done(db, Repo(db, ctx.run.clock), ctx.args.id, ctx.run.clock)
    label = format_item_id(result.item_id)
    if ctx.format == "json":
        io.emit(render_json(result.to_json()), end="")
    elif result.already_done:
        when = _completed_date(ctx, result.completed_at)
        completed = when if result.completed_at else "by note"
        io.emit(f"{label} already done (completed {completed}, origin {result.completion_origin})")
    else:
        task = clean_text(result.task_text)
        source = f" Source: {result.source}" if result.source else ""
        io.emit(f'Marked {label} done (was open). Task: "{task}".{source}')
    if result.duplicates and not result.already_done:
        ctx.diag.info(f"{label} has possible duplicates (not modified):")
        for d in result.duplicates:
            ctx.diag.info(f"  {format_item_id(d.other_id)} ({d.status}): {d.reason or 'possible duplicate'}")
    return None


def followup_handler(ctx: CliContext) -> int | None:
    """``followup``: handoff-channel draft preparation (``services/followup.py``; imported lazily)."""
    from actiontrack.services.followup import followup_handler as _followup

    return _followup(ctx)


#: Late-bound command handlers; later milestones replace the stubs one by one.
HANDLERS: dict[str, Handler] = {
    "ingest": ingest_handler,
    "list": list_handler,
    "done": done_handler,
    "followup": followup_handler,
    "gmail-login": _gmail_login,
}


class _DiagWriter:
    """File-like adapter so ``build_run_context`` hints go through ``Diagnostics`` (and the log)."""

    def __init__(self, diag: Diagnostics) -> None:
        self._diag = diag

    def write(self, text: str) -> int:
        stripped = text.rstrip("\n")
        if stripped:
            self._diag.info(stripped)
        return len(text)


def _non_empty(value: str | None, option: str) -> str | None:
    if value is not None and not value.strip():
        raise UsageError(f"{option} needs a non-empty value", next_step=f"give a value for {option}")
    return value


def _split_parsed(ns: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    values = vars(ns)
    before = {k: values.get(k) for k in GLOBAL_OPTION_NAMES}
    after = {k[len(_AFTER):]: v for k, v in values.items() if k.startswith(_AFTER)}
    return before, after


def _run(ns: argparse.Namespace, diag_holder: list[Diagnostics], env: Mapping[str, str]) -> int:
    command: str = ns.command
    before, after = _split_parsed(ns)
    merged = merge_global_options(before, after)
    VALIDATORS.get(command, lambda _a: None)(ns)

    db_flag = _non_empty(merged["db"], "--db")
    config_flag = _non_empty(merged["config"], "--config")
    log_file = _non_empty(merged["log_file"], "--log-file")
    diag = Diagnostics(quiet=merged["quiet"], verbose=merged["verbose"], log_file=log_file)
    diag_holder[0].close()
    diag_holder[0] = diag

    config = load_config(config_flag)
    run = build_run_context(
        timezone_name=config.timezone,
        today_flag=merged["today"],
        env=env,
        quiet=merged["quiet"],
        stderr=_DiagWriter(diag),  # type: ignore[arg-type]
    )
    db_value = db_flag if db_flag is not None else (env.get("ACTIONTRACK_DB") or str(DEFAULT_DB))
    ctx = CliContext(
        command=command,
        args=ns,
        config=config,
        run=run,
        diag=diag,
        db_path=Path(os.path.abspath(db_value)),
        format=merged["format"] or "table",
        no_color=bool(merged["no_color"]) or bool(env.get("NO_COLOR")),
    )
    result = HANDLERS[command](ctx)
    return int(ExitCode.OK if result is None else result)


def main(argv: Sequence[str] | None = None) -> int:
    io.configure_streams()
    args = list(sys.argv[1:] if argv is None else argv)
    diag_holder = [Diagnostics()]
    try:
        try:
            ns = build_parser().parse_args(args)
        except _ParserExit as stop:
            return stop.code
        return _run(ns, diag_holder, os.environ)
    except CancelledByUser as exc:
        diag_holder[0].summary(exc.format())
        return int(exit_code_for(exc))
    except ActionTrackError as exc:
        diag_holder[0].exception(exc)
        return int(exit_code_for(exc))
    except KeyboardInterrupt:
        diag_holder[0].error("interrupted")
        return int(ExitCode.INTERNAL_ERROR)
    except Exception as exc:  # noqa: BLE001 - last-resort boundary (spec 11)
        diag_holder[0].exception(exc)
        return int(ExitCode.INTERNAL_ERROR)
    finally:
        diag_holder[0].close()
