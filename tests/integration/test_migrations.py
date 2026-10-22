"""Migration runner, schema_version and backup policy (T-M1-12; NFR-REL-04)."""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from actiontrack.errors import EnvError, InternalError
from actiontrack.store.db import MIGRATIONS_DIR, Database, load_migrations

from tests.conftest import REPO_ROOT

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "migrations_test"


def make_dir(tmp_path: Path, name: str, *extra: tuple[str, str]) -> Path:
    """Migrations folder = real 0001 + fixture files (source name, published name)."""
    folder = tmp_path / name
    folder.mkdir()
    shutil.copy(MIGRATIONS_DIR / "0001_initial.sql", folder / "0001_initial.sql")
    for src, dest in extra:
        shutil.copy(FIXTURE_DIR / src, folder / dest)
    return folder


def tables(path: Path) -> set[str]:
    c = sqlite3.connect(path)
    try:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        c.close()


def versions(path: Path) -> list[int]:
    c = sqlite3.connect(path)
    try:
        return [r[0] for r in c.execute("SELECT version FROM schema_version ORDER BY version")]
    finally:
        c.close()


def test_fresh_db_created_at_latest_version_no_bak(tmp_db_path):
    with Database.open(tmp_db_path) as db:
        assert db.version == 1
        row = db.conn.execute("SELECT version, name, applied_at FROM schema_version").fetchone()
        assert (row["version"], row["name"]) == (1, "initial")
        assert row["applied_at"].endswith("Z")
    assert [p.name for p in tmp_db_path.parent.iterdir() if ".bak" in p.name] == []


def test_applied_at_uses_injected_clock(tmp_db_path):
    from datetime import UTC, datetime

    from actiontrack.clock import FixedClock

    clock = FixedClock(datetime(2026, 10, 22, 9, 30, tzinfo=UTC))
    with Database.open(tmp_db_path, clock=clock) as db:
        assert db.conn.execute("SELECT applied_at FROM schema_version").fetchone()[0] == "2026-10-22T09:30:00Z"


def test_newer_schema_refused_exit4(tmp_db_path):
    Database.open(tmp_db_path).close()
    c = sqlite3.connect(tmp_db_path)
    c.execute("INSERT INTO schema_version (version, name, applied_at) VALUES (999, 'future', 'x')")
    c.commit()
    c.close()
    before = tmp_db_path.read_bytes()
    for opener in (
        lambda: Database.open(tmp_db_path),
        lambda: Database.open(tmp_db_path, readonly=True),
        lambda: Database.open_dry_run(tmp_db_path),
    ):
        with pytest.raises(EnvError) as info:
            opener()
        assert info.value.exit_code == 4
        assert "newer" in info.value.what and "upgrade" in str(info.value)
    assert tmp_db_path.read_bytes() == before


def test_older_schema_migrates_after_backup(tmp_path, tmp_db_path):
    Database.open(tmp_db_path).close()  # version 1
    newer = make_dir(tmp_path, "m3", ("0002_add_note.sql", "0002_add_note.sql"), ("0003_good.sql", "0003_good.sql"))
    with Database.open(tmp_db_path, migrations_dir=newer) as db:
        assert db.version == 3
    assert {"test_note", "test_extra"} <= tables(tmp_db_path)
    bak = Path(f"{tmp_db_path}.bak")
    assert bak.exists()
    assert versions(bak) == [1]
    assert "test_note" not in tables(bak)
    # the backup is a usable database at the old version
    with Database.open(bak, readonly=True) as old:
        assert old.version == 1 or old.in_memory


def test_older_schema_readonly_and_dry_run_never_touch_file(tmp_path, tmp_db_path):
    Database.open(tmp_db_path).close()
    newer = make_dir(tmp_path, "m2", ("0002_add_note.sql", "0002_add_note.sql"))
    before = tmp_db_path.read_bytes()
    listing = sorted(os.listdir(tmp_db_path.parent))
    with Database.open(tmp_db_path, readonly=True, migrations_dir=newer) as ro:
        assert ro.in_memory and ro.version == 2
    with Database.open_dry_run(tmp_db_path, migrations_dir=newer) as dry:
        assert dry.version == 2
    assert tmp_db_path.read_bytes() == before
    assert sorted(os.listdir(tmp_db_path.parent)) == listing


def test_failed_migration_rolls_back_exit4(tmp_path, tmp_db_path):
    Database.open(tmp_db_path).close()
    good = make_dir(tmp_path, "m2", ("0002_add_note.sql", "0002_add_note.sql"))
    Database.open(tmp_db_path, migrations_dir=good).close()
    assert versions(tmp_db_path) == [1, 2]
    bad = make_dir(tmp_path, "m3bad", ("0002_add_note.sql", "0002_add_note.sql"), ("0003_bad.sql", "0003_bad.sql"))
    with pytest.raises(EnvError) as info:
        Database.open(tmp_db_path, migrations_dir=bad)
    assert info.value.exit_code == 4
    assert "0003_bad.sql" in info.value.what and "test_note" in info.value.what
    assert versions(tmp_db_path) == [1, 2]
    assert "test_partial" not in tables(tmp_db_path)  # first statement of 0003 rolled back too


def test_migration_file_gap_detected(tmp_path):
    folder = make_dir(tmp_path, "gap", ("0003_good.sql", "0003_good.sql"))
    with pytest.raises(InternalError, match="gap"):
        load_migrations(folder)
    with pytest.raises(InternalError):
        Database.open(tmp_path / "x.db", migrations_dir=folder)
    assert not (tmp_path / "x.db").exists()


def test_migration_duplicate_number_detected(tmp_path):
    folder = make_dir(tmp_path, "dup", ("0002_add_note.sql", "0002_a.sql"), ("0003_good.sql", "0002_b.sql"))
    with pytest.raises(InternalError, match="duplicate"):
        load_migrations(folder)


def test_empty_migrations_folder_is_internal_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(InternalError):
        load_migrations(empty)


def test_bundled_migrations_are_valid():
    migs = load_migrations()
    assert [m.version for m in migs] == list(range(1, len(migs) + 1))


def test_snapshot_equals_after_all_migrations(tmp_db_path):
    from tests.unit.test_schema import STORE_DIR, normalised_schema
    from actiontrack.store.db import split_sql

    snap = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in split_sql((STORE_DIR / "schema.sql").read_text(encoding="utf-8")):
        snap.execute(stmt)
    with Database.open(tmp_db_path) as db:
        assert normalised_schema(snap) == normalised_schema(db.conn)


def test_backup_not_overwritten_on_second_migration(tmp_path, tmp_db_path):
    Database.open(tmp_db_path).close()  # v1
    m2 = make_dir(tmp_path, "m2", ("0002_add_note.sql", "0002_add_note.sql"))
    Database.open(tmp_db_path, migrations_dir=m2).close()  # v1 -> v2, backup .bak
    bak = Path(f"{tmp_db_path}.bak")
    bak_bytes = bak.read_bytes()
    m3 = make_dir(tmp_path, "m3", ("0002_add_note.sql", "0002_add_note.sql"), ("0003_good.sql", "0003_good.sql"))
    Database.open(tmp_db_path, migrations_dir=m3).close()  # v2 -> v3, must not overwrite .bak
    assert bak.read_bytes() == bak_bytes
    bak2 = Path(f"{tmp_db_path}.bak.2")
    assert bak2.exists() and versions(bak2) == [1, 2]
    # an unmigrated open makes no new backup
    Database.open(tmp_db_path, migrations_dir=m3).close()
    assert sorted(p.name for p in tmp_db_path.parent.iterdir() if ".bak" in p.name) == [
        f"{tmp_db_path.name}.bak",
        f"{tmp_db_path.name}.bak.2",
    ]
