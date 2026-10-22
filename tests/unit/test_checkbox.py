"""T-M2-08: checkbox line recogniser."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing.markdown import (
    LIST_CHECKBOX_DONE,
    LIST_CHECKBOX_EMPTY,
    LIST_CHECKBOX_NONSTANDARD,
    LIST_CHECKBOX_OPEN,
    LIST_PLAIN_BULLET,
    KIND_NORMAL,
    annotate,
    classify_info,
    classify_list_line,
    list_line_warning,
    scan_document,
)
from actiontrack.parsing.reader import DecodedText, classify_and_decode, split_lines

OPEN, DONE, NONSTD, EMPTY, PLAIN = (
    LIST_CHECKBOX_OPEN, LIST_CHECKBOX_DONE, LIST_CHECKBOX_NONSTANDARD, LIST_CHECKBOX_EMPTY, LIST_PLAIN_BULLET
)

# (line, kind, rest, indent_width, warning codes)
POSITIVE = [
    ("- [ ] X", OPEN, "X", 0, ()),
    ("- [ ]X", OPEN, "X", 0, ()),
    ("* [ ] X", OPEN, "X", 0, ()),
    ("+ [X] X", DONE, "X", 0, ()),
    ("- [x] X", DONE, "X", 0, ()),
    ("1. [ ] X", OPEN, "X", 0, ()),
    ("1) [x] X", DONE, "X", 0, ()),
    ("12. [ ] X", OPEN, "X", 0, ()),
    ("123456789) [ ] X", OPEN, "X", 0, ()),
    ("    - [ ] X", OPEN, "X", 4, ()),
    ("\t- [ ] X", OPEN, "X", 4, ()),
    (" \t- [ ] X", OPEN, "X", 4, ()),
    ("  \t  - [ ] X", OPEN, "X", 6, ()),
    ("  - [ ] X", OPEN, "X", 2, ()),
    ("-\t[ ] X", OPEN, "X", 0, ()),
    ("-   [ ]   X  y", OPEN, "X  y", 0, ()),
    ("- []X", OPEN, "X", 0, (codes.W_CHECKBOX_NOSPACE,)),
    ("- [] X", OPEN, "X", 0, (codes.W_CHECKBOX_NOSPACE,)),
    ("- [ ]Priya to send the deck", OPEN, "Priya to send the deck", 0, ()),
    ("- [x]Priya to send the deck", DONE, "Priya to send the deck", 0, ()),
    ("- [-] X", NONSTD, "X", 0, (codes.W_CHECKBOX_NONSTANDARD,)),
    ("- [~] X", NONSTD, "X", 0, (codes.W_CHECKBOX_NONSTANDARD,)),
    ("- [/] X", NONSTD, "X", 0, (codes.W_CHECKBOX_NONSTANDARD,)),
    ("- [?] X", NONSTD, "X", 0, (codes.W_CHECKBOX_NONSTANDARD,)),
    ("- [!] X", NONSTD, "X", 0, (codes.W_CHECKBOX_NONSTANDARD,)),
    ("- [>] X", NONSTD, "X", 0, (codes.W_CHECKBOX_NONSTANDARD,)),
    ("- [-]", NONSTD, "", 0, (codes.W_CHECKBOX_NONSTANDARD,)),
    ("- [ ]", EMPTY, "", 0, (codes.W_CHECKBOX_EMPTY,)),
    ("- [x]", EMPTY, "", 0, (codes.W_CHECKBOX_EMPTY,)),
    ("- [X]", EMPTY, "", 0, (codes.W_CHECKBOX_EMPTY,)),
    ("- [ ]   ", EMPTY, "", 0, (codes.W_CHECKBOX_EMPTY,)),
    ("- [ ]\t", EMPTY, "", 0, (codes.W_CHECKBOX_EMPTY,)),
    ("1. [ ]", EMPTY, "", 0, (codes.W_CHECKBOX_EMPTY,)),
    ("- []", EMPTY, "", 0, (codes.W_CHECKBOX_EMPTY,)),
    ("- [] ", EMPTY, "", 0, (codes.W_CHECKBOX_EMPTY,)),
    ("- plain bullet", PLAIN, "plain bullet", 0, ()),
    ("1. plain", PLAIN, "plain", 0, ()),
    ("  * nested plain", PLAIN, "nested plain", 2, ()),
    ("- [a] note", PLAIN, "[a] note", 0, ()),
    ("- [1] note", PLAIN, "[1] note", 0, ()),
    ("- [link](http://u) text", PLAIN, "[link](http://u) text", 0, ()),
    ("- [x](http://u) text", PLAIN, "[x](http://u) text", 0, ()),
    ("- [  ] x", PLAIN, "[  ] x", 0, ()),
    ("- [-]X", PLAIN, "[-]X", 0, ()),
    ("- [ab] x", PLAIN, "[ab] x", 0, ()),
]

NEGATIVE = [
    "-[ ] x",
    "[ ] x",
    "[x] done",
    "- ",  # empty marker is still a bullet? (checked separately)
    "text - [ ] x",
    "-- [ ] x",
    "1.[ ] x",
    "1234567890. [ ] x",
    "a. [ ] x",
    "",
    "   ",
    "# - [ ] x",
    "---",
    "-text",
    "**bold** - [ ] x",
]


@pytest.mark.parametrize(("line", "kind", "rest", "indent", "warn"), POSITIVE)
def test_classification_table(line: str, kind: str, rest: str, indent: int, warn: tuple[str, ...]) -> None:
    ll = classify_list_line(line)
    assert ll is not None, line
    assert ll.kind == kind
    assert ll.rest == rest
    assert ll.indent_width == indent
    assert ll.warning_codes == warn
    assert line[ll.rest_start :].strip() == rest.strip() or kind in (EMPTY, NONSTD)
    assert ll.importable == (kind in (OPEN, DONE))
    assert (ll.nonstandard_char is not None) == (kind == NONSTD)


def test_table_has_at_least_thirty_lines() -> None:
    assert len(POSITIVE) >= 30


@pytest.mark.parametrize("line", [n for n in NEGATIVE if n != "- "])
def test_not_a_list_line(line: str) -> None:
    assert classify_list_line(line) is None, line


@pytest.mark.ac("AC-01.4")
def test_ac_01_4_variants_import_or_not() -> None:
    importable = ["- [ ]X", "* [ ] X", "+ [X] X", "1. [ ] X", "1) [x] X", "    - [ ] X", "\t- [ ] X", "- []X"]
    for line in importable:
        ll = classify_list_line(line)
        assert ll is not None and ll.importable, line
    for line in ("- [-] X", "- [~] X", "- [/] X", "- [?] X"):
        ll = classify_list_line(line)
        assert ll is not None and not ll.importable and ll.kind == NONSTD, line
    only_nospace = [ln for ln in importable if codes.W_CHECKBOX_NOSPACE in classify_list_line(ln).warning_codes]
    assert only_nospace == ["- []X"]


@pytest.mark.ac("AC-01.18")
def test_ac_01_18_silent_space_form_and_nospace_form() -> None:
    silent = classify_list_line("- [ ]Priya to send the deck")
    assert silent.kind == OPEN and silent.warning_codes == ()
    assert list_line_warning(silent, "- [ ]Priya to send the deck", 3) is None
    ns = classify_list_line("- []Priya to send the deck")
    assert ns.kind == OPEN and ns.warning_codes == (codes.W_CHECKBOX_NOSPACE,)
    w = list_line_warning(ns, "- []Priya to send the deck", 7, "n.md")
    assert w.code == codes.W_CHECKBOX_NOSPACE and w.line == 7 and "n.md:7" in w.message
    assert w.detail == "- []Priya to send the deck"


@pytest.mark.ac("AC-01.18")
@pytest.mark.parametrize("line", ["- [ ]", "- [x]", "- [ ]   ", "1. [ ]"])
def test_empty_checkbox_is_empty_not_nonstandard(line: str) -> None:
    ll = classify_list_line(line)
    assert ll.kind == EMPTY and not ll.importable
    assert ll.warning_codes == (codes.W_CHECKBOX_EMPTY,)
    w = list_line_warning(ll, line, 4, "n.md")
    assert w.code == codes.W_CHECKBOX_EMPTY and w.detail == line


def test_nonstandard_warning_carries_raw_text() -> None:
    w = list_line_warning(classify_list_line("- [~] Sam to x"), "- [~] Sam to x", 9, "n.md")
    assert w.code == codes.W_CHECKBOX_NONSTANDARD and w.line == 9 and w.detail == "- [~] Sam to x"


def test_empty_first_line_is_not_rescued_by_following_text() -> None:
    lines = ["- [ ]", "  Priya to send the deck"]
    assert classify_list_line(lines[0]).kind == EMPTY
    assert classify_list_line(lines[1]) is None


def test_bare_marker_is_a_plain_bullet_with_empty_rest() -> None:
    ll = classify_list_line("- ")
    assert ll is not None and ll.kind == PLAIN and ll.rest == ""
    assert classify_list_line("-") is None


def test_offsets_point_into_the_line() -> None:
    line = "  - [x]  Sam to go"
    ll = classify_list_line(line)
    assert ll.marker == "-" and ll.marker_col == 2 and ll.box_raw == "[x]"
    assert line[ll.rest_start :] == "Sam to go"


@pytest.mark.sc("SC-38")
@pytest.mark.parametrize(
    ("name", "lineno", "kind", "rest_prefix", "indent"),
    [
        ("2026-09-30-q4-offsite.md", 11, OPEN, "Lena to run the customer survey", 0),
        ("2026-09-30-q4-offsite.md", 12, OPEN, "Sam to propose", 0),
        ("2026-10-02-finance-sync.md", 9, DONE, "Karan to approve", 0),
        ("2026-09-30-q4-offsite.md", 10, OPEN, "Karan to validate", 2),
    ],
)
def test_sample_lines(fixtures_dir: Path, name: str, lineno: int, kind: str, rest_prefix: str, indent: int) -> None:
    d = classify_and_decode((fixtures_dir / name).read_bytes())
    assert isinstance(d, DecodedText)
    ll = classify_list_line(split_lines(d.text)[lineno - 1])
    assert ll is not None and ll.kind == kind and ll.rest.startswith(rest_prefix) and ll.indent_width == indent


def test_sample_checkbox_census(fixtures_dir: Path) -> None:
    """Zero EMPTY/NOSPACE warnings anywhere in the sample; counts of open/done outside regions."""
    open_n = done_n = 0
    seen: set[str] = set()
    for p in sorted(fixtures_dir.glob("*.md")):
        d = classify_and_decode(p.read_bytes(), p.name)
        if not isinstance(d, DecodedText):
            continue
        scan = scan_document(split_lines(d.text), p.name)
        for info in scan.annotation.lines:
            ll = classify_info(info)
            if ll is None:
                continue
            seen.update(ll.warning_codes)
            open_n += ll.kind == OPEN
            done_n += ll.kind == DONE
    assert codes.W_CHECKBOX_EMPTY not in seen and codes.W_CHECKBOX_NOSPACE not in seen
    assert (open_n, done_n) == (104, 13)


def test_classify_info_only_for_normal_lines() -> None:
    ann = annotate(["- [ ] a", "```", "- [ ] b", "```", "<!-- - [ ] c -->", "> - [ ] d", "x <!-- y --> - [ ] e"])
    got = [classify_info(i) is not None for i in ann.lines]
    assert got == [True, False, False, False, False, False, False]
    assert [i.kind for i in ann.lines][6] == KIND_NORMAL
    ann2 = annotate(["<!-- c --> - [ ] real"])
    ll = classify_info(ann2.lines[0])
    assert ll is not None and ll.rest == "real"


def test_long_line_is_linear() -> None:
    for line in ("- [ ] " + "x" * 200_000, "-" + " " * 200_000 + "[ ]", "1" * 200_000 + ". [ ] x", "- [" + " " * 100_000):
        t0 = time.perf_counter()
        classify_list_line(line)
        assert time.perf_counter() - t0 < 1.0
