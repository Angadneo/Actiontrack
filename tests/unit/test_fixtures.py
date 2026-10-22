"""T-M1-18: the fixture set is complete (36 files), byte-exact and hash-pinned."""

import hashlib
import re

import pytest

from tests.conftest import FIXTURES_NOTES, REPO_ROOT

README = REPO_ROOT / "tests" / "fixtures" / "README.md"
ROW = re.compile(r"^\| `(?P<name>[^`]+)` \| `(?P<origin>[^`]+)` \| `(?P<sha>[0-9a-f]{64})` \|$", re.M)


@pytest.mark.ac("AC-01.13")
def test_fixture_file_counts():
    files = [p for p in FIXTURES_NOTES.iterdir()]
    assert len(files) == 36 and all(p.is_file() for p in files)
    assert sum(p.suffix.lower() == ".md" for p in files) == 35
    assert sum(p.suffix.lower() == ".txt" for p in files) == 1


@pytest.mark.ac("AC-01.11")
def test_readme_hashes_match_fixtures_and_origins():
    rows = {m["name"]: m for m in ROW.finditer(README.read_text(encoding="utf-8"))}
    assert set(rows) == {p.name for p in FIXTURES_NOTES.iterdir()}
    for name, m in rows.items():
        digest = hashlib.sha256((FIXTURES_NOTES / name).read_bytes()).hexdigest()
        assert digest == m["sha"], name
        origin = REPO_ROOT / m["origin"] / name
        assert hashlib.sha256(origin.read_bytes()).hexdigest() == digest, f"{name} differs from {origin}"
    assert "read-only" in README.read_text(encoding="utf-8")


def test_scratch_is_binary_and_untitled_is_valid_note():
    scratch = (FIXTURES_NOTES / "scratch.md").read_bytes()
    assert b"\x00" in scratch
    for name in (p.name for p in FIXTURES_NOTES.iterdir() if p.name != "scratch.md"):
        assert b"\x00" not in (FIXTURES_NOTES / name).read_bytes(), name
    text = (FIXTURES_NOTES / "untitled.md").read_bytes().decode("utf-8")
    assert len(re.findall(r"^\s*[-*+] \[[ xX]\]", text, re.M)) == 2
