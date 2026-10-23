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
5. Matching integration (T-M5-08..11/22) -- implemented; 5b. changed files, missing files, source_state (T-M5-12/13) -- implemented
6. 6a. Whole-database owner re-resolution persisted (T-M4-15); 6b. finalisation (T-M5-14), dry run (T-M5-17), driver ``run_ingest`` -- implemented

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

Section 5 public API (T-M5-08/09/10/11/22; spec 10.6, 10.6.1, 10.7.3): ``match_or_create`` now does Tier-1b
(``try_tier1b``, T-M5-10: a done source completes all matching open items, evidence holder vs ``CI``) -> Tier-1 merge (``merge_source(env, item_id, src)``) -> else new item +
``possible_duplicate`` links (``MatchResult.links`` = ``(other_item_id, reason)`` pairs, ``possible_duplicates`` their
count). ``recompute_canonical(repo, item_id, run_id)`` re-derives description/due/owners from the sources (call it after
any source change); ``load_source_views(repo, item_id)``.
T-M5-11: ``apply_status_effects(env, item_id, change: EvidenceChange | None) -> StatusEffects`` (reopen rule, sticky
manual done; called by T-M5-12 per changed source row), ``manual_done_conflicts(repo) -> list[int]`` and
``emit_status_conflicts(repo, run_id)`` (W-STATUS-CONFLICT whole-database pass, T-M5-14 step 4).

Section 5b public API (T-M5-12/13; spec 10.7.2-10.7.5, 10.7.8): ``reingest_file(env, entry, ordinal) -> FileWrite`` (the
default ``IngestEnv.reingest``: pairing via ``domain.matching.pair_sources`` incl. ``present=0`` rows, edits, moves,
removal, restore, status effects), ``recompute_source_state(repo, item_ids=None) -> list[int]`` (``missing`` iff all
sources absent; call with None for the whole DB), ``mark_missing_files(env, rows) -> list[MissingOutcome]`` (rows =
``FilePlan.missing``; ``run_ingest`` calls it after ``process_plan`` and returns it as ``IngestResult.missing``).
Fault points: ``reingest_paired|removed|new[:rel]``, ``missing_start|written[:rel]``.

Section 6a public API (T-M4-15; spec 10.3 step 6, 10.7.9, H-05): ``reresolve_database(repo, cfg, run_id) ->
ReResolutionApplied`` (= ``load_owner_snapshot(repo)`` -> pure ``re_resolve`` -> ``apply_reresolution(repo, result,
run_id)``; caller owns the transaction). ``ReResolutionApplied(merges, attendee_changes, changes, created_people,
warnings)``: ``changes`` are canonical item-level ``OwnerChange`` rows (``merged_or_completed_under_earlier_resolution``
set on an H-05 downgrade), ``warnings`` = ``(code, item_id | None, message)`` as written; ``.downgrades``,
``.is_empty``. ``merged_or_completed(repo, item_id)``, ``H05_NOTE``. See the section comment for the meeting-context
decision (per source) and the apply order.

Section 6b public API (T-M5-14 / T-M5-17): ``finalise_run(env, counters) -> (Finalisation, IngestRunRow)`` (one
transaction: re-resolution, ``recompute_source_state``, ``emit_status_conflicts``, state counter
``resolved_via_attendance``, ``finalize_run``; failure rolls back only itself and closes the run ``partial`` with an
``E-INTERNAL-PARSE`` run warning), ``run_ingest(db_path, folder, *, cfg, run_ctx, recursive=True, dry_run=False,
fault_hook=None, match=None, reingest=None) -> IngestResult`` (discover -> session -> registry -> parse all -> plan ->
``process_plan`` -> ``mark_missing_files`` -> ``finalise_run``). ``IngestResult`` adds ``finalisation``,
``.reresolution``, ``.conflicts``, ``.finalisation_error``, ``.dry_run``; ``exit_code`` is 5 when a file failed else 0.
``--dry-run`` runs the identical pipeline on an in-memory copy (no file, run row, lock or side file is written).
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import os
import traceback
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from actiontrack import codes
from actiontrack.clock import Clock, RunContext
from actiontrack.config import Config
from actiontrack.domain.dates import DueResult
from actiontrack.domain.canonical import OwnerView, SourceView, select_canonical
from actiontrack.domain.matching import (
    ItemView,
    NewView,
    all_resolved,
    NewEntry,
    OldSource,
    classify_pair,
    core_key,
    owner_person_set,
    pair_sources,
    pick_tier1,
    pick_tier1b,
)
from actiontrack.domain.status import (
    EvidenceChange,
    ItemStatus,
    conflicting_manual_done,
    derive_status,
    reopens,
)
from actiontrack.domain.models import (
    OwnerRole,
    OwnerStatus,
    ParsedFile,
    ParsedItem,
    ParseWarning,
    Person,
    Provenance,
    ResolvedOwner,
    format_item_id,
    ingest_order_key,
    parse_message,
)
from actiontrack.errors import EnvError, ExitCode
from actiontrack.parsing import parse_file
from actiontrack.parsing.discovery import discover
from actiontrack.parsing.owner_phrase import OwnerAnalyzer
from actiontrack.resolution.owners import (
    KIND_CHANGED,
    KIND_DOWNGRADED,
    KIND_MERGE_PROVISIONAL,
    KIND_RESOLVED,
    AttendeeChange,
    MeetingContext,
    OwnerChange,
    ReResolutionResult,
    ReResolutionSnapshot,
    StoredAttendee,
    StoredMeeting,
    StoredOwner,
    re_resolve,
    resolve_attendees,
    resolve_owner,
)
from actiontrack.resolution.people import MergeRecord, PeopleRegistry, file_transaction, known_name_predicate
from actiontrack.store.db import Database
from actiontrack.store.repo import IngestRunRow, ItemSourceRow, Repo, SourceFileRow, to_storage_text

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
        return (e.reingest or reingest_file)(e, en, n)

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
    #: the possible_duplicate links this call created as ``(other_item_id, reason)`` (T-M5-09)
    links: tuple[tuple[int, str], ...] = ()


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


# --------------------------------------------------------------------------- matching (T-M5-08/09/22)
#
# ``match_or_create`` order of play for one parsed item (document order, inside the file transaction):
#   0. EXTENSION POINT ``try_tier1b`` (T-M5-10): a dateless/differently-dated ``[x]`` source that completes
#      earlier open copies runs BEFORE Tier-1 and may return a ``MatchResult``. Until it lands it returns
#      None, so a done source is handled by Tier-1 only (10.6.1 row 5: existing open + new done with the same
#      due or both undated) and otherwise falls to the ``possible_duplicate`` links below.
#   1. candidates = items sharing the ``core_key`` (index lookup, not a scan), ascending id.
#   2. Tier-1 (all six conditions, ``domain.matching.pick_tier1``): the lowest-id compatible candidate gets the
#      source (``merge_source``): ``source_added`` + ``merged`` events, status effect of 10.6.1 row 5, then
#      ``recompute_canonical`` (T-M5-22). A merge creates no new links (pair links between items exist already
#      from when the second of the two was created, and they are never altered).
#   3. otherwise a NEW item, plus one ``possible_duplicate`` link per candidate pair that has a reason
#      (``classify_pair``: G-27 precedence) and per fuzzy candidate (same resolved owner set, Jaccard >=
#      ``limits.fuzzy_threshold``, found through the owner person bucket). Links never change items.


def _owner_view(o: object) -> OwnerView:
    return OwnerView(
        getattr(o, "owner_raw", None),
        _s(getattr(o, "role", "owner")) or "owner",
        getattr(o, "person_id", None),
        _s(getattr(o, "status", None) or getattr(o, "owner_status", None)) or "unassigned",
        _s(getattr(o, "provenance", None)) or "none",
        tuple(getattr(o, "candidates", ()) or ()),
    )


def load_source_views(repo: Repo, item_id: int) -> list[SourceView]:
    """The item's sources (present or not) as pure ``SourceView`` data, in source-id order."""
    out: list[SourceView] = []
    for c in repo.list_source_context(item_id):
        s = c.source
        out.append(
            SourceView(
                source_id=s.id,
                rel_path=c.rel_path,
                line_start=s.line_start,
                meeting_date=c.meeting_date,
                source_file_id=s.source_file_id,
                present=s.present,
                description=s.description or "",
                task_text=s.task_text or "",
                core_key=s.core_key or "",
                checkbox_state=s.checkbox_state,
                due_raw=s.due_raw,
                due_raw_other=s.due_raw_other,
                due_date=s.due_date,
                due_confidence=s.due_confidence,
                due_rule=s.due_rule,
                due_reason=s.due_reason,
                owners=tuple(_owner_view(o) for o in repo.list_source_owners(s.id)),
            )
        )
    return out


def recompute_canonical(
    repo: Repo,
    item_id: int,
    run_id: int | None,
    fresh_owners: Mapping[int, Sequence[ResolvedOwner]] | None = None,
    *,
    owner_event: bool = True,
) -> dict[str, tuple[object, object]]:
    """Re-derive an item's canonical description / task text / core key / due / owners from its sources
    (``domain.canonical.select_canonical``) and write them through ``touch_item`` (T-M5-22).

    ``updated_at`` moves only when something changed; events: ``updated_from_source`` (description / due) and
    ``owner_changed`` (owners, before/after). ``status`` is never written here (module docstring of
    ``canonical``). Owner ``candidates`` (not stored per source) are carried over from the current item rows
    when the phrase is unchanged, or taken from ``fresh_owners`` (``{source_id: resolved owners}`` of a source
    that was just re-resolved). ``owner_event=False`` writes the owner rows without the ``owner_changed`` event
    (the whole-database re-resolution records its own event with the full before / after, T-M4-15). Returns what
    changed (idempotent: a second call returns ``{}``)."""
    views = load_source_views(repo, item_id)
    if not views:
        return {}
    sel = select_canonical(views)
    current = repo.list_item_owners(item_id)

    fresh = list(fresh_owners.get(sel.driver_source_id, ())) if fresh_owners else []

    def carry(o: OwnerView) -> OwnerView:
        for r in fresh:  # a just-resolved owner of the driving source brings its candidates (T-M5-12)
            if (r.owner_raw, _s(r.role), r.person_id, _s(r.status)) == (o.owner_raw, o.role, o.person_id, o.status):
                return OwnerView(o.owner_raw, o.role, o.person_id, o.status, o.provenance, tuple(r.candidates))
        for row in current:
            if (row.owner_raw, row.role, row.person_id, row.owner_status) == (
                o.owner_raw,
                o.role,
                o.person_id,
                o.status,
            ):
                return OwnerView(o.owner_raw, o.role, o.person_id, o.status, o.provenance, row.candidates)
        return o

    changed = dict(repo.touch_item(item_id, sel.item_columns(), event_kind="updated_from_source", run_id=run_id))
    changed.update(
        repo.touch_item(
            item_id,
            None,
            owners=tuple(carry(o) for o in sel.owners),
            event_kind="owner_changed" if owner_event else None,
            run_id=run_id,
        )
    )
    return changed


def _load_item_view(repo: Repo, item_id: int) -> ItemView:
    row = repo.get_action_item(item_id)
    assert row is not None, f"candidate item {item_id} vanished"
    return ItemView(
        item_id,
        tuple(row.description_core_key.split()),
        row.status,
        tuple(_owner_view(o) for o in repo.list_item_owners(item_id)),
        tuple(load_source_views(repo, item_id)),
        row.due_date,
        row.due_confidence,
    )


def _new_view(src: NewSource) -> NewView:
    cols = _due_columns(src.due)
    return NewView(
        src.file.source_file_id,
        src.file.meeting_date,
        src.core_tokens,
        tuple(_owner_view(o) for o in src.owners),
        cols.get("due_date"),  # type: ignore[arg-type]
        str(cols["due_confidence"]),
        "done" if src.item.checkbox_state == "done" else "open",
    )


def _complete_from_note(
    env: IngestEnv, item_id: int, evidence_source_id: int, where: str, raw_text: str, **extra: object
) -> None:
    """Set an open item done by note evidence (origin ``note``, ``completed_at`` NULL, evidence recorded) and
    log ``status_changed`` citing the evidence (file:line and raw text). The status effect itself is decided by
    ``domain.status.derive_status``; this only writes it."""
    env.repo.touch_item(
        item_id,
        {
            "status": "done",
            "completion_origin": "note",
            "completed_at": None,
            "completion_evidence_source_id": evidence_source_id,
        },
    )
    env.repo.record_event(
        item_id,
        "status_changed",
        {"status": "open"},
        {"status": "done", "completion_origin": "note", "evidence": where, "raw_text": raw_text, **extra},
        env.run_id,
    )


def try_tier1b(env: IngestEnv, src: NewSource) -> MatchResult | None:
    """Tier-1b completion match (T-M5-10; spec 10.6, G-31). Runs before Tier-1; returns a ``MatchResult`` when it
    handled the source, else None (the source then goes through Tier-1 / links).

    A new ``[x]`` source completes EVERY open item that is the same obligation (``pick_tier1b``: identical
    core of >= 3 tokens, no recurrence word, identical resolved owners, other file, within the window, the
    done note not earlier than the item's latest source; the due date is ignored). The source is attached to the
    evidence holder (the most recent matching item, ties by highest id): ``source_added`` + ``merged``
    (``tier`` ``1b``) + ``status_changed`` events. Every other matching item becomes done through its own
    ``status_changed`` event, an ``ItemLink completed_by`` (that item -> holder) and shows ``CI``. All of them
    record the new source as ``completion_evidence_source_id``. Nothing is deleted or reopened and items that
    are already done (manual or note) never match. ``MatchResult(outcome="merged")``: counted as merged."""
    if src.item.checkbox_state != "done" or not src.core_tokens:
        return None
    repo = env.repo
    new = _new_view(src)
    candidates = [_load_item_view(repo, i) for i in repo.item_ids_by_core_key(src.core_key)]
    found = pick_tier1b(new, candidates, window_days=env.cfg.limits.dup_window_days)
    if found is None:
        return None
    holder = found.holder.item_id
    f, it = src.file, src.item
    where = f"{f.rel_path}:{it.line_start}"
    source_id = add_source(env, holder, src)
    repo.record_event(holder, "source_added", None, {"source": where, "source_id": source_id}, env.run_id)
    repo.record_event(holder, "merged", None, {"source": where, "source_id": source_id, "tier": "1b"}, env.run_id)
    _complete_from_note(env, holder, source_id, where, it.raw_text)
    for other in found.others:
        _complete_from_note(env, other.item_id, source_id, where, it.raw_text, completed_by=holder)
        repo.insert_link(other.item_id, holder, "completed_by", None, env.run_id)
    recompute_canonical(repo, holder, env.run_id)
    return MatchResult(holder, source_id, "merged")


def merge_source(env: IngestEnv, item_id: int, src: NewSource) -> MatchResult:
    """Tier-1 merge effect (10.6.1): attach the source, ``source_added`` + ``merged`` events, existing open +
    new ``[x]`` -> done (origin ``note``, ``completed_at`` NULL, evidence = the new source, ``status_changed``),
    then ``recompute_canonical``. No source row is ever deleted or rewritten (BR-08)."""
    repo, f, it = env.repo, src.file, src.item
    prior = repo.get_action_item(item_id)
    assert prior is not None
    source_id = add_source(env, item_id, src)
    where = f"{f.rel_path}:{it.line_start}"
    repo.record_event(item_id, "source_added", None, {"source": where, "source_id": source_id}, env.run_id)
    repo.record_event(item_id, "merged", None, {"source": where, "source_id": source_id, "tier": "1"}, env.run_id)
    if prior.status == "open" and it.checkbox_state == "done":
        _complete_from_note(env, item_id, source_id, where, it.raw_text)
    recompute_canonical(repo, item_id, env.run_id)
    return MatchResult(item_id, source_id, "merged")


# --------------------------------------------------------------------------- status effects (T-M5-11)
#
# ``apply_status_effects`` is called by changed-file reconciliation (T-M5-12) AFTER it has updated the changed
# ``ItemSource`` row (checkbox, ``present``) and its meeting date, once per (item, changed source row). The pairing
# step describes the row with an ``EvidenceChange`` (``domain.status``). Decisions are pure (``derive_status``,
# ``reopens``); this function only writes them. Manual done is never touched (a contradicting ``[ ]`` is the
# whole-database ``W-STATUS-CONFLICT`` pass below). Evidence-line removal never reopens (G-C24).


@dataclass(frozen=True, slots=True)
class StatusEffects:
    """What ``apply_status_effects`` did: items reopened (holder first, then its dependents, ascending id)
    and whether the item itself became done."""

    reopened: tuple[int, ...] = ()
    completed: bool = False


def _item_status(row: object) -> ItemStatus:
    return ItemStatus(
        getattr(row, "status"),
        getattr(row, "completion_origin"),
        getattr(row, "completion_evidence_source_id"),
    )


def _source_location(repo: Repo, source_id: int) -> tuple[int, str, int]:
    """``(source_file_id, rel_path, line_start)`` of an item source."""
    s = repo.get_item_source(source_id)
    assert s is not None
    f = repo.get_source_file_by_id(s.source_file_id)
    assert f is not None
    return s.source_file_id, f.rel_path, s.line_start


def _reopen(env: IngestEnv, item_id: int, evidence_source_id: int, *, inferred_from: int | None = None) -> None:
    repo = env.repo
    sf_id, rel, line = _source_location(repo, evidence_source_id)
    where = f"{rel}:{line}"
    repo.touch_item(
        item_id,
        {"status": "open", "completion_origin": None, "completed_at": None, "completion_evidence_source_id": None},
    )
    after: dict[str, object] = {"status": "open", "reopened_by": where}
    if inferred_from is not None:
        after["reopened_with"] = inferred_from
    repo.record_event(
        item_id, "status_changed", {"status": "done", "completion_origin": "note"}, after, env.run_id
    )
    repo.add_warning(
        env.run_id,
        codes.W_STATUS_REOPENED,
        _warning_message(codes.W_STATUS_REOPENED, rel, line, item=format_item_id(item_id)),
        source_file_id=sf_id,
        line=line,
    )


def apply_status_effects(env: IngestEnv, item_id: int, change: EvidenceChange | None = None) -> StatusEffects:
    """Apply the status effect of one changed source row to ``item_id`` (see the section comment).

    * manual done: nothing, ever;
    * note completion: reopened iff ``domain.status.reopens`` (same source row as the evidence, now ``[ ]``,
      later-dated); then every item completed from that evidence (``completed_by`` links to this item, done,
      origin ``note``, same evidence source) is reopened too, each with its own ``status_changed`` event and
      ``W-STATUS-REOPENED``; any other change leaves it done;
    * open item: becomes done (origin ``note``, evidence = the changed source) only when ``derive_status``
      (latest present source is ``[x]``) says so AND that source is the changed one. An open item is never
      completed as a side effect of an unrelated edit, and a reopened item is not re-derived in the same step."""
    if change is None:
        return StatusEffects()
    repo = env.repo
    row = repo.get_action_item(item_id)
    assert row is not None, f"unknown action item {item_id}"
    state = _item_status(row)
    if state.is_manual_done:
        return StatusEffects()
    if state.is_note_done:
        if not reopens(state, change):
            return StatusEffects()
        evidence = state.evidence_source_id
        assert evidence is not None
        dependents = [
            link.from_item_id
            for link in repo.links_of(item_id)
            if link.kind == "completed_by" and link.to_item_id == item_id
        ]
        reopened = [item_id]
        _reopen(env, item_id, evidence)
        for dep_id in sorted(set(dependents)):
            dep = repo.get_action_item(dep_id)
            if dep is not None and _item_status(dep).is_note_done and dep.completion_evidence_source_id == evidence:
                _reopen(env, dep_id, evidence, inferred_from=item_id)
                reopened.append(dep_id)
        return StatusEffects(tuple(reopened))
    decision = derive_status(state, load_source_views(repo, item_id))
    if decision.status == "done" and decision.evidence_source_id == change.source_id:
        _, rel, line = _source_location(repo, change.source_id)
        src_row = repo.get_item_source(change.source_id)
        assert src_row is not None
        _complete_from_note(env, item_id, change.source_id, f"{rel}:{line}", src_row.raw_text)
        return StatusEffects(completed=True)
    return StatusEffects()


def manual_done_conflicts(repo: Repo) -> list[int]:
    """Ids (ascending) of manual-done items whose latest present source is ``[ ]`` (10.7.9 b, AC-03.6). Whole
    database, independent of file hashes; read-only."""
    out: list[int] = []
    for row in repo.list_action_items(status="done"):
        state = _item_status(row)
        if state.is_manual_done and conflicting_manual_done(state, load_source_views(repo, row.id)):
            out.append(row.id)
    return out


def emit_status_conflicts(repo: Repo, run_id: int) -> list[int]:
    """Write one ``W-STATUS-CONFLICT`` run warning per conflicting manual-done item (once per run per item,
    also when its note is unchanged) and return their ids. Called by the finalisation (T-M5-14 step 4)."""
    ids = manual_done_conflicts(repo)
    already = {w.message for w in repo.list_warnings(run_id) if w.code == codes.W_STATUS_CONFLICT}
    for item_id in ids:
        message = codes.REGISTRY[codes.W_STATUS_CONFLICT].format(item=format_item_id(item_id))
        if message not in already:
            repo.add_warning(run_id, codes.W_STATUS_CONFLICT, message)
    return ids


def match_or_create(env: IngestEnv, src: NewSource) -> MatchResult:
    """The matcher seam: Tier-1b hook, Tier-1 merge, else a new item plus possible_duplicate links
    (see the comment block above for the order of play)."""
    handled = try_tier1b(env, src)
    if handled is not None:
        return handled
    repo, lim = env.repo, env.cfg.limits
    new = _new_view(src)
    candidate_ids = repo.item_ids_by_core_key(src.core_key) if src.core_tokens else []
    candidates = [_load_item_view(repo, i) for i in candidate_ids]
    target = pick_tier1(new, candidates, window_days=lim.dup_window_days)
    if target is not None:
        return merge_source(env, target.item_id, src)
    result = create_item(env, src)
    pool = list(candidates)
    if src.core_tokens and all_resolved(new.owners):
        seen = set(candidate_ids)
        pool += [
            _load_item_view(repo, i)
            for i in repo.item_ids_by_owner_persons(owner_person_set(new.owners))
            if i not in seen and i != result.item_id
        ]
    links: list[tuple[int, str]] = []
    for other in sorted(pool, key=lambda v: v.item_id):
        reason = classify_pair(new, other, window_days=lim.dup_window_days, fuzzy_threshold=lim.fuzzy_threshold)
        if reason is not None and repo.insert_link(
            result.item_id, other.item_id, "possible_duplicate", str(reason.value), env.run_id
        ):
            links.append((other.item_id, str(reason.value)))
    return MatchResult(result.item_id, result.source_id, "new", len(links), tuple(links))


def _emit_item_warnings(
    env: IngestEnv,
    sf_id: int,
    rel: str,
    item: ParsedItem,
    owners: Sequence[ResolvedOwner],
    owner_warnings: Sequence[tuple[str, str]],
) -> None:
    """Run-scoped warnings of one item's owner resolution (W-OWNER-MISSING, W-NAME-DIACRITIC, ...)."""
    repo = env.repo
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
            other = env.registry.full_name(owner.person_id) if owner.person_id else ""
            repo.add_warning(
                env.run_id,
                code,
                _warning_message(code, rel, item.line_start, name=owner.owner_raw, other=other),
                source_file_id=sf_id,
                line=item.line_start,
            )


def _new_source(fctx: FileCtx, item: ParsedItem, owners: Sequence[ResolvedOwner]) -> NewSource:
    key = core_key(item.task_text, fallback=item.description)
    due = item.due_hook if isinstance(item.due_hook, DueResult) else None
    return NewSource(fctx, item, tuple(owners), due, key.key, key.tokens)


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
        _emit_item_warnings(env, sf_id, rel, item, owners, owner_warnings)
        result = matcher(env, _new_source(fctx, item, owners))
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
# SECTION 5b -- Changed files, removed/restored lines, missing files, source_state (T-M5-12/13)
# =============================================================================================
#
# ``reingest_file`` (default ``IngestEnv.reingest``, runs inside the file transaction) reconciles a CHANGED file:
#
#   1. meeting row (date / source / title) and attendees (replaced only when the raw-name list changed);
#   2. ``domain.matching.pair_sources`` pairs the parsed entries with ALL of the file's ``item_source`` rows,
#      ``present=0`` ones included (10.7.2 a-c, G-24);
#   3. paired rows are rewritten from the note (lines, raw text / hash, checkbox, description, task text, core
#      key, due, owners). A pure move (lines only) writes only the row: no event, the item is untouched. An edit
#      goes through ``recompute_canonical`` (one ``updated_from_source`` event when a canonical field moved, else
#      one source-level ``updated_from_source`` event); a ``present=0`` row that is paired is REVIVED
#      (``present=1``, event ``source_added`` with ``restored``) and never becomes a new item or a ``same_file`` link;
#   4. a checkbox change (or a revival) is handed to ``apply_status_effects`` with an ``EvidenceChange`` carrying
#      the old and new meeting date (reopen rule 10.7.3, sticky manual done);
#   5. unpaired old rows become ``present=0`` (event ``source_removed``; rows are never deleted);
#   6. unpaired new entries go through the matcher seam as new sources (Tier-1b / Tier-1 / links);
#   7. ``recompute_source_state`` for every item whose sources changed; the file's hash is written LAST.
#
# Owners of a pair are re-resolved (the normal owner path) unless the pair is a verbatim (a) match and the
# attendee list did not change: identical text never fights the whole-database re-resolution pass (10.7.9).
# Not redone for paired entries: Tier-1b / duplicate matching (documented simplification).
#
# Fault points (``db.fault``): ``reingest_paired[:<rel>]`` after the paired rows were written,
# ``reingest_removed[:<rel>]`` after the removals, ``reingest_new[:<rel>]`` after the new entries (before the
# hash); plus the engine's ``file_start`` / ``file_written``. Any exception rolls back the whole file.

_DUE_KEYS = ("due_raw", "due_raw_other", "due_date", "due_confidence", "due_rule", "due_reason")
_CONTENT_KEYS = ("checkbox_state", "description", "task_text", "core_key", *_DUE_KEYS)


def _full_due(due: DueResult | None) -> dict[str, object]:
    """All six due columns (explicit ``None`` where absent) so an update clears stale values."""
    cols = _due_columns(due)
    return {k: cols.get(k) for k in _DUE_KEYS} | {"due_confidence": cols.get("due_confidence", "none")}


def _norm(value: object, raw: bool = False) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and not raw:
        return to_storage_text(value)
    return value


def _json_value(value: object) -> object:
    return value.isoformat() if isinstance(value, date) else value


@dataclass(slots=True)
class _ItemChange:
    """What happened to one item's sources in a changed file."""

    content: dict[str, tuple[object, object]] = dataclasses.field(default_factory=dict)
    sources: list[int] = dataclasses.field(default_factory=list)
    revived: list[int] = dataclasses.field(default_factory=list)
    removed: list[int] = dataclasses.field(default_factory=list)
    status_changes: list[EvidenceChange] = dataclasses.field(default_factory=list)
    fresh: dict[int, tuple[ResolvedOwner, ...]] = dataclasses.field(default_factory=dict)
    event_source: int | None = None


def _update_paired_source(
    env: IngestEnv,
    row: ItemSourceRow,
    it: ParsedItem,
    resolved: tuple[tuple[ResolvedOwner, ...], tuple[tuple[str, str], ...]] | None,
    old_date: date | None,
    new_date: date | None,
    rel: str,
    ch: _ItemChange,
) -> None:
    """Rewrite one paired ``item_source`` row from the note and record what changed in ``ch``."""
    repo = env.repo
    due = it.due_hook if isinstance(it.due_hook, DueResult) else None
    key = core_key(it.task_text, fallback=it.description)
    new_cols: dict[str, object] = {
        "line_start": it.line_start,
        "line_end": it.line_end,
        "raw_text": it.raw_text,
        "section_heading": it.section_heading,
        "occurrence_index": it.occurrence_index,
        "block_hash": it.block_hash,
        "checkbox_state": it.checkbox_state,
        "description": it.description,
        "task_text": it.task_text,
        "core_key": key.key,
        **_full_due(due),
    }
    diff: dict[str, object] = {}
    content: dict[str, tuple[object, object]] = {}
    for column, new in new_cols.items():
        old = getattr(row, column)
        if _norm(old, column == "raw_text") != _norm(new, column == "raw_text"):
            diff[column] = new
            if column in _CONTENT_KEYS:
                content[column] = (old, new)
    revived = not row.present
    if revived:
        diff["present"] = True
    diff["last_seen_run"] = env.run_id
    repo.update_item_source(row.id, **diff)
    ch.sources.append(row.id)
    if resolved is not None:
        owners = resolved[0]
        before = [o.owner_raw for o in repo.list_source_owners(row.id)]
        if repo.set_source_owners(row.id, owners):
            content["owners"] = (before, [o.owner_raw for o in owners])
        ch.fresh[row.id] = tuple(owners)
    if content:
        ch.event_source = ch.event_source or row.id
        for k, v in content.items():
            ch.content.setdefault(k, v)
    if revived:
        ch.revived.append(row.id)
        repo.record_event(
            row.item_id,
            "source_added",
            None,
            {"source": f"{rel}:{it.line_start}", "source_id": row.id, "note": "restored"},
            env.run_id,
        )
    if revived or "checkbox_state" in content:
        ch.status_changes.append(EvidenceChange(row.id, it.checkbox_state, old_date, new_date, True))


def reingest_file(env: IngestEnv, entry: PlanEntry, ordinal: int) -> FileWrite:
    """Reconcile a changed file with its stored data (section comment above). Runs inside the file transaction."""
    parsed, prev, repo, registry = entry.parsed, entry.previous, env.repo, env.registry
    assert prev is not None, "reingest_file needs a stored source_file row"
    meeting = parsed.meeting
    assert meeting is not None, "an 'ok' parse result always carries a meeting"
    rel, sf_id = parsed.rel_path, prev.id
    stored_meeting = repo.get_meeting_for_file(sf_id)
    assert stored_meeting is not None, "reingest_file needs a stored meeting row"
    meeting_id = stored_meeting.id

    # 1. meeting row and attendees
    wanted = {"meeting_date": meeting.date, "date_source": meeting.date_source, "title": meeting.title}
    changed_meeting = {k: v for k, v in wanted.items() if _norm(getattr(stored_meeting, k)) != _norm(v)}
    if changed_meeting:
        repo.update_meeting(meeting_id, **changed_meeting)
    stored_att = repo.list_attendees(meeting_id)
    names_changed = {r["raw_name"] for r in stored_att} != {to_storage_text(a.raw_name) for a in meeting.attendees}
    att = None
    if names_changed:
        att = resolve_attendees(meeting.attendees, registry, ordinal=ordinal)
        mctx = att.context()
        repo.persist_registry(registry)
        repo.replace_attendees(meeting_id, [(r.raw_name, r.person_id, r.email_hint) for r in att.refs])
    else:  # keep the stored (possibly pass-refreshed) person ids: nothing to re-resolve
        ids = frozenset(r["person_id"] for r in stored_att if r["person_id"])
        mctx = MeetingContext(ids if stored_att else None)
    for w in parsed.warnings:
        repo.add_warning(env.run_id, w.code, w.message, source_file_id=sf_id, line=w.line)
    if meeting.date is not None and meeting.date > env.today:
        repo.add_warning(
            env.run_id,
            codes.W_MEETING_DATE_FUTURE,
            _warning_message(codes.W_MEETING_DATE_FUTURE, rel, date=meeting.date.isoformat(), today=env.today.isoformat()),
            source_file_id=sf_id,
        )
    if att is not None:
        for ref in att.refs:
            for code in ref.warnings:
                repo.add_warning(env.run_id, code, _warning_message(code, rel, name=ref.raw_name), source_file_id=sf_id)

    # 2. pairing against ALL stored rows of the file
    old_rows = {r.id: r for r in repo.list_item_sources(source_file_id=sf_id)}
    result = pair_sources(
        [
            OldSource(r.id, r.block_hash, r.occurrence_index, r.description or "", r.section_heading, r.line_start, r.present)
            for r in old_rows.values()
        ],
        [
            NewEntry(i, it.block_hash, it.occurrence_index, it.description, it.section_heading, it.line_start)
            for i, it in enumerate(parsed.items)
        ],
    )
    verbatim = {p.new_index for p in result.pairs if p.rule == "hash"}
    needs_owners = [i for i in range(len(parsed.items)) if i not in verbatim or names_changed]
    resolved = {i: resolve_item_owners(parsed.items[i], mctx, registry, env.cfg) for i in needs_owners}
    repo.persist_registry(registry)  # people rows before the rows that reference them
    for i in needs_owners:
        owners, owner_warnings = resolved[i]
        _emit_item_warnings(env, sf_id, rel, parsed.items[i], owners, owner_warnings)

    # 3. paired rows
    changes: dict[int, _ItemChange] = {}
    for pair in result.pairs:
        row, it = old_rows[pair.old_id], parsed.items[pair.new_index]
        ch = changes.setdefault(row.item_id, _ItemChange())
        _update_paired_source(
            env, row, it, resolved.get(pair.new_index), stored_meeting.meeting_date, meeting.date, rel, ch
        )
    env.db.fault("reingest_paired")
    env.db.fault(f"reingest_paired:{rel}")

    # 5. removed lines (never deleted): present=0 + source_removed
    for old_id in result.old_only:
        row = old_rows[old_id]
        if not row.present:
            continue
        repo.update_item_source(row.id, present=False)
        changes.setdefault(row.item_id, _ItemChange()).removed.append(row.id)
        repo.record_event(
            row.item_id, "source_removed", None, {"source": f"{rel}:{row.line_start}", "source_id": row.id}, env.run_id
        )
    env.db.fault("reingest_removed")
    env.db.fault(f"reingest_removed:{rel}")

    # item level: canonical fields, status effects, source state
    updated: set[int] = set()
    unchanged: set[int] = set()
    for item_id in sorted(changes):
        ch = changes[item_id]
        if ch.content or ch.revived or ch.removed:
            updated.add(item_id)
            canonical = recompute_canonical(repo, item_id, env.run_id, ch.fresh)
            if ch.content and not canonical:
                src_id = ch.event_source or ch.sources[0]
                src_row = repo.get_item_source(src_id)
                assert src_row is not None
                repo.record_event(
                    item_id,
                    "updated_from_source",
                    {k: _json_value(old) for k, (old, _new) in ch.content.items()},
                    {
                        "source": f"{rel}:{src_row.line_start}",
                        "source_id": src_id,
                        **{k: _json_value(new) for k, (_old, new) in ch.content.items()},
                    },
                    env.run_id,
                )
        else:
            unchanged.add(item_id)
        for change in ch.status_changes:
            effects = apply_status_effects(env, item_id, change)
            if effects.reopened or effects.completed:
                updated.add(item_id)
                unchanged.discard(item_id)
    updated.update(recompute_source_state(repo, [i for i, c in changes.items() if c.revived or c.removed]))
    unchanged -= updated

    # 6. new entries: the normal matcher path, document order
    fctx = FileCtx(sf_id, meeting_id, rel, env.folder_root, meeting.date, env.run_id, mctx.attendee_ids)
    matcher = env.match or match_or_create
    totals = {"new": 0, "merged": 0, "dups": 0, "done": 0}
    for i in result.new_only:
        item = parsed.items[i]
        res = matcher(env, _new_source(fctx, item, resolved[i][0]))
        totals["new" if res.outcome == "new" else "merged"] += 1
        totals["dups"] += res.possible_duplicates
        totals["done"] += item.checkbox_state == "done"
    env.db.fault("reingest_new")
    env.db.fault(f"reingest_new:{rel}")

    # 7. last statement: the hash that makes the file "unchanged" next run
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
        items_updated=len(updated),
        items_unchanged=len(unchanged),
        possible_duplicates=totals["dups"],
        already_done_in_note=totals["done"],
    )


# --------------------------------------------------------------------------- source_state (T-M5-13, 10.7.4)


def recompute_source_state(repo: Repo, item_ids: Iterable[int] | None = None) -> list[int]:
    """``source_state`` of the given items (all items when None): ``missing`` iff the item has sources and
    none is ``present`` (flag ``SM``), else ``present``. Status is never touched; items are never deleted
    (BR-16). Writes only through ``touch_item`` (``updated_at`` moves only on a real change), so it is
    idempotent. Returns the ids whose state changed (ascending)."""
    out: list[int] = []
    for item_id, has_present in sorted(repo.source_presence(item_ids).items()):
        if repo.touch_item(item_id, {"source_state": "present" if has_present else "missing"}):
            out.append(item_id)
    return out


# --------------------------------------------------------------------------- missing files (T-M5-13, 10.7.5)
#
# ``mark_missing_files(env, plan.missing)``: for every stored row of THIS folder_root that the scan did not find
# and that is inside the scan scope (``plan_files`` already applied the scope rule: under ``--no-recursive``
# only top-level rows) -- one transaction per file: ``source_file.status='missing'`` (hash kept), its present
# sources become ``present=0`` (event ``source_removed`` with ``reason: file_missing``), run warning
# ``W-FILE-MISSING`` and ``recompute_source_state`` for the affected items. Items are never deleted nor closed.
# A row that never held data and only ``failed`` is marked quietly (no warning). A missing file that comes
# back (same or changed content) is class ``changed`` and is reconciled by ``reingest_file``: identical lines
# re-pair with their rows (revived, no new items). Fault points ``missing_start[:<rel>]``,
# ``missing_written[:<rel>]``.


@dataclass(frozen=True, slots=True)
class MissingOutcome:
    """Result of marking one stored file missing. ``failure_code`` is set when the file's transaction failed
    (rolled back, the row is left as it was and retried by the next run)."""

    rel_path: str
    source_file_id: int
    sources_marked: int = 0
    items_missing: tuple[int, ...] = ()
    warned: bool = True
    failure_code: str | None = None
    traceback: str | None = None


def _mark_file_missing(env: IngestEnv, row: SourceFileRow) -> MissingOutcome:
    repo, rel = env.repo, row.rel_path
    had_data = _has_data(repo, row)
    marked = repo.list_item_sources(source_file_id=row.id, present=True)
    for s in marked:
        repo.update_item_source(s.id, present=False)
        repo.record_event(
            s.item_id,
            "source_removed",
            None,
            {"source": f"{rel}:{s.line_start}", "source_id": s.id, "reason": "file_missing"},
            env.run_id,
        )
    repo.update_source_file(row.id, status="missing", failure_code=None, last_run_id=env.run_id)
    warn = had_data or row.status in ("ok", "empty")
    if warn:
        repo.add_warning(
            env.run_id,
            codes.W_FILE_MISSING,
            codes.REGISTRY[codes.W_FILE_MISSING].format(path=rel),
            source_file_id=row.id,
        )
    became = recompute_source_state(repo, {s.item_id for s in marked})
    return MissingOutcome(rel, row.id, len(marked), tuple(became), warn)


def mark_missing_files(env: IngestEnv, rows: Iterable[SourceFileRow]) -> list[MissingOutcome]:
    """Mark each stored file missing in its own transaction (section comment). ``rows`` = ``FilePlan.missing``;
    call after ``process_plan``. One file's failure never stops the run (``EnvError`` / ``BaseException`` excepted)."""
    out: list[MissingOutcome] = []
    for row in rows:
        rel = row.rel_path
        try:
            with file_transaction(env.db, env.registry):
                env.db.fault("missing_start")
                env.db.fault(f"missing_start:{rel}")
                outcome = _mark_file_missing(env, row)
                env.db.fault("missing_written")
                env.db.fault(f"missing_written:{rel}")
        except EnvError:
            raise
        except Exception as exc:
            code = codes.E_INTERNAL_PARSE
            trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            with env.db.transaction():
                env.repo.add_warning(env.run_id, code, parse_message(code, rel), source_file_id=row.id)
            outcome = MissingOutcome(rel, row.id, warned=False, failure_code=code, traceback=trace)
        out.append(outcome)
    return out


# =============================================================================================
# SECTION 6a -- Whole-database owner re-resolution, persisted (T-M4-15; spec 10.3 step 6, 10.7.9, H-05, G-08)
# =============================================================================================
#
# ``reresolve_database(repo, cfg, run_id)`` = ``load_owner_snapshot`` -> ``re_resolve`` (pure, T-M4-11) ->
# ``apply_reresolution``. It runs inside ONE transaction owned by the caller (the finalisation, T-M5-14, or a test).
#
# Meeting context for merged items (decision): the pure pass keys owners by (id, ordinal) and takes ONE meeting
# per owner. The snapshot is therefore built from the PER-SOURCE owner rows (``item_source_owner``), with the
# synthetic ``StoredOwner.item_id`` = the ``item_source.id`` and the meeting of THAT source: every phrase is
# re-resolved exactly as it was at ingest time, in the attendee context of the note it was written in (an item
# merged from a note with an attendee list and one without keeps both verdicts per source). The canonical
# ``item_owner`` rows are then rebuilt by ``recompute_canonical`` from the latest present source (the owner
# driver, ``domain.canonical``), so the item shows the verdict of its canonical source. ``item_source_owner``
# stores no candidates: for an ambiguous source row they are taken from the item's canonical row with the same
# (phrase, role) when there is one, else empty; a change that differs only in such candidates is no row change.
#
# Apply order (``apply_reresolution``): registry persisted (created people, roster changes, aliases) -> provisional
# merges (``Repo.merge_person``: ItemOwner, ItemSourceOwner, MeetingAttendee and draft rows repointed, alias kept,
# provisional person deleted) -> attendee refresh -> per-source owner rows -> ``recompute_canonical(owner_event=False)`` for
# every affected item -> ONE ``owner_changed`` event per item whose canonical owners really changed (before / after
# = ``{"owners": [[raw, role, person, status, candidates, provenance], ...]}``; the H-05 note is added to ``after``
# as ``"note"``) and the warnings ``W-OWNER-NOW-AMBIGUOUS`` (once per item per run, file:line of the item's first
# source) / ``W-NAME-DIACRITIC``. Nothing else is written: ``item_source`` rows, links, status and completion are
# never touched (H-05: merges and completions are never undone; only the owner link changes). Idempotent: a second
# pass over the result finds nothing. ``updated_at`` moves only for items whose owners really changed.

H05_NOTE = "merged/completed under an earlier owner resolution"


@dataclass(frozen=True, slots=True)
class ReResolutionApplied:
    """What ``apply_reresolution`` persisted. ``changes`` are CANONICAL item-level rows (one ``OwnerChange`` per
    changed ``item_owner`` row, ``merged_or_completed_under_earlier_resolution`` set for H-05 downgrades);
    ``warnings`` are ``(code, item_id | None, message)`` as written to ``ingest_warning``."""

    merges: tuple[MergeRecord, ...] = ()
    attendee_changes: tuple[AttendeeChange, ...] = ()
    changes: tuple[OwnerChange, ...] = ()
    created_people: tuple[Person, ...] = ()
    warnings: tuple[tuple[str, int | None, str], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.merges or self.attendee_changes or self.changes or self.created_people)

    @property
    def downgrades(self) -> tuple[OwnerChange, ...]:
        return tuple(c for c in self.changes if codes.W_OWNER_NOW_AMBIGUOUS in c.warnings)


def load_owner_snapshot(repo: Repo) -> ReResolutionSnapshot:
    """Whole-database snapshot for the pure pass (see the section comment; all folder roots, explicit order)."""
    attendees: dict[int, list[StoredAttendee]] = {}
    for row in repo.list_all_attendees():
        attendees.setdefault(row["meeting_id"], []).append(
            StoredAttendee(row["raw_name"], row["person_id"], row["email_hint"])
        )
    meetings = tuple(StoredMeeting(mid, tuple(rows)) for mid, rows in sorted(attendees.items()))
    canonical: dict[tuple[int, str | None, str], tuple[str, ...]] = {}
    for o in repo.list_all_item_owners():
        if o.owner_status == "ambiguous":
            canonical[(o.item_id, o.owner_raw, o.role)] = tuple(o.candidates)
    sources = {s.id: s for s in repo.list_item_sources()}
    owners: list[StoredOwner] = []
    for row in repo.list_all_source_owners():
        src = sources[row.item_source_id]
        owners.append(
            StoredOwner(
                item_id=row.item_source_id,
                ordinal=row.ordinal,
                meeting_id=src.meeting_id,
                owner_raw=row.owner_raw,
                status=OwnerStatus(row.owner_status),
                person_id=row.person_id,
                candidates=canonical.get((src.item_id, row.owner_raw, row.role), ()),
                provenance=Provenance(row.provenance),
                role=OwnerRole(row.role),
            )
        )
    return ReResolutionSnapshot(meetings, tuple(owners))


def _owner_rows(repo: Repo) -> dict[int, list[tuple[object, ...]]]:
    out: dict[int, list[tuple[object, ...]]] = {}
    for r in repo.list_all_item_owners():
        out.setdefault(r.item_id, []).append(
            (r.owner_raw, r.role, r.person_id, r.owner_status, tuple(sorted(r.candidates)), r.provenance)
        )
    return out


def merged_or_completed(repo: Repo, item_id: int) -> bool:
    """H-05 flag: the item was merged (Tier-1 ``merged`` event) or takes part in a Tier-1b completion
    (``completed_by`` link at either end), so its sources / status stem from an earlier owner resolution."""
    if any(e.kind == "merged" for e in repo.list_events(item_id)):
        return True
    return any(link.kind == "completed_by" for link in repo.links_of(item_id))


def _item_warning(
    repo: Repo, run_id: int, item_id: int, code: str, suffix: str = "", **kw: object
) -> tuple[str, int | None, str] | None:
    """One run warning anchored at the item's first source; deduped per (run, message, file)."""
    sources = repo.list_item_sources(item_id)
    if sources:
        first = sources[0]
        sf = repo.get_source_file_by_id(first.source_file_id)
        sf_id, rel, line = first.source_file_id, (sf.rel_path if sf else "?"), first.line_start
    else:
        sf_id, rel, line = None, format_item_id(item_id), None
    message = _warning_message(code, rel, line, **kw) + suffix
    if any(w.code == code and w.message == message and w.source_file_id == sf_id for w in repo.list_warnings(run_id)):
        return None
    repo.add_warning(run_id, code, message, source_file_id=sf_id, line=line)
    return (code, item_id, message)


def _kind(old: tuple[object, ...], new: tuple[object, ...], merged_ids: set[str]) -> str:
    if old[2] in merged_ids:
        return KIND_MERGE_PROVISIONAL
    if (old[3], new[3]) == ("resolved", "ambiguous"):
        return KIND_DOWNGRADED
    if (old[3], new[3]) == ("ambiguous", "resolved"):
        return KIND_RESOLVED
    return KIND_CHANGED


def apply_reresolution(repo: Repo, result: ReResolutionResult, run_id: int) -> ReResolutionApplied:
    """Persist a ``ReResolutionResult`` computed by ``re_resolve(load_owner_snapshot(repo), ...)`` (section
    comment). The caller owns the transaction."""
    before = _owner_rows(repo)
    repo.persist_registry(result.registry)  # first: created people and a new roster person must exist as rows
    for m in result.merges:
        repo.merge_person(m.provisional_id, m.into_person_id, alias=m.alias, alias_key=m.alias_key)
    warnings: list[tuple[str, int | None, str]] = []
    for ac in result.attendee_changes:
        if repo.set_attendee_person(int(ac.meeting_id), ac.raw_name, ac.new_person_id):
            sf_id = repo.meeting_source_file_id(int(ac.meeting_id))
            sf = None if sf_id is None else repo.get_source_file_by_id(sf_id)
            for code in ac.warnings:
                msg = _warning_message(code, sf.rel_path if sf else "?", name=ac.raw_name)
                repo.add_warning(run_id, code, msg, source_file_id=sf_id)
                warnings.append((code, None, msg))
    # per-source owner rows (the synthetic item_id of a pure-pass change is the source id)
    sources = {s.id: s for s in repo.list_item_sources()}
    by_source: dict[int, dict[int, OwnerChange]] = {}
    for c in result.changes:
        by_source.setdefault(c.item_id, {})[c.ordinal] = c
    fresh: dict[int, list[ResolvedOwner]] = {}
    affected: set[int] = set()
    for source_id in sorted(by_source):
        new_rows: list[ResolvedOwner] = []
        for row in repo.list_source_owners(source_id):
            c = by_source[source_id].get(row.ordinal)
            if c is None:
                new_rows.append(
                    ResolvedOwner(
                        row.ordinal,
                        row.owner_raw,
                        row.person_id,
                        OwnerStatus(row.owner_status),
                        (),
                        Provenance(row.provenance),
                        OwnerRole(row.role),
                    )
                )
                continue
            owner = ResolvedOwner(
                row.ordinal,
                row.owner_raw,
                c.new_person_id,
                c.new_status,
                c.new_candidates,
                c.new_provenance,
                OwnerRole(row.role),
            )
            new_rows.append(owner)
            fresh.setdefault(source_id, []).append(owner)  # only re-resolved rows carry fresh candidates
        repo.set_source_owners(source_id, new_rows)
        affected.add(sources[source_id].item_id)
    after_merge = _owner_rows(repo)
    affected |= {i for i in set(before) | set(after_merge) if before.get(i) != after_merge.get(i)}
    for item_id in sorted(affected):
        mine = {sid: owners for sid, owners in fresh.items() if sources[sid].item_id == item_id}
        recompute_canonical(repo, item_id, run_id, mine, owner_event=False)
    after = _owner_rows(repo)
    merged_ids = {m.provisional_id for m in result.merges}
    changes: list[OwnerChange] = []
    for item_id in sorted(affected):
        old, new = before.get(item_id, []), after.get(item_id, [])
        if old == new:
            continue
        flag = merged_or_completed(repo, item_id)
        repo.bump_updated_at(item_id)
        downgraded: list[str] = []
        for ordinal, n in enumerate(new):
            o = old[ordinal] if ordinal < len(old) else (n[0], n[1], None, "unassigned", (), "none")
            if o == n:
                continue
            kind = _kind(o, n, merged_ids)
            is_down = kind == KIND_DOWNGRADED
            if is_down:
                downgraded.append(str(n[0]))
            changes.append(
                OwnerChange(
                    item_id=item_id,
                    ordinal=ordinal,
                    owner_raw=n[0],  # type: ignore[arg-type]
                    role=OwnerRole(n[1]),
                    kind=kind,
                    old_status=OwnerStatus(o[3]),
                    old_person_id=o[2],  # type: ignore[arg-type]
                    old_candidates=tuple(o[4]),  # type: ignore[arg-type]
                    old_provenance=Provenance(o[5]),
                    new_status=OwnerStatus(n[3]),
                    new_person_id=n[2],  # type: ignore[arg-type]
                    new_candidates=tuple(n[4]),  # type: ignore[arg-type]
                    new_provenance=Provenance(n[5]),
                    warnings=(codes.W_OWNER_NOW_AMBIGUOUS,) if is_down else (),
                    merged_or_completed_under_earlier_resolution=flag and is_down,
                )
            )
            if n[5] == Provenance.DIACRITIC_FOLD.value and o[5] != n[5] and n[2] is not None:
                w = _item_warning(
                    repo, run_id, item_id, codes.W_NAME_DIACRITIC,
                    name=n[0], other=result.registry.full_name(str(n[2])),
                )
                if w:
                    warnings.append(w)
        payload: dict[str, object] = {"owners": new}
        if flag and downgraded:
            payload["note"] = H05_NOTE
        repo.record_event(item_id, "owner_changed", {"owners": old}, payload, run_id)
        if downgraded:
            w = _item_warning(
                repo, run_id, item_id, codes.W_OWNER_NOW_AMBIGUOUS,
                f" ({H05_NOTE})" if flag else "", name=", ".join(downgraded),
            )
            if w:
                warnings.append(w)
    return ReResolutionApplied(
        result.merges, result.attendee_changes, tuple(changes), result.created_people, tuple(warnings)
    )


def reresolve_database(repo: Repo, cfg: Config, run_id: int) -> ReResolutionApplied:
    """Snapshot -> pure ``re_resolve`` with the CURRENT roster and config -> ``apply_reresolution`` (one call)."""
    registry = repo.load_registry(cfg.roster)
    result = re_resolve(load_owner_snapshot(repo), registry, cfg)
    return apply_reresolution(repo, result, run_id)


# =============================================================================================
# SECTION 6b -- Finalisation (T-M5-14), dry run (T-M5-17) and the driver ``run_ingest``
# =============================================================================================
#
# ``run_ingest``: discover -> session (lock + run row; dry run: in-memory copy, no lock, no file) -> registry from
# the database + roster -> parse every file -> plan -> ``process_plan`` (one transaction per file) ->
# ``mark_missing_files`` -> ``finalise_run`` (ONE transaction) -> ``IngestResult``.
#
# ``finalise_run(env, counters)`` runs, in this order, inside a single transaction, on EVERY ingest (also when
# every file was ``unchanged``, 10.7.9 / G-08):
#   1. whole-database owner re-resolution (``reresolve_database``: current roster, aliases, known people and
#      ``attendee_disambiguation``), persisted with ``owner_changed`` events and ``W-OWNER-NOW-AMBIGUOUS`` /
#      ``W-NAME-DIACRITIC`` warnings;
#   2. ``recompute_source_state`` for the whole database (``missing`` iff no present source);
#   3. ``emit_status_conflicts`` (``W-STATUS-CONFLICT``, whole database);
#   4. ``counters.resolved_via_attendance`` := STATE count of canonical owner rows with provenance
#      ``attendee_in_meeting`` over the whole database (all folder roots, open and done items; namesake groups
#      only because only step 3a writes that provenance, AC-05.18) -- not a run delta;
#   5. ``finalize_run``: counters, status ``complete`` / ``partial`` (>= 1 failed file), ``finished_at``.
# Display names are not recomputed here: the registry derives them from stored spellings on demand (no step).
#
# An exception inside this transaction rolls back ONLY the finalisation (files already committed stay). The run is
# then closed in a small second transaction as ``partial`` with the file counters and a run warning
# ``E-INTERNAL-PARSE`` ("finalisation failed ..."), and ``IngestResult.finalisation_error`` carries the message
# (T-M5-18 decides the exit code; the block says exit 4). ``EnvError`` and ``BaseException`` (a kill) propagate and
# leave the run ``in_progress`` for the next run to settle. Fault points: ``finalise_start`` (before step 1),
# ``finalise_written`` (after step 4).
#
# Dry run (``dry_run=True``): ``ingest_session`` opens ``Database.open_dry_run`` (a read-only ``sqlite3.backup``
# of the real file into ``:memory:``; an absent file starts from an empty in-memory schema): the SAME pipeline
# runs against the copy, including both whole-database passes. Nothing reaches the disk: no database, no run row,
# no ``<db>.lock``, no ``-wal`` / ``-shm`` side file, no stale-run marking. The run id is the copy's "next id".
# ``IngestResult.reresolution`` / ``.conflicts`` list what the passes WOULD change.


@dataclass(frozen=True, slots=True)
class Finalisation:
    """What the finalisation transaction did (also what a dry run would do)."""

    reresolution: ReResolutionApplied = ReResolutionApplied()
    source_state_changed: tuple[int, ...] = ()
    conflicts: tuple[int, ...] = ()
    error: str | None = None


def finalise_run(env: IngestEnv, counters: RunCounters) -> tuple[Finalisation, IngestRunRow]:
    """The finalisation transaction (section comment). On failure: rollback of the finalisation only, the run is
    closed ``partial`` with a run warning and ``Finalisation.error`` is set."""
    db, repo = env.db, env.repo
    try:
        with db.transaction():
            db.fault("finalise_start")
            applied = reresolve_database(repo, env.cfg, env.run_id)
            state = recompute_source_state(repo)
            conflicts = emit_status_conflicts(repo, env.run_id)
            counters.resolved_via_attendance = repo.count_owner_rows(Provenance.ATTENDEE_IN_MEETING.value)
            db.fault("finalise_written")
            row = finalize_run(repo, env.run_id, counters)
        return Finalisation(applied, tuple(state), tuple(conflicts)), row
    except EnvError:
        raise
    except Exception as exc:
        message = f"finalisation failed ({type(exc).__name__}: {exc}); files already ingested are kept"
        with db.transaction():
            repo.add_warning(env.run_id, codes.E_INTERNAL_PARSE, message)
            repo.update_ingest_run(env.run_id, **counters.as_columns())
            repo.update_ingest_run(env.run_id, status="partial", finished_at=repo.now_iso())
        failed = repo.get_ingest_run(env.run_id)
        assert failed is not None
        return Finalisation(error=message), failed


@dataclass(frozen=True, slots=True)
class IngestResult:
    run: RunHandle
    run_row: IngestRunRow
    counters: RunCounters
    outcomes: tuple[FileOutcome, ...]
    plan: FilePlan
    discovery_warnings: tuple[ParseWarning, ...]
    #: stored files of this folder not found by the scan (T-M5-13); empty when none
    missing: tuple[MissingOutcome, ...] = ()
    #: the finalisation transaction (T-M5-14); in a dry run: what it would have done
    finalisation: Finalisation = Finalisation()

    @property
    def reresolution(self) -> ReResolutionApplied:
        return self.finalisation.reresolution

    @property
    def conflicts(self) -> tuple[int, ...]:
        """Item ids that got ``W-STATUS-CONFLICT`` in this run."""
        return self.finalisation.conflicts

    @property
    def finalisation_error(self) -> str | None:
        return self.finalisation.error

    @property
    def dry_run(self) -> bool:
        return self.run.dry_run

    @property
    def exit_code(self) -> int:
        """5 when at least one file failed (spec 4.1), else 0 (T-M5-18 owns the final mapping)."""
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
    """Ingest ``folder`` into the database at ``db_path`` (section comment above).

    A bad folder raises ``FolderError`` (exit 4) before the database is opened. A second concurrent writer
    raises ``EnvError`` (exit 4) from the lock (never for a dry run). ``fault_hook`` is installed on the database
    after the run row and the roster are committed (so a transaction-counting hook sees only the per-file
    transactions, then the finalisation)."""
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
        missing = mark_missing_files(env, plan.missing)
        counters = RunCounters()
        for outcome in outcomes:
            counters.record(outcome)
        assert counters.invariant_holds(), "discovered = ingested + unchanged + empty + failed"
        final, row = finalise_run(env, counters)
        return IngestResult(
            session.run, row, counters, tuple(outcomes), plan, discovery.warnings, tuple(missing), final
        )
