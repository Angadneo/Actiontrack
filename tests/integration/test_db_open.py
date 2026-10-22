"""Database.open, transactions, error mapping (T-M1-11; NFR-REL-01, AC-01.10 prerequisite)."""

from __future__ import annotations

import hashlib
import os
import sqlite3

import pytest

from actiontrack.errors import EnvError, ExitCode, InternalError, NotFoundError
from actiontrack.store.db import BUSY_TIMEOUT_MS, Database, map_sqlite_error


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_person(conn, pid="a-b"):
    conn.execute(
        "INSERT INTO person (id, first_name, display_name, name_key, identity_key, origin)"
        " VALUES (?, 'A', 'A B', 'a b', ?, 'attendee')",
        (pid, pid),
    )


def test_open_creates_db_and_parent_at_latest_version(tmp_path):
    path = tmp_path / "nested" / ".actiontrack" / "t.db"
    with Database.open(path) as db:
        assert path.exists()
        assert db.version == 1
        assert db.conn.row_factory is sqlite3.Row
        assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db.conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
        assert db.conn.execute("PRAGMA encoding").fetchone()[0] == "UTF-8"
        assert db.conn.isolation_level is None
        assert db.conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS


def test_open_create_false_missing_is_not_found(tmp_path):
    with pytest.raises(NotFoundError):
        Database.open(tmp_path / "nope.db", create=False)
    with pytest.raises(NotFoundError):
        Database.open(tmp_path / "nope.db", readonly=True)
    assert not (tmp_path / "nope.db").exists()


def test_foreign_keys_on_for_every_connection(tmp_db_path):
    rw = Database.open(tmp_db_path)
    ro = Database.open(tmp_db_path, readonly=True)
    mem = Database.open_dry_run(tmp_db_path)
    copy = rw.in_memory_copy()
    for db in (rw, ro, mem, copy):
        assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        db.close()


def test_rollback_on_exception_leaves_no_rows(tmp_db_path):
    with Database.open(tmp_db_path) as db:
        with pytest.raises(ValueError):
            with db.transaction() as conn:
                add_person(conn)
                raise ValueError("boom")
        assert db.conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 0
        with db.transaction() as conn:
            add_person(conn)
        assert db.conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 1
        assert not db.conn.in_transaction


def test_nested_transaction_is_an_error(tmp_db_path):
    with Database.open(tmp_db_path) as db:
        with pytest.raises(InternalError):
            with db.transaction():
                with db.transaction():
                    pass
        assert not db.conn.in_transaction
        with db.transaction():  # usable again afterwards
            pass


def test_readonly_database_refuses_transactions(tmp_db_path):
    Database.open(tmp_db_path).close()
    with Database.open(tmp_db_path, readonly=True) as db:
        with pytest.raises(InternalError):
            with db.transaction():
                pass


def test_fault_hook_raises_inside_transaction_rolls_back(tmp_db_path):
    with Database.open(tmp_db_path) as db:
        def hook(name):
            if name == "after_file_1":
                raise RuntimeError("injected")

        db.fault_hook = hook
        with pytest.raises(RuntimeError, match="injected"):
            with db.transaction() as conn:
                add_person(conn)
                db.fault("after_file_1")
        assert db.conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 0
        # the built-in before_commit point also rolls back
        db.fault_hook = lambda name: (_ for _ in ()).throw(RuntimeError("late")) if name == "before_commit" else None
        with pytest.raises(RuntimeError, match="late"):
            with db.transaction() as conn:
                add_person(conn)
        assert db.conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 0
        db.fault_hook = None
        db.fault("anything")  # no hook is a no-op


def test_busy_database_maps_to_exit4(tmp_db_path):
    Database.open(tmp_db_path).close()
    blocker = sqlite3.connect(tmp_db_path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with Database.open(tmp_db_path.parent / "other.db") as other:  # unrelated DB unaffected
            assert other.version == 1
        with pytest.raises(EnvError) as info:
            db = Database.open(tmp_db_path)
            try:
                with db.transaction():
                    pass
            finally:
                db.close()
        assert info.value.exit_code == ExitCode.ENVIRONMENT
        assert "locked" in info.value.what
        assert str(tmp_db_path) in str(info.value)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_map_sqlite_error_messages(tmp_path):
    p = tmp_path / "x.db"
    cases = {
        "database is locked": "locked by another actiontrack process",
        "attempt to write a readonly database": "read-only",
        "unable to open database file": "cannot open",
        "file is not a database": "corrupt",
        "database disk image is malformed": "corrupt",
        "disk I/O error": "database error",
    }
    for raw, expected in cases.items():
        err = map_sqlite_error(sqlite3.OperationalError(raw), p)
        assert isinstance(err, EnvError) and err.exit_code == 4
        assert expected in err.what
        assert err.where == str(p)


def test_corrupt_db_file_exit4(tmp_path):
    bad = tmp_path / "corrupt.db"
    data = os.urandom(8192)
    bad.write_bytes(data)
    for kwargs in ({}, {"readonly": True}):
        with pytest.raises(EnvError) as info:
            Database.open(bad, **kwargs)
        assert info.value.exit_code == 4
        assert str(bad) in str(info.value)
    with pytest.raises(EnvError):
        Database.open_dry_run(bad)
    assert bad.read_bytes() == data


def test_foreign_sqlite_db_refused_exit4(tmp_path):
    other = tmp_path / "other.db"
    c = sqlite3.connect(other)
    c.execute("CREATE TABLE unrelated (x)")
    c.commit()
    c.close()
    before = sha(other)
    with pytest.raises(EnvError) as info:
        Database.open(other)
    assert "not an actiontrack database" in info.value.what
    assert sha(other) == before


def test_not_a_directory_parent_exit4(tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    with pytest.raises(EnvError) as info:
        Database.open(blocker / "sub" / "t.db")
    assert info.value.exit_code == 4
    with pytest.raises(EnvError):
        Database.open(blocker / "t.db")


@pytest.mark.ac("AC-01.10")
def test_open_existing_db_readonly_does_not_modify_bytes(tmp_db_path):
    with Database.open(tmp_db_path) as db, db.transaction() as conn:
        add_person(conn)
    before, listing = sha(tmp_db_path), sorted(os.listdir(tmp_db_path.parent))
    with Database.open(tmp_db_path, readonly=True) as ro:
        assert ro.conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 1
        assert sorted(os.listdir(tmp_db_path.parent)) == listing
        with pytest.raises(sqlite3.OperationalError):
            ro.conn.execute("DELETE FROM person")
    assert sha(tmp_db_path) == before
    assert sorted(os.listdir(tmp_db_path.parent)) == listing


def test_in_memory_copy_is_independent(tmp_db_path):
    with Database.open(tmp_db_path) as db:
        with db.transaction() as conn:
            add_person(conn, "p-one")
        before = sha(tmp_db_path)
        copy = db.in_memory_copy()
        assert copy.in_memory and copy.path is None
        with copy.transaction() as conn:
            add_person(conn, "p-two")
        assert copy.conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 2
        assert db.conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 1
        copy.close()
    assert sha(tmp_db_path) == before
