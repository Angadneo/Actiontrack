"""T-M2-20: AC-01.18 composite acceptance (SC-69), parse level.

One ``tmp_path`` folder mirrors SC-69 and is run through ``discover`` + ``parse_file``:

    a.md            normal note, one valid item
    .draft.md       valid checkbox, dot-prefixed file          -> skipped silently   (clause 1)
    .hidden/x.md    valid checkbox, dot-prefixed directory     -> skipped silently   (clause 1)
    unclosed.md     two items, ``<!-- notes`` never closed, then ``- [ ] hidden task``  (clause 2)
    boxes.md        ``- [ ]``, ``- [ ]Priya ...``, ``- []Lena ...``                     (clauses 3, 4)
    sub/locked/     unreadable: ``PermissionError`` injected into the ``scandir`` seam    (clause 5)

(An unreadable directory cannot be produced with ACLs reliably on Windows, hence the injected
``scandir``.)  The ingest report and the unchanged exit code are asserted in M5 (T-M5-17).
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

from actiontrack import codes
from actiontrack.parsing import parse_file
from actiontrack.parsing.discovery import discover

HEADER = "# Meeting: Composite\nDate: 2026-10-14\nAttendees: Priya Nair, Lena Fischer\n\n"

A_MD = HEADER + "## Action items\n- [ ] Priya Nair to send the revised quote\n"
DRAFT = HEADER + "- [ ] Draft task that must never be seen\n"
HIDDEN = HEADER + "- [ ] Hidden dir task that must never be seen\n"
UNCLOSED = (
    HEADER
    + "## Action items\n"  # line 5
    + "- [ ] Priya Nair to send the first thing\n"  # 6
    + "- [ ] Lena Fischer to send the second thing\n"  # 7
    + "<!-- notes\n"  # 8  (never closed)
    + "- [ ] hidden task\n"  # 9
    + "more text\n"
)
BOXES = (
    HEADER
    + "## Action items\n"  # 5
    + "- [ ]\n"  # 6 empty checkbox
    + "- [ ]Priya to send the deck\n"  # 7 `[ ]X` is fine
    + "- []Lena to book the room\n"  # 8 `[]X` is NOSPACE
)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "notes"
    (root / ".hidden").mkdir(parents=True)
    (root / "sub" / "locked").mkdir(parents=True)
    (root / "sub" / "locked" / "secret.md").write_text(A_MD, encoding="utf-8", newline="\n")
    for name, text in (
        ("a.md", A_MD),
        (".draft.md", DRAFT),
        (".hidden/x.md", HIDDEN),
        ("unclosed.md", UNCLOSED),
        ("boxes.md", BOXES),
    ):
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    return root


def _scandir_with_locked(locked_name: str = "locked"):
    real = os.scandir

    def scandir(path):
        if os.path.basename(os.fspath(path)) == locked_name:
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    return scandir


@pytest.fixture
def run(folder):
    disc = discover(folder, scandir=_scandir_with_locked())
    parsed = {f.rel_path: parse_file(f) for f in disc.files}
    return disc, parsed


def _warnings(disc, parsed):
    """Every warning code in the folder, discovery + per file (multiset)."""
    out = Counter(w.code for w in disc.warnings)
    for pf in parsed.values():
        out.update(w.code for w in pf.warnings)
    return out


@pytest.mark.ac("AC-01.18")
@pytest.mark.sc("SC-69")
def test_clause1_dot_files_and_dirs_are_skipped_silently(run):
    disc, parsed = run
    assert [f.rel_path for f in disc.files] == ["a.md", "boxes.md", "unclosed.md"]
    assert set(parsed) == {"a.md", "unclosed.md", "boxes.md"}
    assert not any(pf.status == "failed" for pf in parsed.values())
    # nothing from the dot-prefixed entries: no item, no warning (discovery warnings only mention sub/locked)
    all_text = " ".join(i.description for pf in parsed.values() for i in pf.items)
    assert "Draft task" not in all_text and "Hidden dir task" not in all_text
    assert all(w.file in (None, "sub/locked") for w in disc.warnings)
    assert not any(".draft" in (w.file or "") or ".hidden" in (w.file or "") for w in disc.warnings)
    assert a_ok(parsed["a.md"])


def a_ok(pf) -> bool:
    return pf.status == "ok" and [i.description for i in pf.items] == ["Priya Nair to send the revised quote"] and not [
        w for w in pf.warnings if w.code != codes.W_DATE_NOT_DUE
    ]


@pytest.mark.ac("AC-01.18")
@pytest.mark.sc("SC-69")
def test_clause2_unclosed_comment_imports_nothing_after_it(run):
    _, parsed = run
    pf = parsed["unclosed.md"]
    assert pf.status == "ok"
    assert [(i.line_start, i.description) for i in pf.items] == [
        (6, "Priya Nair to send the first thing"),
        (7, "Lena Fischer to send the second thing"),
    ]
    assert not any("hidden task" in i.description for i in pf.items)
    unclosed = [w for w in pf.warnings if w.code == codes.W_HTML_COMMENT_UNCLOSED]
    assert [w.line for w in unclosed] == [8]  # at the opener line, exactly once
    ignored = [w for w in pf.warnings if w.code == codes.W_CHECKBOX_IGNORED_REGION]
    assert [w.line for w in ignored] == [9]
    assert Counter(w.code for w in pf.warnings) == {
        codes.W_HTML_COMMENT_UNCLOSED: 1,
        codes.W_CHECKBOX_IGNORED_REGION: 1,
    }


@pytest.mark.ac("AC-01.18")
@pytest.mark.sc("SC-69")
def test_clause3_empty_checkbox_not_imported_and_warned(run):
    _, parsed = run
    pf = parsed["boxes.md"]
    assert 6 not in [i.line_start for i in pf.items]
    empty = [w for w in pf.warnings if w.code == codes.W_CHECKBOX_EMPTY]
    assert [w.line for w in empty] == [6]
    assert codes.W_CHECKBOX_NONSTANDARD not in [w.code for w in pf.warnings]


@pytest.mark.ac("AC-01.18")
@pytest.mark.sc("SC-69")
def test_clause4_bracket_space_x_is_silent_and_empty_brackets_warn(run):
    _, parsed = run
    pf = parsed["boxes.md"]
    assert [(i.line_start, i.description, i.checkbox_state) for i in pf.items] == [
        (7, "Priya to send the deck", "open"),
        (8, "Lena to book the room", "open"),
    ]
    assert [i.raw_text for i in pf.items] == ["- [ ]Priya to send the deck", "- []Lena to book the room"]
    nospace = [w for w in pf.warnings if w.code == codes.W_CHECKBOX_NOSPACE]
    assert [w.line for w in nospace] == [8]  # `[]X` only, never line 7
    assert not [w for w in pf.warnings if w.line == 7]  # `[ ]X` carries no warning at all
    assert Counter(w.code for w in pf.warnings) == {codes.W_CHECKBOX_EMPTY: 1, codes.W_CHECKBOX_NOSPACE: 1}


@pytest.mark.ac("AC-01.18")
@pytest.mark.sc("SC-69")
def test_clause5_unreadable_subdirectory_warns_and_others_are_processed(run):
    disc, parsed = run
    unreadable = [w for w in disc.warnings if w.code == codes.W_DIR_UNREADABLE]
    assert len(unreadable) == 1
    w = unreadable[0]
    assert w.file == "sub/locked"
    assert "Permission denied" in (w.detail or "")
    assert "sub/locked" in w.message
    assert "sub/locked/secret.md" not in parsed  # never reached
    assert not any(pf.failure for pf in parsed.values())  # no FileFailure at all
    assert [pf.status for pf in parsed.values()] == ["ok", "ok", "ok"]
    assert sum(len(pf.items) for pf in parsed.values()) == 1 + 2 + 2


@pytest.mark.ac("AC-01.18")
@pytest.mark.sc("SC-69")
def test_warning_multiset_over_the_folder_is_exactly_the_expected_one(run):
    disc, parsed = run
    c = _warnings(disc, parsed)
    assert c == {
        codes.W_HTML_COMMENT_UNCLOSED: 1,
        codes.W_CHECKBOX_IGNORED_REGION: 1,
        codes.W_CHECKBOX_EMPTY: 1,
        codes.W_CHECKBOX_NOSPACE: 1,
        codes.W_DIR_UNREADABLE: 1,
    }


@pytest.mark.ac("AC-01.18")
def test_without_injection_the_real_directory_is_readable(folder):
    """Control: the same tree without the injected failure reaches ``sub/locked/secret.md``."""
    disc = discover(folder)
    assert [f.rel_path for f in disc.files] == ["a.md", "boxes.md", "sub/locked/secret.md", "unclosed.md"]
    assert disc.warnings == ()


@pytest.mark.ac("AC-01.18")
def test_unreadable_root_is_fatal_but_subdirectory_is_not(folder):
    """Only a sub-directory is downgraded to a warning (the root is exit 4, M1/M5 contract)."""
    from actiontrack.parsing.discovery import FolderError

    def boom(path):
        raise PermissionError(13, "Permission denied", path)

    with pytest.raises(FolderError):
        discover(folder, scandir=boom)


@pytest.mark.ac("AC-01.18")
def test_source_folder_is_unchanged_by_the_composite_run(folder):
    import hashlib

    def snap():
        return {
            p.relative_to(folder).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(folder.rglob("*"))
            if p.is_file()
        }

    before = snap()
    disc = discover(folder, scandir=_scandir_with_locked())
    for f in disc.files:
        parse_file(f)
    assert snap() == before
