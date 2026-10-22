"""Table renderer and list formatters (T-M1-07, AC-09.8, AC-09.6, NFR-USE-02)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from actiontrack.report.table import (
    LIST_COLUMNS,
    Column,
    display_width,
    fmt_due,
    fmt_flags,
    fmt_item_id,
    render_table,
)

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "table_basic.txt"

COLS = [Column("NAME", min_width=4), Column("TASK", min_width=4, truncatable=True), Column("X")]
LONG_COLS = [Column("ID", 7), Column("TASK", 37, truncatable=True), Column("SOURCE", 30, truncatable=True)]


@pytest.mark.ac("AC-09.8")
def test_escape_sequence_not_emitted_raw():
    out = render_table(COLS, [["a", "Fix \x1b[31mred\x1b[0m \x9b \x7f", "x"]])
    assert "\x1b" not in out and "\x9b" not in out and "\x7f" not in out


def test_newline_in_cell_becomes_space():
    out = render_table(COLS, [["a", "one\ntwo\r\nthree", "x"]])
    assert out.count("\n") == 1
    assert "one two three" in out


@pytest.mark.ac("AC-09.6")
def test_unicode_preserved_and_aligned():
    rows = [["Zoë Åström", "t1", "a"], ["李雷", "t2", "b"], ["😀 Ana", "t3", "c"], ["é", "t4", "d"]]
    out = render_table(COLS, rows)
    for name in ("Zoë Åström", "李雷", "😀 Ana"):
        assert name in out
    lines = out.split("\n")
    starts = {display_width(ln[: ln.index(t)]) for ln, t in zip(lines[1:], ("t1", "t2", "t3", "t4"))}
    assert len(starts) == 1
    assert display_width("李雷") == 4 and display_width("é") == 1


def test_truncation_at_120_columns():
    out = render_table(LONG_COLS, [["AT-0001", "x" * 200, "y" * 40]], max_width=120)
    for line in out.split("\n"):
        assert display_width(line) <= 120
    assert "..." in out.split("\n")[1]
    assert "x" * 200 not in out


def test_truncation_shrinks_task_before_source():
    out = render_table(LONG_COLS, [["AT-0001", "x" * 60, "y" * 30]], max_width=100)
    assert "y" * 30 in out and "x" * 60 not in out


def test_wide_flag_no_truncation():
    out = render_table(LONG_COLS, [["AT-0001", "x" * 200, "y" * 40]], max_width=120, wide=True)
    assert "x" * 200 in out and "..." not in out


def test_empty_rows_header_only():
    assert render_table(COLS, []) == "NAME  TASK  X"


def test_row_length_mismatch_rejected():
    with pytest.raises(ValueError):
        render_table(COLS, [["a", "b"]])


def test_render_is_deterministic():
    rows = [["Zoë", "t " * 80, "s"], ["李雷", "u", "v"]]
    assert render_table(COLS, rows) == render_table(COLS, rows)


def test_fmt_due_variants():
    assert fmt_due(date(2026, 10, 23), "Fri", False) == "2026-10-23"
    assert fmt_due(None, "next wk", True) == "?(next wk)"
    assert fmt_due(None, None, True) == "-"
    assert fmt_due(None, None, False) == "-"


def test_fmt_flags_order():
    assert fmt_flags({"CI", "DU", "CR", "D?", "SM", "OU", "OA"}) == "CR D? OA OU DU SM CI"
    assert fmt_flags({"DU", "OA"}) == "OA DU"
    assert fmt_flags(set()) == ""
    with pytest.raises(ValueError):
        fmt_flags({"ZZ"})


def test_fmt_item_id():
    assert fmt_item_id(4) == "AT-0004" and fmt_item_id(12345) == "AT-12345"


def test_golden_table_basic():
    rows = [
        [fmt_item_id(4), "overdue", fmt_due(date(2026, 10, 16), None, False), "Priya Shah",
         "Send the revised quote", "2026-10-14-sprint.md:18", fmt_flags(set())],
        [fmt_item_id(9), "open", fmt_due(date(2026, 10, 23), None, False), "Rahul Verma",
         "Complete the API review", "2026-10-14-sprint.md:21 (+1)", fmt_flags({"CR"})],
        [fmt_item_id(6), "open", fmt_due(None, "next wk", True), "?Priya (Nair | Shah)",
         "Draft onboarding doc", "2026-10-14-sprint.md:24", fmt_flags({"OA", "DU"})],
    ]
    expected = GOLDEN.read_bytes().decode("utf-8")
    assert "\r" not in expected
    # The spec 4.3 sample is 130 columns wide (its padding exceeds the 120 limit of
    # NFR-USE-02), so the golden is rendered with max_width=130; at 120 the TASK padding
    # shrinks (see test_golden_table_basic_at_120).
    assert render_table(LIST_COLUMNS, rows, max_width=130) + "\n" == expected


def test_golden_table_basic_at_120():
    rows = [["AT-0004", "overdue", "2026-10-16", "Priya Shah", "Send the revised quote",
             "2026-10-14-sprint.md:18", ""]]
    for line in render_table(LIST_COLUMNS, rows).split("\n"):
        assert display_width(line) <= 120
    assert "Send the revised quote" in render_table(LIST_COLUMNS, rows)
