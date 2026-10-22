"""UTF-8 and deterministic newlines on all text I/O (T-M1-06)."""

from __future__ import annotations

import ast
import io as stdio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from actiontrack import io as at_io

REPO = Path(__file__).resolve().parent.parent.parent
SRC = REPO / "src"
NAMES = "Zoë Åström 李雷"


def _run_child(code: str, *args: str, encoding_env: str = "cp1252") -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = encoding_env
    env.pop("PYTHONUTF8", None)
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, env=env, timeout=60)


_ECHO = (
    "import sys\n"
    "from actiontrack import io\n"
    "io.configure_streams()\n"
    "io.emit(sys.argv[1])\n"
    "io.diag(sys.argv[1])\n"
)


@pytest.mark.ac("AC-09.6")
def test_unicode_stdout_roundtrip_in_subprocess():
    res = _run_child(_ECHO, NAMES)
    assert res.returncode == 0, res.stderr
    assert res.stdout == (NAMES + "\n").encode("utf-8")
    assert res.stderr == (NAMES + "\n").encode("utf-8")


@pytest.mark.ac("AC-09.4")
def test_stdout_uses_lf_not_crlf():
    code = "from actiontrack import io\nio.configure_streams()\nio.emit('a\\nb')\nio.diag('c\\r\\nd')\n"
    res = _run_child(code)
    assert res.returncode == 0, res.stderr
    assert b"\r" not in res.stdout and res.stdout == b"a\nb\n"
    assert b"\r" not in res.stderr and res.stderr == b"c\nd\n"


def test_unencodable_does_not_crash_in_subprocess():
    code = "from actiontrack import io\nio.configure_streams()\nio.emit('x\\udc80y')\nio.diag('x\\udc80y')\n"
    res = _run_child(code)
    assert res.returncode == 0, res.stderr
    assert res.stdout == b"x\\udc80y\n"


def test_unencodable_does_not_crash_in_process(capsys):
    at_io.emit("a\udc80b")
    at_io.diag("a\udc80b")
    cap = capsys.readouterr()
    assert cap.out == "a\\udc80b\n" and cap.err == "a\\udc80b\n"


def test_emit_diag_strip_control_characters(capsys):
    at_io.emit("ok \x1b[31mred\x1b[0m\r\nnext\x7f")
    at_io.diag("\x9bwarn")
    cap = capsys.readouterr()
    assert "\x1b" not in cap.out and "\x7f" not in cap.out and cap.out == "ok [31mred[0m\nnext\n"
    assert cap.err == "warn\n"


def test_emit_goes_to_stdout_and_diag_to_stderr(capsys):
    at_io.emit("data")
    at_io.diag("note")
    cap = capsys.readouterr()
    assert cap.out == "data\n" and cap.err == "note\n"


def test_configure_streams_reconfigures_real_wrappers():
    raw_out, raw_err, raw_in = stdio.BytesIO(), stdio.BytesIO(), stdio.BytesIO("é".encode("utf-8"))
    out = stdio.TextIOWrapper(raw_out, encoding="cp1252", newline="\r\n", write_through=True)
    err = stdio.TextIOWrapper(raw_err, encoding="cp1252", newline="\r\n", write_through=True)
    inp = stdio.TextIOWrapper(raw_in, encoding="cp1252")
    at_io.configure_streams(inp, out, err)
    out.write("é李\n")
    out.flush()
    assert raw_out.getvalue() == "é李\n".encode("utf-8")
    assert inp.read() == "é"
    assert out.errors == "backslashreplace" and inp.errors == "strict"


def test_configure_streams_tolerates_non_reconfigurable():
    at_io.configure_streams(object(), stdio.StringIO(), stdio.StringIO())  # must not raise


def test_text_helpers_utf8_lf(tmp_path):
    p = tmp_path / "f.txt"
    at_io.write_text_utf8(p, "Zoë\n李雷\n")
    assert p.read_bytes() == "Zoë\n李雷\n".encode("utf-8")
    at_io.write_text_utf8(p, "more\n", append=True)
    assert at_io.read_text_utf8(p) == "Zoë\n李雷\nmore\n"
    assert b"\r" not in p.read_bytes()


def test_read_does_not_translate_newlines(tmp_path):
    p = tmp_path / "crlf.txt"
    p.write_bytes(b"a\r\nb\r\n")
    assert at_io.read_text_utf8(p) == "a\r\nb\r\n"


def test_write_private_file(tmp_path):
    p = tmp_path / "h.json"
    at_io.write_private_file(p, "Zoë\n{}\n")
    assert p.read_bytes() == "Zoë\n{}\n".encode("utf-8")
    if os.name != "nt":
        assert (p.stat().st_mode & 0o077) == 0
    at_io.write_private_file(p, "x")
    assert p.read_bytes() == b"x"


# ---------------------------------------------------------------- repository lint
def _is_binary_mode(call: ast.Call) -> bool:
    mode = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and "b" in mode


def _is_pathlib_style_open(call: ast.Call) -> bool:
    """Path.open(mode) takes mode as the first positional argument."""
    mode = call.args[0] if call.args else None
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value


def test_no_open_without_encoding():
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "open":
                kind = "open"
            elif isinstance(fn, ast.Attribute) and fn.attr in {"open", "read_text", "write_text", "fdopen"}:
                if isinstance(fn.value, ast.Name) and fn.value.id == "os" and fn.attr == "open":
                    continue  # os.open is the low-level fd call, not a text open
                kind = fn.attr
            else:
                continue
            if any(kw.arg == "encoding" for kw in node.keywords):
                continue
            if kind in {"open", "fdopen"} and _is_binary_mode(node):
                continue
            if kind == "open" and isinstance(fn, ast.Attribute) and _is_pathlib_style_open(node):
                continue
            offenders.append(f"{path.relative_to(REPO)}:{node.lineno} {kind}(...)")
    assert not offenders, "text I/O without explicit encoding=:\n" + "\n".join(offenders)


def test_lint_detects_a_missing_encoding(tmp_path):
    """The lint's own predicate flags a bare open() (guards against a vacuous lint)."""
    call = ast.parse("open('x')").body[0].value
    assert not any(kw.arg == "encoding" for kw in call.keywords) and not _is_binary_mode(call)
    assert _is_binary_mode(ast.parse("open('x', 'rb')").body[0].value)
