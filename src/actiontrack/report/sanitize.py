"""Control-character sanitiser (SEC-08, AC-09.8, T-M1-07).

Untrusted note text must never reach a terminal or log with raw control
characters (escape-sequence injection). Everything that leaves the process as
text goes through one of these two functions (``io.emit`` / ``io.diag`` use
``clean_block``; the table renderer uses ``clean_text`` per cell).

Public API
----------
``clean_text(s) -> str``   single-line text (table cells, log message heads):
                           every Unicode category ``Cc`` character (C0, DEL, C1)
                           is removed, except TAB, LF and CR which become ONE space
                           (a CR LF pair collapses to one space).
``clean_block(s) -> str``  multi-line text: line breaks (CRLF / CR / LF) are
                           normalised to LF and kept, TAB is kept, every other ``Cc``
                           character is removed.
Everything else (emoji, CJK, combining marks, U+2028/2029) is preserved unchanged
(AC-09.6). The functions are pure and never raise on odd input (lone surrogates pass).
"""

from __future__ import annotations

import re
import unicodedata

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_BLANKS = re.compile(r"\r\n|[\t\r\n]")


def _strip_cc(s: str) -> str:
    # Fast path: pure printable ASCII has no Cc other than what we already handled.
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cc")


def clean_text(s: str) -> str:
    """Single-line sanitising: TAB/CR/LF -> one space, other ``Cc`` removed."""
    return _strip_cc(_BLANKS.sub(" ", s))


def clean_block(s: str) -> str:
    """Multi-line sanitising: line breaks -> ``\n``, TAB kept, other ``Cc`` removed."""
    out = []
    for part in _LINE_BREAK.split(s):
        out.append("".join(ch for ch in part if ch == "\t" or unicodedata.category(ch) != "Cc"))
    return "\n".join(out)
