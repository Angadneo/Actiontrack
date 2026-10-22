"""T-M2-07: region detection (fences, HTML comments, blockquotes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing.markdown import (
    KIND_COMMENT,
    KIND_FENCE,
    KIND_FRONT_MATTER,
    KIND_NORMAL,
    KIND_QUOTE,
    annotate,
    scan_document,
)
from actiontrack.parsing.reader import DecodedText, classify_and_decode, split_lines


def _ann(text: str, fm_end: int = 0):
    return annotate(split_lines(text), fm_end, "n.md")


def _kinds(text: str, fm_end: int = 0) -> list[str]:
    return [i.kind for i in _ann(text, fm_end).lines]


def _load(fixtures_dir: Path, name: str):
    d = classify_and_decode((fixtures_dir / name).read_bytes(), name)
    assert isinstance(d, DecodedText)
    return split_lines(d.text)


@pytest.mark.ac("AC-01.5")
@pytest.mark.sc("SC-33")
def test_security_review_sample(fixtures_dir: Path) -> None:
    ann = annotate(_load(fixtures_dir, "2026-10-09-security-review.md"), 0, "x")
    kinds = {n: i.kind for n, i in enumerate(ann.lines, 1)}
    assert [kinds[n] for n in range(8, 12)] == [KIND_FENCE] * 4
    assert kinds[13] == KIND_COMMENT
    assert all(kinds[n] == KIND_NORMAL for n in (18, 19, 20, 21))
    assert ann.warnings == ()


@pytest.mark.ac("AC-01.14")
def test_incident_review_quote(fixtures_dir: Path) -> None:
    ann = annotate(_load(fixtures_dir, "2026-09-21-incident-review.md"))
    kinds = {n: i.kind for n, i in enumerate(ann.lines, 1)}
    assert kinds[8] == kinds[9] == KIND_QUOTE
    assert kinds[10] == KIND_NORMAL
    assert all(kinds[n] == KIND_NORMAL for n in (14, 15, 16, 17))


def test_sample_matches_golden_region_table(fixtures_dir: Path) -> None:
    golden = json.loads((Path(__file__).parents[1] / "golden" / "m2_regions.json").read_text(encoding="utf-8"))
    golden.pop("_comment")
    seen_warnings: list[str] = []
    for p in sorted(fixtures_dir.glob("*.md")):
        d = classify_and_decode(p.read_bytes(), p.name)
        if not isinstance(d, DecodedText):
            continue
        scan = scan_document(split_lines(d.text), p.name)
        ranges: list[list] = []
        for n, info in enumerate(scan.annotation.lines, 1):
            if info.kind == KIND_NORMAL:
                continue
            if ranges and ranges[-1][0] == info.kind and ranges[-1][2] == n - 1:
                ranges[-1][2] = n
            else:
                ranges.append([info.kind, n, n])
        assert ranges == golden.get(p.name, []), p.name
        seen_warnings += [w.code for w in scan.annotation.warnings]
    assert codes.W_FENCE_UNCLOSED not in seen_warnings
    assert codes.W_HTML_COMMENT_UNCLOSED not in seen_warnings


@pytest.mark.ac("AC-01.18")
def test_unclosed_comment_swallows_the_rest() -> None:
    text = "- [ ] real a\n- [ ] real b\n<!-- todo later\n- [ ] hidden task\nprose\n"
    ann = _ann(text)
    assert [i.kind for i in ann.lines] == [KIND_NORMAL, KIND_NORMAL, KIND_COMMENT, KIND_COMMENT, KIND_COMMENT]
    assert [(w.code, w.line) for w in ann.warnings] == [(codes.W_HTML_COMMENT_UNCLOSED, 3)]


def test_unclosed_comment_after_text_keeps_the_text_visible() -> None:
    ann = _ann("text <!-- never closed\nmore\n")
    assert ann.lines[0].kind == KIND_NORMAL and ann.lines[0].visible_text.strip() == "text"
    assert ann.lines[1].kind == KIND_COMMENT
    assert [(w.code, w.line) for w in ann.warnings] == [(codes.W_HTML_COMMENT_UNCLOSED, 1)]


def test_unclosed_comment_inside_fence_is_literal() -> None:
    ann = _ann("```\n<!-- not a comment\n```\n- [ ] x\n")
    assert [i.kind for i in ann.lines] == [KIND_FENCE] * 3 + [KIND_NORMAL]
    assert ann.warnings == ()


def test_comment_inside_closed_comment_does_not_raise() -> None:
    ann = _ann("<!-- a <!-- b -->\n- [ ] x\n")
    assert [i.kind for i in ann.lines] == [KIND_COMMENT, KIND_NORMAL]
    assert ann.warnings == ()


def test_multiline_comment_and_midline_spans() -> None:
    ann = _ann("<!-- a\nb\nc --> tail\n<!-- a --> - [ ] real\nx <!-- y --> z\n")
    assert [i.kind for i in ann.lines] == [KIND_COMMENT, KIND_COMMENT, KIND_NORMAL, KIND_NORMAL, KIND_NORMAL]
    assert ann.lines[2].visible_text.strip() == "tail"
    assert ann.lines[3].visible_text.strip() == "- [ ] real"
    assert ann.lines[4].visible_text.split() == ["x", "z"]
    assert len(ann.lines[3].visible_text) == len(ann.lines[3].text)
    assert ann.warnings == ()


def test_comment_closes_and_reopens_on_one_line() -> None:
    ann = _ann("<!-- a\nb --> x <!-- open\nstill\n")
    assert [i.kind for i in ann.lines] == [KIND_COMMENT, KIND_NORMAL, KIND_COMMENT]
    assert [(w.code, w.line) for w in ann.warnings] == [(codes.W_HTML_COMMENT_UNCLOSED, 2)]


def test_single_line_comment_is_comment_kind_and_blank_is_normal() -> None:
    assert _kinds("<!-- x -->\n\ntext\n") == [KIND_COMMENT, KIND_NORMAL, KIND_NORMAL]


def test_fence_marker_inside_comment_is_literal() -> None:
    ann = _ann("<!--\n```\n-->\n- [ ] x\n")
    assert [i.kind for i in ann.lines] == [KIND_COMMENT] * 3 + [KIND_NORMAL]
    assert ann.warnings == ()


def test_tilde_fence() -> None:
    assert _kinds("~~~\n- [ ] a\n~~~\n- [ ] b\n") == [KIND_FENCE] * 3 + [KIND_NORMAL]


def test_backtick_fence_needs_equal_or_longer_closer() -> None:
    text = "````\n```\n- [ ] a\n````\n- [ ] b\n"
    assert _kinds(text) == [KIND_FENCE] * 4 + [KIND_NORMAL]
    ann = _ann("````\n```\n- [ ] a\n")
    assert [i.kind for i in ann.lines] == [KIND_FENCE] * 3
    assert [(w.code, w.line) for w in ann.warnings] == [(codes.W_FENCE_UNCLOSED, 1)]


def test_other_character_does_not_close() -> None:
    ann = _ann("```\n~~~\n- [ ] a\n")
    assert [i.kind for i in ann.lines] == [KIND_FENCE] * 3
    assert [w.code for w in ann.warnings] == [codes.W_FENCE_UNCLOSED]
    assert _kinds("~~~\n```\n~~~\nx\n") == [KIND_FENCE] * 3 + [KIND_NORMAL]


def test_closer_must_have_only_whitespace_after() -> None:
    ann = _ann("```\n``` text\n- [ ] a\n```\nz\n")
    assert [i.kind for i in ann.lines] == [KIND_FENCE] * 4 + [KIND_NORMAL]


def test_backtick_opener_info_string_may_not_contain_backtick() -> None:
    assert _kinds("``` a`b\n- [ ] x\n") == [KIND_NORMAL, KIND_NORMAL]
    assert _kinds("```python\n- [ ] x\n```\n") == [KIND_FENCE] * 3


@pytest.mark.sc("SC-42")
def test_unclosed_fence_swallows_the_rest() -> None:
    ann = _ann("- [ ] before\n```\ncode\n- [ ] later\n")
    assert [i.kind for i in ann.lines] == [KIND_NORMAL] + [KIND_FENCE] * 3
    assert [(w.code, w.line) for w in ann.warnings] == [(codes.W_FENCE_UNCLOSED, 2)]
    assert not any(w.code == codes.W_HTML_COMMENT_UNCLOSED for w in ann.warnings)


def test_tab_and_deep_indented_fences() -> None:
    assert _kinds("\t```\n- [ ] a\n\t```\nx\n") == [KIND_FENCE] * 3 + [KIND_NORMAL]
    assert _kinds("      ~~~\na\n      ~~~\nx\n") == [KIND_FENCE] * 3 + [KIND_NORMAL]


def test_blockquote_rules() -> None:
    text = "> a\n> > nested\n   > three spaces\n    > four spaces\nlazy\n> b\n\n> c\n"
    assert _kinds(text) == [
        KIND_QUOTE, KIND_QUOTE, KIND_QUOTE, KIND_NORMAL, KIND_NORMAL, KIND_QUOTE, KIND_NORMAL, KIND_QUOTE
    ]


def test_quote_line_never_opens_fence_or_comment() -> None:
    ann = _ann("> ```\n- [ ] a\n> <!-- x\n- [ ] b\n")
    assert [i.kind for i in ann.lines] == [KIND_QUOTE, KIND_NORMAL, KIND_QUOTE, KIND_NORMAL]
    assert ann.warnings == ()


def test_precedence_front_matter_fence_comment_quote() -> None:
    ann = _ann("---\n> q\n---\n```\n> q\n<!-- c\n```\n<!-- c\n> q\n-->\n> q\n", fm_end=3)
    assert [i.kind for i in ann.lines] == [
        KIND_FRONT_MATTER, KIND_FRONT_MATTER, KIND_FRONT_MATTER,
        KIND_FENCE, KIND_FENCE, KIND_FENCE, KIND_FENCE,
        KIND_COMMENT, KIND_COMMENT, KIND_COMMENT,
        KIND_QUOTE,
    ]


def test_crlf_gives_identical_annotation() -> None:
    lf = "# T\n```\n- [ ] a\n```\n<!-- c\n- [ ] b\n-->\n> q\n- [ ] ok\n"
    a = annotate(split_lines(lf))
    r = classify_and_decode(lf.replace("\n", "\r\n").encode())
    assert isinstance(r, DecodedText)
    assert annotate(split_lines(r.text)) == a


def test_no_quadratic_blowup_on_many_comment_openers() -> None:
    text = "<!--" * 50_000 + "\n- [ ] x\n"
    ann = _ann(text)
    assert [i.kind for i in ann.lines] == [KIND_COMMENT, KIND_COMMENT]
    assert [w.code for w in ann.warnings] == [codes.W_HTML_COMMENT_UNCLOSED]
