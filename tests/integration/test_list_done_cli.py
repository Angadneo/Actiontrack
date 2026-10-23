"""``list`` and ``done`` end to end on the ingested sample (M6; spec 4.3, 4.4, AC-02.*, AC-03.*).

The sample is ingested ONCE per module; every test that changes state works on its own copy of the database.
"""

from __future__ import annotations

import io as _io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from actiontrack import cli
from tests.conftest import REPO_ROOT

CONFIG = str(REPO_ROOT / "tests" / "fixtures" / "config" / "valid_full.toml")
TODAY = "2026-10-22"


def invoke(db, *args):
    """``cli.main`` with captured streams -> ``(rc, stdout, stderr)``."""
    out, err = _io.StringIO(), _io.StringIO()
    argv = [*args, "--config", CONFIG, "--today", TODAY, "--db", str(db)]
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture(scope="module")
def sample_db(tmp_path_factory):
    root = tmp_path_factory.mktemp("listdone")
    notes = root / "notes"
    shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "notes", notes)
    db = root / "a.db"
    rc, _out, _err = invoke(db, "ingest", str(notes))
    assert rc == 5  # scratch.md is a deliberately binary file; the other files ingest
    return db


@pytest.fixture
def db_copy(sample_db, tmp_path):
    target = tmp_path / "copy.db"
    shutil.copyfile(sample_db, target)
    return target


def items_json(db, *args):
    rc, out, err = invoke(db, "list", "--format", "json", *args)
    assert rc == 0, err
    return json.loads(out)


# ----------------------------------------------------------------------------------- list
def test_default_list_is_the_open_items_table(sample_db):
    rc, out, err = invoke(sample_db, "list")
    assert rc == 0
    lines = out.split("\n")
    assert lines[0] == f"As of {TODAY} (tz Asia/Kolkata) — 85 items"
    assert lines[1].split() == ["ID", "STATUS", "DUE", "OWNER", "TASK", "SOURCE", "FLAGS"]
    assert len(lines) - 3 == 85 and lines[-1] == ""  # header + columns + 85 rows + final newline
    assert max(len(line) for line in lines[1:]) <= 120
    statuses = {line.split()[1] for line in lines[2:-1]}
    assert statuses == {"open", "overdue"}
    # sorted by due date; undated items last
    dues = [line.split()[2] for line in lines[2:-1]]
    dated = [d for d in dues if d[:2] == "20"]
    assert dated == sorted(dated) and dues[: len(dated)] == dated


def test_status_filters_and_counts(sample_db):
    assert len(items_json(sample_db, "--status", "all")["items"]) == 103
    assert len(items_json(sample_db, "--status", "done")["items"]) == 18
    overdue = items_json(sample_db, "--status", "overdue")["items"]
    assert overdue and all(i["display_status"] == "overdue" and i["status"] == "open" for i in overdue)
    assert all(i["days_overdue"] >= 1 and i["due_date"] < TODAY for i in overdue)


def test_overdue_table_has_no_days_overdue_column(sample_db):
    rc, out, _ = invoke(sample_db, "list", "--status", "overdue")
    assert rc == 0 and "overdue" in out.split("\n")[1] + out.split("\n")[2]
    assert "DAYS" not in out.upper().split("\n")[1]


def test_due_before_is_strict_and_excludes_undated(sample_db):
    items = items_json(sample_db, "--due-before", "2026-10-16")["items"]
    assert items and all(i["due_date"] is not None and i["due_date"] < "2026-10-16" for i in items)
    on_boundary = {i["id"] for i in items_json(sample_db, "--due-before", "2026-10-17")["items"]}
    assert {i["id"] for i in items} < on_boundary  # items due exactly 10-16 appear only for the later bound
    assert all(i["id"] not in {u["id"] for u in items} for i in items_json(sample_db)["items"] if i["due_date"] is None)


def test_owner_filter_priya_nair(sample_db):
    items = items_json(sample_db, "--owner", "Priya Nair")["items"]
    assert items
    for i in items:
        assert any(o["person_id"] == "priya-nair" for o in i["owners"])
        assert i["status"] == "open"
    texts = " ".join(i["task_text"].lower() for i in items)
    assert "revised quote" not in texts and "notify" not in texts  # completed via Tier-1b, not open
    done = items_json(sample_db, "--owner", "Priya Nair", "--status", "done")["items"]
    assert any("revised quote" in i["task_text"].lower() and "CI" in i["flags"] for i in done)
    rc, out, _ = invoke(sample_db, "list", "--owner", "Priya Nair")
    assert rc == 0 and out.split("\n")[0].endswith(f"— {len(items)} items")


def test_owner_bare_first_name_exits_6_and_prints_no_table(sample_db):
    rc, out, err = invoke(sample_db, "list", "--owner", "Priya")
    assert rc == 6 and out == ""
    assert "Priya Nair <" in err and "Priya Shah <" in err and "full name" in err


def test_owner_unknown_exits_3(sample_db):
    rc, out, err = invoke(sample_db, "list", "--owner", "Zed Nobody")
    assert rc == 3 and out == "" and "Zed Nobody" in err


def test_ambiguous_listing_and_include_ambiguous(sample_db):
    rc, out, _ = invoke(sample_db, "list", "--ambiguous")
    assert rc == 0 and out.split("\n")[0].endswith("— 7 items")
    assert "Priya Nair | Priya Shah" in out and "Rahul Mehta | Rahul Verma" in out and "?Priya (Nair | Shah)" in out
    amb = items_json(sample_db, "--ambiguous", "--status", "all")["items"]
    assert len(amb) >= 7 and all(any(o["owner_status"] == "ambiguous" for o in i["owners"]) for i in amb)
    plain = {i["id"] for i in items_json(sample_db, "--owner", "Priya Nair")["items"]}
    inc = items_json(sample_db, "--owner", "Priya Nair", "--include-ambiguous")["items"]
    extra = [i for i in inc if i["id"] not in plain]
    assert extra and all("OA" in i["flags"] for i in extra)
    assert all(
        "priya-nair" in [c["person_id"] for o in i["owners"] for c in o["candidates"]] for i in extra
    )


@pytest.mark.parametrize(
    "args", [["--status", "bogus"], ["--due-before", "2026-02-30"], ["--include-ambiguous"], ["--ambiguous", "--owner", "X"]]
)
def test_bad_list_arguments_exit_2_without_touching_the_db(tmp_path, args):
    missing = tmp_path / "never.db"
    rc, out, err = invoke(missing, "list", *args)
    assert rc == 2 and out == "" and not missing.exists()


def test_missing_database_exits_3_and_is_not_created(tmp_path):
    missing = tmp_path / "sub" / "none.db"
    rc, out, err = invoke(missing, "list")
    assert rc == 3 and out == "" and "actiontrack ingest" in err
    assert not missing.exists() and not missing.parent.exists()


def test_json_envelope_and_item_fields(sample_db):
    data = items_json(sample_db)
    assert list(data) == ["as_of", "timezone", "items"] and data["as_of"] == TODAY
    assert data["timezone"] == "Asia/Kolkata" and len(data["items"]) == 85
    item = data["items"][0]
    for key in ("id", "status", "display_status", "due_date", "task_text", "owners", "sources", "flags", "links"):
        assert key in item
    merged = [i for i in data["items"] if "CR" in i["flags"]]
    assert merged and all(len(i["sources"]) >= 2 for i in merged)
    assert [i["id"] for i in data["items"]] == [i["id"] for i in items_json(sample_db)["items"]]  # deterministic


# ------------------------------------------------------------------------------------ done
def first_open_id(db):
    return items_json(db)["items"][0]["id"]


def test_done_then_list_no_longer_shows_it(db_copy):
    item_id = first_open_id(db_copy)
    label = f"AT-{item_id:04d}"
    rc, out, err = invoke(db_copy, "done", label)
    assert rc == 0 and out.startswith(f"Marked {label} done (was open). Task: \"") and "Source: " in out
    assert item_id not in {i["id"] for i in items_json(db_copy)["items"]}
    done = {i["id"]: i for i in items_json(db_copy, "--status", "done")["items"]}
    assert done[item_id]["completion_origin"] == "manual" and done[item_id]["completed_at"].endswith("Z")
    assert len(done) == 19
    # second call: already done, exit 0, no change
    before = done[item_id]
    rc, out, _ = invoke(db_copy, "done", str(item_id))
    assert rc == 0 and out.startswith(f"{label} already done (completed ") and "origin manual" in out
    assert {i["id"]: i for i in items_json(db_copy, "--status", "done")["items"]}[item_id] == before


def test_done_json_and_id_forms(db_copy):
    item_id = first_open_id(db_copy)
    rc, out, _ = invoke(db_copy, "done", f"at-{item_id}", "--format", "json")
    obj = json.loads(out)
    assert rc == 0 and list(obj) == ["id", "status", "previous_status", "already_done", "completion_origin", "completed_at"]
    assert obj["id"] == item_id and obj["previous_status"] == "open" and obj["already_done"] is False
    rc, out, _ = invoke(db_copy, "--format", "json", "done", str(item_id))
    assert rc == 0 and json.loads(out)["already_done"] is True


def test_done_unknown_and_malformed_ids(db_copy):
    before = db_copy.read_bytes()
    rc, out, err = invoke(db_copy, "done", "AT-9999")
    assert rc == 3 and out == "" and "AT-9999" in err
    for bad in ("abc", "AT-", "1.5"):
        rc, out, err = invoke(db_copy, "done", bad)
        assert rc == 2 and out == ""
    assert db_copy.read_bytes() == before


def test_done_prints_possible_duplicate_notice_on_stderr(db_copy):
    statuses = {i["id"]: i["status"] for i in items_json(db_copy, "--status", "all")["items"]}
    linked = next(i for i in items_json(db_copy)["items"] if "D?" in i["flags"])
    others = {
        lk["to_id"] if lk["from_id"] == linked["id"] else lk["from_id"]
        for lk in linked["links"]
        if lk["kind"] == "possible_duplicate"
    }
    assert others
    rc, out, err = invoke(db_copy, "done", str(linked["id"]))
    assert rc == 0 and "Marked" in out and "duplicate" not in out
    assert "possible duplicates" in err and all(f"AT-{o:04d}" in err for o in others)
    after = {i["id"]: i["status"] for i in items_json(db_copy, "--status", "all")["items"]}
    assert all(after[o] == statuses[o] for o in others)  # linked items are never modified by done
    other_open = next(i["id"] for i in items_json(db_copy)["items"] if "D?" in i["flags"])
    rc, out, err = invoke(db_copy, "--quiet", "done", str(other_open))
    assert rc == 0 and "Marked" in out and "possible duplicates" not in err


def test_done_on_missing_database_exits_3(tmp_path):
    rc, out, err = invoke(tmp_path / "none.db", "done", "1")
    assert rc == 3 and out == "" and not (tmp_path / "none.db").exists()


# ------------------------------------------------------------------------------ subprocess
def test_real_process_list(sample_db, tmp_path):
    env = {
        "PYTHONPATH": str(REPO_ROOT / "src"),
        **{k: os.environ[k] for k in ("SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE") if k in os.environ},
        "PYTHONIOENCODING": "cp1252",
    }
    r = subprocess.run(
        [sys.executable, "-m", "actiontrack", "list", "--owner", "Priya Nair", "--config", CONFIG,
         "--today", TODAY, "--db", str(sample_db)],
        capture_output=True, env=env, cwd=tmp_path, timeout=120,
    )
    out = r.stdout.decode("utf-8")
    assert r.returncode == 0 and r.stderr == b"" and b"\r\n" not in r.stdout
    assert out.startswith(f"As of {TODAY} (tz Asia/Kolkata) — ") and "Priya Nair" in out
