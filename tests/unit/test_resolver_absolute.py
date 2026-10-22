"""T-M3-06: resolver A (absolute, year-less G-06 rule, weekday+date year search, day-of-month, 9.4, missing M)."""

from __future__ import annotations

import inspect
from datetime import date, timedelta

import pytest

from actiontrack import codes
from actiontrack.domain import dates as dates_mod
from actiontrack.domain.dates import Confidence, Reason, Resolution, Rule, resolve_phrase, resolve_text

W = codes.W_DUE_BEFORE_MEETING
M1014 = date(2026, 10, 14)


def R(text, m, order=None, *, event=False) -> Resolution:
    res = resolve_text(text, m, order, event=event)
    assert res is not None, text
    return res


def ok(res: Resolution, d: date, conf: str, rule: str, *warnings: str) -> None:
    assert res.date == d
    assert res.confidence == conf
    assert res.rule == rule
    assert res.reason is None
    assert res.warnings == tuple(warnings)


def unresolved(res: Resolution, reason: str) -> None:
    assert res.date is None
    assert res.confidence == Confidence.UNRESOLVED
    assert res.rule is None
    assert res.reason == reason


# ------------------------------------------------------------------ AC-06.8
@pytest.mark.ac("AC-06.8")
def test_ac_06_8_rows():
    ok(R("by 9 Oct".removeprefix("by "), date(2026, 10, 12)), date(2026, 10, 9), "inferred", "yearless_date", W) \
        if False else None
    ok(R("9 Oct", date(2026, 10, 12)), date(2026, 10, 9), "inferred", "yearless_date", W)
    ok(R("Jan 6", date(2026, 12, 20)), date(2027, 1, 6), "inferred", "yearless_date")
    ok(R("Mar 1", M1014), date(2027, 3, 1), "inferred", "yearless_date")
    ok(R("Oct 31", M1014), date(2026, 10, 31), "inferred", "yearless_date")
    ok(R("Sep 16", M1014), date(2026, 9, 16), "inferred", "yearless_date", W)
    unresolved(R("Feb 29", M1014), "invalid_date")


@pytest.mark.ac("AC-06.8")
def test_ac_06_8_gap_days():
    assert (date(2026, 10, 12) - date(2026, 10, 9)).days == 3
    assert (date(2026, 12, 20) - date(2026, 1, 6)).days == 348
    assert (M1014 - date(2026, 3, 1)).days == 227
    assert (M1014 - date(2026, 9, 16)).days == 28


@pytest.mark.ac("AC-06.8")
def test_ac_06_8_sixty_day_boundary():
    assert (M1014 - date(2026, 8, 15)).days == 60 and (M1014 - date(2026, 8, 14)).days == 61
    ok(R("Aug 15", M1014), date(2026, 8, 15), "inferred", "yearless_date", W)
    ok(R("Aug 14", M1014), date(2027, 8, 14), "inferred", "yearless_date")


@pytest.mark.ac("AC-06.8")
def test_ac_06_8_boundary_sweep_every_gap_around_sixty():
    for gap in range(0, 130):
        d = M1014 - timedelta(days=gap)
        if (d.month, d.day) == (2, 29):
            continue
        res = R(f"{d.day} {d.strftime('%b')}", M1014)
        if gap <= dates_mod.PAST_ACCEPT_DAYS:
            ok(res, d, "inferred", "yearless_date", *((W,) if gap else ()))
        else:
            ok(res, date(2027, d.month, d.day), "inferred", "yearless_date")


def test_feb_29_year_choice_is_literal_reading():
    # TODO (note B-17, spec-silent, NOT asserted as normative): M = 2027-12-20 `Feb 29` could mean
    # 2028-02-29, but the literal G-06 reading ("candidate year has no 29 February") gives invalid_date.
    unresolved(R("Feb 29", date(2027, 12, 20)), "invalid_date")
    ok(R("Feb 29", date(2028, 1, 10)), date(2028, 2, 29), "inferred", "yearless_date")
    # past within 60 days in a leap year is accepted with a warning
    ok(R("Feb 29", date(2028, 3, 20)), date(2028, 2, 29), "inferred", "yearless_date", W)
    # next-year candidate that is not a leap year => invalid_date
    unresolved(R("Feb 29", date(2028, 12, 20)), "invalid_date")


# ------------------------------------------------------------------ AC-06.9
@pytest.mark.ac("AC-06.9")
def test_ac_06_9_rows():
    ok(R("Wed 16 Sep", M1014), date(2026, 9, 16), "inferred", "weekday_date", W)
    ok(R("Wed 16 Sep", date(2026, 9, 14)), date(2026, 9, 16), "inferred", "weekday_date")
    ok(R("Tue 16 Sep", date(2026, 9, 1)), date(2025, 9, 16), "inferred", "weekday_date", W)
    unresolved(R("Fri 16 Sep", date(2026, 9, 1)), "weekday_date_mismatch")
    unresolved(R("Wed 16 Sep", None), "no_meeting_date")


@pytest.mark.ac("AC-06.9")
def test_ac_06_9_saturday_10_oct_event_flag():
    m = date(2026, 9, 29)
    ok(R("Saturday 10 Oct", m, event=True), date(2026, 10, 10), "inferred", "event_date")
    ok(R("Saturday 10 Oct", m, event=False), date(2026, 10, 10), "inferred", "weekday_date")


@pytest.mark.ac("AC-06.9")
def test_year_order_is_m_then_next_then_previous():
    ok(R("Thu 16 Sep", M1014), date(2027, 9, 16), "inferred", "weekday_date")
    # M's year wins when it matches even if another year also matches (2026-09-16 is a Wed)
    ok(R("Wed 16 Sep", date(2026, 12, 1)), date(2026, 9, 16), "inferred", "weekday_date", W)


def test_weekday_claim_with_year_less_feb_29():
    ok(R("Tue 29 Feb", date(2028, 1, 1)), date(2028, 2, 29), "inferred", "weekday_date")
    unresolved(R("Mon 29 Feb", date(2026, 1, 1)), "weekday_date_mismatch")


# ------------------------------------------------------------------ table rows
@pytest.mark.ac("AC-06.1", "AC-06.2")
def test_table_rows_m_wed_2026_10_14():
    for t in ("Oct 31", "31 Oct", "31st October"):
        ok(R(t, M1014), date(2026, 10, 31), "inferred", "yearless_date")
    ok(R("the 15th", M1014), date(2026, 10, 15), "inferred", "day_of_month")
    ok(R("15th", M1014), date(2026, 10, 15), "inferred", "day_of_month")
    ok(R("31 Oct 2026", M1014), date(2026, 10, 31), "explicit", "text_date")
    ok(R("Oct 31, 2026", M1014), date(2026, 10, 31), "explicit", "text_date")
    ok(R("2026-10-31", M1014), date(2026, 10, 31), "explicit", "iso_date")
    ok(R("2026/10/31", M1014), date(2026, 10, 31), "explicit", "iso_date")
    unresolved(R("2026-02-30", M1014), "invalid_date")
    unresolved(R("2026-13-01", M1014), "invalid_date")
    unresolved(R("Fri Oct 17 2026", M1014), "weekday_date_mismatch")  # 2026-10-17 is a Saturday
    ok(R("Sat Oct 17 2026", M1014), date(2026, 10, 17), "explicit", "text_date")


@pytest.mark.ac("AC-06.3")
def test_numeric_dates_follow_9_4():
    m = date(2026, 1, 1)
    unresolved(R("03/04/2026", m), "ambiguous_numeric")
    ok(R("03/04/2026", m, "dmy"), date(2026, 4, 3), "explicit", "numeric_date")
    ok(R("03/04/2026", m, "mdy"), date(2026, 3, 4), "explicit", "numeric_date")
    ok(R("13/04/2026", m), date(2026, 4, 13), "explicit", "numeric_date")
    ok(R("04/13/26", m), date(2026, 4, 13), "explicit", "numeric_date")
    ok(R("05/05/2026", m), date(2026, 5, 5), "explicit", "numeric_date")
    unresolved(R("31/02/2026", m), "invalid_date")
    ok(R("03/04/2026", M1014, "dmy"), date(2026, 4, 3), "explicit", "numeric_date", W)
    # year-less numeric follows the year-less rule
    ok(R("10/31", M1014), date(2026, 10, 31), "inferred", "yearless_date")
    unresolved(R("03/04", m), "ambiguous_numeric")
    ok(R("03/04", m, "dmy"), date(2026, 4, 3), "inferred", "yearless_date")
    ok(R("03/04", M1014, "dmy"), date(2027, 4, 3), "inferred", "yearless_date")


@pytest.mark.sc("SC-13")
def test_invalid_without_meeting_date_still_invalid():
    unresolved(R("2026-02-30", None), "invalid_date")
    unresolved(R("03/04/2026", None), "ambiguous_numeric")


# ------------------------------------------------------------------ G-07
@pytest.mark.ac("AC-06.10")
@pytest.mark.parametrize("event", [True, False])
def test_g07_year_bearing_is_explicit_with_own_rule(event):
    ok(R("2026-10-14", M1014, event=event), M1014, "explicit", "iso_date")
    ok(R("14 Oct 2026", M1014, event=event), M1014, "explicit", "text_date")
    ok(R("Wed 14 Oct 2026", M1014, event=event), M1014, "explicit", "text_date")
    ok(R("10/14/2026", M1014, event=event), M1014, "explicit", "numeric_date")
    ok(R("2026-10-14", None, event=event), M1014, "explicit", "iso_date")


def test_g07_yearless_event_is_inferred_event_date():
    ok(R("Oct 14", M1014, event=True), M1014, "inferred", "event_date")
    ok(R("Oct 14", M1014, event=False), M1014, "inferred", "yearless_date")


# ------------------------------------------------------------------ day of month
def test_day_of_month_edges():
    ok(R("the 15th", date(2026, 10, 16)), date(2026, 11, 15), "inferred", "day_of_month")
    ok(R("the 14th", M1014), M1014, "inferred", "day_of_month")  # on/after M
    ok(R("the 15th", date(2026, 12, 20)), date(2027, 1, 15), "inferred", "day_of_month")
    unresolved(R("the 31st", date(2026, 11, 5)), "invalid_date")
    unresolved(R("the 30th", date(2026, 2, 10)), "invalid_date")  # no skipping to March
    ok(R("the 31st", date(2026, 10, 14)), date(2026, 10, 31), "inferred", "day_of_month")
    unresolved(R("the 15th", None), "no_meeting_date")


# ------------------------------------------------------------------ samples
@pytest.mark.parametrize(("text", "m", "expected"), [
    ("10 Sep", date(2026, 9, 3), date(2026, 9, 10)),
    ("18 Sep", date(2026, 9, 14), date(2026, 9, 18)),
    ("2 Oct", date(2026, 9, 24), date(2026, 10, 2)),
    ("Oct 3rd", date(2026, 9, 30), date(2026, 10, 3)),
    ("15 October", date(2026, 9, 30), date(2026, 10, 15)),
    ("9 Oct", date(2026, 10, 5), date(2026, 10, 9)),
    ("Wed 16 Sep", date(2026, 9, 14), date(2026, 9, 16)),
    ("Saturday 10 Oct", date(2026, 9, 29), date(2026, 10, 10)),
    ("7 Oct", date(2026, 9, 30), date(2026, 10, 7)),
    ("25 Sep", date(2026, 9, 21), date(2026, 9, 25)),
    ("12 Oct", date(2026, 10, 2), date(2026, 10, 12)),
    ("23 Oct", date(2026, 10, 9), date(2026, 10, 23)),
    ("15 Oct", date(2026, 10, 12), date(2026, 10, 15)),
])
def test_sample_yearless_phrases_resolve_in_2026_without_warning(text, m, expected):
    res = R(text, m)
    ok(res, expected, "inferred", "weekday_date" if text[:3] in ("Wed", "Sat") else "yearless_date")


def test_sample_iso_and_ac_06_7_inferred():
    ok(R("2026-09-08", date(2026, 9, 1)), date(2026, 9, 8), "explicit", "iso_date")
    assert R("9 Oct", date(2026, 10, 5)).confidence == "inferred"  # AC-06.7


# ------------------------------------------------------------------ purity / SC-10
@pytest.mark.ac("AC-06.4")
def test_signature_has_no_today_and_no_clock_dependence():
    for fn in (resolve_phrase, resolve_text):
        assert "today" not in inspect.signature(fn).parameters
    a = R("tomorrow", M1014)
    b = R("tomorrow", M1014)
    assert a == b


@pytest.mark.sc("SC-10")
def test_missing_meeting_date():
    unresolved(R("tomorrow", None), "no_meeting_date")
    ok(R("2026-11-05", None), date(2026, 11, 5), "explicit", "iso_date")
    ok(R("2026-10-09", M1014), date(2026, 10, 9), "explicit", "iso_date", W)
    for t in ("Oct 31", "the 15th", "Wed 16 Sep", "Friday", "EOD", "end of month", "in 3 days"):
        unresolved(R(t, None), "no_meeting_date")


def test_explicit_on_or_after_meeting_has_no_warning():
    ok(R("2026-10-14", M1014), M1014, "explicit", "iso_date")
    ok(R("2026-10-15", M1014), date(2026, 10, 15), "explicit", "iso_date")


def test_result_types_are_enums():
    res = R("Oct 31", M1014)
    assert isinstance(res.confidence, Confidence) and isinstance(res.rule, Rule)
    assert isinstance(R("Feb 29", M1014).reason, Reason)
