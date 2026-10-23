"""``actiontrack list``: query model, filters, sort, row view-model (M6: T-M6-01..06; spec 4.3, FR-02.*).

Pure and read-only: everything is computed from rows the caller loaded through ``Repo`` and from the single
``today`` the CLI got from the clock (the system clock is never read here).

Public API
----------
``ListQuery(owner, status, due_before, ambiguous, include_ambiguous, wide)``   validated query (default status open)
``validate_list_args(args)``     argument-combination and value checks (exit 2) BEFORE any database access
``parse_list_args(args, today)`` -> ``ListQuery`` (``--due-before today|tomorrow`` resolved against ``today``)
``display_status(status, due_date, today)`` / ``days_overdue(due_date, today)``   BR-02/BR-03 (strict ``<``)
``OVERDUE_SQL``                  the same predicate as SQL (one definition; consistency-tested)
``sort_key(row)``                FR-02.4 deterministic key (Python, case-folded NFC ordinal, never DB collation)
``list_items(repo, registry, query, today) -> ListResult``
``ListRow``                      single view-model consumed by the table and JSON renderers
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

from actiontrack.domain.models import OwnerStatus
from actiontrack.errors import UsageError
from actiontrack.resolution.owners import (
    candidate_key,
    group_ambiguous,
    owner_display,
    owner_matches_person,
)
from actiontrack.resolution.people import PeopleRegistry, resolve_person_query
from actiontrack.store.repo import ActionItemRow, ItemLinkRow, ItemOwnerRow, Repo, SourceContextRow

STATUSES: tuple[str, ...] = ("open", "done", "overdue", "all")

#: SQL twin of ``display_status(...) == "overdue"`` (parameter ``:today`` = ISO date). One definition.
OVERDUE_SQL = "status = 'open' AND due_date IS NOT NULL AND due_date < :today"

_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_DUE_FORMS = "YYYY-MM-DD, today or tomorrow"
_EXCERPT_LEN = 12


# ------------------------------------------------------------------------------ T-M6-01 query model
@dataclass(frozen=True)
class ListQuery:
    owner: str | None = None
    status: str = "open"
    due_before: date | None = None
    ambiguous: bool = False
    include_ambiguous: bool = False
    wide: bool = False


def _parse_due_before(text: str, today: date | None) -> date | None:
    """``YYYY-MM-DD`` | ``today`` | ``tomorrow``; ``None`` for a relative word when ``today`` is not known."""
    word = text.strip()
    if word in ("today", "tomorrow"):
        if today is None:
            return None
        return today if word == "today" else today + timedelta(days=1)
    if _ISO_DATE.fullmatch(word):
        try:
            return date.fromisoformat(word)
        except ValueError:
            pass
    raise UsageError(
        f"invalid --due-before value {text!r}", next_step=f"use one of: {_DUE_FORMS}"
    )


def validate_list_args(args: argparse.Namespace) -> None:
    """Exit-2 checks that need neither the clock nor the database (FR-02.7/02.8, G-19, AC-02.9/02.11)."""
    status = getattr(args, "status", "open")
    if status not in STATUSES:
        raise UsageError(f"invalid --status value {status!r}", next_step="use one of: " + ", ".join(STATUSES))
    if args.due_before is not None:
        _parse_due_before(args.due_before, None)
    if args.ambiguous and args.owner is not None:
        raise UsageError("--ambiguous cannot be combined with --owner", next_step="use either option, not both")
    if args.include_ambiguous and args.owner is None:
        raise UsageError("--include-ambiguous requires --owner", next_step="add --owner <full name|id|email>")


def parse_list_args(args: argparse.Namespace, today: date) -> ListQuery:
    validate_list_args(args)
    due = None if args.due_before is None else _parse_due_before(args.due_before, today)
    return ListQuery(
        owner=args.owner,
        status=args.status,
        due_before=due,
        ambiguous=bool(args.ambiguous),
        include_ambiguous=bool(args.include_ambiguous),
        wide=bool(args.wide),
    )


# --------------------------------------------------------------------- T-M6-02 overdue / display status
def display_status(status: str, due_date: date | None, today: date) -> str:
    """``overdue`` iff stored status is open with a resolved due date strictly before ``today``."""
    if status == "open" and due_date is not None and due_date < today:
        return "overdue"
    return status


def days_overdue(due_date: date | None, today: date) -> int | None:
    if due_date is None or due_date >= today:
        return None
    return (today - due_date).days


def status_matches(query_status: str, stored: str, shown: str) -> bool:
    if query_status == "all":
        return True
    if query_status == "overdue":
        return shown == "overdue"
    return stored == query_status


# ------------------------------------------------------------------------------------ row view-model
@dataclass(frozen=True)
class OwnerView:
    ordinal: int
    owner_raw: str | None
    role: str
    person_id: str | None
    display_name: str
    owner_status: str
    provenance: str
    candidates: tuple[tuple[str, str], ...]  # (person_id, full name)


@dataclass(frozen=True)
class SourceView:
    rel_path: str
    line_start: int
    line_end: int
    raw_text: str
    section_heading: str | None
    checkbox_state: str
    present: bool


@dataclass(frozen=True)
class LinkView:
    kind: str
    from_id: int
    to_id: int
    reason: str | None


@dataclass(frozen=True)
class ListRow:
    item: ActionItemRow
    display_status: str
    days_overdue: int | None
    due_display: str
    owner_display: str
    owners: tuple[OwnerView, ...]
    owner_sort: tuple[int, str]
    source_display: str
    sources: tuple[SourceView, ...]
    flags: tuple[str, ...]
    links: tuple[LinkView, ...]
    raw_owners: tuple[Any, ...] = field(default=(), repr=False)

    @property
    def item_id(self) -> int:
        return self.item.id

    @property
    def task(self) -> str:
        return self.item.task_text or self.item.description


@dataclass(frozen=True)
class ListGroup:
    heading: str | None
    rows: tuple[ListRow, ...]


@dataclass(frozen=True)
class ListResult:
    query: ListQuery
    today: date
    groups: tuple[ListGroup, ...]

    @property
    def rows(self) -> tuple[ListRow, ...]:
        """Flat rows, each item once (an item in two ambiguous groups is counted once)."""
        seen: set[int] = set()
        out: list[ListRow] = []
        for g in self.groups:
            for r in g.rows:
                if r.item_id not in seen:
                    seen.add(r.item_id)
                    out.append(r)
        return tuple(out)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def format_due(item: ActionItemRow) -> str:
    if item.due_confidence == "unresolved":
        raw = (item.due_raw or "").strip()
        if not raw:
            return "-"
        excerpt = raw if len(raw) <= _EXCERPT_LEN else raw[:_EXCERPT_LEN] + "…"
        return f"?({excerpt})"
    return item.due_date.isoformat() if item.due_date is not None else "-"


def _owner_adapter(o: ItemOwnerRow) -> Any:
    return SimpleNamespace(
        status=o.owner_status,
        owner_raw=o.owner_raw,
        person_id=o.person_id,
        candidates=o.candidates,
        ordinal=o.ordinal,
        role=o.role,
        provenance=o.provenance,
    )


def _owner_text(o: Any, registry: PeopleRegistry) -> str:
    text = owner_display(o, registry).text
    if OwnerStatus(o.status) is OwnerStatus.PROVISIONAL and not text.startswith("?"):
        text = "?" + text
    return text or "-"


def _owner_rank(first: Any | None) -> int:
    if first is None:
        return 2
    status = OwnerStatus(first.status)
    if status is OwnerStatus.AMBIGUOUS:
        return 1
    if status is OwnerStatus.UNASSIGNED:
        return 2
    return 0


def build_row(
    item: ActionItemRow,
    owners: list[ItemOwnerRow],
    sources: list[SourceContextRow],
    links: list[ItemLinkRow],
    registry: PeopleRegistry,
    today: date,
) -> ListRow:
    shown = display_status(item.status, item.due_date, today)
    adapters = [_owner_adapter(o) for o in owners]
    texts = [_owner_text(a, registry) for a in adapters]
    owner_text = ", ".join(texts) if texts else "-"
    views = tuple(
        OwnerView(
            a.ordinal,
            a.owner_raw,
            a.role,
            a.person_id,
            t,
            str(a.status),
            str(a.provenance),
            tuple(
                (pid, registry.full_name(pid) if pid in registry else pid) for pid in candidate_key(a.candidates)
            ),
        )
        for a, t in zip(adapters, texts)
    )
    rank = _owner_rank(adapters[0] if adapters else None)
    owner_sort = (rank, _fold(texts[0]) if rank < 2 and texts else "")

    def order(s: SourceContextRow) -> tuple[Any, ...]:
        return (s.meeting_date is None, s.meeting_date or date.min, s.rel_path, s.source.line_start)

    ordered = sorted(sources, key=order)
    considered = [s for s in ordered if s.source.present] or ordered
    if considered:
        first = considered[0]
        source_text = f"{first.rel_path.replace(chr(92), '/')}:{first.source.line_start}"
        if len(considered) > 1:
            source_text += f" (+{len(considered) - 1})"
    else:
        source_text = "-"
    source_views = tuple(
        SourceView(
            s.rel_path.replace("\\", "/"),
            s.source.line_start,
            s.source.line_end,
            s.source.raw_text,
            s.source.section_heading,
            s.source.checkbox_state,
            s.source.present,
        )
        for s in ordered
    )

    flags: set[str] = set()
    if len(sources) >= 2:
        flags.add("CR")
    if any(lk.kind == "possible_duplicate" for lk in links):
        flags.add("D?")
    if any(OwnerStatus(a.status) is OwnerStatus.AMBIGUOUS for a in adapters):
        flags.add("OA")
    if not adapters or any(OwnerStatus(a.status) in (OwnerStatus.UNASSIGNED, OwnerStatus.PROVISIONAL) for a in adapters):
        flags.add("OU")
    if item.due_confidence in ("unresolved", "none"):
        flags.add("DU")
    if item.source_state == "missing":
        flags.add("SM")
    if item.status == "done" and any(lk.kind == "completed_by" and lk.from_item_id == item.id for lk in links):
        flags.add("CI")
    from actiontrack.report.table import FLAG_ORDER

    return ListRow(
        item=item,
        display_status=shown,
        days_overdue=days_overdue(item.due_date, today) if shown == "overdue" else None,
        due_display=format_due(item),
        owner_display=owner_text,
        owners=views,
        owner_sort=owner_sort,
        source_display=source_text,
        sources=source_views,
        flags=tuple(f for f in FLAG_ORDER if f in flags),
        links=tuple(LinkView(lk.kind, lk.from_item_id, lk.to_item_id, lk.reason) for lk in links),
        raw_owners=tuple(adapters),
    )


# --------------------------------------------------------------------------------- T-M6-04 sorting
def sort_key(row: ListRow) -> tuple[Any, ...]:
    due = row.item.due_date
    return (due is None, due or date.min, row.owner_sort[0], row.owner_sort[1], row.item.id)


# --------------------------------------------------------------------- selection (T-M6-02/03/05)
def load_rows(repo: Repo, registry: PeopleRegistry, today: date) -> list[ListRow]:
    owners_by_item: dict[int, list[ItemOwnerRow]] = {}
    for o in repo.list_all_item_owners():
        owners_by_item.setdefault(o.item_id, []).append(o)
    sources_by_item = repo.list_all_source_contexts()
    links_by_item: dict[int, list[ItemLinkRow]] = {}
    for lk in repo.list_links():
        links_by_item.setdefault(lk.from_item_id, []).append(lk)
        links_by_item.setdefault(lk.to_item_id, []).append(lk)
    return [
        build_row(
            item,
            sorted(owners_by_item.get(item.id, []), key=lambda o: o.ordinal),
            sources_by_item.get(item.id, []),
            links_by_item.get(item.id, []),
            registry,
            today,
        )
        for item in repo.list_action_items()
    ]


def list_items(repo: Repo, registry: PeopleRegistry, query: ListQuery, today: date) -> ListResult:
    """Apply the AND-combined filters, sort, and (for ``--ambiguous``) group by candidate set."""
    person_id: str | None = None
    if query.owner is not None:
        person_id = resolve_person_query(query.owner, registry).id  # PersonAmbiguous (6) / PersonNotFound (3)

    selected: list[ListRow] = []
    for row in load_rows(repo, registry, today):
        if not status_matches(query.status, row.item.status, row.display_status):
            continue
        if query.due_before is not None and (row.item.due_date is None or row.item.due_date >= query.due_before):
            continue
        if person_id is not None and not owner_matches_person(row.raw_owners, person_id, query.include_ambiguous):
            continue
        selected.append(row)

    if not query.ambiguous:
        return ListResult(query, today, (ListGroup(None, tuple(sorted(selected, key=sort_key))),))

    by_id = {r.item_id: r for r in selected}
    views = [
        SimpleNamespace(item_id=r.item_id, status="open", owners=r.raw_owners)  # status filtering already done
        for r in selected
    ]
    groups = []
    for g in group_ambiguous(views, registry, status="all"):
        rows = sorted((by_id[v.item_id] for v in g.items), key=sort_key)
        groups.append(ListGroup(" | ".join(g.names), tuple(rows)))
    return ListResult(query, today, tuple(groups))


def ambiguous_count(repo: Repo, registry: PeopleRegistry, today: date, status: str = "open") -> int:
    """Number of items listed by ``--ambiguous --status <status>`` (reconciles with the ingest report)."""
    q = ListQuery(status=status, ambiguous=True)
    return len(list_items(repo, registry, q, today).rows)
