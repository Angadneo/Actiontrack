"""``done`` service on a synthetic database (M6: T-M6-09/10; spec 4.4, FR-03.*, AC-03.*)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from actiontrack.clock import FixedClock
from actiontrack.errors import NotFoundError, UsageError
from actiontrack.services.done import mark_done
from actiontrack.store.db import Database
from actiontrack.store.repo import Repo

NOW = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)
CLOCK = FixedClock(NOW)


@pytest.fixture
def world(tmp_db_path):
    db = Database.open(tmp_db_path, clock=FixedClock(datetime(2026, 10, 1, tzinfo=UTC)))
    repo = Repo(db, db.clock)
    a = repo.insert_action_item(description="Alpha desc", task_text="Alpha", description_core_key="alpha")
    b = repo.insert_action_item(description="Beta desc", task_text="Beta", description_core_key="beta")
    repo.insert_link(a, b, "possible_duplicate", "due_differs")
    yield db, Repo(db, CLOCK), a, b
    db.close()


@pytest.mark.parametrize("ref", ["AT-0001", "1", "at-1", " 1 "])
def test_id_forms_all_hit_the_same_item(world, ref):
    db, repo, a, _ = world
    result = mark_done(db, repo, ref, CLOCK)
    assert result.item_id == a and not result.already_done and result.previous_status == "open"


@pytest.mark.parametrize("ref", ["abc", "AT-", "1.5", "²", "AT--1", "12abc", "0", ""])
def test_bad_syntax_is_usage_error(world, ref):
    db, repo, *_ = world
    with pytest.raises(UsageError):
        mark_done(db, repo, ref, CLOCK)


def test_unknown_id_is_not_found_and_changes_nothing(world):
    db, repo, a, b = world
    before = [tuple(r) for r in db.conn.execute("SELECT * FROM action_item ORDER BY id")]
    for ref in ("AT-9999", "9" * 30):
        with pytest.raises(NotFoundError) as e:
            mark_done(db, repo, ref, CLOCK)
        assert "AT-" in e.value.what or ref in e.value.what
    assert [tuple(r) for r in db.conn.execute("SELECT * FROM action_item ORDER BY id")] == before


def test_open_to_done_sets_fields_and_one_event(world):
    db, repo, a, b = world
    before = repo.get_action_item(a)
    result = mark_done(db, repo, "AT-0001", CLOCK)
    row = repo.get_action_item(a)
    assert (row.status, row.completion_origin, row.completed_at, row.updated_at) == (
        "done", "manual", "2026-10-22T09:30:00Z", "2026-10-22T09:30:00Z",
    )
    assert (row.description, row.task_text, row.due_date, row.due_raw) == (
        before.description, before.task_text, before.due_date, before.due_raw,
    )
    events = [e for e in repo.list_events(a) if e.kind == "manual_done"]
    assert len(events) == 1 and events[0].before["status"] == "open"
    assert result.to_json() == {
        "id": a, "status": "done", "previous_status": "open", "already_done": False,
        "completion_origin": "manual", "completed_at": "2026-10-22T09:30:00Z",
    }
    assert repo.get_action_item(b).status == "open"  # the linked possible duplicate is untouched
    assert [(d.other_id, d.status, d.reason) for d in result.duplicates] == [(b, "open", "due_differs")]


def test_second_done_is_a_noop(world):
    db, repo, a, _ = world
    mark_done(db, repo, "1", CLOCK)
    snapshot = [tuple(r) for r in db.conn.execute("SELECT * FROM action_item")]
    events = len(repo.list_events())
    later = FixedClock(datetime(2026, 11, 1, tzinfo=UTC))
    again = mark_done(db, Repo(db, later), "1", later)
    assert again.already_done and again.previous_status == "done" and again.completion_origin == "manual"
    assert again.to_json()["already_done"] is True
    assert [tuple(r) for r in db.conn.execute("SELECT * FROM action_item")] == snapshot
    assert len(repo.list_events()) == events
