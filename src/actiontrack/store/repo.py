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
    ``upsert_alias(person_id, alias, alias_key, auto=False)``
    ``persist_registry(registry) -> {"inserted", "updated", "aliases"}``  DB rows := registry
    ``upsert_roster(registry, roster) -> tuple[Person, ...]``  start of every non-dry ingest
    ``load_registry(roster=None) -> PeopleRegistry``  rebuild K from DB rows (+ current roster)
    ``insert_attendees(meeting_id, [(raw_name, person_id, email_hint)]) -> int`` / ``list_attendees``
    ``merge_person(provisional_id, into_id, *, alias=None, alias_key=None)``  repoint + alias + delete
    Pair with ``resolution.people.file_transaction(db, registry)`` for rollback of registry state.
Re-exported from ``actiontrack.domain.models``: ``format_item_id``, ``parse_item_id``.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import unicodedata
from collections.abc import Iterable

from actiontrack.config import Roster
from actiontrack.domain.models import Person, PersonOrigin, format_item_id, parse_item_id
from actiontrack.errors import InternalError
from actiontrack.resolution.people import PeopleRegistry
from actiontrack.store.db import Database

__all__ = [
    "VERBATIM_COLUMNS",
    "Repo",
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


class Repo:
    def __init__(self, db: Database) -> None:
        self.db = db
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
            "SELECT auto FROM person_alias WHERE person_id = ? AND alias_key = ?",
            (person_id, to_storage_text(alias_key)),
        ).fetchone()
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
