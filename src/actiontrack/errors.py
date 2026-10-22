"""Exit codes and the exception hierarchy (spec 4.1, NFR-USE-01, T-M1-02).

Public API
----------
``ExitCode``                 IntEnum, exactly 0..9 as in spec 4.1.
``EXIT_HANDOFF_PREPARED``    the ONE place deciding the exit value of a prepared
                             ``followup`` handoff (currently ``ExitCode.OK`` = 0).
                             Always refer to this constant, never to a literal 0.
``ActionTrackError``         base; ``ActionTrackError(what, where=None, next_step=None)``;
                             attributes ``exit_code`` (class attr), ``what``, ``where``,
                             ``next_step``; ``format() -> str`` renders what / where /
                             next step on one or two lines.
Subclasses (class attribute ``exit_code``):
    ``InternalError`` 1, ``UsageError`` 2, ``NotFoundError`` 3, ``EnvError`` 4
    (also: handoff dir/file not writable, DB locked by another process, unopenable
    ``--log-file``), ``IngestFailuresError`` 5 (not raised; used for end-of-run
    mapping), ``AmbiguousOwnerError`` 6, ``PreconditionError`` 7,
    ``DraftNotCreatedError`` 8, ``CancelledByUser`` 9 (default message
    "Cancelled. Nothing created.").
``exit_code_for(exc) -> ExitCode``  maps any exception to an exit code (unknown -> 1).

Only ``cli.py`` may call ``sys.exit``; everything else raises these errors.
"""

from __future__ import annotations

import enum


class ExitCode(enum.IntEnum):
    OK = 0
    INTERNAL_ERROR = 1
    USAGE = 2
    NOT_FOUND = 3
    ENVIRONMENT = 4
    INGEST_FILE_FAILURES = 5
    AMBIGUOUS_OWNER = 6
    PRECONDITION_FAILED = 7
    DRAFT_NOT_CREATED = 8
    CANCELLED = 9


#: Exit value of a prepared handoff (spec 4.1 "Handoff exit value"). Single definition.
EXIT_HANDOFF_PREPARED: ExitCode = ExitCode.OK


class ActionTrackError(Exception):
    """Base error: what happened, where (file/key/line), and what to do next."""

    exit_code: ExitCode = ExitCode.INTERNAL_ERROR

    def __init__(self, what: str, where: str | None = None, next_step: str | None = None) -> None:
        super().__init__(what)
        self.what = what
        self.where = where
        self.next_step = next_step

    def format(self) -> str:
        first = self.what if not self.where else f"{self.what} ({self.where})"
        if self.next_step:
            return f"{first}\nNext: {self.next_step}"
        return first

    def __str__(self) -> str:
        return self.format()


class InternalError(ActionTrackError):
    exit_code = ExitCode.INTERNAL_ERROR


class UsageError(ActionTrackError):
    exit_code = ExitCode.USAGE


class NotFoundError(ActionTrackError):
    exit_code = ExitCode.NOT_FOUND


class EnvError(ActionTrackError):
    exit_code = ExitCode.ENVIRONMENT


class IngestFailuresError(ActionTrackError):
    """Not raised: represents 'ingest finished with >=1 file failure' (exit 5)."""

    exit_code = ExitCode.INGEST_FILE_FAILURES


class AmbiguousOwnerError(ActionTrackError):
    exit_code = ExitCode.AMBIGUOUS_OWNER


class PreconditionError(ActionTrackError):
    exit_code = ExitCode.PRECONDITION_FAILED


class DraftNotCreatedError(ActionTrackError):
    exit_code = ExitCode.DRAFT_NOT_CREATED


class CancelledByUser(ActionTrackError):
    exit_code = ExitCode.CANCELLED

    def __init__(
        self,
        what: str = "Cancelled. Nothing created.",
        where: str | None = None,
        next_step: str | None = None,
    ) -> None:
        super().__init__(what, where, next_step)


def exit_code_for(exc: BaseException) -> ExitCode:
    """Exit code for an exception; anything that is not an ActionTrackError is internal (1)."""
    if isinstance(exc, ActionTrackError):
        return exc.exit_code
    return ExitCode.INTERNAL_ERROR
