"""Diagnostics facade: levels, --quiet/--verbose, --log-file (T-M1-09, NFR-LOG-01/02, SEC-06/08).

Not the global ``logging`` root: ``Diagnostics`` is a plain object that the CLI builds once
from the global options and injects everywhere, so tests can create their own.

Public API
----------
``Diagnostics(*, quiet=False, verbose=False, log_file=None, strict_codes=True)``
    * ``quiet`` and ``verbose`` together raise ``UsageError`` (exit 2, spec 4.1); the
      CLI decides nothing more than passing the flags.
    * ``log_file``: opened for append (UTF-8, LF) IMMEDIATELY, so an unusable path raises
      ``EnvError`` (exit 4) before any other work. The file receives exactly what stderr
      receives, at the same verbosity (including the summary line).
    * ``error(message, *, code=None)``  always shown. ``code`` must be an ``E-*`` code.
    * ``warn(code, message, *, file=None, line=None)``  shown by default and verbose;
      printed ``W-XXXX file:line message``. ``code`` must be registered in
      ``codes.WARNING_CODES``; otherwise ``AssertionError`` (typo catcher; pass
      ``strict_codes=False`` to emit unknown codes instead of failing).
    * ``info(message)``  shown by default and verbose, hidden by ``--quiet``.
    * ``debug(message)`` only with ``--verbose``.
    * ``summary(line)``  the final summary line (NFR-LOG-02): printed in every mode (see the
      summary note below).
    * ``exception(exc)`` one-line report of a raised error; the stack trace only with
      ``--verbose``; unexpected exceptions get a "re-run with --verbose" hint otherwise.
    * ``debug_person(name)``  logs a personal name only at debug level (SEC-06).
    * ``debug_body(label, body)``  logs only the label and the length, never the body
      (email bodies / handoff bodies never reach logs, SEC-06).
    * ``close()`` / context manager; ``warning_count`` counts emitted ``warn`` calls
      (even when hidden) for the "Warnings (n)" report line.
    Every message passes ``redact()`` and ``sanitize.clean_block`` (SEC-08) and goes to
    stderr through ``io.diag``. stdout (result data) is never affected by any of this.
``redact(text) -> str``  replaces token-like strings (bearer tokens, ``ya29.`` / ``AIza``
    / ``GOCSPX-`` credentials, JWTs, ``password=``/``token=``/``secret=`` assignments) with
    ``[REDACTED]``.

Summary semantics. Spec 4.1 / NFR-LOG-01 say ``--quiet`` leaves "errors and the final
summary line only", while the T-M1-09 text says ``summary(line)`` "prints unless --quiet".
These conflict; this implementation follows spec 4.1 and NFR-LOG-02 ("always printed,
including with --quiet"): ``summary()`` is printed in every mode, and ``--quiet``
suppresses warnings and info only. Change ``_SUMMARY_IN_QUIET`` to flip it.

Default verbosity shows error + warn + info (``--quiet`` exists precisely to hide the
warn/info lines); ``debug`` needs ``--verbose``. No timestamps are written (determinism;
the clock is not read here).
"""

from __future__ import annotations

import enum
import os
import re
import traceback
from types import TracebackType
from typing import TextIO

from actiontrack import codes, io
from actiontrack.errors import ActionTrackError, EnvError, UsageError
from actiontrack.report.sanitize import clean_block

_SUMMARY_IN_QUIET = True

_REDACTED = "[REDACTED]"
_SECRET_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bya29\.[A-Za-z0-9._-]+"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bGOCSPX-[0-9A-Za-z_-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(
        r"(?i)\b(access_token|refresh_token|id_token|client_secret|api[_-]?key|password|secret|token)"
        r"(\s*[=:]\s*)[^\s,;&\"']+"
    ),
)


def redact(text: str) -> str:
    """Replace token-like substrings with ``[REDACTED]`` (SEC-06)."""
    for pat in _SECRET_PATTERNS[:-1]:
        text = pat.sub(_REDACTED, text)
    return _SECRET_PATTERNS[-1].sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)


class Level(enum.IntEnum):
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40


class Diagnostics:
    def __init__(
        self,
        *,
        quiet: bool = False,
        verbose: bool = False,
        log_file: str | os.PathLike[str] | None = None,
        strict_codes: bool = True,
    ) -> None:
        if quiet and verbose:
            raise UsageError(
                "--quiet and --verbose cannot be used together",
                next_step="give only one of --quiet or --verbose",
            )
        self.quiet = quiet
        self.verbose = verbose
        self.strict_codes = strict_codes
        self.warning_count = 0
        self._fh: TextIO | None = None
        if verbose:
            self.threshold = Level.DEBUG
        elif quiet:
            self.threshold = Level.ERROR
        else:
            self.threshold = Level.INFO
        if log_file is not None:
            self._open_log(log_file)

    # ------------------------------------------------------------ log file
    def _open_log(self, path: str | os.PathLike[str]) -> None:
        try:
            self._fh = open(path, "a", encoding="utf-8", newline="\n")
        except (OSError, ValueError) as exc:
            # ValueError: Windows reserved device names (e.g. "con:") make open() raise it.
            raise EnvError(
                f"cannot open log file for append: {getattr(exc, 'strerror', None) or exc}",
                where=os.fspath(path),
                next_step="choose a writable --log-file path (its folder must exist)",
            ) from exc

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    def __enter__(self) -> Diagnostics:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------ plumbing
    def _write(self, text: str) -> None:
        text = clean_block(redact(text))
        io.diag(text)
        if self._fh is not None:
            self._fh.write(text + "\n")
            self._fh.flush()

    def _enabled(self, level: Level) -> bool:
        return level >= self.threshold

    # ------------------------------------------------------------ levels
    def error(self, message: str, *, code: str | None = None) -> None:
        if code is not None:
            if self.strict_codes and code not in codes.ERROR_CODES:
                raise AssertionError(f"unregistered error code: {code!r}")
            message = f"{code} {message}"
        self._write(f"error: {message}")

    def warn(
        self,
        code: str,
        message: str,
        *,
        file: str | None = None,
        line: int | None = None,
    ) -> None:
        if self.strict_codes and code not in codes.WARNING_CODES:
            raise AssertionError(f"unregistered warning code: {code!r}")
        self.warning_count += 1
        if not self._enabled(Level.WARN):
            return
        loc = ""
        if file is not None:
            loc = f"{file}:{line}" if line is not None else file
        self._write(" ".join(p for p in (code, loc, message) if p))

    def info(self, message: str) -> None:
        if self._enabled(Level.INFO):
            self._write(message)

    def debug(self, message: str) -> None:
        if self._enabled(Level.DEBUG):
            self._write(f"debug: {message}")

    def summary(self, line: str) -> None:
        """Final summary line (NFR-LOG-02); see the module note on ``--quiet``."""
        if self.quiet and not _SUMMARY_IN_QUIET:
            return
        self._write(line)

    # ------------------------------------------------------------ helpers
    def debug_person(self, name: str) -> None:
        """Personal names appear in logs only at debug level (SEC-06); no-op otherwise."""
        if self._enabled(Level.DEBUG):
            self._write(f"debug: person: {name}")

    def debug_body(self, label: str, body: str) -> None:
        """Log that a body exists, never its text (SEC-06)."""
        if self._enabled(Level.DEBUG):
            self._write(f"debug: {label}: <{len(body)} characters omitted>")

    def exception(self, exc: BaseException) -> None:
        """Report a raised exception; stack trace only with ``--verbose`` (spec 11)."""
        if isinstance(exc, ActionTrackError):
            self.error(exc.format())
        else:
            self.error(f"unexpected {type(exc).__name__}: {exc}")
            if not self.verbose:
                self.error("re-run with --verbose for details")
        if self.verbose:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
            self._write(f"debug: traceback:\n{tb}")
