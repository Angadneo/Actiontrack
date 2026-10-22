"""T-M1-02: warning/error code registry vs spec Appendix B, 9.5 (read-only parse of the spec)."""

import re

import pytest

from actiontrack import codes
from actiontrack.codes import (
    DUE_REASONS,
    DUE_RULES,
    EMITTERS,
    ERROR_CODES,
    REGISTRY,
    WARNING_CODES,
    ItemLinkReason,
    Severity,
    is_file_failure,
    message,
)
from tests.conftest import REPO_ROOT

SPEC = (REPO_ROOT / "specs" / "actiontrack-spec.md").read_text(encoding="utf-8")


def _appendix_b() -> str:
    return SPEC.split("## Appendix B", 1)[1].split("## Appendix C", 1)[0]


def _line(prefix: str) -> str:
    return next(ln for ln in _appendix_b().splitlines() if ln.startswith(prefix))


def test_codes_match_appendix_b():
    errors_line = _line("Errors")
    warnings_line = _line("Warnings")
    spec_e = set(re.findall(r"`(E-[A-Z0-9-]+)`", errors_line))
    spec_w = set(re.findall(r"`(W-[A-Z0-9-]+)`", warnings_line))
    assert spec_e and spec_w
    assert set(ERROR_CODES) - spec_e == set() and spec_e - set(ERROR_CODES) == set(), "E-codes differ"
    assert set(WARNING_CODES) - spec_w == set() and spec_w - set(WARNING_CODES) == set(), "W-codes differ"
    for g20 in ("W-ATTENDEE-NAME-INCOMPLETE", "W-HTML-COMMENT-UNCLOSED", "W-CHECKBOX-EMPTY", "W-DIR-UNREADABLE"):
        assert g20 in WARNING_CODES


def test_constants_match_registry():
    consts = {v for k, v in vars(codes).items() if re.fullmatch(r"[EW]_[A-Z_]+", k) and isinstance(v, str)}
    assert consts == set(REGISTRY)
    for name, value in vars(codes).items():
        if re.fullmatch(r"[EW]_[A-Z_]+", name):
            assert value == name.replace("_", "-")


def test_due_reasons_and_rules_match_spec():
    reasons = set(re.findall(r"`([a-z_]+)`", _line("Due-unresolved reasons").split("`vague` vs `range`")[0]))
    assert set(DUE_REASONS) == reasons and len(reasons) == 10
    section = SPEC.split("**`due_rule` catalogue", 1)[1].split("Unresolved dates store", 1)[0]
    rules: set[str] = set()
    for row in re.findall(r"^\| (.+?) \|.*$", section, re.M):
        first = row.split(" | ")[0]
        if first.startswith(("Rule id", "---")):
            continue
        rules |= set(re.findall(r"`([a-z_]+)`", first))
    assert set(DUE_RULES) == rules


def test_e_codes_are_file_failures():
    assert is_file_failure("E-BINARY") is True
    assert is_file_failure("W-EMPTY-FILE") is False
    assert is_file_failure("W-DIR-UNREADABLE") is False
    for c in ERROR_CODES:
        assert is_file_failure(c)
    for c in WARNING_CODES:
        assert not is_file_failure(c)
    assert ERROR_CODES.isdisjoint(WARNING_CODES)


def test_registry_entries_complete():
    assert set(REGISTRY) == ERROR_CODES | WARNING_CODES
    for code, info in REGISTRY.items():
        assert info.code == code and info.template and info.emitter in {"parsers", "ingest", "resolution"}
        assert (info.severity is Severity.ERROR) == code.startswith("E-")
    assert set(EMITTERS) == set(REGISTRY)


def test_emitter_mapping_follows_appendix_b_note():
    assert EMITTERS["W-MEETING-DATE-FUTURE"] == "ingest"
    assert EMITTERS["E-INTERNAL-PARSE"] == "ingest"
    for code in REGISTRY:
        if code.startswith(("W-OWNER-", "W-STATUS-")) or code in {
            "W-ATTENDEE-NAME-INCOMPLETE",
            "W-NAME-DIACRITIC",
            "W-FILE-MISSING",
        }:
            assert EMITTERS[code] == "resolution", code
    assert EMITTERS["W-EMPTY-FILE"] == "parsers"


def test_message_formatting():
    assert message("E-ENCODING", offset=1042).startswith("E-ENCODING: not valid UTF-8 at byte 1042")
    assert "{offset}" in REGISTRY["E-ENCODING"].format()  # missing keys kept, no KeyError


def test_item_link_reasons_separate_from_codes():
    assert {r.value for r in ItemLinkReason} == {
        "due_differs",
        "owner_differs",
        "status_differs",
        "same_file",
        "fuzzy",
        "recurrence",
        "outside_window",
        "owner_unresolved",
    }
    assert not {r.value for r in ItemLinkReason} & set(REGISTRY)
