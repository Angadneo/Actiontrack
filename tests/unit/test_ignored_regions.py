"""T-M2-11: checkboxes inside ignored regions are reported, never imported."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing.markdown import (
    classify_info,
    ignored_region_warnings,
    scan_document,
)
from actiontrack.parsing.reader import DecodedText, classify_and_decode, split_lines


def _scan(text: str):
    return scan_document(split_lines(text), "n.md")


def _ignored(text: str):
    scan = _scan(text)
    return [(w.line, json.loads(w.detail)["region"], json.loads(w.detail)["raw_text"]) for w in
            ignored_region_warnings(scan.annotation, "n.md")], scan


@pytest.mark.ac("AC-01.5", "AC-01.14")
@pytest.mark.sc("SC-33")
def test_sample_has_exactly_three_ignored_region_warnings(fixtures_dir: Path) -> None:
    found: list[tuple[str, int, str, str]] = []
    for p in sorted(fixtures_dir.glob("*.md")):
        d = classify_and_decode(p.read_bytes(), p.name)
        if not isinstance(d, DecodedText):
            continue
        scan = scan_document(split_lines(d.text), p.name)
        for w in ignored_region_warnings(scan.annotation, p.name):
            assert w.code == codes.W_CHECKBOX_IGNORED_REGION
            detail = json.loads(w.detail)
            found.append((p.name, w.line, detail["region"], detail["raw_text"]))
    assert found == [
        ("2026-09-21-incident-review.md", 9, "quote", "> - [ ] Karan to refund affected customers by Friday"),
        ("2026-10-09-security-review.md", 10, "fence", "- [ ] Owner to remediate finding by <date>"),
        (
            "2026-10-09-security-review.md",
            13,
            "comment",
            "<!-- - [ ] Sam to buy hardware tokens for everyone by Friday  (cut from the agenda, not agreed) -->",
        ),
    ]


def test_message_has_file_and_line() -> None:
    scan = _scan("> - [ ] a\n")
    (w,) = ignored_region_warnings(scan.annotation, "notes/n.md")
    assert "notes/n.md:1" in w.message and w.line == 1


def test_nested_quote() -> None:
    found, _ = _ignored("> > - [ ] nested\n")
    assert found == [(1, "quote", "> > - [ ] nested")]


def test_tilde_fence_with_done_box() -> None:
    found, _ = _ignored("~~~\n* [x] a\n~~~\n")
    assert found == [(2, "fence", "* [x] a")]


def test_multiline_comment_second_line() -> None:
    found, _ = _ignored("<!-- note\n- [ ] b\n-->\n")
    assert found == [(2, "comment", "- [ ] b")]


def test_comment_opener_line_with_task() -> None:
    found, _ = _ignored("<!-- - [ ] x\nmore -->\n")
    assert found == [(1, "comment", "<!-- - [ ] x")]


def test_unclosed_fence_remainder() -> None:
    found, scan = _ignored("- [ ] real\n```\n- [ ] a\n- [x] b\n")
    assert [(n, r) for n, r, _ in found] == [(3, "fence"), (4, "fence")]
    assert [w.code for w in scan.warnings] == [codes.W_FENCE_UNCLOSED]


@pytest.mark.ac("AC-01.18")
def test_unclosed_comment_remainder() -> None:
    found, scan = _ignored("- [ ] real\n<!-- never closed\n- [ ] hidden\n")
    assert found == [(3, "comment", "- [ ] hidden")]
    assert [w.code for w in scan.warnings] == [codes.W_HTML_COMMENT_UNCLOSED]
    assert classify_info(scan.annotation.lines[2]) is None


def test_front_matter_yaml_list_item() -> None:
    found, scan = _ignored("---\ntitle: T\ntasks:\n  - [ ] z\n---\n- [ ] real\n")
    assert found == [(4, "front_matter", "  - [ ] z")]
    assert scan.warnings == ()
    assert classify_info(scan.annotation.lines[5]) is not None


def test_nonstandard_and_empty_boxes_only_reported_as_ignored() -> None:
    found, _ = _ignored("```\n- [-] x\n- [ ]\n- [] y\n```\n")
    assert [n for n, _, _ in found] == [2, 3, 4]
    scan = _scan("```\n- [-] x\n- [ ]\n```\n")
    warns = ignored_region_warnings(scan.annotation)
    assert {w.code for w in warns} == {codes.W_CHECKBOX_IGNORED_REGION}


def test_plain_bullets_and_prose_in_regions_are_silent() -> None:
    found, _ = _ignored("```\n- plain\ntext\n```\n> - plain\n> text\n<!-- - plain -->\n")
    assert found == []


def test_document_order_one_per_line_and_normal_lines_ignored() -> None:
    text = "- [ ] normal\n> - [ ] q\n```\n- [ ] f\n```\n<!--\n- [ ] c\n-->\n"
    found, _ = _ignored(text)
    assert [(n, r) for n, r, _ in found] == [(2, "quote"), (4, "fence"), (7, "comment")]


def test_crlf_gives_same_warnings() -> None:
    lf = "> - [ ] q\n```\n- [ ] f\n```\n"
    r = classify_and_decode(lf.replace("\n", "\r\n").encode())
    assert isinstance(r, DecodedText)
    a = ignored_region_warnings(_scan(lf).annotation, "n.md")
    b = ignored_region_warnings(scan_document(split_lines(r.text), "n.md").annotation, "n.md")
    assert a == b
