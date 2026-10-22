"""T-M2-17: sample-corpus acceptance and golden parse output (AC-01.14..17, AC-01.19/20 sample halves).

The full 36-file sample set (``tests/fixtures/notes``, read-only) is run through ``discover`` +
``parse_file`` / ``parse_document``.  ``EXPECTED`` below is a hand-checked table (each row was verified
against the file text and spec Appendix A.2/A.3, not blindly dumped): status, meeting date, date source,
raw date text, title, attendee count, ``(line, state)`` per imported item and the structural parser
warnings (``W-DATE-NOT-DUE`` comes from the due analyzer and is asserted separately).

Golden file ``tests/golden/m2_sample_parse.json`` = ``ParsedFile.to_dict()`` of every file in ingest
order (``parse_document`` on the bytes, so ``mtime_ns`` is ``null``).  It was written only after the
output had been reviewed against the spec.  To regenerate after an intended parser change, review the
diff and run::

    M2_REGEN_GOLDEN=1 python -m pytest tests/integration/test_m2_sample.py -k golden
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.domain.models import ingest_order_key
from actiontrack.parsing import NullAnalyzer, parse_document, parse_file
from actiontrack.parsing.discovery import discover
from tests.conftest import FIXTURES_NOTES, REPO_ROOT

GOLDEN = REPO_ROOT / "tests" / "golden" / "m2_sample_parse.json"

# rel_path -> (status, meeting date, date_source, date_raw, title, n_attendees, items, warnings)
EXPECTED = {
    "2026-09-01-quote-portal-kickoff.md": ("ok", '2026-09-01', "date_line", '2026-09-01', 'Quote portal kickoff', 4, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-09-02-standup.md": ("ok", '2026-09-02', "date_line", '2026-09-02', 'Team standup', 3, ((9, 'o'), (10, 'o')), ()),
    "2026-09-03-billing-migration-sync.md": ("ok", '2026-09-03', "date_line", '2026-09-03', 'Billing migration sync', 4, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-09-08-quote-portal-weekly.md": ("ok", '2026-09-08', "date_line", '2026-09-08', 'Quote portal weekly', 5, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'd')), ()),
    "2026-09-09-notes-final-v2.md": ("empty", None, None, None, None, 0, (), (('EMPTY-FILE', None),)),
    "2026-09-09-qa-standup.md": ("ok", '2026-09-09', "date_line", '9 September 2026', 'QA and billing standup', 3, ((9, 'o'), (10, 'o')), ()),
    "2026-09-10-acme-renewal-prep.md": ("ok", '2026-09-10', "date_line", '2026-09-10', 'Acme renewal prep', 4, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-09-14-onboarding-flow-design.md": ("ok", '2026-09-14', "date_line", 'Monday, 14 September 2026', 'Onboarding flow design', 4, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-09-15-quote-portal-weekly.md": ("ok", '2026-09-15', "date_line", '2026-09-15', 'Quote portal weekly', 5, ((9, 'd'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-09-16-standup.md": ("ok", '2026-09-16', "front_matter", '2026-09-16', 'Team standup', 3, ((11, 'o'), (12, 'o')), ()),
    "2026-09-17-billing-migration-sync.md": ("ok", '2026-09-17', "date_line", '2026-09-17', 'Billing migration sync', 5, ((9, 'o'), (10, 'o'), (11, 'd'), (12, 'o')), ()),
    "2026-09-18-design-review.md": ("ok", '2026-09-18', "date_line", '2026-09-18', 'Design review (brain dump)', 4, ((11, 'o'), (12, 'o'), (13, 'o'), (14, 'o'), (15, 'o')), ()),
    "2026-09-21-incident-review.md": ("ok", '2026-09-21', "date_line", '2026-09-21', 'Incident review: billing double-charge', 4, ((14, 'o'), (15, 'o'), (16, 'o'), (17, 'o')), (('CHECKBOX-IGNORED-REGION', 9),)),
    "2026-09-22-quote-portal-weekly.md": ("ok", '2026-09-22', "date_line", '2026-09-22', 'Quote portal weekly', 5, ((9, 'o'), (10, 'd'), (11, 'o'), (12, 'o')), ()),
    "2026-09-23-standup.md": ("ok", '2026-09-23', "date_line", '2026-09-23', 'Team standup', 0, ((8, 'o'), (9, 'o')), (('ATTENDEES-MISSING', None),)),
    "2026-09-24-onboarding-flow-design.md": ("ok", '2026-09-24', "date_line", '2026-09-24', 'Onboarding flow design', 4, ((9, 'o'), (10, 'd'), (11, 'o'), (12, 'o')), ()),
    "2026-09-25-qa-sync.md": ("ok", '2026-09-25', "date_line", '2026-09-25', 'QA sync', 2, ((9, 'o'), (10, 'o')), ()),
    "2026-09-28-acme-renewal-checkin.md": ("ok", '2026-09-28', "date_line", '2026-09-28', 'Acme renewal check-in', 4, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-09-29-billing-cutover-planning.md": ("ok", '2026-09-29', "date_line", '2026-09-29', 'Billing migration cutover planning', 5, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-09-30-q4-offsite.md": ("ok", '2026-09-30', "date_line", '2026-09-30', 'Q4 planning offsite', 9, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'o'), (16, 'o'), (17, 'o'), (18, 'o')), (('NONCHECKBOX-CANDIDATES', 22), ('NONCHECKBOX-CANDIDATES', 24))),
    "2026-09-30-standup.md": ("ok", '2026-09-30', "front_matter", '2026-09-30', 'Team standup', 3, ((11, 'o'), (12, 'o')), ()),
    "2026-10-01-quote-portal-weekly.md": ("ok", '2026-10-01', "date_line", '2026-10-01', 'Quote portal weekly', 5, ((9, 'd'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-10-02-finance-sync.md": ("ok", '2026-10-02', "date_line", '2026-10-02', 'Finance sync', 3, ((9, 'd'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-10-03-sales-pipeline.md": ("ok", None, "none", 'sometime last week', 'Sales pipeline', 2, ((6, 'o'), (7, 'o')), (('MEETING-DATE-INVALID', 2),)),
    "2026-10-05-onboarding-retro.md": ("ok", '2026-10-05', "date_line", '2026-10-05', 'Onboarding flow checkpoint', 4, ((9, 'o'), (10, 'o'), (11, 'd'), (12, 'o')), ()),
    "2026-10-06-quote-portal-weekly.md": ("ok", '2026-10-06', "date_line", '2026-10-06', 'Quote portal weekly', 5, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-10-07-standup.md": ("ok", '2026-10-07', "date_line", '2026-10-07', 'Team standup', 3, ((9, 'o'), (10, 'o')), ()),
    "2026-10-08-billing-go-no-go.md": ("ok", '2026-10-08', "date_line", '2026-10-08', 'Billing migration go/no-go', 5, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'd')), ()),
    "2026-10-09-security-review.md": ("ok", '2026-10-09', "date_line", '2026-10-09', 'Security review', 4, ((18, 'o'), (19, 'o'), (20, 'o'), (21, 'd')), (('CHECKBOX-IGNORED-REGION', 10), ('CHECKBOX-IGNORED-REGION', 13))),
    "2026-10-10-sales-weekly.md": ("ok", '2026-10-10', "date_line", '2026-10-10', 'Sales weekly', 2, ((9, 'o'), (10, 'o'), (11, 'd')), ()),
    "2026-10-12-billing-post-cutover.md": ("ok", '2026-10-12', "date_line", '2026-10-12', 'Billing migration post-cutover', 4, ((9, 'o'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-10-13-quote-portal-weekly.md": ("ok", '2026-10-13', "date_line", '2026-10-13', 'Quote portal weekly', 5, ((9, 'd'), (10, 'o'), (11, 'o'), (12, 'o')), ()),
    "2026-10-14-acme-renewal-wrapup.md": ("ok", '2026-10-14', "date_line", '2026-10-14', 'Acme renewal wrap-up', 4, ((9, 'o'), (10, 'o'), (11, 'd'), (12, 'o')), ()),
    "scratch.md": ("failed", None, None, None, None, 0, (), ()),
    "untitled.md": ("ok", None, "none", None, 'untitled', 0, ((1, 'o'), (2, 'o')), (('ATTENDEES-MISSING', None), ('MEETING-DATE-MISSING', None))),
}

EMPTY_FILE = "2026-09-09-notes-final-v2.md"
FAILED_FILE = "scratch.md"


def _snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.stat()
        out[p.relative_to(root).as_posix()] = (
            "<dir>" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest(),
            st.st_size,
            st.st_mtime_ns,
        )
    return out


def _parse_all(root: Path = FIXTURES_NOTES, **kw):
    """discover + parse_file in ingest order -> (DiscoveryResult, [ParsedFile])."""
    disc = discover(root)
    parsed = [parse_file(f, **kw) for f in disc.files]
    parsed.sort(key=ingest_order_key)
    return disc, parsed


@pytest.fixture(scope="module")
def corpus():
    before = _snapshot(FIXTURES_NOTES)
    disc, parsed = _parse_all()
    assert _snapshot(FIXTURES_NOTES) == before  # AC-01.11 / BR-01: nothing touched
    return disc, parsed


@pytest.fixture(scope="module")
def by_name(corpus):
    return {p.rel_path: p for p in corpus[1]}


def _wcodes(pf):
    return [w.code for w in pf.warnings]


# --------------------------------------------------------------------------- discovery
@pytest.mark.ac("AC-01.1", "AC-01.13")
@pytest.mark.sc("SC-41")
def test_discovery_finds_35_md_and_ignores_txt(corpus):
    disc, _ = corpus
    names = [f.rel_path for f in disc.files]
    assert len(names) == 35
    assert all(n.endswith(".md") for n in names)
    assert "2026-09-19-offsite-draft.txt" not in names
    assert names == sorted(names)
    assert disc.warnings == ()
    assert len(list(FIXTURES_NOTES.iterdir())) == 36
    assert set(names) == set(EXPECTED)


@pytest.mark.ac("AC-01.13")
@pytest.mark.sc("SC-41")
def test_discovery_one_level_down_finds_same_35(copy_fixture_notes):
    root = copy_fixture_notes(nested="notes")
    disc = discover(root)
    assert [f.rel_path for f in disc.files] == sorted("notes/" + f.name for f in FIXTURES_NOTES.glob("*.md"))
    assert len(disc.files) == 35
    flat = discover(root, recursive=False)
    assert flat.files == () and flat.subfolder_md_count == 35
    assert [w.code for w in flat.warnings] == [codes.W_NO_FILES]
    assert "35 .md files exist in subfolders" in flat.warnings[0].message


# --------------------------------------------------------------------------- aggregates
@pytest.mark.ac("AC-01.14", "AC-01.13")
@pytest.mark.sc("SC-41")
def test_status_counts(corpus):
    _, parsed = corpus
    assert Counter(p.status for p in parsed) == {"ok": 33, "empty": 1, "failed": 1}
    empty = [p for p in parsed if p.status == "empty"]
    failed = [p for p in parsed if p.status == "failed"]
    assert [p.rel_path for p in empty] == [EMPTY_FILE]
    assert _wcodes(empty[0]) == [codes.W_EMPTY_FILE]
    assert empty[0].size_bytes == 0 and empty[0].items == ()
    assert [p.rel_path for p in failed] == [FAILED_FILE]
    assert failed[0].failure.code == codes.E_BINARY
    assert failed[0].items == () and failed[0].meeting is None


@pytest.mark.ac("AC-01.14")
def test_117_items_104_open_13_done(corpus):
    _, parsed = corpus
    items = [i for p in parsed for i in p.items]
    assert len(items) == 117
    assert Counter(i.checkbox_state for i in items) == {"open": 104, "done": 13}


@pytest.mark.ac("AC-01.14", "AC-01.15", "AC-01.17", "AC-01.20")
def test_warning_aggregates(corpus):
    _, parsed = corpus
    c = Counter(w.code for p in parsed for w in p.warnings)
    assert c[codes.W_CHECKBOX_IGNORED_REGION] == 3
    assert c[codes.W_NONCHECKBOX_CANDIDATES] == 2
    assert c[codes.W_MEETING_DATE_INVALID] == 1
    assert c[codes.W_MEETING_DATE_MISSING] == 1
    assert c[codes.W_ATTENDEES_MISSING] == 2
    assert c[codes.W_EMPTY_FILE] == 1
    assert c[codes.W_DATE_NOT_DUE] == 2
    for zero in (
        codes.W_CHECKBOX_NOSPACE,
        codes.W_CHECKBOX_NONSTANDARD,
        codes.W_CHECKBOX_EMPTY,
        codes.W_FENCE_UNCLOSED,
        codes.W_HTML_COMMENT_UNCLOSED,
        codes.W_FRONTMATTER_INVALID,
        codes.W_SYMLINK_SKIPPED,
        codes.W_DIR_UNREADABLE,
        codes.W_LONG_LINE,
        codes.W_MEETING_DATE_FUTURE,
        codes.W_DATE_WEEKDAY_MISMATCH,
        codes.W_DATE_CONFLICT,
        codes.W_MEETING_DATE_FROM_FILENAME,
        codes.W_NO_ITEMS,
    ):
        assert c[zero] == 0, zero
    assert set(c) == {
        codes.W_CHECKBOX_IGNORED_REGION,
        codes.W_NONCHECKBOX_CANDIDATES,
        codes.W_MEETING_DATE_INVALID,
        codes.W_MEETING_DATE_MISSING,
        codes.W_ATTENDEES_MISSING,
        codes.W_EMPTY_FILE,
        codes.W_DATE_NOT_DUE,
    }


@pytest.mark.ac("AC-01.14")
def test_where_each_aggregate_warning_lives(by_name):
    def where(code):
        return sorted((n, w.line or 0) for n, p in by_name.items() for w in p.warnings if w.code == code)

    assert where(codes.W_CHECKBOX_IGNORED_REGION) == [
        ("2026-09-21-incident-review.md", 9),
        ("2026-10-09-security-review.md", 10),
        ("2026-10-09-security-review.md", 13),
    ]
    assert where(codes.W_NONCHECKBOX_CANDIDATES) == [
        ("2026-09-30-q4-offsite.md", 22),
        ("2026-09-30-q4-offsite.md", 24),
    ]
    assert where(codes.W_MEETING_DATE_INVALID) == [("2026-10-03-sales-pipeline.md", 2)]
    assert where(codes.W_MEETING_DATE_MISSING) == [("untitled.md", 0)]
    assert where(codes.W_ATTENDEES_MISSING) == [("2026-09-23-standup.md", 0), ("untitled.md", 0)]
    assert where(codes.W_EMPTY_FILE) == [(EMPTY_FILE, 0)]
    # the not-due phrase "from 9 Oct" (spec 9.2 trap), nothing else
    assert where(codes.W_DATE_NOT_DUE) == [
        ("2026-09-29-billing-cutover-planning.md", 12),
        ("2026-10-08-billing-go-no-go.md", 11),
    ]


# --------------------------------------------------------------------------- per file table
@pytest.mark.parametrize("name", sorted(EXPECTED))
@pytest.mark.ac("AC-01.14", "AC-01.15")
def test_per_file_expectations(by_name, name):
    status, mdate, source, raw, title, n_att, items, warns = EXPECTED[name]
    pf = by_name[name]
    assert pf.status == status
    if status == "failed":
        assert pf.failure.code == codes.E_BINARY
        return
    if status == "empty":
        assert pf.meeting is None
        assert _wcodes(pf) == [codes.W_EMPTY_FILE]
        return
    m = pf.meeting
    assert (m.date.isoformat() if m.date else None) == mdate
    assert m.date_source == source
    assert m.date_raw == raw
    assert m.title == title
    assert len(m.attendees) == n_att
    assert tuple((i.line_start, "d" if i.checkbox_state == "done" else "o") for i in pf.items) == items
    got = tuple((w.code.removeprefix("W-"), w.line) for w in pf.warnings if w.code != codes.W_DATE_NOT_DUE)
    assert got == warns
    assert all(i.line_start == i.line_end for i in pf.items)  # one-line items throughout the sample


# --------------------------------------------------------------------------- headers
@pytest.mark.ac("AC-01.15")
def test_front_matter_notes(by_name):
    for name, d in (("2026-09-16-standup.md", "2026-09-16"), ("2026-09-30-standup.md", "2026-09-30")):
        pf = by_name[name]
        assert pf.meeting.date_source == "front_matter" and pf.meeting.date.isoformat() == d
        assert pf.meeting.title == "Team standup"
        assert len(pf.meeting.attendees) == 3
        assert _wcodes(pf) == []
        assert [i.line_start for i in pf.items] == [11, 12]  # front matter lines count
    assert [a.raw_name for a in by_name["2026-09-16-standup.md"].meeting.attendees] == [
        "Anita Desai",
        "Lena Fischer",
        "Meera Iyer",
    ]


@pytest.mark.ac("AC-01.15")
def test_alternative_date_formats_have_no_mismatch_warning(by_name):
    a = by_name["2026-09-14-onboarding-flow-design.md"]
    assert a.meeting.date.isoformat() == "2026-09-14" and a.meeting.date_raw == "Monday, 14 September 2026"
    assert codes.W_DATE_WEEKDAY_MISMATCH not in _wcodes(a)
    b = by_name["2026-09-09-qa-standup.md"]
    assert b.meeting.date.isoformat() == "2026-09-09" and b.meeting.date_raw == "9 September 2026"
    assert _wcodes(b) == []


@pytest.mark.ac("AC-01.15", "AC-01.20")
@pytest.mark.sc("SC-49")
def test_invalid_date_is_invalid_not_missing_and_filename_unused(by_name):
    pf = by_name["2026-10-03-sales-pipeline.md"]
    assert pf.meeting.date is None and pf.meeting.date_source == "none"
    assert pf.meeting.date_raw == "sometime last week"
    assert _wcodes(pf) == [codes.W_MEETING_DATE_INVALID]
    assert codes.W_MEETING_DATE_MISSING not in _wcodes(pf)
    assert codes.W_MEETING_DATE_FROM_FILENAME not in _wcodes(pf)
    assert len(pf.items) == 2


@pytest.mark.ac("AC-01.15")
def test_yearless_date_is_treated_like_invalid():
    pf = parse_document(b"# Meeting: X\nDate: 16 Sep\nAttendees: A B\n- [ ] A B to do it\n", "2026-09-16-x.md")
    assert pf.meeting.date is None and pf.meeting.date_source == "none"
    assert _wcodes(pf) == [codes.W_MEETING_DATE_INVALID]


@pytest.mark.ac("AC-01.9")
def test_missing_attendees_and_headerless_note(by_name):
    s = by_name["2026-09-23-standup.md"]
    assert s.meeting.attendees == () and s.meeting.date.isoformat() == "2026-09-23"
    assert _wcodes(s) == [codes.W_ATTENDEES_MISSING]
    u = by_name["untitled.md"]
    assert u.status == "ok" and u.meeting.date is None and u.meeting.date_source == "none"
    assert u.meeting.date_raw is None and u.meeting.attendees == ()
    assert sorted(_wcodes(u)) == [codes.W_ATTENDEES_MISSING, codes.W_MEETING_DATE_MISSING]
    assert [(i.line_start, i.raw_text) for i in u.items] == [
        (1, "- [ ] Sam to chase the vendor"),
        (2, "- [ ] Priya to send the slides by Friday"),
    ]


# --------------------------------------------------------------------------- traps
@pytest.mark.ac("AC-01.14")
@pytest.mark.sc("SC-42")
def test_quoted_checkbox_is_not_imported(by_name):
    pf = by_name["2026-09-21-incident-review.md"]
    assert [i.line_start for i in pf.items] == [14, 15, 16, 17]
    assert pf.items[3].description == "Karan to refund affected customers"
    assert not any("by Friday" in i.description and i.description.startswith("Karan") for i in pf.items)
    (w,) = pf.warnings
    assert (w.code, w.line) == (codes.W_CHECKBOX_IGNORED_REGION, 9)
    assert json.loads(w.detail)["region"] == "quote"


@pytest.mark.ac("AC-01.5", "AC-01.14")
@pytest.mark.sc("SC-33", "SC-43")
def test_fenced_and_commented_checkboxes_are_not_imported(by_name):
    pf = by_name["2026-10-09-security-review.md"]
    assert [(i.line_start, i.checkbox_state) for i in pf.items] == [
        (18, "open"),
        (19, "open"),
        (20, "open"),
        (21, "done"),
    ]
    assert pf.items[3].description == "Karan to revoke access for the two ex-contractors"
    texts = " ".join(i.raw_text for i in pf.items)
    assert "Owner to remediate" not in texts and "hardware tokens" not in texts
    regions = {(w.line, json.loads(w.detail)["region"]) for w in pf.warnings}
    assert regions == {(10, "fence"), (13, "comment")}


@pytest.mark.ac("AC-01.17")
@pytest.mark.sc("SC-48")
def test_offsite_nested_items_and_candidates(by_name):
    pf = by_name["2026-09-30-q4-offsite.md"]
    assert [i.line_start for i in pf.items] == [9, 10, 11, 12, 16, 17, 18]
    assert pf.items[1].raw_text.startswith("  - [ ] Karan to validate")  # nested child is its own item
    assert pf.items[0].line_end == 9  # child is not folded into the parent
    assert pf.items[2].raw_text.startswith("* [ ] Lena") and pf.items[3].raw_text.startswith("+ [ ] Sam")
    assert [i.section_heading for i in pf.items] == ["Morning: where we are"] * 4 + [
        "Afternoon: people and process"
    ] * 3
    assert [(c.kind, c.line) for c in pf.candidates] == [("table_row", 22), ("action_line", 24)]
    assert pf.candidates[0].text == "| Anita | Refresh the brand kit | 2026-10-09 |"
    assert pf.candidates[1].text == "ACTION: Karan to circulate the offsite summary tomorrow"
    assert len(pf.meeting.attendees) == 9


@pytest.mark.ac("AC-01.16")
def test_finance_sync_crlf_bullets_and_states(by_name):
    pf = by_name["2026-10-02-finance-sync.md"]
    assert [(i.line_start, i.checkbox_state) for i in pf.items] == [
        (9, "done"),
        (10, "open"),
        (11, "open"),
        (12, "open"),
    ]
    assert [i.raw_text[:5] for i in pf.items] == ["- [X]", "* [ ]", "+ [ ]", "- [ ]"]
    assert not any("\r" in i.raw_text or "\r" in i.description for i in pf.items)
    assert pf.items[1].description == "Sam to cut cloud costs by 10% — proposal by 2026-10-09"
    assert pf.meeting.title == "Finance sync" and len(pf.meeting.attendees) == 3


@pytest.mark.ac("AC-01.16")
def test_crlf_file_parses_identically_to_lf_copy():
    data = (FIXTURES_NOTES / "2026-10-02-finance-sync.md").read_bytes()
    assert b"\r\n" in data
    lf = data.replace(b"\r\n", b"\n")
    assert lf != data
    a = parse_document(data, "2026-10-02-finance-sync.md").to_dict()
    b = parse_document(lf, "2026-10-02-finance-sync.md").to_dict()
    assert a["content_sha256"] != b["content_sha256"] and a["size_bytes"] != b["size_bytes"]
    for k in ("content_sha256", "size_bytes"):
        a.pop(k)
        b.pop(k)
    assert a == b


@pytest.mark.ac("AC-01.16")
def test_crlf_copy_through_files_on_disk(tmp_path):
    src = FIXTURES_NOTES / "2026-10-02-finance-sync.md"
    d = tmp_path / "n"
    d.mkdir()
    (d / "crlf.md").write_bytes(src.read_bytes())
    (d / "lf.md").write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))
    res = {f.rel_path: parse_file(f) for f in discover(d).files}
    x, y = res["crlf.md"].to_dict(), res["lf.md"].to_dict()
    for k in ("rel_path", "content_sha256", "size_bytes", "mtime_ns"):
        x.pop(k)
        y.pop(k)
    assert x == y


@pytest.mark.ac("AC-01.20")
def test_yaml_library_not_imported_for_front_matter_notes():
    code = (
        "import sys\n"
        "from actiontrack.parsing import parse_document\n"
        "from pathlib import Path\n"
        f"root = Path({str(FIXTURES_NOTES)!r})\n"
        "for n in ('2026-09-16-standup.md', '2026-09-30-standup.md'):\n"
        "    pf = parse_document((root / n).read_bytes(), n)\n"
        "    assert pf.meeting.date_source == 'front_matter', n\n"
        "assert 'yaml' not in sys.modules and 'ruamel' not in sys.modules\n"
        "print('ok')\n"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


# --------------------------------------------------------------------------- AC-01.19 invariants
def _source_lines(name: str) -> list[str]:
    text = (FIXTURES_NOTES / name).read_bytes().decode("utf-8")
    return re.split(r"\r\n|\n|\r", text)


@pytest.mark.ac("AC-01.19")
def test_item_text_invariants_over_all_117_items(corpus):
    _, parsed = corpus
    n = 0
    for p in parsed:
        lines = _source_lines(p.rel_path) if p.status == "ok" else []
        for it in p.items:
            n += 1
            where = (p.rel_path, it.line_start)
            assert it.raw_text == "\n".join(lines[it.line_start - 1 : it.line_end]), where
            assert unicodedata.is_normalized("NFC", it.description), where
            assert unicodedata.is_normalized("NFC", it.task_text), where
            assert it.task_text and it.task_text.strip() == it.task_text, where
            if it.task_text[0].isalpha():
                assert it.task_text[0].isupper(), (where, it.task_text)
            if it.due_hook is not None:
                for raw in it.due_hook.raws:
                    assert raw not in it.task_text, (where, it.task_text, raw)
    assert n == 117


@pytest.mark.ac("AC-01.19")
def test_null_analyzer_task_text_equals_description():
    _, parsed = _parse_all(analyzer=NullAnalyzer())
    items = [i for p in parsed for i in p.items]
    assert len(items) == 117
    for it in items:
        assert it.owner_hook is None and it.due_hook is None
        assert it.task_text == it.description, it


@pytest.mark.ac("AC-01.2", "AC-01.19")
def test_due_phrase_removed_from_task_text_but_kept_in_description(by_name):
    it = by_name["untitled.md"].items[1]
    assert it.description == "Priya to send the slides by Friday"
    assert it.task_text == "Priya to send the slides"  # owner-clause removal is M4's job
    assert it.raw_text == "- [ ] Priya to send the slides by Friday"


# --------------------------------------------------------------------------- parse_file == parse_document
@pytest.mark.ac("AC-01.14")
def test_parse_file_equals_parse_document_except_mtime(corpus):
    disc, parsed = corpus
    by = {p.rel_path: p for p in parsed}
    for f in disc.files:
        a = parse_document((FIXTURES_NOTES / f.rel_path).read_bytes(), f.rel_path).to_dict()
        b = by[f.rel_path].to_dict()
        assert a.pop("mtime_ns") is None
        b.pop("mtime_ns")
        assert a == b, f.rel_path


# --------------------------------------------------------------------------- determinism + golden
def _dicts(parsed):
    out = []
    for p in parsed:
        d = p.to_dict()
        d["mtime_ns"] = None
        out.append(d)
    return out


def _dump(dicts) -> str:
    return json.dumps(dicts, indent=1, ensure_ascii=False) + "\n"


@pytest.mark.ac("AC-01.14")
def test_ingest_order_of_corpus(corpus):
    _, parsed = corpus
    dated = [p for p in parsed if p.meeting is not None and p.meeting.date is not None]
    undated = [p for p in parsed if p.meeting is None or p.meeting.date is None]
    assert [p.rel_path for p in parsed] == [p.rel_path for p in dated] + [p.rel_path for p in undated]
    assert [p.rel_path for p in undated] == [EMPTY_FILE, "2026-10-03-sales-pipeline.md", FAILED_FILE, "untitled.md"]
    keys = [(p.meeting.date, p.rel_path) for p in dated]
    assert keys == sorted(keys)
    assert len(dated) == 31


@pytest.mark.ac("AC-01.14")
def test_parse_is_byte_identical_twice_and_under_shuffled_discovery(corpus):
    _, first = corpus
    _, second = _parse_all()
    assert _dump(_dicts(first)) == _dump(_dicts(second))
    disc = discover(FIXTURES_NOTES)
    shuffled = list(disc.files)
    random.Random(20261022).shuffle(shuffled)
    assert [f.rel_path for f in shuffled] != [f.rel_path for f in disc.files]
    reparsed = sorted((parse_file(f) for f in shuffled), key=ingest_order_key)
    assert _dump(_dicts(reparsed)) == _dump(_dicts(first))


@pytest.mark.ac("AC-01.14")
def test_golden_parse_output(corpus):
    _, parsed = corpus
    current = _dicts([parse_document((FIXTURES_NOTES / p.rel_path).read_bytes(), p.rel_path) for p in parsed])
    if os.environ.get("M2_REGEN_GOLDEN") == "1":  # manual regeneration only
        GOLDEN.write_text(_dump(current), encoding="utf-8", newline="\n")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert [g["rel_path"] for g in golden] == [c["rel_path"] for c in current]
    for g, c in zip(golden, current):
        assert g == c, g["rel_path"]
    assert GOLDEN.read_text(encoding="utf-8") == _dump(current)


@pytest.mark.ac("AC-01.11")
def test_fixture_tree_untouched_by_a_full_parse():
    before = _snapshot(FIXTURES_NOTES)
    _parse_all()
    assert _snapshot(FIXTURES_NOTES) == before
