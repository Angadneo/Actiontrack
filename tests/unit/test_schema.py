"""Schema tests for migration 0001 (T-M1-10). Spec 7.2 as amended by G-11/G-12/G-26/G-30."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from actiontrack.store.db import MIGRATIONS_DIR, Database, load_migrations, split_sql

STORE_DIR = MIGRATIONS_DIR.parent

# spec 7.2 entity name -> table name
ENTITIES = {
    "ActionItem": "action_item",
    "ItemOwner": "item_owner",
    "Person": "person",
    "PersonAlias": "person_alias",
    "Meeting": "meeting",
    "MeetingAttendee": "meeting_attendee",
    "SourceFile": "source_file",
    "ItemSource": "item_source",
    "ItemLink": "item_link",
    "ItemEvent": "item_event",
    "IngestRun": "ingest_run",
    "IngestWarning": "ingest_warning",
    "FollowupDraft": "followup_draft",
}
# columns the schema has that the spec table does not name (documented additions)
EXTRA_COLUMNS = {"ingest_warning": {"id"}, "followup_draft": set()}
# Entities whose spec row lists `id` implicitly through a surrogate key we add
SURROGATE_ID = {"ingest_warning"}


@pytest.fixture
def db(tmp_db_path):
    database = Database.open_dry_run(tmp_db_path)  # fresh in-memory latest schema
    yield database
    database.close()


@pytest.fixture
def conn(db):
    return db.conn


def columns(conn, table):
    return [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def spec_tokens(repo_root: Path) -> dict[str, set[str]]:
    text = (repo_root / "specs" / "actiontrack-spec.md").read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for line in text.splitlines():
        m = re.match(r"^\| \*\*(\w+)\*\* \| (.*) \|$", line)
        if m:
            out[m.group(1)] = set(re.findall(r"`([^`]+)`", m.group(2)))
    return out


def seed_item(conn, n=1):
    conn.execute(
        "INSERT INTO action_item (description, task_text, description_core_key, created_at, updated_at)"
        " VALUES (?, ?, 'k', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (f"d{n}", f"t{n}"),
    )
    return conn.execute("SELECT max(id) FROM action_item").fetchone()[0]


def seed_person(conn, pid="priya-nair"):
    conn.execute(
        "INSERT INTO person (id, first_name, last_name, display_name, name_key, identity_key, origin)"
        " VALUES (?, 'Priya', 'Nair', 'Priya Nair', 'priya nair', ?, 'roster')",
        (pid, pid),
    )
    return pid


def test_apply_0001_creates_all_entities(conn, repo_root):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    spec = spec_tokens(repo_root)
    assert set(ENTITIES) <= set(spec), "entity list drifted from spec 7.2"
    for table in [*ENTITIES.values(), "schema_version", "item_source_owner"]:
        assert table in tables, table
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 1


def test_spec_7_2_columns_present(conn, repo_root):
    spec = spec_tokens(repo_root)
    for entity, table in ENTITIES.items():
        cols = set(columns(conn, table))
        tokens = spec[entity]
        # every column is named in the spec row (or a documented surrogate key)
        unknown = cols - tokens - EXTRA_COLUMNS.get(table, set())
        assert not unknown, f"{table} has columns not in spec 7.2: {sorted(unknown)}"
    # and every key field the spec row names that is a column-like identifier exists in the table
    expected = {
        "action_item": "id description task_text description_core_key status completion_origin completed_at"
        " completion_evidence_source_id due_raw due_raw_other due_date due_confidence due_rule due_reason"
        " source_state created_at updated_at",
        "item_owner": "item_id ordinal owner_raw role person_id owner_status candidates provenance",
        "person": "id first_name last_name qualifier display_name name_key identity_key email origin verified",
        "person_alias": "person_id alias alias_key",
        "meeting": "id source_file_id meeting_date date_source title",
        "meeting_attendee": "meeting_id raw_name person_id email_hint",
        "source_file": "id folder_root rel_path content_sha256 size_bytes mtime status failure_code last_run_id"
        " first_seen_run last_ingested_run",
        "item_source": "id item_id source_file_id meeting_id line_start line_end raw_text section_heading"
        " checkbox_state occurrence_index block_hash present first_seen_run last_seen_run description"
        " task_text core_key due_raw due_raw_other due_date due_confidence due_rule due_reason",
        "item_link": "from_item_id to_item_id kind reason created_run",
        "item_event": 'id item_id ts kind before after run_id',
        "ingest_run": "id started_at finished_at folder_root status dry_run files_discovered files_ingested"
        " files_unchanged files_empty files_failed items_new items_merged items_updated items_unchanged"
        " possible_duplicates already_done_in_note resolved_via_attendance",
        "ingest_warning": "id run_id source_file_id line code message",
        "followup_draft": "id person_id channel item_ids_hash item_ids include_ambiguous ambiguous_item_ids"
        " handoff_path args_sha256 gmail_draft_id thread_id view_url source status created_at recorded_at",
        "item_source_owner": "item_source_id ordinal owner_raw role person_id owner_status provenance",
    }
    for table, names in expected.items():
        assert set(columns(conn, table)) == set(names.split()), table


def test_enum_checks_reject_bad_values(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO action_item (description, task_text, description_core_key, status, created_at, updated_at)"
            " VALUES ('d', 't', 'k', 'weird', 'x', 'x')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO action_item (description, task_text, description_core_key, due_confidence, created_at,"
            " updated_at) VALUES ('d', 't', 'k', 'maybe', 'x', 'x')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO person (id, first_name, display_name, name_key, identity_key, origin)"
            " VALUES ('a', 'A', 'A', 'a', 'a', 'alien')"
        )


def test_autoincrement_never_reuses_id(db):
    conn = db.conn
    ids = []
    with db.transaction():
        ids += [seed_item(conn, 1), seed_item(conn, 2)]
    with pytest.raises(RuntimeError):
        with db.transaction():
            seed_item(conn, 3)
            raise RuntimeError("fail the file")
    with db.transaction():
        ids.append(seed_item(conn, 4))
    assert ids == sorted(ids) and len(set(ids)) == len(ids) == 3
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'action_item'").fetchone()[0]
    assert seq == ids[-1]


def normalised_schema(conn) -> list[tuple]:
    rows = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return [(t, n, tn, re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", s or "")).strip()) for t, n, tn, s in rows]


def test_schema_sql_snapshot_matches_migrations(db):
    snapshot = sqlite3.connect(":memory:", isolation_level=None)
    snapshot.row_factory = sqlite3.Row
    for stmt in split_sql((STORE_DIR / "schema.sql").read_text(encoding="utf-8")):
        snapshot.execute(stmt)
    assert normalised_schema(snapshot) == normalised_schema(db.conn)
    assert [m.version for m in load_migrations()] == [1]


@pytest.mark.ac("AC-09.6")
def test_unicode_text_roundtrip(conn):
    names = ["Zoë Åström", "李雷"]
    pid = seed_person(conn)
    for i, name in enumerate(names):
        conn.execute(
            "INSERT INTO item_owner (item_id, ordinal, owner_raw, owner_status, provenance)"
            " VALUES (?, 0, ?, 'resolved', 'roster')",
            (seed_item(conn, i), name),
        )
    stored = [r[0] for r in conn.execute("SELECT owner_raw FROM item_owner ORDER BY item_id")]
    assert stored == names
    assert [s.encode("utf-8") for s in stored] == [n.encode("utf-8") for n in names]
    assert pid


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO item_owner (item_id, ordinal, owner_status, provenance)"
            " VALUES (999, 0, 'unassigned', 'none')"
        )


@pytest.mark.ac("AC-04.12")
def test_followup_draft_has_no_body_column(conn):
    banned = {"body", "subject", "email_body", "message_text"}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        assert not banned & set(columns(conn, table)), table
    # spec 7.2 IngestWarning.message is a warning text, not an email; no other table has one
    assert "message" not in set(columns(conn, "followup_draft"))
    pid = seed_person(conn)

    def insert(status="created", source="actiontrack"):
        conn.execute(
            "INSERT INTO followup_draft (person_id, channel, item_ids_hash, item_ids, source, status, created_at)"
            " VALUES (?, 'handoff', 'h', '[1]', ?, ?, 'x')",
            (pid, source, status),
        )

    good = ["pending_handoff", "created_by_host", "failed_by_host", "created", "failed", "unknown"]
    for status in good:
        insert(status)
    for bad in ["sent", "draft", "pending", ""]:
        with pytest.raises(sqlite3.IntegrityError):
            insert(bad)
    insert(source="host")
    for bad in ["google", "fake", "user"]:
        with pytest.raises(sqlite3.IntegrityError):
            insert(source=bad)


def test_item_link_symmetric_storage_check(conn):
    a, b = seed_item(conn, 1), seed_item(conn, 2)
    ins = "INSERT INTO item_link (from_item_id, to_item_id, kind, reason) VALUES (?, ?, ?, ?)"
    conn.execute(ins, (a, b, "possible_duplicate", "fuzzy"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (b, a, "possible_duplicate", "fuzzy"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (a, a, "completed_by", None))
    conn.execute(ins, (b, a, "completed_by", None))
    conn.execute(ins, (a, b, "completed_by", None))


def test_provenance_and_reason_enums(conn):
    item = seed_item(conn)
    ins = (
        "INSERT INTO item_owner (item_id, ordinal, owner_status, provenance) VALUES (?, ?, 'resolved', ?)"
    )
    for i, prov in enumerate(
        ["roster", "alias", "attendee", "attendee_in_meeting", "unique_first_name", "initial_form",
         "diacritic_fold", "provisional", "none"]
    ):
        conn.execute(ins, (item, i, prov))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (item, 50, "guess"))
    upd = "UPDATE action_item SET due_rule = ? WHERE id = ?"
    conn.execute(upd, ("weekday_next", item))
    conn.execute(upd, (None, item))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(upd, ("next_blue_moon", item))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE action_item SET due_reason = 'because' WHERE id = ?", (item,))
    conn.execute("UPDATE action_item SET due_reason = 'vague' WHERE id = ?", (item,))


def test_ingest_run_status_partial_and_counters(conn):
    counters = (
        "files_discovered files_ingested files_unchanged files_empty files_failed items_new items_merged"
        " items_updated items_unchanged possible_duplicates already_done_in_note resolved_via_attendance"
    ).split()
    assert len(counters) == 12
    for status in ["in_progress", "complete", "partial", "interrupted"]:
        conn.execute("INSERT INTO ingest_run (started_at, folder_root, status) VALUES ('x', 'f', ?)", (status,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ingest_run (started_at, folder_root, status) VALUES ('x', 'f', 'done')")
    row = conn.execute("SELECT * FROM ingest_run LIMIT 1").fetchone()
    for name in counters:
        assert row[name] == 0, name
    assert row["dry_run"] == 0


def test_source_file_unique_and_restrict(conn):
    ins = "INSERT INTO source_file (folder_root, rel_path, status) VALUES ('r', 'a.md', 'ok')"
    conn.execute(ins)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins)
    conn.execute(
        "INSERT INTO meeting (source_file_id, date_source) VALUES (1, 'none')"
    )
    with pytest.raises(sqlite3.IntegrityError):  # ON DELETE RESTRICT
        conn.execute("DELETE FROM source_file WHERE id = 1")


def test_list_indexes_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {
        "idx_action_item_status_due",
        "idx_item_owner_person",
        "idx_item_source_item",
        "idx_item_source_file_present",
    } <= names
