# actiontrack — Implementation Plan (SDD Phase 2)

| Field | Value |
|---|---|
| Input | `specs/actiontrack-spec.md` v0.6 (amended after the Tasks gap register G-01…G-32 and the small decisions H-01…H-06; v0.3 was the original input) |
| Phase | **Plan** — architecture, technology decisions, milestones, test strategy. Tasks drafted separately (`actiontrack-tasks.md`). **No application code written.** |
| Version | 0.4 — re-synced to spec v0.6: removes the stale "Tier 2 export" fallback wording (phase-1 handoff channel, `HandoffGateway`, `--record-draft` is the supported path; no `--out`/`ExportGateway`) and records exit code 9, the `<db>.lock` lock file, `ACTIONTRACK_NOW` and global-option placement among the cross-cutting decisions. Earlier: 0.3 — applies gap-register items P-A…P-D and the plan-side effects of G-02/G-10/G-16 (module list §2.3, ordering §3, prerequisites §7, gateway contract §1.4/§2.5). Earlier: 0.2 — decisions PD-1…PD-5 resolved; phase-1 draft path = host-connector handoff; Python 3.12 installed |
| Date | 2026-10-22 |

---

## 1. Spike A-13 — Can a standalone CLI create Gmail drafts through an MCP connector?

### 1.1 Question and method
Spec assumption A-13: *a standalone process can act as an MCP client of the Gmail connector.* Evidence gathered on 2026-10-22 without touching any mailbox data: (1) listed this session's connectors; (2) read Google's Gmail MCP documentation; (3) probed the public endpoint unauthenticated (`initialize`, `tools/list`, and one `tools/call` with empty arguments that returned an auth error); (4) checked the local toolchain.

### 1.2 Findings
| # | Finding | Evidence |
|---|---|---|
| F-1 | The Gmail connector visible in this Claude session is a **claude.ai-managed connector with 30 tools including `send_message`, `reply`, `forward`, `update_draft`, `delete_draft`**. It is reachable only through the Claude host's own MCP connection; a separate `actiontrack` process cannot call it. `claude mcp list` in a shell shows no servers configured. | session connector status; `claude mcp list` |
| F-2 | Google publishes the **same capability as a remote MCP server**: `https://gmailmcp.googleapis.com/mcp/v1`, **Streamable HTTP**, OAuth 2.0, Developer Preview. The live `create_draft` schema (fields `to, cc, bcc, subject, body, htmlBody, includeSignature, replyToMessageId, attachments`; none required) matches the in-session connector's. | live `tools/list`; Google docs |
| F-3 | The Google server's live `tools/list` returns **23 tools; none can send** (no `send_message`, `reply`, `forward`, `update_draft`, `delete_draft`). It does include `trash_*`, `mark_*_spam` and label tools, which the app must never call. `get_draft`, `list_drafts` are read-only. | live `tools/list` |
| F-4 | The server is **stateless**: `initialize` and `tools/list` succeed with **no credentials**; `tools/call` needs an OAuth token. | live probes |
| F-5 | **Auth failure is reported as HTTP 200 with `result.isError = true`**, text "Request is missing required authentication credential". A gateway that only checks HTTP status would report a false success. | live probe |
| F-6 | Auth is **interactive OAuth 2.0 (browser consent), no service account**. Requires: Google Cloud project; `gmail.googleapis.com` + `gmailmcp.googleapis.com` enabled; Workspace Developer Preview enrollment; an OAuth client of type *Web application* with "Use this client for an AI-powered agent" selected; scopes `gmail.readonly` + `gmail.compose`; `access_type=offline` (+ `prompt=consent` to re-issue) for refresh tokens. Documented redirect URIs are for Antigravity and claude.ai only; **loopback (`http://localhost:<port>`) redirect support is not documented**. | Google docs |
| F-7 | Google's docs are internally inconsistent (create_draft "returns only `id`" vs full Draft object; tool lists differ between pages; docs list no `get_draft` while the live server has it). **Live `tools/list` is the source of truth**; the gateway must not depend on `threadId`/`viewUrl`. | docs vs live |
| F-8 | `gmail.compose` is a scope that Gmail's REST API accepts for *sending*. The MCP server exposes no send tool, but the issued token is powerful if misused; therefore the app must only ever talk to the MCP endpoint and protect the token file. | OAuth scope semantics |
| F-9 | **No Python, Node, .NET, Go or Java is installed on this machine** (only Git and Windows PowerShell 5.1). The Python shortcut is the Microsoft Store stub. Spec decision D-03 (Python ≥ 3.11) therefore needs a prerequisite install. | local probes |

### 1.3 Verdict on A-13
**Partially confirmed.** A standalone CLI **can** be an MCP client — but of **Google's remote Gmail MCP server**, not of the in-session connector. The path is technically straightforward (JSON-RPC over HTTPS) and *safer by construction* (no send tool exists). It is **blocked on user-owned prerequisites** (Cloud project, Developer Preview enrollment, OAuth client) and **one unverified point** (loopback redirect for the OAuth flow). Those require the account owner and cannot be completed autonomously.

### 1.4 Decision (supersedes spec D-01 wording)
**Gateway strategy — layered, all behind one `DraftGateway` interface:**

| Tier | Gateway | Status |
|---|---|---|
| **Phase 1 — `handoff`** (default; user decision D-11: "do not send but create a Gmail draft", Google setup bypassed) | `HandoffGateway`: `followup` writes a handoff JSON whose `create_draft_args` object holds the exact `create_draft` arguments (`to`, `subject`, `body`, `includeSignature`), plus metadata and `args_sha256` (SHA-256 of the canonical JSON of `create_draft_args`, also stored in the DB; spec FR-04.11), and records `pending_handoff`; the **host session's existing Gmail connector** then calls `create_draft` with `create_draft_args` unchanged, treating the body as data (spec GM-11/GM-12). `followup --record-draft <file> [--draft-id <id>] [--status created\|failed]` re-checks `args_sha256` and records the outcome (`created_by_host` / `failed_by_host`); a prepared handoff exits 0 (`EXIT_HANDOFF_PREPARED`) with `status: pending_handoff` in JSON | Build in M7; host instructions (`CLAUDE.md`/skill) in M7 |
| **Phase 2 — `google`** | `GoogleMcpGateway`: direct Streamable-HTTP MCP client to `gmailmcp.googleapis.com`, OAuth 2.0 auth-code + PKCE with loopback listener; token in a protected user-profile file; `gmail-login` command | **Deferred (M8)** until Google Cloud/Preview prerequisites exist; live verification user-gated |
| Testing | `FakeGateway` | M7 |

**Honesty rule (spec FR-04.13/GM-07):** on `handoff`, actiontrack says "Handoff prepared. Draft NOT yet created in Gmail" — it only says "created" when a draft id was returned to it (`google`) or recorded via `--record-draft`. Phase 1 therefore does create real Gmail drafts, but the creating call is made by the host session, not by actiontrack.

The `google` channel is never required for the other commands to work. The honest reporting rule (spec GM-07) applies to every channel: only a returned (or, on `handoff`, recorded) draft `id` counts as success. There is no export-to-file fallback: the `handoff` channel is the phase-1 delivery path.

### 1.5 Spike part 2 (user-gated) — acceptance
Run once prerequisites exist (§7 P-2…P-5). Pass when, from a throwaway script outside the app: (a) loopback OAuth completes, a refresh token is obtained and reused after a restart; (b) `tools/call create_draft` with `to=[user's own address]`, plain-text body returns an `id`; (c) `get_draft(id)` returns the same subject/body; (d) the draft appears in Gmail Drafts and nothing is sent; (e) a revoked/expired token yields `isError`/HTTP failure that the gateway classifies as failure. **Fail ⇒** the phase-1 `handoff` channel stays the supported path for v1 and M8 is re-scoped (it already is the v1 path; this spike only gates phase 2).

---

## 2. Architecture

### 2.1 Principles (from the spec)
Read-only inputs (BR-01); pure, deterministic core (BR-18); per-file atomic persistence (NFR-REL-01); single outbound channel with allow-list (GM-02); everything time/Gmail/filesystem-dependent injected (NFR-TEST-02).

### 2.2 Technology decisions
| Area | Decision | Rationale / note |
|---|---|---|
| Language | **Python ≥ 3.12** (spec D-03 said ≥ 3.11) | Not installed → prerequisite P-1. 3.12 chosen as current stable at planning time; 3.11 remains acceptable |
| CLI | stdlib `argparse` | No dependency; exit-code mapping in one place |
| Persistence | stdlib `sqlite3`, `foreign_keys=ON`, durable journal mode (WAL vs default to be chosen in M1 after testing on Windows), forward-only migrations | Spec §7.1 |
| Config | stdlib `tomllib` (read-only) | Roster/config per Appendix C |
| Time zones | stdlib `zoneinfo` **+ `tzdata` package** | Windows has no system IANA database; `tzdata` is the one required third-party dependency. With no `timezone` configured the default is a fixed-offset label taken from the OS and printed in output (spec FR-09.1, G-10); config should set an IANA zone such as `Asia/Kolkata` |
| HTTP/MCP | stdlib `urllib.request`/`http.client`, hand-written JSON-RPC client (stateless server; JSON responses) | Avoids pulling an MCP SDK; gateway is ~1 module |
| OAuth | stdlib `http.server` loopback listener + PKCE (`hashlib`, `secrets`) | No dependency |
| Tests | `pytest` (dev only) | |
| Packaging | `pyproject.toml`, `src/` layout, console script `actiontrack`; optional `pipx`/zipapp | Decision PD-3 |
| Output encoding | Force UTF-8 on stdout/stderr and file I/O explicitly | Windows console defaults to a legacy code page; required for AC-09.6 (Unicode names) |

### 2.3 Module layout (no code yet)
```
actiontrack/
  pyproject.toml
  src/actiontrack/
    cli.py                 argparse wiring, exit codes (spec §4.1), output streams
    errors.py              exception hierarchy mapped to exit codes (new, P-A)
    codes.py               warning/error code catalogue (spec Appendix B) and exit-code constants incl. EXIT_HANDOFF_PREPARED (new, P-A)
    io.py                  UTF-8 stream setup, safe file read/write helpers (new, P-A)
    logging_setup.py       levels, --quiet/--verbose, --log-file (new, P-A)
    config.py              toml load/validate, roster, defaults, error reporting (exit 4)
    clock.py               Clock interface; SystemClock; FixedClock(--today / ACTIONTRACK_NOW)
    domain/
      models.py            dataclasses: ParsedFile, ParsedItem, DateResolution, OwnerRef, ...
      dates.py             date phrase locator + resolver (pure; spec §9)
      names.py             name keys, identity key (First+Last), initials, diacritic fold (spec §10.2/10.2a)
      matching.py          description core, Tier-1/1b/possible-duplicate rules (spec §10.6)
      rules.py             rule tables as data: weekday/month names, non-person words, recurrence words, stop-words (new, P-A; NFR-MAINT-02)
      status.py            canonical status/due/owner from the latest present source; reopen rule (new, P-A; spec §10.6.1, §10.7.3)
    parsing/
      discovery.py         folder walk, recursion, symlink/hidden skipping, ordering
      reader.py            safe read: size limit, NUL/binary check, UTF-8/BOM, EOL normalise
      markdown.py          regions (front matter, fences, comments, blockquotes), list items, continuation lines
      meeting.py           meeting date/title/attendees (spec §8.1)
      items.py             item extraction → owner phrase + due phrase + description/task_text
      owner_phrase.py      owner-clause location and multi-owner expansion (new, P-A; spec §10.1)
    resolution/
      people.py            registry: roster ∪ attendees ∪ provisional; namesake groups
      owners.py            resolution algorithm (spec §10.3), whole-DB re-resolution pass
    store/
      schema.sql, migrations/
      db.py                connection, transactions, schema_version
      lock.py              process-level lock file `<db>.lock` (new, P-A; spec §10.7.7)
      repo.py              queries/upserts for entities in spec §7.2
    services/
      ingest.py            orchestrate: discover → parse all → order → per-file txn → match → report
      listing.py           filters, overdue, sort, flags
      done.py              mark done
      followup.py          select items, build DraftRequest, confirmation gate, record FollowupDraft
      email_template.py    spec §12.4 body/subject (plain text)
    gmail/
      gateway.py           DraftGateway protocol, DraftRequest/DraftResult, allow-list enforcement
      google_mcp.py        phase 2 (`google` channel): JSON-RPC client, isError handling, timeouts, no retry
      oauth.py             loopback PKCE flow, token store, refresh
      handoff.py           phase 1 `HandoffGateway`: handoff file (create_draft_args, args_sha256), --record-draft checks (new, P-A; replaces the former export.py)
      fake.py              FakeGateway / fake transport (tests)
    report/
      table.py, json.py    rendering; flags legend; truncation; control-char stripping
      sanitize.py          control-character stripping (new, P-A; spec SEC-08)
      rows.py              shared row model for list/report/email so all renderers agree (new, P-A)
  tools/                   dev scripts: traceability gate, static no-send check (new, P-A)
  docs/                    user docs, host instructions templates (new, P-A)
  tests/
    unit/ integration/ golden/ fixtures/notes/   (copy of the sample; originals never modified)
  specs/
```

### 2.4 Key flows
**Ingest.** `discover` → `read` each file (failures isolated, mapped to `E-*`) → `parse` (pure) → `resolve dates` (pure) → sort by (meeting date, rel path) → for each file in order: **one transaction** {upsert SourceFile/Meeting/Attendees; resolve owners; match/merge/link items (spec §10.6); reconcile with previous sources (spec §10.7); write events/warnings} → end-of-run whole-database re-resolution pass and `W-STATUS-CONFLICT` check (run on every ingest, independent of file hashes; spec §10.7.9) → report → exit code. Unchanged hash ⇒ skip that file. The run holds the process lock `<db>.lock` throughout (spec §10.7.7). `--dry-run` runs everything against an in-memory copy and writes nothing.

**Follow-up.** resolve person (spec §10.5) → select open items (+ ambiguity summary) → build `DraftRequest` (deterministic) → preview → confirm gate → `gateway.create_draft` → classify result → record `FollowupDraft` → report.

### 2.5 Gateway contract (derived from spike)
- Interface: `create_draft(DraftRequest) -> DraftResult(status ∈ created | failed | unknown, draft_id?, detail)`; `verify(draft_id) -> bool|None` optional.
- **Allow-list (hard-coded, tested):** host `gmailmcp.googleapis.com` only (plus, for `gmail-login`/token refresh only, the Google OAuth endpoints named in spec GM-02); tools `create_draft` (write) and `get_draft` (read-only). Every other tool name, including `trash_*`, `mark_*_spam`, label tools and any send-capable tool, is refused in code before any request is made. The host is hard-coded; the `google_mcp_url` config key is only validated against it (other host ⇒ exit 4), and tests inject a **fake transport**, never a URL (spec G-16).
- **Success criteria:** HTTP 2xx **and** no JSON-RPC `error` **and** `result.isError` is not true **and** a non-empty draft `id` can be extracted from `structuredContent` or JSON in `content[0].text`. Anything else ⇒ `failed`; timeout or unparsable-after-sent ⇒ `unknown` (spec GM-06/07: no retry).
- Do **not** depend on `threadId` or `viewUrl` (F-7); show them only if present.
- `includeSignature` is present in the live schema, so spec config `include_signature` stays; default false.
- At startup of a real draft run the client calls `tools/list` once and aborts if `create_draft` is absent or its schema lacks `to`/`subject`/`body` (guards against Preview drift).

### 2.6 Cross-cutting decisions
- **Exit codes, warnings catalogue, ID format** exactly as in spec Appendix B / §4.1. Exit codes are 0–9: **9 = cancelled by user** (confirmation declined, nothing created); a prepared handoff exits 0 via the single constant `EXIT_HANDOFF_PREPARED` (`status: pending_handoff` in JSON); an unwritable handoff directory exits 4 (H-01); `--yes` never bypasses the repeat-draft warning without `--force`.
- **Process lock:** every writing command (`ingest`, `done`, `followup` row writes, `--record-draft`) holds an OS file lock on `<db>.lock`; a second writer exits 4; the lock also distinguishes a crashed `in_progress` run from a live one (spec §10.7.7, `store/lock.py`).
- **Clock and determinism:** `--today` / `ACTIONTRACK_TODAY` fix the date; the test-only `ACTIONTRACK_NOW` fixes the instant for stored/printed timestamps; determinism (AC-09.4) is scoped to `list`, follow-up preview/handoff output and the ingest report excluding run number and absolute `folder=`.
- **Global options** are accepted before and after the subcommand, last occurrence wins, unused ones ignored (H-03); `--quiet`, `--log-file` and `--format` on `done`/`followup` are defined in spec §4.1/§4.4/§4.5 (AC-09.9, AC-09.11).
- **Determinism:** all sorts are explicit; dict/set iteration never leaks into output; timestamps come from `Clock`.
- **DB location default** `./.actiontrack/actiontrack.db`; token store `%LOCALAPPDATA%\actiontrack\` (Windows) / `~/.config/actiontrack/` (POSIX), restricted ACL/permissions.
- **Windows specifics:** SQLite file locking errors map to exit 4 "DB locked"; paths stored with `/`; PowerShell 5.1 is the reference shell for docs.

---

## 3. Milestones

Each milestone ends with a demonstrable exit criterion tied to spec acceptance criteria. Sizes: S ≈ 1–2 days, M ≈ 3–5, L ≈ 1–2 weeks of one developer.

| # | Milestone | Scope | Exit criteria (spec refs) | Size | Depends on |
|---|---|---|---|---|---|
| **M0** | Prerequisites | Runtime installed (P-1 **done**: Python 3.12.10); `pipx`/`tzdata`/`pytest` installed (P-1b done; `build` still to install, P-1c); spike part 2 **deferred to phase 2** | Runtime available | S | — |
| **M1** | Foundations | Skeleton, config + roster validation, clock, DB schema + migrations, CLI shell (`--help`, exit codes, streams, UTF-8) | AC-09.1, 09.2, 09.3 (clock), AC-05.12 (roster) | M | M0(P-1) |
| **M2** | Parsing core | Discovery, safe reader, regions, checkbox/items, multiline, meeting metadata, non-checkbox candidates, warnings | AC-01.1–01.6, 01.9, 01.14–01.20; E-codes (§11) | L | M1; the meeting-date task (T-M2-12) also needs the M3 date-literal parser (T-M3-01/02) |
| **M3** | Date resolver | Phrase locator + resolver per §9 incl. traps; table-driven suite from §9.3 and AC-06.7 | AC-06.1–06.10 | M | M1 (parallel to M2; **T-M3-01/02 are scheduled before T-M2-12**) |
| **M4** | People & owners | Name keys, First+Last identity, registry, owner-phrase extraction, resolution algorithm, ambiguity, re-resolution | AC-05.1–05.16 | L | M2, M3 |
| **M5** | Ingest service | Persistence, ordering, Tier-1/1b/possible-duplicate, re-ingestion, deleted/changed files, interruption recovery, ingest report | AC-01.7, 01.8, 01.10–01.13; AC-07.*; AC-08.*; SC-41–51 | L | M4 |
| **M6** | `list` + `done` | Filters, overdue, sorting, flags, `--ambiguous`, JSON; done semantics | AC-02.*; AC-03.*; AC-05.14–05.15 | M | M5 |
| **M7** | `followup` — phase 1 (`handoff`) | Person query, selection, email template, preview/confirm, `FakeGateway`, `HandoffGateway` (handoff file, `pending_handoff`, `--record-draft`), `FollowupDraft` log, **host instructions** (`CLAUDE.md`, `.claude/skills/actiontrack-handoff/SKILL.md`, `.claude/settings.json` — locations to be confirmed by you (P-D); the permission allow/deny list embeds an installation-specific connector id, so it is **generated from a template at setup time**; allow `create_draft`/`get_draft`, deny send-capable tools; body is data, GM-11), one end-to-end handoff demo creating a real draft to a sample address | AC-04.*; GM-01–GM-12; SC-19–24, 54, 56–62; demo draft visible in Gmail Drafts, nothing sent | M | M5, M6 |
| **M8** | `google` channel + `gmail-login` — **phase 2, deferred** | OAuth loopback PKCE + protected token file; MCP JSON-RPC client; failure classification incl. `isError`; `gmail-login [--logout]`; live smoke to self | Spike §1.5 (a)–(e); AC-04.4 vs real error cases; FR-10 | M + user-gated | M7, P-2…P-5 |
| **M9** | Hardening & release | NFR checks (perf, determinism, static "no send" check), full sample run, docs, packaging | NFR-PERF-01; AC-09.4–09.8; full traceability sweep | M | all |

**Ordering note (P-B):** M2 and M3 run in parallel after M1 *except* that T-M3-01/02 (calendar primitives and absolute-date literal parser) must land before T-M2-12 (meeting-date extraction), which reuses them; the rest of M3 stays parallel.

**Critical path:** M1 → M2/M3 → M4 → M5 → M6 → M7 → M9. M8 runs in parallel after M7 and is the only user-gated work; the product is fully usable in phase 1 (`handoff` channel) without it.

### 3.1 Suggested first slice (walking skeleton)
M1 + the smallest vertical cut of M2/M3/M5/M6 to ingest the 3 quote-portal notes and `list` them — proves schema, ordering, Tier-1b (the 09-01/09-08/09-15 revised-quote cluster) and output formatting early. The Tasks phase will cut this slice into tasks.

---

## 4. Test strategy

| Layer | What | How |
|---|---|---|
| Unit (pure) | dates (§9.3 table as fixtures), names/identity, description core + matching, owner phrase/due phrase extraction, markdown regions | table-driven `pytest`; every row of §9.3 and AC-06.7 is a case |
| Integration | ingest → DB → list/done/followup against temp DB | fixtures copied from the sample; `FixedClock`; `FakeGateway` |
| Golden | ingest report, list (table + JSON), email body for sample scenarios | stored expected outputs; Windows/POSIX path normalised |
| Fault injection | crash after file *k*; DB lock; unreadable file mid-run | hook points in ingest orchestrator (AC-08.5, 08.7) |
| Safety | allow-list; no send-capable tool reachable; no non-`gmailmcp.googleapis.com` host; `isError` ⇒ failure; timeout ⇒ unknown, no retry | gateway tests with an injected fake **transport** (no socket, no URL; AC-04.2, 04.4, 09.7, 09.10) |
| Property/invariants | idempotent re-ingest; ItemSource rows never decrease on merge; IDs never reused | randomised folder permutations |
| Live (opt-in) | spike part 2 / M8 smoke, creates one draft to the user's own address | manual, behind an env flag; never in CI |

Spec-to-test mapping is the AC ID: each test names the AC(s) it proves; a script lists ACs without a test (traceability gate in M9). Sample files are never modified; tests operate on copies.

---

## 5. Spec impact from the spike (**applied in spec v0.4**, 2026-10-22; table kept for traceability)
| Spec item | Change |
|---|---|
| §12.1 / D-01 / A-13 | Replace "MCP client of the connector" with the tiered gateway strategy (§1.4) and record F-1…F-9 as observed capability |
| GM-02 | Allow-list adds host restriction `gmailmcp.googleapis.com`; verification tool `get_draft` confirmed present on the live server |
| GM-07 | Add: HTTP 200 with `isError: true` is a **failure**; success needs a non-empty `id`; `threadId`/`viewUrl` optional |
| GM-09 | "Connector unavailable" includes: no OAuth token, expired/revoked token, Preview schema drift |
| D-03 | Python ≥ **3.12**; prerequisite install; `tzdata` dependency; UTF-8 forcing on Windows |
| New (needs your decision) | PD-1 below: a setup command for OAuth sign-in |

## 6. Decisions (resolved 2026-10-22)
| ID | Decision | Outcome |
|---|---|---|
| **PD-1** | `gmail-login` fifth command | **Yes** (spec D-09, FR-10, §4.6). Built in M8; no-op on the `handoff` channel |
| **PD-2** | Token storage | **Protected file** under the user profile (spec D-10) |
| **PD-3** | Distribution | **`pipx` install** for v1 |
| **PD-4** | Draft path without Google setup | **"Do not send but create a Gmail draft."** Reconciled as: phase 1 `handoff` channel — real Gmail drafts created by the host session's connector from a handoff file (no Google Cloud setup); direct `google` channel is phase 2 (spec D-11). The export-only gap is therefore **not** accepted; the remaining difference is that actiontrack itself does not make the creating call in phase 1 |
| **PD-5** | Python floor | **3.12** (installed) |
| PD-6 (new) | Sample roster emails | `angad.kulkarni@coditas.com` and `pranay.pathak@coditas.com` recorded as samples (assigned to the two Priyas to exercise namesake paths; mapping to be confirmed; "…@coditas" normalised to ".com"; spelling "pathak" corrected by the user) |

**Still to confirm by you:** (a) the sample-email mapping and spellings; (b) that a Claude Code session with the Gmail connector connected is the intended host for phase 1. **Resolved in spec v0.5 (G-01):** (c) a prepared handoff exits 0 (`EXIT_HANDOFF_PREPARED`, one constant, so it can change) with `status: pending_handoff` in JSON and explicit "NOT yet created" wording; cancel is exit 9.

## 7. Prerequisites
| ID | Action | Needed for | Status |
|---|---|---|---|
| P-1 | Python 3.12+ | M1 onward | **Done** — Python 3.12.10 installed for the current user (`%LOCALAPPDATA%\Programs\Python\Python312`; `winget`, user scope). Verified: `sqlite3` 3.49.1, `tomllib` available. It is **not on the shell PATH of already-running sessions**; use the full path or open a new terminal |
| P-1b | `pipx`, `tzdata`, `pytest` via pip | M1, M9 | **Done** — now installed (the first attempt had failed on a PyPI read timeout) |
| P-1c | `build` (PyPA packaging front end, dev dependency for the wheel/sdist step of T-M9-06) via pip | M9 | **Still to install** via pip (not yet present) |
| P-2 | Google Cloud project; enable `gmail.googleapis.com` and `gmailmcp.googleapis.com` | M8 (phase 2) | **Deferred by user** for phase 1 |
| P-3 | Workspace Developer Preview enrollment (admin may be required) | M8 | Deferred |
| P-4 | OAuth client (*Web application*, "AI-powered agent", loopback redirect to verify) | M8 | Deferred |
| P-5 | OAuth consent screen / test users | M8 | Deferred |
| P-6 | Roster emails | `followup` | **Sample addresses supplied** (spec Appendix C); remaining people blank |
| P-7 | Host session with the Gmail connector connected (this Claude Code session qualifies) | M7 demo, phase-1 drafts | Available now |

## 8. Plan-level risks
| ID | Risk | L/I | Mitigation |
|---|---|---|---|
| PR-1 | Google Gmail MCP is Developer Preview: schema, scopes or availability may change | M/M | Startup `tools/list` guard; schema-tolerant parsing; phase-1 `handoff` channel remains the working path |
| PR-2 | Loopback redirect not accepted for the *Web application* client / AI-agent flow | M/H | Spike part 2 tests it first; fallback: copy-paste auth-code flow (as Antigravity does); else stay on the `handoff` channel |
| PR-3 | Admin/Preview enrollment delays | M/M | Product works without the `google` channel; M8 is parallel and non-blocking |
| PR-4 | Token with `gmail.compose` leaks and is used outside the app | L/H | ACL-restricted file; `--logout`; hard-coded host + tool allow-list; never log tokens |
| PR-5 | False success (HTTP 200 + `isError`) | H if naive | Success criteria §2.5; test with the observed payload |
| PR-6 | Windows quirks (console encoding, file locks, no tz database) | M/M | Force UTF-8; `tzdata`; DB lock → exit 4; CI on Windows |
| PR-7 | Sample is small/synthetic; real notes more varied | M/M | Defensive parser; warnings catalogue; fixtures grow from real cases |
| PR-8 | Scope creep (extra commands) | M/L | Only PD-1 proposed; everything else stays in spec NG list |

## 9. Traceability: spec → milestones
| Spec area | Milestones |
|---|---|
| FR-01, §8, §11 | M2, M5 |
| FR-02, FR-03 | M6 |
| FR-04, §12, GM-* | M7, M8 |
| FR-05, §10.1–10.5 | M4 |
| FR-06, §9 | M3 |
| FR-07, FR-08, §10.6–10.7 | M5 |
| FR-09, NFR, §13 | M1, M9 |

## 10. Out of this phase
The **Tasks** phase (granular task list, ordering within milestones, estimates per task) has **not** started and awaits approval of this plan.
