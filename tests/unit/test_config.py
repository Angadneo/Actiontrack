"""Tests for config.py non-roster keys and gmail/allowlist.py (T-M1-04)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from actiontrack.config import Config, load_config
from actiontrack.errors import EnvError, ExitCode
from actiontrack.gmail import allowlist

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "config"


def write(tmp_path: Path, text: str, name: str = "actiontrack.toml") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def err_of(path: Path, **kw) -> EnvError:
    with pytest.raises(EnvError) as ei:
        load_config(path, **kw)
    assert ei.value.exit_code == ExitCode.ENVIRONMENT
    return ei.value


@pytest.mark.ac("AC-09.1")
def test_unknown_key_exit4_names_key(tmp_path):
    e = err_of(FIX / "bad_unknown_key.toml")
    msg = str(e)
    assert 'key "limits.max_file_byte": unknown key (did you mean "max_file_bytes"?)' in msg
    assert "bad_unknown_key.toml" in msg
    # top-level and other nested tables
    p = write(tmp_path, 'bogus = 1\n[gmail]\nnope = 2\n[owners]\nx = 1\n')
    msg = str(err_of(p))
    for k in ('"bogus"', '"gmail.nope"', '"owners.x"'):
        assert f"key {k}: unknown key" in msg


def test_client_secret_key_rejected(tmp_path):
    p = write(tmp_path, '[gmail]\noauth_client_secret = "x"\n')
    assert 'key "gmail.oauth_client_secret": unknown key' in str(err_of(p))


def test_wrong_type_exit4():
    msg = str(err_of(FIX / "bad_types.toml"))
    assert 'key "limits.max_file_bytes"' in msg and "integer" in msg


def test_fuzzy_threshold_out_of_range(tmp_path):
    for bad in ("0", "1.5", "-0.1", '"high"', "true"):
        p = write(tmp_path, f"[limits]\nfuzzy_threshold = {bad}\n")
        assert 'key "limits.fuzzy_threshold"' in str(err_of(p))
    assert load_config(write(tmp_path, "[limits]\nfuzzy_threshold = 1\n")).limits.fuzzy_threshold == 1.0


def test_numeric_bounds(tmp_path):
    assert 'key "limits.max_file_bytes"' in str(err_of(write(tmp_path, "[limits]\nmax_file_bytes = 0\n")))
    assert 'key "limits.dup_window_days"' in str(err_of(write(tmp_path, "[limits]\ndup_window_days = -1\n")))
    assert load_config(write(tmp_path, "[limits]\ndup_window_days = 0\n")).limits.dup_window_days == 0
    assert 'key "limits.max_file_bytes"' in str(err_of(write(tmp_path, "[limits]\nmax_file_bytes = true\n")))
    assert 'key "gmail.timeout_seconds"' in str(err_of(write(tmp_path, "[gmail]\ntimeout_seconds = 0\n")))


def test_date_order_invalid_value(tmp_path):
    assert 'key "date_order"' in str(err_of(write(tmp_path, 'date_order = "ymd"\n')))
    assert load_config(write(tmp_path, 'date_order = "dmy"\n')).date_order == "dmy"
    assert load_config(write(tmp_path, 'date_order = "mdy"\n')).date_order == "mdy"
    assert load_config(write(tmp_path, 'date_order = ""\n')).date_order is None
    assert load_config(write(tmp_path, "")).date_order is None


@pytest.mark.ac("AC-09.1")
def test_defaults_when_no_file(tmp_path):
    cfg = load_config(cwd=tmp_path)
    assert isinstance(cfg, Config) and cfg.source is None
    assert cfg.timezone is None and cfg.date_order is None
    assert (cfg.limits.max_file_bytes, cfg.limits.dup_window_days, cfg.limits.fuzzy_threshold) == (
        5242880,
        60,
        0.85,
    )
    assert cfg.owners.attendee_disambiguation is True
    g = cfg.gmail
    assert (g.channel, g.include_signature, g.timeout_seconds, g.verify_after_create) == ("handoff", False, 30, True)
    assert g.google_mcp_url == "https://gmailmcp.googleapis.com/mcp/v1"
    assert len(cfg.roster) == 0


def test_default_file_in_cwd_is_picked_up(tmp_path):
    write(tmp_path, 'date_order = "mdy"\n')
    cfg = load_config(cwd=tmp_path)
    assert cfg.date_order == "mdy" and cfg.source == tmp_path / "actiontrack.toml"


def test_discovery_order_flag_env_cwd(tmp_path):
    write(tmp_path, 'date_order = "mdy"\n')
    env_file = write(tmp_path, 'date_order = "dmy"\n', "env.toml")
    flag_file = write(tmp_path, 'timezone = "Asia/Kolkata"\n', "flag.toml")
    env = {"ACTIONTRACK_CONFIG": str(env_file)}
    assert load_config(flag_file, env=env, cwd=tmp_path).timezone == "Asia/Kolkata"
    assert load_config(env=env, cwd=tmp_path).date_order == "dmy"
    assert load_config(env={}, cwd=tmp_path).date_order == "mdy"


@pytest.mark.ac("AC-09.1")
def test_appendix_c_example_loads():
    cfg = load_config(FIX / "valid_full.toml")
    assert cfg.timezone == "Asia/Kolkata"
    assert cfg.limits.fuzzy_threshold == 0.85
    assert cfg.gmail.channel == "handoff"
    assert cfg.gmail.oauth_client_id == ""
    assert len(cfg.roster) == 9
    assert cfg.resolve_zone().label == "Asia/Kolkata"


@pytest.mark.ac("AC-09.10")
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/mcp",
        "http://gmailmcp.googleapis.com/mcp/v1",
        "https://gmailmcp.googleapis.com.evil.example/",
        "https://gmailmcp.googleapis.com@evil.example/",
        "https://user:pw@gmailmcp.googleapis.com/mcp/v1",
        "https://gmailmcp.googleapis.com:8443/mcp/v1",
        "https://GMAILMCP.googleapis.com.evil/",
        "gmailmcp.googleapis.com/mcp/v1",
        "",
    ],
)
def test_google_url_wrong_host_rejected(tmp_path, url):
    p = write(tmp_path, f'[gmail]\ngoogle_mcp_url = "{url}"\n')
    e = err_of(p)
    assert e.exit_code == ExitCode.ENVIRONMENT
    assert "google_mcp_url" in str(e)


@pytest.mark.ac("AC-09.10")
def test_google_url_default_and_absent_ok(tmp_path):
    p = write(tmp_path, '[gmail]\ngoogle_mcp_url = "https://gmailmcp.googleapis.com/mcp/v1"\n')
    assert load_config(p).gmail.google_mcp_url == "https://gmailmcp.googleapis.com/mcp/v1"
    assert load_config(write(tmp_path, "[gmail]\n", "b.toml")).gmail.google_mcp_url.startswith("https://")
    assert "google_mcp_url" in str(err_of(FIX / "bad_google_host.toml"))


@pytest.mark.ac("AC-09.10")
def test_allowed_host_and_tools_are_code_constants():
    assert allowlist.ALLOWED_MCP_HOST == "gmailmcp.googleapis.com"
    assert allowlist.ALLOWED_TOOLS == frozenset({"create_draft", "get_draft"})
    assert isinstance(allowlist.ALLOWED_TOOLS, frozenset)


@pytest.mark.ac("AC-09.10")
def test_config_cannot_change_allowlist(tmp_path):
    for key in ("allowed_host", "allowed_tools", "allowed_mcp_host"):
        p = write(tmp_path, f'[gmail]\n{key} = "send_message"\n')
        assert f'key "gmail.{key}": unknown key' in str(err_of(p))
    assert allowlist.ALLOWED_TOOLS == frozenset({"create_draft", "get_draft"})


@pytest.mark.ac("AC-05.8")
def test_attendee_disambiguation_key_parsed(tmp_path):
    assert load_config(write(tmp_path, "[owners]\nattendee_disambiguation = false\n")).owners.attendee_disambiguation is False
    assert load_config(write(tmp_path, "", "e.toml")).owners.attendee_disambiguation is True
    p = write(tmp_path, '[owners]\nattendee_disambiguation = "false"\n', "s.toml")
    assert 'key "owners.attendee_disambiguation"' in str(err_of(p))


def test_handoff_dir_default_and_relative_resolution(tmp_path):
    cfg = load_config(cwd=tmp_path)
    assert cfg.gmail.handoff_dir == tmp_path / ".actiontrack" / "handoff"
    assert cfg.gmail.handoff_dir.is_absolute()
    p = write(tmp_path, '[gmail]\nhandoff_dir = "out/h"\n')
    assert load_config(p, cwd=tmp_path).gmail.handoff_dir == tmp_path / "out" / "h"
    abs_dir = tmp_path / "abs"
    p = write(tmp_path, f'[gmail]\nhandoff_dir = "{abs_dir.as_posix()}"\n', "a.toml")
    assert load_config(p, cwd=tmp_path).gmail.handoff_dir == abs_dir


def test_channel_validation(tmp_path):
    for ch in ("handoff", "google", "fake"):
        assert load_config(write(tmp_path, f'[gmail]\nchannel = "{ch}"\n')).gmail.channel == ch
    assert 'key "gmail.channel"' in str(err_of(write(tmp_path, '[gmail]\nchannel = "smtp"\n')))


@pytest.mark.ac("AC-09.1")
def test_explicit_missing_config_exit4(tmp_path):
    e = err_of(tmp_path / "nope.toml")
    assert "nope.toml" in str(e)
    with pytest.raises(EnvError):
        load_config(env={"ACTIONTRACK_CONFIG": str(tmp_path / "nope2.toml")}, cwd=tmp_path)


def test_toml_syntax_error_reports_line():
    e = err_of(FIX / "bad_syntax.toml")
    assert "bad_syntax.toml" in str(e) and "line" in str(e)


def test_non_utf8_file_exit4(tmp_path):
    p = tmp_path / "latin.toml"
    p.write_bytes(b'timezone = "\xe9"\n')
    assert "UTF-8" in str(err_of(p))


def test_multiple_errors_reported_together():
    msg = str(err_of(FIX / "bad_types.toml"))
    lines = [ln for ln in msg.splitlines() if ln.startswith("config ")]
    assert len(lines) == 3
    assert any("max_file_bytes" in x for x in lines)
    assert any("fuzzy_threshold" in x for x in lines)
    assert any("attendee_disambiguation" in x for x in lines)


def test_unknown_timezone_checked_at_load(tmp_path):
    p = write(tmp_path, 'timezone = "Mars/Olympus"\n')
    e = err_of(p)
    assert 'key "timezone"' in str(e) and "Mars/Olympus" in str(e)
    assert load_config(write(tmp_path, 'timezone = "Asia/Kolkata"\n', "ok.toml")).timezone == "Asia/Kolkata"
    assert load_config(write(tmp_path, 'timezone = ""\n', "blank.toml")).timezone is None


def test_fixture_copy_roundtrip(tmp_path):
    # config loads the same from any location (no hidden dependency on the repo path)
    dest = tmp_path / "copy.toml"
    shutil.copy(FIX / "valid_full.toml", dest)
    assert len(load_config(dest).roster) == 9
