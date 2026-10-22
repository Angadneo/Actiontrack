"""T-M3-11: sample sweep over all 117 items, purity guard, integration with M2 parsing.

The expectation table ``tests/golden/m3_sample_due.json`` was authored by hand (one row per action item of the
35 ``.md`` files of ``tests/fixtures/notes``) and is *not* a dump of the implementation: every ``due_*`` field
was computed with plain ``datetime``/``calendar`` arithmetic from the meeting date and the phrase (``how`` /
``args`` of the row record the rule applied; ``_derive`` below is the independent calculator and
``test_golden_matches_independent_derivation`` re-derives every row with it).

Verify:      ``pytest tests/integration/test_m3_sample.py``
Regenerate:  edit a row by hand, recompute its ``due_*`` fields with ``_derive(date, how, args)``; never paste
             output of ``actiontrack`` into the golden.

Verified totals over the 117 items (the real numbers equal the task block's): 73 resolved, 4 unresolved
(2 ``vague``, 2 ``no_meeting_date``), 40 ``none`` (38 without a phrase + 2 ``from 9 Oct``);
confidence ``explicit`` 17 / ``inferred`` 14 / ``relative`` 42.
"""

from __future__ import annotations

import calendar
import datetime
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from unittest import mock

import pytest

from actiontrack import codes
from actiontrack.domain import dates
from actiontrack.parsing import parse_document
from actiontrack.parsing.discovery import discover
from tests.conftest import FIXTURES_NOTES, REPO_ROOT

GOLDEN = REPO_ROOT / "tests" / "golden" / "m3_sample_due.json"
_WD = {n: i for i, n in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}
_ONE = datetime.timedelta(days=1)
_REAL_DATE = datetime.date


# ------------------------------------------------------------------ independent calculator
def _derive(m: datetime.date | None, how: str, args: list) -> dict:
    """Expected due fields from hand-written rule semantics (spec 9.3), no actiontrack code involved."""
    out: dict = {"due_date": None, "due_confidence": "none", "due_rule": None, "due_reason": None, "warnings": []}

    def res(d, conf, rule, warn=False):
        out.update(due_date=d.isoformat(), due_confidence=conf, due_rule=rule)
        if warn and d < m:
            out["warnings"].append("W-DUE-BEFORE-MEETING")

    def unres(reason):
        out.update(due_confidence="unresolved", due_reason=reason)

    if how == "none":
        return out
    if how == "notdue":
        out["warnings"] = ["W-DATE-NOT-DUE"]
        return out
    if how == "vague":
        unres("vague")
        return out
    if how == "nomd" or (m is None and how != "iso"):
        unres("no_meeting_date")
        return out
    if how == "iso":
        d = _REAL_DATE.fromisoformat(args[0])
        out.update(due_date=d.isoformat(), due_confidence="explicit", due_rule="iso_date")
        if m is not None and d < m:
            out["warnings"].append("W-DUE-BEFORE-MEETING")
        return out
    if how in ("tomorrow", "eod_tomorrow"):
        res(m + _ONE, "relative", "tomorrow")
    elif how == "eod":
        res(m, "relative", "eod_alone")
    elif how == "wd":
        delta = (_WD[args[0]] - m.weekday()) % 7
        if delta == 0:
            unres("same_weekday")
        else:
            res(m + datetime.timedelta(days=delta), "relative", "weekday_next")
    elif how == "next_wd":
        res(m - datetime.timedelta(days=m.weekday()) + datetime.timedelta(days=7 + _WD[args[0]]),
            "relative", "weekday_next_week")
    elif how == "eonw":
        res(m - datetime.timedelta(days=m.weekday()) + datetime.timedelta(days=11), "relative", "end_of_next_week")
    elif how == "eom":
        res(_REAL_DATE(m.year, m.month, calendar.monthrange(m.year, m.month)[1]), "relative", "end_of_month")
    elif how == "yl":
        mo, d = args
        c = _REAL_DATE(m.year, mo, d)
        if c >= m:
            res(c, "inferred", "yearless_date")
        elif (m - c).days <= 60:
            res(c, "inferred", "yearless_date", True)
        else:
            res(_REAL_DATE(m.year + 1, mo, d), "inferred", "yearless_date")
    elif how in ("wdd", "event"):
        for y in (m.year, m.year + 1, m.year - 1):
            c = _REAL_DATE(y, args[1], args[2])
            if c.weekday() == _WD[args[0]]:
                res(c, "inferred", "weekday_date" if how == "wdd" else "event_date", True)
                break
        else:
            unres("weekday_date_mismatch")
    else:  # pragma: no cover
        raise ValueError(how)
    return out


# ------------------------------------------------------------------------------ data
def _golden() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["rows"]


def _md_files() -> list[str]:
    return [f.rel_path for f in discover(FIXTURES_NOTES).files]


def _snapshot(analyzer=None) -> tuple[dict[str, dict], dict]:
    """Parse every discovered ``.md`` fixture. Returns ({'file:line': due-fields}, {file: ParsedFile})."""
    blobs = {rel: (FIXTURES_NOTES / rel).read_bytes() for rel in _md_files()}
    parsed = {rel: parse_document(b, rel, analyzer=analyzer) for rel, b in blobs.items()}
    return _flatten(parsed), parsed


def _flatten(parsed: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rel, pf in parsed.items():
        for it in pf.items:
            d = it.due_hook
            out[f"{rel}:{it.line_start}"] = {
                "meeting_date": pf.meeting.date.isoformat() if pf.meeting.date else None,
                "due_raw": d.due_raw,
                "due_date": d.due_date.isoformat() if d.due_date else None,
                "due_confidence": d.due_confidence.value,
                "due_rule": d.due_rule.value if d.due_rule else None,
                "due_reason": d.due_reason.value if d.due_reason else None,
                "due_raw_other": d.due_raw_other,
                "warnings": list(d.warnings),
            }
    return out


@pytest.fixture(scope="module")
def snapshot():
    return _snapshot()


# ------------------------------------------------------------------------------ golden
def test_golden_shape_and_totals():
    rows = _golden()
    assert len(rows) == 117
    keys = [f"{r['file']}:{r['line']}" for r in rows]
    assert len(set(keys)) == 117
    assert sum(1 for r in rows if r["due_date"]) == 73
    assert sum(1 for r in rows if r["due_confidence"] == "unresolved") == 4
    assert sum(1 for r in rows if r["due_confidence"] == "none") == 40
    assert Counter(r["due_reason"] for r in rows if r["due_confidence"] == "unresolved") == {
        "vague": 2, "no_meeting_date": 2}
    assert {k for k, r in zip(keys, rows) if r["due_reason"] == "vague"} == {
        "2026-09-30-q4-offsite.md:12", "2026-10-14-acme-renewal-wrapup.md:10"}
    assert {k for k, r in zip(keys, rows) if r["due_reason"] == "no_meeting_date"} == {
        "2026-10-03-sales-pipeline.md:6", "untitled.md:2"}
    assert sum(1 for r in rows if r["due_confidence"] == "none" and r["due_raw"] is None
               and r["due_raw_other"] is None) == 38
    assert sum(1 for r in rows if r["due_raw_other"] == "9 Oct") == 2


@pytest.mark.ac("AC-06.1", "AC-06.7")
def test_golden_matches_independent_derivation():
    """Every hand-typed golden row equals the test module's own date arithmetic (golden integrity)."""
    for r in _golden():
        m = _REAL_DATE.fromisoformat(r["meeting_date"]) if r["meeting_date"] else None
        # the meeting date is the date in the file name (== the note's Date header) except the two undated notes
        if m is not None:
            assert r["file"].startswith(m.isoformat())
        want = _derive(m, r["how"], r["args"])
        for k in ("due_date", "due_confidence", "due_rule", "due_reason", "warnings"):
            assert r[k] == want[k], (r["file"], r["line"], k)


def test_golden_category_distribution():
    rows = [r for r in _golden() if r["due_date"]]
    conf = Counter(r["due_confidence"] for r in rows)
    assert conf == {"explicit": 17, "inferred": 14, "relative": 42}
    rules = Counter(r["due_rule"] for r in rows)
    assert rules == {
        "weekday_next": 31, "tomorrow": 6, "eod_alone": 2, "weekday_next_week": 1,
        "end_of_next_week": 1, "end_of_month": 1, "yearless_date": 12, "weekday_date": 1,
        "event_date": 1, "iso_date": 17,
    }
    # 5 plain `tomorrow` + 1 `EOD tomorrow` share the rule id `tomorrow`
    assert sum(1 for r in rows if r["how"] == "tomorrow") == 5 and sum(1 for r in rows if r["how"] == "eod_tomorrow") == 1
    assert sum(1 for r in rows if r["how"] in ("yl", "wdd")) == 13
    assert not any(r["due_rule"] == "weekday_next" and r["due_reason"] for r in rows)


# ------------------------------------------------------------------------------ the sweep
@pytest.mark.ac("AC-01.2", "AC-06.1", "AC-06.6", "AC-06.7", "AC-06.10")
@pytest.mark.sc("SC-01", "SC-49", "SC-64")
def test_sweep_matches_golden(snapshot):
    snap, parsed = snapshot
    rows = _golden()
    assert set(snap) == {f"{r['file']}:{r['line']}" for r in rows}, "item set differs from golden"
    for r in rows:
        got = snap[f"{r['file']}:{r['line']}"]
        item = next(i for i in parsed[r["file"]].items if i.line_start == r["line"])
        assert r["snippet"] in item.description, (r["file"], r["line"], item.description)
        assert got["meeting_date"] == r["meeting_date"], (r["file"], r["line"])
        for k in ("due_raw", "due_date", "due_confidence", "due_rule", "due_reason", "due_raw_other", "warnings"):
            assert got[k] == r[k], (r["file"], r["line"], k, got[k], r[k])


def test_sweep_file_statuses(snapshot):
    _, parsed = snapshot
    assert len(parsed) == 35  # discovered .md files (the .txt is ignored by discovery)
    st = Counter(pf.status for pf in parsed.values())
    assert st == {"ok": 33, "empty": 1, "failed": 1}
    assert parsed["scratch.md"].status == "failed" and parsed["2026-09-09-notes-final-v2.md"].status == "empty"
    assert sum(len(pf.items) for pf in parsed.values()) == 117


@pytest.mark.ac("AC-06.6", "AC-06.10")
def test_sweep_totals_and_distribution(snapshot):
    snap, _ = snapshot
    vals = list(snap.values())
    resolved = [v for v in vals if v["due_date"]]
    unresolved = [v for v in vals if v["due_confidence"] == "unresolved"]
    none = [v for v in vals if v["due_confidence"] == "none"]
    assert (len(resolved), len(unresolved), len(none)) == (73, 4, 40)
    assert Counter(v["due_reason"] for v in unresolved) == {"vague": 2, "no_meeting_date": 2}
    assert Counter(v["due_confidence"] for v in resolved) == {"explicit": 17, "inferred": 14, "relative": 42}
    # G-07: no year-bearing date is inferred (a raw containing a 4-digit year is always explicit)
    for v in resolved:
        has_year = re.search(r"\b\d{4}\b", v["due_raw"] or "") is not None
        assert (v["due_confidence"] == "explicit") == has_year, v
    # every stored rule is in the 9.5 catalogue; unresolved / none carry no rule (AC-06.6)
    assert {v["due_rule"] for v in resolved} <= codes.DUE_RULES
    assert all(v["due_rule"] is None and v["due_date"] is None for v in unresolved)
    assert all(v["due_reason"] in codes.DUE_REASONS for v in unresolved)
    assert all(v["due_rule"] is None and v["due_reason"] is None for v in none)
    # no same_weekday anywhere in the sample (every bare weekday differs from its meeting's weekday)
    assert not any(v["due_reason"] == "same_weekday" for v in vals)
    # 31 by-weekday items
    assert sum(1 for v in resolved if v["due_rule"] == "weekday_next") == 31


@pytest.mark.ac("AC-06.5")
def test_traps_never_multiple_dates(snapshot):
    snap, parsed = snapshot
    trap = re.compile(r"Q4|6 months|5 customer|50 invoices|on staging|in CRM|on all admin accounts")
    seen = 0
    for rel, pf in parsed.items():
        for it in pf.items:
            if trap.search(it.description):
                seen += 1
                assert snap[f"{rel}:{it.line_start}"]["due_reason"] != "multiple_dates", it.description
    assert seen >= 8
    assert not any(v["due_reason"] == "multiple_dates" for v in snap.values())


@pytest.mark.ac("AC-06.8")
def test_sweep_warning_counts(snapshot):
    _, parsed = snapshot
    codes_seen = Counter(w.code for pf in parsed.values() for w in pf.warnings)
    assert codes_seen.get("W-DUE-BEFORE-MEETING", 0) == 0
    assert codes_seen.get("W-DATE-NOT-DUE", 0) == 2


# ------------------------------------------------------------------------------ purity (BR-05, AC-06.4)
class _NoClockDate(_REAL_DATE):
    @classmethod
    def today(cls):
        raise AssertionError("date.today() called during resolution (BR-05)")


class _NoClockDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        raise AssertionError("datetime.now() called")

    @classmethod
    def utcnow(cls):
        raise AssertionError("datetime.utcnow() called")

    @classmethod
    def today(cls):
        raise AssertionError("datetime.today() called")


def _boom(*a, **k):
    raise AssertionError("clock function called during resolution (BR-05)")


@pytest.mark.ac("AC-06.4")
def test_sweep_independent_of_clock(snapshot):
    baseline, _ = snapshot
    blobs = {rel: (FIXTURES_NOTES / rel).read_bytes() for rel in _md_files()}
    with mock.patch.object(time, "time", _boom), mock.patch.object(time, "localtime", _boom), \
            mock.patch.object(time, "gmtime", _boom), mock.patch.object(time, "time_ns", _boom), \
            mock.patch.object(datetime, "date", _NoClockDate), \
            mock.patch.object(datetime, "datetime", _NoClockDatetime):
        parsed = {rel: parse_document(b, rel) for rel, b in blobs.items()}
        flat = _flatten(parsed)
    assert flat == baseline


_SUBPROCESS_SCRIPT = r"""
import json, sys, pathlib
sys.path.insert(0, sys.argv[1])
from actiontrack.parsing import parse_document
root = pathlib.Path(sys.argv[2])
out = {}
for p in sorted(root.glob("*.md")):
    pf = parse_document(p.read_bytes(), p.name)
    for it in pf.items:
        d = it.due_hook
        out[p.name + ":" + str(it.line_start)] = [
            str(d.due_raw), str(d.due_date), d.due_confidence.value, str(d.due_rule), str(d.due_reason),
            str(d.due_raw_other), list(d.warnings)]
print(json.dumps(out, sort_keys=True))
"""


def _run_under_tz(tz: str) -> dict:
    env = {k: os.environ[k] for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "SYSTEMROOT", "SYSTEMDRIVE",
                                      "PATH", "TEMP", "TMP") if k in os.environ}
    env["TZ"] = tz
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(REPO_ROOT / "src"), str(FIXTURES_NOTES)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.ac("AC-06.4")
def test_sweep_identical_in_subprocess_under_three_timezones(snapshot):
    snap, _ = snapshot
    expected = {
        k: [str(v["due_raw"]), str(v["due_date"]), v["due_confidence"], str(v["due_rule"]),
            str(v["due_reason"]), str(v["due_raw_other"]), v["warnings"]]
        for k, v in snap.items()
    }
    results = [_run_under_tz(tz) for tz in ("UTC", "PST8PDT", "IST-5:30")]
    assert len(expected) == 117
    for res in results:
        assert res == expected


# ------------------------------------------------------------------------------ M2 integration
def _item_warning_view(pf) -> list[tuple]:
    return sorted(
        (w.line, w.code, w.detail) for w in pf.warnings
        if w.code in (codes.W_DATE_NOT_DUE, codes.W_DUE_BEFORE_MEETING)
    )


def _expected_item_warnings(pf) -> list[tuple]:
    out = []
    m = pf.meeting.date
    for it in pf.items:
        r = dates.resolve_due(it.description, m)
        for code in r.warnings:
            if code == codes.W_DATE_NOT_DUE:
                out.append((it.line_start, code, r.due_raw_other))
            else:
                out.append((it.line_start, code, f"{r.due_date.isoformat()} < {m.isoformat()}"))
    return sorted(out)


def test_parse_document_item_warnings_match_resolve_due_on_sample(snapshot):
    _, parsed = snapshot
    total = 0
    for pf in parsed.values():
        if pf.status != "ok":
            continue
        assert _item_warning_view(pf) == _expected_item_warnings(pf), pf.rel_path
        total += len(_item_warning_view(pf))
    assert total == 2  # the two `from 9 Oct` items


@pytest.mark.ac("AC-06.8")
def test_parse_document_item_warnings_match_resolve_due_synthetic():
    note = (
        "# Meeting: Sync\nDate: 2026-10-12\nAttendees: Priya Nair, Sam Okafor\n\n## Actions\n"
        "- [ ] Priya to ship the fix by 9 Oct\n"
        "- [ ] Sam to freeze edits from 9 Oct\n"
        "- [ ] Priya to report on it by yesterday\n"
        "- [ ] Sam to close the old ticket by 2026-10-01\n"
        "- [ ] Priya to wrap up by 2026-10-30\n"
        "- [ ] Sam to review by Wed 7 Oct\n"
    )
    pf = parse_document(note.encode(), "2026-10-12-sync.md")
    assert pf.status == "ok" and len(pf.items) == 6
    assert _item_warning_view(pf) == _expected_item_warnings(pf)
    view = _item_warning_view(pf)
    before = [v for v in view if v[1] == codes.W_DUE_BEFORE_MEETING]
    assert [(v[0], v[2]) for v in before] == [
        (6, "2026-10-09 < 2026-10-12"), (8, "2026-10-11 < 2026-10-12"),
        (9, "2026-10-01 < 2026-10-12"), (11, "2026-10-07 < 2026-10-12")]
    assert [v[0] for v in view if v[1] == codes.W_DATE_NOT_DUE] == [7]
    # 2026-10-11 is yesterday = Sunday; 7 Oct 2026 is a Wednesday: independent check
    assert _REAL_DATE(2026, 10, 7).weekday() == 2


# ------------------------------------------------------------------------------ task_text interplay
def _word_in(raw: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(raw) + r"(?!\w)", text, re.IGNORECASE) is not None


@pytest.mark.ac("AC-01.19")
def test_task_text_free_of_due_phrase_and_introducer(snapshot):
    _, parsed = snapshot
    checked = 0
    for pf in parsed.values():
        for it in pf.items:
            d = it.due_hook
            tt = it.task_text
            assert tt and tt[0].isupper() or not tt[0].isalpha(), tt
            assert not re.search(r"[\s,:\-–—]$", tt), (pf.rel_path, it.line_start, tt)
            for raw in [d.due_raw, d.due_raw_other]:
                if raw:
                    checked += 1
                    for part in raw.split("; "):
                        assert not _word_in(part, tt), (pf.rel_path, it.line_start, part, tt)
            if d.due_raw:
                assert not re.search(r"\b(by|due|on|for|before|until|till)\s*$", tt, re.IGNORECASE), tt
    assert checked >= 75
    items = {(rel, it.line_start): it for rel, pf in parsed.items() for it in pf.items}
    for key in (("2026-09-29-billing-cutover-planning.md", 12), ("2026-10-08-billing-go-no-go.md", 11)):
        tt = items[key].task_text
        assert "9 Oct" not in tt and "from" not in tt.lower().split()[-1:], tt
        assert tt == "Karan to freeze invoice edits", tt
    assert items[("2026-09-01-quote-portal-kickoff.md", 9)].task_text == "Priya to send the revised quote to Acme"
