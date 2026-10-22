"""Shared pytest harness for actiontrack (T-M1-01).

Provided to every test
----------------------
Markers (registered in pyproject.toml, ``--strict-markers`` on):
    ``@pytest.mark.ac("AC-01.11", ...)``  acceptance criteria proven by the test
    ``@pytest.mark.sc("SC-37", ...)``     scenarios covered by the test
    ``@pytest.mark.network``              opt out of the socket guard (release gate refuses)

Traceability hook (read by the T-M9-05 gate):
    ``pytest_collection_finish`` builds ``config.traceability``:
    ``{"ac": {"AC-01.11": ["tests/unit/x.py::test_y", ...]}, "sc": {...}}``.
    ``pytest --traceability-out=FILE`` dumps it as JSON. Malformed ids (not
    ``AC-NN.N`` / ``SC-N``) abort collection with a usage error.

Path fixtures:
    ``repo_root``          Path of the repository root
    ``fixtures_dir``       tests/fixtures/notes (READ-ONLY, never write there)
    ``samples_dir``        samples/notes (READ-ONLY)
    ``copy_fixture_notes`` factory ``(dest=None, names=None, nested=None) -> Path``:
                           copies fixture notes byte-exact into ``dest`` (default
                           ``tmp_path/"notes"``); ``names`` = subset of file names;
                           ``nested="notes"`` puts them one level down inside
                           ``dest`` (AC-01.13). Returns ``dest`` (the folder to ingest).
Environment / workdir:
    ``clean_env`` (autouse)  removes ACTIONTRACK_* and NO_COLOR from os.environ
    ``tmp_workdir``          chdir into a fresh temp dir (default ./.actiontrack/ stays out of the repo)
    ``tmp_db_path``          ``tmp_path/"actiontrack.db"`` (not created)
Guards (autouse):
    ``socket_guard``       any socket connect raises ``NetworkAccessError`` (AC-09.10)
    ``sample_write_guard`` fails a test that adds/changes/removes anything under
                           samples/notes, notes/notes or tests/fixtures/notes (BR-01)

Importable helpers: ``from tests.conftest import REPO_ROOT, FIXTURES_NOTES,
NetworkAccessError, snapshot_tree``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_NOTES = REPO_ROOT / "tests" / "fixtures" / "notes"
SAMPLES_NOTES = REPO_ROOT / "samples" / "notes"
READ_ONLY_TREES = (
    SAMPLES_NOTES,
    REPO_ROOT / "notes" / "notes",
    FIXTURES_NOTES,
)

_ID_PATTERNS = {"ac": re.compile(r"^AC-\d{2}\.\d+$"), "sc": re.compile(r"^SC-\d+$")}


class NetworkAccessError(RuntimeError):
    """Raised by the socket guard when a test tries to open a connection."""


# ---------------------------------------------------------------- traceability
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--traceability-out",
        default=None,
        help="write the AC/SC marker map (JSON) after collection",
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    mapping: dict[str, dict[str, list[str]]] = {"ac": defaultdict(list), "sc": defaultdict(list)}
    bad: list[str] = []
    for item in session.items:
        for kind in ("ac", "sc"):
            for mark in item.iter_markers(kind):
                for ident in mark.args:
                    if not isinstance(ident, str) or not _ID_PATTERNS[kind].match(ident):
                        bad.append(f"{item.nodeid}: bad {kind} id {ident!r}")
                    elif item.nodeid not in mapping[kind][ident]:
                        mapping[kind][ident].append(item.nodeid)
    if bad:
        raise pytest.UsageError("malformed ac/sc markers:\n" + "\n".join(bad))
    result = {k: dict(v) for k, v in mapping.items()}
    session.config.traceability = result  # type: ignore[attr-defined]
    out = session.config.getoption("--traceability-out")
    if out:
        Path(out).write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
        )


# --------------------------------------------------------------------- paths
@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_NOTES


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES_NOTES


@pytest.fixture
def copy_fixture_notes(tmp_path: Path):
    """Factory copying fixture notes (bytes, no newline translation) to a temp dir."""

    def _copy(dest: Path | None = None, names=None, nested: str | None = None) -> Path:
        root = Path(dest) if dest is not None else tmp_path / "notes"
        target = root / nested if nested else root
        target.mkdir(parents=True, exist_ok=True)
        for src in sorted(FIXTURES_NOTES.iterdir()):
            if src.is_file() and (names is None or src.name in names):
                (target / src.name).write_bytes(src.read_bytes())
        return root

    return _copy


# ------------------------------------------------------------- env / workdir
@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ACTIONTRACK_") or name == "NO_COLOR":
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def tmp_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "actiontrack.db"


# -------------------------------------------------------------------- guards
@pytest.fixture(autouse=True)
def socket_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("network"):
        return

    def _blocked(*_a, **_k):
        raise NetworkAccessError("network access is blocked in tests (socket guard)")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def snapshot_tree(root: Path) -> dict[str, str]:
    """Map relative path -> sha256 (dirs map to '<dir>'); empty if root is missing."""
    snap: dict[str, str] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        snap[rel] = "<dir>" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


@pytest.fixture(autouse=True)
def sample_write_guard():
    before = {t: snapshot_tree(t) for t in READ_ONLY_TREES}
    yield
    problems = []
    for tree, old in before.items():
        new = snapshot_tree(tree)
        if new != old:
            changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
            problems.append(f"{tree}: {changed}")
    if problems:
        pytest.fail("test modified read-only sample/fixture tree (BR-01): " + "; ".join(problems))
