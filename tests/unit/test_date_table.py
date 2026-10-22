"""T-M3-10: table-driven date-resolution fixture (spec 9.3-9.5, AC-06.1/2/3/7/8/9/10/11).

``tests/fixtures/date_table.json`` is hand-written from the spec (not generated from the code) and
every expected date was re-verified against the real 2026 calendar. Each row has an ``entry``:

* ``phrase`` - ``resolve_text(text, M, date_order)`` (= ``classify_phrase`` + ``resolve_phrase``).
* ``item``   - a whole task description: ``locate`` -> ``distinct_due_phrases`` / ``not_due_phrases`` ->
  ``resolve_phrase`` (``event=True`` for an ``event`` role). ``evaluate_item`` below is the stand-in
  for ``resolve_due`` (T-M3-09): when that lands, switch ``evaluate_item`` to call it (rows unchanged).

Results are pure functions of (text, meeting date, date_order): BR-05 is guarded by a source scan,
a run with every wall-clock function patched to raise, and a subprocess run under two TZ values.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.domain import dates as dates_mod
from actiontrack.domain.dates import (
    distinct_due_phrases,
    locate,
    not_due_phrases,
    resolve_phrase,
    resolve_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TABLE = REPO_ROOT / "tests" / "fixtures" / "date_table.json"
ROWS: list[dict] = json.loads(TABLE.read_text(encoding="utf-8"))["rows"]


def _iso(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _val(x):
    return None if x is None else getattr(x, "value", x)


def evaluate_item(text: str, m: date | None, order: str | None) -> dict:
    """Stand-in for ``resolve_due`` over a full description (see module docstring)."""
    located = locate(text, order)
    distinct = distinct_due_phrases(located)
    extra = [codes.W_DATE_NOT_DUE] if not_due_phrases(located) else []
    if len(distinct) >= 2:
        return {"date": None, "confidence": "unresolved", "rule": None, "reason": "multiple_dates",
                "warnings": sorted(extra)}
    if not distinct:
        return {"date": None, "confidence": "none", "rule": None, "reason": None, "warnings": sorted(extra)}
    loc = distinct[0]
    res = resolve_phrase(loc.phrase, m, order, event=(loc.role == "event"))
    return {"date": res.date, "confidence": _val(res.confidence), "rule": _val(res.rule),
            "reason": _val(res.reason), "warnings": sorted(list(res.warnings) + extra)}


def evaluate(row: dict) -> dict:
    m, order = _iso(row["meeting_date"]), row["date_order"]
    if row["entry"] == "item":
        return evaluate_item(row["text"], m, order)
    res = resolve_text(row["text"], m, order)
    assert res is not None, f"{row['id']}: not recognised as a date phrase"
    return {"date": res.date, "confidence": _val(res.confidence), "rule": _val(res.rule),
            "reason": _val(res.reason), "warnings": sorted(res.warnings)}


def expected(row: dict) -> dict:
    return {"date": _iso(row["expected_date"]), "confidence": row["expected_confidence"],
            "rule": row["expected_rule"], "reason": row["expected_reason"],
            "warnings": sorted(row["expected_warnings"])}


def _params():
    for row in ROWS:
        marks = []
        if row["ac"]:
            marks.append(pytest.mark.ac(*row["ac"]))
        if row["sc"]:
            marks.append(pytest.mark.sc(*row["sc"]))
        yield pytest.param(row, id=row["id"], marks=marks)


# --------------------------------------------------------------------------- the table
@pytest.mark.ac("AC-06.1", "AC-06.2", "AC-06.3", "AC-06.7", "AC-06.8", "AC-06.9", "AC-06.10", "AC-06.11")
@pytest.mark.sc("SC-10", "SC-11", "SC-12", "SC-13", "SC-35", "SC-63", "SC-70")
@pytest.mark.parametrize("row", list(_params()))
def test_table_row(row):
    got, want = evaluate(row), expected(row)
    assert got == want, f"{row['id']} [{row['source']}] M={row['meeting_date']} text={row['text']!r} order={row['date_order']}"
    # AC-06.6 shape invariants
    if want["confidence"] == "unresolved":
        assert got["date"] is None and got["rule"] is None and got["reason"] in codes.DUE_REASONS
    elif want["date"] is not None:
        assert got["rule"] in codes.DUE_RULES and got["reason"] is None
    # no year-bearing date is ever inferred (G-07)
    if got["rule"] in ("iso_date", "text_date", "numeric_date"):
        assert got["confidence"] == "explicit"


# --------------------------------------------------------------------------- fixture guards
def _count(group: str, pred=lambda r: True) -> int:
    return sum(1 for r in ROWS if r["group"] == group and pred(r))


def test_row_ids_unique_and_wellformed():
    ids = [r["id"] for r in ROWS]
    assert len(ids) == len(set(ids))
    for r in ROWS:
        assert r["entry"] in ("phrase", "item")
        assert r["expected_confidence"] in ("explicit", "relative", "inferred", "unresolved", "none")
        assert (r["expected_date"] is None) == (r["expected_confidence"] in ("unresolved", "none"))
        assert (r["expected_reason"] is None) == (r["expected_confidence"] != "unresolved")
        assert r["expected_rule"] is None or r["expected_rule"] in codes.DUE_RULES
        assert r["expected_reason"] is None or r["expected_reason"] in codes.DUE_REASONS
        for w in r["ac"]:
            assert re.fullmatch(r"AC-\d{2}\.\d+", w)


def test_clause_row_counts_cover_the_spec():
    assert ROWS and len(ROWS) >= 91
    assert _count("s93") >= 23  # section 9.3 table incl. alternates
    assert _count("further") == 13  # M=Fri 3, M=Sat 3, M=10-31 1, year-less 6
    assert _count("ac06_7") == 20
    assert _count("ac06_8") >= 8
    assert _count("ac06_9") >= 6
    ac10 = [r for r in ROWS if r["group"] == "ac06_10"]
    assert sum(1 for r in ac10 if r["expected_confidence"] == "explicit") >= 4
    assert sum(1 for r in ac10 if r["expected_confidence"] == "inferred") >= 2
    assert sum(1 for r in ac10 if r["expected_reason"] == "range") >= 4
    assert sum(1 for r in ac10 if r["expected_reason"] == "vague") >= 5
    assert _count("ac06_11") >= 6
    assert _count("ac06_2") >= 3 and _count("ac06_3") >= 3


def test_every_due_rule_and_reason_is_covered():
    rules = {r["expected_rule"] for r in ROWS if r["expected_rule"]}
    reasons = {r["expected_reason"] for r in ROWS if r["expected_reason"]}
    assert rules == set(codes.DUE_RULES), set(codes.DUE_RULES) ^ rules
    assert reasons == set(codes.DUE_REASONS), set(codes.DUE_REASONS) ^ reasons
    assert any(codes.W_DUE_BEFORE_MEETING in r["expected_warnings"] for r in ROWS)
    assert any(codes.W_DATE_NOT_DUE in r["expected_warnings"] for r in ROWS)


@pytest.mark.ac("AC-06.8", "AC-06.9")
def test_boundary_dates_in_fixture_really_are_60_and_61_days():
    by_id = {r["id"]: r for r in ROWS}
    m = date(2026, 10, 14)
    assert (m - date(2026, 8, 15)).days == 60 and by_id["ac06_8.boundary-60-days"]["expected_date"] == "2026-08-15"
    assert (m - date(2026, 8, 14)).days == 61 and by_id["ac06_8.boundary-61-days"]["expected_date"] == "2027-08-14"


# --------------------------------------------------------------------------- BR-05 (no clock)
_FORBIDDEN = re.compile(r"\b(?:date\.today|datetime\.now|datetime\.utcnow|\.today\(|\.now\(|time\.time|time\.localtime"
                        r"|time\.monotonic|import\s+time\b|from\s+time\s+import|zoneinfo|\btzinfo\b)")


def test_resolver_source_never_touches_a_clock():
    src = Path(dates_mod.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)  # docstrings may mention the rule
    assert not _FORBIDDEN.search(code), _FORBIDDEN.search(code)


def test_table_unaffected_when_every_wall_clock_function_raises(monkeypatch):
    baseline = [evaluate(r) for r in ROWS]

    def boom(*_a, **_k):
        raise AssertionError("resolver consulted the clock (BR-05)")

    for name in ("time", "localtime", "gmtime", "monotonic", "perf_counter", "time_ns"):
        monkeypatch.setattr(time, name, boom)
    assert [evaluate(r) for r in ROWS] == baseline


_SUBPROC = (
    "import json,sys;sys.path.insert(0,{root!r});sys.path.insert(0,{src!r});"
    "from tests.unit import test_date_table as t;"
    "print(json.dumps([[str(x['date']),x['confidence'],x['rule'],x['reason'],x['warnings']] "
    "for x in map(t.evaluate,t.ROWS)]))"
)


@pytest.mark.parametrize("tz", ["UTC", "Pacific/Kiritimati", "America/Los_Angeles"])
def test_results_identical_under_different_process_time_zones(tz):
    env = {**os.environ, "TZ": tz, "PYTHONHASHSEED": "0"}
    for k in list(env):
        if k.startswith("ACTIONTRACK_"):
            del env[k]
    code = _SUBPROC.format(root=str(REPO_ROOT), src=str(REPO_ROOT / "src"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=str(REPO_ROOT), timeout=120)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    want = [[str(x["date"]), x["confidence"], x["rule"], x["reason"], x["warnings"]]
            for x in map(evaluate, ROWS)]
    assert got == want
