"""People persistence and registry transactionality (T-M4-07; spec 7.2, FR-05.3, NFR-REL-01, BR-18,
AC-05.10, AC-09.6) plus the person query against persisted people (T-M4-13, Appendix C roster)."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from actiontrack.config import load_config, parse_roster
from actiontrack.domain.models import PersonOrigin
from actiontrack.errors import AmbiguousOwnerError, NotFoundError
from actiontrack.resolution.people import (
    PeopleRegistry,
    PersonAmbiguous,
    PersonNotFound,
    file_transaction,
    resolve_person_query,
)
from actiontrack.store.db import Database
from actiontrack.store.repo import Repo

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "config"
NOW = "2026-10-22T00:00:00Z"


@pytest.fixture
def roster():
    return load_config(FIX / "valid_full.toml").roster


@pytest.fixture
def db(tmp_db_path):
    with Database.open(tmp_db_path, lock=True) as d:
        yield d


@pytest.fixture
def repo(db):
    return Repo(db)


def person_rows(db):
    return [tuple(r) for r in db.conn.execute("SELECT * FROM person ORDER BY id")]


def alias_rows(db):
    return [tuple(r) for r in db.conn.execute("SELECT * FROM person_alias ORDER BY person_id, alias_key")]


def new_meeting(repo) -> int:
    sf = repo.insert_row(
        "source_file", {"folder_root": "/n", "rel_path": "a.md", "status": "ok"}
    )
    return repo.insert_row("meeting", {"source_file_id": sf, "date_source": "none"})


def live_registry(repo, roster):
    """An ingest-like sequence: roster, attendees, provisional, an auto alias."""
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    reg.add_attendee("Tom Baker")
    reg.add_attendee("Zoë Åström")
    reg.add_provisional("Zed")
    reg.add_alias("priya-nair", "P. Nair", auto=True)
    repo.persist_registry(reg)
    return reg


@pytest.mark.ac("AC-05.10")
def test_rebuild_from_rows_equals_live_registry(repo, roster):
    live = live_registry(repo, roster)
    rebuilt = repo.load_registry(roster)
    assert rebuilt == live
    assert rebuilt.people() == live.people()
    assert rebuilt.aliases() == live.aliases()
    # also without a roster object: stored rows alone give the same people (roster flags kept)
    assert {p.id: p for p in repo.load_registry().people()} == {p.id: p for p in live.people()}


def test_rebuild_keeps_auto_flag_and_roster_alias_changes(repo, roster):
    live_registry(repo, roster)
    rebuilt = repo.load_registry(roster)
    flags = {(a.person_id, a.alias_key): a.auto for a in rebuilt.aliases()}
    assert flags[("priya-nair", "priya n")] is False
    assert flags[("priya-nair", "p nair")] is True
    # a roster alias dropped from the config disappears; the auto alias stays
    smaller = parse_roster([{"first_name": "Priya", "last_name": "Nair", "email": "x@y.zz"}])
    reg = repo.load_registry(smaller)
    keys = {a.alias_key for a in reg.aliases("priya-nair")}
    assert "priya n" not in keys and "p nair" in keys
    repo.upsert_roster(reg, smaller)
    assert ("priya-nair", "priya n") not in {(r[0], r[2]) for r in alias_rows(repo.db)}


@pytest.mark.ac("AC-05.10")
def test_same_attendees_twice_same_ids_one_row_each(db, repo, roster):
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    ids = []
    for _ in range(2):
        with file_transaction(db, reg):
            ids.append([reg.add_attendee(n).id for n in ("Tom Baker", "Ann Lee", "Priya Nair")])
            repo.persist_registry(reg)
    assert ids[0] == ids[1]
    assert ids[0][2] == "priya-nair"
    n = db.conn.execute("SELECT COUNT(*) FROM person WHERE id IN ('tom-baker', 'ann-lee')").fetchone()[0]
    assert n == 2
    assert db.conn.execute("SELECT COUNT(*) FROM person WHERE identity_key = 'priya nair'").fetchone()[0] == 1
    # a fresh process sees the same ids
    again = repo.load_registry(roster)
    assert again.add_attendee("Tom Baker").id == "tom-baker"
    assert len(again) == len(reg)


def test_rollback_leaves_neither_rows_nor_registry_changed(db, repo, roster):
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    rows_before = (person_rows(db), alias_rows(db))
    reg_before = reg.copy()
    snap_people = reg.people()

    def boom(name):
        if name == "after_attendees":
            raise RuntimeError("injected")

    db.fault_hook = boom
    with pytest.raises(RuntimeError, match="injected"):
        with file_transaction(db, reg):
            reg.add_attendee("Tom Baker")
            reg.add_provisional("Zed")
            reg.add_alias("priya-nair", "PN", auto=True)
            repo.persist_registry(reg)
            assert db.conn.execute("SELECT COUNT(*) FROM person WHERE id = 'tom-baker'").fetchone()[0] == 1
            db.fault("after_attendees")
    assert (person_rows(db), alias_rows(db)) == rows_before
    assert reg == reg_before and reg.people() == snap_people
    assert "tom-baker" not in reg and "zed" not in reg


def test_rollback_on_failure_at_commit_time(db, repo, roster):
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    before = person_rows(db)
    db.fault_hook = lambda name: (_ for _ in ()).throw(RuntimeError("x")) if name == "before_commit" else None
    with pytest.raises(RuntimeError):
        with file_transaction(db, reg):
            reg.add_attendee("Tom Baker")
            repo.persist_registry(reg)
    assert person_rows(db) == before and "tom-baker" not in reg


def test_commit_keeps_both(db, repo, roster):
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    with file_transaction(db, reg):
        reg.add_attendee("Tom Baker")
        repo.persist_registry(reg)
    assert "tom-baker" in reg
    assert db.conn.execute("SELECT origin FROM person WHERE id = 'tom-baker'").fetchone()[0] == "attendee"


def test_roster_email_change_updates_email_keeps_id(repo):
    reg = PeopleRegistry()
    r1 = parse_roster([{"first_name": "Priya", "last_name": "Nair", "email": "old@example.com"}])
    r2 = parse_roster([{"first_name": "Priya", "last_name": "Nair", "email": "new@example.com"}])
    repo.upsert_roster(reg, r1)
    repo.upsert_roster(reg, r2)
    rows = repo.conn.execute("SELECT id, email, origin, verified FROM person").fetchall()
    assert [tuple(r) for r in rows] == [("priya-nair", "new@example.com", "roster", 1)]
    fresh = repo.load_registry(r2)
    assert fresh.get("priya-nair").email == "new@example.com"
    assert repo.load_registry().get("priya-nair").email == "new@example.com"


def test_roster_person_removed_from_config_is_kept(repo, roster):
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    smaller = parse_roster([{"first_name": "Anita", "last_name": "Desai"}])
    reg2 = repo.load_registry(smaller)
    assert "priya-nair" in reg2 and reg2.get("priya-nair").origin is PersonOrigin.ROSTER
    repo.upsert_roster(reg2, smaller)
    assert repo.conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == len(roster)


@pytest.mark.ac("AC-09.6")
def test_unicode_names_roundtrip_byte_identical(repo):
    reg = PeopleRegistry()
    for raw in ("Zoë Åström", "李雷 Wang"):
        reg.add_attendee(raw)
    repo.persist_registry(reg)
    stored = {r[0]: r[1] for r in repo.conn.execute("SELECT id, display_name FROM person")}
    for raw in ("Zoë Åström", "李雷 Wang"):
        assert raw in stored.values()
        assert [v.encode("utf-8") for v in stored.values() if v == raw] == [raw.encode("utf-8")]
    back = repo.load_registry()
    assert back == reg


@pytest.mark.ac("AC-09.6")
def test_decomposed_input_is_stored_nfc(repo):
    reg = PeopleRegistry()
    decomposed = unicodedata.normalize("NFD", "Zoë Åström")
    reg.add_attendee(decomposed)
    repo.persist_registry(reg)
    (name,) = [r[0] for r in repo.conn.execute("SELECT display_name FROM person")]
    assert name == unicodedata.normalize("NFC", decomposed)
    assert name.encode() != decomposed.encode()


def test_persist_is_idempotent(repo, roster):
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    again = repo.persist_registry(reg)
    assert again["inserted"] == 0 and again["updated"] == 0


def test_merge_person_repoints_everything(db, repo, roster):
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    zed = reg.add_provisional("Pri")
    repo.persist_registry(reg)
    mid = new_meeting(repo)
    repo.insert_attendees(mid, [("Pri", zed.id, None), ("Tom", None, "t@x.zz")])
    item = repo.insert_row(
        "action_item",
        {"description": "d", "task_text": "t", "description_core_key": "k", "created_at": NOW, "updated_at": NOW},
    )
    repo.insert_row(
        "item_owner",
        {"item_id": item, "ordinal": 0, "owner_raw": "Pri", "person_id": zed.id,
         "owner_status": "provisional", "provenance": "provisional"},
    )
    rec = reg.merge_provisional(zed.id, "priya-nair")
    with db.transaction():
        repo.merge_person(rec.provisional_id, rec.into_person_id, alias=rec.alias, alias_key=rec.alias_key)
    refs = 0
    for table in ("meeting_attendee", "item_owner", "item_source_owner", "followup_draft"):
        refs += db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE person_id = ?", (zed.id,)).fetchone()[0]
    assert refs == 0
    assert db.conn.execute("SELECT COUNT(*) FROM person WHERE id = ?", (zed.id,)).fetchone()[0] == 0
    assert db.conn.execute("SELECT person_id FROM item_owner").fetchone()[0] == "priya-nair"
    assert [r["person_id"] for r in repo.list_attendees(mid) if r["raw_name"] == "Pri"] == ["priya-nair"]
    alias = db.conn.execute(
        "SELECT auto FROM person_alias WHERE person_id = 'priya-nair' AND alias_key = ?", (rec.alias_key,)
    ).fetchone()
    assert alias is not None and alias["auto"] == 1
    assert repo.load_registry(roster) == reg


def test_merge_refuses_roster_and_self(repo, roster):
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    from actiontrack.errors import InternalError

    with pytest.raises(InternalError):
        repo.merge_person("priya-nair", "priya-shah")
    with pytest.raises(InternalError):
        repo.merge_person("priya-nair", "priya-nair")


def test_ids_stable_and_never_reassigned_to_other_identity(repo):
    reg = PeopleRegistry()
    a = reg.add_attendee("Tom Baker")
    repo.persist_registry(reg)
    other = PeopleRegistry()
    roster = parse_roster([{"first_name": "Tom", "last_name": "Baker", "email": "t@x.zz"}])
    repo.upsert_roster(other, roster)  # empty registry: same identity -> same slug
    fresh = repo.load_registry(roster)
    assert fresh.get(a.id).origin is PersonOrigin.ROSTER
    assert [r[0] for r in repo.conn.execute("SELECT id FROM person")] == [a.id]


# ------------------------------------------------------------------------- T-M4-13 over the DB
@pytest.mark.ac("AC-02.6", "AC-04.6")
def test_person_query_against_rebuilt_registry(repo, roster):
    reg = PeopleRegistry()
    repo.upsert_roster(reg, roster)
    reg.add_provisional("Zed")
    repo.persist_registry(reg)
    rebuilt = repo.load_registry(roster)
    assert resolve_person_query("Priya Nair", rebuilt).id == "priya-nair"
    assert resolve_person_query("zed", rebuilt).origin is PersonOrigin.NOTE_OWNER
    with pytest.raises(PersonAmbiguous) as ei:
        resolve_person_query("Priya", rebuilt)
    assert isinstance(ei.value, AmbiguousOwnerError)
    assert ei.value.candidate_lines == (
        "Priya Nair <angad.kulkarni@coditas.com>",
        "Priya Shah <pranay.pathak@coditas.com>",
    )
    with pytest.raises(PersonNotFound) as nf:
        resolve_person_query("Priyaa Nair", rebuilt)
    assert isinstance(nf.value, NotFoundError)
