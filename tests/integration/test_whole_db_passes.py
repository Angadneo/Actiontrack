"""Whole-database passes on the sample corpus (first part of T-M5-21's acceptance; T-M4-15, T-M5-14, T-M5-17).

Only the CONFIG changes between runs; no note file is touched, so every re-run reports ``unchanged 33`` and the
owner outcome can only change through the whole-database passes of spec 10.7.9 (G-08).

Hand-derived expectations (spec A.3, ``tests/golden/expected_owners.csv``): 117 sources -> 103 items; the
attendance rule (10.3 step 3a) decides 36 owner phrases (17 Priya + 19 Rahul); five of them were merged into
other items by Tier-1/1b, so the canonical ``item_owner`` state has 31 such rows (15 Priya + 16 Rahul);
7 items are ambiguous (4 Priya, 3 Rahul).
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.services import ingest as ing

from tests.integration.whole_db_support import copy_sample, dump, ingest, make_cfg, q

PRIYA_NAIR = "priya-nair"
BUSINESS_GUARD = ("item_source", "item_link")


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    root = tmp_path_factory.mktemp("whole_db")
    folder = copy_sample(root)
    cfg = make_cfg(root)
    db = root / "base.db"
    first = ingest(db, folder, cfg)
    assert first.finalisation_error is None
    return db, folder, cfg, root


@pytest.fixture
def fresh(base, tmp_path):
    """A private copy of the baseline database (the notes folder is shared and never written)."""
    db, folder, cfg, root = base
    copy = tmp_path / "t.db"
    shutil.copyfile(db, copy)
    return copy, folder, cfg, tmp_path


def item_state(db: Path) -> dict[str, list[tuple]]:
    return {
        "status": [tuple(r) for r in q(db, "SELECT id, status, completion_origin, completed_at FROM action_item ORDER BY id")],
        "source": dump(db, ("item_source",))["item_source"],
        "links": dump(db, ("item_link",))["item_link"],
    }


@pytest.mark.ac("AC-05.17")
@pytest.mark.sc("SC-65")
def test_roster_alias_settles_ambiguous_priya_items_on_an_unchanged_folder(fresh):
    """Spec 10.3 step 1: a roster alias hit pins the bare token ``Priya`` to ONE person (alias provenance) and wins
    over the namesake rules of step 3. Adding ``Priya`` as an alias of Priya Nair is a legitimate roster edit (it
    never pins two people). Consequences on the sample: the 4 ambiguous Priya items (09-17, 09-23, 09-30, untitled)
    become resolved/alias; the 15 attendance-resolved Priya rows are re-pinned by the alias too (provenance alias;
    those that had been Priya Shah move to Priya Nair, each with an owner_changed event); Rahul is untouched."""
    db, folder, cfg, tmp = fresh
    before_ambiguous = q(db, "SELECT item_id FROM item_owner WHERE owner_status = 'ambiguous' AND owner_raw = 'Priya' ORDER BY item_id")
    assert len(before_ambiguous) == 4
    guard = dump(db, BUSINESS_GUARD)
    pinned = make_cfg(tmp, aliases={"Priya Nair": ("Priya",)}, name="pinned.toml")
    res = ingest(db, folder, pinned)
    assert res.counters.files_unchanged == 33 and res.counters.files_ingested == 0  # "unchanged 33"
    assert dump(db, BUSINESS_GUARD) == guard
    for row in before_ambiguous:
        (o,) = q(db, "SELECT * FROM item_owner WHERE item_id = ?", (row["item_id"],))
        assert (o["owner_status"], o["person_id"], o["provenance"]) == ("resolved", PRIYA_NAIR, "alias")
        evs = q(db, "SELECT * FROM item_event WHERE item_id = ? AND kind = 'owner_changed' AND run_id = ?",
                (row["item_id"], res.run.run_id))
        assert len(evs) == 1
    assert q(db, "SELECT count(*) FROM item_owner WHERE owner_raw = 'Priya' AND provenance != 'alias'")[0][0] == 0
    assert q(db, "SELECT count(*) FROM item_owner WHERE owner_status = 'ambiguous'")[0][0] == 3  # only Rahul left
    assert {r["owner_raw"] for r in q(db, "SELECT owner_raw FROM item_owner WHERE owner_status = 'ambiguous'")} == {"Rahul"}
    assert res.counters.resolved_via_attendance == 16  # the Rahul attendance rows only
    again = ingest(db, folder, pinned)
    assert again.reresolution.is_empty


@pytest.mark.ac("AC-05.8")
@pytest.mark.sc("SC-65")
def test_attendee_disambiguation_off_downgrades_every_attendance_row_on_an_unchanged_folder(fresh):
    db, folder, cfg, tmp = fresh
    assert q(db, "SELECT count(*) FROM item_source_owner WHERE provenance = 'attendee_in_meeting'")[0][0] == 36
    off = make_cfg(tmp, attendance=False, name="off.toml")
    res = ingest(db, folder, off)
    assert res.counters.files_unchanged == 33
    down = res.reresolution.downgrades
    assert len(down) == 31  # the 36 phrases = 31 canonical rows (5 were merged away)
    assert Counter(c.owner_raw for c in down) == {"Rahul": 16, "Priya": 15}
    assert {c.kind for c in res.reresolution.changes} == {"downgraded_to_ambiguous"}
    assert q(db, "SELECT count(*) FROM item_source_owner WHERE provenance = 'attendee_in_meeting'")[0][0] == 0
    assert q(db, "SELECT count(*) FROM item_owner WHERE owner_status = 'ambiguous'")[0][0] == 7 + 31
    warns = [w for w in q(db, "SELECT * FROM ingest_warning WHERE run_id = ?", (res.run.run_id,))
             if w["code"] == codes.W_OWNER_NOW_AMBIGUOUS]
    assert len(warns) == 31
    assert all(w["source_file_id"] is not None and w["line"] for w in warns)
    assert res.counters.resolved_via_attendance == 0
    assert q(db, "SELECT resolved_via_attendance FROM ingest_run WHERE id = ?", (res.run.run_id,))[0][0] == 0
    # switching the setting back restores every one of them (10.3 step 6 is symmetric)
    back = ingest(db, folder, cfg)
    assert back.counters.resolved_via_attendance == 31
    assert {c.kind for c in back.reresolution.changes} == {"resolved_from_ambiguous"}


@pytest.mark.ac("AC-05.18")
def test_second_sam_makes_only_the_no_attendee_list_rows_ambiguous(fresh):
    db, folder, cfg, tmp = fresh
    assert q(db, "SELECT count(*) FROM item_owner WHERE owner_raw = 'Sam' AND provenance = 'unique_first_name'")[0][0] == 15
    two = make_cfg(tmp, add=(("Sam", "Lee", ()),), name="two_sams.toml")
    res = ingest(db, folder, two)
    ambiguous = q(
        db,
        "SELECT f.rel_path, s.line_start FROM item_owner o JOIN item_source s ON s.item_id = o.item_id "
        "JOIN source_file f ON f.id = s.source_file_id WHERE o.owner_raw = 'Sam' AND o.owner_status = 'ambiguous' "
        "ORDER BY f.rel_path",
    )
    assert [(r["rel_path"], r["line_start"]) for r in ambiguous] == [("2026-09-23-standup.md", 9), ("untitled.md", 1)]
    rest = q(db, "SELECT provenance, owner_status FROM item_owner WHERE owner_raw = 'Sam' AND owner_status != 'ambiguous'")
    assert len(rest) == 13 and {(r[0], r[1]) for r in rest} == {("attendee_in_meeting", "resolved")}
    kinds = Counter(c.kind for c in res.reresolution.changes)
    assert kinds == {"changed": 13, "downgraded_to_ambiguous": 2}
    assert res.counters.resolved_via_attendance == 31 + 13


@pytest.mark.ac("AC-05.19")
@pytest.mark.sc("SC-72")
def test_merges_and_completions_survive_the_downgrade(fresh):
    db, folder, cfg, tmp = fresh
    state = item_state(db)
    merged_or_completed_sql = (
        "SELECT DISTINCT item_id FROM item_source GROUP BY item_id HAVING count(*) > 1 "
        "UNION SELECT from_item_id FROM item_link WHERE kind = 'completed_by' "
        "UNION SELECT to_item_id FROM item_link WHERE kind = 'completed_by'"
    )
    expect_flag = {r[0] for r in q(db, merged_or_completed_sql)}
    res = ingest(db, folder, make_cfg(tmp, attendance=False, name="off.toml"))
    assert item_state(db) == state  # sources, links, status and completion untouched
    flagged = {c.item_id for c in res.reresolution.changes if c.merged_or_completed_under_earlier_resolution}
    downgraded = {c.item_id for c in res.reresolution.downgrades}
    assert flagged == downgraded & expect_flag and len(flagged) == 8
    # 6 completed items (Tier-1b) + the 2 OPEN items merged by the dateless-restatement rule (amended D-04)
    by_status = Counter(q(db, "SELECT status FROM action_item WHERE id = ?", (i,))[0][0] for i in flagged)
    assert by_status == {"done": 6, "open": 2}
    msgs = {w[1]: w[2] for w in res.reresolution.warnings if w[0] == codes.W_OWNER_NOW_AMBIGUOUS}
    for item_id in downgraded:
        assert ("merged/completed under an earlier owner resolution" in msgs[item_id]) == (item_id in flagged)
    for item_id in flagged:
        ev = q(db, "SELECT after FROM item_event WHERE item_id = ? AND kind = 'owner_changed' AND run_id = ?",
               (item_id, res.run.run_id))
        assert json.loads(ev[0]["after"])["note"] == ing.H05_NOTE
        assert q(db, "SELECT count(*) FROM item_source WHERE item_id = ?", (item_id,))[0][0] >= 1


@pytest.mark.ac("AC-08.1")
def test_second_run_over_unchanged_files_and_config_changes_nothing(fresh):
    db, folder, cfg, tmp = fresh
    before = dump(db)
    res = ingest(db, folder, cfg)
    assert res.reresolution.is_empty and res.conflicts == ()
    after = dump(db)
    assert after == before  # all business tables identical, including item_event (no events at all)


@pytest.mark.ac("AC-01.10")
@pytest.mark.sc("SC-40")
def test_dry_run_after_a_config_change_is_byte_identical_and_reports_what_a_real_run_does(fresh):
    db, folder, cfg, tmp = fresh
    off = make_cfg(tmp, attendance=False, name="off.toml")
    digest = db.read_bytes()
    listing = sorted(p.name for p in tmp.iterdir())
    dry = ingest(db, folder, off, dry_run=True)
    assert db.read_bytes() == digest
    assert sorted(p.name for p in tmp.iterdir()) == listing
    assert not Path(f"{db}.lock").exists()
    real = ingest(db, folder, off)
    assert dry.counters == real.counters
    assert dry.reresolution == real.reresolution
    assert len(dry.reresolution.downgrades) == 31
    assert dry.run_row.status == real.run_row.status == "partial"  # the sample has one failed file (exit 5)
    assert dry.exit_code == real.exit_code == 5


# ====================================================================================================================
# T-M5-21 (second part): small synthetic fixtures under ``tests/fixtures/whole_db/`` -- config / roster changes on an
# UNCHANGED folder, merges and completions that survive an owner downgrade, W-STATUS-CONFLICT on an unchanged note.
# ====================================================================================================================
from tests.integration.ingest_support import open_repo  # noqa: E402
from tests.integration.whole_db_support import copy_scenario, file_stamp  # noqa: E402

OWNER_TABLES = ("item_owner", "item_source_owner")
NOTE_GUARD = ("item_source", "source_file", "meeting", "meeting_attendee")


def item_id(db: Path, like: str) -> int:
    (row,) = q(db, "SELECT id FROM action_item WHERE description LIKE ?", (f"%{like}%",))
    return row[0]


def warnings_of(db: Path, run_id: int, code: str) -> list[str]:
    return [r["message"] for r in q(db, "SELECT message FROM ingest_warning WHERE run_id = ? AND code = ?", (run_id, code))]


def owner_of(db: Path, item: int) -> tuple:
    (o,) = q(db, "SELECT owner_status, person_id, provenance, candidates FROM item_owner WHERE item_id = ?", (item,))
    return tuple(o)


def manual_done(db: Path, item: int) -> None:
    d, repo = open_repo(db)
    try:
        with d.transaction():
            repo.set_manual_done(item)
    finally:
        d.close()


@pytest.mark.ac("AC-05.17")
@pytest.mark.sc("SC-65")
def test_alias_settles_an_ambiguous_owner_on_an_unchanged_note_and_removing_it_restores_the_ambiguity(tmp_path):
    folder = copy_scenario(tmp_path, "ambiguous_priya")
    db = tmp_path / "a.db"
    base = make_cfg(tmp_path)
    first = ingest(db, folder, base)
    assert first.counters.files_ingested == 1
    priya, rahul = item_id(db, "revised quote"), item_id(db, "login screen")
    initial_owner = owner_of(db, priya)
    assert initial_owner == ("ambiguous", None, "none", '["priya-nair","priya-shah"]')  # no attendee list: step 3 fails
    initial_owners = dump(db, OWNER_TABLES)
    stamp, guard = file_stamp(folder), dump(db, NOTE_GUARD)

    pinned = make_cfg(tmp_path, aliases={"Priya Nair": ("Priya",)}, name="pinned.toml")
    res = ingest(db, folder, pinned)
    # the unchanged note is NOT re-ingested ...
    c = res.counters
    assert (c.files_unchanged, c.files_ingested, c.items_new, c.items_merged, c.items_updated) == (1, 0, 0, 0, 0)
    assert dump(db, NOTE_GUARD) == guard  # every ItemSource / file / meeting row identical (last_seen_run included)
    assert file_stamp(folder) == stamp  # ... and no note file was touched (sha256, size, mtime)
    # ... yet the owner changed: resolved by the alias, one owner_changed event, nothing else
    assert owner_of(db, priya) == ("resolved", "priya-nair", "alias", "[]")
    evs = q(db, "SELECT item_id FROM item_event WHERE run_id = ? AND kind = 'owner_changed'", (res.run.run_id,))
    assert [e["item_id"] for e in evs] == [priya]
    assert [ch.item_id for ch in res.reresolution.changes] == [priya]
    assert warnings_of(db, res.run.run_id, codes.W_OWNER_NOW_AMBIGUOUS) == []
    assert q(db, "SELECT count(*) FROM item_event WHERE item_id = ? AND run_id = ?", (rahul, res.run.run_id))[0][0] == 0
    assert ingest(db, folder, pinned).reresolution.is_empty  # a settled owner is stable

    back = ingest(db, folder, base)  # remove the alias again
    assert back.counters.files_unchanged == 1 and back.counters.files_ingested == 0
    assert owner_of(db, priya) == initial_owner
    assert dump(db, OWNER_TABLES) == initial_owners  # idempotent in both directions: the original owner state is back
    (warn,) = warnings_of(db, back.run.run_id, codes.W_OWNER_NOW_AMBIGUOUS)
    assert warn.startswith("2026-09-01-sync.md:5:")
    evs = q(db, "SELECT item_id FROM item_event WHERE run_id = ? AND kind = 'owner_changed'", (back.run.run_id,))
    assert [e["item_id"] for e in evs] == [priya]
    assert dump(db, NOTE_GUARD) == guard and file_stamp(folder) == stamp
    final = ingest(db, folder, base)
    assert final.reresolution.is_empty and warnings_of(db, final.run.run_id, codes.W_OWNER_NOW_AMBIGUOUS) == []


@pytest.mark.ac("AC-05.17")
@pytest.mark.sc("SC-65")
def test_dry_run_with_a_new_alias_reports_what_the_real_run_does_and_writes_nothing(tmp_path):
    folder = copy_scenario(tmp_path, "ambiguous_priya")
    db = tmp_path / "d.db"
    ingest(db, folder, make_cfg(tmp_path))
    pinned = make_cfg(tmp_path, aliases={"Priya Nair": ("Priya",)}, name="pinned.toml")
    digest, listing = db.read_bytes(), sorted(p.name for p in tmp_path.iterdir())
    dry = ingest(db, folder, pinned, dry_run=True)
    assert db.read_bytes() == digest and sorted(p.name for p in tmp_path.iterdir()) == listing
    assert [(c.item_id, c.kind) for c in dry.reresolution.changes] == [(1, "resolved_from_ambiguous")]
    real = ingest(db, folder, pinned)
    assert dry.reresolution == real.reresolution and dry.counters == real.counters


@pytest.mark.ac("AC-05.8")
@pytest.mark.sc("SC-65")
def test_attendee_disambiguation_switch_round_trip_restores_the_exact_owner_state(fresh):
    """AC-05.8 on an existing database: off then on again gives back every owner row of the baseline (both directions
    are plain whole-database passes over an unchanged folder) and a further run is a no-op."""
    db, folder, cfg, tmp = fresh
    owners_before = dump(db, OWNER_TABLES)
    guard = dump(db, NOTE_GUARD)
    off = ingest(db, folder, make_cfg(tmp, attendance=False, name="off.toml"))
    assert off.counters.files_unchanged == 33 and dump(db, OWNER_TABLES) != owners_before
    on = ingest(db, folder, cfg)
    assert on.counters.files_unchanged == 33
    assert dump(db, OWNER_TABLES) == owners_before
    assert dump(db, NOTE_GUARD) == guard
    assert ingest(db, folder, cfg).reresolution.is_empty


@pytest.mark.ac("AC-05.19")
@pytest.mark.sc("SC-72")
def test_tier1b_completion_survives_a_namesake_joining_the_roster(tmp_path):
    """(i) 09-01 / 09-08 revised-quote items completed by the 09-15 `[x]`; then a third Priya joins the roster so
    `Priya` / `Priya N.` become ambiguous. Nothing is reopened, split or unlinked; only the owner link changes."""
    folder = copy_scenario(tmp_path, "revised_quote")
    db = tmp_path / "t1b.db"
    solo = make_cfg(tmp_path, people=(("Priya", "Nair", ()), ("Rahul", "Mehta", ())), name="solo.toml")
    first = ingest(db, folder, solo)
    assert (first.counters.items_new, first.counters.items_merged) == (2, 1)
    items = [r["id"] for r in q(db, "SELECT id FROM action_item ORDER BY id")]
    assert len(items) == 2 and {r["status"] for r in q(db, "SELECT status FROM action_item")} == {"done"}
    assert q(db, "SELECT count(*) FROM item_source")[0][0] == 3
    assert len(q(db, "SELECT * FROM item_link WHERE kind = 'completed_by'")) == 1  # inferred item -> evidence holder
    state = item_state(db)
    assert all(owner_of(db, i)[:2] == ("resolved", "priya-nair") for i in items)

    wider = make_cfg(
        tmp_path, people=(("Priya", "Nair", ()), ("Rahul", "Mehta", ()), ("Priya", "Narang", ())), name="wide.toml"
    )
    res = ingest(db, folder, wider)
    assert res.counters.files_unchanged == 3 and res.counters.files_ingested == 0
    assert item_state(db) == state  # status, completion origin/time, ItemSource rows, completed_by link: untouched
    for i in items:
        status, person, _prov, cands = owner_of(db, i)
        assert (status, person, cands) == ("ambiguous", None, '["priya-nair","priya-narang"]')
        (ev,) = q(db, "SELECT after FROM item_event WHERE item_id = ? AND kind = 'owner_changed' AND run_id = ?",
                  (i, res.run.run_id))
        assert json.loads(ev["after"])["note"] == ing.H05_NOTE
    assert {c.item_id for c in res.reresolution.changes if c.merged_or_completed_under_earlier_resolution} == set(items)
    msgs = warnings_of(db, res.run.run_id, codes.W_OWNER_NOW_AMBIGUOUS)
    assert len(msgs) == 2 and all("merged/completed under an earlier owner resolution" in m for m in msgs)
    assert q(db, "SELECT count(*) FROM item_event WHERE run_id = ? AND kind IN ('status_changed', 'source_removed', 'merged')",
             (res.run.run_id,))[0][0] == 0
    again = ingest(db, folder, wider)
    assert again.reresolution.is_empty and item_state(db) == state

    # a person who vanishes from the roster file stays a known person (``Repo.upsert_roster``: history), so dropping
    # the namesake from the config does not silently re-resolve the items (nothing is undone, H-05) ...
    back = ingest(db, folder, solo)
    assert back.reresolution.is_empty and item_state(db) == state
    assert all(owner_of(db, i)[0] == "ambiguous" for i in items)
    # ... but an explicit alias settles them again, still without reopening or splitting anything
    pinned = make_cfg(
        tmp_path,
        people=(("Priya", "Nair", ()), ("Rahul", "Mehta", ()), ("Priya", "Narang", ())),
        aliases={"Priya Nair": ("Priya", "Priya N.")},
        name="pinned.toml",
    )
    settled = ingest(db, folder, pinned)
    assert {c.kind for c in settled.reresolution.changes} == {"resolved_from_ambiguous"}
    assert item_state(db) == state
    assert all(owner_of(db, i)[:3] == ("resolved", "priya-nair", "alias") for i in items)


@pytest.mark.ac("AC-05.19")
@pytest.mark.sc("SC-72")
def test_tier1_merge_keeps_both_sources_when_a_namesake_attendee_arrives(tmp_path):
    """(ii) two sources of one `unique_first_name` owner merged by Tier-1 in a run without any roster; a later note
    brings a namesake attendee. The item keeps both sources (no split), its owner turns ambiguous, nothing reopens."""
    names = ("2026-09-01-billing-sync.md", "2026-09-08-billing-weekly.md")
    folder = copy_scenario(tmp_path, "tier1_namesake", names=names)
    db = tmp_path / "t1.db"
    nobody = make_cfg(tmp_path, people=(), name="nobody.toml")
    first = ingest(db, folder, nobody)
    assert (first.counters.items_new, first.counters.items_merged) == (1, 1)
    freeze = item_id(db, "freeze invoice")
    sources_before = q(db, "SELECT * FROM item_source WHERE item_id = ? ORDER BY id", (freeze,))
    assert len(sources_before) == 2
    assert owner_of(db, freeze)[:3] == ("resolved", "priya-nair", "unique_first_name")
    status_before = tuple(q(db, "SELECT status, completion_origin, completed_at FROM action_item WHERE id = ?", (freeze,))[0])

    late = "later-2026-09-15-ops-review.md"
    shutil.copyfile(copy_scenario(tmp_path, "tier1_namesake", names=(late,), dest="x") / late, folder / late)
    res = ingest(db, folder, nobody)
    assert (res.counters.files_ingested, res.counters.files_unchanged) == (1, 2)
    assert [tuple(r) for r in q(db, "SELECT * FROM item_source WHERE item_id = ? ORDER BY id", (freeze,))] == [
        tuple(r) for r in sources_before
    ]  # both sources, untouched: no split
    assert q(db, "SELECT count(*) FROM action_item")[0][0] == 2  # the merged item + the new note's own item
    assert owner_of(db, freeze) == ("ambiguous", None, "none", '["priya-nair","priya-shah"]')
    assert tuple(q(db, "SELECT status, completion_origin, completed_at FROM action_item WHERE id = ?", (freeze,))[0]) == status_before
    (change,) = [c for c in res.reresolution.changes if c.item_id == freeze]
    assert change.merged_or_completed_under_earlier_resolution
    (msg,) = warnings_of(db, res.run.run_id, codes.W_OWNER_NOW_AMBIGUOUS)
    assert "merged/completed under an earlier owner resolution" in msg
    (ev,) = q(db, "SELECT after FROM item_event WHERE item_id = ? AND kind = 'owner_changed' AND run_id = ?",
              (freeze, res.run.run_id))
    assert json.loads(ev["after"])["note"] == ing.H05_NOTE
    assert q(db, "SELECT count(*) FROM item_event WHERE item_id = ? AND run_id = ? AND kind IN ('source_removed', 'merged')",
             (freeze, res.run.run_id))[0][0] == 0
    assert ingest(db, folder, nobody).reresolution.is_empty


@pytest.mark.ac("AC-03.6")
@pytest.mark.sc("SC-18")
def test_manual_done_with_an_open_checkbox_in_an_unchanged_note_warns_on_every_run(tmp_path):
    folder = copy_scenario(tmp_path, "status_conflict")
    db = tmp_path / "c.db"
    cfg = make_cfg(tmp_path)
    first = ingest(db, folder, cfg)
    assert first.conflicts == () and warnings_of(db, first.run.run_id, codes.W_STATUS_CONFLICT) == []
    target, other = item_id(db, "login screen"), item_id(db, "wireframes")
    manual_done(db, target)
    stamp, guard = file_stamp(folder), dump(db, NOTE_GUARD)
    for _ in (1, 2):  # on EVERY run: one warning, the status stays done
        res = ingest(db, folder, cfg)
        assert res.counters.files_unchanged == 1 and res.counters.files_ingested == 0
        assert res.conflicts == (target,)
        (msg,) = warnings_of(db, res.run.run_id, codes.W_STATUS_CONFLICT)
        assert "AT-%04d" % target in msg  # the warning names the item (Appendix B template)
        (row,) = q(db, "SELECT status, completion_origin FROM action_item WHERE id = ?", (target,))
        assert (row["status"], row["completion_origin"]) == ("done", "manual")
        assert q(db, "SELECT status FROM action_item WHERE id = ?", (other,))[0][0] == "open"  # only the manual one
    assert dump(db, NOTE_GUARD) == guard and file_stamp(folder) == stamp
    digest = db.read_bytes()  # a dry run shows the same conflict and writes nothing
    dry = ingest(db, folder, cfg, dry_run=True)
    assert dry.conflicts == (target,) and db.read_bytes() == digest
    before = dump(db)  # the warning is advisory: business tables never move between runs
    ingest(db, folder, cfg)
    assert dump(db) == before


@pytest.mark.ac("AC-03.6")
def test_manual_done_survives_an_unrelated_edit_of_the_note_and_still_warns(tmp_path):
    folder = copy_scenario(tmp_path, "status_conflict")
    db = tmp_path / "e.db"
    cfg = make_cfg(tmp_path)
    ingest(db, folder, cfg)
    target = item_id(db, "login screen")
    manual_done(db, target)
    note = folder / "2026-09-01-standup.md"
    note.write_bytes(note.read_bytes() + b"- [ ] Sam Okafor to book the offsite venue by 2026-09-09\n")
    res = ingest(db, folder, cfg)
    assert res.counters.files_ingested == 1
    assert res.conflicts == (target,)
    (row,) = q(db, "SELECT status, completion_origin FROM action_item WHERE id = ?", (target,))
    assert (row["status"], row["completion_origin"]) == ("done", "manual")
