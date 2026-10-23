"""Whole-database passes on the sample corpus (first part of T-M5-21's acceptance; T-M4-15, T-M5-14, T-M5-17).

Only the CONFIG changes between runs; no note file is touched, so every re-run reports ``unchanged 33`` and the
owner outcome can only change through the whole-database passes of spec 10.7.9 (G-08).

Hand-derived expectations (spec A.3, ``tests/golden/expected_owners.csv``): 117 sources -> 105 items; the
attendance rule (10.3 step 3a) decides 36 owner phrases (17 Priya + 19 Rahul); three of them were merged into
other items by Tier-1/1b, so the canonical ``item_owner`` state has 33 such rows (16 Priya + 17 Rahul);
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
    become resolved/alias; the 16 attendance-resolved Priya rows are re-pinned by the alias too (provenance alias;
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
    assert res.counters.resolved_via_attendance == 17  # the Rahul attendance rows only
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
    assert len(down) == 33  # the 36 phrases = 33 canonical rows (3 were merged away)
    assert Counter(c.owner_raw for c in down) == {"Rahul": 17, "Priya": 16}
    assert {c.kind for c in res.reresolution.changes} == {"downgraded_to_ambiguous"}
    assert q(db, "SELECT count(*) FROM item_source_owner WHERE provenance = 'attendee_in_meeting'")[0][0] == 0
    assert q(db, "SELECT count(*) FROM item_owner WHERE owner_status = 'ambiguous'")[0][0] == 7 + 33
    warns = [w for w in q(db, "SELECT * FROM ingest_warning WHERE run_id = ?", (res.run.run_id,))
             if w["code"] == codes.W_OWNER_NOW_AMBIGUOUS]
    assert len(warns) == 33
    assert all(w["source_file_id"] is not None and w["line"] for w in warns)
    assert res.counters.resolved_via_attendance == 0
    assert q(db, "SELECT resolved_via_attendance FROM ingest_run WHERE id = ?", (res.run.run_id,))[0][0] == 0
    # switching the setting back restores every one of them (10.3 step 6 is symmetric)
    back = ingest(db, folder, cfg)
    assert back.counters.resolved_via_attendance == 33
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
    assert res.counters.resolved_via_attendance == 33 + 13


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
    assert flagged == downgraded & expect_flag and len(flagged) == 6
    assert all(q(db, "SELECT status FROM action_item WHERE id = ?", (i,))[0][0] == "done" for i in flagged)
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
    assert len(dry.reresolution.downgrades) == 33
    assert dry.run_row.status == real.run_row.status == "partial"  # the sample has one failed file (exit 5)
    assert dry.exit_code == real.exit_code == 5
