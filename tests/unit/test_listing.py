"""``list`` logic on small synthetic databases (M6: T-M6-01..08; spec 4.3, FR-02.*, AC-02.*)."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from actiontrack.clock import FixedClock
from actiontrack.config import load_config
from actiontrack.errors import UsageError
from actiontrack.report.list_report import render_list_json, render_list_table
from actiontrack.resolution.people import PeopleRegistry, PersonAmbiguous, PersonNotFound
from actiontrack.services.listing import (
    OVERDUE_SQL,
    ListQuery,
    days_overdue,
    display_status,
    list_items,
    parse_list_args,
    validate_list_args,
)
from actiontrack.store.db import Database
from actiontrack.store.repo import Repo

CFG = Path(__file__).resolve().parent.parent / "fixtures" / "config" / "valid_full.toml"
TODAY = date(2026, 10, 22)


def ns(**kw) -> argparse.Namespace:
    base = dict(owner=None, status="open", due_before=None, include_ambiguous=False, ambiguous=False, wide=False)
    base.update(kw)
    return argparse.Namespace(**base)


def owner(raw, status="resolved", person_id=None, candidates=()):
    return SimpleNamespace(
        owner_raw=raw, role="owner", person_id=person_id, status=status, candidates=tuple(candidates),
        provenance="roster",
    )


@pytest.fixture
def world(tmp_db_path):
    reg = PeopleRegistry()
    reg.add_roster(load_config(CFG).roster)
    db = Database.open(tmp_db_path, clock=FixedClock(datetime(2026, 10, 22, 9, 0, tzinfo=UTC)))
    repo = Repo(db, db.clock)
    repo.persist_registry(reg)
    sf = repo.insert_source_file("/n", "sub/2026-10-14-sprint.md", status="ok")
    sf2 = repo.insert_source_file("/n", "2026-10-20-x.md", status="ok")

    def add(text, due=None, conf="none", status="open", owners=(), raw=None, sources=((),)):
        i = repo.insert_action_item(
            description=text, task_text=text, description_core_key=text.lower(), status=status,
            due_date=due, due_confidence=conf, due_raw=raw,
            completion_origin="manual" if status == "done" else None,
            completed_at="2026-10-01T00:00:00Z" if status == "done" else None,
        )
        repo.set_item_owners(i, list(owners))
        for n, _ in enumerate(sources):
            repo.insert_item_source(
                item_id=i, source_file_id=sf if n == 0 else sf2, meeting_id=None, line_start=10 + i, line_end=10 + i,
                raw_text=f"- [ ] {text}", checkbox_state="open", block_hash=f"h{i}{n}",
            )
        return i

    ids = SimpleNamespace(
        overdue=add("overdue", "2026-10-21", "explicit", owners=[owner("Priya Nair", person_id="priya-nair")]),
        today=add("due today", "2026-10-22", "explicit", owners=[owner("anita", person_id="anita-desai")]),
        later=add("later", "2026-10-30", "explicit", owners=[owner("Anita Desai", person_id="anita-desai")],
                  sources=((), ())),
        undated=add("undated", owners=[owner(None, "unassigned")]),
        vague=add("vague", None, "unresolved", raw="next week is the plan",
                  owners=[owner("Priya", "ambiguous", None, ["priya-nair", "priya-shah"])]),
        done=add("finished", "2026-10-01", "explicit", status="done",
                 owners=[owner("Priya Shah", person_id="priya-shah")]),
        shah=add("shah thing", "2026-10-21", "explicit", owners=[owner("Priya Shah", person_id="priya-shah")]),
        multi=add("multi", "2026-10-21", "explicit",
                  owners=[owner("Rahul Mehta", person_id="rahul-mehta"), owner("Anita Desai", person_id="anita-desai")]),
    )
    yield SimpleNamespace(repo=repo, reg=repo.load_registry(), ids=ids, db=db)
    db.close()


def run(world, **kw):
    return list_items(world.repo, world.reg, ListQuery(**kw), TODAY)


def listed(result):
    return [r.item_id for r in result.rows]


# ------------------------------------------------------------------ T-M6-01 arguments
def test_validation_rules():
    validate_list_args(ns())
    for bad in (
        ns(status="bogus"),
        ns(due_before="2026-02-30"),
        ns(due_before="friday"),
        ns(ambiguous=True, owner="X"),
        ns(include_ambiguous=True),
        ns(ambiguous=True, include_ambiguous=True),
    ):
        with pytest.raises(UsageError):
            validate_list_args(bad)
    with pytest.raises(UsageError, match="--include-ambiguous requires --owner"):
        validate_list_args(ns(include_ambiguous=True))
    with pytest.raises(UsageError) as e:
        validate_list_args(ns(status="bogus"))
    assert all(w in (e.value.next_step or "") for w in ("open", "done", "overdue", "all"))


def test_parse_due_before_relative_to_today():
    assert parse_list_args(ns(due_before="tomorrow"), TODAY).due_before == date(2026, 10, 23)
    assert parse_list_args(ns(due_before="today"), TODAY).due_before == TODAY
    assert parse_list_args(ns(due_before="2026-11-01"), TODAY).due_before == date(2026, 11, 1)
    assert parse_list_args(ns(ambiguous=True), TODAY).status == "open"
    assert parse_list_args(ns(ambiguous=True, status="all"), TODAY).status == "all"


# ------------------------------------------------------------------ T-M6-02 status / overdue
@pytest.mark.parametrize(
    "status,due,shown,days",
    [
        ("open", date(2026, 10, 21), "overdue", 1),
        ("open", date(2026, 10, 22), "open", None),
        ("open", None, "open", None),
        ("done", date(2026, 10, 1), "done", None),
        ("open", date(2026, 10, 16), "overdue", 6),
    ],
)
def test_display_status_and_days_overdue(status, due, shown, days):
    assert display_status(status, due, TODAY) == shown
    if status == "open":
        assert days_overdue(due, TODAY) == days


def test_status_filters(world):
    i = world.ids
    assert set(listed(run(world, status="open"))) == {i.overdue, i.today, i.later, i.undated, i.vague, i.shah, i.multi}
    assert set(listed(run(world, status="done"))) == {i.done}
    assert set(listed(run(world, status="overdue"))) == {i.overdue, i.shah, i.multi}
    assert len(listed(run(world, status="all"))) == 8


def test_due_before_is_strict_and_excludes_undated(world):
    i = world.ids
    got = set(listed(run(world, status="all", due_before=date(2026, 10, 22))))
    assert got == {i.overdue, i.done, i.shah, i.multi}
    assert i.today not in got and i.undated not in got and i.vague not in got


def test_sql_and_python_overdue_agree(world):
    rows = world.repo.conn.execute(
        f"SELECT id FROM action_item WHERE {OVERDUE_SQL}", {"today": TODAY.isoformat()}
    ).fetchall()
    assert {r[0] for r in rows} == set(listed(run(world, status="overdue")))


# ------------------------------------------------------------------ T-M6-03 owner
def test_owner_filter_resolves_name_id_and_email(world):
    i = world.ids
    for q in ("Priya Nair", "priya-nair", "priya nair", "angad.kulkarni@coditas.com"):
        assert listed(run(world, owner=q, status="all")) == [i.overdue], q
    assert set(listed(run(world, owner="Anita Desai", status="all"))) == {i.multi, i.today, i.later}


def test_owner_bare_shared_first_name_is_ambiguous_and_unknown_is_not_found(world):
    with pytest.raises(PersonAmbiguous) as e:
        run(world, owner="Priya")
    assert len(e.value.candidate_lines) == 2
    with pytest.raises(PersonNotFound):
        run(world, owner="Nobody Atall")


def test_include_ambiguous_adds_only_sets_containing_the_person(world):
    i = world.ids
    plain = set(listed(run(world, owner="Priya Nair")))
    assert i.vague not in plain
    inc = run(world, owner="Priya Nair", include_ambiguous=True)
    assert set(listed(inc)) == plain | {i.vague}
    row = {r.item_id: r for r in inc.rows}[i.vague]
    assert "OA" in row.flags
    shah = set(listed(run(world, owner="Priya Shah", include_ambiguous=True)))
    assert i.vague in shah and i.overdue not in shah


def test_multi_owner_item_appears_once_per_owner_query(world):
    i = world.ids
    assert i.multi in listed(run(world, owner="Rahul Mehta"))
    assert listed(run(world, owner="Rahul Mehta")).count(i.multi) == 1


# ------------------------------------------------------------------ T-M6-04 sort
def test_default_sort_due_then_owner_then_id(world):
    i = world.ids
    order = listed(run(world, status="all"))
    # due 10-01 (done), 10-21: Priya Nair < Priya Shah < Rahul Mehta (first owner), 10-22, 10-30,
    # then undated: ambiguous before unassigned
    assert order == [i.done, i.overdue, i.shah, i.multi, i.today, i.later, i.vague, i.undated]


def test_sort_is_stable_for_any_insertion_order(world):
    first = listed(run(world, status="all"))
    for _ in range(3):
        assert listed(run(world, status="all")) == first


# ------------------------------------------------------------------ T-M6-05 ambiguous
def test_ambiguous_groups_and_status_default(world):
    i = world.ids
    res = run(world, ambiguous=True)
    assert [g.heading for g in res.groups] == ["Priya Nair | Priya Shah"]
    assert listed(res) == [i.vague]
    assert listed(run(world, ambiguous=True, status="done")) == []
    world.repo.touch_item(i.vague, {"status": "done"})
    assert listed(run(world, ambiguous=True)) == []
    assert listed(run(world, ambiguous=True, status="all")) == [i.vague]


# ------------------------------------------------------------------ T-M6-06 row view-model
def test_row_fields_flags_and_source(world):
    i = world.ids
    rows = {r.item_id: r for r in run(world, status="all").rows}
    r = rows[i.overdue]
    assert (r.display_status, r.due_display, r.owner_display, r.days_overdue) == (
        "overdue", "2026-10-21", "Priya Nair", 1,
    )
    assert r.source_display == f"sub/2026-10-14-sprint.md:{10 + i.overdue}" and r.flags == ()
    assert rows[i.later].source_display.endswith(" (+1)") and rows[i.later].flags == ("CR",)
    v = rows[i.vague]
    assert v.due_display.startswith("?(next week i") and v.due_display.endswith("…)")
    assert v.owner_display == "?Priya (Nair | Shah)" and v.flags == ("OA", "DU")
    u = rows[i.undated]
    assert (u.due_display, u.owner_display, u.flags, u.source_display) == ("-", "-", ("OU", "DU"), f"sub/2026-10-14-sprint.md:{10 + i.undated}")
    assert rows[i.today].owner_display == "Anita Desai" and rows[i.today].days_overdue is None
    assert rows[i.multi].owner_display == "Rahul Mehta, Anita Desai"


def test_link_flags_d_and_ci(world):
    i = world.ids
    world.repo.insert_link(i.overdue, i.shah, "possible_duplicate", "due_differs")
    world.repo.insert_link(i.done, i.later, "completed_by")
    rows = {r.item_id: r for r in run(world, status="all").rows}
    assert "D?" in rows[i.overdue].flags and "D?" in rows[i.shah].flags
    assert "CI" in rows[i.done].flags and "CI" not in rows[i.later].flags


def test_sm_flag(world):
    i = world.ids
    world.repo.touch_item(i.today, {"source_state": "missing"})
    rows = {r.item_id: r for r in run(world).rows}
    assert "SM" in rows[i.today].flags


# ------------------------------------------------------------------ T-M6-07/08 renderers
def test_table_header_columns_and_empty(world):
    res = run(world, status="all")
    text = render_list_table(res, "Asia/Kolkata")
    lines = text.split("\n")
    assert lines[0] == "As of 2026-10-22 (tz Asia/Kolkata) — 8 items"
    assert lines[1].split() == ["ID", "STATUS", "DUE", "OWNER", "TASK", "SOURCE", "FLAGS"]
    assert all(len(l) <= 120 for l in lines[1:]) and "\r" not in text
    empty = render_list_table(run(world, owner="Rahul Mehta", status="done"), "UTC")
    assert empty == "As of 2026-10-22 (tz UTC) — 0 items\nNo action items match."


def test_table_truncates_long_task_but_wide_does_not(world):
    long_task = "x" * 200
    world.repo.touch_item(world.ids.today, {"task_text": long_task})
    res = run(world, status="all")
    assert long_task not in render_list_table(res, "UTC")
    assert long_task in render_list_table(res, "UTC", wide=True)


def test_table_strips_control_characters(world):
    world.repo.touch_item(world.ids.today, {"task_text": "\x1b[31mred"})
    assert "\x1b" not in render_list_table(run(world, status="all"), "UTC")


def test_json_envelope_days_overdue_only_when_overdue(world):
    i = world.ids
    data = json.loads(render_list_json(run(world, status="all"), "Asia/Kolkata"))
    assert list(data) == ["as_of", "timezone", "items"] and data["as_of"] == "2026-10-22"
    by_id = {x["id"]: x for x in data["items"]}
    assert by_id[i.overdue]["days_overdue"] == 1 and by_id[i.overdue]["display_status"] == "overdue"
    assert "days_overdue" not in by_id[i.today] and "days_overdue" not in by_id[i.done]
    assert len(by_id[i.later]["sources"]) == 2
    assert by_id[i.vague]["owners"][0]["candidates"] == [
        {"person_id": "priya-nair", "full_name": "Priya Nair"},
        {"person_id": "priya-shah", "full_name": "Priya Shah"},
    ]
    empty = json.loads(render_list_json(run(world, owner="Rahul Mehta", status="done"), "UTC"))
    assert empty["items"] == []
    assert render_list_json(run(world, status="all"), "X") == render_list_json(run(world, status="all"), "X")


def test_module_never_reads_the_system_clock():
    src = (Path(__file__).resolve().parents[2] / "src" / "actiontrack" / "services" / "listing.py").read_text(
        encoding="utf-8"
    )
    assert "date.today" not in src and "datetime.now" not in src
