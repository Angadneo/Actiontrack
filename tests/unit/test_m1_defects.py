"""Regression tests for the three defects found by the M1 exit-gate review (2026-10-22)."""

from __future__ import annotations

import pytest

from actiontrack.clock import parse_today
from actiontrack.config import load_config
from actiontrack.errors import EnvError, UsageError
from actiontrack.logging_setup import Diagnostics


@pytest.mark.ac("AC-09.9")
def test_log_file_windows_device_name_is_env_error_not_internal(tmp_path):
    # On Windows "con:" makes open() raise ValueError; it must map to exit 4 (EnvError).
    with pytest.raises(EnvError) as excinfo:
        Diagnostics(log_file="con:")
    assert excinfo.value.exit_code == 4


@pytest.mark.ac("AC-09.1")
def test_config_with_utf8_bom_loads(tmp_path):
    cfg = tmp_path / "actiontrack.toml"
    cfg.write_bytes(b"\xef\xbb\xbf" + 'timezone = "Asia/Kolkata"\n'.encode("utf-8"))
    config = load_config(cfg)
    assert config.timezone == "Asia/Kolkata"


@pytest.mark.ac("AC-09.1")
def test_config_with_bom_and_crlf_loads(tmp_path):
    cfg = tmp_path / "actiontrack.toml"
    cfg.write_bytes(b"\xef\xbb\xbf" + b'timezone = "Asia/Kolkata"\r\ndate_order = "dmy"\r\n')
    assert load_config(cfg).date_order == "dmy"


@pytest.mark.ac("AC-09.3")
def test_invalid_today_message_names_source_once():
    with pytest.raises(UsageError) as excinfo:
        parse_today("2026-13-01", source="--today")
    assert excinfo.value.format().count("--today") == 1
