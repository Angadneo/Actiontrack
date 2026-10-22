"""Configuration file and roster loading/validation (spec FR-09.1/09.2, Appendix C, 10.2a; T-M1-04/05).

Public API
----------
``load_config(config_path=None, *, env=None, cwd=None) -> Config``
    Discovery: ``config_path`` (``--config``) > ``ACTIONTRACK_CONFIG`` > ``<cwd>/actiontrack.toml``
    if it exists > built-in defaults. An explicit path that is missing/unreadable -> ``EnvError``
    (exit 4); an absent default file is fine. Load is lazy: call it only from commands that need
    it (so ``--help``/``--version`` never fail on a bad config).
    Every problem (TOML syntax, unknown key, wrong type, bad value, bad roster entry, bad
    ``timezone``) is an ``EnvError`` (exit 4); all value errors are collected and reported in one
    exception, one per line, as ``config <file>: key "<dotted.key>": <reason>``.
``parse_config(data, *, source=None, cwd=None) -> Config``   same validation from an already parsed dict.
``default_config(cwd=None) -> Config``                        Appendix C defaults, empty roster.
``parse_roster(entries, *, source="<roster>") -> Roster``     validate ``[[person]]`` entries only.

``Config`` (frozen)   ``source: Path|None, timezone: str|None, date_order: str|None ("dmy"|"mdy"|None;
                      "" and absent both -> None), limits: Limits, owners: Owners, gmail: GmailConfig,
                      roster: Roster``. ``config.resolve_zone(os_offset_provider=None)`` returns the
                      ``clock.ResolvedZone`` for ``timezone`` (fixed-offset fallback when unset).
``Limits``            ``max_file_bytes=5242880, dup_window_days=60, fuzzy_threshold=0.85``
``Owners``            ``attendee_disambiguation=True``
``GmailConfig``       ``channel="handoff"|"google"|"fake", handoff_dir: Path (absolute, resolved against
                      cwd), google_mcp_url (validated https URL on ``ALLOWED_MCP_HOST``),
                      include_signature=False, timeout_seconds=30, verify_after_create=True,
                      oauth_client_id=""``. (The allowed host/tools are code constants in
                      ``gmail/allowlist.py`` and never come from config; a client-secret key is an
                      unknown key.)
``RosterPerson``      frozen: ``index, first_name, last_name, full_name, email|None, aliases (tuple),
                      alias_keys (tuple), qualifier|None, identity_key (base, qualifier excluded),
                      qualified_key (identity incl. qualifier), person_id, first_name_key``.
``Roster``            ordered as in the file (sort when printing); ``people`` tuple, iterable, ``len``,
                      ``by_person_id(pid)``, ``find_by_email(email)`` (case-insensitive),
                      ``namesake_groups() -> dict[first_name_key, tuple[RosterPerson, ...]]``
                      (first names carried by >= 2 people).

Roster rules: first/last name required and non-blank, last name must not be only an initial;
valid single-address ``email``; two entries with the same identity key are an error naming both
(``person[0] "Priya Nair" and person[3] "Priya Nair"``) unless BOTH carry non-blank, distinct
qualifiers; duplicate emails (case-insensitive) are an error; an alias shared by two people is
allowed (spec 10.3 step 1 makes it ``ambiguous``).
"""

from __future__ import annotations

import difflib
import os
import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from actiontrack.clock import ResolvedZone, resolve_timezone
from actiontrack.domain.names import (
    first_name_key,
    identity_key,
    name_key,
    person_id_for,
)
from actiontrack.errors import EnvError
from actiontrack.gmail.allowlist import ALLOWED_MCP_HOST

ENV_CONFIG = "ACTIONTRACK_CONFIG"
DEFAULT_CONFIG_NAME = "actiontrack.toml"
DEFAULT_GOOGLE_MCP_URL = f"https://{ALLOWED_MCP_HOST}/mcp/v1"
DEFAULT_HANDOFF_DIR = ".actiontrack/handoff"


# ------------------------------------------------------------------ data model
@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 5_242_880
    dup_window_days: int = 60
    fuzzy_threshold: float = 0.85


@dataclass(frozen=True)
class Owners:
    attendee_disambiguation: bool = True


@dataclass(frozen=True)
class GmailConfig:
    channel: str = "handoff"
    handoff_dir: Path = Path(DEFAULT_HANDOFF_DIR)
    google_mcp_url: str = DEFAULT_GOOGLE_MCP_URL
    include_signature: bool = False
    timeout_seconds: float = 30
    verify_after_create: bool = True
    oauth_client_id: str = ""


@dataclass(frozen=True)
class RosterPerson:
    index: int
    first_name: str
    last_name: str
    full_name: str
    email: str | None
    aliases: tuple[str, ...]
    alias_keys: tuple[str, ...]
    qualifier: str | None
    identity_key: str
    qualified_key: str
    person_id: str
    first_name_key: str


@dataclass(frozen=True)
class Roster:
    people: tuple[RosterPerson, ...] = ()

    def __iter__(self) -> Iterator[RosterPerson]:
        return iter(self.people)

    def __len__(self) -> int:
        return len(self.people)

    def by_person_id(self, person_id: str) -> RosterPerson | None:
        return next((p for p in self.people if p.person_id == person_id), None)

    def find_by_email(self, email: str) -> RosterPerson | None:
        e = email.strip().lower()
        return next((p for p in self.people if p.email and p.email.lower() == e), None)

    def namesake_groups(self) -> dict[str, tuple[RosterPerson, ...]]:
        groups: dict[str, list[RosterPerson]] = {}
        for p in self.people:
            groups.setdefault(p.first_name_key, []).append(p)
        return {k: tuple(v) for k, v in sorted(groups.items()) if len(v) >= 2}


@dataclass(frozen=True)
class Config:
    source: Path | None = None
    timezone: str | None = None
    date_order: str | None = None
    limits: Limits = field(default_factory=Limits)
    owners: Owners = field(default_factory=Owners)
    gmail: GmailConfig = field(default_factory=GmailConfig)
    roster: Roster = field(default_factory=Roster)

    def resolve_zone(
        self, os_offset_provider: Callable[[], timedelta] | None = None
    ) -> ResolvedZone:
        return resolve_timezone(self.timezone, os_offset_provider=os_offset_provider)


# ------------------------------------------------------------------ errors
class _Errors:
    def __init__(self, label: str) -> None:
        self.label = label
        self.lines: list[str] = []

    def add(self, key: str, reason: str) -> None:
        self.lines.append(f'config {self.label}: key "{key}": {reason}')

    def raise_if_any(self) -> None:
        if self.lines:
            raise EnvError(
                "\n".join(self.lines),
                next_step="fix the config file and re-run" if self.lines else None,
            )


def _hint(key: str, allowed: Mapping[str, Any] | set[str] | frozenset[str]) -> str:
    close = difflib.get_close_matches(key, list(allowed), n=1, cutoff=0.6)
    return f' (did you mean "{close[0]}"?)' if close else ""


def _check_keys(
    table: Mapping[str, Any], allowed: frozenset[str], prefix: str, errors: _Errors
) -> None:
    for k in table:
        if k not in allowed:
            errors.add(f"{prefix}{k}", f"unknown key{_hint(k, allowed)}")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _get_table(data: Mapping[str, Any], key: str, errors: _Errors) -> Mapping[str, Any]:
    v = data.get(key, {})
    if not isinstance(v, dict):
        errors.add(key, f"expected a table, got {type(v).__name__}")
        return {}
    return v


# ------------------------------------------------------------------ roster
_EMAIL_BAD = set(" \t\r\n,;<>()[]\"\\")


def _email_ok(e: str) -> bool:
    if any(c in _EMAIL_BAD for c in e) or e.count("@") != 1:
        return False
    local, domain = e.split("@")
    return bool(local) and bool(domain) and not domain.startswith(".") and not domain.endswith(".")


_PERSON_KEYS = frozenset({"first_name", "last_name", "email", "aliases", "qualifier"})


def _validate_roster(entries: Any, errors: _Errors) -> Roster:
    if entries is None:
        return Roster()
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        errors.add("person", "expected an array of tables ([[person]])")
        return Roster()
    parsed: list[dict[str, Any]] = []
    for i, entry in enumerate(entries):
        pre = f"person[{i}]."
        _check_keys(entry, _PERSON_KEYS, pre, errors)
        ok = True
        names: dict[str, str] = {}
        for k in ("first_name", "last_name"):
            v = entry.get(k)
            if v is None:
                errors.add(f"person[{i}].{k}", "required key is missing")
                ok = False
            elif not isinstance(v, str):
                errors.add(f"person[{i}].{k}", f"expected a string, got {type(v).__name__}")
                ok = False
            elif not name_key(v):
                errors.add(f"person[{i}].{k}", "must not be blank")
                ok = False
            else:
                names[k] = v.strip()
        email = entry.get("email", "")
        if not isinstance(email, str):
            errors.add(f"person[{i}].email", f"expected a string, got {type(email).__name__}")
            ok = False
            email = ""
        email = email.strip()
        if email and not _email_ok(email):
            errors.add(
                f"person[{i}].email",
                f'invalid email address "{email}" (expected a single address like name@example.com)',
            )
            ok = False
        aliases_raw = entry.get("aliases", [])
        aliases: list[str] = []
        if not isinstance(aliases_raw, list) or not all(isinstance(a, str) for a in aliases_raw):
            errors.add(f"person[{i}].aliases", "expected a list of strings")
            ok = False
        else:
            aliases = [a.strip() for a in aliases_raw if a.strip()]
        qual = entry.get("qualifier", "")
        if not isinstance(qual, str):
            errors.add(f"person[{i}].qualifier", f"expected a string, got {type(qual).__name__}")
            ok = False
            qual = ""
        qual = qual.strip()
        if ok:
            full = f"{names['first_name']} {names['last_name']}"
            base = identity_key(full)
            if base is None:
                errors.add(
                    f"person[{i}].last_name",
                    f'"{names["last_name"]}" is only an initial; a full last name is required',
                )
                continue
            parsed.append(
                {
                    "index": i,
                    "first_name": names["first_name"],
                    "last_name": names["last_name"],
                    "full_name": full,
                    "email": email or None,
                    "aliases": tuple(aliases),
                    "qualifier": qual or None,
                    "base": base,
                }
            )
    # duplicate identity (qualifier rule) -- names BOTH entries
    bad_index: set[int] = set()
    for a_pos, a in enumerate(parsed):
        for b in parsed[a_pos + 1 :]:
            if a["base"] != b["base"]:
                continue
            qa, qb = name_key(a["qualifier"]), name_key(b["qualifier"])
            if qa and qb and qa != qb:
                continue
            why = (
                "same qualifier"
                if qa and qa == qb
                else "give each a distinct non-blank `qualifier` to keep both"
            )
            errors.add(
                f"person[{b['index']}]",
                f'duplicate identity: person[{a["index"]}] "{a["full_name"]}" and '
                f'person[{b["index"]}] "{b["full_name"]}" ({why})',
            )
            bad_index.update({a["index"], b["index"]})
    # duplicate email
    seen_email: dict[str, dict[str, Any]] = {}
    for p in parsed:
        if not p["email"]:
            continue
        e = p["email"].lower()
        if e in seen_email:
            o = seen_email[e]
            errors.add(
                f"person[{p['index']}].email",
                f'duplicate email "{p["email"]}": person[{o["index"]}] "{o["full_name"]}" and '
                f'person[{p["index"]}] "{p["full_name"]}"',
            )
        else:
            seen_email[e] = p
    people: list[RosterPerson] = []
    taken: set[str] = set()
    for p in parsed:
        qkey = identity_key(p["full_name"], p["qualifier"]) or p["base"]
        pid = person_id_for(qkey, taken)
        taken.add(pid)
        people.append(
            RosterPerson(
                index=p["index"],
                first_name=p["first_name"],
                last_name=p["last_name"],
                full_name=p["full_name"],
                email=p["email"],
                aliases=p["aliases"],
                alias_keys=tuple(k for k in (name_key(a) for a in p["aliases"]) if k),
                qualifier=p["qualifier"],
                identity_key=p["base"],
                qualified_key=qkey,
                person_id=pid,
                first_name_key=first_name_key(p["full_name"]) or "",
            )
        )
    return Roster(tuple(people))


def parse_roster(entries: Any, *, source: str = "<roster>") -> Roster:
    errors = _Errors(source)
    roster = _validate_roster(entries, errors)
    errors.raise_if_any()
    return roster


# ------------------------------------------------------------------ config
_TOP_KEYS = frozenset({"timezone", "date_order", "limits", "owners", "gmail", "person"})
_LIMIT_KEYS = frozenset({"max_file_bytes", "dup_window_days", "fuzzy_threshold"})
_OWNER_KEYS = frozenset({"attendee_disambiguation"})
_GMAIL_KEYS = frozenset(
    {
        "channel",
        "handoff_dir",
        "google_mcp_url",
        "include_signature",
        "timeout_seconds",
        "verify_after_create",
        "oauth_client_id",
    }
)


def _abs(path: str, cwd: Path) -> Path:
    return Path(os.path.abspath(os.path.join(str(cwd), path)))


def _google_url_problem(url: str) -> str | None:
    host_msg = f"must be an https URL whose host is exactly {ALLOWED_MCP_HOST}"
    if any(c.isspace() or ord(c) < 32 or c == "\\" for c in url):
        return host_msg
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return host_msg
    if parts.scheme != "https":
        return f'{host_msg} (got scheme "{parts.scheme or "none"}")'
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return f"{host_msg} (credentials in the URL are not allowed)"
    if parts.hostname != ALLOWED_MCP_HOST:
        return f'{host_msg} (got host "{parts.hostname}")'
    if port not in (None, 443):
        return f"{host_msg} (non-default port {port} is not allowed)"
    return None


def default_config(cwd: Path | str | None = None) -> Config:
    base = Path(cwd) if cwd is not None else Path.cwd()
    return Config(gmail=GmailConfig(handoff_dir=_abs(DEFAULT_HANDOFF_DIR, base)))


def parse_config(
    data: Mapping[str, Any], *, source: Path | None = None, cwd: Path | str | None = None
) -> Config:
    base = Path(cwd) if cwd is not None else Path.cwd()
    errors = _Errors(str(source) if source is not None else "<config>")
    _check_keys(data, _TOP_KEYS, "", errors)

    tz = data.get("timezone")
    if tz is not None:
        if not isinstance(tz, str):
            errors.add("timezone", f"expected a string, got {type(tz).__name__}")
            tz = None
        elif tz.strip():
            try:
                resolve_timezone(tz)
            except EnvError as e:
                reason = e.what + (f" -- {e.next_step}" if e.next_step else "")
                errors.add("timezone", reason)
            tz = tz.strip()
        else:
            tz = None

    date_order = data.get("date_order", "")
    if not isinstance(date_order, str):
        errors.add("date_order", f"expected a string, got {type(date_order).__name__}")
        date_order = ""
    elif date_order not in ("", "dmy", "mdy"):
        errors.add("date_order", f'invalid value "{date_order}" (expected "dmy", "mdy" or "")')
        date_order = ""

    # [limits]
    lim = _get_table(data, "limits", errors)
    _check_keys(lim, _LIMIT_KEYS, "limits.", errors)
    d = Limits()
    mfb, dwd, fz = d.max_file_bytes, d.dup_window_days, d.fuzzy_threshold
    if "max_file_bytes" in lim:
        v = lim["max_file_bytes"]
        if not _is_int(v):
            errors.add("limits.max_file_bytes", f"expected an integer, got {v!r}")
        elif v <= 0:
            errors.add("limits.max_file_bytes", f"must be > 0, got {v}")
        else:
            mfb = v
    if "dup_window_days" in lim:
        v = lim["dup_window_days"]
        if not _is_int(v):
            errors.add("limits.dup_window_days", f"expected an integer, got {v!r}")
        elif v < 0:
            errors.add("limits.dup_window_days", f"must be >= 0, got {v}")
        else:
            dwd = v
    if "fuzzy_threshold" in lim:
        v = lim["fuzzy_threshold"]
        if not _is_num(v):
            errors.add("limits.fuzzy_threshold", f"expected a number, got {v!r}")
        elif not (0 < v <= 1):
            errors.add("limits.fuzzy_threshold", f"must be in (0, 1], got {v}")
        else:
            fz = float(v)

    # [owners]
    own = _get_table(data, "owners", errors)
    _check_keys(own, _OWNER_KEYS, "owners.", errors)
    att = True
    if "attendee_disambiguation" in own:
        v = own["attendee_disambiguation"]
        if not isinstance(v, bool):
            errors.add("owners.attendee_disambiguation", f"expected a boolean, got {v!r}")
        else:
            att = v

    # [gmail]
    gm = _get_table(data, "gmail", errors)
    _check_keys(gm, _GMAIL_KEYS, "gmail.", errors)
    channel = "handoff"
    if "channel" in gm:
        v = gm["channel"]
        if v not in ("handoff", "google", "fake") or not isinstance(v, str):
            errors.add("gmail.channel", f'invalid value {v!r} (expected "handoff", "google" or "fake")')
        else:
            channel = v
    handoff = _abs(DEFAULT_HANDOFF_DIR, base)
    if "handoff_dir" in gm:
        v = gm["handoff_dir"]
        if not isinstance(v, str) or not v.strip():
            errors.add("gmail.handoff_dir", f"expected a non-empty string, got {v!r}")
        else:
            handoff = _abs(v.strip(), base)
    url = DEFAULT_GOOGLE_MCP_URL
    if "google_mcp_url" in gm:
        v = gm["google_mcp_url"]
        if not isinstance(v, str):
            errors.add("gmail.google_mcp_url", f"expected a string, got {v!r}")
        else:
            problem = _google_url_problem(v)
            if problem:
                errors.add("gmail.google_mcp_url", f'"{v}": {problem}')
            else:
                url = v
    sig, verify = False, True
    for key in ("include_signature", "verify_after_create"):
        if key in gm:
            v = gm[key]
            if not isinstance(v, bool):
                errors.add(f"gmail.{key}", f"expected a boolean, got {v!r}")
            elif key == "include_signature":
                sig = v
            else:
                verify = v
    timeout: float = 30
    if "timeout_seconds" in gm:
        v = gm["timeout_seconds"]
        if not _is_num(v):
            errors.add("gmail.timeout_seconds", f"expected a number, got {v!r}")
        elif v <= 0:
            errors.add("gmail.timeout_seconds", f"must be > 0, got {v}")
        else:
            timeout = v
    client_id = ""
    if "oauth_client_id" in gm:
        v = gm["oauth_client_id"]
        if not isinstance(v, str):
            errors.add("gmail.oauth_client_id", f"expected a string, got {v!r}")
        else:
            client_id = v.strip()

    roster = _validate_roster(data.get("person"), errors)
    errors.raise_if_any()
    return Config(
        source=source,
        timezone=tz,
        date_order=date_order or None,
        limits=Limits(mfb, dwd, fz),
        owners=Owners(att),
        gmail=GmailConfig(channel, handoff, url, sig, timeout, verify, client_id),
        roster=roster,
    )


def load_config(
    config_path: str | os.PathLike[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> Config:
    environ = os.environ if env is None else env
    base = Path(cwd) if cwd is not None else Path.cwd()
    explicit = config_path if config_path is not None else (environ.get(ENV_CONFIG) or None)
    if explicit is not None:
        path = Path(_abs(os.fspath(explicit), base))
        if not path.is_file():
            raise EnvError(
                f"config file not found or not a file: {path}",
                where="--config" if config_path is not None else ENV_CONFIG,
                next_step="check the path or remove the option",
            )
    else:
        path = base / DEFAULT_CONFIG_NAME
        if not path.is_file():
            return default_config(base)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        # utf-8-sig: Windows Notepad saves a BOM; accept it (a BOM is not part of the TOML text).
        data = tomllib.loads(raw.decode("utf-8-sig"))
    except tomllib.TOMLDecodeError as e:
        raise EnvError(
            f"config {path}: invalid TOML: {e}",
            next_step="fix the syntax error and re-run",
        ) from None
    except UnicodeDecodeError as e:
        raise EnvError(
            f"config {path}: not valid UTF-8 ({e.reason} at byte {e.start})",
            next_step="save the file as UTF-8",
        ) from None
    except OSError as e:
        raise EnvError(
            f"config {path}: cannot read file: {e.strerror or e}",
            next_step="check the file permissions",
        ) from None
    return parse_config(data, source=path, cwd=base)
