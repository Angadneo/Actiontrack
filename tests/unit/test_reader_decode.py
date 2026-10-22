"""T-M2-05: content classification and decoding."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.domain.models import FileFailure
from actiontrack.parsing import reader
from actiontrack.parsing.reader import (
    DecodedText,
    Empty,
    classify_and_decode,
    find_long_lines,
    split_lines,
)

BOM = b"\xef\xbb\xbf"


def test_scratch_sample_is_binary_not_encoding(fixtures_dir: Path) -> None:
    raw = (fixtures_dir / "scratch.md").read_bytes()
    assert raw[:5] == b"\x00\x01\x02\xff\xfe"
    r = classify_and_decode(raw, "scratch.md")
    assert isinstance(r, FileFailure) and r.code == codes.E_BINARY
    assert "scratch.md" in r.message


def test_untitled_sample_decodes(fixtures_dir: Path) -> None:
    r = classify_and_decode((fixtures_dir / "untitled.md").read_bytes(), "untitled.md")
    assert isinstance(r, DecodedText)


def test_invalid_utf8_reports_byte_offset() -> None:
    r = classify_and_decode(b"caf\xe9\n", "a.md")
    assert isinstance(r, FileFailure) and r.code == codes.E_ENCODING and r.byte_offset == 3
    assert "3" in r.message
    r2 = classify_and_decode(BOM + b"caf\xe9\n", "a.md")
    assert isinstance(r2, FileFailure) and r2.byte_offset == 6


def test_nul_precedes_encoding_check() -> None:
    r = classify_and_decode(b"ab\xff\x00cd", "a.md")
    assert isinstance(r, FileFailure) and r.code == codes.E_BINARY


@pytest.mark.parametrize(
    "raw",
    [
        "日本".encode("utf-16"),  # BOM FF FE, no NUL bytes
        b"\xfe\xff\x65\xe5",
        b"\xff\xfe\x00\x00a\x00\x00\x00",
        b"\x00\x00\xfe\xff",
        b"\x89PNG\r\n\x1a\nIHDRabc",
    ],
)
def test_binary_headers(raw: bytes) -> None:
    r = classify_and_decode(raw)
    assert isinstance(r, FileFailure) and r.code == codes.E_BINARY


def test_utf16_le_japanese_has_no_nul() -> None:
    raw = "日本".encode("utf-16-le")
    assert b"\x00" not in b"\xff\xfe" + raw
    r = classify_and_decode(b"\xff\xfe" + raw)
    assert isinstance(r, FileFailure) and r.code == codes.E_BINARY


def test_bom_stripped_and_text_starts_with_dash() -> None:
    r = classify_and_decode(BOM + b"- [ ] X\n")
    assert isinstance(r, DecodedText) and r.text.startswith("-") and r.had_bom


@pytest.mark.parametrize("raw", [b"", BOM, b"\n \t\n", BOM + b"  \r\n", b"\xc2\xa0\n"])
def test_empty_forms(raw: bytes) -> None:
    assert classify_and_decode(raw) == Empty()


def test_empty_sample(copy_fixture_notes) -> None:
    root = copy_fixture_notes(names={"2026-09-09-notes-final-v2.md"})
    assert classify_and_decode((root / "2026-09-09-notes-final-v2.md").read_bytes()) == Empty()


@pytest.mark.ac("AC-01.16")
def test_crlf_cr_lf_identical_split_lines() -> None:
    base = "# T\n- [ ] a\n\n- [x] b\n"
    results = []
    for eol in ("\n", "\r\n", "\r"):
        raw = base.replace("\n", eol).encode()
        d = classify_and_decode(raw)
        assert isinstance(d, DecodedText)
        results.append(split_lines(d.text))
    assert results[0] == results[1] == results[2] == ["# T", "- [ ] a", "", "- [x] b"]


def test_crlf_sample_normalised(fixtures_dir: Path) -> None:
    raw = (fixtures_dir / "2026-10-02-finance-sync.md").read_bytes()
    assert b"\r\n" in raw
    d = classify_and_decode(raw)
    assert isinstance(d, DecodedText) and "\r" not in d.text
    assert any(line.startswith("- [X] Karan to approve") for line in split_lines(d.text))


def test_split_lines_only_on_newline() -> None:
    assert split_lines("a\x0bb\u2028c\x85d\x1ce\n") == ["a\x0bb\u2028c\x85d\x1ce"]
    assert split_lines("") == []
    assert split_lines("\n") == [""]
    assert split_lines("a\n\n") == ["a", ""]
    assert split_lines("a") == ["a"]


def test_long_line_kept_and_reported() -> None:
    long = "x" * 100_001
    t0 = time.perf_counter()
    d = classify_and_decode(("- [ ] a\n" + long + "\n").encode())
    assert isinstance(d, DecodedText)
    lines = split_lines(d.text)
    warns = find_long_lines(lines, "n.md")
    assert time.perf_counter() - t0 < 1
    assert len(lines[1]) == 100_001
    assert [(w.code, w.line) for w in warns] == [(codes.W_LONG_LINE, 2)]
    assert find_long_lines(["y" * 100_000]) == []


def test_text_is_not_nfc_normalised() -> None:
    raw = "e\u0301".encode() + b"\n"
    d = classify_and_decode(raw)
    assert isinstance(d, DecodedText) and d.text.startswith("e\u0301")


def test_no_splitlines_in_parsing_or_domain() -> None:
    import tokenize

    root = Path(reader.__file__).parents[1]
    for sub in ("parsing", "domain"):
        for p in (root / sub).glob("*.py"):
            with tokenize.open(p) as fh:
                names = [t.string for t in tokenize.generate_tokens(fh.readline) if t.type == tokenize.NAME]
            assert "splitlines" not in names, p
