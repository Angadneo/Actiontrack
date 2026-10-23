"""T-M5-19: fault injection around the ingest service (AC-08.5 / SC-28, AC-08.7, NFR-REL-01/02, BR-15..17, 10.7.6/10.7.8).

A fault is injected through ``run_ingest(..., fault_hook=...)`` (``db.fault(name)`` points, see the section
comment of ``services/ingest.py``). A ``SimulatedCrash(BaseException)`` stands for a kill: no ``except Exception``
handler may run, the run stays ``in_progress`` and the next run must recover. A plain ``Exception`` stands for a
bug inside one unit of work: only that unit may be rolled back.

What the spec promises and what is asserted here:

* AC-08.5 / SC-28: after a kill after file ``k`` (every k of the sample's 33 ingestable files) a rerun reaches the SAME
  business state as an uninterrupted run (full table dumps; only run-id columns are masked because the two runs
  legitimately differ in WHICH run wrote a row), the dead run is ``interrupted`` and no item is duplicated.
* NFR-REL-01 / 10.7.6: a kill INSIDE a file transaction (``file_start`` / ``file_written`` / ``before_commit``) leaves the
  database exactly as it was after the previous file (a Tier-1b completion that touched OLD items is rolled back
  with the file), never a half-written file.
* 10.7.9 / 10.7.5 / 10.7.2: faults in the missing-file, reconcile and finalisation units roll back only that unit.
* AC-08.7 / 10.7.8: a previously OK file that becomes unreadable keeps its items, is not marked missing, is
  ``failed`` and the run exits 5.
"""

from __future__ import annotations

import dataclasses
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing import reader

from tests.integration.ingest_support import simple_note, write_note
from tests.integration.whole_db_support import (
    copy_sample,
    dump,
    dump_masked,
    ingest,
    make_cfg,
    q,
    structural_violations,
)

SAMPLE_OK_FILES = 33  # 35 .md = 33 ingestable + 1 empty + 1 binary (spec A.2)


class SimulatedCrash(BaseException):
    """A kill: not an ``Exception``, so no per-file / finalisation handler may swallow it."""


def hook_at(*names: str, nth: int = 1, exc: type[BaseException] = SimulatedCrash):
    """Raise ``exc`` the ``nth`` time any of ``names`` fires."""
    seen: Counter[str] = Counter()

    def hook(name: str) -> None:
        if name in names:
            seen[name] += 1
            if seen[name] == nth:
                raise exc(name)

    return hook


class Boom(Exception):
    """A bug inside a unit of work (isolated, not a kill)."""


# ---------------------------------------------------------------------------------------------------- sample baseline
@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    root = tmp_path_factory.mktemp("fault_sample")
    folder = copy_sample(root)
    cfg = make_cfg(root)
    res = ingest(root / "base.db", folder, cfg)
    assert res.counters.files_ingested == SAMPLE_OK_FILES and res.exit_code == 5
    assert structural_violations(root / "base.db") == []
    order = [e.rel_path for e in res.plan.entries]
    assert len(order) == SAMPLE_OK_FILES
    return folder, cfg, dump_masked(root / "base.db"), order


def person_orphans(db: Path) -> list[str]:
    """Non-roster people (attendee / provisional) that nothing references: a half-written file would leave them."""
    sql = (
        "SELECT p.id FROM person p WHERE p.origin != 'roster' "
        "AND p.id NOT IN (SELECT person_id FROM meeting_attendee WHERE person_id IS NOT NULL) "
        "AND p.id NOT IN (SELECT person_id FROM item_owner WHERE person_id IS NOT NULL) "
        "AND p.id NOT IN (SELECT person_id FROM item_source_owner WHERE person_id IS NOT NULL)"
    )
    return [r[0] for r in q(db, sql)]


def assert_clean(db: Path) -> None:
    assert structural_violations(db) == []
    assert person_orphans(db) == []


def committed_files(db: Path) -> list[str]:
    return [r[0] for r in q(db, "SELECT rel_path FROM source_file WHERE status = 'ok' ORDER BY id")]


# ------------------------------------------------------------------------------------------------ AC-08.5 / SC-28
@pytest.mark.ac("AC-08.5")
@pytest.mark.sc("SC-28")
@pytest.mark.parametrize("k", range(1, SAMPLE_OK_FILES + 1))
def test_kill_after_file_k_then_rerun_equals_an_uninterrupted_run(sample, tmp_path, k):
    folder, cfg, baseline, order = sample
    db = tmp_path / "k.db"
    with pytest.raises(SimulatedCrash):
        ingest(db, folder, cfg, fault_hook=hook_at(f"after_file:{k}"))
    # what a kill leaves behind: the run stays in_progress, exactly the first k files are committed, nothing half-written
    (crashed,) = q(db, "SELECT id, status, finished_at FROM ingest_run")
    assert (crashed["status"], crashed["finished_at"]) == ("in_progress", None)
    assert committed_files(db) == order[:k]
    assert_clean(db)

    rerun = ingest(db, folder, cfg)
    assert [r.run_id for r in rerun.run.interrupted] == [crashed["id"]]  # reported (10.7.6)
    assert q(db, "SELECT status FROM ingest_run WHERE id = ?", (crashed["id"],))[0][0] == "interrupted"
    assert (rerun.counters.files_unchanged, rerun.counters.files_ingested) == (k, SAMPLE_OK_FILES - k)
    assert rerun.counters.invariant_holds() and rerun.exit_code == 5  # scratch.md still fails
    assert_clean(db)
    assert dump_masked(db) == baseline  # same final business state, ids included
    assert q(db, "SELECT count(*) FROM action_item")[0][0] == 103  # no duplicate items
    assert q(db, "SELECT count(*) FROM item_source")[0][0] == 117


@pytest.mark.ac("AC-08.5")
@pytest.mark.sc("SC-28")
def test_two_kills_in_a_row_then_a_clean_run_still_converge(sample, tmp_path):
    folder, cfg, baseline, order = sample
    db = tmp_path / "kk.db"
    with pytest.raises(SimulatedCrash):
        ingest(db, folder, cfg, fault_hook=hook_at("after_file:5"))
    with pytest.raises(SimulatedCrash):  # the rerun walks 5 unchanged + 5 new files, then dies again
        ingest(db, folder, cfg, fault_hook=hook_at("after_file:12"))
    assert committed_files(db) == order[:12]
    assert [r["status"] for r in q(db, "SELECT status FROM ingest_run ORDER BY id")] == [
        "interrupted",
        "in_progress",
    ]
    final = ingest(db, folder, cfg)
    assert len(final.run.interrupted) == 1
    assert [r["status"] for r in q(db, "SELECT status FROM ingest_run ORDER BY id")] == [
        "interrupted",
        "interrupted",
        "partial",
    ]
    assert dump_masked(db) == baseline


@pytest.mark.ac("AC-08.5")
def test_kill_before_any_file_leaves_only_the_roster_and_the_dead_run(sample, tmp_path):
    folder, cfg, baseline, order = sample
    db = tmp_path / "k0.db"
    with pytest.raises(SimulatedCrash):
        ingest(db, folder, cfg, fault_hook=hook_at("file_start"))
    for table in ("source_file", "meeting", "action_item", "item_source", "item_owner", "item_link", "item_event"):
        assert q(db, f"SELECT count(*) FROM {table}")[0][0] == 0, table
    assert ingest(db, folder, cfg).run.interrupted[0].files_committed == 0
    assert dump_masked(db) == baseline


# --------------------------------------------------------------------- a kill INSIDE a file's transaction (NFR-REL-01)
#: files whose ingestion touches OLD items (Tier-1b completions, Tier-1 merges, links): the strongest atomicity cases
ATOMIC_FILES = (
    "2026-09-01-quote-portal-kickoff.md",  # the very first file
    "2026-09-08-quote-portal-weekly.md",  # possible_duplicate links to the kickoff item
    "2026-09-15-quote-portal-weekly.md",  # Tier-1b completion of revised-quote items from two older files
    "2026-10-08-billing-go-no-go.md",  # Tier-1 merge of `freeze invoice edits` + completion
    "untitled.md",  # the last plan entry (undated)
)


@pytest.mark.ac("AC-08.5")
@pytest.mark.sc("SC-28")
@pytest.mark.parametrize("point", ["file_start", "file_written", "before_commit"])
@pytest.mark.parametrize("rel", ATOMIC_FILES)
def test_kill_inside_a_file_transaction_leaves_that_file_entirely_absent(sample, tmp_path, rel, point):
    folder, cfg, baseline, order = sample
    j = order.index(rel) + 1
    names = {"file_start": f"file_start:{rel}", "file_written": f"file_written:{rel}", "before_commit": "before_commit"}
    hook = hook_at(names[point], nth=j if point == "before_commit" else 1)
    db = tmp_path / "inside.db"
    with pytest.raises(SimulatedCrash):
        ingest(db, folder, cfg, fault_hook=hook)
    assert committed_files(db) == order[: j - 1]  # nothing of file j is visible
    assert rel not in [r[0] for r in q(db, "SELECT rel_path FROM source_file")]
    assert_clean(db)
    # byte-for-byte the state after file j-1 was committed (completions/links/events of OLD items rolled back too)
    control = tmp_path / "control.db"
    if j > 1:
        with pytest.raises(SimulatedCrash):
            ingest(control, folder, cfg, fault_hook=hook_at(f"after_file:{j - 1}"))
        assert dump_masked(db) == dump_masked(control)
    else:
        for table in ("source_file", "meeting", "action_item", "item_source", "item_event", "item_link"):
            assert q(db, f"SELECT count(*) FROM {table}")[0][0] == 0, table
    final = ingest(db, folder, cfg)
    assert final.counters.files_ingested == SAMPLE_OK_FILES - j + 1
    assert dump_masked(db) == baseline


@pytest.mark.ac("AC-08.5")
@pytest.mark.parametrize("point", ["file_start", "file_written", "before_commit"])
def test_a_bug_inside_a_file_transaction_isolates_only_that_file(sample, tmp_path, point):
    """BR-17 / spec 11: a plain exception rolls the file back, marks it failed (E-INTERNAL-PARSE, exit 5), the other
    files complete, and a later clean run ingests it without duplicating anything."""
    folder, cfg, baseline, order = sample
    rel = "2026-09-15-quote-portal-weekly.md"
    j = order.index(rel) + 1
    names = {"file_start": f"file_start:{rel}", "file_written": f"file_written:{rel}", "before_commit": "before_commit"}
    db = tmp_path / "iso.db"
    res = ingest(db, folder, cfg, fault_hook=hook_at(names[point], nth=j if point == "before_commit" else 1, exc=Boom))
    out = {o.rel_path: o for o in res.outcomes}
    assert (out[rel].result, out[rel].failure_code) == ("failed", codes.E_INTERNAL_PARSE)
    assert res.counters.files_failed == 2 and res.counters.files_ingested == SAMPLE_OK_FILES - 1
    assert res.counters.invariant_holds() and res.exit_code == 5
    assert q(db, "SELECT status FROM ingest_run WHERE id = ?", (res.run.run_id,))[0][0] == "partial"
    (sf,) = q(db, "SELECT * FROM source_file WHERE rel_path = ?", (rel,))
    assert (sf["status"], sf["failure_code"]) == ("failed", codes.E_INTERNAL_PARSE)
    for table in ("meeting", "item_source"):
        assert q(db, f"SELECT count(*) FROM {table} WHERE {'source_file_id' if table == 'meeting' else 'source_file_id'} = ?", (sf["id"],))[0][0] == 0
    assert_clean(db)
    again = ingest(db, folder, cfg)
    assert again.counters.files_ingested == 1 and again.counters.files_unchanged == SAMPLE_OK_FILES - 1
    assert [o.result for o in again.outcomes if o.rel_path == rel] == ["ingested"]
    assert q(db, "SELECT status FROM source_file WHERE rel_path = ?", (rel,))[0][0] == "ok"
    assert_clean(db)
    assert q(db, "SELECT count(*) FROM (SELECT 1 FROM item_source GROUP BY source_file_id, line_start HAVING count(*) > 1)")[0][0] == 0
    # the retried file was matched late (documented order effect, 10.7.10), but it holds exactly its own sources
    assert q(db, "SELECT count(*) FROM item_source")[0][0] == 117


# --------------------------------------------------------------------------------- reconcile / missing / finalise units
V1_A = [
    "- [ ] Priya Nair to send the revised quote by Friday",
    "- [ ] Rahul Mehta to fix the login screen",
    "- [ ] Anita Desai to share the wireframes",
]
V2_A = [  # line 1 edited in place, line 3 removed, a new line added: paired + removed + new all fire
    "- [ ] Priya Nair to send the revised quote by Monday",
    "- [ ] Rahul Mehta to fix the login screen",
    "- [ ] Sam Okafor to book the offsite venue",
]
B_LINES = ["- [ ] Meera Iyer to renew the staging certificate", "- [x] Karan Joshi to revoke the stale token"]


@pytest.fixture
def small(tmp_path):
    folder = tmp_path / "n"
    folder.mkdir()
    write_note(folder, "a.md", simple_note("2026-09-01", "A", tuple(V1_A)))
    write_note(folder, "b.md", simple_note("2026-09-02", "B", tuple(B_LINES)))
    cfg = make_cfg(tmp_path)
    db = tmp_path / "s.db"
    first = ingest(db, folder, cfg)
    assert first.counters.files_ingested == 2 and first.exit_code == 0
    return folder, cfg, db, tmp_path


def edit_a(folder: Path) -> None:
    write_note(folder, "a.md", simple_note("2026-09-01", "A", tuple(V2_A)))


@pytest.fixture
def control_after_edit(small, tmp_path):
    """The business state of the same edit applied by a fault-free run (the reference for every reconcile fault)."""
    folder, cfg, db, tmp = small
    ref = tmp / "ref.db"
    shutil.copyfile(db, ref)
    edit_a(folder)
    ingest(ref, folder, cfg)
    write_note(folder, "a.md", simple_note("2026-09-01", "A", tuple(V1_A)))  # back to v1 for the faulty run
    return dump_masked(ref, mask=("mtime",))


@pytest.mark.ac("AC-08.5")
@pytest.mark.parametrize("point", ["reingest_paired", "reingest_removed", "reingest_new"])
def test_kill_inside_the_reconcile_of_a_changed_file_rolls_the_whole_file_back(small, control_after_edit, point):
    folder, cfg, db, tmp = small
    before = dump(db)
    edit_a(folder)
    with pytest.raises(SimulatedCrash):
        ingest(db, folder, cfg, fault_hook=hook_at(f"{point}:a.md"))
    assert dump(db) == before  # business tables byte-identical: events, sources, owners, hash of a.md untouched
    assert_clean(db)
    final = ingest(db, folder, cfg)
    assert len(final.run.interrupted) == 1
    assert (final.counters.files_ingested, final.counters.files_unchanged) == (1, 1)
    assert dump_masked(db, mask=("mtime",)) == control_after_edit


@pytest.mark.ac("AC-08.5")
@pytest.mark.ac("AC-08.7")
@pytest.mark.parametrize("point", ["reingest_paired", "reingest_removed", "reingest_new", "file_written"])
def test_a_bug_in_the_reconcile_of_a_changed_file_keeps_its_previous_data(small, control_after_edit, point):
    """10.7.8 / BR-17: the changed file fails (E-INTERNAL-PARSE, exit 5), its previous items and hash stay, nothing
    is marked missing, and a later clean run applies the change exactly like a run that never failed."""
    folder, cfg, db, tmp = small
    before = dump(db)
    old_hash = q(db, "SELECT content_sha256 FROM source_file WHERE rel_path = 'a.md'")[0][0]
    edit_a(folder)
    res = ingest(db, folder, cfg, fault_hook=hook_at(f"{point}:a.md", exc=Boom))
    assert res.exit_code == 5 and res.counters.files_failed == 1 and res.counters.files_unchanged == 1
    assert [o.failure_code for o in res.outcomes if o.rel_path == "a.md"] == [codes.E_INTERNAL_PARSE]
    after = dump(db)
    for table in ("action_item", "item_source", "item_owner", "item_event", "item_link", "meeting"):
        assert after[table] == before[table], table
    (sf,) = q(db, "SELECT status, content_sha256, failure_code FROM source_file WHERE rel_path = 'a.md'")
    assert (sf["status"], sf["content_sha256"], sf["failure_code"]) == ("failed", old_hash, codes.E_INTERNAL_PARSE)
    assert res.missing == () and q(db, "SELECT count(*) FROM action_item WHERE source_state = 'missing'")[0][0] == 0
    assert_clean(db)
    final = ingest(db, folder, cfg)
    assert (final.counters.files_ingested, final.exit_code) == (1, 0)
    assert dump_masked(db, mask=("mtime",)) == control_after_edit


@pytest.mark.ac("AC-08.4")
@pytest.mark.ac("AC-08.5")
@pytest.mark.parametrize("point", ["missing_start", "missing_written"])
def test_fault_while_marking_a_deleted_file_missing_rolls_back_only_that_file(small, point):
    folder, cfg, db, tmp = small
    before = dump(db)
    (folder / "b.md").unlink()
    res = ingest(db, folder, cfg, fault_hook=hook_at(f"{point}:b.md", exc=Boom))
    (m,) = res.missing
    assert (m.rel_path, m.failure_code) == ("b.md", codes.E_INTERNAL_PARSE)
    assert dump(db) == before  # b.md still `ok`, its sources present, no event
    assert q(db, "SELECT count(*) FROM ingest_warning WHERE code = ?", (codes.W_FILE_MISSING,))[0][0] == 0
    assert_clean(db)
    final = ingest(db, folder, cfg)  # retried by the next run
    assert [x.rel_path for x in final.missing] == ["b.md"] and final.missing[0].failure_code is None
    assert q(db, "SELECT status FROM source_file WHERE rel_path = 'b.md'")[0][0] == "missing"
    assert q(db, "SELECT count(*) FROM action_item WHERE source_state = 'missing'")[0][0] == 2
    assert_clean(db)


@pytest.mark.ac("AC-08.5")
def test_kill_while_marking_a_file_missing_is_recovered_by_the_next_run(small):
    folder, cfg, db, tmp = small
    before = dump(db)
    (folder / "b.md").unlink()
    with pytest.raises(SimulatedCrash):
        ingest(db, folder, cfg, fault_hook=hook_at("missing_written:b.md"))
    assert dump(db) == before
    final = ingest(db, folder, cfg)
    assert len(final.run.interrupted) == 1 and [m.rel_path for m in final.missing] == ["b.md"]
    assert q(db, "SELECT count(*) FROM action_item WHERE source_state = 'missing'")[0][0] == 2
    assert_clean(db)


NAMESAKE_NOTE = (
    "# Meeting: Sync\n\nDate: 2026-09-03\nAttendees: Priya Nair, Rahul Mehta\n\n## Action items\n\n"
    "- [ ] Priya to send the invoice pack by Friday\n"
)


def owner_rows(db: Path) -> list[tuple]:
    return [
        tuple(r)
        for r in q(db, "SELECT item_id, owner_status, person_id, provenance FROM item_owner ORDER BY item_id, ordinal")
    ]


@pytest.mark.ac("AC-08.5")
@pytest.mark.ac("AC-05.17")
@pytest.mark.parametrize("point", ["finalise_start", "finalise_written"])
def test_a_bug_in_the_finalisation_rolls_back_only_the_whole_database_passes(tmp_path, point):
    """10.7.9: the passes run in ONE transaction; when it fails the files already ingested are kept, the config
    change is NOT half-applied, and the next run applies it completely."""
    folder = tmp_path / "n"
    folder.mkdir()
    write_note(folder, "a.md", NAMESAKE_NOTE)
    db = tmp_path / "f.db"
    on, off = make_cfg(tmp_path), make_cfg(tmp_path, attendance=False, name="off.toml")
    ingest(db, folder, on)
    start = owner_rows(db)
    assert start[0][3] == "attendee_in_meeting"
    before = dump(db)
    res = ingest(db, folder, off, fault_hook=hook_at(point, exc=Boom))
    assert res.finalisation_error is not None and "finalisation failed" in res.finalisation_error
    assert dump(db) == before and owner_rows(db) == start  # nothing of the pass is visible
    warns = [w["code"] for w in q(db, "SELECT code FROM ingest_warning WHERE run_id = ?", (res.run.run_id,))]
    assert codes.E_INTERNAL_PARSE in warns and codes.W_OWNER_NOW_AMBIGUOUS not in warns
    assert q(db, "SELECT status, finished_at IS NOT NULL FROM ingest_run WHERE id = ?", (res.run.run_id,))[0][0] == "partial"
    assert_clean(db)
    later = ingest(db, folder, off)
    assert later.finalisation_error is None and len(later.reresolution.downgrades) == 1
    assert owner_rows(db)[0][1] == "ambiguous"
    assert_clean(db)


@pytest.mark.ac("AC-08.5")
@pytest.mark.parametrize("point", ["finalise_start", "finalise_written"])
def test_kill_during_the_finalisation_leaves_files_committed_and_the_next_run_completes_it(tmp_path, point):
    folder = tmp_path / "n"
    folder.mkdir()
    write_note(folder, "a.md", NAMESAKE_NOTE)
    db = tmp_path / "fk.db"
    on, off = make_cfg(tmp_path), make_cfg(tmp_path, attendance=False, name="off.toml")
    ingest(db, folder, on)
    before = dump(db)
    with pytest.raises(SimulatedCrash):
        ingest(db, folder, off, fault_hook=hook_at(point))
    assert dump(db) == before
    assert q(db, "SELECT status FROM ingest_run ORDER BY id DESC LIMIT 1")[0][0] == "in_progress"
    final = ingest(db, folder, off)
    assert len(final.run.interrupted) == 1 and len(final.reresolution.downgrades) == 1
    assert_clean(db)


@pytest.mark.ac("AC-08.5")
def test_kill_during_a_first_ingest_finalisation_keeps_every_file(tmp_path, sample):
    """The files are committed before the passes run: a kill in the finalisation of the very first run loses nothing."""
    folder, cfg, baseline, order = sample
    db = tmp_path / "fin.db"
    with pytest.raises(SimulatedCrash):
        ingest(db, folder, cfg, fault_hook=hook_at("finalise_written"))
    assert committed_files(db) == order
    assert_clean(db)
    final = ingest(db, folder, cfg)
    assert final.counters.files_unchanged == SAMPLE_OK_FILES and final.counters.files_ingested == 0
    assert dump_masked(db) == baseline


# --------------------------------------------------------------------------------------------------------- AC-08.7
def _binary(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")


def _bad_utf8(path: Path) -> None:
    path.write_bytes(b"# Meeting\n\xff\xfe\xfa not utf-8\n")


@pytest.mark.ac("AC-08.7")
@pytest.mark.parametrize(
    ("corrupt", "code"),
    [(_binary, codes.E_BINARY), (_bad_utf8, codes.E_ENCODING)],
    ids=["binary", "invalid-utf8"],
)
def test_previously_ok_file_turned_unreadable_keeps_items_and_is_not_missing(small, corrupt, code):
    folder, cfg, db, tmp = small
    before = dump(db)
    original = (folder / "a.md").read_bytes()
    old_hash = q(db, "SELECT content_sha256 FROM source_file WHERE rel_path = 'a.md'")[0][0]
    corrupt(folder / "a.md")
    res = ingest(db, folder, cfg)
    assert res.exit_code == 5 and res.counters.files_failed == 1 and res.counters.files_unchanged == 1
    assert res.counters.invariant_holds() and res.missing == ()
    (sf,) = q(db, "SELECT status, failure_code, content_sha256 FROM source_file WHERE rel_path = 'a.md'")
    assert (sf["status"], sf["failure_code"], sf["content_sha256"]) == ("failed", code, old_hash)
    after = dump(db)
    for table in ("action_item", "item_source", "item_owner", "item_event", "item_link", "meeting"):
        assert after[table] == before[table], table  # items retained, still present, no event
    assert q(db, "SELECT count(*) FROM action_item WHERE source_state = 'missing'")[0][0] == 0
    assert q(db, "SELECT count(*) FROM ingest_warning WHERE code = ?", (codes.W_FILE_MISSING,))[0][0] == 0
    assert q(db, "SELECT status FROM ingest_run WHERE id = ?", (res.run.run_id,))[0][0] == "partial"
    assert_clean(db)
    # failing again stays failing (never "unchanged"), and restoring the original content revives the file untouched
    assert ingest(db, folder, cfg).exit_code == 5
    (folder / "a.md").write_bytes(original)
    back = ingest(db, folder, cfg)
    assert back.exit_code == 0 and back.counters.files_unchanged == 2 and back.counters.files_ingested == 0
    assert q(db, "SELECT status, failure_code FROM source_file WHERE rel_path = 'a.md'")[0][:] == ("ok", None)
    assert dump(db)["action_item"] == before["action_item"] and dump(db)["item_source"] == before["item_source"]


@pytest.mark.ac("AC-08.7")
def test_previously_ok_file_over_the_size_limit_keeps_its_items(small):
    folder, cfg, db, tmp = small
    before = dump(db)
    small_limit = dataclasses.replace(cfg, limits=dataclasses.replace(cfg.limits, max_file_bytes=1200))
    (folder / "a.md").write_bytes((folder / "a.md").read_bytes() + b"\n" + b"x" * 5000)
    res = ingest(db, folder, small_limit)
    assert res.exit_code == 5
    assert q(db, "SELECT failure_code FROM source_file WHERE rel_path = 'a.md'")[0][0] == codes.E_TOO_LARGE
    after = dump(db)
    assert (after["action_item"], after["item_source"]) == (before["action_item"], before["item_source"])
    assert res.missing == () and q(db, "SELECT count(*) FROM action_item WHERE source_state = 'missing'")[0][0] == 0


@pytest.mark.ac("AC-08.7")
def test_previously_ok_file_that_cannot_be_read_keeps_its_items(small, monkeypatch):
    """E-UNREADABLE (permission / lock): simulated at the opener of the safe reader, the one place the OS error surfaces."""
    folder, cfg, db, tmp = small
    before = dump(db)
    real_open = reader._open_rb

    def deny(path):
        if str(path).endswith("a.md"):
            raise PermissionError(13, "denied", str(path))
        return real_open(path)

    monkeypatch.setattr(reader, "_open_rb", deny)
    res = ingest(db, folder, cfg)
    assert res.exit_code == 5 and res.counters.files_failed == 1
    assert q(db, "SELECT failure_code FROM source_file WHERE rel_path = 'a.md'")[0][0] == codes.E_UNREADABLE
    after = dump(db)
    assert (after["action_item"], after["item_source"], after["item_event"]) == (
        before["action_item"], before["item_source"], before["item_event"])
    assert res.missing == () and q(db, "SELECT count(*) FROM ingest_warning WHERE code = ?", (codes.W_FILE_MISSING,))[0][0] == 0
    assert_clean(db)


@pytest.mark.ac("AC-08.7")
def test_previously_ok_file_that_becomes_empty_keeps_its_items(small):
    """An emptied file is 'empty', not a failure (spec 11): exit 0, and its items are never deleted (BR-16)."""
    folder, cfg, db, tmp = small
    ids_before = [r[0] for r in q(db, "SELECT id FROM action_item ORDER BY id")]
    (folder / "a.md").write_bytes(b"")
    res = ingest(db, folder, cfg)
    assert res.counters.files_empty == 1 and res.counters.files_failed == 0 and res.exit_code == 0
    assert [r[0] for r in q(db, "SELECT id FROM action_item ORDER BY id")] == ids_before
    assert q(db, "SELECT status FROM source_file WHERE rel_path = 'a.md'")[0][0] == "empty"
    assert res.missing == ()
    assert_clean(db)


# ------------------------------------------------------------------------- real process kill (AC-08.5, AC-08.9 recovery)
KILL_SCRIPT = """
import os, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from pathlib import Path
from tests.integration.whole_db_support import ingest, make_cfg
db, folder, point = Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]
def hook(name):
    if name == point:
        os._exit(137)   # no handler, no finally, no rollback call: the OS drops the connection
ingest(db, folder, make_cfg(Path(sys.argv[6])), fault_hook=hook)
"""


@pytest.mark.ac("AC-08.5", "AC-08.9")
@pytest.mark.parametrize("point_kind", ["after_file", "file_written"])
def test_real_process_kill_midway_then_rerun_equals_an_uninterrupted_run(sample, tmp_path, point_kind):
    import os
    import subprocess
    import sys

    from tests.conftest import REPO_ROOT

    folder, cfg, baseline, order = sample
    point = "after_file:12" if point_kind == "after_file" else f"file_written:{order[12]}"
    db = tmp_path / "real.db"
    script = tmp_path / "kill.py"
    script.write_text(KILL_SCRIPT, encoding="utf-8", newline="\n")
    (tmp_path / "cfgdir").mkdir()
    env = {k: os.environ[k] for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP") if k in os.environ}
    proc = subprocess.run(
        [sys.executable, str(script), str(REPO_ROOT / "src"), str(REPO_ROOT), str(db), str(folder), point, str(tmp_path / "cfgdir")],
        env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 137, proc.stderr
    assert q(db, "SELECT status FROM ingest_run")[0][0] == "in_progress"
    assert len(committed_files(db)) == (12 if point_kind == "after_file" else 12)
    assert_clean(db)
    final = ingest(db, folder, cfg)
    assert len(final.run.interrupted) == 1
    assert dump_masked(db) == baseline
