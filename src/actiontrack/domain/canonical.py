"""Canonical field selection for multi-source items (T-M5-22; spec 10.6.1 last two rows, 10.7.3, BR-09, G-05).

Pure: no I/O, no clock, no DB. This is the ONLY place that decides which source drives which canonical
field of an action item (``services/ingest.py::recompute_canonical`` just applies the result).

Rules (spec 10.6.1)
-------------------
* One ascending ordering key over sources: ``(meeting date, null LAST; folder-relative path in ordinal
  code-point order; line_start; source id)``. *Earliest* = first, *latest* = last, so a source with an
  unknown meeting date sorts as the latest (default adopted for the unstated null case, gap G-C25).
* ``description``, ``task_text`` and the core key follow the EARLIEST present source.
* ``due_*`` (``due_raw``, ``due_raw_other``, ``due_date``, ``due_confidence``, ``due_rule``, ``due_reason``)
  and the owner list follow the LATEST present source; ``note_status`` is that source's checkbox
  (``[x]`` -> ``done``). ``note_status`` is informational: the item's stored ``status`` is NOT rewritten
  from it here (manual done is sticky, BR-09; a Tier-1 / Tier-1b completion and the reopen rule belong to
  T-M5-08 / T-M5-10 / T-M5-11, G-C25).
* A source with ``present=False`` is ignored while any present source exists; when none is present the
  same rules apply over all sources.

Public API
----------
``OwnerView``                 one owner phrase (raw text, role, person id, status, provenance, candidates).
``SourceView``                one item source with its meeting date and file path (plain frozen data).
``source_order_key(source)``  the ascending key above.
``source_pool(sources)``      the sources that count (present ones, else all).
``earliest_source(sources)`` / ``latest_source(sources)``
``CanonicalFields``           result dataclass (see below).
``select_canonical(sources) -> CanonicalFields``   raises ``ValueError`` for no sources.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

__all__ = [
    "CanonicalFields",
    "OwnerView",
    "SourceView",
    "earliest_source",
    "latest_source",
    "select_canonical",
    "source_order_key",
    "source_pool",
]


@dataclass(frozen=True, slots=True)
class OwnerView:
    """One owner phrase of a source or of an item (``status`` is the ``owner_status`` string)."""

    owner_raw: str | None = None
    role: str = "owner"
    person_id: str | None = None
    status: str = "unassigned"
    provenance: str = "none"
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceView:
    """One ``item_source`` row joined with its meeting date and file path."""

    source_id: int
    rel_path: str
    line_start: int
    meeting_date: date | None
    source_file_id: int = 0
    present: bool = True
    description: str = ""
    task_text: str = ""
    core_key: str = ""
    checkbox_state: str = "open"
    due_raw: str | None = None
    due_raw_other: str | None = None
    due_date: date | None = None
    due_confidence: str = "none"
    due_rule: str | None = None
    due_reason: str | None = None
    owners: tuple[OwnerView, ...] = ()


def source_order_key(s: SourceView) -> tuple[bool, date, str, int, int]:
    """Ascending order: dated before undated, then date, path (ordinal), line, id."""
    return (s.meeting_date is None, s.meeting_date or date.min, s.rel_path, s.line_start, s.source_id)


def source_pool(sources: Sequence[SourceView]) -> list[SourceView]:
    """The sources that count: the present ones, or all of them when none is present."""
    present = [s for s in sources if s.present]
    return present or list(sources)


def earliest_source(sources: Sequence[SourceView]) -> SourceView:
    pool = source_pool(sources)
    if not pool:
        raise ValueError("an item needs at least one source")
    return min(pool, key=source_order_key)


def latest_source(sources: Sequence[SourceView]) -> SourceView:
    pool = source_pool(sources)
    if not pool:
        raise ValueError("an item needs at least one source")
    return max(pool, key=source_order_key)


@dataclass(frozen=True, slots=True)
class CanonicalFields:
    """The canonical (item-level) fields derived from the sources, plus which sources drove them."""

    description: str
    task_text: str
    core_key: str
    due_raw: str | None
    due_raw_other: str | None
    due_date: date | None
    due_confidence: str
    due_rule: str | None
    due_reason: str | None
    owners: tuple[OwnerView, ...]
    note_status: str  # "open" | "done": checkbox of the latest present source
    description_source_id: int
    driver_source_id: int

    def item_columns(self) -> dict[str, object]:
        """The item business columns this selection owns (never ``status``)."""
        return {
            "description": self.description,
            "task_text": self.task_text,
            "description_core_key": self.core_key,
            **self.due_columns(),
        }

    def description_columns(self) -> dict[str, object]:
        return {
            "description": self.description,
            "task_text": self.task_text,
            "description_core_key": self.core_key,
        }

    def due_columns(self) -> dict[str, object]:
        return {
            "due_raw": self.due_raw,
            "due_raw_other": self.due_raw_other,
            "due_date": self.due_date,
            "due_confidence": self.due_confidence,
            "due_rule": self.due_rule,
            "due_reason": self.due_reason,
        }


def select_canonical(sources: Sequence[SourceView]) -> CanonicalFields:
    """Canonical fields of an item from its sources (module docstring has the rules)."""
    first = earliest_source(sources)
    last = latest_source(sources)
    return CanonicalFields(
        description=first.description,
        task_text=first.task_text or first.description,
        core_key=first.core_key,
        due_raw=last.due_raw,
        due_raw_other=last.due_raw_other,
        due_date=last.due_date,
        due_confidence=last.due_confidence,
        due_rule=last.due_rule,
        due_reason=last.due_reason,
        owners=last.owners,
        note_status="done" if last.checkbox_state == "done" else "open",
        description_source_id=first.source_id,
        driver_source_id=last.source_id,
    )
