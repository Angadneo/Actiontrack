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
    ``first_name_key`` also accepts a person-like object with ``first_name``.

T-M4-01/02 additions
--------------------
``fold_key``                alias of ``folded_key``. Non-decomposable letters (``ø``, ``ß``, ``đ``)
                            are NOT folded in v1.
``split_name(text)`` / ``first_token`` / ``last_token``   key tokens (hyphenated surname = 1 token).
``classify_key_kind(text) -> 'full'|'initial_form'|'single_token'|'empty'``  (G-30)
``provisional_key(text) -> str | None``   single token -> token; initial form -> ``first initial``
                            (``zed q``); full name -> ``identity_key``; blank -> None.
``person_slug(identity_key, taken=())``   alias of ``person_id_for`` (collisions ``-2``, ``-3``).
``classify_name(raw, qualifier=None) -> NameForm | None``  delegates to ``classify_key_kind``;
                            None (rejected) for blank, more than 4 tokens, or a possessive.
``is_possessive(raw)``      ends in ``'s`` / ``s'`` (spec 10.1 rule 6: never an owner).
``initial_matches(person, form, *, fold=False)``  person-like (``first_name``, ``last_name``) vs a
                            ``first_initial`` NameForm (spec 10.3 step 2).
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Collection

from actiontrack.domain import rules as _rules
from actiontrack.domain.models import NameForm, NameFormKind

QUALIFIER_SEP = "|"
_HONORIFICS = _rules.HONORIFICS
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


def first_name_key(full_name: object) -> str | None:
    """First-token key of a name string, or of a person-like object (``.first_name``)."""
    text = full_name if isinstance(full_name, str) or full_name is None else full_name.first_name  # type: ignore[attr-defined]
    tokens = name_tokens(text)
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


fold_key = folded_key
person_slug = person_id_for


def split_name(text: str | None) -> list[str]:
    return name_tokens(text)


def first_token(text: str | None) -> str | None:
    tokens = name_tokens(text)
    return tokens[0] if tokens else None


def last_token(text: str | None) -> str | None:
    tokens = name_tokens(text)
    return tokens[-1] if tokens else None


def classify_key_kind(text: str | None) -> str:
    """``full`` | ``initial_form`` | ``single_token`` | ``empty`` (single source of truth, G-30)."""
    tokens = name_tokens(text)
    if not tokens:
        return "empty"
    if len(tokens) == 1:
        return "single_token"
    if _is_initial(_raw_last_token(text or "")):
        return "initial_form"
    return "full"


def provisional_key(text: str | None) -> str | None:
    kind = classify_key_kind(text)
    if kind == "empty":
        return None
    tokens = name_tokens(text)
    if kind == "initial_form":
        return f"{tokens[0]} {tokens[-1]}"
    return identity_key(text)


def is_possessive(raw: str | None) -> bool:
    """``Anita's`` / ``James'`` (curly apostrophes included) as the last written token."""
    parts = (raw or "").split()
    if not parts:
        return False
    last = parts[-1].translate(_APOSTROPHES).rstrip(".,;:)")
    low = last.casefold()
    return low.endswith("'s") or (low.endswith("s'") and len(low) > 2)


def classify_name(raw: str | None, qualifier: str | None = None) -> NameForm | None:
    tokens = name_tokens(raw)
    if not tokens or len(tokens) > _rules.MAX_NAME_TOKENS or is_possessive(raw):
        return None
    qkey = name_key(qualifier) or None
    kind = classify_key_kind(raw)
    if kind == "single_token":
        return NameForm(NameFormKind.FIRST_ONLY, tokens[0], None, None, qkey)
    if kind == "initial_form":
        return NameForm(NameFormKind.FIRST_INITIAL, tokens[0], None, tokens[-1][:1], qkey)
    return NameForm(NameFormKind.FULL, tokens[0], tokens[-1], tokens[-1][:1], qkey)


def initial_matches(person: object, form: NameForm, *, fold: bool = False) -> bool:
    """True when ``person`` has ``form``'s first name and a last name starting with its initial."""
    if form.kind is not NameFormKind.FIRST_INITIAL or not form.initial:
        return False
    key = folded_key if fold else name_key
    first = key(getattr(person, "first_name", None))
    last = key(getattr(person, "last_name", None))
    if not first or not last:
        return False
    want_first, want_initial = key(form.first), key(form.initial)
    return first.split()[0] == want_first and last.split()[-1][:1] == want_initial
