"""T-M5-06: description core, token splitting, recurrence words, Jaccard (pure, spec 8.3 / 10.6)."""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

import pytest

from actiontrack.domain import matching, rules
from actiontrack.domain.matching import (
    CoreResult,
    core_equal,
    core_key,
    core_tokens,
    description_core,
    has_recurrence_word,
    jaccard,
)
from actiontrack.parsing import parse_document
from actiontrack.parsing.owner_phrase import OwnerAnalyzer
from tests.conftest import FIXTURES_NOTES


# --------------------------------------------------------------------------- token splitting (8.3)
@pytest.mark.ac("AC-07.1")
@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("don't", ("dont",)),
        ("Anita's CTO", ("anitas", "cto")),
        ("Anita’s CTO", ("anitas", "cto")),  # curly apostrophe folded then deleted
        ("go/no-go", ("go", "no", "go")),
        ("10%", ("10",)),
        ("API-review", ("api", "review")),
        ("welcome-email copy", ("welcome", "email", "copy")),
        ("Freeze invoice edits", ("freeze", "invoice", "edits")),
        ("Café  —  résumé", ("café", "résumé")),
        ("the a an to and of for please", ()),
        ("", ()),
        (None, ()),
        ("!!! ...", ()),
    ],
)
def test_token_splitting_table(text, tokens):
    assert core_tokens(text) == tokens


def test_stop_words_only_removed_from_tokens_not_core_string():
    assert description_core("Send the revised quote to Acme") == "send the revised quote to acme"
    assert core_tokens("Send the revised quote to Acme") == ("send", "revised", "quote", "acme")


def test_core_string_normalisation():
    assert description_core('  “Ship” it…  ') == 'ship" it'
    assert description_core("Fix \U0001f525 the bug ✅ —") == "fix the bug"
    assert description_core("ﬁle ①–x.") == "file 1-x"  # NFKC: ligature, circled digit
    assert description_core("STRASSE Straße") == "strasse strasse"  # casefold, not lower
    assert description_core("-- Do  this\t\nnow: ") == "do this now"
    once = description_core("Anita’s ‘X’!")
    assert description_core(once) == once


def test_core_key_result_and_fallback():
    r = core_key("Send the revised quote to Acme")
    assert r == CoreResult(("send", "revised", "quote", "acme"), "send revised quote acme")
    # task_text with no tokens falls back to the description (documented 8.2 edge)
    assert core_key("", "Priya to ship it").key == "priya ship it"
    assert core_key("the to", None) == CoreResult((), "")
    assert core_key(None) == CoreResult((), "")


def test_decomposed_input_matches_composed():
    assert core_tokens("Café menu") == core_tokens("Café menu") == ("café", "menu")


def test_module_is_pure():
    assert not hasattr(matching, "sqlite3") and not hasattr(matching, "datetime")


# --------------------------------------------------------------------------- recurrence (AC-07.7)
@pytest.mark.ac("AC-07.7")
def test_recurrence_words():
    assert has_recurrence_word(core_tokens("Send weekly status report"))
    for w in rules.RECURRENCE_WORDS:
        assert has_recurrence_word((w,))
    assert rules.RECURRENCE_WORDS == frozenset(
        "weekly daily monthly every each recurring standing regular".split()
    )
    assert not has_recurrence_word(core_tokens("Review pricing proposal"))
    assert not has_recurrence_word(())
    assert not has_recurrence_word(("weekdays", "everything"))  # whole tokens only


# --------------------------------------------------------------------------- equality / jaccard
@pytest.mark.ac("AC-07.1")
def test_core_equal_is_sequence_not_set():
    assert core_equal(("a", "b"), ["a", "b"])
    assert not core_equal(("a", "b"), ("b", "a"))
    assert not core_equal(("a", "b"), ("a", "b", "b"))


@pytest.mark.ac("AC-07.10")
def test_jaccard_values():
    assert jaccard((), ()) == 0.0
    assert jaccard(("a",), ()) == 0.0
    assert jaccard(("a", "b"), ("b", "a")) == 1.0
    assert jaccard(("a", "a", "b"), ("a", "b")) == 1.0  # sets, not multisets
    assert jaccard(core_tokens("Share first wireframes"), core_tokens("Share updated wireframes")) == 0.5
    assert (
        jaccard(core_tokens("Review pricing proposal"), core_tokens("Review pricing model"))
        < rules.FUZZY_JACCARD_THRESHOLD
    )
    assert (
        jaccard(
            core_tokens("Ask Acme for their procurement contact"),
            core_tokens("Send Acme the procurement form"),
        )
        < 0.3
    )
    assert rules.FUZZY_JACCARD_THRESHOLD == 0.85 and rules.MIN_CORE_TOKENS == 3


# --------------------------------------------------------------------------- real parsed samples
@lru_cache(maxsize=1)
def _sample_items():
    out = []
    for p in sorted(FIXTURES_NOTES.rglob("*.md")):
        rel = p.relative_to(FIXTURES_NOTES).as_posix()
        pf = parse_document(p.read_bytes(), rel, analyzer=OwnerAnalyzer())
        for it in pf.items:
            out.append((p.name, it.line_start, it.checkbox_state, it))
    return out


def _item(name: str, line: int):
    for n, ln, _, it in _sample_items():
        if n == name and ln == line:
            return it
    raise AssertionError(f"{name}:{line} not found")


@pytest.mark.ac("AC-07.1")
def test_revised_quote_variants_identical_after_owner_due_removal():
    members = [
        ("2026-09-01-quote-portal-kickoff.md", 9),
        ("2026-09-08-quote-portal-weekly.md", 9),
        ("2026-09-15-quote-portal-weekly.md", 9),
    ]
    results = [core_key(_item(n, ln).task_text, _item(n, ln).description) for n, ln in members]
    assert all(r.key == "send revised quote acme" and len(r.tokens) == 4 for r in results)
    assert core_equal(results[0].tokens, results[1].tokens)
    assert core_equal(results[1].tokens, results[2].tokens)


@pytest.mark.ac("AC-07.12")
def test_freeze_invoice_edits_exactly_three_tokens_in_both_files():
    # Fragile on purpose: the 3-token floor (10.6 condition 2) only holds because `from 9 Oct` is
    # removed from task_text (8.3's own example). A 2-token core here would silently disable the merge.
    a = core_key(_item("2026-09-29-billing-cutover-planning.md", 12).task_text)
    b = core_key(_item("2026-10-08-billing-go-no-go.md", 11).task_text)
    assert a == b == CoreResult(("freeze", "invoice", "edits"), "freeze invoice edits")
    assert len(a.tokens) >= rules.MIN_CORE_TOKENS


def test_wireframe_pair_from_sample():
    texts = [it.task_text for _, _, _, it in _sample_items()]
    first = [t for t in texts if t.lower().startswith("share first wireframes")]
    updated = [t for t in texts if t.lower().startswith("share updated wireframes")]
    assert first and len(updated) == 2
    f, u = core_tokens(first[0]), core_tokens(updated[0])
    assert jaccard(f, u) == 0.5 < 0.85
    assert core_equal(core_tokens(updated[0]), core_tokens(updated[1]))


def test_customer_quotes_due_forms_do_not_affect_core():
    keys = [
        core_key(it.task_text).key
        for _, _, _, it in _sample_items()
        if it.task_text.startswith("Collect 5 customer quotes")
    ]
    assert keys == ["collect 5 customer quotes landing page"] * 2


def test_percent_trap_synthetic():
    # `10%` is not a due phrase (AC-06.7 trap): only the real date is removed from task_text.
    pf = parse_document(
        "# Meeting: X\nDate: 2026-10-01\nAttendees: Sam Lee\n\n## Action items\n"
        "- [ ] Sam to cut cloud costs by 10% — proposal by 2026-10-09\n".encode(),
        "x.md",
        analyzer=OwnerAnalyzer(),
    )
    (item,) = pf.items
    assert core_tokens(item.task_text) == ("cut", "cloud", "costs", "by", "10", "proposal")


def test_no_owner_item_removes_nothing_else():
    pf = parse_document(
        b"# Meeting: X\nDate: 2026-10-01\n\n## Action items\n- [ ] Review pricing proposal\n",
        "x.md",
        analyzer=OwnerAnalyzer(),
    )
    assert core_tokens(pf.items[0].task_text) == ("review", "pricing", "proposal")


# Expected identical-core clusters of Appendix A.4 (18): key -> member count
_CLUSTERS = {
    "send revised quote acme": 3,
    "confirm cloud budget with finance": 3,
    "set up staging environment": 2,
    "export last quarter invoices": 2,
    "finalise welcome email copy": 3,
    "write acceptance tests signup flow": 2,
    "book renewal call with acmes cto": 3,
    "send acme procurement form": 2,
    "deploy beta staging": 3,
    "approve q4 cloud spend": 2,
    "notify customers about maintenance window": 2,
    "freeze invoice edits": 2,
    "share updated wireframes": 2,
    "fix pagination bug": 2,
    "review schema mapping doc": 2,
    "estimate sso work": 2,
    "prepare renewal one pager": 2,
    "collect 5 customer quotes landing page": 2,
}


@pytest.mark.ac("AC-07.1", "AC-07.10")
def test_sample_sweep_clusters_match_appendix_a4():
    items = _sample_items()
    assert len(items) == 117
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for name, line, _state, it in items:
        r = core_key(it.task_text, it.description)
        assert r.tokens, f"empty core at {name}:{line}"
        groups[r.key].append((name, line))
    multi = {k: len(v) for k, v in groups.items() if len(v) > 1}
    assert multi == _CLUSTERS
    assert not any(has_recurrence_word(k.split()) for k in groups)
    assert all(len(k.split()) >= rules.MIN_CORE_TOKENS for k in multi)
