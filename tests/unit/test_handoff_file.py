"""Handoff file structure, atomic writer and gateway module hygiene (T-M7-10/11/19)."""

from __future__ import annotations

import ast
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from actiontrack.errors import EnvError
from actiontrack.gmail import handoff as handoff_module
from actiontrack.gmail.gateway import DraftRequest, args_sha256, item_ids_hash
from actiontrack.gmail.handoff import HandoffGateway, safe_person_slug

NOW = datetime(2026, 10, 22, 9, 30, 0, tzinfo=UTC)
KEYS = {"version", "channel", "created_at", "person_id", "item_ids", "item_ids_hash", "as_of", "create_draft_args", "args_sha256"}


def gateway(dir_, person="priya-shah"):
    return HandoffGateway(
        dir_, created_at=NOW, person_id=person, item_ids=[3, 1], item_ids_hash=item_ids_hash([1, 3]), as_of=date(2026, 10, 22)
    )


def test_file_structure_and_atomicity(tmp_path):
    d = tmp_path / "h" / "nested"
    req = DraftRequest("priya@example.com", "Subject", "Body 李雷")
    res = gateway(d).create_draft(req)
    assert res.status == "pending_handoff" and res.draft_id is None
    path = Path(res.detail)
    assert path.name == "20261022T093000Z-priya-shah.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc) == KEYS and doc["version"] == 1 and doc["channel"] == "handoff"
    assert doc["created_at"] == "2026-10-22T09:30:00Z" and doc["item_ids"] == [1, 3]
    assert set(doc["create_draft_args"]) == {"to", "subject", "body", "includeSignature"}
    assert doc["create_draft_args"]["to"] == ["priya@example.com"]
    assert doc["args_sha256"] == args_sha256(doc["create_draft_args"])
    assert not list(d.glob("*.tmp"))
    assert b"\r" not in path.read_bytes() and not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_same_second_collision_gets_suffix(tmp_path):
    req = DraftRequest("a@b.com", "S", "B")
    p1 = Path(gateway(tmp_path).create_draft(req).detail)
    p2 = Path(gateway(tmp_path).create_draft(req).detail)
    assert p1 != p2 and p2.name.endswith("-priya-shah-2.json")


def test_unwritable_dir_is_env_error_and_leaves_nothing(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(EnvError):
        gateway(blocker / "sub").create_draft(DraftRequest("a@b.com", "S", "B"))
    assert [p.name for p in tmp_path.iterdir()] == ["file"]


def test_failed_replace_removes_tmp(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(handoff_module.os, "replace", boom)
    with pytest.raises(EnvError):
        gateway(tmp_path).create_draft(DraftRequest("a@b.com", "S", "B"))
    assert list(tmp_path.iterdir()) == []


def test_slug_is_filename_safe():
    assert safe_person_slug("priya-shah") == "priya-shah"
    s = safe_person_slug("李雷")
    assert s.isascii() and s.startswith("p-")
    assert safe_person_slug("zoë-åström").isascii()


def test_handoff_module_has_no_network_imports():
    tree = ast.parse(Path(handoff_module.__file__).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert not roots & {"urllib", "http", "socket", "ssl", "requests", "httpx"}
