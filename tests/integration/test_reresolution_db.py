"""T-M4-15: persisted whole-database owner re-resolution (``reresolve_database`` / ``apply_reresolution``).

Each test ingests synthetic notes, changes the config (roster / alias / attendance) and runs ONLY the
re-resolution step (``reresolve_only``) so that the DB application is isolated from the ingest engine.
"""

from __future__ import annotations

import json

import pytest

from actiontrack import codes
from actiontrack.services import ingest as ing

from tests.integration.ingest_support import write_note
from tests.integration.whole_db_support import (
    dump,
    ingest,
    make_cfg,
    owners_of,
    q,
    reresolve_only,
)

NO_ATT = "# Meeting: Sync\n\nDate: {date}\n\n## Action items\n\n{items}\n"
WITH_ATT = "# Meeting: Sync\n\nDate: {date}\nAttendees: {att}\n\n## Action items\n\n{items}\n"


def plain(date: str, *items: str) -> str:
    return NO_ATT.format(date=date, items="\n".join(items))


def with_att(date: str, att: str, *items: str) -> str:
    return WITH_ATT.format(date=date, att=att, items="\n".join(items))


def events(db, item_id, kind="owner_changed"):
    return [r for r in q(db, "SELECT * FROM item_event WHERE item_id = ? AND kind = ? ORDER BY id", (item_id, kind))]


@pytest.mark.ac("AC-05.17")
@pytest.mark.sc("SC-65")
def test_alias_resolves_ambiguous_then_removal_downgrades_again(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", plain("2026-09-01", "- [ ] Priya to send the quote by 2026-09-05"))
    db = tmp_path / "t.db"
    base = make_cfg(tmp_path)
    ingest(db, folder, base)
    (o,) = owners_of(db, 1)
    assert (o["owner_status"], o["person_id"]) == ("ambiguous", None)
    assert json.loads(o["candidates"]) == ["priya-nair", "priya-shah"]
    sources_before = dump(db, ("item_source", "item_link", "action_item"))

    pinned = make_cfg(tmp_path, aliases={"Priya Nair": ("Priya",)}, name="pinned.toml")
    applied, run_id = reresolve_only(db, pinned, folder)
    (o,) = owners_of(db, 1)
    assert (o["owner_status"], o["person_id"], o["provenance"]) == ("resolved", "priya-nair", "alias")
    assert [c.kind for c in applied.changes] == ["resolved_from_ambiguous"]
    assert len(events(db, 1)) == 1
    ev = events(db, 1)[0]
    assert json.loads(ev["before"])["owners"][0][3] == "ambiguous"
    assert json.loads(ev["after"])["owners"][0][3] == "resolved"
    src_after = dump(db, ("item_source", "item_link", "action_item"))
    assert src_after["item_source"] == sources_before["item_source"]  # byte-identical (H-05, AC-05.17)
    assert src_after["item_link"] == sources_before["item_link"]
    (so,) = q(db, "SELECT * FROM item_source_owner")
    assert (so["owner_status"], so["person_id"], so["provenance"]) == ("resolved", "priya-nair", "alias")
    # the alias is stored (roster alias) and a second pass is empty
    assert q(db, "SELECT 1 FROM person_alias WHERE person_id = 'priya-nair' AND alias_key = 'priya'")
    again, _ = reresolve_only(db, pinned, folder)
    assert again.is_empty

    removed, rid2 = reresolve_only(db, base, folder)
    (o,) = owners_of(db, 1)
    assert (o["owner_status"], o["person_id"]) == ("ambiguous", None)
    assert [c.kind for c in removed.changes] == ["downgraded_to_ambiguous"]
    assert len(events(db, 1)) == 2
    warns = [w for w in q(db, "SELECT * FROM ingest_warning WHERE run_id = ?", (rid2,)) if w["code"] == codes.W_OWNER_NOW_AMBIGUOUS]
    assert len(warns) == 1 and "2026-09-01-a.md:" in warns[0]["message"]
    assert warns[0]["line"] is not None and warns[0]["source_file_id"] is not None


@pytest.mark.ac("AC-05.10")
def test_provisional_and_attendee_person_merged_into_known_person(tmp_path):
    folder = tmp_path / "notes"
    write_note(
        folder,
        "2026-09-01-a.md",
        with_att("2026-09-01", "Priya N., Anita Desai", "- [ ] Priya N. to send the quote by 2026-09-05"),
    )
    db = tmp_path / "t.db"
    no_priya = make_cfg(tmp_path, drop=("Priya Nair", "Priya Shah"), name="no_priya.toml")
    ingest(db, folder, no_priya)
    prov = q(db, "SELECT id, origin FROM person WHERE origin != 'roster' AND identity_key LIKE 'priya n%'")
    assert prov
    (o,) = owners_of(db, 1)
    assert o["person_id"] == prov[0]["id"]

    full = make_cfg(tmp_path, name="full.toml")
    applied, run_id = reresolve_only(db, full, folder)
    assert {m.into_person_id for m in applied.merges} == {"priya-nair"}
    assert q(db, "SELECT id FROM person WHERE origin != 'roster'") == []  # no provisional / attendee row remains
    assert q(db, "SELECT 1 FROM person_alias WHERE person_id = 'priya-nair' AND alias_key = 'priya n'")
    (o,) = owners_of(db, 1)
    assert o["person_id"] == "priya-nair"
    (a,) = q(db, "SELECT * FROM meeting_attendee WHERE raw_name = 'Priya N.'")
    assert a["person_id"] == "priya-nair"
    assert [r["person_id"] for r in q(db, "SELECT person_id FROM item_source_owner")] == ["priya-nair"]
    assert len(events(db, 1)) == 1  # owner link moved from the provisional person
    assert reresolve_only(db, full, folder)[0].is_empty


def two_sam_notes(tmp_path):
    folder = tmp_path / "notes"
    item = "- [ ] Sam to freeze invoice edits by 2026-10-30"
    write_note(folder, "2026-10-14-a.md", plain("2026-10-14", item))
    write_note(folder, "2026-10-15-b.md", plain("2026-10-15", item))
    return folder


@pytest.mark.ac("AC-05.19")
@pytest.mark.sc("SC-72")
def test_tier1_merged_item_keeps_sources_when_owner_is_downgraded(tmp_path):
    folder = two_sam_notes(tmp_path)
    db = tmp_path / "t.db"
    ingest(db, folder, make_cfg(tmp_path))
    assert q(db, "SELECT count(*) FROM action_item")[0][0] == 1
    assert q(db, "SELECT count(*) FROM item_source")[0][0] == 2
    (o,) = owners_of(db, 1)
    assert (o["owner_status"], o["provenance"]) == ("resolved", "unique_first_name")
    guard = dump(db, ("item_source", "item_link"))
    status_before = q(db, "SELECT status, completion_origin, completed_at FROM action_item")[0]
    two = make_cfg(tmp_path, add=(("Sam", "Lee", ()),), name="two.toml")
    applied, run_id = reresolve_only(db, two, folder)
    assert dump(db, ("item_source", "item_link")) == guard  # H-05 guard
    assert tuple(q(db, "SELECT status, completion_origin, completed_at FROM action_item")[0]) == tuple(status_before)
    (o,) = owners_of(db, 1)
    assert o["owner_status"] == "ambiguous"
    (c,) = applied.changes
    assert c.kind == "downgraded_to_ambiguous"
    assert c.merged_or_completed_under_earlier_resolution is True
    (w,) = [x for x in q(db, "SELECT * FROM ingest_warning WHERE run_id = ?", (run_id,)) if x["code"] == codes.W_OWNER_NOW_AMBIGUOUS]
    assert "merged/completed under an earlier owner resolution" in w["message"]
    assert json.loads(events(db, 1)[-1]["after"])["note"] == ing.H05_NOTE
    # both per-source rows were downgraded (same context: no attendee list)
    assert {r["owner_status"] for r in q(db, "SELECT owner_status FROM item_source_owner")} == {"ambiguous"}


@pytest.mark.ac("AC-05.19")
@pytest.mark.sc("SC-72")
def test_tier1b_completed_item_stays_done_when_owner_is_downgraded(tmp_path):
    folder = tmp_path / "notes"
    line = "Sam to send the revised quote to Acme"
    write_note(folder, "2026-09-01-a.md", plain("2026-09-01", f"- [ ] {line} by 2026-09-04"))
    write_note(folder, "2026-09-15-b.md", plain("2026-09-15", f"- [x] {line}"))
    db = tmp_path / "t.db"
    ingest(db, folder, make_cfg(tmp_path))
    assert [r["status"] for r in q(db, "SELECT status FROM action_item ORDER BY id")] == ["done"]
    guard = dump(db, ("item_source", "item_link"))
    two = make_cfg(tmp_path, add=(("Sam", "Lee", ()),), name="two.toml")
    applied, _ = reresolve_only(db, two, folder)
    assert dump(db, ("item_source", "item_link")) == guard
    assert [r["status"] for r in q(db, "SELECT status FROM action_item ORDER BY id")] == ["done"]
    assert applied.changes and all(c.merged_or_completed_under_earlier_resolution for c in applied.changes)
    assert {o["owner_status"] for i in (1,) for o in owners_of(db, i)} == {"ambiguous"}


def test_namesake_candidates_follow_a_growing_ambiguity(tmp_path):
    """ambiguous -> ambiguous with MORE candidates: item row updated (candidates), no status change."""
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", plain("2026-09-01", "- [ ] Priya to send the quote by 2026-09-05"))
    db = tmp_path / "t.db"
    ingest(db, folder, make_cfg(tmp_path))
    three = make_cfg(tmp_path, add=(("Priya", "Rao", ()),), name="three.toml")
    applied, _ = reresolve_only(db, three, folder)
    (o,) = owners_of(db, 1)
    assert json.loads(o["candidates"]) == ["priya-nair", "priya-rao", "priya-shah"]
    assert [c.kind for c in applied.changes] == ["changed"]
    assert applied.warnings == ()
    assert reresolve_only(db, three, folder)[0].is_empty


def test_second_pass_writes_nothing(tmp_path):
    folder = two_sam_notes(tmp_path)
    db = tmp_path / "t.db"
    ingest(db, folder, make_cfg(tmp_path))
    two = make_cfg(tmp_path, add=(("Sam", "Lee", ()),), name="two.toml")
    first, _ = reresolve_only(db, two, folder)
    assert not first.is_empty
    snap = dump(db)
    second, _ = reresolve_only(db, two, folder)
    assert second.is_empty
    assert dump(db) == snap


def test_updated_at_moves_only_for_items_whose_owner_changed(tmp_path):
    folder = tmp_path / "notes"
    write_note(
        folder,
        "2026-09-01-a.md",
        plain("2026-09-01", "- [ ] Sam to send the quote by 2026-09-05", "- [ ] Anita to send the deck by 2026-09-05"),
    )
    db = tmp_path / "t.db"
    ingest(db, folder, make_cfg(tmp_path))
    before = {r["id"]: r["updated_at"] for r in q(db, "SELECT id, updated_at FROM action_item")}
    # a later clock would show a bump; reresolve_only uses FIXED_NOW, so compare through a changed roster only:
    two = make_cfg(tmp_path, add=(("Sam", "Lee", ()),), name="two.toml")
    reresolve_only(db, two, folder)
    after = {r["id"]: r["updated_at"] for r in q(db, "SELECT id, updated_at FROM action_item")}
    assert after[2] == before[2]  # Anita untouched (not even the timestamp)
    assert {o["owner_status"] for o in owners_of(db, 1)} == {"ambiguous"}
    assert {o["owner_status"] for o in owners_of(db, 2)} == {"resolved"}


@pytest.mark.ac("AC-05.18")
def test_each_source_is_re_resolved_in_its_own_meeting_context(tmp_path):
    """Decision (section 6a comment): every source phrase is re-resolved in ITS OWN meeting context. Note A has an
    attendee list (only Priya Shah attends), note B has none, so the two items differ and re-resolution leaves both
    alone (attendance for A, ambiguous for B) instead of applying one context to both."""
    folder = tmp_path / "notes"
    item = "- [ ] Priya to freeze invoice edits by 2026-10-30"
    write_note(folder, "2026-10-14-a.md", with_att("2026-10-14", "Priya Shah, Anita Desai", item))
    write_note(folder, "2026-10-15-b.md", plain("2026-10-15", item))
    db = tmp_path / "t.db"
    cfg = make_cfg(tmp_path)
    ingest(db, folder, cfg)
    rows = q(db, "SELECT f.rel_path, so.owner_status, so.provenance FROM item_source s JOIN source_file f ON f.id = s.source_file_id JOIN item_source_owner so ON so.item_source_id = s.id ORDER BY f.rel_path")
    assert [(r["rel_path"][:12], r["owner_status"], r["provenance"]) for r in rows] == [
        ("2026-10-14-a", "resolved", "attendee_in_meeting"),
        ("2026-10-15-b", "ambiguous", "none"),
    ]
    snap = dump(db)
    assert reresolve_only(db, cfg, folder)[0].is_empty  # idempotent with mixed contexts
    assert dump(db) == snap
