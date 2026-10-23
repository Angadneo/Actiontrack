"""Status derivation, sticky manual done and the reopen rule (T-M5-11; spec BR-09, BR-10, FR-03.7, 10.7.3, G-05).

Pure: no I/O, no clock, no DB. ``services/ingest.py::apply_status_effects`` applies the decisions.

Rules
-----
* **Manual done is sticky** (``completion_origin='manual'``): ``derive_status`` always returns it unchanged
  and ``reopens`` is always False; a contradicting ``[ ]`` only produces ``W-STATUS-CONFLICT`` (whole-database
  pass, ``conflicting_manual_done``).
* A **note-derived completion** (``completion_origin='note'``: a ``[x]`` source, a Tier-1 row-5 merge or a
  Tier-1b match) stays ``done`` until the reopen rule fires; ``completed_at`` stays NULL.
* An **open** item with no completion follows the **latest present source**: ``[x]`` -> done (origin ``note``,
  evidence = that source), ``[ ]`` -> open (G-C25 reading of 10.6.1 "note-derived status follows the latest
  present source").
* **Reopen rule** (10.7.3): a note completion is reopened ONLY by a ``[ ]`` for the same source key as the
  evidence (the same ``ItemSource`` row across edits) that is later-dated: the evidence file's meeting date
  after the change is later than the evidence note's date, or equal to it (the evidence file itself was
  edited so the line now reads ``[ ]`` instead of ``[x]``). A date edited EARLIER does not reopen (literal
  reading, G-C24); when only one of the two dates is known the dates are not comparable and nothing reopens
  (conservative). Removal of the evidence line (``present=False``) is not a ``[ ]`` and never reopens.
  Reopening an evidence holder cascades to the items completed from it (``completed_by`` links).

Public API
----------
``ItemStatus``                 status / origin / evidence source id of an item.
``derive_status(item, sources) -> StatusDecision``   status for an item given its sources (see above).
``EvidenceChange``             what the pairing step reports about one changed source row.
``reopens(item, change) -> bool``   the reopen test (``maybe_reopen``-style date test).
``conflicting_manual_done(item, sources) -> bool``   True when a manual-done item's latest present source is ``[ ]``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from actiontrack.domain.canonical import SourceView, source_order_key

__all__ = [
    "EvidenceChange",
    "ItemStatus",
    "StatusDecision",
    "conflicting_manual_done",
    "derive_status",
    "later_dated",
    "reopens",
]


@dataclass(frozen=True, slots=True)
class ItemStatus:
    """The status facts of one action item."""

    status: str  # "open" | "done"
    origin: str | None = None  # completion_origin: "note" | "manual" | None
    evidence_source_id: int | None = None  # completion_evidence_source_id

    @property
    def is_manual_done(self) -> bool:
        return self.status == "done" and self.origin == "manual"

    @property
    def is_note_done(self) -> bool:
        return self.status == "done" and self.origin != "manual"


@dataclass(frozen=True, slots=True)
class StatusDecision:
    """Result of ``derive_status``: the item's status/origin and the evidence source (None if none)."""

    status: str
    origin: str | None
    evidence_source_id: int | None

    @property
    def completed_at(self) -> None:
        """Note-origin completion never has a completion time (the date is unknown, 10.6.1)."""
        return None


def _present_latest(sources: Sequence[SourceView]) -> SourceView | None:
    present = [s for s in sources if s.present]
    return max(present, key=source_order_key) if present else None  # same ordering as canonical (T-M5-22)


def derive_status(item: ItemStatus, sources: Sequence[SourceView]) -> StatusDecision:
    """Status of an item given its sources (module docstring has the rules). It never reopens a completion:
    that is ``reopens`` (an explicit event of the evidence source), not a derivation."""
    if item.is_manual_done:
        return StatusDecision("done", "manual", item.evidence_source_id)
    if item.is_note_done:
        return StatusDecision("done", item.origin or "note", item.evidence_source_id)
    last = _present_latest(sources)
    if last is not None and last.checkbox_state == "done":
        return StatusDecision("done", "note", last.source_id)
    return StatusDecision("open", None, None)


@dataclass(frozen=True, slots=True)
class EvidenceChange:
    """One source row seen again by the pairing step (T-M5-12 (a)-(c)).

    ``source_id``          the ``ItemSource`` row (same key across edits).
    ``now_checkbox``       its checkbox now (``open`` | ``done``); ``present`` False = the line was removed.
    ``old_meeting_date``   meeting date of its file before this ingest (the evidence note's date).
    ``new_meeting_date``   meeting date of its file in the new content."""

    source_id: int
    now_checkbox: str
    old_meeting_date: date | None
    new_meeting_date: date | None
    present: bool = True


def later_dated(old: date | None, new: date | None) -> bool:
    """10.7.3 'later-dated': ``new`` is later than ``old``, or equal (the evidence file itself was edited).
    Two unknown dates count as equal; one unknown date is not comparable (False)."""
    if old is None and new is None:
        return True
    if old is None or new is None:
        return False
    return new >= old


def reopens(item: ItemStatus, change: EvidenceChange) -> bool:
    """True when ``change`` reopens the item's note-derived completion (10.7.3, AC-07.16)."""
    if not item.is_note_done or item.evidence_source_id is None:
        return False  # manual done is never reopened; an item without recorded evidence has nothing to reopen
    if change.source_id != item.evidence_source_id:
        return False  # an unrelated source (or another file) never reopens
    if not change.present or change.now_checkbox != "open":
        return False  # removal of the line is not a ``[ ]``
    return later_dated(change.old_meeting_date, change.new_meeting_date)


def conflicting_manual_done(item: ItemStatus, sources: Sequence[SourceView]) -> bool:
    """W-STATUS-CONFLICT condition (10.7.9 b): manual done whose latest present source is ``[ ]``."""
    if not item.is_manual_done:
        return False
    last = _present_latest(sources)
    return last is not None and last.checkbox_state != "done"
