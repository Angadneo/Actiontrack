"""T-M1-02: exit codes and error hierarchy (spec 4.1)."""

import re

import pytest

from actiontrack import errors
from actiontrack.errors import (
    EXIT_HANDOFF_PREPARED,
    ActionTrackError,
    AmbiguousOwnerError,
    CancelledByUser,
    DraftNotCreatedError,
    EnvError,
    ExitCode,
    IngestFailuresError,
    InternalError,
    NotFoundError,
    PreconditionError,
    UsageError,
    exit_code_for,
)
from tests.conftest import REPO_ROOT

SPEC = REPO_ROOT / "specs" / "actiontrack-spec.md"


def _spec_exit_table() -> dict[int, str]:
    text = SPEC.read_text(encoding="utf-8")
    block = text.split("**Exit codes**", 1)[1].split("**Handoff exit value.**", 1)[0]
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"^\| (\d) \| (.+) \|$", block, re.M)}


def test_exit_code_values():
    values = [m.value for m in ExitCode]
    assert sorted(values) == list(range(10))
    assert len(set(values)) == 10
    assert sorted(_spec_exit_table()) == list(range(10))  # spec table is 0..9 too
    assert EXIT_HANDOFF_PREPARED == 0
    assert EXIT_HANDOFF_PREPARED is ExitCode.OK


def test_handoff_constant_defined_once():
    src = REPO_ROOT / "src" / "actiontrack"
    defs = [
        p
        for p in src.rglob("*.py")
        if re.search(r"^EXIT_HANDOFF_PREPARED\b.*=", p.read_text(encoding="utf-8"), re.M)
    ]
    assert defs == [src / "errors.py"]


def test_no_inline_exit_integers_outside_cli():
    src = REPO_ROOT / "src" / "actiontrack"
    allowed = {"cli.py", "__main__.py"}
    for p in src.rglob("*.py"):
        if p.name in allowed:
            continue
        text = p.read_text(encoding="utf-8")
        assert "sys.exit(" not in text, p
        assert not re.search(r"\bexit_code\s*=\s*\d", text), p
        assert not re.search(r"\bExitCode\(\s*\d", text), p


@pytest.mark.parametrize(
    "cls, code",
    [
        (InternalError, 1),
        (UsageError, 2),
        (NotFoundError, 3),
        (EnvError, 4),
        (IngestFailuresError, 5),
        (AmbiguousOwnerError, 6),
        (PreconditionError, 7),
        (DraftNotCreatedError, 8),
        (CancelledByUser, 9),
    ],
)
def test_error_exit_codes(cls, code):
    err = cls("boom") if cls is not CancelledByUser else cls()
    assert isinstance(err, ActionTrackError)
    assert err.exit_code == code and int(exit_code_for(err)) == code


def test_exit_code_for_unknown_exception_is_internal():
    assert exit_code_for(ValueError("x")) is ExitCode.INTERNAL_ERROR


def test_error_format_has_what_where_next():
    err = EnvError(
        "cannot open log file",
        where="--log-file C:\\x\\a.log",
        next_step="choose a writable path",
    )
    out = err.format()
    assert "cannot open log file" in out and "--log-file C:\\x\\a.log" in out
    assert "choose a writable path" in out
    assert out.count("\n") <= 1
    assert str(err) == out
    assert EnvError("only what").format() == "only what"


def test_cancelled_by_user():
    err = CancelledByUser()
    assert err.exit_code == 9
    assert err.format() == "Cancelled. Nothing created."


def test_exports_documented():
    assert errors.__doc__ and "EXIT_HANDOFF_PREPARED" in errors.__doc__
