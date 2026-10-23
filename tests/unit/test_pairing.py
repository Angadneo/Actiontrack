"""T-M5-12: pure changed-file pairing (spec 10.7.2 a-c, G-24)."""

from __future__ import annotations

import pytest

from actiontrack.domain.matching import NewEntry, OldSource, pair_entries, pair_sources


def old(i, desc, *, h=None, occ=0, sec="S", line=None, present=True):
    return OldSource(i, h or f"h-{desc}", occ, desc, sec, line or i, present)


def new(i, desc, *, h=None, occ=0, sec="S"):
    return NewEntry(i, h or f"h-{desc}", occ, desc, sec, i + 1)


def rules(res):
    return {p.new_index: (p.old_id, p.rule) for p in res.pairs}


def test_alias_and_tuple_shape():
    assert pair_entries is pair_sources
    pairs, new_only, old_only = pair_sources([], [new(0, "x")])
    assert pairs == () and new_only == (0,) and old_only == ()


@pytest.mark.ac("AC-08.6")
def test_same_hash_is_paired_even_when_the_line_moved():
    res = pair_sources([old(1, "fix pagination bug", line=4)], [new(0, "fix pagination bug")])
    assert rules(res) == {0: (1, "hash")} and not res.new_only and not res.old_only


def test_same_description_different_raw_text_is_a_move():
    res = pair_sources([old(1, "ship it", h="A")], [new(0, "ship it", h="B")])
    assert rules(res) == {0: (1, "moved")}


@pytest.mark.ac("AC-08.2")
def test_edit_in_place_needs_same_section_and_jaccard():
    o = [old(1, "confirm cloud budget with finance", h="A")]
    res = pair_sources(o, [new(0, "confirm the cloud budget with finance team", h="B")])
    assert rules(res) == {0: (1, "edited")} and res.pairs[0].score >= 0.6
    other = pair_sources(o, [new(0, "confirm the cloud budget with finance team", h="B", sec="Other")])
    assert other.pairs == () and other.new_only == (0,) and other.old_only == (1,)
    far = pair_sources(o, [new(0, "completely different words here", h="B")])
    assert far.pairs == ()


def test_jaccard_boundary_is_inclusive():
    # 3 shared tokens of 5 -> exactly 0.6
    res = pair_sources([old(1, "alpha beta gamma delta", h="A")], [new(0, "alpha beta gamma epsilon", h="B")])
    assert res.pairs and res.pairs[0].score == pytest.approx(0.6)
    res = pair_sources([old(1, "alpha beta gamma delta", h="A")], [new(0, "alpha beta epsilon zeta", h="B")])
    assert res.pairs == ()


@pytest.mark.ac("AC-08.10")
def test_absent_row_is_restored_by_hash_and_by_edit():
    res = pair_sources([old(1, "write the report", present=False)], [new(0, "write the report")])
    assert rules(res) == {0: (1, "hash")}
    res = pair_sources(
        [old(1, "write the quarterly report now", h="A", present=False)],
        [new(0, "write the quarterly report", h="B")],
    )
    assert rules(res) == {0: (1, "edited")}


def test_present_row_is_not_stolen_by_a_restored_one():
    """Identical line kept (id 1, occurrence 0) and an identical line removed+restored (id 2, occurrence 1)."""
    olds = [old(1, "same line", h="H", occ=0, line=3), old(2, "same line", h="H", occ=1, line=6, present=False)]
    news = [new(0, "same line", h="H", occ=0), new(1, "same line", h="H", occ=1)]
    assert rules(pair_sources(olds, news)) == {0: (1, "hash"), 1: (2, "hash")}
    # one identical line left: the present row wins, the absent one stays unpaired
    res = pair_sources(olds, [new(0, "same line", h="H", occ=1)])
    assert rules(res) == {0: (2, "hash")}  # same occurrence index -> rule (a) is exact
    res = pair_sources(
        [old(1, "same line", h="H1", line=3), old(2, "same line", h="H2", line=6, present=False)],
        [new(0, "same line", h="H3")],
    )
    assert rules(res) == {0: (1, "moved")} and res.old_only == (2,)


def test_greedy_highest_score_then_document_order():
    olds = [old(1, "alpha beta gamma delta", h="A", line=1), old(2, "alpha beta gamma", h="B", line=2)]
    news = [new(0, "alpha beta gamma delta epsilon", h="C"), new(1, "alpha beta gamma", h="D")]
    # (1,0)=0.8, (2,1) is an exact-description move -> rule (b) goes first
    res = pair_sources(olds, news)
    assert rules(res) == {0: (1, "edited"), 1: (2, "moved")}


def test_ties_break_by_new_then_old_document_order():
    olds = [old(1, "alpha beta gamma delta", h="A", line=1), old(2, "alpha beta gamma epsilon", h="B", line=2)]
    news = [new(0, "alpha beta gamma zeta", h="C")]
    res = pair_sources(olds, news)
    assert rules(res) == {0: (1, "edited")} and res.old_only == (2,)


def test_deterministic_regardless_of_input_order():
    olds = [old(i, f"task number {i} alpha beta", h=f"o{i}", line=i) for i in range(1, 5)]
    news = [new(i, f"task number {i} alpha beta gamma", h=f"n{i}") for i in range(4)]
    a = pair_sources(olds, news)
    b = pair_sources(list(reversed(olds)), list(reversed(news)))
    assert a == b
