"""Parsing layer: pure document parsing assembled from the parsing modules (T-M2-15, T-M3-09).

Public API
----------
``parse_document(source, rel_path=None, *, date_order=None, max_file_bytes=5_242_880,
                 long_line_limit=LONG_LINE_LIMIT, analyzer=None) -> ParsedFile``
    The single pure entry point.  ``source`` is a ``reader.RawRead`` (bytes + hash/size/mtime as read)
    or plain ``bytes`` (then ``rel_path`` is required; size = ``len``, hash computed, mtime ``None``).
    The result is a function of the bytes, ``rel_path``, ``date_order``, the limits and the analyzer
    only: no clock, no filesystem (``open``/``os.stat`` are never touched).  Never raises for a
    problem inside one file:

    * a byte-level problem gives ``status="failed"`` with ``E-BINARY`` / ``E-ENCODING`` /
      ``E-TOO-LARGE`` (the size limit is checked here for bytes input too);
    * 0 bytes, BOM only or whitespace only gives ``status="empty"`` with exactly ``W-EMPTY-FILE``;
    * any unexpected exception (including one raised by the analyzer) gives ``status="failed"`` with
      ``E-INTERNAL-PARSE``; the traceback is kept in ``FileFailure.traceback`` (BR-17);
    * otherwise ``status="ok"``: ``meeting`` (``parse_meeting``), ``items`` (document order, with
      ``task_text`` derived from the analyzer's owner + due spans, ``owner_hook`` / ``due_hook`` set
      from the analysis), ``candidates`` and ``warnings`` (front matter / fences / comments, ignored
      checkbox regions, checkbox warnings, candidates, long lines, meeting warnings, item-level due
      warnings, ``W-NO-ITEMS`` when no item was imported), sorted by ``(line or 0, code, detail)``.
      ``W-MEETING-DATE-FUTURE`` is never produced here (ingest service, G-20).

    ``analyzer`` defaults to ``DueAnalyzer()`` (real due resolution, no owner spans until M4);
    pass ``NullAnalyzer()`` to leave both hooks ``None``.  The analyzer receives the item's full
    cleaned ``description`` (never ``raw_text``) and an ``AnalyzerContext(meeting_date, date_order)``.
    The due hook holds a ``dates.DueResult``; its ``W-DATE-NOT-DUE`` and ``W-DUE-BEFORE-MEETING``
    warnings are attached to ``item.line_start`` here (the parsers emit them, Appendix B; the
    ingest service does not need to recompute them).

``parse_file(discovered, max_file_bytes=5_242_880, *, date_order=None, long_line_limit=...,
             analyzer=None, opener=None, stat_fn=None) -> ParsedFile``
    ``reader.read_bytes`` + ``parse_document``; a reader failure (``E-UNREADABLE``, ``E-TOO-LARGE``)
    becomes a failed ``ParsedFile``; never raises.

Also re-exported: ``ItemAnalyzer``, ``NullAnalyzer``, ``DueAnalyzer``, ``AnalyzerContext``,
``ItemAnalysis``, ``OwnerSpanProvider``, ``NullOwnerSpans``.
"""

from __future__ import annotations

import hashlib
import traceback
from collections.abc import Callable
from dataclasses import replace
from typing import IO, Any

from actiontrack import codes
from actiontrack.domain.models import (
    FileFailure,
    ParsedFile,
    ParsedItem,
    ParseWarning,
    parse_message,
)
from actiontrack.parsing import markdown as _md
from actiontrack.parsing import reader as _reader
from actiontrack.parsing.discovery import DiscoveredFile
from actiontrack.parsing.items import (
    AnalyzerContext,
    DueAnalyzer,
    ItemAnalysis,
    ItemAnalyzer,
    NullAnalyzer,
    NullOwnerSpans,
    OwnerSpanProvider,
    derive_task_text,
    extract_items,
    item_due_warnings,
)
from actiontrack.parsing.meeting import parse_meeting
from actiontrack.parsing.reader import LONG_LINE_LIMIT, RawRead

__all__ = [
    "parse_document",
    "parse_file",
    "ItemAnalyzer",
    "NullAnalyzer",
    "DueAnalyzer",
    "AnalyzerContext",
    "ItemAnalysis",
    "OwnerSpanProvider",
    "NullOwnerSpans",
    "DEFAULT_MAX_FILE_BYTES",
]

DEFAULT_MAX_FILE_BYTES = 5_242_880  # config.Limits.max_file_bytes default


def _failed(rel_path: str, failure: FileFailure, raw: RawRead | None) -> ParsedFile:
    return ParsedFile(
        rel_path=rel_path,
        status="failed",
        failure=failure,
        content_sha256=raw.content_sha256 if raw else None,
        size_bytes=raw.size_bytes if raw else None,
        mtime_ns=raw.mtime_ns if raw else None,
    )


def _warning_key(w: ParseWarning) -> tuple[int, str, str]:
    return (w.line or 0, w.code, w.detail or "")


def _parse_text(
    raw: RawRead,
    text: str,
    date_order: str | None,
    long_line_limit: int,
    analyzer: ItemAnalyzer,
) -> ParsedFile:
    rel = raw.rel_path
    lines = _reader.split_lines(text)
    scan = _md.scan_document(lines, rel)
    meeting = parse_meeting(scan, rel, date_order, rel)
    ctx = AnalyzerContext(meeting.meta.date, date_order)

    analyses: dict[str, ItemAnalysis] = {}

    def analysis_of(description: str) -> ItemAnalysis:
        got = analyses.get(description)
        if got is None:
            got = analyses[description] = analyzer.analyze(description, ctx)
        return got

    def task_text(description: str) -> str:
        a = analysis_of(description)
        return derive_task_text(description, [*a.owner_spans, *a.due_spans])

    scanned = extract_items(scan, rel, task_text)
    items: list[ParsedItem] = []
    warnings: list[ParseWarning] = [
        *scan.warnings,
        *_md.ignored_region_warnings(scan.annotation, rel),
        *scanned.warnings,
        *meeting.warnings,
        *_reader.find_long_lines(lines, rel, long_line_limit),
    ]
    for item in scanned.items:
        a = analysis_of(item.description)
        if a.owner is not None or a.due is not None:
            item = replace(item, owner_hook=a.owner, due_hook=a.due)
        items.append(item)
        warnings.extend(item_due_warnings(item, meeting.meta.date, rel))
    candidates = _md.find_candidates(scan.annotation, rel)
    warnings.extend(candidates.warnings)
    if not items:
        warnings.append(ParseWarning(codes.W_NO_ITEMS, None, parse_message(codes.W_NO_ITEMS, rel)))
    warnings.sort(key=_warning_key)
    return ParsedFile(
        rel_path=rel,
        status="ok",
        content_sha256=raw.content_sha256,
        size_bytes=raw.size_bytes,
        mtime_ns=raw.mtime_ns,
        meeting=meeting.meta,
        items=tuple(items),
        candidates=candidates.candidates,
        warnings=tuple(warnings),
    )


def parse_document(
    source: RawRead | bytes,
    rel_path: str | None = None,
    *,
    date_order: str | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    long_line_limit: int = LONG_LINE_LIMIT,
    analyzer: ItemAnalyzer | None = None,
) -> ParsedFile:
    """Parse one file's bytes into a ``ParsedFile`` (pure, never raises; see module docstring)."""
    if isinstance(source, RawRead):
        raw = source
        if rel_path is not None and rel_path != raw.rel_path:
            raw = replace(raw, rel_path=rel_path)
    else:
        if rel_path is None:
            raise TypeError("rel_path is required when parsing raw bytes")
        data = bytes(source)
        raw = RawRead(rel_path, data, hashlib.sha256(data).hexdigest(), len(data), None)  # type: ignore[arg-type]
    rel = raw.rel_path
    try:
        if len(raw.data) > max_file_bytes:
            return _failed(
                rel,
                FileFailure(
                    codes.E_TOO_LARGE,
                    parse_message(codes.E_TOO_LARGE, rel, size=len(raw.data), limit=max_file_bytes),
                    size_bytes=len(raw.data),
                ),
                raw,
            )
        decoded = _reader.classify_and_decode(raw.data, rel)
        if isinstance(decoded, FileFailure):
            return _failed(rel, decoded, raw)
        if isinstance(decoded, _reader.Empty):
            return ParsedFile(
                rel_path=rel,
                status="empty",
                content_sha256=raw.content_sha256,
                size_bytes=raw.size_bytes,
                mtime_ns=raw.mtime_ns,
                warnings=(ParseWarning(codes.W_EMPTY_FILE, None, parse_message(codes.W_EMPTY_FILE, rel)),),
            )
        return _parse_text(raw, decoded.text, date_order, long_line_limit, analyzer or DueAnalyzer())
    except Exception as exc:  # failure isolation (BR-17): one file never takes the run down
        return _failed(
            rel,
            FileFailure(
                codes.E_INTERNAL_PARSE,
                parse_message(codes.E_INTERNAL_PARSE, rel),
                os_reason=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            ),
            raw,
        )


def parse_file(
    discovered: DiscoveredFile,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    *,
    date_order: str | None = None,
    long_line_limit: int = LONG_LINE_LIMIT,
    analyzer: ItemAnalyzer | None = None,
    opener: Callable[[str], IO[bytes]] | None = None,
    stat_fn: Callable[[str], Any] | None = None,
) -> ParsedFile:
    """Read one discovered file and parse it; every failure is returned, never raised."""
    try:
        read = _reader.read_bytes(discovered, max_file_bytes, opener=opener, stat_fn=stat_fn)
    except Exception as exc:
        return _failed(
            discovered.rel_path,
            FileFailure(
                codes.E_INTERNAL_PARSE,
                parse_message(codes.E_INTERNAL_PARSE, discovered.rel_path),
                os_reason=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            ),
            None,
        )
    if isinstance(read, FileFailure):
        return _failed(discovered.rel_path, read, None)
    return parse_document(
        read,
        date_order=date_order,
        max_file_bytes=max_file_bytes,
        long_line_limit=long_line_limit,
        analyzer=analyzer,
    )
