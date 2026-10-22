"""T-M2-06: front-matter detection and the restricted YAML-subset parser."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing.markdown import (
    KIND_FRONT_MATTER,
    KIND_NORMAL,
    annotate,
    classify_list_line,
    detect_front_matter,
    scan_document,
)
from actiontrack.parsing.reader import DecodedText, classify_and_decode, split_lines

BODY = "- [ ] Priya to send the deck"


def _lines(text: str) -> list[str]:
    return split_lines(text)


def _fm(text: str):
    return detect_front_matter(_lines(text), "n.md")


@pytest.mark.ac("AC-01.15")
@pytest.mark.parametrize(
    ("name", "title", "date", "attendees"),
    [
        ("2026-09-16-standup.md", "Team standup", "2026-09-16", ["Anita Desai", "Lena Fischer", "Meera Iyer"]),
        ("2026-09-30-standup.md", "Team standup", "2026-09-30", ["Priya Nair", "Sam Okafor", "Karan Joshi"]),
    ],
)
def test_sample_front_matter(fixtures_dir: Path, name: str, title: str, date: str, attendees: list[str]) -> None:
    d = classify_and_decode((fixtures_dir / name).read_bytes())
    assert isinstance(d, DecodedText)
    lines = split_lines(d.text)
    fm, warns = detect_front_matter(lines, name)
    assert warns == ()
    assert fm is not None and fm.end_line == 5
    assert fm.fields["title"] == title and fm.fields["date"] == date
    assert fm.fields["attendees"] == attendees
    assert fm.key_lines == {"title": 2, "date": 3, "attendees": 4}
    ann = annotate(lines, fm.end_line)
    assert [i.kind for i in ann.lines[:5]] == [KIND_FRONT_MATTER] * 5
    first_cb = next(n for n, ln in enumerate(lines, 1) if classify_list_line(ln))
    assert first_cb == 11


@pytest.mark.ac("AC-01.20")
def test_no_yaml_library_used() -> None:
    root = Path(__file__).parents[2]
    pat = re.compile(r"^\s*(?:import|from)\s+(?:yaml|ruamel|pyyaml)\b", re.IGNORECASE | re.MULTILINE)
    for p in (root / "src").rglob("*.py"):
        assert not pat.search(p.read_text(encoding="utf-8")), p
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "pyyaml" not in pyproject and "ruamel" not in pyproject
    fm, _ = _fm("---\ntitle: x\n---\n")
    assert fm is not None
    assert "yaml" not in sys.modules


@pytest.mark.ac("AC-01.20")
@pytest.mark.parametrize(
    "block",
    [
        "base: &a x",
        "ref: *a",
        "n: !!int 5",
        "n: !custom 5",
        "meta:\n  author: Sam",
        "desc: |\n  line one",
        "desc: >\n  folded",
        "desc: first\n  continued",
        "{a: 1}",
        "m: {a: 1}",
        "this: is: not [valid",
        "attendees: [A, B",
        'title: "unterminated',
        "title: 'unterminated",
        "title: a [b",
        "just prose without colon",
        "- orphan item",
        "k: v\n- orphan after scalar",
        "date: 2026-09-16 extra: x",
        "t: \"a\" junk",
        "l: [a, [b]]",
    ],
)
def test_outside_subset_is_absent_with_one_warning_and_body_survives(block: str) -> None:
    text = f"---\n{block}\n---\n{BODY}\n"
    lines = _lines(text)
    fm, warns = detect_front_matter(lines, "n.md")
    assert fm is None
    assert [(w.code, w.line) for w in warns] == [(codes.W_FRONTMATTER_INVALID, 1)]
    scan = scan_document(lines, "n.md")
    assert scan.front_matter is None
    assert [w.code for w in scan.warnings] == [codes.W_FRONTMATTER_INVALID]
    body_line = len(lines)
    assert scan.annotation.lines[body_line - 1].kind == KIND_NORMAL
    ll = classify_list_line(scan.annotation.lines[body_line - 1].visible_text)
    assert ll is not None and ll.rest == "Priya to send the deck"
    assert lines[body_line - 1] == BODY  # unshifted line number


@pytest.mark.sc("SC-34")
def test_unclosed_block() -> None:
    lines = _lines(f"---\ntitle: x\n\n{BODY}\n")
    fm, warns = detect_front_matter(lines, "n.md")
    assert fm is None and [(w.code, w.line) for w in warns] == [(codes.W_FRONTMATTER_INVALID, 1)]
    scan = scan_document(lines)
    assert all(i.kind == KIND_NORMAL for i in scan.annotation.lines)


def test_horizontal_rule_document_is_false_positive() -> None:
    lines = _lines("---\nSome prose here.\nMore prose\n---\n" + BODY + "\n")
    fm, warns = detect_front_matter(lines)
    assert fm is None and [w.code for w in warns] == [codes.W_FRONTMATTER_INVALID]


def test_no_front_matter_no_warning() -> None:
    assert _fm("# Title\n---\n") == (None, ())
    assert _fm("") == (None, ())
    assert _fm(" ---\na: b\n---\n") == (None, ())


def test_supported_forms() -> None:
    text = (
        "---\n"
        "# whole line comment\n"
        "Date: 2026-09-16   # trailing comment\n"
        'title: "A: B"\n'
        "subtitle: 'It''s ok'\n"
        "attendees:\n"
        "  - Anita Desai\n"
        "  - 'Lena Fischer'\n"
        "- Meera Iyer\n"
        "empty:\n"
        "flow: [ 'A, B', C ,D ]\n"
        "none: []\n"
        "time: 10:30\n"
        "title: Last wins\n"
        "\n"
        "...\n"
        f"{BODY}\n"
    )
    fm, warns = _fm(text)
    assert warns == () and fm is not None
    f = fm.fields
    assert f["date"] == "2026-09-16"  # lower-cased key, no coercion, comment stripped
    assert f["title"] == "Last wins"
    assert f["subtitle"] == "It's ok"
    assert f["attendees"] == ["Anita Desai", "Lena Fischer", "Meera Iyer"]
    assert f["empty"] == ""
    assert f["flow"] == ["A, B", "C", "D"]
    assert f["none"] == []
    assert f["time"] == "10:30"
    assert fm.end_line == 16
    assert fm.key_lines["date"] == 3 and fm.key_lines["title"] == 14


def test_quoted_title_with_colon_and_trailing_spaces_on_fences() -> None:
    fm, warns = _fm('---   \ntitle: "A: B"\n---  \n')
    assert warns == () and fm is not None and fm.fields == {"title": "A: B"} and fm.end_line == 3


def test_bom_before_opening_line() -> None:
    d = classify_and_decode(b"\xef\xbb\xbf---\ntitle: T\n---\n- [ ] X\n")
    assert isinstance(d, DecodedText)
    fm, warns = detect_front_matter(split_lines(d.text))
    assert warns == () and fm is not None and fm.fields["title"] == "T"
    fm2, _ = detect_front_matter(["\ufeff---", "title: T", "---"])
    assert fm2 is not None


def test_checkbox_looking_list_line_stays_in_region() -> None:
    text = "---\ntitle: T\ntasks:\n  - [ ] Foo\n  - [x] Bar baz\n---\n- [ ] real\n"
    lines = _lines(text)
    fm, warns = detect_front_matter(lines)
    assert warns == () and fm is not None and fm.end_line == 6
    assert fm.fields["tasks"] == ["[ ] Foo", "[x] Bar baz"]
    ann = annotate(lines, fm.end_line)
    assert [i.kind for i in ann.lines] == [KIND_FRONT_MATTER] * 6 + [KIND_NORMAL]


def test_crlf_front_matter_same_as_lf() -> None:
    lf = "---\ntitle: T\ndate: 2026-01-02\n---\n- [ ] a\n"
    r = classify_and_decode(lf.replace("\n", "\r\n").encode())
    assert isinstance(r, DecodedText)
    assert detect_front_matter(split_lines(r.text)) == detect_front_matter(split_lines(lf))
