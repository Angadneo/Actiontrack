"""Renderers for ``actiontrack list`` (T-M6-07 table, T-M6-08 JSON). Pure: return strings for ``io.emit``.

Both renderers consume the same ``services.listing.ListRow`` so they cannot diverge.

Table: header ``As of <date> (tz <zone>) — <n> item(s)``, then the ``LIST_COLUMNS`` table
(``ID STATUS DUE OWNER TASK SOURCE FLAGS``; no days-overdue column), 120-column budget unless ``--wide``;
an empty result prints the header and ``No action items match.``. ``--ambiguous`` results get one heading
line (``Priya Nair | Priya Shah``) per candidate group; the columns stay aligned across groups.
JSON: the envelope ``{as_of, timezone, items[]}``; each item carries every stored field, all sources,
flags, links and ``days_overdue`` ONLY for overdue items (the key is absent otherwise).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from actiontrack.report.json import envelope, render_json
from actiontrack.report.table import GAP, LIST_COLUMNS, display_width, fmt_flags, fmt_item_id, render_table
from actiontrack.services.listing import ListResult, ListRow

EMPTY_MESSAGE = "No action items match."


def header_line(result: ListResult, tz_label: str) -> str:
    n = len(result.rows)
    return f"As of {result.today.isoformat()} (tz {tz_label}) — {n} item{'' if n == 1 else 's'}"


def _cells(row: ListRow) -> list[str]:
    return [
        fmt_item_id(row.item_id),
        row.display_status,
        row.due_display,
        row.owner_display,
        row.task,
        row.source_display,
        fmt_flags(row.flags),
    ]


TASK_MIN_WIDTH = 20
MAX_WIDTH = 120


def _columns(cells: list[list[str]]) -> tuple:
    """LIST_COLUMNS with a SOURCE cap so that TASK keeps >= 20 columns before SOURCE is cut (T-M6-07)."""
    widths = [max([c.min_width, display_width(c.name)] + [display_width(r[i]) for r in cells]) for i, c in enumerate(LIST_COLUMNS)]
    task_i, src_i = 4, 5
    fixed = sum(w for i, w in enumerate(widths) if i not in (task_i, src_i)) + len(GAP) * (len(widths) - 1)
    remaining = MAX_WIDTH - fixed
    task_nat = max([display_width(r[task_i]) for r in cells] + [display_width("TASK")])
    src_nat = max([display_width(r[src_i]) for r in cells] + [display_width("SOURCE")])
    task_target = min(task_nat, remaining - src_nat)
    src_cap = src_nat
    if task_target < TASK_MIN_WIDTH:
        task_target = min(task_nat, TASK_MIN_WIDTH)
        src_cap = max(remaining - task_target, display_width("SOURCE"))
    cols = list(LIST_COLUMNS)
    cols[src_i] = replace(cols[src_i], max_width=src_cap, min_width=0)
    cols[task_i] = replace(cols[task_i], min_width=0)
    return tuple(cols)


def render_list_table(result: ListResult, tz_label: str, *, wide: bool = False) -> str:
    head = header_line(result, tz_label)
    flat = [r for g in result.groups for r in g.rows]
    if not flat:
        return f"{head}\n{EMPTY_MESSAGE}"
    cells = [_cells(r) for r in flat]
    lines = render_table(_columns(cells), cells, wide=wide).split("\n")
    out = [head, lines[0]]
    pos = 1
    for g in result.groups:
        if g.heading is not None:
            out.append(g.heading)
        out.extend(lines[pos : pos + len(g.rows)])
        pos += len(g.rows)
    return "\n".join(out)


def item_json(row: ListRow) -> dict[str, Any]:
    it = row.item
    d: dict[str, Any] = {
        "id": it.id,
        "status": it.status,
        "display_status": row.display_status,
        "completion_origin": it.completion_origin,
        "completed_at": it.completed_at,
        "due_raw": it.due_raw,
        "due_raw_other": it.due_raw_other,
        "due_date": it.due_date,
        "due_confidence": it.due_confidence,
        "due_rule": it.due_rule,
        "due_reason": it.due_reason,
        "description": it.description,
        "task_text": it.task_text,
        "source_state": it.source_state,
        "owners": [
            {
                "ordinal": o.ordinal,
                "owner_raw": o.owner_raw,
                "role": o.role,
                "person_id": o.person_id,
                "display_name": o.display_name,
                "owner_status": o.owner_status,
                "provenance": o.provenance,
                "candidates": [{"person_id": pid, "full_name": name} for pid, name in o.candidates],
            }
            for o in row.owners
        ],
        "sources": [
            {
                "rel_path": s.rel_path,
                "line_start": s.line_start,
                "line_end": s.line_end,
                "raw_text": s.raw_text,
                "section_heading": s.section_heading,
                "checkbox_state": s.checkbox_state,
                "present": s.present,
            }
            for s in row.sources
        ],
        "flags": list(row.flags),
        "links": [
            {"kind": lk.kind, "from_id": lk.from_id, "to_id": lk.to_id, "reason": lk.reason}
            for lk in row.links
        ],
        "created_at": it.created_at,
        "updated_at": it.updated_at,
    }
    if row.days_overdue is not None:
        d["days_overdue"] = row.days_overdue
    return d


def render_list_json(result: ListResult, tz_label: str) -> str:
    return render_json(envelope(result.today, tz_label, [item_json(r) for r in result.rows]))
