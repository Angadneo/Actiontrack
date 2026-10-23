"""T-M4-03 / T-M4-04: owner-phrase extraction (spec §10.1, BR-19, BR-20, AC-05.4/5/9, SC-05, SC-51, G-23)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from actiontrack.domain.models import OwnerRole, TextSpan
from actiontrack.parsing.items import AnalyzerContext, derive_task_text, due_spans, extract_items
from actiontrack.parsing.markdown import scan_document
from actiontrack.parsing.owner_phrase import (
    OwnerAnalyzer,
    OwnerPhraseProvider,
    extract_owner_phrase,
    phrase_spans,
)
from actiontrack.parsing.reader import split_lines

NOTES = Path(__file__).resolve().parents[1] / "fixtures" / "notes"

# Hand-checked expectation table for every checkbox item of the sample (file:line -> owners).
# `Name_Parts` = mention raw (underscore = space); `(with)` = role with; `+` joins mentions;
# `!Word` = non-person owner (raw kept, no mentions); `-` = no owner clause.
SAMPLE_TABLE = """
2026-09-01-quote-portal-kickoff.md:9 Priya
2026-09-01-quote-portal-kickoff.md:10 Rahul
2026-09-01-quote-portal-kickoff.md:11 Anita
2026-09-01-quote-portal-kickoff.md:12 Sam
2026-09-02-standup.md:9 Rahul
2026-09-02-standup.md:10 Sam
2026-09-03-billing-migration-sync.md:9 Karan
2026-09-03-billing-migration-sync.md:10 Rahul
2026-09-03-billing-migration-sync.md:11 Priya
2026-09-03-billing-migration-sync.md:12 Sam
2026-09-08-quote-portal-weekly.md:9 Priya_N.
2026-09-08-quote-portal-weekly.md:10 Rahul
2026-09-08-quote-portal-weekly.md:11 Meera
2026-09-08-quote-portal-weekly.md:12 Rahul
2026-09-09-qa-standup.md:9 Karan
2026-09-09-qa-standup.md:10 Priya
2026-09-10-acme-renewal-prep.md:9 Lena
2026-09-10-acme-renewal-prep.md:10 Karan
2026-09-10-acme-renewal-prep.md:11 Meera
2026-09-10-acme-renewal-prep.md:12 !Someone
2026-09-14-onboarding-flow-design.md:9 Anita
2026-09-14-onboarding-flow-design.md:10 Priya
2026-09-14-onboarding-flow-design.md:11 Rahul
2026-09-14-onboarding-flow-design.md:12 Lena
2026-09-15-quote-portal-weekly.md:9 Priya
2026-09-15-quote-portal-weekly.md:10 Anita
2026-09-15-quote-portal-weekly.md:11 Sam
2026-09-15-quote-portal-weekly.md:12 Rahul
2026-09-16-standup.md:11 Lena
2026-09-16-standup.md:12 Meera
2026-09-17-billing-migration-sync.md:9 Priya
2026-09-17-billing-migration-sync.md:10 Sam
2026-09-17-billing-migration-sync.md:11 Karan
2026-09-17-billing-migration-sync.md:12 Rahul
2026-09-18-design-review.md:11 anita
2026-09-18-design-review.md:12 Anita+Rahul
2026-09-18-design-review.md:13 Lena+Priya(with)
2026-09-18-design-review.md:14 !Everyone
2026-09-18-design-review.md:15 !TBD
2026-09-19-offsite-draft.txt:2 -
2026-09-21-incident-review.md:14 Sam
2026-09-21-incident-review.md:15 Rahul
2026-09-21-incident-review.md:16 Priya
2026-09-21-incident-review.md:17 Karan
2026-09-22-quote-portal-weekly.md:9 Anita
2026-09-22-quote-portal-weekly.md:10 Sam
2026-09-22-quote-portal-weekly.md:11 Meera
2026-09-22-quote-portal-weekly.md:12 Rahul
2026-09-23-standup.md:8 Rahul
2026-09-23-standup.md:9 Sam
2026-09-24-onboarding-flow-design.md:9 Anita
2026-09-24-onboarding-flow-design.md:10 Priya
2026-09-24-onboarding-flow-design.md:11 Lena
2026-09-24-onboarding-flow-design.md:12 Rahul
2026-09-25-qa-sync.md:9 Priya
2026-09-25-qa-sync.md:10 Rahul
2026-09-28-acme-renewal-checkin.md:9 Meera
2026-09-28-acme-renewal-checkin.md:10 Karan
2026-09-28-acme-renewal-checkin.md:11 Lena
2026-09-28-acme-renewal-checkin.md:12 Priya
2026-09-29-billing-cutover-planning.md:9 Priya_S.
2026-09-29-billing-cutover-planning.md:10 Priya_N.
2026-09-29-billing-cutover-planning.md:11 Sam
2026-09-29-billing-cutover-planning.md:12 Karan
2026-09-30-q4-offsite.md:9 Meera
2026-09-30-q4-offsite.md:10 Karan
2026-09-30-q4-offsite.md:11 Lena
2026-09-30-q4-offsite.md:12 Sam
2026-09-30-q4-offsite.md:16 Priya
2026-09-30-q4-offsite.md:17 Rahul
2026-09-30-q4-offsite.md:18 !Everyone
2026-09-30-standup.md:11 Karan
2026-09-30-standup.md:12 Priya
2026-10-01-quote-portal-weekly.md:9 Meera
2026-10-01-quote-portal-weekly.md:10 Rahul
2026-10-01-quote-portal-weekly.md:11 Anita
2026-10-01-quote-portal-weekly.md:12 !Team
2026-10-02-finance-sync.md:9 Karan
2026-10-02-finance-sync.md:10 Sam
2026-10-02-finance-sync.md:11 Meera
2026-10-02-finance-sync.md:12 Karan
2026-10-03-sales-pipeline.md:6 Meera
2026-10-03-sales-pipeline.md:7 Lena
2026-10-05-onboarding-retro.md:9 Rahul
2026-10-05-onboarding-retro.md:10 Lena
2026-10-05-onboarding-retro.md:11 Anita
2026-10-05-onboarding-retro.md:12 Priya
2026-10-06-quote-portal-weekly.md:9 Rahul
2026-10-06-quote-portal-weekly.md:10 Meera
2026-10-06-quote-portal-weekly.md:11 Sam
2026-10-06-quote-portal-weekly.md:12 Priya
2026-10-07-standup.md:9 Rahul
2026-10-07-standup.md:10 Anita
2026-10-08-billing-go-no-go.md:9 Priya
2026-10-08-billing-go-no-go.md:10 Sam
2026-10-08-billing-go-no-go.md:11 Karan
2026-10-08-billing-go-no-go.md:12 Priya_N.
2026-10-09-security-review.md:18 Sam
2026-10-09-security-review.md:19 Rahul
2026-10-09-security-review.md:20 Priya
2026-10-09-security-review.md:21 Karan
2026-10-10-sales-weekly.md:9 Meera
2026-10-10-sales-weekly.md:10 Lena
2026-10-10-sales-weekly.md:11 Lena
2026-10-12-billing-post-cutover.md:9 Sam
2026-10-12-billing-post-cutover.md:10 Priya
2026-10-12-billing-post-cutover.md:11 Karan
2026-10-12-billing-post-cutover.md:12 Rahul
2026-10-13-quote-portal-weekly.md:9 Rahul
2026-10-13-quote-portal-weekly.md:10 Anita
2026-10-13-quote-portal-weekly.md:11 Priya
2026-10-13-quote-portal-weekly.md:12 Meera
2026-10-14-acme-renewal-wrapup.md:9 Karan
2026-10-14-acme-renewal-wrapup.md:10 Lena
2026-10-14-acme-renewal-wrapup.md:11 Meera
2026-10-14-acme-renewal-wrapup.md:12 Priya
untitled.md:1 Sam
untitled.md:2 Priya
"""


def _expected() -> dict[str, str]:
    return dict(line.split() for line in SAMPLE_TABLE.strip().splitlines())


def _sample_items() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in sorted(NOTES.glob("*.*")):
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue  # scratch.md is binary
        scan = scan_document(split_lines(text), path.name)
        for item in extract_items(scan, path.name).items:
            out.append((f"{path.name}:{item.line_start}", item.description))
    return out


def _render(description: str, known=None) -> str:
    phrase = extract_owner_phrase(description, known)
    if phrase is None:
        return "-"
    if phrase.nonperson_raw is not None:
        return "!" + phrase.nonperson_raw
    return "+".join(
        m.raw.replace(" ", "_") + ("(with)" if m.role is OwnerRole.WITH else "") for m in phrase.mentions
    )


def _raws(description: str, known=None) -> list[str]:
    phrase = extract_owner_phrase(description, known)
    assert phrase is not None
    return [m.raw for m in phrase.mentions]


def _is_known(raw: str) -> bool:
    return raw.casefold() in {"priya", "anita", "rahul", "李雷"}


# ---------------------------------------------------------------- sample acceptance
def test_sample_table_covers_every_item() -> None:
    items = _sample_items()
    assert len(items) == 118  # 117 .md items + the single .txt item
    assert {k for k, _ in items} == set(_expected())


def test_sample_owner_raws_match_hand_checked_table() -> None:
    expected = _expected()
    bad = {k: (expected[k], _render(d)) for k, d in _sample_items() if _render(d) != expected[k]}
    assert bad == {}


def test_sample_summary_counts() -> None:
    values = list(_expected().values())
    assert sum(v == "-" for v in values) == 1
    assert sum(v.startswith("!") for v in values) == 5
    assert sum("(with)" in v for v in values) == 1
    assert sum("+" in v and "(with)" not in v for v in values) == 1
    assert sum(not v.startswith("!") and v != "-" for v in values) == 112


def test_sample_clause_deleted_from_task_text() -> None:
    for key, desc in _sample_items():
        phrase = extract_owner_phrase(desc)
        if phrase is None:
            continue
        task = derive_task_text(desc, [*phrase_spans(phrase), *due_spans(desc)])
        clause = desc[phrase.clause_span.start : phrase.clause_span.end]
        assert clause.strip() not in task, key
        assert task[:1].isupper(), key
        assert not task.startswith(desc.split()[0] + " "), key


def test_sample_spans_slice_exactly() -> None:
    for key, desc in _sample_items():
        phrase = extract_owner_phrase(desc)
        if phrase is None:
            continue
        for m in phrase.mentions:
            assert desc[m.span.start : m.span.end] == m.raw, key
        c = phrase.clause_span
        assert 0 <= c.start < c.end <= len(desc), key


def test_no_owner_items() -> None:
    assert extract_owner_phrase("Book the venue") is None
    assert extract_owner_phrase("Send the quote by Friday") is None  # SC-05


# ---------------------------------------------------------------- T-M4-03: single owner
@pytest.mark.parametrize(
    ("desc", "owner", "clause"),
    [
        ("Priya to send the revised quote to Acme by Friday", "Priya", "Priya to"),
        ("Priya N. to send revised quote to Acme — by Friday", "Priya N.", "Priya N. to"),
        ("Meera to chase Orbit Labs by Friday", "Meera", "Meera to"),
        ("Sam to cut cloud costs by 10% — proposal by 2026-10-09", "Sam", "Sam to"),
        ("Lena to pull Acme's usage report for the last 6 months by tomorrow", "Lena", "Lena to"),
        ("Karan will send the invoices", "Karan", "Karan will"),
        ("Priya should review the doc", "Priya", "Priya should"),
        ("Rahul needs to fix the build", "Rahul", "Rahul needs to"),
        ("Sam is going to call legal", "Sam", "Sam is going to"),
        ("Lena can pick up the survey", "Lena", "Lena can"),
        ("Meera must sign the form", "Meera", "Meera must"),
        ("Meera MUST sign the form", "Meera", "Meera MUST"),
        ("Zoë Åström to review the contract", "Zoë Åström", "Zoë Åström to"),
        ("Dr. Rao to approve the protocol", "Dr. Rao", "Dr. Rao to"),
        ("Anne-Marie O'Neil to book the room", "Anne-Marie O'Neil", "Anne-Marie O'Neil to"),
    ],
)
def test_single_owner(desc: str, owner: str, clause: str) -> None:
    phrase = extract_owner_phrase(desc)
    assert phrase is not None and phrase.rule_id == 2
    assert [m.raw for m in phrase.mentions] == [owner]
    assert phrase.mentions[0].role is OwnerRole.OWNER
    assert desc[phrase.clause_span.start : phrase.clause_span.end] == clause


@pytest.mark.parametrize(
    ("desc", "word"),
    [
        ("Everyone to review the prototype before the next review", "Everyone"),
        ("TBD to write the design-system changelog", "TBD"),
        ("Someone to update the CRM notes", "Someone"),
        ("Team to decide on the launch date", "Team"),
        ("Everyone to submit their Q4 goals by 7 Oct", "Everyone"),
        ("Everybody to read the doc", "Everybody"),
        ("Anyone can pick this up", "Anyone"),
        ("Nobody to touch prod", "Nobody"),
        ("We will revisit the pricing", "We"),
        ("I will draft the summary", "I"),
        ("Me to tidy up the board", "Me"),
        ("All to confirm attendance", "All"),
        ("we will revisit the pricing", "we"),
    ],
)
def test_non_person_words(desc: str, word: str) -> None:  # AC-05.5, BR-20
    phrase = extract_owner_phrase(desc)
    assert phrase is not None
    assert phrase.nonperson_raw == word
    assert phrase.mentions == ()


def test_rule1_known_gate() -> None:  # G-23
    assert extract_owner_phrase("Update: rollout moved") is None
    assert extract_owner_phrase("Blocked - waiting on legal") is None
    assert extract_owner_phrase("Note: remember to book") is None
    assert extract_owner_phrase("Priya: send the deck") is None  # not known
    phrase = extract_owner_phrase("Priya: send the deck", _is_known)
    assert phrase is not None and phrase.rule_id == 1
    assert _raws("Priya: send the deck", _is_known) == ["Priya"]
    assert phrase.clause_span == TextSpan(0, len("Priya: "))
    for sep in ("-", "–", "—"):
        d = f"Priya {sep} send the deck"
        p = extract_owner_phrase(d, _is_known)
        assert p is not None and p.rule_id == 1 and [m.raw for m in p.mentions] == ["Priya"]
        assert derive_task_text(d, phrase_spans(p)) == "Send the deck"
    # `Note:` never becomes a person; rule 2 locates Priya
    for known in (None, _is_known):
        note = extract_owner_phrase("Note: Priya will send the deck", known)
        assert note is not None and note.rule_id == 2 and [m.raw for m in note.mentions] == ["Priya"]


def test_rule1_at_name_any_case() -> None:
    for desc, raw in [
        ("@anita: audit colour contrast on the dashboard by Mon", "anita"),
        ("@Anita: audit colour contrast", "Anita"),
        ("@ANITA: audit", "ANITA"),
        ("@zed: fix the build", "zed"),  # unknown names are fine behind '@'
    ]:
        p = extract_owner_phrase(desc)
        assert p is not None and p.rule_id == 1
        assert [m.raw for m in p.mentions] == [raw]
        assert desc[p.mentions[0].span.start : p.mentions[0].span.end] == raw
        assert desc[p.clause_span.start : p.clause_span.end].startswith("@")
    d = "@anita: audit colour contrast on the dashboard by Mon"
    p = extract_owner_phrase(d)
    assert derive_task_text(d, [*phrase_spans(p), *due_spans(d)]) == "Audit colour contrast on the dashboard"


def test_rule1_list_before_colon() -> None:
    p = extract_owner_phrase("@anita & Rahul: pair on the API")
    assert p is not None and p.rule_id == 1 and [m.raw for m in p.mentions] == ["anita", "Rahul"]
    p2 = extract_owner_phrase("@anita and @rahul: pair on the API")
    assert p2 is not None and [m.raw for m in p2.mentions] == ["anita", "rahul"]


def test_everyone_colon_is_not_an_owner() -> None:  # G-C26 corollary
    assert extract_owner_phrase("Everyone: please review") is None
    assert extract_owner_phrase("Team - please review") is None
    p = extract_owner_phrase("@everyone: please review")
    assert p is not None and p.nonperson_raw == "everyone"


def test_caseless_script_needs_known() -> None:
    d = "李雷 to book the room"
    assert extract_owner_phrase(d) is None
    p = extract_owner_phrase(d, lambda raw: raw == "李雷")
    assert p is not None and [m.raw for m in p.mentions] == ["李雷"]
    assert derive_task_text(d, phrase_spans(p)) == "Book the room"


def test_known_lowercase_name() -> None:
    p = extract_owner_phrase("priya to send it", lambda raw: raw.casefold() == "priya")
    assert p is not None and [m.raw for m in p.mentions] == ["priya"]
    assert extract_owner_phrase("priya to send it") is None


# ---------------------------------------------------------------- false-owner guards
@pytest.mark.parametrize(
    "desc",
    [
        "Upgrade to Python 3.12 by Friday",
        "Move to the new cluster",
        "Migrate to AWS",
        "Proposal will be sent by Priya",
        "Deck must be reviewed",
        "Ask Acme to confirm the dates",
        "Send the quote to Acme by Friday",
        "Friday to be decided",
        "Monday will be the cutover",
        "by Friday to ship",
        "It will ship soon",
    ],
)
def test_guards_no_owner(desc: str) -> None:
    assert extract_owner_phrase(desc) is None


def test_leading_filler_is_skipped() -> None:
    assert _raws("Also Priya to send the deck") == ["Priya"]


def test_name_has_at_most_four_tokens() -> None:
    p = extract_owner_phrase("Aa Bb Cc Dd Ee to do it")
    assert p is None  # five capitalised tokens are not a name


# ---------------------------------------------------------------- T-M4-04: multi-owner, with, possessive
def test_multi_owner_ampersand() -> None:  # SC-51, design-review:12
    d = "Anita & Rahul to agree on the component API by Wednesday"
    p = extract_owner_phrase(d)
    assert p is not None and p.rule_id == 2
    assert [m.raw for m in p.mentions] == ["Anita", "Rahul"]
    assert all(m.role is OwnerRole.OWNER for m in p.mentions)
    assert d[p.clause_span.start : p.clause_span.end] == "Anita & Rahul to"
    assert [d[m.span.start : m.span.end] for m in p.mentions] == ["Anita", "Rahul"]


@pytest.mark.parametrize(
    ("desc", "owners"),
    [
        ("Priya and Rahul to ship", ["Priya", "Rahul"]),  # AC-05.4
        ("Anita, Rahul and Sam to review", ["Anita", "Rahul", "Sam"]),
        ("Anita, Rahul, and Sam to review", ["Anita", "Rahul", "Sam"]),
        ("Anita/Rahul to review", ["Anita", "Rahul"]),
        ("Priya N. & Priya S. to compare", ["Priya N.", "Priya S."]),
        ("Priya Nair and Rahul Mehta will pair", ["Priya Nair", "Rahul Mehta"]),
    ],
)
def test_multi_owner_lists(desc: str, owners: list[str]) -> None:
    assert _raws(desc) == owners


def test_rahul_not_matched_separately() -> None:
    p = extract_owner_phrase("Anita & Rahul to agree")
    assert p is not None and p.clause_span.start == 0 and len(p.mentions) == 2


def test_multi_owner_cap() -> None:
    nine = ", ".join(["Aa", "Bb", "Cc", "Dd", "Ee", "Ff", "Gg", "Hh", "Ii"])
    assert extract_owner_phrase(f"{nine} to ship") is None
    eight = ", ".join(["Aa", "Bb", "Cc", "Dd", "Ee", "Ff", "Gg", "Hh"])
    p = extract_owner_phrase(f"{eight} to ship")
    assert p is not None and len(p.mentions) == 8


def test_mixed_person_and_nonperson_is_unassigned() -> None:
    p = extract_owner_phrase("Anita and Team to review")
    assert p is not None and p.mentions == () and p.nonperson_raw == "Anita and Team"


def test_with_co_owner() -> None:  # AC-05.9, SC-51
    d = "Lena (with Priya) to collect user feedback on the prototype by 2026-09-25"
    p = extract_owner_phrase(d)
    assert p is not None
    assert [(m.raw, m.role) for m in p.mentions] == [("Lena", OwnerRole.OWNER), ("Priya", OwnerRole.WITH)]
    assert p.with_spans == (TextSpan(5, 17),)
    assert d[5:17] == "(with Priya)"
    assert d[p.clause_span.start : p.clause_span.end] == "Lena (with Priya) to"
    assert derive_task_text(d, [*phrase_spans(p), *due_spans(d)]) == "Collect user feedback on the prototype"


def test_with_removed_alone_leaves_no_co_owner_name() -> None:
    d = "Lena (with Priya) to update the plan"
    p = extract_owner_phrase(d)
    assert p is not None
    assert derive_task_text(d, p.with_spans) == "Lena to update the plan"
    assert derive_task_text(d, phrase_spans(p)) == "Update the plan"  # AC-01.19


def test_with_list_and_nonperson() -> None:
    p = extract_owner_phrase("Lena (with Priya and Sam) to update the plan")
    assert p is not None
    assert [(m.raw, m.role.value) for m in p.mentions] == [("Lena", "owner"), ("Priya", "with"), ("Sam", "with")]
    q = extract_owner_phrase("Lena (with Team) to update the plan")
    assert q is not None and [m.raw for m in q.mentions] == ["Lena"] and len(q.with_spans) == 1


def test_possessive_is_never_owner() -> None:
    assert _raws("Rahul to review Anita's PR by tomorrow") == ["Rahul"]
    assert _raws("Rahul to review Anita’s PR") == ["Rahul"]
    assert extract_owner_phrase("Anita's PR needs to be reviewed") is None
    assert extract_owner_phrase("Review Anita's PR") is None
    assert _raws("Review James' PR (Rahul)") == ["Rahul"]


# ---------------------------------------------------------------- rule 3 (trailing)
@pytest.mark.parametrize(
    ("desc", "owner", "clause"),
    [
        ("Draft onboarding doc (Priya)", "Priya", "(Priya)"),
        ("Draft onboarding doc (Priya Nair).", "Priya Nair", "(Priya Nair)"),
        ("Draft doc — Priya Nair", "Priya Nair", "— Priya Nair"),
        ("Draft doc – Priya Nair", "Priya Nair", "– Priya Nair"),
        ("Draft doc - Priya Nair", "Priya Nair", "- Priya Nair"),
        ("Draft doc [owner: Priya Nair]", "Priya Nair", "[owner: Priya Nair]"),
        ("Draft doc Owner: Priya", "Priya", "Owner: Priya"),
        ("Draft doc Assignee: Priya", "Priya", "Assignee: Priya"),
        ("Draft doc DRI: Rahul", "Rahul", "DRI: Rahul"),
        ("Draft doc (owner: Rahul)", "Rahul", "(owner: Rahul)"),
        ("Draft doc Owner: Priya by Friday", "Priya", "Owner: Priya"),
    ],
)
def test_trailing_forms(desc: str, owner: str, clause: str) -> None:
    p = extract_owner_phrase(desc)
    assert p is not None and p.rule_id == 3
    assert [m.raw for m in p.mentions] == [owner]
    assert desc[p.clause_span.start : p.clause_span.end] == clause
    task = derive_task_text(desc, phrase_spans(p))
    assert task.startswith("Draft ")
    assert owner.split()[0] not in task


def test_trailing_list_and_nonperson() -> None:
    assert _raws("Draft doc (Priya and Rahul)") == ["Priya", "Rahul"]
    p = extract_owner_phrase("Draft doc (TBD)")
    assert p is not None and p.nonperson_raw == "TBD"
    q = extract_owner_phrase("Draft doc — Everyone")
    assert q is not None and q.nonperson_raw == "Everyone"


@pytest.mark.parametrize(
    "desc",
    [
        "Draft doc — Friday",
        "Draft doc (Friday)",
        "Draft doc (Sept)",
        "Draft doc (Oct 3rd)",
        "Draft doc — by Friday",
        "Send the quote to Acme — by Friday",
        "Draft doc (ASAP)",
        "Draft doc (see notes)",
        "Draft doc (phase 2)",
        "Draft doc (they)",
        "Draft doc — he",
        "Draft doc Owner: friday",
    ],
)
def test_trailing_not_owner(desc: str) -> None:
    assert extract_owner_phrase(desc) is None


def test_trailing_acronym_ok_when_known() -> None:
    p = extract_owner_phrase("Draft doc (ASAP)", lambda raw: raw == "ASAP")
    assert p is not None and p.rule_id == 3


def test_precedence_leading_beats_trailing() -> None:
    p = extract_owner_phrase("Priya to review Anita's PR (Rahul)")
    assert p is not None and p.rule_id == 2 and [m.raw for m in p.mentions] == ["Priya"]
    assert _raws("Priya to review the doc — Rahul") == ["Priya"]


def test_due_phrase_after_dash_not_owner() -> None:
    assert extract_owner_phrase("Draft doc — due Friday") is None
    assert _raws("Priya to draft doc — by Friday") == ["Priya"]


# ---------------------------------------------------------------- provider / analyzer
def test_provider_returns_clause_and_with_spans() -> None:
    d = "Lena (with Priya) to update the plan"
    spans = OwnerPhraseProvider().owner_spans(d)
    assert TextSpan(0, len("Lena (with Priya) to")) in spans
    assert TextSpan(5, 17) in spans
    assert tuple(OwnerPhraseProvider().owner_spans("Send the quote")) == ()


def test_analyzer_fills_owner_and_spans() -> None:
    d = "Priya to send the revised quote by Friday"
    analysis = OwnerAnalyzer().analyze(d, AnalyzerContext(None))
    assert analysis.owner is not None and analysis.owner.mentions[0].raw == "Priya"
    assert analysis.owner_spans and analysis.due is not None
    assert derive_task_text(d, [*analysis.owner_spans, *analysis.due_spans]) == "Send the revised quote"
    none = OwnerAnalyzer().analyze("Send the quote", AnalyzerContext(None))
    assert none.owner is None and tuple(none.owner_spans) == ()


def test_analyzer_known_predicate_threaded() -> None:
    res = OwnerAnalyzer(lambda raw: raw == "Priya").analyze("Priya: send the deck", AnalyzerContext(None))
    assert res.owner is not None and res.owner.rule_id == 1


def test_empty_and_odd_inputs() -> None:
    assert extract_owner_phrase("") is None
    assert extract_owner_phrase("   ") is None
    assert extract_owner_phrase("@") is None
    assert extract_owner_phrase("Priya to") is None
    assert extract_owner_phrase("(") is None
    assert extract_owner_phrase("A" * 50_000 + " to") is None


def test_pathological_long_line_is_fast() -> None:
    d = " ".join(["Aa"] * 5000) + " to do"
    t0 = time.perf_counter()
    extract_owner_phrase(d)
    assert time.perf_counter() - t0 < 5
