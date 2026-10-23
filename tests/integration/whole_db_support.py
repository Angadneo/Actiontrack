"""Helpers for the whole-database pass tests (T-M4-15, T-M5-14, T-M5-17). Not a test module."""

from __future__ import annotations

import dataclasses
import shutil
import sqlite3
from pathlib import Path

from actiontrack.config import Config, Owners, load_config
from actiontrack.services import ingest as ing
from actiontrack.store.db import Database
from actiontrack.store.repo import Repo

from tests.conftest import FIXTURES_NOTES
from tests.integration.ingest_support import BUSINESS_TABLES, FIXED_NOW, ROSTER_TOML, run_ctx
from tests.integration.ingest_support import dump_tables as dump_tables  # noqa: PLC0414 (re-export)

#: the 9 people of ``valid_full.toml`` as (first, last, aliases)
BASE_PEOPLE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Priya", "Nair", ("Priya N.",)),
    ("Priya", "Shah", ("Priya S.",)),
    ("Rahul", "Mehta", ("Rahul M.",)),
    ("Rahul", "Verma", ("Rahul V.",)),
    ("Anita", "Desai", ()),
    ("Sam", "Okafor", ()),
    ("Meera", "Iyer", ()),
    ("Karan", "Joshi", ()),
    ("Lena", "Fischer", ()),
)


def _toml_head() -> str:
    text = ROSTER_TOML.read_text(encoding="utf-8")
    return text[: text.index("# Each person:")]


def make_cfg(
    tmp_path: Path,
    *,
    drop: tuple[str, ...] = (),
    add: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    aliases: dict[str, tuple[str, ...]] | None = None,
    attendance: bool = True,
    name: str = "config.toml",
    people: tuple[tuple[str, str, tuple[str, ...]], ...] | None = None,
) -> Config:
    """The sample roster with people dropped (``"Priya Nair"``), added, or extra aliases per full name.
    ``people`` replaces the base roster altogether (``()`` = no roster at all)."""
    extra = aliases or {}
    blocks: list[str] = []
    for first, last, al in (*(BASE_PEOPLE if people is None else people), *add):
        full = f"{first} {last}"
        if full in drop:
            continue
        all_aliases = (*al, *extra.get(full, ()))
        quoted = ", ".join(f'"{a}"' for a in all_aliases)
        blocks.append(
            f'[[person]]\nfirst_name = "{first}"\nlast_name = "{last}"\nemail = ""\naliases = [{quoted}]\n'
        )
    path = tmp_path / name
    path.write_text(_toml_head() + "\n".join(blocks), encoding="utf-8", newline="\n")
    cfg = load_config(path)
    return dataclasses.replace(cfg, owners=Owners(attendee_disambiguation=attendance))


def copy_sample(tmp_path: Path, name: str = "notes") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    for src in sorted(FIXTURES_NOTES.iterdir()):
        if src.is_file():
            shutil.copyfile(src, folder / src.name)
    return folder


def ingest(db_path: Path, folder: Path, cfg: Config, **kw):
    return ing.run_ingest(db_path, folder, cfg=cfg, run_ctx=run_ctx(), **kw)


def q(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def dump(db_path: Path, tables=None) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(db_path)
    try:
        return dump_tables(conn) if tables is None else dump_tables(conn, tables)
    finally:
        conn.close()


def reresolve_only(db_path: Path, cfg: Config, folder: Path):
    """Run ONLY the whole-database owner re-resolution in its own run + transaction (T-M4-15 level)."""
    from actiontrack.clock import FixedClock

    db = Database.open(db_path, clock=FixedClock(FIXED_NOW))
    try:
        repo = Repo(db)
        with db.transaction():
            run_id = repo.insert_ingest_run(str(folder.resolve()))
        with db.transaction():
            applied = ing.reresolve_database(repo, cfg, run_id)
        return applied, run_id
    finally:
        db.close()


def owners_of(db_path: Path, item_id: int) -> list[sqlite3.Row]:
    return q(db_path, "SELECT * FROM item_owner WHERE item_id = ? ORDER BY ordinal", (item_id,))


# ------------------------------------------------------------------------------------------------ T-M5-19 / T-M5-21
#: columns that hold a run id or a folder/file stamp: they legitimately differ between "one run" and "an
#: interrupted run plus its rerun" (AC-08.5 compares the BUSINESS state, ignoring which run wrote what)
RUN_COLUMNS = frozenset(
    {"run_id", "created_run", "first_seen_run", "last_seen_run", "last_run_id", "last_ingested_run"}
)


def dump_masked(db_path: Path, *, mask: tuple[str, ...] = (), tables=None) -> dict[str, list[tuple]]:
    """``dump`` with every run-id column (and the extra ``mask`` columns) replaced by a marker that keeps NULL-ness.

    The marker is ``None`` / ``"*"`` so two databases built by different run splits compare equal exactly when
    their business content (ids, text, owners, links, statuses, events, ``updated_at`` ...) is equal."""
    conn = sqlite3.connect(db_path)
    try:
        out: dict[str, list[tuple]] = {}
        for table in tables or BUSINESS_TABLES:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            hide = [i for i, c in enumerate(cols) if c in RUN_COLUMNS or c in mask]
            rows = []
            for r in conn.execute(f'SELECT * FROM "{table}"'):
                row = list(r)
                for i in hide:
                    row[i] = None if row[i] is None else "*"
                rows.append(tuple(row))
            out[table] = sorted(rows, key=repr)
        return out
    finally:
        conn.close()


def structural_violations(db_path: Path) -> list[str]:
    """Every database-level invariant the spec promises at ALL times (NFR-REL-01, BR-08, BR-11, BR-16, 10.6/10.7).

    Returns human readable violations (empty list = consistent). Safe to call on a database left behind by a crash."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bad: list[str] = []

    def rows(sql: str) -> list[sqlite3.Row]:
        return conn.execute(sql).fetchall()

    try:
        if rows("PRAGMA foreign_key_check"):
            bad.append("foreign_key_check reports dangling references")
        if rows("PRAGMA integrity_check")[0][0] != "ok":
            bad.append("integrity_check not ok")
        for r in rows("SELECT id FROM action_item WHERE id NOT IN (SELECT item_id FROM item_source)"):
            bad.append(f"item {r[0]} has no source (BR-08/BR-16)")
        for r in rows("SELECT id FROM action_item WHERE id NOT IN (SELECT item_id FROM item_owner)"):
            bad.append(f"item {r[0]} has no owner row")
        for r in rows(
            "SELECT s.id FROM item_source s WHERE s.id NOT IN (SELECT item_source_id FROM item_source_owner)"
        ):
            bad.append(f"item_source {r[0]} has no owner phrase row")
        # source_state <-> presence of sources (10.7.4)
        for r in rows(
            "SELECT a.id, a.source_state, (SELECT count(*) FROM item_source s WHERE s.item_id = a.id AND s.present = 1) AS p "
            "FROM action_item a"
        ):
            want = "present" if r["p"] else "missing"
            if r["source_state"] != want:
                bad.append(f"item {r['id']} source_state={r['source_state']} but {r['p']} present sources")
        # a stored file with status ok has its hash and a meeting row; a meeting row never exists without data
        for r in rows("SELECT id, rel_path FROM source_file WHERE status = 'ok' AND content_sha256 IS NULL"):
            bad.append(f"ok file {r['rel_path']} without hash")
        for r in rows(
            "SELECT f.rel_path FROM source_file f WHERE f.status = 'ok' "
            "AND f.id NOT IN (SELECT source_file_id FROM meeting)"
        ):
            bad.append(f"ok file {r[0]} without meeting row (half-written file)")
        for r in rows("SELECT f.rel_path FROM source_file f WHERE f.status = 'missing' AND EXISTS "
                      "(SELECT 1 FROM item_source s WHERE s.source_file_id = f.id AND s.present = 1)"):
            bad.append(f"missing file {r[0]} still has present sources")
        # owners: resolved/provisional -> existing person; ambiguous -> candidates exist; unassigned -> no person
        people = {r[0] for r in rows("SELECT id FROM person")}
        import json

        for r in rows("SELECT item_id, ordinal, owner_status, person_id, candidates FROM item_owner"):
            if r["owner_status"] in ("resolved", "provisional") and r["person_id"] not in people:
                bad.append(f"item {r['item_id']}#{r['ordinal']} {r['owner_status']} owner without a person row")
            if r["owner_status"] == "ambiguous" and (
                r["person_id"] is not None or not set(json.loads(r["candidates"])) <= people
                or len(json.loads(r["candidates"])) < 2
            ):
                bad.append(f"item {r['item_id']}#{r['ordinal']} malformed ambiguous owner")
            if r["owner_status"] == "unassigned" and r["person_id"] is not None:
                bad.append(f"item {r['item_id']}#{r['ordinal']} unassigned owner with a person")
        # links: stored once, from < to, no reverse twin, completed_by points at a done item
        pairs = {(r["from_item_id"], r["to_item_id"], r["kind"]) for r in rows("SELECT * FROM item_link")}
        for a, b, kind in pairs:
            if kind == "possible_duplicate" and (a >= b or (b, a, kind) in pairs):
                bad.append(f"possible_duplicate link {a}-{b} not stored once with from < to")
            if kind == "completed_by":
                (st,) = rows(f"SELECT status FROM action_item WHERE id = {b}")
                if st[0] != "done":
                    bad.append(f"completed_by {a}->{b}: evidence item {b} is not done")
        # completed_at is null for a note completion (AC-01.3, spec 10.6: date unknown) but set for a manual done
        for r in rows(
            "SELECT id FROM action_item WHERE status = 'done' AND (completion_origin IS NULL "
            "OR (completion_origin = 'manual' AND completed_at IS NULL))"
        ):
            bad.append(f"done item {r[0]} without origin / manual completed_at")
        for r in rows("SELECT id FROM action_item WHERE status = 'open' AND (completed_at IS NOT NULL OR completion_origin IS NOT NULL)"):
            bad.append(f"open item {r[0]} with completion data")
        return bad
    finally:
        conn.close()


WHOLE_DB_FIXTURES = FIXTURES_NOTES.parent / "whole_db"


def copy_scenario(tmp_path: Path, scenario: str, names: tuple[str, ...] | None = None, dest: str = "notes") -> Path:
    """Copy (a subset of) ``tests/fixtures/whole_db/<scenario>/`` into ``tmp_path/<dest>`` (fixtures are never written)."""
    folder = tmp_path / dest
    folder.mkdir(exist_ok=True)
    for src in sorted((WHOLE_DB_FIXTURES / scenario).iterdir()):
        if src.is_file() and (names is None or src.name in names):
            shutil.copyfile(src, folder / src.name)
    return folder


def file_stamp(folder: Path) -> dict[str, tuple[str, int, int]]:
    """(sha256, size, mtime_ns) of every file of the folder: proof that no note was touched (BR-01, AC-01.11)."""
    import hashlib

    out = {}
    for p in sorted(folder.iterdir()):
        if p.is_file():
            st = p.stat()
            out[p.name] = (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_size, st.st_mtime_ns)
    return out
