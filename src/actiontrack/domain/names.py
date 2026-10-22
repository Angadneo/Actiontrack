"""Name keys and full-name identity (spec 10.2, 10.2a, D-07; T-M1-05 minimum).

Later tasks (T-M4) extend this module and MUST reuse these functions.

Public API
----------
``name_key(text) -> str``
    NFKC, case-fold, collapse whitespace, remove punctuation except an internal hyphen or
    apostrophe, drop the honorific tokens Dr/Mr/Ms/Mrs (with or without a dot). Diacritics
    are KEPT (``José`` != ``Jose``). Curly apostrophes become ``'``.
``name_tokens(text) -> list[str]``
    ``name_key(text).split()``.
``identity_key(full_name, qualifier=None) -> str | None``
    ``name_key`` of the FIRST token + LAST token (middle tokens ignored; hyphenated surnames
    are one token): ``Priya R. Nair`` == ``Dr Priya Nair`` == ``PRIYA  nair`` -> ``priya nair``.
    * single-token name (``Zed``) -> the token alone (``zed``); ``is_known_identity`` is False.
    * initial form (``Priya N.``: last token is a lone cased letter, optionally with a dot)
      -> ``None`` (not an identity; resolved only by spec 10.3 step 2).
    * empty/blank -> ``None``.
    With a non-blank ``qualifier`` the qualifier key is part of the identity:
    ``"priya nair|sales"`` (``QUALIFIER_SEP`` joins them).
``is_known_identity(full_name) -> bool``
    True only for a First+Last identity (>= 2 tokens, last token not an initial).
``folded_key(text) -> str``
    ``name_key`` with diacritics folded (NFKD, combining marks dropped). Applied identically
    to roster, attendee and owner tokens (10.2a(c)); ``identity_key`` itself keeps
    diacritics. For a folded identity use ``folded_key(identity_key(name))``.
``person_id_for(identity, taken=()) -> str``
    Deterministic, filesystem-safe slug of an identity key (as returned by
    ``identity_key``, qualifier included): ``priya nair`` -> ``priya-nair``;
    ``priya nair|sales`` -> ``priya-nair-sales``.
    Transliteration rule (integrator note b-6): NO transliteration. The identity key is
    NFKC/case-folded, then every run of characters that are not Unicode letters, digits or
    combining marks becomes a single ``-``, leading/trailing ``-`` are stripped, and the
    result is NFC. Non-Latin names therefore keep their script (``李雷 王`` -> ``李雷-王``,
    ``zoë åström`` -> ``zoë-åström``), which is non-empty and valid in NTFS file names.
    A key with no letters/digits at all falls back to ``p-<sha1[:8]>``. If the slug is
    already in ``taken`` (collisions after folding), ``-2``, ``-3``... is appended.
``first_name_key(full_name) -> str | None`` / ``last_initial(full_name) -> str | None``
    small helpers for namesake grouping (first token key / initial of the last token).
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Collection

QUALIFIER_SEP = "|"
_HONORIFICS = frozenset({"dr", "mr", "ms", "mrs"})
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "‐": "-", "‑": "-"})


def _keep(ch: str) -> bool:
    return ch.isalnum() or unicodedata.category(ch).startswith("M")


def _clean_token(token: str) -> str:
    out = "".join(ch for ch in token if _keep(ch) or ch in "-'")
    return out.strip("-'")


def name_key(text: str | None) -> str:
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = unicodedata.normalize("NFKC", s.casefold()).translate(_APOSTROPHES)
    tokens: list[str] = []
    for raw in s.split():
        if raw.strip(".,").strip() in _HONORIFICS:
            continue
        tok = _clean_token(raw)
        if tok:
            tokens.append(tok)
    return " ".join(tokens)


def name_tokens(text: str | None) -> list[str]:
    return name_key(text).split()


def _is_initial(raw_token: str) -> bool:
    t = raw_token.strip()
    if t.endswith("."):
        t = t[:-1]
    return len(t) == 1 and t.isalpha() and t.lower() != t.upper()


def _raw_last_token(full_name: str) -> str:
    # last token that survives honorific/punctuation cleaning, still in raw form
    raw = [t for t in full_name.split() if name_key(t)]
    return raw[-1] if raw else ""


def identity_key(full_name: str | None, qualifier: str | None = None) -> str | None:
    tokens = name_tokens(full_name)
    if not tokens:
        return None
    if len(tokens) == 1:
        base = tokens[0]
    else:
        if _is_initial(_raw_last_token(full_name or "")):
            return None
        base = f"{tokens[0]} {tokens[-1]}"
    qkey = name_key(qualifier)
    return f"{base}{QUALIFIER_SEP}{qkey}" if qkey else base


def is_known_identity(full_name: str | None) -> bool:
    tokens = name_tokens(full_name)
    return len(tokens) >= 2 and identity_key(full_name) is not None


def folded_key(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", name_key(text))
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", stripped)


def first_name_key(full_name: str | None) -> str | None:
    tokens = name_tokens(full_name)
    return tokens[0] if tokens else None


def last_initial(full_name: str | None) -> str | None:
    tokens = name_tokens(full_name)
    if len(tokens) < 2:
        return None
    return tokens[-1][:1]


def person_id_for(identity: str, taken: Collection[str] = ()) -> str:
    s = unicodedata.normalize("NFKC", identity).casefold()
    parts: list[str] = []
    current: list[str] = []
    for ch in s:
        if _keep(ch):
            current.append(ch)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    slug = unicodedata.normalize("NFC", "-".join(parts))
    if not slug:
        slug = "p-" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
    candidate = slug
    n = 2
    while candidate in taken:
        candidate = f"{slug}-{n}"
        n += 1
    return candidate
