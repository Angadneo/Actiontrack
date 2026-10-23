"""Repository base: the only module family that writes SQL (T-M1-13; plan 2.3 ``repo.py``).

No SQL may exist outside ``actiontrack/store/`` (enforced by ``tests/integration/test_repo_base.py``).
Later milestones (M2-M7) EXTEND this module instead of writing SQL elsewhere. Planned additions:
  * M4: ``upsert_person`` / ``add_alias`` / ``merge_provisional``, ``upsert_source_file``,
    ``insert_meeting`` / ``insert_attendees``, ``insert_ingest_run`` / ``finish_ingest_run`` /
    ``mark_interrupted_runs``, ``add_warning``.
  * M5: ``insert_action_item`` / ``insert_item_source`` / ``set_item_owners`` / ``add_item_event`` /
    ``upsert_item_link`` (possible_duplicate stored with from < to, spec 7.2) and the whole-database
    re-resolution queries (10.7.9).
  * M6: ``list_items`` filters, ``mark_done`` (manual completion).
  * M7: ``insert_followup_draft`` / ``update_followup_draft`` (never any body/subject text, GM-10).
Every new write method MUST pass free text through ``insert_row`` / ``update_row`` (or call
``to_storage_text`` itself) so NFC normalisation has a single funnel.

Public API
----------
``to_storage_text(s) -> str``   NFC. The funnel for ``description``, ``task_text``, names, titles.
``to_storage_raw(s) -> str``    identity (NO normalisation). For ``ItemSource.raw_text`` (G-22, spec 7.2
                                exception: traceability and ``block_hash`` are byte-for-byte) and for
                                filesystem-derived text (see ``VERBATIM_COLUMNS``).
``VERBATIM_COLUMNS``            ``{(table, column)}`` written through ``to_storage_raw``.
``Repo(db)``                    thin wrapper over a ``Database``:
    ``schema_version() -> int``
    ``next_item_id_preview() -> int``   id the next ``action_item`` insert will receive (AUTOINCREMENT,
                                        BR-11: never reused). A preview only, it is not reserved.
    ``get_item(item_id) -> sqlite3.Row | None``
    ``insert_row(table, values) -> int`` generic insert of ``{column: value}``: ``str`` values go through
                                        ``to_storage_text`` (or ``to_storage_raw`` for verbatim columns);
                                        table/column names are validated against the schema. Returns rowid.
    ``update_row(table, key_column, key_value, values) -> int``  same funnel for UPDATE, returns rowcount.
    People (T-M4-07; ``person_alias.auto`` was added to migration 0001 + schema.sql, the product is
    pre-release so no 0002 migration is needed; ``tests/unit/test_schema.py`` lists the column):
    ``upsert_person(person) -> "inserted"|"updated"|"unchanged"``
    ``upsert_alias(person_id, alias, alias_key, auto=False)``  (no statement at all when the row is identical)
    ``persist_registry(registry) -> {"inserted", "updated", "aliases"}``  DB rows := registry
    ``upsert_roster(registry, roster) -> tuple[Person, ...]``  start of every non-dry ingest
    ``load_registry(roster=None) -> PeopleRegistry``  rebuild K from DB rows (+ current roster)
    ``insert_attendees(meeting_id, [(raw_name, person_id, email_hint)]) -> int`` / ``list_attendees``
    ``merge_person(provisional_id, into_id, *, alias=None, alias_key=None)``  repoint + alias + delete
    Pair with ``resolution.people.file_transaction(db, registry)`` for rollback of registry state.
Ingest entities (T-M5-01/02; row dataclasses are frozen, every list method has an explicit ORDER BY,
no method commits and none deletes an ``item_source`` or ``item_link`` row, BR-08/BR-16):
    ``now_iso() -> str``  current UTC timestamp ``YYYY-MM-DDTHH:MM:SSZ`` from the injected ``Clock``
                         (``Repo(db, clock=None)``: ``clock`` or ``db.clock`` or ``SystemClock``).
    runs:      ``insert_ingest_run(folder_root, *, dry_run=False, started_at=None) -> int``,
               ``update_ingest_run(run_id, **fields)``, ``bump_run_counters(run_id, **deltas)``,
               ``get_ingest_run(id) -> IngestRunRow | None``, ``list_ingest_runs(status=None)``,
               ``count_files_ingested_in_run(run_id) -> int`` / ``last_ingested_file(run_id) -> str | None``
               (what an interrupted run had committed, T-M5-04: derived from ``source_file.last_ingested_run``)
    files:     ``insert_source_file(folder_root, rel_path, *, status, ...) -> int``,
               ``update_source_file(id, **fields)``, ``get_source_file(folder_root, rel_path)``,
               ``get_source_file_by_id(id)``, ``list_source_files(folder_root=None, status=None)``
    meetings:  ``insert_meeting(source_file_id, meeting_date, date_source, title) -> int``,
               ``update_meeting(id, **fields)``, ``get_meeting_for_file(source_file_id)``
    items:     ``insert_action_item(*, description, task_text, description_core_key, ...) -> int``
               (ids come from AUTOINCREMENT, BR-11), ``get_action_item(id) -> ActionItemRow | None``,
               ``list_action_items(status=None)``, ``touch_item(item_id, changes, *, owners=None,
               event_kind=None, run_id=None) -> dict`` (the ONLY writer of business columns, bumps
               ``updated_at`` only on a real change, BR-15), ``set_item_owners(item_id, owners) -> bool``,
               ``list_item_owners(item_id)``
    sources:   ``insert_item_source(*, item_id, source_file_id, meeting_id, ...) -> int``,
               ``update_item_source(id, **fields)``, ``get_item_source(id)``,
               ``list_item_sources(item_id=None, source_file_id=None, present=None)``,
               ``set_source_owners(item_source_id, owners) -> bool``, ``list_source_owners(item_source_id)``,
               ``list_all_source_owners()``, ``list_source_context(item_id)`` (sources + meeting date + path),
               ``item_ids_by_core_key(key)``, ``item_ids_by_owner_persons(ids)`` (matcher candidates)
    links:     ``insert_link(from_id, to_id, kind, reason=None, run_id=None) -> bool`` (possible_duplicate is
               stored once with from < to; completed_by keeps its direction; INSERT OR IGNORE),
               ``links_of(item_id)`` (both ends), ``list_links(kind=None)``
    events:    ``record_event(item_id, kind, before, after, run_id, ts=None) -> int | None`` (idempotent per
               ``(item_id, kind, run_id, payload)``), ``list_events(item_id=None, run_id=None)``
    warnings:  ``add_warning(run_id, code, message, *, source_file_id=None, line=None) -> int``,
               ``list_warnings(run_id=None, source_file_id=None)``
``canonical_json(obj) -> str``  sorted keys, compact, non-ASCII kept (used for event payloads).
Re-exported from ``actiontrack.domain.models``: ``format_item_id``, ``parse_item_id``.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import json
import sqlite3
import unicodedata
from collections.abc import Iterable, Sequence

from actiontrack.clock import Clock, SystemClock
from actiontrack.config import Roster
from actiontrack.domain.models import Person, PersonOrigin, format_item_id, parse_item_id
from actiontrack.errors import InternalError
from actiontrack.resolution.people import PeopleRegistry
from actiontrack.store.db import Database

__all__ = [
    "BUSINESS_COLUMNS",
    "EVENT_KINDS",
    "VERBATIM_COLUMNS",
    "ActionItemRow",
    "IngestRunRow",
    "IngestWarningRow",
    "ItemEventRow",
    "ItemLinkRow",
    "ItemOwnerRow",
    "ItemSourceOwnerRow",
    "ItemSourceRow",
    "MeetingRow",
    "Repo",
    "SourceContextRow",
    "SourceFileRow",
    "canonical_json",
    "format_item_id",
    "parse_item_id",
    "to_storage_raw",
    "to_storage_text",
]

#: Columns stored byte-for-byte: raw note text (G-22) and filesystem names / paths.
VERBATIM_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("item_source", "raw_text"),
        ("source_file", "rel_path"),
        ("source_file", "folder_root"),
        ("followup_draft", "handoff_path"),
    }
)


def to_storage_text(s: str) -> str:
    """NFC-normalise free text for storage (comparison keys are computed separately, spec 8.3)."""
    return unicodedata.normalize("NFC", s)


def to_storage_raw(s: str) -> str:
    """Verbatim storage: no normalisation of any kind."""
    return s


# ---------------------------------------------------------------------------------------------
# Ingest-entity row types (T-M5-01). Frozen value objects; dates are ``datetime.date``, booleans
# ``bool``, JSON columns decoded. Spec 7.2 names the columns.
# ---------------------------------------------------------------------------------------------
EVENT_KINDS: tuple[str, ...] = (
    "created",
    "source_added",
    "source_removed",
    "merged",
    "updated_from_source",
    "status_changed",
    "owner_changed",
    "manual_done",
)
LINK_KINDS: tuple[str, ...] = ("possible_duplicate", "completed_by")
LINK_REASONS: tuple[str, ...] = (
    "due_differs",
    "owner_differs",
    "status_differs",
    "same_file",
    "fuzzy",
    "recurrence",
    "outside_window",
    "owner_unresolved",
)
#: ``action_item`` columns that carry business meaning: only ``touch_item`` writes them after insert and
#: only a real change to one of them (or to the item's owners) moves ``updated_at`` (BR-15, spec 7.2).
BUSINESS_COLUMNS: tuple[str, ...] = (
    "description",
    "task_text",
    "description_core_key",
    "status",
    "completion_origin",
    "completed_at",
    "completion_evidence_source_id",
    "due_raw",
    "due_raw_other",
    "due_date",
    "due_confidence",
    "due_rule",
    "due_reason",
    "source_state",
)
RUN_COUNTERS: tuple[str, ...] = (
    "files_discovered",
    "files_ingested",
    "files_unchanged",
    "files_empty",
    "files_failed",
    "items_new",
    "items_merged",
    "items_updated",
    "items_unchanged",
    "possible_duplicates",
    "already_done_in_note",
    "resolved_via_attendance",
)


def canonical_json(obj: object) -> str:
    """Canonical JSON for event payloads: sorted keys, compact separators, non-ASCII kept."""

    def default(value: object) -> object:
        if isinstance(value, _dt.datetime | _dt.date):
            return value.isoformat()
        if isinstance(value, enum.Enum):
            return value.value
        if isinstance(value, set | frozenset):
            return sorted(value)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return dataclasses.asdict(value)
        raise TypeError(f"not JSON serialisable: {type(value).__name__}")

    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=default)


def _date(value: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(value) if value else None


def _load(cls: type, row: sqlite3.Row, **conv: object) -> object:
    values = {}
    for f in dataclasses.fields(cls):
        raw = row[f.name]
        fn = conv.get(f.name)
        values[f.name] = fn(raw) if fn is not None and raw is not None else raw
    return cls(**values)


@dataclasses.dataclass(frozen=True, slots=True)
class IngestRunRow:
    id: int
    started_at: str
    finished_at: str | None
    folder_root: str
    status: str
    dry_run: bool
    files_discovered: int
    files_ingested: int
    files_unchanged: int
    files_empty: int
    files_failed: int
    items_new: int
    items_merged: int
    items_updated: int
    items_unchanged: int
    possible_duplicates: int
    already_done_in_note: int
    resolved_via_attendance: int


@dataclasses.dataclass(frozen=True, slots=True)
class SourceFileRow:
    id: int
    folder_root: str
    rel_path: str
    content_sha256: str | None
    size_bytes: int | None
    mtime: str | None
    status: str
    failure_code: str | None
    last_run_id: int | None
    first_seen_run: int | None
    last_ingested_run: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class MeetingRow:
    id: int
    source_file_id: int
    meeting_date: _dt.date | None
    date_source: str
    title: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class ActionItemRow:
    id: int
    description: str
    task_text: str
    description_core_key: str
    status: str
    completion_origin: str | None
    completed_at: str | None
    completion_evidence_source_id: int | None
    due_raw: str | None
    due_raw_other: str | None
    due_date: _dt.date | None
    due_confidence: str
    due_rule: str | None
    due_reason: str | None
    source_state: str
    created_at: str
    updated_at: str


@dataclasses.dataclass(frozen=True, slots=True)
class ItemOwnerRow:
    item_id: int
    ordinal: int
    owner_raw: str | None
    role: str
    person_id: str | None
    owner_status: str
    candidates: tuple[str, ...]
    provenance: str


@dataclasses.dataclass(frozen=True, slots=True)
class ItemSourceRow:
    id: int
    item_id: int
    source_file_id: int
    meeting_id: int | None
    line_start: int
    line_end: int
    raw_text: str
    section_heading: str | None
    checkbox_state: str
    occurrence_index: int
    block_hash: str
    present: bool
    first_seen_run: int | None
    last_seen_run: int | None
    description: str | None
    task_text: str | None
    core_key: str | None
    due_raw: str | None
    due_raw_other: str | None
    due_date: _dt.date | None
    due_confidence: str
    due_rule: str | None
    due_reason: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class SourceContextRow:
    """An ``item_source`` row with the facts that order and match it (T-M5-08/22)."""

    source: ItemSourceRow
    meeting_date: _dt.date | None
    rel_path: str
    folder_root: str


@dataclasses.dataclass(frozen=True, slots=True)
class ItemSourceOwnerRow:
    item_source_id: int
    ordinal: int
    owner_raw: str | None
    role: str
    person_id: str | None
    owner_status: str
    provenance: str


@dataclasses.dataclass(frozen=True, slots=True)
class ItemLinkRow:
    from_item_id: int
    to_item_id: int
    kind: str
    reason: str | None
    created_run: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class ItemEventRow:
    id: int
    item_id: int
    ts: str
    kind: str
    before: object
    after: object
    run_id: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class IngestWarningRow:
    id: int
    run_id: int
    source_file_id: int | None
    line: int | None
    code: str
    message: str


def _owner_tuple(o: object) -> tuple[object, ...]:
    """Comparable, storage-normalised form of a ``ResolvedOwner`` (ordinal is positional)."""
    raw = getattr(o, "owner_raw", None)
    return (
        None if raw is None else to_storage_text(raw),
        str(getattr(o, "role", "owner")),
        getattr(o, "person_id", None),
        str(getattr(o, "status", None) or getattr(o, "owner_status", "unassigned")),
        tuple(sorted(getattr(o, "candidates", ()) or ())),
        str(getattr(o, "provenance", "none")),
    )


class Repo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self.db = db
        self.clock: Clock = clock or getattr(db, "clock", None) or SystemClock()
        self._columns: dict[str, frozenset[str]] = {}

    @property
    def conn(self) -> sqlite3.Connection:
        return self.db.conn

    def schema_version(self) -> int:
        return self.db.version

    def next_item_id_preview(self) -> int:
        row = self.conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'action_item'").fetchone()
        return (row[0] if row else 0) + 1

    def get_item(self, item_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM action_item WHERE id = ?", (item_id,)).fetchone()

    # ----------------------------------------------------------- write funnel
    def _table_columns(self, table: str) -> frozenset[str]:
        cols = self._columns.get(table)
        if cols is None:
            cols = frozenset(r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")'))
            if not cols:
                raise InternalError(f"unknown table {table!r}")
            self._columns[table] = cols
        return cols

    def _prepare(self, table: str, values: dict[str, object]) -> tuple[list[str], list[object]]:
        allowed = self._table_columns(table)
        names: list[str] = []
        params: list[object] = []
        for column, value in values.items():
            if column not in allowed:
                raise InternalError(f"unknown column {table}.{column}")
            if isinstance(value, _dt.datetime | _dt.date):
                value = value.isoformat()
            elif isinstance(value, bool):
                value = int(value)
            if isinstance(value, str):
                value = (
                    to_storage_raw(value)
                    if (table, column) in VERBATIM_COLUMNS
                    else to_storage_text(value)
                )
            names.append(column)
            params.append(value)
        return names, params

    def insert_row(self, table: str, values: dict[str, object]) -> int:
        names, params = self._prepare(table, values)
        cols = ", ".join(f'"{n}"' for n in names)
        marks = ", ".join("?" for _ in names)
        cur = self.conn.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})', params)
        return cur.lastrowid

    def update_row(self, table: str, key_column: str, key_value: object, values: dict[str, object]) -> int:
        if key_column not in self._table_columns(table):
            raise InternalError(f"unknown column {table}.{key_column}")
        names, params = self._prepare(table, values)
        sets = ", ".join(f'"{n}" = ?' for n in names)
        cur = self.conn.execute(f'UPDATE "{table}" SET {sets} WHERE "{key_column}" = ?', [*params, key_value])
        return cur.rowcount

    # ----------------------------------------------------------- people (T-M4-07)
    @staticmethod
    def _person_values(p: Person) -> dict[str, object]:
        return {
            "first_name": p.first_name,
            "last_name": p.last_name,
            "qualifier": p.qualifier,
            "display_name": p.display_name,
            "name_key": p.name_key,
            "identity_key": p.identity_key,
            "email": p.email,
            "origin": str(p.origin),
            "verified": 1 if p.verified else 0,
        }

    @staticmethod
    def _row_to_person(row: sqlite3.Row) -> Person:
        return Person(
            id=row["id"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            identity_key=row["identity_key"],
            origin=PersonOrigin(row["origin"]),
            verified=bool(row["verified"]),
            qualifier=row["qualifier"],
            display_name=row["display_name"],
            name_key=row["name_key"],
            email=row["email"],
        )

    def upsert_person(self, person: Person) -> str:
        """Insert or update one ``person`` row (NFC funnel). Returns ``inserted`` / ``updated`` /
        ``unchanged``. The id is never changed or reused for another identity."""
        row = self.conn.execute("SELECT * FROM person WHERE id = ?", (person.id,)).fetchone()
        if row is None:
            self.insert_row("person", {"id": person.id, **self._person_values(person)})
            return "inserted"
        if self._row_to_person(row) == self._normalised(person):
            return "unchanged"
        self.update_row("person", "id", person.id, self._person_values(person))
        return "updated"

    @staticmethod
    def _normalised(p: Person) -> Person:
        def nfc(s: str | None) -> str | None:
            return None if s is None else to_storage_text(s)

        return dataclasses.replace(
            p,
            first_name=nfc(p.first_name),
            last_name=nfc(p.last_name),
            qualifier=nfc(p.qualifier),
            display_name=nfc(p.display_name),
            name_key=nfc(p.name_key),
            identity_key=nfc(p.identity_key),
            email=nfc(p.email),
        )

    def upsert_alias(self, person_id: str, alias: str, alias_key: str, auto: bool = False) -> None:
        """Insert/refresh one ``person_alias`` row (a roster alias is never downgraded to auto)."""
        row = self.conn.execute(
            "SELECT alias, auto FROM person_alias WHERE person_id = ? AND alias_key = ?",
            (person_id, to_storage_text(alias_key)),
        ).fetchone()
        if row is not None and row["alias"] == to_storage_text(alias) and bool(row["auto"]) == bool(auto):
            return  # identical: no statement (an unchanged re-ingest writes nothing, BR-15)
        if row is None:
            self.insert_row(
                "person_alias",
                {"person_id": person_id, "alias": alias, "alias_key": alias_key, "auto": 1 if auto else 0},
            )
        else:
            self.conn.execute(
                "UPDATE person_alias SET alias = ?, auto = ? WHERE person_id = ? AND alias_key = ?",
                (to_storage_text(alias), 1 if auto else 0, person_id, to_storage_text(alias_key)),
            )

    def persist_registry(self, registry: PeopleRegistry) -> dict[str, int]:
        """Make the ``person`` / ``person_alias`` rows equal the registry (call inside the per-file
        transaction). People absent from the registry are NOT deleted here (history; the only
        deletion is ``merge_person``). Returns counts ``inserted`` / ``updated`` / ``aliases``."""
        counts = {"inserted": 0, "updated": 0, "aliases": 0}
        for person in registry.people():
            outcome = self.upsert_person(person)
            if outcome in counts:
                counts[outcome] += 1
        for person in registry.people():
            keep = {rec.alias_key for rec in registry.aliases(person.id)}
            for rec in registry.aliases(person.id):
                self.upsert_alias(rec.person_id, rec.alias, rec.alias_key, rec.auto)
                counts["aliases"] += 1
            for (stored_key,) in self.conn.execute(
                "SELECT alias_key FROM person_alias WHERE person_id = ?", (person.id,)
            ).fetchall():
                if stored_key not in {to_storage_text(k) for k in keep}:
                    self.conn.execute(
                        "DELETE FROM person_alias WHERE person_id = ? AND alias_key = ?",
                        (person.id, stored_key),
                    )
        return counts

    def upsert_roster(self, registry: PeopleRegistry, roster: Roster) -> tuple[Person, ...]:
        """Start of every non-dry ingest: add the roster to the registry and persist it. A roster
        person that vanishes from the config stays in both (history)."""
        persons = registry.add_roster(roster)
        self.persist_registry(registry)
        return persons

    def load_registry(self, roster: Roster | None = None) -> PeopleRegistry:
        """Rebuild K = stored people (any origin) + current roster from the database rows.

        Order: stored ``person`` rows as is, then ``add_roster`` (refreshes email / roster aliases,
        keeps stored ids), then stored aliases: ``auto`` rows always, non-auto rows only for people
        that are not in the current roster (historical roster people keep their aliases).
        """
        registry = PeopleRegistry()
        for row in self.conn.execute("SELECT * FROM person ORDER BY id"):
            registry.load_person(self._row_to_person(row))
        current = {p.id for p in registry.add_roster(roster)} if roster is not None else set()
        for row in self.conn.execute("SELECT * FROM person_alias ORDER BY person_id, alias_key"):
            if row["person_id"] in registry and (row["auto"] or row["person_id"] not in current):
                registry.load_alias(row["person_id"], row["alias"], row["alias_key"], auto=bool(row["auto"]))
        return registry

    def insert_attendees(self, meeting_id: int, attendees: Iterable[tuple[str, str | None, str | None]]) -> int:
        """``meeting_attendee`` rows ``(raw_name, person_id, email_hint)``; duplicates by raw name
        are ignored (PK). Returns the number inserted."""
        n = 0
        for raw_name, person_id, email_hint in attendees:
            exists = self.conn.execute(
                "SELECT 1 FROM meeting_attendee WHERE meeting_id = ? AND raw_name = ?",
                (meeting_id, to_storage_text(raw_name)),
            ).fetchone()
            if exists:
                continue
            self.insert_row(
                "meeting_attendee",
                {"meeting_id": meeting_id, "raw_name": raw_name, "person_id": person_id, "email_hint": email_hint},
            )
            n += 1
        return n

    def list_attendees(self, meeting_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT raw_name, person_id, email_hint FROM meeting_attendee WHERE meeting_id = ? ORDER BY raw_name",
            (meeting_id,),
        ).fetchall()

    #: every table.column that references ``person(id)`` and is repointed by a merge
    _PERSON_REFS = (
        ("meeting_attendee", "person_id"),
        ("item_owner", "person_id"),
        ("item_source_owner", "person_id"),
        ("followup_draft", "person_id"),
    )

    def merge_person(
        self, provisional_id: str, into_id: str, *, alias: str | None = None, alias_key: str | None = None
    ) -> None:
        """Merge a non-roster person into another (10.2a(d), G-30; policy is T-M4-15): repoint every
        reference, move its aliases and the merged-away name as ``auto`` aliases, delete the row."""
        if provisional_id == into_id:
            raise InternalError("cannot merge a person into itself")
        prov = self.conn.execute("SELECT * FROM person WHERE id = ?", (provisional_id,)).fetchone()
        if prov is None or self.conn.execute("SELECT 1 FROM person WHERE id = ?", (into_id,)).fetchone() is None:
            raise InternalError("merge_person: unknown person id")
        if prov["origin"] == "roster":
            raise InternalError("a roster person is never merged away")
        for table, column in self._PERSON_REFS:
            self.conn.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?', (into_id, provisional_id))
        moved = self.conn.execute(
            "SELECT alias, alias_key FROM person_alias WHERE person_id = ?", (provisional_id,)
        ).fetchall()
        self.conn.execute("DELETE FROM person_alias WHERE person_id = ?", (provisional_id,))
        for a, k in moved:
            if not self.conn.execute(
                "SELECT 1 FROM person_alias WHERE person_id = ? AND alias_key = ?", (into_id, k)
            ).fetchone():
                self.insert_row("person_alias", {"person_id": into_id, "alias": a, "alias_key": k, "auto": 1})
        key = alias_key if alias_key is not None else prov["identity_key"].split("|", 1)[0]
        text = alias if alias is not None else (prov["display_name"] or prov["first_name"])
        if not self.conn.execute(
            "SELECT 1 FROM person_alias WHERE person_id = ? AND alias_key = ?", (into_id, key)
        ).fetchone():
            self.insert_row("person_alias", {"person_id": into_id, "alias": text, "alias_key": key, "auto": 1})
        self.conn.execute("DELETE FROM person WHERE id = ?", (provisional_id,))

    # =========================================================================================
    # Ingest entities (T-M5-01, T-M5-02). No method here commits (the caller owns the transaction).
    # =========================================================================================
    def now_iso(self) -> str:
        """Current UTC instant from the injected clock, ``YYYY-MM-DDTHH:MM:SSZ`` (BR-18, AC-09.4)."""
        return self.clock.now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")

    def _check_fields(self, table: str, fields: Iterable[str], *, forbidden: Iterable[str] = ("id",)) -> None:
        allowed = self._table_columns(table)
        banned = set(forbidden)
        for name in fields:
            if name not in allowed or name in banned:
                raise InternalError(f"column {table}.{name} cannot be set here")

    # ----------------------------------------------------------- ingest_run
    def insert_ingest_run(self, folder_root: str, *, dry_run: bool = False, started_at: str | None = None) -> int:
        return self.insert_row(
            "ingest_run",
            {
                "started_at": started_at or self.now_iso(),
                "folder_root": folder_root,
                "dry_run": dry_run,
            },
        )

    def update_ingest_run(self, run_id: int, **fields: object) -> None:
        self._check_fields("ingest_run", fields)
        if fields:
            self.update_row("ingest_run", "id", run_id, fields)

    def bump_run_counters(self, run_id: int, **deltas: int) -> None:
        """Add ``delta`` to each named counter column of the run (names are validated)."""
        for name in deltas:
            if name not in RUN_COUNTERS:
                raise InternalError(f"unknown run counter {name!r}")
        for name, delta in sorted(deltas.items()):
            self.conn.execute(
                f'UPDATE ingest_run SET "{name}" = "{name}" + ? WHERE id = ?', (int(delta), run_id)
            )

    def get_ingest_run(self, run_id: int) -> IngestRunRow | None:
        row = self.conn.execute("SELECT * FROM ingest_run WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else _load(IngestRunRow, row, dry_run=bool)

    def list_ingest_runs(self, status: str | None = None) -> list[IngestRunRow]:
        sql = "SELECT * FROM ingest_run"
        params: tuple[object, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status,)
        rows = self.conn.execute(sql + " ORDER BY id", params).fetchall()
        return [_load(IngestRunRow, r, dry_run=bool) for r in rows]

    def count_files_ingested_in_run(self, run_id: int) -> int:
        """Files whose data was committed by this run (``source_file.last_ingested_run``)."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM source_file WHERE last_ingested_run = ?", (run_id,)
        ).fetchone()
        return int(row[0])

    def last_ingested_file(self, run_id: int) -> str | None:
        """Best-effort name of the file this run committed last (highest ``source_file.id``; a file
        re-ingested under an old id can make this approximate): only used in the interrupted-run notice."""
        row = self.conn.execute(
            "SELECT rel_path FROM source_file WHERE last_ingested_run = ? ORDER BY id DESC LIMIT 1", (run_id,)
        ).fetchone()
        return None if row is None else row[0]

    # ----------------------------------------------------------- source_file
    def insert_source_file(
        self,
        folder_root: str,
        rel_path: str,
        *,
        status: str,
        content_sha256: str | None = None,
        size_bytes: int | None = None,
        mtime: str | None = None,
        failure_code: str | None = None,
        run_id: int | None = None,
        ingested: bool = False,
    ) -> int:
        """New ``source_file`` row; ``first_seen_run`` and ``last_run_id`` = ``run_id``; ``last_ingested_run``
        too when ``ingested`` (a file whose data was committed in this run)."""
        return self.insert_row(
            "source_file",
            {
                "folder_root": folder_root,
                "rel_path": rel_path,
                "content_sha256": content_sha256,
                "size_bytes": size_bytes,
                "mtime": mtime,
                "status": status,
                "failure_code": failure_code,
                "last_run_id": run_id,
                "first_seen_run": run_id,
                "last_ingested_run": run_id if ingested else None,
            },
        )

    def update_source_file(self, source_file_id: int, **fields: object) -> None:
        self._check_fields("source_file", fields, forbidden=("id", "folder_root", "rel_path", "first_seen_run"))
        if fields:
            self.update_row("source_file", "id", source_file_id, fields)

    def get_source_file(self, folder_root: str, rel_path: str) -> SourceFileRow | None:
        row = self.conn.execute(
            "SELECT * FROM source_file WHERE folder_root = ? AND rel_path = ?", (folder_root, rel_path)
        ).fetchone()
        return None if row is None else _load(SourceFileRow, row)

    def get_source_file_by_id(self, source_file_id: int) -> SourceFileRow | None:
        row = self.conn.execute("SELECT * FROM source_file WHERE id = ?", (source_file_id,)).fetchone()
        return None if row is None else _load(SourceFileRow, row)

    def list_source_files(self, folder_root: str | None = None, status: str | None = None) -> list[SourceFileRow]:
        where: list[str] = []
        params: list[object] = []
        if folder_root is not None:
            where.append("folder_root = ?")
            params.append(folder_root)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM source_file" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self.conn.execute(sql + " ORDER BY folder_root, rel_path", params).fetchall()
        return [_load(SourceFileRow, r) for r in rows]

    # ----------------------------------------------------------- meeting
    def insert_meeting(
        self,
        source_file_id: int,
        meeting_date: _dt.date | str | None,
        date_source: str,
        title: str | None,
    ) -> int:
        return self.insert_row(
            "meeting",
            {
                "source_file_id": source_file_id,
                "meeting_date": meeting_date,
                "date_source": date_source,
                "title": title,
            },
        )

    def update_meeting(self, meeting_id: int, **fields: object) -> None:
        self._check_fields("meeting", fields, forbidden=("id", "source_file_id"))
        if fields:
            self.update_row("meeting", "id", meeting_id, fields)

    def get_meeting_for_file(self, source_file_id: int) -> MeetingRow | None:
        row = self.conn.execute("SELECT * FROM meeting WHERE source_file_id = ?", (source_file_id,)).fetchone()
        return None if row is None else _load(MeetingRow, row, meeting_date=_date)

    # ----------------------------------------------------------- action_item
    def insert_action_item(
        self,
        *,
        description: str,
        task_text: str,
        description_core_key: str,
        status: str = "open",
        completion_origin: str | None = None,
        completed_at: str | None = None,
        completion_evidence_source_id: int | None = None,
        due_raw: str | None = None,
        due_raw_other: str | None = None,
        due_date: _dt.date | str | None = None,
        due_confidence: str = "none",
        due_rule: str | None = None,
        due_reason: str | None = None,
        source_state: str = "present",
    ) -> int:
        """Insert an item; the id comes from AUTOINCREMENT (BR-11: never reused, never renumbered);
        ``created_at`` = ``updated_at`` = the injected clock."""
        now = self.now_iso()
        return self.insert_row(
            "action_item",
            {
                "description": description,
                "task_text": task_text,
                "description_core_key": description_core_key,
                "status": status,
                "completion_origin": completion_origin,
                "completed_at": completed_at,
                "completion_evidence_source_id": completion_evidence_source_id,
                "due_raw": due_raw,
                "due_raw_other": due_raw_other,
                "due_date": due_date,
                "due_confidence": due_confidence,
                "due_rule": due_rule,
                "due_reason": due_reason,
                "source_state": source_state,
                "created_at": now,
                "updated_at": now,
            },
        )

    def get_action_item(self, item_id: int) -> ActionItemRow | None:
        row = self.conn.execute("SELECT * FROM action_item WHERE id = ?", (item_id,)).fetchone()
        return None if row is None else _load(ActionItemRow, row, due_date=_date)

    def list_action_items(self, status: str | None = None) -> list[ActionItemRow]:
        sql = "SELECT * FROM action_item"
        params: tuple[object, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status,)
        rows = self.conn.execute(sql + " ORDER BY id", params).fetchall()
        return [_load(ActionItemRow, r, due_date=_date) for r in rows]

    # ----------------------------------------------------------- item_owner / item_source_owner
    def list_item_owners(self, item_id: int) -> list[ItemOwnerRow]:
        rows = self.conn.execute(
            "SELECT * FROM item_owner WHERE item_id = ? ORDER BY ordinal", (item_id,)
        ).fetchall()
        return [_load(ItemOwnerRow, r, candidates=lambda v: tuple(json.loads(v))) for r in rows]

    @staticmethod
    def _item_owner_tuples(rows: Sequence[ItemOwnerRow]) -> list[tuple[object, ...]]:
        return [
            (r.owner_raw, r.role, r.person_id, r.owner_status, tuple(sorted(r.candidates)), r.provenance)
            for r in rows
        ]

    def set_item_owners(self, item_id: int, owners: Sequence[object]) -> bool:
        """Make the item's ``item_owner`` rows equal ``owners`` (``ResolvedOwner``-shaped objects, ordinal
        = position). Returns True and sets ``updated_at`` only when the stored rows actually differ
        (owner is a business field, BR-15); an identical list writes nothing."""
        new = [_owner_tuple(o) for o in owners]
        if new == self._item_owner_tuples(self.list_item_owners(item_id)):
            return False
        self.conn.execute("DELETE FROM item_owner WHERE item_id = ?", (item_id,))
        for ordinal, o in enumerate(owners):
            raw, role, person_id, status, candidates, provenance = _owner_tuple(o)
            self.insert_row(
                "item_owner",
                {
                    "item_id": item_id,
                    "ordinal": ordinal,
                    "owner_raw": raw,
                    "role": role,
                    "person_id": person_id,
                    "owner_status": status,
                    "candidates": canonical_json(list(candidates)),
                    "provenance": provenance,
                },
            )
        self.conn.execute("UPDATE action_item SET updated_at = ? WHERE id = ?", (self.now_iso(), item_id))
        return True

    def list_source_owners(self, item_source_id: int) -> list[ItemSourceOwnerRow]:
        rows = self.conn.execute(
            "SELECT * FROM item_source_owner WHERE item_source_id = ? ORDER BY ordinal", (item_source_id,)
        ).fetchall()
        return [_load(ItemSourceOwnerRow, r) for r in rows]

    def list_all_source_owners(self) -> list[ItemSourceOwnerRow]:
        """Every per-source owner phrase of the whole database (re-resolution input, 10.7.9)."""
        rows = self.conn.execute("SELECT * FROM item_source_owner ORDER BY item_source_id, ordinal").fetchall()
        return [_load(ItemSourceOwnerRow, r) for r in rows]

    def set_source_owners(self, item_source_id: int, owners: Sequence[object]) -> bool:
        """Make the source's owner-phrase child rows equal ``owners``. True when something changed."""

        def key(t: tuple[object, ...]) -> tuple[object, ...]:  # (raw, role, person, status, provenance)
            return (t[0], t[1], t[2], t[3], t[5])

        new = [key(_owner_tuple(o)) for o in owners]
        old = [
            (r.owner_raw, r.role, r.person_id, r.owner_status, r.provenance)
            for r in self.list_source_owners(item_source_id)
        ]
        if new == old:
            return False
        self.conn.execute("DELETE FROM item_source_owner WHERE item_source_id = ?", (item_source_id,))
        for ordinal, o in enumerate(owners):
            raw, role, person_id, status, _cands, provenance = _owner_tuple(o)
            self.insert_row(
                "item_source_owner",
                {
                    "item_source_id": item_source_id,
                    "ordinal": ordinal,
                    "owner_raw": raw,
                    "role": role,
                    "person_id": person_id,
                    "owner_status": status,
                    "provenance": provenance,
                },
            )
        return True

    # ----------------------------------------------------------- item_source
    def insert_item_source(
        self,
        *,
        item_id: int,
        source_file_id: int,
        meeting_id: int | None,
        line_start: int,
        line_end: int,
        raw_text: str,
        checkbox_state: str,
        block_hash: str,
        section_heading: str | None = None,
        occurrence_index: int = 0,
        present: bool = True,
        run_id: int | None = None,
        description: str | None = None,
        task_text: str | None = None,
        core_key: str | None = None,
        due_raw: str | None = None,
        due_raw_other: str | None = None,
        due_date: _dt.date | str | None = None,
        due_confidence: str = "none",
        due_rule: str | None = None,
        due_reason: str | None = None,
    ) -> int:
        """New ``item_source``; ``raw_text`` is stored verbatim (G-22); first/last seen run = ``run_id``."""
        return self.insert_row(
            "item_source",
            {
                "item_id": item_id,
                "source_file_id": source_file_id,
                "meeting_id": meeting_id,
                "line_start": line_start,
                "line_end": line_end,
                "raw_text": raw_text,
                "section_heading": section_heading,
                "checkbox_state": checkbox_state,
                "occurrence_index": occurrence_index,
                "block_hash": block_hash,
                "present": present,
                "first_seen_run": run_id,
                "last_seen_run": run_id,
                "description": description,
                "task_text": task_text,
                "core_key": core_key,
                "due_raw": due_raw,
                "due_raw_other": due_raw_other,
                "due_date": due_date,
                "due_confidence": due_confidence,
                "due_rule": due_rule,
                "due_reason": due_reason,
            },
        )

    def update_item_source(self, item_source_id: int, **fields: object) -> None:
        self._check_fields("item_source", fields, forbidden=("id", "item_id", "first_seen_run"))
        if fields:
            self.update_row("item_source", "id", item_source_id, fields)

    @staticmethod
    def _source_row(row: sqlite3.Row) -> ItemSourceRow:
        return _load(ItemSourceRow, row, present=bool, due_date=_date)

    def get_item_source(self, item_source_id: int) -> ItemSourceRow | None:
        row = self.conn.execute("SELECT * FROM item_source WHERE id = ?", (item_source_id,)).fetchone()
        return None if row is None else self._source_row(row)

    def list_item_sources(
        self, item_id: int | None = None, source_file_id: int | None = None, present: bool | None = None
    ) -> list[ItemSourceRow]:
        where: list[str] = []
        params: list[object] = []
        if item_id is not None:
            where.append("item_id = ?")
            params.append(item_id)
        if source_file_id is not None:
            where.append("source_file_id = ?")
            params.append(source_file_id)
        if present is not None:
            where.append("present = ?")
            params.append(int(present))
        sql = "SELECT * FROM item_source" + (" WHERE " + " AND ".join(where) if where else "")
        return [self._source_row(r) for r in self.conn.execute(sql + " ORDER BY id", params).fetchall()]

    def list_source_context(self, item_id: int) -> list[SourceContextRow]:
        """All sources of an item (present or not, in id order) with meeting date and file path."""
        rows = self.conn.execute(
            "SELECT s.*, m.meeting_date AS _meeting_date, f.rel_path AS _rel_path, f.folder_root AS _folder_root "
            "FROM item_source s JOIN source_file f ON f.id = s.source_file_id "
            "LEFT JOIN meeting m ON m.id = s.meeting_id WHERE s.item_id = ? ORDER BY s.id",
            (item_id,),
        ).fetchall()
        return [
            SourceContextRow(self._source_row(r), _date(r["_meeting_date"]), r["_rel_path"], r["_folder_root"])
            for r in rows
        ]

    def item_ids_by_core_key(self, core_key: str) -> list[int]:
        """Ids (ascending) of items whose canonical core key or any source core key equals ``core_key``
        (candidate lookup of 10.6; an empty key matches nothing)."""
        if not core_key:
            return []
        rows = self.conn.execute(
            "SELECT id FROM action_item WHERE description_core_key = ?1 "
            "UNION SELECT item_id FROM item_source WHERE core_key = ?1 ORDER BY 1",
            (core_key,),
        ).fetchall()
        return [r[0] for r in rows]

    def item_ids_by_owner_persons(self, person_ids: Iterable[str]) -> list[int]:
        """Ids (ascending) of items with an ``item_owner`` row for any of ``person_ids`` (fuzzy candidate
        bucket of 10.6; the caller filters for the exact person set)."""
        ids = sorted(set(person_ids))
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT DISTINCT item_id FROM item_owner WHERE person_id IN ({marks}) ORDER BY item_id", ids
        ).fetchall()
        return [r[0] for r in rows]

    # ----------------------------------------------------------- item_link (never deleted)
    def insert_link(
        self, from_id: int, to_id: int, kind: str, reason: str | None = None, run_id: int | None = None
    ) -> bool:
        """Insert a link once (``INSERT OR IGNORE`` on ``(from, to, kind)``); True if a row was added.

        ``possible_duplicate`` is symmetric: stored once with ``from < to`` whichever order is given
        (spec 7.2) and it needs a ``reason``. ``completed_by`` is directional (from = item whose completion
        was inferred, to = evidence holder) and is never reordered. Arguments are validated here because
        ``OR IGNORE`` would otherwise swallow a CHECK violation silently."""
        if kind not in LINK_KINDS:
            raise InternalError(f"unknown link kind {kind!r}")
        if from_id == to_id:
            raise InternalError("an item cannot be linked to itself")
        if reason is not None and reason not in LINK_REASONS:
            raise InternalError(f"unknown link reason {reason!r}")
        if kind == "possible_duplicate":
            if reason is None:
                raise InternalError("a possible_duplicate link needs a reason")
            from_id, to_id = min(from_id, to_id), max(from_id, to_id)
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO item_link (from_item_id, to_item_id, kind, reason, created_run) "
            "VALUES (?, ?, ?, ?, ?)",
            (from_id, to_id, kind, reason, run_id),
        )
        return cur.rowcount == 1

    def links_of(self, item_id: int) -> list[ItemLinkRow]:
        """Links touching the item at either end (``D?`` appears on both items)."""
        rows = self.conn.execute(
            "SELECT * FROM item_link WHERE from_item_id = ? OR to_item_id = ? "
            "ORDER BY kind, from_item_id, to_item_id",
            (item_id, item_id),
        ).fetchall()
        return [_load(ItemLinkRow, r) for r in rows]

    def list_links(self, kind: str | None = None) -> list[ItemLinkRow]:
        sql = "SELECT * FROM item_link"
        params: tuple[object, ...] = ()
        if kind is not None:
            sql += " WHERE kind = ?"
            params = (kind,)
        rows = self.conn.execute(sql + " ORDER BY kind, from_item_id, to_item_id", params).fetchall()
        return [_load(ItemLinkRow, r) for r in rows]

    # ----------------------------------------------------------- item_event (T-M5-02)
    def record_event(
        self,
        item_id: int,
        kind: str,
        before: object,
        after: object,
        run_id: int | None,
        ts: str | None = None,
    ) -> int | None:
        """Append one ``item_event``; ``before`` / ``after`` are stored as canonical JSON (None => NULL).

        ``ts`` defaults to the injected clock. Idempotent per ``(item_id, kind, run_id, before, after)``:
        a retried file after a crash cannot double-log (returns None when the event already exists). The
        de-duplication applies only when ``run_id`` is not None (an out-of-run event such as a manual
        ``done`` is never dropped)."""
        if kind not in EVENT_KINDS:
            raise InternalError(f"unknown event kind {kind!r}")
        before_json = None if before is None else to_storage_text(canonical_json(before))
        after_json = None if after is None else to_storage_text(canonical_json(after))
        if run_id is not None and self.conn.execute(
            'SELECT 1 FROM item_event WHERE item_id = ? AND kind = ? AND run_id = ? '
            'AND "before" IS ? AND "after" IS ?',
            (item_id, kind, run_id, before_json, after_json),
        ).fetchone():
            return None
        return self.insert_row(
            "item_event",
            {
                "item_id": item_id,
                "ts": ts or self.now_iso(),
                "kind": kind,
                "before": before_json,
                "after": after_json,
                "run_id": run_id,
            },
        )

    def list_events(self, item_id: int | None = None, run_id: int | None = None) -> list[ItemEventRow]:
        where: list[str] = []
        params: list[object] = []
        if item_id is not None:
            where.append("item_id = ?")
            params.append(item_id)
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        sql = 'SELECT id, item_id, ts, kind, "before", "after", run_id FROM item_event'
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self.conn.execute(sql + " ORDER BY id", params).fetchall()
        return [_load(ItemEventRow, r, before=json.loads, after=json.loads) for r in rows]

    # ----------------------------------------------------------- touch_item (T-M5-02)
    def touch_item(
        self,
        item_id: int,
        changes: dict[str, object] | None = None,
        *,
        owners: Sequence[object] | None = None,
        event_kind: str | None = None,
        run_id: int | None = None,
    ) -> dict[str, tuple[object, object]]:
        """The ONLY writer of ``action_item`` business columns after insert (``BUSINESS_COLUMNS``).

        ``changes`` maps column -> new value; ``owners`` (optional) is the complete new owner list. Values
        are compared after storage normalisation (NFC, dates as ISO text); columns whose value is equal are
        skipped. If nothing differs: no UPDATE, ``updated_at`` untouched, no event, returns ``{}`` (a no-op
        re-ingest leaves rows byte-identical, BR-15, AC-08.1). Otherwise one UPDATE sets the changed columns
        plus ``updated_at`` = the injected clock, and when ``event_kind`` is given exactly one event with
        ``before`` / ``after`` holding only the changed fields (owners under ``"owners"``) is recorded.
        Returns ``{field: (before, after)}`` of what changed (owner entries are lists of tuples)."""
        row = self.conn.execute("SELECT * FROM action_item WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise InternalError(f"unknown action item {item_id}")
        diff: dict[str, tuple[object, object]] = {}
        for column, value in (changes or {}).items():
            if column not in BUSINESS_COLUMNS:
                raise InternalError(f"{column!r} is not a business column of action_item")
            if isinstance(value, _dt.datetime | _dt.date):
                value = value.isoformat()
            elif isinstance(value, str):
                value = to_storage_text(value)
            if row[column] != value:
                diff[column] = (row[column], value)
        write = {c: new for c, (_old, new) in diff.items()}
        if write:
            self.update_row("action_item", "id", item_id, {**write, "updated_at": self.now_iso()})
        if owners is not None:
            before_owners = self._item_owner_tuples(self.list_item_owners(item_id))
            if self.set_item_owners(item_id, owners):  # bumps updated_at itself
                diff["owners"] = (before_owners, self._item_owner_tuples(self.list_item_owners(item_id)))
        if diff and event_kind is not None:
            self.record_event(
                item_id,
                event_kind,
                {c: old for c, (old, _new) in diff.items()},
                {c: new for c, (_old, new) in diff.items()},
                run_id,
            )
        return diff

    def set_manual_done(self, item_id: int, ts: str | None = None, run_id: int | None = None) -> None:
        """Mark an item manually done (``completion_origin='manual'``, ``completed_at`` = ``ts`` or now, event
        ``manual_done``). Used by tests until the ``done`` command (M6) owns the real write path."""
        row = self.get_action_item(item_id)
        if row is None:
            raise InternalError(f"unknown action item {item_id}")
        stamp = ts or self.now_iso()
        self.touch_item(item_id, {"status": "done", "completion_origin": "manual", "completed_at": stamp})
        self.record_event(item_id, "manual_done", {"status": row.status}, {"status": "done", "completed_at": stamp}, run_id)

    # ----------------------------------------------------------- ingest_warning
    def add_warning(
        self, run_id: int, code: str, message: str, *, source_file_id: int | None = None, line: int | None = None
    ) -> int:
        return self.insert_row(
            "ingest_warning",
            {"run_id": run_id, "source_file_id": source_file_id, "line": line, "code": code, "message": message},
        )

    def list_warnings(self, run_id: int | None = None, source_file_id: int | None = None) -> list[IngestWarningRow]:
        where: list[str] = []
        params: list[object] = []
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        if source_file_id is not None:
            where.append("source_file_id = ?")
            params.append(source_file_id)
        sql = "SELECT * FROM ingest_warning" + (" WHERE " + " AND ".join(where) if where else "")
        return [_load(IngestWarningRow, r) for r in self.conn.execute(sql + " ORDER BY id", params).fetchall()]
