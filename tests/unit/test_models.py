"""T-M2-01: parse-result model."""

import dataclasses
import datetime as dt
import json
import re

import pytest

from actiontrack import codes
from actiontrack.domain.models import (
    PARSE_MESSAGE_TEMPLATES,
    PARSER_EMITTED_CODES,
    Attendee,
    FileFailure,
    MeetingMeta,
    NonCheckboxCandidate,
    ParsedFile,
    ParsedItem,
    ParseWarning,
    TextSpan,
    parse_message,
)
from tests.conftest import REPO_ROOT


def _item(**kw):
    base = dict(
        line_start=3, line_end=3, raw_text="- [ ] Priya to send", description="Priya to send",
        task_text="send", checkbox_state="open", section_heading="Actions", occurrence_index=0,
        block_hash="h",
    )
    base.update(kw)
    return ParsedItem(**base)


def test_unknown_codes_rejected():
    with pytest.raises(ValueError):
        ParseWarning("W-NOPE", 1, "m")
    with pytest.raises(ValueError):
        ParseWarning(codes.E_BINARY, 1, "m")  # an error code is not a warning
    with pytest.raises(ValueError):
        FileFailure("E-NOPE", "m")
    with pytest.raises(ValueError):
        FileFailure(codes.W_NO_FILES, "m")


def test_template_set_equals_parser_emitted_codes():
    assert set(PARSE_MESSAGE_TEMPLATES) == set(PARSER_EMITTED_CODES)
    ws = {c for c in PARSER_EMITTED_CODES if c in codes.WARNING_CODES}
    es = {c for c in PARSER_EMITTED_CODES if c in codes.ERROR_CODES}
    assert len(ws) == 22 and len(es) == 5
    for other in (
        codes.W_MEETING_DATE_FUTURE, codes.W_OWNER_MISSING, codes.W_OWNER_NOW_AMBIGUOUS,
        codes.W_ATTENDEE_NAME_INCOMPLETE, codes.W_NAME_DIACRITIC, codes.W_STATUS_REOPENED,
        codes.W_STATUS_CONFLICT, codes.W_FILE_MISSING,
    ):
        assert other not in PARSE_MESSAGE_TEMPLATES


def test_severity_mapping():
    assert codes.is_file_failure(codes.E_ENCODING)
    assert not codes.is_file_failure(codes.W_DIR_UNREADABLE)
    ParseWarning(codes.W_DIR_UNREADABLE, None, "m")
    FileFailure(codes.E_ENCODING, "m", byte_offset=4)


def test_every_template_names_file_and_next_step():
    for code, tpl in PARSE_MESSAGE_TEMPLATES.items():
        assert "{file}" in tpl, code
        assert "Next:" in tpl, code


def test_parse_message_renders_location():
    text = parse_message(codes.E_ENCODING, "a/b.md", offset=12)
    assert text.startswith("a/b.md:") and "12" in text
    assert parse_message(codes.W_CHECKBOX_EMPTY, "a.md", 7).startswith("a.md:7:")
    # missing placeholders are kept, not raised
    assert "{offset}" in parse_message(codes.E_ENCODING, "a.md")


def test_models_are_immutable():
    objs = (ParsedFile("a.md", "ok"), _item(), TextSpan(0, 1), Attendee("A"),
            NonCheckboxCandidate("k", 1, "t"), ParseWarning(codes.W_NO_ITEMS, 1, "m"))
    for obj in objs:
        field = dataclasses.fields(obj)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, None)


def test_parsed_file_validation():
    with pytest.raises(ValueError):
        ParsedFile("a.md", "weird")
    with pytest.raises(ValueError):
        ParsedFile("a.md", "failed")  # needs failure
    with pytest.raises(ValueError):
        ParsedFile("a.md", "ok", failure=FileFailure(codes.E_BINARY, "m"))
    with pytest.raises(ValueError):
        MeetingMeta(None, "bogus", None, None)


def test_item_has_description_and_task_text_and_hooks_default_none():
    it = _item()
    assert it.description == "Priya to send" and it.task_text == "send"
    assert it.owner_hook is None and it.due_hook is None


def test_to_dict_stable_and_json_serialisable():
    pf = ParsedFile(
        "a.md", "ok", None, "abc", 10, 5,
        MeetingMeta(dt.date(2026, 9, 1), "date_line", "2026-09-01", "Kickoff", (Attendee("Priya Nair", None),)),
        (_item(),), (NonCheckboxCandidate("todo", 9, "TODO x"),),
        (ParseWarning(codes.W_NO_ITEMS, 5, "b"), ParseWarning(codes.W_LONG_LINE, 2, "a"),
         ParseWarning(codes.W_CHECKBOX_EMPTY, 5, "c")),
    )
    d1, d2 = pf.to_dict(), pf.to_dict()
    assert d1 == d2
    assert list(d1) == [
        "rel_path", "status", "failure", "content_sha256", "size_bytes", "mtime_ns", "meeting",
        "items", "candidates", "warnings",
    ]
    assert d1["meeting"]["date"] == "2026-09-01"
    assert [w["code"] for w in d1["warnings"]] == [codes.W_LONG_LINE, codes.W_CHECKBOX_EMPTY, codes.W_NO_ITEMS]
    json.dumps(d1)
    failed = ParsedFile("x.md", "failed", FileFailure(codes.E_BINARY, "m", size_bytes=3))
    json.dumps(failed.to_dict())


def test_no_code_literals_outside_codes():
    pat = re.compile(r"""["'][EW]-[A-Z]{2,}(?:-[A-Z]+)*["']""")
    offenders = []
    for path in (REPO_ROOT / "src" / "actiontrack").rglob("*.py"):
        if path.name == "codes.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{path.name}:{n}")
    assert not offenders, offenders


def test_domain_models_import_no_io_modules():
    src = (REPO_ROOT / "src" / "actiontrack" / "domain" / "models.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(os|pathlib|time)\b", src, re.M)
    assert "date.today" not in src and "datetime.now" not in src
