"""T-M5-13: deleted / renamed / failed-after-success files, ``--no-recursive`` scope, ``source_state``.

AC-08.3, AC-08.4, AC-08.7, AC-08.10 (no-recursive half), SC-27, SC-68, spec 10.7.4, 10.7.5, 10.7.8."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.services import ingest as ing

from tests.integration.ingest_support import dump_tables, roster_cfg, run_ctx, write_note
from tests.integration.test_tier1_merge import note

BINARY = b"\x00\x01\x02 not text \x00"


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


def states(db) -> dict[str, str]:
    return {r["rel_path"]: r["status"] for r in q(db, "SELECT rel_path, status FROM source_file")}


def warns(db, run_id: int, code: str) -> list[sqlite3.Row]:
    return q(db, "SELECT * FROM ingest_warning WHERE run_id = ? AND code = ?", (run_id, code))


def three(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-01-a.md", note("2026-09-01", "- [ ] Rahul Mehta to book the room"))
    write_note(folder, "2026-09-02-b.md", note("2026-09-02", "- [ ] Priya Nair to write the budget memo"))
    write_note(folder, "2026-09-03-c.md", note("2026-09-03", "- [x] Anita Desai to ship it"))
    ingest(db, folder)
    return db, folder


@pytest.mark.ac("AC-08.4", "AC-08.3")
@pytest.mark.sc("SC-27")
def test_deleted_file_is_missing_items_kept_with_sm_and_warning(tmp_path):
    db, folder = three(tmp_path)
    before = {r["id"]: r for r in q(db, "SELECT * FROM action_item")}
    (folder / "2026-09-02-b.md").unlink()
    res = ingest(db, folder)
    assert res.exit_code == 0
    assert states(db)["2026-09-02-b.md"] == "missing"
    assert [m.rel_path for m in res.missing] == ["2026-09-02-b.md"]
    (w,) = warns(db, res.run_row.id, codes.W_FILE_MISSING)
    assert "2026-09-02-b.md" in w["message"]
    rows = {r["id"]: r for r in q(db, "SELECT * FROM action_item")}
    assert set(rows) == set(before)
    missing = [i for i, r in rows.items() if r["source_state"] == "missing"]
    assert len(missing) == 1 and rows[missing[0]]["status"] == before[missing[0]]["status"]
    assert all(r["source_state"] == "present" for i, r in rows.items() if i not in missing)
    assert q(db, "SELECT present FROM item_source WHERE item_id = ?", (missing[0],))[0][0] == 0
    # counters: the missing file is not a discovered file
    assert res.counters.files_discovered == 2 and res.counters.invariant_holds()


def test_second_run_after_deletion_changes_nothing(tmp_path):
    db, folder = three(tmp_path)
    (folder / "2026-09-02-b.md").unlink()
    ingest(db, folder)
    snap = dump(db)
    res = ingest(db, folder)
    assert dump(db) == snap
    assert not warns(db, res.run_row.id, codes.W_FILE_MISSING) and res.missing == ()


def test_missing_file_back_with_identical_content_is_revived_without_new_items(tmp_path):
    db, folder = three(tmp_path)
    text = (folder / "2026-09-02-b.md").read_bytes()
    (folder / "2026-09-02-b.md").unlink()
    ingest(db, folder)
    snapshot_items = q(db, "SELECT id FROM action_item ORDER BY id")
    (folder / "2026-09-02-b.md").write_bytes(text)
    res = ingest(db, folder)
    assert states(db)["2026-09-02-b.md"] == "ok"
    assert q(db, "SELECT id FROM action_item ORDER BY id") == snapshot_items
    assert all(r["source_state"] == "present" for r in q(db, "SELECT source_state FROM action_item"))
    assert all(r["present"] == 1 for r in q(db, "SELECT present FROM item_source"))
    assert not q(db, "SELECT 1 FROM item_link WHERE reason = 'same_file'")
    assert res.counters.items_new == 0
    snap = dump(db)
    ingest(db, folder)
    assert dump(db) == snap


def test_missing_file_back_with_changed_content_is_reconciled(tmp_path):
    db, folder = three(tmp_path)
    (folder / "2026-09-02-b.md").unlink()
    ingest(db, folder)
    write_note(folder, "2026-09-02-b.md", note("2026-09-02", "- [ ] Priya Nair to write the budget memo", "- [ ] Karan Joshi to add something new"))
    res = ingest(db, folder)
    assert res.counters.items_new == 1
    assert q(db, "SELECT count(*) n FROM action_item")[0]["n"] == 4
    assert all(r["source_state"] == "present" for r in q(db, "SELECT source_state FROM action_item"))


def test_deleted_file_does_not_mark_a_merged_item_missing_while_another_source_is_present(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    line = "- [ ] Rahul Mehta to freeze invoice edits"
    write_note(folder, "2026-09-29-a.md", note("2026-09-29", line))
    write_note(folder, "2026-10-08-b.md", note("2026-10-08", line))
    ingest(db, folder)
    (folder / "2026-09-29-a.md").unlink()
    ingest(db, folder)
    assert q(db, "SELECT source_state FROM action_item")[0][0] == "present"
    (folder / "2026-10-08-b.md").unlink()
    ingest(db, folder)
    assert q(db, "SELECT source_state FROM action_item")[0][0] == "missing"


@pytest.mark.ac("AC-08.4")
def test_renamed_file_is_delete_plus_add_and_matches_the_old_items(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-01-old.md", note("2026-09-01", "- [ ] Rahul Mehta to book the room", "- [ ] Priya Nair to write the budget memo"))
    ingest(db, folder)
    ids = [r["id"] for r in q(db, "SELECT id FROM action_item ORDER BY id")]
    (folder / "2026-09-01-old.md").rename(folder / "2026-09-01-new.md")
    res = ingest(db, folder)
    assert states(db) == {"2026-09-01-old.md": "missing", "2026-09-01-new.md": "ok"}
    assert [m.rel_path for m in res.missing] == ["2026-09-01-old.md"]
    assert [o.rel_path for o in res.outcomes if o.result == "ingested"] == ["2026-09-01-new.md"]
    items = q(db, "SELECT * FROM action_item ORDER BY id")
    # the new file's items merge into the old ones (Tier-1) or become new items linked as possible duplicates
    assert [r["id"] for r in items[: len(ids)]] == ids
    assert all(r["source_state"] == "present" for r in items[: len(ids)]) or len(items) > len(ids)
    old_present = q(
        db,
        "SELECT count(*) n FROM item_source s JOIN source_file f ON f.id = s.source_file_id "
        "WHERE f.rel_path = '2026-09-01-old.md' AND s.present = 1",
    )[0]["n"]
    assert old_present == 0
    assert not q(db, "SELECT 1 FROM item_link WHERE reason = 'same_file'")
    assert len(warns(db, res.run_row.id, codes.W_FILE_MISSING)) == 1


@pytest.mark.ac("AC-08.7")
def test_good_file_replaced_by_binary_is_failed_not_missing(tmp_path):
    db, folder = three(tmp_path)
    snap = {k: v for k, v in dump(db).items() if k not in ("source_file",)}
    (folder / "2026-09-02-b.md").write_bytes(BINARY)
    res = ingest(db, folder)
    assert res.exit_code == 5 and res.counters.files_failed == 1
    assert states(db)["2026-09-02-b.md"] == "failed"
    assert res.missing == () and not warns(db, res.run_row.id, codes.W_FILE_MISSING)
    after = {k: v for k, v in dump(db).items() if k not in ("source_file",)}
    assert after == snap
    assert all(r["source_state"] == "present" for r in q(db, "SELECT source_state FROM action_item"))
    assert all(r["present"] == 1 for r in q(db, "SELECT present FROM item_source"))


@pytest.mark.ac("AC-08.7")
def test_good_file_that_becomes_empty_keeps_its_items(tmp_path):
    db, folder = three(tmp_path)
    (folder / "2026-09-02-b.md").write_bytes(b"")
    res = ingest(db, folder)
    assert res.exit_code == 0 and res.counters.files_empty == 1
    assert states(db)["2026-09-02-b.md"] == "empty"
    assert all(r["source_state"] == "present" for r in q(db, "SELECT source_state FROM action_item"))
    assert all(r["present"] == 1 for r in q(db, "SELECT present FROM item_source"))
    assert not warns(db, res.run_row.id, codes.W_FILE_MISSING)


def test_two_folder_roots_in_one_database_never_cross(tmp_path):
    db = tmp_path / "t.db"
    a, b = tmp_path / "a", tmp_path / "b"
    write_note(a, "2026-09-01-a.md", note("2026-09-01", "- [ ] Rahul Mehta to book the room"))
    write_note(b, "2026-09-02-b.md", note("2026-09-02", "- [ ] Priya Nair to write the budget memo"))
    ingest(db, a)
    ingest(db, b)
    res = ingest(db, b)
    assert res.missing == () and not warns(db, res.run_row.id, codes.W_FILE_MISSING)
    assert set(states(db).values()) == {"ok"}
    assert all(r["source_state"] == "present" for r in q(db, "SELECT source_state FROM action_item"))
    (a / "2026-09-01-a.md").unlink()
    res = ingest(db, b)  # still root B only: A's deleted file is not B's business
    assert res.missing == () and set(states(db).values()) == {"ok"}
    res = ingest(db, a)
    assert [m.rel_path for m in res.missing] == ["2026-09-01-a.md"]


# -------------------------------------------------------------------------------------- --no-recursive
def nested(tmp_path):
    folder, db = tmp_path / "n", tmp_path / "t.db"
    write_note(folder, "2026-09-01-top.md", note("2026-09-01", "- [ ] Rahul Mehta to book the room"))
    write_note(folder, "sub/2026-09-02-nested.md", note("2026-09-02", "- [ ] Priya Nair to write the budget memo"))
    write_note(folder, "sub/deeper/2026-09-03-deep.md", note("2026-09-03", "- [ ] Anita Desai to ship it"))
    ingest(db, folder)
    return db, folder


@pytest.mark.ac("AC-08.10")
@pytest.mark.sc("SC-68")
def test_no_recursive_does_not_mark_nested_files_missing(tmp_path):
    db, folder = nested(tmp_path)
    snap = dump(db)
    res = ingest(db, folder, recursive=False)
    assert res.missing == () and not warns(db, res.run_row.id, codes.W_FILE_MISSING)
    assert set(states(db).values()) == {"ok"}
    assert all(r["source_state"] == "present" for r in q(db, "SELECT source_state FROM action_item"))
    assert all(r["present"] == 1 for r in q(db, "SELECT present FROM item_source"))
    assert dump(db) == snap


@pytest.mark.ac("AC-08.10")
@pytest.mark.sc("SC-68")
def test_nested_file_deleted_under_no_recursive_is_untouched_until_the_next_recursive_run(tmp_path):
    db, folder = nested(tmp_path)
    (folder / "sub" / "2026-09-02-nested.md").unlink()
    res = ingest(db, folder, recursive=False)
    assert res.missing == () and states(db)["sub/2026-09-02-nested.md"] == "ok"
    res = ingest(db, folder)
    assert [m.rel_path for m in res.missing] == ["sub/2026-09-02-nested.md"]
    assert states(db)["sub/2026-09-02-nested.md"] == "missing"


@pytest.mark.ac("AC-08.10")
def test_top_level_file_deleted_under_no_recursive_is_marked_missing(tmp_path):
    db, folder = nested(tmp_path)
    (folder / "2026-09-01-top.md").unlink()
    res = ingest(db, folder, recursive=False)
    assert [m.rel_path for m in res.missing] == ["2026-09-01-top.md"]
    assert states(db)["sub/2026-09-02-nested.md"] == "ok"


@pytest.mark.ac("AC-08.10")
@pytest.mark.sc("SC-68")
def test_sample_set_one_level_down_survives_a_no_recursive_run(tmp_path, copy_fixture_notes):
    parent = tmp_path / "parent"
    copy_fixture_notes(dest=parent, nested="notes")
    db = tmp_path / "t.db"
    first = ingest(db, parent)
    snap = dump(db)
    n_files = q(db, "SELECT count(*) n FROM source_file")[0]["n"]
    res = ingest(db, parent, recursive=False)
    assert res.counters.files_discovered == 0 and res.missing == ()
    assert not warns(db, res.run_row.id, codes.W_FILE_MISSING)
    assert q(db, "SELECT count(*) n FROM source_file WHERE status = 'missing'")[0]["n"] == 0
    assert q(db, "SELECT count(*) n FROM action_item WHERE source_state = 'missing'")[0]["n"] == 0
    assert q(db, "SELECT count(*) n FROM item_source WHERE present = 0")[0]["n"] == 0
    assert q(db, "SELECT count(*) n FROM source_file")[0]["n"] == n_files
    assert dump(db) == snap
    assert first.counters.files_ingested > 0


# ------------------------------------------------------------------------------------------ whole sample
def test_deleting_one_sample_note_keeps_all_items(tmp_path, copy_fixture_notes):
    folder = copy_fixture_notes()
    db = tmp_path / "t.db"
    ingest(db, folder)
    items = q(db, "SELECT id, status FROM action_item ORDER BY id")
    (folder / "2026-09-03-billing-migration-sync.md").unlink()
    res = ingest(db, folder)
    assert q(db, "SELECT id, status FROM action_item ORDER BY id") == items
    assert len(warns(db, res.run_row.id, codes.W_FILE_MISSING)) == 1
    assert q(db, "SELECT count(*) n FROM action_item WHERE source_state = 'missing'")[0]["n"] >= 1
    snap = dump(db)
    ingest(db, folder)
    assert dump(db) == snap


# ------------------------------------------------------------------------------------ source_state API
def test_recompute_source_state_is_idempotent_and_never_touches_status(tmp_path):
    db, folder = three(tmp_path)
    (folder / "2026-09-02-b.md").unlink()
    ingest(db, folder)
    snap = dump(db)
    with ing.ingest_session(db, folder) as s:
        with s.db.transaction():
            assert ing.recompute_source_state(s.repo) == []
            assert ing.recompute_source_state(s.repo, [1, 2, 3]) == []
    assert dump(db) == snap


def test_failure_while_marking_missing_rolls_back_that_file_and_the_run_continues(tmp_path):
    db, folder = three(tmp_path)
    (folder / "2026-09-02-b.md").unlink()
    (folder / "2026-09-03-c.md").unlink()
    snap = {k: v for k, v in dump(db).items() if k != "item_event"}

    def hook(name):
        if name == "missing_written:2026-09-02-b.md":
            raise RuntimeError("injected")

    res = ingest(db, folder, fault_hook=hook)
    bad, good = res.missing
    assert bad.failure_code == codes.E_INTERNAL_PARSE and good.failure_code is None
    st = states(db)
    assert st["2026-09-02-b.md"] == "ok" and st["2026-09-03-c.md"] == "missing"
    after = dump(db)
    assert after["item_source"] != snap["item_source"]  # c's source went absent ...
    rows = {r["rel_path"]: r["id"] for r in q(db, "SELECT id, rel_path FROM source_file")}
    assert q(db, "SELECT present FROM item_source WHERE source_file_id = ?", (rows["2026-09-02-b.md"],))[0][0] == 1  # ... b's did not
    res = ingest(db, folder)  # the retry marks b
    assert [m.rel_path for m in res.missing] == ["2026-09-02-b.md"] and states(db)["2026-09-02-b.md"] == "missing"
