"""Chronological file ordering and the file plan (T-M5-03).

Spec FR-01.1, AC-01.1, 10.7.1, 10.7.5, BR-18, NFR-DET-01.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
import random

import pytest

from actiontrack.domain.models import FileFailure, MeetingMeta, ParsedFile
from actiontrack.parsing import parse_file
from actiontrack.parsing.discovery import discover
from actiontrack.services.ingest import (
    FileClass,
    canonical_folder_root,
    classify_file,
    order_files,
    plan_files,
    to_rel_path,
)
from actiontrack.store.repo import SourceFileRow

ROOT = "/notes"


def pf(rel, date=None, status="ok", sha="h", items=()):
    meeting = MeetingMeta(date, "front_matter" if date else "none", None, None) if status == "ok" else None
    failure = FileFailure("E-BINARY", "bin") if status == "failed" else None
    return ParsedFile(rel, status, failure, sha if status != "failed" else None, 1, 1, meeting, items)


def stored(rel, sha="h", status="ok", root=ROOT, fid=1):
    return SourceFileRow(fid, root, rel, sha, 1, None, status, None, 1, 1, 1)


D = dt.date


@pytest.mark.ac("AC-01.1")
def test_ordinal_path_order_and_case():
    files = [pf("a.md"), pf("B.MD")]
    assert [f.rel_path for f in order_files(files)] == ["B.MD", "a.md"]  # uppercase before lowercase


@pytest.mark.ac("AC-01.1")
def test_discovery_excludes_txt_and_dot_files_then_orders(tmp_path):
    for name in ("a.md", "B.MD", "c.txt", ".draft.md"):
        (tmp_path / name).write_text("- [ ] x\n", encoding="utf-8")
    result = discover(tmp_path)
    parsed = [parse_file(f) for f in result.files]
    assert [p.rel_path for p in order_files(parsed)] == ["B.MD", "a.md"]


def test_date_beats_path_and_null_last():
    files = [pf("a.md", D(2026, 10, 2)), pf("z.md", D(2026, 9, 1)), pf("m.md"), pf("b.md", D(2026, 10, 2))]
    assert [f.rel_path for f in order_files(files)] == ["z.md", "a.md", "b.md", "m.md"]


def test_nested_paths_use_slash_and_code_point_order():
    files = [pf("a/b.md"), pf("a-b.md"), pf("a.md"), pf("é.md"), pf("z.md")]
    # '-' (0x2d) < '.' (0x2e) < '/' (0x2f) < 'z' < U+00E9 : plain code-point comparison
    assert [f.rel_path for f in order_files(files)] == ["a-b.md", "a.md", "a/b.md", "z.md", "é.md"]


@pytest.mark.ac("AC-01.1")
@pytest.mark.sc("SC-01")
def test_sample_order(fixtures_dir):
    result = discover(fixtures_dir)
    parsed = [parse_file(f) for f in result.files]
    order = [p.rel_path for p in order_files(parsed)]
    assert order.index("2026-09-30-q4-offsite.md") + 1 == order.index("2026-09-30-standup.md")
    tail = order[order.index("2026-10-14-acme-renewal-wrapup.md") + 1 :]
    # the null group (failed, empty and invalid-date files) follows in ordinal path order
    assert tail == ["2026-09-09-notes-final-v2.md", "2026-10-03-sales-pipeline.md", "scratch.md", "untitled.md"]
    assert order[0] == "2026-09-01-quote-portal-kickoff.md"
    assert "2026-09-19-offsite-draft.txt" not in order


def test_shuffle_is_stable_with_fixed_seed(fixtures_dir):
    parsed = [parse_file(f) for f in discover(fixtures_dir).files]
    expected = [p.rel_path for p in order_files(parsed)]
    rng = random.Random(20261022)
    for _ in range(20):
        shuffled = parsed[:]
        rng.shuffle(shuffled)
        assert [p.rel_path for p in order_files(shuffled)] == expected


def test_order_files_is_pure():
    files = [pf("b.md", D(2026, 1, 2)), pf("a.md", D(2026, 1, 1))]
    snapshot = list(files)
    assert order_files(files) is not files and files == snapshot
    assert order_files(iter(files)) == order_files(files)


# --------------------------------------------------------------------------- classification
def test_classify_file_table():
    ok = pf("a.md", D(2026, 1, 1), sha="s1")
    assert classify_file(ok, None) is FileClass.NEW
    assert classify_file(ok, stored("a.md", "s1")) is FileClass.UNCHANGED
    assert classify_file(ok, stored("a.md", "s0")) is FileClass.CHANGED
    for prev_status in ("failed", "empty", "missing"):
        assert classify_file(ok, stored("a.md", "s1", status=prev_status)) is FileClass.CHANGED
    assert classify_file(ok, dataclasses.replace(stored("a.md"), content_sha256=None)) is FileClass.CHANGED
    # empty and failed are never unchanged (G-25 / G-28), whatever is stored
    assert classify_file(pf("e.md", status="empty", sha="s1"), stored("e.md", "s1")) is FileClass.EMPTY
    assert classify_file(pf("f.md", status="failed"), stored("f.md", "s1")) is FileClass.FAILED
    assert classify_file(pf("e.md", status="empty"), None) is FileClass.EMPTY


def test_plan_partitions_and_carries_previous_row():
    files = [
        pf("b.md", D(2026, 10, 2), sha="b1"),
        pf("a.md", D(2026, 10, 1), sha="a1"),
        pf("empty.md", status="empty", sha="e"),
        pf("bad.md", status="failed"),
        pf("new.md", None, sha="n"),
    ]
    rows = [stored("a.md", "a1", fid=1), stored("b.md", "b0", fid=2), stored("gone.md", fid=3)]
    plan = plan_files(files, rows, folder_root=ROOT)
    assert [(e.rel_path, e.file_class) for e in plan.entries] == [
        ("a.md", FileClass.UNCHANGED), ("b.md", FileClass.CHANGED), ("new.md", FileClass.NEW)]
    assert [e.rel_path for e in plan.failed] == ["bad.md"] and [e.rel_path for e in plan.empty] == ["empty.md"]
    assert [r.rel_path for r in plan.missing] == ["gone.md"]
    by = {e.rel_path: e for e in plan.entries}
    assert by["b.md"].previous.id == 2 and by["b.md"].previous_hash == "b0"
    assert by["new.md"].previous is None and by["new.md"].previous_hash is None
    assert by["a.md"].order_key == (False, D(2026, 10, 1), "a.md")
    assert by["new.md"].order_key[0] is True
    assert plan.folder_root == ROOT and plan.recursive is True


def test_plan_ignores_stored_rows_of_other_roots_and_already_missing():
    rows = [stored("x.md", root="/other"), stored("old.md", status="missing", fid=2), stored("a.md", fid=3)]
    plan = plan_files([pf("a.md", sha="h")], rows, folder_root=ROOT)
    assert plan.missing == () and plan.entries[0].file_class is FileClass.UNCHANGED


@pytest.mark.ac("AC-08.10")
def test_no_recursive_scope_does_not_mark_nested_files_missing():
    rows = [stored("top.md", fid=1), stored("sub/nested.md", fid=2), stored("gone-top.md", fid=3)]
    flat = plan_files([pf("top.md", sha="h")], rows, folder_root=ROOT, recursive=False)
    assert [r.rel_path for r in flat.missing] == ["gone-top.md"]
    deep = plan_files([pf("top.md", sha="h")], rows, folder_root=ROOT, recursive=True)
    assert [r.rel_path for r in deep.missing] == ["gone-top.md", "sub/nested.md"]


def test_plan_is_independent_of_input_order():
    rng = random.Random(7)
    files = [pf(f"f{i}.md", D(2026, 10, 1 + i % 5), sha=str(i)) for i in range(12)] + [pf("n.md"), pf("x.md", status="empty")]
    rows = [stored(f"f{i}.md", str(i % 3), fid=i + 1) for i in range(0, 12, 2)] + [stored("gone.md", fid=99)]
    expected = plan_files(files, rows, folder_root=ROOT)
    for _ in range(10):
        rng.shuffle(files)
        rng.shuffle(rows)
        assert plan_files(files, rows, folder_root=ROOT) == expected


def test_plan_after_parsing_everything_failures_isolated(tmp_path):
    (tmp_path / "ok.md").write_text("# Meeting\nDate: 2026-10-01\n- [ ] Priya to send it\n", encoding="utf-8")
    (tmp_path / "bin.md").write_bytes(b"\x00\x01binary")
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    result = discover(tmp_path)
    parsed = [parse_file(f) for f in result.files]
    plan = plan_files(parsed, [], folder_root=result.folder_root)
    assert [e.rel_path for e in plan.entries] == ["ok.md"]
    assert [e.rel_path for e in plan.failed] == ["bin.md"] and [e.rel_path for e in plan.empty] == ["empty.md"]


# --------------------------------------------------------------------------- path canonicalisation
def test_canonical_folder_root_and_rel_path(tmp_path):
    sub = tmp_path / "notes"
    (sub / "a").mkdir(parents=True)
    root = canonical_folder_root(sub)
    assert root == os.path.realpath(sub) and os.path.isabs(root)
    assert canonical_folder_root(str(sub) + os.sep + "a" + os.sep + "..") == root  # dot segments resolved
    assert to_rel_path(root, os.path.join(root, "a", "b.md")) == "a/b.md"
    assert to_rel_path(root, os.path.join(root, "B.MD")) == "B.MD"
    assert discover(sub).folder_root == root
