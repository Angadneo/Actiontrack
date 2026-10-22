"""T-M2-04: safe reader (stat, size limit, byte read, SHA-256, read-error mapping)."""

from __future__ import annotations

import builtins
import hashlib
import io
import os
import re
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.domain.models import FileFailure
from actiontrack.parsing import reader
from actiontrack.parsing.discovery import DiscoveredFile
from actiontrack.parsing.reader import RawRead, read_bytes

EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _df(path: Path, rel: str | None = None) -> DiscoveredFile:
    st = path.stat() if path.exists() else None
    return DiscoveredFile(rel or path.name, str(path), st.st_size if st else None, st.st_mtime_ns if st else None)


@pytest.mark.ac("AC-09.5")
def test_boundary_exact_limit_read_and_one_over_rejected(tmp_path: Path) -> None:
    ok = tmp_path / "ok.md"
    ok.write_bytes(b"x" * 100)
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * 101)
    r = read_bytes(_df(ok), 100)
    assert isinstance(r, RawRead) and len(r.data) == 100
    f = read_bytes(_df(big), 100)
    assert isinstance(f, FileFailure) and f.code == codes.E_TOO_LARGE
    assert "101" in f.message and "big.md" in f.message and f.size_bytes == 101
    # neighbouring file unaffected
    assert isinstance(read_bytes(_df(ok), 100), RawRead)


def test_too_large_is_decided_from_stat_without_reading(tmp_path: Path) -> None:
    p = tmp_path / "big.md"
    p.write_bytes(b"x" * 50)

    def boom(_path: str):  # pragma: no cover - must not be called
        raise AssertionError("content must not be read")

    f = read_bytes(_df(p), 10, opener=boom)
    assert isinstance(f, FileFailure) and f.code == codes.E_TOO_LARGE


def test_file_growing_after_stat_is_still_bounded(tmp_path: Path) -> None:
    p = tmp_path / "grow.md"
    p.write_bytes(b"x" * 5)
    seen: list[int] = []

    class Spy(io.BytesIO):
        def read(self, n: int = -1) -> bytes:
            seen.append(n)
            return super().read(n)

    f = read_bytes(_df(p), 10, opener=lambda _p: Spy(b"y" * 1000))
    assert seen == [11]  # at most max + 1 bytes requested (SEC-07)
    assert isinstance(f, FileFailure) and f.code == codes.E_TOO_LARGE and f.size_bytes == 11


def test_hash_size_mtime_cover_raw_bytes_with_bom_and_cr(tmp_path: Path) -> None:
    data = b"\xef\xbb\xbf- [ ] a\r\n- [x] b\r"
    p = tmp_path / "n.md"
    p.write_bytes(data)
    r = read_bytes(_df(p), 1000)
    assert isinstance(r, RawRead)
    assert r.data == data
    assert r.content_sha256 == hashlib.sha256(data).hexdigest()
    assert r.size_bytes == len(data) and r.mtime_ns == p.stat().st_mtime_ns


def test_empty_sample_file(copy_fixture_notes) -> None:
    root = copy_fixture_notes(names={"2026-09-09-notes-final-v2.md"})
    p = root / "2026-09-09-notes-final-v2.md"
    r = read_bytes(_df(p), 1000)
    assert isinstance(r, RawRead) and r.data == b"" and r.size_bytes == 0
    assert r.content_sha256 == EMPTY_SHA


@pytest.mark.parametrize(
    "exc",
    [
        PermissionError(13, "Permission denied"),
        OSError(32, "The process cannot access the file because it is being used by another process"),
        FileNotFoundError(2, "No such file"),
        IsADirectoryError(21, "Is a directory"),
    ],
)
def test_open_errors_map_to_e_unreadable(tmp_path: Path, exc: OSError) -> None:
    p = tmp_path / "n.md"
    p.write_bytes(b"x")

    def bad(_path: str):
        raise exc

    f = read_bytes(_df(p, "sub/n.md"), 100, opener=bad)
    assert isinstance(f, FileFailure) and f.code == codes.E_UNREADABLE
    assert "sub/n.md" in f.message and f.os_reason
    assert "Next:" in f.message


def test_windows_sharing_violation_is_described_as_locked(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_bytes(b"x")
    err = OSError(13, "sharing violation")
    err.winerror = 32  # type: ignore[attr-defined]

    def bad(_path: str):
        raise err

    f = read_bytes(_df(p), 100, opener=bad)
    assert isinstance(f, FileFailure) and "locked" in (f.os_reason or "")


def test_read_error_midway_is_unreadable(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_bytes(b"x")

    class Bad(io.BytesIO):
        def read(self, n: int = -1) -> bytes:
            raise OSError(5, "I/O error")

    f = read_bytes(_df(p), 100, opener=lambda _p: Bad(b"x"))
    assert isinstance(f, FileFailure) and f.code == codes.E_UNREADABLE


def test_deleted_between_discover_and_read(tmp_path: Path) -> None:
    p = tmp_path / "gone.md"
    p.write_bytes(b"x")
    d = _df(p)
    p.unlink()
    f = read_bytes(d, 100)
    assert isinstance(f, FileFailure) and f.code == codes.E_UNREADABLE
    assert "no longer exists" in (f.os_reason or "")


def test_monkeypatched_builtin_open_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "n.md"
    p.write_bytes(b"x")
    d = _df(p)

    def deny(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(builtins, "open", deny)
    f = read_bytes(d, 100)
    monkeypatch.undo()
    assert isinstance(f, FileFailure) and f.code == codes.E_UNREADABLE


@pytest.mark.ac("AC-01.11")
def test_all_sample_files_unchanged_and_only_rb_opens(
    copy_fixture_notes, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_fixture_notes()
    files = sorted(p for p in root.iterdir() if p.is_file())
    before = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size, p.stat().st_mtime_ns) for p in files}
    modes: list[object] = []
    real_open = builtins.open

    def spy(file, mode="r", *a, **k):
        if str(file).startswith(str(root)):
            modes.append(mode)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    reads = [read_bytes(_df(p), 5_242_880) for p in files]
    monkeypatch.undo()
    assert all(isinstance(r, RawRead) for r in reads)
    assert modes and set(modes) == {"rb"}
    for p, r in zip(files, reads, strict=True):
        assert r.content_sha256 == before[p.name][0]
        assert hashlib.sha256(p.read_bytes()).hexdigest() == before[p.name][0]
        assert (p.stat().st_size, p.stat().st_mtime_ns) == before[p.name][1:]


def test_reader_source_has_no_write_capable_open() -> None:
    src = Path(reader.__file__).read_text(encoding="utf-8")
    opens = re.findall(r"\bopen\(([^)]*)\)", src)
    code_opens = [o for o in opens if o.startswith("path")]
    assert code_opens and all(o.replace("'", '"') == 'path, "rb"' for o in code_opens)
    for forbidden in ("os.rename", "os.replace", "os.remove", "os.utime", "write_text", "write_bytes", "touch("):
        assert forbidden not in src
    assert os is not None
