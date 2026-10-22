"""Repository base: NFC funnel, verbatim raw_text, dry-run copy, no-SQL-outside-store (T-M1-13)."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import unicodedata
from pathlib import Path

import pytest

from actiontrack.errors import InternalError
from actiontrack.store.db import Database
from actiontrack.store.repo import (
    VERBATIM_COLUMNS,
    Repo,
    format_item_id,
    parse_item_id,
    to_storage_raw,
    to_storage_text,
)

from tests.conftest import REPO_ROOT

DECOMPOSED = "Café review"  # e + COMBINING ACUTE
COMPOSED = "Café review"
NOW = "2026-10-22T00:00:00Z"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def new_item(repo, description=DECOMPOSED, task_text="x"):
    return repo.insert_row(
        "action_item",
        {
            "description": description,
            "task_text": task_text,
            "description_core_key": "k",
            "created_at": NOW,
            "updated_at": NOW,
        },
    )


@pytest.fixture
def repo(tmp_db_path):
    with Database.open(tmp_db_path) as db:
        yield Repo(db)


def test_id_helpers_reexported():
    assert format_item_id(4) == "AT-0004" and parse_item_id("at-4") == 4


def test_nfc_normalisation_on_write(repo):
    item = new_item(repo, DECOMPOSED, DECOMPOSED)
    row = repo.get_item(item)
    assert row["description"] == COMPOSED and row["task_text"] == COMPOSED
    assert row["description"].encode("utf-8") == COMPOSED.encode("utf-8")
    assert to_storage_text(DECOMPOSED) == COMPOSED
    repo.insert_row(
        "person",
        {"id": "a-b", "first_name": DECOMPOSED, "display_name": DECOMPOSED, "name_key": "k",
         "identity_key": "a-b", "origin": "roster"},
    )
    assert repo.conn.execute("SELECT first_name FROM person").fetchone()[0] == COMPOSED


@pytest.mark.ac("AC-01.19")
def test_raw_text_stored_verbatim_not_nfc(repo):
    item = new_item(repo)
    sf = repo.insert_row("source_file", {"folder_root": "/n", "rel_path": DECOMPOSED + ".md", "status": "ok"})
    src = repo.insert_row(
        "item_source",
        {
            "item_id": item, "source_file_id": sf, "line_start": 1, "line_end": 1,
            "raw_text": "- [ ] " + DECOMPOSED, "checkbox_state": "open", "block_hash": "h",
            "description": DECOMPOSED,
        },
    )
    row = repo.conn.execute("SELECT raw_text, description FROM item_source WHERE id = ?", (src,)).fetchone()
    assert row["raw_text"] == "- [ ] " + DECOMPOSED
    assert unicodedata.is_normalized("NFD", row["raw_text"].split("] ")[1])
    assert row["description"] == COMPOSED  # same text through the per-source description column is NFC
    # filesystem names are verbatim too
    assert repo.conn.execute("SELECT rel_path FROM source_file").fetchone()[0] == DECOMPOSED + ".md"
    assert to_storage_raw(DECOMPOSED) == DECOMPOSED
    assert ("item_source", "raw_text") in VERBATIM_COLUMNS


@pytest.mark.ac("AC-09.6")
def test_unicode_roundtrip_through_repo(repo):
    text = "Zoë Åström 李雷 \U0001f600"
    item = new_item(repo, text, text)
    row = repo.get_item(item)
    assert row["description"] == text and row["task_text"].encode("utf-8") == text.encode("utf-8")


def test_item_id_preview_and_get_item(repo):
    assert repo.next_item_id_preview() == 1
    assert repo.get_item(1) is None
    a = new_item(repo)
    assert repo.next_item_id_preview() == a + 1 == 2
    with pytest.raises(RuntimeError):
        with repo.db.transaction():
            new_item(repo)
            raise RuntimeError
    assert repo.get_item(a) is not None
    assert repo.schema_version() == 1


def test_insert_row_validates_names(repo):
    with pytest.raises(InternalError):
        repo.insert_row("no_such_table", {"a": 1})
    with pytest.raises(InternalError):
        repo.insert_row("person", {"id": "x", "nope": 1})
    with pytest.raises(InternalError):
        repo.update_row("person", "nope", 1, {"id": "x"})


def test_update_row_uses_nfc(repo):
    item = new_item(repo, "plain")
    assert repo.update_row("action_item", "id", item, {"description": DECOMPOSED}) == 1
    assert repo.get_item(item)["description"] == COMPOSED


@pytest.mark.ac("AC-01.10")
@pytest.mark.sc("SC-40")
def test_dry_run_copy_leaves_file_byte_identical(tmp_db_path):
    with Database.open(tmp_db_path) as db:
        new_item(Repo(db))
    before, listing = sha(tmp_db_path), sorted(os.listdir(tmp_db_path.parent))
    dry = Database.open_dry_run(tmp_db_path)
    r = Repo(dry)
    with dry.transaction():
        new_item(r)
        new_item(r)
        r.insert_row("ingest_run", {"started_at": NOW, "folder_root": "f", "dry_run": 1})
    assert dry.conn.execute("SELECT COUNT(*) FROM action_item").fetchone()[0] == 3
    dry.close()
    assert sha(tmp_db_path) == before
    assert sorted(os.listdir(tmp_db_path.parent)) == listing  # no -journal, .bak or .lock
    with Database.open(tmp_db_path, readonly=True) as check:
        assert check.conn.execute("SELECT COUNT(*) FROM action_item").fetchone()[0] == 1


@pytest.mark.ac("AC-01.10")
@pytest.mark.sc("SC-40")
def test_dry_run_with_no_db_creates_nothing(tmp_path):
    target = tmp_path / "work" / ".actiontrack" / "actiontrack.db"
    (tmp_path / "work").mkdir()
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    dry = Database.open_dry_run(target)
    assert dry.in_memory and dry.version == 1
    with dry.transaction():
        new_item(Repo(dry))
    dry.close()
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before
    assert not target.parent.exists()


SQL_RE = re.compile(
    r"^\s*(SELECT\s.+\sFROM\s|INSERT\s+(OR\s+\w+\s+)?INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s|"
    r"CREATE\s+(TABLE|INDEX|UNIQUE)|ALTER\s+TABLE|DROP\s+TABLE|PRAGMA\s|BEGIN(\s+IMMEDIATE)?\s*$)",
    re.IGNORECASE | re.DOTALL,
)


def test_no_sql_outside_store():
    root = REPO_ROOT / "src" / "actiontrack"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] == "store":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "sqlite3" for a in node.names):
                offenders.append(f"{rel}:{node.lineno} imports sqlite3")
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "sqlite3":
                offenders.append(f"{rel}:{node.lineno} imports sqlite3")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and SQL_RE.match(node.value):
                offenders.append(f"{rel}:{node.lineno} SQL string {node.value[:40]!r}")
    assert not offenders, offenders
