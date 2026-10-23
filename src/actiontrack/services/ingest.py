"""Ingest orchestration (spec 4.2, FR-01, FR-08, 10.7; plan 2.3 ``services/ingest.py``).

This module is built up by several M5 tasks; each owns one clearly delimited SECTION below and appends
its public names to this docstring. Keep the sections independent (later ones may use earlier ones,
never the reverse). No SQL lives here (it stays in ``store/repo.py``), no ``datetime.now``.

Sections
--------
1. Ordering and the file plan (T-M5-03)  -- pure, implemented
2. Run lifecycle, lock, interrupted runs (T-M5-04)            -- later
3. Per-file transaction engine / report counters (T-M5-05)     -- later
4. Persisting a new file (T-M5-07)                             -- later
5. Matching integration, changed files, missing files (T-M5-08..13) -- later
6. Whole-database passes and finalisation (T-M5-14)            -- later

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
"""

from __future__ import annotations

import enum
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from actiontrack.domain.models import ParsedFile, ingest_order_key
from actiontrack.store.repo import SourceFileRow

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
