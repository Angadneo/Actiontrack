"""Renderers of the ingest report: human table and JSON (T-M5-16; spec 4.2, FR-01.7, AC-01.7, AC-09.4, SEC-08).

Pure: every function takes an ``IngestReport`` (``domain.models``, built by ``services.ingest.build_ingest_report``)
and returns a string; nothing is written to a stream here (the CLI passes the result to ``io.emit``).

Public API
----------
``render_ingest_table(report, *, verbose=False) -> str``
    The spec 4.2 layout (no trailing newline), at most ``MAX_WIDTH`` (120) display columns per line::

        Ingest run #7  folder=C:\\notes  as-of 2026-10-22
        Files: discovered 34 | ingested 31 | unchanged 0 | empty 1 | failed 2
        Items: new 58 | updated 0 | unchanged 0 | merged-duplicates 6 | possible-duplicates 4 | already-done-in-note 9
        (Files and Items count this run only; the lists below show OPEN items in the current database.)
        resolved via attendance: 33 (current state, all items)
        Unresolved owners (7): ...         <- the five sections always print, an empty one prints ``  none``
        Missing/unresolved due dates (12): ...
        Possible duplicates (4): ...
        Parsing failures (2): ...
        Warnings (n): see --verbose        <- ``--verbose``: the warnings follow, one per line, whole
        Exit code 5 (2 files failed)

    Optional lines, printed only when they apply: dry-run / interrupted-run notices and the ``--no-recursive``
    hint (AC-01.13) under the header, a files notice (missing files, skipped symlinks), the run-finalisation
    failure, an ``Owner re-resolution`` section (changes made by the whole-database pass of THIS run), and, even
    without ``--verbose``, the ``W-OWNER-NOW-AMBIGUOUS`` lines that carry the H-05 note (AC-05.19, G-C23).
    Counts in headings are STATE counts (open items), the Files / Items lines are RUN counts. Free text taken
    from notes goes through ``sanitize.clean_text`` (SEC-08: no ESC or other control character survives) and is
    truncated with ``...`` to fit the width; paths use ``/``.
``ingest_report_dict(report) -> dict`` / ``render_ingest_json(report) -> str``
    ``--format json``: ``as_of`` and ``timezone`` first (the G-19 envelope style), then ``run``, ``notices``,
    ``hint``, ``files``, ``items``, ``resolved_via_attendance``, ``unresolved_owners``, ``missing_due_dates``,
    ``possible_duplicates``, ``parsing_failures``, ``warnings``, ``owner_reresolution``, ``exit_code``,
    ``exit_reason``. Every section object is ``{"count": n, ...extras, "rows": [...]}``; nothing is truncated.
    Stable key order, rows in report order, dates ``YYYY-MM-DD``, control characters escaped by ``render_json``.
``summary_line(report) -> str``
    The one-line final summary (``Diagnostics.summary``, printed even with ``--quiet``, AC-09.9).
``VOLATILE_MASK_RUN`` / ``mask_volatile_table`` / ``mask_volatile_json``
    AC-09.4: byte-identity is asserted EXCLUDING the run number and the absolute ``folder=`` value (and started/
    finished timestamps, which depend on the clock); the helpers replace exactly those fields so tests (goldens)
    can compare the rest byte for byte.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from actiontrack.domain.models import (
    DueIssueRow,
    DuplicatePairRow,
    FailureRow,
    IngestReport,
    OwnerIssueRow,
    OwnerReResolutionRow,
    WarningRow,
    format_item_id,
)
from actiontrack.report.json import render_json
from actiontrack.report.sanitize import clean_text
from actiontrack.report.table import display_width, truncate

MAX_WIDTH = 120
INDENT = "  "
VOLATILE_MASK_RUN = "#<run>"
VOLATILE_MASK_FOLDER = "<folder>"
VOLATILE_MASK_TS = "<timestamp>"



# ------------------------------------------------------------------------------------------ helpers
def _c(text: object) -> str:
    return clean_text("" if text is None else str(text))


def _fit(prefix: str, text: str) -> str:
    """``prefix + text`` truncated (display width) to ``MAX_WIDTH``."""
    room = MAX_WIDTH - display_width(prefix)
    return prefix + truncate(text, max(room, 12))


def _note_lines(notes: Sequence[str], indent: str) -> list[str]:
    """Notes (the H-05 text, AC-05.19) go on their own continuation line so that they are never truncated."""
    return [f"{indent}note: {_c(n)}" for n in notes]


def _where(loc: str | None, extra: int) -> str:
    if loc is None:
        return "-"
    return f"{loc} (+{extra})" if extra else loc


def _pad(text: str, width: int) -> str:
    return text + " " * max(width - display_width(text), 0)


def _quote(text: str | None) -> str:
    return f'"{_c(text)}"'


def _owner_text(row: OwnerIssueRow) -> str:
    """The row text WITHOUT the note suffix."""
    if row.kind == "ambiguous":
        who = " | ".join(_c(n) for n in row.candidates) or "?"
        text = f"ambiguous: {_quote(row.owner_raw)} -> {who}"
    elif row.kind == "unverified":
        text = f"unverified: {_quote(row.owner_raw)} (not in the roster or the attendee list)"
    else:  # unassigned: no owner phrase, or a non-person word such as "Everyone" (visible in the raw line)
        text = f"no owner found: {_quote(row.raw)}"
    return text


def _due_text(row: DueIssueRow) -> str:
    if row.kind == "unresolved":
        reason = f" ({_c(row.reason)})" if row.reason else ""
        return f"unresolved {_quote(row.due_raw)}{reason}"
    return "no date"


def _located(rows: Sequence[tuple[int, str, str, Sequence[str]]]) -> list[str]:
    """``(item id, location text, text, notes)`` rows aligned on the location column; a row's notes follow it."""
    width = max((display_width(_c(loc)) for _i, loc, _t, _n in rows), default=0)
    out: list[str] = []
    for i, loc, text, notes in rows:
        out.append(_fit(f"{INDENT}{format_item_id(i)}  {_pad(_c(loc), width)}  ", text))
        out += _note_lines(notes, INDENT + " " * 9)  # under the location column
    return out


def _heading(title: str, count: int) -> str:
    return f"{title} ({count}):"


def _warning_lines(w: WarningRow, *, full: bool = False) -> list[str]:
    """``full`` (``--verbose``): the whole message including its "Next:" hint, not cut to 120 columns."""
    message = _c(w.message)
    for note in w.notes:  # the message ends with " (note)": show the note on its own line instead
        tail = f" ({_c(note)})"
        if message.endswith(tail):
            message = message[: -len(tail)]
    head = f"{INDENT}{_c(w.code)}  "
    return [head + message if full else _fit(head, message), *_note_lines(w.notes, INDENT * 3)]  # under the message


def _reres_text(row: OwnerReResolutionRow) -> str:
    text = f"{_c(row.old_status)} -> {_c(row.new_status)}: {_quote(row.owner_raw)}"
    if row.new_status == "ambiguous" and row.candidates:
        text += " -> " + " | ".join(_c(n) for n in row.candidates)
    return text


# ------------------------------------------------------------------------------------------ table
def render_ingest_table(report: IngestReport, *, verbose: bool = False) -> str:
    r = report
    f, it = r.files, r.items
    lines = [f"Ingest run #{r.run_id}  folder={_c(r.folder)}  as-of {r.as_of.isoformat()}"]  # never truncated
    lines += [_c(n) for n in r.notices]
    lines.append(
        f"Files: discovered {f.discovered} | ingested {f.ingested} | unchanged {f.unchanged} "
        f"| empty {f.empty} | failed {f.failed}"
    )
    if r.hint:
        lines.append(f"Hint: {_c(r.hint)}")
    extras = []
    if f.missing:
        extras.append(f"{f.missing} stored file{'s' if f.missing != 1 else ''} not found (marked missing)")
    if f.symlinks_skipped:
        extras.append(f"{f.symlinks_skipped} symlink{'s' if f.symlinks_skipped != 1 else ''} skipped")
    if extras:
        lines.append("Note: " + "; ".join(extras))
    lines.append(
        f"Items: new {it.new} | updated {it.updated} | unchanged {it.unchanged} "
        f"| merged-duplicates {it.merged_duplicates} | possible-duplicates {it.possible_duplicates} "
        f"| already-done-in-note {it.already_done_in_note}"
    )
    lines.append("(Files and Items count this run only; the lists below show OPEN items in the current database.)")
    lines.append(f"resolved via attendance: {r.resolved_via_attendance} (current state, all items)")

    lines.append(_heading("Unresolved owners", len(r.unresolved_owners)))
    lines += _located([
            (o.item_id, _where(o.location, o.extra_sources), _owner_text(o), o.notes)
            for o in r.unresolved_owners
        ]) or [
        INDENT + "none"
    ]

    lines.append(_heading("Missing/unresolved due dates", len(r.missing_due_dates)))
    lines += _located(
        [(d.item_id, _where(d.location, d.extra_sources), _due_text(d), ()) for d in r.missing_due_dates]
    ) or [INDENT + "none"]

    lines.append(_heading("Possible duplicates", len(r.possible_duplicate_pairs)))
    pair_lines = [
        _fit(f"{INDENT}{format_item_id(p.item_id)} ~ {format_item_id(p.other_item_id)}  ", _c(p.reason_text))
        for p in r.possible_duplicate_pairs
    ]
    lines += pair_lines or [INDENT + "none"]

    lines.append(_heading("Parsing failures", len(r.failures)))
    if r.failures:
        pw = max(display_width(_c(x.path)) for x in r.failures)
        cw = max(display_width(_c(x.code)) for x in r.failures)
        for x in r.failures:
            lines.append(_fit(f"{INDENT}{_pad(_c(x.path), pw)}  {_pad(_c(x.code), cw)}  ", _c(x.message)))
    else:
        lines.append(INDENT + "none")

    if r.reresolution:
        lines.append(_heading("Owner re-resolution (this run)", len(r.reresolution)))
        lines += _located(
            [(x.item_id, _where(x.location, 0), _reres_text(x), x.notes) for x in r.reresolution]
        )

    if r.finalisation_error:
        lines.append(f"Run finalisation failed: {_c(r.finalisation_error)}")

    h05 = [w for w in r.warnings if w.notes]
    if verbose and r.warnings:
        lines.append(f"Warnings ({r.warning_count}):")
        lines += [ln for w in r.warnings for ln in _warning_lines(w, full=True)]
    elif verbose:
        lines.append(f"Warnings ({r.warning_count}): none")
    else:
        lines.append(f"Warnings ({r.warning_count}): see --verbose" if r.warning_count else "Warnings (0)")
        lines += [ln for w in h05 for ln in _warning_lines(w)]

    reason = f" ({_c(r.exit_reason)})" if r.exit_reason else ""
    lines.append(f"Exit code {r.exit_code}{reason}")
    return "\n".join(lines)


def summary_line(report: IngestReport) -> str:
    r, f, it = report, report.files, report.items
    prefix = "Dry run: " if r.dry_run else ""
    reason = f" ({r.exit_reason})" if r.exit_reason else ""
    return (
        f"{prefix}Ingest run #{r.run_id}: {f.discovered} files ({f.ingested} ingested, {f.unchanged} unchanged, "
        f"{f.empty} empty, {f.failed} failed); items: {it.new} new, {it.merged_duplicates} merged, "
        f"{it.possible_duplicates} possible duplicates; exit {r.exit_code}{reason}"
    )


# ------------------------------------------------------------------------------------------ JSON
def _owner_json(o: OwnerIssueRow) -> dict[str, Any]:
    return {
        "item_id": o.item_id,
        "id": format_item_id(o.item_id),
        "location": o.location,
        "additional_sources": o.extra_sources,
        "kind": o.kind,
        "owner_raw": o.owner_raw,
        "candidates": list(o.candidates),
        "raw": o.raw,
        "notes": list(o.notes),
    }


def _due_json(d: DueIssueRow) -> dict[str, Any]:
    return {
        "item_id": d.item_id,
        "id": format_item_id(d.item_id),
        "location": d.location,
        "additional_sources": d.extra_sources,
        "kind": d.kind,
        "due_raw": d.due_raw,
        "reason": d.reason,
        "raw": d.raw,
    }


def _pair_json(p: DuplicatePairRow) -> dict[str, Any]:
    return {
        "item_id": p.item_id,
        "id": format_item_id(p.item_id),
        "other_item_id": p.other_item_id,
        "other_id": format_item_id(p.other_item_id),
        "reason": p.reason,
        "reason_text": p.reason_text,
        "due": p.due,
        "other_due": p.other_due,
        "task_text": p.task_text,
    }


def _failure_json(x: FailureRow) -> dict[str, Any]:
    return {"path": x.path, "code": x.code, "message": x.message, "next_step": x.next_step}


def _warning_json(w: WarningRow) -> dict[str, Any]:
    return {"code": w.code, "path": w.path, "line": w.line, "message": w.message, "notes": list(w.notes)}


def _reres_json(x: OwnerReResolutionRow) -> dict[str, Any]:
    return {
        "item_id": x.item_id,
        "id": format_item_id(x.item_id),
        "location": x.location,
        "owner_raw": x.owner_raw,
        "kind": x.kind,
        "old_status": x.old_status,
        "new_status": x.new_status,
        "candidates": list(x.candidates),
        "notes": list(x.notes),
    }


def _count_by(rows: Iterable[object], attr: str, order: Sequence[str]) -> dict[str, int]:
    values = [getattr(r, attr) for r in rows]
    return {k: values.count(k) for k in order}


def ingest_report_dict(report: IngestReport) -> dict[str, Any]:
    r, f, it = report, report.files, report.items
    by_code: dict[str, int] = {}
    for w in r.warnings:
        by_code[w.code] = by_code.get(w.code, 0) + 1
    return {
        "as_of": r.as_of,
        "timezone": r.timezone,
        "run": {
            "id": r.run_id,
            "status": r.status,
            "dry_run": r.dry_run,
            "folder": r.folder,
            "recursive": r.recursive,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        },
        "notices": list(r.notices),
        "hint": r.hint,
        "files": {
            "scope": "run",
            "discovered": f.discovered,
            "ingested": f.ingested,
            "unchanged": f.unchanged,
            "empty": f.empty,
            "failed": f.failed,
            "missing": f.missing,
            "symlinks_skipped": f.symlinks_skipped,
        },
        "items": {
            "scope": "run",
            "new": it.new,
            "updated": it.updated,
            "unchanged": it.unchanged,
            "merged_duplicates": it.merged_duplicates,
            "possible_duplicates": it.possible_duplicates,
            "already_done_in_note": it.already_done_in_note,
            "sources_imported": it.sources_imported,
        },
        "resolved_via_attendance": {"scope": "state", "count": r.resolved_via_attendance},
        "unresolved_owners": {
            "scope": "state",
            "count": len(r.unresolved_owners),
            **_count_by(r.unresolved_owners, "kind", ("ambiguous", "unassigned", "unverified")),
            "rows": [_owner_json(o) for o in r.unresolved_owners],
        },
        "missing_due_dates": {
            "scope": "state",
            "count": len(r.missing_due_dates),
            **_count_by(r.missing_due_dates, "kind", ("no_date", "unresolved")),
            "rows": [_due_json(d) for d in r.missing_due_dates],
        },
        "possible_duplicates": {
            "scope": "state",
            "count": len(r.possible_duplicate_pairs),
            "rows": [_pair_json(p) for p in r.possible_duplicate_pairs],
        },
        "parsing_failures": {
            "scope": "run",
            "count": len(r.failures),
            "rows": [_failure_json(x) for x in r.failures],
        },
        "warnings": {
            "scope": "run",
            "count": r.warning_count,
            "by_code": [{"code": c, "count": by_code[c]} for c in sorted(by_code)],
            "rows": [_warning_json(w) for w in r.warnings],
        },
        "owner_reresolution": {
            "scope": "run",
            "count": len(r.reresolution),
            "rows": [_reres_json(x) for x in r.reresolution],
        },
        "finalisation_error": r.finalisation_error,
        "exit_code": r.exit_code,
        "exit_reason": r.exit_reason,
    }


def render_ingest_json(report: IngestReport) -> str:
    return render_json(ingest_report_dict(report))


# ------------------------------------------------------------------------------------------ masking
_RUN_RE = re.compile(r"Ingest run #\d+")
_FOLDER_RE = re.compile(r"folder=.*?(?=  as-of )")
_TS_RE = re.compile(r'"(started_at|finished_at)": (?:"[^"]*"|null)')
_JSON_RUN_ID_RE = re.compile(r'("run": \{\n\s+"id": )\d+')
_JSON_FOLDER_RE = re.compile(r'("folder": )"(?:[^"\\]|\\.)*"')
_NOTICE_RUN_RE = re.compile(r"#\d+ is the number")


def mask_volatile_table(text: str) -> str:
    """Replace the run number and the absolute ``folder=`` value of a table report (AC-09.4)."""
    text = _RUN_RE.sub(f"Ingest run {VOLATILE_MASK_RUN}", text)
    text = _NOTICE_RUN_RE.sub(f"{VOLATILE_MASK_RUN} is the number", text)
    return _FOLDER_RE.sub(f"folder={VOLATILE_MASK_FOLDER}", text)


def mask_volatile_json(text: str) -> str:
    """Replace the run id, the absolute folder and the run timestamps of a JSON report (AC-09.4)."""
    text = _JSON_RUN_ID_RE.sub(r'\1"<run>"', text)
    text = _JSON_FOLDER_RE.sub(r'\1"<folder>"', text)
    return _TS_RE.sub(lambda m: f'"{m.group(1)}": "{VOLATILE_MASK_TS}"', text)
