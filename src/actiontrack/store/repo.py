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
Re-exported from ``actiontrack.domain.models``: ``format_item_id``, ``parse_item_id``.
"""

from __future__ import annotations

import sqlite3
import unicodedata

from actiontrack.domain.models import format_item_id, parse_item_id
from actiontrack.errors import InternalError
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
