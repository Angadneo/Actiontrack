"""T-M2-14: non-checkbox candidates (ACTION:/TODO: lines, table rows, plain bullets)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing.markdown import (
    CAND_ACTION_LINE,
    CAND_PLAIN_BULLET,
    CAND_TABLE_ROW,
    find_candidates,
    heading_text,
    scan_document,
)
from actiontrack.parsing.reader import DecodedText, classify_and_decode, split_lines


def _cands(text: str):
    scan = scan_document(split_lines(text), "n.md")
    res = find_candidates(scan.annotation, "n.md")
    return [(c.kind, c.line, c.text) for c in res.candidates], res


@pytest.mark.ac("AC-01.17")
@pytest.mark.sc("SC-48")
def test_sample_has_exactly_two_candidates(fixtures_dir: Path) -> None:
    found: list[tuple[str, str, int, str]] = []
    for p in sorted(fixtures_dir.glob("*.md")):
        d = classify_and_decode(p.read_bytes(), p.name)
        if not isinstance(d, DecodedText):
            continue
        scan = scan_document(split_lines(d.text), p.name)
        res = find_candidates(scan.annotation, p.name)
        assert len(res.warnings) == len(res.candidates)
        for c in res.candidates:
            found.append((p.name, c.kind, c.line, c.text))
    assert found == [
        ("2026-09-30-q4-offsite.md", CAND_TABLE_ROW, 22, "| Anita | Refresh the brand kit | 2026-10-09 |"),
        ("2026-09-30-q4-offsite.md", CAND_ACTION_LINE, 24, "ACTION: Karan to circulate the offsite summary tomorrow"),
    ]


def test_warning_shape() -> None:
    _, res = _cands("ACTION: do it\n")
    (w,) = res.warnings
    assert w.code == codes.W_NONCHECKBOX_CANDIDATES and w.line == 1 and "n.md:1" in w.message
    assert json.loads(w.detail) == {"kind": CAND_ACTION_LINE, "text": "ACTION: do it"}


@pytest.mark.parametrize("line", ["ACTION: x", "Action: x", "TODO: x", "  TODO: x", "\tAction: x"])
def test_prefix_forms(line: str) -> None:
    found, _ = _cands(f"intro\n{line}\n")
    assert found == [(CAND_ACTION_LINE, 2, line.strip())]


@pytest.mark.parametrize("line", ["action: x", "Todo: x", "ACTIONS: x", "My ACTION: x", "- ACTION: x", "ACTION x"])
def test_not_prefix_forms(line: str) -> None:
    found, _ = _cands(f"intro\n{line}\n")
    assert found == []


def test_table_header_without_action_is_ignored() -> None:
    assert _cands("| Who | Task |\n|---|---|\n| A | b |\n")[0] == []


def test_table_with_action_header_reports_body_rows_only() -> None:
    text = "| Owner | Action | Due |\n|:---|:---:|---:|\n| A | one | d |\n| B | two | e |\n\nafter\n| x | y |\n"
    found, _ = _cands(text)
    assert found == [(CAND_TABLE_ROW, 3, "| A | one | d |"), (CAND_TABLE_ROW, 4, "| B | two | e |")]


def test_table_header_case_insensitive_and_actions_word() -> None:
    assert len(_cands("| ACTIONS |\n|---|\n| a |\n")[0]) == 1
    assert len(_cands("| action item |\n| --- |\n| a |\n")[0]) == 1


def test_pipe_line_without_separator_is_not_a_table() -> None:
    assert _cands("| Action | Who |\n| a | b |\n")[0] == []


@pytest.mark.parametrize("heading", ["Next steps", "Follow-up", "Follow up", "Followup", "Action items", "TODO", "Actions", "## x"])
def test_plain_bullets_under_matching_headings(heading: str) -> None:
    h = heading if heading.startswith("#") else f"## {heading}"
    found, _ = _cands(f"{h}\n- plain\n")
    if heading == "## x":
        assert found == []
    else:
        assert found == [(CAND_PLAIN_BULLET, 2, "- plain")]


def test_plain_bullets_under_other_heading_are_not_candidates() -> None:
    assert _cands("## Discussion\n- plain\n* other\n")[0] == []


def test_heading_context_resets_on_next_heading() -> None:
    found, _ = _cands("## Next steps\n- a\n## Discussion\n- b\n## Follow-up\n1. c\n")
    assert found == [(CAND_PLAIN_BULLET, 2, "- a"), (CAND_PLAIN_BULLET, 6, "1. c")]


def test_bullets_before_any_heading_are_not_candidates() -> None:
    assert _cands("- plain\n")[0] == []


def test_checkbox_bullets_under_action_heading_are_not_candidates() -> None:
    found, _ = _cands("## Action items\n- [ ] a\n- [x] b\n- [-] c\n- [ ]\n")
    assert found == []


def test_plain_child_bullet_under_checkbox_is_not_a_candidate() -> None:
    text = "## Action items\n- [ ] parent\n  - child note\n    - grandchild\n- sibling plain\n"
    found, _ = _cands(text)
    assert found == [(CAND_PLAIN_BULLET, 5, "- sibling plain")]


def test_plain_child_under_plain_parent_is_a_candidate() -> None:
    found, _ = _cands("## Next steps\n- parent\n  - child\n")
    assert [n for _, n, _ in found] == [2, 3]


def test_blank_line_does_not_end_checkbox_nesting_but_paragraph_does() -> None:
    text = "## Next steps\n- [ ] a\n\n  - child\n\nparagraph\n- later plain\n"
    found, _ = _cands(text)
    assert found == [(CAND_PLAIN_BULLET, 7, "- later plain")]


def test_empty_plain_bullet_is_not_a_candidate() -> None:
    assert _cands("## Next steps\n- \n")[0] == []


def test_inside_regions_nothing_is_reported() -> None:
    text = (
        "## Next steps\n```\n- plain\nACTION: x\n| Action |\n|---|\n| a |\n```\n"
        "> - plain\n> ACTION: y\n<!--\n- plain\nTODO: z\n-->\n"
    )
    assert _cands(text)[0] == []
    fm = "---\ntitle: T\nlist:\n  - plain\n---\n## Next steps\n"
    assert _cands(fm)[0] == []


def test_heading_inside_fence_does_not_change_context() -> None:
    found, _ = _cands("## Discussion\n```\n## Next steps\n```\n- plain\n")
    assert found == []


def test_comment_hidden_heading_ignored() -> None:
    found, _ = _cands("## Next steps\n<!-- ## Discussion -->\n- plain\n")
    assert found == [(CAND_PLAIN_BULLET, 3, "- plain")]


def test_candidates_are_never_items_and_in_document_order() -> None:
    text = "## Next steps\n- one\nTODO: two\n| Action |\n|---|\n| three |\n"
    found, res = _cands(text)
    assert [n for _, n, _ in found] == [2, 3, 6]
    assert [w.line for w in res.warnings] == [2, 3, 6]


def test_heading_text() -> None:
    assert heading_text("## Next steps ##") == "Next steps"
    assert heading_text("#NoSpace") is None
    assert heading_text("    ## indented code") is None
    assert heading_text("# Meeting: X") == "Meeting: X"
    assert heading_text("plain") is None


def test_crlf_same_candidates() -> None:
    lf = "## Next steps\n- a\nACTION: b\n"
    r = classify_and_decode(lf.replace("\n", "\r\n").encode())
    assert isinstance(r, DecodedText)
    a = find_candidates(scan_document(split_lines(lf)).annotation)
    b = find_candidates(scan_document(split_lines(r.text)).annotation)
    assert a == b
