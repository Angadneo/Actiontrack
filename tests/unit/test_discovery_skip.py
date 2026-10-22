"""T-M2-03: dot-prefix skipping, links, W-DIR-UNREADABLE, W-NO-FILES."""

import builtins
import os
import subprocess
import sys
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing.discovery import discover


def _touch(root: Path, *names: str) -> None:
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


@pytest.mark.ac("AC-01.18")
def test_dot_prefixed_entries_skipped_silently(tmp_path):
    _touch(tmp_path, "a.md", ".draft.md", ".hidden/x.md", ".git/x.md", "ok/.deep/.more/y.md", ".DS_Store",
           "ok/.also.md", "ok/real.md")
    res = discover(tmp_path)
    assert [f.rel_path for f in res.files] == ["a.md", "ok/real.md"]
    assert res.warnings == ()
    flat = discover(tmp_path, recursive=False)
    assert [f.rel_path for f in flat.files] == ["a.md"]
    assert flat.warnings == ()


def test_root_name_may_start_with_dot(tmp_path):
    root = tmp_path / ".notes"
    _touch(root, "a.md")
    assert [f.rel_path for f in discover(root).files] == ["a.md"]


def _symlink_or_skip(link: Path, target: Path, is_dir: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


def test_symlinks_skipped_with_warning_and_never_opened(tmp_path, monkeypatch):
    _touch(tmp_path, "real.md", "target/t.md")
    _symlink_or_skip(tmp_path / "filelink.md", tmp_path / "real.md", False)
    _symlink_or_skip(tmp_path / "dirlink", tmp_path / "target", True)
    _symlink_or_skip(tmp_path / ".dotlink", tmp_path / "target", True)
    opened = []
    real_open = builtins.open

    def spy(*a, **k):
        opened.append(a)
        return real_open(*a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    res = discover(tmp_path)
    assert opened == []
    assert [f.rel_path for f in res.files] == ["real.md", "target/t.md"]
    got = {(w.code, w.file) for w in res.warnings}
    assert got == {(codes.W_SYMLINK_SKIPPED, "dirlink"), (codes.W_SYMLINK_SKIPPED, "filelink.md")}
    assert not any(".dotlink" in w.message for w in res.warnings)


def test_symlink_loop_terminates(tmp_path):
    _touch(tmp_path, "a/x.md")
    _symlink_or_skip(tmp_path / "a" / "loop", tmp_path, True)
    res = discover(tmp_path)
    assert [f.rel_path for f in res.files] == ["a/x.md"]
    assert [w.code for w in res.warnings] == [codes.W_SYMLINK_SKIPPED]
    assert res.warnings[0].file == "a/loop"


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_junction_skipped(tmp_path):
    _touch(tmp_path, "target/t.md", "r.md")
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(tmp_path / "junc"), str(tmp_path / "target")],
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip("mklink /J unavailable")
    res = discover(tmp_path)
    assert [f.rel_path for f in res.files] == ["r.md", "target/t.md"]
    assert [(w.code, w.file) for w in res.warnings] == [(codes.W_SYMLINK_SKIPPED, "junc")]


@pytest.mark.ac("AC-01.18")
def test_unreadable_subdirectory_warns_and_continues(tmp_path, monkeypatch):
    _touch(tmp_path, "root.md", "locked/x.md", "other/y.md")
    real = os.scandir

    def fake(path):
        if os.path.basename(os.fspath(path)) == "locked":
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", fake)
    res = discover(tmp_path)
    assert [f.rel_path for f in res.files] == ["other/y.md", "root.md"]
    assert [(w.code, w.file) for w in res.warnings] == [(codes.W_DIR_UNREADABLE, "locked")]
    assert "Permission denied" in res.warnings[0].detail
    assert not codes.is_file_failure(res.warnings[0].code)


def test_walk_is_iterative(tmp_path):
    p = tmp_path
    for _ in range(40):
        p = p / "d"
    _touch(p, "deep.md")
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old // 10, 100))
    try:
        res = discover(tmp_path)
    finally:
        sys.setrecursionlimit(old)
    assert len(res.files) == 1


@pytest.mark.ac("AC-01.13")
@pytest.mark.sc("SC-41")
def test_no_recursive_hint_on_sample_parent(copy_fixture_notes, tmp_path):
    parent = copy_fixture_notes(tmp_path / "p", nested="notes")
    _touch(parent, "notes/.hidden.md", ".git/z.md")
    flat = discover(parent, recursive=False)
    assert flat.files == ()
    assert flat.subfolder_md_count == 35
    (w,) = flat.warnings
    assert w.code == codes.W_NO_FILES
    assert "35" in w.message and "--no-recursive" in w.message
    rec = discover(parent)
    assert len(rec.files) == 35 and rec.warnings == ()


def test_empty_and_only_txt_have_no_subfolder_hint(tmp_path):
    empty = tmp_path / "e"
    empty.mkdir()
    txt = tmp_path / "t"
    _touch(txt, "x.txt")
    for folder in (empty, txt):
        for rec in (True, False):
            res = discover(folder, recursive=rec)
            assert res.files == ()
            assert [w.code for w in res.warnings] == [codes.W_NO_FILES]
            assert "subfolders" not in res.warnings[0].message
            assert res.subfolder_md_count == 0
