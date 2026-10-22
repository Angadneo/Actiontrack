"""T-M3-01: calendar primitives, vocabulary tables, Rule/Reason/Confidence enums."""

from __future__ import annotations

import re
from datetime import date

import pytest

from actiontrack import codes
from actiontrack.domain import dates as D

REPO = __import__("pathlib").Path(__file__).resolve().parents[2]
SPEC = REPO / "specs" / "actiontrack-spec.md"


def test_monday_of():
    assert D.monday_of(date(2026, 10, 14)) == date(2026, 10, 12)
    assert D.monday_of(date(2026, 10, 18)) == date(2026, 10, 12)  # Sunday
    assert D.monday_of(date(2026, 10, 12)) == date(2026, 10, 12)
    assert D.monday_of(date(2027, 1, 1)) == date(2026, 12, 28)  # year boundary


def test_iso_week_shift_and_weekday_in_week():
    assert D.iso_week_shift(date(2026, 10, 14), 1) == date(2026, 10, 21)
    assert D.iso_week_shift(date(2026, 10, 14), -2) == date(2026, 9, 30)
    assert D.weekday_in_week(date(2026, 10, 14), 4, 0) == date(2026, 10, 16)
    assert D.weekday_in_week(date(2026, 10, 14), 4, 1) == date(2026, 10, 23)  # next Friday
    assert D.weekday_in_week(date(2026, 10, 5), 1, 1) == date(2026, 10, 13)  # next Tuesday, M=Mon
    assert D.weekday_in_week(date(2026, 10, 14), 0, 1) == date(2026, 10, 19)  # Monday next week
    with pytest.raises(ValueError):
        D.weekday_in_week(date(2026, 10, 14), 7, 0)


@pytest.mark.parametrize(
    ("start", "weekday", "expected"),
    [
        (date(2026, 10, 14), 4, date(2026, 10, 16)),
        (date(2026, 10, 14), 2, date(2026, 10, 21)),  # same weekday: strictly after
        (date(2026, 10, 17), 4, date(2026, 10, 23)),
        (date(2026, 10, 16), 4, date(2026, 10, 23)),
        (date(2026, 9, 1), 3, date(2026, 9, 3)),
    ],
)
def test_first_weekday_strictly_after(start, weekday, expected):
    assert D.first_weekday_strictly_after(start, weekday) == expected


@pytest.mark.parametrize(
    ("y", "m", "last"),
    [(2026, 2, 28), (2028, 2, 29), (2026, 11, 30), (2026, 12, 31), (2100, 2, 28), (2000, 2, 29)],
)
def test_last_day_of_month(y, m, last):
    assert D.last_day_of_month(y, m) == last


def test_add_months():
    assert D.add_months(2026, 12, 1) == (2027, 1)
    assert D.add_months(2026, 10, 1) == (2026, 11)
    assert D.add_months(2026, 1, -1) == (2025, 12)
    assert D.add_months(2026, 5, 0) == (2026, 5)
    assert D.add_months(2026, 11, 14) == (2028, 1)


def test_is_valid_ymd():
    assert D.is_valid_ymd(2028, 2, 29)
    assert not D.is_valid_ymd(2026, 2, 29)
    assert not D.is_valid_ymd(2026, 4, 31)
    assert not D.is_valid_ymd(2026, 13, 1)
    assert not D.is_valid_ymd(2026, 0, 1)
    assert not D.is_valid_ymd(2026, 1, 0)
    assert not D.is_valid_ymd(0, 1, 1)


def test_days_between():
    assert D.days_between(date(2026, 9, 16), date(2026, 10, 14)) == 28
    assert D.days_between(date(2026, 3, 1), date(2026, 10, 14)) == 227
    assert D.days_between(date(2026, 1, 6), date(2026, 12, 20)) == 348
    assert D.days_between(date(2026, 10, 14), date(2026, 10, 12)) == -2


@pytest.mark.parametrize(
    ("d", "weekday"),
    [(date(2026, 10, 14), "wed"), (date(2026, 9, 16), "wed"), (date(2026, 10, 10), "sat"),
     (date(2026, 10, 17), "sat"), (date(2026, 9, 1), "tue"), (date(2025, 9, 16), "tue"),
     (date(2026, 10, 16), "fri"), (date(2026, 10, 5), "mon")],
)
def test_weekday_tables_match_real_calendar(d, weekday):
    assert D.WEEKDAYS[weekday] == d.weekday()


def test_weekday_and_month_tables():
    assert {"tues", "thur", "thurs", "monday", "sun"} <= set(D.WEEKDAYS)
    assert set(D.WEEKDAYS.values()) == set(range(7))
    assert D.MONTHS["sept"] == 9 and D.MONTHS["september"] == 9 and D.MONTHS["sep"] == 9
    assert set(D.MONTHS.values()) == set(range(1, 13))
    assert len(D.WEEKDAY_NAME_ROWS) == len(D.WEEKDAYS)
    assert len(D.MONTH_NAME_ROWS) == len(D.MONTHS)
    assert D.ORDINAL_SUFFIXES == ("st", "nd", "rd", "th")


def test_tables_have_no_duplicates_and_are_lowercase():
    for table in (D.VAGUE_WORDS, D.RANGE_WORDS, D.DUE_INTRODUCERS, D.EVENT_INTRODUCERS,
                  D.NOT_DUE_INTRODUCERS, D.TIME_OF_DAY_WORDS, D.VAGUE_QUANTITY_PHRASES,
                  D.ORDINAL_SUFFIXES):
        assert len(set(table)) == len(table)
        assert all(s == s.lower() for s in table)
    assert {"soon", "later", "asap", "tbd", "ongoing"} == set(D.VAGUE_WORDS)
    assert {"this week", "next week", "this month", "next month"} <= set(D.RANGE_WORDS)
    assert {"eod", "cob", "noon", "midnight"} == set(D.TIME_OF_DAY_WORDS)
    assert {"by", "due", "due on", "before", "until", "till", "on", "no later than",
            "deadline:", "(due:", "[due:"} <= set(D.DUE_INTRODUCERS)
    assert set(D.VAGUE_QUANTITY_PHRASES) == {"a couple of", "a few", "a fortnight"}


def test_time_of_day_pattern():
    rx = re.compile(D.TIME_OF_DAY_PATTERN, re.IGNORECASE)
    for s in ("5pm", "5 PM", "10:30am", "12 am"):
        assert rx.fullmatch(s), s
    for s in ("13", "5 customers", "pm"):
        assert not rx.fullmatch(s), s


def test_number_words():
    expected = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                "ten", "eleven", "twelve"]
    assert set(D.NUMBER_WORDS) == set(expected) | {"a", "an"}
    assert [D.NUMBER_WORDS[w] for w in expected] == list(range(1, 13))
    assert D.NUMBER_WORDS["a"] == D.NUMBER_WORDS["an"] == 1
    assert D.SINGULAR_ONLY_NUMBER_WORDS == {"a", "an"}
    assert "thirteen" not in D.NUMBER_WORDS and "twenty" not in D.NUMBER_WORDS


def test_past_accept_days():
    assert D.PAST_ACCEPT_DAYS == 60


def _spec_rule_ids() -> set[str]:
    text = SPEC.read_text(encoding="utf-8")
    block = text.split("**`due_rule` catalogue", 1)[1].split("Unresolved dates store", 1)[0]
    ids: set[str] = set()
    for line in block.splitlines():
        if line.startswith("|") and "---" not in line and "Rule id" not in line:
            first = line.split("|")[1]
            ids.update(re.findall(r"`([a-z_]+)`", first))
    return ids


def test_rule_enum_matches_spec_table():
    ids = _spec_rule_ids()
    assert len(ids) == 21
    assert {r.value for r in D.Rule} == ids == set(codes.DUE_RULES)
    assert D.Rule.ISO_DATE == "iso_date" and D.Rule("in_n_weeks") is D.Rule.IN_N_WEEKS


def test_reason_enum_matches_appendix_b():
    text = SPEC.read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("Due-unresolved reasons:"))
    listed = set(re.findall(r"`([a-z_]+)`", line.split(".")[0]))
    assert len(listed) == 10
    assert {r.value for r in D.Reason} == listed == set(codes.DUE_REASONS)
    assert D.Reason.AMBIGUOUS_NUMERIC == "ambiguous_numeric"


def test_confidence_enum():
    assert [c.value for c in D.Confidence] == ["explicit", "relative", "inferred", "unresolved", "none"]


def test_module_does_not_read_the_clock():
    src = (REPO / "src" / "actiontrack" / "domain" / "dates.py").read_text(encoding="utf-8")
    assert not re.search(r"\btoday\(|\bnow\(|\butcnow\(|^\s*(import|from) +(time|os|pathlib)\b", src, re.M)
