"""T-M3-13: year-less rule acceptance and property tests (AC-06.8, AC-06.9, SC-63 unit half, G-06).

Spec 9.3 "Year-less rule (G-06)":
  (1) with a weekday (``Wed 16 Sep``): the year is the first of M.year, M.year+1, M.year-1 that makes the
      weekday match; none => ``weekday_date_mismatch``;
  (2) without a weekday: M's year; earlier than M by <= 60 days => accepted as past (+ ``W-DUE-BEFORE-MEETING``);
      earlier by more than 60 days => next year; a date that does not exist in the chosen year => ``invalid_date``.

``Feb 29`` decision (note B-17, J-04): the spec is silent on how the year is *chosen* for a date that exists
only in some years. The default adopted by the spec (J-04) is: unresolved ``invalid_date`` when the candidate year
(M's year, or the next year under the 60-day rule) has no 29 February; no search of later years. The exhaustive
boundary sweep and the property tests therefore EXCLUDE ``Feb 29`` (it is only exercised by the explicit J-04
tests at the bottom, plus the seeded-random test whose oracle encodes J-04 for the no-weekday branch only).

All expectations use independent ``datetime`` arithmetic (``_oracle_*`` below), never actiontrack's helpers.
Hypothesis is not installed: property tests use ``random.Random(seed)``.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from actiontrack.domain.dates import resolve_due

D = dt.date
DAY = dt.timedelta(days=1)
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WDN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WDF = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BEFORE = "W-DUE-BEFORE-MEETING"

# 12 representative year-less dates, no Feb 29
REPRESENTATIVE = [(1, 1), (1, 6), (2, 28), (3, 1), (4, 30), (5, 15), (6, 30), (7, 4), (8, 31), (9, 16), (10, 31), (12, 31)]


def _mk(y: int, mo: int, d: int) -> dt.date | None:
    try:
        return D(y, mo, d)
    except ValueError:
        return None


def _oracle_yearless(m: dt.date, mo: int, d: int):
    """(date | None, warns, reason) per spec 9.3 rule (2)."""
    cand = _mk(m.year, mo, d)
    if cand is None:
        return None, False, "invalid_date"
    if cand >= m:
        return cand, False, None
    if (m - cand).days <= 60:
        return cand, True, None
    nxt = _mk(m.year + 1, mo, d)
    if nxt is None:
        return None, False, "invalid_date"
    return nxt, False, None


def _oracle_weekday(m: dt.date, mo: int, d: int, wd: int):
    """(date | None, warns, reason) per spec 9.3 rule (1)."""
    for y in (m.year, m.year + 1, m.year - 1):
        c = _mk(y, mo, d)
        if c is not None and c.weekday() == wd:
            return c, c < m, None
    return None, False, "weekday_date_mismatch"


def _text(form: int, mo: int, d: int, wd: int | None = None) -> str:
    if wd is None:
        return (f"Sam to ship by {MON[mo - 1]} {d}", f"Sam to ship by {d} {MON[mo - 1]}",
                f"Sam to ship by {d}{_suffix(d)} {MON[mo - 1]}")[form % 3]
    return (f"Sam to ship by {WDN[wd]} {d} {MON[mo - 1]}", f"Sam to ship by {WDF[wd]} {MON[mo - 1]} {d}")[form % 2]


def _suffix(d: int) -> str:
    return "th" if 10 <= d % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")


def _got(r):
    return (r.due_date, BEFORE in r.warnings, r.due_reason.value if r.due_reason else None)


def _all_days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += DAY


# =============================================================================== AC-06.8 rows
@pytest.mark.ac("AC-06.8")
@pytest.mark.parametrize(
    "m,text,want,warn",
    [
        (D(2026, 10, 12), "by 9 Oct", D(2026, 10, 9), True),
        (D(2026, 12, 20), "by Jan 6", D(2027, 1, 6), False),
        (D(2026, 10, 14), "by Mar 1", D(2027, 3, 1), False),
        (D(2026, 10, 14), "by Oct 31", D(2026, 10, 31), False),
        (D(2026, 10, 14), "by Sep 16", D(2026, 9, 16), True),
        (D(2026, 10, 14), "by Aug 15", D(2026, 8, 15), True),   # exactly 60 days earlier: accepted as past
        (D(2026, 10, 14), "by Aug 14", D(2027, 8, 14), False),  # 61 days earlier: next year
    ],
)
def test_ac_06_8_rows(m, text, want, warn):
    r = resolve_due(f"Sam to ship {text}", m)
    assert r.due_date == want and (BEFORE in r.warnings) is warn
    assert r.due_confidence.value == "inferred" and r.due_rule.value == "yearless_date" and r.due_reason is None


@pytest.mark.ac("AC-06.8")
def test_ac_06_8_boundary_arithmetic_is_what_the_spec_says():
    m = D(2026, 10, 14)
    assert (m - D(2026, 8, 15)).days == 60 and (m - D(2026, 8, 14)).days == 61
    assert (D(2026, 10, 14) - D(2026, 9, 16)).days == 28 and (D(2026, 12, 20) - D(2026, 1, 6)).days == 348


@pytest.mark.ac("AC-06.8")
def test_ac_06_8_feb_29_in_2026_is_invalid_not_2028():
    r = resolve_due("Sam to ship by Feb 29", D(2026, 10, 14))
    assert (r.due_date, r.due_confidence.value, r.due_rule, r.due_reason.value) == (None, "unresolved", None, "invalid_date")
    assert r.due_raw == "Feb 29" and r.warnings == ()


# =============================================================================== AC-06.9 rows
@pytest.mark.ac("AC-06.9")
@pytest.mark.parametrize(
    "m,text,want,warn",
    [
        (D(2026, 10, 14), "by Wed 16 Sep", D(2026, 9, 16), True),
        (D(2026, 9, 14), "Wed 16 Sep", D(2026, 9, 16), False),
        (D(2026, 9, 1), "by Tue 16 Sep", D(2025, 9, 16), True),
    ],
)
def test_ac_06_9_resolved_rows(m, text, want, warn):
    r = resolve_due(f"Anita to finalise copy {text}", m)
    assert r.due_date == want and (BEFORE in r.warnings) is warn
    assert r.due_confidence.value == "inferred" and r.due_rule.value == "weekday_date"
    assert want.weekday() == {"Wed": 2, "Tue": 1}[text.split()[-3]]


@pytest.mark.ac("AC-06.9")
def test_ac_06_9_mismatch_event_and_missing_meeting_date():
    r = resolve_due("Anita to finalise copy by Fri 16 Sep", D(2026, 9, 1))
    assert r.due_date is None and r.due_reason.value == "weekday_date_mismatch" and r.due_rule is None
    assert {D(y, 9, 16).strftime("%a") for y in (2025, 2026, 2027)} == {"Tue", "Wed", "Thu"}
    ev = resolve_due("Sam to schedule cutover for Saturday 10 Oct", D(2026, 9, 29))
    assert (ev.due_date, ev.due_confidence.value, ev.due_rule.value) == (D(2026, 10, 10), "inferred", "event_date")
    none = resolve_due("Anita to finalise copy by Wed 16 Sep", None)
    assert none.due_date is None and none.due_reason.value == "no_meeting_date" and none.due_rule is None
    assert none.due_confidence.value == "unresolved" and none.due_raw == "Wed 16 Sep"
    # a year-less date without a meeting date is just as unresolved
    assert resolve_due("by 9 Oct", None).due_reason.value == "no_meeting_date"


@pytest.mark.ac("AC-06.9")
def test_year_given_weekday_mismatch_is_unresolved_regardless_of_meeting():
    r = resolve_due("Karan to file by Fri Oct 17 2026", D(2026, 10, 14))
    assert D(2026, 10, 17).strftime("%a") == "Sat"
    assert r.due_date is None and r.due_reason.value == "weekday_date_mismatch"


# =============================================================================== boundary sweep (60 / 61 days)
@pytest.mark.ac("AC-06.8")
@pytest.mark.sc("SC-63")
def test_boundary_sweep_60_and_61_days_every_meeting_2026_2027():
    n60_same = n61_same = n_cross = 0
    for m in _all_days(D(2026, 1, 1), D(2027, 12, 31)):
        for back in (60, 61):
            c = m - back * DAY
            if (c.month, c.day) == (2, 29):
                continue
            r = resolve_due(_text(c.day, c.month, c.day), m)
            got, warn = r.due_date, BEFORE in r.warnings
            assert r.due_confidence.value == "inferred" and r.due_reason is None, (m, c)
            if c.year == m.year:
                if back == 60:
                    assert (got, warn) == (c, True), (m, c)  # accepted as past, year of that date
                    assert got <= m
                    n60_same += 1
                else:
                    assert (got, warn) == (D(m.year + 1, c.month, c.day), False), (m, c)  # rolls to next year
                    n61_same += 1
            else:
                # the date 60/61 days back lies in the previous calendar year; written without a year it means
                # M's own year, which is later than M (spec 9.3 rule (2) starts from M's year)
                assert (got, warn) == (D(m.year, c.month, c.day), False), (m, c)
                n_cross += 1
    assert n60_same > 500 and n61_same > 500 and n_cross > 100


@pytest.mark.ac("AC-06.8")
def test_boundary_sweep_matches_oracle_for_neighbouring_offsets():
    for m in _all_days(D(2026, 3, 1), D(2027, 12, 31)):
        for back in (-1, 0, 1, 59, 60, 61, 62, 120, 364, 365):
            c = m - back * DAY
            if (c.month, c.day) == (2, 29):
                continue
            want = _oracle_yearless(m, c.month, c.day)
            assert _got(resolve_due(_text(0, c.month, c.day), m)) == want, (m, back)


# =============================================================================== properties over 2026-2027
@pytest.mark.ac("AC-06.8")
def test_yearless_properties_over_every_meeting_date():
    for m in _all_days(D(2026, 1, 1), D(2027, 12, 31)):
        for i, (mo, d) in enumerate(REPRESENTATIVE):
            r = resolve_due(_text(i, mo, d), m)  # never raises
            res = r.due_date
            assert res is not None and r.due_confidence.value == "inferred" and r.due_rule.value == "yearless_date"
            assert res.year in (m.year, m.year + 1) and (res.month, res.day) == (mo, d)
            if res < m:
                assert (m - res).days <= 60
                assert r.warnings == (BEFORE,)
            else:
                assert r.warnings == ()
            assert (res - m).days <= 366
            assert _got(r) == _oracle_yearless(m, mo, d)
            # no year-less date is ever marked relative/explicit, and nothing else is flagged
            assert r.due_raw_other is None and r.raws


@pytest.mark.ac("AC-06.9")
def test_weekday_date_properties_over_every_meeting_date():
    resolved = mismatched = 0
    for m in _all_days(D(2026, 1, 1), D(2027, 12, 31)):
        for i, (mo, d) in enumerate(REPRESENTATIVE):
            for k, y in enumerate((m.year - 1, m.year, m.year + 1)):
                claim_day = D(y, mo, d)
                wd = claim_day.weekday()
                r = resolve_due(_text(i + k, mo, d, wd), m)
                res = r.due_date
                assert res is not None, (m, claim_day)  # a real date's own year is among the tried years
                assert res.weekday() == wd and (res.month, res.day) == (mo, d)
                assert res.year in (m.year - 1, m.year, m.year + 1)
                assert r.due_confidence.value == "inferred" and r.due_rule.value == "weekday_date"
                assert (BEFORE in r.warnings) == (res < m)
                assert _got(r) == _oracle_weekday(m, mo, d, wd)
                resolved += 1
            # deliberately wrong weekday for all three candidate years
            right = {D(y, mo, d).weekday() for y in (m.year - 1, m.year, m.year + 1)}
            wrong = next(w for w in range(7) if w not in right)
            r = resolve_due(_text(i, mo, d, wrong), m)
            assert (r.due_date, r.due_rule, r.due_reason.value) == (None, None, "weekday_date_mismatch")
            assert r.due_confidence.value == "unresolved" and r.warnings == ()
            mismatched += 1
    assert resolved == 730 * 12 * 3 and mismatched == 730 * 12


# =============================================================================== year-end rollover, spec consequences
@pytest.mark.ac("AC-06.8")
@pytest.mark.sc("SC-63")
def test_year_end_rollover_cases():
    r = resolve_due("Sam to ship by Jan 6", D(2026, 12, 20))
    assert (r.due_date, r.warnings) == (D(2027, 1, 6), ())
    for day, want in ((D(2026, 12, 31), D(2027, 1, 1)), (D(2026, 12, 1), D(2027, 1, 1))):
        assert resolve_due("by Jan 1", day).due_date == want
    # M early January, late-December date: rule (2) starts from M's year, so it is later in M's own year (as written)
    r = resolve_due("by Dec 30", D(2027, 1, 5))
    assert (r.due_date, r.warnings) == (D(2027, 12, 30), ())
    # last day of the year, written for the same day
    assert resolve_due("by Dec 31", D(2026, 12, 31)).due_date == D(2026, 12, 31)


# =============================================================================== seeded random properties
def _random_case(rng: random.Random):
    m = D(2024, 1, 1) + rng.randrange((D(2030, 12, 31) - D(2024, 1, 1)).days + 1) * DAY
    y = rng.choice([2024, 2025, 2026, 2027, 2028, 2029, 2030])
    if rng.random() < 0.04:
        return m, 2, 29  # leap day, exercised against the J-04 oracle in the no-weekday branch
    mo = rng.randrange(1, 13)
    d = rng.randrange(1, (D(y + (mo == 12), mo % 12 + 1, 1) - D(y, mo, 1)).days + 1)
    return m, mo, d


@pytest.mark.ac("AC-06.8", "AC-06.9")
def test_seeded_random_year_less_properties():
    rng = random.Random(20261022)
    seen_weekday = seen_plain = seen_invalid = 0
    for i in range(6000):
        m, mo, d = _random_case(rng)
        wd = rng.randrange(7) if rng.random() < 0.5 else None
        r = resolve_due(_text(i, mo, d, wd), m)
        if wd is None:
            want = _oracle_yearless(m, mo, d)
            assert _got(r) == want, (m, mo, d)
            if r.due_date is not None:
                assert (r.due_date - m).days <= 366 and r.due_date.year in (m.year, m.year + 1)
                assert r.due_confidence.value == "inferred" and r.due_rule.value == "yearless_date"
                if r.due_date < m:
                    assert (m - r.due_date).days <= 60 and BEFORE in r.warnings
            else:
                assert r.due_reason.value == "invalid_date" and (mo, d) == (2, 29)
                seen_invalid += 1
            seen_plain += 1
        else:
            if (mo, d) == (2, 29):
                continue  # Feb 29 + weekday is spec-silent (B-17): only the invariants below are checked for resolved
            want = _oracle_weekday(m, mo, d, wd)
            assert _got(r) == want, (m, mo, d, wd)
            if r.due_date is not None:
                assert r.due_date.weekday() == wd and r.due_date.year in (m.year - 1, m.year, m.year + 1)
                assert (r.due_date - m).days <= 731  # M+1 year is tried before M-1 (spec 9.3), so up to ~2 years ahead
                assert r.due_confidence.value == "inferred" and r.due_rule.value == "weekday_date"
            seen_weekday += 1
    assert seen_weekday > 2000 and seen_plain > 2500 and seen_invalid > 0


@pytest.mark.ac("AC-06.10")
def test_seeded_random_year_bearing_dates_are_never_inferred():
    rng = random.Random(9)
    for i in range(3000):
        m = D(2024, 1, 1) + rng.randrange(2557) * DAY
        d = D(2020, 1, 1) + rng.randrange(365 * 15) * DAY
        forms = [
            (d.isoformat(), "iso_date"),
            (f"{d.day} {MON[d.month - 1]} {d.year}", "text_date"),
            (f"{MON[d.month - 1]} {d.day}, {d.year}", "text_date"),
            (f"{WDN[d.weekday()]} {d.day} {MON[d.month - 1]} {d.year}", "text_date"),
        ]
        raw, rule = forms[i % 4]
        intro = rng.choice(["by", "on", "for", "due", "before", "until"])
        r = resolve_due(f"Priya to send it {intro} {raw}", m)
        assert (r.due_date, r.due_confidence.value, r.due_rule.value) == (d, "explicit", rule), (m, raw, intro)
        assert (BEFORE in r.warnings) == (d < m)
        wrong = (d.weekday() + 1 + rng.randrange(6)) % 7
        r2 = resolve_due(f"Priya to send it by {WDN[wrong]} {d.day} {MON[d.month - 1]} {d.year}", m)
        assert (r2.due_date, r2.due_reason.value) == (None, "weekday_date_mismatch")


@pytest.mark.ac("AC-06.4")
def test_resolution_is_pure_deterministic_idempotent_and_state_free():
    rng = random.Random(77)
    cases = []
    for i in range(400):
        m, mo, d = _random_case(rng)
        wd = rng.randrange(7) if i % 3 == 0 else None
        cases.append((_text(i, mo, d, wd), m))
    cases += [("Sam to ship by Jan 6", D(2026, 12, 20)), ("by Wed 16 Sep", None), ("by Feb 29", D(2026, 10, 14))]
    first = [resolve_due(t, m) for t, m in cases]
    assert [resolve_due(t, m) for t, m in cases] == first  # deterministic
    order = list(range(len(cases)))
    random.Random(5).shuffle(order)
    shuffled = {i: resolve_due(*cases[i]) for i in order}  # independent of call order / prior calls
    assert [shuffled[i] for i in range(len(cases))] == first
    for (text, m), r in zip(cases, first):
        # idempotent: re-resolving the located phrase alone gives the same date / confidence / rule / reason
        if r.due_raw is None:
            continue
        again = resolve_due(f"by {r.due_raw}", m)
        assert (again.due_date, again.due_confidence, again.due_rule, again.due_reason) == (
            r.due_date, r.due_confidence, r.due_rule, r.due_reason), (text, m)
        # surrounding text does not matter
        wrapped = resolve_due(f"Lena (with Priya) to tidy the docs - {text.removeprefix('Sam to ship ')}", m)
        assert wrapped.due_date == r.due_date
    # the result carries no hidden dependence on the other argument order of the meeting date
    for t, m in cases[:50]:
        assert resolve_due(t, m) == resolve_due(t, D(m.year, m.month, m.day)) if m else True


# =============================================================================== Feb 29 (J-04; note B-17)
@pytest.mark.ac("AC-06.8")
@pytest.mark.parametrize(
    "m,want,warn,reason",
    [
        (D(2026, 10, 14), None, False, "invalid_date"),    # AC-06.8 row
        (D(2027, 3, 10), None, False, "invalid_date"),     # M's year has no Feb 29; no search of later years
        (D(2027, 12, 20), None, False, "invalid_date"),    # 2028-02-29 exists but is not searched (J-04)
        (D(2028, 1, 10), D(2028, 2, 29), False, None),     # leap M's year, in the future
        (D(2028, 3, 10), D(2028, 2, 29), True, None),      # leap, 10 days earlier: accepted as past
        (D(2028, 4, 29), D(2028, 2, 29), True, None),      # exactly 60 days earlier: accepted as past
        (D(2028, 4, 30), None, False, "invalid_date"),     # 61 days earlier: next year 2029 has no Feb 29
        (D(2028, 12, 1), None, False, "invalid_date"),
    ],
)
def test_feb_29_follows_j04(m, want, warn, reason):
    assert (D(2028, 4, 29) - D(2028, 2, 29)).days == 60
    r = resolve_due("Sam to ship by Feb 29", m)
    assert r.due_date == want and (BEFORE in r.warnings) is warn
    assert (r.due_reason.value if r.due_reason else None) == reason
    if want is not None:
        assert r.due_confidence.value == "inferred" and r.due_rule.value == "yearless_date"
    else:
        assert r.due_confidence.value == "unresolved" and r.due_rule is None


@pytest.mark.ac("AC-06.8")
def test_feb_29_with_weekday_resolves_only_to_a_real_matching_day():
    assert D(2028, 2, 29).weekday() == 1  # Tuesday
    r = resolve_due("by Tue 29 Feb", D(2028, 3, 1))
    assert (r.due_date, r.warnings) == (D(2028, 2, 29), (BEFORE,))
    assert r.due_rule.value == "weekday_date"
    # no year in {2025, 2026, 2027} has a 29 February: nothing can match
    r = resolve_due("by Mon 29 Feb", D(2026, 10, 14))
    assert r.due_date is None and r.due_reason.value == "weekday_date_mismatch"


@pytest.mark.ac("AC-06.3")
@pytest.mark.parametrize("text", ["by Apr 31", "by 31 Apr", "by Feb 30", "by Nov 31", "by Jun 31"])
def test_impossible_yearless_days_are_invalid_date(text):
    r = resolve_due(f"Sam to ship {text}", D(2026, 10, 14))
    assert r.due_date is None and r.due_reason.value == "invalid_date" and r.due_confidence.value == "unresolved"
