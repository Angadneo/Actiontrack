"""T-M3-03: phrase recogniser, every spec 9.3 phrase shape classified (``vague`` vs ``range``)."""

from __future__ import annotations

import pytest

from actiontrack.domain.dates import PHRASE_KINDS, Phrase, classify_phrase

# (text, kind, time_words)
TABLE: list[tuple[str, str, tuple[str, ...]]] = [
    # absolute / year-less / weekday+date / day-of-month
    ("2026-10-31", "absolute", ()),
    ("2026/10/31", "absolute", ()),
    ("31 Oct 2026", "absolute", ()),
    ("Oct 31, 2026", "absolute", ()),
    ("Oct 31", "yearless", ()),
    ("31 Oct", "yearless", ()),
    ("31st October", "yearless", ()),
    ("10 Sep", "yearless", ()),
    ("Oct 3rd", "yearless", ()),
    ("15 October", "yearless", ()),
    ("Wed 16 Sep", "weekday_date", ()),
    ("Saturday 10 Oct", "weekday_date", ()),
    ("Fri Oct 17 2026", "weekday_date", ()),
    ("the 15th", "day_of_month", ()),
    ("15th", "day_of_month", ()),
    ("2026-02-30", "invalid", ()),
    ("03/04/2026", "invalid", ()),
    # relative days
    ("today", "today", ()),
    ("tonight", "today", ()),
    ("this morning", "today", ()),
    ("this afternoon", "today", ()),
    ("EOD", "eod_alone", ("EOD",)),
    ("COB", "eod_alone", ("COB",)),
    ("tomorrow", "tomorrow", ()),
    ("yesterday", "yesterday", ()),
    ("EOD tomorrow", "tomorrow", ("EOD",)),
    ("tomorrow 5pm", "tomorrow", ("5pm",)),
    ("tomorrow at 5 pm", "tomorrow", ("5 pm",)),
    # weekdays
    ("Friday", "weekday", ()),
    ("Thursday", "weekday", ()),
    ("Wed", "weekday", ()),
    ("Mon", "weekday", ()),
    ("Monday", "weekday", ()),
    ("Wednesday", "weekday", ()),
    ("Tuesday", "weekday", ()),
    ("Fri EOD", "weekday", ("EOD",)),
    ("EOD Friday", "weekday", ("EOD",)),
    ("Friday 10:30am", "weekday", ("10:30am",)),
    ("this Friday", "this_weekday", ()),
    ("next Friday", "next_weekday", ()),
    ("next Tuesday", "next_weekday", ()),
    ("Monday next week", "monday_next_week", ()),
    ("Friday next week", "monday_next_week", ()),
    # end of period
    ("end of week", "end_of_week", ()),
    ("EOW", "end_of_week", ()),
    ("end of the week", "end_of_week", ()),
    ("end of next week", "end_of_next_week", ()),
    ("end of month", "end_of_month", ()),
    ("EOM", "end_of_month", ()),
    ("end of the month", "end_of_month", ()),
    ("end of next month", "end_of_next_month", ()),
    # offsets (digits and number words)
    ("in 3 days", "in_n", ()),
    ("within 2 weeks", "in_n", ()),
    ("in 13 days", "in_n", ()),
    ("in two days", "in_n", ()),
    ("in a week", "in_n", ()),
    ("within three weeks", "in_n", ()),
    ("in twelve days", "in_n", ()),
    ("in an day", "in_n", ()),
    # range
    ("this week", "range", ()),
    ("next week", "range", ()),
    ("this month", "range", ()),
    ("next month", "range", ()),
    ("week of Oct 19", "range", ()),
    ("the week of 2026-10-19", "range", ()),
    # vague
    ("soon", "vague", ()),
    ("later", "vague", ()),
    ("ASAP", "vague", ()),
    ("TBD", "vague", ()),
    ("ongoing", "vague", ()),
    ("Q4", "vague", ()),
    ("end of Q4", "vague", ()),
    ("mid-month", "vague", ()),
    ("mid-October", "vague", ()),
    ("for November", "vague", ()),
    ("November", "vague", ()),
    ("after the launch", "vague", ()),
    ("in a couple of days", "vague", ()),
    ("in a few weeks", "vague", ()),
    ("in a fortnight", "vague", ()),
    ("within a few days", "vague", ()),
    # case-insensitivity
    ("FRIDAY", "weekday", ()),
    ("Eod Tomorrow", "tomorrow", ("Eod",)),
    ("IN TWO DAYS", "in_n", ()),
    ("NEXT WEEK", "range", ()),
    ("END OF NEXT MONTH", "end_of_next_month", ()),
]


def test_table_is_large_enough_and_kinds_known():
    assert len(TABLE) >= 70
    assert {k for _, k, _ in TABLE} <= set(PHRASE_KINDS)
    # every kind but "invalid" is exercised by the table
    assert set(PHRASE_KINDS) <= {k for _, k, _ in TABLE}


@pytest.mark.ac("AC-06.1", "AC-06.10", "AC-06.11")
@pytest.mark.parametrize(("text", "kind", "time_words"), TABLE)
def test_phrase_kind(text, kind, time_words):
    p = classify_phrase(text)
    assert p is not None, text
    assert (p.kind, p.time_words) == (kind, time_words)
    assert p.raw == text


@pytest.mark.parametrize("text", [
    "staging", "monitoring", "the next review", "cutover", "CRM", "accessibility",
    "all admin accounts", "10%", "10", "", "   ", "noon", "5pm", "midnight",
    "in thirteen days", "within twenty days", "in 0 days", "in a days", "in 2 months",
    "6 months", "50 invoices", "5 customer quotes", "Friday's meeting", "next review",
    "the 32nd", "week of nothing", "after the cutover", "Mon-itor",
])
def test_not_a_phrase(text):
    assert classify_phrase(text) is None


def test_mon_inside_word_is_not_weekday():
    for w in ("monitoring", "monday-ish", "sunny", "wedding", "satisfaction", "marching", "mayor"):
        assert classify_phrase(w) is None


@pytest.mark.ac("AC-06.10")
def test_range_vs_vague_clause():
    for t in ("next week", "this week", "next month", "this month", "week of Oct 19"):
        assert classify_phrase(t).kind == "range"
    for t in ("soon", "Q4", "mid-October", "for November", "after the launch", "later", "ASAP",
              "TBD", "ongoing", "mid-month", "end of Q4"):
        assert classify_phrase(t).kind == "vague"


@pytest.mark.ac("AC-06.11")
def test_number_words_and_quantity_phrases():
    for t in ("in two days", "in a week", "within three weeks", "in twelve days"):
        assert classify_phrase(t).kind == "in_n"
    for t in ("in a couple of days", "in a few weeks", "in a fortnight"):
        assert classify_phrase(t).kind == "vague"
    assert classify_phrase("in thirteen days") is None
    assert classify_phrase("in 13 days").kind == "in_n"


def test_parameters_extracted():
    assert classify_phrase("in two days") == Phrase("in_n", "in two days", n=2, unit="days")
    assert classify_phrase("within three weeks").n == 3
    assert classify_phrase("within three weeks").unit == "weeks"
    assert classify_phrase("in a week").n == 1 and classify_phrase("in a week").unit == "weeks"
    assert classify_phrase("Friday").weekday == 4
    assert classify_phrase("next tues").weekday == 1
    assert classify_phrase("the 15th").day == 15
    assert classify_phrase("Wed 16 Sep").weekday == 2
    assert classify_phrase("Wed 16 Sep").yearless is True
    assert classify_phrase("Wed 16 Sep 2026").yearless is False
    assert classify_phrase("2026-02-30").reason == "invalid_date"
    assert classify_phrase("03/04/2026").reason == "ambiguous_numeric"
    assert classify_phrase("03/04/2026", "dmy").kind == "absolute"


def test_compound_time_words_split_off():
    p = classify_phrase("EOD tomorrow")
    assert (p.kind, p.time_words) == ("tomorrow", ("EOD",))
    p = classify_phrase("Fri EOD")
    assert (p.kind, p.time_words) == ("weekday", ("EOD",))
    p = classify_phrase("EOD Friday 5pm")
    assert (p.kind, p.time_words) == ("weekday", ("EOD", "5pm"))


def test_every_9_3_row_maps_to_exactly_one_kind():
    seen = {}
    for text, kind, _ in TABLE:
        assert seen.setdefault(text.lower(), kind) == kind
