"""Tests for clock.py (T-M1-03): AC-09.3, AC-09.4 (timestamps half), SC-37, EC-17."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta
from unittest import mock

import pytest

from actiontrack import clock as clock_mod
from actiontrack.clock import (
    FALLBACK_TZ_HINT,
    FixedClock,
    SystemClock,
    build_run_context,
    clock_from_env,
    parse_now,
    parse_today,
    resolve_timezone,
)
from actiontrack.errors import EnvError, ExitCode, UsageError


def z(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@pytest.mark.ac("AC-09.3")
def test_today_kolkata_at_20z():
    c = FixedClock(z("2026-10-21T20:00:00Z"))
    assert c.today(resolve_timezone("Asia/Kolkata")) == date(2026, 10, 22)
    assert c.today(resolve_timezone("America/Los_Angeles")) == date(2026, 10, 21)


@pytest.mark.ac("AC-09.3")
@pytest.mark.sc("SC-37")
def test_today_los_angeles_at_03z():
    c = FixedClock(z("2026-10-22T03:00:00Z"))
    assert c.today(resolve_timezone("America/Los_Angeles")) == date(2026, 10, 21)


@pytest.mark.ac("AC-09.3")
def test_today_override_flag_beats_env_beats_clock():
    clk = FixedClock(z("2026-10-21T20:00:00Z"))
    env = {"ACTIONTRACK_TODAY": "2026-01-02"}
    ctx = build_run_context(timezone_name="Asia/Kolkata", today_flag="2026-03-04", clock=clk, env=env)
    assert ctx.today == date(2026, 3, 4)
    ctx = build_run_context(timezone_name="Asia/Kolkata", clock=clk, env=env)
    assert ctx.today == date(2026, 1, 2)
    ctx = build_run_context(timezone_name="Asia/Kolkata", clock=clk, env={})
    assert ctx.today == date(2026, 10, 22)
    assert ctx.tz_label == "Asia/Kolkata"
    # the override sets the date only; timestamps still come from the clock
    assert ctx.clock.now_utc() == z("2026-10-21T20:00:00Z")


@pytest.mark.parametrize("bad", ["2026-02-30", "22-10-2026", "today", "", "2026-1-2", "20261022"])
def test_today_override_invalid(bad):
    with pytest.raises(UsageError) as ei:
        parse_today(bad)
    assert ei.value.exit_code == ExitCode.USAGE
    assert f'"{bad}"' in str(ei.value) and "YYYY-MM-DD" in str(ei.value)


def test_today_env_invalid_names_variable():
    with pytest.raises(UsageError) as ei:
        build_run_context(
            timezone_name="UTC", env={"ACTIONTRACK_TODAY": "nope"}, clock=SystemClock()
        )
    assert "ACTIONTRACK_TODAY" in str(ei.value)


def test_unknown_timezone_exit4_names_key():
    with pytest.raises(EnvError) as ei:
        resolve_timezone("Mars/Olympus")
    assert ei.value.exit_code == ExitCode.ENVIRONMENT
    assert "timezone" in str(ei.value) and "Mars/Olympus" in str(ei.value)


def test_missing_tzdata_gives_remediation():
    with (
        mock.patch.object(clock_mod, "ZoneInfo", side_effect=clock_mod.ZoneInfoNotFoundError("x")),
        mock.patch.object(clock_mod.importlib.util, "find_spec", return_value=None),
        mock.patch.object(clock_mod.sys, "platform", "win32"),
    ):
        with pytest.raises(EnvError) as ei:
            resolve_timezone("Asia/Kolkata")
    assert "pip install tzdata" in str(ei.value)


@pytest.mark.ac("AC-09.3")
@pytest.mark.parametrize(
    ("instant", "zone", "expected"),
    [
        ("2026-11-01T08:30:00Z", "America/Los_Angeles", date(2026, 11, 1)),  # PDT->PST day
        ("2026-11-01T07:59:00Z", "America/Los_Angeles", date(2026, 11, 1)),
        ("2026-03-08T10:30:00Z", "America/Los_Angeles", date(2026, 3, 8)),  # spring forward
        ("2026-03-08T07:59:00Z", "America/Los_Angeles", date(2026, 3, 7)),
    ],
)
def test_dst_boundary_dates(instant, zone, expected):
    assert FixedClock(z(instant)).today(resolve_timezone(zone)) == expected


@pytest.mark.ac("AC-09.3")
@pytest.mark.parametrize(
    ("offset", "label", "today"),
    [
        (timedelta(hours=5, minutes=30), "UTC+05:30", date(2026, 10, 22)),
        (timedelta(hours=-8), "UTC-08:00", date(2026, 10, 21)),
        (timedelta(0), "UTC", date(2026, 10, 21)),
    ],
)
def test_no_timezone_uses_fixed_offset_label(offset, label, today):
    clk = FixedClock(z("2026-10-21T20:00:00Z"))
    err = io.StringIO()
    with mock.patch.object(clock_mod, "ZoneInfo", side_effect=AssertionError("IANA lookup")) as zi:
        ctx = build_run_context(
            timezone_name=None,
            clock=clk,
            env={},
            os_offset_provider=lambda: offset,
            stderr=err,
        )
    zi.assert_not_called()
    assert ctx.tz_label == label
    assert ctx.today == today
    assert err.getvalue() == FALLBACK_TZ_HINT + "\n"
    assert err.getvalue().count("\n") == 1 and "timezone" in err.getvalue()


def test_fallback_hint_suppressed_by_quiet_and_absent_with_iana():
    clk = FixedClock(z("2026-10-21T20:00:00Z"))
    err = io.StringIO()
    build_run_context(clock=clk, env={}, os_offset_provider=lambda: timedelta(0), quiet=True, stderr=err)
    build_run_context(timezone_name="Asia/Kolkata", clock=clk, env={}, stderr=err)
    assert err.getvalue() == ""


def test_negative_half_hour_offset_label():
    assert resolve_timezone(None, os_offset_provider=lambda: timedelta(hours=-3, minutes=-30)).label == "UTC-03:30"


@pytest.mark.ac("AC-09.4")
def test_actiontrack_now_fixes_stored_instant():
    clk = clock_from_env({"ACTIONTRACK_NOW": "2026-10-22T09:30:00Z"})
    assert clk.now_utc() == z("2026-10-22T09:30:00Z")
    assert clk.now_utc() == clk.now_utc()
    assert clk.now_utc().utcoffset() == timedelta(0) and clk.now_utc().tzinfo is UTC


def test_actiontrack_now_derives_date_in_zone():
    ctx = build_run_context(timezone_name="Asia/Kolkata", env={"ACTIONTRACK_NOW": "2026-10-21T20:00:00Z"})
    assert ctx.today == date(2026, 10, 22)
    # --today wins for the date; NOW still fixes timestamps
    ctx = build_run_context(
        timezone_name="Asia/Kolkata", today_flag="2026-01-01", env={"ACTIONTRACK_NOW": "2026-10-21T20:00:00Z"}
    )
    assert ctx.today == date(2026, 1, 1)
    assert ctx.clock.now_utc() == z("2026-10-21T20:00:00Z")


@pytest.mark.parametrize("bad", ["2026-10-22", "yesterday", "2026-10-22T09:30:00", "2026-13-01T00:00:00Z"])
def test_actiontrack_now_invalid_exit2(bad):
    with pytest.raises(UsageError) as ei:
        clock_from_env({"ACTIONTRACK_NOW": bad})
    assert "ACTIONTRACK_NOW" in str(ei.value)
    assert ei.value.exit_code == ExitCode.USAGE


def test_actiontrack_now_offset_converted_to_utc():
    assert parse_now("2026-10-22T15:00:00+05:30") == z("2026-10-22T09:30:00Z")


def test_unset_uses_real_clock():
    clk = clock_from_env({})
    assert isinstance(clk, SystemClock)
    a = clk.now_utc()
    b = clk.now_utc()
    assert b >= a and a.tzinfo is not None


def test_fixed_clock_rejects_naive():
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 1, 1))
