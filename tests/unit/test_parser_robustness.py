"""T-M2-18: robustness, purity and read-only guard tests for the parsing layer.

hypothesis is not installed, so the "property" tests are deterministic generators driven by a seeded
``random.Random`` (fixed seeds: the whole file is reproducible bit for bit).

Covers AC-01.11 (sources untouched), BR-01, BR-17 (failure isolation), BR-18 (determinism), NFR-PERF-01
(parse share), NFR-TEST-01 (purity), SEC-09, W-LONG-LINE, EC-18/EC-19 (hostile input).
"""

from __future__ import annotations

import ast
import builtins
import datetime
import hashlib
import io
import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.domain.models import ParsedFile, TextSpan, ingest_order_key
from actiontrack.parsing import markdown as md
from actiontrack.parsing import parse_document, parse_file
from actiontrack.parsing import reader as rd
from actiontrack.parsing.discovery import discover
from actiontrack.parsing.items import clean_description, derive_task_text
from tests.conftest import FIXTURES_NOTES, REPO_ROOT

SRC = REPO_ROOT / "src" / "actiontrack"
SAMPLE_NAMES = sorted(p.name for p in FIXTURES_NOTES.glob("*.md"))
UNREADABLE = {"scratch.md", "2026-09-09-notes-final-v2.md"}  # binary + empty

HEADER = b"# Meeting: T\nDate: 2026-10-14\nAttendees: Priya Nair, Lena Fischer\n\n"


def parse(data: bytes | str, name: str = "n.md", **kw) -> ParsedFile:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return parse_document(data, name, **kw)


def best_of(fn, runs: int = 3) -> float:
    """Smallest wall time of ``runs`` calls (shields the bound from scheduler noise)."""
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


# ============================================================================ coherence checker
_NL = re.compile(r"\r\n|\n|\r")


def _lines_of(data: bytes) -> list[str] | None:
    """Independent line split of the decoded file (None when it is not decodable text)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.startswith("﻿"):
        text = text[1:]
    if not text:
        return []
    lines = _NL.split(text)
    if lines[-1] == "":
        lines.pop()
    return lines


_BOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*\[[ xX]?\]")


def assert_coherent(pf: ParsedFile, data: bytes) -> None:
    """Invariants every ``parse_document`` result must satisfy, whatever the input."""
    assert isinstance(pf, ParsedFile)
    assert pf.status in ("ok", "empty", "failed")
    assert pf.content_sha256 == hashlib.sha256(data).hexdigest()
    assert pf.size_bytes == len(data)
    # JSON-safe and deterministic
    json.dumps(pf.to_dict())
    if pf.status == "failed":
        assert pf.failure is not None and pf.failure.code in codes.ERROR_CODES
        # E-INTERNAL-PARSE means an unexpected exception was swallowed: always a parser bug
        assert pf.failure.code != codes.E_INTERNAL_PARSE, pf.failure.traceback
        assert pf.items == () and pf.meeting is None and pf.candidates == ()
        return
    assert pf.failure is None
    if pf.status == "empty":
        assert [w.code for w in pf.warnings] == [codes.W_EMPTY_FILE]
        assert pf.items == () and pf.meeting is None
        return
    lines = _lines_of(data)
    assert lines is not None, "status ok for bytes that are not valid UTF-8"
    n = len(lines)
    assert pf.meeting is not None
    prev = 0
    for it in pf.items:
        assert 1 <= it.line_start <= it.line_end <= n, (it.line_start, it.line_end, n)
        assert it.line_start > prev, "items must be strictly increasing by line_start"
        prev = it.line_start
        assert it.checkbox_state in ("open", "done")
        assert it.description and it.task_text, "empty description/task_text"
        assert it.task_text.strip() == it.task_text
        assert unicodedata.is_normalized("NFC", it.description)
        assert unicodedata.is_normalized("NFC", it.task_text)
        assert len(it.block_hash) == 64 and it.occurrence_index >= 0
        first = lines[it.line_start - 1]
        assert it.raw_text.split("\n")[0] == first
    warn_keys = [(w.line or 0, w.code, w.detail or "") for w in pf.warnings]
    assert warn_keys == sorted(warn_keys)
    for w in pf.warnings:
        assert w.line is None or 1 <= w.line <= n, (w.code, w.line, n)
    for c in pf.candidates:
        assert 1 <= c.line <= n
    assert (codes.W_NO_ITEMS in [w.code for w in pf.warnings]) == (pf.items == ())
    # no item may start inside an ignored region (fence / comment / quote / front matter)
    scan = md.scan_document(rd.split_lines(_NL.sub("\n", "\n".join(lines))), "n.md")
    for it in pf.items:
        info = scan.annotation.lines[it.line_start - 1]
        assert info.kind == "normal", it.line_start
        # visible text (HTML-comment spans blanked) must itself be a done/open checkbox: never `[-]`/`[]`-less
        assert _BOX.match(info.visible_text), ("item from a non-checkbox line", info.visible_text)
    # purity of the result itself: parsing again gives the very same value
    again = parse_document(data, pf.rel_path)
    assert again.to_dict() == pf.to_dict()


# ============================================================================ static purity
PURE_FILES = sorted((SRC / "domain").glob("*.py")) + [
    SRC / "parsing" / "markdown.py",
    SRC / "parsing" / "meeting.py",
    SRC / "parsing" / "items.py",
]
FORBIDDEN_IMPORTS = {"os", "pathlib", "shutil", "socket", "subprocess", "time", "io", "tempfile", "glob"}
YAML_LIBS = {"yaml", "ruamel", "pyyaml", "strictyaml", "oyaml"}


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("path", PURE_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
@pytest.mark.ac("AC-01.11")
def test_static_purity_of_domain_and_parser_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = _imports(tree)
    assert not (imports & FORBIDDEN_IMPORTS), sorted(imports & FORBIDDEN_IMPORTS)
    assert not (imports & YAML_LIBS), "no YAML library (G-22)"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            assert not (isinstance(f, ast.Name) and f.id in {"open", "input", "exec", "eval", "__import__"}), (
                path.name,
                node.lineno,
            )
            if isinstance(f, ast.Attribute):
                chain = ast.unparse(f)
                assert not re.search(
                    r"(date\.today|datetime\.now|datetime\.utcnow|datetime\.today|\.fromtimestamp)$", chain
                ), (path.name, node.lineno, chain)
                assert not chain.startswith(("os.", "time.", "pathlib.", "Path")), (path.name, node.lineno, chain)


@pytest.mark.ac("AC-01.11")
def test_only_the_reader_and_discovery_touch_the_filesystem():
    offenders = []
    for path in sorted((SRC / "parsing").glob("*.py")):
        if path.name in ("reader.py", "discovery.py"):
            continue
        imports = _imports(ast.parse(path.read_text(encoding="utf-8")))
        if imports & {"os", "pathlib", "shutil"}:
            offenders.append(path.name)
    assert offenders == []


@pytest.mark.ac("AC-01.11")
def test_reader_opens_files_read_only_only():
    tree = ast.parse((SRC / "parsing" / "reader.py").read_text(encoding="utf-8"))
    modes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            modes.append(ast.literal_eval(node.args[1]) if len(node.args) > 1 else "r")
    assert modes == ["rb"]


# ============================================================================ ReDoS / performance
def _line(prefix: bytes, unit: bytes, total: int) -> bytes:
    return prefix + unit * ((total - len(prefix)) // len(unit))


PERF_CASES = {
    "checkbox+a x1M": lambda: _line(b"- [ ] ", b"a", 1_000_000),
    "*_ x1M": lambda: _line(b"- [ ] ", b"*_", 1_000_000),
    "[ x100k": lambda: b"- [ ] " + b"[" * 100_000,
    "[ x1M plain line": lambda: b"[" * 1_000_000,
    "<!-- x50k": lambda: b"- [ ] x\n" + b"<!--" * 50_000,
    "<!-- x250k alone": lambda: b"<!--" * 250_000,
    "link opener bomb": lambda: b"- [ ] " + b"[a](b " * 100_000,
    "ref bomb": lambda: b"- [ ] " + b"[a][b" * 100_000,
    "backtick bomb": lambda: b"- [ ] " + b"`a" * 300_000,
    "emphasis bomb": lambda: b"- [ ] " + b"*a" * 300_000,
    "``` bomb": lambda: b"```" * 300_000,
    "unicode line": lambda: b"- [ ] " + "é́".encode() * 250_000,
}


CORE_PERF = {"checkbox+a x1M", "*_ x1M", "[ x100k", "<!-- x50k"}


@pytest.mark.parametrize("case", sorted(PERF_CASES))
@pytest.mark.ac("AC-01.11")
def test_single_huge_line_parses_in_under_a_second(case):
    data = PERF_CASES[case]()
    result: list[ParsedFile] = []
    elapsed = best_of(lambda: result.append(parse(data)))
    pf = result[-1]
    assert pf.status == "ok"
    # the four classes named by the task must beat 1 s; extra hostile shapes get a generous 3 s
    limit = 1.0 if case in CORE_PERF else 3.0
    assert elapsed < limit, f"{case}: {elapsed:.2f}s"
    assert_coherent(pf, data)
    if case in ("checkbox+a x1M", "*_ x1M"):
        assert codes.W_LONG_LINE in [w.code for w in pf.warnings]
        assert len(pf.items) == 1
    if case == "<!-- x50k":
        unclosed = [w for w in pf.warnings if w.code == codes.W_HTML_COMMENT_UNCLOSED]
        assert len(unclosed) == 1


@pytest.mark.parametrize(
    "name,value",
    [("bracket", b"[" * 100_000), ("anchors", b"&a " * 100_000), ("quotes", b'"' * 100_000)],
    ids=["bracket", "anchors", "quotes"],
)
@pytest.mark.ac("AC-01.20")
def test_hostile_front_matter_is_rejected_quickly(name, value):
    data = b"---\ntitle: " + value + b"\n---\n- [ ] Priya to do it\n"
    result: list[ParsedFile] = []
    elapsed = best_of(lambda: result.append(parse(data)))
    pf = result[-1]
    assert elapsed < 1.0, f"{name}: {elapsed:.2f}s"
    assert codes.W_FRONTMATTER_INVALID in [w.code for w in pf.warnings]
    assert [i.line_start for i in pf.items] == [4 - 0] or len(pf.items) == 1  # body item still imported
    assert_coherent(pf, data)


@pytest.mark.ac("AC-01.20")
def test_many_anchor_lines_in_front_matter_stay_linear():
    def run(n):
        data = b"---\n" + b"k: &a\n" * n + b"---\n- [ ] a\n"
        return best_of(lambda: parse(data), runs=2)

    small, big = run(10_000), run(40_000)
    assert big < 4.0
    assert big < small * 12 + 0.2  # ~linear, not quadratic (4x input)


@pytest.mark.ac("AC-01.19")
def test_derive_task_text_with_10000_spans_on_one_million_chars():
    desc = "word " * 200_000
    spans = [TextSpan(i * 100, i * 100 + 10) for i in range(10_000)]
    out: list[str] = []
    elapsed = best_of(lambda: out.append(derive_task_text(desc, spans)))
    assert elapsed < 1.0, elapsed
    assert out[-1] and out[-1][0].isupper()
    assert len(out[-1]) < len(desc)


@pytest.mark.ac("AC-01.11")
def test_thousands_of_checkboxes_scale_linearly():
    def run(n):
        data = HEADER + b"".join(b"- [ ] Priya Nair to do task %d by Friday\n" % i for i in range(n))
        pf = parse(data)
        assert len(pf.items) == n
        return best_of(lambda: parse(data), runs=2), pf, data

    t1, _, _ = run(2_000)
    t2, pf, data = run(8_000)
    assert t2 < 8.0
    assert t2 < t1 * 10 + 0.3  # 4x the input must not cost 10x+
    assert [i.line_start for i in pf.items] == list(range(5, 5 + 8_000))
    assert_coherent(pf, data)


@pytest.mark.ac("AC-01.11")
def test_deeply_nested_lists():
    # 400 levels (up to ~800 columns of indent) and a long staircase repeated: no recursion, no crash
    data = HEADER + b"".join(b"  " * i + b"- [ ] level %d\n" % i for i in range(400))
    pf = parse(data)
    assert len(pf.items) == 400
    assert_coherent(pf, data)
    stair = HEADER + b"".join(b"  " * (i % 12) + b"- [ ] step %d\n" % i for i in range(3000))
    pf2 = parse(stair)
    assert len(pf2.items) == 3000
    assert_coherent(pf2, stair)
    tabs = HEADER + b"".join(b"\t" * i + b"- [ ] tab %d\n" % i for i in range(300))
    assert len(parse(tabs).items) == 300


@pytest.mark.ac("AC-01.11")
def test_parse_all_readable_sample_files_in_under_a_second_total():
    blobs = [(n, (FIXTURES_NOTES / n).read_bytes()) for n in SAMPLE_NAMES if n not in UNREADABLE]
    assert len(blobs) == 33

    def run():
        for n, b in blobs:
            assert parse_document(b, n).status == "ok"

    assert best_of(run, runs=3) < 1.0


# ============================================================================ seeded fuzz
ALPHABET = [
    b"- [ ] ", b"- [x] ", b"* [X] ", b"[]", b"[ ]", b"[-]", b"```", b"~~~", b"<!--", b"-->", b"---", b"...",
    b"> ", b"|", b"\n", b"\r", b"\r\n", b"\t", b"  ", b"#", b"## ", b"Date: ", b"Attendees: ", b"title: ",
    b"&a", b"[", b"]", b"(", b")", b"*", b"_", b"`", b"\xc3\xa9", b"\xe2\x80\x94", b"\xef\xbb\xbf", b"@",
    b"by Friday", b" by 9 Oct", b"1. ", b"+ ", b"\x00", b"\xff", b"\xc3",
]


def mutate(rng: random.Random, data: bytes) -> bytes:
    out = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        op = rng.randrange(7)
        if not out:
            out += rng.choice(ALPHABET)
            continue
        i = rng.randrange(len(out) + 1)
        if op == 0:  # delete a span
            j = min(len(out), i + rng.randint(1, 40))
            del out[i:j]
        elif op == 1:  # insert a token
            out[i:i] = rng.choice(ALPHABET)
        elif op == 2:  # insert random bytes
            out[i:i] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 8)))
        elif op == 3 and len(out) > 2:  # swap two bytes
            j = rng.randrange(len(out))
            i = min(i, len(out) - 1)
            out[i], out[j] = out[j], out[i]
        elif op == 4:  # duplicate a chunk
            j = min(len(out), i + rng.randint(1, 80))
            out[i:i] = out[i:j]
        elif op == 5:  # truncate
            del out[i:]
        else:  # replace a byte with a token
            i = min(i, len(out) - 1)
            out[i : i + 1] = rng.choice(ALPHABET)
    return bytes(out)


@pytest.mark.ac("AC-01.11")
def test_seeded_mutation_fuzz_500_cases_never_breaks_invariants():
    rng = random.Random(0xA17AC7)
    sources = {n: (FIXTURES_NOTES / n).read_bytes() for n in SAMPLE_NAMES}
    names = sorted(sources)
    outcomes = {"ok": 0, "empty": 0, "failed": 0}
    for case in range(500):
        name = names[rng.randrange(len(names))]
        data = mutate(rng, sources[name])
        pf = parse_document(data, name)
        try:
            assert_coherent(pf, data)
        except AssertionError as exc:  # pragma: no cover - failure path only
            raise AssertionError(f"case {case} ({name}, {len(data)} bytes): {exc}\n{data!r}") from exc
        outcomes[pf.status] += 1
    assert outcomes["ok"] > 250  # the generator really exercises the text path
    assert outcomes["failed"] > 0


@pytest.mark.ac("AC-01.11")
def test_fuzz_is_deterministic_for_a_fixed_seed():
    def run():
        rng = random.Random(7)
        base = (FIXTURES_NOTES / "2026-09-30-q4-offsite.md").read_bytes()
        return [parse_document(mutate(rng, base), "n.md").to_dict() for _ in range(60)]

    assert json.dumps(run(), sort_keys=True) == json.dumps(run(), sort_keys=True)


@pytest.mark.ac("AC-01.11")
def test_random_bytes_never_raise():
    rng = random.Random(1234)
    for case in range(300):
        size = rng.choice([0, 1, 2, 3, 7, 64, 500, 4096])
        data = bytes(rng.randrange(256) for _ in range(size))
        pf = parse_document(data, "r.md")
        assert_coherent(pf, data)
        assert pf.status in ("ok", "empty", "failed")


@pytest.mark.ac("AC-01.11")
def test_random_printable_markdown_soup():
    rng = random.Random(99)
    tokens = [t for t in ALPHABET if b"\xff" not in t and b"\x00" not in t and t != b"\xc3"] + [
        b"Priya", b" to ", b"send", b"deck", b"\n", b"\n", b"\n", b"- [ ] x\n", b"- [ ]\n", b"- []y\n",
    ]
    for case in range(300):
        data = b"".join(rng.choice(tokens) for _ in range(rng.randint(1, 120)))
        assert_coherent(parse_document(data, "soup.md"), data)


@pytest.mark.ac("AC-01.11")
def test_truncated_utf8_at_every_byte_of_a_non_ascii_note():
    full = HEADER + "- [ ] Zoë to send the quote — soon é́ 日本語\n".encode()
    saw_encoding_failure = False
    for cut in range(len(full) + 1):
        data = full[:cut]
        pf = parse_document(data, "t.md")
        assert_coherent(pf, data)
        if pf.status == "failed":
            assert pf.failure.code == codes.E_ENCODING
            assert 0 <= pf.failure.byte_offset <= len(data)
            saw_encoding_failure = True
    assert saw_encoding_failure
    assert len(parse_document(full, "t.md").items) == 1


@pytest.mark.ac("AC-01.11")
def test_invalid_utf8_reports_the_byte_offset():
    data = b"- [ ] ok line\n- [ ] bad \xff byte\n"
    pf = parse_document(data, "bad.md")
    assert pf.status == "failed" and pf.failure.code == codes.E_ENCODING
    assert pf.failure.byte_offset == data.index(b"\xff")
    bom = b"\xef\xbb\xbf" + data
    assert parse_document(bom, "bad.md").failure.byte_offset == bom.index(b"\xff")


@pytest.mark.ac("AC-01.11")
def test_line_endings_all_give_the_same_items():
    body = ["# Meeting: T", "Date: 2026-10-14", "Attendees: Priya Nair", "", "- [ ] Priya Nair to a", "- [x] Priya Nair to b", ""]
    results = {}
    for label, eol in (("lf", "\n"), ("crlf", "\r\n"), ("cr", "\r")):
        data = eol.join(body).encode()
        pf = parse_document(data, "e.md")
        assert_coherent(pf, data)
        results[label] = [(i.line_start, i.checkbox_state, i.raw_text) for i in pf.items]
    mixed = "# Meeting: T\r\nDate: 2026-10-14\nAttendees: Priya Nair\r\n\r- [ ] Priya Nair to a\n- [x] Priya Nair to b\r".encode()
    pf = parse_document(mixed, "e.md")
    assert_coherent(pf, mixed)
    results["mixed"] = [(i.line_start, i.checkbox_state, i.raw_text) for i in pf.items]
    assert len({json.dumps(v) for v in results.values()}) == 1, results
    assert results["lf"] == [(5, "open", "- [ ] Priya Nair to a"), (6, "done", "- [x] Priya Nair to b")]


@pytest.mark.ac("AC-01.11")
def test_nul_in_the_middle_is_binary_not_a_crash():
    for pos in (0, 5, 40):
        data = bytearray(HEADER + b"- [ ] Priya to send the deck\n")
        data[pos] = 0
        pf = parse_document(bytes(data), "n.md")
        assert pf.status == "failed" and pf.failure.code == codes.E_BINARY
        assert pf.items == ()


@pytest.mark.ac("AC-01.11")
def test_bom_variants():
    body = HEADER + b"- [ ] Priya Nair to send the deck\n"
    plain = parse_document(body, "b.md")
    bom = parse_document(b"\xef\xbb\xbf" + body, "b.md")
    assert plain.status == bom.status == "ok"
    a, b = plain.to_dict(), bom.to_dict()
    for k in ("content_sha256", "size_bytes"):
        a.pop(k)
        b.pop(k)
    assert a == b  # BOM is not a line and not part of the first heading
    assert parse_document(b"\xef\xbb\xbf", "b.md").status == "empty"
    assert parse_document(b"\xef\xbb\xbf\n  \n", "b.md").status == "empty"
    double = parse_document(b"\xef\xbb\xbf\xef\xbb\xbf" + body, "b.md")
    assert_coherent(double, b"\xef\xbb\xbf\xef\xbb\xbf" + body)
    for bom_bytes in (b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        pf = parse_document(bom_bytes + "- [ ] x".encode("utf-16-le"), "b.md")
        assert pf.status == "failed" and pf.failure.code == codes.E_BINARY
    fm = b"\xef\xbb\xbf---\ntitle: T\ndate: 2026-10-14\nattendees: [A B]\n---\n- [ ] A B to go\n"
    pf = parse_document(fm, "b.md")
    assert pf.meeting.date_source == "front_matter" and [i.line_start for i in pf.items] == [6]


@pytest.mark.ac("AC-01.11")
def test_lone_surrogates_in_names_and_text():
    # file names may carry unpaired surrogates on Windows (os.fsdecode); text with them can not be UTF-8
    weird = "n𐂀x\udfff.md"
    data = HEADER + b"- [ ] Priya Nair to send the deck\n"
    pf = parse_document(data, weird)
    assert pf.status == "ok" and pf.rel_path == weird
    json.dumps(pf.to_dict())  # ascii-escaped, must not raise
    for text in ("Zo\ud800 to send", "\udc00\udc00", "a [b\ud800](c) *d\udfff*", "\ud83d"):
        assert isinstance(clean_description(text), str)
        assert isinstance(derive_task_text(text, [TextSpan(0, 1)]), str)
    bad = HEADER + b"- [ ] Zo" + "\ud800".encode("utf-8", "surrogatepass") + b" to send\n"
    pf = parse_document(bad, "s.md")
    assert pf.status == "failed" and pf.failure.code == codes.E_ENCODING


@pytest.mark.ac("AC-01.5")
def test_unmatched_fences_comments_and_front_matter():
    cases = {
        "fence": (b"```\n- [ ] a\n- [ ] b\n", codes.W_FENCE_UNCLOSED),
        "tilde fence": (b"~~~\n- [ ] a\n", codes.W_FENCE_UNCLOSED),
        "comment": (b"<!-- never closed\n- [ ] a\n", codes.W_HTML_COMMENT_UNCLOSED),
        "front matter": (b"---\ntitle: T\n- [ ] a\n", codes.W_FRONTMATTER_INVALID),
        "all three": (b"---\nx\n```\n<!--\n- [ ] a\n", None),
        "closer before opener": (b"```\n```\n```\n<!--\n-->\n-->\n- [ ] ok\n", None),
    }
    for label, (body, code) in cases.items():
        data = HEADER + body if label != "front matter" and label != "all three" else body
        pf = parse_document(data, "u.md")
        assert_coherent(pf, data)
        if code is not None:
            assert code in [w.code for w in pf.warnings], label
    pf = parse_document(b"```\n- [ ] a\n- [ ] b\n", "u.md")
    assert pf.items == () and codes.W_CHECKBOX_IGNORED_REGION in [w.code for w in pf.warnings]
    pf = parse_document(b"---\ntitle: T\n- [ ] a\n", "u.md")
    assert [i.line_start for i in pf.items] == [3]  # unclosed front matter: whole file is body


@pytest.mark.ac("AC-01.11")
def test_exhaustive_short_alphabet_strings():
    """Every string up to length 5 over a nasty alphabet (deterministic exhaustive generation)."""
    alphabet = [b"-", b" ", b"[", b"]", b"x", b"\n", b"`", b"<!--", b"-->", b"#", b">"]
    count = 0

    def gen(prefix: bytes, depth: int):
        nonlocal count
        count += 1
        pf = parse_document(prefix, "x.md")
        assert pf.status in ("ok", "empty", "failed")
        if pf.status == "failed":
            assert pf.failure.code != codes.E_INTERNAL_PARSE, (prefix, pf.failure.traceback)
        elif pf.status == "ok":
            assert_coherent(pf, prefix)
        if depth:
            for a in alphabet:
                gen(prefix + a, depth - 1)

    for a in alphabet:
        gen(a, 3)
    assert count > 15_000


# ============================================================================ purity / read-only
def _guard(monkeypatch):
    """Make every clock and filesystem entry point explode while the parser runs."""

    def boom(*_a, **_k):
        raise AssertionError("parser touched the clock or the filesystem")

    for owner, names in (
        (builtins, ("open",)),
        (io, ("open",)),
        (os, ("stat", "lstat", "scandir", "listdir", "open", "walk", "getcwd", "mkdir", "remove", "rename", "replace")),
        (time, ("time", "time_ns", "localtime", "gmtime", "strftime", "monotonic", "ctime")),
        (Path, ("open", "read_bytes", "read_text", "write_bytes", "write_text", "stat", "exists", "mkdir")),
    ):
        for name in names:
            if hasattr(owner, name):
                monkeypatch.setattr(owner, name, boom)

    class NoClockDatetime(datetime.datetime):
        @classmethod
        def now(cls, *a, **k):
            boom()

        @classmethod
        def utcnow(cls):
            boom()

        @classmethod
        def today(cls):
            boom()

        @classmethod
        def fromtimestamp(cls, *a, **k):
            boom()

    monkeypatch.setattr(datetime, "datetime", NoClockDatetime)


@pytest.mark.ac("AC-01.11")
def test_parse_document_is_pure_no_clock_no_filesystem(monkeypatch):
    blobs = {n: (FIXTURES_NOTES / n).read_bytes() for n in SAMPLE_NAMES}
    expected = {n: parse_document(b, n).to_dict() for n, b in blobs.items()}
    with monkeypatch.context() as mp:  # scoped: fixture teardown still needs the filesystem
        _guard(mp)
        for n, b in blobs.items():
            assert parse_document(b, n).to_dict() == expected[n], n
        # hostile inputs too (they exercise every branch of the assembler)
        for data in (b"", b"\x00", HEADER + b"- [ ] Priya to go by tomorrow\n", b"```\n<!--\n---\n"):
            assert parse_document(data, "g.md").status in ("ok", "empty", "failed")


@pytest.mark.ac("AC-01.11")
def test_parse_document_is_pure_even_with_a_patched_date_class(monkeypatch):
    class NoTodayDate(datetime.date):
        @classmethod
        def today(cls):
            raise AssertionError("date.today() called")

    blobs = {n: (FIXTURES_NOTES / n).read_bytes() for n in SAMPLE_NAMES}
    expected = {n: parse_document(b, n).to_dict() for n, b in blobs.items()}
    with monkeypatch.context() as mp:
        mp.setattr(datetime, "date", NoTodayDate)
        for n, b in blobs.items():
            assert parse_document(b, n).to_dict() == expected[n], n


@pytest.mark.ac("AC-01.11")
def test_discover_read_parse_leave_the_tree_untouched(copy_fixture_notes):
    def snap(root: Path):
        return {
            p.relative_to(root).as_posix(): (
                "<dir>" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest(),
                p.stat().st_size,
                p.stat().st_mtime_ns,
            )
            for p in sorted(root.rglob("*"))
        }

    for root in (FIXTURES_NOTES, copy_fixture_notes()):
        before = snap(root)
        disc = discover(root)
        parsed = [parse_file(f) for f in disc.files]
        assert len(parsed) == 35
        assert snap(root) == before  # SHA-256, size and mtime of every file and directory
        assert sorted(os.listdir(root)) == sorted(p.relative_to(root).as_posix() for p in root.iterdir())


@pytest.mark.ac("AC-01.11")
def test_parse_writes_nothing_anywhere(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    before = set(Path(os.environ.get("TEMP", str(tmp_path))).glob("*"))
    for n in SAMPLE_NAMES:
        parse_document((FIXTURES_NOTES / n).read_bytes(), n)
    assert list(work.iterdir()) == []
    assert set(Path(os.environ.get("TEMP", str(tmp_path))).glob("*")) == before


@pytest.mark.ac("AC-01.11")
def test_parse_file_on_a_vanished_file_is_a_failure_not_an_exception(tmp_path):
    root = tmp_path / "n"
    root.mkdir()
    (root / "a.md").write_text("- [ ] x\n", encoding="utf-8")
    (disc,) = discover(root).files
    (root / "a.md").unlink()
    pf = parse_file(disc)
    assert pf.status == "failed" and pf.failure.code == codes.E_UNREADABLE


# ============================================================================ locale / time zone
_SCRIPT = r"""
import json, sys
from pathlib import Path
from actiontrack.parsing import parse_document
root = Path(sys.argv[1])
names = sys.argv[2:]
out = []
for n in names:
    out.append(parse_document((root / n).read_bytes(), n).to_dict())
extra = "# Meeting: Zoë — café\nDate: 3 October 2026\nAttendees: Zoë Fischer, 日本 太郎\n- [ ] Zoë Fischer to send the quote by Friday\n".encode("utf-8")
out.append(parse_document(extra, "extra.md").to_dict())
sys.stdout.buffer.write(json.dumps(out, sort_keys=True).encode("ascii"))
"""


def _run_under(env_extra: dict[str, str]) -> bytes:
    keep = {k: os.environ[k] for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "SYSTEMROOT", "SYSTEMDRIVE", "PATH", "TEMP", "TMP", "HOME", "COMSPEC") if k in os.environ}
    env = {**keep, "PYTHONPATH": str(REPO_ROOT / "src"), **env_extra}
    names = ["2026-10-02-finance-sync.md", "2026-09-30-q4-offsite.md", "2026-09-14-onboarding-flow-design.md",
             "2026-09-16-standup.md", "2026-10-03-sales-pipeline.md", "untitled.md", "scratch.md"]
    r = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(FIXTURES_NOTES), *names],
        capture_output=True,
        env=env,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return r.stdout


@pytest.mark.ac("AC-01.11")
def test_result_does_not_depend_on_locale_timezone_or_encoding_settings():
    variants = [
        {},
        {"TZ": "UTC", "LANG": "C", "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"},
        {"TZ": "Pacific/Kiritimati", "LANG": "tr_TR.UTF-8", "LC_ALL": "tr_TR.UTF-8", "PYTHONUTF8": "1"},
        {"TZ": "America/St_Johns", "LANG": "ja_JP.eucJP", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
        {"TZ": "Etc/GMT+12", "PYTHONHASHSEED": "12345", "PYTHONUTF8": "1"},
    ]
    outputs = [_run_under(v) for v in variants]
    assert len(set(outputs)) == 1
    data = json.loads(outputs[0])
    assert len(data) == 8
    assert data[-1]["meeting"]["date"] == "2026-10-03"  # "3 October 2026"
    assert data[-1]["items"][0]["description"].startswith("Zoë Fischer")


@pytest.mark.ac("AC-01.11")
def test_output_independent_of_in_process_hash_seed_and_dict_order():
    # sets / dict ordering must not leak into results: two interpreters, different hash seeds
    a = _run_under({"PYTHONHASHSEED": "1"})
    b = _run_under({"PYTHONHASHSEED": "987654"})
    assert a == b


@pytest.mark.ac("AC-01.11")
def test_ingest_order_is_total_and_stable_under_shuffle():
    parsed = [parse_document((FIXTURES_NOTES / n).read_bytes(), n) for n in SAMPLE_NAMES]
    base = [p.rel_path for p in sorted(parsed, key=ingest_order_key)]
    rng = random.Random(5)
    for _ in range(10):
        shuffled = parsed[:]
        rng.shuffle(shuffled)
        assert [p.rel_path for p in sorted(shuffled, key=ingest_order_key)] == base
