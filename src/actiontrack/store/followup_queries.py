"""SQL used by ``services/followup.py`` (the lint ``test_no_sql_outside_store`` keeps SQL in ``store/``).

Kept in its own module because ``store/repo.py`` is owned by another change; fold into ``Repo`` later.
All functions take a ``Repo``; reads are plain SELECTs, nothing here commits.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import PurePosixPath

from actiontrack.store.repo import Repo


def first_sources(repo: Repo) -> dict[int, tuple[str | None, date | None, str]]:
    """Earliest source of every item: ``{item_id: (meeting title, meeting date, file stem)}``."""
    out: dict[int, tuple[str | None, date | None, str]] = {}
    rows = repo.conn.execute(
        "SELECT s.item_id, m.title, m.meeting_date, f.rel_path FROM item_source s "
        "JOIN source_file f ON f.id = s.source_file_id LEFT JOIN meeting m ON m.id = s.meeting_id "
        "ORDER BY s.item_id, (m.meeting_date IS NULL), m.meeting_date, s.id"
    ).fetchall()
    for r in rows:
        if r["item_id"] in out:
            continue
        d = date.fromisoformat(r["meeting_date"]) if r["meeting_date"] else None
        out[r["item_id"]] = (r["title"], d, PurePosixPath(str(r["rel_path"]).replace("\\", "/")).stem)
    return out


def folder_roots(repo: Repo) -> list[str]:
    return [r["folder_root"] for r in repo.conn.execute("SELECT DISTINCT folder_root FROM source_file").fetchall()]


def repeat_row(repo: Repo, person_id: str, ids_hash: str, statuses: Sequence[str]) -> sqlite3.Row | None:
    marks = ",".join("?" for _ in statuses)
    return repo.conn.execute(
        f"SELECT * FROM followup_draft WHERE person_id = ? AND item_ids_hash = ? AND status IN ({marks}) "
        "ORDER BY id DESC LIMIT 1",
        (person_id, ids_hash, *statuses),
    ).fetchone()


def has_other_pending(repo: Repo, person_id: str, ids_hash: str) -> bool:
    row = repo.conn.execute(
        "SELECT 1 FROM followup_draft WHERE person_id = ? AND status = 'pending_handoff' "
        "AND item_ids_hash <> ? LIMIT 1",
        (person_id, ids_hash),
    ).fetchone()
    return row is not None


def draft_by_path(repo: Repo, handoff_path: str) -> sqlite3.Row | None:
    return repo.conn.execute("SELECT * FROM followup_draft WHERE handoff_path = ?", (handoff_path,)).fetchone()


def draft_by_args_sha(repo: Repo, args_sha256: str) -> sqlite3.Row | None:
    return repo.conn.execute(
        "SELECT * FROM followup_draft WHERE args_sha256 = ? AND channel = 'handoff' ORDER BY id DESC LIMIT 1",
        (args_sha256,),
    ).fetchone()
