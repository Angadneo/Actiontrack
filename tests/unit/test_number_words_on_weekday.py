"""T-M3-12: number words and ``on <weekday>`` (AC-06.11, SC-70 date half, H-02, H-04, J-02, J-05).

All expected dates are computed here with plain ``datetime`` arithmetic, never with ``actiontrack`` helpers.
"""

from __future__ import annotations

import datetime as dt

import pytest

from actiontrack.domain.dates import locate, resolve_due
from actiontrack.parsing import parse_document

M_WED = dt.date(2026, 10, 14)  # Wednesday (spec 9.3 worked-example date)
M_FRI = dt.date(2026, 10, 16)  # Friday
DAY = dt.timedelta(days=1)

NUMBER_WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _due(text: str, m: dt.date | None = M_WED):
    r = resolve_due(text, m)
    return (
        r.due_date.isoformat() if r.due_date else None,
        r.due_confidence.value,
        r.due_rule.value if r.due_rule else None,
        r.due_reason.value if r.due_reason else None,
    )


# ----------------------------------------------------------------------- AC-06.11, one assertion per statement
@pytest.mark.ac("AC-06.11")
def test_ac_06_11_statement_1_number_words_resolve_relative():
    assert M_WED.weekday() == 2 and (M_WED + 2 * DAY).isoformat() == "2026-10-16"
    assert _due("Priya to pay in two days") == ("2026-10-16", "relative", "in_n_days", None)
    assert (M_WED + 7 * DAY).isoformat() == "2026-10-21"
    assert _due("Priya to pay in a week") == ("2026-10-21", "relative", "in_n_weeks", None)
    assert (M_WED + 21 * DAY).isoformat() == "2026-11-04"
    assert _due("Priya to pay within three weeks") == ("2026-11-04", "relative", "in_n_weeks", None)


@pytest.mark.ac("AC-06.11")
def test_ac_06_11_statement_2_quantity_phrase_is_vague():
    r = resolve_due("Priya to pay in a couple of days", M_WED)
    assert r.due_date is None and r.due_confidence.value == "unresolved"
    assert r.due_reason.value == "vague" and r.due_rule is None
    assert r.due_raw == "in a couple of days" and r.warnings == ()


@pytest.mark.ac("AC-06.11")
@pytest.mark.sc("SC-70")
def test_ac_06_11_statement_3_on_weekday_is_weekday_next_not_event():
    assert (M_WED + 2 * DAY).strftime("%A") == "Friday"
    got = _due("Meera to demo on Friday", M_WED)
    assert got == ("2026-10-16", "relative", "weekday_next", None)
    assert got[2] != "event_date"
    located = locate("Meera to demo on Friday")
    assert [(f.raw, f.introducer, f.role) for f in located] == [("Friday", "on", "due")]


@pytest.mark.ac("AC-06.11")
def test_ac_06_11_statement_4_on_friday_in_friday_meeting_is_same_weekday():
    assert M_FRI.strftime("%A") == "Friday"
    assert _due("Meera to demo on Friday", M_FRI) == (None, "unresolved", None, "same_weekday")


@pytest.mark.ac("AC-06.11")
def test_ac_06_11_demo_for_friday_behaves_like_demo_on_friday():
    assert _due("Meera to demo for Friday", M_WED) == _due("Meera to demo on Friday", M_WED)
    assert _due("Meera to demo for Friday", M_FRI) == (None, "unresolved", None, "same_weekday")
    assert [f.role for f in locate("Meera to demo for Friday")] == ["due"]


# ----------------------------------------------------------------------- number words: boundaries
@pytest.mark.ac("AC-06.11")
@pytest.mark.parametrize("n,word", list(enumerate(NUMBER_WORDS, start=1)))
@pytest.mark.parametrize("prefix", ["in", "within"])
def test_every_number_word_days_and_weeks(n, word, prefix):
    unit_d = "day" if n == 1 else "days"
    unit_w = "week" if n == 1 else "weeks"
    d = (M_WED + n * DAY).isoformat()
    w = (M_WED + 7 * n * DAY).isoformat()
    assert _due(f"Pay {prefix} {word} {unit_d}") == (d, "relative", "in_n_days", None)
    assert _due(f"Pay {prefix} {word} {unit_w}") == (w, "relative", "in_n_weeks", None)
    # the digit form is equivalent
    assert _due(f"Pay {prefix} {n} {unit_d}") == (d, "relative", "in_n_days", None)
    assert _due(f"Pay {prefix} {n} {unit_w}") == (w, "relative", "in_n_weeks", None)


@pytest.mark.ac("AC-06.11")
@pytest.mark.parametrize("article", ["a", "an"])
def test_a_and_an_mean_one_before_singular_units(article):
    assert _due(f"Pay in {article} day") == ((M_WED + DAY).isoformat(), "relative", "in_n_days", None)
    assert _due(f"Pay within {article} week") == ((M_WED + 7 * DAY).isoformat(), "relative", "in_n_weeks", None)
    # singular-only (H-04): a plural unit after `a`/`an` is not a date phrase
    assert _due(f"Pay in {article} days") == (None, "none", None, None)
    assert _due(f"Pay in {article} weeks") == (None, "none", None, None)


@pytest.mark.ac("AC-06.11")
def test_twelve_and_thirteen_boundary_h04_j05():
    assert _due("Pay in twelve days") == ("2026-10-26", "relative", "in_n_days", None)
    assert (M_WED + 12 * DAY).isoformat() == "2026-10-26"
    r = resolve_due("Pay in thirteen days", M_WED)
    assert (r.due_raw, r.due_date, r.due_confidence.value, r.due_rule, r.due_reason) == (
        None, None, "none", None, None)
    assert locate("Pay in thirteen days") == []  # not reported as vague either
    assert _due("Pay within twenty days") == (None, "none", None, None)
    assert _due("Pay in 13 days") == ("2026-10-27", "relative", "in_n_days", None)
    assert (M_WED + 13 * DAY).isoformat() == "2026-10-27"


@pytest.mark.ac("AC-06.11")
@pytest.mark.parametrize("text", ["Pay in an hour", "Pay in a month", "Pay in a minute", "Pay in an hour or so"])
def test_non_day_week_units_are_not_phrases(text):
    assert _due(text) == (None, "none", None, None)


@pytest.mark.ac("AC-06.11")
@pytest.mark.parametrize(
    "text,expect",
    [
        ("Pay IN TWO DAYS", ("2026-10-16", "relative", "in_n_days", None)),
        ("Pay Within Three Weeks", ("2026-11-04", "relative", "in_n_weeks", None)),
        ("Pay In A Week", ("2026-10-21", "relative", "in_n_weeks", None)),
        ("Pay WITHIN A DAY", ("2026-10-15", "relative", "in_n_days", None)),
    ],
)
def test_case_variants(text, expect):
    assert _due(text) == expect


@pytest.mark.ac("AC-06.11")
@pytest.mark.parametrize("phrase", [
    "in a couple of days", "in a couple of weeks", "in a few days", "in a few weeks",
    "in a fortnight", "within a couple of days", "within a few weeks", "within a fortnight",
    "IN A FEW WEEKS", "In A Fortnight",
])
def test_quantity_phrases_are_vague_never_none_or_dated(phrase):
    r = resolve_due(f"Pay {phrase}", M_WED)
    assert r.due_confidence.value == "unresolved" and r.due_reason.value == "vague"
    assert r.due_date is None and r.due_rule is None
    assert r.due_raw.lower() == phrase.lower()


@pytest.mark.ac("AC-06.11")
def test_trailing_punctuation_and_time_words_do_not_change_the_date():
    assert _due("Pay in a week.") == ("2026-10-21", "relative", "in_n_weeks", None)
    assert _due("Pay in two days!") == ("2026-10-16", "relative", "in_n_days", None)


@pytest.mark.ac("AC-06.11")
def test_number_word_phrases_across_month_and_year_boundaries():
    m = dt.date(2026, 12, 30)
    assert _due("Pay in two days", m)[0] == (m + 2 * DAY).isoformat() == "2027-01-01"
    assert _due("Pay within twelve weeks", m)[0] == (m + 84 * DAY).isoformat()
    assert _due("Pay in two days", None) == (None, "unresolved", None, "no_meeting_date")


# ----------------------------------------------------------------------- on <weekday> semantics
def _first_strictly_after(m: dt.date, wd: int) -> dt.date:
    d = m + DAY
    while d.weekday() != wd:
        d += DAY
    return d


@pytest.mark.ac("AC-06.11")
@pytest.mark.parametrize("intro", ["on", "for"])
def test_on_and_for_weekday_over_every_meeting_weekday(intro):
    for offset in range(7):  # meetings Mon 2026-10-12 .. Sun 2026-10-18
        m = dt.date(2026, 10, 12) + offset * DAY
        for wd, name in enumerate(WEEKDAY_NAMES):
            for spelled in (name, name[:3], name.lower()):
                got = _due(f"Team to demo {intro} {spelled}", m)
                if wd == m.weekday():
                    assert got == (None, "unresolved", None, "same_weekday"), (m, spelled)
                else:
                    assert got == (_first_strictly_after(m, wd).isoformat(), "relative", "weekday_next", None), (m, spelled)
                assert [f.role for f in locate(f"Team to demo {intro} {spelled}")] == ["due"]


@pytest.mark.ac("AC-06.11")
def test_on_weekday_matches_by_weekday():
    for m in (M_WED, M_FRI, dt.date(2026, 10, 17)):
        for name in WEEKDAY_NAMES:
            assert _due(f"Demo on {name}", m) == _due(f"Demo by {name}", m)


@pytest.mark.ac("AC-06.10", "AC-06.11")
def test_contrast_yearless_date_after_on_is_event_but_full_date_is_not():
    assert _due("Meera to demo on Oct 14", M_WED) == ("2026-10-14", "inferred", "event_date", None)
    assert _due("Meera to demo on 2026-10-14", M_WED) == ("2026-10-14", "explicit", "iso_date", None)
    assert _due("Meera to demo on 14 Oct 2026", M_WED) == ("2026-10-14", "explicit", "text_date", None)
    assert _due("Sam to schedule cutover for Saturday 10 Oct", dt.date(2026, 9, 29)) == (
        "2026-10-10", "inferred", "event_date", None)
    assert dt.date(2026, 10, 10).strftime("%A") == "Saturday"


@pytest.mark.ac("AC-06.11")
def test_on_weekday_keeps_time_words_and_ignores_them_for_the_date():
    r = resolve_due("Demo on Friday EOD", M_WED)
    assert r.due_raw == "Friday EOD" and r.due_date == dt.date(2026, 10, 16) and r.due_rule.value == "weekday_next"
    assert resolve_due("Demo on Friday EOD", M_FRI).due_reason.value == "same_weekday"


@pytest.mark.ac("AC-06.5", "AC-06.11")
def test_on_weekday_twice_is_one_phrase_but_two_weekdays_are_multiple_dates():
    same = resolve_due("Demo on Friday and review by Friday", M_WED)
    assert same.due_date == dt.date(2026, 10, 16) and same.due_reason is None
    two = resolve_due("Demo on Friday and review on Monday", M_WED)
    assert two.due_reason.value == "multiple_dates" and two.due_raw == "Friday; Monday"


# ----------------------------------------------------------------------- SC-70 date half through parse_document
def _parse(lines: str, date: str | None = "2026-10-14"):
    head = f"# Meeting: Demo\nDate: {date}\nAttendees: Meera Iyer\n\n## Actions\n" if date else "# Meeting: Demo\n\n## Actions\n"
    return parse_document((head + lines).encode(), "n.md")


@pytest.mark.ac("AC-06.11")
@pytest.mark.sc("SC-70")
def test_sc70_date_half_note_wed_2026_10_14():
    pf = _parse("- [ ] Meera to demo on Friday\n")
    (it,) = pf.items
    d = it.due_hook
    assert d.due_date == dt.date(2026, 10, 16) and d.due_rule.value == "weekday_next"
    assert d.due_confidence.value == "relative" and d.due_raw == "Friday"
    assert it.owner_hook is None  # owner resolution is M4
    assert it.task_text == "Meera to demo"
    assert [w.code for w in pf.warnings] == []


@pytest.mark.ac("AC-06.11")
def test_parse_document_number_words_and_same_weekday_end_to_end():
    pf = _parse(
        "- [ ] A to pay in two days\n- [ ] B to pay in a week\n- [ ] C to pay within three weeks\n"
        "- [ ] D to pay in a couple of days\n- [ ] E to pay in thirteen days\n- [ ] F to pay in 13 days\n"
    )
    got = [(i.due_hook.due_date.isoformat() if i.due_hook.due_date else None, i.due_hook.due_confidence.value,
            i.due_hook.due_rule.value if i.due_hook.due_rule else None,
            i.due_hook.due_reason.value if i.due_hook.due_reason else None) for i in pf.items]
    assert got == [
        ("2026-10-16", "relative", "in_n_days", None),
        ("2026-10-21", "relative", "in_n_weeks", None),
        ("2026-11-04", "relative", "in_n_weeks", None),
        (None, "unresolved", None, "vague"),
        (None, "none", None, None),
        ("2026-10-27", "relative", "in_n_days", None),
    ]
    assert [i.task_text for i in pf.items][4] == "E to pay in thirteen days"  # nothing removed: not a date phrase
    fri = _parse("- [ ] Meera to demo on Friday\n- [ ] Meera to demo for Friday\n", "2026-10-16")
    assert [i.due_hook.due_reason.value for i in fri.items] == ["same_weekday", "same_weekday"]
    assert all(i.due_hook.due_date is None and i.due_hook.due_rule is None for i in fri.items)
    nodate = _parse("- [ ] Meera to demo on Friday\n- [ ] Pay in two days\n", None)
    assert [i.due_hook.due_reason.value for i in nodate.items] == ["no_meeting_date", "no_meeting_date"]
