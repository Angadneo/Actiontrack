"""Amended D-04 on the real sample: a DATELESS later restatement merges into the dated item (spec 10.6).

Two clusters change (``prepare renewal one-pager`` / Priya Nair and ``review schema mapping doc`` / Rahul
Verma): one item each, two sources, the dated item's due kept, no ``due_differs`` link. The clusters where the
dateless source is the EARLIER one (estimate SSO work, fix pagination bug) stay separate + linked.
The sample is ingested ONCE per module through the real CLI.
"""

from __future__ import annotations

import io as _io
import json
import shutil
import sqlite3
from contextlib import redirect_stderr, redirect_stdout

import pytest

from actiontrack import cli
from actiontrack.services import followup as fu
from tests.conftest import FIXTURES_NOTES, REPO_ROOT

CONFIG = str(REPO_ROOT / "tests" / "fixtures" / "config" / "valid_full.toml")
TODAY = "2026-10-22"

ONE_PAGER = ("2026-09-28-acme-renewal-checkin.md", "2026-10-14-acme-renewal-wrapup.md")
SCHEMA_DOC = ("2026-09-03-billing-migration-sync.md", "2026-09-17-billing-migration-sync.md")


def invoke(db, *args):
    out, err = _io.StringIO(), _io.StringIO()
    argv = [*args, "--config", CONFIG, "--today", TODAY, "--db", str(db)]
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture(scope="module")
def sample_db(tmp_path_factory):
    root = tmp_path_factory.mktemp("dateless")
    notes = root / "notes"
    shutil.copytree(FIXTURES_NOTES, notes)
    db = root / "a.db"
    rc, _out, _err = invoke(db, "ingest", str(notes), "--quiet")
    assert rc == 5  # scratch.md is a deliberately binary file
    return db


def q(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def sources_of(db, core_like):
    return q(
        db,
        "SELECT s.item_id, f.rel_path, s.due_confidence FROM item_source s "
        "JOIN source_file f ON f.id = s.source_file_id WHERE s.core_key LIKE ? ORDER BY s.id",
        (core_like,),
    )


@pytest.mark.parametrize(
    ("core_like", "files", "due", "owner"),
    [
        ("%renewal one%pager", ONE_PAGER, "2026-10-02", "priya-nair"),
        ("%review%schema mapping%", SCHEMA_DOC, "2026-09-07", "rahul-verma"),
    ],
)
def test_dated_then_dateless_cluster_is_one_item(sample_db, core_like, files, due, owner):
    rows = sources_of(sample_db, core_like)
    assert [r["rel_path"] for r in rows] == list(files)
    assert [r["due_confidence"] == "none" for r in rows] == [False, True]  # dated first, dateless later
    ids = {r["item_id"] for r in rows}
    assert len(ids) == 1
    (item_id,) = ids
    item = q(sample_db, "SELECT * FROM action_item WHERE id = ?", (item_id,))[0]
    assert item["due_date"] == due and item["due_confidence"] != "none"  # not erased by the dateless source
    assert item["status"] == "open"
    assert [r["person_id"] for r in q(sample_db, "SELECT person_id FROM item_owner WHERE item_id = ?", (item_id,))] == [owner]
    assert q(sample_db, "SELECT count(*) FROM item_link WHERE from_item_id = ? OR to_item_id = ?", (item_id, item_id))[0][0] == 0


@pytest.mark.parametrize("core_like", ["%estimate%sso%", "%pagination%"])
def test_dateless_earlier_clusters_are_unchanged(sample_db, core_like):
    rows = sources_of(sample_db, core_like)
    assert len({r["item_id"] for r in rows}) >= 2  # still separate items
    ids = sorted({r["item_id"] for r in rows})
    links = q(sample_db, "SELECT reason FROM item_link WHERE kind = 'possible_duplicate' AND from_item_id IN (%s)"
              % ",".join("?" * len(ids)), tuple(ids))
    assert "due_differs" in {r["reason"] for r in links}


def test_new_sample_totals(sample_db):
    assert q(sample_db, "SELECT count(*) FROM item_source")[0][0] == 117
    assert q(sample_db, "SELECT count(*) FROM action_item")[0][0] == 103  # was 105
    assert dict((r[0], r[1]) for r in q(sample_db, "SELECT status, count(*) FROM action_item GROUP BY status")) == {
        "open": 85,
        "done": 18,
    }
    links = {(r[0], r[1]): r[2] for r in q(sample_db, "SELECT kind, reason, count(*) FROM item_link GROUP BY 1, 2")}
    assert links == {("possible_duplicate", "due_differs"): 9, ("completed_by", None): 5}
    # merges: 117 sources - 103 items = 14 (was 12)
    assert 117 - 103 == 14


def test_list_all_status_is_103_items(sample_db):
    rc, out, _ = invoke(sample_db, "list", "--format", "json", "--status", "all")
    assert rc == 0 and len(json.loads(out)["items"]) == 103


def test_list_open_count_is_85(sample_db):
    rc, out, _ = invoke(sample_db, "list")
    assert rc == 0 and out.split("\n")[0] == f"As of {TODAY} (tz Asia/Kolkata) — 85 items"


def test_list_owner_shows_one_pager_once_with_due_and_two_sources(sample_db):
    rc, out, _ = invoke(sample_db, "list", "--owner", "Priya Nair")
    assert rc == 0
    lines = out.split("\n")
    assert lines[0].endswith("5 items")
    pager = [ln for ln in lines if "Prepare the renew" in ln]
    assert len(pager) == 1
    assert pager[0].split()[2] == "2026-10-02" and pager[0].rstrip().endswith("CR")
    rc, out, _ = invoke(sample_db, "list", "--owner", "Priya Nair", "--format", "json")
    items = json.loads(out)["items"]
    assert len(items) == 5
    (one,) = [i for i in items if i["task_text"] == "Prepare the renewal one-pager"]
    assert one["due_date"] == "2026-10-02" and one["flags"] == ["CR"] and one["links"] == []
    assert [s["rel_path"] for s in one["sources"]] == list(ONE_PAGER)
    assert all(i["display_status"] == "overdue" for i in items)


def test_followup_dry_run_previews_five_overdue_items_without_undated_section(sample_db, tmp_path, monkeypatch):
    work = tmp_path / "work.db"
    shutil.copy2(sample_db, work)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(cli.HANDLERS, "followup", fu.followup_handler)
    monkeypatch.setattr(fu, "stdin_is_tty", lambda: False)
    rc, out, _ = invoke(work, "followup", "Priya Nair", "--dry-run")
    assert rc == 0
    assert "Items: 5 open (5 overdue)" in out
    assert "Subject: Follow-up: your open action items (5 open, 5 overdue)" in out
    body = out.split("Hi Priya Nair,", 1)[1]
    assert "Overdue (5)" in body
    assert body.count("Prepare the renewal one-pager") == 1
    assert "was due 2026-10-02 (20 days overdue)" in body
    assert "No due date set" not in out
