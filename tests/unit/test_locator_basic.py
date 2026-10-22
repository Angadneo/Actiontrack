"""T-M3-04: due-phrase locator I (introducers, extent, separators, trailing phrases; spans index description)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from actiontrack.domain.dates import Located, distinct_due_phrases, locate

NOTES = Path(__file__).resolve().parents[1] / "fixtures" / "notes"
_CHECKBOX = re.compile(r"^\s*[-*+]\s*\[.\]\s*")
CALENDAR = "\U0001F4C5"
EM = "—"
EN = "–"


def sample(prefix: str, line_no: int) -> str:
    """Checkbox text (the ``description`` an item would carry) of 1-based ``line_no`` in a sample note."""
    files = sorted(NOTES.glob(f"{prefix}*.md"))
    assert len(files) == 1, (prefix, files)
    line = files[0].read_text(encoding="utf-8").splitlines()[line_no - 1]
    assert _CHECKBOX.match(line), line
    return _CHECKBOX.sub("", line, count=1).strip()


def check_integrity(desc: str, located: list[Located]) -> None:
    for f in located:
        assert desc[f.start:f.end] == f.raw
        if f.introducer_span is not None:
            assert desc[f.introducer_span.start:f.introducer_span.end] == f.introducer
            assert f.introducer_span.end <= f.start
        if f.separator_span is not None:
            assert f.introducer_span.end <= f.separator_span.start < f.separator_span.end <= f.start
        assert not desc[f.start:f.end].endswith(".")
    spans = [(f.start, f.end) for f in located]
    assert spans == sorted(spans)


def pairs(desc: str) -> list[tuple[str | None, str]]:
    loc = locate(desc)
    check_integrity(desc, loc)
    return [((f.introducer or "").lower() or None, f.raw) for f in loc]


@pytest.mark.ac("AC-06.6", "AC-01.19")
@pytest.mark.parametrize(("prefix", "line", "expected"), [
    ("2026-09-01-quote", 9, [("by", "Friday")]),
    ("2026-09-01-quote", 10, [("by", "2026-09-08")]),
    ("2026-09-03-billing", 11, [("by", "end of next week")]),
    ("2026-09-14-onboarding", 9, [("by", "Wed 16 Sep")]),
    ("2026-09-21-incident", 14, [("by", "EOD tomorrow")]),
    ("2026-09-30-q4-offsite", 11, [("due", "15 October")]),
    ("2026-09-30-q4-offsite", 9, [("by", "Oct 3rd")]),
    ("2026-09-08-quote", 9, [("by", "Friday")]),
    ("2026-10-08-billing", 9, [("by", "EOD")]),
    ("2026-10-05-onboarding", 9, [("by", "next Tuesday")]),
    ("2026-10-02-finance", 12, [("by", "12 Oct")]),
])
def test_sample_raw_extraction(prefix, line, expected):
    assert pairs(sample(prefix, line)) == expected


def test_time_words_split_for_compound():
    (f,) = locate(sample("2026-09-21-incident", 14))
    assert f.time_words == ("EOD",)
    assert f.phrase.kind == "tomorrow"
    assert f.role == "due"
    (g,) = locate(sample("2026-10-08-billing", 9))
    assert g.time_words == ("EOD",) and g.phrase.kind == "eod_alone"


def test_em_dash_before_introducer_is_ignored_and_outside_spans():
    desc = sample("2026-09-08-quote", 9)
    assert EM in desc
    (f,) = locate(desc)
    assert desc[f.introducer_span.start - 2:f.introducer_span.start] == EM + " "
    assert f.start > f.introducer_span.end


def test_trailing_period_outside_every_span():
    desc = "Send the revised quote by Friday."
    (f,) = locate(desc)
    assert f.raw == "Friday" and desc[f.end:] == "."
    check_integrity(desc, [f])


@pytest.mark.parametrize(("desc", "expected"), [
    ("Send deck (due: Oct 20)", [("(due:", "Oct 20")]),
    ("Send deck [due: Fri]", [("[due:", "Fri")]),
    ("Send deck deadline: 2026-10-20", [("deadline:", "2026-10-20")]),
    ("Send deck no later than Friday", [("no later than", "Friday")]),
    ("Send deck until Friday", [("until", "Friday")]),
    ("Send deck till tomorrow", [("till", "tomorrow")]),
    ("Send deck before Friday", [("before", "Friday")]),
    ("Send deck due on 14 Oct 2026", [("due on", "14 Oct 2026")]),
    ("Send deck due Oct 31, 2026.", [("due", "Oct 31, 2026")]),
    (f"Send deck {CALENDAR} 2026-10-20", [(CALENDAR, "2026-10-20")]),
    ("Send deck BY FRIDAY", [("by", "FRIDAY")]),
    ("Send deck by - Friday", [("by", "Friday")]),
    ("Send deck by: Friday", [("by", "Friday")]),
    (f"Send deck by {EN} Friday", [("by", "Friday")]),
    ("Send deck by Friday EOD", [("by", "Friday EOD")]),
    ("Send deck by Friday, then rest", [("by", "Friday")]),
    ("Send deck by the 15th", [("by", "the 15th")]),
    ("Send deck by the 15th of October", [("by", "the 15th of October")]),
    ("Send deck by the end of the week", [("by", "the end of the week")]),
    ("Send deck by tomorrow (urgent)", [("by", "tomorrow")]),
    ("Send deck (by Friday)", [("by", "Friday")]),
])
def test_synthetic_introducers(desc, expected):
    assert pairs(desc) == expected


def test_ordinal_of_month_extent_is_longest():
    desc = "Send deck by the 15th of October"
    (f,) = locate(desc)
    assert f.raw == "the 15th of October" and f.phrase.kind == "yearless"


def test_separator_span_recorded():
    desc = "Send deck by - Friday"
    (f,) = locate(desc)
    assert desc[f.separator_span.start:f.separator_span.end].strip() == "-"
    desc = "Send deck deadline:2026-10-20"
    (f,) = locate(desc)
    assert f.raw == "2026-10-20"
    assert desc[f.separator_span.start:f.separator_span.end] == ":" if f.separator_span else True


@pytest.mark.parametrize(("desc", "raw", "kind", "tw"), [
    ("Send deck tomorrow.", "tomorrow", "tomorrow", ()),
    ("Send deck Fri EOD", "Fri EOD", "weekday", ("EOD",)),
    ("Ship it after the launch", "after the launch", "vague", ()),
    ("Send deck in 3 days", "in 3 days", "in_n", ()),
    ("Send deck within two weeks.", "within two weeks", "in_n", ()),
    ("Send deck EOD tomorrow", "EOD tomorrow", "tomorrow", ("EOD",)),
    ("Send deck 2026-10-09", "2026-10-09", "absolute", ()),
    ("Send deck (tomorrow)", "tomorrow", "tomorrow", ()),
    ("Send deck next week", "next week", "range", ()),
    ("Send deck later", "later", "vague", ()),
])
def test_trailing_standalone(desc, raw, kind, tw):
    loc = locate(desc)
    check_integrity(desc, loc)
    (f,) = loc
    assert (f.raw, f.phrase.kind, f.time_words) == (raw, kind, tw)
    assert f.introducer is None and f.introducer_span is None and f.role == "due"


def test_trailing_rule_is_anchored_to_end_never_mid_sentence():
    assert locate("Send tomorrow the deck to Acme") == []
    assert locate("Friday is when the deck goes out to Acme") == []
    assert locate("Ship after the launch of the portal") == []


def test_trailing_not_duplicated_when_introducer_present():
    assert len(locate("Send deck by Friday")) == 1
    assert len(locate("Send deck by next Tuesday")) == 1
    assert len(locate("Send deck by Wed 16 Sep")) == 1


def test_after_the_launch_only_by_trailing_rule():
    # `due after the launch` is not an introducer form; the trailing rule finds the fixed phrase
    (f,) = locate("Ship it due after the launch")
    assert f.introducer is None and f.raw == "after the launch"


@pytest.mark.parametrize(("prefix", "line", "raws"), [
    ("2026-09-17-billing", 10, ["2026-09-22"]),  # on staging skipped, real by-date kept
    ("2026-10-10-sales", 10, ["EOD"]),  # in CRM
    ("2026-10-09-security", 18, ["2026-10-16"]),  # on all admin accounts
    ("2026-09-03-billing", 12, []),  # before cutover
    ("2026-09-18-design", 14, []),  # before the next review
    ("2026-10-05-onboarding", 12, []),  # for accessibility
])
def test_skipped_silently_when_not_a_date(prefix, line, raws):
    desc = sample(prefix, line)
    loc = locate(desc)
    check_integrity(desc, loc)
    assert [f.raw for f in loc] == raws


@pytest.mark.parametrize("desc", [
    "Sam to book a room by <date>",
    "Cut costs by 10%",
    "Reduce churn by 5",
    "Move on staging",
    "Ship it before cutover",
])
def test_introducer_without_date_is_skipped(desc):
    assert locate(desc) == []


@pytest.mark.parametrize(("prefix", "line", "raw"), [
    ("2026-09-30-standup", 12, "Friday"),
    ("2026-10-02-finance", 11, "Wednesday"),
    ("2026-09-30-q4-offsite", 9, "Oct 3rd"),
    ("2026-09-30-q4-offsite", 18, "7 Oct"),
    ("2026-09-30-standup", 11, "2026-10-02"),
])
def test_q4_mid_sentence_is_not_a_candidate(prefix, line, raw):
    desc = sample(prefix, line)
    assert "Q4" in desc
    loc = locate(desc)
    assert [f.raw for f in loc] == [raw]
    check_integrity(desc, loc)


def test_q4_item_without_date_has_no_candidate():
    assert locate(sample("2026-10-02-finance", 9)) == []  # Karan to approve the Q4 cloud spend


@pytest.mark.parametrize(("prefix", "line"), [
    ("2026-09-18-design", 15),  # TBD to write the design-system changelog
    ("2026-10-01-quote", 12),  # Team to decide on the launch date
])
def test_leading_owner_words_are_not_candidates(prefix, line):
    assert locate(sample(prefix, line)) == []


def test_someone_has_no_candidate():
    assert locate("Someone to update the CRM notes") == []


def test_result_independent_of_text_before_introducer():
    for lead in ("Send it ", "Priya to send the revised quote to Acme " + EM + " ", "x " * 40):
        (f,) = locate(lead + "by Friday")
        assert f.raw == "Friday"
        assert f.start == len(lead) + 3


def test_mid_sentence_due():
    desc = "Lena to run the customer survey, results due 15 October"
    (f,) = locate(desc)
    assert (f.introducer, f.raw) == ("due", "15 October")


def test_introducer_word_boundaries():
    assert locate("Nobody to work abyss now") == []
    assert locate("Rahul to review the standby rota") == []
    (f,) = locate("Sam to fix the bypass today")
    assert f.introducer is None and f.raw == "today"  # only the trailing standalone rule


def test_identical_repeats_collapse_in_distinct():
    loc = locate("Send deck by Friday and again by Friday")
    assert len(loc) == 2
    assert [f.raw for f in distinct_due_phrases(loc)] == ["Friday"]


def test_all_sample_items_spans_hold():
    n = 0
    for p in sorted(NOTES.glob("*.md")):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if _CHECKBOX.match(line):
                desc = _CHECKBOX.sub("", line, count=1).strip()
                check_integrity(desc, locate(desc))
                n += 1
    assert n > 100
