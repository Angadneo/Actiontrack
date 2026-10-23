"""followup (handoff channel) end to end on the ingested sample (M7 happy flow).

The sample is ingested once per module; every test works on its own copy of that database and
runs the real ``cli.main`` with the real handler registered.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from actiontrack import cli
from actiontrack.gmail.gateway import args_sha256, item_ids_hash
from actiontrack.services import followup as fu
from tests.conftest import FIXTURES_NOTES, REPO_ROOT

CONFIG = REPO_ROOT / "tests" / "fixtures" / "config" / "valid_full.toml"
TODAY = "2026-10-22"
PENDING_LINE = "Handoff prepared. Draft NOT yet created in Gmail."


@pytest.fixture(scope="module")
def ingested_db(tmp_path_factory):
    root = tmp_path_factory.mktemp("followup_ingest")
    notes = root / "notes"
    shutil.copytree(FIXTURES_NOTES, notes)
    db = root / "a.db"
    mp = pytest.MonkeyPatch()
    try:
        rc = cli.main(["ingest", str(notes), "--config", str(CONFIG), "--today", TODAY, "--db", str(db), "--quiet"])
    finally:
        mp.undo()
    assert rc in (0, 5)
    return db, notes


@pytest.fixture
def run(ingested_db, tmp_path, monkeypatch, capsys):
    """``run(*argv) -> (rc, stdout, stderr)`` against a private copy of the ingested database."""
    db = tmp_path / "work.db"
    shutil.copy2(ingested_db[0], db)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(cli.HANDLERS, "followup", fu.followup_handler)
    monkeypatch.setattr(fu, "stdin_is_tty", lambda: False)

    def _run(*argv):
        capsys.readouterr()
        rc = cli.main(["followup", *argv, "--config", str(CONFIG), "--today", TODAY, "--db", str(db)])
        cap = capsys.readouterr()
        return rc, cap.out, cap.err

    _run.db = db
    _run.handoff = tmp_path / ".actiontrack" / "handoff"
    return _run


def rows(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM followup_draft ORDER BY id").fetchall()
    finally:
        conn.close()


def tty(monkeypatch, *answers):
    monkeypatch.setattr(fu, "stdin_is_tty", lambda: True)
    queue = list(answers)
    asked = []

    def answer(prompt):
        asked.append(prompt)
        return queue.pop(0) if queue else ""

    monkeypatch.setattr(fu, "read_answer", answer)
    return asked


def test_dry_run_preview_writes_nothing(run):
    rc, out, _ = run("Priya Nair", "--dry-run")
    assert rc == 0
    assert "--- DRAFT PREVIEW (not yet created) ---" in out
    assert "Subject: Follow-up: your open action items (" in out
    assert "Hi Priya Nair," in out and "Overdue (" in out
    assert "items with ambiguous owner may belong to Priya Nair" in out
    assert "Priya Shah" in out  # candidates named on the console summary only
    body = out.split("Subject:", 1)[1]
    assert "Priya Shah" not in body.split("Hi Priya Nair,", 1)[1]  # never in the email body
    assert not run.handoff.exists()
    assert rows(run.db) == []


def test_dry_run_works_without_email(run):
    rc, out, _ = run("Rahul Mehta", "--dry-run")
    assert rc in (0,)
    assert "To: (none - dry run)" in out or "no open items for Rahul Mehta" in out


def test_handoff_written_and_recorded(run):
    rc, out, _ = run("Priya Nair", "--yes")
    assert rc == 0
    assert PENDING_LINE in out and "Handoff file:" in out and "NOT SENT. Nothing exists in Gmail yet." in out
    assert "create_draft only; never send" in out
    assert "Draft created" not in out and "success" not in out.lower()
    files = list(run.handoff.glob("*.json"))
    assert len(files) == 1 and files[0].name.endswith("-priya-nair.json")
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert set(doc) == {
        "version", "channel", "created_at", "person_id", "item_ids", "item_ids_hash", "as_of", "create_draft_args", "args_sha256",
    }
    args = doc["create_draft_args"]
    assert set(args) == {"to", "subject", "body", "includeSignature"}
    assert args["to"] == ["angad.kulkarni@coditas.com"] and args["includeSignature"] is False
    assert doc["as_of"] == TODAY and doc["person_id"] == "priya-nair"
    assert doc["args_sha256"] == args_sha256(args)
    assert doc["item_ids_hash"] == item_ids_hash(doc["item_ids"])
    assert args["subject"].startswith("Follow-up: your open action items (6 open, 5 overdue)")
    (row,) = rows(run.db)
    assert row["status"] == "pending_handoff" and row["channel"] == "handoff" and row["source"] == "actiontrack"
    assert row["args_sha256"] == doc["args_sha256"] and row["item_ids_hash"] == doc["item_ids_hash"]
    assert json.loads(row["item_ids"]) == doc["item_ids"] and row["gmail_draft_id"] is None
    assert Path(row["handoff_path"]) == files[0].resolve() or Path(row["handoff_path"]).samefile(files[0])
    assert b"Could you reply with a status" not in run.db.read_bytes()  # body never stored in the DB


def test_json_output_and_repeat_rules(run):
    rc, out, _ = run("Priya Nair", "--yes", "--format", "json")
    obj = json.loads(out)
    assert rc == 0 and obj["status"] == "pending_handoff" and obj["person_id"] == "priya-nair"
    assert {"item_ids", "item_ids_hash", "handoff_file", "args_sha256"} <= set(obj)
    # repeat, non-TTY: --yes alone is refused
    rc, _, err = run("Priya Nair", "--yes")
    assert rc == 2 and "repeat draft: add --force to proceed" in err
    assert len(rows(run.db)) == 1 and len(list(run.handoff.glob("*.json"))) == 1
    # --yes --force proceeds and records a second row
    rc, _, _ = run("Priya Nair", "--yes", "--force")
    assert rc == 0 and len(rows(run.db)) == 2
    # --include-ambiguous changes the hash: no repeat warning
    rc, out, _ = run("Priya Nair", "--yes", "--include-ambiguous")
    assert rc == 0 and "Warning: a handoff was already" not in out
    last = rows(run.db)[-1]
    assert last["include_ambiguous"] == 1 and len(json.loads(last["ambiguous_item_ids"])) == 4
    doc = json.loads(Path(last["handoff_path"]).read_text(encoding="utf-8"))
    body = doc["create_draft_args"]["body"]
    assert "Items that may be yours" in body and "Sign off the cutover checklist" in body
    assert "Priya Shah" not in body


def test_without_yes_non_tty_exits_2(run):
    rc, out, err = run("Priya Nair")
    assert rc == 2 and "--yes" in err and not run.handoff.exists() and rows(run.db) == []


def test_ambiguous_bare_name_exit_6(run):
    rc, out, err = run("Priya", "--yes")
    assert rc == 6 and "Priya Nair <angad.kulkarni@coditas.com>" in err and "Priya Shah <pranay.pathak@coditas.com>" in err
    assert not run.handoff.exists() and rows(run.db) == []


def test_unknown_owner_exit_3(run):
    rc, _, _ = run("Nobody Here", "--yes")
    assert rc == 3 and not run.handoff.exists()


def test_no_recipient_exit_7_and_to_flag(run):
    rc, out, err = run("Rahul Mehta", "--yes")
    if "no open items" in out:
        pytest.skip("Rahul Mehta has no open items in the sample")
    assert rc == 7 and "No email configured" in err and "--to" in err
    assert not run.handoff.exists() and rows(run.db) == []
    rc, _, _ = run("Rahul Mehta", "--yes", "--to", "rahul@example.com")
    assert rc == 0 and len(rows(run.db)) == 1
    rc, _, err = run("Rahul Mehta", "--yes", "--to", "a@b.com, c@d.com")
    assert rc == 2


def test_cancel_on_tty_exit_9(run, monkeypatch):
    asked = tty(monkeypatch, "n")
    rc, out, err = run("Priya Nair")
    assert rc == 9 and "Cancelled. Nothing created." in err
    assert asked == ["Prepare Gmail draft handoff? [y/N] "]
    assert not run.handoff.exists() and rows(run.db) == []
    tty(monkeypatch)  # EOF / empty answer also declines
    rc, _, _ = run("Priya Nair")
    assert rc == 9


def test_tty_yes_proceeds_and_repeat_prompts_even_with_yes(run, monkeypatch):
    tty(monkeypatch, "y")
    rc, out, _ = run("Priya Nair")
    assert rc == 0 and PENDING_LINE in out
    asked = tty(monkeypatch, "n")
    rc, out, err = run("Priya Nair", "--yes")
    assert rc == 9 and asked and "another draft" in asked[0] and len(rows(run.db)) == 1


def test_record_draft_flow(run):
    rc, _, _ = run("Priya Shah", "--yes")
    assert rc == 0
    (file,) = run.handoff.glob("*.json")
    rc, out, _ = run("--record-draft", str(file), "--draft-id", "r-123")
    assert rc == 0 and "Recorded draft r-123 (created by host connector; not verified by actiontrack)" in out
    (row,) = rows(run.db)
    assert row["status"] == "created_by_host" and row["gmail_draft_id"] == "r-123" and row["source"] == "host"
    assert row["recorded_at"]
    rc, _, _ = run("--record-draft", str(file), "--draft-id", "r-123")
    assert rc == 2  # already recorded


def test_record_draft_failure_tamper_and_validation(run, tmp_path):
    run("Priya Shah", "--yes")
    (file,) = run.handoff.glob("*.json")
    assert run("--record-draft", str(file))[0] == 2  # created needs --draft-id
    assert run("--record-draft", str(file), "--status", "failed", "--draft-id", "x")[0] == 2
    assert run("--record-draft", str(file), "--draft-id", "x; rm -rf")[0] == 2
    assert run("--record-draft", str(tmp_path / "missing.json"), "--draft-id", "r-1")[0] == 3
    doc = json.loads(file.read_text(encoding="utf-8"))
    original = file.read_text(encoding="utf-8")
    doc["create_draft_args"]["to"] = ["evil@example.com"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    rc, _, err = run("--record-draft", str(file), "--draft-id", "r-1")
    assert rc == 2 and "modified after it was prepared" in err
    assert rows(run.db)[0]["status"] == "pending_handoff"
    file.write_text(original, encoding="utf-8")
    rc, out, _ = run("--record-draft", str(file), "--status", "failed")
    assert rc == 0 and "Recorded host failure. No draft was created." in out
    assert rows(run.db)[0]["status"] == "failed_by_host"


def test_unwritable_handoff_dir_exit_4_no_row(run, tmp_path):
    (tmp_path / ".actiontrack").write_text("a file, not a directory", encoding="utf-8")
    rc, out, err = run("Priya Nair", "--yes")
    assert rc == 4 and "Handoff prepared" not in out + err
    assert rows(run.db) == []
