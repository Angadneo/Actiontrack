"""ItemId helpers (T-M1-13): FR-03.1 ID syntax, AC-03.1 / AC-03.5 helper halves."""

from __future__ import annotations

import pytest

from actiontrack.domain.models import format_item_id, parse_item_id
from actiontrack.errors import ExitCode, UsageError


@pytest.mark.ac("AC-03.1")
@pytest.mark.parametrize("text,expected", [("AT-0012", 12), ("12", 12), ("at-12", 12), ("At-0007", 7), ("AT-12345", 12345)])
def test_parse_item_id_accepts_AT_0012_12_at_12(text, expected):
    assert parse_item_id(text) == expected


@pytest.mark.ac("AC-03.5")
def test_parse_item_id_rejects_abc():
    with pytest.raises(UsageError) as info:
        parse_item_id("abc")
    assert info.value.exit_code == ExitCode.USAGE


@pytest.mark.parametrize(
    "text",
    ["abc", "AT-", "AT", "-5", "0", "AT-0000", "1.5", "", "   ", "+3", " 12", "12 ", "AT 12", "AT--1", "١٢", "AT-١٢", "１２", "12\n", "0x10", "1e3"],
)
def test_parse_item_id_rejects_everything_else(text):
    with pytest.raises(UsageError):
        parse_item_id(text)


def test_parse_item_id_rejects_non_string():
    with pytest.raises(UsageError):
        parse_item_id(None)  # type: ignore[arg-type]


def test_format_item_id_padding():
    assert format_item_id(4) == "AT-0004"
    assert format_item_id(12345) == "AT-12345"
    assert format_item_id(1) == "AT-0001"
    assert format_item_id(9999) == "AT-9999"


def test_format_parse_roundtrip():
    for n in (1, 4, 99, 10000, 123456):
        assert parse_item_id(format_item_id(n)) == n


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_format_item_id_rejects_invalid(bad):
    with pytest.raises(ValueError):
        format_item_id(bad)
