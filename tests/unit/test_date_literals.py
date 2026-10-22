"""T-M3-02: absolute date literal parser (ISO, textual, numeric, year-less, weekday prefix, invalid)."""

from __future__ import annotations

from datetime import date

import pytest

from actiontrack.domain.dates import (
    Invalid,
    Literal,
    LiteralMatch,
    YearlessDate,
    match_literal_at,
    parse_literal,
)

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def full(y, m, d, rule, claim=None):
    return ("full", date(y, m, d), rule, claim)


def yl(m, d, claim=None):
    return ("yl", m, d, claim)


def inv(reason):
    return ("inv", reason)


def shape(res):
    if isinstance(res, Literal):
        return ("full", res.date, res.rule, res.weekday_claim)
    if isinstance(res, YearlessDate):
        return ("yl", res.month, res.day, res.weekday_claim)
    if isinstance(res, Invalid):
        return ("inv", res.reason)
    return res


# (text, date_order, expected)
CASES = [
    # --- spec 9.3 rows 1-3
    ("2026-10-31", None, full(2026, 10, 31, "iso_date")),
    ("2026/10/31", None, full(2026, 10, 31, "iso_date")),
    ("31 Oct 2026", None, full(2026, 10, 31, "text_date")),
    ("Oct 31, 2026", None, full(2026, 10, 31, "text_date")),
    ("Oct 31", None, yl(10, 31)),
    ("31 Oct", None, yl(10, 31)),
    ("31st October", None, yl(10, 31)),
    # --- ISO with time/zone suffix (calendar date as written)
    ("2026-10-31T23:30:00Z", None, full(2026, 10, 31, "iso_date")),
    ("2026-10-31 09:15", None, full(2026, 10, 31, "iso_date")),
    ("2026-10-31T23:30:00+05:30", None, full(2026, 10, 31, "iso_date")),
    # --- textual with year
    ("31st October 2026", None, full(2026, 10, 31, "text_date")),
    ("Oct 3rd 2026", None, full(2026, 10, 3, "text_date")),
    ("14 Oct 2026", None, full(2026, 10, 14, "text_date")),
    ("1 January 2027", None, full(2027, 1, 1, "text_date")),
    ("29 Feb 2028", None, full(2028, 2, 29, "text_date")),
    ("october 5, 2026", None, full(2026, 10, 5, "text_date")),
    ("16 SEPT 2026", None, full(2026, 9, 16, "text_date")),
    # --- AC-06.2 numeric
    ("03/04/2026", None, inv("ambiguous_numeric")),
    ("03/04/2026", "dmy", full(2026, 4, 3, "numeric_date")),
    ("03/04/2026", "mdy", full(2026, 3, 4, "numeric_date")),
    ("13/04/2026", None, full(2026, 4, 13, "numeric_date")),
    ("04/13/2026", None, full(2026, 4, 13, "numeric_date")),
    ("05/05", None, yl(5, 5)),
    ("05/05/2026", None, full(2026, 5, 5, "numeric_date")),
    ("03/04/26", "dmy", full(2026, 4, 3, "numeric_date")),
    ("03/04", None, inv("ambiguous_numeric")),
    ("03/04", "mdy", yl(3, 4)),
    ("13/04", None, yl(4, 13)),
    # --- AC-06.3 invalid
    ("2026-02-30", None, inv("invalid_date")),
    ("2026-13-01", None, inv("invalid_date")),
    ("31/04/2026", None, inv("invalid_date")),
    ("31 Apr", None, inv("invalid_date")),
    ("30 Feb", None, inv("invalid_date")),
    ("29 Feb 2026", None, inv("invalid_date")),
    ("13/13/2026", None, inv("invalid_date")),
    ("2026/02/29", None, inv("invalid_date")),
    # --- AC-06.8/06.9 year-less literals
    ("9 Oct", None, yl(10, 9)),
    ("Jan 6", None, yl(1, 6)),
    ("Mar 1", None, yl(3, 1)),
    ("Sep 16", None, yl(9, 16)),
    ("Feb 29", None, yl(2, 29)),
    ("Aug 15", None, yl(8, 15)),
    ("Aug 14", None, yl(8, 14)),
    ("Sept 16", None, yl(9, 16)),
    # --- weekday prefix
    ("Wed 16 Sep", None, yl(9, 16, WED)),
    ("Tue 16 Sep", None, yl(9, 16, TUE)),
    ("Fri 16 Sep", None, yl(9, 16, FRI)),
    ("Saturday 10 Oct", None, yl(10, 10, SAT)),
    ("Mon, 14 September 2026", None, full(2026, 9, 14, "text_date", MON)),
    ("Monday, 14 September 2026", None, full(2026, 9, 14, "text_date", MON)),
    ("Fri Oct 17 2026", None, full(2026, 10, 17, "text_date", FRI)),
    ("Thurs 3rd Sep", None, yl(9, 3, THU)),
    ("Wed. 16 Sep", None, yl(9, 16, WED)),
    # --- sample phrases
    ("10 Sep", None, yl(9, 10)),
    ("18 Sep", None, yl(9, 18)),
    ("2 Oct", None, yl(10, 2)),
    ("25 Sep", None, yl(9, 25)),
    ("12 Oct", None, yl(10, 12)),
    ("23 Oct", None, yl(10, 23)),
    ("15 Oct", None, yl(10, 15)),
    ("7 Oct", None, yl(10, 7)),
    ("15 October", None, yl(10, 15)),
    ("Oct 3rd", None, yl(10, 3)),
    ("Oct 5th", None, yl(10, 5)),
    ("2026-09-08", None, full(2026, 9, 8, "iso_date")),
    ("2026-10-16", None, full(2026, 10, 16, "iso_date")),
    # --- non-dates
    ("10%", None, None),
    ("6 months", None, None),
    ("5 customer", None, None),
    ("50 invoices", None, None),
    ("the three new leads", None, None),
    ("10", None, None),
    ("2026", None, None),
    ("October", None, None),
    ("Oct 2026", None, None),
    ("", None, None),
    ("   ", None, None),
    ("Friday", None, None),
    ("2026-10", None, None),
    ("2026-10/31", None, None),
    ("31 Octopus", None, None),
    ("1/2/3/4", None, None),
    ("12026-10-31", None, None),
    ("31 Oct 2026 and more", None, None),
]


@pytest.mark.ac("AC-06.2", "AC-06.3", "AC-06.8", "AC-06.9")
@pytest.mark.sc("SC-13")
@pytest.mark.parametrize(("text", "order", "expected"), CASES, ids=[f"{c[0]!r}-{c[1]}" for c in CASES])
def test_parse_literal_table(text, order, expected):
    assert shape(parse_literal(text, order)) == expected


def test_table_size():
    assert len(CASES) >= 45


@pytest.mark.ac("AC-06.2")
def test_surrounding_whitespace_ignored():
    assert shape(parse_literal("  2026-10-31  ")) == full(2026, 10, 31, "iso_date")


def test_raw_is_matched_text():
    res = parse_literal("Saturday 10 Oct")
    assert isinstance(res, YearlessDate) and res.raw == "Saturday 10 Oct"
    assert parse_literal("Oct 31, 2026").raw == "Oct 31, 2026"


@pytest.mark.ac("AC-06.9")
def test_weekday_claim_with_year_is_kept_for_later_check():
    res = parse_literal("Fri Oct 17 2026")
    assert isinstance(res, Literal)
    assert res.date.weekday() == SAT and res.weekday_claim == FRI


def test_year_less_has_no_year_attribute():
    res = parse_literal("Oct 31")
    assert isinstance(res, YearlessDate) and not hasattr(res, "year")


@pytest.mark.parametrize(
    ("text", "pos", "start", "end", "expected"),
    [
        ("by Oct 31 please", 3, 3, 9, yl(10, 31)),
        ("proposal by 2026-10-09 ok", 12, 12, 22, full(2026, 10, 9, "iso_date")),
        ("on Wed 16 Sep.", 3, 3, 13, yl(9, 16, WED)),
        ("due 31 Oct 2026, thanks", 4, 4, 15, full(2026, 10, 31, "text_date")),
        ("(due: 03/04/26)", 6, 6, 14, inv("ambiguous_numeric")),
        ("cut by 10% - by 9 Oct", 16, 16, 21, yl(10, 9)),
        ("ship 2026-10-31T10:00Z!", 5, 5, 22, full(2026, 10, 31, "iso_date")),
    ],
)
def test_match_literal_at_spans(text, pos, start, end, expected):
    m = match_literal_at(text, pos)
    assert isinstance(m, LiteralMatch)
    assert (m.start, m.end) == (start, end)
    assert text[m.start:m.end] == m.result.raw
    assert shape(m.result) == expected


@pytest.mark.parametrize(
    ("text", "pos"),
    [
        ("by 10% now", 3),
        ("in 6 months", 3),
        ("by Oct 31", 0),  # no literal starts at 'by'
        ("x2026-10-31", 1),  # glued to a word
        ("5 customer", 0),
        ("the 31 Oct", 0),
        ("abc", 5),
        ("abc", -1),
        ("", 0),
    ],
)
def test_match_literal_at_none(text, pos):
    assert match_literal_at(text, pos) is None


def test_match_prefers_longest_and_respects_date_order():
    m = match_literal_at("31 Oct 2026, then 03/04", 0)
    assert m is not None and m.end == 11
    m2 = match_literal_at("then 03/04", 5, "dmy")
    assert m2 is not None and shape(m2.result) == yl(4, 3)


def test_weekday_not_consumed_without_date():
    assert match_literal_at("Friday Oct", 0) is None
    m = match_literal_at("Friday 10 Oct", 0)
    assert m is not None and shape(m.result) == yl(10, 10, FRI)
