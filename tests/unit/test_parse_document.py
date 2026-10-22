"""T-M2-15: pure ``parse_document`` / ``parse_file`` assembly, hooks, failure isolation."""

from __future__ import annotations

import builtins
import os
from collections import Counter
from datetime import date

import pytest

from actiontrack import codes
from actiontrack.domain.models import ParsedFile, TextSpan
from actiontrack.parsing import (
    AnalyzerContext,
    ItemAnalysis,
    NullAnalyzer,
    parse_document,
    parse_file,
)
from actiontrack.parsing.discovery import DiscoveredFile, discover
from actiontrack.parsing.items import derive_task_text
from actiontrack.parsing.reader import RawRead
from tests.conftest import FIXTURES_NOTES


def parse(text: str | bytes, name: str = "n.md", **kw) -> ParsedFile:
    data = text if isinstance(text, bytes) else text.encode("utf-8")
    return parse_document(data, name, **kw)


def wcodes(pf: ParsedFile) -> list[str]:
    return [w.code for w in pf.warnings]


# --------------------------------------------------------------------------- no-items / empty
@pytest.mark.ac("AC-01.18")
def test_only_heading_gives_no_items_warning():
    pf = parse("# Meeting: X\nDate: 2026-10-01\nAttendees: A B\n\n## Notes\nJust talk.\n")
    assert pf.status == "ok" and pf.items == ()
    assert wcodes(pf) == [codes.W_NO_ITEMS]
    assert pf.warnings[0].line is None


@pytest.mark.ac("AC-01.18")
def test_only_nonstandard_checkbox():
    pf = parse("# T\nDate: 2026-10-01\nAttendees: A B\n- [-] x\n")
    assert sorted(wcodes(pf)) == [codes.W_CHECKBOX_NONSTANDARD, codes.W_NO_ITEMS]


@pytest.mark.ac("AC-01.18")
def test_only_empty_checkbox():
    pf = parse("# T\nDate: 2026-10-01\nAttendees: A B\n- [ ]\n")
    assert sorted(wcodes(pf)) == [codes.W_CHECKBOX_EMPTY, codes.W_NO_ITEMS]
    assert codes.W_CHECKBOX_NONSTANDARD not in wcodes(pf)


@pytest.mark.ac("AC-01.16")
def test_empty_file_short_circuits():
    pf = parse_document((FIXTURES_NOTES / "2026-09-09-notes-final-v2.md").read_bytes(), "2026-09-09-notes-final-v2.md")
    assert pf.status == "empty" and wcodes(pf) == [codes.W_EMPTY_FILE]
    assert pf.items == () and pf.meeting is None and pf.failure is None
    for blob in (b"", b"\xef\xbb\xbf", b" \r\n\t\n"):
        e = parse(blob)
        assert e.status == "empty" and wcodes(e) == [codes.W_EMPTY_FILE]
        assert e.size_bytes == len(blob)


# --------------------------------------------------------------------------- reader failures
@pytest.mark.ac("AC-01.16")
def test_byte_level_failures():
    b = parse(b"a\x00b")
    assert b.status == "failed" and b.failure.code == codes.E_BINARY and b.warnings == ()
    e = parse(b"ok\n\xff\xfe-bad")
    assert e.status == "failed" and e.failure.code == codes.E_ENCODING and e.failure.byte_offset == 3
    assert e.content_sha256 is not None  # hash of the bytes as read is kept
    big = parse(b"x" * 11, max_file_bytes=10)
    assert big.status == "failed" and big.failure.code == codes.E_TOO_LARGE and big.failure.size_bytes == 11


def test_sample_scratch_is_binary():
    pf = parse_document((FIXTURES_NOTES / "scratch.md").read_bytes(), "scratch.md")
    assert pf.status == "failed" and pf.failure.code == codes.E_BINARY


def test_parse_file_reader_failures(tmp_path):
    missing = DiscoveredFile("gone.md", str(tmp_path / "gone.md"), 1, 1)
    pf = parse_file(missing)
    assert pf.status == "failed" and pf.failure.code == codes.E_UNREADABLE
    f = tmp_path / "big.md"
    f.write_bytes(b"- [ ] x\n" * 10)
    pf = parse_file(DiscoveredFile("big.md", str(f), 80, 1), max_file_bytes=20)
    assert pf.failure.code == codes.E_TOO_LARGE


def test_parse_file_ok_and_hash_matches_disk(tmp_path):
    f = tmp_path / "2026-10-01-a.md"
    f.write_bytes(b"Date: 2026-10-14\nAttendees: Ann Lee\n- [ ] Ann Lee to ship it by Friday\n")
    (d,) = discover(tmp_path).files
    pf = parse_file(d)
    import hashlib

    assert pf.status == "ok" and pf.content_sha256 == hashlib.sha256(f.read_bytes()).hexdigest()
    assert pf.size_bytes == f.stat().st_size and pf.mtime_ns == f.stat().st_mtime_ns
    assert pf.items[0].due_hook.due_date == date(2026, 10, 16)


# --------------------------------------------------------------------------- failure isolation
class _Boom:
    def analyze(self, description, ctx):
        raise RuntimeError("kaboom")


@pytest.mark.ac("AC-01.16")
def test_analyzer_exception_becomes_internal_parse_failure():
    pf = parse("Date: 2026-10-01\n- [ ] Ann to do it\n", analyzer=_Boom())
    assert pf.status == "failed" and pf.failure.code == codes.E_INTERNAL_PARSE
    assert "kaboom" in pf.failure.traceback and "RuntimeError" in pf.failure.traceback
    assert pf.items == () and pf.warnings == ()
    # other files in the same loop succeed
    results = [parse("- [ ] A to b\n", "x.md", analyzer=a) for a in (_Boom(), NullAnalyzer())]
    assert [r.status for r in results] == ["failed", "ok"]


def test_unexpected_error_in_parsing_stage_is_isolated(monkeypatch):
    import actiontrack.parsing as pkg

    def bad(*a, **k):
        raise ValueError("bad scan")

    monkeypatch.setattr(pkg._md, "scan_document", bad)
    pf = parse("- [ ] x\n")
    assert pf.status == "failed" and pf.failure.code == codes.E_INTERNAL_PARSE
    assert "bad scan" in pf.failure.traceback


# --------------------------------------------------------------------------- purity / determinism
@pytest.mark.ac("AC-01.19")
def test_purity_no_filesystem_and_repeatable(monkeypatch):
    data = (FIXTURES_NOTES / "2026-09-01-quote-portal-kickoff.md").read_bytes()
    first = parse_document(data, "k.md")

    def no(*a, **k):
        raise AssertionError("filesystem touched")

    with monkeypatch.context() as mp:  # undone before the autouse teardown guards run
        mp.setattr(builtins, "open", no)
        mp.setattr(os, "stat", no)
        second = parse_document(data, "k.md")
        third = parse_document(RawRead("k.md", data, first.content_sha256, len(data), 5), "k.md")
    assert first == second and first.to_dict() == second.to_dict()
    assert third.mtime_ns == 5 and third.items == first.items


def test_warnings_sorted_by_line_code():
    pf = parse("- [-] a\n- [ ]\n- [] b\n")
    keys = [(w.line or 0, w.code) for w in pf.warnings]
    assert keys == sorted(keys) and len(keys) >= 4


def test_crlf_bom_and_cr_inputs_equal_lf():
    lf = parse("Date: 2026-10-01\nAttendees: A B\n- [ ] A B to go\n- [x] done thing\n")
    for variant in (
        b"\xef\xbb\xbf" + b"Date: 2026-10-01\r\nAttendees: A B\r\n- [ ] A B to go\r\n- [x] done thing\r\n",
        b"Date: 2026-10-01\rAttendees: A B\r- [ ] A B to go\r- [x] done thing\r",
    ):
        got = parse(variant)
        assert [(i.line_start, i.description, i.checkbox_state) for i in got.items] == [
            (i.line_start, i.description, i.checkbox_state) for i in lf.items
        ]


def test_long_line_warning_uses_limit():
    pf = parse("- [ ] " + "a" * 50 + "\n", long_line_limit=20)
    assert codes.W_LONG_LINE in wcodes(pf)


def test_meeting_future_date_never_emitted():
    pf = parse("Date: 2999-01-01\nAttendees: A B\n- [ ] A B to go\n")
    assert codes.W_MEETING_DATE_FUTURE not in wcodes(pf)


# --------------------------------------------------------------------------- AC-01.20
@pytest.mark.ac("AC-01.20")
def test_body_survives_bad_front_matter():
    pf = parse("---\ntitle:\n  nested: map\n---\n- [ ] Priya to send the deck\n")
    assert pf.status == "ok"
    fm = [w for w in pf.warnings if w.code == codes.W_FRONTMATTER_INVALID]
    assert len(fm) == 1 and fm[0].line == 1
    assert [(i.line_start, i.description) for i in pf.items] == [(5, "Priya to send the deck")]


# --------------------------------------------------------------------------- hooks / task_text
class _Spy:
    def __init__(self, spans=()):
        self.seen: list[tuple[str, AnalyzerContext]] = []
        self.spans = spans

    def analyze(self, description, ctx):
        self.seen.append((description, ctx))
        return ItemAnalysis(owner="OWN", due="DUE", owner_spans=self.spans(description), due_spans=())


@pytest.mark.ac("AC-01.19")
def test_spy_analyzer_gets_description_and_context():
    spy = _Spy(lambda d: ())
    pf = parse("Date: 2026-10-14\n- [ ] **Priya** to send *the deck* by Friday\n", analyzer=spy, date_order="dmy")
    (desc, ctx) = spy.seen[0]
    assert desc == "Priya to send the deck by Friday" and "*" not in desc
    assert ctx == AnalyzerContext(date(2026, 10, 14), "dmy")
    assert (pf.items[0].owner_hook, pf.items[0].due_hook) == ("OWN", "DUE")
    assert pf.items[0].raw_text.startswith("- [ ] **Priya**")


@pytest.mark.ac("AC-01.19")
def test_null_analyzer_leaves_hooks_none_and_task_text_is_description():
    pf = parse("Date: 2026-10-14\n- [ ] priya to send the deck by Friday\n", analyzer=NullAnalyzer())
    (it,) = pf.items
    assert it.owner_hook is None and it.due_hook is None
    assert it.task_text == "Priya to send the deck by Friday"


@pytest.mark.ac("AC-01.19")
def test_task_text_equals_derive_task_text_with_stub_spans():
    spy = _Spy(lambda d: [TextSpan(0, len("Priya to "))])
    pf = parse("- [ ] Priya to send the deck\n", analyzer=spy)
    assert pf.items[0].task_text == derive_task_text("Priya to send the deck", [TextSpan(0, 9)]) == "Send the deck"


def test_default_analyzer_resolves_due_and_strips_it_from_task_text():
    pf = parse("Date: 2026-10-14\n- [ ] Priya to send the revised quote by Friday.\n")
    (it,) = pf.items
    assert it.due_hook.due_date == date(2026, 10, 16)
    assert it.task_text == "Priya to send the revised quote"


def test_same_description_twice_is_analysed_once_and_both_items_kept():
    spy = _Spy(lambda d: ())
    pf = parse("- [ ] Same thing\n- [ ] Same thing\n", analyzer=spy)
    assert len(pf.items) == 2 and len(spy.seen) == 1
    assert [i.occurrence_index for i in pf.items] == [0, 1]


# --------------------------------------------------------------------------- golden / sample
@pytest.mark.ac("AC-01.14")
def test_golden_kickoff():
    name = "2026-09-01-quote-portal-kickoff.md"
    pf = parse_document((FIXTURES_NOTES / name).read_bytes(), name)
    d = pf.to_dict()
    assert d["status"] == "ok" and d["warnings"] == [] and d["candidates"] == []
    assert d["meeting"]["date"] == "2026-09-01" and d["meeting"]["date_source"] == "date_line"
    assert d["meeting"]["title"] == "Quote portal kickoff"
    assert [a["raw_name"] for a in d["meeting"]["attendees"]] == [
        "Priya Nair", "Rahul Mehta", "Anita Desai", "Sam Okafor"]
    assert [(i["line_start"], i["line_end"], i["checkbox_state"]) for i in d["items"]] == [
        (9, 9, "open"), (10, 10, "open"), (11, 11, "open"), (12, 12, "open")]
    assert [i["task_text"] for i in d["items"]] == [
        "Priya to send the revised quote to Acme",
        "Rahul to set up the staging environment",
        "Anita to share first wireframes",
        "Sam to confirm cloud budget with finance",
    ]
    assert [i["section_heading"] for i in d["items"]] == ["Action items"] * 4
    assert pf.to_dict() == pf.to_dict()


@pytest.mark.ac("AC-01.13", "AC-01.16")
def test_sample_corpus_parse_level_aggregates():
    discovered = discover(FIXTURES_NOTES)
    assert len(discovered.files) == 35  # the .txt is not discovered
    parsed = {}
    for f in discovered.files:
        parsed[f.rel_path] = parse_file(f)
    statuses = Counter(p.status for p in parsed.values())
    assert statuses == {"ok": 33, "empty": 1, "failed": 1}
    assert parsed["scratch.md"].failure.code == codes.E_BINARY
    assert parsed["2026-09-09-notes-final-v2.md"].status == "empty"
    items = [i for p in parsed.values() for i in p.items]
    assert Counter(i.checkbox_state for i in items) == {"open": 104, "done": 13} and len(items) == 117
    assert all(i.due_hook is not None for i in items)
    wc = Counter(w.code for p in parsed.values() for w in p.warnings)
    assert wc[codes.W_CHECKBOX_IGNORED_REGION] == 3 and wc[codes.W_NONCHECKBOX_CANDIDATES] == 2
    assert wc[codes.W_EMPTY_FILE] == 1 and wc[codes.W_MEETING_DATE_INVALID] == 1
    assert wc[codes.W_DATE_NOT_DUE] == 2 and wc[codes.W_DUE_BEFORE_MEETING] == 0
    assert wc[codes.W_MEETING_DATE_FUTURE] == 0 and wc[codes.W_FRONTMATTER_INVALID] == 0
    # ordering key sanity: every parsed file sorts without error
    from actiontrack.domain.models import ingest_order_key

    assert len(sorted(parsed.values(), key=ingest_order_key)) == 35
