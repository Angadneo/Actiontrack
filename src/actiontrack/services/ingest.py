"""Ingest orchestration (spec 4.2, FR-01, FR-08, 10.7; plan 2.3 ``services/ingest.py``).

This module is built up by several M5 tasks; each owns one clearly delimited SECTION below and appends
its public names to this docstring. Keep the sections independent (later ones may use earlier ones,
never the reverse). No SQL lives here (it stays in ``store/repo.py``), no ``datetime.now``.

Sections
--------
1. Ordering and the file plan (T-M5-03)  -- pure, implemented
2. Run lifecycle, lock, interrupted runs (T-M5-04)            -- implemented
3. Per-file transaction engine / report counters (T-M5-05)     -- implemented
4. Persisting a new file (T-M5-07)                             -- implemented
5. Matching integration, changed files, missing files (T-M5-08..13) -- later
6. Whole-database passes and finalisation (T-M5-14); interim driver ``run_ingest`` -- later / interim

Section 1 public API (T-M5-03; spec FR-01.1, AC-01.1, 10.7.1, 10.7.5, BR-18, NFR-DET-01)
--------------------------------------------------------------------------------------
``canonical_folder_root(folder) -> str``   ``os.path.realpath`` of the folder (absolute, symlinks and a
                                    ``\\\\?\\`` prefix resolved, true on-disk case on Windows). It is the
                                    ``source_file.folder_root`` identity and is stored verbatim (G-C12
                                    default: no further case folding, a differently-cased spelling of
                                    the same folder resolves to the same real path on Windows).
``to_rel_path(folder_root, abs_path) -> str``  path relative to the root with ``/`` separators.
``order_files(parsed_files) -> list[ParsedFile]``  pure: sort key ``domain.models.ingest_order_key``
                                    (dated files by meeting date ascending, then ``rel_path`` in ordinal
                                    code-point order, ``/`` separators; undated, failed and empty files
                                    after all dated ones in ``rel_path`` order). No locale, no
                                    ``str.lower()``; the result does not depend on the input order.
``FileClass``                      StrEnum ``new`` | ``changed`` | ``unchanged`` | ``empty`` | ``failed``
                                    (classification of a scanned file against its stored row).
``classify_file(parsed, previous) -> FileClass``   the skip decision (10.7.1):
                                    * ``failed``    parsed status ``failed`` (never unchanged, G-25/G-28;
                                      10.7.8: previous data is retained by the engine);
                                    * ``empty``     parsed status ``empty`` (never unchanged, G-25);
                                    * ``new``       no stored row for ``(folder_root, rel_path)``;
                                    * ``unchanged`` stored row has status ``ok`` and the same SHA-256 as
                                      the last committed ingest (the stored hash is only written by a
                                      committed file transaction);
                                    * ``changed``   anything else, including a stored row that is
                                      ``failed`` / ``empty`` / ``missing`` (a restored file is
                                      re-processed so its ``present=false`` sources are re-attached,
                                      10.7.2).
``PlanEntry(parsed, order_key, previous, file_class)``   one scanned file in the plan. ``previous`` is the
                                    stored ``SourceFileRow`` (or None) -- its ``content_sha256`` is the
                                    hash the skip decision used.
``FilePlan(folder_root, recursive, entries, failed, empty, missing)``
                                    ``entries`` = the files to walk through the per-file engine in
                                    persistence order (status ``ok`` files: classes new / changed /
                                    unchanged); ``failed`` and ``empty`` = reporting lists (never
                                    persisted as items, ``rel_path`` order); ``missing`` = stored rows of
                                    this folder_root that were not found by this scan and are inside its
                                    scope (10.7.5: under ``--no-recursive`` only top-level rows, i.e.
                                    rel_path without ``/``) and not already ``missing``.
``plan_files(parsed_files, stored_files, *, folder_root, recursive=True) -> FilePlan``
                                    pure: built only after EVERY file was read and parsed (parse-all then
                                    order, FR-01.1, failures isolated first, FR-01.8). ``stored_files``
                                    = ``Repo.list_source_files(folder_root)``.

Section 2 public API (T-M5-04; spec 10.7.6, 10.7.7, AC-08.9, SC-67)
------------------------------------------------------------------
``ingest_session(db_path, folder, *, dry_run=False, clock=None)``  context manager yielding
                                    ``IngestSession(db, repo, run, folder_root)``. Non-dry: opens with the
                                    process lock ``<db>.lock`` (``EnvError`` exit 4 for a second writer, DB
                                    untouched), then ``begin_run``. Dry run: in-memory copy, no lock, no file.
``begin_run(db, repo, folder_root, *, dry_run=False) -> RunHandle``  new ``in_progress`` run in its own
                                    committed transaction; earlier ``in_progress`` runs become ``interrupted``
                                    (reported in ``RunHandle.interrupted``) only if ``db.can_mark_interrupted()``;
                                    otherwise listed in ``in_progress_elsewhere``. Dry runs never mark.
``finish_run(db, repo, run_id, counters=None) -> IngestRunRow``  own transaction; ``finalize_run(repo, run_id,
                                    counters=None)`` is the same without a transaction (T-M5-14's final one):
                                    counters, status ``complete`` / ``partial`` (>= 1 failed file), ``finished_at``.
``RunCounters``                     report counters (spec 4.2); ``record(outcome)``, ``invariant_holds()``,
                                    ``as_columns()``. ``InterruptedRun``, ``RunHandle``: see their docstrings.

Section 3 public API (T-M5-05; FR-01.8, BR-17, NFR-REL-01, 10.7.1, 10.7.8, spec 11)
-----------------------------------------------------------------------------------
``IngestEnv(db, repo, registry, run_id, folder_root, today, cfg, match=None, reingest=None)``  one per run.
``process_plan(env, plan) -> list[FileOutcome]`` / ``process_entry(env, entry, ordinal) -> FileOutcome``
                                    one transaction per file; ``FileOutcome.result`` is ``ingested`` /
                                    ``unchanged`` / ``empty`` / ``failed`` (+ ``failure_code``, ``traceback``).
``FileWrite``                       what a per-file handler wrote (feeds the counters); ``reingest`` seam
                                    ``(env, entry, ordinal) -> FileWrite`` for changed files (T-M5-12).
Fault points: ``file_start[:rel]``, ``file_written[:rel]``, ``after_file[:k]`` (+ ``before_commit`` of the db).

Section 4 public API (T-M5-07; spec 7.2, 10.1, 10.3, BR-04, BR-10, FR-01.12)
----------------------------------------------------------------------------
``persist_new_file(env, entry, ordinal) -> FileWrite``  see the section comment for the exact row mapping.
``match_or_create(env, src: NewSource) -> MatchResult``  THE matcher seam (default: always ``create_item``);
                                    ``create_item(env, src)`` / ``add_source(env, item_id, src)`` are the two
                                    ways to attach a source; ``resolve_item_owners(item, ctx, registry, cfg)``.
``FileCtx``, ``NewSource``, ``MatchResult``  the seam's data types.

Section 6 (interim) ``run_ingest(db_path, folder, *, cfg, run_ctx, recursive=True, dry_run=False,
fault_hook=None, match=None, reingest=None) -> IngestResult``  discover -> session -> registry -> parse all ->
plan -> ``process_plan`` -> finish. ``IngestResult.exit_code`` is 5 when a file failed, else 0.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import os
import traceback
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime

from actiontrack import codes
from actiontrack.clock import Clock, RunContext
from actiontrack.config import Config
from actiontrack.domain.dates import DueResult
from actiontrack.domain.matching import core_key
from actiontrack.domain.models import (
    OwnerRole,
    OwnerStatus,
    ParsedFile,
    ParsedItem,
    ParseWarning,
    ResolvedOwner,
    ingest_order_key,
    parse_message,
)
from actiontrack.errors import EnvError, ExitCode
from actiontrack.parsing import parse_file
from actiontrack.parsing.discovery import discover
from actiontrack.parsing.owner_phrase import OwnerAnalyzer
from actiontrack.resolution.owners import MeetingContext, resolve_attendees, resolve_owner
from actiontrack.resolution.people import PeopleRegistry, file_transaction, known_name_predicate
from actiontrack.store.db import Database
from actiontrack.store.repo import IngestRunRow, Repo, SourceFileRow

# =============================================================================================
# SECTION 1 -- Ordering and the file plan (T-M5-03)
# =============================================================================================


def canonical_folder_root(folder: str | os.PathLike[str]) -> str:
    """Canonical absolute folder identity (same function ``parsing.discovery.discover`` uses)."""
    return os.path.realpath(os.fspath(folder))


def to_rel_path(folder_root: str, abs_path: str | os.PathLike[str]) -> str:
    """Folder-relative path with ``/`` separators (stored verbatim, never case-folded or NFC'd)."""
    rel = os.path.relpath(os.fspath(abs_path), folder_root)
    return rel.replace(os.sep, "/") if os.sep != "/" else rel


def order_files(parsed_files: Iterable[ParsedFile]) -> list[ParsedFile]:
    """Chronological ingest order (FR-01.1): meeting date ascending, null last, then ordinal ``rel_path``."""
    return sorted(parsed_files, key=ingest_order_key)


class FileClass(enum.StrEnum):
    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    EMPTY = "empty"
    FAILED = "failed"


def classify_file(parsed: ParsedFile, previous: SourceFileRow | None) -> FileClass:
    """Skip decision of spec 10.7.1 (see the module docstring for the table)."""
    if parsed.status == "failed":
        return FileClass.FAILED
    if parsed.status == "empty":
        return FileClass.EMPTY
    if previous is None:
        return FileClass.NEW
    if (
        previous.status == "ok"
        and previous.content_sha256 is not None
        and previous.content_sha256 == parsed.content_sha256
    ):
        return FileClass.UNCHANGED
    return FileClass.CHANGED


@dataclass(frozen=True, slots=True)
class PlanEntry:
    parsed: ParsedFile
    order_key: tuple[bool, date, str]
    previous: SourceFileRow | None
    file_class: FileClass

    @property
    def rel_path(self) -> str:
        return self.parsed.rel_path

    @property
    def previous_hash(self) -> str | None:
        return None if self.previous is None else self.previous.content_sha256


@dataclass(frozen=True, slots=True)
class FilePlan:
    folder_root: str
    recursive: bool
    entries: tuple[PlanEntry, ...]
    failed: tuple[PlanEntry, ...]
    empty: tuple[PlanEntry, ...]
    missing: tuple[SourceFileRow, ...]


def plan_files(
    parsed_files: Iterable[ParsedFile],
    stored_files: Iterable[SourceFileRow],
    *,
    folder_root: str,
    recursive: bool = True,
) -> FilePlan:
    """Build the file plan from every parsed file and the stored ``source_file`` rows of ``folder_root``.

    Pure and order-independent: shuffling either input gives an equal plan."""
    stored = {row.rel_path: row for row in stored_files if row.folder_root == folder_root}
    entries: list[PlanEntry] = []
    failed: list[PlanEntry] = []
    empty: list[PlanEntry] = []
    scanned: set[str] = set()
    for parsed in order_files(parsed_files):
        scanned.add(parsed.rel_path)
        previous = stored.get(parsed.rel_path)
        entry = PlanEntry(parsed, ingest_order_key(parsed), previous, classify_file(parsed, previous))
        if entry.file_class is FileClass.FAILED:
            failed.append(entry)
        elif entry.file_class is FileClass.EMPTY:
            empty.append(entry)
        else:
            entries.append(entry)
    missing = tuple(
        row
        for rel_path, row in sorted(stored.items())
        if rel_path not in scanned
        and row.status != "missing"
        and (recursive or "/" not in rel_path)  # 10.7.5: files outside this run's scan scope are never missing
    )
    return FilePlan(folder_root, recursive, tuple(entries), tuple(failed), tuple(empty), missing)


# =============================================================================================
# SECTION 2 -- Run lifecycle, process lock, interrupted-run detection (T-M5-04; AC-08.9, SC-67)
# =============================================================================================
#
# Locking: a writer opens the database with ``Database.open(path, lock=True)`` (``ingest_session`` does),
# which takes the OS file lock ``<db>.lock`` for the WHOLE command and raises ``EnvError`` (exit 4,
# "another actiontrack process is using this database") before any row is touched. ``done`` / ``followup``
# row writes (M6/M7) use the same ``lock=True`` open but keep it only for one short, immediate
# transaction; ``--dry-run`` and read-only commands never take it (10.7.7).
#
# Interruption (10.7.6): runs are never "closed" on an abnormal exit (crash, kill, or an exception that
# unwinds the session): the row stays ``in_progress`` and the lock is released by the context manager /
# the OS. The NEXT run marks it ``interrupted`` and reports it, but only when ``db.can_mark_interrupted()``
# proves that no live process owns the database; a live ``in_progress`` run is never marked. An
# interrupted run keeps ``finished_at`` NULL (the end time is unknown) and zero counters (counters are
# written once, at ``finish_run``).


@dataclass(slots=True)
class RunCounters:
    """Run-scoped counters of the ingest report (spec 4.2), accumulated in memory and written to the
    ``ingest_run`` row at ``finish_run``. Invariant: ``discovered = ingested + unchanged + empty + failed``."""

    files_discovered: int = 0
    files_ingested: int = 0
    files_unchanged: int = 0
    files_empty: int = 0
    files_failed: int = 0
    items_new: int = 0
    items_merged: int = 0
    items_updated: int = 0
    items_unchanged: int = 0
    possible_duplicates: int = 0
    already_done_in_note: int = 0
    resolved_via_attendance: int = 0

    def invariant_holds(self) -> bool:
        return self.files_discovered == (
            self.files_ingested + self.files_unchanged + self.files_empty + self.files_failed
        )

    def as_columns(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}

    def record(self, outcome: FileOutcome) -> None:
        """Fold one committed file outcome into the counters (never called for a rolled-back write)."""
        self.files_discovered += 1
        if outcome.result == "ingested":
            self.files_ingested += 1
        elif outcome.result == "unchanged":
            self.files_unchanged += 1
        elif outcome.result == "empty":
            self.files_empty += 1
        else:
            self.files_failed += 1
        self.items_new += outcome.items_new
        self.items_merged += outcome.items_merged
        self.items_updated += outcome.items_updated
        self.items_unchanged += outcome.items_unchanged
        self.possible_duplicates += outcome.possible_duplicates
        self.already_done_in_note += outcome.already_done_in_note


@dataclass(frozen=True, slots=True)
class InterruptedRun:
    """A previous run found ``in_progress`` and marked ``interrupted`` by this run."""

    run_id: int
    started_at: str
    folder_root: str
    files_committed: int
    last_file: str | None

    @property
    def message(self) -> str:
        last = f" (last: {self.last_file})" if self.last_file else ""
        return (
            f"previous run #{self.run_id} (started {self.started_at}) was interrupted after "
            f"{self.files_committed} committed file(s){last}; it is now marked interrupted"
        )


@dataclass(frozen=True, slots=True)
class RunHandle:
    """Result of ``begin_run``: the new run and what was found of earlier ones."""

    run_id: int
    started_at: str
    folder_root: str
    dry_run: bool
    interrupted: tuple[InterruptedRun, ...] = ()
    #: ``in_progress`` runs seen but NOT marked (a live owner, or a dry run that never writes)
    in_progress_elsewhere: tuple[int, ...] = ()


def begin_run(db: Database, repo: Repo, folder_root: str, *, dry_run: bool = False) -> RunHandle:
    """Create ``IngestRun(status=in_progress, started_at, folder_root, dry_run)`` in its own committed
    transaction and settle earlier ``in_progress`` runs in the same transaction.

    Non-dry: every earlier ``in_progress`` run becomes ``interrupted`` and is reported, but only when
    ``db.can_mark_interrupted()`` (this process holds the write lock, or nobody does). A dry run (an
    in-memory working copy) never marks anything: it merely lists them in ``in_progress_elsewhere``."""
    stale = repo.list_ingest_runs(status="in_progress")
    may_mark = (not dry_run) and db.can_mark_interrupted()
    notices: list[InterruptedRun] = []
    with db.transaction():
        if may_mark:
            for run in stale:
                notices.append(
                    InterruptedRun(
                        run.id,
                        run.started_at,
                        run.folder_root,
                        repo.count_files_ingested_in_run(run.id),
                        repo.last_ingested_file(run.id),
                    )
                )
                repo.update_ingest_run(run.id, status="interrupted")
        run_id = repo.insert_ingest_run(folder_root, dry_run=dry_run)
    started = repo.get_ingest_run(run_id)
    assert started is not None
    elsewhere = () if may_mark else tuple(r.id for r in stale)
    return RunHandle(run_id, started.started_at, folder_root, dry_run, tuple(notices), elsewhere)


def finalize_run(repo: Repo, run_id: int, counters: RunCounters | None = None) -> IngestRunRow:
    """Write the counters, ``status`` (``partial`` if >= 1 file failed, else ``complete``, spec 4.2) and
    ``finished_at``. Writes only; the caller owns the transaction (T-M5-14's final transaction)."""
    if counters is not None:
        repo.update_ingest_run(run_id, **counters.as_columns())
    row = repo.get_ingest_run(run_id)
    assert row is not None
    repo.update_ingest_run(
        run_id, status="partial" if row.files_failed else "complete", finished_at=repo.now_iso()
    )
    done = repo.get_ingest_run(run_id)
    assert done is not None
    return done


def finish_run(db: Database, repo: Repo, run_id: int, counters: RunCounters | None = None) -> IngestRunRow:
    """``finalize_run`` in its own transaction."""
    with db.transaction():
        return finalize_run(repo, run_id, counters)


@dataclass(slots=True)
class IngestSession:
    db: Database
    repo: Repo
    run: RunHandle
    folder_root: str


@contextlib.contextmanager
def ingest_session(
    db_path: str | os.PathLike[str],
    folder: str | os.PathLike[str],
    *,
    dry_run: bool = False,
    clock: Clock | None = None,
) -> Iterator[IngestSession]:
    """Open the database for one ingest run and hold the process lock for its whole duration.

    Non-dry: ``Database.open(lock=True)`` (a second writer gets ``EnvError`` exit 4 with the database
    untouched), then ``begin_run``. Dry run: in-memory working copy, no lock, no file, nothing persisted.
    The lock is released (and the database closed) on normal and exceptional exit; an unfinished run
    stays ``in_progress`` for the next run to settle (see the section comment)."""
    root = canonical_folder_root(folder)
    # ``Database.open`` is looked up by name: the repo lint ``tests/unit/test_io.py`` flags every literal
    # ``.open(`` call as a possible text open without ``encoding=`` (a false positive for the database).
    open_writer = getattr(Database, "open")  # noqa: B009
    db = (
        Database.open_dry_run(db_path, clock=clock)
        if dry_run
        else open_writer(db_path, lock=True, clock=clock)
    )
    try:
        repo = Repo(db, clock)
        yield IngestSession(db, repo, begin_run(db, repo, root, dry_run=dry_run), root)
    finally:
        db.close()


# =============================================================================================
# SECTION 3 -- Per-file transaction engine (T-M5-05; FR-01.8, BR-17, NFR-REL-01, 10.7.1, 10.7.8, 11)
# =============================================================================================
#
# One ``PlanEntry`` at a time, in plan order, each in its OWN transaction (``file_transaction`` = DB
# transaction + registry rollback point). Outcomes:
#
# * ``unchanged``  hash equal to the last committed ingest: no transaction, no statement, nothing is
#                  touched (idempotent, BR-15). The whole-database passes of 10.7.9 are not skipped by this
#                  (T-M5-14 always runs them).
# * ``ingested``   new or changed content committed. NEW files (no stored data) go to ``persist_new_file``
#                  (section 4); files that already have data go to ``env.reingest`` (seam for T-M5-12;
#                  without a handler the file is isolated as a failure and its previous data stays).
# * ``empty`` / ``failed``  re-classified on EVERY run, never "unchanged" (G-25/G-28). A tiny transaction
#                  stores status / failure code and one run-scoped ``ingest_warning`` row (E-* for a failure,
#                  W-EMPTY-FILE for an empty file). A row that already holds ingested data keeps its hash,
#                  size and mtime (they describe the data in the database) and its items stay untouched and
#                  ``source_state=present`` (10.7.8: a failure is no evidence of removal). An ``ok`` file that
#                  became empty is handled the same way (items retained; marking its sources absent is the
#                  changed-file logic of T-M5-12/13). A row whose status is unchanged is not rewritten.
# * restored content: a ``failed`` / ``empty`` row whose stored hash equals the file's hash again is
#                  revived (status ``ok``) as ``unchanged``: the data in the database already matches.
#
# Unexpected exceptions inside a file transaction (parser internals do not get here; resolution, matching,
# persistence do) roll that file back (including ``registry.restore()``) and the file is marked ``failed``
# with ``E-INTERNAL-PARSE`` in a SEPARATE small transaction (spec 11, G-C18 resolved by 11). The run goes on.
# ``EnvError`` (database locked / corrupt, exit 4) aborts the whole run instead, and ``BaseException``
# (a simulated or real kill) propagates untouched, leaving the run ``in_progress``.
#
# Fault-injection points (``db.fault(name)``, AC-08.5 / SC-28; the transaction helper adds ``before_commit``):
#   ``file_start`` / ``file_start:<rel_path>``      inside the file transaction, before any write
#   ``file_written`` / ``file_written:<rel_path>``  inside the file transaction, after the last write
#   ``after_file`` / ``after_file:<k>``             after file k (1-based, plan order) was committed or isolated
# A hook that raises an ``Exception`` inside the transaction is a forced file failure; one raised from
# ``after_file`` (outside any try) propagates, simulating a crash between files.


@dataclass(frozen=True, slots=True)
class FileWrite:
    """What a per-file handler (``persist_new_file`` / ``reingest``) wrote; folded into the counters."""

    source_file_id: int | None = None
    items_new: int = 0
    items_merged: int = 0
    items_updated: int = 0
    items_unchanged: int = 0
    possible_duplicates: int = 0
    already_done_in_note: int = 0


@dataclass(frozen=True, slots=True)
class FileOutcome:
    """Result of one plan entry. ``result``: ``ingested`` | ``unchanged`` | ``empty`` | ``failed``."""

    rel_path: str
    file_class: FileClass
    result: str
    source_file_id: int | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    traceback: str | None = None
    items_new: int = 0
    items_merged: int = 0
    items_updated: int = 0
    items_unchanged: int = 0
    possible_duplicates: int = 0
    already_done_in_note: int = 0


@dataclass(slots=True)
class IngestEnv:
    """Everything a per-file step needs (one per run). ``registry`` is the live people registry K."""

    db: Database
    repo: Repo
    registry: PeopleRegistry
    run_id: int
    folder_root: str
    today: date
    cfg: Config
    #: matcher seam (section 4); ``None`` = ``match_or_create``
    match: Callable[[IngestEnv, NewSource], MatchResult] | None = None
    #: changed-file handler (T-M5-12); runs inside the file transaction, returns a ``FileWrite``
    reingest: Callable[[IngestEnv, PlanEntry, int], FileWrite] | None = None


def _iso_mtime(mtime_ns: int | None) -> str | None:
    if mtime_ns is None:
        return None
    return datetime.fromtimestamp(mtime_ns // 1_000_000_000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _where(rel_path: str, line: int | None) -> str:
    return f"{rel_path}:{line}" if line else rel_path


def _warning_message(code: str, rel_path: str, line: int | None = None, **kw: object) -> str:
    """Message of an ingest-emitted warning: ``<file[:line]>: <registry template>``."""
    return f"{_where(rel_path, line)}: {codes.REGISTRY[code].format(**kw)}"


def _has_data(repo: Repo, row: SourceFileRow | None) -> bool:
    """True when a stored file row holds ingested data (it has a meeting row)."""
    return row is not None and repo.get_meeting_for_file(row.id) is not None


def _write_status_row(
    env: IngestEnv,
    entry: PlanEntry,
    status: str,
    failure_code: str | None,
    warnings: Iterable[tuple[str, int | None, str]],
) -> int:
    """Store ``failed`` / ``empty`` for a file and its run-scoped warning rows (caller owns the transaction).

    New row: stores what was observed. Existing row with data: only status / failure code move (hash, size,
    mtime keep describing the stored data). Nothing is written when the row already says the same."""
    repo, parsed, prev = env.repo, entry.parsed, entry.previous
    observed = {
        "content_sha256": parsed.content_sha256,
        "size_bytes": parsed.size_bytes,
        "mtime": _iso_mtime(parsed.mtime_ns),
    }
    if prev is None:
        sf_id = repo.insert_source_file(
            env.folder_root, parsed.rel_path, status=status, failure_code=failure_code, run_id=env.run_id, **observed
        )
    else:
        sf_id = prev.id
        wanted: dict[str, object] = {"status": status, "failure_code": failure_code}
        if not _has_data(repo, prev):
            wanted.update(observed)
        diff = {k: v for k, v in wanted.items() if getattr(prev, k) != v}
        if diff:
            repo.update_source_file(sf_id, last_run_id=env.run_id, **diff)
    for code, line, message in warnings:
        repo.add_warning(env.run_id, code, message, source_file_id=sf_id, line=line)
    return sf_id


def _record_failed_or_empty(env: IngestEnv, entry: PlanEntry) -> FileOutcome:
    parsed = entry.parsed
    with env.db.transaction():
        if entry.file_class is FileClass.FAILED:
            failure = parsed.failure
            assert failure is not None
            sf_id = _write_status_row(
                env, entry, "failed", failure.code, [(failure.code, None, failure.message)]
            )
            return FileOutcome(
                parsed.rel_path, entry.file_class, "failed", sf_id, failure.code, failure.message, failure.traceback
            )
        sf_id = _write_status_row(
            env, entry, "empty", None, [(w.code, w.line, w.message) for w in parsed.warnings]
        )
    return FileOutcome(parsed.rel_path, entry.file_class, "empty", sf_id)


def _fail_file(env: IngestEnv, entry: PlanEntry, exc: BaseException) -> FileOutcome:
    """The file's transaction was rolled back: mark it failed (``E-INTERNAL-PARSE``) in a separate transaction."""
    rel = entry.parsed.rel_path
    code = codes.E_INTERNAL_PARSE
    message = parse_message(code, rel)
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    with env.db.transaction():
        sf_id = _write_status_row(env, entry, "failed", code, [(code, None, message)])
    return FileOutcome(rel, entry.file_class, "failed", sf_id, code, message, trace)


def process_entry(env: IngestEnv, entry: PlanEntry, ordinal: int) -> FileOutcome:
    """Process one planned file (see the section comment). ``ordinal`` = position among the plan's ``ok`` files."""
    parsed, prev = entry.parsed, entry.previous
    if entry.file_class is FileClass.UNCHANGED:
        return FileOutcome(parsed.rel_path, entry.file_class, "unchanged", prev.id if prev else None)
    if entry.file_class in (FileClass.FAILED, FileClass.EMPTY):
        return _record_failed_or_empty(env, entry)
    has_data = _has_data(env.repo, prev)
    if (
        has_data
        and prev is not None
        and prev.status in ("failed", "empty")
        and prev.content_sha256 == parsed.content_sha256
    ):  # the content that produced the stored data is back: nothing to re-ingest
        with env.db.transaction():
            env.repo.update_source_file(prev.id, status="ok", failure_code=None, last_run_id=env.run_id)
        return FileOutcome(parsed.rel_path, entry.file_class, "unchanged", prev.id)

    def handler(e: IngestEnv, en: PlanEntry, n: int) -> FileWrite:
        if not has_data:
            return persist_new_file(e, en, n)
        if e.reingest is None:
            raise NotImplementedError("re-ingesting a changed file is implemented by T-M5-12")
        return e.reingest(e, en, n)

    rel = parsed.rel_path
    try:
        with file_transaction(env.db, env.registry):
            env.db.fault("file_start")
            env.db.fault(f"file_start:{rel}")
            write = handler(env, entry, ordinal)
            env.db.fault("file_written")
            env.db.fault(f"file_written:{rel}")
    except EnvError:  # database locked / corrupt: abort the whole run (exit 4)
        raise
    except Exception as exc:  # BR-17: only this file is affected
        return _fail_file(env, entry, exc)
    return FileOutcome(
        rel,
        entry.file_class,
        "ingested",
        write.source_file_id,
        items_new=write.items_new,
        items_merged=write.items_merged,
        items_updated=write.items_updated,
        items_unchanged=write.items_unchanged,
        possible_duplicates=write.possible_duplicates,
        already_done_in_note=write.already_done_in_note,
    )


def process_plan(env: IngestEnv, plan: FilePlan) -> list[FileOutcome]:
    """Run every file of the plan through its own transaction: ``ok`` files in plan order, then the
    ``empty`` and ``failed`` reporting lists. Fires ``after_file`` / ``after_file:<k>`` after each ``ok`` file."""
    outcomes: list[FileOutcome] = []
    for k, entry in enumerate(plan.entries, start=1):
        outcomes.append(process_entry(env, entry, k - 1))
        env.db.fault("after_file")
        env.db.fault(f"after_file:{k}")
    for entry in (*plan.empty, *plan.failed):
        outcomes.append(process_entry(env, entry, -1))
    return outcomes


# =============================================================================================
# SECTION 4 -- Persisting a newly seen file (T-M5-07; BR-04, BR-10, BR-19..21, 7.2, 10.1, 10.3)
# =============================================================================================
#
# ``persist_new_file`` (inside the file transaction, called by ``process_entry``):
#   1. attendees -> registry (``resolve_attendees``), then every item's owners in document order (the
#      registry grows by attendee and provisional people exactly as the M4 pipeline does);
#   2. ``persist_registry`` (people first: ItemOwner / MeetingAttendee reference them);
#   3. ``source_file`` row (reused when a never-ingested failed/empty row exists), ``meeting``,
#      ``meeting_attendee``, run-scoped warnings (parse warnings, W-MEETING-DATE-FUTURE computed here from
#      the injected today, W-ATTENDEE-NAME-INCOMPLETE, W-OWNER-MISSING, W-NAME-DIACRITIC);
#   4. per item, in document order, ``match_or_create`` (the matcher seam, below);
#   5. LAST statement: ``content_sha256`` / size / mtime / ``last_ingested_run`` of the file row.
#
# Owner rows (spec 7.2, G-C3..G-C5): ``owner_hook is None`` -> one unassigned row with ``owner_raw`` NULL and
# ``W-OWNER-MISSING``; a non-person owner (``Everyone``) -> one unassigned row with the raw text kept and no
# warning; otherwise one row per mention, owners first then ``with`` co-owners (stable), ordinals 0..n-1.
# The item's ``item_owner`` rows and the source's ``item_source_owner`` rows are the same list for a new item.
#
# The matcher seam (``match_or_create``): called once per parsed item, in document order, inside the file
# transaction, after the source_file / meeting rows and every earlier item of the same file exist. Contract:
# it must attach the item's source to exactly ONE action item -- either ``create_item(env, src)`` (a new
# item, returns ``MatchResult(outcome="new")``) or ``add_source(env, item_id, src)`` on an existing item
# (``outcome="merged"``) -- and return the ``MatchResult``. Anything it raises isolates the file. The default
# (until T-M5-08..10) always creates. Replace the BODY of ``match_or_create`` (or pass ``env.match``).


@dataclass(frozen=True, slots=True)
class FileCtx:
    """Per-file facts the matcher needs (rows already inserted)."""

    source_file_id: int
    meeting_id: int
    rel_path: str
    folder_root: str
    meeting_date: date | None
    run_id: int
    attendee_ids: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class NewSource:
    """One parsed item, fully resolved, ready to become (or join) an action item."""

    file: FileCtx
    item: ParsedItem
    owners: tuple[ResolvedOwner, ...]
    due: DueResult | None
    core_key: str
    core_tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatchResult:
    item_id: int
    source_id: int
    outcome: str  # "new" | "merged"
    possible_duplicates: int = 0


def _s(value: object) -> str | None:
    """Plain ``str`` of an enum / str value (None stays None)."""
    return None if value is None else str(getattr(value, "value", value))


def _due_columns(due: DueResult | None) -> dict[str, object]:
    if not isinstance(due, DueResult):
        return {"due_confidence": "none"}
    return {
        "due_raw": due.due_raw,
        "due_raw_other": due.due_raw_other,
        "due_date": due.due_date,
        "due_confidence": _s(due.due_confidence),
        "due_rule": _s(due.due_rule),
        "due_reason": _s(due.due_reason),
    }


def resolve_item_owners(
    item: ParsedItem, ctx: MeetingContext, registry: PeopleRegistry, cfg: Config
) -> tuple[tuple[ResolvedOwner, ...], tuple[tuple[str, str], ...]]:
    """Owners of one parsed item and the ingest warnings that came with them as ``(code, message-kwargs)``
    pairs ``(code, name)``. See the section comment for the row rules."""
    phrase = item.owner_hook
    if phrase is not None and phrase.nonperson_raw is not None:
        return (ResolvedOwner(0, phrase.nonperson_raw, None, OwnerStatus.UNASSIGNED),), ()
    if phrase is None or not phrase.mentions:
        return (ResolvedOwner(0, None, None, OwnerStatus.UNASSIGNED),), ((codes.W_OWNER_MISSING, item.task_text),)
    mentions = sorted(phrase.mentions, key=lambda m: m.role is OwnerRole.WITH)  # stable: owners, then with
    resolved = tuple(
        resolve_owner(m, ctx, registry, cfg, ordinal=k) for k, m in enumerate(mentions)
    )
    return resolved, ()


def add_source(env: IngestEnv, item_id: int, src: NewSource) -> int:
    """Attach ``src`` to ``item_id`` as an ``item_source`` row (+ its owner-phrase rows). Returns the source id."""
    it, f = src.item, src.file
    source_id = env.repo.insert_item_source(
        item_id=item_id,
        source_file_id=f.source_file_id,
        meeting_id=f.meeting_id,
        line_start=it.line_start,
        line_end=it.line_end,
        raw_text=it.raw_text,
        section_heading=it.section_heading,
        checkbox_state=it.checkbox_state,
        occurrence_index=it.occurrence_index,
        block_hash=it.block_hash,
        present=True,
        run_id=f.run_id,
        description=it.description,
        task_text=it.task_text,
        core_key=src.core_key,
        **_due_columns(src.due),  # type: ignore[arg-type]
    )
    env.repo.set_source_owners(source_id, src.owners)
    return source_id


def create_item(env: IngestEnv, src: NewSource) -> MatchResult:
    """New action item from one source (T-M5-07): fields of the (only, hence earliest) source, status
    from the checkbox (BR-10; ``[x]`` -> done, origin ``note``, ``completed_at`` NULL, evidence = this
    source), ``source_state=present``, ``item_owner`` rows, event ``created``."""
    repo, it, f = env.repo, src.item, src.file
    done = it.checkbox_state == "done"
    item_id = repo.insert_action_item(
        description=it.description,
        task_text=it.task_text,
        description_core_key=src.core_key,
        status="done" if done else "open",
        completion_origin="note" if done else None,
        source_state="present",
        **_due_columns(src.due),  # type: ignore[arg-type]
    )
    source_id = add_source(env, item_id, src)
    repo.touch_item(
        item_id,
        {"completion_evidence_source_id": source_id} if done else {},
        owners=src.owners,
    )
    repo.record_event(
        item_id,
        "created",
        None,
        {"source": f"{f.rel_path}:{it.line_start}", "status": "done" if done else "open"},
        env.run_id,
    )
    return MatchResult(item_id, source_id, "new")


def match_or_create(env: IngestEnv, src: NewSource) -> MatchResult:
    """The matcher seam (see the section comment). Matching is not implemented yet: always creates."""
    return create_item(env, src)


def persist_new_file(env: IngestEnv, entry: PlanEntry, ordinal: int) -> FileWrite:
    """Persist a file that has no stored data yet (see the section comment). Runs inside the file transaction."""
    parsed, prev, repo, registry = entry.parsed, entry.previous, env.repo, env.registry
    meeting = parsed.meeting
    assert meeting is not None, "an 'ok' parse result always carries a meeting"
    rel = parsed.rel_path
    # 1. people: attendees, then every owner in document order
    att = resolve_attendees(meeting.attendees, registry, ordinal=ordinal)
    mctx = att.context()
    per_item = [resolve_item_owners(it, mctx, registry, env.cfg) for it in parsed.items]
    # 2. people rows before the rows that reference them
    repo.persist_registry(registry)
    # 3. file, meeting, attendees, warnings
    if prev is None:
        sf_id = repo.insert_source_file(env.folder_root, rel, status="ok", run_id=env.run_id)
    else:  # a failed/empty row that never held data is reused
        sf_id = prev.id
        repo.update_source_file(sf_id, status="ok", failure_code=None, last_run_id=env.run_id)
    meeting_id = repo.insert_meeting(sf_id, meeting.date, meeting.date_source, meeting.title)
    repo.insert_attendees(meeting_id, [(r.raw_name, r.person_id, r.email_hint) for r in att.refs])
    for w in parsed.warnings:
        repo.add_warning(env.run_id, w.code, w.message, source_file_id=sf_id, line=w.line)
    if meeting.date is not None and meeting.date > env.today:
        repo.add_warning(
            env.run_id,
            codes.W_MEETING_DATE_FUTURE,
            _warning_message(codes.W_MEETING_DATE_FUTURE, rel, date=meeting.date.isoformat(), today=env.today.isoformat()),
            source_file_id=sf_id,
        )
    for ref in att.refs:
        for code in ref.warnings:
            repo.add_warning(
                env.run_id, code, _warning_message(code, rel, name=ref.raw_name), source_file_id=sf_id
            )
    fctx = FileCtx(sf_id, meeting_id, rel, env.folder_root, meeting.date, env.run_id, mctx.attendee_ids)
    # 4. items in document order through the matcher seam
    matcher = env.match or match_or_create
    totals = {"new": 0, "merged": 0, "dups": 0, "done": 0}
    for item, (owners, owner_warnings) in zip(parsed.items, per_item, strict=True):
        for code, task in owner_warnings:
            repo.add_warning(
                env.run_id,
                code,
                _warning_message(code, rel, item.line_start, task=task),
                source_file_id=sf_id,
                line=item.line_start,
            )
        for owner in owners:
            for code in owner.warnings:
                other = registry.full_name(owner.person_id) if owner.person_id else ""
                repo.add_warning(
                    env.run_id,
                    code,
                    _warning_message(code, rel, item.line_start, name=owner.owner_raw, other=other),
                    source_file_id=sf_id,
                    line=item.line_start,
                )
        key = core_key(item.task_text, fallback=item.description)
        due = item.due_hook if isinstance(item.due_hook, DueResult) else None
        result = matcher(env, NewSource(fctx, item, owners, due, key.key, key.tokens))
        totals["new" if result.outcome == "new" else "merged"] += 1
        totals["dups"] += result.possible_duplicates
        totals["done"] += item.checkbox_state == "done"
    # 5. last statement: the hash that makes the file "unchanged" next run
    repo.update_source_file(
        sf_id,
        content_sha256=parsed.content_sha256,
        size_bytes=parsed.size_bytes,
        mtime=_iso_mtime(parsed.mtime_ns),
        status="ok",
        failure_code=None,
        last_run_id=env.run_id,
        last_ingested_run=env.run_id,
    )
    return FileWrite(
        sf_id,
        items_new=totals["new"],
        items_merged=totals["merged"],
        possible_duplicates=totals["dups"],
        already_done_in_note=totals["done"],
    )


# =============================================================================================
# SECTION 6 (interim) -- driver ``run_ingest``; T-M5-13/14/17 own the final orchestration
# =============================================================================================
#
# Implemented here so the engine can be exercised end to end: discover -> (session: lock, run row) ->
# registry from the database + roster -> parse every file -> plan -> ``process_plan`` -> counters -> finish.
# NOT yet done (later tasks): ``plan.missing`` handling (T-M5-13), the whole-database passes of 10.7.9
# (T-M5-14), dry-run reporting (T-M5-17), the report text (T-M5-15). Discovery warnings are stored as
# run-level ``ingest_warning`` rows (``source_file_id`` NULL).


@dataclass(frozen=True, slots=True)
class IngestResult:
    run: RunHandle
    run_row: IngestRunRow
    counters: RunCounters
    outcomes: tuple[FileOutcome, ...]
    plan: FilePlan
    discovery_warnings: tuple[ParseWarning, ...]

    @property
    def exit_code(self) -> int:
        """5 when at least one file failed (spec 4.1), else 0."""
        return int(ExitCode.INGEST_FILE_FAILURES) if self.counters.files_failed else int(ExitCode.OK)


def run_ingest(
    db_path: str | os.PathLike[str],
    folder: str | os.PathLike[str],
    *,
    cfg: Config,
    run_ctx: RunContext,
    recursive: bool = True,
    dry_run: bool = False,
    fault_hook: Callable[[str], None] | None = None,
    match: Callable[[IngestEnv, NewSource], MatchResult] | None = None,
    reingest: Callable[[IngestEnv, PlanEntry, int], FileWrite] | None = None,
) -> IngestResult:
    """Ingest ``folder`` into the database at ``db_path`` (interim driver, see the section comment).

    A bad folder raises ``FolderError`` (exit 4) before the database is opened. A second concurrent writer
    raises ``EnvError`` (exit 4) from the lock. ``fault_hook`` is installed on the database after the run
    row and the roster are committed (so a transaction-counting hook sees only the per-file transactions)."""
    discovery = discover(folder, recursive)
    root = discovery.folder_root
    with ingest_session(db_path, root, dry_run=dry_run, clock=run_ctx.clock) as session:
        db, repo, run_id = session.db, session.repo, session.run.run_id
        registry = repo.load_registry(cfg.roster)
        with db.transaction():
            repo.persist_registry(registry)  # the roster (10.3), idempotent
            for w in discovery.warnings:
                repo.add_warning(run_id, w.code, w.message)
        db.fault_hook = fault_hook
        analyzer = OwnerAnalyzer(is_known_name=known_name_predicate(registry))
        parsed = [
            parse_file(f, cfg.limits.max_file_bytes, date_order=cfg.date_order, analyzer=analyzer)
            for f in discovery.files
        ]
        plan = plan_files(parsed, repo.list_source_files(root), folder_root=root, recursive=recursive)
        env = IngestEnv(db, repo, registry, run_id, root, run_ctx.today, cfg, match, reingest)
        outcomes = process_plan(env, plan)
        counters = RunCounters()
        for outcome in outcomes:
            counters.record(outcome)
        assert counters.invariant_holds(), "discovered = ingested + unchanged + empty + failed"
        row = finish_run(db, repo, run_id, counters)
        return IngestResult(session.run, row, counters, tuple(outcomes), plan, discovery.warnings)
