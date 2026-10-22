"""Deterministic plain-text table renderer and list-cell formatters (T-M1-07).

Pure: nothing here writes to a stream; callers pass the returned string to ``io.emit``.

Public API
----------
``Column(name, min_width=0, max_width=None, truncatable=False, align="left")``
``render_table(columns, rows, *, max_width=120, wide=False) -> str``
    Header line then one line per row, joined by ``\\n`` (no trailing newline). An empty
    ``rows`` yields the header only (the caller prints "No action items match.").
    Layout rules (fixed, golden-tested):
      * columns are separated by exactly two spaces;
      * every column except the LAST is padded to its width; the last cell is not padded
        (so a row with an empty last cell ends in the two-space gap, as in the spec 4.3
        sample, and the header ends in ``FLAGS`` with no padding);
      * width = max(min_width, header, widest cell); a column's ``max_width`` caps it
        unless ``wide``;
      * if not ``wide`` and the table is wider than ``max_width``, truncatable columns
        shrink in declaration order (TASK first, then SOURCE) down to
        ``max(len(name), 8)`` each; truncated cells end with ``...`` (three ASCII dots);
      * ``wide=True`` never truncates.
    Every cell goes through ``sanitize.clean_text`` (SEC-08).
    Display width: East Asian W/F = 2 columns; combining marks / format characters = 0.
``display_width(s) -> int``
``LIST_COLUMNS``                 the ``list`` columns ID STATUS DUE OWNER TASK SOURCE FLAGS (spec 4.3).
``fmt_due(due, raw, unresolved)``  ``2026-10-23`` | ``?(next wk)`` | ``-``  (spec 9.5)
``fmt_flags(flags)``             fixed order ``CR D? OA OU DU SM CI`` (spec 6.4)
``fmt_item_id(n)``               ``AT-0012``
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from actiontrack.report.sanitize import clean_text

ELLIPSIS = "..."
GAP = "  "
_TRUNC_FLOOR = 8


@dataclass(frozen=True)
class Column:
    name: str
    min_width: int = 0
    max_width: int | None = None
    truncatable: bool = False
    align: str = "left"  # "left" | "right"


def _char_width(ch: str) -> int:
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(s: str) -> int:
    return sum(_char_width(ch) for ch in s)


def _truncate(s: str, width: int) -> str:
    if display_width(s) <= width:
        return s
    budget = max(width - len(ELLIPSIS), 0)
    out: list[str] = []
    used = 0
    for ch in s:
        w = _char_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out) + ELLIPSIS


def _pad(s: str, width: int, align: str) -> str:
    fill = " " * max(width - display_width(s), 0)
    return fill + s if align == "right" else s + fill


def render_table(
    columns: Sequence[Column],
    rows: Iterable[Sequence[object]],
    *,
    max_width: int = 120,
    wide: bool = False,
) -> str:
    ncol = len(columns)
    cells: list[list[str]] = []
    for row in rows:
        if len(row) != ncol:
            raise ValueError(f"row has {len(row)} cells, expected {ncol}")
        cells.append([clean_text(str(c)) for c in row])

    widths: list[int] = []
    for i, col in enumerate(columns):
        w = max([col.min_width, display_width(col.name)] + [display_width(r[i]) for r in cells])
        if not wide and col.max_width is not None:
            w = min(w, max(col.max_width, display_width(col.name)))
        widths.append(w)

    if not wide:
        excess = sum(widths) + len(GAP) * (ncol - 1) - max_width
        for i, col in enumerate(columns):
            if excess <= 0:
                break
            if not col.truncatable:
                continue
            floor = max(display_width(col.name), _TRUNC_FLOOR)
            shrink = min(excess, max(widths[i] - floor, 0))
            widths[i] -= shrink
            excess -= shrink

    def line(values: Sequence[str]) -> str:
        parts = []
        for i, (v, col) in enumerate(zip(values, columns)):
            v = _truncate(v, widths[i])
            last_left = i == ncol - 1 and col.align == "left"
            parts.append(v if last_left else _pad(v, widths[i], col.align))
        return GAP.join(parts)

    return "\n".join([line([c.name for c in columns])] + [line(r) for r in cells])


LIST_COLUMNS: tuple[Column, ...] = (
    Column("ID", min_width=7),
    Column("STATUS", min_width=7),
    Column("DUE", min_width=10),
    Column("OWNER", min_width=22),
    Column("TASK", min_width=37, truncatable=True),
    Column("SOURCE", min_width=30, truncatable=True),
    Column("FLAGS", min_width=5),
)

FLAG_ORDER: tuple[str, ...] = ("CR", "D?", "OA", "OU", "DU", "SM", "CI")


def fmt_due(due: date | None, raw: str | None, unresolved: bool) -> str:
    """``2026-10-23`` resolved; ``?(raw)`` unresolved with a phrase; ``-`` otherwise."""
    if unresolved:
        return f"?({raw})" if raw else "-"
    return due.isoformat() if due is not None else "-"


def fmt_flags(flags: Iterable[str]) -> str:
    present = set(flags)
    unknown = present - set(FLAG_ORDER)
    if unknown:
        raise ValueError(f"unknown flag(s): {sorted(unknown)}")
    return " ".join(f for f in FLAG_ORDER if f in present)


def fmt_item_id(n: int) -> str:
    return f"AT-{n:04d}"
