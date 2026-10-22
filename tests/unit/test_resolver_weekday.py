"""T-M3-07: resolver B (relative days and weekday rows)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.domain.dates import Confidence, Resolution, distinct_due_phrases, locate, resolve_phrase, resolve_text

W = codes.W_DUE_BEFORE_MEETING
NOTES = Path(__file__).resolve().parents[1] / "fixtures" / "notes"
_CHECKBOX = re.compile(r"^\s*[-*+]\s*\[.\]\s*")

WED = date(2026, 10, 14)  # Wed; week Mon 10-12 .. Sun 10-18


def R(text, m, *, event=False) -> Resolution:
    res = resolve_text(text, m, None, event=event)
    assert res is not None, text
    return res


def ok(res, d, rule, *warnings):
    assert (res.date, res.confidence, res.rule, res.reason, res.warnings) == (
        d, Confidence.RELATIVE, rule, None, tuple(warnings))


def unresolved(res, reason):
    assert (res.date, res.confidence, res.rule, res.reason) == (None, Confidence.UNRESOLVED, None, reason)


@pytest.mark.ac("AC-06.1")
def test_spec_table_m_wed_2026_10_14():
    ok(R("today", WED), WED, "today")
    ok(R("tonight", WED), WED, "today")
    ok(R("this morning", WED), WED, "today")
    ok(R("this afternoon", WED), WED, "today")
    ok(R("EOD", WED), WED, "eod_alone")
    ok(R("COB", WED), WED, "eod_alone")
    ok(R("tomorrow", WED), date(2026, 10, 15), "tomorrow")
    ok(R("yesterday", WED), date(2026, 10, 13), "yesterday", W)
    ok(R("Friday", WED), date(2026, 10, 16), "weekday_next")
    ok(R("by Fri".removeprefix("by "), WED), date(2026, 10, 16), "weekday_next")
    ok(R("Fri EOD", WED), date(2026, 10, 16), "weekday_next")
    unresolved(R("Wednesday", WED), "same_weekday")
    ok(R("this Friday", WED), date(2026, 10, 16), "weekday_this")
    ok(R("next Friday", WED), date(2026, 10, 23), "weekday_next_week")
    ok(R("Monday next week", WED), date(2026, 10, 19), "monday_next_week")
    ok(R("EOD tomorrow", WED), date(2026, 10, 15), "tomorrow")  # compound stores the rule of its date part
    ok(R("EOD Friday", WED), date(2026, 10, 16), "weekday_next")
    ok(R("tomorrow 5pm", WED), date(2026, 10, 15), "tomorrow")
    ok(R("this Wednesday", WED), WED, "weekday_this")  # same day allowed
    ok(R("next Wednesday", WED), date(2026, 10, 21), "weekday_next_week")


@pytest.mark.ac("AC-06.1")
def test_further_worked_cases():
    fri, sat, sun = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18)
    unresolved(R("Friday", fri), "same_weekday")  # SC-11
    ok(R("this Friday", fri), fri, "weekday_this")
    ok(R("next Friday", fri), date(2026, 10, 23), "weekday_next_week")
    ok(R("Friday", sat), date(2026, 10, 23), "weekday_next")
    unresolved(R("this Friday", sat), "this_weekday_past")
    ok(R("next Friday", sun), date(2026, 10, 23), "weekday_next_week")
    ok(R("Monday next week", sun), date(2026, 10, 19), "monday_next_week")
    ok(R("Monday", sun), date(2026, 10, 19), "weekday_next")


@pytest.mark.sc("SC-11")
def test_same_weekday_for_every_weekday_name():
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for i, n in enumerate(names):
        m = date(2026, 10, 12 + i)
        assert m.weekday() == i
        unresolved(R(n, m), "same_weekday")
        res = R(names[(i + 1) % 7], m)
        assert res.date.weekday() == (i + 1) % 7 and 0 < (res.date - m).days <= 7


@pytest.mark.ac("AC-06.11")
def test_h02_on_weekday_through_locator():
    (f,) = locate("Demo the portal on Friday")
    assert (f.role, f.phrase.kind) == ("due", "weekday")
    ok(resolve_phrase(f.phrase, WED, event=(f.role == "event")), date(2026, 10, 16), "weekday_next")
    unresolved(resolve_phrase(f.phrase, date(2026, 10, 16)), "same_weekday")
    (g,) = locate("Demo for Friday")
    ok(resolve_phrase(g.phrase, WED), date(2026, 10, 16), "weekday_next")


def test_next_tuesday_on_monday_is_next_iso_week():
    mon = date(2026, 10, 5)
    assert mon.weekday() == 0
    ok(R("next Tuesday", mon), date(2026, 10, 13), "weekday_next_week")  # sample 10-05-onboarding-retro:9
    ok(R("this Tuesday", mon), date(2026, 10, 6), "weekday_this")


def test_iso_weeks_cross_year_end():
    m = date(2026, 12, 30)
    ok(R("Monday next week", m), date(2027, 1, 4), "monday_next_week")
    ok(R("next Friday", m), date(2027, 1, 8), "weekday_next_week")
    ok(R("Friday", m), date(2027, 1, 1), "weekday_next")
    ok(R("tomorrow", m), date(2026, 12, 31), "tomorrow")
    ok(R("tomorrow", date(2026, 12, 31)), date(2027, 1, 1), "tomorrow")
    ok(R("yesterday", date(2027, 1, 1)), date(2026, 12, 31), "yesterday", W)


def test_weekend_meeting_uses_same_arithmetic():
    sat = date(2026, 10, 10)
    ok(R("Tuesday", sat), date(2026, 10, 13), "weekday_next")  # sample 10-10:9
    ok(R("Monday next week", sat), date(2026, 10, 12), "monday_next_week")
    ok(R("next Monday", sat), date(2026, 10, 12), "weekday_next_week")


SAMPLE_WEEKDAY_ROWS = [
    # (note date, phrase, expected)
    (date(2026, 9, 1), "Friday", date(2026, 9, 4)),
    (date(2026, 9, 1), "Thursday", date(2026, 9, 3)),
    (date(2026, 9, 3), "Monday", date(2026, 9, 7)),
    (date(2026, 9, 8), "Wed", date(2026, 9, 9)),
    (date(2026, 9, 18), "Mon", date(2026, 9, 21)),
    (date(2026, 10, 10), "Tuesday", date(2026, 10, 13)),
    (date(2026, 10, 2), "Wednesday", date(2026, 10, 7)),
    (date(2026, 9, 2), "tomorrow", date(2026, 9, 3)),
    (date(2026, 9, 21), "EOD tomorrow", date(2026, 9, 22)),
    (date(2026, 10, 8), "EOD", date(2026, 10, 8)),
]


@pytest.mark.parametrize(("m", "text", "expected"), SAMPLE_WEEKDAY_ROWS)
def test_sample_rows(m, text, expected):
    res = R(text, m)
    assert res.date == expected and res.confidence == Confidence.RELATIVE and res.warnings == ()


def test_sample_sweep_no_same_weekday_no_this_weekday_past():
    n_rel = 0
    for p in sorted(NOTES.glob("2026-*.md")):
        m = date.fromisoformat(p.name[:10])
        for line in p.read_text(encoding="utf-8").splitlines():
            if not _CHECKBOX.match(line):
                continue
            desc = _CHECKBOX.sub("", line, count=1).strip()
            for f in distinct_due_phrases(locate(desc)):
                res = resolve_phrase(f.phrase, m, None, event=(f.role == "event"))
                assert res.reason not in ("same_weekday", "this_weekday_past"), (p.name, desc)
                if res.confidence == Confidence.RELATIVE:
                    n_rel += 1
                    assert res.date > m or f.phrase.kind in ("today", "eod_alone"), (p.name, desc)
    assert n_rel >= 30


def test_rules_use_enum_catalogue():
    for text, rule in [("today", "today"), ("EOD", "eod_alone"), ("Friday", "weekday_next"),
                       ("this Friday", "weekday_this"), ("next Friday", "weekday_next_week")]:
        assert R(text, WED).rule in codes.DUE_RULES
        assert R(text, WED).rule == rule
