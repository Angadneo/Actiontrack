"""T-M3-08: resolver C (end-of-period, offsets with digits and number words, month arithmetic, range/vague)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from actiontrack.domain.dates import Confidence, Resolution, resolve_text

M = date(2026, 10, 14)  # Wednesday


def R(text, m) -> Resolution:
    res = resolve_text(text, m)
    assert res is not None, text
    return res


def ok(res, d, rule, *warnings):
    assert (res.date, res.confidence, res.rule, res.reason, res.warnings) == (
        d, Confidence.RELATIVE, rule, None, tuple(warnings))


def unresolved(res, reason):
    assert (res.date, res.confidence, res.rule, res.reason, res.warnings) == (
        None, Confidence.UNRESOLVED, None, reason, ())


@pytest.mark.ac("AC-06.1")
def test_spec_table_m_2026_10_14():
    ok(R("end of week", M), date(2026, 10, 16), "end_of_week")
    ok(R("EOW", M), date(2026, 10, 16), "end_of_week")
    ok(R("end of next week", M), date(2026, 10, 23), "end_of_next_week")
    ok(R("end of month", M), date(2026, 10, 31), "end_of_month")
    ok(R("EOM", M), date(2026, 10, 31), "end_of_month")
    ok(R("end of next month", M), date(2026, 11, 30), "end_of_next_month")
    ok(R("in 2 weeks", M), date(2026, 10, 28), "in_n_weeks")
    ok(R("within 2 weeks", M), date(2026, 10, 28), "in_n_weeks")
    ok(R("in 3 days", M), date(2026, 10, 17), "in_n_days")
    ok(R("within 3 days", M), date(2026, 10, 17), "in_n_days")
    ok(R("in 1 day", M), date(2026, 10, 15), "in_n_days")


@pytest.mark.ac("AC-06.11")
def test_number_words_resolver_half():
    ok(R("in two days", M), date(2026, 10, 16), "in_n_days")
    ok(R("in a week", M), date(2026, 10, 21), "in_n_weeks")
    ok(R("within three weeks", M), date(2026, 11, 4), "in_n_weeks")
    ok(R("in twelve days", M), date(2026, 10, 26), "in_n_days")
    ok(R("in 13 days", M), date(2026, 10, 27), "in_n_days")
    ok(R("in a day", M), date(2026, 10, 15), "in_n_days")
    ok(R("in an day", M), date(2026, 10, 15), "in_n_days")
    ok(R("in one week", M), date(2026, 10, 21), "in_n_weeks")
    ok(R("in twelve weeks", M), M + timedelta(weeks=12), "in_n_weeks")
    unresolved(R("in a couple of days", M), "vague")
    unresolved(R("in a few weeks", M), "vague")
    unresolved(R("in a fortnight", M), "vague")
    assert resolve_text("in thirteen days", M) is None  # not a date phrase at all


def test_number_words_all_one_to_twelve():
    words = "one two three four five six seven eight nine ten eleven twelve".split()
    for i, w in enumerate(words, 1):
        ok(R(f"in {w} days", M), M + timedelta(days=i), "in_n_days")
        ok(R(f"within {w.upper()} weeks", M), M + timedelta(days=7 * i), "in_n_weeks")


def test_a_and_an_only_before_singular_unit():
    assert resolve_text("in a days", M) is None
    assert resolve_text("within an weeks", M) is None
    ok(R("within a week", M), date(2026, 10, 21), "in_n_weeks")


@pytest.mark.ac("AC-06.1")
def test_weekend_meeting_for_end_of_week():
    sat, sun = date(2026, 10, 17), date(2026, 10, 18)
    unresolved(R("EOW", sat), "weekend_meeting")
    unresolved(R("end of week", sun), "weekend_meeting")
    ok(R("end of next week", sat), date(2026, 10, 23), "end_of_next_week")  # no weekend rule
    ok(R("EOW", date(2026, 10, 16)), date(2026, 10, 16), "end_of_week")
    ok(R("EOW", date(2026, 10, 12)), date(2026, 10, 16), "end_of_week")


def test_end_of_month_on_last_day():
    ok(R("end of month", date(2026, 10, 31)), date(2026, 10, 31), "end_of_month")


@pytest.mark.sc("SC-12")
def test_month_arithmetic():
    ok(R("end of month", date(2026, 2, 10)), date(2026, 2, 28), "end_of_month")
    ok(R("end of next month", date(2026, 1, 31)), date(2026, 2, 28), "end_of_next_month")
    ok(R("end of next month", date(2026, 12, 15)), date(2027, 1, 31), "end_of_next_month")
    ok(R("end of next month", date(2028, 1, 10)), date(2028, 2, 29), "end_of_next_month")
    ok(R("end of month", date(2028, 2, 1)), date(2028, 2, 29), "end_of_month")
    ok(R("end of next month", date(2026, 10, 31)), date(2026, 11, 30), "end_of_next_month")


def test_iso_week_across_year_end():
    m = date(2026, 12, 30)
    assert m.weekday() == 2
    ok(R("end of next week", m), date(2027, 1, 8), "end_of_next_week")
    ok(R("end of week", m), date(2027, 1, 1), "end_of_week")
    ok(R("Monday next week", m), date(2027, 1, 4), "monday_next_week")
    ok(R("in 2 days", m), date(2027, 1, 1), "in_n_days")


@pytest.mark.parametrize(("text", "m", "expected"), [
    ("end of next week", date(2026, 9, 3), date(2026, 9, 11)),  # 09-03:11
    ("end of month", date(2026, 9, 14), date(2026, 9, 30)),  # 09-14:12
])
def test_sample_rows(text, m, expected):
    res = R(text, m)
    assert res.date == expected and res.confidence == Confidence.RELATIVE


@pytest.mark.ac("AC-06.10")
@pytest.mark.parametrize("text", ["next week", "this week", "next month", "this month", "week of Oct 19",
                                  "week of 2026-10-19", "the week of Mon 19 Oct"])
def test_range_phrases_unresolved_range(text):
    unresolved(R(text, M), "range")
    unresolved(R(text, None), "range")  # no meeting date needed to know it is a span


@pytest.mark.ac("AC-06.10")
@pytest.mark.parametrize("text", ["soon", "later", "ASAP", "TBD", "ongoing", "Q4", "end of Q4", "mid-month",
                                  "mid-October", "for November", "November", "after the launch"])
def test_vague_phrases_unresolved_vague(text):
    unresolved(R(text, M), "vague")
    unresolved(R(text, None), "vague")


def test_sample_vague_rows():
    unresolved(R("mid-October", date(2026, 9, 30)), "vague")
    unresolved(R("November", date(2026, 10, 14)), "vague")


def test_unresolved_rows_store_no_rule():
    for t in ("next week", "soon", "in a few weeks", "week of Oct 19"):
        res = R(t, M)
        assert res.rule is None and res.date is None


def test_no_meeting_date_for_period_rows():
    for t in ("end of week", "EOM", "end of next month", "in two days", "within 3 weeks"):
        res = R(t, None)
        assert res.reason == "no_meeting_date" and res.date is None


@pytest.mark.parametrize("text", [
    "today", "tomorrow", "yesterday", "EOD", "Friday", "Wednesday", "this Friday", "next Friday",
    "Monday next week", "end of week", "end of next week", "end of month", "end of next month",
    "in 3 days", "in 999 days", "within 999 weeks", "in a week", "the 15th", "the 31st", "Oct 31", "Feb 29",
    "Wed 16 Sep", "week of Oct 19", "next week", "soon", "2026-10-31", "Oct 31, 2026",
])
def test_never_raises_for_any_meeting_date_2024_to_2030(text):
    d = date(2024, 1, 1)
    end = date(2030, 12, 31)
    while d <= end:
        res = R(text, d)
        assert (res.date is None) == (res.confidence == Confidence.UNRESOLVED)
        d += timedelta(days=1)


def test_extreme_meeting_dates_do_not_raise():
    for m in (date(9999, 12, 31), date(1, 1, 1)):
        for t in ("end of next month", "in 999 weeks", "tomorrow", "yesterday", "Oct 31", "the 15th",
                  "next Friday", "Wed 16 Sep"):
            R(t, m)
