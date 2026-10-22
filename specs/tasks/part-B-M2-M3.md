## Milestone M2 — Parsing core

Conventions for every M2/M3 task: all `tests/` paths are under `actiontrack/tests/`; "sample" means the copy of the sample folder at `tests/fixtures/notes/` (35 `.md` + 1 `.txt`, copied from `samples/notes/` plus `notes/notes/scratch.md` and `notes/notes/untitled.md`, originals never touched). Synthetic cases are built in-test with `tmp_path` and explicit bytes (BOM, CR, CRLF, NUL). Parsers are pure: no `os`/`pathlib`/`open`/clock use inside `domain/` and in `parsing/markdown.py`, `meeting.py`, `items.py` (I/O lives only in `discovery.py` and `reader.py`). Warning/error codes are spelled exactly as in spec **Appendix B (v0.6)** and are imported from `src/actiontrack/codes.py` (T-M1-02); no M2/M3 task defines a code string. Line numbers are 1-based, count original file lines (front matter included, BOM is not a line, CRLF/CR/LF each end one line). Dot-prefixed files and directories below the scan root are skipped silently (G-22, §4.2). **Spec v0.6 re-sync:** task bodies below were re-written against spec v0.6 (see the "Re-sync log" at the end of this file); task IDs are unchanged and new tasks continue the numbering (T-M2-19, T-M2-20, T-M3-12, T-M3-13).

### T-M2-01 — Define the parse-result model (codes come from `codes.py`)
- **Size:** M
- **Depends on:** M1 skeleton, T-M1-02 (`codes.py`)
- **Parallel with:** T-M2-02, T-M2-04, T-M3-01, T-M3-02
- **Spec refs:** FR-01.2, FR-01.4, FR-01.6, FR-01.12, BR-18, NFR-TEST-01, NFR-MAINT-02, §7.2 (fields), §11, Appendix B (v0.6, G-20)
- **Files:** `src/actiontrack/domain/models.py`, `tests/unit/test_models.py`
- **Work:**
  - Frozen dataclasses: `ParseWarning(code, line, message, detail)`, `FileFailure(code, message, byte_offset, size_bytes, os_reason, traceback)`, `Attendee(raw_name, email_hint)`, `MeetingMeta(date, date_source, date_raw, title, attendees)`, `TextSpan(start, end)` (half-open offsets into `description`), `ParsedItem(line_start, line_end, raw_text, description, task_text, checkbox_state, section_heading, occurrence_index, block_hash, owner_hook, due_hook)`, `NonCheckboxCandidate(kind, line, text)`, `ParsedFile(rel_path, status ok|empty|failed, failure, content_sha256, size_bytes, mtime_ns, meeting, items, candidates, warnings)`.
  - `raw_text` is the verbatim source text (never NFC-normalised, G-22); `description` is the full cleaned NFC text (owner and due phrases still present); `task_text` is the derived display text (G-12, T-M2-19). `owner_hook` / `due_hook` are typed `object | None` placeholders that M3/M4 replace with real types; M2 never fills them (see T-M2-15).
  - `ParseWarning.code` / `FileFailure.code` are validated against `codes.WARNING_CODES` / `codes.ERROR_CODES` (T-M1-02); this module defines **no** code list. It adds the **parse-time message templates** (data, keyed by code) for every code the parsers emit: the 5 `E-*` codes and the 22 parser-emitted `W-*` codes (all Appendix B warnings except `W-MEETING-DATE-FUTURE`, `W-OWNER-*`, `W-ATTENDEE-NAME-INCOMPLETE`, `W-NAME-DIACRITIC`, `W-STATUS-*`, `W-FILE-MISSING`, which other layers emit, see the Appendix B emitter note). Each template states what happened, file:line and what to do next (§11 last paragraph).
  - `to_dict()` on `ParsedFile` with fixed key order and ISO dates, used by golden tests; warnings sorted by `(line or 0, code)`.
  - No I/O, no clock, no randomness; all collections are tuples.
- **Tests to write first (TDD):**
  - A `ParseWarning`/`FileFailure` with a code not in `codes.py` raises; the set of codes that have a message template equals the set of parser-emitted codes (22 W + 5 E) derived from `codes.py`'s emitter mapping (so a code added to Appendix B without a template fails here).
  - `E-*` ⇒ failure severity (`is_file_failure`); `W-*` (including `W-DIR-UNREADABLE`) ⇒ warning; every template contains placeholders for file and a next-step phrase.
  - `ParsedFile` is immutable; `to_dict()` is stable across two calls and JSON-serialisable; `ParsedItem` exposes both `description` and `task_text`.
  - Static test: no `W-`/`E-` string literal appears in `src/` outside `codes.py` and the message-template table (forces use of the catalogue).
- **Done when:** model + templates importable, tests green, no module under `domain/` imports `os`, `pathlib`, `datetime.date.today`, `time`.

### T-M2-02 — Folder discovery: walk, `.md` filter, recursion, deterministic order
- **Size:** M
- **Depends on:** M1 skeleton
- **Parallel with:** T-M2-01, T-M2-04, T-M3-01
- **Spec refs:** FR-01.1, AC-01.1, AC-01.12 (discovery part), AC-01.13 (discovery part), BR-01, BR-18, SEC-07, NFR-PORT-01
- **Files:** `src/actiontrack/parsing/discovery.py`, `tests/unit/test_discovery.py`
- **Work:**
  - `discover(folder, recursive=True) -> DiscoveryResult(folder_root, files, warnings, subfolder_md_count)`; `folder_root` is the canonical absolute path (`os.path.realpath`); each file carries `rel_path` (always `/`-separated), `abs_path`, `size`, `mtime_ns` taken from `os.stat` (no content read here).
  - Include a file only if `entry.is_file(follow_symlinks=False)` and name ends with `.md` compared case-insensitively (`B.MD`, `x.Md` included; `c.txt` and a directory called `x.md` excluded).
  - Final list sorted by `rel_path` using plain `str` comparison (ordinal code points, so `B.MD` sorts before `a.md`); never rely on `os.scandir` order.
  - `--no-recursive` scans only the top level (flag exposed as parameter; CLI wiring is M1/M5).
  - Folder missing / not a directory / `PermissionError` on the root ⇒ raise `FolderError(path, os_reason)` (CLI maps to exit 4; M5 asserts "nothing written").
  - Opens nothing for writing; touches no mtime (BR-01).
- **Tests to write first (TDD):**
  - AC-01.1: folder with `a.md`, `B.MD`, `c.txt` ⇒ files `[B.MD, a.md]` in that order, `c.txt` absent.
  - Monkeypatch `os.scandir` to yield entries reversed/shuffled ⇒ identical output (BR-18).
  - Sample copy rooted at `tests/fixtures/notes/` (recursive default; the fixtures must hold all 36 files) and a temp parent `<tmp>/` containing it as `<tmp>/notes/` ⇒ 35 files, first `2026-09-01-quote-portal-kickoff.md`, last two `scratch.md`, `untitled.md`; `2026-09-19-offsite-draft.txt` absent; rel paths use `/`.
  - Nonexistent folder, a file path passed as folder ⇒ `FolderError` (AC-01.12 discovery half).
  - Hash/size/mtime of every fixture file identical before and after `discover` (AC-01.11 partial).
- **Done when:** all tests green on Windows; `discover` returns identical results on two runs; no write-mode `open` anywhere in the module (grep test).

### T-M2-03 — Discovery: dot-prefixed skipping, symlinks/junctions, `W-DIR-UNREADABLE`, W-NO-FILES and the `--no-recursive` hint
- **Size:** M
- **Depends on:** T-M2-02
- **Parallel with:** T-M2-04, T-M2-05, T-M2-06
- **Spec refs:** FR-01.11, §4.2, §11 (rows "Folder has no .md files", "Subdirectory not readable"), AC-01.13, AC-01.18 (dot-files and unreadable-subdirectory clauses), SC-69, SEC-07, W-NO-FILES, W-SYMLINK-SKIPPED, W-DIR-UNREADABLE, G-20, G-22
- **Files:** `src/actiontrack/parsing/discovery.py`, `tests/unit/test_discovery_skip.py`
- **Work:**
  - **Dot-prefix rule (G-22, §4.2):** below the scan root, skip every **file and directory** whose name starts with `.` (`.git`, `.actiontrack`, `.hidden`, `.draft.md`, `.DS_Store`): do not descend, do not read, emit **no warning and no failure**, and do not count them in `discovered` or `subfolder_md_count`. The dot check runs before the symlink check (a dot-prefixed link is skipped silently, no `W-SYMLINK-SKIPPED`; note B-17). It applies to entries only, never to the root folder's own name (`ingest .notes` works). The Windows "hidden" attribute is not consulted (§4.2).
  - Symlinks (file or directory) are never followed and never read; each non-dot link yields `W-SYMLINK-SKIPPED` with its `rel_path` (FR-01.11). On Windows also treat junctions / reparse points as links (`DirEntry.is_junction()` exists in Python 3.12). A symlink loop must not recurse.
  - Walk with an explicit stack, not recursion, so deep trees cannot hit the recursion limit.
  - **Unreadable subdirectory (G-20, §11):** `PermissionError`/`OSError` while scanning a directory below the root ⇒ emit `W-DIR-UNREADABLE` with the directory's `rel_path` and the OS reason in `detail`, **continue the walk**, and create **no** `FileFailure` (so it never counts as a failed file and never changes the exit code; `is_file_failure("W-DIR-UNREADABLE")` is false). An unreadable *root* stays `FolderError` (exit 4, T-M2-02).
  - Zero `.md` files found ⇒ `W-NO-FILES`. With `recursive=False`, additionally count `.md` files in non-dot, non-symlink subfolders into `subfolder_md_count`, and if that count is > 0 the warning text MUST say "N .md files exist in subfolders; remove --no-recursive" (§11, AC-01.13).
- **Tests to write first (TDD):**
  - AC-01.18, first clause: folder with `a.md`, `.draft.md` and `.hidden/x.md` ⇒ `discover` returns only `a.md`; no warning of any code mentions the skipped names; `tmp/.git/x.md` also not returned; a dot-prefixed directory nested two levels down is not descended; `<root>/.draft.md` is **not** returned (this reverses the v0.4 default of reading dot-files).
  - Symlink to a file and to a directory (skip with `pytest.mark.skipif` when `os.symlink` raises `OSError` on unprivileged Windows) ⇒ not returned, `W-SYMLINK-SKIPPED` with rel path; a spy on `builtins.open` proves the link target was never opened. Junction created with `mklink /J` via `subprocess` (skip if unavailable). `.dotlink` ⇒ skipped silently.
  - Symlink loop (`a/loop -> ..`) terminates.
  - AC-01.18, last clause (unreadable subdirectory): `os.scandir` monkeypatched to raise `PermissionError` for `tmp/locked` (real ACL/chmod is unreliable on Windows, so the failure is injected; a real `icacls`-denied variant is `skipif`-guarded) ⇒ `W-DIR-UNREADABLE` for `locked`, files in sibling directories and the root are still returned, no `FileFailure`, no exception.
  - AC-01.13 / SC-41 first half: `discover(<sample parent of notes>, recursive=False)` ⇒ 0 files, `W-NO-FILES`, `subfolder_md_count == 35`, message contains `35` and `--no-recursive`; same call recursive ⇒ 35 files. Dot-prefixed `.md` files in subfolders are not counted in the 35.
  - Empty folder and folder containing only `x.txt` ⇒ 0 files, `W-NO-FILES`, no subfolder hint.
- **Done when:** all of the above pass; no skipped link target or dot-prefixed entry is ever opened; walk is iterative; `W-DIR-UNREADABLE` never appears among failures.

### T-M2-04 — Safe reader: stat, size limit, byte read, SHA-256, read-error mapping
- **Size:** M
- **Depends on:** M1 skeleton, T-M2-01
- **Parallel with:** T-M2-02, T-M2-03, T-M2-06
- **Spec refs:** BR-01, SEC-07, FR-01.6, FR-01.8, §11 (E-UNREADABLE, E-TOO-LARGE), AC-01.11, AC-09.5
- **Files:** `src/actiontrack/parsing/reader.py`, `tests/unit/test_reader_io.py`
- **Work:**
  - `read_bytes(discovered_file, max_file_bytes) -> RawRead | FileFailure`; open only with mode `'rb'`; never `r+`, never create/rename/touch.
  - Size check first, from `stat`: `size > max_file_bytes` ⇒ `E-TOO-LARGE` with the size in the message, without reading content. Also read at most `max_file_bytes + 1` bytes so a file that grows between `stat` and `read` still cannot exceed the bound (SEC-07).
  - `content_sha256` is computed over the raw bytes exactly as on disk (BOM and CR bytes included); record `size_bytes` and `mtime_ns` from the pre-read stat.
  - Map `PermissionError`, `FileNotFoundError` (deleted after discovery), sharing/lock `OSError` (e.g. WinError 32) to `E-UNREADABLE` with the OS reason and a next step.
  - Messages state what happened, which file, what to do next (§11).
- **Tests to write first (TDD):**
  - A file of exactly `max_file_bytes` is read; `max_file_bytes + 1` ⇒ `E-TOO-LARGE` (AC-09.5), neighbouring files unaffected.
  - Monkeypatched `open` raising `PermissionError`/`OSError(32)` and a path deleted between discover and read ⇒ `E-UNREADABLE`, no exception escapes.
  - Reading `2026-09-09-notes-final-v2.md` (0 bytes) returns an empty `RawRead` (classification in T-M2-05), hash `e3b0c442…b855`.
  - Before/after SHA-256, size and mtime of all sample files identical (AC-01.11); a spy asserts no `open(..., 'w'|'a'|'+')`.
- **Done when:** tests green; `reader.py` contains no write-capable open.

### T-M2-05 — Content classification and decoding: binary, UTF-8, BOM, EOL, empty, long lines
- **Size:** M
- **Depends on:** T-M2-04
- **Parallel with:** T-M2-06, T-M2-08
- **Spec refs:** §8.2 (BOM, CRLF/CR), §8.3, §11 (E-BINARY, E-ENCODING, W-EMPTY-FILE, W-LONG-LINE), AC-01.8, AC-01.16, AC-09.5
- **Files:** `src/actiontrack/parsing/reader.py`, `tests/unit/test_reader_decode.py`
- **Work:**
  - `classify_and_decode(raw, ...) -> DecodedText | FileFailure | Empty`, evaluated in this order: (1) 0 bytes ⇒ `empty`; (2) any NUL byte, or a UTF-16/UTF-32 BOM at offset 0 (`FF FE`, `FE FF`, `FF FE 00 00`, `00 00 FE FF`), or the PNG signature `89 50 4E 47 0D 0A 1A 0A` at offset 0 ⇒ `E-BINARY`; (3) strict UTF-8 decode, failure ⇒ `E-ENCODING` with the byte offset in the original file (add 3 if a UTF-8 BOM was stripped); (4) strip one leading U+FEFF; (5) text that is empty or `str.isspace()` ⇒ `empty` (`W-EMPTY-FILE`, not a failure); (6) normalise `\r\n` and lone `\r` to `\n`.
  - Never use `str.splitlines()` anywhere in the parsing package: it also splits on `\v`, `\f`, `\x1c-\x1e`, `\x85`, `\u2028`, `\u2029` and would corrupt line numbers. Provide one shared `split_lines(text)` that splits on `\n` only (after step 6) and use it everywhere.
  - Lines longer than 100 000 characters are kept intact and reported as `W-LONG-LINE` (line number) by a helper `find_long_lines(lines)`.
  - NFC normalisation is NOT applied here (raw lines stay verbatim; see T-M2-10).
- **Tests to write first (TDD):**
  - `scratch.md` ⇒ `E-BINARY` (its bytes start `00 01 02 FF FE`, so it is also invalid UTF-8 at offset 3: assert the code is `E-BINARY`, not `E-ENCODING`; spec §11 "NUL check precedes encoding check").
  - `b"caf\xe9\n"` ⇒ `E-ENCODING` with offset 3; same bytes preceded by a BOM ⇒ offset 6.
  - UTF-16-LE-with-BOM of `日本` (no NUL bytes) ⇒ `E-BINARY`; a file starting with the PNG header and no NUL ⇒ `E-BINARY`.
  - BOM + `- [ ] X` decodes to text starting with `-`; BOM only, or `"\n \t\n"` ⇒ `empty`.
  - CRLF, CR-only and LF versions of the same text yield identical `split_lines` output (AC-01.16, part 1); `"a\x0bb\u2028c\n"` stays one line.
  - 100 001-character line ⇒ one `W-LONG-LINE`, no exception, decode time < 1 s.
- **Done when:** tests green; grep proves no `splitlines(` in `src/actiontrack/parsing/` or `domain/`.

### T-M2-06 — Front-matter detection and the restricted YAML-subset parser (no dependency)
- **Size:** M
- **Depends on:** T-M2-01, T-M2-05 (for `split_lines`)
- **Parallel with:** T-M2-08, T-M2-10
- **Spec refs:** §8.1 (front matter keys, flow lists, restricted parser), §8.2 (ignored region), §11 (`W-FRONTMATTER-INVALID`), AC-01.15, **AC-01.20 (parser half)**, SC-34, G-22
- **Files:** `src/actiontrack/parsing/markdown.py`, `tests/unit/test_frontmatter.py`
- **Work:**
  - Front matter exists only if line 1 (after BOM strip) is `---` (trailing spaces allowed) and a later line is `---` or `...`; the region covers lines 1..closing line (inclusive) and is reported as region kind `front_matter`.
  - **Built-in restricted YAML-subset parser; no third-party YAML library and no new dependency** (§8.1, G-22; resolves B-13). Supported: `key: value` lines with plain or single/double-quoted scalars, flow lists `[A, B]`, block lists (`key:` followed by `  - item` lines), blank lines. Keys case-insensitive; every key remembers its line number. Defaults for what the spec does not say (note B-17): a whole-line or trailing ` # comment` is tolerated; a duplicate key is last-wins; `key:` with nothing after it and no list is an empty string.
  - **Outside the subset ⇒ the whole block is invalid:** anchors/aliases (`&a`, `*a`), tags (`!!str`, `!x`), **nested maps** (a `key:` followed by an indented `sub: value`), **multi-line scalars** (`|`, `>`, or a plain scalar continued on an indented line), flow maps `{a: 1}`, a plain scalar containing `: `, unbalanced `[`/quotes, any other non-blank line, and an **unclosed** block (no closing `---`/`...`).
  - Invalid or unclosed block ⇒ treated as absent (nothing is consumed), one `W-FRONTMATTER-INVALID` at line 1, and the **whole file is parsed as body** so its checkbox items are still imported with unshifted line numbers (SC-34, AC-01.20). Metadata then comes from other sources.
  - Return `FrontMatter(end_line, fields)`; values are `str` or `list[str]`; no type coercion (`date: 2026-09-16` stays a string).
- **Tests to write first (TDD):**
  - Sample `2026-09-16-standup.md` and `2026-09-30-standup.md`: region = lines 1-5; `title == "Team standup"`; `date` as above; `attendees` = 3 names (exact lists in spec Appendix A); the first checkbox in each file is at line 11. After parsing, `"yaml"` is not in `sys.modules` and a static test finds no `import yaml`/`ruamel`/`pyyaml` anywhere in `src/` or `pyproject.toml` (AC-01.20: parsed "without any third-party library").
  - AC-01.20, negative half, one case per construct, each with a body `- [ ] Priya to send the deck` after the block: anchor `base: &a x`, alias `ref: *a`, tag `n: !!int 5`, nested map (`meta:` / `  author: Sam`), multi-line scalar `desc: |` + indented line, folded `desc: >`, plain scalar continued on the next indented line, flow map `{a: 1}` ⇒ each is treated as absent, exactly one `W-FRONTMATTER-INVALID` at line 1, the body item is still imported at its original line number.
  - Unclosed `---` at line 1 (SC-34) ⇒ warning, items in body still present with unshifted line numbers.
  - Invalid line `this: is: not [valid` ⇒ treated absent + warning.
  - Document starting with a horizontal rule `---`, prose, later `---` ⇒ prose lines are not key/value ⇒ absent + `W-FRONTMATTER-INVALID` (documented false-positive behaviour).
  - Supported forms: block-list `attendees:` form, flow list, quoted value (`title: "A: B"`), `Date:` key in capitals, trailing comment, BOM before `---`, `...` as closing line.
  - YAML list line `- [ ] Foo` inside front matter ⇒ part of the region, never an item (reported via T-M2-11).
- **Done when:** tests green; the sample's two front-matter files parse; no third-party import; every construct listed under "outside the subset" has a failing-closed test.

### T-M2-07 — Region detection: fenced code, HTML comments (incl. unclosed), blockquotes
- **Size:** M
- **Depends on:** T-M2-05, T-M2-06
- **Parallel with:** T-M2-08, T-M2-10
- **Spec refs:** §8.2 (ignored regions, unclosed fence, unclosed comment), §11 (`W-FENCE-UNCLOSED`, `W-HTML-COMMENT-UNCLOSED`), AC-01.5, AC-01.14, **AC-01.18 (unclosed-comment clause)**, SC-33, SC-42, SC-43, SC-69, G-20
- **Files:** `src/actiontrack/parsing/markdown.py`, `tests/unit/test_regions.py`
- **Work:**
  - `annotate(lines, front_matter_end) -> list[LineInfo]` where `LineInfo(kind: normal|front_matter|fence|comment|quote, visible_text)`, plus the region warnings. One forward pass with states NORMAL / FENCE(char, length, open_line) / COMMENT(open_line).
  - Fences: opener = optional leading whitespace (any amount, conservative) + ≥3 of `` ` `` or `~`; a backtick opener's info string may not contain a backtick. Closer = same character, length ≥ opener, only whitespace after. Fence open/close lines are themselves kind `fence`. A different character or a shorter run does not close.
  - Unclosed fence ⇒ rest of file is kind `fence`, one `W-FENCE-UNCLOSED` at the opener line.
  - HTML comments: `<!--` … `-->`, may start/end mid-line and span lines. `visible_text` is the line with comment spans blanked (text before `<!--` and after `-->` stays normal and may still hold a real item). A `<!--` inside a fence is literal; a fence marker inside a comment is literal. **Unclosed comment (G-20):** the remainder of the file is kind `comment` (nothing after it is imported) and exactly one `W-HTML-COMMENT-UNCLOSED` is emitted at the line holding the `<!--` opener (resolves the "no code exists" half of B-7).
  - Blockquote: a line whose first non-space character (≤3 spaces indent) is `>`, including nested `> >`. Lines that do not start with `>` end the quote (no lazy continuation). A quote line never opens a fence or comment.
  - Precedence per line: front matter > fence > comment > quote.
- **Tests to write first (TDD):**
  - `2026-10-09-security-review.md`: lines 8-11 `fence`, line 13 `comment`; real items 18-21 `normal`.
  - `2026-09-21-incident-review.md`: lines 8-9 `quote`; line 10 blank is `normal`; items 14-17 `normal`.
  - AC-01.18, comment clause: synthetic note with real items, then a line `<!-- todo later` (never closed) followed by `- [ ] hidden task` and prose ⇒ every line after the opener is kind `comment`, one `W-HTML-COMMENT-UNCLOSED` at the opener line, no `W-FENCE-UNCLOSED`; `text <!-- never closed` ⇒ `visible_text` keeps `text`, remainder comment; an unclosed `<!--` **inside a fence** is literal and raises nothing; a `<!--` inside a closed comment on the same span does not raise it.
  - Synthetic: `~~~` fence; ```` closed only by ≥4 backticks; `~~~` does not close a backtick fence; unclosed fence followed by `- [ ] later` ⇒ later line kind `fence` + `W-FENCE-UNCLOSED`; multi-line comment; `<!-- a --> - [ ] real` same line ⇒ `visible_text` keeps the item; comment inside fence stays fence; tab-indented fence; CRLF input gives identical annotation to LF.
- **Done when:** annotation of every sample file matches a hand-written table of non-normal line ranges stored in `tests/golden/m2_regions.json`; the sample yields zero `W-HTML-COMMENT-UNCLOSED` and zero `W-FENCE-UNCLOSED`.

### T-M2-08 — Checkbox line recogniser: variants, states, tolerated, empty and rejected forms
- **Size:** M
- **Depends on:** T-M2-01
- **Parallel with:** T-M2-05, T-M2-06, T-M2-07
- **Spec refs:** FR-01.3, §8.2 (variants, `[]`, empty checkbox, `[-]` handling), AC-01.4, AC-01.3, **AC-01.18 (`[ ]X`, `[]X`, `- [ ]` clauses)**, SC-38, SC-69, W-CHECKBOX-NOSPACE, W-CHECKBOX-NONSTANDARD, W-CHECKBOX-EMPTY, G-20, G-22
- **Files:** `src/actiontrack/parsing/markdown.py`, `tests/unit/test_checkbox.py`
- **Work:**
  - `classify_list_line(text) -> ListLine | None` with kinds `checkbox_open`, `checkbox_done`, `checkbox_nonstandard(char)`, `checkbox_empty`, `plain_bullet`, plus fields `indent_width` (tab advances to next multiple of 4), `marker`, `box_raw`, `rest`, warnings.
  - Markers: `-`, `*`, `+`, `\d{1,9}.` and `\d{1,9})`; ≥1 space or tab after the marker is required (`-[ ] x` is not a list item).
  - Box content: ` ` ⇒ open; `x`/`X` ⇒ done; **empty `[]` is the only form that raises `W-CHECKBOX-NOSPACE`** (open + warning, §8.2, G-22; resolves B-6); exactly one other non-alphanumeric character (`-`, `~`, `/`, `?`, `!`, `>`) followed by whitespace/end ⇒ nonstandard (not imported, `W-CHECKBOX-NONSTANDARD` with file:line and raw text). Letters/digits in the brackets (`- [a] x`, `- [1] note`), links (`- [text](url)`), and multi-char content are ordinary text, no warning.
  - `[ ]X` with no space after the closing bracket (`- [ ]Priya to …`, the exact form in the scenario text) is accepted as open **silently**: no warning of any code (§8.2).
  - **Empty checkbox:** a checkbox (`[ ]`, `[x]`, `[X]`, or `[]`) with no text after it on its line (`- [ ]`) is **not imported** and reports `W-CHECKBOX-EMPTY` (file:line, raw text) — never `W-CHECKBOX-NONSTANDARD` (this reverses the v0.4 default, resolving the empty-checkbox half of B-7). Defaults for gaps (note B-17): `- []` alone ⇒ `W-CHECKBOX-EMPTY` only (no NOSPACE, nothing imported); text that appears only on following indented lines does not rescue an empty first line.
  - Regexes must be linear (no nested quantifiers) — long lines are in scope (see T-M2-18).
- **Tests to write first (TDD):**
  - AC-01.4 verbatim: `- [ ]X`, `* [ ] X`, `+ [X] X`, `1. [ ] X`, `1) [x] X`, 4-space and tab indented, `[]X` (open + `W-CHECKBOX-NOSPACE`), `[-] X`/`[~] X`/`[/] X`/`[?] X` (nonstandard).
  - AC-01.18: `- [ ]Priya to send the deck` ⇒ open, **zero** warnings; `- []Priya to send the deck` ⇒ open + exactly one `W-CHECKBOX-NOSPACE`; `- [ ]`, `- [x]`, `- [ ]   ` (trailing blanks), `1. [ ]` ⇒ `checkbox_empty`, one `W-CHECKBOX-EMPTY`, no `W-CHECKBOX-NONSTANDARD`.
  - Negative: `-[ ] x`, `[ ] x` at column 0, `- [a] x`, `- [link](u) x`, `- [  ] x`.
  - Sample lines: `* [ ] Lena to run the customer survey…` (09-30-q4-offsite:11), `+ [ ] Sam to propose…` (:12), `- [X] Karan to approve…` (10-02-finance-sync:9, after CRLF normalisation), nested `  - [ ] Karan to validate…` (:10) ⇒ `indent_width == 2`.
- **Done when:** table-driven test of ≥30 lines green; `[ ]`/`[x]`/`[X]` counts over all sample text lines outside ignored regions equal 104 open / 13 done in aggregate once T-M2-09 lands; the sample yields zero `W-CHECKBOX-EMPTY` and zero `W-CHECKBOX-NOSPACE`.

### T-M2-09 — Item extraction: spans, multiline continuation, section heading, occurrence index, block hash
- **Size:** L
- **Depends on:** T-M2-07, T-M2-08, T-M2-10
- **Parallel with:** T-M2-12, T-M2-14
- **Spec refs:** FR-01.2, FR-01.3, FR-01.4, FR-01.6, §7.2 (`raw_text` verbatim, `block_hash` over `raw_text`), §8.2 (multiline, nested, any heading), AC-01.2 (source part), AC-01.3, AC-01.6, AC-01.14, AC-01.17, AC-01.19 (`raw_text` clause), SC-29, EC-18, G-22
- **Files:** `src/actiontrack/parsing/markdown.py`, `src/actiontrack/parsing/items.py`, `tests/unit/test_items.py`
- **Work:**
  - Iterate `LineInfo` in order; an item starts at a `normal` (using `visible_text`) line classified as an open/done checkbox with text (`checkbox_empty`, `checkbox_nonstandard` lines never start an item). `raw_text` = the original lines of the item span joined with `\n`, **verbatim** (indentation kept, line endings stripped, no Unicode normalisation of any kind, G-22).
  - Continuation: following non-blank lines that are `normal`, are NOT list items (any marker, checkbox or plain), are not headings/table rows/fence/comment/quote lines, and whose `indent_width` is greater than the item's marker indent. Stop at the first violating line; a plain child bullet ends the description and later indented lines belong to it, not to this item. Description pieces are joined with single spaces; `line_end` = last continuation line.
  - Nested checkbox children are separate items (no parent link stored, §8.2). Section heading = nearest preceding ATX heading (`#`…`######`, trailing `#`s and inline markup stripped) outside ignored regions; `None` before the first heading.
  - `occurrence_index` = zero-based rank among items in the same file with equal `block_hash`; `block_hash` = SHA-256 of `raw_text` after EOL normalisation (so LF/CRLF give equal hashes), computed over the **verbatim** text (a decomposed and a precomposed spelling therefore hash differently — intended, §7.2).
  - Items returned in document order with `checkbox_state` open/done. `description` (full, NFC) is produced by T-M2-10; `task_text` by T-M2-19; the owner/due hooks are left `None` (filled in T-M2-15).
- **Tests to write first (TDD):**
  - AC-01.6 / SC-29: synthetic 3-line and 4-line indented tasks ⇒ one item, `line_start`/`line_end` span all lines, description joined with single spaces; stops at blank line, at a plain child bullet, at a heading, at an unindented lazy line.
  - `2026-09-30-q4-offsite.md`: items at lines 9, 10, 11, 12, 16, 17, 18 (7 items); line 9's `line_end == 9` (child at 10 is separate); section headings `Morning: where we are` (9-12) and `Afternoon: people and process` (16-18); the table at 20-22 and `ACTION:` at 24 yield no items.
  - Two identical lines in one file ⇒ `occurrence_index` 0 and 1, equal `block_hash`.
  - AC-01.19 (`raw_text` clause): `- [ ] Zoë to send the quote` ⇒ `raw_text` contains the decomposed `ë` byte-for-byte (`raw_text.encode() ==` the source line bytes), `description` contains precomposed `Zoë` (NFC), and `block_hash == sha256(raw_text)`.
  - Empty and nonstandard checkboxes (`- [ ]`, `- [-] x`) produce no item; the next real item's `line_start` is unaffected.
  - Per-file imported counts over the sample equal this table: 09-01:4, 09-02:2, 09-03:4, 09-08:4, 09-09-notes-final-v2:0, 09-09-qa:2, 09-10:4, 09-14:4, 09-15:4, 09-16:2, 09-17:4, 09-18:5, 09-21:4, 09-22:4, 09-23:2, 09-24:4, 09-25:2, 09-28:4, 09-29:4, 09-30-offsite:7, 09-30-standup:2, 10-01:4, 10-02:4, 10-03:2, 10-05:4, 10-06:4, 10-07:2, 10-08:4, 10-09:4, 10-10:3, 10-12:4, 10-13:4, 10-14:4, untitled:2 ⇒ total 117, of which 13 done and 104 open (AC-01.14).
  - `untitled.md` items have `section_heading is None`; `2026-09-01-quote-portal-kickoff.md:9` has heading `Action items` and `line_start == line_end == 9`.
- **Done when:** the 117/104/13 totals and per-file table are asserted in a test; the quoted, fenced, commented and `.txt` checkboxes are absent from the items; `raw_text` is never normalised.

### T-M2-10 — Inline-markup stripping and description cleaning (`description` = full cleaned text)
- **Size:** M
- **Depends on:** T-M2-01
- **Parallel with:** T-M2-06, T-M2-07, T-M2-08
- **Spec refs:** §8.2 (inline markup, `description` vs `task_text`), §8.3 (NFC), FR-01.4, FR-01.12, EC-18, AC-09.6 (storage part), AC-01.19 (`description` clause), G-12, G-22
- **Files:** `src/actiontrack/parsing/items.py`, `tests/unit/test_description.py`
- **Work:**
  - `clean_description(text) -> str`: strip bold/italic/strike/code markers (`**x**`, `__x__`, `*x*`, `_x_`, `~~x~~`, `` `x` ``), `[text](url)` and `[text][ref]` ⇒ `text`, `![alt](url)` ⇒ `alt`, autolinks `<https://x>` ⇒ the URL, remove inline `<!-- … -->` spans, unescape `\*` `\_` `\[`.
  - Emphasis only when the marker is adjacent to non-space text on the inside (CommonMark flanking, simplified); `snake_case_name`, `5 * 3`, `10%`, `Acme's` and `@anita:` are left untouched.
  - Collapse runs of whitespace to one space and trim; join continuation pieces before cleaning; the result is Unicode NFC. `raw_text` is never altered (verbatim, not NFC-normalised: spec §7.2/§8.2 exception, resolves B-12).
  - **`description` is the full cleaned text**: the checkbox marker is removed, but the owner phrase, due phrase, separators and trailing sentence punctuation all stay (`Priya to send the revised quote by Friday.` keeps its period). The display text `task_text` (owner/due phrases removed) is a separate derived field produced by T-M2-19 (G-12; resolves B-11).
  - Regexes linear-time.
- **Tests to write first (TDD):**
  - AC-01.19 (`description` clause): item `- [ ] Priya to send the revised quote by Friday.` ⇒ `description == "Priya to send the revised quote by Friday."` exactly (owner and due phrases and the final period kept).
  - `**Priya** to send _revised_ quote` ⇒ `Priya to send revised quote`; `` `idempotency` keys `` ⇒ `idempotency keys`; `see [the quote](https://x.test/q)` ⇒ `see the quote`; `![a](b.png)` ⇒ `a`; `~~old~~ new`.
  - Untouched: `snake_case_word`, `5 * 3`, `cut cloud costs by 10%`, `Priya N. to send revised quote to Acme — by Friday` (em dash kept), `Lena to pull Acme's usage report` (apostrophe kept).
  - Decomposed `Zoë` ⇒ NFC `Zoë`; `李雷` and emoji preserved; two continuation lines joined with exactly one space.
  - Inline comment: `Sam to ship it <!-- maybe --> by Friday` ⇒ `Sam to ship it by Friday`.
- **Done when:** ≥30-row table-driven test green.

### T-M2-11 — Report checkboxes in ignored regions (`W-CHECKBOX-IGNORED-REGION`)
- **Size:** S
- **Depends on:** T-M2-07, T-M2-08
- **Parallel with:** T-M2-09, T-M2-12
- **Spec refs:** §8.2 (ignored regions incl. unclosed comment/fence), AC-01.5, AC-01.14, SC-33, SC-42, SC-43, R-17
- **Files:** `src/actiontrack/parsing/markdown.py`, `tests/unit/test_ignored_regions.py`
- **Work:**
  - For every line of kind `fence`, `comment`, `quote` or `front_matter` that would classify as a checkbox once the region wrapper is peeled off (strip leading `>` prefixes for quotes; strip the `<!--` opener and following whitespace for comment lines, since a commented-out task does not start at column 0), emit `W-CHECKBOX-IGNORED-REGION` with `line`, `detail={region, raw_text}`; none of these becomes an item. This includes the remainder of an **unclosed** fence or comment (the region warnings themselves come from T-M2-07).
  - Nonstandard and empty boxes inside regions are reported only as ignored-region (never also as `W-CHECKBOX-NONSTANDARD` / `W-CHECKBOX-EMPTY`); plain bullets in regions are silent.
  - One warning per matching line, document order.
- **Tests to write first (TDD):**
  - Sample totals: exactly 3 across all files: `2026-09-21-incident-review.md:9` (quote), `2026-10-09-security-review.md:10` (fence), `2026-10-09-security-review.md:13` (comment), with the raw text of each; none elsewhere.
  - Synthetic: `> > - [ ] nested`, `~~~` fence with `* [x] a`, multi-line comment whose 2nd line is `- [ ] b`, unclosed fence remainder, **unclosed comment remainder** (`- [ ] hidden` after a never-closed `<!--` ⇒ one ignored-region warning, region `comment`, plus the `W-HTML-COMMENT-UNCLOSED` from T-M2-07), YAML list `- [ ] z` in front matter.
  - `[-]` and `- [ ]` (empty) inside a fence ⇒ one ignored-region warning each, zero nonstandard/empty warnings.
- **Done when:** AC-01.14's "first three appear as W-CHECKBOX-IGNORED-REGION" is asserted against the sample.

### T-M2-12 — Meeting date extraction, precedence, conflicts and filename fallback
- **Size:** M
- **Depends on:** T-M3-02 (absolute-date literal parser), T-M2-06, T-M2-07
- **Parallel with:** T-M2-09, T-M2-14
- **Spec refs:** §8.1 (date precedence, formats, weekday prefix, invalid rule, filename rule, future-date note), FR-01.2, AC-01.9, AC-01.15, **AC-01.20 (unparseable `Date:` clause)**, SC-10, SC-49, W-MEETING-DATE-MISSING/INVALID/FROM-FILENAME, W-DATE-CONFLICT, W-DATE-WEEKDAY-MISMATCH, G-21
- **Files:** `src/actiontrack/parsing/meeting.py`, `tests/unit/test_meeting_date.py`
- **Work:**
  - `extract_meeting_date(lines, infos, front_matter, filename, date_order) -> (date|None, date_source, date_raw, warnings)`; scanning uses only `normal` lines.
  - Tiers in order: (1) front-matter `date`/`meeting_date`; (2) first `Date:`/`**Date:**`/`**Date**:` line (case-insensitive) within the first 30 non-blank normal lines; (3) first heading containing an ISO date or `Month D, YYYY` (`date_source=title`); (4) filename prefix `YYYY-MM-DD` or `YYYYMMDD` (must be followed by end, `-`, `_`, `.` or a non-digit and form a valid calendar date). If front matter was rejected (`W-FRONTMATTER-INVALID`, T-M2-06) tier 1 does not exist.
  - **Invalid field ⇒ `W-MEETING-DATE-INVALID` only (G-21, resolves B-5; AC-01.15/AC-01.20):** if tier 1 or 2 has a *field present* but it does not parse (`Date: sometime last week`, `2026-02-30`, `03/04/2026` with no `date_order`, `Date: 16 Sep` — a year-less value is not an accepted meeting-date format, note B-17) ⇒ `date=None`, exactly `W-MEETING-DATE-INVALID`, **never also `W-MEETING-DATE-MISSING`**, and tiers 3/4 are not consulted (the filename is not a fallback).
  - Filename is used only when no date field exists at all: `date_source=filename`, `W-MEETING-DATE-FROM-FILENAME`. Nothing found ⇒ `None`, `W-MEETING-DATE-MISSING` only.
  - Accepted forms via the T-M3-02 parser: ISO (trailing time/zone ignored, calendar date as written, no conversion), `D Month YYYY`, `Month D, YYYY`, abbreviations, leading weekday; numeric `NN/NN/YYYY` only if unambiguous or `date_order` set (§9.4); weekday disagreeing with the date ⇒ `W-DATE-WEEKDAY-MISMATCH`, date still used. When a lower tier also parses to a different date ⇒ `W-DATE-CONFLICT`, higher tier wins.
  - **`W-MEETING-DATE-FUTURE` is NOT emitted by M2 or any parser (G-20, §8.1, resolves B-4):** it needs today and is computed by the ingest service (T-M5-17) from the injected clock; this task exports no `is_future` helper.
  - `empty`-status files skip metadata extraction entirely.
- **Tests to write first (TDD):**
  - Sample: `2026-09-16-standup.md` and `2026-09-30-standup.md` ⇒ `front_matter` dates 2026-09-16 / 2026-09-30; `2026-09-14-onboarding-flow-design.md` (`Monday, 14 September 2026`) ⇒ 2026-09-14, no mismatch warning; `2026-09-09-qa-standup.md` (`9 September 2026`) ⇒ 2026-09-09; `2026-10-03-sales-pipeline.md` ⇒ `None`, exactly `W-MEETING-DATE-INVALID`, no `W-MEETING-DATE-FROM-FILENAME`, no `W-MEETING-DATE-MISSING` (AC-01.15, AC-01.20, SC-49); `untitled.md` ⇒ `None` + `W-MEETING-DATE-MISSING` only (AC-01.9); all other files ⇒ `date_line` equal to the filename date, no conflict warning.
  - AC-01.20, date clause (synthetic): `Date: sometime last week` in a file named `2026-10-03-x.md` ⇒ `date None`, warning set == {`W-MEETING-DATE-INVALID`}; same with front-matter `date: nonsense`; front matter with an anchor (`date: &d 2026-09-16`) ⇒ `W-FRONTMATTER-INVALID`, tier 1 absent, the date then comes from a body `Date:` line, else the filename (`W-MEETING-DATE-FROM-FILENAME`).
  - Synthetic: no Date field + `2026-10-20-x.md` ⇒ filename + warning; `20261020-notes.md`; `2026-13-45-x.md` ⇒ MISSING; `Tuesday, 14 September 2026` ⇒ date used + mismatch warning; `2026-09-01T10:30:00+05:30` ⇒ 2026-09-01; `**Date:** Sep 1, 2026`; `Date: 03/04/2026` with `date_order=None` ⇒ INVALID, with `dmy` ⇒ 2026-04-03; `Date:` as the 31st non-blank line ignored; `Date:` inside a fence ignored; front matter 2026-09-16 vs filename 2026-09-17 ⇒ front matter wins + `W-DATE-CONFLICT`; heading `# Standup 2026-09-01` ⇒ `date_source=title`; `W-DATE-CONFLICT` absent when front matter date is invalid.
  - No parser output ever contains `W-MEETING-DATE-FUTURE` (a note dated 2099-01-01 yields none; the ingest-level warning is asserted in M5).
- **Done when:** all tiers/branches covered by the above; `date_source` values limited to `front_matter|date_line|title|filename|none`.

### T-M2-13 — Meeting title and attendees (all observed and specified formats)
- **Size:** M
- **Depends on:** T-M2-06, T-M2-07
- **Parallel with:** T-M2-09, T-M2-12, T-M2-14
- **Spec refs:** §8.1 (title, attendees), FR-01.2, AC-01.15, AC-01.9, W-ATTENDEES-MISSING
- **Files:** `src/actiontrack/parsing/meeting.py`, `tests/unit/test_meeting_people.py`
- **Work:**
  - Title precedence: front-matter `title`; `# Meeting: <title>` (case-insensitive, split on the FIRST `Meeting:` only, so `Incident review: billing double-charge` keeps its colon); first H1; filename stem. Inline markup stripped via T-M2-10; CR never present (normalised).
  - Attendees precedence: front-matter `attendees`/`participants` (list or comma/semicolon string); a line `Attendees:`/`Participants:`/`Present:` (also bold `**Attendees:**`) on any normal line, first match wins (scope decision in note B-8, still OPEN); a heading with those names followed by a bullet list (plain bullets until the next heading or a blank-then-non-list line).
  - Split on `,` `;` newline and the whole word `and` (case-insensitive, word boundaries so `Sandra Anderson` is intact); strip role parentheticals `(PM)`; capture `<email>` into `email_hint` and remove it from the name; ignore lines starting `Absent:`/`Apologies:`; drop empty tokens and exact duplicates, keep order.
  - None found ⇒ empty tuple + `W-ATTENDEES-MISSING`. `W-ATTENDEE-NAME-INCOMPLETE` (now listed in Appendix B, G-20) is emitted by M4/M5 (it needs the namesake set), not here.
- **Tests to write first (TDD):**
  - Sample titles: `2026-09-01-quote-portal-kickoff.md` ⇒ `Quote portal kickoff`; `2026-09-21-incident-review.md` ⇒ `Incident review: billing double-charge`; `2026-10-02-finance-sync.md` (CRLF) ⇒ `Finance sync`; `2026-10-05-onboarding-retro.md` ⇒ `Onboarding flow checkpoint` (differs from filename); front-matter files ⇒ `Team standup`; `untitled.md` ⇒ `untitled`.
  - Sample attendees: offsite ⇒ 9 names in listed order; `2026-09-25-qa-sync.md` ⇒ `Priya Shah`, `Rahul Verma`; front-matter lists ⇒ 3 each; `2026-09-23-standup.md` and `untitled.md` ⇒ empty + `W-ATTENDEES-MISSING` (exactly 2 files in the sample).
  - Synthetic: semicolons; `Priya, Rahul and Anita`; `Priya (PM), Rahul <rahul@x.test>` ⇒ names plus email hint; `**Attendees:** A, B`; `Participants:`; `Present:`; heading+bullets; `Absent: Karan` ignored; `Attendees:` with empty value; `Zoë Åström, 李雷` unchanged; front matter rejected by the YAML-subset rule (T-M2-06) ⇒ attendees come from the body line.
- **Done when:** every one of the 33 non-empty readable sample files yields the expected title and attendee count from a golden table.

### T-M2-14 — Non-checkbox candidates (`ACTION:`, table rows, plain bullets under action headings)
- **Size:** S
- **Depends on:** T-M2-07, T-M2-08
- **Parallel with:** T-M2-09, T-M2-12, T-M2-13
- **Spec refs:** §8.2 (non-checkbox candidates), NG-4, AC-01.17, SC-48, W-NONCHECKBOX-CANDIDATES
- **Files:** `src/actiontrack/parsing/markdown.py`, `tests/unit/test_candidates.py`
- **Work:**
  - (a) `normal` lines starting (after optional indentation, no bullet) with `ACTION:`, `Action:` or `TODO:`; (b) body rows of a Markdown table whose header row contains the word `Action` (case-insensitive); skip header and `|---|` separator rows; (c) plain bullets (no checkbox) under a heading matching `/action|todo|follow[- ]?up|next steps/i`, excluding bullets nested under a checkbox item (these are the child bullets §8.2 excludes from descriptions; note B-8).
  - Emit one `NonCheckboxCandidate` plus one `W-NONCHECKBOX-CANDIDATES` per candidate (line + text + kind), so the report can count them; never create items.
  - Skip candidates inside fence/comment/quote/front-matter regions.
- **Tests to write first (TDD):**
  - `2026-09-30-q4-offsite.md`: exactly two candidates: table row at line 22 (`| Anita | Refresh the brand kit | 2026-10-09 |`) and `ACTION:` at line 24; header (20) and separator (21) not reported; zero candidates in every other sample file (AC-01.17).
  - Synthetic: `TODO: x`; table with header `| Who | Task |` ⇒ none; `- plain` under `## Next steps` and `## Follow-up` ⇒ candidates, under `## Discussion` ⇒ none; same bullet inside a fence ⇒ none; checkbox bullets under `## Action items` ⇒ not candidates; plain child bullet under a checkbox item ⇒ not a candidate.
- **Done when:** the sample yields exactly 2 candidates in total.

### T-M2-15 — Assemble the pure `parse_document`, owner/due hook interfaces, `task_text`, and failure isolation
- **Size:** M
- **Depends on:** T-M2-05, T-M2-09, T-M2-11, T-M2-12, T-M2-13, T-M2-14, T-M2-19
- **Parallel with:** T-M2-16
- **Spec refs:** FR-01.1–01.6, FR-01.8, FR-01.12, BR-17, BR-18, NFR-TEST-01, §11 (W-NO-ITEMS, W-EMPTY-FILE, E-INTERNAL-PARSE), AC-01.18, AC-01.19, AC-01.20
- **Files:** `src/actiontrack/parsing/__init__.py`, `src/actiontrack/parsing/items.py`, `src/actiontrack/domain/models.py`, `tests/unit/test_parse_document.py`
- **Work:**
  - `parse_document(rel_path, text, read_meta, options, analyzer=NullAnalyzer()) -> ParsedFile` is pure: runs EOL normalise → front matter → regions → items → meeting → candidates → analyzer hooks → `task_text` (T-M2-19), merges warnings sorted by `(line, code)`.
  - `options` carries `date_order`, nothing else clock-related. Status `ok`; zero imported items on a non-empty valid file ⇒ `W-NO-ITEMS`; `empty` input short-circuits with `W-EMPTY-FILE` and no other warnings. The parser never emits `W-MEETING-DATE-FUTURE` (ingest service, G-20).
  - Hook interface for M3/M4, defined here, implemented there: `ItemAnalyzer` protocol `analyze(description, ctx) -> ItemAnalysis(owner, due, owner_spans, due_spans)` where `description` is the item's **full cleaned description** (never `raw_text`; the spans index into it, G-12), `ctx` has `meeting_date` and `date_order`, and the span tuples feed `derive_task_text` (T-M2-19). `NullAnalyzer` returns `None, None, (), ()` (so `task_text == description` capitalised-as-is fallback rules of T-M2-19 apply). `ParsedItem.owner_hook`/`due_hook` are set from it.
  - `parse_file(discovered, options)` = read (T-M2-04/05) + `parse_document`; any unexpected exception ⇒ `ParsedFile(status=failed, failure=E-INTERNAL-PARSE)` with the traceback kept for `--verbose`; never re-raised (BR-17).
- **Tests to write first (TDD):**
  - Analyzer that raises ⇒ that file `failed`/`E-INTERNAL-PARSE`, other files in the same loop succeed.
  - With `builtins.open` and `os.stat` patched to raise, `parse_document` still works (proves purity); calling it twice gives equal results and equal `to_dict()`.
  - File with only a heading ⇒ `W-NO-ITEMS`; file with only `- [-] x` ⇒ `W-NO-ITEMS` plus `W-CHECKBOX-NONSTANDARD`; file with only `- [ ]` ⇒ `W-NO-ITEMS` plus `W-CHECKBOX-EMPTY` (and no NONSTANDARD); `2026-09-09-notes-final-v2.md` ⇒ `empty`, exactly `W-EMPTY-FILE`.
  - AC-01.20 body survives bad front matter: a document whose front matter holds a nested map, then `- [ ] Priya to send the deck` ⇒ `status ok`, one `W-FRONTMATTER-INVALID` (line 1), 1 item at its original line number.
  - A spy `ItemAnalyzer` receives `description` strings (never `raw_text`) and a `NullAnalyzer` run leaves `owner_hook`/`due_hook` `None`; with a stub analyzer returning spans, `ParsedItem.task_text` equals `derive_task_text(description, spans)`.
  - Golden `to_dict()` of `2026-09-01-quote-portal-kickoff.md`: 4 items at lines 9-12, date 2026-09-01 `date_line`, 4 attendees, no warnings.
- **Done when:** one call per sample file produces a `ParsedFile` with no exception; `domain/` and parse functions import no I/O module.

### T-M2-16 — Persistence-order key (meeting date, then path)
- **Size:** S
- **Depends on:** T-M2-01
- **Parallel with:** T-M2-15
- **Spec refs:** FR-01.1, BR-18, NFR-DET-01
- **Files:** `src/actiontrack/parsing/discovery.py` (or `domain/models.py`), `tests/unit/test_order.py`
- **Work:**
  - `ingest_order_key(parsed_file)` = `(meeting_date is None, meeting_date, rel_path)` with `rel_path` ordinal; consumed by M5's ingest orchestration, defined here so it is unit-tested with the parsers.
  - Failed/empty files carry no date and sort into the null group; this has no effect on persistence because they hold no items.
- **Tests to write first (TDD):**
  - Sample sort: first `2026-09-01-quote-portal-kickoff.md`; same-date pair `2026-09-30-q4-offsite.md` before `2026-09-30-standup.md`; dated files in date order; null-date group last and internally ordinal: `2026-09-09-notes-final-v2.md` (empty), `2026-10-03-sales-pipeline.md`, `scratch.md` (failed), `untitled.md`.
  - A file renamed `zzz.md` containing `Date: 2026-09-01` sorts among the 09-01 files by date, not last.
  - Shuffle input 20 times (seeded) ⇒ identical order.
- **Done when:** the test passes; M5 can import the key without change.

### T-M2-17 — Sample-corpus acceptance and golden parse output
- **Size:** M
- **Depends on:** T-M2-03, T-M2-15, T-M2-16, T-M2-19
- **Parallel with:** T-M2-18
- **Spec refs:** AC-01.13 (discovery/parse half), AC-01.14, AC-01.15, AC-01.16, AC-01.17, AC-01.19 (sample invariants), AC-01.20 (sample front-matter half), SC-33, SC-41 (first half), SC-42, SC-43, SC-49
- **Files:** `tests/integration/test_m2_sample.py`, `tests/golden/m2_sample_parse.json`
- **Work:**
  - Run discover + parse over the sample copy and compare with a golden JSON written by hand from the spec/Appendix A, not by dumping the implementation: per file status, meeting date + source, title, attendee count, item line numbers and states, warnings (code, line).
  - Aggregates asserted: 35 `.md` discovered; 33 `ok`, 1 `empty` (`2026-09-09-notes-final-v2.md`), 1 `failed` (`scratch.md`, `E-BINARY`); 117 items (104 open, 13 done); 3 `W-CHECKBOX-IGNORED-REGION`; 2 `W-NONCHECKBOX-CANDIDATES`; `W-MEETING-DATE-INVALID` ×1 (and, in that file, no `W-MEETING-DATE-MISSING`), `W-MEETING-DATE-MISSING` ×1 (`untitled.md`), `W-ATTENDEES-MISSING` ×2 (`2026-09-23-standup.md`, `untitled.md`), `W-EMPTY-FILE` ×1, zero `W-CHECKBOX-NOSPACE`/`NONSTANDARD`/`EMPTY`, zero `W-FENCE-UNCLOSED`/`W-HTML-COMMENT-UNCLOSED`/`W-FRONTMATTER-INVALID`/`W-SYMLINK-SKIPPED`/`W-DIR-UNREADABLE`/`W-LONG-LINE`, and no `W-MEETING-DATE-FUTURE` from any parser.
  - AC-01.20 (sample half): the two front-matter notes parse through the built-in subset parser; `yaml` is not imported during the run.
  - AC-01.19 (sample invariants, not text-for-text): for all 117 items `raw_text` equals the verbatim source lines (re-read bytes), `description` is NFC, `task_text` is non-empty, starts with an upper-case letter when its first character is alphabetic, and contains neither the located due raw nor an `owner to` clause once analyzers from M3/M4 are attached (with `NullAnalyzer` it equals `description`).
  - AC-01.16: copy `2026-10-02-finance-sync.md` to an LF version; the two `ParsedFile.to_dict()` results are equal except `content_sha256` and `size_bytes`; `[X]`, `*`, `+` items are present (lines 9-12).
  - Note for integrators: the ingest-level numbers "ingested 33" and exit code 5 of AC-01.13 are asserted in M5; this task asserts the parse-level facts they derive from.
- **Tests to write first (TDD):** the golden file and assertions above, red until T-M2-03..16 and T-M2-19 are done.
- **Done when:** the test is green and the sample fixture files are byte-identical afterwards (SHA-256 compare).

### T-M2-18 — Robustness, purity and read-only guard tests
- **Size:** S
- **Depends on:** T-M2-15
- **Parallel with:** T-M2-17
- **Spec refs:** AC-01.11, BR-01, BR-17, BR-18, NFR-PERF-01 (parse share), NFR-TEST-01, SEC-09, W-LONG-LINE, EC-18, EC-19
- **Files:** `tests/unit/test_parser_robustness.py`
- **Work:**
  - Static purity test: modules under `domain/`, `parsing/markdown.py`, `meeting.py`, `items.py` do not import `os`, `pathlib`, `shutil`, `socket`, `subprocess`, `time`, and do not reference `open(`, `date.today`, `datetime.now`; none imports a YAML library (G-22).
  - ReDoS/performance: single 1 000 000-character line made of `- [ ] ` + `a`s, of `*_` repeated, of `[`×100 000, and of `<!--` ×50 000 each parse in < 1 s with no exception (the `<!--` case yields one `W-HTML-COMMENT-UNCLOSED`); a front-matter block whose value is `[`×100 000, and one with 100 000 `&a` tokens, is rejected (`W-FRONTMATTER-INVALID`) in < 1 s; `derive_task_text` (T-M2-19) on a 1 000 000-character description with 10 000 spans runs in < 1 s.
  - Seeded fuzz: 500 mutations (delete/insert/swap bytes-as-text, random truncation) of sample files; invariants: never raises, `1 <= line_start <= line_end <= n_lines`, items strictly increasing by `line_start`, no item inside an ignored region, no item from an empty/nonstandard checkbox, `task_text` never empty.
  - Read-only: SHA-256, size and mtime of the whole fixture tree identical after discover + read + parse (AC-01.11).
  - Parse all 33 non-empty readable sample files in < 1 s total (share of the NFR-PERF-01 budget).
- **Tests to write first (TDD):** all of the above (they are the deliverable).
- **Done when:** tests green on Windows, deterministic with the fixed seed.

### T-M2-19 — `task_text` derivation and the `description` / `raw_text` contract (AC-01.19)
- **Size:** M
- **Depends on:** T-M2-01, T-M2-09, T-M2-10, T-M3-04 (due spans; tests only)
- **Parallel with:** T-M2-11, T-M2-12, T-M2-13
- **Spec refs:** FR-01.12, §6.1 (description core), §7.2 (`description`, `task_text`, `raw_text`), §8.2 (`description` vs `task_text`), §8.3 (NFC), **AC-01.19**, G-12, G-22
- **Files:** `src/actiontrack/parsing/items.py`, `src/actiontrack/domain/models.py` (`TextSpan`), `tests/unit/test_task_text.py`
- **Work:**
  - `derive_task_text(description, spans) -> str`, pure. `spans` are half-open offsets into the **cleaned `description`** (locators run on `description`, never on `raw_text`): the due-phrase locator's spans for every located due/event phrase **including its introducer and time words and the non-due phrases** such as `from 9 Oct` (T-M3-04/05), and the owner-clause locator's spans including a `(with Other)` co-owner phrase (M4, `owner_phrase.py`, spec §10.1). An `OwnerSpanProvider` protocol and a `NullOwnerSpans` default are defined here; M4 implements the real one.
  - Algorithm (§8.2): merge overlapping spans, delete them, remove **orphaned separators** (`—`, `–`, `-`, `,`, `:`) left at the ends or doubled next to a deleted span, drop a sentence-final `.`/`;` that became adjacent to a deleted span (needed for AC-01.19 `…by Friday.` ⇒ no period; the spec's separator list omits it, note B-17), collapse whitespace, upper-case **only the first character**, NFC. If the result would be empty, fall back to `description`.
  - `parse_document` (T-M2-15) calls it with the analyzer's spans. With `NullAnalyzer` (no spans) `task_text` is `description` with only the first-letter rule applied.
  - `raw_text` remains the verbatim source (T-M2-09); this task owns the end-to-end assertion that the three fields have the three different contents spec AC-01.19 demands.
- **Tests to write first (TDD):**
  - **AC-01.19 verbatim:** `- [ ] Priya to send the revised quote by Friday.` parsed with the real due locator (T-M3-04) and a stub owner span for `Priya to ` ⇒ `description == "Priya to send the revised quote by Friday."`, `task_text == "Send the revised quote"`.
  - **AC-01.19 co-owner:** `Lena (with Priya) to update the plan` with a stub owner span covering `Lena (with Priya) to ` ⇒ `task_text == "Update the plan"` (no `Priya`). The same assertion with the **real** owner-phrase locator is owned by the M4 owner-phrase task (cross-part dependency; integrator note B-17); this task fixes the span-deletion contract.
  - **AC-01.19 `raw_text`:** `- [ ] Zoë to send the quote` ⇒ `raw_text` byte-for-byte the source line (decomposed), `description` NFC (`Zoë`), `task_text` NFC and starts `Send`.
  - Not-due phrase removed (§8.2): `Karan to freeze invoice edits from 9 Oct` ⇒ `Freeze invoice edits` (its token count of 3 is the spec §8.3 example).
  - Separators: `Priya N. to send revised quote to Acme — by Friday` ⇒ `Send revised quote to Acme`; mid-sentence `Lena to pull the usage report, results due 15 October` ⇒ `Pull the usage report`; `by EOD tomorrow` removed with its time word.
  - Two due spans (`multiple_dates`) are both removed; overlapping spans merge; spans at index 0 and at the end work; no spans ⇒ only the first character is upper-cased (`über task` ⇒ `Über task`; the remaining characters, including inner capitals, are untouched).
  - Fallback: spans covering the whole text (`TBD by Friday`, owner span `TBD`, due span `by Friday`) ⇒ `task_text == description`.
  - Purity and determinism: same input twice ⇒ equal; no `os`/clock import.
- **Done when:** AC-01.19's three clauses are asserted (two with stubbed owner spans, flagged for re-assertion in M4); `task_text` is non-empty for every sample item (T-M2-17).

### T-M2-20 — AC-01.18 composite acceptance (dot-files, unclosed comment, empty checkbox, `[ ]X` vs `[]X`, unreadable subdirectory)
- **Size:** S
- **Depends on:** T-M2-03, T-M2-07, T-M2-08, T-M2-11, T-M2-15
- **Parallel with:** T-M2-17, T-M2-18
- **Spec refs:** **AC-01.18**, SC-69, FR-01.11, §4.2, §8.2, §11, G-20, G-22
- **Files:** `tests/integration/test_m2_ac0118.py`
- **Work:** one end-to-end fixture built with `tmp_path` that mirrors SC-69, run through `discover` + `parse_file` (parse level only; the ingest report and the unchanged exit code are asserted in M5, T-M5-17):
  - `a.md` (normal note with one valid item), `.draft.md` and `.hidden/x.md` (each with a valid checkbox), `unclosed.md` (two real items, then `<!-- notes` never closed, then `- [ ] hidden task`), `boxes.md` (`- [ ]`, `- [ ]Priya to send the deck`, `- []Lena to book the room`), and `sub/locked/` made unreadable by injecting `PermissionError` into `os.scandir` for that path.
- **Tests to write first (TDD):**
  - Discovered set is exactly `{a.md, unclosed.md, boxes.md}`; `.draft.md` and `.hidden/x.md` produce no item, no warning, no failure (clause 1).
  - `unclosed.md`: exactly the two real items are imported; `- [ ] hidden task` is not; one `W-HTML-COMMENT-UNCLOSED` at the opener line (and one `W-CHECKBOX-IGNORED-REGION` for the hidden task) (clause 2).
  - `boxes.md`: `- [ ]` is not imported and emits `W-CHECKBOX-EMPTY` (clause 3); `- [ ]Priya to send the deck` is imported with **no** warning, `- []Lena to book the room` is imported and emits exactly one `W-CHECKBOX-NOSPACE` (clause 4).
  - `sub/locked` ⇒ one `W-DIR-UNREADABLE` (rel path `sub/locked`), no `FileFailure`, all other files parsed (clause 5).
  - The warning multiset over the folder equals the expected one exactly (no stray codes).
- **Done when:** the five clauses of AC-01.18 are each asserted by name; each clause also has its unit-level owner (T-M2-03, T-M2-07, T-M2-08).

## Milestone M3 — Date resolver

M3 can start right after the M1 skeleton and run in parallel with M2, with one coupling: T-M3-01 and T-M3-02 should land early because T-M2-12 (meeting dates) reuses them (P-B). All M3 code lives in `domain/dates.py` (pure; no clock, no `datetime.date.today`, no I/O). **Locators and resolvers take the item's cleaned `description` (never `raw_text`) so that the returned spans index the same string that T-M2-19 deletes from (G-12).** Test items are the real sample items (file:line) with each file's own meeting date M; "M" in tables below is that meeting date. Calendar facts used below were re-verified for spec v0.6 against the real 2026 calendar: Sep 1 Tue, Sep 3 Thu, Sep 14 Mon, Sep 16 Wed (2025-09-16 Tue, 2027-09-16 Thu), Sep 21 Mon, Sep 29 Tue, Sep 30 Wed, Oct 5 Mon, Oct 6 Tue, Oct 9 Fri, Oct 10 Sat, Oct 12 Mon, Oct 14 Wed, Oct 16 Fri, Oct 17 Sat, Oct 21 Wed, Oct 26 Mon, Oct 31 Sat, Nov 4 Wed, Dec 20 Sun, Dec 30 Wed, 2027-01-06 Wed, 2027-03-01 Mon, 2026-08-14 Fri, 2026-08-15 Sat, 2026-08-14 is 61 and 2026-08-15 is 60 days before 2026-10-14. Rule ids (`due_rule`) and unresolved reasons are imported from `codes.py` (`DUE_RULES`, `DUE_REASONS`, T-M1-02); the spec §9.5 catalogue is exhaustive.

### T-M3-01 — Calendar primitives, rule tables and name constants
- **Size:** S
- **Depends on:** M1 skeleton, T-M1-02 (`DUE_RULES`, `DUE_REASONS`)
- **Parallel with:** all M2 tasks except T-M2-12
- **Spec refs:** §9.1 (calendar-day arithmetic, ISO Mon-Sun weeks), §9.2 (number words H-04), §9.3 (year-less rule constant), §9.5 (rule catalogue), NFR-MAINT-02, BR-05, Appendix B (reasons), G-06, G-20
- **Files:** `src/actiontrack/domain/dates.py`, `tests/unit/test_dates_primitives.py`
- **Work:**
  - Data tables (NFR-MAINT-02): weekday names (full, 3-letter, `Tues`, `Thur`, `Thurs`), month names (full, 3-letter, `Sept`), ordinal suffixes, time-of-day words (`EOD`, `COB`, `noon`, `midnight`, `\d{1,2}(:\d\d)?\s?(am|pm)`), vague words (`soon`, `later`, `ASAP`, `TBD`, `ongoing`), **range words** (`this week`, `next week`, `this month`, `next month`, `week of <date>`), introducer lists, **number words** `one`…`twelve` plus `a`/`an` (H-04, singular unit only for `a`/`an`), the quantity phrases that are `vague` (`a couple of`, `a few`, `a fortnight`), and the constant `PAST_ACCEPT_DAYS = 60` (year-less rule, G-06).
  - Functions: `monday_of(d)`, `iso_week_shift(d, n)`, `weekday_in_week(d, weekday, week_offset)`, `first_weekday_strictly_after(d, weekday)`, `last_day_of_month(y, m)`, `add_months(y, m, n)`, `is_valid_ymd(y, m, d)`, `days_between(a, b)`; no function reads the system clock.
  - Enumerations: `Confidence` = explicit, relative, inferred, unresolved, none (§7.2); `Reason` and `Rule` are thin enums built from `codes.DUE_REASONS` / `codes.DUE_RULES` (the ten Appendix B reasons and the 21 §9.5 rule ids: `iso_date`, `text_date`, `numeric_date`, `yearless_date`, `weekday_date`, `event_date`, `day_of_month`, `today`, `eod_alone`, `tomorrow`, `yesterday`, `weekday_next`, `weekday_this`, `weekday_next_week`, `monday_next_week`, `end_of_week`, `end_of_next_week`, `end_of_month`, `end_of_next_month`, `in_n_days`, `in_n_weeks`). The v0.4 "proposed catalogue" is gone: the spec now defines it (resolves B-3, rule-id part).
- **Tests to write first (TDD):**
  - `monday_of(2026-10-14) == 2026-10-12`; `monday_of(2026-10-18 Sun) == 2026-10-12`; year boundary `monday_of(2027-01-01 Fri) == 2026-12-28`.
  - `last_day_of_month`: 2026-02 ⇒ 28, 2028-02 ⇒ 29, 2026-11 ⇒ 30, 2026-12 ⇒ 31; `add_months(2026,12,1) == (2027,1)`.
  - `first_weekday_strictly_after(2026-10-14 Wed, Fri) == 2026-10-16`; `(…, Wed) == 2026-10-21` (strictly after); `(2026-10-17 Sat, Fri) == 2026-10-23`.
  - `days_between(2026-09-16, 2026-10-14) == 28`, `days_between(2026-03-01, 2026-10-14) == 227`, `days_between(2026-01-06, 2026-12-20) == 348`.
  - Weekday names of the dates listed in the M3 introduction come out right; tables contain no duplicate keys; the number-word table has exactly the keys `one`…`twelve`, `a`, `an`.
  - `Rule` members == the spec §9.5 ids (parsed from the spec's table in the test, read-only) and `Reason` members == Appendix B's list.
  - Static check: `dates.py` has no `today(`/`now(`/`time` import.
- **Done when:** tests green; constants importable by M2 and M5.

### T-M3-02 — Absolute date literal parser (ISO, textual, numeric, year-less, weekday prefix, invalid)
- **Size:** M
- **Depends on:** T-M3-01
- **Parallel with:** T-M3-03, T-M2-01..T-M2-11
- **Spec refs:** §9.3 rows 1-3 and the weekday+date row, §9.4, AC-06.2, AC-06.3, AC-06.8 (literal half), SC-13, FR-06.2, G-06
- **Files:** `src/actiontrack/domain/dates.py`, `tests/unit/test_date_literals.py`
- **Work:**
  - `parse_literal(text, date_order) -> Literal | Invalid` for a whole string, and `match_literal_at(text, pos, date_order)` returning the span (used by the locator): ISO `YYYY-MM-DD` and `YYYY/MM/DD` (optional `T…`/space time and zone suffix ignored; calendar date as written), `31 Oct 2026`, `Oct 31, 2026`, `31st October 2026`, `Oct 3rd`, `Sept`, year-less `31 Oct` / `Oct 31` / `31st October` / `Jan 6` / `Feb 29` (returned as `YearlessDate`, resolved later by T-M3-06), optional leading weekday (`Wed 16 Sep`, `Saturday 10 Oct`, `Mon, 14 September 2026`) captured as `weekday_claim` (with or without a year).
  - Numeric `NN/NN[/YY|YYYY]`: a part > 12 implies the order; both ≤ 12 and different ⇒ `date_order` if set else `Invalid(ambiguous_numeric)`; equal (`05/05`) ⇒ day = month; two-digit year ⇒ 20YY (§9.4).
  - Impossible dates (`2026-02-30`, `2026-13-01`, `31/04/2026`, `31 Apr`) ⇒ `Invalid(invalid_date)`; `Feb 29` with no year is a valid `YearlessDate` here and is resolved (or rejected as `invalid_date`) by T-M3-06.
  - Word boundaries everywhere: a bare number, `10%`, `6 months`, `5 customer` are not dates; month words need an adjacent day number (or an introducer, handled by T-M3-03/04).
- **Tests to write first (TDD):**
  - Spec rows: `2026-10-31`, `2026/10/31`, `31 Oct 2026`, `Oct 31, 2026` ⇒ 2026-10-31; `Oct 31`, `31 Oct`, `31st October` ⇒ year-less 10/31 (no year attached).
  - AC-06.2: `03/04/2026` no order ⇒ `ambiguous_numeric`; `dmy` ⇒ 2026-04-03; `mdy` ⇒ 2026-03-04; `13/04/2026` ⇒ 2026-04-13 without order; `04/13/2026` ⇒ 2026-04-13; `05/05` ⇒ year-less 5/5; `03/04/26` with `dmy` ⇒ 2026-04-03.
  - AC-06.3: `2026-02-30`, `2026-13-01`, `31/04/2026` ⇒ `invalid_date`.
  - AC-06.8/06.9 literals: `9 Oct`, `Jan 6`, `Mar 1`, `Oct 31`, `Sep 16`, `Feb 29`, `Aug 15`, `Aug 14` ⇒ `YearlessDate`; `Wed 16 Sep`, `Tue 16 Sep`, `Fri 16 Sep`, `Saturday 10 Oct` ⇒ year-less with `weekday_claim`.
  - Sample phrases: `10 Sep`, `18 Sep`, `2 Oct`, `25 Sep`, `12 Oct`, `9 Oct`, `23 Oct`, `15 Oct`, `7 Oct`, `15 October`, `Oct 3rd`, `Oct 5th`, `Wed 16 Sep`, `Saturday 10 Oct`, `2026-09-08`, `2026-10-16`, `14 Oct 2026`; non-dates `10%`, `6 months`, `5 customer`, `50 invoices`, `the three new leads`.
  - Weekday claim captured for `Fri Oct 17 2026` (to be rejected in T-M3-06).
- **Done when:** table of ≥45 literals green; meeting-date code (T-M2-12) can call it.

### T-M3-03 — Phrase recogniser: classify every §9.3 phrase shape (`vague` vs `range`)
- **Size:** M
- **Depends on:** T-M3-01, T-M3-02
- **Parallel with:** T-M3-04
- **Spec refs:** §9.3 (all rows), §9.2 (compound phrases, time-of-day words, month-only/fuzzy, number words, `vague` vs `range`), FR-06.1, AC-06.10 (range/vague clause), AC-06.11, G-20, H-04
- **Files:** `src/actiontrack/domain/dates.py`, `tests/unit/test_phrase_classify.py`
- **Work:**
  - `classify_phrase(text) -> Phrase` (a small AST: `kind` + parameters) for text that is already known to be a date phrase; it does no date arithmetic. Kinds: `absolute`, `yearless`, `weekday_date` (year-less or year-bearing date with a weekday claim), `day_of_month` (`the 15th`, `15th`), `today` (`today`, `tonight`, `this morning/afternoon`), `eod_alone` (`EOD`/`COB` alone), `tomorrow`, `yesterday`, `weekday` (bare, `Fri EOD`, `by Fri`), `this_weekday`, `next_weekday`, `monday_next_week` (`<Weekday> next week`), `end_of_week` (`end of week`, `EOW`), `end_of_next_week`, `end_of_month` (`end of month`, `EOM`), `end_of_next_month`, `in_n` (`in 3 days`, `within 2 weeks`, and number words `in two days`, `in a week`, `within three weeks`), `vague`, `range`.
  - Compound: leading/trailing time words (`EOD tomorrow`, `EOD Friday`, `Fri EOD`, `tomorrow 5pm`) are split off into `time_words` and kept for `due_raw`; the date part is classified.
  - **`range` (G-20, §9.2):** a phrase that names a definite calendar span but no single day: `this week`, `next week`, `this month`, `next month`, `week of Oct 19` (and explicit ranges). **`vague`:** anything from which neither a single day nor a definite span can be derived: `soon`, `later`, `ASAP`, `TBD`, `ongoing`, `Q4`, `end of Q4`, `mid-month`, `mid-October`, `for November` (month only), `after the launch`, and the quantity phrases `in a couple of days`, `in a few weeks`, `in a fortnight` (H-04). (v0.4 classified `next week`/`next month`/`this week` as `vague`; that is reversed.)
  - Unknown text ⇒ `None` (so the locator can reject non-dates such as `staging`). `after the launch` is accepted as a fixed literal phrase only; there is no general `after <X>` rule (note B-3, still OPEN).
- **Tests to write first (TDD):**
  - Every phrase in the §9.3 table's first column ⇒ expected `kind`, including the two final rows (`range` row and `vague` row) and both alternates of every row; `EOD tomorrow` ⇒ `tomorrow` + time word `EOD`; `Fri EOD` ⇒ `weekday` + `EOD`.
  - AC-06.10 clause: `next week`, `this week`, `next month`, `this month`, `week of Oct 19` ⇒ `range`; `soon`, `Q4`, `mid-October`, `for November`, `after the launch`, `later`, `ASAP`, `TBD`, `ongoing`, `mid-month`, `end of Q4` ⇒ `vague`.
  - AC-06.11 shapes: `in two days`, `in a week`, `within three weeks`, `in twelve days` ⇒ `in_n`; `in a couple of days`, `in a few weeks`, `in a fortnight` ⇒ `vague`; `in thirteen days` ⇒ `None` (only `one`…`twelve`, `a`, `an` are number words; `in 13 days` ⇒ `in_n`).
  - Sample phrases: `Friday`, `Thursday`, `Wed`, `Mon`, `Monday`, `Wednesday`, `Tuesday`, `tomorrow`, `EOD`, `EOD tomorrow`, `end of month`, `end of next week`, `next Tuesday`, `mid-October`, `November`, `Saturday 10 Oct`.
  - Negative: `staging`, `monitoring`, `Mon` inside `monitoring`, `the next review`, `cutover`, `CRM`, `accessibility`, `all admin accounts`, `10%` ⇒ `None`.
  - Case-insensitivity: `FRIDAY`, `Eod Tomorrow`, `IN TWO DAYS`.
- **Done when:** ≥70 phrase table green; every §9.3 phrase maps to exactly one kind.

### T-M3-04 — Due-phrase locator I: introducers, extent, separators, trailing phrases (spans index `description`)
- **Size:** M
- **Depends on:** T-M3-03
- **Parallel with:** T-M3-06, T-M3-07, T-M3-08
- **Spec refs:** §9.2 (introducers, separators, time-of-day, `before`/`until`), §8.2 (spans feed `task_text`), FR-01.4, FR-01.12, AC-06.6 (due_raw), AC-01.19 (due spans), G-12
- **Files:** `src/actiontrack/domain/dates.py`, `tests/unit/test_locator_basic.py`
- **Work:**
  - `locate(description) -> list[Located]` with `Located(raw, start, end, introducer, introducer_span, time_words, role, phrase)`; the input is the item's **cleaned description** and every span indexes into it (G-12; `raw_text` is never scanned). `raw` is the date phrase including time words but excluding the introducer (matches the `unresolved "next week"` wording of §4.2); spans of introducer, separator and phrase are all returned so T-M2-19 can delete them for `task_text` (§8.2) and the description core.
  - Introducers (case-insensitive): `by`, `due`, `due on`, `before`, `until`, `till`, `on`, `no later than`, `deadline:`, `(due: …)`, `[due: …]`, `📅`, or a trailing standalone date phrase; `before X`/`until X` resolve to X itself. An introducer only produces a candidate if what follows classifies as a date phrase via `classify_phrase`; otherwise it is skipped silently. `for` is an introducer only for the event forms handled in T-M3-05.
  - Ignore em dash, en dash, hyphen and `:` between introducer and phrase and before the introducer (`quote to Acme — by Friday`); orphaned separators are removed from `task_text` by T-M2-19, not here.
  - Mid-sentence `due` is recognised anywhere (`, results due 15 October`).
  - Trailing standalone phrase: a date phrase that is the last thing in the text after stripping trailing punctuation (`.`, `)`, `]`), with no introducer. It must be anchored to the end of the text, never matched mid-sentence. `after the launch` is found only by this rule (note B-3).
- **Tests to write first (TDD):**
  - Raw extraction on sample lines (introducer, raw): `09-01:9` (`by`, `Friday`); `09-01:10` (`by`, `2026-09-08`); `09-03:11` (`by`, `end of next week`); `09-14:9` (`by`, `Wed 16 Sep`); `09-21:14` (`by`, `EOD tomorrow`, time word `EOD`); `09-30:11` (`due`, `15 October`); `09-30:9` (`by`, `Oct 3rd`); `09-08:9` (`by`, `Friday`, em dash skipped); `10-08:9` (`by`, `EOD`); `10-05:9` (`by`, `next Tuesday`); `10-02:12` (`by`, `12 Oct`).
  - Span integrity: for every located phrase `description[start:end] == raw` and `description[introducer_span]` is the introducer text; the trailing `.` after `by Friday.` is outside every span.
  - Synthetic introducers: `(due: Oct 20)`, `[due: Fri]`, `deadline: 2026-10-20`, `no later than Friday`, `until Friday`, `till tomorrow`, `📅 2026-10-20`, `Fri EOD`, trailing standalone `Send deck tomorrow.`, trailing `Ship it after the launch`.
  - Skipped silently (no candidate): `by <date>`, `on staging` (09-17:10), `in CRM` (10-10:10), `on all admin accounts` (10-09:18), `before cutover` (09-03:12), `before the next review` (09-18:14), `for accessibility` (10-05:12).
  - Q4 anchor: `09-30-standup.md:12` (`share the Q4 roadmap draft by Friday`) yields exactly one candidate (`Friday`); `Q4` mid-sentence is not a candidate; same for `10-02:11`, `10-02:9`, `09-30:18`, `09-30:9`.
  - Leading `TBD` (09-18:15 `TBD to write the design-system changelog`) and `Someone`/`Team` produce no candidate.
- **Done when:** locator returns correct `raw`/span for the listed lines; results independent of text before the introducer; span integrity asserted.

### T-M3-05 — Due-phrase locator II: traps, not-due phrases, `on`/`for` event roles, multiple dates
- **Size:** M
- **Depends on:** T-M3-04
- **Parallel with:** T-M3-06, T-M3-07, T-M3-08
- **Spec refs:** §9.2 ([S] traps, event dates, `on <weekday>`), FR-06.2, AC-06.5, AC-06.7 (trap rows), AC-06.10, AC-06.11 (`on Friday` clause), SC-35, SC-64, W-DATE-NOT-DUE, G-07, H-02
- **Files:** `src/actiontrack/domain/dates.py`, `tests/unit/test_locator_traps.py`
- **Work:**
  - Not a date: `by` + bare number, `%`, or number without month/ordinal (`by 10%`); numbers followed by nouns (`6 months`, `50 invoices`, `5 customer quotes`) are never part of a phrase.
  - Not a due date: `from <date>`, `since <date>`, `starting <date>` ⇒ `role=not_due`; recorded for `due_raw_other` and `W-DATE-NOT-DUE`; they never make an item "dated"; their spans are still returned for `task_text` (§8.2).
  - **Event roles (G-07, H-02):** `on <date>` and a trailing date after `for` are used as the due date; **confidence follows the date, never the introducer**. A full (year-bearing) date after `on`/`for`/`by`/`due` (`on 2026-10-14`, `for 2026-10-14`, `due 14 Oct 2026`, `by 2026-10-14`) is an ordinary `role=due` phrase and resolves `explicit` with its own rule (`iso_date`/`text_date`/`numeric_date`). Only a **year-less** date phrase (with or without weekday) introduced by `on`/`for` (`on Oct 14`, `for Saturday 10 Oct`) gets `role=event` ⇒ later rule `event_date`, `inferred`. A **bare weekday after `on`** (`demo on Friday`) is an ordinary weekday phrase, `role=due` (rule `weekday_next`), never an event (H-02); `for <bare weekday>` is treated the same way (spec silent, note B-17). Month-only after `for` (`for November`) ⇒ phrase `vague` (T-M3-03).
  - Month words only count next to a day number or directly after an introducer; `may`, `mar`, `sat`, `sun`, `wed` as ordinary words (`Sam may send…`, `uptime monitoring`) are not dates.
  - Multiple dates: two or more *different* located due/event phrases (different normalised `raw`) ⇒ `multiple_dates`, all raws kept in order; identical raw repeated ⇒ one phrase (note B-3, still OPEN for "different raw, same date"). A not-due phrase beside a due phrase does not create `multiple_dates`.
- **Tests to write first (TDD):**
  - `10-02-finance-sync.md:10` (`cut cloud costs by 10% — proposal by 2026-10-09`): exactly one due phrase `2026-10-09`; `10%` ignored (AC-06.7).
  - `09-29:12` and `10-08:11` (`freeze invoice edits from 9 Oct`): zero due phrases, one `not_due` (`9 Oct`); expected `W-DATE-NOT-DUE`.
  - **G-07 / AC-06.10:** `10-06:10` (`demo the portal to Acme on 2026-10-14`) ⇒ one phrase, `role=due` (not `event`), date literal `absolute`; `by 2026-10-14`, `due 14 Oct 2026`, `for 2026-10-14` ⇒ `role=due`; `on Oct 14` ⇒ `role=event`; `09-29:11` (`schedule cutover for Saturday 10 Oct`) ⇒ `role=event`, phrase `Saturday 10 Oct`.
  - **H-02:** `demo on Friday` ⇒ one phrase `Friday`, `role=due`, kind `weekday`; `on staging` still skipped.
  - `10-14:10` (`for November`) ⇒ one `vague` phrase; `09-30:12` (`by mid-October`) ⇒ one `vague` phrase.
  - Non-dates: `09-10:9` (`for the last 6 months by tomorrow`) ⇒ only `tomorrow`; `09-14:12` (`collect 5 customer quotes for the landing page by end of month`) ⇒ only `end of month`; `10-12:10` (`for the first 50 invoices by Wednesday`) ⇒ only `Wednesday`; `09-24:11` (`5 customer quotes … by 2 Oct`) ⇒ only `2 Oct`.
  - SC-35 / AC-06.5 synthetic: `Send deck by Friday, results due 15 October` ⇒ two phrases ⇒ `multiple_dates`; `by Friday … by Friday` ⇒ one; `from 9 Oct, by Friday` ⇒ due `Friday` + not-due.
- **Done when:** all sample trap lines behave as listed; no sample line yields `multiple_dates`; no `role=event` is ever produced for a year-bearing date or a bare weekday.

### T-M3-06 — Resolver A: absolute, year-less (G-06 rule), weekday+date year search, day-of-month, §9.4 rules, missing M
- **Size:** M
- **Depends on:** T-M3-01, T-M3-02, T-M3-03
- **Parallel with:** T-M3-04, T-M3-05, T-M3-07, T-M3-08
- **Spec refs:** §9.3 rows 1-4 and weekday+date, "Year-less rule (G-06)", "Further worked cases", §9.4, §9.5, AC-06.1, AC-06.2, AC-06.3, AC-06.4, **AC-06.8**, **AC-06.9**, SC-10, SC-13, SC-63, W-DUE-BEFORE-MEETING, G-06, G-07
- **Files:** `src/actiontrack/domain/dates.py`, `tests/unit/test_resolver_absolute.py`
- **Work:**
  - `resolve_phrase(phrase, meeting_date, date_order, *, event=False) -> Resolution(date|None, confidence, rule, reason, warnings)`; no argument or import carries today (BR-05). `event=True` is set by the caller (T-M3-09) for a `role=event` phrase (year-less date under `on`/`for`, T-M3-05).
  - **Year-bearing date ⇒ itself, `explicit`, always, whatever the introducer or `event` flag** (G-07; rules `iso_date`/`text_date`/`numeric_date`); explicit date earlier than M ⇒ accepted, warning `W-DUE-BEFORE-MEETING`. Does not need M (rows without †): `2026-11-05` resolves when M is `None`.
  - **Year-less date without weekday (G-06 rule (2)):** use M's year; if the result is earlier than M by **≤ 60 days** (`PAST_ACCEPT_DAYS`) accept it as a past date with `W-DUE-BEFORE-MEETING`; if earlier by **more than 60 days** use the next year (no warning); a result on/after M is used as is. Confidence `inferred`, rule `yearless_date` (replaced by `event_date` when `event=True`). A date that does not exist in the chosen year (`Feb 29` in a non-leap year) ⇒ unresolved `invalid_date`. The v0.4 "first occurrence on/after M within 12 months" is **removed**.
  - **Year-less date with weekday (G-06 rule (1), `Wed 16 Sep`):** try the years M's, then M+1, then M−1 and take the first whose weekday matches the claim; none ⇒ unresolved `weekday_date_mismatch`; a result earlier than M gets `W-DUE-BEFORE-MEETING` (no 60-day cap in this branch). Confidence `inferred`, rule `weekday_date` (replaced by `event_date` when `event=True`). A year-bearing weekday+date that disagrees (`Fri Oct 17 2026`, a Saturday) ⇒ unresolved `weekday_date_mismatch`.
  - `the 15th`/`by 15th` † ⇒ first day-of-month N on/after M (rule `day_of_month`, `inferred`); if that month lacks day N ⇒ unresolved `invalid_date` (do not skip to the next month).
  - M `None` ⇒ every † row (all year-less forms, `the 15th`) unresolved `no_meeting_date`; invalid/ambiguous numeric propagate from T-M3-02.
- **Tests to write first (TDD):**
  - **AC-06.8 verbatim (expected values re-verified against the 2026 calendar; day gaps computed):**
    - M = 2026-10-12, `by 9 Oct` ⇒ 2026-10-09, `inferred`, rule `yearless_date`, `W-DUE-BEFORE-MEETING` (3 days earlier; not 2027).
    - M = 2026-12-20, `by Jan 6` ⇒ 2027-01-06, `inferred`, no warning (2026-01-06 is 348 days earlier).
    - M = 2026-10-14: `by Mar 1` ⇒ 2027-03-01 (2026-03-01 is 227 days earlier), no warning; `by Oct 31` ⇒ 2026-10-31, no warning; `by Sep 16` ⇒ 2026-09-16 + `W-DUE-BEFORE-MEETING` (28 days earlier); `by Feb 29` ⇒ unresolved `invalid_date`.
    - Boundary, M = 2026-10-14: `by Aug 15` (exactly 60 days earlier) ⇒ 2026-08-15 + `W-DUE-BEFORE-MEETING`; `by Aug 14` (61 days earlier) ⇒ 2027-08-14, no warning.
    - Not asserted (spec-silent, note B-17): `Feb 29` when M's year has no Feb 29 but the next year does (e.g. M = 2027-12-20 ⇒ next-year 2028 would be valid); the test file records the case as an explicit TODO with the literal reading (`invalid_date`) so the decision is visible.
  - **AC-06.9 verbatim:** M = 2026-10-14, `by Wed 16 Sep` ⇒ 2026-09-16 (a Wednesday) + `W-DUE-BEFORE-MEETING`, rule `weekday_date`; M = 2026-09-14, `Wed 16 Sep` ⇒ 2026-09-16, no warning; M = 2026-09-01 (Tue), `Tue 16 Sep` ⇒ 2025-09-16 (2026-09-16 is Wed, 2027-09-16 Thu, 2025-09-16 Tue) + `W-DUE-BEFORE-MEETING`; M = 2026-09-01, `Fri 16 Sep` ⇒ unresolved `weekday_date_mismatch`; M = 2026-09-29 (Tue), `Saturday 10 Oct` ⇒ 2026-10-10 with `event=True` ⇒ rule `event_date`, `inferred`, and with `event=False` ⇒ rule `weekday_date`; M `None` ⇒ unresolved `no_meeting_date`.
  - Year-order check: M = 2026-10-14, `Thu 16 Sep` ⇒ 2027-09-16 (M's year 2026 is a Wednesday, M+1 matches), no warning.
  - Table rows (M = 2026-10-14): `Oct 31`, `31 Oct`, `31st October` ⇒ 2026-10-31 inferred; `the 15th` ⇒ 2026-10-15; `31 Oct 2026` ⇒ explicit; `2026-02-30` ⇒ `invalid_date`; `Fri Oct 17 2026` ⇒ `weekday_date_mismatch` (2026-10-17 is a Saturday).
  - **G-07:** `2026-10-14` with `event=True` and `event=False` ⇒ both 2026-10-14, `explicit`, rule `iso_date` (never `event_date`, never `inferred`); `14 Oct 2026` ⇒ `text_date`; `on Oct 14` year-less with `event=True` ⇒ `inferred`, `event_date`.
  - Day-of-month edges: `the 15th` with M = 2026-10-16 ⇒ 2026-11-15; `the 31st` with M = 2026-11-05 ⇒ `invalid_date`.
  - Sample: `10 Sep` (M 2026-09-03) ⇒ 2026-09-10 inferred; `by 18 Sep` (09-14) ⇒ 2026-09-18; `by 2 Oct` (09-24) ⇒ 2026-10-02; `Oct 3rd` (09-30) ⇒ 2026-10-03; `15 October` (09-30) ⇒ 2026-10-15; `by 9 Oct` (10-05) ⇒ 2026-10-09 inferred (AC-06.7); `Wed 16 Sep` (09-14) ⇒ 2026-09-16 inferred, no warning; `Saturday 10 Oct` (09-29) ⇒ 2026-10-10; ISO `2026-09-08` ⇒ explicit.
  - AC-06.4: same results for any `today` (no parameter exists; also assert signature has no `today`).
  - SC-10: M `None`: `tomorrow` ⇒ `no_meeting_date`, `2026-11-05` ⇒ resolved explicit. Explicit `2026-10-09` with M = 2026-10-14 ⇒ resolved + `W-DUE-BEFORE-MEETING`.
- **Done when:** AC-06.2, AC-06.3, AC-06.8 and AC-06.9 rows exactly as in the spec; no sample year-less phrase resolves to a year other than 2026 and none of them raises `W-DUE-BEFORE-MEETING`.

### T-M3-07 — Resolver B: relative days and weekday rows
- **Size:** M
- **Depends on:** T-M3-01, T-M3-03
- **Parallel with:** T-M3-04, T-M3-05, T-M3-06, T-M3-08
- **Spec refs:** §9.3 (today/tomorrow/yesterday/weekday/same-weekday/this/next/Monday next week, further worked cases), §9.5 (rule ids), §9.2 (`on <weekday>`, H-02), AC-06.1, AC-06.11 (weekday clause), SC-11
- **Files:** `src/actiontrack/domain/dates.py`, `tests/unit/test_resolver_weekday.py`
- **Work:**
  - `today`, `tonight`, `this morning/afternoon` ⇒ M (`relative`, rule `today`); `EOD`/`COB` alone ⇒ M (rule `eod_alone`); `tomorrow` ⇒ M+1; `yesterday` ⇒ M-1 with `W-DUE-BEFORE-MEETING`; compound `EOD tomorrow` ⇒ tomorrow (the compound stores the rule of its date part, §9.5).
  - Bare weekday (`Friday`, `by Fri`, `Fri EOD`, and **`on Friday`**, H-02) ⇒ first such weekday strictly after M, rule `weekday_next`, `relative`; same weekday as M ⇒ unresolved `same_weekday`.
  - `this <weekday>` ⇒ that weekday in M's ISO week if ≥ M (same day allowed) else unresolved `this_weekday_past` (rule `weekday_this`).
  - `next <weekday>` ⇒ that weekday in the ISO week after M's week (even when M is Monday; rule `weekday_next_week`); `<Weekday> next week` ⇒ rule `monday_next_week` (same week arithmetic).
  - Rules use ISO Mon-Sun weeks only; no business-day logic; weekend M handled by the same arithmetic.
- **Tests to write first (TDD):**
  - Spec table, M = Wed 2026-10-14: `today` 10-14, `tomorrow` 10-15, `yesterday` 10-13 (+warning), `Friday` 10-16, `Wednesday` ⇒ `same_weekday`, `this Friday` 10-16, `next Friday` 10-23, `Monday next week` 10-19, `EOD tomorrow` 10-15; `this Wednesday` ⇒ 10-14; `next Wednesday` ⇒ 10-21; each asserts its §9.5 rule id.
  - Further worked cases: M = Fri 10-16: `Friday` ⇒ `same_weekday` (SC-11), `this Friday` 10-16, `next Friday` 10-23; M = Sat 10-17: `Friday` 10-23, `this Friday` ⇒ `this_weekday_past`; M = Sun 10-18: `next Friday` 10-23, `Monday next week` 10-19.
  - H-02: M = Wed 2026-10-14, `on Friday` ⇒ 2026-10-16, `weekday_next`, `relative`; M = Fri 2026-10-16, `on Friday` ⇒ `same_weekday`.
  - `next Tuesday` with M = Mon 2026-10-05 ⇒ 2026-10-13 (not 10-06) — sample `10-05-onboarding-retro.md:9`.
  - Sample weekdays (all 31 resolvable `by <weekday>` items; each differs from its meeting's weekday): e.g. `09-01:9` Friday ⇒ 09-04; `09-01:11` Thursday ⇒ 09-03; `09-03:10` Monday ⇒ 09-07; `09-08:10` Wed ⇒ 09-09; `09-18:11` Mon (M Fri) ⇒ 09-21; `10-10:9` Tuesday (M Sat 10-10) ⇒ 10-13; `10-02:11` Wednesday (M Fri) ⇒ 10-07; `09-02:9` tomorrow ⇒ 09-03; `09-21:14` EOD tomorrow ⇒ 09-22; `10-08:9` EOD ⇒ 10-08.
- **Done when:** every spec row and "further worked case" passes; no sample item produces `same_weekday` or `this_weekday_past`.

### T-M3-08 — Resolver C: end-of-period, offsets (digits and number words), month arithmetic, `range` / `vague`
- **Size:** M
- **Depends on:** T-M3-01, T-M3-03
- **Parallel with:** T-M3-04, T-M3-05, T-M3-06, T-M3-07
- **Spec refs:** §9.3 (`end of week`, `EOW`, `end of next week`, `end of month`, `EOM`, `end of next month`, `in N days`, `within N weeks`, `range` and `vague` rows), §9.2 (number words H-04, `vague` vs `range`), §9.1, §9.5, AC-06.1, AC-06.10 (range/vague clause), AC-06.11, SC-12, G-20, H-04
- **Files:** `src/actiontrack/domain/dates.py`, `tests/unit/test_resolver_periods.py`
- **Work:**
  - `end of week`/`EOW` ⇒ Friday of M's ISO week; M on Sat/Sun ⇒ unresolved `weekend_meeting`. `end of next week` ⇒ Friday of the week after M's week (no weekend rule).
  - `end of month`/`EOM` ⇒ last day of M's month; `end of next month` ⇒ last day of the following month with year rollover and leap-year handling.
  - `in N days`, `within N days` ⇒ M+N (rule `in_n_days`); `in N weeks`, `within N weeks` ⇒ M+7N (rule `in_n_weeks`). **N is a digit string or a number word `one`…`twelve`, or `a`/`an` (= 1, singular unit)** (H-04; resolves the number-word half of B-3): `in two days`, `in a week` = 7 days, `within three weeks`.
  - **Unresolved phrases:** `range` phrases (`next week`, `this week`, `next month`, `this month`, `week of Oct 19`) ⇒ unresolved reason `range`; `vague` phrases (`later`, `soon`, `ASAP`, `TBD`, `ongoing`, `Q4`, `end of Q4`, `mid-month`, `mid-October`, `for November`, `after the launch`, `in a couple of days`, `in a few weeks`, `in a fortnight`) ⇒ reason `vague`. Both: `due_rule = null`, `due_raw` kept, no date. (v0.4 resolved `next week`/`next month`/`this week` as `vague`; reversed by G-20/H-06.)
- **Tests to write first (TDD):**
  - Spec table, M = 2026-10-14: `end of week`/`EOW` 10-16, `end of next week` 10-23, `end of month`/`EOM` 10-31, `end of next month` 11-30, `in 2 weeks` 10-28, `within 2 weeks` 10-28, `in 3 days` 10-17.
  - **AC-06.11 (resolver half), M = 2026-10-14:** `in two days` ⇒ 2026-10-16 (`in_n_days`), `in a week` ⇒ 2026-10-21 (`in_n_weeks`), `within three weeks` ⇒ 2026-11-04 (`in_n_weeks`), all `relative`; `in a couple of days` ⇒ unresolved `vague`. More: `in twelve days` ⇒ 2026-10-26; `in 13 days` ⇒ 2026-10-27; `in a day` ⇒ 2026-10-15; `in a few weeks` and `in a fortnight` ⇒ `vague`.
  - M = Sat 10-17 `EOW` ⇒ `weekend_meeting`; M = 2026-10-31 `end of month` ⇒ 2026-10-31.
  - SC-12: M = 2026-02-10 `by end of month` ⇒ 2026-02-28; M = 2026-01-31 `end of next month` ⇒ 2026-02-28; M = 2026-12-15 `end of next month` ⇒ 2027-01-31; M = 2028-01-10 `end of next month` ⇒ 2028-02-29.
  - ISO week across year end: M = Wed 2026-12-30, `end of next week` ⇒ 2027-01-08; `Monday next week` ⇒ 2027-01-04.
  - Sample: `09-03:11` end of next week (M Thu 09-03) ⇒ 2026-09-11; `09-14:12` end of month (M 09-14) ⇒ 2026-09-30; `09-14:12` and `09-24:11` unaffected by the `5 customer quotes` text; `09-30:12` mid-October ⇒ unresolved `vague`; `10-14:10` for November ⇒ unresolved `vague`.
  - AC-06.10 clause: `next week`, `this week`, `next month`, `this month`, `week of Oct 19` ⇒ unresolved `range`; `soon`, `Q4`, `mid-October`, `for November`, `after the launch` ⇒ unresolved `vague`; no `range`/`vague` row stores a rule.
- **Done when:** all rows green; month arithmetic never raises on any M in 2024-2030 (loop test over every day).

### T-M3-09 — `resolve_due`: provenance fields, composition and the M2 analyzer adapter
- **Size:** M
- **Depends on:** T-M3-05, T-M3-06, T-M3-07, T-M3-08
- **Parallel with:** T-M3-10
- **Spec refs:** §9.2, §9.5 (catalogue and display), §7.2 (due fields), FR-01.5, FR-06.1, FR-06.2, AC-01.2 (date part), AC-06.4, AC-06.5, AC-06.6, **AC-06.10**, BR-05, W-DATE-NOT-DUE, W-DUE-BEFORE-MEETING, SC-64, G-07, G-12, G-20
- **Files:** `src/actiontrack/domain/dates.py`, `src/actiontrack/domain/models.py`, `src/actiontrack/parsing/items.py`, `tests/unit/test_resolve_due.py`
- **Work:**
  - `resolve_due(description, meeting_date, date_order) -> DueResult(due_raw, due_date, due_confidence, due_rule, due_reason, due_raw_other, raws, spans, warnings)`; the input is the cleaned `description` and all spans index it (G-12). Locate (T-M3-04/05), resolve the single due/event phrase, or compose `multiple_dates` (all raws kept), or `none`. `spans` carries every due, event, not-due phrase (with introducer and time words) for T-M2-19.
  - Field rules: dated ⇒ `due_confidence` explicit/relative/inferred as in §9.3/§9.5, `due_rule` a member of the §9.5 catalogue (`codes.DUE_RULES`), `due_reason` null; **a year-bearing date is always `explicit` regardless of introducer** (`on`/`for`/`by`/`due`, G-07; resolves B-1), `inferred` only for year-less dates and the year-less `on`/`for` event phrases (`event_date` replaces `yearless_date`/`weekday_date`); unresolved ⇒ `due_date` null, `due_confidence=unresolved`, **`due_rule` null**, `due_raw` kept, `due_reason` from Appendix B; no phrase ⇒ `due_confidence=none`, `due_raw` null; not-due phrase only ⇒ `none` + `due_raw_other` + `W-DATE-NOT-DUE`.
  - Display helper for §9.5: unresolved prints `?(raw excerpt)`, missing prints `-`.
  - Adapter implementing the M2 `ItemAnalyzer` hook (T-M2-15): fills `ParsedItem.due_hook` with `DueResult` and returns `due_spans` using the file's meeting date and the configured `date_order`.
  - Time-of-day words stay inside `due_raw` and never change the date; `due_raw` never contains the introducer.
- **Tests to write first (TDD):**
  - **AC-06.10 matrix (M = 2026-10-06 for the year-less row; each asserted for date, confidence, rule):** `Demo the portal on 2026-10-14` ⇒ 2026-10-14, `explicit`, `iso_date`; `Demo by 2026-10-14` ⇒ same; `Demo due 14 Oct 2026` ⇒ `explicit`, `text_date`; `Demo for 2026-10-14` ⇒ `explicit`, `iso_date`; `Demo on Oct 14` ⇒ 2026-10-14, `inferred`, `event_date`; (M = 2026-09-29) `Schedule cutover for Saturday 10 Oct` ⇒ 2026-10-10, `inferred`, `event_date`; property over all rows: no year-bearing date is ever `inferred`.
  - AC-06.6 field matrix for the sample items (raw, rule, confidence): `09-01:9` (`Friday`, `weekday_next`, relative); `09-01:10` (`2026-09-08`, `iso_date`, explicit); `09-03:9` (`10 Sep`, `yearless_date`, inferred); `09-14:9` (`Wed 16 Sep`, `weekday_date`, inferred); `09-21:14` (`EOD tomorrow`, `tomorrow`, relative, date 09-22); `09-29:11` (`Saturday 10 Oct`, `event_date`, inferred); `10-06:10` (`2026-10-14`, **`iso_date`, explicit**, SC-64); `10-02:10` (`2026-10-09`, `iso_date`, explicit); `10-05` customer quotes by `9 Oct` (`yearless_date`, inferred, 2026-10-09); `09-30:12` (`mid-October`, unresolved, `vague`, rule null); and every stored rule is in the §9.5 catalogue while every unresolved result has `due_rule is None` and a reason in Appendix B (AC-06.6).
  - `none`: `09-01:12` (`confirm cloud budget with finance`) ⇒ `none`, `due_raw` null; `09-29:12` ⇒ `none`, `due_raw_other == "9 Oct"`, `W-DATE-NOT-DUE`.
  - `10-03-sales-pipeline.md:6` (M None) ⇒ unresolved `no_meeting_date`, raw `Friday`; `untitled.md:2` same.
  - Multiple dates (SC-35) ⇒ unresolved `multiple_dates` with both raws.
  - Purity: result identical for repeated calls; function has no clock dependency.
  - Adapter: parsing `2026-09-01-quote-portal-kickoff.md` with the real analyzer attaches `due_date` 2026-09-04 to line 9; AC-01.2/SC-01: a note dated 2026-10-14 with `- [ ] Priya to send the revised quote by Friday.` ⇒ `due_date` 2026-10-16, `relative`; `spans` cover `by Friday` so that T-M2-19 yields `Send the revised quote` (with the owner span stubbed).
- **Done when:** AC-06.5, AC-06.6 and AC-06.10 asserted; `parse_document` with the adapter gives all 117 items a `DueResult`.

### T-M3-10 — Table-driven fixture from spec §9.3 and AC-06.1/06.7/06.8/06.9/06.10/06.11
- **Size:** M
- **Depends on:** T-M3-06, T-M3-07, T-M3-08
- **Parallel with:** T-M3-09
- **Spec refs:** §9.3, "Year-less rule (G-06)", "Further worked cases", §9.5, AC-06.1, AC-06.7, AC-06.8, AC-06.9, AC-06.10, AC-06.11, NFR-TEST-01, SC-11, SC-12, SC-13, SC-63, SC-64, SC-70 (date half)
- **Files:** `tests/unit/test_date_table.py`, `tests/fixtures/date_table.json`
- **Work:**
  - One JSON fixture, written by hand from the spec (not generated from the implementation): columns `id, source, meeting_date, text, date_order, expected_date, expected_confidence, expected_rule?, expected_reason?, expected_warnings`. Contents: every row of the §9.3 table (M = 2026-10-14), the whole "Further worked cases" paragraph (including the six year-less cases), every AC-06.7 item (with its own M), and every AC-06.8, AC-06.9, AC-06.10 and AC-06.11 expectation, plus the extra month/week edges from T-M3-08 and AC-06.2/06.3.
  - The test iterates the file, calls `resolve_due` on the full item text for AC-06.7/06.10/06.11 rows and `resolve_phrase`/locator for table rows; each failing row is reported by `id`.
  - A guard test asserts the fixture has ≥ 1 row for every clause (row-count check per group): the **23** rows of the §9.3 table incl. alternates; the **13** expectations of "Further worked cases" (M = Fri: 3; M = Sat: 3; M = 10-31: 1; year-less: 6); all **20** AC-06.7 items (the v0.4 count of 18 is superseded: spec v0.6 lists 20); **8** AC-06.8 rows (6 listed plus the 60/61-day boundary pair); **6** AC-06.9 rows; **15** AC-06.10 rows (4 explicit forms, 2 year-less inferred, 4 `range`, 5 `vague`); **6** AC-06.11 rows. Minimum 91 rows overall.
  - AC-06.7 rows (item text ⇒ expected): `09-01 Priya … by Friday` 2026-09-04; `09-01 Anita … by Thursday` 2026-09-03; `09-02 Rahul … by tomorrow` 2026-09-03; `09-03 Priya … by end of next week` 2026-09-11; `09-03 Karan … by 10 Sep` 2026-09-10 inferred; `09-03 Rahul … by Monday` 2026-09-07; `09-21 Sam … by EOD tomorrow` 2026-09-22; `09-14 Lena … by end of month` 2026-09-30; `09-14 Anita … by Wed 16 Sep` 2026-09-16 (`weekday_date`, inferred); `10-05 Rahul … by next Tuesday` 2026-10-13; `09-30 Meera … by Oct 3rd` 2026-10-03 (`yearless_date`, inferred); `10-05 Lena … publish the customer quotes by 9 Oct` 2026-10-09 inferred; `10-06 Meera … on 2026-10-14` 2026-10-14 (`iso_date`, **explicit**); `09-30 Lena … results due 15 October` 2026-10-15; `09-30 Sam … by mid-October` unresolved `vague`; `10-14 Lena … for November` unresolved `vague`; `09-29 Sam … for Saturday 10 Oct` 2026-10-10 (`event_date`, inferred); `09-29 Karan … from 9 Oct` none + `W-DATE-NOT-DUE`; `10-02 Sam … by 10% — proposal by 2026-10-09` 2026-10-09 explicit; `10-03 Meera … by Friday` unresolved `no_meeting_date`.
  - AC-06.8/06.9/06.11 rows are exactly those in T-M3-06, T-M3-08 and T-M3-12 (same M values, same expected dates and warnings); AC-06.10 rows are those of T-M3-08/09.
- **Tests to write first (TDD):** the fixture and runner are the first artefacts; they fail until T-M3-06..09 and T-M3-12 are complete.
- **Done when:** all rows pass; every expected value in the fixture was re-verified against the real 2026 calendar for spec v0.6 (done in this re-sync, see the Part B notes: AC-06.8, AC-06.9, AC-06.11 and the §9.3 year-less cases are arithmetically correct; no value is wrong).

### T-M3-11 — Sample sweep over all 117 items, purity guard and integration with M2 parsing
- **Size:** M
- **Depends on:** T-M3-09, T-M2-15, T-M2-19
- **Parallel with:** T-M3-10, T-M2-17
- **Spec refs:** AC-06.1, AC-06.4, AC-06.6, AC-06.7, AC-06.10, AC-01.2, AC-01.9, AC-01.19 (sample `task_text` invariants), SC-01, SC-10, SC-49, SC-64, BR-05, FR-06, G-07
- **Files:** `tests/integration/test_m3_sample.py`, `tests/golden/m3_sample_due.json`
- **Work:**
  - Parse the sample with the real `DueAnalyzer` and compare every one of the 117 items (file:line, `due_raw`, `due_date`, `due_confidence`, `due_rule`, `due_reason`) with a hand-authored golden. Check-sums carried over from the v0.4 classification and re-derived for v0.6 (they must be re-verified line by line when the golden is authored, because the sample folder was not re-read during this re-sync): 73 items resolved to a date, 4 unresolved (`09-30:12` and `10-14:10` as `vague`; `10-03:6` and `untitled.md:2` as `no_meeting_date`), 40 with `due_confidence=none` (38 with no phrase at all + 2 `from 9 Oct` not-due items with `W-DATE-NOT-DUE`); 73 + 4 + 40 = 117.
  - Of the 73: 31 by-weekday items (none `same_weekday`), 5 `tomorrow`, 2 `EOD`, 1 `EOD tomorrow`, 1 `next Tuesday`, 2 end-of-period (`end of next week`, `end of month`), 13 year-less dates (incl. `Wed 16 Sep`, `Oct 3rd`, `Oct 5th`, `15 October`, `9 Oct`), **17 year-bearing dates (16 ISO dates incl. `10% — proposal by 2026-10-09`, plus the `on 2026-10-14` item, which is now `iso_date`/`explicit` instead of the v0.4 `event_date`/`inferred`, G-07)**, 1 `for Saturday 10 Oct` event (`event_date`, inferred). Check: 31+5+2+1+1+2+13+17+1 = 73.
  - **Confidence totals over the 73 (G-07):** `explicit` = 17, `inferred` = 14 (13 year-less + 1 year-less event), `relative` = 42 (31+5+2+1+1+2); no year-bearing date is `inferred`; every stored rule is in the §9.5 catalogue; the 4 unresolved items have `due_rule` null.
  - Assert zero `W-DUE-BEFORE-MEETING` over the sample (no sample year-less date is earlier than its meeting); exactly two `W-DATE-NOT-DUE`.
  - Run the whole sweep with the process clock patched to three different fake `today` values (and with `date.today` replaced by a function that raises): results identical (AC-06.4, BR-05).
  - Traps that must not leak: no item containing `Q4`, `6 months`, `5 customer`, `50 invoices`, `on staging`, `in CRM`, `on all admin accounts` is flagged `multiple_dates`.
  - `task_text` interplay (AC-01.19): with the real due analyzer attached (owner spans stubbed to empty), no `task_text` contains its item's located due phrase or introducer, and the `from 9 Oct` items have `task_text` without `from 9 Oct`.
- **Tests to write first (TDD):** golden + sweep + clock-patch test above.
- **Done when:** the sweep is green and its category counts equal the numbers above.

### T-M3-12 — Number words and `on <weekday>` (AC-06.11 owner)
- **Size:** S
- **Depends on:** T-M3-05, T-M3-07, T-M3-08, T-M3-09, T-M2-15
- **Parallel with:** T-M3-10, T-M3-13
- **Spec refs:** **AC-06.11**, SC-70 (date half; the global-option placement half is AC-09.11, M1/M6), §9.2 (`on <weekday>`, number words), §9.5 (`in_n_days`, `in_n_weeks`, `weekday_next`), H-02, H-04
- **Files:** `src/actiontrack/domain/dates.py` (data only if not already complete), `tests/unit/test_number_words_on_weekday.py`
- **Work:** an end-to-end acceptance task over the pieces built in T-M3-03/05/07/08: number words `one`…`twelve`, `a`, `an` in `in/within <n> day(s)/week(s)`; quantity phrases `a couple of`, `a few`, `a fortnight` ⇒ `vague`; `on <weekday>` is an ordinary weekday phrase (`weekday_next`, `relative`, never `event_date`) with the same-weekday rule. Drives the full path `description` → `locate` → `resolve_due` and the `parse_document` adapter.
- **Tests to write first (TDD):**
  - **AC-06.11 verbatim (M = Wed 2026-10-14):** `in two days` ⇒ 2026-10-16; `in a week` ⇒ 2026-10-21; `within three weeks` ⇒ 2026-11-04 (14 Oct + 21 days), all `relative` with rules `in_n_days` / `in_n_weeks` / `in_n_weeks`; `in a couple of days` ⇒ unresolved `vague`, rule null; `Meera to demo on Friday` ⇒ 2026-10-16, rule `weekday_next` (not `event_date`), `relative`; with M = Fri 2026-10-16, `demo on Friday` ⇒ unresolved `same_weekday`.
  - Boundaries: `in twelve days` ⇒ 2026-10-26; `in thirteen days` ⇒ no phrase (`due_confidence=none`, number words stop at twelve; `in 13 days` ⇒ 2026-10-27 — a documented consequence of H-04, note B-17); `in an hour`/`in a month` ⇒ no phrase; case variants `IN TWO DAYS`, `Within Three Weeks`; `in a few weeks`, `in a fortnight` ⇒ `vague`.
  - Contrast: `demo on Oct 14` (year-less) ⇒ `event_date`, `inferred` (T-M3-09 matrix) while `demo on 2026-10-14` ⇒ `iso_date`, `explicit`.
  - SC-70 date half: parsing a note dated 2026-10-14 containing `- [ ] Meera to demo on Friday` gives `due_date` 2026-10-16, rule `weekday_next` (owner `Meera` is M4).
- **Done when:** AC-06.11's four statements are asserted by name; the fixture rows of T-M3-10 for AC-06.11 pass.

### T-M3-13 — Year-less rule acceptance and property tests (AC-06.8 / AC-06.9 / SC-63)
- **Size:** S
- **Depends on:** T-M3-06, T-M3-09, T-M2-15
- **Parallel with:** T-M3-10, T-M3-12
- **Spec refs:** **AC-06.8**, **AC-06.9**, SC-63, §9.3 ("Year-less rule (G-06)", "Further worked cases"), §9.4, W-DUE-BEFORE-MEETING, G-06
- **Files:** `tests/unit/test_yearless_rule.py`, `tests/integration/test_m3_yearless_notes.py`
- **Work:** acceptance and invariant tests that pin the G-06 rule beyond the example rows (the example rows themselves are owned by T-M3-06 and T-M3-10).
- **Tests to write first (TDD):**
  - **SC-63 (parse level):** two synthetic notes through `parse_document` with the real analyzer: one dated 2026-10-12 with `by 9 Oct` ⇒ due 2026-10-09, `inferred`, `W-DUE-BEFORE-MEETING` (not 2027); one dated 2026-12-20 with `by Jan 6` ⇒ 2027-01-06, no warning.
  - **Boundary sweep over every M from 2026-01-01 to 2027-12-31:** for the date exactly 60 days before M written as `Mon D` (skipping Feb 29, note B-17) ⇒ resolves ≤ M with `W-DUE-BEFORE-MEETING` and the year of that date; for the date 61 days before ⇒ next year's date, no warning.
  - **Properties over the same M range and 12 representative year-less dates (no Feb 29):** result year ∈ {M.year, M.year+1}; without a weekday, `M − result ≤ 60` days whenever `result < M`; `result ≥ M` ⇒ no warning; never raises.
  - **Weekday properties:** for every M in the range and every weekday+date claim built from a real date in one of the years M.year−1, M.year, M.year+1 (the only years the rule tries; Feb 29 excluded, note B-17), the result is resolved and its weekday equals the claim; a claim with a deliberately wrong weekday for all three candidate years ⇒ `weekday_date_mismatch`; any resolved result earlier than M carries `W-DUE-BEFORE-MEETING`.
  - AC-06.9 worked cases re-asserted through `resolve_due` text (`by Wed 16 Sep` at M = 2026-10-14; `Tue 16 Sep` at M = 2026-09-01 ⇒ 2025-09-16; `Fri 16 Sep` ⇒ `weekday_date_mismatch`; M `None` ⇒ `no_meeting_date`).
- **Done when:** the sweep and properties are green; the `Feb 29` decision (note B-17) is recorded in the test module docstring as spec-silent.

## Part B — notes for the integrator

### (a) Acceptance criteria covered by these tasks (re-synced to spec v0.6)

- **AC-01.1** (T-M2-02), **AC-01.2** (date part T-M3-09/T-M3-11; source path+line T-M2-09; owner part is M4), **AC-01.3** (T-M2-08/09; "counted under already-done-in-note" is M5), **AC-01.4** (T-M2-08), **AC-01.5** (T-M2-07/11), **AC-01.6** (T-M2-09), **AC-01.9** (T-M2-12/13 for warnings; items still imported T-M2-15; relative dates unresolved T-M3-06/09), **AC-01.11** (T-M2-02/04/18; command-level half is M5), **AC-01.12** (discovery half T-M2-02), **AC-01.13** (parse/discovery half: T-M2-03/17; the "ingested 33"/exit 5 numbers are M5), **AC-01.14** (T-M2-09/11/17), **AC-01.15** (T-M2-06/12/17), **AC-01.16** (T-M2-05/17), **AC-01.17** (T-M2-09/14/17).
- **AC-01.18 (new, v0.6):** owner **T-M2-20** (composite SC-69 fixture); clause owners: dot-prefixed files/dirs skipped and unreadable subdirectory ⇒ `W-DIR-UNREADABLE` = **T-M2-03**; unclosed `<!--` ⇒ `W-HTML-COMMENT-UNCLOSED` = **T-M2-07**; `- [ ]` ⇒ `W-CHECKBOX-EMPTY`, `- [ ]Priya…` silent, `[]X` ⇒ `W-CHECKBOX-NOSPACE` = **T-M2-08**. Ingest-level report/exit-code half = M5 (T-M5-17).
- **AC-01.19 (new, v0.6):** owner **T-M2-19** (`task_text` and the three-field contract); `raw_text` verbatim = T-M2-09, `description` full/NFC = T-M2-10, due spans = T-M3-04/T-M3-09; the `Lena (with Priya)` example is asserted here with stubbed owner spans and **must be re-asserted with the real owner-phrase locator in the M4 owner-phrase task** (OPEN cross-part item).
- **AC-01.20 (new, v0.6):** restricted YAML-subset parser and `W-FRONTMATTER-INVALID` with body items still imported = **T-M2-06** (+ T-M2-15 integration, T-M2-17 sample half); unparseable `Date:` ⇒ `W-MEETING-DATE-INVALID` and not `W-MEETING-DATE-MISSING` = **T-M2-12**.
- **AC-09.5** (parse half, T-M2-04), **AC-06.1** (T-M3-06/07/08/10), **AC-06.2** (T-M3-02/06), **AC-06.3** (T-M3-02/06), **AC-06.4** (T-M3-06/09/11), **AC-06.5** (T-M3-05/09), **AC-06.6** (fields produced in T-M3-09, rule ids and `due_rule` null for unresolved checked there; persistence is M5), **AC-06.7** (T-M3-10/11).
- **AC-06.8 (new):** owner **T-M3-06** (rows incl. 60/61-day boundary), pinned further by **T-M3-13** (boundary sweep, properties, SC-63) and T-M3-10. **AC-06.9 (new):** owner **T-M3-06**, plus T-M3-13 and T-M3-10. **AC-06.10 (new):** `explicit` vs `inferred` matrix = **T-M3-09** (with the locator roles of T-M3-05); `range` vs `vague` = **T-M3-03/T-M3-08**; fixture rows T-M3-10. **AC-06.11 (new):** owner **T-M3-12** (resolver T-M3-08, locator T-M3-05, weekday T-M3-07, fixture T-M3-10).
- Scenarios: SC-01 (date), SC-10, SC-11, SC-12, SC-13, SC-29, SC-33, SC-34, SC-35, SC-38, SC-41 (first half), SC-42, SC-43, SC-49, **SC-63** (T-M3-13), **SC-64** (T-M3-09/10/11), **SC-69** (T-M2-20), **SC-70 date half** (T-M3-12; the option-placement half, AC-09.11, is M1/M6). SC-62, SC-65–SC-68, SC-71, SC-72 belong to other parts (M7, M5, M5, M5, M7, M4/M5).
- Not covered here though listed in the plan's M2 exit criteria: AC-01.7, AC-01.8, AC-01.10, AC-01.12 (exit code 4 and "nothing written") and the "ingested/empty/failed" counters; they need the ingest service (M5). M2 produces the `ParsedFile.status` values they consume.
- Warnings emitted by M2/M3 code (the 22 parser-emitted `W-*` codes of Appendix B v0.6): `W-NO-FILES`, `W-SYMLINK-SKIPPED`, `W-DIR-UNREADABLE`, `W-EMPTY-FILE`, `W-NO-ITEMS`, `W-FRONTMATTER-INVALID`, `W-FENCE-UNCLOSED`, `W-HTML-COMMENT-UNCLOSED`, `W-LONG-LINE`, `W-CHECKBOX-IGNORED-REGION`, `W-CHECKBOX-NOSPACE`, `W-CHECKBOX-NONSTANDARD`, `W-CHECKBOX-EMPTY`, `W-NONCHECKBOX-CANDIDATES`, `W-MEETING-DATE-MISSING/INVALID/FROM-FILENAME`, `W-DATE-CONFLICT`, `W-DATE-WEEKDAY-MISMATCH`, `W-ATTENDEES-MISSING`, `W-DATE-NOT-DUE`, `W-DUE-BEFORE-MEETING`; errors `E-UNREADABLE`, `E-ENCODING`, `E-BINARY`, `E-TOO-LARGE`, `E-INTERNAL-PARSE`. Left to other layers: `W-MEETING-DATE-FUTURE` (ingest service, G-20), `W-OWNER-*`, `W-ATTENDEE-NAME-INCOMPLETE`, `W-NAME-DIACRITIC`, `W-STATUS-*`, `W-FILE-MISSING`.

### (b) Ambiguities, contradictions and gaps (status against spec v0.6)

**Date arithmetic verification (2026 calendar, re-run for v0.6).** All expected values in AC-06.7, §9.3, "Further worked cases", AC-06.8, AC-06.9 and AC-06.11 were recomputed and are correct. AC-06.8: M = 2026-10-12 `by 9 Oct` is 3 days earlier ⇒ 2026-10-09 + warning; M = 2026-12-20 `by Jan 6`: 2026-01-06 is 348 days earlier (> 60) ⇒ 2027-01-06; M = 2026-10-14 `by Mar 1`: 227 days earlier ⇒ 2027-03-01; `by Oct 31` is later ⇒ 2026-10-31, no warning; `by Sep 16` is 28 days earlier ⇒ 2026-09-16 + warning, as stated; `by Feb 29` ⇒ 2026 has no Feb 29; the boundary dates are 2026-08-15 (60 days before 10-14, accepted as past) and 2026-08-14 (61 days, next year ⇒ 2027-08-14). AC-06.9: 2026-09-16 is a Wednesday (09-14 is a Monday), 2025-09-16 is a Tuesday and 2027-09-16 a Thursday ⇒ `Tue 16 Sep` at M = 2026-09-01 ⇒ 2025-09-16, `Fri 16 Sep` matches no year ⇒ mismatch; 2026-10-10 is a Saturday. AC-06.11: 10-14 + 2 = 10-16, + 7 = 10-21, + 21 = 11-04; 2026-10-16 is a Friday. v0.4 checks still hold (2026-09-01 Tuesday, `Fri Oct 17 2026` is a Saturday, 2026 not a leap year ⇒ SC-12 2026-02-28). No wrong expected value found. The sample set was relocated on 2026-10-22 (`samples\notes\` holds 34 files, `notes\notes\` holds `scratch.md` and `untitled.md`; file contents unchanged), but when this note was written the originals could not be re-read in one place, so the file:line sample claims inherited from v0.4 could not be re-read in this re-sync; they are unchanged and must be re-checked when the golden files are authored.

1. **B-1 §9.3 vs §9.2 on `on <ISO date>`** — **RESOLVED (G-07, AC-06.10, H-02).** A year-bearing date is always `explicit` with its own rule (`iso_date`/`text_date`/`numeric_date`); `event_date`/`inferred` only for year-less `on`/`for` phrases; `on <weekday>` is an ordinary `weekday_next` phrase (H-02). Implemented in T-M3-05/06/09/12. (Residual: `for <bare weekday>` is not covered by H-02, see B-17.)
2. **B-2 Year-less weekday+date contradicts the year-less rule** — **RESOLVED (G-06, AC-06.8, AC-06.9).** Weekday+date: years M, M+1, M−1, first weekday match; year-less without weekday: M's year, ≤ 60 days past accepted else next year. Implemented in T-M3-06, pinned by T-M3-13. (Residual: `Feb 29` year choice, B-17.)
3. **B-3 Undefined vocabularies** — **PARTLY RESOLVED.** RESOLVED: `due_rule` catalogue (§9.5, G-20; T-M3-01, `codes.DUE_RULES`), `vague` vs `range` (§9.2, G-20, H-06; T-M3-03/08), number words (§9.2/§9.5, H-04; T-M3-08/12). **OPEN:** (i) `after the launch` is listed as `vague` but `after` is not an introducer: handled as a fixed literal found by the trailing-standalone rule only (no general `after <X>`); (ii) "two different date phrases" when the raws differ but resolve to the same date (`Oct 9` vs `2026-10-09`): defaulted to different raw ⇒ `multiple_dates`.
4. **B-4 `W-MEETING-DATE-FUTURE` needs today** — **RESOLVED (G-20, §8.1).** Computed by the ingest service from the injected clock; M2 neither emits it nor exports `is_future` (T-M2-12); asserted in M5 (T-M5-17).
5. **B-5 §8.1 contradicts itself on an invalid date** — **RESOLVED (G-21, AC-01.15, AC-01.20).** `W-MEETING-DATE-INVALID` only, never also MISSING (T-M2-12). The two sub-questions (invalid front-matter `date:` with a later valid `Date:` line ⇒ invalid stops the search; empty file ⇒ no metadata) remain implementation defaults consistent with §8.1, not spec statements.
6. **B-6 `W-CHECKBOX-NOSPACE` scope** — **RESOLVED (G-22, §8.2, AC-01.18).** `[]` is the only form that warns; `- [ ]Priya…` is silent (T-M2-08, T-M2-20).
7. **B-7 Missing codes for known situations** — **RESOLVED (G-20, Appendix B v0.6).** `W-HTML-COMMENT-UNCLOSED` (T-M2-07), `W-CHECKBOX-EMPTY` (T-M2-08), `W-DIR-UNREADABLE` (T-M2-03; no failure, exit code unchanged, so the v0.4 default `E-UNREADABLE` on the directory is withdrawn) and `W-ATTENDEE-NAME-INCOMPLETE` (M4/M5) now exist; the v0.4 defaults for the first two (silent remainder, `W-CHECKBOX-NONSTANDARD`) are withdrawn.
8. **B-8 Under-specified scopes** — **OPEN (spec unchanged).** (i) `Attendees:`/`Present:` lines: no 30-line limit stated; implemented as "anywhere outside ignored regions, first wins"; (ii) `W-NONCHECKBOX-CANDIDATES` (c) child plain bullets under a checkbox item are excluded by me; (iii) rule (3) of the date precedence lists ISO and `Month D, YYYY` for headings but not `D Month YYYY`; I accept all accepted forms; (iv) one warning per candidate assumed.
9. **B-9 "Hidden" means directories only** — **RESOLVED (G-22, §4.2, FR-01.11, AC-01.18).** Dot-prefixed files **and** directories are skipped silently; the Windows hidden attribute is ignored (T-M2-03, T-M2-20). The v0.4 decision to read dot-files is reversed.
10. **B-10 Subfolder `--no-recursive` hint when top level has some files** — **OPEN (spec unchanged).** §11/AC-01.13 describe only the zero-top-level-files case; the hint is emitted whenever the subfolder `.md` count is > 0 under `--no-recursive` (T-M2-03).
11. **B-11 What `description` contains** — **RESOLVED (G-12, FR-01.12, §8.2, §7.2, AC-01.19).** `description` = full cleaned NFC text; `task_text` = owner/co-owner/due/non-due phrases and orphaned separators removed (T-M2-10, T-M2-19). Locators now run on `description` so spans align (T-M3-04, T-M3-09).
12. **B-12 `raw_text` vs NFC** — **RESOLVED (G-22, §7.2, §8.2, AC-01.19).** `raw_text` verbatim, `description` NFC, `block_hash` over verbatim text (T-M2-09/10/19).
13. **B-13 No YAML library** — **RESOLVED (G-22, §8.1, AC-01.20).** Built-in restricted YAML-subset parser, no dependency; anything outside the subset ⇒ front matter absent + `W-FRONTMATTER-INVALID` (T-M2-06).
14. **B-14 Plan layout and dependency gaps** — **RESOLVED (P-A, P-B).** The catalogue now lives in `codes.py` (T-M1-02), M2 imports it; the M3 date-parser-before-T-M2-12 ordering is plan P-B. The front-matter reader and ingest-order key still sit in `parsing/markdown.py` and `parsing/discovery.py`/`domain/models.py` (not named in plan §2.3; harmless).
15. **B-15 Sample vs spec detail** — **OPEN (informational, unchanged).** `scratch.md` has `FF FE` at offset 3, so only the NUL rule is exercised by the sample; BOM-at-offset-0 and PNG-at-offset-0 rules are covered by synthetic fixtures; the sample never exercises `[]`, `[ ]X`, `[-]`, an unclosed fence/comment, front-matter errors, a BOM, a weekday mismatch, the filename-date fallback, `multiple_dates`, `same_weekday`, `ambiguous_numeric`, `W-DUE-BEFORE-MEETING`, dot-files, empty checkboxes or an unreadable directory; these are synthetic-only.
16. **B-16 Trailing-standalone rule vs sample text** — **OPEN (spec unchanged; task default retained).** §9.2's trailing standalone phrase is not defined precisely; T-M3-04 anchors it to the end of the text and requires an introducer elsewhere so the five `Q4` items and the `TBD` item stay single-phrase or phraseless.
17. **B-17 New gaps found in spec v0.6 (all OPEN, defaults chosen in the tasks):**
    - **`task_text` and the final period.** AC-01.19 expects `Send the revised quote` from `…by Friday.`, but §8.2 lists only `—`, `-`, `,`, `:` as orphaned separators; T-M2-19 also drops a sentence-final `.`/`;` that became adjacent to a deleted span. Whether a trailing period is removed when no phrase was deleted is unspecified (kept).
    - **§9.4 "Impossible dates ⇒ unresolved `invalid_date` + warning"** names a warning but Appendix B has no code for it; implemented as no extra warning (the unresolved reason is reported in the "Missing/unresolved due dates" section).
    - **`Feb 29` year choice (G-06).** "A date that does not exist in the chosen year ⇒ `invalid_date`" does not say how the year is chosen for a date that exists only in some years (e.g. M = 2027-12-20 `by Feb 29` could mean 2028-02-29). AC-06.8 only shows M = 2026-10-14. Not asserted; the sweep tests exclude Feb 29 (T-M3-06, T-M3-13).
    - **`for <bare weekday>`.** H-02 covers `on Friday`; `for Friday` is treated the same (ordinary weekday), since event semantics are defined only for dates (T-M3-05).
    - **Number words beyond twelve** (`in thirteen days`) are not date phrases (H-04 lists `one`…`twelve`, `a`, `an` only), so such an item has no due date while `in 13 days` resolves.
    - **Front-matter subset details.** `#` comments and duplicate keys are not mentioned in §8.1; tolerated comments, last-wins duplicates (T-M2-06).
    - **Empty checkbox edge cases:** `- []` alone (EMPTY, no NOSPACE) and an empty first line followed by indented text (still empty) are unspecified (T-M2-08).
    - **Dot-prefixed symlink order:** §4.2 reads "symlinks are skipped silently (symlinks with W-SYMLINK-SKIPPED)", which is self-contradictory; FR-01.11 gives `W-SYMLINK-SKIPPED`; the dot check runs first (a dot-link is silent) (T-M2-03).
    - **Year-less meeting `Date:`** (`Date: 16 Sep`) is not an accepted §8.1 format ⇒ treated as invalid (T-M2-12).
    - **Weekday branch has no 60-day cap:** `Tue 16 Sep` at M = 2026-09-01 yields 2025-09-16 (350 days earlier) with only a warning, while the no-weekday branch caps at 60 days. Explicit in the spec, so tested as written; flagged because it can produce surprising past dates.
    - **`inferred` is "only for year-less dates"** (§9.2) but §9.5 also gives `day_of_month` (`the 15th`) `inferred`; read as consistent (month-less and year-less).
    - **Untestable as stated on Windows:** AC-01.18's unreadable subdirectory cannot be reproduced reliably with `chmod`/ACLs, so the failure is injected into `os.scandir` (an ACL variant is `skipif`-guarded).
    - **Cross-part:** the real `Lena (with Priya) to update the plan` ⇒ `Update the plan` assertion needs the owner-phrase locator (M4) and is therefore split between T-M2-19 (stubbed spans) and the M4 task.

## Re-sync log (spec v0.6)

Re-synced on 2026-10-22 against `actiontrack-spec.md` v0.6 (change log Appendix E: G-01..G-32, E.2: H-01..H-06). Task IDs are stable; no task was removed or renumbered. Counts: **24 task bodies changed, 4 tasks added, 5 re-checked and left unchanged** (M2 now 20 tasks, M3 now 13; Part B total 33, was 29).

**Changed tasks (one-line reason each)**
- T-M2-01 — codes are imported from `codes.py` (T-M1-02, G-20, 30 W + 5 E codes) instead of defined here; `ParsedItem.task_text`, `TextSpan` added (G-12).
- T-M2-03 — dot-prefixed *files* now skipped too (G-22/AC-01.18); unreadable subdirectory ⇒ `W-DIR-UNREADABLE`, not `E-UNREADABLE` (G-20/§11).
- T-M2-06 — restricted YAML-subset parser is now specified behaviour with explicit reject cases (anchors, tags, nested maps, multi-line scalars) and no-dependency test (G-22, AC-01.20).
- T-M2-07 — unclosed HTML comment emits `W-HTML-COMMENT-UNCLOSED` (G-20, AC-01.18).
- T-M2-08 — `- [ ]` ⇒ `W-CHECKBOX-EMPTY` (not NONSTANDARD); `[ ]X` silent / `[]` only NOSPACE confirmed (G-20, G-22, AC-01.18).
- T-M2-09 — `raw_text` verbatim/`block_hash` over verbatim text, empty checkboxes never items, `task_text` hand-off (G-12, G-22, AC-01.19).
- T-M2-10 — `description` is the full cleaned NFC text; `task_text` split out (G-12; B-11 resolved; AC-01.19).
- T-M2-11 — unclosed-comment/fence remainder and empty boxes inside regions (G-20).
- T-M2-12 — `W-MEETING-DATE-INVALID` only (G-21, AC-01.20 clause); `is_future` removed, `W-MEETING-DATE-FUTURE` moved to the ingest service (G-20); front-matter rejection feeds tier 1.
- T-M2-13 — `W-ATTENDEE-NAME-INCOMPLETE` now in Appendix B (still M4/M5); front-matter-rejected case added.
- T-M2-15 — analyzer hook takes `description` and returns spans; `task_text` computed; new tests for `W-CHECKBOX-EMPTY` and AC-01.20 body survival; depends on T-M2-19.
- T-M2-17 — golden aggregates extended (new zero-count codes, front-matter subset half, `task_text` invariants).
- T-M2-18 — robustness cases for `<!--` ×50 000, front-matter subset, `derive_task_text`; empty-checkbox fuzz invariant; no-YAML static check.
- T-M3-01 — rule-id catalogue now the spec §9.5 list (`codes.DUE_RULES`), number-word and 60-day constants, range words (G-06, G-20, H-04).
- T-M3-02 — year-less literals incl. `Jan 6`, `Mar 1`, `Feb 29`, weekday claims with/without year (G-06, AC-06.8/06.9).
- T-M3-03 — `range` vs `vague` split redefined (`next week`/`this week`/`next month` are now `range`), number words and quantity phrases (G-20, H-04).
- T-M3-04 — locator runs on `description`, returns introducer spans and span-integrity tests for `task_text` (G-12); `after the launch`.
- T-M3-05 — year-bearing dates after `on`/`for` are ordinary `explicit`; event role only for year-less dates; `on <weekday>` ordinary (G-07, H-02, AC-06.10/06.11).
- T-M3-06 — year-less rule replaced (M's year + 60-day window; weekday+date year search M, M+1, M−1), AC-06.8/06.9 rows with recomputed values (G-06).
- T-M3-07 — rule ids per catalogue, `on <weekday>` (H-02).
- T-M3-08 — `range` vs `vague` reasons, number words, vague quantity phrases (G-20, H-04, AC-06.11).
- T-M3-09 — `on 2026-10-14` is `iso_date`/`explicit` (G-07, SC-64, B-1 resolved); AC-06.10 matrix; unresolved ⇒ `due_rule` null (AC-06.6); spans for `task_text`.
- T-M3-10 — fixture extended to AC-06.8/06.9/06.10/06.11 and the six year-less further cases; AC-06.7 now 20 items; guard counts per group.
- T-M3-11 — sample check-sums updated (17 explicit, 14 inferred, 42 relative; `on 2026-10-14` moves from event to ISO), task_text interplay.

**Re-checked, no change needed:** T-M2-02, T-M2-04, T-M2-05, T-M2-14, T-M2-16 (spec v0.6 text for discovery filter/order, reader, decoding, non-checkbox candidates and ingest ordering is unchanged).

**New tasks**
- T-M2-19 — `task_text` derivation and the `description`/`raw_text` contract (AC-01.19).
- T-M2-20 — AC-01.18 composite acceptance fixture (SC-69).
- T-M3-12 — number words and `on <weekday>` (AC-06.11, SC-70 date half).
- T-M3-13 — year-less rule acceptance, boundary sweep and properties (AC-06.8/06.9, SC-63).

**ACs now owned (previously without a task):** AC-01.18 → T-M2-20 (clauses T-M2-03, T-M2-07, T-M2-08); AC-01.19 → T-M2-19 (T-M2-09, T-M2-10, T-M3-04/09; M4 re-assertion open); AC-01.20 → T-M2-06 + T-M2-12; AC-06.8 → T-M3-06 + T-M3-13; AC-06.9 → T-M3-06 + T-M3-13; AC-06.10 → T-M3-09 (+T-M3-03/05/08); AC-06.11 → T-M3-12. Scenarios newly owned: SC-63 (T-M3-13), SC-64 (T-M3-09/10/11), SC-69 (T-M2-20), SC-70 date half (T-M3-12).

**Integrator notes B-1..B-16 against spec v0.6:** RESOLVED — B-1 (G-07, H-02), B-2 (G-06), B-4 (G-20), B-5 (G-21), B-6 (G-22), B-7 (G-20), B-9 (G-22), B-11 (G-12), B-12 (G-22), B-13 (G-22), B-14 (P-A, P-B); PARTLY RESOLVED — B-3 (rule ids and `vague`/`range`: G-20/H-06; number words: H-04; `after the launch` and same-date-different-raw OPEN); OPEN — B-8, B-10, B-15, B-16. New OPEN note: B-17.
