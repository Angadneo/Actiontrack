"""Process-level writer lock ``<db>.lock`` (spec 10.7.7, AC-08.9, SC-67; T-M1-11).

Why: ingest commits per file, so SQLite's own lock is released between files. Two concurrent
writers could interleave; the spec demands the second one fail with exit 4. Every command that
WRITES (``ingest`` for the whole run, ``done``, ``followup`` row writes, ``followup
--record-draft``) holds this lock for its whole duration. ``--dry-run`` and read-only commands
never take it (and never create the lock file).

Mechanism: an exclusive, non-blocking OS lock on one byte of ``<db>.lock``
(``msvcrt.locking`` on Windows, ``fcntl.flock`` elsewhere). The OS releases it when the
process dies, so a stale lock file is harmless and is NEVER deleted as a recovery step.
The lock conflicts even with a second handle inside the same process, so accidental nested
acquisition fails the same way a second process does.

Public API
----------
``lock_path_for(db_path) -> Path``         ``<db>.lock``
``WriteLock(db_path)``                     ``acquire()`` (raises ``EnvError`` exit 4 with
                                           "another actiontrack process is using this database"),
                                           ``release()``, ``held`` property, context manager.
``can_mark_interrupted(db_path, own_lock=None) -> bool``
    True only when this process holds the lock (pass its ``WriteLock`` as ``own_lock``) or can
    acquire it right now (then it is released again). False while another live process holds it:
    a live ``in_progress`` ingest_run is a concurrent run and must never be marked ``interrupted``
    (10.7.6). Never creates ``<db>.lock`` unless it can lock it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from actiontrack.errors import EnvError

LOCK_MESSAGE = "another actiontrack process is using this database"

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:  # pragma: no cover - already released
            pass

else:  # pragma: no cover - exercised on POSIX only
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def lock_path_for(db_path: str | os.PathLike[str]) -> Path:
    return Path(str(db_path) + ".lock")


class WriteLock:
    """Exclusive non-blocking writer lock for one database file."""

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.db_path = Path(db_path)
        self.path = lock_path_for(db_path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> WriteLock:
        if self._fd is not None:
            raise EnvError(LOCK_MESSAGE, where=str(self.path), next_step="this process already holds the lock")
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise EnvError(
                f"cannot open the lock file: {exc.strerror or exc}",
                where=str(self.path),
                next_step="check that the database folder exists and is writable",
            ) from exc
        if not _try_lock(fd):
            os.close(fd)
            raise EnvError(
                LOCK_MESSAGE,
                where=str(self.path),
                next_step="wait for the other actiontrack command to finish, then retry",
            )
        self._fd = fd
        return self

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            _unlock(fd)
            os.close(fd)

    def __enter__(self) -> WriteLock:
        return self.acquire() if not self.held else self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def can_mark_interrupted(db_path: str | os.PathLike[str], own_lock: WriteLock | None = None) -> bool:
    """True only if no *other* live process owns the database (see module docstring)."""
    if own_lock is not None and own_lock.held:
        return True
    probe = WriteLock(db_path)
    try:
        probe.acquire()
    except EnvError:
        return False
    probe.release()
    return True
