"""Deterministic JSON renderer (T-M1-08)."""

from __future__ import annotations

import enum
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from actiontrack.report.json import envelope, render_json

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "json_basic.json"


class Color(enum.Enum):
    RED = "red"


def _sample():
    return envelope(
        date(2026, 10, 22),
        "Asia/Kolkata",
        [
            {
                "id": "AT-0004",
                "owner": "Zoë Åström",
                "task": "李雷 sends the quote",
                "due_date": date(2026, 10, 16),
                "completed_at": datetime(2026, 10, 22, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
                "status": Color.RED,
                "days_overdue": 6,
                "flags": [],
                "note": None,
                "sources": [{"path": "a.md", "line": 18}],
            }
        ],
    )


@pytest.mark.ac("AC-09.8")
def test_json_escapes_c0_c1_and_del():
    out = render_json({"t": "\x1b[31m \x9b \x7f  "})
    for raw in ("\x1b", "\x9b", "\x7f", " "):
        assert raw not in out
    assert "\\u001b" in out and "\\u009b" in out and "\\u007f" in out and "\\u2028" in out
    assert json.loads(out)["t"] == "\x1b[31m \x9b \x7f  "


@pytest.mark.ac("AC-09.6")
def test_json_unicode_preserved():
    out = render_json({"a": "Zoë Åström", "b": "李雷"})
    assert "Zoë Åström" in out and "李雷" in out


def test_json_lone_surrogate_escaped_not_crashing():
    out = render_json({"a": "x\udc80"})
    out.encode("utf-8")
    assert "\\udc80" in out


def test_json_rejects_set_and_unordered():
    with pytest.raises(TypeError):
        render_json({"a": {1, 2}})
    with pytest.raises(TypeError):
        render_json({"a": frozenset()})
    with pytest.raises(TypeError):
        render_json({1: "x"})
    with pytest.raises(TypeError):
        render_json(datetime(2026, 1, 1))
    with pytest.raises(TypeError):
        render_json(float("nan"))


def test_json_preserves_insertion_order_no_sort():
    assert render_json({"b": 1, "a": 2}) == '{\n  "b": 1,\n  "a": 2\n}\n'


@pytest.mark.ac("AC-09.4")
def test_json_byte_identical_two_calls_and_golden():
    a, b = render_json(_sample()), render_json(_sample())
    assert a == b
    assert a.endswith("}\n") and "\r" not in a
    assert a == GOLDEN.read_bytes().decode("utf-8")


def test_json_datetime_z_format():
    ist = timezone(timedelta(hours=5, minutes=30))
    assert render_json(datetime(2026, 10, 22, 5, 30, tzinfo=ist)) == '"2026-10-22T00:00:00Z"\n'
    frac = datetime(2026, 10, 22, 1, 2, 3, 500000, tzinfo=timezone.utc)
    assert render_json(frac) == '"2026-10-22T01:02:03.500000Z"\n'
    assert render_json(date(2026, 10, 22)) == '"2026-10-22"\n'


def test_envelope_helper_shape_and_empty_items():
    env = envelope("2026-10-22", "UTC", [])
    assert list(env) == ["as_of", "timezone", "items"]
    assert render_json(env) == '{\n  "as_of": "2026-10-22",\n  "timezone": "UTC",\n  "items": []\n}\n'
