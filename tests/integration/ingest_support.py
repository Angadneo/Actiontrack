"""Shared helpers for the M5 ingest integration tests (T-M5-04/05/07). Not a test module."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import UTC, date, datetime, timezone
from pathlib import Path

from actiontrack.clock import FixedClock, ResolvedZone, RunContext
from actiontrack.config import Config, Owners, load_config
from actiontrack.store.db import Database
from actiontrack.store.repo import Repo

from tests.conftest import REPO_ROOT

ROSTER_TOML = REPO_ROOT / "tests" / "fixtures" / "config" / "valid_full.toml"
FIXED_NOW = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)

#: tables whose content is the business state (everything except run bookkeeping / warnings)
BUSINESS_TABLES = (
    "person",
    "person_alias",
    "source_file",
    "meeting",
    "meeting_attendee",
    "action_item",
    "item_source",
    "item_source_owner",
    "item_owner",
    "item_link",
    "item_event",
)


def roster_cfg(*, attendance: bool = True) -> Config:
    cfg = load_config(ROSTER_TOML)
    return dataclasses.replace(cfg, owners=Owners(attendee_disambiguation=attendance))


def run_ctx(today: str = "2026-10-22", clock: FixedClock | None = None) -> RunContext:
    clk = clock or FixedClock(FIXED_NOW)
    return RunContext(clk, date.fromisoformat(today), "UTC", ResolvedZone("UTC", timezone.utc, False))


def dump_tables(conn: sqlite3.Connection, tables: tuple[str, ...] = BUSINESS_TABLES) -> dict[str, list[tuple]]:
    """Every row of each table, ordered by all columns (so row order never matters)."""
    out: dict[str, list[tuple]] = {}
    for table in tables:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        order = ", ".join(f'"{c}"' for c in cols)
        out[table] = [tuple(r) for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}')]
    return out


def write_note(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def open_repo(path: Path) -> tuple[Database, Repo]:
    db = Database.open(path, clock=FixedClock(FIXED_NOW))
    return db, Repo(db)


def simple_note(date_text: str = "2026-09-01", title: str = "Kickoff", items: tuple[str, ...] = ()) -> str:
    """A minimal valid note with attendees from the sample roster."""
    body = "\n".join(items) if items else "- [ ] Priya Nair to send the revised quote by Friday"
    return (
        f"# Meeting: {title}\n\nDate: {date_text}\nAttendees: Priya Nair, Rahul Mehta, Anita Desai\n\n"
        f"## Action items\n\n{body}\n"
    )
