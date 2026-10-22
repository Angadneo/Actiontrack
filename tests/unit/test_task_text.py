"""T-M2-19: ``task_text`` derivation and the description / raw_text contract (AC-01.19, G-12, J-01).

Owner spans come from a *stub* (``StubOwnerSpans``): M4's real owner-phrase locator does not exist
yet.  Tests marked ``M4-REASSERT`` MUST be re-run by the M4 owner-phrase task with the real locator
(cross-part item, integrator note B-17).  Due spans always come from the real ``dates.locate``.
"""

from __future__ import annotations

import ast
import inspect
import unicodedata

import pytest

from actiontrack.domain.models import TextSpan
from actiontrack.parsing import items as items_mod
from actiontrack.parsing.items import (
    NullOwnerSpans,
    collect_spans,
    compute_task_text,
    derive_task_text,
    due_spans,
    extract_items,
    make_task_text_fn,
)
from actiontrack.parsing.markdown import scan_document
from actiontrack.parsing.reader import split_lines


class StubOwnerSpans:
    """Owner-span provider for tests: covers ``prefix`` when the description starts with it."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def owner_spans(self, description: str) -> list[TextSpan]:
        if description.startswith(self.prefix):
            return [TextSpan(0, len(self.prefix))]
        return []


def _span(text: str, part: str) -> TextSpan:
    i = text.index(part)
    return TextSpan(i, i + len(part))


# ---------------------------------------------------------------- AC-01.19 (three clauses)
@pytest.mark.ac("AC-01.19")
def test_ac_01_19_description_and_task_text() -> None:  # M4-REASSERT (owner span is a stub)
    desc = "Priya to send the revised quote by Friday."
    assert compute_task_text(desc, StubOwnerSpans("Priya to ")) == "Send the revised quote"


@pytest.mark.ac("AC-01.19")
def test_ac_01_19_item_has_three_different_fields() -> None:  # M4-REASSERT
    line = "- [ ] Priya to send the revised quote by Friday."
    scan = scan_document(split_lines(line), "n.md")
    (item,) = extract_items(scan, "n.md", make_task_text_fn(StubOwnerSpans("Priya to "))).items
    assert item.raw_text == line
    assert item.description == "Priya to send the revised quote by Friday."
    assert item.task_text == "Send the revised quote"


@pytest.mark.ac("AC-01.19")
def test_ac_01_19_co_owner() -> None:  # M4-REASSERT: re-run with the real owner-phrase locator
    desc = "Lena (with Priya) to update the plan"
    assert derive_task_text(desc, [TextSpan(0, len("Lena (with Priya) to "))]) == "Update the plan"
    assert "Priya" not in compute_task_text(desc, StubOwnerSpans("Lena (with Priya) to "))


@pytest.mark.ac("AC-01.19")
def test_ac_01_19_raw_text_decomposed_description_nfc() -> None:  # M4-REASSERT
    line = "- [ ] Zoë to send the quote"  # decomposed e + U+0308
    scan = scan_document(split_lines(line), "n.md")
    (item,) = extract_items(scan, "n.md", make_task_text_fn(StubOwnerSpans("Zoë to "))).items
    assert item.raw_text.encode() == line.encode()
    assert item.description == "Zoë to send the quote"
    assert unicodedata.is_normalized("NFC", item.task_text)
    assert item.task_text == "Send the quote"
    assert item.task_text.startswith("Send")


# ---------------------------------------------------------------- phrase removal
def test_not_due_phrase_removed() -> None:
    desc = "Karan to freeze invoice edits from 9 Oct"
    text = compute_task_text(desc, StubOwnerSpans("Karan to "))
    assert text == "Freeze invoice edits"
    assert len(text.split()) == 3


def test_separator_before_due_phrase_removed() -> None:
    desc = "Priya N. to send revised quote to Acme — by Friday"
    assert compute_task_text(desc, StubOwnerSpans("Priya N. to ")) == "Send revised quote to Acme"


def test_mid_sentence_due_with_stubbed_wide_span() -> None:
    desc = "Lena to pull the usage report, results due 15 October"
    spans = [TextSpan(0, 8), _span(desc, "results due 15 October")]
    assert derive_task_text(desc, spans) == "Pull the usage report"


def test_mid_sentence_due_real_locator() -> None:
    desc = "Lena to pull the usage report, due 15 October"
    assert compute_task_text(desc, StubOwnerSpans("Lena to ")) == "Pull the usage report"


def test_time_word_removed_with_phrase() -> None:
    desc = "Sam to book the venue by EOD tomorrow"
    assert compute_task_text(desc, StubOwnerSpans("Sam to ")) == "Book the venue"


@pytest.mark.parametrize(
    "desc, expected",
    [
        ("Send the plan (due: Oct 20)", "Send the plan"),  # orphan ")" cleaned
        ("Send the plan [due: Oct 20]", "Send the plan"),  # orphan "]" cleaned
        ("Send the plan (by Friday) now", "Send the plan now"),  # empty brackets removed
        ("Send the plan (see notes) by Friday", "Send the plan (see notes)"),  # balanced kept
        ("due: 2026-10-20 — send the plan", "Send the plan"),  # leading separator
    ],
)
def test_bracket_and_leading_cleanup(desc: str, expected: str) -> None:
    assert compute_task_text(desc) == expected


def test_both_sentence_punctuations() -> None:
    assert derive_task_text("Send it by Friday;", due_spans("Send it by Friday;")) == "Send it"
    # a "." ending a task with nothing removed next to it is kept (spec 8.2)
    assert derive_task_text("Send it.", ()) == "Send it."
    # ... and so is a sentence boundary in the middle
    assert compute_task_text("Send it by Friday. Then call Bob") == "Send it. Then call Bob"


def test_two_due_spans_removed_and_doubled_separator_collapses() -> None:
    desc = "Fix A, by Friday, then B"
    assert compute_task_text(desc) == "Fix A, then B"
    assert compute_task_text("Do X by Friday and Y by Monday.") == "Do X and Y"


# ---------------------------------------------------------------- span mechanics
def test_overlapping_spans_merge() -> None:
    desc = "Priya to send the quote"
    assert derive_task_text(desc, [TextSpan(0, 6), TextSpan(3, 9)]) == "Send the quote"


def test_span_at_start_and_end() -> None:
    desc = "Priya to send the quote by Friday"
    spans = [TextSpan(0, 9), _span(desc, "by Friday")]
    assert derive_task_text(desc, spans) == "Send the quote"


def test_out_of_range_and_empty_spans_are_ignored() -> None:
    assert derive_task_text("abc def", [TextSpan(5, 99), TextSpan(2, 2), TextSpan(-4, 0)]) == "Abc d"
    assert derive_task_text("abc def", [TextSpan(2, 2)]) == "Abc def"


def test_no_spans_only_first_character_uppercased() -> None:
    assert derive_task_text("über task", ()) == "Über task"
    assert derive_task_text("send iPhone SDK", ()) == "Send iPhone SDK"
    assert derive_task_text("über task") == "Über task"


def test_empty_result_falls_back_to_description() -> None:
    desc = "TBD by Friday"
    spans = [TextSpan(0, 3), *due_spans(desc)]
    assert derive_task_text(desc, spans) == desc
    assert derive_task_text("", ()) == ""
    assert derive_task_text("by Friday", due_spans("by Friday")) == "by Friday"


def test_result_is_nfc() -> None:
    assert unicodedata.is_normalized("NFC", derive_task_text("zoë sends it", ()))


# ---------------------------------------------------------------- glue / purity
def test_null_owner_spans_default() -> None:
    assert NullOwnerSpans().owner_spans("anything") == ()
    assert collect_spans("Send it by Friday") == due_spans("Send it by Friday")


def test_deterministic() -> None:
    desc = "Priya to send the revised quote by Friday."
    owner = StubOwnerSpans("Priya to ")
    assert compute_task_text(desc, owner) == compute_task_text(desc, owner)
    spans = collect_spans(desc, owner)
    assert derive_task_text(desc, spans) == derive_task_text(desc, spans)


def test_pure_no_io_or_clock_imports() -> None:
    tree = ast.parse(inspect.getsource(items_mod))
    imported = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    } | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"os", "pathlib", "time", "datetime", "sqlite3", "socket", "subprocess"}


def test_long_separator_run_is_linear() -> None:
    desc = "a " + "-" * 200_000 + " by Friday"
    assert compute_task_text(desc) == "A"


# ---------------------------------------------------------------- sample invariant (T-M2-17 half)
def test_task_text_non_empty_for_every_sample_item(fixtures_dir) -> None:
    from actiontrack.parsing.reader import DecodedText, classify_and_decode

    count = 0
    for p in sorted(fixtures_dir.glob("*.md")):
        d = classify_and_decode(p.read_bytes(), p.name)
        if not isinstance(d, DecodedText):
            continue
        scan = scan_document(split_lines(d.text), p.name)
        for item in extract_items(scan, p.name, make_task_text_fn()).items:
            count += 1
            assert item.task_text.strip(), (p.name, item.line_start)
            assert item.task_text[0] == item.task_text[0].upper()
    assert count == 117
