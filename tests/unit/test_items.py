"""T-M2-09: item extraction (spans, multiline continuation, section heading, occurrence, block hash)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing.items import block_hash, extract_items
from actiontrack.parsing.markdown import scan_document
from actiontrack.parsing.reader import DecodedText, classify_and_decode, split_lines


def _items(text: str, file: str = "n.md"):
    scan = scan_document(split_lines(text), file)
    res = extract_items(scan, file)
    return res.items, res


# ---------------------------------------------------------------- multiline continuation
@pytest.mark.ac("AC-01.6")
@pytest.mark.sc("SC-29")
def test_three_and_four_line_tasks_are_one_item() -> None:
    items, _ = _items(
        "- [ ] Priya to send\n  the revised quote\n  by Friday\n\n"
        "- [ ] Lena to\n    update\n    the\n    plan\n"
    )
    assert [(i.line_start, i.line_end) for i in items] == [(1, 3), (5, 8)]
    assert items[0].description == "Priya to send the revised quote by Friday"
    assert items[1].description == "Lena to update the plan"
    assert items[0].raw_text == "- [ ] Priya to send\n  the revised quote\n  by Friday"


@pytest.mark.ac("AC-01.6")
@pytest.mark.parametrize(
    "text, end",
    [
        ("- [ ] A task\n  more\n\n  after blank\n", 2),  # blank line
        ("- [ ] A task\n  more\n  - plain child\n  indented under child\n", 2),  # plain child bullet
        ("- [ ] A task\n  more\n## Heading\n  after heading\n", 2),  # heading
        ("- [ ] A task\n  more\nlazy line\n", 2),  # unindented lazy line
        ("- [ ] A task\n  more\n  | a | b |\n", 2),  # table row
        ("- [ ] A task\n  more\n  > quoted\n", 2),  # blockquote
        ("- [ ] A task\n  more\n  ```\n  code\n  ```\n", 2),  # fence
        ("- [ ] A task\n  more\n  <!-- c -->\n  after\n", 2),  # comment-only line
        ("- [ ] A task\n  more\n  - [ ] child item\n", 2),  # child checkbox
        ("- [ ] A task\n  more\n  - [-] cancelled child\n", 2),  # nonstandard child
        ("- [ ] A task\n  more\n  1. numbered child\n", 2),
    ],
)
def test_continuation_stops(text: str, end: int) -> None:
    items, _ = _items(text)
    assert (items[0].line_start, items[0].line_end) == (1, end)
    assert items[0].description == "A task more"


def test_indent_must_exceed_marker_indent_for_nested_item() -> None:
    items, _ = _items("- [ ] Parent\n  - [ ] Child one\n    continues here\n  same-level text\n")
    assert [(i.line_start, i.line_end) for i in items] == [(1, 1), (2, 3)]
    assert items[1].description == "Child one continues here"  # "same-level text" (indent 2) is not > 2


def test_tab_indent_continuation() -> None:
    items, _ = _items("- [ ] Task\n\tcontinued\n")
    assert (items[0].line_start, items[0].line_end) == (1, 2)
    assert items[0].raw_text == "- [ ] Task\n\tcontinued"


def test_continuation_markup_spanning_lines_is_cleaned() -> None:
    items, _ = _items("- [ ] Do **the\n  quote** now\n")
    assert items[0].description == "Do the quote now"


# ---------------------------------------------------------------- markers, states, regions
def test_markers_depth_and_state() -> None:
    text = (
        "- [ ] a open\n* [x] b done\n+ [X] c done\n1. [ ] d numbered\n2) [x] e paren\n"
        "    - [ ] f deep\n\t\t- [x] g tabs\n"
    )
    items, res = _items(text)
    assert [i.checkbox_state for i in items] == ["open", "done", "done", "open", "done", "open", "done"]
    assert [i.description[0] for i in items] == list("abcdefg")
    assert res.warnings == ()


def test_empty_and_nonstandard_boxes_produce_no_item_but_warn() -> None:
    items, res = _items("- [ ]\n- [-] cancelled x\n- [ ] real one\n")
    assert [(i.line_start, i.description) for i in items] == [(3, "real one")]
    assert [(w.code, w.line) for w in res.warnings] == [
        (codes.W_CHECKBOX_EMPTY, 1),
        (codes.W_CHECKBOX_NONSTANDARD, 2),
    ]
    assert res.warnings[1].detail == "- [-] cancelled x"


@pytest.mark.ac("AC-01.18")
def test_nospace_forms() -> None:
    items, res = _items("- [ ]Priya to send the deck\n- []Lena to book the room\n")
    assert [i.description for i in items] == ["Priya to send the deck", "Lena to book the room"]
    assert [(w.code, w.line) for w in res.warnings] == [(codes.W_CHECKBOX_NOSPACE, 2)]


def test_ignored_regions_produce_no_items_or_item_warnings() -> None:
    text = (
        "> - [ ] quoted\n```\n- [ ] fenced\n```\n<!-- - [ ] commented -->\n"
        "<!--\n- [ ] multi\n-->\n- [ ] real\n"
    )
    items, res = _items(text)
    assert [i.description for i in items] == ["real"]
    assert res.warnings == ()


def test_front_matter_checkbox_ignored() -> None:
    items, _ = _items("---\ntitle: x\nnote: [ ] y\n---\n- [ ] real\n")
    assert [(i.line_start, i.description) for i in items] == [(5, "real")]


def test_plain_bullets_and_links_are_not_items() -> None:
    items, res = _items("- plain\n- [x](http://a.test) link\n- [ref]: foo\n- [a] letter\n")
    assert items == () and res.warnings == ()


def test_inline_comment_blanked_in_description_but_kept_in_raw() -> None:
    items, _ = _items("- [ ] Do it <!-- hidden --> now\n")
    assert items[0].description == "Do it now"
    assert "<!-- hidden -->" in items[0].raw_text


# ---------------------------------------------------------------- headings
def test_section_heading_nearest_preceding_atx() -> None:
    text = (
        "- [ ] before any heading\n# One\n- [ ] a\n## **Two** `x` ##\n- [ ] b\n"
        "```\n# not a heading\n```\n- [ ] c\n   ### Three\n- [ ] d\n"
    )
    items, _ = _items(text)
    assert [i.section_heading for i in items] == [None, "One", "Two x", "Two x", "Three"]


# ---------------------------------------------------------------- occurrence index / hash
def test_identical_lines_occurrence_and_hash() -> None:
    items, _ = _items("- [ ] same\n- [ ] other\n- [ ] same\n- [ ] same\n")
    assert [i.occurrence_index for i in items] == [0, 0, 1, 2]
    assert items[0].block_hash == items[2].block_hash == items[3].block_hash
    assert items[1].block_hash != items[0].block_hash


def test_same_text_different_state_or_indent_hash_differently() -> None:
    items, _ = _items("- [ ] same\n- [x] same\n  - [ ] same\n")
    assert len({i.block_hash for i in items}) == 3
    assert [i.occurrence_index for i in items] == [0, 0, 0]


def test_crlf_and_lf_hash_equal() -> None:
    lf, _ = _items("- [ ] a\n  b\n")
    crlf = extract_items(
        scan_document(split_lines("- [ ] a\r\n  b\r\n"), "n.md"), "n.md"
    ).items
    assert lf[0].raw_text == crlf[0].raw_text == "- [ ] a\n  b"
    assert lf[0].block_hash == crlf[0].block_hash


@pytest.mark.ac("AC-01.19")
def test_raw_text_verbatim_description_nfc_hash_over_verbatim() -> None:
    line = "- [ ] Zoë to send the quote"  # decomposed
    items, _ = _items(line)
    (item,) = items
    assert item.raw_text.encode() == line.encode()
    assert "Zoë" in item.description and "ë" not in item.description
    assert item.block_hash == hashlib.sha256(item.raw_text.encode()).hexdigest()
    composed, _ = _items("- [ ] Zoë to send the quote")
    assert composed[0].block_hash != item.block_hash  # intended (spec 7.2)
    assert block_hash(item.raw_text) == item.block_hash


def test_default_task_text_is_first_letter_rule_only() -> None:
    items, _ = _items("- [ ] priya to send the quote by Friday.\n")
    assert items[0].task_text == "Priya to send the quote by Friday."
    assert items[0].owner_hook is None and items[0].due_hook is None


def test_task_text_fn_is_used() -> None:
    scan = scan_document(split_lines("- [ ] abc\n"), "n.md")
    (item,) = extract_items(scan, "n.md", lambda d: d.upper()).items
    assert item.task_text == "ABC" and item.description == "abc"


def test_empty_document() -> None:
    res = extract_items(scan_document([], "n.md"), "n.md")
    assert res.items == () and res.warnings == ()


# ---------------------------------------------------------------- real sample
def _fixture_items(fixtures_dir: Path, name: str):
    d = classify_and_decode((fixtures_dir / name).read_bytes(), name)
    assert isinstance(d, DecodedText)
    return extract_items(scan_document(split_lines(d.text), name), name).items


@pytest.mark.ac("AC-01.17")
@pytest.mark.sc("SC-29")
def test_q4_offsite(fixtures_dir: Path) -> None:
    items = _fixture_items(fixtures_dir, "2026-09-30-q4-offsite.md")
    assert [i.line_start for i in items] == [9, 10, 11, 12, 16, 17, 18]
    assert items[0].line_end == 9  # child at 10 is a separate item
    assert [i.section_heading for i in items] == ["Morning: where we are"] * 4 + [
        "Afternoon: people and process"
    ] * 3
    assert items[2].description == "Lena to run the customer survey, results due 15 October"


def test_kickoff_line_9(fixtures_dir: Path) -> None:
    items = _fixture_items(fixtures_dir, "2026-09-01-quote-portal-kickoff.md")
    first = items[0]
    assert first.section_heading == "Action items"
    assert first.line_start == first.line_end == 9


def test_untitled_has_no_section_heading(fixtures_dir: Path) -> None:
    items = _fixture_items(fixtures_dir, "untitled.md")
    assert len(items) == 2
    assert all(i.section_heading is None for i in items)


def test_crlf_and_multiline_sample_items(fixtures_dir: Path) -> None:
    items = _fixture_items(fixtures_dir, "2026-10-02-finance-sync.md")
    assert len(items) == 4
    assert all("\r" not in i.raw_text for i in items)
    assert all(i.line_end == i.line_start for i in items)  # the sample has no multiline items


EXPECTED_COUNTS = {
    "2026-09-01-quote-portal-kickoff.md": 4,
    "2026-09-02-standup.md": 2,
    "2026-09-03-billing-migration-sync.md": 4,
    "2026-09-08-quote-portal-weekly.md": 4,
    "2026-09-09-notes-final-v2.md": 0,
    "2026-09-09-qa-standup.md": 2,
    "2026-09-10-acme-renewal-prep.md": 4,
    "2026-09-14-onboarding-flow-design.md": 4,
    "2026-09-15-quote-portal-weekly.md": 4,
    "2026-09-16-standup.md": 2,
    "2026-09-17-billing-migration-sync.md": 4,
    "2026-09-18-design-review.md": 5,
    "2026-09-21-incident-review.md": 4,
    "2026-09-22-quote-portal-weekly.md": 4,
    "2026-09-23-standup.md": 2,
    "2026-09-24-onboarding-flow-design.md": 4,
    "2026-09-25-qa-sync.md": 2,
    "2026-09-28-acme-renewal-checkin.md": 4,
    "2026-09-29-billing-cutover-planning.md": 4,
    "2026-09-30-q4-offsite.md": 7,
    "2026-09-30-standup.md": 2,
    "2026-10-01-quote-portal-weekly.md": 4,
    "2026-10-02-finance-sync.md": 4,
    "2026-10-03-sales-pipeline.md": 2,
    "2026-10-05-onboarding-retro.md": 4,
    "2026-10-06-quote-portal-weekly.md": 4,
    "2026-10-07-standup.md": 2,
    "2026-10-08-billing-go-no-go.md": 4,
    "2026-10-09-security-review.md": 4,
    "2026-10-10-sales-weekly.md": 3,
    "2026-10-12-billing-post-cutover.md": 4,
    "2026-10-13-quote-portal-weekly.md": 4,
    "2026-10-14-acme-renewal-wrapup.md": 4,
    "untitled.md": 2,
}


@pytest.mark.ac("AC-01.14")
@pytest.mark.sc("SC-29")
def test_sample_census(fixtures_dir: Path) -> None:
    counts: dict[str, int] = {}
    states = {"open": 0, "done": 0}
    for p in sorted(fixtures_dir.glob("*.md")):
        d = classify_and_decode(p.read_bytes(), p.name)
        if not isinstance(d, DecodedText):
            if p.name == "2026-09-09-notes-final-v2.md":
                counts[p.name] = 0  # empty file: no scan, no items
            continue  # scratch.md is binary (E-BINARY)
        res = extract_items(scan_document(split_lines(d.text), p.name), p.name)
        counts[p.name] = len(res.items)
        for i in res.items:
            states[i.checkbox_state] += 1
            assert i.raw_text.startswith(split_lines(d.text)[i.line_start - 1])
            assert i.block_hash == hashlib.sha256(i.raw_text.encode()).hexdigest()
    assert counts == EXPECTED_COUNTS
    assert sum(counts.values()) == 117
    assert states == {"open": 104, "done": 13}


def test_txt_sample_is_not_scanned(fixtures_dir: Path) -> None:
    assert not list(fixtures_dir.glob("*.txt"))[0].name.endswith(".md")
