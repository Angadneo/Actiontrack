"""``actiontrack done <id>``: mark one action item done (M6: T-M6-09/10; spec 4.4, FR-03.*, BR-09).

``mark_done(db, repo, item_ref, clock) -> DoneResult``
    ``item_ref`` is ``AT-0012`` / ``12`` / ``at-12`` (``UsageError`` exit 2 otherwise). Unknown id ->
    ``NotFoundError`` (exit 3, nothing written). An open item becomes ``status=done``,
    ``completion_origin=manual``, ``completed_at=updated_at=<clock UTC>`` and gets exactly one ``manual_done``
    ``item_event``, all in ONE ``BEGIN IMMEDIATE`` transaction (the caller holds the process write lock by
    opening ``db`` with ``lock=True``). An already-done item is reported and NOTHING is written, not even an
    empty transaction (FR-03.4). Description, owners, due and sources are never touched.
``DoneResult.to_json()``   the 4.4 object ``{id, status, previous_status, already_done, completion_origin,
                           completed_at}``.
``possible_duplicates_of(repo, item_id)``   notice lines data for FR-03.6 (linked items are not modified).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from actiontrack.clock import Clock
from actiontrack.domain.models import format_item_id, parse_item_id
from actiontrack.errors import NotFoundError
from actiontrack.store.db import Database
from actiontrack.store.repo import ActionItemRow, Repo

_MAX_SQLITE_INT = 2**63 - 1


@dataclass(frozen=True)
class DuplicateNotice:
    other_id: int
    status: str
    reason: str | None


@dataclass(frozen=True)
class DoneResult:
    item_id: int
    previous_status: str
    already_done: bool
    completion_origin: str | None
    completed_at: str | None
    task_text: str
    source: str | None
    duplicates: tuple[DuplicateNotice, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "status": "done",
            "previous_status": self.previous_status,
            "already_done": self.already_done,
            "completion_origin": self.completion_origin,
            "completed_at": self.completed_at,
        }


def possible_duplicates_of(repo: Repo, item_id: int) -> tuple[DuplicateNotice, ...]:
    out = []
    for lk in repo.links_of(item_id):
        if lk.kind != "possible_duplicate":
            continue
        other = lk.to_item_id if lk.from_item_id == item_id else lk.from_item_id
        row = repo.get_action_item(other)
        out.append(DuplicateNotice(other, row.status if row else "unknown", lk.reason))
    return tuple(sorted(out, key=lambda d: d.other_id))


def primary_source(repo: Repo, item_id: int) -> str | None:
    ctx = repo.list_source_context(item_id)
    ordered = sorted(
        ctx,
        key=lambda s: (
            not s.source.present,
            s.meeting_date is None,
            s.meeting_date.isoformat() if s.meeting_date else "",
            s.rel_path,
            s.source.line_start,
        ),
    )
    if not ordered:
        return None
    return f"{ordered[0].rel_path.replace(chr(92), '/')}:{ordered[0].source.line_start}"


def _result(repo: Repo, row: ActionItemRow, *, previous: str, already: bool) -> DoneResult:
    return DoneResult(
        item_id=row.id,
        previous_status=previous,
        already_done=already,
        completion_origin=row.completion_origin,
        completed_at=row.completed_at,
        task_text=row.task_text or row.description,
        source=primary_source(repo, row.id),
        duplicates=possible_duplicates_of(repo, row.id),
    )


def _lookup(repo: Repo, item_no: int) -> ActionItemRow:
    row = repo.get_action_item(item_no) if item_no <= _MAX_SQLITE_INT else None
    if row is None:
        raise NotFoundError(
            f"no action item {format_item_id(item_no) if item_no >= 1 else item_no}",
            next_step="run `actiontrack list --status all` to see the known ids",
        )
    return row


def mark_done(db: Database, repo: Repo, item_ref: str, clock: Clock) -> DoneResult:
    item_no = parse_item_id(item_ref.strip())
    row = _lookup(repo, item_no)
    if row.status == "done":
        return _result(repo, row, previous="done", already=True)
    stamp = clock.now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.transaction():
        current = repo.get_action_item(item_no)  # re-read inside the write transaction
        if current is None:
            raise NotFoundError(f"no action item {format_item_id(item_no)}")
        if current.status == "done":
            return _result(repo, current, previous="done", already=True)
        repo.set_manual_done(item_no, ts=stamp)
    done_row = repo.get_action_item(item_no)
    assert done_row is not None
    return _result(repo, done_row, previous="open", already=False)
