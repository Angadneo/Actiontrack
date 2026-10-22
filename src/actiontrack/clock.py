"""Clock, ``--today`` override and time-zone resolution (spec BR-02, BR-18, FR-09.1, 4.1; T-M1-03).

All timestamps and the "current date" of a run come through this module; nothing else may
call ``datetime.now()``.

Public API
----------
``Clock``                     Protocol: ``now_utc() -> datetime`` (aware, UTC) and
                              ``today(tz) -> date`` (calendar date of ``now_utc()`` in ``tz``;
                              ``tz`` is a ``tzinfo`` or a ``ResolvedZone``).
``SystemClock``               real clock (``datetime.now(timezone.utc)``).
``FixedClock(instant_utc)``   always returns that instant (naive input is rejected; non-UTC
                              aware input is converted to UTC). Used by tests and by
                              ``ACTIONTRACK_NOW``.
``ResolvedZone``              frozen dataclass ``(label, tzinfo, is_fallback)``. ``label`` is the
                              IANA name (``Asia/Kolkata``) or the fixed-offset label
                              (``UTC+05:30``, ``UTC-08:00``, ``UTC``).
``resolve_timezone(name, *, os_offset_provider=None, key="timezone") -> ResolvedZone``
                              ``name`` set  -> ``zoneinfo.ZoneInfo`` (unknown name or missing
                              tzdata -> ``EnvError`` exit 4 naming the key ``timezone``).
                              ``name`` None/blank -> fixed-offset label from the OS current
                              UTC offset (G-10); NO IANA lookup on any platform.
``default_os_offset() -> timedelta``   default ``os_offset_provider``.
``offset_label(offset) -> str``        ``UTC+05:30`` style label.
``FALLBACK_TZ_HINT``          the single stderr line printed on the fallback.
``parse_today(value, *, source="--today") -> date``
                              strict ``YYYY-MM-DD`` else ``UsageError`` (exit 2, names the value).
``parse_now(value) -> datetime``
                              strict UTC ISO-8601 instant else ``UsageError`` (exit 2, names
                              ``ACTIONTRACK_NOW``).
``clock_from_env(env=None) -> Clock``
                              ``FixedClock`` if ``ACTIONTRACK_NOW`` is set, else ``SystemClock``.
``RunContext``                frozen dataclass ``(clock, today, tz_label, zone)``; the one object
                              commands read the date/zone from ("As of {today} (tz {tz_label})").
``build_run_context(*, timezone_name=None, today_flag=None, clock=None, env=None,
                    os_offset_provider=None, quiet=False, stderr=None) -> RunContext``
                              Evaluated ONCE per invocation (BR-02). Date precedence:
                              ``--today`` flag > ``ACTIONTRACK_TODAY`` > date of the clock
                              instant (``ACTIONTRACK_NOW`` when set) in the zone. A
                              ``--today``/``ACTIONTRACK_TODAY`` override sets the calendar date
                              only; timestamps still come from ``clock``. On the fixed-offset
                              fallback exactly one hint line is written to ``stderr`` (default
                              ``io.diag``, the diagnostics writer) unless ``quiet``.

Env vars: ``ACTIONTRACK_NOW`` (test-only), ``ACTIONTRACK_TODAY``.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Protocol, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from actiontrack import io
from actiontrack.errors import EnvError, UsageError

ENV_NOW = "ACTIONTRACK_NOW"
ENV_TODAY = "ACTIONTRACK_TODAY"

FALLBACK_TZ_HINT = (
    'note: no "timezone" configured; using the operating system UTC offset '
    '(set e.g. timezone = "Asia/Kolkata" in actiontrack.toml for a stable zone).'
)


@dataclass(frozen=True)
class ResolvedZone:
    label: str
    tzinfo: tzinfo
    is_fallback: bool = False


def _tzinfo_of(tz: tzinfo | ResolvedZone) -> tzinfo:
    return tz.tzinfo if isinstance(tz, ResolvedZone) else tz


class Clock(Protocol):
    def now_utc(self) -> datetime: ...

    def today(self, tz: tzinfo | ResolvedZone) -> date: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def today(self, tz: tzinfo | ResolvedZone) -> date:
        return self.now_utc().astimezone(_tzinfo_of(tz)).date()


class FixedClock:
    def __init__(self, instant_utc: datetime) -> None:
        if instant_utc.tzinfo is None or instant_utc.utcoffset() is None:
            raise ValueError("FixedClock requires a timezone-aware instant")
        self._instant = instant_utc.astimezone(UTC)

    def now_utc(self) -> datetime:
        return self._instant

    def today(self, tz: tzinfo | ResolvedZone) -> date:
        return self._instant.astimezone(_tzinfo_of(tz)).date()


def default_os_offset() -> timedelta:
    offset = datetime.now().astimezone().utcoffset()
    return offset if offset is not None else timedelta(0)


def offset_label(offset: timedelta) -> str:
    total_minutes = round(offset.total_seconds() / 60)
    if total_minutes == 0:
        return "UTC"
    sign = "+" if total_minutes > 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def resolve_timezone(
    name: str | None,
    *,
    os_offset_provider: Callable[[], timedelta] | None = None,
    key: str = "timezone",
) -> ResolvedZone:
    if name is None or not name.strip():
        provider = os_offset_provider or default_os_offset
        offset = timedelta(minutes=round(provider().total_seconds() / 60))
        return ResolvedZone(offset_label(offset), timezone(offset), True)
    name = name.strip()
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        if importlib.util.find_spec("tzdata") is None and sys.platform == "win32":
            raise EnvError(
                f'cannot load time zone "{name}": the tz database is not installed',
                where=f'key "{key}"',
                next_step="pip install tzdata",
            ) from None
        raise EnvError(
            f'unknown time zone "{name}" (expected an IANA name such as "Asia/Kolkata")',
            where=f'key "{key}"',
            next_step=f'fix "{key}" in the config file',
        ) from None
    return ResolvedZone(name, zone, False)


def parse_today(value: str, *, source: str = "--today") -> date:
    text = value.strip() if isinstance(value, str) else value
    if isinstance(text, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    raise UsageError(
        f'invalid date "{value}": expected a real calendar date as YYYY-MM-DD',
        where=source,
    )


_NOW_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})")


def parse_now(value: str) -> datetime:
    text = value.strip() if isinstance(value, str) else ""
    if _NOW_RE.fullmatch(text):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    raise UsageError(
        f'invalid {ENV_NOW} value "{value}": expected a UTC ISO-8601 instant '
        "such as 2026-10-22T09:30:00Z",
        where=ENV_NOW,
    )


def clock_from_env(env: Mapping[str, str] | None = None) -> Clock:
    environ = os.environ if env is None else env
    raw = environ.get(ENV_NOW)
    if raw is None or raw == "":
        return SystemClock()
    return FixedClock(parse_now(raw))


@dataclass(frozen=True)
class RunContext:
    clock: Clock
    today: date
    tz_label: str
    zone: ResolvedZone


def build_run_context(
    *,
    timezone_name: str | None = None,
    today_flag: str | None = None,
    clock: Clock | None = None,
    env: Mapping[str, str] | None = None,
    os_offset_provider: Callable[[], timedelta] | None = None,
    quiet: bool = False,
    stderr: TextIO | None = None,
) -> RunContext:
    environ = os.environ if env is None else env
    the_clock = clock if clock is not None else clock_from_env(environ)
    zone = resolve_timezone(timezone_name, os_offset_provider=os_offset_provider)
    if today_flag is not None:
        today = parse_today(today_flag, source="--today")
    elif environ.get(ENV_TODAY):
        today = parse_today(environ[ENV_TODAY], source=ENV_TODAY)
    else:
        today = the_clock.today(zone)
    if zone.is_fallback and not quiet:
        if stderr is not None:
            stderr.write(FALLBACK_TZ_HINT + "\n")
        else:
            io.diag(FALLBACK_TZ_HINT)
    return RunContext(the_clock, today, zone.label, zone)
