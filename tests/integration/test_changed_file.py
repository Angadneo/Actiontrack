"""T-M5-12: changed-file reconciliation (pairing incl. present=0 rows, edits, moves, removal, restore, status).

Runs the real ingest (``run_ingest``) over copies of the sample fixtures and small synthetic notes."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.services import ingest as ing

from tests.integration.ingest_support import dump_tables, roster_cfg, run_ctx, write_note
from tests.integration.test_tier1_merge import note

BILLING = "2026-09-03-billing-migration-sync.md"
QUOTE15 = "2026-09-15-quote-portal-weekly.md"
BUSINESS = ("action_item", "item_source", "item_source_owner", "item_owner", "item_link", "item_event", "meeting", "source_file")


def ingest(db_path, folder, **kw):
    return ing.run_ingest(db_path, folder, cfg=roster_cfg(), run_ctx=run_ctx(), **kw)


@contextlib.contextmanager
def conn_ro(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def q(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with conn_ro(db_path) as c:
        return c.execute(sql, params).fetchall()


def dump(db_path: Path):
    with conn_ro(db_path) as c:
        return dump_tables(c)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_bytes().decode("utf-8")
    assert old in text, old
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def item_by_text(db, fragment: str) -> sqlite3.Row:
    rows = q(db, "SELECT * FROM action_item WHERE description LIKE ? ORDER BY id", (f"%{fragment}%",))
    assert rows, fragment
    return rows[0]


def file_sources(db, rel: str) -> list[sqlite3.Row]:
    return q(
        db,
        "SELECT s.* FROM item_source s JOIN source_file f ON f.id = s.source_file_id WHERE f.rel_path = ? ORDER BY s.id",
        (rel,),
    )


def sources_of(db, item_id: int) -> list[sqlite3.Row]:
    return q(db, "SELECT * FROM item_source WHERE item_id = ? ORDER BY id", (item_id,))


def events(db, run_id: int, kind: str | None = None, item_id: int | None = None) -> list[sqlite3.Row]:
    sql, params = "SELECT * FROM item_event WHERE run_id = ?", [run_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if item_id:
        sql += " AND item_id = ?"
        params.append(item_id)
    return q(db, sql + " ORDER BY id", tuple(params))


@pytest.fixture
def sample(tmp_path, copy_fixture_notes):
    folder = copy_fixture_notes()
    db = tmp_path / "t.db"
    first = ingest(db, folder)
    return db, folder, first


# ------------------------------------------------------------------------------------------ edits in place
@pytest.mark.ac("AC-08.2")
@pytest.mark.sc("SC-26")
def test_edit_one_word_keeps_the_item_and_logs_one_event(sample):
    db, folder, _ = sample
    before = item_by_text(db, "Karan to export last quarter invoices")
    items_before = q(db, "SELECT count(*) n FROM action_item")[0]["n"]
    edit(folder / BILLING, "export last quarter invoices", "export the last quarter invoices")
    res = ingest(db, folder)
    assert res.counters.files_failed == 1  # only scratch.md (binary), unchanged behaviour
    after = item_by_text(db, "export the last quarter invoices")
    assert after["id"] == before["id"]
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == items_before
    evs = events(db, res.run_row.id, "updated_from_source", before["id"])
    assert len(evs) == 1
    assert "last quarter" in evs[0]["before"] and "the last quarter" in evs[0]["after"]
    assert after["updated_at"] >= before["updated_at"]


@pytest.mark.ac("AC-08.2", "AC-08.1")
def test_reingesting_the_changed_file_twice_is_idempotent(sample):
    db, folder, _ = sample
    edit(folder / BILLING, "export last quarter invoices", "export the last quarter invoices")
    ingest(db, folder)
    snap = dump(db)
    second = ingest(db, folder)
    assert second.counters.files_ingested == 0
    assert dump(db) == snap


@pytest.mark.ac("AC-08.6")
def test_moved_lines_change_only_line_numbers(sample):
    db, folder, _ = sample
    ids_before = [r["id"] for r in q(db, "SELECT id FROM action_item ORDER BY id")]
    items_before = q(db, "SELECT * FROM action_item ORDER BY id")
    src_before = {r["id"]: r["line_start"] for r in file_sources(db, BILLING)}
    edit(folder / BILLING, "## Action items", "Two extra\nintro lines here.\n\n## Action items")
    res = ingest(db, folder)
    assert [r["id"] for r in q(db, "SELECT id FROM action_item ORDER BY id")] == ids_before
    assert events(db, res.run_row.id) == []
    for old, new in zip(items_before, q(db, "SELECT * FROM action_item ORDER BY id")):
        assert tuple(old) == tuple(new)  # updated_at included: the items were not touched
    after = file_sources(db, BILLING)
    assert {r["id"]: r["line_start"] - 3 for r in after} == src_before
    assert all(r["present"] == 1 for r in after)


@pytest.mark.ac("AC-08.6")
def test_task_line_moved_up_within_the_file(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    lines = ["- [ ] Rahul Mehta to send the revised quote", "- [ ] Priya Nair to book the room", "- [ ] Anita Desai to ship it", "- [ ] Karan Joshi to fix the pagination bug"]
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", *lines))
    ingest(db, folder)
    ids = {r["description"]: r["id"] for r in q(db, "SELECT id, description FROM action_item")}
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", lines[3], *lines[:3]))
    res = ingest(db, folder)
    assert {r["description"]: r["id"] for r in q(db, "SELECT id, description FROM action_item")} == ids
    assert events(db, res.run_row.id) == []
    assert q(db, "SELECT count(*) n FROM item_source WHERE present = 1")[0]["n"] == 4


@pytest.mark.ac("AC-08.2")
def test_edit_of_a_two_candidate_section_pairs_one_to_one(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Rahul Mehta to review the billing schema mapping doc", "- [ ] Rahul Mehta to review the billing schema mapping notes"))
    ingest(db, folder)
    ids = [r["id"] for r in q(db, "SELECT id FROM action_item ORDER BY id")]
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Rahul Mehta to review the billing schema mapping doc today", "- [ ] Rahul Mehta to review the billing schema mapping notes today"))
    ingest(db, folder)
    rows = q(db, "SELECT id, description FROM action_item ORDER BY id")
    assert [r["id"] for r in rows] == ids
    assert rows[0]["description"].endswith("doc today") and rows[1]["description"].endswith("notes today")


# ------------------------------------------------------------------------------------- removed / restored
@pytest.mark.ac("AC-08.3", "AC-08.8")
@pytest.mark.sc("SC-25")
def test_removed_line_keeps_the_item_marks_it_missing_and_ids_are_not_reused(sample):
    db, folder, _ = sample
    it = item_by_text(db, "Sam to snapshot the production DB")
    max_before = q(db, "SELECT max(id) m FROM action_item")[0]["m"]
    edit(folder / BILLING, "- [ ] Sam to snapshot the production DB before cutover\n", "")
    res = ingest(db, folder)
    row = q(db, "SELECT * FROM action_item WHERE id = ?", (it["id"],))[0]
    assert row["source_state"] == "missing" and row["status"] == it["status"]
    (src,) = sources_of(db, it["id"])
    assert src["present"] == 0
    (ev,) = events(db, res.run_row.id, "source_removed", it["id"])
    assert "billing-migration-sync.md" in ev["after"]
    # a later new item never reuses the id
    write_note(folder, "2026-10-20-new.md", note("2026-10-20", "- [ ] Anita Desai to ship it"))
    ingest(db, folder)
    assert q(db, "SELECT max(id) m FROM action_item")[0]["m"] == max_before + 1


@pytest.mark.ac("AC-08.10")
@pytest.mark.sc("SC-68")
def test_restored_line_revives_its_row_without_a_duplicate_or_same_file_link(sample):
    db, folder, _ = sample
    line = "- [ ] Sam to snapshot the production DB before cutover\n"
    it = item_by_text(db, "Sam to snapshot the production DB")
    items_n = q(db, "SELECT count(*) n FROM action_item")[0]["n"]
    links_n = q(db, "SELECT count(*) n FROM item_link")[0]["n"]
    edit(folder / BILLING, line, "")
    ingest(db, folder)
    assert q(db, "SELECT source_state FROM action_item WHERE id = ?", (it["id"],))[0][0] == "missing"
    # restore at a different position, same text
    edit(folder / BILLING, "- [ ] Karan to export", line + "- [ ] Karan to export")
    res = ingest(db, folder)
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == items_n
    assert q(db, "SELECT count(*) n FROM item_link")[0]["n"] == links_n
    assert not q(db, "SELECT 1 FROM item_link WHERE reason = 'same_file'")
    (src,) = sources_of(db, it["id"])
    assert src["present"] == 1
    assert q(db, "SELECT source_state FROM action_item WHERE id = ?", (it["id"],))[0][0] == "present"
    restored = [e for e in events(db, res.run_row.id, "source_added", it["id"]) if "restored" in (e["after"] or "")]
    assert len(restored) == 1
    assert res.counters.items_updated >= 1


@pytest.mark.ac("AC-08.10")
def test_restored_with_a_small_wording_change_pairs_by_edit(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    keep = "- [ ] Priya Nair to book the room"
    gone = "- [ ] Rahul Mehta to prepare the quarterly billing report"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", keep, gone))
    ingest(db, folder)
    rid = item_by_text(db, "quarterly billing report")["id"]
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", keep))
    ingest(db, folder)
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", keep, gone + " soon"))
    ingest(db, folder)
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == 2
    row = q(db, "SELECT * FROM action_item WHERE id = ?", (rid,))[0]
    assert row["description"].endswith("soon") and row["source_state"] == "present"
    assert not q(db, "SELECT 1 FROM item_link WHERE reason = 'same_file'")


@pytest.mark.ac("AC-08.10")
def test_identical_present_line_is_not_stolen_by_a_restored_duplicate(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    dup = "- [ ] Rahul Mehta to book the room"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", dup, dup))
    ingest(db, folder)
    ids = [r["id"] for r in q(db, "SELECT id FROM action_item ORDER BY id")]
    assert len(ids) == 2
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", dup))
    ingest(db, folder)
    pres = {r["item_id"]: r["present"] for r in q(db, "SELECT item_id, present FROM item_source")}
    assert pres[ids[0]] == 1 and pres[ids[1]] == 0
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", dup, dup))
    ingest(db, folder)
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == 2
    assert all(r["present"] == 1 for r in q(db, "SELECT present FROM item_source"))


# ------------------------------------------------------------------------------------------ merged items
LINE = "Rahul Mehta to freeze invoice edits"


def merged_pair(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-29-a.md", note("2026-09-29", f"- [ ] {LINE}"))
    write_note(folder, "2026-10-08-b.md", note("2026-10-08", f"- [ ] {LINE}"))
    ingest(db, folder)
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == 1
    return db, folder


def test_edit_of_the_later_source_of_a_merged_item_does_not_touch_the_canonical_description(tmp_path):
    db, folder = merged_pair(tmp_path)
    before = q(db, "SELECT * FROM action_item")[0]
    edit(folder / "2026-10-08-b.md", "freeze invoice edits", "freeze invoice edits entirely")
    res = ingest(db, folder)
    after = q(db, "SELECT * FROM action_item")[0]
    assert after["description"] == before["description"]
    srcs = q(db, "SELECT * FROM item_source ORDER BY id")
    assert srcs[1]["description"].endswith("entirely")
    assert len(events(db, res.run_row.id, "updated_from_source")) == 1


def test_edit_of_the_earliest_source_updates_the_canonical_description(tmp_path):
    db, folder = merged_pair(tmp_path)
    edit(folder / "2026-09-29-a.md", "freeze invoice edits", "freeze invoice edits entirely")
    res = ingest(db, folder)
    assert q(db, "SELECT description FROM action_item")[0][0].endswith("entirely")
    assert len(events(db, res.run_row.id, "updated_from_source")) == 1


# ------------------------------------------------------------------------------------------ status effects
@pytest.mark.ac("AC-07.16")
def test_checkbox_marked_done_in_a_note_completes_the_item(sample):
    db, folder, _ = sample
    # (not the schema-mapping item: since amended D-04 it is merged with the later 09-17 restatement, whose
    # own [ ] is the latest present source, so ticking the 09-03 copy alone does not complete it)
    it = item_by_text(db, "Priya to draft the test plan")
    assert it["status"] == "open"
    edit(folder / BILLING, "- [ ] Priya to draft", "- [x] Priya to draft")
    res = ingest(db, folder)
    row = q(db, "SELECT * FROM action_item WHERE id = ?", (it["id"],))[0]
    assert row["status"] == "done" and row["completion_origin"] == "note" and row["completed_at"] is None
    assert len(events(db, res.run_row.id, "status_changed", it["id"])) == 1


@pytest.mark.ac("AC-07.16", "AC-03.6")
@pytest.mark.sc("SC-66")
def test_unchecking_the_evidence_line_reopens_the_holder_and_its_inferred_items(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    ln = "Rahul Mehta to send the revised quote to Acme"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {ln} by 2026-09-04"))
    write_note(folder, "2026-09-08-b.md", note("2026-09-08", f"- [ ] {ln} by 2026-09-11"))
    write_note(folder, "2026-09-15-c.md", note("2026-09-15", f"- [x] {ln}"))
    ingest(db, folder)
    assert [r["status"] for r in q(db, "SELECT status FROM action_item ORDER BY id")] == ["done", "done"]
    edit(folder / "2026-09-15-c.md", f"- [x] {ln}", f"- [ ] {ln}")
    res = ingest(db, folder)
    assert [r["status"] for r in q(db, "SELECT status FROM action_item ORDER BY id")] == ["open", "open"]
    assert len(events(db, res.run_row.id, "status_changed")) == 2
    warns = q(db, "SELECT * FROM ingest_warning WHERE run_id = ? AND code = ?", (res.run_row.id, codes.W_STATUS_REOPENED))
    assert len(warns) == 2


def test_manual_done_is_never_reopened_by_an_edit(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [x] Rahul Mehta to book the room"))
    ingest(db, folder)
    with ing.ingest_session(db, folder) as s:
        with s.db.transaction():
            s.repo.set_manual_done(1, run_id=s.run.run_id)
    edit(folder / "2026-09-01-a.md", "- [x]", "- [ ]")
    ingest(db, folder)
    row = q(db, "SELECT * FROM action_item")[0]
    assert row["status"] == "done" and row["completion_origin"] == "manual"


# -------------------------------------------------------------------------------- meeting, owners, due
def test_meeting_date_change_updates_the_meeting_row_and_relative_due(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Rahul Mehta to book the room tomorrow"))
    ingest(db, folder)
    d0 = q(db, "SELECT due_date FROM action_item")[0][0]
    edit(folder / "2026-09-01-a.md", "Date: 2026-09-01", "Date: 2026-09-03")
    res = ingest(db, folder)
    assert q(db, "SELECT meeting_date FROM meeting")[0][0] == "2026-09-03"
    d1 = q(db, "SELECT due_date FROM action_item")[0][0]
    assert d0 != d1 and d1 == "2026-09-04"
    assert len(events(db, res.run_row.id, "updated_from_source")) == 1
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == 1


def test_owner_edit_is_re_resolved(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Rahul Mehta to book the quarterly review room for the product planning offsite team"))
    ingest(db, folder)
    edit(folder / "2026-09-01-a.md", "Rahul Mehta to book", "Anita Desai to book")
    res = ingest(db, folder)
    (own,) = q(db, "SELECT * FROM item_owner")
    assert own["owner_raw"] == "Anita Desai" and own["owner_status"] == "resolved"
    assert len(events(db, res.run_row.id, "owner_changed")) == 1
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == 1


def test_new_line_in_a_changed_file_becomes_a_new_item(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Rahul Mehta to book the room"))
    ingest(db, folder)
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Rahul Mehta to book the room", "- [ ] Priya Nair to write the budget memo"))
    res = ingest(db, folder)
    assert res.counters.items_new == 1 and res.counters.items_unchanged == 1
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == 2
    assert not q(db, "SELECT 1 FROM item_link WHERE reason = 'same_file'")


# ----------------------------------------------------------------------------------------- atomicity
@pytest.mark.parametrize("point", ["reingest_paired", "reingest_removed", "reingest_new", "file_written"])
def test_failure_inside_the_reconcile_rolls_back_the_whole_file(tmp_path, point):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Rahul Mehta to book the room", "- [ ] Priya Nair to old line"))
    write_note(folder, "2026-09-02-b.md", note("2026-09-02", "- [ ] Anita Desai to ship it"))
    ingest(db, folder)
    with conn_ro(db) as c:
        before = {k: v for k, v in dump_tables(c, BUSINESS[:-1]).items()}
    write_note(
        folder,
        "2026-09-01-a.md",
        note("2026-09-01", "- [x] Rahul Mehta to book the room today", "- [ ] Karan Joshi to add something new"),
    )
    edit(folder / "2026-09-02-b.md", "ship it", "ship it now")

    def hook(name):
        if name == f"{point}:2026-09-01-a.md":
            raise RuntimeError("injected")

    res = ingest(db, folder, fault_hook=hook)
    assert res.counters.files_failed == 1 and res.exit_code == 5
    failed = [o for o in res.outcomes if o.result == "failed"]
    assert failed[0].rel_path == "2026-09-01-a.md" and failed[0].failure_code == codes.E_INTERNAL_PARSE
    with conn_ro(db) as c:
        after = dump_tables(c, BUSINESS[:-1])
    # the failed file's rows are untouched; the other file went through
    assert after["item_source"][0] == before["item_source"][0]
    assert len(after["action_item"]) == len(before["action_item"])
    assert q(db, "SELECT description FROM action_item WHERE description LIKE '%ship it now%'")
    # retry without the fault completes the file
    again = ingest(db, folder)
    assert again.counters.files_failed == 0
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == 4
