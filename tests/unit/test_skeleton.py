"""T-M1-01: package, entry point and harness behaviour."""

import os
import re
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import actiontrack
from tests.conftest import FIXTURES_NOTES, REPO_ROOT, NetworkAccessError, snapshot_tree


def test_package_importable():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'attr = "actiontrack.__version__"' in text
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", actiontrack.__version__)


def test_project_metadata():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == ["tzdata"]
    assert data["project"]["optional-dependencies"]["dev"] == ["pytest"]
    assert data["project"]["scripts"]["actiontrack"] == "actiontrack.cli:main"
    assert data["project"]["requires-python"] == ">=3.12"


def _run(*args):
    env = {"PYTHONPATH": str(REPO_ROOT / "src"), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    return subprocess.run(
        [sys.executable, "-m", "actiontrack", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_main_module_runs():
    r = _run()
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
    v = _run("--version")
    assert v.returncode == 0 and actiontrack.__version__ in v.stdout


@pytest.mark.ac("AC-00.0")
@pytest.mark.sc("SC-00")
def test_marker_registration():
    assert True


@pytest.mark.ac("AC-09.10")
def test_socket_guard_blocks_connect():
    with pytest.raises(NetworkAccessError):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(NetworkAccessError):
        socket.socket().connect(("127.0.0.1", 9))


def test_env_scrubbed():
    assert "ACTIONTRACK_NOW" not in os.environ and "NO_COLOR" not in os.environ


def test_tmp_workdir_and_db(tmp_workdir, tmp_db_path):
    assert Path.cwd() == tmp_workdir.resolve() or Path.cwd() == tmp_workdir
    assert REPO_ROOT not in tmp_workdir.parents
    assert not tmp_db_path.exists()


def test_copy_fixture_notes_helper(copy_fixture_notes, tmp_path):
    out = copy_fixture_notes(names={"scratch.md", "untitled.md"}, nested="x")
    assert out == tmp_path / "notes"
    assert (out / "x" / "scratch.md").read_bytes() == (FIXTURES_NOTES / "scratch.md").read_bytes()


def test_snapshot_detects_change(tmp_path):
    (tmp_path / "a").write_text("1")
    s1 = snapshot_tree(tmp_path)
    (tmp_path / "a").write_text("2")
    assert snapshot_tree(tmp_path) != s1


def test_sample_guard_fails_a_writing_test():
    """The guard must fail a test that writes into a read-only tree (run in a subprocess)."""
    probe = FIXTURES_NOTES / "__guard_probe.md"
    scratch = REPO_ROOT / "tests" / "unit" / "_guard_probe_test.py"
    scratch.write_text(
        f"import pathlib\n\ndef test_bad():\n    pathlib.Path(r'{probe}').write_text('x')\n",
        encoding="utf-8",
    )
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(scratch)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
    finally:
        scratch.unlink(missing_ok=True)
        probe.unlink(missing_ok=True)
    assert r.returncode != 0
    assert "read-only sample/fixture tree" in r.stdout
