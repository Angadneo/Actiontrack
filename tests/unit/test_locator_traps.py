"""T-M3-05: due-phrase locator II (traps, not-due phrases, on/for event roles, multiple dates)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from actiontrack.domain.dates import (
    Located,
    distinct_due_phrases,
    has_multiple_dates,
    locate,
    not_due_phrases,
)

NOTES = Path(__file__).resolve().parents[1] / "fixtures" / "notes"
_CHECKBOX = re.compile(r"^\s*[-*+]\s*\[.\]\s*")
EM = "—"


def sample(prefix: str, line_no: int) -> str:
    files = sorted(NOTES.glob(f"{prefix}*.md"))
    assert len(files) == 1, (prefix, files)
    line = files[0].read_text(encoding="utf-8").splitlines()[line_no - 1]
    assert _CHECKBOX.match(line), line
    return _CHECKBOX.sub("", line, count=1).strip()


def due_raws(desc: str) -> list[str]:
    return [f.raw for f in distinct_due_phrases(locate(desc))]


def only(desc: str) -> Located:
    loc = locate(desc)
    assert len(loc) == 1, loc
    return loc[0]


@pytest.mark.ac("AC-06.7")
@pytest.mark.sc("SC-35")
def test_percent_is_not_a_date_only_iso_phrase_remains():
    desc = sample("2026-10-02-finance", 10)
    assert "10%" in desc and EM in desc
    assert due_raws(desc) == ["2026-10-09"]
    assert len(locate(desc)) == 1


@pytest.mark.parametrize(("prefix", "line"), [("2026-09-29-billing", 12), ("2026-10-08-billing", 11)])
def test_from_date_is_not_due(prefix, line):
    desc = sample(prefix, line)
    loc = locate(desc)
    assert due_raws(desc) == []
    (nd,) = not_due_phrases(loc)
    assert nd.raw == "9 Oct" and nd.role == "not_due"
    assert nd.introducer == "from"
    assert desc[nd.start:nd.end] == "9 Oct"
    assert desc[nd.introducer_span.start:nd.introducer_span.end] == "from"
    assert not has_multiple_dates(loc)


@pytest.mark.parametrize(("desc", "intro"), [
    ("Freeze edits since 9 Oct", "since"),
    ("Freeze edits starting 9 Oct", "starting"),
    ("Freeze edits starting on 9 Oct", "starting on"),
    ("Freeze edits from Monday", "from"),
    ("Freeze edits FROM 9 Oct.", "FROM"),
])
def test_other_not_due_introducers(desc, intro):
    loc = locate(desc)
    (nd,) = loc
    assert nd.role == "not_due" and nd.introducer == intro
    assert distinct_due_phrases(loc) == []


@pytest.mark.ac("AC-06.5")
def test_not_due_beside_due_does_not_create_multiple_dates():
    loc = locate("Freeze edits from 9 Oct, by Friday")
    assert [(f.raw, f.role) for f in loc] == [("9 Oct", "not_due"), ("Friday", "due")]
    assert [f.raw for f in distinct_due_phrases(loc)] == ["Friday"]
    assert not has_multiple_dates(loc)


@pytest.mark.ac("AC-06.10")
@pytest.mark.sc("SC-64")
def test_g07_full_date_after_on_is_due_not_event():
    desc = sample("2026-10-06-quote", 10)
    assert desc.endswith("on 2026-10-14")
    f = only(desc)
    assert (f.raw, f.role, f.introducer, f.phrase.kind) == ("2026-10-14", "due", "on", "absolute")


@pytest.mark.ac("AC-06.10")
@pytest.mark.parametrize("desc", [
    "Demo by 2026-10-14",
    "Demo due 14 Oct 2026",
    "Demo for 2026-10-14",
    "Demo on 2026-10-14",
    "Demo on 14 Oct 2026",
    "Demo for Oct 14, 2026",
    "Demo on Wed 14 Oct 2026",
    "Demo on 10/14/2026",
    "Demo for 2026/10/14",
])
def test_year_bearing_dates_are_never_events(desc):
    f = only(desc)
    assert f.role == "due"


@pytest.mark.ac("AC-06.10")
@pytest.mark.parametrize(("desc", "raw", "intro", "kind"), [
    ("Demo on Oct 14", "Oct 14", "on", "yearless"),
    ("Demo on 14 Oct", "14 Oct", "on", "yearless"),
    ("Demo for Oct 14", "Oct 14", "for", "yearless"),
    ("Demo on Wed 14 Oct", "Wed 14 Oct", "on", "weekday_date"),
    ("Demo on 10/14", "10/14", "on", "yearless"),
])
def test_yearless_date_after_on_or_for_is_event(desc, raw, intro, kind):
    f = only(desc)
    assert (f.raw, f.role, f.introducer, f.phrase.kind) == (raw, "event", intro, kind)


def test_schedule_cutover_for_saturday_10_oct_is_event():
    desc = sample("2026-09-29-billing", 11)
    f = only(desc)
    assert (f.raw, f.role, f.introducer, f.phrase.kind) == ("Saturday 10 Oct", "event", "for", "weekday_date")


@pytest.mark.parametrize(("desc", "intro", "raw"), [
    ("Sam to demo the thing by Oct 14", "by", "Oct 14"),
    ("Sam to demo the thing due Oct 14", "due", "Oct 14"),
    ("Sam to demo the thing due on Oct 14", "due on", "Oct 14"),
    ("Sam to demo the thing before Oct 14", "before", "Oct 14"),
    ("Sam to demo the thing Oct 14", None, "Oct 14"),
])
def test_yearless_date_with_other_introducers_is_due(desc, intro, raw):
    f = only(desc)
    assert (f.raw, f.role, f.introducer) == (raw, "due", intro)


@pytest.mark.ac("AC-06.11")
@pytest.mark.parametrize(("desc", "intro"), [("Demo on Friday", "on"), ("Demo for Friday", "for"),
                                              ("Demo on fri", "on"), ("Demo on Monday.", "on")])
def test_h02_bare_weekday_after_on_for_is_ordinary_due(desc, intro):
    f = only(desc)
    assert f.role == "due" and f.phrase.kind == "weekday" and f.introducer == intro


def test_on_staging_still_skipped():
    assert locate("Rahul to deploy on staging") == []


@pytest.mark.parametrize(("prefix", "line", "kind", "raw"), [
    ("2026-10-14-acme", 10, "vague", "November"),
    ("2026-09-30-q4-offsite", 12, "vague", "mid-October"),
])
def test_vague_phrases_found(prefix, line, kind, raw):
    f = only(sample(prefix, line))
    assert (f.phrase.kind, f.raw, f.role) == (kind, raw, "due")


def test_month_only_requires_clause_end():
    assert locate("Sam to plan for November planning") == []
    assert locate("Sam to plan by may send") == []
    assert only("Sam to plan for November.").raw == "November"
    assert only("Sam to plan by November, then rest").raw == "November"


@pytest.mark.parametrize(("prefix", "line", "raw"), [
    ("2026-09-10-acme", 9, "tomorrow"),
    ("2026-09-14-onboarding", 12, "end of month"),
    ("2026-10-12-billing", 10, "Wednesday"),
    ("2026-09-24-onboarding", 11, "2 Oct"),
])
def test_numbers_before_nouns_are_never_part_of_a_phrase(prefix, line, raw):
    desc = sample(prefix, line)
    assert any(ch.isdigit() for ch in desc.split(" by ")[0])
    assert due_raws(desc) == [raw]
    assert len(locate(desc)) == 1


@pytest.mark.parametrize("desc", [
    "Sam to review 6 months of data",
    "Sam to pay 50 invoices",
    "Sam may send the deck",
    "Check uptime monitoring",
    "Sam to cut costs by 10%",
    "Rahul to take the mar report to Sam",
    "Priya to look at the sat nav docs",
    "Lena to wed the plan to the budget",
    "The sun may set early",
])
def test_ordinary_words_that_look_like_dates_are_not_dates(desc):
    assert locate(desc) == []


@pytest.mark.ac("AC-06.5")
@pytest.mark.sc("SC-35")
def test_multiple_different_dates():
    loc = locate("Send deck by Friday, results due 15 October")
    assert [f.raw for f in loc] == ["Friday", "15 October"]
    assert [f.raw for f in distinct_due_phrases(loc)] == ["Friday", "15 October"]
    assert has_multiple_dates(loc)


def test_multiple_dates_keep_order_and_all_raws():
    loc = locate("Send by Friday, due 2026-10-20 and before next Tuesday")
    assert [f.raw for f in distinct_due_phrases(loc)] == ["Friday", "2026-10-20", "next Tuesday"]
    assert has_multiple_dates(loc)


def test_identical_raw_repeated_is_one_phrase():
    loc = locate("Send deck by Friday, really by Friday")
    assert len(loc) == 2  # both spans are kept for task_text
    assert [f.raw for f in distinct_due_phrases(loc)] == ["Friday"]
    assert not has_multiple_dates(loc)
    loc = locate("Send deck by Friday, really by FRIDAY")
    assert not has_multiple_dates(loc)


def test_different_raw_same_date_defaults_to_multiple():  # note B-3 (ii), OPEN: documented default
    assert has_multiple_dates(locate("Send deck by Oct 9 and by 2026-10-09"))


def test_event_and_due_phrases_both_count():
    assert has_multiple_dates(locate("Demo on Oct 14, results due 15 October"))


@pytest.mark.ac("AC-06.10", "AC-06.11")
def test_sample_sweep_no_multiple_dates_no_bad_event_roles():
    n = 0
    for p in sorted(NOTES.glob("*.md")):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if not _CHECKBOX.match(line):
                continue
            desc = _CHECKBOX.sub("", line, count=1).strip()
            loc = locate(desc)
            assert not has_multiple_dates(loc), (p.name, desc)
            for f in loc:
                if f.role == "event":
                    assert f.phrase.yearless and f.phrase.kind in ("yearless", "weekday_date")
            n += 1
    assert n > 100
