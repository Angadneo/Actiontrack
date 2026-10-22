"""T-M3-13 (integration half): year-less dates end to end through real note files (SC-63, AC-06.8, AC-06.9).

Synthetic notes are written to ``tmp_path`` (next to a byte-exact copy of the fixture notes), discovered with
``discover`` and parsed with ``parse_file`` using the real due analyzer. Expected values are plain ``datetime``
arithmetic (see the module docstring of ``tests/unit/test_yearless_rule.py`` for the rule and for the Feb 29
decision, spec-silent B-17 / J-04).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing import parse_file
from actiontrack.parsing.discovery import discover

D = dt.date
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "notes"
GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "m3_sample_due.json"


def _note(date_line: str | None, items: list[str], title: str = "Sync") -> str:
    head = f"# Meeting: {title}\n"
    if date_line is not None:
        head += f"Date: {date_line}\n"
    head += "Attendees: Priya Nair, Sam Okafor\n\n## Action items\n"
    return head + "".join(f"- [ ] {i}\n" for i in items)


def _write(folder: Path, name: str, text: str, newline: str = "\n") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(text.replace("\n", newline).encode("utf-8"))


def _parse_all(folder: Path) -> dict:
    out = {}
    for f in discover(folder).files:
        out[f.rel_path] = parse_file(f)
    return out


def _view(item) -> tuple:
    d = item.due_hook
    return (
        d.due_date.isoformat() if d.due_date else None,
        d.due_confidence.value,
        d.due_rule.value if d.due_rule else None,
        d.due_reason.value if d.due_reason else None,
    )


def _warns(pf, code: str) -> list[tuple]:
    return [(w.line, w.detail) for w in pf.warnings if w.code == code]


@pytest.fixture
def folder(tmp_path: Path, copy_fixture_notes) -> Path:
    root = copy_fixture_notes()  # byte-exact copy of tests/fixtures/notes
    # SC-63: two notes
    _write(root, "2026-10-12-sc63-before.md", _note("2026-10-12", ["Priya to send the pack by 9 Oct"]))
    _write(root, "2026-12-20-sc63-rollover.md", _note("2026-12-20", ["Sam to book the venue by Jan 6"]))
    # AC-06.8 rows, M = Wed 2026-10-14 (header: long date form)
    _write(root, "2026-10-14-yearless-rows.md", _note("14 October 2026", [
        "Priya to a by Mar 1",
        "Priya to b by Oct 31",
        "Priya to c by Sep 16",
        "Priya to d by Feb 29",
        "Priya to e by Aug 15",
        "Priya to f by Aug 14",
        "Priya to g by Wed 16 Sep",
        "Priya to h by Fri 16 Oct, then by Oct 20",
        "Priya to i for Saturday 17 Oct",
        "Priya to j by 2026-09-16",
    ]))
    # AC-06.9 rows with M = Tue 2026-09-01 (front matter date, CRLF line endings)
    fm = ("---\ntitle: Weekly\ndate: 2026-09-01\nattendees: [Priya Nair, Sam Okafor]\n---\n\n"
          "- [ ] Sam to k by Tue 16 Sep\n- [ ] Sam to l by Fri 16 Sep\n- [ ] Sam to m by Wed 16 Sep\n")
    _write(root, "2026-09-01-weekday-rows.md", fm, newline="\r\n")
    # M = Mon 2026-09-14 and an undated note (filename date must not be used)
    _write(root, "2026-09-14-nowarn.md", _note("2026-09-14", ["Sam to n Wed 16 Sep", "Sam to o by Sep 14"]))
    _write(root, "2026-10-03-undated.md", _note("sometime last week", ["Meera to chase by Oct 9", "Sam to p by Wed 16 Sep"]))
    return root


@pytest.mark.ac("AC-06.8")
@pytest.mark.sc("SC-63")
def test_sc63_two_notes_end_to_end(folder):
    parsed = _parse_all(folder)
    a = parsed["2026-10-12-sc63-before.md"]
    (item,) = a.items
    d = item.due_hook
    assert (d.due_date, d.due_confidence.value, d.due_rule.value, d.due_raw) == (D(2026, 10, 9), "inferred", "yearless_date", "9 Oct")
    assert d.due_date.year == 2026  # not 2027
    assert _warns(a, codes.W_DUE_BEFORE_MEETING) == [(item.line_start, "2026-10-09 < 2026-10-12")]
    b = parsed["2026-12-20-sc63-rollover.md"]
    (item,) = b.items
    assert (item.due_hook.due_date, item.due_hook.due_confidence.value) == (D(2027, 1, 6), "inferred")
    assert (D(2026, 12, 20) - D(2026, 1, 6)).days == 348
    assert _warns(b, codes.W_DUE_BEFORE_MEETING) == []
    # the task text no longer holds the date phrase
    assert parsed["2026-10-12-sc63-before.md"].items[0].task_text == "Priya to send the pack"


@pytest.mark.ac("AC-06.8")
def test_ac_06_8_rows_through_a_note_file(folder):
    pf = _parse_all(folder)["2026-10-14-yearless-rows.md"]
    assert pf.meeting.date == D(2026, 10, 14) and pf.status == "ok" and len(pf.items) == 10
    got = [_view(i) for i in pf.items]
    assert got == [
        ("2027-03-01", "inferred", "yearless_date", None),
        ("2026-10-31", "inferred", "yearless_date", None),
        ("2026-09-16", "inferred", "yearless_date", None),
        (None, "unresolved", None, "invalid_date"),
        ("2026-08-15", "inferred", "yearless_date", None),  # exactly 60 days earlier
        ("2027-08-14", "inferred", "yearless_date", None),  # 61 days earlier
        ("2026-09-16", "inferred", "weekday_date", None),
        (None, "unresolved", None, "multiple_dates"),
        ("2026-10-17", "inferred", "event_date", None),
        ("2026-09-16", "explicit", "iso_date", None),
    ]
    assert (D(2026, 10, 14) - D(2026, 8, 15)).days == 60 and D(2026, 10, 17).weekday() == 5
    first = pf.items[0].line_start
    before = _warns(pf, codes.W_DUE_BEFORE_MEETING)
    # lines of items c (3rd), e (5th), g (7th) and j (10th: explicit date earlier than M, spec 9.4)
    assert [ln for ln, _ in before] == [first + 2, first + 4, first + 6, first + 9]
    assert [det for _, det in before] == [
        "2026-09-16 < 2026-10-14", "2026-08-15 < 2026-10-14", "2026-09-16 < 2026-10-14", "2026-09-16 < 2026-10-14"]


@pytest.mark.ac("AC-06.9")
def test_ac_06_9_rows_through_front_matter_crlf_note(folder):
    pf = _parse_all(folder)["2026-09-01-weekday-rows.md"]
    assert pf.meeting.date == D(2026, 9, 1) and len(pf.items) == 3
    assert D(2026, 9, 1).weekday() == 1  # Tuesday
    got = [_view(i) for i in pf.items]
    assert got == [
        ("2025-09-16", "inferred", "weekday_date", None),
        (None, "unresolved", None, "weekday_date_mismatch"),
        ("2026-09-16", "inferred", "weekday_date", None),
    ]
    assert (D(2025, 9, 16).weekday(), D(2026, 9, 16).weekday(), D(2027, 9, 16).weekday()) == (1, 2, 3)
    assert [ln for ln, _ in _warns(pf, codes.W_DUE_BEFORE_MEETING)] == [pf.items[0].line_start]
    assert pf.items[0].due_hook.due_raw == "Tue 16 Sep"


@pytest.mark.ac("AC-06.9")
def test_weekday_date_without_warning_and_missing_meeting_date(folder):
    parsed = _parse_all(folder)
    ok = parsed["2026-09-14-nowarn.md"]
    assert [_view(i) for i in ok.items] == [
        ("2026-09-16", "inferred", "weekday_date", None),
        ("2026-09-14", "inferred", "yearless_date", None),
    ]
    assert _warns(ok, codes.W_DUE_BEFORE_MEETING) == []  # a date equal to M is not "before"
    nodate = parsed["2026-10-03-undated.md"]
    codes_seen = {w.code for w in nodate.warnings}
    assert codes.W_MEETING_DATE_INVALID in codes_seen and codes.W_MEETING_DATE_MISSING not in codes_seen
    assert nodate.meeting.date is None  # filename date (2026-10-03) is not used
    assert [_view(i) for i in nodate.items] == [(None, "unresolved", None, "no_meeting_date")] * 2
    assert _warns(nodate, codes.W_DUE_BEFORE_MEETING) == []


@pytest.mark.ac("AC-06.8")
def test_fixture_notes_year_less_items_unchanged_after_copy(folder):
    parsed = _parse_all(folder)
    rows = json.loads(GOLDEN.read_text(encoding="utf-8"))["rows"]
    checked = 0
    for r in rows:
        if r["how"] not in ("yl", "wdd", "event"):
            continue
        pf = parsed[r["file"]]
        it = next(i for i in pf.items if i.line_start == r["line"])
        d = it.due_hook
        assert (d.due_date.isoformat(), d.due_confidence.value, d.due_rule.value) == (
            r["due_date"], r["due_confidence"], r["due_rule"]), (r["file"], r["line"])
        checked += 1
    assert checked == 14
    # no sample year-less date is earlier than its meeting: zero W-DUE-BEFORE-MEETING over the fixture notes
    synthetic = {f.name for f in folder.iterdir()} - {f.name for f in FIXTURES.iterdir()}
    assert len(synthetic) == 6
    assert not [w for name, pf in parsed.items() if name not in synthetic
                for w in pf.warnings if w.code == codes.W_DUE_BEFORE_MEETING]


@pytest.mark.ac("AC-06.8")
def test_year_less_resolution_is_a_pure_function_of_the_file_bytes(folder):
    a = {k: [_view(i) for i in pf.items] for k, pf in _parse_all(folder).items()}
    # same bytes in another folder with other mtimes: identical results (no clock, BR-05)
    other = folder.parent / "elsewhere"
    other.mkdir()
    for f in folder.iterdir():
        shutil.copyfile(f, other / f.name)
        os.utime(other / f.name, (1_000_000_000, 1_000_000_000))
    b = {k: [_view(i) for i in pf.items] for k, pf in _parse_all(other).items()}
    assert a == b
