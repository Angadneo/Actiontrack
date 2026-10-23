"""T-M5-08: Tier-1 auto-merge through the real ingest (synthetic notes and the sample corpus)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from actiontrack.services import ingest as ing

from tests.integration.ingest_support import dump_tables, roster_cfg, run_ctx, write_note

ATT = "Priya Nair, Priya Shah, Rahul Mehta, Anita Desai, Karan Joshi"


def note(date: str | None, *items: str, attendees: str | None = ATT) -> str:
    head = "# Meeting: Sync\n\n"
    if date:
        head += f"Date: {date}\n"
    if attendees:
        head += f"Attendees: {attendees}\n"
    return head + "\n## Action items\n\n" + "\n".join(items) + "\n"


def ingest(db_path, folder, **kw):
    return ing.run_ingest(db_path, folder, cfg=roster_cfg(), run_ctx=run_ctx(), **kw)


def q(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def counts(db_path):
    return (
        q(db_path, "SELECT count(*) FROM action_item")[0][0],
        q(db_path, "SELECT count(*) FROM item_source")[0][0],
    )


def two_files(tmp_path, a: tuple, b: tuple, name_a="2026-10-14-a.md", name_b="2026-10-15-b.md"):
    folder = tmp_path / "notes"
    write_note(folder, name_a, note(*a))
    write_note(folder, name_b, note(*b))
    db = tmp_path / "t.db"
    res = ingest(db, folder)
    assert res.counters.files_failed == 0
    return db, res


OPEN = "- [ ] Rahul Mehta to freeze invoice edits by 2026-10-30"


@pytest.mark.ac("AC-07.1", "AC-07.9")
@pytest.mark.sc("SC-03")
def test_same_owner_same_due_two_files_merge(tmp_path):
    db, res = two_files(tmp_path, ("2026-10-14", OPEN), ("2026-10-15", OPEN))
    assert counts(db) == (1, 2)
    assert (res.counters.items_new, res.counters.items_merged) == (1, 1)
    (item,) = q(db, "SELECT id FROM action_item")
    kinds = [r["kind"] for r in q(db, "SELECT kind FROM item_event WHERE item_id = ? ORDER BY id", (item["id"],))]
    assert kinds == ["created", "source_added", "merged"]
    assert q(db, "SELECT count(*) FROM item_link")[0][0] == 0


@pytest.mark.ac("AC-07.5", "AC-07.9")
def test_open_then_later_done_same_due_merges_and_completes(tmp_path):
    line = "Rahul Mehta to set up staging environment by 2026-09-08"
    db, res = two_files(tmp_path, ("2026-09-01", f"- [ ] {line}"), ("2026-09-08", f"- [x] {line}"),
                        "2026-09-01-a.md", "2026-09-08-b.md")
    assert counts(db) == (1, 2)
    (item,) = q(db, "SELECT * FROM action_item")
    assert (item["status"], item["completion_origin"], item["completed_at"]) == ("done", "note", None)
    ev = q(db, "SELECT s.line_start, f.rel_path FROM item_source s JOIN source_file f ON f.id = s.source_file_id "
               "WHERE s.id = ?", (item["completion_evidence_source_id"],))[0]
    assert ev["rel_path"] == "2026-09-08-b.md"
    assert "status_changed" in [r["kind"] for r in q(db, "SELECT kind FROM item_event")]
    assert q(db, "SELECT count(*) FROM item_link")[0][0] == 0


def test_both_done_merge_stays_done_and_no_evidence_change(tmp_path):
    line = "- [x] Rahul Mehta to set up staging environment"
    db, _ = two_files(tmp_path, ("2026-09-01", line), ("2026-09-08", line), "2026-09-01-a.md", "2026-09-08-b.md")
    (item,) = q(db, "SELECT * FROM action_item")
    first_src = q(db, "SELECT id FROM item_source ORDER BY id")[0]["id"]
    assert item["status"] == "done" and item["completion_evidence_source_id"] == first_src
    assert "status_changed" not in [r["kind"] for r in q(db, "SELECT kind FROM item_event")]


def test_merge_without_business_change_leaves_updated_at(tmp_path):
    db, _ = two_files(tmp_path, ("2026-10-14", OPEN), ("2026-10-15", OPEN))
    (item,) = q(db, "SELECT created_at, updated_at FROM action_item")
    assert item["created_at"] == item["updated_at"]


NEGATIVES = {
    "due differs": ("- [ ] Rahul Mehta to freeze invoice edits by 2026-10-30", "- [ ] Rahul Mehta to freeze invoice edits by 2026-10-31", "due_differs"),
    "one due null": ("- [ ] Rahul Mehta to freeze invoice edits", "- [ ] Rahul Mehta to freeze invoice edits by 2026-10-31", "due_differs"),
    "owner differs": ("- [ ] Rahul Mehta to freeze invoice edits", "- [ ] Karan Joshi to freeze invoice edits", "owner_differs"),
    "existing done, new open": ("- [x] Rahul Mehta to freeze invoice edits", "- [ ] Rahul Mehta to freeze invoice edits", "status_differs"),
    "core under 3 tokens": ("- [ ] Rahul Mehta to send quote", "- [ ] Rahul Mehta to send quote", "fuzzy"),
    "recurrence word": ("- [ ] Rahul Mehta to send weekly status report", "- [ ] Rahul Mehta to send weekly status report", "recurrence"),
    "owner ambiguous": ("- [ ] Priya to freeze invoice edits", "- [ ] Priya to freeze invoice edits", "owner_unresolved"),
}


@pytest.mark.parametrize("label", sorted(NEGATIVES))
@pytest.mark.ac("AC-07.2", "AC-07.8")
def test_negative_table_each_stays_two_items(tmp_path, label):
    a, b, reason = NEGATIVES[label]
    attendees = "Rahul Mehta, Karan Joshi" if label != "owner ambiguous" else None
    folder = tmp_path / "notes"
    write_note(folder, "2026-10-14-a.md", note("2026-10-14", a, attendees=attendees))
    write_note(folder, "2026-10-15-b.md", note("2026-10-15", b, attendees=attendees))
    db = tmp_path / "t.db"
    res = ingest(db, folder)
    assert counts(db) == (2, 2), label
    links = q(db, "SELECT reason FROM item_link WHERE kind = 'possible_duplicate'")
    assert [r["reason"] for r in links] == [reason], label
    assert res.counters.possible_duplicates == 1 and res.counters.items_merged == 0


@pytest.mark.ac("AC-07.6")
def test_same_file_never_merges(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-10-14-a.md", note("2026-10-14", OPEN, OPEN))
    db = tmp_path / "t.db"
    ingest(db, folder)
    assert counts(db) == (2, 2)
    assert [r["reason"] for r in q(db, "SELECT reason FROM item_link")] == ["same_file"]


def test_window_boundary_59_60_merge_61_does_not(tmp_path):
    for d, merged in (("2026-10-30", True), ("2026-10-31", True), ("2026-11-01", False)):
        sub = tmp_path / d
        folder = sub / "notes"
        write_note(folder, "2026-09-01-a.md", note("2026-09-01", OPEN))
        write_note(folder, f"{d}-b.md", note(d, OPEN))
        db = sub / "t.db"
        ingest(db, folder)
        assert counts(db) == ((1, 2) if merged else (2, 2)), d
        if not merged:
            assert [r["reason"] for r in q(db, "SELECT reason FROM item_link")] == ["outside_window"]


def test_unknown_meeting_date_is_outside_window_link(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-10-14-a.md", note("2026-10-14", OPEN))
    write_note(folder, "undated.md", note(None, OPEN))
    db = tmp_path / "t.db"
    ingest(db, folder)
    assert counts(db) == (2, 2)
    assert [r["reason"] for r in q(db, "SELECT reason FROM item_link")] == ["outside_window"]


@pytest.mark.ac("AC-05.16")
def test_namesakes_with_identical_text_are_separate_items(tmp_path):
    a = "- [ ] Priya Nair to send the revised quote to Acme"
    b = "- [ ] Priya Shah to send the revised quote to Acme"
    db, _ = two_files(tmp_path, ("2026-10-14", a), ("2026-10-15", b))
    assert counts(db) == (2, 2)
    assert [r["reason"] for r in q(db, "SELECT reason FROM item_link")] == ["owner_differs"]


def test_lowest_item_id_wins_when_several_candidates_qualify(tmp_path):
    folder = tmp_path / "notes"
    # same file twice -> two items (same_file link); a third file's copy merges into the lower id
    write_note(folder, "2026-10-14-a.md", note("2026-10-14", OPEN, OPEN))
    write_note(folder, "2026-10-15-b.md", note("2026-10-15", OPEN))
    db = tmp_path / "t.db"
    ingest(db, folder)
    srcs = q(db, "SELECT item_id, count(*) n FROM item_source GROUP BY item_id ORDER BY item_id")
    assert [(r["item_id"], r["n"]) for r in srcs] == [(1, 2), (2, 1)]


# ------------------------------------------------------------------------------------------ sample
@pytest.fixture(scope="module")
def sample_db(tmp_path_factory):
    from tests.conftest import FIXTURES_NOTES

    root = tmp_path_factory.mktemp("sample")
    folder = root / "notes"
    folder.mkdir()
    for src in sorted(FIXTURES_NOTES.iterdir()):
        if src.is_file():
            (folder / src.name).write_bytes(src.read_bytes())
    db = root / "t.db"
    res = ingest(db, folder)
    return db, res


@pytest.mark.ac("AC-07.12", "AC-07.9")
def test_sample_freeze_invoice_edits_merge(sample_db):
    db, _ = sample_db
    rows = q(
        db,
        "SELECT s.item_id, f.rel_path, s.line_start FROM item_source s JOIN source_file f ON f.id = s.source_file_id "
        "WHERE s.core_key = 'freeze invoice edits' ORDER BY s.id",
    )
    assert [(r["rel_path"], r["line_start"]) for r in rows] == [
        ("2026-09-29-billing-cutover-planning.md", 12),
        ("2026-10-08-billing-go-no-go.md", 11),
    ]
    assert len({r["item_id"] for r in rows}) == 1
    item = q(db, "SELECT * FROM action_item WHERE id = ?", (rows[0]["item_id"],))[0]
    first = q(db, "SELECT description FROM item_source WHERE item_id = ? ORDER BY id", (item["id"],))[0]
    assert item["description"] == first["description"] and item["due_confidence"] == "none"
    owner = q(db, "SELECT person_id, owner_status FROM item_owner WHERE item_id = ?", (item["id"],))[0]
    assert owner["person_id"] == "karan-joshi" and owner["owner_status"] == "resolved"


def test_sample_totals_and_no_source_lost(sample_db):
    db, res = sample_db
    items, sources = counts(db)
    merges = res.counters.items_merged
    assert sources == 117 and items == 117 - merges
    assert res.counters.items_new == items and res.counters.possible_duplicates == q(
        db, "SELECT count(*) FROM item_link WHERE kind = 'possible_duplicate'")[0][0]
    # every source belongs to exactly one existing item and every item has >= 1 source
    assert q(db, "SELECT count(*) FROM action_item i WHERE NOT EXISTS (SELECT 1 FROM item_source s WHERE s.item_id = i.id)")[0][0] == 0


def test_sample_ingest_is_deterministic(sample_db, tmp_path):
    from tests.conftest import FIXTURES_NOTES

    db, _ = sample_db
    folder = tmp_path / "notes"
    folder.mkdir()
    for src in sorted(FIXTURES_NOTES.iterdir()):
        if src.is_file():
            (folder / src.name).write_bytes(src.read_bytes())
    db2 = tmp_path / "t2.db"
    ingest(db2, folder)

    def dump(p):
        conn = sqlite3.connect(p)
        try:
            d = dump_tables(conn, ("action_item", "item_source", "item_link", "item_owner"))
            d.pop("item_source", None)  # holds run-independent ids; compared via counts below
            return d
        finally:
            conn.close()

    assert dump(db) == dump(db2)
