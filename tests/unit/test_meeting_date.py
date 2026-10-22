"""T-M2-12: meeting date extraction (tiers, invalid field, filename fallback, conflicts)."""

from __future__ import annotations

import datetime as dt

import pytest

from actiontrack import codes
from actiontrack.parsing.markdown import scan_document
from actiontrack.parsing.meeting import extract_meeting_date, parse_meeting
from actiontrack.parsing.reader import DecodedText, classify_and_decode, split_lines
from tests.conftest import FIXTURES_NOTES

D = dt.date


def run(text: str, filename: str = "note.md", date_order: str | None = None):
    scan = scan_document(split_lines(text), filename)
    return extract_meeting_date(
        scan.lines, scan.annotation.lines, scan.front_matter, filename, date_order, filename
    )


def codes_of(res) -> set[str]:
    return {w.code for w in res.warnings}


# --------------------------------------------------------------------------- samples
def _sample(name: str):
    dec = classify_and_decode((FIXTURES_NOTES / name).read_bytes(), name)
    assert isinstance(dec, DecodedText)
    scan = scan_document(split_lines(dec.text), name)
    return extract_meeting_date(scan.lines, scan.annotation.lines, scan.front_matter, name, None, name)


@pytest.mark.parametrize(
    "name,date,source",
    [
        ("2026-09-16-standup.md", D(2026, 9, 16), "front_matter"),
        ("2026-09-30-standup.md", D(2026, 9, 30), "front_matter"),
        ("2026-09-14-onboarding-flow-design.md", D(2026, 9, 14), "date_line"),
        ("2026-09-09-qa-standup.md", D(2026, 9, 9), "date_line"),
    ],
)
def test_sample_headers(name, date, source):
    res = _sample(name)
    assert (res.date, res.source) == (date, source)
    assert res.warnings == ()


@pytest.mark.ac("AC-01.15", "AC-01.20")
@pytest.mark.sc("SC-49")
def test_sample_sales_pipeline_invalid_only():
    res = _sample("2026-10-03-sales-pipeline.md")
    assert res.date is None and res.source == "none"
    assert codes_of(res) == {codes.W_MEETING_DATE_INVALID}
    assert len(res.warnings) == 1 and res.warnings[0].line == 2


@pytest.mark.ac("AC-01.9")
def test_sample_untitled_missing_only():
    text = (FIXTURES_NOTES / "untitled.md").read_text("utf-8")
    scan = scan_document(split_lines(text), "untitled.md")
    res = extract_meeting_date(scan.lines, scan.annotation.lines, scan.front_matter, "untitled.md")
    assert res.date is None and codes_of(res) == {codes.W_MEETING_DATE_MISSING}


def test_all_other_samples_match_filename_without_conflict():
    special = {
        "2026-09-09-notes-final-v2.md",  # empty
        "2026-10-03-sales-pipeline.md",
        "untitled.md",
        "scratch.md",
    }
    checked = 0
    for p in sorted(FIXTURES_NOTES.glob("*.md")):
        if p.name in special:
            continue
        res = _sample(p.name)
        assert res.date == D.fromisoformat(p.name[:10]), p.name
        assert res.source in {"front_matter", "date_line"}
        assert res.warnings == (), p.name
        checked += 1
    assert checked == 31


# --------------------------------------------------------------------------- invalid field
@pytest.mark.ac("AC-01.20")
def test_invalid_date_line_no_filename_fallback():
    res = run("# Meeting: X\nDate: sometime last week\n", "2026-10-03-x.md")
    assert res.date is None and codes_of(res) == {codes.W_MEETING_DATE_INVALID}


@pytest.mark.ac("AC-01.20")
def test_invalid_front_matter_date():
    res = run("---\ndate: nonsense\n---\nDate: 2026-09-01\n", "2026-10-03-x.md")
    assert res.date is None and codes_of(res) == {codes.W_MEETING_DATE_INVALID}
    assert res.source == "none"


@pytest.mark.parametrize("value", ["2026-02-30", "03/04/2026", "16 Sep", "Sep 16", "", "TBD"])
def test_invalid_values(value):
    res = run(f"Date: {value}\n", "2026-10-03-x.md")
    assert res.date is None and codes_of(res) == {codes.W_MEETING_DATE_INVALID}


@pytest.mark.ac("AC-01.20")
def test_rejected_front_matter_falls_to_body_then_filename():
    fm = "---\ndate: &d 2026-09-16\n---\n"
    res = run(fm + "Date: 2026-09-20\n", "2026-09-16-x.md")
    assert (res.date, res.source) == (D(2026, 9, 20), "date_line")
    res = run(fm + "text\n", "2026-09-16-x.md")
    assert (res.date, res.source) == (D(2026, 9, 16), "filename")
    assert codes_of(res) == {codes.W_MEETING_DATE_FROM_FILENAME}


# --------------------------------------------------------------------------- filename / missing
def test_filename_fallback_iso():
    res = run("no date here\n", "2026-10-20-x.md")
    assert (res.date, res.source, res.raw) == (D(2026, 10, 20), "filename", "2026-10-20")
    assert codes_of(res) == {codes.W_MEETING_DATE_FROM_FILENAME}


def test_filename_fallback_compact_and_subdir():
    res = run("x\n", "sub/dir/20261020-notes.md")
    assert res.date == D(2026, 10, 20) and res.source == "filename"
    res = run("x\n", "sub\\2026-10-20_notes.md")
    assert res.date == D(2026, 10, 20)


@pytest.mark.parametrize("name", ["2026-13-45-x.md", "notes.md", "12026-10-20.md", "2026-10-201.md"])
def test_filename_unusable_is_missing(name):
    res = run("x\n", name)
    assert res.date is None and codes_of(res) == {codes.W_MEETING_DATE_MISSING}


# --------------------------------------------------------------------------- formats
def test_iso_with_time_zone_as_written():
    assert run("Date: 2026-09-01T10:30:00+05:30\n").date == D(2026, 9, 1)


def test_bold_forms_and_case():
    assert run("**Date:** Sep 1, 2026\n").date == D(2026, 9, 1)
    assert run("**Date**: 1 September 2026\n").date == D(2026, 9, 1)
    assert run("DATE: 2026-09-01\n").date == D(2026, 9, 1)
    assert run("---\nmeeting_date: 2026-09-02\n---\n").date == D(2026, 9, 2)
    assert run("---\ndate: '2026-09-02'\n---\n").date == D(2026, 9, 2)


def test_weekday_mismatch_still_uses_date():
    res = run("Date: Tuesday, 14 September 2026\n")
    assert res.date == D(2026, 9, 14) and codes_of(res) == {codes.W_DATE_WEEKDAY_MISMATCH}
    assert run("Date: Monday, 14 September 2026\n").warnings == ()


def test_numeric_needs_date_order():
    assert run("Date: 03/04/2026\n").date is None
    assert run("Date: 03/04/2026\n", date_order="dmy").date == D(2026, 4, 3)
    assert run("Date: 03/04/2026\n", date_order="mdy").date == D(2026, 3, 4)
    assert run("Date: 13/04/2026\n").date == D(2026, 4, 13)  # unambiguous


# --------------------------------------------------------------------------- scope
def test_date_line_window_is_30_nonblank_lines():
    body = "".join(f"line {i}\n\n" for i in range(29))  # 29 non-blank lines
    assert run(body + "Date: 2026-09-01\n").date == D(2026, 9, 1)
    res = run(body + "line 30\nDate: 2026-09-01\n", "x.md")
    assert res.date is None and codes_of(res) == {codes.W_MEETING_DATE_MISSING}


def test_date_in_fence_comment_quote_ignored():
    res = run("```\nDate: 2026-09-01\n```\n<!-- Date: 2026-09-02 -->\n> Date: 2026-09-03\n")
    assert res.date is None and codes_of(res) == {codes.W_MEETING_DATE_MISSING}


# --------------------------------------------------------------------------- conflicts, headings
def test_front_matter_beats_filename_with_conflict():
    res = run("---\ndate: 2026-09-16\n---\n", "2026-09-17-x.md")
    assert (res.date, res.source) == (D(2026, 9, 16), "front_matter")
    assert codes_of(res) == {codes.W_DATE_CONFLICT}


def test_date_line_beats_heading_and_filename():
    res = run("# Standup 2026-09-02\nDate: 2026-09-01\n", "2026-09-03-x.md")
    assert res.date == D(2026, 9, 1)
    assert [w.code for w in res.warnings] == [codes.W_DATE_CONFLICT] * 2


def test_no_conflict_when_front_matter_invalid():
    res = run("---\ndate: nonsense\n---\n", "2026-09-17-x.md")
    assert codes_of(res) == {codes.W_MEETING_DATE_INVALID}


def test_heading_date_source_title():
    res = run("# Standup 2026-09-01\n", "2026-09-01-x.md")
    assert (res.date, res.source, res.raw) == (D(2026, 9, 1), "title", "2026-09-01")
    assert res.warnings == ()
    res = run("# Review Sep 3, 2026\n", "x.md")
    assert (res.date, res.source) == (D(2026, 9, 3), "title")
    assert run("## Planning for 2026-09-05\n").source == "title"
    assert run("# Standup 16 Sep\n", "x.md").date is None  # year-less heading ignored


def test_heading_beats_filename_no_fallback_warning():
    res = run("# Standup 2026-09-01\n", "2026-09-05-x.md")
    assert res.date == D(2026, 9, 1) and codes_of(res) == {codes.W_DATE_CONFLICT}


# --------------------------------------------------------------------------- never FUTURE
def test_future_dates_never_warn_here():
    res = run("Date: 2099-01-01\n")
    assert res.date == D(2099, 1, 1) and res.warnings == ()
    meta = parse_meeting(scan_document(split_lines("Date: 2099-01-01\nAttendees: A\n")), "x.md")
    assert codes.W_MEETING_DATE_FUTURE not in {w.code for w in meta.warnings}


def test_source_values_limited():
    from actiontrack.domain.models import DATE_SOURCES

    cases = [
        ("Date: 2026-09-01", "a.md"),
        ("x", "a.md"),
        ("x", "2026-09-01.md"),
        ("# t 2026-09-01", "a.md"),
        ("---\ndate: 2026-09-01\n---", "a.md"),
        ("Date: no", "a.md"),
    ]
    for text, name in cases:
        assert run(text + "\n", name).source in DATE_SOURCES
