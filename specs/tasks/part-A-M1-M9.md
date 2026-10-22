## Milestone M1 — Foundations

Conventions used by every task in this fragment (defined once in T-M1-01, referenced afterwards):
- **AC marker:** every test that proves an acceptance criterion carries `@pytest.mark.ac("AC-xx.y", ...)` (scenario IDs: `@pytest.mark.sc("SC-37")`). The traceability gate (T-M9-05) reads these markers.
- **Python:** all commands use the full interpreter path `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` (not on PATH of already-running shells).
- **Spec version:** this part is synced to spec **v0.6** (plan v0.4). Exit codes are **0-9** (9 = cancelled by user); `EXIT_HANDOFF_PREPARED = 0` is one named constant. Global options are accepted before **and** after the subcommand (H-03). See "Re-sync log (spec v0.6)" at the end of the file.
- **New modules not in plan section 2.3** (all registered in plan v0.4 except `gmail/allowlist.py`, which only holds two constants shared by config validation and the M7/M8 gateway): `src/actiontrack/errors.py`, `src/actiontrack/codes.py`, `src/actiontrack/io.py`, `src/actiontrack/logging_setup.py`, `src/actiontrack/report/sanitize.py`, `src/actiontrack/store/lock.py`, `src/actiontrack/store/migrations/*.sql`, `src/actiontrack/gmail/allowlist.py`, `tools/` (M9 scripts).

### T-M1-01 — Create project skeleton, packaging and test harness
- **Size:** S
- **Depends on:** none (needs plan P-1 done; P-1b `tzdata`/`pytest`/`pipx` install must succeed)
- **Parallel with:** -
- **Spec refs:** D-03/D-12, NFR-MAINT-01, NFR-TEST-02, NFR-PORT-01; plan 2.2 (Packaging), PD-3, PD-5
- **Files:** `pyproject.toml`, `src/actiontrack/__init__.py`, `src/actiontrack/__main__.py`, `src/actiontrack/cli.py` (placeholder `main(argv) -> int`), `tests/conftest.py`, `tests/unit/`, `tests/integration/`, `tests/golden/`, `tests/fixtures/notes/` (empty placeholder; sample copy is added by M2), `.gitignore`
- **Work:**
  - `pyproject.toml`: `requires-python = ">=3.12"`, runtime dependency only `tzdata`, optional extra `dev = [pytest]`, `[project.scripts] actiontrack = "actiontrack.cli:main"` (main returns int; wrapper calls `sys.exit`), src layout, version single-sourced (`actiontrack.__version__`).
  - `python -m actiontrack` works (`__main__.py`).
  - `tests/conftest.py`: register markers `ac(*ids)` and `sc(*ids)` (`--strict-markers`), fixtures `tmp_db_path`, `tmp_workdir` (chdir into tmp dir so default `./.actiontrack/` never touches the repo), and a fixture that scrubs `ACTIONTRACK_DB/CONFIG/TODAY/NOW` and `NO_COLOR` from the environment.
  - A `conftest` guard fixture that fails any test which writes under `samples\notes` or `notes\notes` (BR-01); the real sample is only ever read or copied.
  - An autouse **socket guard** fixture (patches `socket.socket.connect`/`create_connection` to raise) so no test can open a network connection; tests of the real gateway use an injected fake transport only (GM-02, NFR-TEST-02, AC-09.10; completed by T-M9-11). A test may opt out only with an explicit `@pytest.mark.network` that the release gate refuses to run.
  - Document in a short `DEVELOPING` section of the (later) README-stub: create venv with the full Python path, `pip install -e .[dev]`, run `pytest`.
  - Verify `pipx install .` from a local path in a scratch environment works and `actiontrack --version` runs (record result; full release check is T-M9-06).
- **Tests to write first (TDD):**
  - `test_package_importable`: `import actiontrack` exposes `__version__` matching `pyproject`.
  - `test_main_module_runs`: `python -m actiontrack` in a subprocess exits with a defined code (placeholder) without traceback.
  - `test_marker_registration`: a dummy test using `@pytest.mark.ac("AC-00.0")` passes under `--strict-markers`.
  - `test_socket_guard_blocks_connect` (supports AC-09.10): a test that tries `socket.create_connection(("127.0.0.1", 9))` fails with the guard's error.
- **Done when:** `pip install -e .[dev]` succeeds in a fresh venv; `pytest` runs green; `actiontrack` console script resolves; `pipx install .` smoke documented; no dependency other than `tzdata` in `[project.dependencies]`.

### T-M1-02 — Define exit codes, error hierarchy and warning/error code registry
- **Size:** S
- **Depends on:** T-M1-01
- **Parallel with:** T-M1-03, T-M1-06, T-M1-10
- **Spec refs:** spec 4.1 (exit-code table incl. exit 9 and `EXIT_HANDOFF_PREPARED`), Appendix B (v0.6 catalogue, G-20), NFR-MAINT-02, NFR-USE-01, FR-09.2, FR-04.8/04.11, H-01
- **Files:** `src/actiontrack/errors.py`, `src/actiontrack/codes.py`, `tests/unit/test_errors.py`, `tests/unit/test_codes.py`
- **Work:**
  - `errors.py`: `enum ExitCode` with exactly the **ten** values 0..9 of spec 4.1 (9 = cancelled by user: `followup` confirmation or repeat-draft prompt declined, nothing created). Module constant `EXIT_HANDOFF_PREPARED = ExitCode.OK` (= 0), the **one** place that decides the exit value of a prepared handoff (spec 4.1 "Handoff exit value"); every later task refers to the constant, never to a literal 0. `ActionTrackError(Exception)` carrying `exit_code`, `what`, `where` (file/key/line, optional), `next_step` (NFR-USE-01: what happened, where, what to do next) and a `format()` that renders those three parts on one or two lines. Subclasses: `UsageError(2)`, `NotFoundError(3)`, `EnvError(4)` (also used for: handoff directory/file cannot be written, a database locked by another process, an unopenable `--log-file`; H-01), `IngestFailuresError(5)` (not raised, used for end-of-run mapping), `AmbiguousOwnerError(6)`, `PreconditionError(7)`, `DraftNotCreatedError(8)`, `CancelledByUser(9)` (message "Cancelled. Nothing created.").
  - `codes.py`: constants (data, not scattered literals, NFR-MAINT-02) for every `E-*` and `W-*` code in **Appendix B v0.6** (which now includes `W-ATTENDEE-NAME-INCOMPLETE`, `W-HTML-COMMENT-UNCLOSED`, `W-CHECKBOX-EMPTY`, `W-DIR-UNREADABLE`) and every due-unresolved reason listed there (`no_meeting_date`, `vague`, `range`, `same_weekday`, `this_weekday_past`, `weekend_meeting`, `ambiguous_numeric`, `invalid_date`, `weekday_date_mismatch`, `multiple_dates`), grouped in frozensets `ERROR_CODES`, `WARNING_CODES`, `DUE_REASONS`; also the `due_rule` id catalogue of spec 9.5 as `DUE_RULES` (data; used by M3 and AC-06.6) and the ItemLink reasons (`ItemLinkReason` incl. `outside_window`, `recurrence`, `owner_unresolved`) kept separate from warning codes. Helper `is_file_failure(code)` (E-* => file failure, exit 5; W-* => warning, exit 0, spec 11). Also records which component emits which warning (parsers / ingest service for `W-MEETING-DATE-FUTURE` / ingest-resolution layer for `W-OWNER-*`, `W-ATTENDEE-NAME-INCOMPLETE`, `W-NAME-DIACRITIC`, `W-STATUS-*`, `W-FILE-MISSING`; Appendix B note) as a mapping used by T-M9-09.
  - No business logic; this is the single place later milestones import codes from.
- **Tests to write first (TDD):**
  - `test_exit_code_values`: each enum member equals the number in spec 4.1; no duplicates; 0..9 contiguous; `EXIT_HANDOFF_PREPARED == 0` and is referenced as a constant (grep test: no module compares or returns a bare `0` for a handoff).
  - `test_error_format_has_what_where_next`: an `EnvError` formats all three parts (NFR-USE-01); `CancelledByUser` carries exit 9.
  - `test_codes_match_appendix_b`: parse Appendix B from `actiontrack-spec.md` (read-only) and assert the registered sets equal the listed codes exactly in both directions (the catalogue is now complete, so it is no longer a floor; fail lists missing and extra codes) and that the four G-20 codes are present.
  - `test_due_reasons_and_rules_match_spec`: `DUE_REASONS` equals Appendix B's list; `DUE_RULES` equals the 9.5 catalogue ids (parsed from the spec tables).
  - `test_e_codes_are_file_failures`: `is_file_failure("E-BINARY")` true, `"W-EMPTY-FILE"` false, `"W-DIR-UNREADABLE"` false (exit code unchanged).
- **Done when:** tests pass; no other module defines exit-code integers or code strings inline (grep check in the test for `sys.exit(` and bare numeric exits outside `cli.py`).

### T-M1-03 — Implement Clock, `--today` override and time-zone resolution
- **Size:** M
- **Depends on:** T-M1-01, T-M1-02
- **Parallel with:** T-M1-04, T-M1-06, T-M1-10
- **Spec refs:** BR-02, BR-03 (today semantics), BR-18, AC-09.3, AC-09.4 (timestamps half), SC-37, EC-17, FR-09.1 (`timezone`, fixed-offset default, G-10), spec 4.1 (`ACTIONTRACK_NOW`, G-13), plan 2.2 (zoneinfo + tzdata)
- **Files:** `src/actiontrack/clock.py`, `tests/unit/test_clock.py`
- **Work:**
  - `Clock` protocol: `now_utc() -> datetime` (aware, UTC), `today(tz) -> date`, `tz_name`. `SystemClock` uses `datetime.now(timezone.utc)`; `FixedClock(instant_utc)` for tests (instant injection is required for AC-09.3, which fixes a UTC instant, not only a date).
  - **`ACTIONTRACK_NOW` (G-13, test-only):** an environment variable holding a UTC ISO-8601 instant (`2026-10-22T09:30:00Z`); when set, the process clock returns exactly that instant for **every** stored or printed timestamp (`created_at`, `updated_at`, `completed_at`, handoff `created_at`, handoff file name, ItemEvent `ts`, run `started_at`/`finished_at`). Parse strictly; invalid value => `UsageError` exit 2 naming the variable. It is documented as test-only (not in `--help` option list, listed under "Environment" only). `--today` still wins for the calendar date; when only `ACTIONTRACK_NOW` is set the date is derived from that instant in the configured zone (assumption A-T1, see notes). Without `ACTIONTRACK_NOW` stored timestamps come from the real clock and are outside the AC-09.4 guarantee.
  - `--today YYYY-MM-DD` / `ACTIONTRACK_TODAY`: parse strictly (`date.fromisoformat`, reject `2026-02-30` and non-ISO forms) => `UsageError` exit 2 naming the value and format; flag wins over env. An override sets the *calendar date* only; evaluated once per invocation (BR-02) and the resolved date plus zone label are exposed as `RunContext.today`, `RunContext.tz_label` for later output headers ("As of 2026-10-22 (tz Asia/Kolkata)").
  - Time zone resolution: configured IANA name via `zoneinfo.ZoneInfo` (requires `tzdata` on Windows); unknown name => `EnvError` exit 4 naming the key `timezone`; missing `tzdata` => exit 4 with remediation (`pip install tzdata`).
  - Default zone when `timezone` is unset (FR-09.1, G-10, AC-09.3): **no IANA lookup is attempted on any platform.** The zone is a fixed-offset label derived from the operating system's *current* UTC offset (e.g. `UTC+05:30`, `UTC-08:00`, `UTC`), obtained through an injectable `os_offset_provider()` (default: `datetime.now().astimezone().utcoffset()`), has no DST rules, and is the string printed wherever the zone is printed (`list` header, JSON `timezone`, email "as of"). On this fallback exactly one line recommending `timezone = "Asia/Kolkata"`-style configuration is written to stderr per invocation (suppressed by `--quiet`, spec 4.1). `RunContext.tz_label` carries either the IANA name or the offset label. (Integrator note b-1 is resolved by the spec.)
  - Determinism helper: `now_utc()` is the single source for every stored timestamp; with `ACTIONTRACK_NOW` set the stored values are reproducible and appear identically in `--format json` output; without it they are real-clock values and never part of table output.
- **Tests to write first (TDD):**
  - `test_today_kolkata_at_20z` (AC-09.3, SC-37 family): `FixedClock(2026-10-21T20:00Z)` with `Asia/Kolkata` => `2026-10-22`; with `America/Los_Angeles` => `2026-10-21`.
  - `test_today_los_angeles_at_03z` (SC-37): `2026-10-22T03:00Z` + LA => `2026-10-21`.
  - `test_today_override_flag_beats_env_beats_clock`.
  - `test_today_override_invalid` (`2026-02-30`, `22-10-2026`, `today`) => exit 2.
  - `test_unknown_timezone_exit4_names_key`.
  - `test_dst_boundary_dates` (zone `America/Los_Angeles` at 2026-11-01T08:30Z and 2026-03-08T10:30Z) returns the expected local calendar day.
  - `test_no_timezone_uses_fixed_offset_label` (AC-09.3, third sentence): `timezone` unset, injected OS offset `+05:30` and instant 2026-10-21T20:00Z => zone label `UTC+05:30`, today `2026-10-22`; offset `-08:00` => label `UTC-08:00`, today `2026-10-21`; offset 0 => `UTC`; stderr carries the one-line recommendation to set `timezone` exactly once; no IANA lookup is attempted (assert `ZoneInfo` never called for the default).
  - `test_actiontrack_now_fixes_stored_instant` (AC-09.4 timestamps half): `ACTIONTRACK_NOW=2026-10-22T09:30:00Z` => `now_utc()` is that instant on two calls; invalid value => exit 2; unset => real clock advances.
- **Done when:** AC-09.3 and SC-37 tests pass using only injected clocks (no sleeping, no reliance on machine zone), including the no-`timezone` offset-label case; invalid `--today` yields exit 2 with message naming the value; `ACTIONTRACK_NOW` is honoured by the one clock.

### T-M1-04 — Load and validate the configuration file (non-roster keys)
- **Size:** M
- **Depends on:** T-M1-02
- **Parallel with:** T-M1-03, T-M1-06, T-M1-10
- **Spec refs:** FR-09.1, FR-09.2, AC-09.1, **AC-09.10 (config half)**, **AC-05.8 (config key)**, Appendix C, D-02, GM-02 (hard-coded host, `google_mcp_url` validated only; G-16), FR-10.2, plan 2.3 `config.py`
- **Files:** `src/actiontrack/config.py`, `src/actiontrack/gmail/allowlist.py`, `tests/unit/test_config.py`, `tests/fixtures/config/valid_full.toml`, `tests/fixtures/config/bad_*.toml`
- **Work:**
  - Discovery order: `--config <path>` > `ACTIONTRACK_CONFIG` > `./actiontrack.toml` if present > built-in defaults. An explicitly given path that does not exist or is unreadable => exit 4 (see integrator note b-4); an absent default file is silently fine.
  - Parse with `tomllib` opened in binary mode (UTF-8 enforced by tomllib); TOML syntax error => exit 4 with file, line/col and reason.
  - Strict schema as frozen dataclasses: top-level `timezone` (str, IANA, default unset), `date_order` ("dmy"|"mdy"|"" ; "" and absent both mean unset), `[limits]` `max_file_bytes` (int > 0, default 5242880), `dup_window_days` (int >= 0, default 60), `fuzzy_threshold` (float in (0, 1], default 0.85), `[owners]` `attendee_disambiguation` (bool, default true), `[gmail]` `channel` ("handoff"|"google"|"fake", default "handoff"), `handoff_dir` (default `.actiontrack/handoff`), `google_mcp_url` (https URL whose host is exactly `gmailmcp.googleapis.com`), `include_signature` (bool, false), `timeout_seconds` (number > 0, 30), `verify_after_create` (bool, true), `[[person]]` handled by T-M1-05.
  - **GM-02 host rule (G-16):** `gmail/allowlist.py` defines the two constants `ALLOWED_MCP_HOST = "gmailmcp.googleapis.com"` and `ALLOWED_TOOLS = frozenset({"create_draft", "get_draft"})` **in code**; config never supplies or widens them. `google_mcp_url` is only *validated* against `ALLOWED_MCP_HOST` (any other host, `http://`, userinfo, or a non-default port trick such as `gmailmcp.googleapis.com.evil.example` => `EnvError` exit 4 naming the key `gmail.google_mcp_url`); the key's absence is fine. The M7/M8 gateway imports these same constants (single source, asserted by T-M9-01/T-M9-11).
  - The OAuth endpoints (`accounts.google.com`, `oauth2.googleapis.com`) and the client-secret storage are **not** config here; they are fixed in T-M8 (G-32) and never appear in `actiontrack.toml` (FR-10.2).
  - **Unknown keys and wrong types are errors** (AC-09.1: "invalid config key => exit 4 naming key"): message format `config <file>: key "limits.max_file_byte": unknown key (did you mean "max_file_bytes"?)`; report all errors found in one pass (list), then exit 4; no partial operation (FR-09.2).
  - Relative paths (`handoff_dir`) are resolved against the current directory, matching the DB default (see integrator note b-4); store the resolved absolute path in the config object.
  - Timezone validity is checked here by calling the T-M1-03 resolver.
  - Config is loaded lazily per command (T-M1-14) so `--help`/`--version` never fail on a bad config.
- **Tests to write first (TDD):**
  - `test_unknown_key_exit4_names_key` (AC-09.1) for top-level and nested tables.
  - `test_wrong_type_exit4` (`max_file_bytes = "5MB"`), `test_fuzzy_threshold_out_of_range`, `test_date_order_invalid_value`.
  - `test_defaults_when_no_file` equals Appendix C defaults.
  - `test_appendix_c_example_loads`: the Appendix C TOML block (copied into a fixture) loads without error.
  - `test_google_url_wrong_host_rejected` (AC-09.10, first half): `https://evil.example/mcp`, `http://gmailmcp.googleapis.com/mcp/v1`, `https://gmailmcp.googleapis.com.evil.example/` all => exit 4 and the message names `google_mcp_url`; the default URL and an absent key load fine.
  - `test_allowed_host_and_tools_are_code_constants`: `ALLOWED_MCP_HOST`/`ALLOWED_TOOLS` equal the spec values; a config file cannot change them (no config key maps to them).
  - `test_attendee_disambiguation_key_parsed` (AC-05.8 prerequisite, index item G-08): `[owners] attendee_disambiguation = false` loads as `False`; `"false"` (string) => exit 4 naming the key; default `True`.
  - `test_handoff_dir_default_and_relative_resolution`: default `.actiontrack/handoff` resolved against the current directory (writability is checked at `followup` time, T-M7; exit 4 there, H-01).
  - `test_explicit_missing_config_exit4`; `test_toml_syntax_error_reports_line`.
  - `test_multiple_errors_reported_together`.
- **Done when:** AC-09.1 test green; the config half of AC-09.10 green (wrong host => exit 4 naming `google_mcp_url`); the Appendix C example loads; every validation failure is an `EnvError` exit 4 whose text names file + key + reason.

### T-M1-05 — Validate the roster, identity key and qualifiers
- **Size:** M
- **Depends on:** T-M1-04
- **Parallel with:** T-M1-03, T-M1-06, T-M1-10
- **Spec refs:** AC-05.12, FR-05.3, 10.2, 10.2a (roster rule and identity-key corner cases, G-30), 7.2 (`Person` fields, G-11), D-02, D-07, BR-06, plan 2.3 `domain/names.py`
- **Files:** `src/actiontrack/config.py` (roster section), `src/actiontrack/domain/names.py` (minimal: `name_key`, `identity_key`, `person_id_for`), `tests/unit/test_roster.py`, `tests/unit/test_names_identity.py`
- **Work:**
  - `[[person]]` entries: `first_name` and `last_name` REQUIRED and non-blank (missing => exit 4 naming the entry index and key); optional `email` (""/absent = none), `aliases` (list of str), `qualifier` (str).
  - Implement `name_key()` per spec 10.2 (NFKC, case-fold, collapse whitespace, strip punctuation except internal hyphen/apostrophe, drop honorifics Dr/Mr/Ms/Mrs; **keep diacritics**) and `identity_key()` = name_key of *first token + last token* of the full name (10.2a; hyphenated surnames stay one token). Only this minimum lives here; T-M4 tasks extend `names.py` and MUST reuse these functions (no second implementation). Per spec 10.2a(a)(b) `identity_key()` is also defined for the non-roster shapes so M4 does not re-decide it: a **single-token** name (`Zed`) has the token alone as its key and is flagged `is_known_identity=False` (never matches a known First+Last person); an **initial form** (`Priya N.`) has **no** identity key (returns `None`; resolved only by 10.3 step 2); the diacritic-folded key is a separate function `folded_key()` applied identically to roster, attendee and owner tokens while `identity_key` keeps diacritics (10.2a(c)).
  - Duplicate identity keys across roster entries => exit 4 naming **both** entries (`person[0] "Priya Nair" and person[3] "Priya Nair"`), unless both carry a non-blank, distinct `qualifier` (qualifier becomes part of identity: identity key = key + qualifier key). Same qualifier on both => still an error.
  - `person_id_for(identity)`: deterministic slug (e.g. `priya-nair`; with qualifier `priya-nair-sales`). Define the transliteration rule for non-Latin names (e.g. `李雷`) so ids are non-empty, filesystem-safe, deterministic and unique (suggested: NFKC-lowercase, keep Unicode letters/digits, `-` separators; collision after fold => append `-2`). Document the rule in the module docstring (see integrator note b-6).
  - Email syntax check (single address, no whitespace/commas/angle brackets, one `@`); invalid => exit 4. Two persons with the same email (case-insensitive) => exit 4, because `--owner <email>` must resolve to exactly one person (10.5; see integrator note b-7).
  - Aliases are normalised via `name_key` into `alias_key`; an alias shared by two people is **allowed** (spec 10.3 step 1 maps it to `ambiguous`), never a config error.
  - Expose `Roster` (ordered as in file; order is not semantically relevant, sort when iterating for output).
- **Tests to write first (TDD):**
  - `test_duplicate_roster_entries_exit4_names_both` (AC-05.12).
  - `test_distinct_qualifiers_accepted_as_two_people` (AC-05.12): `Priya Nair (Sales)` / `Priya Nair (Ops)` give two distinct person ids.
  - `test_same_qualifier_still_duplicate`.
  - `test_identity_key_cases`: `Priya R. Nair` == `Priya Nair`; `Priya Nair-Rao` last token is `nair-rao`; `Dr Priya Nair` == `Priya Nair`; `PRIYA  nair` same; `José` != `Jose` (diacritics kept).
  - `test_first_or_last_name_missing_exit4`; `test_invalid_email_exit4`; `test_duplicate_email_exit4`.
  - `test_identity_key_corner_cases` (G-30): `Zed` => key `zed`, not a known identity; `Priya N.` => no identity key; `folded_key("José Alvarez") == folded_key("Jose Alvarez")` while `identity_key` differs.
  - `test_person_id_unicode`: `Zoë Åström`, `李雷 王` produce stable non-empty ids (AC-09.6 prerequisite).
  - `test_appendix_c_roster_has_nine_people_and_two_namesake_groups`.
- **Done when:** AC-05.12 tests pass; Appendix C roster loads into 9 people; `identity_key` unit table agrees with every example in spec 10.2a.

### T-M1-06 — Force UTF-8 and deterministic newlines on all text I/O
- **Size:** S
- **Depends on:** T-M1-01
- **Parallel with:** T-M1-02, T-M1-03, T-M1-04, T-M1-10
- **Spec refs:** plan 2.2 (Output encoding), AC-09.6, AC-09.4 (LF output), NFR-PORT-01, BR-18, PR-6, spec 4.1 (`--log-file` is UTF-8 with LF newlines), FR-04.11/SEC-10 (handoff file written by these helpers)
- **Files:** `src/actiontrack/io.py`, `tests/unit/test_io.py`
- **Work:**
  - `configure_streams(stdin, stdout, stderr)`: call `reconfigure(encoding="utf-8", errors=...)` on each stream that supports it (pytest capture replaces them; skip tolerantly). stdout/stderr use `errors="backslashreplace"` (never raise `UnicodeEncodeError`); stdin `errors="strict"` -> prompt answers decode failure treated as "no".
  - Force `newline="\n"` on stdout/stderr so output is byte-identical on Windows and POSIX (needed by AC-09.4); this means no `\r\n` translation.
  - Provide `read_text_utf8(path)`, `write_text_utf8(path, text)` helpers (explicit `encoding="utf-8"`, `newline="\n"` for files actiontrack itself writes: the `--log-file` (append, UTF-8, LF; spec 4.1) and the handoff JSON, whose canonical-JSON hashing rule `args_sha256` (UTF-8, sorted keys, `,`/`:` separators, `ensure_ascii=False`, FR-04.11) is implemented once by M7 on top of `write_text_utf8`); also `write_private_file(path, text)` creating files owner-only where the OS supports it (SEC-10; Windows: relies on the profile ACL, documented); note that note-file reading is done by M2 `reader.py` in binary mode.
  - `emit(text)` (stdout, result data) and `diag(text)` (stderr) wrappers used by `cli.py`; both route text through `report.sanitize` (T-M1-07) so no raw control characters leave the process (SEC-08).
  - Add a repository lint test that AST-scans `src/` and fails on `open(`, `Path.read_text(`, `Path.write_text(` calls lacking an explicit `encoding=` (binary mode excepted).
- **Tests to write first (TDD):**
  - `test_unicode_stdout_roundtrip_in_subprocess` (AC-09.6 partial): `python -m actiontrack` run with `PYTHONIOENCODING=cp1252` and a Unicode argument echoes `Zoë Åström 李雷` as UTF-8 bytes (via a hidden test-only code path or the `--version`/error path that prints a user-supplied value).
  - `test_stdout_uses_lf_not_crlf` on Windows (`b"\r\n"` absent).
  - `test_unencodable_does_not_crash` (lone surrogate => backslash-escaped, no exception).
  - `test_no_open_without_encoding` (the AST lint).
- **Done when:** the above pass on Windows (reference platform, PowerShell 5.1) and the lint test is part of the default suite.

### T-M1-07 — Control-character sanitiser and table renderer
- **Size:** M
- **Depends on:** T-M1-01, T-M1-06
- **Parallel with:** T-M1-08, T-M1-09, T-M1-10
- **Spec refs:** SEC-08, AC-09.8, NFR-USE-02, FR-02.5/4.3 (column layout), 6.4 (FLAGS legend), 9.5 (`?(raw)` / `-` cells), BR-18, AC-09.6
- **Files:** `src/actiontrack/report/sanitize.py`, `src/actiontrack/report/table.py`, `tests/unit/test_sanitize.py`, `tests/unit/test_table.py`, `tests/golden/table_basic.txt`
- **Work:**
  - `sanitize.clean_text(s)`: remove every Unicode category `Cc` character (C0, DEL U+007F, C1 U+0080-U+009F) except that TAB and newline characters inside single-line cells are replaced by one space; apply to **every** string that reaches stdout or stderr, including log messages (T-M1-09). Everything else (emoji, CJK, combining marks) is preserved unchanged (AC-09.6).
  - `table.render_table(columns, rows, *, max_width=120, wide=False)`; `Column(name, min_width, max_width, truncatable, align)`. Deterministic: widths computed from content, no randomness. When not `wide` and total width exceeds `max_width` (NFR-USE-02), shrink the designated truncatable column (TASK, then SOURCE) and end truncated text with `...` (or one-character ellipsis; pick one and fix it in the golden file); `--wide` never truncates.
  - Display-width measurement must treat East Asian wide characters (`unicodedata.east_asian_width in {"W","F"}`) as 2 columns and combining marks as 0 so `李雷` and `Zoë` align; no third-party `wcwidth`.
  - Header row, then rows; empty input => caller prints the "No action items match." line (AC-02.8 is M6) so `render_table` itself returns header only for an empty list.
  - Provide small pure formatters used later by list: `fmt_due(date|None, raw|None, unresolved)` -> `2026-10-23` / `?(next wk)` / `-` (spec 9.5), `fmt_flags(set[str])` fixed order `CR D? OA OU DU SM CI` (spec 6.4), `fmt_item_id(n)`.
  - Trailing spaces: decide and fix (spec 4.3 sample shows trailing padding in FLAGS); golden files normalise nothing else.
- **Tests to write first (TDD):**
  - `test_escape_sequence_not_emitted_raw` (AC-09.8): input `"Fix \x1b[31mred\x1b[0m"` contains no `\x1b` in the rendered output; `\x9b` (C1 CSI) and `\x7f` also removed.
  - `test_newline_in_cell_becomes_space`.
  - `test_unicode_preserved_and_aligned`: `Zoë Åström`, `李雷`, emoji; each rendered row has equal display width.
  - `test_truncation_at_120_columns` and `test_wide_flag_no_truncation`.
  - `test_fmt_due_variants`, `test_fmt_flags_order`.
  - `test_render_is_deterministic` (same input twice => identical string).
  - Golden: `table_basic.txt` reproducing the three-row example of spec 4.3 (with its exact column order).
- **Done when:** AC-09.8 test green; golden file matches; no function in `report/` writes to a stream directly.

### T-M1-08 — JSON renderer with deterministic output
- **Size:** S
- **Depends on:** T-M1-01, T-M1-07
- **Parallel with:** T-M1-07 (after sanitize helper exists), T-M1-09, T-M1-10
- **Spec refs:** FR-02.5 / 4.3 (`list` envelope `{as_of, timezone, items[]}`, G-19), 4.4/4.5 (`done`/`followup` JSON objects, G-18), BR-18, NFR-DET-01, AC-09.4 (timestamps with `ACTIONTRACK_NOW`), SEC-08, AC-09.6, AC-09.8, AC-02.5, AC-09.9
- **Files:** `src/actiontrack/report/json.py` (module name per plan; import it as `report.json` carefully to avoid shadowing stdlib `json` - use `from __future__ import absolute_import` style absolute imports and `import json as _json`), `tests/unit/test_json_render.py`, `tests/golden/json_basic.json`
- **Work:**
  - `render_json(obj) -> str`: UTF-8 text, `ensure_ascii=False`, 2-space indent, `\n` terminated, fixed separators, **no `sort_keys` reliance**: callers pass `dict`s built in an explicit key order (document it) and lists already sorted; the renderer rejects `set` and unordered types (TypeError) to prevent nondeterminism leaking (plan 2.6).
  - Dates/datetimes: `YYYY-MM-DD` and UTC `...Z` ISO-8601 (spec 7.2 timestamp rule); `Enum` => value; `None` => `null`.
  - Control characters: `json.dumps` already escapes C0; additionally escape DEL and C1 (U+007F-U+009F) and U+2028/U+2029 as `\uXXXX` (they are not escaped by default with `ensure_ascii=False` and C1 CSI is dangerous on terminals). Unlike the table renderer this *escapes* rather than removes, preserving data fidelity for machine output.
  - Output is a single JSON document on stdout; diagnostics never go to stdout in JSON mode (spec 4.1). One document per command: a `list` envelope, a `done` object, a `followup` object (their key order is fixed by the M6/M7 builders and documented next to the renderer; this task only supplies the renderer and a shared `envelope(as_of, timezone, items)` helper so the `{as_of, timezone, items[]}` shape exists once, with `"items": []` for an empty result).
  - Timestamps rendered from stored values are byte-stable across runs only when `ACTIONTRACK_NOW` is set (T-M1-03, G-13); the renderer itself adds no clock reads.
- **Tests to write first (TDD):**
  - `test_json_escapes_c0_c1_and_del` (AC-09.8): `"\x1b[31m"`, `"\x9b"`, `"\x7f"` appear only as `\u00xx` escapes.
  - `test_json_unicode_preserved` (AC-09.6): `Zoë Åström`, `李雷` appear literally.
  - `test_json_rejects_set`.
  - `test_json_byte_identical_two_calls` and golden comparison.
  - `test_json_datetime_z_format`.
  - `test_envelope_helper_shape_and_empty_items` (spec 4.3): keys exactly `as_of`, `timezone`, `items` in that order; empty => `"items": []`.
- **Done when:** tests pass; module documented with the "caller supplies ordering" rule used later by list/ingest report authors.

### T-M1-09 — Logging and diagnostics (levels, `--quiet/--verbose`, `--log-file`)
- **Size:** S
- **Depends on:** T-M1-02, T-M1-06, T-M1-07
- **Parallel with:** T-M1-03, T-M1-08, T-M1-10
- **Spec refs:** NFR-LOG-01, NFR-LOG-02, NFR-USE-01, SEC-06, FR-09.3 (help mentions), spec 11 (stack trace only with `--verbose`), 4.1 (`--quiet`/`--verbose`/`--log-file` as defined in v0.6, G-18), AC-09.9 (diagnostics half; CLI wiring and ownership in T-M1-17)
- **Files:** `src/actiontrack/logging_setup.py`, `tests/unit/test_logging.py`
- **Work:**
  - Levels error/warn/info/debug; mapping per spec 4.1: default = warn+error (+ info lines routed by the caller), `--quiet` = **errors and the final summary line only** (no warnings or info on stderr, including the time-zone recommendation of T-M1-03), `--verbose` = debug diagnostics, stack traces and personal names (SEC-06). **Neither flag ever suppresses result data the command was asked to print** (stdout is not governed by the log level). `--quiet` and `--verbose` together => `UsageError` exit 2 (spec 4.1 writes `--quiet | --verbose`; assumption A-T2, see notes).
  - A `Diagnostics` facade (not the global `logging` root) so tests inject it: `error()`, `warn(code, message, *, file=None, line=None)`, `info()`, `debug()`. Warnings take a stable code from `codes.py` and print as `W-XXXX file:line message`; unknown code => assertion error in tests (catches typos).
  - Writes to stderr via `io.diag`; every message passes `sanitize.clean_text` (SEC-08).
  - `--log-file <path>` (now defined in spec 4.1, G-18): diagnostics are **appended** (UTF-8, LF newlines) at the same verbosity as stderr; a path that cannot be opened for append => `EnvError` exit 4 **before any other work**; the file never contains an email body or handoff body (SEC-06). The summary line is written to the log file too.
  - Stack traces only with `--verbose` (spec 11, NFR-USE-01); unexpected exceptions otherwise print one line plus "re-run with --verbose for details".
  - Redaction rule (SEC-06): a `redact()` helper; personal names appear in log output only at debug level; never log email bodies or token-like strings. Provide a `debug_person(name)` helper that is a no-op below debug.
  - Guarantee for NFR-LOG-02: expose `Diagnostics.summary(line)` that prints unless `--quiet` (the ingest summary uses it in M5; defined here so semantics are fixed once).
- **Tests to write first (TDD):**
  - `test_quiet_suppresses_info_and_warn_keeps_error` and `test_quiet_keeps_summary_line_and_errors` (AC-09.9): warnings/info gone from stderr, summary line and errors present.
  - `test_log_level_never_touches_stdout_result_data` (AC-09.9): a fake command printing result data through `io.emit` prints identically under default, `--quiet`, `--verbose`.
  - `test_verbose_shows_debug`.
  - `test_quiet_and_verbose_conflict_exit2`.
  - `test_warning_requires_registered_code`.
  - `test_traceback_only_in_verbose`.
  - `test_log_file_written_utf8_and_sanitised`; `test_log_file_unwritable_exit4` (AC-09.9: a directory path, a read-only file and a missing parent directory each => exit 4); `test_log_file_appends_not_truncates`; `test_log_file_never_contains_body_text` (SEC-06).
  - `test_summary_printed_unless_quiet` (NFR-LOG-02).
  - `test_control_chars_stripped_in_diag` (SEC-08, AC-09.8).
- **Done when:** tests pass; no module other than `logging_setup`/`io` writes to `sys.stderr` (grep test).

### T-M1-10 — Author the v1 database schema as migration 0001
- **Size:** M
- **Depends on:** T-M1-01
- **Parallel with:** T-M1-02, T-M1-03, T-M1-04, T-M1-06
- **Spec refs:** 7.1, 7.2 (all entities as amended by G-11/G-12/G-26/G-30 in v0.5/v0.6), BR-11, BR-16, GM-10 (no body in the DB), FR-04.11/04.12, NFR-REL-01, NFR-REL-04, AC-09.6, AC-04.12 (DB half), plan 2.3 `store/schema.sql`, `store/migrations/`
- **Files:** `src/actiontrack/store/migrations/0001_initial.sql`, `src/actiontrack/store/schema.sql` (consolidated snapshot), `tests/unit/test_schema.py`
- **Work:**
  - DDL for: `schema_version`, `person`, `person_alias`, `ingest_run`, `ingest_warning`, `source_file`, `meeting`, `meeting_attendee`, `action_item`, `item_source`, `item_owner`, `item_link`, `item_event`, `followup_draft`. All keys and enum value sets exactly as spec 7.2; enums as `CHECK (col IN (...))`; booleans as INTEGER 0/1; dates `TEXT YYYY-MM-DD`; timestamps `TEXT` ISO-8601 `Z`.
  - `action_item.id INTEGER PRIMARY KEY AUTOINCREMENT` (BR-11: never reused, even after rollback of a failed file the sequence rolls back with the transaction, which is acceptable because the ID was never committed/visible). `item_link` PK `(from_item_id, to_item_id, kind)`, with a CHECK `from_item_id <> to_item_id`; the symmetric storage rule is now fixed by spec 7.2 (see the `item_link` bullet below; document it in the DDL comments).
  - `UNIQUE(folder_root, rel_path)` on `source_file`; FK `ON DELETE` is `RESTRICT` everywhere (nothing is ever deleted, BR-08/BR-16); indexes for the list hot paths (`action_item(status, due_date)`, `item_owner(person_id)`, `item_source(item_id)`, `item_source(source_file_id, present)`).
  - **The 7.2 field list is now authoritative (G-11); the old "integrator note b-9 additions" are no longer additions.** Implement exactly:
    - `person`: `id`, `first_name`, `last_name`, `qualifier` (NULL), `display_name`, `name_key`, `identity_key` (UNIQUE), `email` (NULL), `origin` (`roster`/`attendee`/`note_owner`), `verified`.
    - `action_item`: `description` (cleaned, full) **and** `task_text` (G-12), `description_core_key`, `status`, `completion_origin`, `completed_at`, `due_raw`, `due_raw_other`, `due_date`, `due_confidence` (`explicit`/`relative`/`inferred`/`unresolved`/`none`), `due_rule` (CHECK against the 9.5 id catalogue or NULL), `due_reason` (NULL or an Appendix B reason), `source_state`, `created_at`, `updated_at`.
    - `item_owner`: `ordinal`, `owner_raw`, `role` (`owner`/`with`), `person_id`, `owner_status`, `candidates`, `provenance` CHECK in (`roster`,`alias`,`attendee`,`attendee_in_meeting`,`unique_first_name`,`initial_form`,`diacritic_fold`,`provisional`,`none`) (G-26).
    - `item_source`: all 7.2 fields incl. `raw_text` (verbatim, not NFC), `block_hash` over the verbatim text, `present`, `first_seen_run`, `last_seen_run`, plus the **per-source parse columns** `description`, `task_text`, `core_key`, `due_raw`, `due_raw_other`, `due_date`, `due_confidence`, `due_rule`, `due_reason`; the per-source owner phrases are stored as a child table `item_source_owner` (`item_source_id`, `ordinal`, `owner_raw`, `role`, `person_id`, `owner_status`, `provenance`). Spec 7.2 leaves "child rows or a JSON column" to the Plan; **decision here: child rows** (queryable by the whole-database re-resolution pass, spec 10.7.9).
    - `item_link`: `kind` (`possible_duplicate`/`completed_by`), `reason` CHECK in (`due_differs`,`owner_differs`,`status_differs`,`same_file`,`fuzzy`,`recurrence`,`outside_window`,`owner_unresolved`), `created_run`; **storage rule** (G-11): a `possible_duplicate` link is stored once with `from_item_id < to_item_id` (CHECK `kind <> 'possible_duplicate' OR from_item_id < to_item_id`) and read from both ends; `completed_by` is directional (`from` = item whose completion was inferred, `to` = evidence holder). This replaces the earlier PK `(from,to,kind)` wording only in that the CHECK is added.
    - `ingest_run`: `status` CHECK in (`in_progress`,`complete`,`partial`,`interrupted`), `dry_run`, and the counters `files_discovered`, `files_ingested`, `files_unchanged`, `files_empty`, `files_failed`, `items_new`, `items_merged`, `items_updated`, `items_unchanged`, `possible_duplicates`, `already_done_in_note`, `resolved_via_attendance`.
    - `followup_draft`: `person_id`, `channel` (`handoff`/`google`/`fake`), `item_ids_hash`, `item_ids` (JSON), `include_ambiguous`, `ambiguous_item_ids` (JSON), `handoff_path` (NULL), `args_sha256` (NULL), `gmail_draft_id`, `thread_id`, `view_url`, `source` (`actiontrack`/`host`), `status` CHECK in (`pending_handoff`,`created_by_host`,`failed_by_host`,`created`,`failed`,`unknown`), `created_at`, `recorded_at` (NULL). **There is no `body`, `subject` or message-text column** (GM-10; asserted by a test). The earlier draft's extra columns `to_email`, `as_of_date` and a `google|host|fake` source enum are dropped: they are not in spec v0.6.
  - `item_owner.candidates` stored as a JSON array of person ids, sorted ascending (deterministic); `action_item.description`/`task_text`/`item_source.description` stored NFC while `item_source.raw_text` is stored **verbatim** (spec 7.2 exception) - enforcement is in T-M1-13.
  - `schema.sql` is the consolidated snapshot of all migrations; a test guarantees it equals the schema produced by applying the migrations to an empty DB.
- **Tests to write first (TDD):**
  - `test_apply_0001_creates_all_entities`: every entity of spec 7.2 exists (parse the mermaid/entity names list into the test) and `PRAGMA foreign_key_check` is clean.
  - `test_enum_checks_reject_bad_values` (e.g. `status='weird'` raises).
  - `test_autoincrement_never_reuses_id`: insert, commit, delete is impossible (FK) - instead insert 3 items, roll back item 3's transaction, insert again, assert ids are monotonic and unique; commit then `PRAGMA` check of `sqlite_sequence`.
  - `test_schema_sql_snapshot_matches_migrations` (dump compare).
  - `test_unicode_text_roundtrip` (AC-09.6): `Zoë Åström`, `李雷` stored and read byte-identical (NFC input).
  - `test_foreign_keys_enforced` (inserting `item_owner` for a missing item fails).
  - `test_followup_draft_has_no_body_column` (AC-04.12 DB half, GM-10): no column of any table is named `body`/`subject`/`email_body`; `followup_draft.status` accepts exactly the six v0.6 values; `source` accepts only `actiontrack`/`host`.
  - `test_item_link_symmetric_storage_check` (G-11): inserting a `possible_duplicate` with `from > to` fails; `completed_by` may have any order.
  - `test_provenance_and_reason_enums` (G-26): `initial_form` and `provisional` accepted; unknown value rejected; `due_rule` outside the 9.5 catalogue rejected.
  - `test_ingest_run_status_partial_and_counters`: `partial` accepted; all twelve counter columns exist and default 0.
  - `test_spec_7_2_columns_present`: for each entity, the column set equals the key-field list parsed from the spec 7.2 table (guards drift against G-11).
- **Done when:** the schema applies cleanly with `foreign_keys=ON`; every spec 7.2 field exists (the `item_source_owner` child table is the only addition, documented); snapshot test green.

### T-M1-11 — Database connection, transactions, locking and journal-mode decision
- **Size:** L
- **Depends on:** T-M1-02, T-M1-10
- **Parallel with:** T-M1-03, T-M1-04, T-M1-06 (not with T-M1-12/13, which build on it)
- **Spec refs:** NFR-REL-01, NFR-REL-02, 10.7.6, 10.7.7 (lock file, G-14), AC-08.9, SC-67, 11 (DB unusable => exit 4), SEC-04, AC-01.10 (prerequisite), plan 2.2 (journal mode "to be chosen in M1 after testing on Windows"), 2.6 (DB locked => exit 4)
- **Files:** `src/actiontrack/store/db.py`, `src/actiontrack/store/lock.py`, `tests/integration/test_db_locking.py`, `tests/integration/test_db_open.py`
- **Work:**
  - `Database.open(path, *, create, readonly_ok)`: resolves path (parent directory created with owner-only mode on POSIX; Windows relies on the user-profile ACL, documented per SEC-04); `PRAGMA foreign_keys=ON` on every connection; `isolation_level=None` with explicit `BEGIN IMMEDIATE`/`COMMIT` so per-file transactions (NFR-REL-01) are explicit; `sqlite3.Row` factory; text encoding UTF-8.
  - **Journal-mode decision (plan 2.2 leaves it to M1):** write a throwaway measurement test on Windows comparing `journal_mode=DELETE` vs `WAL` against these criteria: (1) a dry-run/read of an existing DB leaves the main DB file byte-identical and creates no sibling files (AC-01.10); (2) kill-process-mid-transaction leaves a consistent DB; (3) concurrent reader while a writer holds a transaction; (4) behaviour when the DB lives on a network/synced folder. Record the decision and measurements as a docstring in `db.py`. Default recommendation: `DELETE` with `synchronous=FULL` unless WAL proves necessary (WAL adds `-wal`/`-shm` files that complicate AC-01.10 and the `.bak` copy of NFR-REL-04).
  - Error mapping: `sqlite3.OperationalError` "database is locked"/`busy` => `EnvError` exit 4 message "database is locked by another actiontrack process"; "unable to open"/corrupt/not a database => exit 4 naming the path; readonly filesystem => exit 4. `busy_timeout` fixed (e.g. 2000 ms; not a config key; see integrator note b-10).
  - **Writer lock for whole-run exclusion:** because ingest commits per file, SQLite's own lock is released between files, so two concurrent ingests could interleave (spec 10.7.7 demands exit 4). Implement `store/lock.py`: an exclusive non-blocking OS lock on `<db>.lock` (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere) held by **every command that writes** - `ingest` (whole run), `done`, `followup` row writes (a `FollowupDraft` insert/update) and `followup --record-draft` - for that command's whole duration (spec 10.7.7); an OS file lock is released automatically when the process dies, so a stale `<db>.lock` file is harmless and is never deleted as a recovery step. Failure to acquire => `EnvError` exit 4 with the spec message "another actiontrack process is using this database", the database untouched. **`--dry-run` and read-only commands (`list`, `followup --dry-run`, a `followup` that is declined) do not take the write lock** (they work on an in-memory copy or read-only connection; a declined `followup` writes nothing, exit 9). The lock also disambiguates a crashed run from a live one: an `ingest_run.status='in_progress'` row is only marked `interrupted` (10.7.6) **if the lock can be acquired by this process**; an `in_progress` run whose owning process is alive is a concurrent run, is never marked `interrupted`, and its owner is what the second writer collides with.
  - `transaction()` context manager (commit on success, rollback on any exception, re-raise); nested use is an error. `Database.in_memory_copy()` for `--dry-run` (see T-M1-13).
  - SEC-04: on POSIX `chmod 0600` on creation; on Windows no ACL manipulation, but print nothing; documented in README (T-M9-07).
- **Tests to write first (TDD):**
  - `test_second_writer_exit4_while_ingest_holds_lock` (AC-08.9, SC-67; spawn two processes; a second `ingest` **and** a second `done`-style write each get `EnvError` code 4 with the "another actiontrack process" message and the DB bytes are unchanged; first unaffected). Covers 10.7.7. The M5 task that drives real `ingest`/`done` re-asserts this end to end; **AC-08.9 ownership: T-M1-11 (lock primitive and exit 4) + T-M5 ingest tasks (interrupted-run marking in a real ingest)**.
  - `test_lock_released_after_crash` (AC-08.9, second sentence): kill a subprocess holding the lock; the lock is reacquirable (OS releases it) and the helper `can_mark_interrupted()` returns True only when the lock was acquired; while the first process is alive it returns False (a live `in_progress` run is never marked interrupted).
  - `test_dry_run_and_readonly_take_no_lock`: opening for dry-run or read-only while another process holds the lock succeeds and does not create or touch `<db>.lock`.
  - `test_busy_database_maps_to_exit4`: external connection holds `BEGIN EXCLUSIVE`.
  - `test_corrupt_db_file_exit4` (random bytes in file), `test_not_a_directory_parent_exit4`.
  - `test_rollback_on_exception_leaves_no_rows` (NFR-REL-01).
  - `test_foreign_keys_on_for_every_connection`.
  - `test_open_existing_db_readonly_does_not_modify_bytes` (SHA-256 before/after; AC-01.10 prerequisite).
  - Fault-injection hook point `Database.fault_hook(name)` callable in tests (used by M5 for AC-08.5): `test_fault_hook_raises_inside_transaction_rolls_back`.
- **Done when:** the journal-mode decision and its measurements are recorded in the module; all locking/exit-4 tests pass on Windows; no `sqlite3` import exists outside `store/`.

### T-M1-12 — Migration runner, schema_version and backup policy
- **Size:** M
- **Depends on:** T-M1-10, T-M1-11
- **Parallel with:** T-M1-13, T-M1-14 (after T-M1-02), T-M1-15
- **Spec refs:** NFR-REL-04, 7.1 (forward-only migrations), 11 (migration fails => abort exit 4, no partial writes), R-09
- **Files:** `src/actiontrack/store/db.py` (migrate), `src/actiontrack/store/migrations/`, `tests/integration/test_migrations.py`, `tests/fixtures/migrations_test/` (fake 0002/0003 files used only by tests)
- **Work:**
  - `schema_version` table holds the applied version integer(s) with `applied_at`. `Database.open` compares the DB version with the highest bundled migration: equal => proceed; **newer than the code knows => refuse, exit 4** ("DB was created by a newer actiontrack; upgrade the program"); older => migrate.
  - Migration discovery: files `NNNN_name.sql` sorted numerically, gaps or duplicates numbers => internal error at startup (test-checked), applied in order, each in its own transaction (`executescript` is not transactional: execute statements inside an explicit `BEGIN`, or split safely; SQLite DDL is transactional).
  - **Backup before migrating an older DB (NFR-REL-04):** copy to `<db>.bak` using `sqlite3.Connection.backup()` (not a raw file copy; safe while connections exist); naming rule for repeated migrations (e.g. `<db>.bak.<fromversion>`; never overwrite an existing backup silently) - decision recorded in the docstring (integrator note b-11). Backup failure aborts the migration (exit 4).
  - A failed migration rolls back and leaves the DB at the previous version, exit 4, naming migration file and SQL error.
  - Fresh DB (no file): create at latest version without a `.bak`.
  - `--dry-run` never migrates the real file (see T-M1-13): migration runs on the in-memory copy only.
- **Tests to write first (TDD):**
  - `test_fresh_db_created_at_latest_version_no_bak`.
  - `test_newer_schema_refused_exit4` (set `schema_version` to 999).
  - `test_older_schema_migrates_after_backup` using fixture migrations 0002/0003; `.bak` exists and opens at the old version.
  - `test_failed_migration_rolls_back_exit4` (0003 contains a bad statement): version stays 0002, no partial tables.
  - `test_migration_file_gap_detected`.
  - `test_snapshot_equals_after_all_migrations` (re-uses T-M1-10 check).
  - `test_backup_not_overwritten_on_second_migration`.
- **Done when:** NFR-REL-04 behaviours (newer refuse, older backup+migrate, failure rollback) are covered by green tests; error messages state what/where/next step.

### T-M1-13 — Repository base: ID helpers, NFC storage rule, in-memory dry-run copy
- **Size:** M
- **Depends on:** T-M1-10, T-M1-11
- **Parallel with:** T-M1-12, T-M1-14, T-M1-15
- **Spec refs:** BR-11, FR-03.1 (ID syntax), 4.4, 7.2 (NFC, ID display `AT-nnnn`), FR-01.10, AC-01.10 (primitive), AC-09.6, SC-40, plan 2.3 `repo.py`, 2.4 (`--dry-run` against an in-memory copy)
- **Files:** `src/actiontrack/store/repo.py`, `src/actiontrack/domain/models.py` (only `ItemId` helpers), `tests/unit/test_item_id.py`, `tests/integration/test_repo_base.py`
- **Work:**
  - `format_item_id(n) -> "AT-0004"` (zero-pad to at least 4 digits, wider numbers print in full, never truncated); `parse_item_id(text) -> int` accepting `AT-<digits>`, `<digits>`, `at-<digits>` case-insensitively (FR-03.1), rejecting everything else (`abc`, `AT-`, `-5`, `0`, `1.5`, whitespace-only, Unicode digits such as `١٢` => `UsageError` exit 2; ASCII digits only; no leading `+`).
  - `to_storage_text(s)` => NFC; the single funnel through which repo write methods pass free text (`description`, `task_text`, names, titles) so comparison keys stay separate (spec 7.2/8.3). **Exception (G-22, spec 7.2):** `ItemSource.raw_text` is stored **verbatim** through a separate `to_storage_raw(s)` that applies no normalisation, so traceability and `block_hash` reflect the file byte-for-byte (AC-01.19). Verified by tests that a decomposed `e + U+0301` in a description is stored composed and the same text in `raw_text` is stored decomposed.
  - `Database.in_memory_copy()` for `--dry-run`: when the DB file exists, `sqlite3.backup` into `:memory:` (migrating the copy if older); when absent, a fresh in-memory DB at the latest schema. A dry-run NEVER creates the DB file, `-journal`, `.bak`, lock file (it takes no write lock, spec 10.7.7), or the `.actiontrack` directory, and never writes an `ingest_run` row (FR-01.10). Under `--dry-run` the whole-database passes of spec 10.7.9 still run (against the copy) and are reported, nothing is written.
  - Thin `Repo` class over a connection with just: `next_item_id_preview()`, `get_item(id)`, `schema_version()`; entity-specific upserts are added by M2-M7 tasks (list them in the module docstring so authors extend `repo.py` rather than writing SQL elsewhere).
  - No SQL outside `store/` (enforced by a grep test).
- **Tests to write first (TDD):**
  - `test_parse_item_id_accepts_AT_0012_12_at_12` (AC-03.1 helper) and `test_parse_item_id_rejects_abc` (AC-03.5 helper) plus the reject list above.
  - `test_format_item_id_padding` (`4` => `AT-0004`; `12345` => `AT-12345`).
  - `test_nfc_normalisation_on_write`; `test_raw_text_stored_verbatim_not_nfc` (G-22, AC-01.19 storage half); `test_unicode_roundtrip_through_repo` (AC-09.6).
  - `test_dry_run_copy_leaves_file_byte_identical` (AC-01.10, SC-40 primitive): hash DB file and listing of its directory before and after writes in the in-memory copy.
  - `test_dry_run_with_no_db_creates_nothing` (directory listing unchanged).
  - `test_no_sql_outside_store` (AST/grep).
- **Done when:** AC-01.10 primitive tests pass; ID parser table passes; the funnel for NFC is the only write path used by `Repo` methods.

### T-M1-14 — CLI shell: argparse, global options, precedence, exit-code mapping
- **Size:** L
- **Depends on:** T-M1-02, T-M1-03, T-M1-04, T-M1-06, T-M1-09
- **Parallel with:** T-M1-12, T-M1-13
- **Spec refs:** 4.1 (global options, placement H-03, exit codes 0-9), 4.2-4.6 (command signatures as of v0.6), FR-04.8/04.9, FR-09.2, FR-09.3 (via T-M1-15), NFR-MAINT-01, NFR-USE-01, BR-18, FR-10 (`gmail-login` stub), plan 2.3 `cli.py`; AC-09.11 and AC-09.9 are owned by T-M1-17 (which builds on this shell)
- **Files:** `src/actiontrack/cli.py`, `tests/unit/test_cli_shell.py`, `tests/integration/test_cli_exit_codes.py`
- **Work:**
  - Build the parser with the global options of spec 4.1: `--db`, `--config`, `--today`, `--format table|json`, `--quiet`/`--verbose` (mutually exclusive), `--no-color`, `--log-file` (see T-M1-09), `--help`, `--version`. **Placement is now decided by the spec (H-03): global options are accepted both before and after the subcommand, with identical semantics, last occurrence wins, and an option a command does not use is accepted and ignored.** This task installs the argparse structure for it (a shared `parents=[global_parser]` on every subparser with `default=SUPPRESS` on the subparser copies so a value given before the subcommand is not overwritten by a subparser default, and an explicit last-wins merge); the full test table and AC-09.11 belong to T-M1-17.
  - Subcommands registered now with their full argument signatures from spec 4.2-4.6 (v0.6): `ingest <folder> [--no-recursive] [--dry-run]`; `list [--owner] [--status open|done|overdue|all] [--due-before] [--include-ambiguous] [--ambiguous] [--wide] [--format table|json]`; `done <id> [--format table|json]`; `followup <owner> [--to] [--include-ambiguous] [--dry-run] [--yes] [--force] [--format table|json]` **and** `followup --record-draft <handoff-file> [--draft-id <id>] [--status created|failed]` (`<owner>` is optional/forbidden in this form; the grammar is `owner` positional `nargs="?"` with a `validate_args` extension point that M7 fills with the exit-2 combinations of FR-04.12/AC-04.13: `--status created` needs `--draft-id`, `--status failed` forbids it, `--record-draft` with an owner or with `--dry-run`/`--to`/`--include-ambiguous`/`--force`/`--yes` => exit 2); `gmail-login [--logout]` (`--format` accepted and ignored, H-03). `--force` is registered here with the help text "needed together with `--yes` to proceed past the repeat-draft warning without a TTY (FR-04.9)". Handlers are late-bound stubs (`NotImplementedYet`) so M2-M8 plug in without touching the shell; a stub prints "not implemented in this build" to stderr and exits 1.
  - Precedence for each setting: command-line flag > environment (`ACTIONTRACK_DB`, `ACTIONTRACK_CONFIG`, `ACTIONTRACK_TODAY`) > config (where applicable) > default (`./.actiontrack/actiontrack.db`, `./actiontrack.toml`). Empty-string env values are treated as unset.
  - `RunContext` (config, clock, today/tz label, diagnostics, db path, format, color flag) built once per invocation. Config is loaded lazily **after** argument parsing and **only** for commands that need it; `--help` and `--version` exit 0 without reading config, DB or env-supplied dates. A config error aborts before any DB write (FR-09.2).
  - Single top-level `main(argv=None) -> int` catching `ActionTrackError` (print `format()` to stderr, return its exit code), `argparse` usage errors (exit 2; argparse default is already 2 - override `error()` so the message goes through the sanitising writer and mentions `--help`), `KeyboardInterrupt` (exit 1, one line), and any other exception (exit 1, one line; traceback only with `--verbose`; spec 11).
  - Result data to stdout via `io.emit`, everything else to stderr (spec 4.1); `--format table|json` applies to **all four** result commands `ingest`, `list`, `done`, `followup` (G-18; the JSON objects of 4.4/4.5 are built by M6/M7, the dispatcher only passes `RunContext.format`).
  - **Exit-code mapping 0-9:** `ActionTrackError.exit_code` is returned as is; `CancelledByUser` => 9 with its message on stderr; a handler that returns normally after preparing a handoff returns `EXIT_HANDOFF_PREPARED` (constant from T-M1-02, = 0) - the dispatcher never hard-codes the integer.
  - `ACTIONTRACK_NOW` (test-only env var, T-M1-03) is read into the clock when `RunContext` is built; it is not a command-line option.
  - `--no-color`/`NO_COLOR` accepted and recorded; no output is coloured in v1 (spec defines no colour use), so this is a stored flag only.
  - `--version` prints `actiontrack <version>` to stdout, exit 0.
  - `--today` / `ACTIONTRACK_TODAY` parsed through the T-M1-03 function, exit 2 on invalid values, **before** any DB access.
  - Business logic is forbidden here (NFR-MAINT-01): handlers call services only.
- **Tests to write first (TDD):**
  - `test_exit_code_2_unknown_option_and_missing_args` (every subcommand with missing required args).
  - `test_exit_code_4_invalid_config_before_any_write` (DB file not created).
  - `test_exit_code_1_internal_error_no_traceback_unless_verbose` (stub handler that raises).
  - `test_quiet_verbose_mutually_exclusive_exit2`.
  - `test_flag_beats_env_for_db_config_today`.
  - `test_help_and_version_work_with_broken_config_and_invalid_today_env`.
  - `test_stdout_vs_stderr_split`: result goes to stdout only; warnings/errors to stderr only.
  - `test_global_options_after_subcommand` (H-03; detailed table in T-M1-17).
  - `test_all_five_commands_registered` and `test_stub_handlers_exit_1`.
  - `test_followup_grammar_accepts_record_draft_without_owner` and `test_followup_accepts_force_and_format`: parse-level only (the semantics are M7).
  - `test_exit_9_reachable_via_test_handler`: a test-only handler raising `CancelledByUser` yields exit 9 and "Cancelled. Nothing created." on stderr; a handler returning `EXIT_HANDOFF_PREPARED` yields 0.
  - `test_list_ambiguous_with_owner_exit2` is deferred to M6 (AC-02.9) but argument-conflict hooks are added here as a `validate_args` extension point (document, do not implement).
- **Done when:** the exit-code table 0-9 is reachable through `main()` in tests for codes 0,1,2,4 (3,5,6,7,8,9 are exercised via a test-only handler that raises the matching `ActionTrackError`/`CancelledByUser`); the `followup` grammar covers `--force`, `--format`, and `--record-draft` without an owner; no module other than `cli.py` calls `sys.exit`.

### T-M1-15 — `--help` content for the program and every command
- **Size:** S
- **Depends on:** T-M1-14
- **Parallel with:** T-M1-12, T-M1-13
- **Spec refs:** FR-09.3, AC-09.2, NFR-USE-01, 4.1 (exit-code table 0-9, global-option placement, `ACTIONTRACK_NOW`), 4.2-4.6 (options and examples, v0.6), AC-09.7 (help text may use prose; only exact tool-name identifiers/literals are forbidden, G-15)
- **Files:** `src/actiontrack/cli.py` (help text constants in a `HELP` data block), `tests/unit/test_help.py`, `tests/golden/help_*.txt`
- **Work:**
  - Program help: synopsis, global options (`--db`, `--config`, `--today`, `--format`, `--quiet | --verbose`, `--log-file`, `--no-color`; stating that they may be placed before **or** after the subcommand, last wins), the five commands with one-line descriptions, and the full exit-code table (**0-9**, including `9 Cancelled by user` and the note that a prepared handoff exits 0 but prints "Draft NOT yet created in Gmail") from spec 4.1. `ACTIONTRACK_NOW` is test-only and is **not** advertised in help.
  - Each command's `--help` has four labelled parts: **Synopsis**, **Options**, **Exit codes** (only the codes that command can return, taken from spec 4.1-4.6: e.g. `ingest`: 0, 2, 4, 5; `list`: 0, 2, 3, 4, 6; `done`: 0, 2, 3, 4; `followup`: 0, 2, 3, 4, 6, 7, 8, **9**; `gmail-login`: 0, 4, 8), **Example** (at least one runnable example, using PowerShell-safe quoting with double quotes). `followup` help documents `--yes`/`--force` (a repeat draft needs both without a TTY), `--format`, `--include-ambiguous` (opt-in; co-owner names omitted; 20-item section), and the second form `--record-draft <file> [--draft-id <id>] [--status created|failed]`.
  - Use a custom formatter that preserves line breaks (`RawDescriptionHelpFormatter`) and a table of per-command codes defined once next to the help text and shared with the README generator test (T-M9-07/T-M9-08).
  - Help text is pure data so the consistency check in T-M9-08 can compare it with the code constants.
  - Help must not mention behaviours that do not exist in the spec. The help text for `followup` states drafts are never sent. Since AC-09.7 (G-15) matches whole identifiers/string literals *equal* to a tool name, ordinary prose ("reply", "forward") in a help paragraph is allowed, but a help line must never be a bare literal equal to a forbidden tool name; T-M9-01 scans the real help data to prove it.
- **Tests to write first (TDD):**
  - `test_help_has_four_parts_for_each_command` (AC-09.2): parametrised over the five commands + program; asserts presence of `Synopsis`, `Options`, `Exit codes`, `Example` markers and at least one code number.
  - `test_command_exit_codes_subset_of_table`; `test_exit_code_9_listed_for_followup_and_program` (G-09); `test_program_help_lists_all_global_options_and_placement_rule` (G-18, H-03); `test_actiontrack_now_not_in_help` (test-only variable).
  - `test_every_option_in_spec_appears_in_help` (flag names parsed from spec 4.1-4.6 block text vs help output).
  - Golden files for each help screen (width-fixed with `COLUMNS=100` env in the test).
- **Done when:** AC-09.2 green for all commands; golden help files committed.

### T-M1-16 — M1 exit gate: end-to-end shell smoke and exit-criteria review
- **Size:** S
- **Depends on:** T-M1-03, T-M1-05, T-M1-07, T-M1-08, T-M1-09, T-M1-12, T-M1-13, T-M1-15, T-M1-17
- **Parallel with:** -
- **Spec refs:** AC-09.1, AC-09.2, AC-09.3, AC-09.9, AC-09.11, AC-09.10 (config half), AC-05.12, AC-08.9 (lock primitive), SC-37, plan section 3 (M1 exit criteria)
- **Files:** `tests/integration/test_m1_exit.py`, `tests/fixtures/config/` (final roster fixtures), `tests/golden/`
- **Work:**
  - Subprocess-level tests using the installed console script on Windows: run `actiontrack --version`, `actiontrack --help`, each `<command> --help`, an invalid config, a duplicate-roster config, a config with a foreign `google_mcp_url` host (exit 4 naming the key), a bad `--today`, `--format json` before/after the subcommand (same output; AC-09.11), `--quiet`/`--log-file` against a stub command (AC-09.9 CLI half), and a stub command against a temp DB, asserting exit codes and stdout/stderr split.
  - A "marker coverage" check that every M1 AC (list in integrator notes) has at least one test with `@pytest.mark.ac(...)`.
  - Verify clean-state behaviour: running any command from an empty temp directory never creates files except the `.actiontrack` DB location when a DB-needing command legitimately opens it; `--help`/`--version` create nothing.
  - Confirm the sample folder is untouched (hash listing of `samples\notes` and `notes\notes` before/after test run) - BR-01/AC-01.11 baseline.
  - Write the M1 walking-skeleton notes into the test module docstring: which stubs M2+ replace.
- **Tests to write first (TDD):** the subprocess tests above are the deliverable; write them to fail against the placeholder CLI first.
- **Done when:** the whole M1 test suite is green on Windows; AC-09.1, AC-09.2, AC-09.3, AC-09.9 (quiet/log-file halves), AC-09.11, the config half of AC-09.10, AC-05.12 each proven by a named test; no files created outside temp directories; sample folder hash unchanged.

### T-M1-17 — Global option placement and CLI-level `--quiet` / `--log-file` / `--format` contract (new in re-sync)
- **Size:** M
- **Depends on:** T-M1-09, T-M1-14
- **Parallel with:** T-M1-12, T-M1-13, T-M1-15
- **Spec refs:** spec 4.1 (global options, "Global option placement (H-03)"), 4.4/4.5 (`--format` on `done`/`followup`), FR-09.3, NFR-LOG-01/02, **AC-09.11** (owner), **AC-09.9** (owner of the CLI contract; final end-to-end proof in T-M9-10), H-03, G-18
- **Files:** `src/actiontrack/cli.py` (option merge logic), `tests/unit/test_cli_global_options.py`, `tests/integration/test_cli_options_contract.py`
- **Work:**
  - Implement and lock down **H-03**: every global option is accepted before and after the subcommand; if an option is repeated (in either position, or in both) the **last occurrence on the command line wins**; an option the command does not use (e.g. `--format` on `gmail-login`, `--today` on `gmail-login`) is accepted and ignored without a warning and without changing the exit code. The environment-variable and default precedence of T-M1-14 is unchanged (flag > env > config > default).
  - Implement a single `merge_global_options(parsed_before, parsed_after)` function (no argparse side effects) so the last-wins rule is unit-testable on plain dicts, then wire it into `main()`.
  - **CLI contract for diagnostics options (AC-09.9, CLI half; semantics built by T-M1-09):** (a) `--quiet` removes warning/info lines from stderr but keeps errors and the final summary line (`Diagnostics.summary`), and never changes stdout result data; (b) `--log-file <path>` appends diagnostics and an unopenable path exits 4 before any other work (even for `--help`-less read commands); (c) `--format table|json` is accepted by `ingest`, `list`, `done`, `followup` (and ignored by `gmail-login`); the JSON bodies of `done` and `followup` are produced by M6/M7 and proven in T-M9-10.
  - Ensure `--help`/`--version` placed after the subcommand still win (`actiontrack list --help`, `actiontrack list --format json --help`).
- **Tests to write first (TDD):**
  - `test_format_json_before_and_after_subcommand_identical` (AC-09.11): a test-only handler echoing `RunContext.format` and a result string; `actiontrack --format json <cmd>` and `actiontrack <cmd> --format json` give byte-identical stdout, exit code and stderr.
  - `test_repeated_option_last_wins` (AC-09.11): `--format json ... --format table` => table; `--format table <cmd> --format json` => json; `--today 2026-10-01 <cmd> --today 2026-10-22` => 2026-10-22; same for `--db`, `--config`.
  - `test_unused_option_accepted_and_ignored_on_gmail_login` (AC-09.11): `actiontrack gmail-login --format json` and `actiontrack --format json gmail-login` print the handoff-channel no-op text and exit 0.
  - `test_merge_global_options_table`: parametrised dict-level table of before/after/repeat combinations.
  - `test_quiet_cli_level_keeps_summary_and_errors` (AC-09.9): through `main()` with a test handler that emits a warning, an info line, a summary line, result data and an error.
  - `test_log_file_unopenable_exit4_before_work` (AC-09.9): `--log-file` pointing at a directory => exit 4, handler never invoked.
  - `test_format_accepted_by_all_four_result_commands` (AC-09.9, parse level) and `test_help_after_subcommand_wins`.
- **Done when:** AC-09.11 tests green; the CLI-level halves of AC-09.9 green; the last-wins rule exists in exactly one function.

### T-M1-18 — Complete the sample fixtures (36 files)
- **Size:** S (<=0.5 day)
- **Depends on:** M1 skeleton (T-M1-01)
- **Parallel with:** T-M1-02, T-M1-03
- **Spec refs:** BR-01, AC-01.11, AC-01.13, SC-41, spec Appendix A.1a (relocation)
- **Files:** `tests/fixtures/notes/scratch.md`, `tests/fixtures/notes/untitled.md`, `tests/fixtures/README.md` (new)
- **Work:**
  - `tests/fixtures/notes/` currently holds byte-identical copies of the 34 files in `samples/notes/` only. Add byte-exact copies of `notes/notes/scratch.md` (binary: NUL bytes, `FF FE`, PNG signature) and `notes/notes/untitled.md` (VALID note, 2 items) so the fixtures hold the full 36-file sample set (35 `.md` + 1 `.txt`).
  - Do not move, edit or delete the originals; `untitled.md` is a valid parse target and `scratch.md` is the only binary file (spec A.1a).
  - Record SHA-256 hashes of all 36 fixture files in `tests/fixtures/README.md` together with their origin (`samples/notes/` vs `notes/notes/`) and a one-line rule: fixtures are read-only; tests copy them to `tmp_path` before use.
  - The test that builds the "one level down" layout (`<tmp>/notes/<36 files>`) for AC-01.13 reuses these fixtures.
- **Tests to write first (TDD):** a test that lists `tests/fixtures/notes/` and asserts exactly 36 files, 35 with extension `.md` (case-insensitive) and 1 `.txt`; a test that recomputes the hashes in README against the files (AC-01.11 baseline); a test that `scratch.md` contains a NUL byte and `untitled.md` decodes as UTF-8 with 2 checkbox lines.
- **Done when:** the three tests pass; hashes of `samples/notes/*` and `notes/notes/*` are unchanged before/after; the fixture count is 36.
## Milestone M9 — Hardening & release

### T-M9-01 — Static "no send-capable tool" check
- **Size:** S
- **Depends on:** M7 complete (gateway and email template exist); optionally M8 modules if present
- **Parallel with:** T-M9-02, T-M9-03
- **Spec refs:** AC-09.7 (exact identifiers/string literals, G-15), AC-04.19 (host-instruction files are exempt), GM-01, GM-02, GM-12, R-06, NFR-TEST (safety layer, plan 4)
- **Files:** `tools/check_no_send.py`, `tests/unit/test_no_send_static.py`, `tests/safety/` (allow-list test is the only permitted reference site)
- **Work:**
  - Implement the check with `ast`: walk every `.py` under `src/actiontrack/`, collect string constants (exact match), identifiers, attribute names and function names equal to the forbidden set: `send_message`, `reply`, `forward`, `update_draft`, `delete_draft`, plus every `trash_*`, `mark_*_spam`, label tool name (`create_label`, `delete_label`, `update_label`, `label_message`, `label_thread`, `unlabel_*`, `update_message_labels`, etc., built from the 30-tool list of plan F-1/F-3 stored as a data file `tools/forbidden_gmail_tools.txt`).
  - Do **not** use a naive substring grep: the email template legitimately contains the English word "reply" in prose (spec 12.4: "Could you reply with a status ..."). Spec AC-09.7 (v0.6) states the rule precisely: match **whole identifiers and string literals that are *equal* to a tool name**, never substrings or prose. A string literal such as `"Could you reply with a status or a revised date..."` is not equal to `reply` and must not be flagged; a literal `"reply"`, an identifier `reply`, an attribute `.forward`, or a function `def send_message` is flagged. (Integrator note b-13 is resolved by the spec.)
  - **Exempt locations per AC-09.7:** the allow-list/safety tests under `tests/safety/` (the scan covers `src/` only) and the **host-instruction files** (`CLAUDE.md`, the skill, the generated permission template) which must name the denied tools (AC-04.19). Because the permission template is generated from a template at setup time, the template must live as a **data/doc file under `docs/`** (not as a Python string in `src/`), otherwise it would trip this check; T-M7 owns the file, this task asserts no `.py` under `src/` embeds a literal equal to a tool name. The CLI exits non-zero printing every offending file:line.
  - Additionally assert at the gateway level (re-using M7 modules and `gmail/allowlist.py` from T-M1-04, import only) that the hard-coded allow-list equals exactly `{create_draft, get_draft}` and the allowed host is exactly `gmailmcp.googleapis.com` (GM-02); a mutated copy of the source tree containing `"send_message"` makes the check fail (self-test). The forbidden set is the five names of AC-09.7 plus the `trash_*`/`mark_*_spam`/label tools of GM-01/GM-02 (a superset of the AC; documented as such).
  - Also scan the shipped help data (T-M1-15) and the email template for any literal *equal* to a forbidden name (must find none).
  - Also scan `CLAUDE.md`/host-instruction files shipped by M7 only for the *presence* of the deny rules from GM-12, not for absence of names (the content test of AC-04.19 itself belongs to T-M7).
- **Tests to write first (TDD):**
  - `test_clean_tree_passes` (AC-09.7) against the real `src/`.
  - `test_planted_send_message_literal_fails` (temp copy of the tree with `x = "send_message"`).
  - `test_planted_forward_function_call_fails`, `test_trash_prefixed_names_fail`.
  - `test_word_reply_in_email_prose_is_not_flagged` (AC-09.7 second sentence): the exact sentence "Could you reply with a status or a revised date for any item?" in a string literal does not trigger; `x = "reply"` does.
  - `test_substring_and_prefix_not_flagged`: identifiers such as `forwarding_address`, `replyable`, a literal `"send_message_count"` do not trigger (whole-name equality only).
  - `test_host_instruction_files_exempt`: a planted `send_message` inside `CLAUDE.md` or `docs/` does not fail the scan, while the same literal in a `.py` under `src/` does.
  - `test_allowlist_is_exactly_create_and_get_draft` and `test_host_restriction_exact`.
- **Done when:** AC-09.7 test green; the planted-violation tests prove the check can fail; the script is runnable standalone (`python tools/check_no_send.py`) with exit code 0/1 and wired into the release checklist (T-M9-08).

### T-M9-02 — Determinism test
- **Size:** M
- **Depends on:** M6 (list) at minimum; M7 for the follow-up preview part
- **Parallel with:** T-M9-01, T-M9-03
- **Spec refs:** AC-09.4 (scope as amended, G-13), BR-18, NFR-DET-01, spec 4.1 (`ACTIONTRACK_NOW`), spec 4.2 (report definitions), FR-02.4 (AC-02.4), FR-04.11 (handoff output), plan 2.6, plan 4 (property/invariants: randomised folder permutations)
- **Files:** `tests/integration/test_determinism.py`, `tests/fixtures/notes/` (copy of the sample; created in M2 - re-use), `tests/helpers/permute_folder.py`
- **Work:**
  - **Scope (AC-09.4 as amended by G-13; integrator note b-5 is resolved):** the guarantee covers (1) **`list`** in both formats, (2) the **follow-up preview and handoff output** (stdout text/JSON), and (3) the **ingest report excluding its `Ingest run #N` number and its absolute `folder=` value**. Stored timestamps are reproducible only when the test-only `ACTIONTRACK_NOW` is set; without it they are real-clock values and are **outside** the guarantee. The test must not claim more than that.
  - Run `ingest` + `list` (table and JSON, `--status all`, `--ambiguous`) + `followup "Priya Shah" --dry-run` + `followup "Priya Shah" --yes --format json` on the `handoff` channel (handoff file written into a temp `handoff_dir`) twice against fresh temp DBs, each with the same `--today 2026-10-22`, the same `ACTIONTRACK_NOW=2026-10-22T09:30:00Z`, a fixed config, copies of the sample; assert **byte-identical stdout** (compare raw bytes), identical normalised stderr, identical handoff file **name and bytes** (the name embeds the timestamp, FR-04.11), and identical `args_sha256`/`item_ids_hash`.
  - Path-separator invariance (AC-09.4 "across OS path separators"): run the same pipeline on a copy placed under a different parent directory and, on Windows, feed the folder both as `a\b` and `a/b`; assert stored `rel_path` values use `/` and that stdout is identical **after** masking only the line that prints the absolute `folder=` root and the `Ingest run #N` number. Only Windows is available as a reference platform, so the cross-OS half is covered by asserting `/` separators in every printed path (SOURCE column, report lines) rather than by running on a second OS (see notes, untestable-as-written item).
  - Order independence: ingest the same files copied in 5 different random creation orders (seeded permutations) and in two different relative-name casings; the final business-table dump (sorted dump of all tables except timestamps and run ids) and `list --status all --format json` (run with `ACTIONTRACK_NOW`) must be identical (FR-01.1 ordering rule, BR-18).
  - Hash-seed independence: run the pipeline in subprocesses with `PYTHONHASHSEED=0` and `=12345`; outputs identical (guards against set/dict iteration leaks, plan 2.6).
  - **Timestamp half of AC-09.4:** with `ACTIONTRACK_NOW` set, `list --format json` shows identical `created_at`/`updated_at`/`completed_at` values across two runs (and equal to the injected instant); without it two runs separated in time differ in those fields only, and no stored timestamp is ever printed in table output.
- **Tests to write first (TDD):**
  - `test_two_runs_byte_identical_list_table_and_json` (AC-09.4).
  - `test_path_separator_invariance_rel_paths_slash`.
  - `test_creation_order_independence_business_dump`.
  - `test_pythonhashseed_independence`.
  - `test_followup_dry_run_preview_deterministic` (SC-54 data).
  - `test_handoff_output_and_file_deterministic_with_now` (AC-09.4 handoff scope, SC-57 data).
  - `test_json_timestamps_reproducible_only_with_actiontrack_now` (AC-09.4 last sentences).
  - `test_ingest_report_deterministic_modulo_run_number_and_folder` (AC-09.4 report scope).
- **Done when:** AC-09.4 test green on Windows; if a nondeterminism is found it is filed against the owning milestone (not patched here).

### T-M9-03 — Performance check (NFR-PERF-01)
- **Size:** M
- **Depends on:** M6 complete
- **Parallel with:** T-M9-01, T-M9-02
- **Spec refs:** NFR-PERF-01, AC-09.5 (large-file limit sanity), plan 4
- **Files:** `tools/gen_perf_corpus.py`, `tests/perf/test_perf.py`, `pytest.ini`/`pyproject` marker `perf`
- **Work:**
  - Corpus generator (seeded, deterministic) producing 100 `.md` files totalling about 10 MiB (each under the 5 MiB `max_file_bytes`, so none triggers `E-TOO-LARGE`), realistic structure copied from the sample patterns (header, attendees, checkbox lists, relative dates, namesakes), with enough carry-over duplicates to exercise Tier-1/1b and possible-duplicate matching so the timing includes the expensive paths.
  - `ingest` of the corpus must complete in <= 10 s wall clock (monotonic timer, cold DB, temp directory on local disk), measured as a subprocess of the installed console script; report time, item count, per-phase breakdown if `--verbose` provides it.
  - Separately generate a DB containing 10,000 items (via a repo-level bulk loader or by ingesting a generated corpus) and time `list` (default and `--status all`, table and JSON) <= 1 s each.
  - Marker `perf` excluded from the default suite (`-m "not perf"`) but required in the release checklist; thresholds read from one constant block; on failure print the measured numbers; allow one retry-by-rerun only through the human, not automatic retries (no flaky auto-pass).
  - Record a baseline result (machine description, timings) in the release checklist (T-M9-08), "typical laptop" per NFR-PERF-01.
- **Tests to write first (TDD):**
  - `test_corpus_generator_deterministic_and_sized` (same seed => same bytes; total size within 9.5-10.5 MiB; file count 100).
  - `test_ingest_100_files_10mib_under_10s`.
  - `test_list_10k_items_under_1s` (table and JSON).
  - `test_no_file_triggers_too_large_in_corpus`.
- **Done when:** both timing tests pass on the Windows reference laptop with margin >= 20 percent (or the shortfall is reported with a profile and an owner); baseline recorded.

### T-M9-04 — Full sample-folder run (SC-41) and sample-derived regression pack
- **Size:** M
- **Depends on:** M6, M7 (for the follow-up scenarios SC-54/56)
- **Parallel with:** T-M9-05 (after T-M9-02 for shared fixtures)
- **Spec refs:** SC-41, AC-01.13, AC-01.11, AC-01.14, AC-01.15, AC-01.16, AC-01.17, AC-05.14 (reworded: open-only default, state-scoped report, G-19/G-25), AC-07.11-07.13 and AC-07.15/07.16 (end-to-end re-verification), AC-08.1/SC-25 (33 + 1 + 1, G-28), AC-09.5, AC-09.6, AC-09.8 (end-to-end halves), SC-42-SC-56, SC-66, spec Appendix A, spec 4.2 report definitions
- **Files:** `tests/integration/test_sample_folder_e2e.py`, `tests/golden/sample_ingest_report.txt`, `tests/golden/sample_list_all.json`
- **Work:**
  - End-to-end through the installed console script with `--today 2026-10-22` and the Appendix C roster: build a temp dir `<tmp>\notes\` holding a copy of the full 36-file sample set (`samples\notes` plus `notes\notes\scratch.md` and `untitled.md`), run `ingest <tmp>\notes` (recursive default) and `ingest <tmp>` ; assert **discovered 35 `.md`, ingested 33, empty 1, failed 1 (`scratch.md`, `E-BINARY`), `.txt` ignored, exit 5** (SC-41/AC-01.13), and the same counts for both paths.
  - `--no-recursive` on the parent folder: 0 files, exit 0, hint that 35 `.md` files exist in subfolders (AC-01.13 first half).
  - Before/after SHA-256, size and mtime of every file in the copy and in the original sample folder (AC-01.11, BR-01).
  - Sample-derived checks via CLI output: 117 checkboxes (104 open, 13 done) (AC-01.14); trap files not imported and reported as `W-CHECKBOX-IGNORED-REGION`; front-matter notes (AC-01.15); CRLF note equals LF copy (AC-01.16); offsite note candidates (AC-01.17); `list --ambiguous` (default `--status open`) returns exactly the **open** ambiguous items, `list --ambiguous --status all` all of them (the six documented groups of Appendix A.3), and the default count equals the number of ambiguous items in the report's state-scoped "Unresolved owners" section, which is also open-only (AC-05.14 as reworded, SC-53); revised-quote cluster done with `CI` and 3 sources (AC-07.11, SC-47); freeze-invoice merge (AC-07.12); wireframes `due_differs` (AC-07.13); `followup Priya` exit 6 and `followup "Priya Shah" --dry-run` preview (SC-54); `followup "Priya Nair" --include-ambiguous --dry-run` (SC-56).
  - Second consecutive ingest on the same DB (SC-25, AC-08.1 as corrected by G-28): the report reads **discovered 35 = unchanged 33 + empty 1 + failed 1**, 0 new / 0 updated, business tables identical (dump diff), and the exit code is **still 5** because `scratch.md` fails again with `E-BINARY`; the 0-byte `2026-09-09-notes-final-v2.md` stays `empty` (never "unchanged"). A test asserting "all 35 unchanged" or exit 0 is wrong.
  - End-to-end halves of cross-cutting ACs on the real sample: **AC-09.5** (copy a note, pad it above the 5 MiB `max_file_bytes` default, ingest: that file is `E-TOO-LARGE` with its size reported, other files unaffected, exit 5; also with a lowered `max_file_bytes` in a fixture config); **AC-09.6** (`Zoë Åström` and `李雷` added to a fixture note and roster round-trip unchanged through the DB, `list` table and JSON, and the follow-up preview); **AC-09.8** (a task containing `\x1b[31m` never emits a raw ESC to stdout in table or `followup` preview output, and is `\u001b`-escaped in JSON). Primary owners remain M2/M6/M7; this task re-proves them through the console script.
  - Quote-portal reopen scenario (SC-66, AC-07.16): unrelated edit to the 09-01 note leaves the cluster done; changing the 09-15 evidence line to `[ ]` reopens the 09-08 and 09-01 items with `W-STATUS-REOPENED`.
  - Golden files for the ingest report and `list --status all --format json` (path-normalised: replace the temp root with a token; do not golden the `Ingest run #` number; run with `ACTIONTRACK_NOW` so timestamps are goldenable, G-13).
  - Any divergence from Appendix A numbers is a spec question first (the A.3 text counts 100+13+2+2 = 117 including 13 done; AC-01.14 states 104 open + 13 done, consistent), not a test edit.
- **Tests to write first (TDD):** write the e2e test module and golden fixtures from spec numbers before running; unexpected results are triaged to the owning milestone.
- **Done when:** SC-41 and AC-01.13 pass exactly; AC-01.11 hash check passes for the original sample folder; the regression pack passes without edits to the sample files.

### T-M9-05 — AC-to-test traceability gate script
- **Size:** M
- **Depends on:** T-M1-01 (marker convention); runs against all milestones' tests
- **Parallel with:** T-M9-04
- **Spec refs:** plan section 4 last paragraph, plan 3 (M9 "full traceability sweep"), spec 15 (all AC IDs), spec 19 (traceability table), spec 16 (SC IDs)
- **Files:** `tools/trace_gate.py`, `tools/trace_waivers.toml`, `tests/unit/test_trace_gate.py`, `tests/fixtures/trace/`
- **Work:**
  - Extract the AC ID universe from `specs/actiontrack-spec.md` section 15: all lines matching `^- (AC-\d{2}\.\d+)` (IDs are **not** contiguous and not ordered: AC-02.9 precedes AC-02.8; AC-04.11 sits before AC-04.7; AC-05.x are interleaved; and in v0.6 four H-decision criteria are filed under the FR-06 block although they belong to other requirements - AC-09.11, AC-04.20, AC-05.19 sit after AC-06.11; the script must not assume ranges or section membership) and the SC IDs (SC-01..SC-72) from section 16's table. The v0.6 universe is **124** ACs (the index's 120 plus AC-04.20, AC-05.19, AC-06.11, AC-09.11); the gate must print the discovered count and fail if it differs from the number in a one-line `tools/trace_expected_counts.toml` that the integrator updates deliberately.
  - Extract test coverage by AST-scanning `tests/` for `@pytest.mark.ac("...")` / `@pytest.mark.sc("...")` decorators (class, function and `pytestmark` forms) rather than relying on test names or docstrings; also accept ID strings inside `pytest.param(..., marks=...)`.
  - Report: ACs with no test, tests referencing unknown AC IDs (typos), ACs whose only tests are marked `skip`/`xfail`, and ACs covered only by `perf`/`live` markers. Exit 1 on any gap not listed in `tools/trace_waivers.toml`, where each waiver needs `id`, `reason`, `owner` and `approved_by`; waivers for AC-04.x that need a live Gmail are not allowed because the fake/handoff gateway covers them (plan 4).
  - Output sorted, deterministic, plain text table (also `--format json`); a `--cross-check` mode compares spec section 19's table against the discovered set and reports a requirement row without any test.
  - Wire into the release checklist; CI runs `pytest` then `trace_gate.py`.
- **Tests to write first (TDD):**
  - `test_extracts_noncontiguous_ac_ids_from_spec` (AC-02.9 before 02.8, AC-04.11, AC-05.x, and AC-09.11/AC-04.20/AC-05.19 listed under the FR-06 block) using a trimmed spec fixture and the real spec; the real-spec test asserts all of AC-09.9, AC-09.10, AC-09.11 are in the universe.
  - `test_cross_check_reports_stale_traceability_rows`: `--cross-check` against spec section 19 flags that its AC ranges (`AC-04.1–19`, `AC-05.1–18`, `AC-06.1–10`, `AC-09.1–10`) do not include AC-04.20, AC-05.19, AC-06.11, AC-09.11 (spec housekeeping item for the integrator, not a test gap).
  - `test_detects_untested_ac`, `test_detects_unknown_ac_typo`, `test_skipped_test_does_not_count`.
  - `test_waiver_requires_reason_and_owner`.
  - `test_pytestmark_module_level_and_class_level_markers`.
  - `test_output_deterministic`.
- **Done when:** running the gate on the full repo reports zero untested ACs/SCs (or only approved waivers); the script is stable (two runs identical) and documented in the release checklist.

### T-M9-06 — Packaging and `pipx` install verification
- **Size:** M
- **Depends on:** all functional milestones (M1-M7; M8 optional)
- **Parallel with:** T-M9-07
- **Spec refs:** D-03, D-12, PD-3, PD-5, NFR-PORT-01, FR-09.3 (`--version`), plan 2.2 (Packaging), P-1b
- **Files:** `pyproject.toml` (metadata/classifiers/readme/license field), `MANIFEST.in` or build config including migration `.sql` files, `tests/packaging/test_wheel_contents.py`
- **Work:**
  - Build sdist and wheel (`python -m build`; needs the `build` package, record that P-1b network access is required); confirm wheel contains `store/migrations/*.sql`, `store/schema.sql` and any help/golden data used at runtime (package-data configuration), and **no** `tests/`, `specs/`, `notes/` content or the sample data.
  - Clean-room install on Windows from the wheel into (a) a fresh venv and (b) via `pipx install <wheel>`; run `actiontrack --version`, `--help`, and a minimal ingest of two copied sample files with `--today`; assert console script placement and that `tzdata` is pulled automatically (no manual install).
  - Verify behaviour from a directory containing no source tree (proves package data resolves via `importlib.resources`, not relative paths) and with `PYTHONUTF8` unset and a legacy code page console.
  - Upgrade path: install version N, create a DB, install version N+1 with a test migration (fixture build), verify T-M1-12 backup and migrate behaviour via the installed program.
  - Version single-sourcing and metadata (name, summary, python requires, dependencies = only `tzdata`) checked by a test that reads wheel METADATA.
- **Tests to write first (TDD):**
  - `test_wheel_contains_migrations_and_schema`.
  - `test_wheel_excludes_tests_specs_notes`.
  - `test_metadata_requires_python_312_and_only_tzdata`.
  - `test_installed_console_script_version_and_help` (marked `packaging`; slow; run in release checklist).
  - `test_runs_outside_source_tree_loads_migrations`.
- **Done when:** wheel installs by `pipx` on a clean user profile (or venv) and the smoke ingest/list works; metadata test green; install log stored with the release record.

### T-M9-07 — README and user documentation
- **Size:** M
- **Depends on:** T-M1-15 (help text data), M7 (handoff workflow finalised); M8 status known
- **Parallel with:** T-M9-06
- **Spec refs:** FR-09.3, NFR-USE-01, spec 3 (workflows W-1..W-6), 4 (commands, v0.6 options), 4.1 (exit codes 0-9, global-option placement H-03), 4.5 (`--record-draft`, `--yes`/`--force`), Appendix B (codes), Appendix C (config), 2.2 (non-goals NG-1..NG-9), SEC-04/SEC-10 (Windows ACL note, handoff file on disk), FR-09.1 (time-zone default), 12.5 (channels), plan 7 (prerequisites)
- **Files:** `README.md`, `docs/config-reference.md`, `docs/commands.md`, `docs/warnings.md`, `docs/troubleshooting.md`
- **Work:**
  - README: what it is, install (`pipx install`, full-path Python note for Windows, `tzdata` automatic), quick start with PowerShell 5.1 examples (`actiontrack --db .\.actiontrack\actiontrack.db ingest "C:\path\notes"`; quoting of names with spaces), the five workflows W-1..W-6, safety statement (drafts only, never sends; handoff channel says "NOT yet created in Gmail"), where data lives (DB, handoff dir, config), what is read-only (BR-01).
  - `docs/config-reference.md`: every key from T-M1-04/05 with type, default, validation error; Appendix C annotated; roster rules (First+Last identity, qualifiers, aliases; SEC note that sample emails are sample values).
  - `docs/commands.md`: each command, options, exit codes **0-9** (generated from the shared help data used in T-M1-15, so it cannot drift - a test compares them), the rule that global options may precede or follow the subcommand with the last occurrence winning, `followup`'s two forms (`<owner>` and `--record-draft <file> [--draft-id|--status]`), `--yes` versus `--force`, exit 9 on cancel, "Handoff prepared ... NOT yet created" exiting 0 with `status: pending_handoff` in JSON, and examples with real output samples taken from the golden files of T-M9-04. Documents `--quiet` (errors and summary only), `--log-file`, and the test-only `ACTIONTRACK_NOW` (flagged "tests only, not for normal use").
  - `docs/warnings.md`: every Appendix B code with meaning and remediation (generated or checked against `codes.py`, see T-M9-09).
  - `docs/troubleshooting.md`: exit 4 (another process holds `<db>.lock`, config invalid, unwritable `handoff_dir` or `--log-file`), exit 5 per file failure codes, exit 6 ambiguity workflow (alias in roster then re-ingest; the re-resolution pass runs over the whole database on every ingest, so no note edit is needed), exit 7/8/9, the handoff workflow end to end (prepare, host `create_draft` with `create_draft_args` unchanged, `--record-draft`, `--status failed`, the edited-file exit 2), Windows console encoding, time-zone default fallback to a fixed-offset label and why to set `timezone = "Asia/Kolkata"` (T-M1-03), WAL/journal file notes, notes folder containing the DB.
  - Limitations section mirroring NG-1..NG-9 and open questions OQ-05..OQ-18 defaults.
  - Do not write or duplicate the host instructions (`CLAUDE.md`/skill) owned by M7; link to them.
- **Tests to write first (TDD):**
  - `test_readme_commands_match_help_data` (command list and exit-code table text equal the constants, including exit 9).
  - `test_docs_state_handoff_wording_and_exit_semantics`: README/commands doc contain the phrase "Draft NOT yet created in Gmail", name `EXIT_HANDOFF_PREPARED`-equivalent behaviour (exit 0 + `pending_handoff`), and never describe handoff preparation as a created draft.
  - `test_docs_list_every_config_key` (walk the config schema from `config.py` and require each key name in `docs/config-reference.md`).
  - `test_docs_warning_codes_complete` (shared with T-M9-09).
  - `test_readme_code_blocks_parse` (every fenced `actiontrack ...` example is syntactically accepted by the argparse parser in `--help`-less dry validation mode; no execution).
- **Done when:** the doc tests are green; a person who has never seen the project can reproduce install -> ingest -> list -> done -> followup `--dry-run` on the sample folder by following the README only (record who verified and when).

### T-M9-08 — Release checklist and final gate run (independent of CLAUDE.md)
- **Size:** S
- **Depends on:** T-M9-01 through T-M9-07, T-M9-09, T-M9-10, T-M9-11
- **Parallel with:** -
- **Spec refs:** AC-09.1-09.11, NFR-PERF-01, GM-01/02, BR-01, plan 3 (M9 exit criteria), plan 8 (PR-1..PR-8)
- **Files:** `docs/release-checklist.md`, `tools/release_gate.py`
- **Work:**
  - `docs/release-checklist.md` is a stand-alone human-and-script checklist that works with no `CLAUDE.md`, no Claude session and no Gmail connector: version bump, clean venv from the wheel, then, in order: (1) `pytest -m "not perf and not packaging"`; (2) `tools/check_no_send.py` (T-M9-01); (3) determinism suite (T-M9-02); (4) `pytest -m perf` with baseline recorded (T-M9-03); (5) sample-folder e2e and original-sample hash check (T-M9-04); (6) `tools/trace_gate.py` (T-M9-05); (7) packaging smoke (T-M9-06); (8) docs tests (T-M9-07), CLI options end-to-end (T-M9-10) and gateway host/transport conformance (T-M9-11); (9) `pip list` shows only `tzdata` as runtime dependency; (10) schema snapshot test and migration backup test; (11) manual check that the handoff channel text says "Handoff prepared. Draft NOT yet created in Gmail." with exit 0 and `status: pending_handoff` in JSON (AC-04.14), that declining the prompt exits 9 (AC-04.16), that an unwritable `handoff_dir` exits 4 (AC-04.20), and that no output contains "created" on failure paths (re-run AC-04.4/SC-20/21 tests); (12) manual confirmation that live Gmail steps (spike part 2, M8) are **not** required for v1 release when `channel = "handoff"`, and an explicit line recording the M8 deferral.
  - `tools/release_gate.py` runs the scriptable items sequentially, stops on first failure, prints a pass/fail table, and writes a `release-report.txt` (versions, timings, pass/fail, git status if any, sample-folder SHA listing) - no network access, no Gmail access.
  - Final cross-checks: help/exit-code table versus code constants (AC-09.2 re-run), and every milestone exit criterion of plan section 3 mapped to a passing test in the report.
  - Tag/version steps and rollback notes (previous wheel retained; DB `.bak` behaviour from T-M1-12).
- **Tests to write first (TDD):**
  - `test_release_gate_stops_on_first_failure` (inject a failing step).
  - `test_release_gate_makes_no_network_calls` (socket patched to raise).
  - `test_checklist_references_existing_scripts` (each command in the checklist exists).
- **Done when:** `tools/release_gate.py` passes on a clean checkout; the checklist has been executed once by a person other than the implementer and signed in the report.

### T-M9-09 — Warning/error code catalogue consistency test
- **Size:** S
- **Depends on:** M2-M7 (codes emitted by their code paths); T-M1-02
- **Parallel with:** T-M9-05, T-M9-06
- **Spec refs:** Appendix B (v0.6 complete catalogue incl. the four G-20 additions and the emitting-component note), spec 11, NFR-LOG-01 ("each warning has a stable code"), NFR-MAINT-02, AC-01.4/01.9/01.18/05.13 (code names asserted by many tests)
- **Files:** `tests/unit/test_code_catalogue.py`, `tools/list_codes.py`
- **Work:**
  - AST-scan `src/` for every code string used in `Diagnostics.warn(...)`/`ParseWarning(...)`/`IngestWarning` creation and assert each is (a) registered in `codes.py` and (b) present in Appendix B (v0.6 is complete: `W-ATTENDEE-NAME-INCOMPLETE`, `W-HTML-COMMENT-UNCLOSED`, `W-CHECKBOX-EMPTY`, `W-DIR-UNREADABLE` are now listed, so no "documented additions" list is needed); unused registered codes are listed (report only, not a failure, with a waiver list for codes the spec defines but v1 cannot trigger).
  - For every code in `codes.py` assert at least one test triggers it (via a `@pytest.mark.code("W-...")` marker or by scanning test assertions for the string) so each documented warning has a regression.
  - Ensure the due-unresolved reasons in `DUE_REASONS` equal the Appendix B list exactly (`no_meeting_date`, `vague`, `range`, `same_weekday`, `this_weekday_past`, `weekend_meeting`, `ambiguous_numeric`, `invalid_date`, `weekday_date_mismatch`, `multiple_dates`) and that every stored `due_rule` used in `src/` is an id of the spec 9.5 catalogue (`DUE_RULES`, AC-06.6).
  - Emitter check: each warning code is emitted only by the component Appendix B assigns (parser, ingest service for `W-MEETING-DATE-FUTURE`, ingest/resolution layer for the `W-OWNER-*`/`W-STATUS-*`/`W-FILE-MISSING` group); a parser module emitting `W-MEETING-DATE-FUTURE` fails the test (BR-05: the parser is independent of today).
  - `tools/list_codes.py` prints the catalogue (code, kind, tests referencing it) deterministically; this feeds `docs/warnings.md` (T-M9-07).
- **Tests to write first (TDD):**
  - `test_every_emitted_code_is_registered`.
  - `test_every_registered_code_has_a_triggering_test_or_waiver`.
  - `test_catalogue_matches_spec_appendix_b_exactly` (both directions; replaces the earlier "plus additions" variant).
  - `test_warning_emitted_only_by_assigned_component` (Appendix B emitter note).
- **Done when:** the three tests pass; the waiver list (if any) contains a reason for each entry.

### T-M9-10 — CLI options end-to-end: `--quiet`, `--log-file`, `--format` on every result command (new in re-sync)
- **Size:** S
- **Depends on:** M6 complete (`list`, `done`), M7 complete (`followup`, `--record-draft`), T-M1-17
- **Parallel with:** T-M9-01, T-M9-02, T-M9-11
- **Spec refs:** **AC-09.9** (final proof; primary owner T-M1-17), spec 4.1, 4.4, 4.5, 4.2 (summary line), NFR-LOG-01/02, SEC-06
- **Files:** `tests/integration/test_cli_options_e2e.py`
- **Work:**
  - Through the installed console script against a temp DB seeded from the sample (`--today 2026-10-22`, `ACTIONTRACK_NOW` fixed): prove with **real handlers** what T-M1-17 proved with test handlers.
  - `ingest ... --quiet`: stderr carries no warnings/info (the sample emits many) but the ingest summary line and the failure line for `scratch.md` (errors) are present, and stdout still has the full report/result data; exit 5.
  - `--log-file <path>` on `ingest`: file is created/appended, UTF-8 with LF only, contains the warning lines at stderr's verbosity, never contains a task body longer than a log line is allowed to carry nor, in a `followup` run, any text of the email body (SEC-06); `--log-file` pointing at a directory => exit 4 and the DB untouched.
  - `done 12 --format json` => exactly the object of spec 4.4 (`id`, `status`, `previous_status`, `already_done`, `completion_origin`, `completed_at`); a second `done 12 --format json` prints `"already_done": true`, exit 0, `completed_at` unchanged, no DB write; `done AT-9999 --format json` => exit 3.
  - `followup "Priya Shah" --yes --format json` on `handoff`: object with `status: "pending_handoff"`, `person_id`, `item_ids`, `item_ids_hash`, `handoff_file`, `args_sha256`; `--format json` with the `fake` channel => `status: "created"` and `draft_id`; `--dry-run --format json` => `status: "dry_run"`; declined prompt => `status: "cancelled"` exit 9; owner with no open items => `status: "no_open_items"` (exit 0). The statuses are the set listed in spec 4.5 and nothing else.
  - Option placement through real commands: `--format json` before and after `list`/`done`/`followup` identical (AC-09.11 re-check).
- **Tests to write first (TDD):**
  - `test_quiet_ingest_keeps_summary_and_errors_drops_warnings` (AC-09.9).
  - `test_log_file_utf8_lf_append_and_exit4_for_directory` (AC-09.9).
  - `test_done_json_object_and_already_done_flag` (AC-09.9, spec 4.4).
  - `test_followup_json_status_values_per_spec_4_5` (AC-09.9, AC-04.14 wording check).
  - `test_requested_result_data_never_suppressed_by_quiet` (AC-09.9).
- **Done when:** the five tests are green and every `--format json` object matches the key list of spec 4.4/4.5; AC-09.9 has tests in both T-M1-17 and here.

### T-M9-11 — Gateway host/transport conformance and no-network guard (new in re-sync)
- **Size:** S
- **Depends on:** M7 complete (gateway interface, `FakeGateway`, `HandoffGateway`); M8 modules if present; T-M1-01 socket guard, T-M1-04
- **Parallel with:** T-M9-01, T-M9-02, T-M9-10
- **Spec refs:** **AC-09.10** (transport half; config half owned by T-M1-04), GM-02 (G-16), NFR-TEST-02, SEC-02, plan 2.5/4 (safety layer)
- **Files:** `tests/safety/test_gateway_conformance.py`, `tests/safety/fake_transport.py`
- **Work:**
  - A reusable `RecordingTransport` (in-process object: receives the request - host, path, JSON-RPC method, tool name, arguments - and returns a scripted response) used by every real-gateway test; the real gateway code accepts a transport by injection and **never a URL** for tests (GM-02). The conformance suite is **parametrised over every gateway implementation present**: `FakeGateway`, `HandoffGateway` (makes zero transport calls), and, once M8 lands, `GoogleMcpGateway`.
  - Assertions per AC-09.10: with the transport recording each request, (1) no request names any tool other than `create_draft` or `get_draft` (loop over the 30 known tool names of plan F-1/F-3: every other one is refused in code **before** any request reaches the transport); (2) no request targets a host other than `gmailmcp.googleapis.com` (a gateway constructed with a different host raises before sending); (3) the OAuth-endpoint exception (`accounts.google.com`, `oauth2.googleapis.com`) is reachable only from the sign-in/refresh component, never from the draft path; (4) the whole suite runs under the T-M1-01 socket guard so no socket is opened (a test that would open one fails loudly).
  - Re-assert the config half: a config with `google_mcp_url` on another host => exit 4 naming the key (re-uses T-M1-04's fixture), and `ALLOWED_MCP_HOST`/`ALLOWED_TOOLS` in `gmail/allowlist.py` are the only definitions (grep test; the M7/M8 gateway imports them).
  - Phase 1 scope note: `GoogleMcpGateway` is phase 2 (M8, deferred). Until it exists the suite covers `FakeGateway` and `HandoffGateway` (which must show **zero** transport calls and zero socket attempts across `followup --yes` and `--record-draft`), and the parametrisation picks the M8 gateway up automatically; the release gate marks the M8 rows "deferred" explicitly rather than skipping silently.
- **Tests to write first (TDD):**
  - `test_only_create_and_get_draft_reach_transport` (AC-09.10, GM-02).
  - `test_other_host_refused_before_send` (AC-09.10).
  - `test_handoff_gateway_makes_no_network_or_transport_call` (AC-04.1/AC-04.2 support, SEC-02).
  - `test_suite_runs_without_opening_a_socket` (AC-09.10).
  - `test_allowlist_constants_defined_once` (grep test; ties to T-M9-01).
  - `test_conformance_is_parametrised_over_all_gateways` (fails if a gateway module exists that is not in the parameter list).
- **Done when:** AC-09.10 has a green config test (T-M1-04) and a green transport test (here); the M8 rows are recorded as deferred in the release report.

## Part A — notes for the integrator (updated for spec v0.6)

### (a) AC / SC IDs covered by these tasks (spec v0.6)
- **Fully proven in Part A:** AC-05.12 (T-M1-05), AC-09.1 (T-M1-04, T-M1-16), AC-09.2 (T-M1-15, T-M1-16, re-run T-M9-08), AC-09.3 (T-M1-03, incl. the no-`timezone` offset-label case), AC-09.4 (T-M9-02; timestamp primitive T-M1-03), AC-09.7 (T-M9-01), **AC-09.11 (T-M1-17, re-checked in T-M9-10)**, SC-37 (T-M1-03), SC-41 (T-M9-04), AC-01.13 and AC-01.11 (T-M9-04).
- **Owned jointly (both halves are tasks):** **AC-09.9** = T-M1-17 (CLI contract, test handlers) + T-M1-09 (diagnostics semantics) + T-M9-10 (end to end with real `ingest`/`done`/`followup`); **AC-09.10** = T-M1-04 (config half: wrong `google_mcp_url` host => exit 4) + T-M9-11 (transport half: fake transport, only `create_draft`/`get_draft`, no other host, no socket); AC-08.9 = T-M1-11 (lock primitive, exit 4, live vs crashed) + the M5 ingest tasks (interrupted-run marking in a real ingest).
- **Primitives built here, completed by other milestones:** AC-09.8 (sanitiser and renderers T-M1-07/08/09; end-to-end re-proof T-M9-04), AC-09.6 (UTF-8 streams, DB round-trip, table width T-M1-05/06/07/10/13; list/email halves in M6/M7; re-proof T-M9-04), AC-09.5 (primary M2/M5; re-proved end to end in T-M9-04), AC-01.10 and SC-40 (in-memory dry-run copy and byte-identical DB in T-M1-13; report half in M5), AC-01.19 storage half (`raw_text` verbatim, T-M1-13), AC-03.1 and AC-03.5 (ID parser T-M1-13; command in M6), AC-02.7/AC-02.9/AC-02.11/AC-04.13 exit-2 plumbing (T-M1-14 `validate_args` extension point; semantics in M6/M7), AC-04.12 DB half (no body column, six statuses, T-M1-10), AC-05.8 (config key parsed, T-M1-04; behaviour in M4/M5), NFR-REL-04 (T-M1-12), NFR-LOG-01/02 (T-M1-09), NFR-REL-01 (T-M1-11), NFR-PERF-01 (T-M9-03), packaging/NFR-PORT-01 (T-M9-06).
- **Re-verified end to end in M9 (owned by earlier milestones):** AC-01.14 to AC-01.17, AC-05.14, AC-07.11 to AC-07.13, AC-07.15, AC-07.16 (SC-66), AC-08.1 (SC-25, 33 + 1 + 1, exit 5), SC-42 to SC-56 (T-M9-04), AC-04.4 / SC-20 / SC-21 and AC-04.14/04.16/04.20 wording and exit codes (release checklist item 11).
- **Gate coverage:** T-M9-05 asserts every AC of spec section 15 (124 in v0.6) has a test.
- **Not covered by Part A and not coverable without the other parts:** all AC-01.1-01.9, AC-01.12, AC-01.18-01.20, AC-02.*, AC-03.2-03.6, AC-04.*, AC-05.1-05.11/05.13-05.19, AC-06.*, AC-07.1-07.10, AC-07.14, AC-08.2-08.8, AC-08.10, AC-09.5 (primary).

### (b) Ambiguities, contradictions and gaps (status against spec v0.6)
**Resolved by v0.5/v0.6 (no longer open):** 1 default time zone (G-10, FR-09.1: fixed-offset label, T-M1-03); 2 concurrency lock (G-14, `<db>.lock`, T-M1-11); 3 Appendix B mismatches (G-20, T-M1-02/T-M9-09); 5 determinism scope and timestamps (G-13, `ACTIONTRACK_NOW`, T-M1-03/T-M9-02); 8 `--log-file`/`--quiet` semantics (G-18, T-M1-09/T-M1-17); 9 entity fields (G-11, T-M1-10); 12 `--format` on `done`/`followup` (G-18); 13 AC-09.7 meaning (G-15, T-M9-01); config `handoff_dir` base (Appendix C: relative to the current directory).
**Still open (defaults adopted, please confirm):**
4. An explicit `--config`/`ACTIONTRACK_CONFIG` path that does not exist is assumed exit 4 (not stated); `--db` inside the notes folder is not refused (SEC-01 says "outside by default" only).
6. `person_id` for non-Latin names and post-fold collisions is not defined in spec 10.2a (T-M1-05 defines a Unicode-preserving slug).
7. Roster email uniqueness is not specified (T-M1-05: duplicate emails => exit 4).
10. Busy timeout and journal mode are not specified (T-M1-11 fixes a constant and records the measured decision).
11. Backup naming on a second migration (`*.bak`) is unspecified (T-M1-12 never overwrites).
14. Counts are consistent (35 `.md` + 1 `.txt`); the hint must say "35 `.md` files".
15. P-1c: `build` is still not installed (needed by T-M9-06).
16. AC-09.2 spans milestones; later option changes must update the help data (T-M9-08 re-checks).
17. New files outside plan 2.3 are now registered in plan v0.4 except `gmail/allowlist.py` (two constants) and `store/migrations` helpers.

**New findings against spec v0.6 (for the integrator):**
18. **Spec housekeeping:** section 15 files AC-04.20, AC-05.19 and AC-09.11 under the FR-06 block (after AC-06.11); section 19 traceability ranges are stale (`AC-04.1-19`, `AC-05.1-18`, `AC-06.1-10`, `AC-09.1-10`; SC-70..SC-72 missing from the SC column); the index says the spec holds 120 ACs but the file now has **124** (checked: 124 unique `- AC-` lines); Appendix D still names plan v0.3 and tasks v0.1. T-M9-05 `--cross-check` will report these; none is a test gap.
19. `--quiet` and `--verbose` together: spec 4.1 writes `--quiet | --verbose` (exclusive) but H-03 says a repeated option is "last wins" - the two flags are different options, so combining them is unspecified. Assumption A-T2: exit 2.
20. `ACTIONTRACK_NOW` and `--today`: spec 4.1 says NOW fixes the instant of stored/printed timestamps and `--today` fixes the date; it does not say whether NOW alone also drives "today" or whether NOW belongs in `--help`. Assumption A-T1: NOW alone drives the date in the configured zone; NOW is not advertised in help.
21. **AC-09.4 untestable as written on one machine:** "across OS path separators" cannot be proven without a second OS; T-M9-02 asserts `/` separators in every printed path on Windows (the only reference platform). The handoff file name embeds a timestamp, so the follow-up handoff half of AC-09.4 is deterministic only with `ACTIONTRACK_NOW`.
22. **AC-09.10 "real gateway tests":** the real gateway is phase 2 (M8, deferred), so in phase 1 only the config half and the `FakeGateway`/`HandoffGateway` (zero transport calls) are testable; T-M9-11 is parametrised to pick M8 up automatically and the release report marks it deferred.
23. **AC-09.7 vs the permission template:** AC-04.19 requires the host-instruction/permission files to name `send_message`, `reply`, `forward`; AC-09.7 exempts "host-instruction files" but a generator written as Python in `src/` would carry those literals. Constraint adopted: the template is a doc/data file under `docs/`, not a `.py` string (T-M9-01, T-M7).
24. AC-09.3 default label has no DST rules: a user in a DST zone gets a wrong "today" for part of the year; spec accepts this and strongly recommends an IANA `timezone`; `--quiet` also suppresses the one-line recommendation (spec 4.1), which AC-09.3 does not mention.
25. AC-04.19 depends on an installation-specific connector id embedded in permission names; the test can only assert the template, not a live generated list.

## Re-sync log (spec v0.6)
Part A (M1, M9) re-synced to spec v0.6, plan v0.4, tasks index v0.2 section 4.4. Task IDs are stable; 3 tasks are new.

### Changed tasks (existing IDs, rewritten in place)
| Task | Reason |
|---|---|
| Conventions header | Synced to v0.6; exit codes 0-9; H-03 placement; new module `gmail/allowlist.py` |
| T-M1-01 | Autouse socket guard for GM-02/AC-09.10 (no test opens a socket) |
| T-M1-02 | Exit code **9** and `EXIT_HANDOFF_PREPARED = 0` constant (G-01, G-09); H-01 exit 4 mapping; Appendix B now complete (G-20), `DUE_RULES`, ItemLink reasons, emitter map |
| T-M1-03 | `ACTIONTRACK_NOW` (G-13); default zone = fixed-offset label from the OS, no IANA lookup, stderr recommendation (G-10, AC-09.3) |
| T-M1-04 | GM-02 host/tools as code constants, `google_mcp_url` only validated (G-16, AC-09.10 config half); `attendee_disambiguation` key test (G-08, AC-05.8); `handoff_dir` base |
| T-M1-05 | Identity-key corner cases and folded key (G-30), `Person` fields (G-11) |
| T-M1-06 | `--log-file` UTF-8/LF, owner-only private file helper for the handoff file (G-13/G-18, SEC-10), AC-09.4 LF |
| T-M1-08 | Shared `{as_of, timezone, items[]}` envelope helper, `done`/`followup` JSON objects, timestamps with `ACTIONTRACK_NOW` (G-18, G-19, G-13) |
| T-M1-09 | `--quiet` = errors + summary only, never suppresses result data; `--log-file` now spec-defined, exit 4 (G-18, AC-09.9) |
| T-M1-10 | Schema rebuilt from the v0.6 7.2 field list (G-11, G-12, G-26): `task_text`, per-source columns, `item_source_owner` child table, symmetric link CHECK, six `followup_draft` statuses, `args_sha256`, `handoff_path`, no body column, `partial` run status; dropped obsolete `to_email`/`as_of_date`/`google` source |
| T-M1-11 | Lock file `<db>.lock` held by every writer, exact exit-4 message, dry-run/read-only take no lock, live vs crashed run (G-14, AC-08.9) |
| T-M1-13 | `raw_text` stored verbatim vs NFC funnel (G-22); dry-run takes no lock and still runs the 10.7.9 passes in memory |
| T-M1-14 | Global options before and after the subcommand (H-03); `followup --force`, `--record-draft` without owner, `--status`, `--format` on all four commands (G-18); exit-code mapping 0-9 incl. 9 and `EXIT_HANDOFF_PREPARED` |
| T-M1-15 | Help for exit 9, `--force`, `--record-draft`, global-option placement, `--log-file`; AC-09.7 prose allowance (G-15) |
| T-M1-16 | Exit gate also proves AC-09.9 (CLI half), AC-09.11, AC-09.10 config half; depends on T-M1-17 |
| T-M9-01 | AC-09.7 exact-identifier/literal rule, exempt host-instruction files, permission template as a data file (G-15) |
| T-M9-02 | AC-09.4 scope and `ACTIONTRACK_NOW`; handoff output/file included; ingest report modulo run number and `folder=` (G-13) |
| T-M9-04 | AC-05.14 open-only wording; AC-08.1 on the sample = 33 + 1 + 1, exit 5 (G-28); AC-09.5/09.6/09.8 end-to-end; SC-66 reopen scenario; goldens with `ACTIONTRACK_NOW` |
| T-M9-05 | Universe is 124 ACs incl. misfiled AC-04.20/05.19/09.11; stale section 19 cross-check |
| T-M9-07 | Docs for exit 9, `--yes`/`--force`, handoff flow, H-03, `--quiet`/`--log-file`, lock, time-zone fallback |
| T-M9-08 | AC-09.1-09.11; depends on T-M9-10/11; release item 11 covers exit 0/9/4 handoff cases |
| T-M9-09 | Appendix B complete (no "additions" list); `DUE_REASONS`/`DUE_RULES` exact; emitter-component check |

Reviewed and **unchanged** (no v0.6 impact): T-M1-07, T-M1-12, T-M9-03, T-M9-06.

### New tasks
| Task | Purpose |
|---|---|
| T-M1-17 | Global option placement and last-wins (H-03, **AC-09.11**), CLI-level `--quiet`/`--log-file`/`--format` contract (**AC-09.9**) |
| T-M9-10 | End-to-end proof of `--quiet`, `--log-file`, `--format json` on `done`/`followup` with real handlers (**AC-09.9**) |
| T-M9-11 | Gateway host/transport conformance and no-network guard (**AC-09.10**, transport half) |

### ACs that previously had no task and are now owned
- **AC-09.9:** primary T-M1-17; semantics T-M1-09; end-to-end T-M9-10.
- **AC-09.10:** config half T-M1-04; transport half T-M9-11 (M8 gateway tests plug into it).
- **AC-09.11:** T-M1-17 (re-checked in T-M1-16 and T-M9-10).

### Remit coverage check (every AC-09.* and config/roster/clock/DB/CLI-shell/determinism/performance/packaging item has at least one task)
AC-09.1 T-M1-04/16 - AC-09.2 T-M1-15/16 - AC-09.3 T-M1-03 - AC-09.4 T-M9-02 - AC-09.5 M2 primary, T-M9-04 e2e - AC-09.6 T-M1-05/06/07/10/13, T-M9-04 - AC-09.7 T-M9-01 - AC-09.8 T-M1-07/08/09, T-M9-04 - AC-09.9 T-M1-17/T-M9-10 - AC-09.10 T-M1-04/T-M9-11 - AC-09.11 T-M1-17 - AC-05.12 T-M1-05 - AC-08.9 T-M1-11 (+M5) - AC-01.10 T-M1-13 (+M5) - AC-01.11/01.13 T-M9-04 - NFR-REL-04 T-M1-12 - NFR-PERF-01 T-M9-03 - packaging T-M9-06.
