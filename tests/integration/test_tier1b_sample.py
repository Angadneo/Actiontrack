"""T-M5-10: Tier-1b completion match through the real ingest (sample corpus and synthetic notes)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from actiontrack.services import ingest as ing

from tests.conftest import FIXTURES_NOTES
from tests.integration.ingest_support import open_repo, roster_cfg, run_ctx, write_note
from tests.integration.test_tier1_merge import ATT, note

# core key -> (open items before the [x] note, holder source file, inferred (CI) item count)
CLUSTERS = {
    "send revised quote acme": (2, "2026-09-15", 1),
    "confirm cloud budget with finance": (2, "2026-09-22", 1),
    "set up staging environment": (1, "2026-09-08", 0),
    "export last quarter invoices": (1, "2026-09-17", 0),
    "finalise welcome email copy": (2, "2026-10-05", 1),
    "write acceptance tests signup flow": (1, "2026-09-24", 0),
    "book renewal call with acmes cto": (2, "2026-10-14", 1),
    "send acme procurement form": (1, "2026-10-01", 0),
    "deploy beta staging": (2, "2026-10-13", 1),
    "approve q4 cloud spend": (1, "2026-10-02", 0),
    "notify customers about maintenance window": (1, "2026-10-08", 0),
}


def ingest(db_path, folder, **kw):
    return ing.run_ingest(db_path, folder, cfg=roster_cfg(), run_ctx=run_ctx(), **kw)


def q(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    root = tmp_path_factory.mktemp("tier1b")
    folder = root / "notes"
    folder.mkdir()
    for src in sorted(FIXTURES_NOTES.iterdir()):
        if src.is_file():
            (folder / src.name).write_bytes(src.read_bytes())
    db = root / "t.db"
    return db, ingest(db, folder), folder


def cluster_items(db, core):
    return q(
        db,
        "SELECT * FROM action_item WHERE id IN (SELECT item_id FROM item_source WHERE core_key = ?) ORDER BY id",
        (core,),
    )


def done_source(db, core):
    (row,) = q(
        db,
        "SELECT s.id, s.item_id, s.line_start, s.raw_text, f.rel_path FROM item_source s "
        "JOIN source_file f ON f.id = s.source_file_id WHERE s.core_key = ? AND s.checkbox_state = 'done'",
        (core,),
    )
    return row


@pytest.mark.parametrize("core", sorted(CLUSTERS))
@pytest.mark.ac("AC-07.11")
@pytest.mark.sc("SC-47")
def test_cluster_holder_completed_items_links_and_events(sample, core):
    db = sample[0]
    n_open, holder_date, n_inferred = CLUSTERS[core]
    items = cluster_items(db, core)
    ev = done_source(db, core)
    assert ev["rel_path"].startswith(holder_date)
    assert len(items) == n_open and all(i["status"] == "done" for i in items)
    assert all(
        (i["completion_origin"], i["completed_at"], i["completion_evidence_source_id"]) == ("note", None, ev["id"])
        for i in items
    )
    holder = ev["item_id"]
    # holder = most recent matching item = highest item id of the cluster here (chronological ingest)
    assert holder == max(i["id"] for i in items)
    links = q(db, "SELECT * FROM item_link WHERE kind = 'completed_by' AND (from_item_id IN ({0}))".format(",".join(str(i["id"]) for i in items)))
    assert len(links) == n_inferred
    assert all(r["to_item_id"] == holder and r["from_item_id"] != holder and r["reason"] is None for r in links)
    assert q(db, "SELECT count(*) FROM item_link WHERE kind = 'completed_by' AND from_item_id = ?", (holder,))[0][0] == 0
    # every completed item has a status_changed event citing file:line and the raw text of the evidence
    for i in items:
        events = [e for e in q(db, "SELECT * FROM item_event WHERE item_id = ? AND kind = 'status_changed'", (i["id"],))]
        assert len(events) == 1
        after = json.loads(events[0]["after"])
        assert after["evidence"] == f"{ev['rel_path']}:{ev['line_start']}" and after["raw_text"] == ev["raw_text"]
        assert after["status"] == "done" and after["completion_origin"] == "note"
        assert (after.get("completed_by") == holder) == (i["id"] != holder)
    kinds = [r["kind"] for r in q(db, "SELECT kind FROM item_event WHERE item_id = ? ORDER BY id", (holder,))]
    assert kinds[:1] == ["created"] and {"source_added", "merged", "status_changed"} <= set(kinds)
    merged = json.loads(q(db, "SELECT after FROM item_event WHERE item_id = ? AND kind = 'merged'", (holder,))[0]["after"])
    assert merged["tier"] == "1b"


@pytest.mark.ac("AC-07.11")
@pytest.mark.sc("SC-47")
def test_quote_cluster_09_08_holds_evidence_09_01_is_inferred(sample):
    db = sample[0]
    core = "send revised quote acme"
    rows = q(
        db,
        "SELECT s.item_id, f.rel_path, s.line_start FROM item_source s JOIN source_file f ON f.id = s.source_file_id "
        "WHERE s.core_key = ? ORDER BY s.id",
        (core,),
    )
    assert len(rows) == 3
    by_file = {r["rel_path"][:10]: r["item_id"] for r in rows}
    first, second, third = (by_file[d] for d in ("2026-09-01", "2026-09-08", "2026-09-15"))
    assert second == third != first  # the 09-15 [x] joined the 09-08 item
    link = q(db, "SELECT * FROM item_link WHERE kind = 'completed_by'")
    assert [(r["from_item_id"], r["to_item_id"]) for r in link if r["from_item_id"] == first] == [(first, second)]
    # `list --owner "Priya Nair" --status open` data: no open item of this cluster
    open_ids = {r["id"] for r in q(
        db, "SELECT i.id FROM action_item i JOIN item_owner o ON o.item_id = i.id WHERE o.person_id = 'priya-nair' AND i.status = 'open'")}
    assert first not in open_ids and second not in open_ids


@pytest.mark.ac("AC-07.11")
@pytest.mark.sc("SC-47")
def test_sample_totals_eleven_holders_five_inferred_nothing_lost(sample):
    db, res, _ = sample
    assert q(db, "SELECT count(*) FROM item_source")[0][0] == 117
    assert q(db, "SELECT count(*) FROM action_item")[0][0] == 103
    assert dict((r[0], r[1]) for r in q(db, "SELECT status, count(*) FROM action_item GROUP BY status")) == {
        "open": 85, "done": 18}  # 13 [x] sources: 11 Tier-1b evidence + 2 standalone; + 5 inferred
    done = q(db, "SELECT * FROM action_item WHERE status = 'done'")
    assert all(r["completion_origin"] == "note" and r["completed_at"] is None for r in done)
    holders = {r["completion_evidence_source_id"] for r in done if r["completion_evidence_source_id"]}
    assert len(holders) == 13  # one evidence source per done cluster/standalone item group
    cb = q(db, "SELECT * FROM item_link WHERE kind = 'completed_by'")
    assert len(cb) == 5  # the five 2-open-item clusters
    tier1b_holders = {cb_row["to_item_id"] for cb_row in cb} | {
        done_source(db, core)["item_id"] for core in CLUSTERS
    }
    assert len(tier1b_holders) == 11
    assert q(db, "SELECT count(*) FROM item_link WHERE kind = 'possible_duplicate'")[0][0] == 9
    assert res.counters.items_new == 103 and res.counters.items_merged == 14
    assert res.counters.already_done_in_note == 13
    # no source row was deleted and every item keeps >= 1 source
    assert q(db, "SELECT count(*) FROM item_source WHERE present = 0")[0][0] == 0
    assert q(db, "SELECT count(*) FROM action_item i WHERE NOT EXISTS (SELECT 1 FROM item_source s WHERE s.item_id = i.id)")[0][0] == 0


@pytest.mark.ac("AC-07.12", "AC-07.13")
def test_freeze_merge_and_four_due_differs_clusters_unchanged(sample):
    db = sample[0]
    assert len(cluster_items(db, "freeze invoice edits")) == 1
    # amended D-04: the two clusters whose later source is dateless (schema mapping doc, renewal one-pager)
    # merge into one open item; the four others (dateless source EARLIER, or both dated) stay two items
    for core in ("review schema mapping doc", "prepare renewal one pager"):
        (item,) = cluster_items(db, core)
        assert item["status"] == "open" and item["due_confidence"] != "none"
    for core in ("share updated wireframes", "fix pagination bug", "estimate sso work",
                 "collect 5 customer quotes landing page"):
        items = cluster_items(db, core)
        assert len(items) == 2 and all(i["status"] == "open" for i in items)


def test_unchanged_rerun_changes_nothing(sample, tmp_path):
    db, _, folder = sample
    copy = tmp_path / "t.db"
    copy.write_bytes(db.read_bytes())
    before = [tuple(r) for r in q(copy, "SELECT * FROM action_item ORDER BY id")]
    links = [tuple(r) for r in q(copy, "SELECT * FROM item_link ORDER BY 1,2,3")]
    ingest(copy, folder)
    assert [tuple(r) for r in q(copy, "SELECT * FROM action_item ORDER BY id")] == before
    assert [tuple(r) for r in q(copy, "SELECT * FROM item_link ORDER BY 1,2,3")] == links


# ------------------------------------------------------------------------------------------- synthetic
LINE = "Rahul Mehta to send the revised quote to Acme"


def run(tmp_path, files: dict[str, tuple], attendees=ATT):
    folder = tmp_path / "notes"
    for name, (day, *items) in files.items():
        write_note(folder, name, note(day, *items, attendees=attendees))
    db = tmp_path / "t.db"
    res = ingest(db, folder)
    assert res.counters.files_failed == 0
    return db, res


def statuses(db):
    return [r["status"] for r in q(db, "SELECT status FROM action_item ORDER BY id")]


@pytest.mark.ac("AC-07.11")
def test_tie_break_two_open_items_same_meeting_date_highest_id_holds_evidence(tmp_path):
    db, _ = run(tmp_path, {
        "2026-09-01-a.md": ("2026-09-01", f"- [ ] {LINE} by 2026-09-04"),
        "2026-09-01-b.md": ("2026-09-01", f"- [ ] {LINE} by 2026-09-11"),
        "2026-09-15-c.md": ("2026-09-15", f"- [x] {LINE}"),
    })
    assert statuses(db) == ["done", "done"]
    (link,) = q(db, "SELECT * FROM item_link WHERE kind = 'completed_by'")
    assert (link["from_item_id"], link["to_item_id"]) == (1, 2)
    assert q(db, "SELECT item_id FROM item_source WHERE checkbox_state = 'done'")[0]["item_id"] == 2


@pytest.mark.ac("AC-07.14")
def test_done_note_dated_before_open_item_does_not_complete_it(tmp_path):
    db, _ = run(tmp_path, {
        "2026-09-01-a.md": ("2026-09-01", f"- [x] {LINE}"),
        "2026-09-08-b.md": ("2026-09-08", f"- [ ] {LINE} by 2026-09-11"),
    })
    assert statuses(db) == ["done", "open"]  # separate items, status_differs link (existing done, new open)
    assert q(db, "SELECT count(*) FROM item_link WHERE kind = 'completed_by'")[0][0] == 0


NEGATIVES = {
    "owner ambiguous": (f"- [ ] Priya to send the revised quote to Acme by 2026-09-04", "- [x] Priya to send the revised quote to Acme", None),
    "owner unassigned": ("- [ ] Send the revised quote to Acme by 2026-09-04", "- [x] Send the revised quote to Acme", None),
    "different owners": ("- [ ] Priya Nair to send the revised quote to Acme by 2026-09-04", "- [x] Priya Shah to send the revised quote to Acme", ATT),
    "recurrence word": ("- [ ] Rahul Mehta to send weekly status report by 2026-09-04", "- [x] Rahul Mehta to send weekly status report", ATT),
}


@pytest.mark.parametrize("label", sorted(NEGATIVES))
@pytest.mark.ac("AC-07.14")
def test_negatives_leave_open_item_open(tmp_path, label):
    a, b, att = NEGATIVES[label]
    db, _ = run(tmp_path, {"2026-09-01-a.md": ("2026-09-01", a), "2026-09-15-b.md": ("2026-09-15", b)}, attendees=att)
    assert statuses(db) == ["open", "done"], label
    assert q(db, "SELECT count(*) FROM item_link WHERE kind = 'completed_by'")[0][0] == 0


@pytest.mark.ac("AC-07.14")
def test_window_61_days_and_same_file_do_not_match(tmp_path):
    db, _ = run(tmp_path, {
        "2026-09-01-a.md": ("2026-09-01", f"- [ ] {LINE} by 2026-09-04"),
        "2026-11-01-b.md": ("2026-11-01", f"- [x] {LINE}"),
    })
    assert statuses(db) == ["open", "done"]
    sub = tmp_path / "same"
    sub.mkdir()
    db2, _ = run(sub, {"2026-09-01-a.md": ("2026-09-01", f"- [ ] {LINE} by 2026-09-04", f"- [x] {LINE}")})
    assert statuses(db2) == ["open", "done"]


@pytest.mark.ac("AC-07.14", "AC-07.11")
def test_window_60_days_matches(tmp_path):
    db, _ = run(tmp_path, {
        "2026-09-01-a.md": ("2026-09-01", f"- [ ] {LINE} by 2026-09-04"),
        "2026-10-31-b.md": ("2026-10-31", f"- [x] {LINE}"),
    })
    assert statuses(db) == ["done"]


def test_manual_done_item_is_never_matched_or_altered(tmp_path):
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {LINE} by 2026-09-04"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    d, repo = open_repo(db)
    with d.transaction():
        repo.set_manual_done(1, "2026-09-02T10:00:00Z")
    before = tuple(q(db, "SELECT * FROM action_item WHERE id = 1")[0])
    d.close()
    write_note(folder, "2026-09-15-b.md", note("2026-09-15", f"- [x] {LINE}"))
    ingest(db, folder)
    assert tuple(q(db, "SELECT * FROM action_item WHERE id = 1")[0]) == before
    # amended D-04: the dateless [x] note is a later restatement of the dated item, so it is a Tier-1 merge
    # (done + done); the manual-done item row itself is untouched (asserted above), it only gains a source
    assert statuses(db) == ["done"]
    assert q(db, "SELECT count(*) FROM item_link")[0][0] == 0
    assert q(db, "SELECT count(*) FROM item_source WHERE item_id = 1")[0][0] == 2


def test_manual_done_item_is_not_absorbed_by_a_note_with_another_due(tmp_path):
    """The original intent of the test above, with a DIFFERENT due so that Tier-1 / Tier-1b do not apply."""
    folder = tmp_path / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {LINE} by 2026-09-04"))
    db = tmp_path / "t.db"
    ingest(db, folder)
    d, repo = open_repo(db)
    with d.transaction():
        repo.set_manual_done(1, "2026-09-02T10:00:00Z")
    before = tuple(q(db, "SELECT * FROM action_item WHERE id = 1")[0])
    d.close()
    write_note(folder, "2026-09-15-b.md", note("2026-09-15", f"- [x] {LINE} by 2026-09-20"))
    ingest(db, folder)
    assert tuple(q(db, "SELECT * FROM action_item WHERE id = 1")[0]) == before
    assert statuses(db) == ["done", "done"]  # the [x] note is its own item (due differs: not Tier-1, not Tier-1b)
    assert q(db, "SELECT count(*) FROM item_link WHERE kind = 'completed_by'")[0][0] == 0
    assert q(db, "SELECT count(*) FROM item_source WHERE item_id = 1")[0][0] == 1


def test_single_match_with_same_due_stays_a_tier1_merge(tmp_path):
    db, res = run(tmp_path, {
        "2026-09-01-a.md": ("2026-09-01", f"- [ ] {LINE} by 2026-09-08"),
        "2026-09-08-b.md": ("2026-09-08", f"- [x] {LINE} by 2026-09-08"),
    })
    assert statuses(db) == ["done"] and res.counters.items_merged == 1
    assert q(db, "SELECT count(*) FROM item_link")[0][0] == 0


def test_second_done_note_after_completion_does_not_touch_done_items(tmp_path):
    db, _ = run(tmp_path, {
        "2026-09-01-a.md": ("2026-09-01", f"- [ ] {LINE} by 2026-09-04"),
        "2026-09-15-b.md": ("2026-09-15", f"- [x] {LINE}"),
        "2026-09-20-c.md": ("2026-09-20", f"- [x] {LINE}"),
    })
    # the later [x] copy finds no OPEN item: Tier-1 'both done' (both undated after canonical selection) merges it
    assert statuses(db) == ["done"]
    (item,) = q(db, "SELECT * FROM action_item")
    assert item["completion_evidence_source_id"] == 2  # evidence of the first completion is unchanged
