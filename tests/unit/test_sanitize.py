"""Control-character sanitiser (T-M1-07, SEC-08, AC-09.8, AC-09.6)."""

from __future__ import annotations

import pytest

from actiontrack.report.sanitize import clean_block, clean_text


@pytest.mark.ac("AC-09.8")
def test_escape_sequences_removed():
    out = clean_text("Fix \x1b[31mred\x1b[0m \x9b31m \x7f")
    assert "\x1b" not in out and "\x9b" not in out and "\x7f" not in out


def test_c0_and_c1_ranges_all_removed():
    cc = "".join(chr(c) for c in list(range(0, 32)) + list(range(0x7F, 0xA0)))
    assert clean_text("a" + cc + "b").replace(" ", "") == "ab"
    kept = clean_block("a" + cc + "b")
    assert all(ch in "\n\t" or (ord(ch) >= 32 and not 0x7F <= ord(ch) < 0xA0) for ch in kept)


def test_tab_newline_cr_become_one_space():
    assert clean_text("a\tb\nc\r\nd\re") == "a b c d e"


@pytest.mark.ac("AC-09.6")
def test_unicode_preserved():
    s = "Zoë Åström 李雷 😀 é"
    assert clean_text(s) == s and clean_block(s) == s


def test_clean_block_keeps_lf_and_tab_normalises_crlf():
    assert clean_block("a\r\nb\rc\n\td\x1b") == "a\nb\nc\n\td"


def test_lone_surrogate_does_not_raise():
    assert clean_text("a\udc80b") == "a\udc80b"
