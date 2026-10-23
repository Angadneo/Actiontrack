"""T-M5-16: rendering the ingest report as table and JSON, with golden files (spec 4.2, FR-01.7, AC-01.7, AC-09.4,
AC-09.8, SEC-08).

Goldens live in ``tests/golden/ingest_*.txt|json``. Per AC-09.4 byte-identity is asserted EXCLUDING the run number and
the absolute ``folder=`` value (and the run timestamps in JSON): every golden is compared after
``mask_volatile_table`` / ``mask_volatile_json``.

Regenerating (manual, only after reviewing the new content against spec 4.2)::

    ACTIONTRACK_REGEN_GOLDEN=1 python -m pytest tests/unit/test_report_render.py -k golden

Goldens are written with LF newlines (UTF-8).
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import pytest

from actiontrack.domain.models import (
    DueIssueRow,
    DuplicatePairRow,
    FailureRow,
    IngestReport,
    OwnerIssueRow,
    OwnerReResolutionRow,
    ReportFileCounts,
    ReportItemCounts,
    WarningRow,
)
from actiontrack.report.ingest_report import (
    ingest_report_dict,
    mask_volatile_json,
    mask_volatile_table,
    render_ingest_json,
    render_ingest_table,
    summary_line,
)
from actiontrack.services import ingest as ing

from tests.conftest import REPO_ROOT
from tests.integration.ingest_support import roster_cfg, run_ctx
from tests.integration.whole_db_support import copy_sample, make_cfg

GOLDEN = REPO_ROOT / "tests" / "golden"
REGEN = os.environ.get("ACTIONTRACK_REGEN_GOLDEN") == "1"


def check_golden(name: str, text: str) -> None:
    path = GOLDEN / name
    data = text if text.endswith("\n") else text + "\n"
    if REGEN:
        path.write_bytes(data.encode("utf-8"))
    assert path.read_bytes().decode("utf-8") == data, f"{name} differs (ACTIONTRACK_REGEN_GOLDEN=1 to regenerate)"
    assert b"\r" not in path.read_bytes()


def spec_report(folder: str = "C:\\notes", run_id: int = 7) -> IngestReport:
    """The spec 4.2 example, as far as the five sections go (counts in headings are state counts of the rows)."""
    return IngestReport(
        run_id=run_id,
        status="partial",
        dry_run=False,
        folder=folder,
        recursive=True,
        as_of=date(2026, 10, 22),
        timezone="Asia/Kolkata",
        started_at="2026-10-22T09:30:00Z",
        finished_at="2026-10-22T09:30:01Z",
        files=ReportFileCounts(34, 31, 0, 1, 2),
        items=ReportItemCounts(new=58, updated=0, unchanged=0, merged_duplicates=6, possible_duplicates=4, already_done_in_note=9),
        resolved_via_attendance=3,
        unresolved_owners=(
            OwnerIssueRow(4, "notes/2026-10-07-sprint.md:18", 0, "unassigned", None, "- [ ] Send revised quote by Friday"),
            OwnerIssueRow(
                11, "notes/2026-10-09-sync.md:22", 0, "ambiguous", "Priya", "- [ ] Priya to draft the memo",
                ("Priya Nair", "Priya Shah"),
            ),
        ),
        missing_due_dates=(
            DueIssueRow(6, "notes/2026-10-07-sprint.md:20", 0, "no_date", "- [ ] Sam to tidy the wiki"),
            DueIssueRow(
                13, "notes/2026-10-09-sync.md:30", 0, "unresolved", "- [ ] Karan to plan next week", "next week", "vague"
            ),
        ),
        possible_duplicate_pairs=(
            DuplicatePairRow(
                21, 8, "due_differs", "same task+owner, due differs (2026-10-23 vs 2026-10-16)",
                "2026-10-23", "2026-10-16", "Send the revised quote",
            ),
        ),
        failures=(
            FailureRow("notes/2026-09-30-board.md", "E-ENCODING", "not valid UTF-8 at byte 1042; skipped", "re-save as UTF-8"),
            FailureRow("notes/old-binary.md", "E-BINARY", "contains NUL bytes; skipped", None),
        ),
        warnings=(
            WarningRow("W-MEETING-DATE-MISSING", "notes/untitled.md", None, "notes/untitled.md: no meeting date found"),
            WarningRow("W-NONCHECKBOX-CANDIDATES", "notes/x.md", 22, "notes/x.md:22: possible action line is not a checkbox"),
        ),
        exit_code=5,
        exit_reason="2 files failed",
    )


def empty_report(**kw) -> IngestReport:
    base = dict(
        run_id=1, status="complete", dry_run=False, folder="/tmp/n", recursive=True, as_of=date(2026, 10, 22),
        timezone="UTC", started_at="2026-10-22T09:30:00Z", finished_at="2026-10-22T09:30:00Z",
        files=ReportFileCounts(), items=ReportItemCounts(), resolved_via_attendance=0,
        unresolved_owners=(), missing_due_dates=(), possible_duplicate_pairs=(), failures=(), warnings=(),
    )
    base.update(kw)
    return IngestReport(**base)


# ----------------------------------------------------------------------------- hand-built report
@pytest.mark.ac("AC-01.7", "AC-09.4")
def test_golden_spec_example_table_and_json():
    r = spec_report()
    table = mask_volatile_table(render_ingest_table(r))
    check_golden("ingest_spec_example.txt", table)
    check_golden("ingest_spec_example.json", mask_volatile_json(render_ingest_json(r)))
    assert table.splitlines()[0] == "Ingest run #<run>  folder=<folder>  as-of 2026-10-22".replace("#<run>", "#<run>")


@pytest.mark.ac("AC-09.4")
def test_windows_and_posix_inputs_give_byte_identical_output_after_masking():
    win = spec_report("C:\\notes", 7)
    posix = spec_report("/home/anna/notes", 9)
    assert render_ingest_table(win) != render_ingest_table(posix)  # only the two masked fields differ
    assert mask_volatile_table(render_ingest_table(win)) == mask_volatile_table(render_ingest_table(posix))
    assert mask_volatile_json(render_ingest_json(win)) == mask_volatile_json(render_ingest_json(posix))


@pytest.mark.ac("AC-01.7")
def test_table_follows_the_spec_layout():
    lines = render_ingest_table(spec_report()).splitlines()
    assert lines[0] == "Ingest run #7  folder=C:\\notes  as-of 2026-10-22"
    assert lines[1] == "Files: discovered 34 | ingested 31 | unchanged 0 | empty 1 | failed 2"
    assert lines[2] == (
        "Items: new 58 | updated 0 | unchanged 0 | merged-duplicates 6 | possible-duplicates 4 "
        "| already-done-in-note 9"
    )
    assert "Unresolved owners (2):" in lines
    assert '  AT-0004  notes/2026-10-07-sprint.md:18  no owner found: "- [ ] Send revised quote by Friday"' in lines
    assert '  AT-0011  notes/2026-10-09-sync.md:22    ambiguous: "Priya" -> Priya Nair | Priya Shah' in lines
    assert "  AT-0006  notes/2026-10-07-sprint.md:20  no date" in lines
    assert '  AT-0013  notes/2026-10-09-sync.md:30    unresolved "next week" (vague)' in lines
    assert "  AT-0021 ~ AT-0008  same task+owner, due differs (2026-10-23 vs 2026-10-16)" in lines
    assert "  notes/2026-09-30-board.md  E-ENCODING  not valid UTF-8 at byte 1042; skipped" in lines
    assert "  notes/old-binary.md        E-BINARY    contains NUL bytes; skipped" in lines
    assert lines[-2:] == ["Warnings (2): see --verbose", "Exit code 5 (2 files failed)"]
    assert any(line.startswith("(Files and Items count this run only") for line in lines)  # run-scoped labelling
    assert "resolved via attendance: 3 (current state, all items)" in lines


@pytest.mark.ac("AC-01.7")
def test_empty_report_prints_all_five_sections_as_none():
    text = render_ingest_table(empty_report())
    lines = text.splitlines()
    for heading in ("Unresolved owners (0):", "Missing/unresolved due dates (0):", "Possible duplicates (0):", "Parsing failures (0):"):
        i = lines.index(heading)
        assert lines[i + 1] == "  none"
    assert lines[-2:] == ["Warnings (0)", "Exit code 0"]
    assert render_ingest_json(empty_report()).count('"count": 0') >= 5


def test_verbose_expands_warnings_but_default_only_shows_h05_lines():
    h05 = WarningRow(
        "W-OWNER-NOW-AMBIGUOUS", "a.md", 4,
        "a.md:4: owner 'Priya' is now ambiguous (merged/completed under an earlier owner resolution)",
        (ing.H05_NOTE,),
    )
    plain = WarningRow("W-ATTENDEES-MISSING", "b.md", None, "b.md: no attendees listed")
    r = empty_report(warnings=(h05, plain))
    default = render_ingest_table(r).splitlines()
    assert "Warnings (2): see --verbose" in default
    i = next(n for n, line in enumerate(default) if line.startswith("  W-OWNER-NOW-AMBIGUOUS"))
    assert default[i].endswith("is now ambiguous") and default[i + 1] == f"      note: {ing.H05_NOTE}"
    assert not any("W-ATTENDEES-MISSING" in line for line in default)
    verbose = render_ingest_table(r, verbose=True).splitlines()
    assert "Warnings (2):" in verbose and any("W-ATTENDEES-MISSING  b.md: no attendees listed" in x for x in verbose)


def test_reresolution_section_only_when_something_changed():
    row = OwnerReResolutionRow(
        3, "a.md:4", "Priya", "downgraded_to_ambiguous", "resolved", "ambiguous",
        ("Priya Nair", "Priya Shah"), (ing.H05_NOTE,),
    )
    assert "Owner re-resolution" not in render_ingest_table(empty_report())
    text = render_ingest_table(empty_report(reresolution=(row,)))
    assert "Owner re-resolution (this run) (1):" in text
    lines = text.splitlines()
    i = lines.index('  AT-0003  a.md:4  resolved -> ambiguous: "Priya" -> Priya Nair | Priya Shah')
    assert lines[i + 1] == f"           note: {ing.H05_NOTE}"  # the note has its own line: never truncated


def test_optional_lines_hint_notices_files_note_and_finalisation_failure():
    r = empty_report(
        dry_run=True, hint="0 .md files at the top level; 35 .md files exist in subfolders. Re-run without --no-recursive",
        notices=("Dry run: nothing was written; #3 is the number a real run would get", "previous run #2 was interrupted"),
        files=ReportFileCounts(missing=2, symlinks_skipped=1), finalisation_error="finalisation failed (boom)",
        exit_code=4, exit_reason="run finalisation failed; files already ingested are kept", status="partial",
    )
    lines = render_ingest_table(r).splitlines()
    assert lines[1].startswith("Dry run: nothing was written; #3 ") and lines[2] == "previous run #2 was interrupted"
    assert any(x.startswith("Hint: 0 .md files at the top level; 35 .md files exist in subfolders") for x in lines)
    assert "Note: 2 stored files not found (marked missing); 1 symlink skipped" in lines
    assert "Run finalisation failed: finalisation failed (boom)" in lines
    assert lines[-1] == "Exit code 4 (run finalisation failed; files already ingested are kept)"
    assert summary_line(r).startswith("Dry run: Ingest run #1: 0 files")


# ----------------------------------------------------------------------------- SEC-08 / width
@pytest.mark.ac("AC-09.8")
def test_control_characters_never_reach_the_table():
    esc = "\x1b[31mred\x1b[0m\x07\x9b"
    r = empty_report(
        unresolved_owners=(OwnerIssueRow(1, f"a{esc}.md:3", 0, "unassigned", None, f"- [ ] {esc} fix it"),
                           OwnerIssueRow(2, "a.md:4", 0, "ambiguous", f"Pri{esc}ya", "x", (f"Priya{esc} Nair", "Priya Shah"))),
        missing_due_dates=(DueIssueRow(3, "a.md:5", 0, "unresolved", "x", f"nex{esc}t", "vague"),),
        failures=(FailureRow(f"b{esc}.md", "E-BINARY", f"bad{esc}", None),),
        warnings=(WarningRow("W-EMPTY-FILE", "c.md", None, f"c.md: {esc}", ()),),
        folder=f"/tmp/{esc}n",
    )
    out = render_ingest_table(r, verbose=True)
    assert "\x1b" not in out and "\x07" not in out and "\x9b" not in out
    assert "red" in out  # the text survives, only the control characters are removed


@pytest.mark.ac("AC-09.8")
def test_json_keeps_the_text_but_escapes_control_characters():
    r = empty_report(unresolved_owners=(OwnerIssueRow(1, "a.md:3", 0, "unassigned", None, "- [ ] \x1b[31mfix\x1b[0m it"),))
    text = render_ingest_json(r)
    assert "\x1b" not in text and "\\u001b[31mfix" in text
    assert json.loads(text)["unresolved_owners"]["rows"][0]["raw"] == "- [ ] \x1b[31mfix\x1b[0m it"


@pytest.mark.ac("AC-01.7")
def test_long_free_text_is_truncated_to_120_columns_in_the_table_but_not_in_json():
    long = "- [ ] " + "word " * 80
    r = empty_report(unresolved_owners=(OwnerIssueRow(1, "a.md:3", 0, "unassigned", None, long),))
    lines = render_ingest_table(r).splitlines()
    row = next(x for x in lines if x.startswith("  AT-0001"))
    assert len(row) <= 120 and row.endswith("...")
    assert json.loads(render_ingest_json(r))["unresolved_owners"]["rows"][0]["raw"] == long


def test_json_structure_and_key_order():
    d = ingest_report_dict(spec_report())
    assert list(d) == [
        "as_of", "timezone", "run", "notices", "hint", "files", "items", "resolved_via_attendance",
        "unresolved_owners", "missing_due_dates", "possible_duplicates", "parsing_failures", "warnings",
        "owner_reresolution", "finalisation_error", "exit_code", "exit_reason",
    ]
    parsed = json.loads(render_ingest_json(spec_report()))
    assert parsed["as_of"] == "2026-10-22" and parsed["timezone"] == "Asia/Kolkata"
    assert parsed["files"]["discovered"] == 34 and parsed["items"]["sources_imported"] == 58 + 6
    assert parsed["unresolved_owners"]["ambiguous"] == 1 and parsed["unresolved_owners"]["unassigned"] == 1
    assert parsed["warnings"]["by_code"] == [
        {"code": "W-MEETING-DATE-MISSING", "count": 1}, {"code": "W-NONCHECKBOX-CANDIDATES", "count": 1},
    ]
    assert parsed["exit_code"] == 5 and parsed["parsing_failures"]["rows"][0]["next_step"] == "re-save as UTF-8"


def test_mask_helpers_touch_only_the_volatile_fields():
    t = "Ingest run #12  folder=C:\\a b\\notes  as-of 2026-10-22\nFiles: discovered 1"
    assert mask_volatile_table(t) == "Ingest run #<run>  folder=<folder>  as-of 2026-10-22\nFiles: discovered 1"
    j = render_ingest_json(spec_report("D:\\x\\y", 31))
    m = mask_volatile_json(j)
    assert '"id": "<run>"' in m and '"folder": "<folder>"' in m and '"started_at": "<timestamp>"' in m
    assert re.sub(r'"(id|folder|started_at|finished_at)": [^\n]*', "", m).count("D:") == 0


# ----------------------------------------------------------------------------- the sample (goldens)
@pytest.fixture(scope="module")
def sample_reports(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("render_sample")
    folder = copy_sample(tmp)
    db = tmp / "t.db"
    first = ing.run_ingest(db, folder, cfg=roster_cfg(), run_ctx=run_ctx())
    second = ing.run_ingest(db, folder, cfg=roster_cfg(), run_ctx=run_ctx())
    return first.report, second.report


@pytest.mark.ac("AC-01.7", "AC-09.4")
@pytest.mark.sc("SC-41")
def test_golden_sample_ingest_table_json_verbose_and_second_run(sample_reports):
    first, second = sample_reports
    check_golden("ingest_sample.txt", mask_volatile_table(render_ingest_table(first)))
    check_golden("ingest_sample_verbose.txt", mask_volatile_table(render_ingest_table(first, verbose=True)))
    check_golden("ingest_sample.json", mask_volatile_json(render_ingest_json(first)))
    check_golden("ingest_sample_second_run.txt", mask_volatile_table(render_ingest_table(second)))


@pytest.mark.ac("AC-09.4")
def test_sample_report_is_byte_stable_across_two_fresh_databases(tmp_path, sample_reports):
    folder = copy_sample(tmp_path)
    other = ing.run_ingest(tmp_path / "other.db", folder, cfg=roster_cfg(), run_ctx=run_ctx()).report
    first, _ = sample_reports
    assert mask_volatile_table(render_ingest_table(other)) == mask_volatile_table(render_ingest_table(first))
    assert mask_volatile_json(render_ingest_json(other)) == mask_volatile_json(render_ingest_json(first))


@pytest.mark.ac("AC-01.7")
def test_sample_table_stays_within_120_columns_and_uses_forward_slashes(sample_reports):
    first, _ = sample_reports
    lines = render_ingest_table(first).splitlines()
    assert all(len(x) <= 120 for x in lines[1:])  # (--verbose prints each warning whole, even when longer)
    verbose = render_ingest_table(first, verbose=True)
    assert "Next: add an 'Attendees:' line so owners can be resolved." in verbose
    body = "\n".join(lines[1:])
    assert "\\" not in body  # paths are shown with "/" on every OS


def test_h05_sample_golden_lines(tmp_path):
    folder = copy_sample(tmp_path)
    db = tmp_path / "t.db"
    ing.run_ingest(db, folder, cfg=make_cfg(tmp_path), run_ctx=run_ctx())
    res = ing.run_ingest(db, folder, cfg=make_cfg(tmp_path, attendance=False, name="off.toml"), run_ctx=run_ctx())
    text = mask_volatile_table(render_ingest_table(res.report))
    check_golden("ingest_sample_h05.txt", text)
    assert text.count(ing.H05_NOTE) >= 6
