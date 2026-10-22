"""Folder discovery (spec 4.2, FR-01.1, FR-01.11; T-M2-02, T-M2-03).

Public API
----------
``discover(folder, recursive=True, *, scandir=None) -> DiscoveryResult``
``DiscoveryResult(folder_root, files, warnings, subfolder_md_count)``
``DiscoveredFile(rel_path, abs_path, size, mtime_ns)``
``FolderError(path, os_reason)``   (exit 4, an ``EnvError``): root missing / not a directory / unreadable

Rules
-----
* ``folder_root`` is ``os.path.realpath(folder)``; ``rel_path`` is relative to it, always ``/``-separated.
* A file is included only if it is a regular file (``follow_symlinks=False``) whose name ends with
  ``.md`` (case-insensitive). Directories named ``x.md`` and ``.txt`` files are excluded.
* Below the root, every dot-prefixed file or directory is skipped silently (checked first, so a
  dot-prefixed link is silent too). The root's own name is never subject to the rule.
* Symlinks and junctions are never followed or read; each non-dot link gives ``W-SYMLINK-SKIPPED``.
* A subdirectory that cannot be scanned gives ``W-DIR-UNREADABLE`` (detail = OS reason); the walk
  continues. No file failure is ever produced here.
* Zero files gives ``W-NO-FILES``; with ``recursive=False`` and ``.md`` files in subfolders the
  message says "N .md files exist in subfolders; remove --no-recursive" and the count is in
  ``subfolder_md_count``.
* ``files`` is sorted by plain ``str`` comparison of ``rel_path`` (ordinal code points), never by
  ``scandir`` order. The walk is iterative (explicit stack). Nothing is opened, written or touched.
* ``size`` / ``mtime_ns`` come from ``DirEntry.stat``; they are ``None`` if that stat fails (the
  reader then reports ``E-UNREADABLE`` for the file).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from actiontrack import codes
from actiontrack.domain.models import ParseWarning, parse_message
from actiontrack.errors import EnvError


class FolderError(EnvError):
    """The scan root cannot be used (exit 4)."""

    def __init__(self, path: str, os_reason: str) -> None:
        super().__init__(
            f"cannot scan folder: {os_reason}",
            where=str(path),
            next_step="check that the folder exists, is a directory and is readable",
        )
        self.path = str(path)
        self.os_reason = os_reason


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    rel_path: str
    abs_path: str
    size: int | None
    mtime_ns: int | None


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    folder_root: str
    files: tuple[DiscoveredFile, ...]
    warnings: tuple[ParseWarning, ...]
    subfolder_md_count: int


def _reason(exc: OSError) -> str:
    return exc.strerror or str(exc) or type(exc).__name__


def _is_link(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)
    return bool(is_junction()) if is_junction is not None else False


def _warn(code: str, rel: str, detail: str | None = None, **kw: object) -> ParseWarning:
    return ParseWarning(code, None, parse_message(code, rel, **kw), detail, rel)


def discover(
    folder: str | os.PathLike[str],
    recursive: bool = True,
    *,
    scandir: Callable[[str], Iterable[os.DirEntry[str]]] | None = None,
) -> DiscoveryResult:
    """Walk ``folder`` and return the ``.md`` files to ingest (see module docstring)."""
    root = os.path.realpath(os.fspath(folder))
    if not os.path.exists(root):
        raise FolderError(str(folder), "no such file or directory")
    if not os.path.isdir(root):
        raise FolderError(str(folder), "not a directory")

    def _scan(path: str) -> list[os.DirEntry[str]]:
        # looked up at call time so tests can monkeypatch ``os.scandir``
        func = scandir if scandir is not None else os.scandir
        result = func(path)
        if hasattr(result, "__enter__"):
            with result as it:  # type: ignore[union-attr]
                return sorted(it, key=lambda e: e.name)
        return sorted(result, key=lambda e: e.name)

    files: list[DiscoveredFile] = []
    warnings: list[ParseWarning] = []
    sub_md = 0

    # (absolute dir, rel prefix ("" for root or "a/b/"), counting_only)
    stack: list[tuple[str, str, bool]] = [(root, "", False)]
    while stack:
        dir_abs, prefix, counting = stack.pop()
        try:
            entries = _scan(dir_abs)
        except OSError as exc:
            if not prefix:
                raise FolderError(str(folder), _reason(exc)) from exc
            if not counting:
                rel = prefix.rstrip("/")
                warnings.append(_warn(codes.W_DIR_UNREADABLE, rel, _reason(exc), reason=_reason(exc)))
            continue
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            rel = prefix + name
            try:
                if _is_link(entry):
                    if not counting:
                        warnings.append(_warn(codes.W_SYMLINK_SKIPPED, rel, rel))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append((entry.path, rel + "/", counting or not recursive))
                    continue
                is_md_file = name.lower().endswith(".md") and entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if not is_md_file:
                continue
            if counting:
                sub_md += 1
                continue
            try:
                st = entry.stat(follow_symlinks=False)
                size: int | None = st.st_size
                mtime_ns: int | None = st.st_mtime_ns
            except OSError:
                size = mtime_ns = None
            files.append(DiscoveredFile(rel, os.path.join(root, *rel.split("/")), size, mtime_ns))

    files.sort(key=lambda f: f.rel_path)
    if not files:
        message = parse_message(codes.W_NO_FILES, root)
        detail = None
        if not recursive and sub_md > 0:
            detail = f"{sub_md} .md files exist in subfolders; remove --no-recursive"
            message = f"{message} {detail}"
        warnings.append(ParseWarning(codes.W_NO_FILES, None, message, detail, root))
    warnings.sort(key=lambda w: (w.file or "", w.code))
    return DiscoveryResult(root, tuple(files), tuple(warnings), sub_md if not recursive else 0)
