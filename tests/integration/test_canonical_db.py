"""T-M5-22: canonical field recompute against the database (AC-07.15), hand-built multi-source items."""

from __future__ import annotations

from datetime import date

import pytest

from actiontrack.domain.canonical import OwnerView
from actiontrack.domain.models import Person, PersonOrigin
from actiontrack.services import ingest as ing

from tests.integration.ingest_support import open_repo

KARAN = OwnerView("Karan", "owner", "karan-joshi", "resolved", "roster")
ANITA = OwnerView("Anita", "owner", "anita-desai", "resolved", "roster")


@pytest.fixture
def repo(tmp_path):
    db, repo = open_repo(tmp_path / "t.db")
    with db.transaction():
        for pid, first, last in (("karan-joshi", "Karan", "Joshi"), ("anita-desai", "Anita", "Desai")):
            repo.upsert_person(Person(pid, first, last, f"{first.lower()} {last.lower()}", PersonOrigin.ROSTER))
    yield repo
    db.close()


def make_run(repo):
    return repo.insert_ingest_run("/notes")


def add_file_source(repo, item_id, rel, mdate, line, *, description, task_text=None, core="freeze invoice edits",
                    owners=(), state="open", **due):
    sf = repo.insert_source_file("/notes", rel, status="ok")
    mt = repo.insert_meeting(sf, date.fromisoformat(mdate) if mdate else None, "date_line", "t")
    sid = repo.insert_item_source(
        item_id=item_id, source_file_id=sf, meeting_id=mt, line_start=line, line_end=line, raw_text=description,
        checkbox_state=state, block_hash=f"h{rel}{line}", description=description,
        task_text=task_text or description, core_key=core, **due,
    )
    repo.set_source_owners(sid, owners)
    return sid


def build(repo):
    """Item with a 09-29 source (due none, Karan) and a 10-08 source (due 10-09 explicit, Anita)."""
    run = make_run(repo)
    iid = repo.insert_action_item(description="x", task_text="x", description_core_key="x")
    s1 = add_file_source(repo, iid, "2026-09-29-a.md", "2026-09-29", 12, description="Karan to freeze invoice edits",
                         task_text="Freeze invoice edits", owners=(KARAN,))
    s2 = add_file_source(repo, iid, "2026-10-08-b.md", "2026-10-08", 11, description="Anita freeze invoice edits by 9 Oct",
                         task_text="Freeze invoice edits now", owners=(ANITA,), due_raw="9 Oct",
                         due_date=date(2026, 10, 9), due_confidence="explicit", due_rule="text_date")
    return run, iid, s1, s2


@pytest.mark.ac("AC-07.15")
def test_canonical_follows_latest_description_earliest_and_toggles_with_presence(repo):
    run, iid, s1, s2 = build(repo)
    ing.recompute_canonical(repo, iid, run)
    item = repo.get_action_item(iid)
    assert item.description == "Karan to freeze invoice edits" and item.task_text == "Freeze invoice edits"
    assert item.description_core_key == "freeze invoice edits"
    assert (item.due_date, item.due_confidence, item.due_rule, item.due_raw) == (
        date(2026, 10, 9), "explicit", "text_date", "9 Oct")
    assert [(o.person_id, o.owner_raw) for o in repo.list_item_owners(iid)] == [("anita-desai", "Anita")]
    kinds = [e.kind for e in repo.list_events(iid)]
    assert "updated_from_source" in kinds and "owner_changed" in kinds
    # latest source goes absent -> follows 09-29
    repo.update_item_source(s2, present=False)
    ing.recompute_canonical(repo, iid, run)
    item = repo.get_action_item(iid)
    assert item.due_date is None and item.due_confidence == "none" and item.due_rule is None
    assert [o.person_id for o in repo.list_item_owners(iid)] == ["karan-joshi"]
    # restored -> switches back
    repo.update_item_source(s2, present=True)
    ing.recompute_canonical(repo, iid, run)
    assert repo.get_action_item(iid).due_date == date(2026, 10, 9)
    assert [o.person_id for o in repo.list_item_owners(iid)] == ["anita-desai"]


def test_recompute_is_idempotent_and_touches_updated_at_only_on_change(repo):
    run, iid, s1, s2 = build(repo)
    assert ing.recompute_canonical(repo, iid, run)
    before = (repo.get_action_item(iid), repo.list_item_owners(iid), len(repo.list_events(iid)))
    assert ing.recompute_canonical(repo, iid, run) == {}
    after = (repo.get_action_item(iid), repo.list_item_owners(iid), len(repo.list_events(iid)))
    assert before == after


def test_editing_only_the_later_source_wording_changes_no_description(repo):
    run, iid, s1, s2 = build(repo)
    ing.recompute_canonical(repo, iid, run)
    desc = repo.get_action_item(iid).description
    repo.update_item_source(s2, description="Anita freeze the invoice edits please", task_text="Freeze the invoice edits")
    ing.recompute_canonical(repo, iid, run)
    assert repo.get_action_item(iid).description == desc
    assert repo.get_item_source(s2).description == "Anita freeze the invoice edits please"  # per-source column kept


def test_all_sources_absent_keeps_following_the_ordering_and_status_untouched(repo):
    run, iid, s1, s2 = build(repo)
    repo.update_item_source(s1, present=False)
    repo.update_item_source(s2, present=False)
    ing.recompute_canonical(repo, iid, run)
    item = repo.get_action_item(iid)
    assert item.due_date == date(2026, 10, 9) and item.description == "Karan to freeze invoice edits"
    assert item.status == "open"


def test_status_is_never_written_by_recompute_and_manual_done_is_sticky(repo):
    run, iid, s1, s2 = build(repo)
    repo.touch_item(iid, {"status": "done", "completion_origin": "manual", "completed_at": "2026-10-20T00:00:00Z"})
    ing.recompute_canonical(repo, iid, run)
    item = repo.get_action_item(iid)
    assert (item.status, item.completion_origin) == ("done", "manual")


def test_canonical_py_is_the_only_selector_of_fields_from_sources():
    import pathlib

    root = pathlib.Path(ing.__file__).parents[1]
    hits = [
        p.name
        for p in root.rglob("*.py")
        if "earliest_source(" in p.read_text(encoding="utf-8") or "latest_source(" in p.read_text(encoding="utf-8")
    ]
    assert sorted(hits) == ["canonical.py", "matching.py"]  # matching only reads the latest *date* for the window
