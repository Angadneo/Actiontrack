"""T-M5-09 (+ T-M5-08 on the sample): possible-duplicate links and the 18 identical-core sample clusters."""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from actiontrack.services import ingest as ing
from actiontrack.store.db import Database
from actiontrack.store.repo import Repo

from tests.conftest import FIXTURES_NOTES
from tests.integration.ingest_support import roster_cfg, run_ctx, write_note
from tests.integration.test_tier1_merge import ATT, note

CAPTURED: list = []


def spy_matcher(env, src):
    result = ing.match_or_create(env, src)
    CAPTURED.append(result)
    return result


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
    root = tmp_path_factory.mktemp("links")
    folder = root / "notes"
    folder.mkdir()
    for src in sorted(FIXTURES_NOTES.iterdir()):
        if src.is_file():
            (folder / src.name).write_bytes(src.read_bytes())
    db = root / "t.db"
    CAPTURED.clear()
    res = ingest(db, folder, match=spy_matcher)
    return db, res, list(CAPTURED), folder


# cluster core key -> (items, sorted sources per item, link reasons among the cluster's items)
#
# Final since T-M5-10 (Tier-1b): the dateless [x] source joins the evidence holder (the most recent matching open
# item), the earlier open copies become done with a `completed_by` link; see tests/integration/test_tier1b_sample.py
# for statuses, holders, `CI` and evidence. Here: items, sources per item, possible_duplicate reasons.
D = ["due_differs"]
CLUSTERS = {
    # --- Tier-1 merge (AC-07.12)
    "freeze invoice edits": (1, [2], []),
    # --- 6 due_differs clusters (AC-07.13), final: two items + one link
    "share updated wireframes": (2, [1, 1], D),
    "fix pagination bug": (2, [1, 1], D),
    "review schema mapping doc": (2, [1, 1], D),
    "estimate sso work": (2, [1, 1], D),
    "prepare renewal one pager": (2, [1, 1], D),
    "collect 5 customer quotes landing page": (2, [1, 1], D),
    # --- 11 Tier-1b clusters (T-M5-10): 2-open clusters keep the pre-existing due_differs link between the two
    # open items; every [x] source is attached to the holder (the most recent matching item)
    "send revised quote acme": (2, [1, 2], D),
    "confirm cloud budget with finance": (2, [1, 2], D),
    "set up staging environment": (1, [2], []),
    "export last quarter invoices": (1, [2], []),
    "finalise welcome email copy": (2, [1, 2], D),
    "write acceptance tests signup flow": (1, [2], []),
    "book renewal call with acmes cto": (2, [1, 2], D),
    "send acme procurement form": (1, [2], []),
    "deploy beta staging": (2, [1, 2], D),
    "approve q4 cloud spend": (1, [2], []),
    "notify customers about maintenance window": (1, [2], []),
}


def cluster_outcome(db, core):
    rows = q(db, "SELECT item_id, count(*) n FROM item_source WHERE core_key = ? GROUP BY item_id ORDER BY item_id", (core,))
    ids = {r["item_id"] for r in rows}
    links = [r for r in q(db, "SELECT * FROM item_link WHERE kind = 'possible_duplicate'")
             if r["from_item_id"] in ids and r["to_item_id"] in ids]
    return len(rows), sorted(r["n"] for r in rows), sorted(r["reason"] for r in links)


def test_eighteen_clusters_table_is_complete():
    assert len(CLUSTERS) == 18


@pytest.mark.parametrize("core", sorted(CLUSTERS))
@pytest.mark.ac("AC-07.12", "AC-07.13")
def test_cluster_outcome(sample, core):
    db = sample[0]
    n_items, per_item, reasons = CLUSTERS[core]
    assert cluster_outcome(db, core) == (n_items, sorted(per_item), sorted(reasons)), core


def test_only_identical_core_clusters_have_multiple_sources_and_links_are_due_differs_only(sample):
    db = sample[0]
    multi = {r["core_key"] for r in q(db, "SELECT core_key FROM item_source GROUP BY core_key HAVING count(*) > 1")}
    assert multi == set(CLUSTERS)
    assert Counter(r["reason"] for r in q(db, "SELECT reason FROM item_link WHERE kind = 'possible_duplicate'")) == {
        "due_differs": 11
    }
    assert q(db, "SELECT count(*) FROM item_link WHERE kind = 'completed_by'")[0][0] == 5


def test_totals_and_symmetric_storage(sample):
    db, res, captured, _ = sample
    assert q(db, "SELECT count(*) FROM item_source")[0][0] == 117
    assert q(db, "SELECT count(*) FROM action_item")[0][0] == 105
    assert res.counters.items_merged == 12 and res.counters.possible_duplicates == 11  # freeze + 11 Tier-1b
    assert all(
        r["from_item_id"] < r["to_item_id"] for r in q(db, "SELECT * FROM item_link WHERE kind = 'possible_duplicate'")
    )
    # links are reported through MatchResult, once, naming the OTHER (existing, lower-id) item
    assert sum(r.possible_duplicates for r in captured) == 11
    for r in captured:
        assert r.possible_duplicates == len(r.links)
        assert all(other < r.item_id for other, _reason in r.links)  # new item 9 vs existing 3 stored as (3, 9)


@pytest.mark.ac("AC-07.13")
def test_wireframes_pair_linked_first_wireframes_not(sample):
    db = sample[0]
    upd = q(db, "SELECT id, due_date FROM action_item WHERE task_text LIKE 'Share updated wireframes%' ORDER BY id")
    assert [r["due_date"] for r in upd] == ["2026-09-17", "2026-09-24"]
    link = q(db, "SELECT reason FROM item_link WHERE from_item_id = ? AND to_item_id = ?", (upd[0]["id"], upd[1]["id"]))
    assert [r["reason"] for r in link] == ["due_differs"]
    first = q(db, "SELECT id FROM action_item WHERE task_text LIKE '%first wireframes%'")
    assert first
    for r in first:
        assert q(db, "SELECT 1 FROM item_link WHERE from_item_id = ? OR to_item_id = ?", (r["id"], r["id"])) == []


@pytest.mark.ac("AC-07.10")
def test_similar_but_different_pairs_have_no_link(sample):
    db = sample[0]
    for like in ("%usage report%", "%procurement contact%"):
        for r in q(db, "SELECT id FROM action_item WHERE task_text LIKE ?", (like,)):
            assert q(db, "SELECT 1 FROM item_link WHERE from_item_id = ? OR to_item_id = ?", (r["id"], r["id"])) == [], like


def test_rerun_over_unchanged_folder_adds_no_links(sample, tmp_path):
    db, _, _, folder = sample
    copy = tmp_path / "t.db"
    copy.write_bytes(db.read_bytes())
    before = q(copy, "SELECT count(*) FROM item_link")[0][0]
    res = ingest(copy, folder)
    assert res.counters.files_unchanged == 33
    assert q(copy, "SELECT count(*) FROM item_link")[0][0] == before == 16  # 11 possible_duplicate + 5 completed_by


def test_insert_link_is_idempotent_and_symmetric(tmp_path):
    db = Database.open(tmp_path / "x.db")
    repo = Repo(db)
    a = repo.insert_action_item(description="a", task_text="a", description_core_key="a")
    b = repo.insert_action_item(description="a", task_text="a", description_core_key="a")
    assert repo.insert_link(b, a, "possible_duplicate", "due_differs") is True
    assert repo.insert_link(a, b, "possible_duplicate", "owner_differs") is False  # stored once
    assert [(r.from_item_id, r.to_item_id, r.reason) for r in repo.list_links()] == [(a, b, "due_differs")]
    db.close()


# ------------------------------------------------------------------------------- synthetic reasons
def run_pair(tmp_path, a, b, *, same_file=False, attendees=ATT):
    folder = tmp_path / "notes"
    if same_file:
        write_note(folder, "2026-10-14-a.md", note("2026-10-14", a, b, attendees=attendees))
    else:
        write_note(folder, "2026-10-14-a.md", note("2026-10-14", a, attendees=attendees))
        write_note(folder, "2026-10-15-b.md", note("2026-10-15", b, attendees=attendees))
    db = tmp_path / "t.db"
    res = ingest(db, folder)
    return db, res


@pytest.mark.ac("AC-07.4")
@pytest.mark.sc("SC-32")
def test_existing_done_new_open_new_item_old_stays_done(tmp_path):
    db, _ = run_pair(tmp_path, "- [x] Rahul Mehta to freeze invoice edits", "- [ ] Rahul Mehta to freeze invoice edits")
    items = q(db, "SELECT id, status FROM action_item ORDER BY id")
    assert [r["status"] for r in items] == ["done", "open"]
    assert [r["reason"] for r in q(db, "SELECT reason FROM item_link")] == ["status_differs"]


def test_fuzzy_same_owner_links_but_never_merges(tmp_path):
    base = "Rahul Mehta to review alpha beta gamma delta epsilon zeta eta theta iota"
    db, _ = run_pair(tmp_path, f"- [ ] {base}", f"- [ ] {base} kappa")
    assert q(db, "SELECT count(*) FROM action_item")[0][0] == 2
    assert [r["reason"] for r in q(db, "SELECT reason FROM item_link")] == ["fuzzy"]


def test_fuzzy_text_with_different_owners_is_no_link(tmp_path):
    base = "to review alpha beta gamma delta epsilon zeta eta theta iota"
    db, _ = run_pair(tmp_path, f"- [ ] Rahul Mehta {base}", f"- [ ] Karan Joshi {base} kappa")
    assert q(db, "SELECT count(*) FROM action_item")[0][0] == 2
    assert q(db, "SELECT count(*) FROM item_link")[0][0] == 0


def test_same_file_fuzzy_lines_link(tmp_path):
    base = "Rahul Mehta to review alpha beta gamma delta epsilon zeta eta theta iota"
    db, _ = run_pair(tmp_path, f"- [ ] {base}", f"- [ ] {base} kappa", same_file=True)
    assert [r["reason"] for r in q(db, "SELECT reason FROM item_link")] == ["fuzzy"]


@pytest.mark.ac("AC-07.3")
@pytest.mark.sc("SC-39")
def test_owner_differs_beats_due_differs(tmp_path):
    db, _ = run_pair(
        tmp_path,
        "- [ ] Rahul Mehta to freeze invoice edits by 2026-10-30",
        "- [ ] Karan Joshi to freeze invoice edits by 2026-10-31",
    )
    assert [r["reason"] for r in q(db, "SELECT reason FROM item_link")] == ["owner_differs"]


def test_due_differs_beats_same_file(tmp_path):
    db, _ = run_pair(
        tmp_path,
        "- [ ] Rahul Mehta to freeze invoice edits by 2026-10-30",
        "- [ ] Rahul Mehta to freeze invoice edits by 2026-10-31",
        same_file=True,
    )
    assert [r["reason"] for r in q(db, "SELECT reason FROM item_link")] == ["due_differs"]


# ------------------------------------------------------------------------- Tier-1b boundary (T-M5-10)
def test_tier1b_dateless_done_copy_completes_open_item_with_due_date_without_link(tmp_path):
    """Since T-M5-10 a dateless [x] copy of an earlier OPEN item with a due date is a Tier-1b completion: one item
    (the holder), done, no possible_duplicate link, no completed_by link (single match: no CI); with both undated
    it is the 10.6.1 row 5 Tier-1 merge with the same visible outcome."""
    line = "Rahul Mehta to send revised quote"
    folder = tmp_path / "a" / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {line} to Acme by 2026-09-04"))
    write_note(folder, "2026-09-15-b.md", note("2026-09-15", f"- [x] {line} to Acme"))
    db = tmp_path / "a" / "t.db"
    ingest(db, folder)
    (item,) = q(db, "SELECT * FROM action_item")
    assert (item["status"], item["completion_origin"], item["completed_at"]) == ("done", "note", None)
    assert q(db, "SELECT count(*) FROM item_link")[0][0] == 0
    assert q(db, "SELECT count(*) FROM item_source")[0][0] == 2

    folder = tmp_path / "b" / "notes"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", f"- [ ] {line} to Acme"))
    write_note(folder, "2026-09-15-b.md", note("2026-09-15", f"- [x] {line} to Acme"))
    db = tmp_path / "b" / "t.db"
    ingest(db, folder)
    (item,) = q(db, "SELECT * FROM action_item")
    assert (item["status"], item["completion_origin"], item["completed_at"]) == ("done", "note", None)
    assert q(db, "SELECT count(*) FROM item_source")[0][0] == 2
