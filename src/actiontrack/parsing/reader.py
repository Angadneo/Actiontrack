"""Safe file reader and byte-level decoding (T-M2-04, T-M2-05).

All filesystem access of the parsing layer's *read* side lives in :func:`read_bytes`; everything
else in this module (:func:`classify_and_decode`, :func:`split_lines`, :func:`find_long_lines`) is a
pure function of bytes / text. Notes are read-only input (BR-01): the only ``open`` call here uses
mode ``'rb'``.

Public API
----------
``RawRead(rel_path, data, content_sha256, size_bytes, mtime_ns)``
    Result of a successful read. ``content_sha256`` is over the bytes exactly as on disk.
``read_bytes(discovered_file, max_file_bytes, *, opener=None, stat_fn=None) -> RawRead | FileFailure``
    Never raises for I/O problems: ``E-UNREADABLE`` (permission, deleted after discovery, sharing
    violation, any other ``OSError``) or ``E-TOO-LARGE`` come back as a ``FileFailure``.
    ``opener(path) -> binary file object`` and ``stat_fn(path) -> os.stat_result`` are injectable
    for tests (defaults: ``open(path, 'rb')`` and ``os.stat``).
``DecodedText(text, had_bom)``
    ``text`` is UTF-8 decoded, BOM stripped, ``\r\n`` / lone ``\r`` normalised to ``\n``
    (line content itself is otherwise verbatim, no NFC).
``Empty()``  marker: 0 bytes, BOM only, or whitespace only (status ``empty`` + ``W-EMPTY-FILE``).
``classify_and_decode(raw, rel_path="") -> DecodedText | FileFailure | Empty``
    Order: empty -> binary (NUL / UTF-16/32 BOM / PNG signature) -> strict UTF-8 (``E-ENCODING``
    with the byte offset in the original file) -> strip BOM -> whitespace-only -> EOL normalise.
``normalize_eol(text) -> str``;  ``split_lines(text) -> list[str]``
    Splits on ``"\n"`` only (also normalises EOLs defensively); a single trailing newline does not
    add an empty last line; ``""`` gives ``[]``. NEVER use ``str.splitlines()`` in the parsing
    package.
``find_long_lines(lines, file="", limit=LONG_LINE_LIMIT) -> list[ParseWarning]``
    One ``W-LONG-LINE`` (1-based line) per line longer than ``limit`` (100 000) characters.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import IO, Any

from actiontrack import codes
from actiontrack.domain.models import FileFailure, ParseWarning, parse_message
from actiontrack.parsing.discovery import DiscoveredFile

LONG_LINE_LIMIT = 100_000
_UTF8_BOM = b"\xef\xbb\xbf"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_UTF16_32_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff\xfe", b"\xfe\xff")
_WINERROR_SHARING = 32
_WINERROR_LOCK = 33


@dataclass(frozen=True, slots=True)
class RawRead:
    rel_path: str
    data: bytes
    content_sha256: str
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    had_bom: bool = False


@dataclass(frozen=True, slots=True)
class Empty:
    """Classification result: nothing to parse (0 bytes, BOM only or whitespace only)."""


def _open_rb(path: str) -> IO[bytes]:
    return open(path, "rb")  # noqa: SIM115 - the one and only open in the reader, read-only


def _unreadable(rel_path: str, exc: OSError) -> FileFailure:
    winerror = getattr(exc, "winerror", None)
    errno_ = exc.errno
    if isinstance(exc, FileNotFoundError):
        reason = "file no longer exists (deleted or moved after it was listed)"
    elif winerror in (_WINERROR_SHARING, _WINERROR_LOCK) or errno_ in (_WINERROR_SHARING, _WINERROR_LOCK):
        reason = f"file is locked by another program ({exc.strerror or exc})"
    elif isinstance(exc, PermissionError):
        reason = f"permission denied ({exc.strerror or exc})"
    else:
        reason = str(exc.strerror or exc) or type(exc).__name__
    return FileFailure(
        codes.E_UNREADABLE,
        parse_message(codes.E_UNREADABLE, rel_path, reason=reason),
        os_reason=reason,
    )


def _too_large(rel_path: str, size: int, limit: int) -> FileFailure:
    return FileFailure(
        codes.E_TOO_LARGE,
        parse_message(codes.E_TOO_LARGE, rel_path, size=size, limit=limit),
        size_bytes=size,
    )


def read_bytes(
    discovered_file: DiscoveredFile,
    max_file_bytes: int,
    *,
    opener: Callable[[str], IO[bytes]] | None = None,
    stat_fn: Callable[[str], Any] | None = None,
) -> RawRead | FileFailure:
    """Read one discovered file safely (read-only, size-bounded). See the module docstring."""
    rel = discovered_file.rel_path
    path = discovered_file.abs_path
    open_fn = opener or _open_rb
    stat_call = stat_fn or os.stat
    try:
        st = stat_call(path)
        size = st.st_size
        mtime_ns = st.st_mtime_ns
        if size > max_file_bytes:
            return _too_large(rel, size, max_file_bytes)
        with open_fn(path) as fh:
            data = fh.read(max_file_bytes + 1)
    except OSError as exc:
        return _unreadable(rel, exc)
    if len(data) > max_file_bytes:  # grew between stat and read (SEC-07)
        return _too_large(rel, len(data), max_file_bytes)
    return RawRead(rel, data, hashlib.sha256(data).hexdigest(), size, mtime_ns)


# --------------------------------------------------------------------------- decoding
def _is_binary(raw: bytes) -> bool:
    return b"\x00" in raw or raw.startswith(_PNG_SIGNATURE) or raw.startswith(_UTF16_32_BOMS)


def normalize_eol(text: str) -> str:
    """``\r\n`` and lone ``\r`` become ``\n``."""
    if "\r" not in text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def classify_and_decode(raw: bytes, rel_path: str = "") -> DecodedText | FileFailure | Empty:
    """Classify raw bytes and decode them to normalised text (see module docstring)."""
    name = rel_path or "<file>"
    if not raw:
        return Empty()
    if _is_binary(raw):
        return FileFailure(codes.E_BINARY, parse_message(codes.E_BINARY, name), size_bytes=len(raw))
    had_bom = raw.startswith(_UTF8_BOM)
    body = raw[len(_UTF8_BOM):] if had_bom else raw
    shift = len(_UTF8_BOM) if had_bom else 0
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        offset = exc.start + shift
        return FileFailure(
            codes.E_ENCODING,
            parse_message(codes.E_ENCODING, name, offset=offset),
            byte_offset=offset,
            size_bytes=len(raw),
        )
    if not text or text.isspace():
        return Empty()
    return DecodedText(normalize_eol(text), had_bom)


def split_lines(text: str) -> list[str]:
    """Split on ``\n`` only (never ``str.splitlines``); one trailing newline adds no empty line."""
    text = normalize_eol(text)
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def find_long_lines(
    lines: Sequence[str], file: str = "", limit: int = LONG_LINE_LIMIT
) -> list[ParseWarning]:
    """``W-LONG-LINE`` for every line longer than ``limit`` characters (lines stay intact)."""
    name = file or "<file>"
    out: list[ParseWarning] = []
    for number, line in enumerate(lines, start=1):
        if len(line) > limit:
            out.append(
                ParseWarning(
                    codes.W_LONG_LINE,
                    number,
                    parse_message(codes.W_LONG_LINE, name, number, length=len(line)),
                    detail=str(len(line)),
                )
            )
    return out
