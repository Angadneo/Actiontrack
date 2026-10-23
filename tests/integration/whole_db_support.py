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
from tests.integration.ingest_support import FIXED_NOW, ROSTER_TOML, run_ctx
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
) -> Config:
    """The sample roster with people dropped (``"Priya Nair"``), added, or extra aliases per full name."""
    extra = aliases or {}
    blocks: list[str] = []
    for first, last, al in (*BASE_PEOPLE, *add):
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
