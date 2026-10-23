"""T-M5-11: status effects, sticky manual done and the 10.7.3 reopen rule (AC-07.16, AC-03.6, SC-18, SC-66, BR-21).

Changed-file reconciliation (T-M5-12) is not part of this task, so an "edit" is simulated the way T-M5-12 will
report it: the changed ``item_source`` row / meeting date is updated in place, then ``apply_status_effects`` is
called with the ``EvidenceChange`` the pairing step would produce."""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.domain.status import EvidenceChange
from actiontrack.services import ingest as ing

from tests.conftest import FIXTURES_NOTES
from tests.integration.ingest_support import roster_cfg, run_ctx, write_note
from tests.integration.test_tier1_merge import note

LINE = "Rahul Mehta to send the revised quote to Acme"


def ingest(db_path, folder, **kw):
    return ing.run_ingest(db_path, folder, cfg=roster_cfg(), run_ctx=run_ctx(), **kw)


def q(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@contextlib.contextmanager
def session(db_path, folder):
    """A write session on an ingested database: ``(env, repo, db)``; the caller wraps writes in ``db.transaction()``."""
    cfg = roster_cfg()
    with ing.ingest_session(db_path, folder) as s:
        registry = s.repo.load_registry(cfg.roster)
        env = ing.IngestEnv(s.db, s.repo, registry, s.run.run_id, s.folder_root, date(2026, 10, 22), cfg)
        yield env, s.repo, s.db


def quote_portal(tmp_path):
    """09-01 open (due 09-04), 09-08 open (due 09-11), 09-15 [x] dateless: items 1 (CI) and 2 (holder), both done."""
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {LINE} by 2026-09-04"))
    write_note(folder, "2026-09-08-b.md", note("2026-09-08", f"- [ ] {LINE} by 2026-09-11"))
    write_note(folder, "2026-09-15-c.md", note("2026-09-15", f"- [x] {LINE}"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    assert [r["status"] for r in q(db, "SELECT status FROM action_item ORDER BY id")] == ["done", "done"]
    return db, folder


def src_id(db, rel_prefix):
    return q(
        db,
        "SELECT s.id FROM item_source s JOIN source_file f ON f.id = s.source_file_id WHERE f.rel_path LIKE ?",
        (rel_prefix + "%",),
    )[0]["id"]


def statuses(db):
    return [r["status"] for r in q(db, "SELECT status FROM action_item ORDER BY id")]


def change(env_repo, source_id, now, *, old=None, new=None, present=True):
    s = env_repo.get_item_source(source_id)
    d = env_repo.list_source_context(s.item_id)
    when = next(c.meeting_date for c in d if c.source.id == source_id)
    return EvidenceChange(source_id, now, old or when, new or when, present)


# --------------------------------------------------------------------------------------- AC-07.16 (a)
@pytest.mark.ac("AC-07.16")
@pytest.mark.sc("SC-66")
def test_ac_07_16_a_unrelated_edit_of_the_inferred_items_own_source_never_reopens(tmp_path):
    db, folder = quote_portal(tmp_path)
    own = src_id(db, "2026-09-01")  # the file holding the CI item's own [ ] source
    events_before = q(db, "SELECT count(*) n FROM item_event")[0]["n"]
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            # (1) an unrelated word edited: the line is paired in place and still reads [ ]
            r1 = ing.apply_status_effects(env, 1, change(repo, own, "open"))
            # (2) the line is rewritten in place and stays [ ]
            repo.update_item_source(own, raw_text="- [ ] rewritten", description="rewritten")
            r2 = ing.apply_status_effects(env, 1, change(repo, own, "open"))
            # (3) the same row reported against the holder: not the holder's evidence either
            r3 = ing.apply_status_effects(env, 2, change(repo, own, "open"))
    assert r1.reopened == r2.reopened == r3.reopened == ()
    assert statuses(db) == ["done", "done"]
    assert q(db, "SELECT count(*) n FROM item_event")[0]["n"] == events_before  # no event
    assert [w for w in q(db, "SELECT code FROM ingest_warning") if w["code"] == codes.W_STATUS_REOPENED] == []


# --------------------------------------------------------------------------------------- AC-07.16 (b)
@pytest.mark.ac("AC-07.16")
@pytest.mark.sc("SC-66")
def test_ac_07_16_b_evidence_line_to_open_reopens_holder_and_inferred_item(tmp_path):
    db, folder = quote_portal(tmp_path)
    evidence = src_id(db, "2026-09-15")
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.update_item_source(evidence, checkbox_state="open")
            res = ing.apply_status_effects(env, 2, change(repo, evidence, "open"))
        run_id = env.run_id
    assert res.reopened == (2, 1)  # holder first, then the item completed from it
    for r in q(db, "SELECT * FROM action_item ORDER BY id"):
        assert (r["status"], r["completion_origin"], r["completed_at"], r["completion_evidence_source_id"]) == (
            "open", None, None, None)
    for item_id in (1, 2):
        ev = q(db, "SELECT * FROM item_event WHERE item_id = ? AND kind = 'status_changed' AND run_id = ?", (item_id, run_id))
        assert len(ev) == 1 and '"status":"open"' in ev[0]["after"] and "2026-09-15-c.md:" in ev[0]["after"]
    warns = q(db, "SELECT * FROM ingest_warning WHERE code = ? AND run_id = ? ORDER BY id", (codes.W_STATUS_REOPENED, run_id))
    assert len(warns) == 2
    assert "AT-0002" in warns[0]["message"] and "AT-0001" in warns[1]["message"]
    assert all(w["source_file_id"] is not None and w["line"] for w in warns)
    # nothing deleted: links and sources stay
    assert q(db, "SELECT count(*) n FROM item_link WHERE kind = 'completed_by'")[0]["n"] == 1
    assert q(db, "SELECT count(*) n FROM item_source")[0]["n"] == 3


@pytest.mark.ac("AC-07.16")
def test_reporting_the_inferred_item_first_gives_the_same_final_state(tmp_path):
    db, folder = quote_portal(tmp_path)
    evidence = src_id(db, "2026-09-15")
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.update_item_source(evidence, checkbox_state="open")
            first = ing.apply_status_effects(env, 1, change(repo, evidence, "open"))
            second = ing.apply_status_effects(env, 2, change(repo, evidence, "open"))
    assert first.reopened == (1,) and second.reopened == (2,)  # holder's dependent (item 1) was already open
    assert statuses(db) == ["open", "open"]


@pytest.mark.ac("AC-07.16")
def test_plain_note_done_item_reopens_when_its_own_line_flips_to_open(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [x] {LINE}"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    evidence = src_id(db, "2026-09-01")
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.update_item_source(evidence, checkbox_state="open")
            res = ing.apply_status_effects(env, 1, change(repo, evidence, "open"))
    assert res.reopened == (1,) and statuses(db) == ["open"]


@pytest.mark.ac("AC-07.16")
def test_only_the_evidence_source_decides_a_second_done_source_does_not_keep_it_closed(tmp_path):
    """Chosen reading (10.7.3): the item stays closed only through its evidence source. Open item from file A,
    [x] in file B (evidence), a second [x] in file C (merged, evidence unchanged). Flipping C to [ ] reopens
    nothing; flipping B (the evidence) reopens the item although C still reads [x]."""
    line = f"- [ ] {LINE} by 2026-09-08"
    done = f"- [x] {LINE} by 2026-09-08"
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", line))
    write_note(folder, "2026-09-05-b.md", note("2026-09-05", done))
    write_note(folder, "2026-09-10-c.md", note("2026-09-10", done))
    db = tmp_path / "t.db"
    ingest(db, folder)
    assert statuses(db) == ["done"]
    b, c = src_id(db, "2026-09-05"), src_id(db, "2026-09-10")
    assert q(db, "SELECT completion_evidence_source_id e FROM action_item")[0]["e"] == b
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.update_item_source(c, checkbox_state="open")
            assert ing.apply_status_effects(env, 1, change(repo, c, "open")).reopened == ()
    assert statuses(db) == ["done"]
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.update_item_source(b, checkbox_state="open")
            res = ing.apply_status_effects(env, 1, change(repo, b, "open"))
    assert res.reopened == (1,) and statuses(db) == ["open"]


@pytest.mark.ac("AC-07.16")
def test_later_dated_reading_later_meeting_date_reopens_earlier_does_not(tmp_path):
    for label, new_date, reopened in (("later", date(2026, 9, 20), True), ("earlier", date(2026, 9, 10), False),
                                      ("equal", date(2026, 9, 15), True)):
        sub = tmp_path / label
        sub.mkdir()
        db, folder = quote_portal(sub)
        evidence = src_id(db, "2026-09-15")
        with session(db, folder) as (env, repo, d):
            with d.transaction():
                repo.update_item_source(evidence, checkbox_state="open")
                ctx = next(c for c in repo.list_source_context(2) if c.source.id == evidence)
                repo.update_meeting(ctx.source.meeting_id, meeting_date=new_date.isoformat())
                res = ing.apply_status_effects(
                    env, 2, EvidenceChange(evidence, "open", date(2026, 9, 15), new_date))
        assert bool(res.reopened) is reopened, label
        assert statuses(db) == (["open", "open"] if reopened else ["done", "done"]), label


@pytest.mark.ac("AC-07.16")
def test_evidence_line_deleted_keeps_items_done_without_warning(tmp_path):
    db, folder = quote_portal(tmp_path)
    evidence = src_id(db, "2026-09-15")
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.update_item_source(evidence, present=False)
            res = ing.apply_status_effects(env, 2, change(repo, evidence, "done", present=False))
            res2 = ing.apply_status_effects(env, 2, change(repo, evidence, "open", present=False))
    assert res.reopened == () and res2.reopened == ()
    assert statuses(db) == ["done", "done"]
    assert q(db, "SELECT count(*) n FROM ingest_warning WHERE code = ?", (codes.W_STATUS_REOPENED,))[0]["n"] == 0


# --------------------------------------------------------------------------------------- AC-07.16 (c)
@pytest.mark.ac("AC-07.16")
@pytest.mark.sc("SC-18")
def test_ac_07_16_c_manual_done_is_never_reopened(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {LINE} by 2026-09-04"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    own = src_id(db, "2026-09-01")
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.set_manual_done(1, "2026-09-02T10:00:00Z")
            # even a (hypothetical) evidence record and a later-dated [ ] of the same row cannot reopen it
            repo.touch_item(1, {"completion_evidence_source_id": own})
            res = ing.apply_status_effects(env, 1, EvidenceChange(own, "open", date(2026, 9, 1), date(2026, 12, 1)))
    assert res.reopened == () and not res.completed
    row = q(db, "SELECT * FROM action_item")[0]
    assert (row["status"], row["completion_origin"], row["completed_at"]) == ("done", "manual", "2026-09-02T10:00:00Z")


@pytest.mark.ac("AC-03.6")
@pytest.mark.sc("SC-18")
def test_ac_03_6_conflict_warning_once_per_run_also_for_an_unchanged_note(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {LINE} by 2026-09-04"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.set_manual_done(1)
    # unchanged-note variant: no file is touched; the whole-database pass still finds the conflict
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            assert ing.manual_done_conflicts(repo) == [1]
            assert ing.emit_status_conflicts(repo, env.run_id) == [1]
            assert ing.emit_status_conflicts(repo, env.run_id) == [1]  # once per run per item
        run_id = env.run_id
    warns = q(db, "SELECT * FROM ingest_warning WHERE code = ? AND run_id = ?", (codes.W_STATUS_CONFLICT, run_id))
    assert len(warns) == 1 and "AT-0001" in warns[0]["message"]
    assert q(db, "SELECT status, completion_origin FROM action_item")[0]["completion_origin"] == "manual"
    # the next run warns again
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            ing.emit_status_conflicts(repo, env.run_id)
        assert len(repo.list_warnings(env.run_id)) == 1


@pytest.mark.ac("AC-03.6")
def test_manual_done_with_edited_note_still_open_stays_done_and_warns(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {LINE} by 2026-09-04"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    own = src_id(db, "2026-09-01")
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.set_manual_done(1)
            repo.update_item_source(own, raw_text="- [ ] edited", description="edited")
            res = ing.apply_status_effects(env, 1, change(repo, own, "open"))
            assert ing.emit_status_conflicts(repo, env.run_id) == [1]
    assert res.reopened == () and statuses(db) == ["done"]


def test_manual_done_with_latest_done_source_emits_nothing(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [x] {LINE}"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.set_manual_done(1)
            assert ing.emit_status_conflicts(repo, env.run_id) == []
        assert repo.list_warnings(env.run_id) == []


# ------------------------------------------------------------------------------ open items and [x] edits
def test_open_item_whose_latest_source_flips_to_done_completes_with_evidence(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {LINE} by 2026-09-08"))
    write_note(folder, "2026-09-05-b.md", note("2026-09-05", f"- [ ] {LINE} by 2026-09-08"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    assert statuses(db) == ["open"]
    a, b = src_id(db, "2026-09-01"), src_id(db, "2026-09-05")
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.update_item_source(a, checkbox_state="done")  # not the latest present source: note status follows B
            assert not ing.apply_status_effects(env, 1, change(repo, a, "done")).completed
    assert statuses(db) == ["open"]
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            repo.update_item_source(b, checkbox_state="done")
            res = ing.apply_status_effects(env, 1, change(repo, b, "done"))
    assert res.completed
    row = q(db, "SELECT * FROM action_item")[0]
    assert (row["status"], row["completion_origin"], row["completed_at"], row["completion_evidence_source_id"]) == (
        "done", "note", None, b)
    ev = q(db, "SELECT * FROM item_event WHERE kind = 'status_changed'")
    assert len(ev) == 1 and "2026-09-05-b.md:" in ev[0]["after"]


def test_open_item_stays_open_for_open_restatements_and_no_change(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {LINE} by 2026-09-08"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    own = src_id(db, "2026-09-01")
    with session(db, folder) as (env, repo, d):
        with d.transaction():
            assert ing.apply_status_effects(env, 1, None) == ing.StatusEffects()
            assert ing.apply_status_effects(env, 1, change(repo, own, "open")) == ing.StatusEffects()
    assert statuses(db) == ["open"]


# ---------------------------------------------------------------------------------------------- BR-21
@pytest.mark.ac("AC-01.3")
def test_br_21_sample_done_checkboxes_import_as_done_and_leave_the_open_derivation(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    for src in sorted(FIXTURES_NOTES.iterdir()):
        if src.is_file():
            (folder / src.name).write_bytes(src.read_bytes())
    db = tmp_path / "t.db"
    ingest(db, folder)
    assert q(db, "SELECT count(*) n FROM item_source WHERE checkbox_state = 'done'")[0]["n"] == 13
    with_done = {r["item_id"] for r in q(db, "SELECT item_id FROM item_source WHERE checkbox_state = 'done'")}
    assert len(with_done) == 13
    open_ids = {r["id"] for r in q(db, "SELECT id FROM action_item WHERE status = 'open'")}
    assert not (with_done & open_ids)  # absent from the "open" derivation
    assert all(
        (r["completion_origin"], r["completed_at"]) == ("note", None)
        for r in q(db, "SELECT * FROM action_item WHERE status = 'done'")
    )
