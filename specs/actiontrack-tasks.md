# actiontrack — Tasks (SDD Phase 3)

| Field | Value |
|---|---|
| Inputs | `actiontrack-spec.md` v0.5 (task bodies were written against v0.4; see §4.4), `actiontrack-plan.md` v0.3 |
| Phase | **Tasks** — implementation task breakdown. **No application code has been written.** |
| Version | 0.2 — index status updated: gap register §4 accepted and applied to spec v0.5 and plan v0.3; task bodies (`tasks/part-*.md`) unchanged, still v0.1, pending re-check per §4.4 |
| Date | 2026-10-22 |
| Authoring | Four parallel drafting agents (one per milestone group), then integrated and checked here |

## 1. How this document is organised
The task bodies live in four files under `specs/tasks/` (each task has size, dependencies, spec refs, files, work, tests-first, done-when). This index gives the totals, the execution order, and — most importantly — the **register of spec/plan gaps the task authors found** (§4). Several tasks adopt a *default* for a gap; those defaults are listed so you can overrule them before work starts.

| File | Milestones | Tasks |
|---|---|---|
| [tasks/part-A-M1-M9.md](tasks/part-A-M1-M9.md) | M1 Foundations, M9 Hardening & release | 28 |
| [tasks/part-B-M2-M3.md](tasks/part-B-M2-M3.md) | M2 Parsing core, M3 Date resolver | 33 |
| [tasks/part-C-M4-M5.md](tasks/part-C-M4-M5.md) | M4 People & owners, M5 Ingest service | 37 |
| [tasks/part-D-M6-M7-M8.md](tasks/part-D-M6-M7-M8.md) | M6 list+done, M7 followup (handoff), M8 google channel (phase 2) | 44 |

## 2. Totals
| Milestone | Tasks | S | M | L | Est. days |
|---|---|---|---|---|---|
| Milestone M1 — Foundations | 17 | 7 | 8 | 2 | 15.5 |
| Milestone M2 — Parsing core | 20 | 5 | 14 | 1 | 18.5 |
| Milestone M3 — Date resolver | 13 | 3 | 10 | 0 | 11.5 |
| Milestone M4 — People & owners | 15 | 3 | 12 | 0 | 13.5 |
| Milestone M5 — Ingest service | 22 | 4 | 15 | 3 | 23 |
| Milestone M6 — list + done | 12 | 6 | 6 | 0 | 9 |
| Milestone M7 — followup (phase 1: handoff) | 21 | 8 | 13 | 0 | 17 |
| Milestone M8 — google channel + gmail-login (phase 2, deferred) | 11 | 3 | 8 | 0 | 9.5 |
| Milestone M9 — Hardening & release | 11 | 5 | 6 | 0 | 8.5 |
| **Total (all)** | **142** | | | | **126** |
| **Phase 1 (excl. M8)** | **131** | | | | **116.5** |

Effort uses S = 0.5, M = 1, L = 2 developer-days; it is a planning figure, not a commitment. M8 (phase 2) is excluded from the phase-1 total.

## 3. Execution order
```
M1 ──► M3 (T-M3-01/02 first) ──► M2 ──► M4 ──► M5 ──► M6 ──► M7 ──► M9
        └────────── M3 and M2 run in parallel after M1; M2's meeting-date task needs the M3 date parser ─────────┘
M8 (phase 2) can start after M7, but is blocked on the Google prerequisites you deferred.
```
- **Walking skeleton (first deliverable):** M1 + the minimum slice of M2/M3/M5/M6 that ingests the three quote-portal notes (09-01, 09-08, 09-15) and lists them, proving Tier-1b completion matching end to end.
- **Parallel lanes once M1 exists:** (1) M3 date resolver, (2) M2 parsing, (3) M4 name/identity units that need no parser output, (4) M7 gateway interface + `FakeGateway` + email template, (5) the host-instruction artifacts. Everything else is sequential.
- **Always TDD:** each task lists the tests to write first, named by AC ID.

## 4. Gap register — what the task authors found in the spec and plan
Authors read the whole spec and plan, and walked the real sample through the rules. **Result of the walk:** every Appendix A.4 duplicate cluster and the 7 ambiguous-owner cases produce the outcomes the spec states; all date expectations in AC-06.7 and §9.3 are correct against the 2026 calendar; the 117/104/13 checkbox counts hold. The items below are things that would block or mislead implementation.

### 4.1 Decisions G-01…G-10 — ACCEPTED and APPLIED (spec v0.5)
| ID | Topic | Problem | Recommended resolution |
|---|---|---|---|
| **G-01** | Handoff exit code | Exit 0 for "handoff prepared" looks identical to exit 0 for "draft created" in a script | Keep exit **0**, add machine-readable status (`pending_handoff`) in `--format json`, keep the explicit "NOT yet created" text; the exit value sits behind one constant so it can change |
| **G-02** | Handoff file integrity and body on disk | The handoff file contains the email body (note text) although GM-10 says the body is not stored; nothing detects edits between preparation and the host's `create_draft` call | Nest the four draft arguments under `create_draft_args`; add `args_sha256` (stored in DB, checked by `--record-draft`); amend GM-10 to "body is not stored in the **database**; the handoff file holds it and lives under `.actiontrack/handoff/` (git-ignored, user-deletable)" |
| **G-03** | Prompt-injection path | Untrusted note text reaches a host LLM session that reads the handoff file | Host instructions say the body is **data**, must be passed unchanged, never acted on; tool permissions deny every send-capable tool; keep GM-11 |
| **G-04** | `--include-ambiguous` vs data minimisation | Task text/meeting titles/co-owners may expose other people, and the item may belong to the other namesake; subject/cap/hash behaviour undefined | Keep opt-in; **omit co-owner names**; ambiguous items excluded from subject counts and the 50-item cap (own section cap of 20); the flag is part of `item_ids_hash` |
| **G-05** | Which source drives a merged item's fields | No rule for status/due/owner when an item has several sources; an unrelated edit could reopen a Tier-1b-completed item | Canonical fields follow the **latest present source by (meeting date, path)**; a Tier-1b/manual completion is reopened only by a **later-dated** note's `[ ]` for the *same* source key, with warning |
| **G-06** | Year-less dates | "Wed 16 Sep" (year from M) conflicts with "first on/after M"; `by 9 Oct` in a note dated 10-12 would become **2027**-10-09 | With weekday+date: pick the year (M's, then ±1) whose weekday matches. Year-less without weekday: use M's year; if that is earlier than M by ≤ 60 days accept it as past (`W-DUE-BEFORE-MEETING`), otherwise next year |
| **G-07** | `on <ISO date>` classification | §9.3 says ISO = `explicit`; §9.2 says `on 2026-10-14` is `event_date`/`inferred` | A full (year-bearing) date is always **`explicit`**; `inferred` is only for year-less/trailing-event phrases. Fix the §9.2 example |
| **G-08** | Re-resolution vs unchanged files | §10.7.1 skips unchanged files, so the documented fix ("add a roster alias, re-ingest") and AC-05.8 (`attendee_disambiguation=false`) cannot take effect | The owner re-resolution pass runs **over the whole DB on every `ingest`** (and on config/roster change), independent of file hashes; also covers the AC-03.6 `W-STATUS-CONFLICT` check |
| **G-09** | Exit codes for cancel and repeat drafts | Declining the confirmation prompt has no code; `--yes` would skip the repeat-draft warning and could create a duplicate | New exit **9 = cancelled by user** (nothing created); `--yes` does **not** bypass the repeat-draft warning unless `--force` is also given |
| **G-10** | Default time zone on Windows | "System local zone" has no stdlib IANA lookup (only `tzdata`) | Default to a fixed-offset label from the OS and print it; strongly recommend setting `timezone = "Asia/Kolkata"` in config (roster already shows it) |

### 4.2 Spec housekeeping G-11…G-32 — ACCEPTED and APPLIED (spec v0.5)
| ID | Topic | Resolution |
|---|---|---|
| G-11 | §7.2 data-model gaps (all authors) | Add: `Person` first/last name, qualifier, identity key; `ItemOwner.role`; `ActionItem.due_raw_other`, `task_text`; per-source due/owner/core columns on `ItemSource`; `FollowupDraft` statuses `pending_handoff`/`created_by_host`/`failed_by_host`, channel, handoff path, `args_sha256`; `IngestRun` counters and `partial`; symmetric `ItemLink` storage rule |
| G-12 | Description vs display text | Store `description` (cleaned, full) **and** `task_text` (owner/due phrases removed) used for list/email/core; define token splitting |
| G-13 | Determinism scope (AC-09.4) | Scope to `list`, follow-up preview/handoff, and the ingest report excluding run number and absolute `folder=`; add test-only `ACTIONTRACK_NOW` so stored timestamps are reproducible |
| G-14 | Concurrency (§10.7.7) | Process-level lock file `<db>.lock`; distinguishes a crashed `in_progress` run from a live one |
| G-15 | Static no-send check (AC-09.7) | Match exact tool-name identifiers/string literals, not substrings (the word "reply" appears in the email template) |
| G-16 | GM-02 host vs tests | Allowed host hard-coded in code; tests inject a fake **transport**, not a URL; `google_mcp_url` config is validated against the allowed host |
| G-17 | Missing acceptance criteria | Add AC-04.12–16 for handoff file structure, `--record-draft` (success and host-failure), "NOT yet created" wording, repeat-draft with `--yes`, cancel exit 9 |
| G-18 | CLI option gaps | `--record-draft` makes `<owner>` optional; add `--status created|failed` to it; define `--log-file`, `--quiet` (errors+summary only), and `--format` on `done`/`followup` (`table|json`) |
| G-19 | `list` gaps | `days_overdue` in JSON only; JSON envelope `{as_of, timezone, items[]}`; `--ambiguous` default status = open (AC-05.14 reworded); `--include-ambiguous` without `--owner` ⇒ exit 2; SOURCE shows root-relative path:line |
| G-20 | Warning/error catalogue (Appendix B) | Add `W-ATTENDEE-NAME-INCOMPLETE`, `W-HTML-COMMENT-UNCLOSED`, `W-CHECKBOX-EMPTY`, `W-DIR-UNREADABLE`; define `vague` vs `range`, enumerate `due_rule` ids; `W-MEETING-DATE-FUTURE` computed in the ingest service |
| G-21 | Invalid meeting date warnings | Emit `W-MEETING-DATE-INVALID` only (matches AC-01.15); §8.1 wording fixed |
| G-22 | Parsing small gaps | Dot-prefixed files/dirs skipped; `raw_text` verbatim, `description` NFC; restricted built-in YAML-subset front-matter parser (no dependency); `W-CHECKBOX-NOSPACE` scope = `[]` only |
| G-23 | Owner-extraction rules | Rule order locates the owner clause; multi-owner expansion happens inside it; leading `Name:` only when the name is known or `@`-prefixed (so `Note:` never becomes a person) |
| G-24 | Lifecycle edge cases | `--no-recursive` must not mark nested files missing; pairing considers `present=0` rows so a removed-then-restored line does not duplicate; late-arriving older notes may yield a different final state than one combined run (documented limitation) |
| G-25 | Report definitions | Define every Items count; sections are state-scoped (current DB) with run-scoped counts clearly labelled |
| G-26 | Provenance enum & counts | Add `initial_form`, `provisional`; count "resolved via attendance" only within namesake groups |
| G-27 | Link reasons | Precedence when several apply: `owner_differs` > `status_differs` > `due_differs` > `same_file` > `recurrence` > `outside_window` > `fuzzy`; AC-05.16 clarified against AC-07.3 |
| G-28 | AC-08.1/SC-25 on the sample | "All files unchanged" applies to readable files; `scratch.md` keeps failing (exit 5) and the 0-byte file stays `empty` |
| G-29 | Example names | Spec examples use "Priya Sharma"; roster/sample have **Priya Shah** — replace; SC-44's "1 item" holds only for a single-file run (full sample has 4 ambiguous Priya items) |
| G-30 | Identity-key corner cases | Define keys for single-token and initial forms; diacritic fold applies to the folded key and to attendees; provisional row after aliasing is kept as alias |
| G-31 | Multi-source `CI` flag | Evidence holder gets `completed_by`; `CI` shows on the completed items, not the holder |
| G-32 | OAuth details (phase 2) | Client-secret storage in the protected token file, extra Google OAuth hosts and scope list defined in T-M8 before build |

### 4.3 Plan changes P-A…P-D — ACCEPTED and APPLIED (plan v0.3)
| ID | Change |
|---|---|
| P-A | New modules beyond plan §2.3: `errors.py`, `codes.py`, `io.py`, `logging_setup.py`, `report/sanitize.py`, `report/rows.py`, `store/lock.py`, `gmail/handoff.py`, `domain/rules.py`, `domain/status.py`, `parsing/owner_phrase.py`, `tools/`, `docs/` |
| P-B | T-M3-01/02 (date parser) precede T-M2-12 (meeting date) |
| P-C | Dev dependency `build` for packaging (T-M9-06); `pipx`, `tzdata`, `pytest` are now installed |
| P-D | Host-instruction files (`CLAUDE.md`, `.claude/skills/actiontrack-handoff/SKILL.md`, `.claude/settings.json`) need your confirmation of locations; permission names embed an installation-specific connector id, so the allow-list is generated from a template at setup time |

### 4.4 Task bodies affected (re-check against spec v0.5)
The part files were written against spec v0.4 and are **not edited** (read-only). The tasks below cite behaviour that v0.5 changed; each must be re-checked (and, where its work or tests-first list names the old behaviour, revised in a later pass) before work starts. Task IDs were found by searching `tasks/part-*.md` for the changed items; AC/SC numbers in brackets are the new or reworded criteria to add to a task's tests-first list. Part files: A = `part-A-M1-M9.md` (M1, M9), B = `part-B-M2-M3.md`, C = `part-C-M4-M5.md`, D = `part-D-M6-M7-M8.md`.

| Changed item (spec v0.5) | Part | Tasks to re-check |
|---|---|---|
| Exit-code table: new code **9**, `EXIT_HANDOFF_PREPARED = 0` constant, `followup` JSON `status`, cancel and repeat (G-01, G-09; AC-04.14–04.16) | A, D | T-M1-02, T-M1-08, T-M1-14, T-M1-15, T-M1-16, T-M9-07, T-M9-08; T-M7-02, T-M7-08 (cancel was exit 0; repeat+`--yes` was exit 2, now needs `--force`), T-M7-09, T-M7-11, T-M7-13, T-M7-17, T-M7-18, T-M8-10, T-M8-11 |
| Handoff file structure `create_draft_args` + `args_sha256`, `--record-draft` shape (`<owner>` optional, `--status created\|failed`), FR-04.11/04.12, GM-10 reworded (G-02, G-17, G-18; AC-04.12–04.13) | A, D | T-M1-10 (FollowupDraft columns), T-M1-14; T-M7-01, T-M7-06, T-M7-09, T-M7-10, T-M7-11, T-M7-12, T-M7-14, T-M7-18, T-M8-10, T-M8-11 |
| Body is data / host instructions / permission template (G-03, P-D; AC-04.19) | D | T-M7-14, T-M7-15 |
| `--include-ambiguous`: omit co-owner names, not in subject/50-cap, own 20-item cap, flag in `item_ids_hash` (G-04; AC-04.17–04.18) | D | T-M7-03, T-M7-05, T-M7-06, T-M7-12 |
| Canonical fields follow the latest present source; reopen rule; `CI` only on inferred items, evidence holder not flagged (G-05, G-31; AC-07.11, AC-07.15–07.16) | C, D | T-M5-08, T-M5-10, T-M5-11, T-M5-12, T-M5-13, T-M5-20; T-M6-06 (`CI` display) |
| Year-less rule and weekday+date year search, `Oct 31`-style row, AC-06.7 additions (G-06; AC-06.8–06.9) | B | T-M3-01, T-M3-02, T-M3-03, T-M3-06, T-M3-10, T-M3-11 (the "first on/after M" and "M's year, weekday must match" defaults are replaced) |
| `on <ISO date>` is `explicit`; `event_date` only for year-less trailing events (G-07; AC-06.10) | B | T-M3-01, T-M3-05, T-M3-09, T-M3-10, T-M3-11 |
| `due_rule` catalogue, `vague` vs `range` (G-20) | B, D | T-M3-01, T-M3-03, T-M3-08, T-M3-09; T-M6-08 |
| Whole-database re-resolution pass and `W-STATUS-CONFLICT` check on every ingest (G-08; AC-03.6, AC-05.8, AC-05.17, W-6) | C, D | T-M4-08, T-M4-09, T-M4-11, T-M4-14, T-M5-05, T-M5-11, T-M5-14, T-M5-20; T-M6-10; T-M1-04 (config key test for AC-05.8) |
| `AC-05.14` reworded (`--ambiguous` default status open; report count state-scoped, open only) (G-19, G-25) | A, C, D | T-M4-12, T-M4-14, T-M5-15, T-M5-20; T-M6-05 (its "keep `open` and warn" default is now the rule), T-M6-01; T-M9-04, T-M9-09 |
| `list` gaps: JSON envelope `{as_of, timezone, items[]}`, `days_overdue` JSON only, `--include-ambiguous` without `--owner` exit 2, SOURCE root-relative (G-19; AC-02.10–02.11) | D | T-M6-01, T-M6-02, T-M6-06, T-M6-07, T-M6-08, T-M6-11, T-M7-04 |
| `AC-09.7` static check: exact identifiers/literals (G-15) | A, D | T-M1-15, T-M9-01, T-M9-09; T-M7-16, T-M8-06 |
| `AC-09.4` determinism scope and `ACTIONTRACK_NOW` (G-13) | A, C, D | T-M1-06, T-M9-02; T-M5-16; T-M6-07, T-M6-08, T-M6-11, T-M7-17 |
| Default time zone = fixed-offset label from the OS (G-10; AC-09.3) | A | T-M1-03, T-M9-09 |
| Concurrency lock file `<db>.lock`, crashed vs live run (G-14; AC-08.9) | A, C | T-M1-11, T-M1-13; T-M5-04, T-M5-17 |
| `description` vs `task_text`, token splitting, core definition (G-12; AC-01.19) | B, C, D | T-M2-09 (and any task storing `description`), T-M3-04; T-M4-03, T-M5-06, T-M5-20; T-M7-05 |
| `GM-02`: hard-coded host, fake transport, `google_mcp_url` validation; OAuth hosts, client-secret storage, scopes (G-16, G-32; AC-09.10) | A, D | T-M1-04; T-M8-01, T-M8-02, T-M8-03, T-M8-06, T-M8-07, T-M8-08, T-M8-11 |
| `--quiet`, `--log-file`, `--format` on `done`/`followup` (G-18; AC-09.9) | A, D | T-M1-08, T-M1-09, T-M1-14; T-M6-09, T-M6-10, T-M7-13 |
| Data model §7.2 additions (G-11) | A, C, D | T-M1-10, T-M4-07, T-M5-01, T-M7-01 |
| Parsing gaps: dot-prefixed files, `raw_text` verbatim, YAML subset, `W-CHECKBOX-NOSPACE` scope, new codes `W-HTML-COMMENT-UNCLOSED`/`W-CHECKBOX-EMPTY`/`W-DIR-UNREADABLE`/`W-ATTENDEE-NAME-INCOMPLETE`, `W-MEETING-DATE-INVALID` only, `W-MEETING-DATE-FUTURE` in the ingest service (G-20, G-21, G-22; AC-01.18–01.20) | A, B, C | T-M1-02 (catalogue), T-M1-06; T-M2-01, T-M2-02, T-M2-03, T-M2-06, T-M2-07, T-M2-08, T-M2-12, T-M2-13, T-M2-17; T-M5-17 |
| Owner-extraction rule order and leading `Name:` (G-23); identity-key corner cases and diacritic fold (G-30); provenance `initial_form`/`provisional`, attendance counted only in namesake groups (G-26; AC-05.18) | C | T-M4-02, T-M4-03, T-M4-04, T-M4-05, T-M4-06, T-M4-07, T-M4-08, T-M4-10, T-M4-11, T-M4-14; T-M5-07, T-M5-14, T-M5-20 |
| Lifecycle: `--no-recursive` must not mark nested files missing, `present=0` rows in pairing, late-arriving older notes limitation (G-24; AC-08.10) | C | T-M5-12, T-M5-13, T-M5-19 |
| Report definitions: Files/Items counts, state-scoped sections, run status `partial` (G-25) | C | T-M5-02, T-M5-05, T-M5-10, T-M5-15, T-M5-16, T-M5-20 |
| Link-reason precedence; AC-05.16 vs AC-07.3 (G-27) | C | T-M4-04, T-M5-09, T-M5-18 (link tests), T-M5-20 |
| `AC-08.1`/SC-25 on the sample: 33 unchanged + 1 empty + 1 failed (G-28) | A, C | T-M5-02, T-M5-05, T-M5-19, T-M5-20; T-M9-04, T-M9-09 |
| Example names: `Priya Sharma` → `Priya Shah`; SC-44 single-file wording (G-29) | C, D | T-M5-20 (the only task body that contains `Priya Sharma`); T-M7-03, T-M7-17 (SC-44) |

New scenarios SC-57…SC-69 and ACs AC-01.18–20, AC-02.10–11, AC-04.12–19, AC-05.17–18, AC-06.8–10, AC-07.15–16, AC-08.9–10, AC-09.9–10 have **no owning task yet**; the T-M9-05 traceability gate will flag them until tasks reference them. Spec §15 now holds 120 acceptance criteria (96 + 24 new IDs, none removed), so the "96" in §5 below is the v0.4 figure.

## 5. Coverage
Every one of the spec's **124 acceptance criteria** and **72 scenarios** (spec v0.6) is referenced by at least one task (checked by script after the v0.6 re-sync: 142 tasks, no duplicate or dangling task IDs). Several ACs are only *partly* covered by their first task and are completed by later ones — see each part's "notes for the integrator" and "Re-sync log". Sample-count expectations hand-derived by the M5 author (105 items, 18 done, 11 links, 36 attendance resolutions) are verified by T-M5-20, not assumed. **Sample location (updated 2026-10-22):** the sample notes were relocated after the v0.4 inspection: the 36-file sample set is `samples/notes/` (34 files) plus the two leftovers `notes/notes/scratch.md` (binary) and `notes/notes/untitled.md` (valid, 2 items). Sample-path references in the spec and tasks were updated accordingly; `tests/fixtures/notes/` must hold all 36 files (task T-M1-18 adds the two missing copies).

## 6. Out of this phase
Implementation (writing code) has **not** started. The §4.1 decisions are accepted and applied (spec v0.5); the task bodies still need the §4.4 re-check, after which this task breakdown awaits your approval.
