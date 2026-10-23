"""SQLite connection, transactions, writer lock, migrations (T-M1-11, T-M1-12).

This package (``actiontrack.store``) is the ONLY place that imports ``sqlite3`` or contains SQL.

Public API
----------
``Database.open(path, *, create=True, readonly=False, lock=False, clock=None, migrations_dir=None)``
    Open (and, for a writer, create + migrate) the database.

    * writer (default): ``create=True`` creates the parent directory (0700 on POSIX; Windows relies on
      the user-profile ACL, SEC-04) and the file (0600 on POSIX). ``create=False`` with a missing file
      raises ``NotFoundError`` (exit 3). ``lock=True`` first takes the process-level writer lock
      ``<db>.lock`` (see ``store/lock.py``); failure raises ``EnvError`` (exit 4, "another actiontrack
      process is using this database") before the DB is touched. EVERY command that writes
      (``ingest``, ``done``, ``followup`` row writes, ``followup --record-draft``) must pass
      ``lock=True`` and keep the Database open for the whole command.
    * ``readonly=True``: connection with ``mode=ro``; never creates or modifies a file, never takes
      the lock (list, declined followup). Missing file => ``NotFoundError``. A DB older than the code
      is NOT migrated on disk: the caller gets an in-memory migrated copy (``db.in_memory`` is True).
    * A DB newer than the bundled migrations is refused (``EnvError`` exit 4).
``Database.open_dry_run(path, *, clock=None, migrations_dir=None)``
    In-memory database for ``--dry-run``: a ``sqlite3.backup`` copy of the file if it exists
    (migrated in memory if older), else a fresh latest-schema DB. Creates NO file, directory,
    ``-journal``, ``.bak`` or lock (FR-01.10, 10.7.7).
``db.in_memory_copy() -> Database``   in-memory copy of this (open) database, migrated if needed.
``db.conn``                  ``sqlite3.Connection`` (``isolation_level=None``, ``sqlite3.Row`` rows,
                              ``foreign_keys=ON``, UTF-8). Use it only through ``repo.py`` code.
``db.transaction()``         context manager: ``BEGIN IMMEDIATE`` ... ``COMMIT`` on success, ROLLBACK on
                              any exception (re-raised). Nested use or use on a read-only database
                              raises ``InternalError``. One transaction per ingested file (NFR-REL-01).
``db.fault_hook``            ``None`` or ``callable(name)``; tests set it to inject failures.
``db.fault(name)``           call at named points in services; the transaction helper itself fires
                              ``"before_commit"`` just before COMMIT (AC-08.5 fault injection).
``db.can_mark_interrupted()`` True only if this process holds the writer lock or can acquire it.
``db.version``               applied schema version (int). ``db.close()``; context manager.
``migrate(conn, migrations, *, db_path=None, clock=None) -> list[int]``  apply pending migrations.
``load_migrations(directory=None) -> list[Migration]``   discover ``NNNN_name.sql`` (gap/duplicate
                              => ``InternalError``). ``MIGRATIONS_DIR`` is the bundled folder.
``split_sql(text) -> list[str]``  safe statement splitter (``sqlite3.complete_statement``).
``map_sqlite_error(exc, path) -> EnvError``  sqlite3 error -> exit-4 error naming the path.
``DbLocked(EnvError)``       typed exit-4 error for SQLite "database is locked/busy" after the busy timeout
                              (T-M5-04); ``map_sqlite_error`` returns it for that case. Still an ``EnvError``.
``BUSY_TIMEOUT_MS = 2000``   fixed, not a config key.

Journal-mode decision (plan 2.2, measured on Windows 10, Python 3.12.10, SQLite 3.49): DELETE with
``synchronous=FULL``.  Measurements (throwaway script, 1000-row DB, results identical in ``WAL``/``DELETE``
unless noted):
  1. read-only open + SELECT of an existing DB: main file byte-identical in both modes, BUT in WAL mode
     ``-wal`` and ``-shm`` sibling files appear (and persist) after a plain read-only connection; in DELETE
     mode the directory listing is unchanged. AC-01.10 (dry-run leaves no new files) therefore rules out WAL.
  2. process killed mid-transaction (5000 uncommitted inserts, ``taskkill``-style ``Popen.kill``):
     ``integrity_check`` = ok and only the committed 1000 rows in both modes (DELETE leaves a harmless
     rollback journal until the next writer; WAL leaves ``-wal``/``-shm`` until the next open).
  3. a reader while a writer holds an open ``BEGIN IMMEDIATE`` transaction: reader succeeded in both modes
     (only COMMIT briefly blocks readers in DELETE mode; the 2 s busy timeout covers it).
  4. network/synced folders (not measurable here): WAL needs shared memory and is documented by SQLite as
     unsafe on network filesystems and fragile with sync clients that copy ``-wal``/``-shm`` separately,
     whereas DELETE mode is a single file plus a transient journal. NFR-REL-04's ``.bak`` copy is also
     a plain single-file ``sqlite3.backup`` in DELETE mode.
  The tool has one writer at a time (writer lock), so WAL's reader/writer concurrency gain is not needed.

Backup policy (NFR-REL-04, integrator note b-11): before migrating an existing older DB a copy is made with
``sqlite3.Connection.backup()`` to ``<db>.bak``; if that exists the copy goes to
``<db>.bak.<fromversion>`` and then ``<db>.bak.<fromversion>.<n>`` (n = 2, 3, ...): an existing backup is
never overwritten. A backup failure aborts the migration (EnvError exit 4). A fresh DB gets no backup.
Each migration runs in its own ``BEGIN IMMEDIATE`` transaction together with its ``schema_version`` row;
a failure rolls back, leaves the DB at the previous version and raises ``EnvError`` naming the file and
the SQL error.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from actiontrack.clock import Clock, SystemClock
from actiontrack.errors import EnvError, InternalError, NotFoundError
from actiontrack.store import lock as _lock

BUSY_TIMEOUT_MS = 2000
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_\-]+)\.sql$")


# ------------------------------------------------------------------ errors
class DbLocked(EnvError):
    """SQLite stayed locked/busy beyond ``BUSY_TIMEOUT_MS`` (exit 4; T-M5-04). An ``EnvError`` subclass."""


def map_sqlite_error(exc: BaseException, path: str | os.PathLike[str] | None = None) -> EnvError:
    """Translate a sqlite3 error into an exit-4 ``EnvError`` that names the database path."""
    where = str(path) if path is not None else None
    msg = str(exc).lower()
    if "locked" in msg or "busy" in msg:
        return DbLocked(
            "database is locked by another actiontrack process",
            where=where,
            next_step="wait for the other command to finish, then retry",
        )
    if "readonly" in msg or "read-only" in msg:
        return EnvError(
            "database is read-only", where=where, next_step="make the file and its folder writable"
        )
    if "unable to open" in msg:
        return EnvError(
            "cannot open the database file",
            where=where,
            next_step="check the --db path and folder permissions",
        )
    if "not a database" in msg or "malformed" in msg or "corrupt" in msg:
        return EnvError(
            "database file is corrupt or not an actiontrack database",
            where=where,
            next_step="restore it from a .bak copy or choose another --db path",
        )
    return EnvError(f"database error: {exc}", where=where)


def _is_env_like(exc: sqlite3.Error) -> bool:
    return isinstance(exc, sqlite3.OperationalError)


# -------------------------------------------------------------- migrations
@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def load_migrations(directory: str | os.PathLike[str] | None = None) -> list[Migration]:
    """Discover ``NNNN_name.sql`` files, numerically sorted. Gap/duplicate/none => InternalError."""
    folder = Path(directory) if directory is not None else MIGRATIONS_DIR
    found: list[Migration] = []
    for entry in folder.iterdir():
        match = _MIGRATION_RE.match(entry.name)
        if match and entry.is_file():
            found.append(Migration(int(match.group(1)), match.group(2), entry))
    found.sort(key=lambda m: m.version)
    if not found:
        raise InternalError("no migrations found", where=str(folder))
    for index, mig in enumerate(found, start=1):
        if mig.version != index:
            kind = "duplicate" if index > 1 and mig.version == found[index - 2].version else "gap"
            raise InternalError(
                f"migration numbering has a {kind}: expected {index:04d}, found {mig.path.name}",
                where=str(folder),
            )
    return found


def split_sql(text: str) -> list[str]:
    """Split a script into complete statements (comments and blank lines tolerated)."""
    statements: list[str] = []
    buf = ""
    for line in text.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    leftover = "\n".join(
        ln for ln in buf.splitlines() if ln.strip() and not ln.strip().startswith("--")
    )
    if leftover.strip():
        statements.append(buf.strip())  # incomplete statement: let SQLite report the error
    return statements


def read_schema_version(conn: sqlite3.Connection) -> int:
    """Applied version: 0 for an empty database; EnvError if it is not an actiontrack database."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables:
        return 0
    if "schema_version" not in tables:
        raise EnvError(
            "file is a SQLite database but not an actiontrack database",
            next_step="choose another --db path",
        )
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]


def _now_iso(clock: Clock | None) -> str:
    return (clock or SystemClock()).now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def _choose_backup_path(db_path: Path, from_version: int) -> Path:
    candidates = [Path(f"{db_path}.bak"), Path(f"{db_path}.bak.{from_version}")]
    for cand in candidates:
        if not cand.exists():
            return cand
    n = 2
    while True:
        cand = Path(f"{db_path}.bak.{from_version}.{n}")
        if not cand.exists():
            return cand
        n += 1


def _backup_file(conn: sqlite3.Connection, db_path: Path, from_version: int) -> Path:
    dest_path = _choose_backup_path(db_path, from_version)
    dest = None
    try:
        dest = sqlite3.connect(dest_path)
        conn.backup(dest)
    except (sqlite3.Error, OSError) as exc:
        if dest is not None:
            dest.close()
            dest = None
        try:
            dest_path.unlink()
        except OSError:
            pass
        raise EnvError(
            f"cannot back up the database before migrating: {exc}",
            where=str(dest_path),
            next_step="free disk space or fix permissions, then retry (the database was not changed)",
        ) from exc
    finally:
        if dest is not None:
            dest.close()
    return dest_path


def migrate(
    conn: sqlite3.Connection,
    migrations: list[Migration],
    *,
    db_path: str | os.PathLike[str] | None = None,
    clock: Clock | None = None,
) -> list[int]:
    """Apply pending migrations in order, one transaction each; returns the versions applied.

    ``db_path`` (a real file) enables the pre-migration backup of an existing older DB. Newer-than-code
    raises ``EnvError``. A failing migration rolls back and raises ``EnvError`` naming file and error.
    """
    current = read_schema_version(conn)
    latest = migrations[-1].version
    where = str(db_path) if db_path is not None else None
    if current > latest:
        raise EnvError(
            f"database schema version {current} is newer than this actiontrack supports ({latest})",
            where=where,
            next_step="the DB was created by a newer actiontrack, upgrade the program",
        )
    pending = [m for m in migrations if m.version > current]
    if not pending:
        return []
    if current >= 1 and db_path is not None:
        _backup_file(conn, Path(db_path), current)
    applied: list[int] = []
    for mig in pending:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise map_sqlite_error(exc, db_path) from exc
        try:
            for stmt in split_sql(mig.sql):
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                (mig.version, mig.name, _now_iso(clock)),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise EnvError(
                f"migration {mig.path.name} failed: {exc}",
                where=where,
                next_step=f"the database stays at version {mig.version - 1}, report this problem",
            ) from exc
        applied.append(mig.version)
    return applied


# ---------------------------------------------------------------- Database
def _configure(conn: sqlite3.Connection, *, readonly: bool, memory: bool) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not readonly and not memory:
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA synchronous = FULL")


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    try:
        if readonly:
            uri = path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        else:
            conn = sqlite3.connect(str(path), isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA encoding")  # forces the header read: corrupt files fail here
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        raise map_sqlite_error(exc, path) from exc
    return conn


class Database:
    """An open actiontrack database (see the module docstring for the full contract)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        path: Path | None,
        *,
        readonly: bool = False,
        in_memory: bool = False,
        write_lock: _lock.WriteLock | None = None,
        clock: Clock | None = None,
        migrations: list[Migration] | None = None,
    ) -> None:
        self.conn = conn
        self.path = path
        self.readonly = readonly
        self.in_memory = in_memory
        self.write_lock = write_lock
        self.clock = clock
        self._migrations = migrations if migrations is not None else load_migrations()
        self._in_txn = False
        self.fault_hook: Callable[[str], None] | None = None

    # ------------------------------------------------------------ opening
    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        *,
        create: bool = True,
        readonly: bool = False,
        lock: bool = False,
        clock: Clock | None = None,
        migrations_dir: str | os.PathLike[str] | None = None,
    ) -> Database:
        db_path = Path(path)
        migrations = load_migrations(migrations_dir)
        if readonly:
            if lock:
                raise InternalError("a read-only database cannot take the writer lock")
            return cls._open_readonly(db_path, clock, migrations)
        exists = db_path.exists()
        if not exists and not create:
            raise NotFoundError(
                "database not found", where=str(db_path), next_step="run `actiontrack ingest` first"
            )
        if not exists:
            try:
                if sys.platform == "win32":
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                else:  # pragma: no cover - POSIX
                    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError as exc:
                raise EnvError(
                    f"cannot create the database folder: {exc.strerror or exc}",
                    where=str(db_path.parent),
                    next_step="choose a --db path inside a writable folder",
                ) from exc
        write_lock = _lock.WriteLock(db_path).acquire() if lock else None
        try:
            conn = _connect(db_path, readonly=False)
            try:
                _configure(conn, readonly=False, memory=False)
                if not exists and sys.platform != "win32":  # pragma: no cover - POSIX
                    os.chmod(db_path, 0o600)
                migrate(conn, migrations, db_path=db_path, clock=clock)
            except BaseException as exc:
                conn.close()
                if isinstance(exc, sqlite3.Error):
                    raise map_sqlite_error(exc, db_path) from exc
                raise
        except BaseException:
            if write_lock is not None:
                write_lock.release()
            raise
        return cls(conn, db_path, write_lock=write_lock, clock=clock, migrations=migrations)

    @classmethod
    def _open_readonly(cls, db_path: Path, clock: Clock | None, migrations: list[Migration]) -> Database:
        if not db_path.exists():
            raise NotFoundError(
                "database not found", where=str(db_path), next_step="run `actiontrack ingest` first"
            )
        conn = _connect(db_path, readonly=True)
        try:
            _configure(conn, readonly=True, memory=False)
            current = read_schema_version(conn)
            latest = migrations[-1].version
            if current > latest:
                raise EnvError(
                    f"database schema version {current} is newer than this actiontrack supports ({latest})",
                    where=str(db_path),
                    next_step="the DB was created by a newer actiontrack, upgrade the program",
                )
            if current < latest:
                copy = cls._copy_into_memory(conn, None, clock, migrations)
                conn.close()
                return copy
        except BaseException:
            conn.close()
            raise
        return cls(conn, db_path, readonly=True, clock=clock, migrations=migrations)

    @classmethod
    def open_dry_run(
        cls,
        path: str | os.PathLike[str],
        *,
        clock: Clock | None = None,
        migrations_dir: str | os.PathLike[str] | None = None,
    ) -> Database:
        db_path = Path(path)
        migrations = load_migrations(migrations_dir)
        if not db_path.exists():
            mem = sqlite3.connect(":memory:", isolation_level=None)
            _configure(mem, readonly=False, memory=True)
            migrate(mem, migrations, clock=clock)
            return cls(mem, None, in_memory=True, clock=clock, migrations=migrations)
        conn = _connect(db_path, readonly=True)
        try:
            return cls._copy_into_memory(conn, None, clock, migrations)
        finally:
            conn.close()

    @classmethod
    def _copy_into_memory(
        cls,
        src: sqlite3.Connection,
        path: Path | None,
        clock: Clock | None,
        migrations: list[Migration],
    ) -> Database:
        mem = sqlite3.connect(":memory:", isolation_level=None)
        try:
            src.backup(mem)
            _configure(mem, readonly=False, memory=True)
            migrate(mem, migrations, clock=clock)  # no db_path: no backup for an in-memory copy
        except sqlite3.Error as exc:
            mem.close()
            raise map_sqlite_error(exc, path) from exc
        except BaseException:
            mem.close()
            raise
        return cls(mem, None, in_memory=True, clock=clock, migrations=migrations)

    def in_memory_copy(self) -> Database:
        """In-memory copy of this database (dry-run working copy); the file is never touched."""
        return self._copy_into_memory(self.conn, self.path, self.clock, self._migrations)

    # ----------------------------------------------------------- behaviour
    @property
    def version(self) -> int:
        return read_schema_version(self.conn)

    def fault(self, name: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(name)

    def can_mark_interrupted(self) -> bool:
        if self.path is None:
            return True  # an in-memory copy has no concurrent owner
        return _lock.can_mark_interrupted(self.path, self.write_lock)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self._in_txn:
            raise InternalError("nested transaction() is not allowed")
        if self.readonly:
            raise InternalError("cannot start a transaction on a read-only database")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise map_sqlite_error(exc, self.path) from exc
        self._in_txn = True
        try:
            yield self.conn
            self.fault("before_commit")
            self.conn.execute("COMMIT")
        except BaseException as exc:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            if isinstance(exc, sqlite3.Error) and _is_env_like(exc):
                raise map_sqlite_error(exc, self.path) from exc
            raise
        finally:
            self._in_txn = False

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            if self.write_lock is not None:
                self.write_lock.release()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
