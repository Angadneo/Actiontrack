"""Repository layer for the ingest entities and item-id allocation (T-M5-01).

Spec 7.2 (G-11), BR-08, BR-11, AC-08.8, NFR-REL-01, NFR-DET-01.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from datetime import UTC, datetime

import pytest

from actiontrack.clock import FixedClock
from actiontrack.domain.models import OwnerRole, OwnerStatus, Provenance, ResolvedOwner
from actiontrack.errors import InternalError
from actiontrack.store.db import Database
from actiontrack.store.repo import (
    ActionItemRow,
    ItemSourceRow,
    Repo,
    format_item_id,
    parse_item_id,
)

from tests.conftest import REPO_ROOT

T0 = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)
ROOT = "C:/notes"


@pytest.fixture
def db(tmp_db_path):
    with Database.open(tmp_db_path, clock=FixedClock(T0)) as database:
        yield database


@pytest.fixture
def repo(db):
    return Repo(db)


def make_item(repo, text="Send the quote", **kw):
    return repo.insert_action_item(description=text, task_text=text, description_core_key="send quote", **kw)


def make_source_stack(repo):
    run = repo.insert_ingest_run(ROOT)
    sf = repo.insert_source_file(ROOT, "a.md", status="ok", content_sha256="ab", run_id=run, ingested=True)
    meeting = repo.insert_meeting(sf, dt.date(2026, 10, 14), "front_matter", "Sync")
    item = make_item(repo)
    src = repo.insert_item_source(
        item_id=item, source_file_id=sf, meeting_id=meeting, line_start=3, line_end=4,
        raw_text="- [ ] x", checkbox_state="open", block_hash="h", run_id=run,
    )
    return run, sf, meeting, item, src


ALL_TABLES = (
    "ingest_run", "source_file", "ingest_warning", "meeting", "meeting_attendee", "action_item",
    "item_source", "item_source_owner", "item_owner", "item_link", "item_event",
)


def counts(repo):
    return {t: repo.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in ALL_TABLES}


# --------------------------------------------------------------------------- id allocation (BR-11)
@pytest.mark.ac("AC-08.8")
def test_item_ids_never_reused_after_manual_delete(repo):
    ids = [make_item(repo, f"t{i}") for i in range(3)]
    assert ids == [1, 2, 3]
    repo.conn.execute("DELETE FROM action_item WHERE id = 3")
    assert repo.next_item_id_preview() == 4
    assert make_item(repo, "fourth") == 4
    assert format_item_id(4) == "AT-0004"


def test_item_id_after_rollback_is_not_a_committed_id(repo):
    a = make_item(repo)
    with pytest.raises(RuntimeError), repo.db.transaction():
        make_item(repo, "lost")
        raise RuntimeError
    # nothing was committed under the rolled-back id, so the sequence continues from the last committed one
    assert repo.list_action_items() and [r.id for r in repo.list_action_items()] == [a]
    assert make_item(repo, "next") == a + 1


def test_id_helpers():
    assert parse_item_id("at-12") == 12
    assert format_item_id(12) == "AT-0012"
    assert format_item_id(123456) == "AT-123456"


# --------------------------------------------------------------------------- links
def test_insert_link_idempotent_and_symmetric_storage(repo):
    ids = [make_item(repo, f"t{i}") for i in range(9)]
    assert ids[-1] == 9
    assert repo.insert_link(9, 3, "possible_duplicate", "fuzzy") is True
    assert repo.insert_link(9, 3, "possible_duplicate", "fuzzy") is False
    assert repo.insert_link(3, 9, "possible_duplicate", "fuzzy") is False
    rows = repo.list_links("possible_duplicate")
    assert [(r.from_item_id, r.to_item_id) for r in rows] == [(3, 9)]
    assert [(r.from_item_id, r.to_item_id) for r in repo.links_of(3)] == [(3, 9)]
    assert [(r.from_item_id, r.to_item_id) for r in repo.links_of(9)] == [(3, 9)]
    assert repo.links_of(5) == []


def test_completed_by_is_directional_and_never_reordered(repo):
    for i in range(9):
        make_item(repo, f"t{i}")
    assert repo.insert_link(9, 3, "completed_by", None) is True
    assert repo.insert_link(9, 3, "completed_by", None) is False
    row = repo.list_links("completed_by")[0]
    assert (row.from_item_id, row.to_item_id, row.reason) == (9, 3, None)
    assert repo.insert_link(3, 9, "completed_by") is True  # opposite direction is a different link
    assert len(repo.list_links("completed_by")) == 2


def test_link_validation_is_loud(repo):
    make_item(repo)
    make_item(repo, "b")
    with pytest.raises(InternalError):
        repo.insert_link(1, 1, "possible_duplicate", "fuzzy")
    with pytest.raises(InternalError):
        repo.insert_link(1, 2, "possible_duplicate", None)
    with pytest.raises(InternalError):
        repo.insert_link(1, 2, "possible_duplicate", "nonsense")
    with pytest.raises(InternalError):
        repo.insert_link(1, 2, "other", None)
    with pytest.raises(sqlite3.IntegrityError):  # foreign key to a missing item
        repo.insert_link(1, 99, "completed_by")


# --------------------------------------------------------------------------- transactions / FKs
@pytest.mark.ac("AC-08.5")
def test_rollback_leaves_no_rows_in_any_table(repo):
    base = counts(repo)
    with pytest.raises(RuntimeError), repo.db.transaction():
        run, sf, meeting, item, src = make_source_stack(repo)
        other = make_item(repo, "other")
        repo.insert_link(item, other, "possible_duplicate", "fuzzy")
        repo.record_event(item, "created", None, {"x": 1}, run)
        repo.add_warning(run, "W-NO-ITEMS", "m", source_file_id=sf, line=1)
        repo.set_item_owners(item, [ResolvedOwner(0, None, None, OwnerStatus.UNASSIGNED)])
        repo.set_source_owners(src, [ResolvedOwner(0, None, None, OwnerStatus.UNASSIGNED)])
        repo.insert_attendees(meeting, [("Priya Nair", None, None)])
        assert counts(repo) != base
        raise RuntimeError
    assert counts(repo) == base


def test_foreign_keys_enforced(repo):
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_item_source(
            item_id=42, source_file_id=1, meeting_id=None, line_start=1, line_end=1,
            raw_text="x", checkbox_state="open", block_hash="h",
        )
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_meeting(7, None, "none", None)


# --------------------------------------------------------------------------- per entity round trips
def test_ingest_run_roundtrip_and_counters(repo):
    run = repo.insert_ingest_run(ROOT, dry_run=True)
    row = repo.get_ingest_run(run)
    assert (row.status, row.dry_run, row.started_at, row.finished_at) == ("in_progress", True, "2026-10-22T09:30:00Z", None)
    repo.bump_run_counters(run, files_discovered=3, items_new=2)
    repo.bump_run_counters(run, items_new=1)
    repo.update_ingest_run(run, status="partial", finished_at=repo.now_iso())
    row = repo.get_ingest_run(run)
    assert (row.files_discovered, row.items_new, row.status) == (3, 3, "partial")
    assert repo.list_ingest_runs("partial")[0].id == run and repo.list_ingest_runs("complete") == []
    with pytest.raises(InternalError):
        repo.bump_run_counters(run, bogus=1)
    with pytest.raises(InternalError):
        repo.update_ingest_run(run, id=5)
    assert repo.get_ingest_run(99) is None


def test_source_file_roundtrip_and_ordering(repo):
    run = repo.insert_ingest_run(ROOT)
    repo.insert_source_file(ROOT, "b.md", status="ok", content_sha256="1", size_bytes=5, mtime="2026-10-01T00:00:00Z", run_id=run, ingested=True)
    repo.insert_source_file(ROOT, "B.md", status="failed", failure_code="E-BINARY", run_id=run)
    repo.insert_source_file("D:/other", "a.md", status="empty", run_id=run)
    assert [r.rel_path for r in repo.list_source_files(ROOT)] == ["B.md", "b.md"]  # ordinal, uppercase first
    assert [(r.folder_root, r.rel_path) for r in repo.list_source_files()] == [
        (ROOT, "B.md"), (ROOT, "b.md"), ("D:/other", "a.md")]
    f = repo.get_source_file(ROOT, "b.md")
    assert (f.content_sha256, f.size_bytes, f.first_seen_run, f.last_ingested_run) == ("1", 5, run, run)
    assert repo.get_source_file(ROOT, "B.md").last_ingested_run is None
    assert repo.get_source_file(ROOT, "zzz.md") is None
    repo.update_source_file(f.id, status="missing", last_run_id=run)
    assert repo.get_source_file_by_id(f.id).status == "missing"
    assert [r.rel_path for r in repo.list_source_files(ROOT, status="missing")] == ["b.md"]
    with pytest.raises(InternalError):
        repo.update_source_file(f.id, rel_path="x")
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_source_file(ROOT, "b.md", status="ok")  # (folder_root, rel_path) unique


def test_rel_path_is_verbatim_not_nfc(repo):
    decomposed = "Cafe\u0301.md"
    repo.insert_source_file(ROOT, decomposed, status="ok")
    assert repo.get_source_file(ROOT, decomposed).rel_path == decomposed
    assert repo.get_source_file(ROOT, "Caf\u00e9.md") is None


def test_meeting_and_attendees(repo):
    sf = repo.insert_source_file(ROOT, "a.md", status="ok")
    mid = repo.insert_meeting(sf, dt.date(2026, 10, 14), "filename", "Cafe\u0301 sync")
    row = repo.get_meeting_for_file(sf)
    assert (row.id, row.meeting_date, row.date_source, row.title) == (mid, dt.date(2026, 10, 14), "filename", "Caf\u00e9 sync")
    repo.update_meeting(mid, meeting_date=None, date_source="none")
    assert repo.get_meeting_for_file(sf).meeting_date is None
    assert repo.insert_attendees(mid, [("Priya", None, None), ("Anita", None, "a@x.org")]) == 2
    assert [r["raw_name"] for r in repo.list_attendees(mid)] == ["Anita", "Priya"]
    assert repo.get_meeting_for_file(999) is None
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_meeting(sf, None, "none", None)  # one meeting per file


def test_action_item_roundtrip_nfc_and_clock(repo):
    item = repo.insert_action_item(
        description="Cafe\u0301 review", task_text="Cafe\u0301", description_core_key="cafe review",
        due_raw="Friday", due_date=dt.date(2026, 10, 16), due_confidence="relative", due_rule="weekday_next",
    )
    row = repo.get_action_item(item)
    assert isinstance(row, ActionItemRow)
    assert row.description == "Caf\u00e9 review" and row.task_text == "Caf\u00e9"
    assert row.due_date == dt.date(2026, 10, 16) and row.due_confidence == "relative"
    assert row.created_at == row.updated_at == "2026-10-22T09:30:00Z"
    assert (row.status, row.source_state, row.completion_origin) == ("open", "present", None)
    make_item(repo, "b")
    repo.insert_action_item(description="c", task_text="c", description_core_key="c", status="done",
                            completion_origin="note", completed_at="2026-10-22T09:30:00Z")
    assert [r.id for r in repo.list_action_items()] == [1, 2, 3]
    assert [r.id for r in repo.list_action_items("done")] == [3]
    assert repo.get_action_item(77) is None


def test_item_source_roundtrip_verbatim_raw_text(repo):
    run = repo.insert_ingest_run(ROOT)
    sf = repo.insert_source_file(ROOT, "a.md", status="ok")
    item = make_item(repo)
    raw = "- [ ] Cafe\u0301 \u2014 by Friday"
    sid = repo.insert_item_source(
        item_id=item, source_file_id=sf, meeting_id=None, line_start=5, line_end=6, raw_text=raw,
        checkbox_state="open", block_hash="h1", section_heading="Actions", occurrence_index=1, run_id=run,
        description="Cafe\u0301", task_text="Cafe\u0301", core_key="cafe", due_raw="by Friday",
        due_date="2026-10-16", due_confidence="relative", due_rule="weekday_next",
    )
    row = repo.get_item_source(sid)
    assert isinstance(row, ItemSourceRow)
    assert row.raw_text == raw  # verbatim, not NFC
    assert row.description == "Caf\u00e9" and row.due_date == dt.date(2026, 10, 16)
    assert row.present is True and (row.first_seen_run, row.last_seen_run) == (run, run)
    repo.update_item_source(sid, present=False, line_start=9, last_seen_run=run)
    row = repo.get_item_source(sid)
    assert row.present is False and row.line_start == 9
    other = repo.insert_item_source(
        item_id=item, source_file_id=sf, meeting_id=None, line_start=1, line_end=1, raw_text="x",
        checkbox_state="done", block_hash="h2",
    )
    assert [r.id for r in repo.list_item_sources(item_id=item)] == [sid, other]
    assert [r.id for r in repo.list_item_sources(present=True)] == [other]
    assert [r.id for r in repo.list_item_sources(source_file_id=sf, present=False)] == [sid]
    with pytest.raises(InternalError):
        repo.update_item_source(sid, item_id=2)
    assert repo.get_item_source(99) is None


def test_item_and_source_owners(repo):
    repo.insert_row("person", {"id": "priya-nair", "first_name": "Priya", "last_name": "Nair",
                               "display_name": "Priya Nair", "name_key": "priya nair",
                               "identity_key": "priya nair", "origin": "roster"})
    _run, _sf, _m, item, src = make_source_stack(repo)
    owners = [
        ResolvedOwner(0, "Priya", "priya-nair", OwnerStatus.RESOLVED, (), Provenance.ROSTER),
        ResolvedOwner(1, "Sam", None, OwnerStatus.AMBIGUOUS, ("b", "a"), Provenance.NONE, OwnerRole.WITH),
    ]
    assert repo.set_item_owners(item, owners) is True
    assert repo.set_item_owners(item, owners) is False
    rows = repo.list_item_owners(item)
    assert [(r.ordinal, r.owner_raw, r.person_id, r.owner_status, r.role) for r in rows] == [
        (0, "Priya", "priya-nair", "resolved", "owner"), (1, "Sam", None, "ambiguous", "with")]
    assert rows[1].candidates == ("a", "b") and rows[0].provenance == "roster"
    assert repo.set_item_owners(item, owners[:1]) is True and len(repo.list_item_owners(item)) == 1
    assert repo.set_source_owners(src, owners) is True
    assert repo.set_source_owners(src, owners) is False
    assert [(r.ordinal, r.owner_raw, r.role) for r in repo.list_source_owners(src)] == [(0, "Priya", "owner"), (1, "Sam", "with")]
    assert [r.item_source_id for r in repo.list_all_source_owners()] == [src, src]


def test_warnings_and_attendee_order(repo):
    run = repo.insert_ingest_run(ROOT)
    sf = repo.insert_source_file(ROOT, "a.md", status="ok")
    w1 = repo.add_warning(run, "W-NO-ITEMS", "no items", source_file_id=sf)
    w2 = repo.add_warning(run, "W-EMPTY-FILE", "empty", line=3)
    assert [w.id for w in repo.list_warnings(run)] == [w1, w2]
    assert [w.code for w in repo.list_warnings(source_file_id=sf)] == ["W-NO-ITEMS"]
    assert repo.list_warnings(run)[1].line == 3 and repo.list_warnings(run)[1].source_file_id is None


# --------------------------------------------------------------------------- structure guards
def test_repo_never_commits_and_never_deletes_sources_or_links():
    text = (REPO_ROOT / "src" / "actiontrack" / "store" / "repo.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert ".commit(" not in code and not re.search(r"\bCOMMIT\b", code)
    assert not re.search(r"DELETE\s+FROM\s+\"?(item_source|item_link)\b", code, re.IGNORECASE)


# column names of spec 7.2 for the ingest entities (copied from the spec table)
SPEC_COLUMNS = {
    "action_item": "id description task_text description_core_key status completion_origin completed_at "
    "completion_evidence_source_id due_raw due_raw_other due_date due_confidence due_rule due_reason "
    "source_state created_at updated_at",
    "item_owner": "item_id ordinal owner_raw role person_id owner_status candidates provenance",
    "meeting": "id source_file_id meeting_date date_source title",
    "meeting_attendee": "meeting_id raw_name person_id email_hint",
    "source_file": "id folder_root rel_path content_sha256 size_bytes mtime status failure_code last_run_id "
    "first_seen_run last_ingested_run",
    "item_source": "id item_id source_file_id meeting_id line_start line_end raw_text section_heading "
    "checkbox_state occurrence_index block_hash present first_seen_run last_seen_run description task_text "
    "core_key due_raw due_raw_other due_date due_confidence due_rule due_reason",
    "item_link": "from_item_id to_item_id kind reason created_run",
    "item_event": "id item_id ts kind before after run_id",
    "ingest_run": "id started_at finished_at folder_root status dry_run files_discovered files_ingested "
    "files_unchanged files_empty files_failed items_new items_merged items_updated items_unchanged "
    "possible_duplicates already_done_in_note resolved_via_attendance",
    "ingest_warning": "run_id source_file_id line code message",
}


def test_schema_has_every_spec_7_2_column_for_ingest_entities(repo):
    for table, names in SPEC_COLUMNS.items():
        have = {r["name"] for r in repo.conn.execute(f'PRAGMA table_info("{table}")')}
        assert set(names.split()) <= have, (table, set(names.split()) - have)
