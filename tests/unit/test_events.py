"""Item event writer and ``updated_at`` discipline (T-M5-02).

Spec 7.2 ItemEvent, FR-03.5, BR-08, BR-15, BR-18, 10.6, 10.7.2.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
from datetime import UTC, datetime, timedelta

import pytest

from actiontrack.clock import FixedClock
from actiontrack.domain.models import OwnerStatus, Provenance, ResolvedOwner
from actiontrack.errors import InternalError
from actiontrack.store.db import Database
from actiontrack.store.repo import BUSINESS_COLUMNS, EVENT_KINDS, Repo, canonical_json

from tests.conftest import REPO_ROOT

T0 = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)
T1 = T0 + timedelta(hours=3)


@pytest.fixture
def db(tmp_db_path):
    with Database.open(tmp_db_path, clock=FixedClock(T0)) as database:
        yield database


@pytest.fixture
def repo(db):
    return Repo(db)


@pytest.fixture
def item(repo):
    return repo.insert_action_item(description="Send quote", task_text="Send quote", description_core_key="send quote")


def later(db):
    return Repo(db, clock=FixedClock(T1))


@pytest.mark.ac("AC-08.1")
def test_noop_touch_issues_no_update_and_writes_no_event(db, repo, item):
    run = repo.insert_ingest_run("/r")
    before_row = repo.conn.execute("SELECT * FROM action_item WHERE id = ?", (item,)).fetchone()
    changes_before = repo.conn.total_changes
    result = later(db).touch_item(
        item,
        {"description": "Send quote", "status": "open", "due_date": None, "source_state": "present"},
        owners=[],
        event_kind="updated_from_source",
        run_id=run,
    )
    assert result == {}
    assert repo.conn.total_changes == changes_before  # no UPDATE / INSERT was issued at all
    assert tuple(repo.conn.execute("SELECT * FROM action_item WHERE id = ?", (item,)).fetchone()) == tuple(before_row)
    assert repo.get_action_item(item).updated_at == "2026-10-22T09:30:00Z"
    assert repo.list_events(item) == []


def test_real_change_writes_one_event_and_bumps_updated_at(db, repo, item):
    run = repo.insert_ingest_run("/r")
    r1 = later(db)
    result = r1.touch_item(
        item,
        {"due_date": dt.date(2026, 10, 16), "due_raw": "Friday", "due_confidence": "relative", "description": "Send quote"},
        event_kind="updated_from_source",
        run_id=run,
    )
    assert set(result) == {"due_date", "due_raw", "due_confidence"}
    row = r1.get_action_item(item)
    assert row.updated_at == "2026-10-22T12:30:00Z" and row.created_at == "2026-10-22T09:30:00Z"
    assert row.due_date == dt.date(2026, 10, 16)
    (event,) = r1.list_events(item)
    assert event.kind == "updated_from_source" and event.run_id == run and event.ts == "2026-10-22T12:30:00Z"
    assert event.before == {"due_confidence": "none", "due_date": None, "due_raw": None}
    assert event.after == {"due_confidence": "relative", "due_date": "2026-10-16", "due_raw": "Friday"}
    stored = r1.conn.execute('SELECT "before", "after" FROM item_event').fetchone()
    assert stored["after"] == '{"due_confidence":"relative","due_date":"2026-10-16","due_raw":"Friday"}'  # sorted keys


def test_change_without_event_kind_still_bumps_but_logs_nothing(db, repo, item):
    later(db).touch_item(item, {"status": "done", "completion_origin": "manual", "completed_at": "2026-10-22T12:00:00Z"})
    assert repo.get_action_item(item).updated_at == "2026-10-22T12:30:00Z"
    assert repo.list_events(item) == []


@pytest.mark.ac("AC-09.4")
def test_fixed_now_gives_identical_timestamps_across_runs(tmp_path):
    stamps = []
    for n in (1, 2):
        with Database.open(tmp_path / f"r{n}.db", clock=FixedClock(T0)) as database:
            r = Repo(database)
            run = r.insert_ingest_run("/r")
            i = r.insert_action_item(description="x", task_text="x", description_core_key="x")
            r.record_event(i, "created", None, {"a": 1}, run)
            r.touch_item(i, {"status": "done"}, event_kind="status_changed", run_id=run)
            stamps.append(
                [tuple(x) for x in r.conn.execute("SELECT created_at, updated_at FROM action_item")]
                + [tuple(x) for x in r.conn.execute("SELECT ts, kind, \"before\", \"after\" FROM item_event ORDER BY id")]
                + [tuple(x) for x in r.conn.execute("SELECT started_at FROM ingest_run")]
            )
    assert stamps[0] == stamps[1] and stamps[0][0] == ("2026-10-22T09:30:00Z", "2026-10-22T09:30:00Z")


def test_replaying_events_in_a_second_transaction_adds_none(db, repo, item):
    run = repo.insert_ingest_run("/r")

    def play():
        with db.transaction():
            ids = [
                repo.record_event(item, "created", None, {"task": "x"}, run),
                repo.record_event(item, "source_added", None, {"source": 1}, run),
                repo.record_event(item, "status_changed", {"status": "open"}, {"status": "done"}, run),
            ]
        return ids

    assert all(i is not None for i in play())
    assert play() == [None, None, None]
    assert len(repo.list_events(item)) == 3
    # a different payload, kind or run is a different event
    assert repo.record_event(item, "source_added", None, {"source": 2}, run) is not None
    run2 = repo.insert_ingest_run("/r")
    assert repo.record_event(item, "created", None, {"task": "x"}, run2) is not None


def test_events_without_run_id_are_never_deduplicated(repo, item):
    a = repo.record_event(item, "manual_done", {"status": "open"}, {"status": "done"}, None)
    b = repo.record_event(item, "manual_done", {"status": "open"}, {"status": "done"}, None)
    assert a is not None and b is not None and a != b


def test_event_kinds_are_exactly_the_spec_enum(repo, item):
    assert EVENT_KINDS == (
        "created", "source_added", "source_removed", "merged", "updated_from_source", "status_changed",
        "owner_changed", "manual_done",
    )
    for kind in EVENT_KINDS:
        assert repo.record_event(item, kind, None, None, None) is not None
    with pytest.raises(InternalError):
        repo.record_event(item, "reopened", None, None, None)  # reopening is a status_changed event


def test_canonical_json_sorted_compact_and_unicode():
    assert canonical_json({"b": 1, "a": [dt.date(2026, 10, 1), "café"]}) == '{"a":["2026-10-01","café"],"b":1}'
    assert canonical_json({"z": None, "y": {"b": 2, "a": 1}}) == '{"y":{"a":1,"b":2},"z":null}'
    with pytest.raises(TypeError):
        canonical_json({"x": object()})


def test_event_payload_nfc_and_null_sides(repo, item):
    repo.record_event(item, "created", None, {"t": "Café"}, None)
    row = repo.conn.execute('SELECT "before", "after" FROM item_event').fetchone()
    assert row["before"] is None and json.loads(row["after"]) == {"t": "Café"}


def test_nfc_equal_value_is_not_a_change(db, repo):
    i = repo.insert_action_item(description="Café", task_text="Café", description_core_key="cafe")
    assert later(db).touch_item(i, {"description": "Café"}, event_kind="updated_from_source", run_id=None) == {}
    assert repo.get_action_item(i).updated_at == "2026-10-22T09:30:00Z"


def test_owner_change_is_a_business_change(db, repo, item):
    run = repo.insert_ingest_run("/r")
    owners = [ResolvedOwner(0, "Everyone", None, OwnerStatus.UNASSIGNED, (), Provenance.NONE)]
    r1 = later(db)
    assert r1.touch_item(item, owners=owners, event_kind="owner_changed", run_id=run).keys() == {"owners"}
    assert r1.get_action_item(item).updated_at == "2026-10-22T12:30:00Z"
    (event,) = r1.list_events(item)
    assert event.kind == "owner_changed" and event.before == {"owners": []}
    assert event.after["owners"][0][0] == "Everyone"
    # the same owners again: nothing
    r2 = Repo(db, clock=FixedClock(T1 + timedelta(days=1)))
    assert r2.touch_item(item, owners=owners, event_kind="owner_changed", run_id=run) == {}
    assert r2.get_action_item(item).updated_at == "2026-10-22T12:30:00Z" and len(r2.list_events(item)) == 1


def test_only_business_columns_can_be_touched(repo, item):
    assert "updated_at" not in BUSINESS_COLUMNS and "id" not in BUSINESS_COLUMNS
    for bad in ("updated_at", "created_at", "id", "nonsense"):
        with pytest.raises(InternalError):
            repo.touch_item(item, {bad: "x"})
    with pytest.raises(InternalError):
        repo.touch_item(999, {"status": "done"})


def test_status_reopen_is_status_changed_event(db, repo, item):
    run = repo.insert_ingest_run("/r")
    r = later(db)
    r.touch_item(item, {"status": "done", "completion_origin": "note", "completed_at": "2026-10-22T12:00:00Z"},
                 event_kind="status_changed", run_id=run)
    run2 = r.insert_ingest_run("/r")
    r.touch_item(item, {"status": "open", "completion_origin": None, "completed_at": None},
                 event_kind="status_changed", run_id=run2)
    events = r.list_events(item)
    assert [e.kind for e in events] == ["status_changed", "status_changed"]
    assert events[1].before["status"] == "done" and events[1].after == {
        "completed_at": None, "completion_origin": None, "status": "open"}


def test_no_module_other_than_repo_writes_action_item(tmp_path):
    """Done-when: nothing but ``store/repo.py`` mentions the ``action_item`` table (no SQL outside the store,
    no ``update_row("action_item", ...)`` from services) and no other store module writes it."""
    root = REPO_ROOT / "src" / "actiontrack"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == "store/repo.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "action_item" in node.value:
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []
