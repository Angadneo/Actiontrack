"""T-M2-13: meeting title and attendees."""

from __future__ import annotations

import re

import pytest

from actiontrack import codes
from actiontrack.parsing.markdown import scan_document
from actiontrack.parsing.meeting import extract_attendees, extract_title, parse_meeting, split_names
from actiontrack.parsing.reader import DecodedText, classify_and_decode, split_lines
from tests.conftest import FIXTURES_NOTES


def scan(text: str, name: str = "note.md"):
    return scan_document(split_lines(text), name)


def att(text: str) -> list[str]:
    s = scan(text)
    return [a.raw_name for a in extract_attendees(s.annotation.lines, s.front_matter)]


def title(text: str, name: str = "note.md") -> str:
    s = scan(text)
    return extract_title(s.annotation.lines, s.front_matter, name)


# --------------------------------------------------------------------------- titles
def test_title_precedence():
    assert title("---\ntitle: FM title\n---\n# Meeting: Body\n") == "FM title"
    assert title("# Other\n# Meeting: Body\n") == "Body"
    assert title("# Plain H1\n## Sub\n") == "Plain H1"
    assert title("no heading\n", "dir/2026-01-01-weekly-sync.md") == "2026-01-01-weekly-sync"
    assert title("## only h2\n", "a.txt") == "a"


def test_title_keeps_colon_and_strips_markup():
    assert title("# Meeting: Incident review: billing double-charge\n") == "Incident review: billing double-charge"
    assert title("# meeting:   **Bold** `code`\n") == "Bold code"
    assert title("# Meeting: Design review (brain dump)\n") == "Design review (brain dump)"


def test_title_ignores_fence_and_empty_meeting():
    assert title("```\n# Meeting: no\n```\n# Real\n") == "Real"
    assert title("# Meeting:\n# Fallback\n") == "Fallback"


def _decoded(name: str) -> str:
    dec = classify_and_decode((FIXTURES_NOTES / name).read_bytes(), name)
    assert isinstance(dec, DecodedText)
    return dec.text


@pytest.mark.parametrize(
    "name,expected",
    [
        ("2026-09-01-quote-portal-kickoff.md", "Quote portal kickoff"),
        ("2026-09-21-incident-review.md", "Incident review: billing double-charge"),
        ("2026-10-02-finance-sync.md", "Finance sync"),
        ("2026-10-05-onboarding-retro.md", "Onboarding flow checkpoint"),
        ("2026-09-16-standup.md", "Team standup"),
        ("2026-09-30-standup.md", "Team standup"),
        ("untitled.md", "untitled"),
    ],
)
def test_sample_titles(name, expected):
    assert parse_meeting(scan(_decoded(name), name), name).meta.title == expected


# --------------------------------------------------------------------------- attendees: samples
def test_sample_offsite_nine_in_order():
    name = "2026-09-30-q4-offsite.md"
    names = [a.raw_name for a in parse_meeting(scan(_decoded(name), name), name).meta.attendees]
    assert names == [
        "Priya Nair", "Rahul Mehta", "Anita Desai", "Sam Okafor", "Meera Iyer",
        "Karan Joshi", "Lena Fischer", "Priya Shah", "Rahul Verma",
    ]


def test_sample_qa_sync_and_front_matter_lists():
    n = "2026-09-25-qa-sync.md"
    got = [a.raw_name for a in parse_meeting(scan(_decoded(n), n), n).meta.attendees]
    assert got == ["Priya Shah", "Rahul Verma"]
    for n, first in (("2026-09-16-standup.md", "Anita Desai"), ("2026-09-30-standup.md", "Priya Nair")):
        three = parse_meeting(scan(_decoded(n), n), n).meta.attendees
        assert len(three) == 3 and three[0].raw_name == first


@pytest.mark.ac("AC-01.9", "AC-01.15")
def test_sample_golden_all_readable_files():
    missing = []
    count = 0
    for p in sorted(FIXTURES_NOTES.glob("*.md")):
        dec = classify_and_decode(p.read_bytes(), p.name)
        if not isinstance(dec, DecodedText):
            continue  # empty or binary (scratch.md): no metadata
        count += 1
        res = parse_meeting(scan(dec.text, p.name), p.name)
        m = re.search(r"^# Meeting: (.+)$", dec.text, re.M)
        fm = re.search(r"^title: (.+)$", dec.text, re.M)
        found = m or fm
        want_title = found.group(1).strip() if found else p.stem
        assert res.meta.title == want_title, p.name
        line = re.search(r"^Attendees: (.+)$", dec.text, re.M)
        fml = re.search(r"^attendees: \[(.+)\]$", dec.text, re.M)
        src = line or fml
        want = [x.strip() for x in src.group(1).split(",")] if src else []
        assert [a.raw_name for a in res.meta.attendees] == want, p.name
        codes_seen = {w.code for w in res.warnings}
        if not want:
            missing.append(p.name)
            assert codes.W_ATTENDEES_MISSING in codes_seen
        else:
            assert codes.W_ATTENDEES_MISSING not in codes_seen
    assert count == 33
    assert sorted(missing) == ["2026-09-23-standup.md", "untitled.md"]


# --------------------------------------------------------------------------- attendees: synthetic
def test_separators():
    assert att("Attendees: A One; B Two\n") == ["A One", "B Two"]
    assert att("Attendees: Priya, Rahul and Anita\n") == ["Priya", "Rahul", "Anita"]
    assert att("Attendees: Priya AND Rahul\n") == ["Priya", "Rahul"]
    assert att("Attendees: Sandra Anderson, Andy\n") == ["Sandra Anderson", "Andy"]


def test_role_and_email():
    s = scan("Attendees: Priya (PM), Rahul <rahul@x.test>\n")
    got = extract_attendees(s.annotation.lines, s.front_matter)
    assert [(a.raw_name, a.email_hint) for a in got] == [("Priya", None), ("Rahul", "rahul@x.test")]
    assert att("Attendees: Priya (PM, lead) and Sam (eng)\n") == ["Priya", "Sam"]


def test_forms_and_headings():
    assert att("**Attendees:** A, B\n") == ["A", "B"]
    assert att("**Attendees**: A, B\n") == ["A", "B"]
    assert att("Participants: A, B\n") == ["A", "B"]
    assert att("Present: A, B\n") == ["A", "B"]
    assert att("## Attendees\n\n- A\n- B (PM)\n* C\n\n## Next\n- D\n") == ["A", "B", "C"]
    assert att("### Participants:\n- A, B\nplain text\n- X\n") == ["A", "B"]
    assert att("Attendees:\n- A\n- B\n") == ["A", "B"]


def test_absent_and_empty():
    assert att("Absent: Karan\nApologies: Lena\n") == []
    assert att("Attendees: A\nAbsent: Karan\n") == ["A"]
    assert att("Attendees:\n\ntext\n") == []
    assert att("## Attendees\ntext\n") == []


def test_dedupe_order_and_unicode():
    assert att("Attendees: A, B, A, B\n") == ["A", "B"]
    assert att("Attendees: Zoë Åström, 李雷\n") == ["Zoë Åström", "李雷"]


def test_front_matter_forms_and_priority():
    assert att("---\nattendees: [A, B]\n---\nAttendees: Z\n") == ["A", "B"]
    assert att("---\nparticipants: A; B and C\n---\n") == ["A", "B", "C"]
    assert att("---\nattendees:\n  - A\n  - B (PM)\n---\n") == ["A", "B"]
    assert att("---\nattendees:\n---\nAttendees: Z\n") == ["Z"]


def test_rejected_front_matter_uses_body_line():
    text = "---\nattendees: &x [A]\n---\nAttendees: Body One, Body Two\n"
    assert scan(text).front_matter is None
    assert att(text) == ["Body One", "Body Two"]


def test_attendee_in_fence_ignored_and_first_wins():
    assert att("```\nAttendees: Z\n```\nAttendees: A\nAttendees: B\n") == ["A"]


def test_incomplete_names_parsed_as_given_no_incomplete_warning():
    res = parse_meeting(scan("Attendees: Priya, Rahul\nDate: 2026-09-01\n"), "x.md")
    assert [a.raw_name for a in res.meta.attendees] == ["Priya", "Rahul"]
    assert res.warnings == ()


def test_split_names_direct():
    assert split_names("") == ()
    assert [a.raw_name for a in split_names("A,,B; ;")] == ["A", "B"]


def test_parse_meeting_warning_order_and_labels():
    res = parse_meeting(scan("text\n"), "dir/x.md", file="dir/x.md")
    assert [w.code for w in res.warnings] == [codes.W_MEETING_DATE_MISSING, codes.W_ATTENDEES_MISSING]
    assert all("dir/x.md" in w.message for w in res.warnings)
    assert res.meta.date is None and res.meta.date_source == "none" and res.meta.title == "x"
