"""T-M1-16: M1 exit gate. End-to-end smoke through the real CLI in subprocesses.

Every test here runs ``python -m actiontrack ...`` as a separate process from an empty temp
directory, with a cp1252 console forced (``PYTHONIOENCODING=cp1252``), the Appendix C roster
(``tests/fixtures/config/valid_full.toml``) and ``--today 2026-10-22`` unless the test is about
the clock itself. Subprocess environments carry APPDATA/LOCALAPPDATA/USERPROFILE so the
user-site ``tzdata`` package is found on Windows.

M1 walking skeleton - what exists now and what M2+ replace
----------------------------------------------------------
Real in M1: argparse shell + global option merge (H-03), exit-code mapping, UTF-8/LF streams,
``--help``/``--version``, diagnostics (``--quiet``/``--verbose``/``--log-file``), config +
roster validation, clock/zone/``--today``/``ACTIONTRACK_NOW``, SQLite schema + migrations,
``<db>.lock`` primitive, ``gmail-login`` no-op for the handoff/fake channels.

Stubs (``cli.HANDLERS``; each raises ``NotImplementedYet``, exit 1, message
"<command>: not implemented in this build"):
  * ``ingest``    -> M5 (services/ingest.py; parsing in M2, dates M3, owners M4)
  * ``list``      -> M6 (services/listing.py; list-conflict validators also registered in M6)
  * ``done``      -> M6 (services/done.py)
  * ``followup``  -> M7 (services/followup.py; handoff gateway), ``--record-draft`` grammar in M7
  * ``gmail-login`` for ``channel = "google"`` -> M8 (user-gated)
The stubs never open the database, so no M1 command creates ``.actiontrack/``.

Tests that need the "current date" (SC-37) install a one-line ``list`` handler through a tiny
driver script that still enters through ``cli.main()``; M6 replaces that driver with the real
``list`` output.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import FIXTURES_NOTES, REPO_ROOT, SAMPLES_NOTES, snapshot_tree

CONFIG_DIR = REPO_ROOT / "tests" / "fixtures" / "config"
ROSTER = CONFIG_DIR / "valid_full.toml"  # Appendix C roster
GOLDEN = REPO_ROOT / "tests" / "golden"
TODAY = "2026-10-22"
COMMANDS = ("ingest", "list", "done", "followup", "gmail-login")
M1_ACS = (
    "AC-09.1", "AC-09.2", "AC-09.3", "AC-09.9", "AC-09.10", "AC-09.11",
    "AC-05.12", "AC-08.9", "AC-09.6", "AC-09.8", "AC-01.10", "AC-01.11",
)

# argv for a syntactically valid call of every command
VALID_ARGV = {
    "ingest": ["ingest", "notes"],
    "list": ["list"],
    "done": ["done", "AT-0001"],
    "followup": ["followup", "Priya Shah"],
    "gmail-login": ["gmail-login"],
}

DRIVER_DATE = """\
import sys
from actiontrack import cli, io
def handler(ctx):
    io.emit(f"today={ctx.run.today} tz={ctx.run.tz_label} fmt={ctx.format} "
            f"now={ctx.run.clock.now_utc().isoformat()}")
cli.HANDLERS["list"] = handler
sys.exit(cli.main())
"""

DRIVER_ROSTER = """\
import sys
from actiontrack import cli, io
def handler(ctx):
    for p in sorted(ctx.config.roster, key=lambda p: p.full_name):
        io.emit(f"{p.full_name}|{p.person_id}|{p.qualifier or ''}")
    io.emit(f"count={len(ctx.config.roster)}")
cli.HANDLERS["list"] = handler
sys.exit(cli.main())
"""

DRIVER_CHATTY = """\
import sys
from actiontrack import cli, codes, io
from actiontrack.errors import EnvError
def handler(ctx):
    ctx.diag.warn(codes.W_EMPTY_FILE, "WARN-TEXT", file="a.md", line=2)
    ctx.diag.info("INFO-TEXT")
    ctx.diag.debug("DEBUG-TEXT")
    io.emit("RESULT-TEXT")
    ctx.diag.summary("SUMMARY-TEXT")
    raise EnvError("ERROR-TEXT")
cli.HANDLERS["list"] = handler
sys.exit(cli.main())
"""


# ------------------------------------------------------------------------- harness
def child_env(**extra: str) -> dict[str, str]:
    env = {
        k: os.environ[k]
        for k in ("SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP", "HOMEDRIVE", "HOMEPATH")
        if k in os.environ
    }
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PYTHONIOENCODING"] = "cp1252"  # cp1252 console: the CLI must still emit UTF-8
    env.update(extra)
    return env


class Result:
    def __init__(self, proc: subprocess.CompletedProcess[bytes]) -> None:
        self.rc = proc.returncode
        self.out_bytes = proc.stdout
        self.err_bytes = proc.stderr
        self.out = proc.stdout.decode("utf-8")
        self.err = proc.stderr.decode("utf-8")

    def triple(self) -> tuple[int, bytes, bytes]:
        return self.rc, self.out_bytes, self.err_bytes


def run_cli(cwd: Path, *argv: str, driver: str | None = None, env: dict[str, str] | None = None) -> Result:
    cmd = [sys.executable, "-m", "actiontrack", *argv]
    if driver is not None:
        cmd = [sys.executable, "-c", driver, *argv]
    proc = subprocess.run(
        cmd, capture_output=True, cwd=cwd, env=env if env is not None else child_env(), timeout=120
    )
    return Result(proc)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    d = tmp_path / "empty"
    d.mkdir()
    return d


@pytest.fixture(scope="module", autouse=True)
def samples_untouched_by_module():
    trees = (SAMPLES_NOTES, REPO_ROOT / "notes" / "notes", FIXTURES_NOTES)
    before = [snapshot_tree(t) for t in trees]
    assert all(before), "sample trees must exist and be non-empty"
    yield
    after = [snapshot_tree(t) for t in trees]
    assert before == after, "BR-01: the M1 smoke run changed a sample/fixture notes tree"


# ------------------------------------------------------------------ AC-09.1 / AC-09.2
@pytest.mark.ac("AC-09.2")
def test_version_and_help_exit0_stdout_only_and_create_nothing(work):
    r = run_cli(work, "--version")
    assert (r.rc, r.err) == (0, "") and re.fullmatch(r"actiontrack \S+\n", r.out)
    assert b"\r" not in r.out_bytes

    r = run_cli(work, "--help")
    assert (r.rc, r.err) == (0, "")
    assert r.out_bytes == (GOLDEN / "help_program.txt").read_bytes()  # byte-exact, LF only
    assert list(work.iterdir()) == []


@pytest.mark.ac("AC-09.2")
@pytest.mark.parametrize("command", COMMANDS)
def test_each_command_help_has_synopsis_options_exit_codes_example(work, command):
    for argv in ([command, "--help"], [command, "-h"], ["--format", "json", command, "--help"],
                 [command, "--quiet", "--help"]):
        r = run_cli(work, *argv)
        assert (r.rc, r.err) == (0, ""), argv
        assert r.out_bytes == (GOLDEN / f"help_{command}.txt").read_bytes(), argv
        for label in ("Synopsis:", "Options:", "Exit codes:", "Example:"):
            assert f"\n{label}\n" in "\n" + r.out
        assert "ACTIONTRACK_NOW" not in r.out  # AC-09.3: test-only variable stays undocumented
    assert list(work.iterdir()) == []


@pytest.mark.ac("AC-09.3")
def test_program_help_does_not_mention_actiontrack_now(work):
    assert "ACTIONTRACK_NOW" not in run_cli(work, "--help").out


@pytest.mark.ac("AC-09.1")
@pytest.mark.parametrize(
    "fixture, key_text, exit_code",
    [
        ("bad_unknown_key.toml", 'key "limits.max_file_byte"', 4),
        ("bad_syntax.toml", "invalid TOML", 4),
        ("bad_types.toml", 'key "limits.max_file_bytes"', 4),
        ("bad_google_host.toml", 'key "gmail.google_mcp_url"', 4),  # AC-09.10 config half
        ("bad_duplicate_person.toml", "Priya Nair", 4),  # AC-05.12
    ],
)
def test_invalid_config_exits_4_naming_key(work, fixture, key_text, exit_code):
    r = run_cli(work, "--config", str(CONFIG_DIR / fixture), "--today", TODAY, "gmail-login")
    assert r.rc == exit_code
    assert r.out == ""  # nothing on stdout
    assert key_text in r.err and fixture in r.err
    assert "Traceback" not in r.err
    # same verdict for a command that is a stub, and with the option after the command
    r2 = run_cli(work, "list", "--config", str(CONFIG_DIR / fixture))
    assert (r2.rc, r2.out) == (exit_code, "")
    assert list(work.iterdir()) == []


@pytest.mark.ac("AC-09.1")
def test_missing_explicit_config_exits_4_but_help_still_works(work):
    r = run_cli(work, "--config", str(work / "nope.toml"), "list")
    assert r.rc == 4 and "nope.toml" in r.err and r.out == ""
    assert run_cli(work, "--config", str(work / "nope.toml"), "--help").rc == 0


@pytest.mark.ac("AC-09.10")
def test_foreign_google_host_rejected_even_with_lookalike_hosts(work):
    for url in (
        "http://gmailmcp.googleapis.com/mcp/v1",  # not https
        "https://gmailmcp.googleapis.com.evil.example/",
        "https://evil.example/gmailmcp.googleapis.com",
        "https://user@evil.example/",
    ):
        cfg = work / "g.toml"
        cfg.write_text(f'[gmail]\ngoogle_mcp_url = "{url}"\n', encoding="utf-8")
        r = run_cli(work, "--config", str(cfg), "gmail-login")
        assert r.rc == 4 and "gmail.google_mcp_url" in r.err, url


@pytest.mark.ac("AC-05.12")
def test_appendix_c_roster_loads_with_unicode_under_cp1252_console(work):
    r = run_cli(work, "--config", str(ROSTER), "--today", TODAY, "list", driver=DRIVER_ROSTER)
    assert (r.rc, r.err) == (0, "")
    lines = r.out.splitlines()
    assert lines[-1] == "count=9"
    names = [ln.split("|")[0] for ln in lines[:-1]]
    assert "Priya Nair" in names and "Priya Shah" in names and "Rahul Mehta" in names

    uni = work / "uni.toml"
    uni.write_text(
        'timezone = "Asia/Kolkata"\n'
        '[[person]]\nfirst_name = "Zoë"\nlast_name = "Åström"\n'
        '[[person]]\nfirst_name = "雷"\nlast_name = "李"\n',
        encoding="utf-8",
    )
    r = run_cli(work, "--config", str(uni), "list", driver=DRIVER_ROSTER)
    assert (r.rc, r.err) == (0, ""), r.err
    assert "Zoë Åström|" in r.out and "雷 李|" in r.out
    assert b"\r" not in r.out_bytes
    r.out_bytes.decode("utf-8")  # strictly valid UTF-8 even though the console was cp1252


@pytest.mark.ac("AC-05.12")
def test_duplicate_identity_rejected_unless_distinct_qualifiers(work):
    dup = work / "dup.toml"
    dup.write_text(
        'timezone = "Asia/Kolkata"\n'
        '[[person]]\nfirst_name = "Priya"\nlast_name = "Nair"\n'
        '[[person]]\nfirst_name = "Rahul"\nlast_name = "Mehta"\n'
        '[[person]]\nfirst_name = "Priya"\nlast_name = "Nair"\n',
        encoding="utf-8",
    )
    r = run_cli(work, "--config", str(dup), "gmail-login")
    assert r.rc == 4 and "person[0]" in r.err and "person[2]" in r.err and "Priya Nair" in r.err

    ok = work / "ok.toml"
    ok.write_text(
        'timezone = "Asia/Kolkata"\n'
        '[[person]]\nfirst_name = "Priya"\nlast_name = "Nair"\nqualifier = "Finance"\n'
        '[[person]]\nfirst_name = "Priya"\nlast_name = "Nair"\nqualifier = "Sales"\n',
        encoding="utf-8",
    )
    r = run_cli(work, "--config", str(ok), "list", driver=DRIVER_ROSTER)
    assert (r.rc, r.err) == (0, "")
    ids = [ln.split("|")[1] for ln in r.out.splitlines() if "|" in ln]
    assert len(ids) == 2 and len(set(ids)) == 2 and r.out.splitlines()[-1] == "count=2"


# ------------------------------------------------------------------ exit codes / streams
@pytest.mark.parametrize(
    "argv, rc",
    [
        (["--today", "2026-02-30", "gmail-login"], 2),
        (["--today", "22/10/2026", "gmail-login"], 2),
        (["gmail-login", "--today", "tomorrow"], 2),
        (["--quiet", "--verbose", "gmail-login"], 2),
        (["gmail-login", "--verbose", "--quiet"], 2),
        (["bogus"], 2),
        ([], 2),
        (["--format", "xml", "list"], 2),
        (["list", "--status", "bogus"], 2),
        (["done"], 2),
        (["ingest"], 2),
        (["followup"], 2),
        (["--db", "", "list"], 2),
    ],
)
def test_usage_errors_exit2_stderr_only(work, argv, rc):
    r = run_cli(work, *argv)
    assert r.rc == rc and r.out == ""
    assert r.err.startswith("error: ") and "Traceback" not in r.err
    assert list(work.iterdir()) == []


@pytest.mark.parametrize("command", ["ingest", "list", "done", "followup"])
def test_stub_commands_exit1_clean_message_and_touch_nothing(work, command):
    db = work / "sub" / "actiontrack.db"  # a --db whose folder does not exist: stubs must not create it
    argv = [*VALID_ARGV[command], "--config", str(ROSTER), "--today", TODAY, "--db", str(db)]
    r = run_cli(work, *argv)
    assert r.rc == 1 and r.out == ""
    assert f"{command}: not implemented in this build" in r.err
    assert "Traceback" not in r.err
    assert list(work.iterdir()) == []  # no DB, no .actiontrack, no lock file


def test_gmail_login_handoff_channel_is_noop_exit0(work):
    r = run_cli(work, "--config", str(ROSTER), "--today", TODAY, "gmail-login")
    assert (r.rc, r.err) == (0, "")
    assert r.out == "gmail-login: not needed for the handoff channel\n"
    assert list(work.iterdir()) == []


@pytest.mark.ac("AC-09.11")
def test_format_json_before_after_and_last_wins_in_subprocess(work):
    base = ["--config", str(ROSTER), "--today", TODAY]
    for command in COMMANDS:
        tail = VALID_ARGV[command]
        before = run_cli(work, *base, "--format", "json", *tail)
        after = run_cli(work, *base, *tail, "--format", "json")
        assert before.triple() == after.triple(), command

    def fmt(*argv: str) -> str:
        r = run_cli(work, "--config", str(ROSTER), *argv, "list", driver=DRIVER_DATE)
        assert r.rc == 0, r.err
        return re.search(r"fmt=(\w+)", r.out).group(1)

    assert fmt("--format", "json") == "json"
    assert fmt() == "table"

    def run_list(*argv: str) -> Result:
        return run_cli(work, "--config", str(ROSTER), *argv, driver=DRIVER_DATE)

    assert "fmt=table" in run_list("--format", "json", "list", "--format", "table").out
    assert "fmt=json" in run_list("--format", "table", "list", "--format", "json").out
    assert "today=2026-10-22" in run_list("--today", "2026-10-01", "list", "--today", "2026-10-22").out
    assert "today=2026-10-22" in run_list("--today", "2026-10-01", "--today", "2026-10-22", "list").out
    # --format and --today are accepted and ignored by gmail-login (valid values)
    base_out = run_cli(work, "--config", str(ROSTER), "gmail-login")
    assert run_cli(work, "--config", str(ROSTER), "--format", "json", "gmail-login").triple() == base_out.triple()
    assert run_cli(work, "--config", str(ROSTER), "gmail-login", "--format", "json").triple() == base_out.triple()
    assert list(work.iterdir()) == []


@pytest.mark.ac("AC-09.9")
def test_quiet_and_log_file_against_stub_command(work):
    log = work / "run.log"
    plain = run_cli(work, "--today", TODAY, "list")  # no timezone configured -> hint + stub error
    assert plain.rc == 1 and "note: " in plain.err and "not implemented in this build" in plain.err

    quiet = run_cli(work, "--today", TODAY, "--quiet", "list", "--log-file", str(log))
    assert quiet.rc == 1 and quiet.out == ""
    assert "note: " not in quiet.err  # --quiet hides the info hint
    assert "list: not implemented in this build" in quiet.err  # errors are kept
    text = log.read_bytes().decode("utf-8")
    assert text == quiet.err.replace("\r", "") and "\r" not in text  # log == stderr, LF, UTF-8

    again = run_cli(work, "--today", TODAY, "--quiet", "--log-file", str(log), "list")
    assert log.read_text(encoding="utf-8") == quiet.err + again.err  # appended, not truncated


@pytest.mark.ac("AC-09.9")
def test_quiet_keeps_summary_and_errors_never_result_data(work):
    cfg = ["--config", str(ROSTER), "--today", TODAY]
    normal = run_cli(work, *cfg, "list", driver=DRIVER_CHATTY)
    assert normal.rc == 4 and normal.out == "RESULT-TEXT\n"
    for t in ("WARN-TEXT", "INFO-TEXT", "SUMMARY-TEXT", "ERROR-TEXT"):
        assert t in normal.err
    assert "DEBUG-TEXT" not in normal.err
    for argv in ([*cfg, "--quiet", "list"], [*cfg, "list", "--quiet"]):
        q = run_cli(work, *argv, driver=DRIVER_CHATTY)
        assert q.rc == 4 and q.out == "RESULT-TEXT\n"
        assert "WARN-TEXT" not in q.err and "INFO-TEXT" not in q.err
        assert "SUMMARY-TEXT" in q.err and "ERROR-TEXT" in q.err
    v = run_cli(work, *cfg, "--verbose", "list", driver=DRIVER_CHATTY)
    assert "DEBUG-TEXT" in v.err and v.out == "RESULT-TEXT\n"


@pytest.mark.ac("AC-09.9")
def test_unopenable_log_file_exit4_before_work_but_help_wins(work):
    for bad in (work, work / "missing-dir" / "x.log"):
        r = run_cli(work, "--config", str(ROSTER), "--log-file", str(bad), "list", driver=DRIVER_CHATTY)
        assert r.rc == 4 and r.out == "" and "log file" in r.err
        assert "WARN-TEXT" not in r.err  # handler never ran
    assert run_cli(work, "--log-file", str(work), "--help").rc == 0
    assert run_cli(work, "--log-file", str(work), "--version").rc == 0
    both = work / "never.log"
    r = run_cli(work, "--quiet", "--verbose", "--log-file", str(both), "list")
    assert r.rc == 2 and "--quiet" in r.err and "--verbose" in r.err and not both.exists()


# ------------------------------------------------------------------------ AC-09.3 clock
def _today(work: Path, tz: str | None, now: str | None, *extra: str) -> Result:
    cfg = work / "tz.toml"
    cfg.write_text(f'timezone = "{tz}"\n' if tz else "", encoding="utf-8")
    env = child_env(**({"ACTIONTRACK_NOW": now} if now else {}))
    return run_cli(work, "--config", str(cfg), *extra, "list", driver=DRIVER_DATE, env=env)


@pytest.mark.ac("AC-09.3")
def test_today_from_clock_in_configured_zone(work):
    r = _today(work, "Asia/Kolkata", "2026-10-21T20:00:00Z")
    assert (r.rc, r.err) == (0, "")
    assert "today=2026-10-22 tz=Asia/Kolkata" in r.out
    r = _today(work, "America/Los_Angeles", "2026-10-21T20:00:00Z")
    assert "today=2026-10-21 tz=America/Los_Angeles" in r.out


@pytest.mark.ac("AC-09.3")
@pytest.mark.sc("SC-37")
def test_sc37_los_angeles_before_utc_midnight_rollover(work):
    r = _today(work, "America/Los_Angeles", "2026-10-22T03:00:00Z")
    assert r.rc == 0 and "today=2026-10-21" in r.out


@pytest.mark.ac("AC-09.3")
def test_today_flag_overrides_date_not_timestamps(work):
    r = _today(work, "Asia/Kolkata", "2026-10-21T20:00:00Z", "--today", "2026-10-25")
    assert "today=2026-10-25" in r.out
    assert "now=2026-10-21T20:00:00+00:00" in r.out  # stored timestamps keep the clock instant


@pytest.mark.ac("AC-09.3")
def test_no_timezone_uses_os_offset_label_and_hint(work):
    now = "2026-10-21T20:00:00Z"
    r = _today(work, None, now)
    assert r.rc == 0
    m = re.search(r"today=(\S+) tz=(UTC(?:([+-])(\d\d):(\d\d))?) ", r.out)
    assert m, r.out
    sign, hh, mm = m.group(3), m.group(4), m.group(5)
    offset = timedelta(0) if sign is None else timedelta(hours=int(hh), minutes=int(mm)) * (1 if sign == "+" else -1)
    expected = datetime(2026, 10, 21, 20, tzinfo=UTC).astimezone(timezone(offset)).date().isoformat()
    assert m.group(1) == expected  # "today" uses that same fixed offset
    assert r.err.count("timezone") >= 1 and r.err.count("\n") == 1  # exactly one hint line
    quiet = _today(work, None, now, "--quiet")
    assert quiet.err == ""


@pytest.mark.ac("AC-09.3")
def test_unknown_timezone_name_is_config_error_exit4(work):
    r = _today(work, "Mars/Olympus", None)
    assert r.rc == 4 and "timezone" in r.err and r.out == ""


@pytest.mark.ac("AC-09.3")
def test_invalid_actiontrack_now_is_usage_error(work):
    r = _today(work, "Asia/Kolkata", "yesterday")
    assert r.rc == 2 and "ACTIONTRACK_NOW" in r.err and r.out == ""


# ------------------------------------------------------------- AC-08.9 lock primitive
HOLDER = """\
import sys
from actiontrack.store.lock import WriteLock
lock = WriteLock(sys.argv[1]).acquire()
print("READY", flush=True)
sys.stdin.readline()
"""

CONTENDER = """\
import sys
from actiontrack.errors import ActionTrackError
from actiontrack.store.lock import WriteLock
try:
    WriteLock(sys.argv[1]).acquire().release()
except ActionTrackError as exc:
    print(int(exc.exit_code), flush=True)
    sys.exit(int(exc.exit_code))
print("ACQUIRED", flush=True)
"""


@pytest.mark.ac("AC-08.9")
@pytest.mark.sc("SC-67")
def test_lock_second_process_exits_4_then_acquires_after_holder_killed(work):
    db = work / "a.db"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(db)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        env=child_env(), cwd=work,
    )
    try:
        assert holder.stdout.readline().strip() == b"READY"
        blocked = subprocess.run([sys.executable, "-c", CONTENDER, str(db)], capture_output=True,
                                 env=child_env(), cwd=work, timeout=60)
        assert blocked.returncode == 4 and blocked.stdout.strip() == b"4"
        holder.kill()
        holder.wait(timeout=30)
        freed = subprocess.run([sys.executable, "-c", CONTENDER, str(db)], capture_output=True,
                               env=child_env(), cwd=work, timeout=60)
        assert freed.returncode == 0 and freed.stdout.strip() == b"ACQUIRED"
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=30)


# ------------------------------------------------------------------ clean state / BR-01
@pytest.mark.ac("AC-01.11")
def test_no_command_creates_files_in_an_empty_directory(work):
    invocations = [["--version"], ["--help"]]
    for command in COMMANDS:
        invocations += [[command, "--help"], VALID_ARGV[command], [*VALID_ARGV[command], "--format", "json", "--quiet"]]
    for argv in invocations:
        run_cli(work, *argv)
        assert list(work.iterdir()) == [], argv
    # only an explicitly requested log file may appear
    log = work / "only.log"
    run_cli(work, "list", "--log-file", str(log))
    assert [p.name for p in work.iterdir()] == ["only.log"]


@pytest.mark.ac("AC-01.11", "AC-01.10")
def test_sample_folders_unchanged_by_cli_runs(work):
    trees = (SAMPLES_NOTES, REPO_ROOT / "notes" / "notes", FIXTURES_NOTES)
    before = [snapshot_tree(t) for t in trees]
    for t in trees:
        run_cli(work, "ingest", str(t), "--config", str(ROSTER), "--today", TODAY, "--db", str(work / "x.db"))
    assert [snapshot_tree(t) for t in trees] == before
    assert list(work.iterdir()) == []


@pytest.mark.ac("AC-09.8")
def test_control_characters_in_diagnostics_are_not_emitted_raw(work):
    r = run_cli(work, "ingest", "no\x1b[31mfolder", "--db", "ok\x07.db", "--format", "json")
    assert b"\x1b" not in r.out_bytes + r.err_bytes and b"\x07" not in r.err_bytes
    bad = run_cli(work, "--today", "\x1b[31mred", "list")
    assert bad.rc == 2 and b"\x1b" not in bad.err_bytes


# ------------------------------------------------------------------ marker coverage
@pytest.mark.parametrize("ac_id", M1_ACS)
def test_every_m1_acceptance_criterion_has_a_marked_test(ac_id):
    """Marker coverage (T-M1-16): each M1 AC is claimed by at least one other test module."""
    pattern = re.compile(r"pytest\.mark\.ac\(([^)]*)\)")
    found: set[str] = set()
    for path in (REPO_ROOT / "tests").rglob("test_*.py"):
        if path.name == Path(__file__).name:
            continue
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            found.update(re.findall(r'"(AC-\d{2}\.\d+)"', m.group(1)))
    assert ac_id in found, f"{ac_id} has no marked test outside {Path(__file__).name}"
