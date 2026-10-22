"""T-M3-09: ``resolve_due`` (provenance, composition) and the M2 analyzer adapter."""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from actiontrack import codes
from actiontrack.domain import dates
from actiontrack.domain.dates import DueResult, format_due, resolve_due
from actiontrack.domain.models import TextSpan
from actiontrack.parsing import parse_document
from actiontrack.parsing.items import (
    AnalyzerContext,
    DueAnalyzer,
    derive_task_text,
    item_due_warnings,
)


def _v(x):
    return None if x is None else getattr(x, "value", x)


def D(s):
    return date.fromisoformat(s) if s else None


# --------------------------------------------------------------------------- AC-06.10 matrix
@pytest.mark.ac("AC-06.10", "AC-06.5")
@pytest.mark.sc("SC-64")
@pytest.mark.parametrize(
    ("text", "m", "want_date", "conf", "rule", "raw"),
    [
        ("Demo the portal on 2026-10-14", "2026-10-06", "2026-10-14", "explicit", "iso_date", "2026-10-14"),
        ("Demo by 2026-10-14", "2026-10-06", "2026-10-14", "explicit", "iso_date", "2026-10-14"),
        ("Demo due 14 Oct 2026", "2026-10-06", "2026-10-14", "explicit", "text_date", "14 Oct 2026"),
        ("Demo for 2026-10-14", "2026-10-06", "2026-10-14", "explicit", "iso_date", "2026-10-14"),
        ("Demo on Oct 14", "2026-10-06", "2026-10-14", "inferred", "event_date", "Oct 14"),
        ("Schedule cutover for Saturday 10 Oct", "2026-09-29", "2026-10-10", "inferred", "event_date",
         "Saturday 10 Oct"),
    ],
)
def test_ac_06_10_matrix(text, m, want_date, conf, rule, raw):
    r = resolve_due(text, D(m))
    assert (r.due_date, _v(r.due_confidence), _v(r.due_rule), r.due_raw) == (D(want_date), conf, rule, raw)
    assert r.due_reason is None
    assert r.due_rule in codes.DUE_RULES
    assert not r.due_raw.lower().startswith(("on ", "by ", "for ", "due "))  # introducer never in raw


@pytest.mark.ac("AC-06.10")
def test_no_year_bearing_date_is_inferred():
    for text in ("on 2026-10-14", "by 2026-10-14", "due 14 Oct 2026", "for 2026-10-14", "by 14/10/2026"):
        r = resolve_due("Demo " + text, D("2026-10-06"), "dmy")
        assert r.due_date is not None and _v(r.due_confidence) == "explicit", text


def test_time_words_stay_in_raw_not_in_date():
    r = resolve_due("Send it by Friday 5pm", D("2026-10-14"))
    assert r.due_date == D("2026-10-16") and r.due_raw == "Friday 5pm"
    r = resolve_due("Write the postmortem by EOD tomorrow", D("2026-09-21"))
    assert (r.due_raw, r.due_date, _v(r.due_rule), _v(r.due_confidence)) == (
        "EOD tomorrow", D("2026-09-22"), "tomorrow", "relative")


# --------------------------------------------------------------------------- none / not-due / multiple
@pytest.mark.ac("AC-06.5", "AC-06.6")
@pytest.mark.sc("SC-35")
def test_multiple_dates_keeps_all_raws():
    r = resolve_due("Ship by Friday and review by 2026-10-30", D("2026-10-14"))
    assert r.due_date is None and _v(r.due_confidence) == "unresolved"
    assert _v(r.due_reason) == "multiple_dates" and r.due_rule is None
    assert r.raws == ("Friday", "2026-10-30")
    assert r.due_raw == "Friday; 2026-10-30"


def test_same_phrase_twice_is_not_multiple():
    r = resolve_due("Ship by Friday, definitely by Friday", D("2026-10-14"))
    assert r.due_date == D("2026-10-16") and r.raws == ("Friday",)


def test_none_result():
    r = resolve_due("Confirm cloud budget with finance", D("2026-09-01"))
    assert (r.due_raw, r.due_date, _v(r.due_confidence), r.due_rule, r.due_reason) == (
        None, None, "none", None, None)
    assert r.due_raw_other is None and r.warnings == () and r.spans == () and r.raws == ()


@pytest.mark.ac("AC-06.5")
def test_not_due_phrase_only():
    text = "Freeze invoice edits from 9 Oct"
    r = resolve_due(text, D("2026-09-29"))
    assert _v(r.due_confidence) == "none" and r.due_raw is None and r.due_date is None
    assert r.due_raw_other == "9 Oct"
    assert r.warnings == (codes.W_DATE_NOT_DUE,)
    assert derive_task_text(text, r.spans) == "Freeze invoice edits"


def test_not_due_next_to_due_phrase():
    r = resolve_due("Freeze edits from 9 Oct and report by Friday", D("2026-09-29"))
    assert r.due_raw == "Friday" and r.due_raw_other == "9 Oct" and codes.W_DATE_NOT_DUE in r.warnings


# --------------------------------------------------------------------------- unresolved
@pytest.mark.ac("AC-06.6")
def test_unresolved_has_no_rule_and_reason():
    r = resolve_due("Propose the rotation by mid-October", D("2026-09-30"))
    assert (r.due_raw, r.due_date, _v(r.due_confidence), r.due_rule, _v(r.due_reason)) == (
        "mid-October", None, "unresolved", None, "vague")
    r = resolve_due("Chase Orbit Labs by Friday", None)
    assert (r.due_raw, _v(r.due_confidence), r.due_rule, _v(r.due_reason)) == (
        "Friday", "unresolved", None, "no_meeting_date")


def test_before_meeting_warning_comes_from_the_resolver():
    r = resolve_due("Deliver by 9 Oct", D("2026-10-12"))
    assert r.due_date == D("2026-10-09") and r.warnings == (codes.W_DUE_BEFORE_MEETING,)


# --------------------------------------------------------------------------- display, purity
def test_format_due():
    m = D("2026-10-14")
    assert format_due(resolve_due("by Friday", m)) == "2026-10-16"
    assert format_due(resolve_due("by mid-October", m)) == "?(mid-October)"
    assert format_due(resolve_due("nothing here", m)) == "-"
    long = DueResult("x" * 50, None, dates.Confidence.UNRESOLVED, None, dates.Reason("vague"))
    assert format_due(long) == "?(" + "x" * 23 + "…)"


def test_pure_and_frozen():
    a = resolve_due("Do it by Friday 5pm", D("2026-10-14"))
    b = resolve_due("Do it by Friday 5pm", D("2026-10-14"))
    assert a == b
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.due_raw = "x"  # type: ignore[misc]
    text = open(dates.__file__, encoding="utf-8").read()
    assert "datetime.now" not in text and "date.today" not in text and "time.time" not in text


def test_spans_cover_introducer_and_phrase():
    text = "Priya to send the revised quote by Friday."
    r = resolve_due(text, D("2026-10-14"))
    cut = [text[s.start : s.end] for s in r.spans]
    assert "Friday" in cut and any(c.strip() == "by" for c in cut)
    assert derive_task_text(text, [TextSpan(0, len("Priya to ")), *r.spans]) == "Send the revised quote"


# --------------------------------------------------------------------------- adapter + samples
@pytest.mark.ac("AC-01.2", "AC-06.6")
@pytest.mark.sc("SC-01")
def test_adapter_note_dated_2026_10_14():
    note = b"Date: 2026-10-14\n\n- [ ] Priya to send the revised quote by Friday.\n"
    pf = parse_document(note, "n.md")
    (item,) = pf.items
    due = item.due_hook
    assert isinstance(due, DueResult)
    assert (due.due_date, _v(due.due_confidence), due.due_raw) == (D("2026-10-16"), "relative", "Friday")
    assert item.task_text == "Priya to send the revised quote"  # owner spans stubbed by NullOwnerSpans
    assert derive_task_text(item.description, [TextSpan(0, 9), *due.spans]) == "Send the revised quote"


def test_adapter_analyzer_direct():
    a = DueAnalyzer().analyze("Demo by Friday", AnalyzerContext(D("2026-10-14")))
    assert a.owner is None and a.due.due_date == D("2026-10-16") and a.due_spans == a.due.spans


FIELD_MATRIX = [
    # file, line, raw, rule, confidence, date
    ("2026-09-01-quote-portal-kickoff.md", 9, "Friday", "weekday_next", "relative", "2026-09-04"),
    ("2026-09-01-quote-portal-kickoff.md", 10, "2026-09-08", "iso_date", "explicit", "2026-09-08"),
    ("2026-09-03-billing-migration-sync.md", 9, "10 Sep", "yearless_date", "inferred", "2026-09-10"),
    ("2026-09-14-onboarding-flow-design.md", 9, "Wed 16 Sep", "weekday_date", "inferred", "2026-09-16"),
    ("2026-09-21-incident-review.md", 14, "EOD tomorrow", "tomorrow", "relative", "2026-09-22"),
    ("2026-09-29-billing-cutover-planning.md", 11, "Saturday 10 Oct", "event_date", "inferred", "2026-10-10"),
    ("2026-10-06-quote-portal-weekly.md", 10, "2026-10-14", "iso_date", "explicit", "2026-10-14"),
    ("2026-10-02-finance-sync.md", 10, "2026-10-09", "iso_date", "explicit", "2026-10-09"),
    ("2026-10-05-onboarding-retro.md", 10, "9 Oct", "yearless_date", "inferred", "2026-10-09"),
]


@pytest.fixture(scope="module")
def parsed_sample():
    from tests.conftest import FIXTURES_NOTES

    return {p.name: parse_document(p.read_bytes(), p.name) for p in sorted(FIXTURES_NOTES.glob("*.md"))}


def _item(parsed, name, line):
    return next(i for i in parsed[name].items if i.line_start == line)


@pytest.mark.ac("AC-06.6", "AC-06.10")
@pytest.mark.sc("SC-64")
@pytest.mark.parametrize(("name", "line", "raw", "rule", "conf", "when"), FIELD_MATRIX)
def test_sample_field_matrix(parsed_sample, name, line, raw, rule, conf, when):
    due = _item(parsed_sample, name, line).due_hook
    assert (due.due_raw, _v(due.due_rule), _v(due.due_confidence), due.due_date) == (raw, rule, conf, D(when))


@pytest.mark.ac("AC-06.6", "AC-06.5")
def test_sample_unresolved_none_and_not_due(parsed_sample):
    due = _item(parsed_sample, "2026-09-30-q4-offsite.md", 12).due_hook
    assert (due.due_raw, _v(due.due_confidence), due.due_rule, _v(due.due_reason)) == (
        "mid-October", "unresolved", None, "vague")
    due = _item(parsed_sample, "2026-09-01-quote-portal-kickoff.md", 12).due_hook
    assert due.due_raw is None and _v(due.due_confidence) == "none"
    name = "2026-09-29-billing-cutover-planning.md"
    item = _item(parsed_sample, name, 12)
    assert item.due_hook.due_raw_other == "9 Oct" and _v(item.due_hook.due_confidence) == "none"
    got = [(w.code, w.line) for w in parsed_sample[name].warnings if w.code == codes.W_DATE_NOT_DUE]
    assert got == [(codes.W_DATE_NOT_DUE, 12)]
    assert "9 Oct" not in item.task_text and item.task_text == "Karan to freeze invoice edits"


@pytest.mark.ac("AC-06.6")
def test_sample_no_meeting_date(parsed_sample):
    for name, line in (("2026-10-03-sales-pipeline.md", 6), ("untitled.md", 2)):
        due = _item(parsed_sample, name, line).due_hook
        assert (due.due_raw, _v(due.due_confidence), _v(due.due_reason), due.due_rule) == (
            "Friday", "unresolved", "no_meeting_date", None)


@pytest.mark.ac("AC-06.6", "AC-06.5")
def test_sample_every_item_has_due_result_and_catalogue_ids(parsed_sample):
    n = 0
    for pf in parsed_sample.values():
        for it in pf.items:
            n += 1
            d = it.due_hook
            assert isinstance(d, DueResult)
            if d.due_confidence == dates.Confidence.UNRESOLVED:
                assert d.due_rule is None and _v(d.due_reason) in codes.DUE_REASONS
            elif d.due_date is not None:
                assert _v(d.due_rule) in codes.DUE_RULES and d.due_reason is None
    assert n == 117
    assert not any(w.code == codes.W_DUE_BEFORE_MEETING for pf in parsed_sample.values() for w in pf.warnings)


def test_before_meeting_warning_attached_to_item_line():
    note = b"Date: 2026-10-12\n\n- [ ] Priya to send it by 9 Oct\n"
    pf = parse_document(note, "n.md")
    (w,) = [w for w in pf.warnings if w.code == codes.W_DUE_BEFORE_MEETING]
    assert w.line == 3 and w.detail == "2026-10-09 < 2026-10-12"
    assert pf.items[0].due_hook.due_date == D("2026-10-09")
    assert item_due_warnings(pf.items[0], D("2026-10-12"), "n.md")[0].code == codes.W_DUE_BEFORE_MEETING
