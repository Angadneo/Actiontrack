"""T-M5-19: invariant / property tests over randomised ingest histories (plan section 4; hypothesis is not installed,
so the scenarios come from seeded ``random.Random`` generators and are fully reproducible from the seed).

Random operation sequences run over a small synthetic notes folder (add a file, edit / move / delete / restore a
line, toggle a checkbox, delete or rename a file, corrupt a file, mark an item done by hand, re-run unchanged); after
EVERY step ``run_ingest`` runs and these invariants must hold:

* BR-11 item ids are never reused and never renumbered (new ids are larger than every id seen before, an item's
  ``created`` event is never rewritten) and no item is ever deleted (BR-16);
* BR-08 ``item_source`` rows are never deleted (count never decreases) and keep their item and file;
* every item has >= 1 source, an owner row, ``source_state`` equal to the presence of its sources (10.7.4);
* links are stored once with ``from < to`` (no reverse twin), ``completed_by`` points at a done item;
* ``foreign_key_check`` is empty, ``integrity_check`` is ok, owners resolve to existing people;
* BR-09 manual done is never reopened;
* the run counters obey ``discovered = ingested + unchanged + empty + failed`` and agree with the run row and the
  folder (file count, failed count);
* BR-15 / 10.7.1 an immediate re-run over unchanged input changes NOTHING (full business-table dump, ``updated_at``
  and ``item_event`` included) and writes no event;
* a file that is gone is ``missing`` with no present source; a file that is there is not.

Order independence is asserted exactly where the spec promises it (FR-01.1: the order is chronological and does not
depend on creation order, so one run over the same files gives the same database); the documented limitation of
10.7.10 / R-18 (a late-arriving OLDER note) is an explicit named xfail.
"""

from __future__ import annotations

import os
import random
import shutil
import sqlite3
from pathlib import Path

import pytest

from actiontrack import codes

from tests.integration.ingest_support import open_repo, write_note
from tests.integration.whole_db_support import (
    copy_sample,
    dump,
    file_stamp,
    dump_masked,
    ingest,
    make_cfg,
    q,
    structural_violations,
)

TASKS = [
    "send the revised quote to Acme",
    "fix the login screen",
    "share the first wireframes",
    "book the offsite venue",
    "renew the staging certificate",
    "revoke the stale access token",
    "draft the new pricing page",
    "update the billing runbook",
    "review the pagination bug",
    "plan the retrospective agenda",
    "migrate the invoice schema",
    "confirm the cloud budget",
]
OWNERS = ["Priya Nair", "Rahul Mehta", "Anita Desai", "Sam Okafor", "Priya", "Rahul", "Meera Iyer"]
DUES = ["", " by 2026-09-30", " by 2026-10-15"]
FILE_NAMES = [
    "2026-09-01-kickoff.md",
    "2026-09-03-weekly.md",
    "2026-09-08-planning.md",
    "2026-09-10-review.md",
    "2026-09-15-sync.md",
    "2026-09-22-retro.md",
    "undated-scratch.md",
]
OPS = (
    ("add_file", 4),
    ("edit_line", 4),
    ("move_line", 3),
    ("delete_line", 3),
    ("restore_line", 3),
    ("toggle", 4),
    ("delete_file", 2),
    ("rename_file", 2),
    ("corrupt_file", 1),
    ("manual_done", 2),
    ("rerun", 2),
    ("cfg_attendance", 2),
    ("cfg_alias", 2),
    ("cfg_namesake", 1),
)
#: operations that change ONLY the configuration (no note file): the whole-database passes (10.7.9) must change
#: owners and nothing else
CFG_OPS = frozenset({"cfg_attendance", "cfg_alias", "cfg_namesake"})


class Model:
    """The folder as the test believes it is: name -> list of ``[done, text]``; ``deleted`` = lines removed so far."""

    def __init__(self, rng: random.Random, folder: Path):
        self.rng, self.folder = rng, folder
        self.files: dict[str, list[list]] = {}
        self.corrupt: set[str] = set()
        self.deleted: list[tuple[str, int, str]] = []  # (file, index, text)
        self.removed_files: set[str] = set()
        self.cfg_serial, self.attendance, self.alias, self.namesake = 0, True, False, False
        self.cfg = None  # set by the history driver (a Config)

    # ---- rendering
    @staticmethod
    def new_line(rng: random.Random) -> list:
        return [False, f"{rng.choice(OWNERS)} to {rng.choice(TASKS)}{rng.choice(DUES)}"]

    def render(self, name: str) -> str:
        date = name[:10] if name[:4] == "2026" else "2026-09-29"
        body = "\n".join(f"- [{'x' if d else ' '}] {t}" for d, t in self.files[name])
        return (
            f"# Meeting: {name[:-3]}\n\nDate: {date}\nAttendees: Priya Nair, Rahul Mehta, Anita Desai, Sam Okafor\n\n"
            f"## Action items\n\n{body}\n"
        )

    def write(self, name: str) -> None:
        self.corrupt.discard(name)
        write_note(self.folder, name, self.render(name))

    # ---- operations (each returns True when it changed something)
    def op_add_file(self) -> bool:
        free = [n for n in FILE_NAMES if n not in self.files]
        if not free or len(self.files) >= 5:
            return False
        name = self.rng.choice(free)
        self.files[name] = [self.new_line(self.rng) for _ in range(self.rng.randint(2, 4))]
        self.write(name)
        return True

    def _pick(self, need_lines: bool = True) -> str | None:
        names = sorted(n for n, lines in self.files.items() if lines or not need_lines)
        return self.rng.choice(names) if names else None

    def op_edit_line(self) -> bool:
        name = self._pick()
        if name is None:
            return False
        line = self.rng.choice(self.files[name])
        line[1] = line[1] + self.rng.choice([" again", " today", " carefully"])
        self.write(name)
        return True

    def op_move_line(self) -> bool:
        name = self._pick()
        if name is None or len(self.files[name]) < 2:
            return False
        lines = self.files[name]
        i, j = self.rng.sample(range(len(lines)), 2)
        lines[i], lines[j] = lines[j], lines[i]
        self.write(name)
        return True

    def op_delete_line(self) -> bool:
        name = self._pick()
        if name is None:
            return False
        idx = self.rng.randrange(len(self.files[name]))
        text = self.files[name].pop(idx)
        self.deleted.append((name, idx, text[1]))
        self.write(name)
        return True

    def op_restore_line(self) -> bool:
        cands = [d for d in self.deleted if d[0] in self.files]
        if not cands:
            return False
        entry = self.rng.choice(cands)
        self.deleted.remove(entry)
        name, idx, text = entry
        self.files[name].insert(min(idx, len(self.files[name])), [False, text])
        self.write(name)
        return True

    def op_toggle(self) -> bool:
        name = self._pick()
        if name is None:
            return False
        line = self.rng.choice(self.files[name])
        line[0] = not line[0]
        self.write(name)
        return True

    def op_delete_file(self) -> bool:
        if not self.files:
            return False
        name = self.rng.choice(sorted(self.files))
        os.unlink(self.folder / name)
        del self.files[name]
        self.corrupt.discard(name)
        self.removed_files.add(name)
        return True

    def op_rename_file(self) -> bool:
        free = [n for n in FILE_NAMES if n not in self.files]
        if not self.files or not free:
            return False
        old, new = self.rng.choice(sorted(self.files)), self.rng.choice(free)
        self.files[new] = self.files.pop(old)
        self.corrupt.discard(old)
        os.unlink(self.folder / old)
        self.removed_files.add(old)
        self.removed_files.discard(new)
        self.write(new)
        return True

    def op_corrupt_file(self) -> bool:
        cands = sorted(n for n in self.files if n not in self.corrupt)
        if not cands:
            return False
        name = self.rng.choice(cands)
        (self.folder / name).write_bytes(b"\x00\x01binary\x00garbage")
        self.corrupt.add(name)
        return True

    def op_rerun(self) -> bool:
        return True

    # ---- configuration changes (roster / alias / attendee_disambiguation): files stay byte-identical
    def _rebuild_cfg(self) -> bool:
        self.cfg_serial += 1
        self.cfg = make_cfg(
            self.folder.parent,
            attendance=self.attendance,
            aliases={"Priya Nair": ("Priya",)} if self.alias else None,
            add=(("Priya", "Narang", ()),) if self.namesake else (),
            name=f"cfg{self.cfg_serial}.toml",
        )
        return True

    def op_cfg_attendance(self) -> bool:
        self.attendance = not self.attendance
        return self._rebuild_cfg()

    def op_cfg_alias(self) -> bool:
        self.alias = not self.alias
        return self._rebuild_cfg()

    def op_cfg_namesake(self) -> bool:
        if self.namesake:
            return False
        self.namesake = True
        return self._rebuild_cfg()

    def op_manual_done(self) -> bool:  # handled by the driver (needs the database)
        return True


def weighted_ops(rng: random.Random, n: int) -> list[str]:
    names, weights = zip(*OPS, strict=True)
    return rng.choices(names, weights=weights, k=n)


class Tracker:
    """What was seen in earlier steps (the 'never' invariants compare against it)."""

    def __init__(self) -> None:
        self.max_item_id = 0
        self.items: dict[int, tuple] = {}  # id -> its created event (ts, run_id)
        self.sources: dict[int, tuple[int, int]] = {}  # item_source id -> (item_id, source_file_id)
        self.manual: set[int] = set()
        self.seen_files = 0


def snapshot_items(db: Path) -> dict[int, tuple]:
    created = {
        r["item_id"]: (r["ts"], r["run_id"])
        for r in q(db, "SELECT item_id, ts, run_id FROM item_event WHERE kind = 'created'")
    }
    return {r["id"]: created.get(r["id"]) for r in q(db, "SELECT id FROM action_item")}


def check_invariants(db: Path, res, model: Model, tr: Tracker, step: str) -> None:
    ctx = f"[{step}]"
    # --- structural (foreign keys, integrity, links, owners, source_state, evidence)
    assert structural_violations(db) == [], ctx
    # --- BR-11 / BR-16: ids never reused, never renumbered, never deleted
    items = snapshot_items(db)
    for item_id, created in tr.items.items():
        assert item_id in items, f"{ctx} item {item_id} was deleted"
        assert items[item_id] == created, f"{ctx} item {item_id} created event rewritten (id reused?)"
    new_ids = set(items) - set(tr.items)
    assert all(i > tr.max_item_id for i in new_ids), f"{ctx} a new item reuses an old id"
    assert all(v is not None for v in items.values()), f"{ctx} item without a created event"
    # --- BR-08: item_source rows never deleted, keep item and file
    sources = {r["id"]: (r["item_id"], r["source_file_id"]) for r in q(db, "SELECT id, item_id, source_file_id FROM item_source")}
    for sid, where in tr.sources.items():
        assert sources.get(sid) == where, f"{ctx} item_source {sid} deleted or re-pointed"
    assert len(sources) >= len(tr.sources), ctx
    # --- BR-09 manual done is sticky
    for item_id in tr.manual:
        (row,) = q(db, "SELECT status, completion_origin FROM action_item WHERE id = ?", (item_id,))
        assert (row["status"], row["completion_origin"]) == ("done", "manual"), f"{ctx} manual done of {item_id} reopened"
    # --- counters
    c = res.counters
    assert c.invariant_holds(), ctx
    on_disk = sorted(p.name for p in model.folder.iterdir() if p.name.endswith(".md"))
    assert c.files_discovered == len(on_disk), ctx
    assert c.files_failed == len(model.corrupt), ctx
    assert res.exit_code == (5 if model.corrupt else 0), ctx
    row = q(db, "SELECT * FROM ingest_run WHERE id = ?", (res.run.run_id,))[0]
    for name, value in c.as_columns().items():
        assert row[name] == value, f"{ctx} run row {name}"
    assert row["status"] == ("partial" if model.corrupt else "complete"), ctx
    # --- files: present on disk <=> not missing; gone <=> missing with no present source
    stored = {r["rel_path"]: r["status"] for r in q(db, "SELECT rel_path, status FROM source_file")}
    for name in on_disk:
        assert stored.get(name) in ("ok", "failed", "empty"), f"{ctx} {name} is {stored.get(name)}"
        if name in model.corrupt:
            assert stored[name] == "failed", ctx
    for name in set(stored) - set(on_disk):
        assert stored[name] == "missing", f"{ctx} {name} is gone but {stored[name]}"
    n_missing_warn = q(db, "SELECT count(*) FROM ingest_warning WHERE run_id = ? AND code = ?", (res.run.run_id, codes.W_FILE_MISSING))[0][0]
    assert n_missing_warn == len(res.missing) - sum(1 for m in res.missing if not m.warned), ctx
    # --- W-STATUS-CONFLICT only for manual-done items, once per run
    conflict_rows = q(db, "SELECT message FROM ingest_warning WHERE run_id = ? AND code = ?", (res.run.run_id, codes.W_STATUS_CONFLICT))
    assert len(conflict_rows) == len(res.conflicts) == len(set(res.conflicts)), ctx
    assert set(res.conflicts) <= tr.manual, ctx
    # --- BR-15: an immediate re-run over unchanged input changes nothing at all
    before = dump(db)
    again = ingest(db, model.folder, model.cfg)
    assert dump(db) == before, f"{ctx} re-run over unchanged input changed business tables"
    a = again.counters
    assert (a.files_ingested, a.items_new, a.items_merged, a.items_updated, a.possible_duplicates) == (0, 0, 0, 0, 0), ctx
    assert a.files_unchanged == c.files_discovered - len(model.corrupt), ctx
    assert again.reresolution.is_empty and not again.finalisation_error, ctx
    assert q(db, "SELECT count(*) FROM item_event WHERE run_id = ?", (again.run.run_id,))[0][0] == 0, ctx
    # --- remember
    tr.items, tr.sources = items, sources
    tr.max_item_id = max(tr.max_item_id, *items) if items else tr.max_item_id


def run_history(tmp_path: Path, seed: int, steps: int) -> tuple[Path, Model]:
    rng = random.Random(seed)
    folder = tmp_path / "notes"
    folder.mkdir()
    model = Model(rng, folder)
    model.cfg = make_cfg(tmp_path)
    db = tmp_path / "h.db"
    tr = Tracker()
    # seed the folder with two files so every history starts with something to work on
    model.op_add_file()
    model.op_add_file()
    res = ingest(db, folder, model.cfg)
    check_invariants(db, res, model, tr, "initial")
    for n, op in enumerate(weighted_ops(rng, steps), start=1):
        if op == "manual_done":
            open_items = [r["id"] for r in q(db, "SELECT id FROM action_item WHERE status = 'open' ORDER BY id")]
            if open_items:
                item = rng.choice(open_items)
                d, repo = open_repo(db)
                try:
                    with d.transaction():
                        repo.set_manual_done(item)
                finally:
                    d.close()
                tr.manual.add(item)
        else:
            getattr(model, f"op_{op}")()
        notes_before = file_stamp(folder) if op in CFG_OPS else None
        frozen = dump(db, ("item_source", "source_file", "meeting", "meeting_attendee", "item_link")) if op in CFG_OPS else None
        status_before = q(db, "SELECT id, status, completion_origin, completed_at, source_state FROM action_item ORDER BY id")
        res = ingest(db, folder, model.cfg)
        check_invariants(db, res, model, tr, f"seed={seed} step={n} op={op}")
        if op in CFG_OPS:  # AC-05.17 / AC-05.19: owners may change, nothing else may
            assert file_stamp(folder) == notes_before
            assert res.counters.files_ingested == 0 and res.counters.items_new == 0
            assert dump(db, ("item_source", "source_file", "meeting", "meeting_attendee", "item_link")) == frozen, op
            assert [tuple(r) for r in q(db, "SELECT id, status, completion_origin, completed_at, source_state FROM action_item ORDER BY id")] == [tuple(r) for r in status_before], op
            # Items WITH a present source change owners only. (An item whose sources are ALL absent may have its
            # canonical description re-picked by the pass: spec silent, reported as a finding, not asserted.)
            kinds = {
                r[0]
                for r in q(
                    db,
                    "SELECT DISTINCT e.kind FROM item_event e JOIN action_item a ON a.id = e.item_id "
                    "WHERE e.run_id = ? AND a.source_state = 'present'",
                    (res.run.run_id,),
                )
            }
            assert kinds <= {"owner_changed"}, f"{op}: unexpected events {kinds}"
    return db, model


@pytest.mark.ac("AC-08.1", "AC-08.5", "AC-08.8")
@pytest.mark.parametrize("seed", range(14))
def test_random_histories_keep_every_invariant_after_every_step(tmp_path, seed):
    """BR-08/09/11/15/16/17, NFR-REL-01, 10.7.1/10.7.4: see the module docstring for the full list."""
    db, model = run_history(tmp_path, seed, steps=16)
    assert structural_violations(db) == []


# ---------------------------------------------------------------------------------------- order independence
def build_folder(root: Path, model: Model, order: list[str]) -> Path:
    """The model's files created in ``order`` (different creation order, different mtimes)."""
    folder = root / "notes"
    folder.mkdir()
    for i, name in enumerate(order):
        path = write_note(folder, name, model.render(name))
        os.utime(path, (1_700_000_000 + i * 977, 1_700_000_000 + i * 977))
    return folder


@pytest.mark.ac("AC-09.4")
@pytest.mark.parametrize("seed", range(6))
def test_one_run_over_the_same_files_is_independent_of_their_creation_order(tmp_path, seed):
    """FR-01.1: the ingest order is (meeting date, rel_path), never the creation order or the mtime, so the same
    files give the same database (ids included) however they were created."""
    rng = random.Random(1000 + seed)
    model = Model(rng, tmp_path)
    for _ in range(5):
        model.op_add_file()
    for _ in range(6):
        model.op_toggle()
    dumps = []
    for n in range(3):
        order = sorted(model.files)
        rng.shuffle(order)
        root = tmp_path / f"v{n}"
        root.mkdir()
        folder = build_folder(root, model, order)
        res = ingest(root / "o.db", folder, make_cfg(root))
        assert res.counters.files_ingested == len(model.files)
        dumps.append(dump_masked(root / "o.db", mask=("mtime", "folder_root")))
    assert dumps[0] == dumps[1] == dumps[2]


@pytest.mark.ac("AC-09.4")
def test_sample_folder_in_25_shuffled_creation_orders_gives_identical_databases(tmp_path):
    """T-M5-19: same sample content, different file creation order and mtimes -> identical business tables."""
    from tests.conftest import FIXTURES_NOTES

    names = sorted(p.name for p in FIXTURES_NOTES.iterdir() if p.is_file())
    rng = random.Random(25)
    reference = None
    for n in range(25):
        order = names[:]
        rng.shuffle(order)
        root = tmp_path / f"s{n}"
        folder = root / "notes"
        folder.mkdir(parents=True)
        for i, name in enumerate(order):
            shutil.copyfile(FIXTURES_NOTES / name, folder / name)
            os.utime(folder / name, (1_700_000_000 + i * 31, 1_700_000_000 + i * 31))
        res = ingest(root / "s.db", folder, make_cfg(root))
        assert (res.counters.files_ingested, res.counters.files_empty, res.counters.files_failed) == (33, 1, 1)
        got = dump_masked(root / "s.db", mask=("mtime", "folder_root"))
        if reference is None:
            reference = got
        assert got == reference, f"shuffle {n} differs"


@pytest.mark.ac("AC-09.4")
def test_sample_ingested_chronologically_in_three_batches_equals_one_run(tmp_path):
    """10.7.10 limits the divergence to a newly added OLDER note, so adding notes in chronological order across runs
    must reach the state of one combined run (items, sources, owners, links, events; only run ids differ)."""
    from tests.conftest import FIXTURES_NOTES

    one = tmp_path / "one"
    folder_one = copy_sample(one.parent, "one")
    ingest(tmp_path / "one.db", folder_one, make_cfg(tmp_path))
    # the empty and the binary file are processed AFTER every ingestable file in one run (their source_file ids come
    # last), so they join the last batch; the undated ``untitled.md`` is ordered after all dated notes anyway
    tail = {"2026-09-09-notes-final-v2.md", "scratch.md", "untitled.md"}
    dated = sorted(
        p.name for p in FIXTURES_NOTES.iterdir() if p.is_file() and p.name[:2] == "20" and p.suffix == ".md" and p.name not in tail
    )
    rest = sorted(p.name for p in FIXTURES_NOTES.iterdir() if p.is_file() and p.name not in dated)
    third = len(dated) // 3
    batches = [dated[:third], dated[third : 2 * third], dated[2 * third :] + rest]
    staged = tmp_path / "staged"
    staged.mkdir()
    cfg = make_cfg(tmp_path, name="c2.toml")
    for batch in batches:
        for name in batch:
            shutil.copyfile(FIXTURES_NOTES / name, staged / name)
        ingest(tmp_path / "staged.db", staged, cfg)
    assert dump_masked(tmp_path / "staged.db", mask=("mtime", "folder_root")) == dump_masked(
        tmp_path / "one.db", mask=("mtime", "folder_root")
    )


# ------------------------------------------------------------------------------- documented limitation (10.7.10, R-18)
OLD_OPEN = "- [ ] Priya Nair to send the revised quote to Acme by Friday"
NEW_DONE = "- [x] Priya Nair to send the revised quote to Acme"


def _two_notes(folder: Path) -> None:
    head = "# Meeting: {t}\n\nDate: {d}\nAttendees: Priya Nair, Rahul Mehta\n\n## Action items\n\n{b}\n"
    write_note(folder, "2026-09-01-old.md", head.format(t="Old", d="2026-09-01", b=OLD_OPEN))
    write_note(folder, "2026-09-08-new.md", head.format(t="New", d="2026-09-08", b=NEW_DONE))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="documented limitation, not a defect (spec 10.7.10, R-18): a note OLDER than already-ingested notes is matched "
    "against the existing later items, so two runs (newer first, older later) differ from one combined run "
    "(the Tier-1b completion needs the [x] note to be not earlier than the open item)",
)
@pytest.mark.ac("AC-08.1")
def test_late_arriving_older_note_gives_the_same_state_as_one_combined_run_R18(tmp_path):
    combined = tmp_path / "c"
    combined.mkdir()
    _two_notes(combined)
    ingest(tmp_path / "c.db", combined, make_cfg(tmp_path))

    split = tmp_path / "s"
    split.mkdir()
    _two_notes(split)
    old = split / "2026-09-01-old.md"
    hidden = old.read_bytes()
    old.unlink()
    cfg = make_cfg(tmp_path, name="c2.toml")
    ingest(tmp_path / "s.db", split, cfg)  # newer note first
    old.write_bytes(hidden)
    ingest(tmp_path / "s.db", split, cfg)  # the older note arrives late
    mask = ("mtime", "folder_root", "id")
    assert dump_masked(tmp_path / "s.db", mask=mask) == dump_masked(tmp_path / "c.db", mask=mask)


def test_late_arriving_older_note_still_ingests_cleanly_and_keeps_all_invariants(tmp_path):
    """What the limitation does NOT excuse: the late older note is still ingested, nothing is lost or duplicated,
    and the database stays consistent (the final state merely may differ from the combined run)."""
    split = tmp_path / "s"
    split.mkdir()
    _two_notes(split)
    old = split / "2026-09-01-old.md"
    hidden = old.read_bytes()
    old.unlink()
    cfg = make_cfg(tmp_path)
    ingest(tmp_path / "s.db", split, cfg)
    old.write_bytes(hidden)
    res = ingest(tmp_path / "s.db", split, cfg)
    assert res.counters.files_ingested == 1 and res.counters.files_unchanged == 1
    assert q(tmp_path / "s.db", "SELECT count(*) FROM item_source")[0][0] == 2
    assert structural_violations(tmp_path / "s.db") == []
    again = ingest(tmp_path / "s.db", split, cfg)
    assert again.counters.files_ingested == 0 and again.reresolution.is_empty


def test_ingest_is_idempotent_on_a_sqlite_level_too(tmp_path):
    """The raw file content of the business tables, not only the Python view of them, is stable across re-runs."""
    folder = tmp_path / "n"
    folder.mkdir()
    model = Model(random.Random(7), folder)
    for _ in range(3):
        model.op_add_file()
    db = tmp_path / "i.db"
    cfg = make_cfg(tmp_path)
    ingest(db, folder, cfg)
    conn = sqlite3.connect(db)
    try:
        snap = {t: conn.execute(f'SELECT * FROM "{t}" ORDER BY 1, 2').fetchall() for t in ("action_item", "item_source", "item_event", "item_owner")}
    finally:
        conn.close()
    ingest(db, folder, cfg)
    conn = sqlite3.connect(db)
    try:
        assert snap == {t: conn.execute(f'SELECT * FROM "{t}" ORDER BY 1, 2').fetchall() for t in snap}
    finally:
        conn.close()


# ------------------------------------------------------------------------------------------------- former defect D1
# D1 (found by random-history seed 107, fixed in services/ingest.py `_mark_file_missing`): deleting a file must
# recompute the canonical fields of the items that lost a source (§10.6.1: present sources win).
@pytest.mark.ac("AC-08.4")
def test_canonical_description_follows_the_earliest_present_source_after_a_file_is_deleted(tmp_path):
    folder = tmp_path / "n"
    folder.mkdir()
    head = "# Meeting: {t}\n\nDate: {d}\nAttendees: Priya Nair, Rahul Mehta\n\n## Action items\n\n{b}\n"
    write_note(folder, "2026-09-03-a.md", head.format(t="A", d="2026-09-03", b="- [ ] Priya to confirm the cloud budget by 2026-10-15"))
    write_note(folder, "2026-09-10-b.md", head.format(t="B", d="2026-09-10", b="- [ ] Priya Nair to confirm the cloud budget by 2026-10-15"))
    db = tmp_path / "m.db"
    cfg = make_cfg(tmp_path)
    ingest(db, folder, cfg)
    assert q(db, "SELECT count(*) FROM action_item")[0][0] == 1  # Tier-1 merge
    assert q(db, "SELECT description FROM action_item")[0][0] == "Priya to confirm the cloud budget by 2026-10-15"
    (folder / "2026-09-03-a.md").unlink()
    res = ingest(db, folder, cfg)
    assert [m.rel_path for m in res.missing] == ["2026-09-03-a.md"]
    assert q(db, "SELECT description FROM action_item")[0][0] == "Priya Nair to confirm the cloud budget by 2026-10-15"
