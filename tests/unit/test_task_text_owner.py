"""M4-REASSERT: the AC-01.19 assertions of ``test_task_text.py`` re-run with the REAL owner-phrase provider.

``test_task_text.py`` used a stub owner locator; these tests assert the same outcomes (plus more) with
``OwnerPhraseProvider`` (T-M4-03/04), so the stub-based expectations are confirmed against real spans.
"""

from __future__ import annotations

import unicodedata

import pytest

from actiontrack.parsing.items import (
    compute_task_text,
    derive_task_text,
    extract_items,
    make_task_text_fn,
)
from actiontrack.parsing.markdown import scan_document
from actiontrack.parsing.owner_phrase import OwnerPhraseProvider, extract_owner_phrase
from actiontrack.parsing.reader import split_lines

REAL = OwnerPhraseProvider()


@pytest.mark.ac("AC-01.19")
def test_ac_01_19_description_and_task_text() -> None:
    desc = "Priya to send the revised quote by Friday."
    assert compute_task_text(desc, REAL) == "Send the revised quote"


@pytest.mark.ac("AC-01.19")
def test_ac_01_19_item_has_three_different_fields() -> None:
    line = "- [ ] Priya to send the revised quote by Friday."
    scan = scan_document(split_lines(line), "n.md")
    (item,) = extract_items(scan, "n.md", make_task_text_fn(REAL)).items
    assert item.raw_text == line
    assert item.description == "Priya to send the revised quote by Friday."
    assert item.task_text == "Send the revised quote"


@pytest.mark.ac("AC-01.19")
def test_ac_01_19_co_owner() -> None:
    desc = "Lena (with Priya) to update the plan"
    phrase = extract_owner_phrase(desc)
    assert phrase is not None
    assert derive_task_text(desc, [phrase.clause_span]) == "Update the plan"
    assert compute_task_text(desc, REAL) == "Update the plan"
    assert "Priya" not in compute_task_text(desc, REAL)


@pytest.mark.ac("AC-01.19")
def test_ac_01_19_raw_text_decomposed_description_nfc() -> None:
    line = "- [ ] Zoë to send the quote"  # decomposed e + U+0308
    scan = scan_document(split_lines(line), "n.md")
    (item,) = extract_items(scan, "n.md", make_task_text_fn(REAL)).items
    assert item.raw_text.encode() == line.encode()
    assert item.description == "Zoë to send the quote"
    assert unicodedata.is_normalized("NFC", item.task_text)
    assert item.task_text == "Send the quote"
    assert item.task_text.startswith("Send")


def test_not_due_phrase_removed_with_real_owner() -> None:
    desc = "Karan to freeze invoice edits from 9 Oct"
    text = compute_task_text(desc, REAL)
    assert text == "Freeze invoice edits"
    assert len(text.split()) == 3


def test_separator_before_due_phrase_removed_with_real_owner() -> None:
    desc = "Priya N. to send revised quote to Acme — by Friday"
    assert compute_task_text(desc, REAL) == "Send revised quote to Acme"


def test_with_phrase_and_due_together() -> None:
    desc = "Lena (with Priya) to collect user feedback on the prototype by 2026-09-25"
    assert compute_task_text(desc, REAL) == "Collect user feedback on the prototype"


def test_multi_owner_and_at_name_clauses_removed() -> None:
    assert compute_task_text("Anita & Rahul to agree on the component API by Wednesday", REAL) == (
        "Agree on the component API"
    )
    assert compute_task_text("@anita: audit colour contrast on the dashboard by Mon", REAL) == (
        "Audit colour contrast on the dashboard"
    )


def test_possessive_stays_in_task_text() -> None:
    assert compute_task_text("Rahul to review Anita's PR by tomorrow", REAL) == "Review Anita's PR"


def test_non_person_clause_removed() -> None:
    assert compute_task_text("Everyone to submit their Q4 goals by 7 Oct", REAL) == "Submit their Q4 goals"


def test_no_owner_text_unchanged_apart_from_due() -> None:
    assert compute_task_text("Send the quote by Friday", REAL) == "Send the quote"
    assert compute_task_text("Book the venue", REAL) == "Book the venue"
