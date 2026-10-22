"""UTF-8 and deterministic newlines on all text I/O (T-M1-06, AC-09.6, AC-09.4, SEC-08/10).

Public API
----------
``configure_streams(stdin=None, stdout=None, stderr=None)``
    ``reconfigure`` each stream to UTF-8; stdout/stderr get ``errors="backslashreplace"``
    and ``newline="\n"`` (no CRLF translation on Windows), stdin ``errors="strict"``.
    Streams that cannot be reconfigured (pytest capture) are skipped tolerantly.
    Defaults to the current ``sys.stdin/stdout/stderr``.
``emit(text, *, end="\n")``   result data -> stdout. Sanitised (``clean_block``).
``diag(text, *, end="\n")``   warnings/diagnostics/errors -> stderr. Sanitised.
    ``emit``/``diag`` look the stream up at call time and are independent of the log
    level (stdout is never governed by --quiet/--verbose). Characters the stream cannot
    encode (e.g. lone surrogates) are written backslash-escaped, never raising.
    These two functions and ``logging_setup`` are the only writers to the std streams.
``read_text_utf8(path) -> str``       UTF-8, no newline translation.
``write_text_utf8(path, text, *, append=False)``   UTF-8, ``\n`` newlines (no CRLF).
``write_private_file(path, text)``    like ``write_text_utf8`` (truncating) but the file is
    created owner-only (mode 0600 on POSIX). On Windows the mode bits are ignored by the
    OS; the file inherits the user-profile ACL, which is the documented protection (SEC-10).
Note-file reading is done in binary mode by ``parsing/reader.py`` (M2), not here.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from actiontrack.report.sanitize import clean_block

PathLike = str | os.PathLike[str]


def _reconfigure(stream: object, **kwargs: object) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(**kwargs)
    except (ValueError, OSError, TypeError):
        # Closed stream, or a wrapper that rejects an option: skip tolerantly.
        pass


def configure_streams(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    """Force UTF-8 (and LF on output) on the three standard streams."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    if stdin is not None:
        _reconfigure(stdin, encoding="utf-8", errors="strict")
    for out in (stdout, stderr):
        if out is not None:
            _reconfigure(out, encoding="utf-8", errors="backslashreplace", newline="\n")


def _write(stream: TextIO | None, text: str, end: str) -> None:
    if stream is None:
        return
    data = clean_block(text) + end
    # Make the text encodable whatever the stream's error handler is.
    data = data.encode("utf-8", "backslashreplace").decode("utf-8")
    try:
        stream.write(data)
    except UnicodeEncodeError:
        # Stream is not UTF-8 (not reconfigurable): degrade to escapes, never crash.
        enc = getattr(stream, "encoding", None) or "ascii"
        stream.write(data.encode(enc, "backslashreplace").decode(enc))
    try:
        stream.flush()
    except (ValueError, OSError):
        pass


def emit(text: str, *, end: str = "\n") -> None:
    """Write result data to stdout (sanitised, LF)."""
    _write(sys.stdout, text, end)


def diag(text: str, *, end: str = "\n") -> None:
    """Write a diagnostic to stderr (sanitised, LF)."""
    _write(sys.stderr, text, end)


def read_text_utf8(path: PathLike) -> str:
    """Read a whole UTF-8 text file without newline translation."""
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text_utf8(path: PathLike, text: str, *, append: bool = False) -> None:
    """Write (or append) UTF-8 text using ``\n`` newlines on every OS."""
    with open(path, "a" if append else "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_private_file(path: PathLike, text: str) -> None:
    """Write a file readable by the owner only where the OS supports it (SEC-10)."""
    fd = os.open(os.fspath(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if os.name != "nt":
        os.chmod(os.fspath(path), 0o600)


__all__ = [
    "configure_streams",
    "diag",
    "emit",
    "read_text_utf8",
    "write_private_file",
    "write_text_utf8",
]
