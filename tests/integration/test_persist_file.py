"""T-M5-07: persisting a newly seen file (meeting, attendees, items, owners, source anchors); no matching yet."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import unicodedata
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.services import ingest as ing

from tests.conftest import REPO_ROOT
from tests.integration.ingest_support import dump_tables, roster_cfg, run_ctx, simple_note, write_note

GOLDEN_OWNERS = REPO_ROOT / "tests" / "golden" / "expected_owners.csv"


def ingest(db_path, folder, *, today="2026-10-22", **kw):
    return ing.run_ingest(db_path, folder, cfg=kw.pop("cfg", None) or roster_cfg(), run_ctx=run_ctx(today), **kw)


def q(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def warnings_of(db_path: Path, rel_path: str | None = None) -> list[tuple[str, int | None]]:
    sql = (
        "SELECT w.code, w.line FROM ingest_warning w LEFT JOIN source_file f ON f.id = w.source_file_id "
        + ("WHERE f.rel_path = ? " if rel_path else "")
        + "ORDER BY w.id"
    )
    return [(r["code"], r["line"]) for r in q(db_path, sql, (rel_path,) if rel_path else ())]


def items_of(db_path: Path, rel_path: str):
    return q(
        db_path,
        "SELECT i.*, s.line_start, s.line_end, s.raw_text, s.section_heading, s.checkbox_state, "
        "s.occurrence_index, s.block_hash, s.present, s.first_seen_run, s.last_seen_run, "
        "s.description AS s_description, s.task_text AS s_task_text, s.core_key AS s_core_key, "
        "s.due_raw AS s_due_raw, s.due_date AS s_due_date, s.due_confidence AS s_due_confidence, s.id AS source_id "
        "FROM action_item i JOIN item_source s ON s.item_id = i.id JOIN source_file f ON f.id = s.source_file_id "
        "WHERE f.rel_path = ? ORDER BY s.line_start, i.id",
        (rel_path,),
    )


def owners_of(db_path: Path, item_id: int):
    return q(db_path, "SELECT * FROM item_owner WHERE item_id = ? ORDER BY ordinal", (item_id,))


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    return root


# ------------------------------------------------------------------------- sample files in isolation
@pytest.mark.ac("AC-01.2", "AC-06.6")
@pytest.mark.sc("SC-01")
def test_kickoff_note_in_isolation(tmp_db_path, copy_fixture_notes):
    folder = copy_fixture_notes(names={"2026-09-01-quote-portal-kickoff.md"})
    res = ingest(tmp_db_path, folder)
    assert res.counters.files_ingested == 1 and res.counters.items_new == 4
    rel = "2026-09-01-quote-portal-kickoff.md"
    rows = items_of(tmp_db_path, rel)
    assert [r["line_start"] for r in rows] == [9, 10, 11, 12]
    first = rows[0]
    assert first["section_heading"] == "Action items"
    assert (first["due_date"], first["due_confidence"]) == ("2026-09-04", "relative")
    assert first["source_state"] == "present" and first["status"] == "open"
    (own,) = owners_of(tmp_db_path, first["id"])
    assert (own["person_id"], own["owner_status"], own["provenance"], own["role"]) == (
        "priya-nair", "resolved", "attendee_in_meeting", "owner")
    assert own["owner_raw"] == "Priya"
    meeting = q(tmp_db_path, "SELECT m.* FROM meeting m JOIN source_file f ON f.id = m.source_file_id WHERE f.rel_path = ?", (rel,))[0]
    assert (meeting["meeting_date"], meeting["date_source"], meeting["title"]) == (
        "2026-09-01", "date_line", "Quote portal kickoff")
    atts = q(tmp_db_path, "SELECT raw_name, person_id FROM meeting_attendee WHERE meeting_id = ? ORDER BY raw_name", (meeting["id"],))
    assert [a["raw_name"] for a in atts] == ["Anita Desai", "Priya Nair", "Rahul Mehta", "Sam Okafor"]
    assert all(a["person_id"] for a in atts)
    sf = q(tmp_db_path, "SELECT * FROM source_file WHERE rel_path = ?", (rel,))[0]
    assert sf["status"] == "ok" and sf["last_ingested_run"] == res.run_row.id and sf["content_sha256"]
    # a 'created' event per item, stamped by the injected clock
    ev = q(tmp_db_path, "SELECT * FROM item_event WHERE item_id = ?", (first["id"],))
    assert [e["kind"] for e in ev] == ["created"] and ev[0]["ts"] == "2026-10-22T09:30:00Z"
    assert ev[0]["run_id"] == res.run_row.id


@pytest.mark.ac("AC-01.3")
def test_checkbox_done_gives_done_item_with_note_origin(tmp_db_path, copy_fixture_notes):
    folder = copy_fixture_notes(names={"2026-09-08-quote-portal-weekly.md"})
    ingest(tmp_db_path, folder)
    rows = items_of(tmp_db_path, "2026-09-08-quote-portal-weekly.md")
    done = [r for r in rows if r["line_start"] == 12]
    assert len(done) == 1
    r = done[0]
    assert (r["status"], r["completion_origin"], r["completed_at"]) == ("done", "note", None)
    assert r["checkbox_state"] == "done"
    assert r["completion_evidence_source_id"] == r["source_id"]  # evidence = that source (G-C21)
    assert [x["status"] for x in rows].count("open") == 3


@pytest.mark.ac("AC-01.9", "AC-06.7")
@pytest.mark.sc("SC-49", "SC-50")
def test_undated_notes_keep_items_with_unresolved_dues(tmp_db_path, copy_fixture_notes):
    folder = copy_fixture_notes(names={"2026-10-03-sales-pipeline.md", "untitled.md"})
    ingest(tmp_db_path, folder)
    sp = "2026-10-03-sales-pipeline.md"
    m = q(tmp_db_path, "SELECT m.* FROM meeting m JOIN source_file f ON f.id = m.source_file_id WHERE f.rel_path = ?", (sp,))[0]
    assert m["meeting_date"] is None and m["date_source"] == "none"
    codes_sp = [c for c, _ in warnings_of(tmp_db_path, sp)]
    assert codes.W_MEETING_DATE_INVALID in codes_sp and codes.W_MEETING_DATE_MISSING not in codes_sp
    meera = items_of(tmp_db_path, sp)[0]
    assert meera["due_confidence"] == "unresolved" and meera["due_reason"] == "no_meeting_date"
    assert meera["due_raw"] == "Friday" and meera["due_date"] is None
    un = items_of(tmp_db_path, "untitled.md")
    assert len(un) == 2
    codes_un = [c for c, _ in warnings_of(tmp_db_path, "untitled.md")]
    assert codes.W_MEETING_DATE_MISSING in codes_un and codes.W_ATTENDEES_MISSING in codes_un
    sam, priya = un
    (o,) = owners_of(tmp_db_path, priya["id"])
    assert o["owner_status"] == "ambiguous" and json.loads(o["candidates"]) == ["priya-nair", "priya-shah"]
    assert o["person_id"] is None and o["owner_raw"] == "Priya"
    (so,) = owners_of(tmp_db_path, sam["id"])
    assert (so["owner_status"], so["person_id"]) == ("resolved", "sam-okafor")


@pytest.mark.ac("AC-05.4", "AC-05.5")
def test_multi_owner_with_and_nonperson_owners(tmp_db_path, copy_fixture_notes):
    folder = copy_fixture_notes(names={"2026-09-18-design-review.md"})
    ingest(tmp_db_path, folder)
    rel = "2026-09-18-design-review.md"
    by_line = {r["line_start"]: r for r in items_of(tmp_db_path, rel)}
    two = owners_of(tmp_db_path, by_line[12]["id"])
    assert [(o["ordinal"], o["owner_raw"], o["role"], o["person_id"]) for o in two] == [
        (0, "Anita", "owner", "anita-desai"), (1, "Rahul", "owner", "rahul-mehta")]
    lw = owners_of(tmp_db_path, by_line[13]["id"])
    assert [(o["owner_raw"], o["role"], o["person_id"], o["ordinal"]) for o in lw] == [
        ("Lena", "owner", "lena-fischer", 0), ("Priya", "with", "priya-nair", 1)]
    for line, raw in ((14, "Everyone"), (15, "TBD")):
        (o,) = owners_of(tmp_db_path, by_line[line]["id"])
        assert (o["owner_status"], o["person_id"], o["owner_raw"], o["provenance"]) == ("unassigned", None, raw, "none")
    assert (14 in {l for _, l in warnings_of(tmp_db_path, rel)}) is False  # non-person owner: no W-OWNER-MISSING
    # per-source owner phrases mirror the item's (one source each)
    src = by_line[13]["source_id"]
    so = q(tmp_db_path, "SELECT * FROM item_source_owner WHERE item_source_id = ? ORDER BY ordinal", (src,))
    assert [(o["owner_raw"], o["role"], o["person_id"]) for o in so] == [("Lena", "owner", "lena-fischer"), ("Priya", "with", "priya-nair")]


# ------------------------------------------------------------------------------------ synthetic notes
@pytest.mark.ac("AC-01.19")
def test_description_vs_task_text_and_verbatim_raw_text(tmp_db_path, folder):
    nfd_line = unicodedata.normalize("NFD", "- [ ] Zoë Åström to review the plan by Friday")
    text = (
        "# Meeting: Texts\n\nDate: 2026-09-01\nAttendees: Priya Nair, Lena Fischer\n\n## Action items\n\n"
        "- [ ] Priya to send the revised quote by Friday.\n"
        "- [ ] Lena (with Priya) to update the plan\n"
        f"{nfd_line}\n"
    )
    write_note(folder, "n.md", text)
    ingest(tmp_db_path, folder)
    a, b, c = items_of(tmp_db_path, "n.md")
    assert a["description"] == "Priya to send the revised quote by Friday."
    assert a["task_text"] == "Send the revised quote"
    assert (a["s_description"], a["s_task_text"]) == (a["description"], a["task_text"])
    assert a["description_core_key"] == "send revised quote" == a["s_core_key"]
    assert b["task_text"] == "Update the plan"
    nfd_text = unicodedata.normalize("NFD", "Zoë Åström to review the plan by Friday")
    assert c["raw_text"].endswith(nfd_text)  # verbatim, byte for byte (NFD kept)
    assert c["raw_text"] != unicodedata.normalize("NFC", c["raw_text"])
    assert c["description"] == unicodedata.normalize("NFC", c["description"])
    assert c["description"].startswith("Zoë Åström")  # NFC in the description
    assert c["block_hash"] == hashlib.sha256(c["raw_text"].encode("utf-8")).hexdigest()
    # SC-30: the Unicode owner round-trips unchanged (NFC), as a provisional person
    (o,) = owners_of(tmp_db_path, c["id"])
    assert o["owner_raw"] == "Zoë Åström" and o["owner_status"] == "provisional"
    person = q(tmp_db_path, "SELECT * FROM person WHERE id = ?", (o["person_id"],))[0]
    assert person["origin"] == "note_owner" and person["display_name"] == "Zoë Åström"


@pytest.mark.ac("AC-05.7")
@pytest.mark.sc("SC-05")
def test_item_without_owner_phrase_is_unassigned_with_warning(tmp_db_path, folder):
    write_note(folder, "n.md", simple_note(items=("- [ ] Send the quote by Friday",)))
    ingest(tmp_db_path, folder)
    (item,) = items_of(tmp_db_path, "n.md")
    (o,) = owners_of(tmp_db_path, item["id"])
    assert (o["owner_status"], o["owner_raw"], o["person_id"], o["provenance"]) == ("unassigned", None, None, "none")
    got = [(c, l) for c, l in warnings_of(tmp_db_path, "n.md") if c == codes.W_OWNER_MISSING]
    assert got == [(codes.W_OWNER_MISSING, item["line_start"])]
    assert item["task_text"] == "Send the quote"


def test_two_identical_lines_get_occurrence_index_0_and_1(tmp_db_path, folder):
    same = "- [ ] Priya Nair to send the notes"
    write_note(folder, "n.md", simple_note(items=(same, same)))
    ingest(tmp_db_path, folder)
    rows = items_of(tmp_db_path, "n.md")
    assert [r["occurrence_index"] for r in rows] == [0, 1]
    assert rows[0]["block_hash"] == rows[1]["block_hash"]
    assert rows[0]["id"] != rows[1]["id"]  # no matching yet: every parsed item is its own item


@pytest.mark.sc("SC-10")
def test_due_pair_without_meeting_date(tmp_db_path, folder):
    write_note(folder, "n.md", "- [ ] Meera Iyer to ping Acme by tomorrow\n- [ ] Meera Iyer to send the plan by 2026-11-05\n")
    ingest(tmp_db_path, folder)
    a, b = items_of(tmp_db_path, "n.md")
    assert (a["due_confidence"], a["due_reason"], a["due_date"]) == ("unresolved", "no_meeting_date", None)
    assert (b["due_confidence"], b["due_date"], b["due_rule"]) == ("explicit", "2026-11-05", "iso_date")


def test_meeting_date_future_warning_computed_by_the_service(tmp_db_path, folder):
    write_note(folder, "future.md", simple_note("2026-12-01", "Future"))
    write_note(folder, "past.md", simple_note("2026-09-01", "Past"))
    ingest(tmp_db_path, folder, today="2026-10-22")
    assert codes.W_MEETING_DATE_FUTURE in [c for c, _ in warnings_of(tmp_db_path, "future.md")]
    assert codes.W_MEETING_DATE_FUTURE not in [c for c, _ in warnings_of(tmp_db_path, "past.md")]
    msg = q(tmp_db_path, "SELECT message FROM ingest_warning WHERE code = ?", (codes.W_MEETING_DATE_FUTURE,))[0][0]
    assert "2026-12-01" in msg and "2026-10-22" in msg and "future.md" in msg


@pytest.mark.sc("SC-63", "SC-64")
@pytest.mark.ac("AC-06.8")
def test_due_dates_around_the_meeting_are_persisted(tmp_db_path, folder):
    write_note(folder, "before.md", simple_note("2026-10-12", "B", ("- [ ] Meera Iyer to demo it by 9 Oct",)))
    write_note(folder, "yearend.md", simple_note("2026-12-20", "Y", ("- [ ] Meera Iyer to plan it by Jan 6",)))
    write_note(folder, "event.md", simple_note("2026-10-13", "E", ("- [ ] Meera Iyer to demo the portal to Acme on 2026-10-14",)))
    ingest(tmp_db_path, folder, today="2027-01-01")
    (b,) = items_of(tmp_db_path, "before.md")
    assert b["due_date"] == "2026-10-09"
    assert codes.W_DUE_BEFORE_MEETING in [c for c, _ in warnings_of(tmp_db_path, "before.md")]
    (y,) = items_of(tmp_db_path, "yearend.md")
    assert y["due_date"] == "2027-01-06"
    (e,) = items_of(tmp_db_path, "event.md")
    assert (e["due_confidence"], e["due_date"]) == ("explicit", "2026-10-14")


@pytest.mark.ac("AC-01.18", "AC-01.13")
@pytest.mark.sc("SC-69")
def test_folder_with_skipped_and_odd_files_persists_only_normal_files(tmp_db_path, folder, monkeypatch):
    write_note(folder, "ok.md", simple_note())
    write_note(folder, ".draft.md", simple_note(title="Draft"))
    write_note(folder, ".hidden/x.md", simple_note(title="Hidden"))
    write_note(
        folder,
        "odd.md",
        "# Meeting: Odd\n\nDate: 2026-09-02\nAttendees: Priya Nair\n\n- [ ] Priya Nair to do a thing\n- [ ]\n\n<!-- never closed\n- [ ] Sam to hide\n",
    )
    (folder / "locked").mkdir()
    write_note(folder, "locked/y.md", simple_note(title="Locked"))
    real_scandir = os.scandir

    def scandir(path="."):
        if str(path).endswith("locked"):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    res = ingest(tmp_db_path, folder)
    assert res.exit_code == 0 and res.counters.files_ingested == 2
    names = [r["rel_path"] for r in q(tmp_db_path, "SELECT rel_path FROM source_file ORDER BY rel_path")]
    assert names == ["odd.md", "ok.md"]
    all_codes = [c for c, _ in warnings_of(tmp_db_path)]
    for code in (codes.W_HTML_COMMENT_UNCLOSED, codes.W_CHECKBOX_EMPTY, codes.W_DIR_UNREADABLE):
        assert code in all_codes, code
    dir_w = q(tmp_db_path, "SELECT * FROM ingest_warning WHERE code = ?", (codes.W_DIR_UNREADABLE,))[0]
    assert dir_w["source_file_id"] is None and "locked" in dir_w["message"]


def test_attendee_name_incomplete_warning_is_recorded(tmp_db_path, folder):
    write_note(folder, "n.md", simple_note(items=("- [ ] Priya Nair to do it",)).replace("Rahul Mehta", "Priya"))
    ingest(tmp_db_path, folder)
    got = [c for c, _ in warnings_of(tmp_db_path, "n.md")]
    assert codes.W_ATTENDEE_NAME_INCOMPLETE in got
    att = {r["raw_name"]: r["person_id"] for r in q(tmp_db_path, "SELECT raw_name, person_id FROM meeting_attendee")}
    assert att["Priya"] is None and att["Priya Nair"] == "priya-nair"


def test_match_or_create_seam_is_called_per_item_in_document_order(tmp_db_path, folder):
    write_note(folder, "n.md", simple_note(items=("- [ ] Priya Nair to a1", "- [ ] Rahul Mehta to b2", "- [ ] Anita Desai to c3")))
    seen: list[tuple[int, str, int]] = []

    def matcher(env, src):
        # the file, meeting and earlier items of this file are already persisted
        n_items = env.repo.conn.execute("SELECT COUNT(*) FROM action_item").fetchone()[0]
        seen.append((src.item.line_start, src.item.task_text, n_items))
        assert src.file.rel_path == "n.md" and src.file.meeting_id and src.file.source_file_id
        assert [o.ordinal for o in src.owners] == [0]
        return ing.create_item(env, src)

    ing.run_ingest(tmp_db_path, folder, cfg=roster_cfg(), run_ctx=run_ctx(), match=matcher)
    assert [(l, t) for l, t, _ in seen] == [(8, "A1"), (9, "B2"), (10, "C3")]
    assert [n for *_, n in seen] == [0, 1, 2]


def test_failed_item_write_rolls_the_whole_file_back_including_people(tmp_db_path, folder):
    write_note(folder, "n.md", simple_note(items=("- [ ] Zed Quill to do it", "- [ ] Rahul Mehta to explode")))

    def matcher(env, src):
        if src.item.task_text.startswith("Explode"):
            raise RuntimeError("boom")
        return ing.create_item(env, src)

    res = ing.run_ingest(tmp_db_path, folder, cfg=roster_cfg(), run_ctx=run_ctx(), match=matcher)
    assert res.counters.files_failed == 1
    assert q(tmp_db_path, "SELECT COUNT(*) FROM action_item")[0][0] == 0
    assert q(tmp_db_path, "SELECT COUNT(*) FROM person WHERE origin <> 'roster'")[0][0] == 0  # provisional Zed rolled back
    assert q(tmp_db_path, "SELECT COUNT(*) FROM meeting")[0][0] == 0


# --------------------------------------------------------------------------- the whole sample (no matching)
@pytest.mark.ac("AC-01.14", "AC-01.2", "AC-08.1")
@pytest.mark.sc("SC-25")
def test_full_sample_persists_117_items_and_second_run_changes_nothing(tmp_db_path, copy_fixture_notes):
    folder = copy_fixture_notes()
    res = ingest(tmp_db_path, folder, match=ing.create_item)  # persistence only: matching (T-M5-08/09) is tested elsewhere
    c = res.counters
    assert (c.files_discovered, c.files_ingested, c.files_empty, c.files_failed, c.files_unchanged) == (35, 33, 1, 1, 0)
    assert res.exit_code == 5 and res.run_row.status == "partial"
    assert [r[0] for r in q(tmp_db_path, "SELECT status || ':' || COUNT(*) FROM source_file GROUP BY status ORDER BY status")] == [
        "empty:1", "failed:1", "ok:33"]
    assert q(tmp_db_path, "SELECT COUNT(*) FROM action_item")[0][0] == 117 == c.items_new
    assert q(tmp_db_path, "SELECT COUNT(*) FROM item_source")[0][0] == 117
    assert q(tmp_db_path, "SELECT COUNT(*) FROM item_owner")[0][0] == 119
    assert q(tmp_db_path, "SELECT COUNT(*) FROM item_source_owner")[0][0] == 119
    assert q(tmp_db_path, "SELECT COUNT(*) FROM action_item WHERE status = 'open'")[0][0] == 104
    assert q(tmp_db_path, "SELECT COUNT(*) FROM action_item WHERE status = 'done'")[0][0] == 13 == c.already_done_in_note
    assert q(tmp_db_path, "SELECT COUNT(*) FROM person WHERE origin <> 'roster'")[0][0] == 0  # zero provisional
    assert q(tmp_db_path, "SELECT COUNT(*) FROM person")[0][0] == 9
    assert q(tmp_db_path, "SELECT COUNT(*) FROM item_event WHERE kind = 'created'")[0][0] == 117
    assert q(tmp_db_path, "SELECT COUNT(*) FROM action_item WHERE completion_origin = 'note' AND completion_evidence_source_id IS NOT NULL")[0][0] == 13

    # owner rows equal the hand-written golden table (file, line, ordinal)
    with open(GOLDEN_OWNERS, encoding="utf-8", newline="") as fh:
        golden = {(r["file"], int(r["line"]), int(r["ordinal"])): r for r in csv.DictReader(fh)}
    actual = {}
    for r in q(
        tmp_db_path,
        "SELECT f.rel_path, s.line_start, o.* FROM item_owner o JOIN item_source s ON s.item_id = o.item_id "
        "JOIN source_file f ON f.id = s.source_file_id",
    ):
        who = "|".join(sorted(json.loads(r["candidates"]))) if r["owner_status"] == "ambiguous" else (r["person_id"] or "")
        actual[(r["rel_path"], r["line_start"], r["ordinal"])] = (
            r["owner_raw"], r["role"], r["owner_status"], who, r["provenance"])
    expected = {k: (g["owner_raw"], g["role"], g["status"], g["persons"], g["provenance"]) for k, g in golden.items()}
    assert actual == expected

    conn = sqlite3.connect(tmp_db_path)
    try:
        before = dump_tables(conn)
    finally:
        conn.close()
    second = ingest(tmp_db_path, folder, match=ing.create_item)
    c2 = second.counters
    assert (c2.files_discovered, c2.files_unchanged, c2.files_empty, c2.files_failed, c2.files_ingested) == (35, 33, 1, 1, 0)
    assert (c2.items_new, c2.items_merged, c2.items_updated) == (0, 0, 0)
    assert second.exit_code == 5
    conn = sqlite3.connect(tmp_db_path)
    try:
        assert dump_tables(conn) == before  # byte-for-byte equal business tables
    finally:
        conn.close()
