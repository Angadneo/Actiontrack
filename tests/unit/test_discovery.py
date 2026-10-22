"""T-M2-02: folder discovery (walk, .md filter, recursion, deterministic order)."""

import hashlib
import os
import re
from pathlib import Path

import pytest

from actiontrack.errors import EnvError, ExitCode
from actiontrack.parsing.discovery import DiscoveredFile, FolderError, discover
from tests.conftest import REPO_ROOT


def _touch(root: Path, *names: str) -> None:
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


@pytest.mark.ac("AC-01.1")
def test_md_filter_and_ordinal_order(tmp_path):
    _touch(tmp_path, "a.md", "B.MD", "c.txt", "x.Md")
    (tmp_path / "dir.md").mkdir()
    res = discover(tmp_path)
    assert [f.rel_path for f in res.files] == ["B.MD", "a.md", "x.Md"]
    assert res.warnings == ()
    f = res.files[0]
    assert isinstance(f, DiscoveredFile)
    assert f.size == 1 and f.mtime_ns == (tmp_path / "B.MD").stat().st_mtime_ns
    assert f.abs_path == os.path.join(res.folder_root, "B.MD")


def test_recursive_and_no_recursive(tmp_path):
    _touch(tmp_path, "top.md", "sub/in.md", "sub/deep/d.md")
    rec = discover(tmp_path)
    assert [f.rel_path for f in rec.files] == ["sub/deep/d.md", "sub/in.md", "top.md"]
    flat = discover(tmp_path, recursive=False)
    assert [f.rel_path for f in flat.files] == ["top.md"]
    assert all("\\" not in f.rel_path for f in rec.files)


def test_scandir_order_irrelevant(tmp_path, monkeypatch):
    _touch(tmp_path, "a.md", "b.md", "c/d.md", "c/e.md", "Z.md")
    baseline = discover(tmp_path)
    real = os.scandir

    class _Reversed(list):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return list.__reversed__(self)

    def reversed_scandir(path):
        with real(path) as it:
            return _Reversed(it)

    monkeypatch.setattr(os, "scandir", reversed_scandir)
    assert discover(tmp_path) == baseline


@pytest.mark.ac("AC-01.13")
def test_sample_copy_root_and_parent(copy_fixture_notes, tmp_path):
    root = copy_fixture_notes(tmp_path / "direct")
    res = discover(root)
    names = [f.rel_path for f in res.files]
    assert len(names) == 35
    assert names[0] == "2026-09-01-quote-portal-kickoff.md"
    assert names[-2:] == ["scratch.md", "untitled.md"]
    assert "2026-09-19-offsite-draft.txt" not in names
    assert names == sorted(names)
    parent = copy_fixture_notes(tmp_path / "parent", nested="notes")
    res2 = discover(parent)
    assert len(res2.files) == 35
    assert res2.files[0].rel_path == "notes/2026-09-01-quote-portal-kickoff.md"
    assert res2.files[-1].rel_path == "notes/untitled.md"
    assert res2.warnings == ()


@pytest.mark.ac("AC-01.12")
def test_missing_and_file_as_folder(tmp_path):
    with pytest.raises(FolderError) as ei:
        discover(tmp_path / "nope")
    assert isinstance(ei.value, EnvError) and ei.value.exit_code == ExitCode.ENVIRONMENT
    assert "nope" in ei.value.format()
    f = tmp_path / "f.md"
    f.write_bytes(b"x")
    with pytest.raises(FolderError) as ei2:
        discover(f)
    assert ei2.value.os_reason == "not a directory"


def test_unreadable_root_is_folder_error(tmp_path):
    def boom(path):
        raise PermissionError(13, "Permission denied", path)

    with pytest.raises(FolderError) as ei:
        discover(tmp_path, scandir=boom)
    assert "Permission denied" in ei.value.os_reason


def test_discover_does_not_modify_files(copy_fixture_notes, tmp_path):
    root = copy_fixture_notes(tmp_path / "n")

    def snap():
        return {
            p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.iterdir()
        }

    before = snap()
    a = discover(root)
    b = discover(root)
    assert snap() == before
    assert a == b


def test_no_write_mode_open_in_module():
    src = (REPO_ROOT / "src" / "actiontrack" / "parsing" / "discovery.py").read_text(encoding="utf-8")
    assert not re.search(r"\bopen\(", src)
    assert not re.search(r"write_(text|bytes)|os\.(utime|remove|rename)", src)
