"""T-M5-05: per-file transaction engine (hash skip, empty/failed classification, isolation, faults).

Drives ``services.ingest.run_ingest`` (the interim driver) with the real parser, registry and database.
Matching does not exist yet: every parsed item becomes its own item.
"""

from __future__ import annotations

import dataclasses
import hashlib
import sqlite3
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.config import Limits
from actiontrack.services import ingest as ing
from actiontrack.store.db import Database

from tests.integration.ingest_support import (
    dump_tables,
    roster_cfg,
    run_ctx,
    simple_note,
    write_note,
)

BINARY = b"\x00\x01\x02 not text \x00"


class Crash(BaseException):
    """Simulated hard kill (not an ``Exception``: the engine must not swallow it)."""


def ingest(db_path, folder, *, cfg=None, hook=None, **kw):
    return ing.run_ingest(
        db_path, folder, cfg=cfg or roster_cfg(), run_ctx=run_ctx(), fault_hook=hook, **kw
    )


def open_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def count(db_path: Path, table: str, where: str = "1=1") -> int:
    conn = open_ro(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
    finally:
        conn.close()


def dump(db_path: Path):
    conn = open_ro(db_path)
    try:
        return dump_tables(conn)
    finally:
        conn.close()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    return root


def three_notes(folder):
    write_note(folder, "2026-09-01-a.md", simple_note("2026-09-01", "A"))
    write_note(folder, "2026-09-02-b.md", simple_note("2026-09-02", "B", ("- [ ] Rahul Mehta to book the room",)))
    write_note(folder, "2026-09-03-c.md", simple_note("2026-09-03", "C", ("- [x] Anita Desai to ship it",)))


@pytest.mark.ac("AC-08.1")
@pytest.mark.sc("SC-25")
def test_second_run_all_unchanged_and_business_tables_identical(tmp_db_path, folder):
    three_notes(folder)
    first = ingest(tmp_db_path, folder)
    assert (first.counters.files_discovered, first.counters.files_ingested) == (3, 3)
    assert first.run_row.status == "complete" and first.exit_code == 0
    before = dump(tmp_db_path)
    second = ingest(tmp_db_path, folder)
    c = second.counters
    assert (c.files_discovered, c.files_ingested, c.files_unchanged, c.files_empty, c.files_failed) == (3, 0, 3, 0, 0)
    assert (c.items_new, c.items_merged, c.items_updated) == (0, 0, 0)
    assert c.invariant_holds()
    assert dump(tmp_db_path) == before  # AC-08.1: dump diff is empty (rows incl. updated_at untouched)
    assert second.run_row.id != first.run_row.id


@pytest.mark.ac("AC-08.1", "AC-01.8")
@pytest.mark.sc("SC-09", "SC-25")
def test_3_readable_1_empty_1_binary_twice_exit_5_both_times(tmp_db_path, folder):
    for i in (1, 2, 3):
        write_note(folder, f"2026-09-0{i}-n.md", simple_note(f"2026-09-0{i}", f"N{i}"))
    (folder / "empty.md").write_bytes(b"")
    (folder / "scratch.md").write_bytes(BINARY)
    first = ingest(tmp_db_path, folder)
    c = first.counters
    assert (c.files_discovered, c.files_ingested, c.files_empty, c.files_failed) == (5, 3, 1, 1)
    assert first.exit_code == 5 and first.run_row.status == "partial"
    before = dump(tmp_db_path)
    second = ingest(tmp_db_path, folder)
    c = second.counters
    assert (c.files_discovered, c.files_unchanged, c.files_empty, c.files_failed, c.files_ingested) == (5, 3, 1, 1, 0)
    assert c.invariant_holds()
    assert second.exit_code == 5  # the failed file is re-classified, never "unchanged"
    assert dump(tmp_db_path) == before
    # the second run still reports the failure and the empty file (run-scoped warning rows)
    conn = open_ro(tmp_db_path)
    try:
        codes_seen = [r[0] for r in conn.execute(
            "SELECT code FROM ingest_warning WHERE run_id = ? ORDER BY id", (second.run_row.id,))]
    finally:
        conn.close()
    assert codes.E_BINARY in codes_seen and codes.W_EMPTY_FILE in codes_seen


@pytest.mark.ac("AC-01.8")
def test_binary_among_good_files_is_isolated(tmp_db_path, folder):
    for i in range(1, 6):
        write_note(folder, f"2026-09-0{i}-n.md", simple_note(f"2026-09-0{i}", f"N{i}"))
    (folder / "scratch.md").write_bytes(BINARY)
    res = ingest(tmp_db_path, folder)
    assert res.counters.files_ingested == 5 and res.counters.files_failed == 1
    (bad,) = [o for o in res.outcomes if o.result == "failed"]
    assert bad.rel_path == "scratch.md" and bad.failure_code == codes.E_BINARY
    conn = open_ro(tmp_db_path)
    try:
        row = conn.execute("SELECT * FROM source_file WHERE rel_path = 'scratch.md'").fetchone()
        assert row["status"] == "failed" and row["failure_code"] == codes.E_BINARY
        assert conn.execute("SELECT COUNT(*) FROM item_source WHERE source_file_id = ?", (row["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM meeting WHERE source_file_id = ?", (row["id"],)).fetchone()[0] == 0
    finally:
        conn.close()
    assert count(tmp_db_path, "action_item") == 5


@pytest.mark.ac("AC-08.7")
def test_ok_file_replaced_by_binary_keeps_items_and_is_not_missing(tmp_db_path, folder):
    write_note(folder, "a.md", simple_note("2026-09-01", "A"))
    ingest(tmp_db_path, folder)
    items_before = dump(tmp_db_path)["action_item"]
    original = (folder / "a.md").read_bytes()
    (folder / "a.md").write_bytes(BINARY)
    res = ingest(tmp_db_path, folder)
    assert res.exit_code == 5 and res.counters.files_failed == 1
    tables = dump(tmp_db_path)
    assert tables["action_item"] == items_before  # untouched, incl. source_state='present'
    conn = open_ro(tmp_db_path)
    try:
        sf = conn.execute("SELECT * FROM source_file WHERE rel_path = 'a.md'").fetchone()
        assert sf["status"] == "failed" and sf["failure_code"] == codes.E_BINARY
        assert conn.execute("SELECT COUNT(*) FROM item_source WHERE present = 1").fetchone()[0] == 1
        assert conn.execute("SELECT source_state FROM action_item").fetchone()[0] == "present"
        old_hash = sf["content_sha256"]
    finally:
        conn.close()
    assert old_hash == hashlib.sha256(original).hexdigest()  # row still describes the data that is in the DB
    # restoring the identical content revives the row without re-ingesting anything
    (folder / "a.md").write_bytes(original)
    res3 = ingest(tmp_db_path, folder)
    assert res3.counters.files_unchanged == 1 and res3.counters.files_ingested == 0 and res3.exit_code == 0
    conn = open_ro(tmp_db_path)
    try:
        sf = conn.execute("SELECT status, failure_code FROM source_file WHERE rel_path = 'a.md'").fetchone()
        assert (sf["status"], sf["failure_code"]) == ("ok", None)
    finally:
        conn.close()
    assert dump(tmp_db_path)["action_item"] == items_before


@pytest.mark.ac("AC-08.1")
def test_ok_file_turned_empty_keeps_data(tmp_db_path, folder):
    write_note(folder, "a.md", simple_note("2026-09-01", "A"))
    ingest(tmp_db_path, folder)
    items_before = dump(tmp_db_path)["action_item"]
    (folder / "a.md").write_bytes(b"  \n")
    res = ingest(tmp_db_path, folder)
    assert res.counters.files_empty == 1 and res.exit_code == 0
    assert dump(tmp_db_path)["action_item"] == items_before
    assert count(tmp_db_path, "source_file", "status = 'empty'") == 1


def test_exception_inside_file_two_rolls_that_file_back_only(tmp_db_path, folder):
    three_notes(folder)

    def hook(name: str) -> None:
        if name == "file_written:2026-09-02-b.md":
            raise RuntimeError("forced failure in file 2")

    res = ingest(tmp_db_path, folder, hook=hook)
    assert res.counters.files_ingested == 2 and res.counters.files_failed == 1
    assert res.exit_code == 5 and res.run_row.status == "partial"
    (bad,) = [o for o in res.outcomes if o.result == "failed"]
    assert bad.rel_path == "2026-09-02-b.md" and bad.failure_code == codes.E_INTERNAL_PARSE
    assert "RuntimeError" in (bad.traceback or "")
    conn = open_ro(tmp_db_path)
    try:
        sf = conn.execute("SELECT * FROM source_file WHERE rel_path = '2026-09-02-b.md'").fetchone()
        assert sf["status"] == "failed" and sf["failure_code"] == codes.E_INTERNAL_PARSE
        assert sf["last_ingested_run"] is None
        for table in ("meeting", "meeting_attendee", "item_source"):
            col = "source_file_id" if table != "meeting_attendee" else None
            if col:
                assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (sf["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM item_source").fetchone()[0] == 2  # files 1 and 3 only
        assert conn.execute("SELECT COUNT(*) FROM action_item").fetchone()[0] == 2
    finally:
        conn.close()
    # the next clean run ingests the repaired file like a new one
    again = ingest(tmp_db_path, folder)
    assert (again.counters.files_ingested, again.counters.files_unchanged, again.counters.files_failed) == (1, 2, 0)
    assert count(tmp_db_path, "action_item") == 3


def test_fault_before_commit_is_a_file_failure_and_leaves_no_rows(tmp_db_path, folder):
    three_notes(folder)
    seen = {"n": 0}

    def hook(name: str) -> None:
        if name == "before_commit":
            seen["n"] += 1
            if seen["n"] == 3:  # the hook is installed after the run row and roster commit: 3rd commit = 3rd file
                raise RuntimeError("before_commit fault")

    res = ingest(tmp_db_path, folder, hook=hook)
    failed = [o.rel_path for o in res.outcomes if o.result == "failed"]
    assert len(failed) == 1
    conn = open_ro(tmp_db_path)
    try:
        bad = conn.execute("SELECT id FROM source_file WHERE rel_path = ?", (failed[0],)).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM item_source WHERE source_file_id = ?", (bad["id"],)).fetchone()[0] == 0
    finally:
        conn.close()


def _projection(db_path: Path):
    """Business state without run ids (a resumed run differs from an uninterrupted one only in those)."""
    conn = open_ro(db_path)
    try:
        tables = dump_tables(conn)
        run_cols = {"first_seen_run", "last_seen_run", "last_run_id", "last_ingested_run", "run_id", "created_run"}
        out = {}
        for table, rows in tables.items():
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            keep = [i for i, c in enumerate(cols) if c not in run_cols and c != "completion_evidence_source_id"]
            out[table] = sorted(tuple(r[i] for i in keep) for r in rows)
        return out
    finally:
        conn.close()


@pytest.mark.ac("AC-08.5", "AC-08.9")
@pytest.mark.sc("SC-28")
def test_crash_after_file_k_then_rerun_equals_uninterrupted_run(tmp_path, folder):
    three_notes(folder)
    clean = tmp_path / "clean.db"
    ingest(clean, folder)

    crashed = tmp_path / "crashed.db"

    def hook(name: str) -> None:
        if name == "after_file:2":
            raise Crash

    with pytest.raises(Crash):
        ingest(crashed, folder, hook=hook)
    conn = open_ro(crashed)
    try:
        runs = conn.execute("SELECT id, status FROM ingest_run").fetchall()
        assert [(r["status"]) for r in runs] == ["in_progress"]  # a kill leaves the run unfinished
        assert conn.execute("SELECT COUNT(*) FROM source_file WHERE status = 'ok'").fetchone()[0] == 2
    finally:
        conn.close()
    resumed = ingest(crashed, folder)
    assert [r.run_id for r in resumed.run.interrupted] == [runs[0]["id"]]  # previous run reported interrupted
    assert (resumed.counters.files_unchanged, resumed.counters.files_ingested) == (2, 1)
    assert resumed.run_row.status == "complete"
    assert _projection(crashed) == _projection(clean)  # no duplicate items, same final state
    statuses = [r[0] for r in open_ro(crashed).execute("SELECT status FROM ingest_run ORDER BY id")]
    assert statuses == ["interrupted", "complete"]


@pytest.mark.ac("AC-01.11")
def test_source_files_are_never_modified(tmp_db_path, folder):
    three_notes(folder)
    (folder / "scratch.md").write_bytes(BINARY)
    before = {p.name: (sha(p), p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}
    ingest(tmp_db_path, folder)
    ingest(tmp_db_path, folder)
    after = {p.name: (sha(p), p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}
    assert before == after
    # size and mtime are recorded from stat
    conn = open_ro(tmp_db_path)
    try:
        row = conn.execute("SELECT size_bytes, mtime FROM source_file WHERE rel_path = '2026-09-01-a.md'").fetchone()
    finally:
        conn.close()
    assert row["size_bytes"] == before["2026-09-01-a.md"][1]
    assert row["mtime"] and row["mtime"].endswith("Z")


@pytest.mark.ac("AC-09.5")
def test_file_over_max_file_bytes_reports_too_large(tmp_db_path, folder):
    write_note(folder, "ok.md", simple_note("2026-09-01", "Small"))
    write_note(folder, "big.md", simple_note("2026-09-02", "Big", tuple(f"- [ ] Rahul Mehta to do thing {i}" for i in range(60))))
    cfg = dataclasses.replace(roster_cfg(), limits=Limits(max_file_bytes=1024))
    assert (folder / "big.md").stat().st_size > 1024 > (folder / "ok.md").stat().st_size
    res = ingest(tmp_db_path, folder, cfg=cfg)
    (bad,) = [o for o in res.outcomes if o.result == "failed"]
    assert bad.rel_path == "big.md" and bad.failure_code == codes.E_TOO_LARGE
    assert res.counters.files_ingested == 1 and res.exit_code == 5


def test_unchanged_file_issues_no_write_statements(tmp_db_path, folder, monkeypatch):
    three_notes(folder)
    ingest(tmp_db_path, folder)
    statements: list[str] = []
    orig_open = Database.open

    def traced_open(*a, **k):
        db = orig_open(*a, **k)
        db.conn.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(Database, "open", staticmethod(traced_open))
    res = ingest(tmp_db_path, folder)
    assert res.counters.files_unchanged == 3
    writes = [s for s in statements if s.strip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}]
    # the only writes are run bookkeeping (ingest_run insert/update): nothing touches any business table
    assert writes and all("ingest_run" in w for w in writes), writes


def test_changed_file_is_routed_to_the_reingest_seam(tmp_db_path, folder):
    write_note(folder, "a.md", simple_note("2026-09-01", "A"))
    ingest(tmp_db_path, folder)
    write_note(folder, "a.md", simple_note("2026-09-01", "A", ("- [ ] Priya Nair to send the quote by Friday", "- [ ] Rahul Mehta to book it")))
    calls: list[tuple[str, str]] = []

    def reingest(env, entry, ordinal):
        calls.append((entry.rel_path, entry.file_class.value))
        return ing.FileWrite(source_file_id=entry.previous.id)

    res = ingest(tmp_db_path, folder, reingest=reingest)
    assert calls == [("a.md", "changed")]
    assert res.counters.files_ingested == 1


def test_changed_file_without_handler_is_isolated_not_lost(tmp_db_path, folder):
    """Until T-M5-12 lands a changed file is reported failed and its previous data stays (10.7.8 spirit)."""
    write_note(folder, "a.md", simple_note("2026-09-01", "A"))
    ingest(tmp_db_path, folder)
    before = dump(tmp_db_path)["action_item"]
    write_note(folder, "a.md", simple_note("2026-09-01", "A", ("- [ ] Rahul Mehta to book it",)))
    res = ingest(tmp_db_path, folder)
    assert res.counters.files_failed == 1 and res.exit_code == 5
    assert dump(tmp_db_path)["action_item"] == before


def test_bad_folder_aborts_before_any_write(tmp_path):
    db_path = tmp_path / "x.db"
    from actiontrack.errors import EnvError

    with pytest.raises(EnvError):
        ingest(db_path, tmp_path / "does-not-exist")
    assert not db_path.exists()


def test_empty_and_failed_rows_are_not_rewritten_when_nothing_changed(tmp_db_path, folder):
    (folder / "empty.md").write_bytes(b"")
    (folder / "scratch.md").write_bytes(BINARY)
    write_note(folder, "a.md", simple_note())
    first = ingest(tmp_db_path, folder)
    conn = open_ro(tmp_db_path)
    try:
        rows1 = [tuple(r) for r in conn.execute("SELECT * FROM source_file ORDER BY id")]
    finally:
        conn.close()
    second = ingest(tmp_db_path, folder)
    conn = open_ro(tmp_db_path)
    try:
        rows2 = [tuple(r) for r in conn.execute("SELECT * FROM source_file ORDER BY id")]
    finally:
        conn.close()
    assert rows1 == rows2 and first.run_row.id != second.run_row.id
