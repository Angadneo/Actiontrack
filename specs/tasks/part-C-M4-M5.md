## Milestone M4 — People & owners

Conventions used below: "sample" = `samples/notes/` plus `notes/notes/scratch.md` and `untitled.md` (read-only; tests use copies under `tests/fixtures/notes/`, which must hold all 36 files). `G-Cn` refers to a gap in "Part C — notes for the integrator" (status against spec v0.6 is listed there); `G-nn` / `H-nn` refer to the spec change log (Appendix E / E.2). Where a task needs a decision that the spec still does not make, it states the **default adopted** so work can proceed and the decision can be reversed in one place. New files that are not in plan section 2.3 are marked **(new)**; the integrator should approve or fold them into existing modules. This part was re-synced to **spec v0.6** (see the "Re-sync log" at the end).

### T-M4-01 — Implement name keys, folded keys, identity key and person slug
- **Size:** M
- **Depends on:** M1 skeleton (package, pytest wiring)
- **Parallel with:** T-M4-02, T-M4-03
- **Spec refs:** §10.2, §10.2a (incl. corner cases (a)–(d), G-30), §8.3, BR-06, AC-05.10, AC-05.6, AC-09.6, NFR-MAINT-02, NFR-TEST-01
- **Files:** `src/actiontrack/domain/names.py`, `tests/unit/test_names.py`
- **Work:**
  - `name_key(text)`: NFKC, `casefold()` (not `lower()`), curly quotes/dashes to ASCII, collapse whitespace, strip punctuation except internal `-` and `'`, drop leading honorifics `Dr/Mr/Ms/Mrs` with or without a trailing dot. Diacritics are kept (§10.2).
  - `fold_key(text)`: `name_key` then NFKD and removal of combining marks, used only by the diacritic rule (§10.3 step 4). Per §10.2a(c) it is applied **identically** to roster entries, attendee names and owner tokens (one function, three call sites); `identity_key` itself keeps diacritics. Document that non-decomposable letters (`ø`, `ß`, `đ`) are not folded in v1.
  - `split_name(text)` returns tokens; `first_token`, `last_token` (hyphenated surnames stay one token, `Nair-Rao`; middle tokens are display-only).
  - `classify_key_kind(text)` (G-30): returns `full` (≥2 tokens, last token longer than one letter), `initial_form` (`Priya N.` / `Priya N`: last token a single letter), `single_token` (`Zed`).
  - `identity_key(full_name, qualifier=None)` = `name_key(first) + " " + name_key(last)` plus the qualifier when present (§10.2a roster rule); returns **`None` for an `initial_form`** (it is not an identity, §10.2a(b)); for a `single_token` it returns the token alone and the caller must never match that against a known First+Last person (§10.2a(a)). This is the single function the M1 roster validator must call for the AC-05.12 duplicate check (coordinate with the M1 config task).
  - `provisional_key(text)` (G-30): single token gives the token; initial form gives `first + " " + initial` (e.g. `zed q`); full name gives its `identity_key`.
  - `person_slug(identity_key)` produces the `person_id` (`priya-nair`): lower-case NFC, spaces to `-`, no transliteration (so `李雷` and `Zoë Åström` survive, AC-09.6). Deterministic collision suffix `-2`, `-3` in order of first creation. Format of a qualifier slug: `priya-nair-sales` (default adopted, G-C6 residual).
  - All functions pure; no locale lookups.
- **Tests to write first (TDD):**
  - Table: `Priya Nair`, `priya nair`, `  PRIYA   Nair `, `Dr. Priya Nair`, `Priya R. Nair` all give identity key `priya nair` (AC-05.10); `Priya Nair-Rao` gives `priya nair-rao`; `O’Brien` (curly) equals `O'Brien`.
  - `José` vs `Jose`: `name_key` differ, `fold_key` equal (AC-05.6). `Zoë Åström` keeps diacritics in `name_key`, folds to `zoe astrom`; `李雷` unchanged and slug `李雷`.
  - Corner cases (G-30): `identity_key("Priya N.") is None`; `identity_key("Zed") == "zed"` and is flagged `single_token`; `provisional_key("Priya N.") == "priya n"`; `provisional_key("Zed") == "zed"`; `fold_key` of roster `José Alvarez`, attendee `Jose Alvarez` and owner `Jose Alvarez` are all equal while their `identity_key`s differ.
  - Slug collisions: two different identity keys that slugify identically get `-2` suffix deterministically.
  - Property: `name_key(name_key(x)) == name_key(x)`; NFC and NFD inputs give the same key.
- **Done when:** the table has at least 25 rows, all green; no function in the module touches I/O, clock or `locale`; `identity_key` is imported (not re-implemented) by the roster validator test for AC-05.12.

### T-M4-02 — Define owner/person domain models, rule tables and name-form classifier
- **Size:** M
- **Depends on:** T-M4-01
- **Parallel with:** T-M4-03 (interface agreed first)
- **Spec refs:** §10.1 (token grammar), §10.2a, §10.3 steps 1–3, §7.2 (`ItemOwner.provenance`, `Person`), BR-20, NFR-MAINT-02, AC-05.3, AC-05.5, AC-05.18
- **Files:** `src/actiontrack/domain/models.py`, `src/actiontrack/domain/rules.py` **(new; rule tables as data, NFR-MAINT-02)**, `src/actiontrack/domain/names.py`
- **Work:**
  - Dataclasses/enums: `OwnerStatus` (`resolved|provisional|ambiguous|unassigned`), `Provenance` exactly as spec §7.2 (G-26): `roster|alias|attendee|attendee_in_meeting|unique_first_name|initial_form|diacritic_fold|provisional|none` (the v0.4 workaround values `initial`/`provisional` are now spec values; `initial` is renamed `initial_form`), `NameMention(raw, span, role=owner|with)`, `OwnerPhrase(mentions, rule_id, clause_span, nonperson_raw, with_spans)`, `ResolvedOwner(ordinal, owner_raw, person_id, status, candidates, provenance, role, warnings)`. `Person` model has the §7.2 fields (`first_name`, `last_name`, `qualifier`, `identity_key`, `origin`, `verified`).
  - Tables in `rules.py`: non-person words (`Everyone, Someone, Team, TBD, Everybody, Anyone, Nobody, We, I, Me`, case-insensitive; **plus `All`**, because BR-20 lists "all" while the §10.1 set omits it: default adopted, G-C26), not-a-name words (weekdays, months, pronouns, `by/due/before/until`), owner verbs (`to, will, should, needs to, is going to, can, must`), honorifics, label words for trailing forms (`Owner, Assignee, DRI`).
  - `classify_name(raw) -> NameForm(kind=full|first_initial|first_only, first, last, initial, qualifier)`: `Priya N.` and `Priya N` (single-letter last token) are `first_initial`; `Priya Nair`, `Priya R. Nair` are `full`; one token is `first_only`; more than 4 tokens is rejected (not a name, §10.1). Consistent with `classify_key_kind` of T-M4-01 (single source of truth: this delegates to it).
  - `initial_matches(person, form)` and `first_name_key(person)` helpers used by the registry.
- **Tests to write first (TDD):**
  - `classify_name`: `Priya N.` (first_initial, initial `n`), `Priya S.` (sample `2026-09-29-billing-cutover-planning.md:9`), `Priya Nair` (full), `Priya` (first_only), `Priya R. Nair` (full, last=`nair`), `Zoë Åström` (full), five tokens (rejected).
  - Tables: every word in the non-person set maps to non-person case-insensitively (`everyone`, `TBD`, `Team`, `All`); `Friday`, `Oct`, `By` are not names.
  - The `Provenance` enum members equal the §7.2 list exactly (drift test against a constant copied from the spec).
- **Done when:** models import cleanly into M2/M5 code; rule tables are the only place the word lists live (grep check in test); classifier table green.

### T-M4-03 — Owner-phrase extraction I: clause location (rules 1–3 order), single owner, name tokens, non-person words
- **Size:** M
- **Depends on:** T-M4-02, M2 parse result model (cleaned description and item text)
- **Parallel with:** T-M4-01, T-M4-05
- **Spec refs:** §10.1 (rules 1–3 locate the clause, G-23), name token grammar, BR-20, FR-05.1, AC-05.5, AC-05.7, AC-05.9 (`@anita:`), AC-09.6, AC-01.19 (spans feed `task_text`), SC-05, SC-51
- **Files:** `src/actiontrack/parsing/owner_phrase.py` **(new; called from `parsing/items.py`)**, `tests/unit/test_owner_phrase.py`
- **Work:**
  - `extract_owner(text, is_known_name) -> OwnerPhrase | None`; `is_known_name(token)` is an injected predicate meaning "matches a known person in K by **any §10.3 step**" (registry lookup, including folded and initial forms) so caseless scripts (`李雷`) and known lower-case names qualify (§10.1 token definition).
  - Rules **1, 2, 3 are tried in order and decide where the owner clause is** (first match wins, G-23). Rule 1: leading `@name:` (any case, `@anita:`), or `Name:` / `Name -` / `Name –` — **the unprefixed form is an owner only if `Name` is a known person (`is_known_name`) or is `@`-prefixed**; otherwise it is ordinary text, so `Note:`, `Update:`, `Blocked -` never create a person, not even a provisional one (replaces the v0.4 not-a-name heuristic of G-C15). Rule 2: `Name to|will|should|needs to|is going to|can|must <verb>`, name = 1–4 tokens, each starting with an uppercase letter (or known, or `@`), letters in any script, `'`, `-`, `.` initials (`Priya N.`). This task locates the clause and the single/leading name; expansion inside the clause (rules 4–6) is T-M4-04, and rule 3 (trailing forms) is T-M4-04 as well.
  - Return the `raw` owner text and the character span of the whole clause (e.g. `Priya N. to`) so that `task_text` derivation (§8.2, §10.6) can delete it. The `task_text` builder itself lives in `parsing/items.py` (M2); this task only supplies spans and a test that deleting the span reproduces the expected `task_text` for the AC-01.19 examples.
  - Non-person words (BR-20): return `nonperson_raw="Everyone"` with status unassigned; text preserved. No owner phrase: `None` (caller sets `owner_raw=null`, status unassigned, warning `W-OWNER-MISSING`, §10.1). Default adopted for G-C15(iv) (spec still silent): non-person owners keep `owner_raw` and do **not** emit `W-OWNER-MISSING`.
  - Corollary of the known-or-`@` rule: `Everyone: please review` (non-person word followed by colon, not a known person) is **not** rule 1; it falls through to rules 2–3 and yields no owner (`owner_raw=null` + `W-OWNER-MISSING`). Documented, tested.
- **Tests to write first (TDD):**
  - Sample lines: `Priya to send the revised quote to Acme by Friday` (Priya); `Priya N. to send revised quote to Acme — by Friday` (`Priya N.`; the trailing `— by` is not an owner); `@anita: audit colour contrast on the dashboard by Mon` (Anita); `Meera to chase Orbit Labs by Friday` (Meera, not `Orbit Labs`); `Sam to cut cloud costs by 10% — proposal by 2026-10-09` (Sam); `Lena to pull Acme's usage report for the last 6 months by tomorrow` (Lena; `Acme's` never an owner).
  - Non-person: `Everyone to review the prototype before the next review`, `TBD to write the design-system changelog`, `Someone to update the CRM notes`, `Team to decide on the launch date`, `Everyone to submit their Q4 goals by 7 Oct` all return non-person with raw kept (AC-05.5).
  - No owner: `Send the quote by Friday` (spec SC-05) returns `None`.
  - Rule 1 gate (G-23): `Note: Priya will send the deck` is located by rule 2 (Priya), never rule 1; `Update: rollout moved` and `Blocked - waiting on legal` return `None` and create nothing; `Priya: send the deck` with `is_known_name("Priya")` true is an owner, with it false is `None`; `@zed: fix the build` is an owner even though Zed is unknown (provisional later).
  - Verb variants (synthetic): `Karan will send…`, `Priya should…`, `Rahul needs to…`, `Sam is going to…`, `Lena can…`, `Meera must…`.
  - Unicode: `李雷 to book the room` only with `is_known_name` true; `Zoë Åström to review` works by capitalisation.
  - Spans: slicing the span from each sample line reproduces exactly the clause text; deleting it and applying the M2 `task_text` rules gives `Send the revised quote` for AC-01.19's first example.
- **Done when:** all sample owner lines of the 35 files (see T-M4-14 table) extract the expected leading mention; no line in the sample yields a mention other than the leading name; spans verified.

### T-M4-04 — Owner-phrase extraction II: expansion inside the clause (multi-owner, `(with X)`, possessives) and trailing forms
- **Size:** M
- **Depends on:** T-M4-03
- **Parallel with:** T-M4-05
- **Spec refs:** §10.1 rules 3–6 (G-23), BR-19, AC-05.4, AC-05.9, AC-01.19, SC-51, OQ-16
- **Files:** `src/actiontrack/parsing/owner_phrase.py`, `tests/unit/test_owner_phrase.py`
- **Work:**
  - **Rules 4–6 work inside the clause that rules 1–3 located** (spec v0.6 states this explicitly; the v0.4 "treat as extensions of rule 2" workaround of G-C15(i) is now just the spec). Multi-owner lists before the verb joined by `and`, `&`, `,`, `/` (`Anita & Rahul to…`, `Priya and Rahul to…`): one `OwnerPhrase` with several `NameMention(role=owner)` in document order. Default for the unstated case: the same expansion also applies to the name list before the colon of a rule-1 clause (`@anita & rahul:`).
  - `Name (with Other) to …`: `Name` owner, `Other` role `with` (stored as co-owner, BR-19); a `with` person is an owner for follow-up purposes (OQ-16). The `(with Other)` span is returned separately (`with_spans`) so `task_text` removes the co-owner phrase and no co-owner name leaks into emails (AC-01.19, G-04).
  - Possessives: a name followed by `'s`/`’s` is never an owner mention anywhere (`Rahul to review Anita's PR`: only Rahul).
  - Rule 3 trailing forms, only when rules 1–2 found nothing: trailing `(Name)`, `— Name` / `– Name`, `[owner: Name]`, `Owner:|Assignee:|DRI: Name`. Weekday/month/pronoun words never qualify (`— Friday`, `(Sept)`). A trailing `Name` follows the same token grammar (uppercase or known).
  - Ordering contract: leading forms beat trailing forms (first match wins); a due phrase after an em dash (`— by Friday`) must not be mistaken for a trailing owner.
  - Cap at the 4-token name length; at most 8 mentions per phrase (defensive, warn above).
- **Tests to write first (TDD):**
  - `Anita & Rahul to agree on the component API by Wednesday` (`2026-09-18-design-review.md:12`) yields owners Anita, Rahul; `Priya and Rahul to ship` (AC-05.4) yields two; `Anita, Rahul and Sam to…` yields three; `Anita/Rahul to…` yields two. Regression for the spec's own remark: `Rahul to` is **not** matched separately inside `Anita & Rahul to …`.
  - `Lena (with Priya) to collect user feedback on the prototype by 2026-09-25` (line 13) yields Lena owner, Priya `with`; `with_spans` deleted gives `task_text` `Collect user feedback on the prototype` (AC-01.19: `Lena (with Priya) to update the plan` gives `Update the plan`).
  - `Rahul to review Anita's PR by tomorrow` (`2026-10-07-standup.md:9`) yields Rahul only.
  - Trailing (synthetic): `Draft onboarding doc (Priya)`, `Draft doc — Priya Nair`, `Draft doc [owner: Priya Nair]`, `Draft doc Owner: Priya`, `Draft doc DRI: Rahul`, `Draft doc — Friday` (none).
  - Precedence: `Priya to review Anita's PR (Rahul)` yields Priya only.
- **Done when:** AC-05.4, AC-05.9 extraction halves pass; every `&`/`(with …)`/possessive line in the sample extracts as in SC-51 and `2026-10-07-standup.md:9`.

### T-M4-05 — Build the in-memory people registry (roster, attendees, provisional, namesake groups, provisional merge)
- **Size:** M
- **Depends on:** T-M4-01, T-M4-02, M1 roster config model
- **Parallel with:** T-M4-03, T-M4-04
- **Spec refs:** §10.2a (incl. (a)–(d)), §10.3 (known people K, display name), FR-05.3, BR-06, BR-23, AC-05.10, AC-05.11, AC-05.12 (support), SC-52, A-15
- **Files:** `src/actiontrack/resolution/people.py`, `tests/unit/test_people_registry.py`
- **Work:**
  - `PeopleRegistry` holds `Person(id, first_name, last_name, display_name, name_key, identity_key, first_key, last_key, qualifier, email, origin, verified)` and indexes: identity key, alias key (one alias key may map to several people), first-name key, (first key, last initial), folded variants of each. Index lookups return **sorted** lists (BR-18). Single-token and initial-form provisional people are indexed by `provisional_key` and are **never** returned by the identity-key index for a known First+Last person (§10.2a(a)/(b)).
  - Mutators: `add_roster(entries)` (emails come only from the roster, FR-05.3), `add_attendee(person_ref)`, `add_provisional(provisional_key)` (identical `name_key` shares one provisional person, §10.3 step 5), `add_alias(person_id, alias, auto=False)`.
  - `merge_provisional(provisional_id, into_person_id)` (§10.2a(d), G-30): the provisional person's references are repointed, the provisional Person is removed from K, and its name key is **kept as a `PersonAlias`** (`auto=True`). Default adopted (new gap G-C28, because §10.2a(d) read literally contradicts BR-06): an auto-alias whose key is a bare first name or an initial form is recorded but is **not consulted by step 1** while the key is shared by ≥2 people (it falls to steps 2–3), so a later namesake cannot be hidden behind an auto-alias; auto-aliases made of a full-name key resolve directly as the spec says.
  - `namesake_groups()`: first-name key to people with that first name (≥2). `is_namesake(person_id)` drives full-name display (§10.2a rule 1).
  - Display name rule (§10.3 last paragraph): roster `display_name` (derived as `First Last [qualifier]` if roster lacks one, G-C19) else the most complete attendee spelling else the first-seen raw spelling, ties by ordinal order; recomputable from stored spellings so it is order-independent.
  - `snapshot()` / `restore(snapshot)` so M5 can roll back registry changes when a per-file transaction fails (see T-M4-07).
- **Tests to write first (TDD):**
  - Load the Appendix C roster (9 people): namesake groups are exactly `{priya: [priya-nair, priya-shah], rahul: [rahul-mehta, rahul-verma]}`; `is_namesake` false for Anita Desai.
  - `add_attendee` for `Priya Nair`, `Priya N. `… and `Priya R. Nair` never creates a second person (AC-05.10, SC-52).
  - Provisional `Zed Zed` added twice returns the same id; roster entry never mutated by attendee spellings; provisional `Zed` (single token) and `Zed Q.` (initial form) are keyed `zed` / `zed q`, never found by `identity_key("Zed Zed")`.
  - `merge_provisional` of provisional `Priya N.` into `priya-nair`: provisional gone from K, alias `priya n` kept and resolving to Nair; provisional `Priya` merged into a lone Priya then a second Priya added: bare `Priya` is **not** pinned by the auto-alias (G-C28 default).
  - Display name: roster wins; without roster the longer attendee spelling `Priya Nair` beats `Priya N.`; equal-length ties break by ordinal.
  - Snapshot/restore round trip leaves indexes identical.
- **Done when:** registry state is fully derivable from (roster, attendee rows, provisional rows, alias rows); iteration order never depends on insertion order of dicts; snapshot/restore test green.

### T-M4-06 — Process meeting attendee names (incomplete-name rule, steps 1, 2, 4 before creation)
- **Size:** S
- **Depends on:** T-M4-05, M2 attendee parsing (raw names + `email_hint`)
- **Parallel with:** T-M4-08
- **Spec refs:** §10.2a (attendee lists with incomplete names), §10.3 step 4 (attendees pass steps 1, 2, 4), §8.1 attendees, AC-05.13, SC-55, `W-ATTENDEE-NAME-INCOMPLETE` (now in Appendix B, G-20)
- **Files:** `src/actiontrack/resolution/people.py`, `src/actiontrack/resolution/owners.py`, `tests/unit/test_attendees.py`
- **Work:**
  - `resolve_attendees(raw_names, registry) -> list[MeetingAttendeeRef]` with `person_id` nullable. Order per attendee: step 1 (identity/alias), step 2 (`First L.` initial form), step 4 (diacritic variant) **before** creating an attendee-origin Person (now stated by §10.3 step 4; G-C5 resolved).
  - First-name-only attendee (`Priya`): if that first name is carried by ≥2 known people, do **not** attribute it to anyone, `person_id=null`, emit `W-ATTENDEE-NAME-INCOMPLETE` (file, line, name). If exactly one known person has that first name, accept as that person (§10.2a last sentence: `unique_first_name`). If nobody known has it, create an attendee-origin person with a single-token key (G-30(a)); it is never an identity of a known First+Last person.
  - New attendee persons: `origin=attendee`, `verified=false` (default adopted, G-C19 residual); roster persons `verified=true`.
  - `email_hint` is stored on `MeetingAttendee` only; never copied to `Person.email` (BR-13).
  - Return the **set of person ids present** for the meeting (the `A` set of §10.3 step 3a), excluding null entries. The same function is re-used by the re-resolution pass (T-M4-11) to rebuild `A` from stored raw names (default adopted, new gap G-C20).
- **Tests to write first (TDD):**
  - `2026-09-17-billing-migration-sync.md` attendees give `A` = {karan-joshi, sam-okafor, rahul-verma, priya-shah, priya-nair}.
  - `Attendees: Priya, Rahul` with the Appendix C roster: two `W-ATTENDEE-NAME-INCOMPLETE`, both `person_id` null, `A` empty (AC-05.13, SC-55).
  - `Attendees: Anita, Sam` with roster: both accepted as the unique people.
  - `Attendees: Priya N., Rahul V.` resolves via initials to nair/verma.
  - Roster `José Alvarez` with attendee `Jose Alvarez`: resolves via step 4 to the roster person, no second Person (G-30, §10.3 step 4).
  - Attendee `Priya Nair <angad.kulkarni@coditas.com>`: `email_hint` kept, `Person.email` unchanged.
- **Done when:** AC-05.13 test green; `W-ATTENDEE-NAME-INCOMPLETE` is present in the T-M1-02 warning catalogue (it is now in Appendix B; drift test).

### T-M4-07 — Persist people and make the registry transactional with per-file ingest
- **Size:** M
- **Depends on:** T-M4-05, T-M4-06, M1 DB schema (Person, PersonAlias, MeetingAttendee per spec §7.2)
- **Parallel with:** T-M4-08
- **Spec refs:** §7.2 Person/PersonAlias/MeetingAttendee (G-11), FR-05.3, NFR-REL-01, BR-18, AC-05.10, AC-09.6
- **Files:** `src/actiontrack/store/repo.py`, `src/actiontrack/resolution/people.py`, `tests/integration/test_people_persistence.py`
- **Work:**
  - `repo.upsert_roster(conn, roster)` at the start of every non-dry ingest: insert/update Person (`origin=roster`, email from roster, `first_name`, `last_name`, `qualifier`, `identity_key`) and PersonAlias rows; a roster person that disappears from config is kept (history), flagged by absence only.
  - `repo.load_registry(conn, roster) -> PeopleRegistry` builds K = roster ∪ attendees of any ingested meeting ∪ provisional people from the DB, so a new run sees earlier runs' people.
  - Per-file flow contract used by M5: `registry.snapshot()` before the file transaction; on rollback `registry.restore()`; on commit nothing. Provide a test helper that raises mid-file.
  - The §7.2 person columns now exist in the spec (G-11); this task only **verifies** that migration 0001 (T-M1-10) matches (schema drift test), it no longer asks M1 for additions.
  - Unicode names stored NFC and returned unchanged (AC-09.6).
  - `repo.merge_person(conn, provisional_id, into_id)` primitive (repoint `ItemOwner`/`MeetingAttendee`, insert alias, delete provisional row); the policy that calls it is T-M4-15.
- **Tests to write first (TDD):**
  - Ingest-like sequence with a forced exception after attendees were added: DB has no new Person rows and registry equals the snapshot.
  - Run the same attendee list twice: same person ids, one row each (AC-05.10).
  - Roster email changed between runs: `Person.email` updated, `person_id` stable.
  - `Zoë Åström` attendee round-trips through the DB byte-identically.
  - `merge_person`: afterwards no row references the provisional id, the Person row is gone, the alias row exists.
- **Done when:** registry rebuilt from an empty in-memory object plus the DB equals the live registry after a run (equality test); rollback test green.

### T-M4-08 — Resolution algorithm steps 1–2: exact full name, alias, first name + last initial
- **Size:** S
- **Depends on:** T-M4-05, T-M4-02
- **Parallel with:** T-M4-06, T-M4-07
- **Spec refs:** §10.3 steps 1–2, §10.2a rules 2 and 4 and corner case (b), AC-05.3, AC-05.10, AC-05.18, SC-08, SC-52
- **Files:** `src/actiontrack/resolution/owners.py`, `tests/unit/test_resolve_steps12.py`
- **Work:**
  - `resolve_owner(mention, meeting_ctx, registry, cfg) -> ResolvedOwner` skeleton with step functions returning a result or `None` (fall through). `meeting_ctx` carries the attendee set `A` or `None` when the note has no attendee list (`W-ATTENDEES-MISSING`).
  - Step 1: identity key or roster alias key equals a known person gives `resolved` with provenance `roster`/`alias`/`attendee` according to how the person entered K; alias mapping to ≥2 people gives `ambiguous` with those candidates. A roster alias that equals a bare first name (`"Priya"` mapped to one person) is a step-1 hit (AC-05.17 relies on it). Single-token owner text is never matched against identity keys of known First+Last people (§10.2a(a)); auto-aliases follow the T-M4-05 default.
  - Step 2: first name plus last initial: exactly one match gives `resolved` to that full-name person (provenance **`initial_form`**, G-26); ≥2 gives `ambiguous`; none falls through. An initial form has no identity key and creates no identity (§10.2a(b)). Attendance is **not** consulted in steps 1–2 (`Priya N.` is an identity, not first-name-only).
  - Never return a bare first name as an identity; `ResolvedOwner.candidates` is a sorted tuple of ids.
- **Tests to write first (TDD):**
  - `Priya N.` resolves to priya-nair at `2026-09-08-quote-portal-weekly.md:9` and `2026-09-29-billing-cutover-planning.md:10` with provenance `initial_form` (AC-05.18); `Priya S.` to priya-shah (`…cutover-planning.md:9`); `Priya Nair`, `priya nair`, `@priya nair` all to priya-nair (provenance `roster`), zero provisional rows (AC-05.10).
  - Synthetic: roster with Priya Nair and Priya Narang: `Priya N.` is ambiguous with both candidates; roster alias `"Priya (Sales)"` mapped to two people is ambiguous; alias to one person resolves with provenance `alias`; roster alias `"Priya"` to Priya Shah only resolves bare `Priya` to Shah with provenance `alias`.
  - Attendance does not change step 1–2 outcomes: `Priya N.` in the 09-17 meeting (both Priyas attend) still resolves to Nair.
- **Done when:** table green; function signature stable for T-M4-09/10.

### T-M4-09 — Resolution step 3: first name only, attendance within namesake groups, global rule, config switch
- **Size:** M
- **Depends on:** T-M4-08, T-M4-06
- **Parallel with:** T-M4-12
- **Spec refs:** §10.3 steps 3, 3a, 3b, §10.4 (last bullet), BR-06, BR-23, AC-05.1, AC-05.2, AC-05.8, AC-05.18, SC-44, SC-45, SC-46, SC-48, SC-50, SC-55, D-08, R-15
- **Files:** `src/actiontrack/resolution/owners.py`, `tests/unit/test_resolve_step3.py`
- **Work:**
  - `C` = known people with the first-name key. **3a applies only when `|C| >= 2`** (§10.3 as amended by G-26; when `|C| = 1` go straight to 3b): `A` = members of `C` in the meeting's attendee set. `|A|=1` resolved (provenance `attendee_in_meeting`), `|A|>=2` ambiguous (candidates `A`), `|A|=0` falls to 3b. 3b: no attendee list, or `|A|=0`, or `|C|=1`: `|C|=1` resolved (provenance `unique_first_name`, **even if the note has an attendee list**), `|C|>=2` ambiguous, `|C|=0` falls to step 4/5.
  - `cfg.attendee_disambiguation=false`: skip 3a entirely, so every shared first name is ambiguous (AC-05.8). Attendance never resolves when `A` is empty because the note had no attendees or all attendees were incomplete names.
  - Provenance is now fixed by the spec (G-C4 resolved, no constant to flip): `attendee_in_meeting` only inside namesake groups; the "resolved via attendance" figure (T-M5-14/15) counts exactly the rows with that provenance.
  - Pure function: depends only on (mention, `A`, registry snapshot, cfg), so the same inputs give the same output regardless of run order. It is the function re-invoked by the whole-database re-resolution pass (T-M4-11).
- **Tests to write first (TDD)** (roster = Appendix C; `A` from the note's attendee line):
  - Ambiguous (both attend): `2026-09-17-billing-migration-sync.md:9` `Priya to sign off the cutover checklist` (candidates nair+shah, AC-05.1, SC-44); `2026-10-08-billing-go-no-go.md:9` `Priya to circulate the go/no-go summary by EOD`; `2026-09-30-q4-offsite.md:16` Priya and `:17` Rahul (all four attend, SC-48); `2026-10-07-standup.md:9` Rahul (Mehta and Verma both attend).
  - Ambiguous (no attendee list): `2026-09-23-standup.md:8` `Rahul to rebase the feature branch by Thursday` (mehta+verma, SC-46); `untitled.md:2` `Priya to send the slides by Friday`.
  - Resolved by attendance (`attendee_in_meeting`): `2026-09-01-quote-portal-kickoff.md:9` Priya to nair (SC-45, AC-05.2); `2026-09-03-billing-migration-sync.md:11` Priya to shah; `2026-09-17-billing-migration-sync.md:12` Rahul to verma (only Verma attends); `2026-09-18-design-review.md:12` `Anita & Rahul` Rahul to mehta (AC-05.9); `…:13` `Lena (with Priya)` Priya to nair.
  - **Provenance (AC-05.18):** `Sam to …` in a note that has an attendee list (only one known Sam) is `unique_first_name`, **not** `attendee_in_meeting`; same for Anita/Meera/Karan/Lena first-name-only owners in noted meetings. Over the sample no non-Priya/non-Rahul row has provenance `attendee_in_meeting`.
  - Unique first name without attendees: `untitled.md:1` `Sam to chase the vendor` (`unique_first_name`, SC-50); `2026-09-23-standup.md:9` `Sam to rotate the staging credentials`.
  - Namesake present but nobody of that name attended (synthetic: `Priya to …` in a meeting whose attendees are Anita and Sam): `|A|=0` falls to 3b, `|C|=2`: ambiguous.
  - Config off: the `2026-09-01` Priya line becomes ambiguous (AC-05.8).
  - No-roster mode (attendees only, files fed chronologically): same results as above for every sample line; also a synthetic file with a first name carried by nobody known falls through (to step 4/5).
  - `A` empty because attendees were all incomplete (`Attendees: Priya, Rahul`, SC-55, AC-05.13): the owner stays ambiguous.
- **Done when:** every row above green; across the whole sample exactly 7 items are ambiguous and 36 first-name owners have provenance `attendee_in_meeting` (counts hand-derived; verified by T-M4-14).

### T-M4-10 — Resolution steps 4–5: diacritic variant and provisional people
- **Size:** S
- **Depends on:** T-M4-09
- **Parallel with:** T-M4-11
- **Spec refs:** §10.3 steps 4–5, §10.2a(a)–(c), AC-05.6, AC-05.7, W-NAME-DIACRITIC, §10.2 folded key, G-30
- **Files:** `src/actiontrack/resolution/owners.py`, `src/actiontrack/resolution/people.py`, `tests/unit/test_resolve_step45.py`
- **Work:**
  - Step 4 (now specified, G-C5 resolved): steps 1–3 are re-run on the **folded** keys (identity key, initial form and first-name key all compared as `fold_key`). Exactly one person matched gives `resolved`, provenance `diacritic_fold`, warning `W-NAME-DIACRITIC` (file, line, both spellings); ≥2 gives `ambiguous`. Attendance applies to the folded first-name case the same way as 3a (including the `|C|>=2` condition).
  - Step 5: no match: create or reuse a provisional Person (`origin=note_owner`, `verified=false`), `owner_status=provisional`, provenance `provisional`; identical `name_key` share one person. Key per G-30: single-token provisional key = the token; an unmatched initial form (`Zed Q.`) is keyed `first + initial`; a full unmatched name is keyed by its identity key. Provisional people are in K for later files and matchable by `list` (§10.5).
  - Reported under "unresolved owners (unverified)" (data only; rendering in T-M5-15/16).
- **Tests to write first (TDD):**
  - Roster `José Alvarez` only: `Jose to send the invoice` resolves with `W-NAME-DIACRITIC`; roster `José Alvarez` and `Jose Alvarez` both known: ambiguous, no resolution (AC-05.6 "only if unique").
  - `Zed to fix the build` with nobody named Zed: provisional person `zed`; second occurrence in another file reuses the same person id; `ResolvedOwner.status=provisional`, provenance `provisional`. `Zed Q. to …`: provisional keyed `zed q`.
  - A diacritic variant never creates a provisional person when a unique fold match exists.
- **Done when:** the three behaviours green; no provisional rows are created anywhere in the sample (checked in T-M4-14).

### T-M4-11 — Whole-database re-resolution pass (§10.3 step 6, §10.7.9a) — pure function
- **Size:** M
- **Depends on:** T-M4-09, T-M4-10, T-M4-06
- **Parallel with:** T-M4-12
- **Spec refs:** §10.3 step 6, §10.4 (resolution path), §10.7.9(a), W-OWNER-NOW-AMBIGUOUS, R-12, AC-05.2 (last sentence), AC-05.8, AC-05.17, AC-05.19 (no-undo contract), W-6, G-08
- **Files:** `src/actiontrack/resolution/owners.py`, `tests/unit/test_reresolution.py`
- **Work:**
  - `re_resolve(snapshot, registry, cfg) -> ReResolutionResult` where `snapshot` is the stored data of the **whole database** (all folder roots): every `ItemOwner`/per-source owner phrase (`owner_raw`, `role`, current status/provenance/person) and, per meeting, the stored attendee **raw names**. Pure: no DB access, no clock. This is mandatory on **every** ingest regardless of file hashes (G-08, D-16); there is no "switch" and no config fingerprint any more (the v0.4 G-C2 workaround is withdrawn).
  - Step A (rebuild context): recompute each meeting's attendee set `A` from the stored raw names against the **current** K using T-M4-06's function (default adopted, new gap G-C20: the spec re-evaluates "each owner phrase with its meeting's stored attendee list" but does not say whether the list is re-resolved; without it a later namesake could never turn an attendee into an incomplete name).
  - Step B (re-evaluate): every stored owner is re-run through steps 1–5 against the current roster, aliases, known people and `attendee_disambiguation`. Outcomes: (i) provisional person whose key is a first-name/initial form of **exactly one** known person: aliased/merged (`OwnerChange(kind=merge_provisional)`, event `owner_changed`); (ii) `unique_first_name` owner whose first name now has ≥2 candidates: `ambiguous` + `W-OWNER-NOW-AMBIGUOUS`; (iii) `ambiguous` owner now pinned to one person (new alias, edited roster entry, `attendee_disambiguation` switched on): `resolved`; (iv) the reverse (`attendee_disambiguation=false`, alias removed, a namesake added): `ambiguous`; every changed owner emits `owner_changed` and every resolved-to-ambiguous transition also `W-OWNER-NOW-AMBIGUOUS` (AC-05.8 requires the warning for the config switch).
  - **No-undo contract (H-05):** the result never contains any change to sources, merges, links, completion or status; `OwnerChange` records only owner fields plus a flag `merged_or_completed_under_earlier_resolution` computed by the caller (T-M4-15) from the item's sources/links. Manual-done items: their owners are re-resolved like any other (status untouched). Item descriptions, due and sources are never altered.
  - Must be idempotent: a second pass with no new input yields zero changes; deterministic ordering of changes (by item id, ordinal).
- **Tests to write first (TDD):**
  - No roster; file A (no attendee list) `Priya to draft the spec` with only Priya Nair known: `unique_first_name`; then file B with Priya Shah attending: after the pass the item is ambiguous, `W-OWNER-NOW-AMBIGUOUS` emitted once, no source/description fields in the result (AC-05.2).
  - Provisional `Priya N.` created before `Priya Nair` is known: after the pass it is merged into priya-nair with an `owner_changed` change.
  - W-6 / AC-05.17 (pure): ambiguous `Priya` (meeting without attendees), registry gains alias `"Priya"` → Priya Shah, **no file input at all**: result has one change, `resolved`, provenance `alias`; alias removed: back to `ambiguous` (+ warning). Variant: alias `"Priya (Sales)" -> Priya Shah` with a note edited to `Priya (Sales) to…`.
  - AC-05.8 (pure): items with provenance `attendee_in_meeting` and `cfg.attendee_disambiguation=false` all become `ambiguous` (count equals the number of such rows); switching back restores them.
  - A attendee `Priya` (stored raw name) resolved while one Priya was known becomes incomplete (null) after a second Priya joins; the dependent owner becomes ambiguous (G-C20 default).
  - Second pass produces no changes; manual-done item's owner is re-resolved but the result carries no status change.
- **Done when:** all behaviours above green; pass duration on 10k owner rows stays under 1 s (NFR-PERF share); the module contains no import of `store`.

### T-M4-12 — Ambiguity model and surfacing hooks (display names, candidate groups, include-ambiguous predicate)
- **Size:** M
- **Depends on:** T-M4-05, T-M4-09
- **Parallel with:** T-M4-11, T-M4-13
- **Spec refs:** §10.2a rule 1, §10.4, BR-06, BR-14, BR-23, FR-02.9, FR-02.6, AC-05.11, AC-05.14, AC-05.15, AC-05.16, AC-02.11, SC-53
- **Files:** `src/actiontrack/resolution/owners.py`, `src/actiontrack/domain/models.py`, `tests/unit/test_ambiguity_hooks.py`
- **Work:**
  - `candidate_key(candidates)`: canonical sorted tuple used for grouping and DB storage.
  - `owner_display(owner, registry)`: resolved namesake gives full name; resolved non-namesake gives display name; ambiguous gives `?Priya (Nair | Shah)` (bare first name plus candidate last names; candidates that differ in first name are shown as full names); provisional/unassigned gives the raw text with the `OU` marker data. Returns structured `(text, flags)` so M6 renders without re-deriving rules (no business rules in CLI, NFR-MAINT-01).
  - `group_ambiguous(items, status="open")`: only items with `owner_status=ambiguous`, filtered by status exactly like `list --ambiguous` (default **open**, `all` includes done; G-19, AC-02.11), grouped by candidate set, groups sorted by candidate full names, items by id (FR-02.9).
  - `owner_matches_person(item_owners, person_id, include_ambiguous) -> bool`: resolved owner equals person, or `include_ambiguous` and the person is in `candidates`; ambiguous items never match when the flag is off (BR-14).
  - `candidate_lines(candidates, registry)`: `Full Name <email>` lines, sorted, for exit-6 messages (AC-04.6) and report rows.
  - Hook data for the ingest report: one record per ambiguous owner `(item_id, file:line, raw, candidate full names)`, **open items only** (the report section is open-scoped, §4.2); the done-item case is covered by the `status="all"` variant used by `list`.
- **Tests to write first (TDD):**
  - Priya Nair item shows `Priya Nair` (never `Priya`); ambiguous shows `?Priya (Nair | Shah)` and `?Rahul (Mehta | Verma)` (AC-05.11).
  - Fixture of the 7 sample ambiguous items (09-17, 09-23, 09-30 Priya, 09-30 Rahul, 10-07, 10-08, untitled): `group_ambiguous(..., "all")` returns the groups (Priya Nair|Priya Shah has 4, Rahul Mehta|Rahul Verma has 3) and, with the default `open` filter, a done ambiguous fixture item is excluded; the default count equals the report-hook count (AC-05.14, SC-53).
  - `owner_matches_person` over mixed fixture rows: with the flag, Nair's resolved items plus ambiguous items whose candidates include Nair; excludes Shah's resolved items and ambiguous items of other candidate sets (AC-05.15).
  - Two items with identical text owned by Priya Nair and Priya Shah stay distinct person ids (AC-05.16).
- **Done when:** M6/M7 can call these helpers with no further business logic; grep shows no `?Priya`-style formatting outside this module.

### T-M4-13 — Person query resolution for `list --owner` and `followup`
- **Size:** M
- **Depends on:** T-M4-05, T-M4-07
- **Parallel with:** T-M4-11, T-M4-12
- **Spec refs:** §10.5, FR-02.6, FR-04.1, BR-06, AC-02.6, AC-04.6, SC-07, SC-54, exit codes 3 and 6
- **Files:** `src/actiontrack/resolution/people.py`, `tests/unit/test_person_query.py`
- **Work:**
  - `resolve_person_query(query, registry) -> Person`, raising `PersonNotFound(query, near=[...])` (maps to exit 3) or `PersonAmbiguous(query, candidates)` (exit 6); CLI mapping is M6/M7 work.
  - Order (§10.5): person id, email (case-insensitive), exact identity key or alias key (so `Priya N.` works when the roster lists that alias, as Appendix C does, and `priya nair` works). Without the alias an initial-form query is **not** matched by §10.5 (it lists only full name/alias); default adopted: not found with near matches. First-name-only query: exactly one known person with that first name (no namesake) chosen; ≥2 raises ambiguous carrying full name + email for every candidate, with the hint "re-run with the full name".
  - 0 matches: up to 3 near matches by Levenshtein distance ≤2 over identity keys and aliases, sorted by (distance, identity key); never auto-used.
  - Provisional people are matchable (for `list`); expose `person.verified` and `person.email` so `followup` can add the recipient precondition (FR-04.7).
  - Implement edit distance in-module (no dependency), deterministic.
- **Tests to write first (TDD):**
  - `Priya` raises ambiguous listing `Priya Nair <angad.kulkarni@coditas.com>` and `Priya Shah <pranay.pathak@coditas.com>` (AC-02.6, AC-04.6); `Rahul` lists both with blank email shown as `(no email)`.
  - `priya-nair`, `Priya Nair`, `ANGAD.KULKARNI@CODITAS.COM` (email matched case-insensitively); `Priya N.` alias resolved; `Anita` unique first name accepted.
  - `Priyaa Nair` gives not-found with near match `Priya Nair`; `Zzz` gives not-found with no near matches.
  - Provisional person `Zed` is matched by `zed`.
- **Done when:** exception types documented in the module docstring and consumed by a contract test; table green.

### T-M4-14 — M4 acceptance suite grounded in the 35 sample files
- **Size:** M
- **Depends on:** T-M4-03, T-M4-04, T-M4-06, T-M4-09, T-M4-10, T-M4-11, T-M4-12, M2 parse result model
- **Parallel with:** -
- **Spec refs:** AC-05.1–05.11, 05.13–05.16, 05.18, SC-05, SC-07, SC-08, SC-30, SC-44–46, SC-48, SC-50–53, SC-55, Appendix A.3 (AC-05.17 and 05.19 are owned by T-M4-15/T-M5-21)
- **Files:** `tests/fixtures/notes/` (copy of sample), `tests/golden/expected_owners.csv`, `tests/integration/test_owners_sample.py`
- **Work:**
  - Build `expected_owners.csv` with one row per checkbox source (117 rows): file, line, owner raw, status, person ids, provenance (spec §7.2 enum), role. Generate the first draft by running the pure owner pipeline on M2 output, then hand-review every non-trivial row against Appendix A.3 (do not rubber-stamp).
  - The test feeds files in FR-01.1 order into registry + resolver (no DB) and compares to the CSV.
  - Add synthetic fixtures for what the sample cannot cover: provisional people (single token and initial form), diacritics, qualifier roster entries, aliases to two people, label-like `Note:`/`Update:` prefixes (G-23), no-roster order dependence, `attendee_disambiguation=false`, Unicode (`Zoë Åström`, `李雷`).
- **Tests to write first (TDD):**
  - Aggregate assertions on the sample: 7 ambiguous (listed in AC-05.14), 5 unassigned non-person (`Someone` 09-10, `Everyone` and `TBD` 09-18, `Team` 10-01, `Everyone` 09-30 offsite), 0 provisional, 0 `W-NAME-DIACRITIC`, provenance distribution: `attendee_in_meeting` rows are exactly 36 and all belong to Priya/Rahul items (AC-05.18), every `Sam/Anita/Meera/Karan/Lena` first-name-only row is `unique_first_name`, `Priya N./S./Rahul M./V.` rows are `initial_form`, exactly one Person row per roster person after all files.
  - `2026-09-18-design-review.md`: `@anita:` to Anita Desai; `Anita & Rahul` to Anita Desai + Rahul Mehta; `Lena (with Priya)` to Lena Fischer owner + Priya Nair `with`; `Everyone`/`TBD` unassigned (SC-51).
  - `2026-10-07-standup.md:9`: owner list is exactly the (ambiguous) Rahul; Anita is not an owner.
  - `2026-10-03-sales-pipeline.md` and `untitled.md` (null meeting date) resolve owners independent of date.
  - No label text in the sample (`Note:`, `Update:` etc.) produces an owner; Unassigned/ambiguous counts are asserted both over all items and over open items (the report/`list` scope, AC-05.14): the test records which of the 12 are done, if any, and the expectation is updated only after review.
- **Done when:** suite green; the CSV is committed and cited by T-M5-20; running with no roster gives identical results for all sample rows except `Person.email`.

### T-M4-15 — Persist re-resolution outcomes: owner repointing, provisional merge, attendee refresh, canonical-owner rebuild (new)
- **Size:** M
- **Depends on:** T-M4-11, T-M4-07, T-M5-07, T-M5-22
- **Parallel with:** T-M5-12
- **Spec refs:** §10.3 step 6, §10.2a(d), §10.7.9(a), AC-05.17 (persistence half), AC-05.19 (data half), AC-05.10, H-05, G-08, G-30, W-OWNER-NOW-AMBIGUOUS
- **Files:** `src/actiontrack/store/repo.py`, `src/actiontrack/resolution/owners.py`, `tests/integration/test_reresolution_db.py`
- **Work:**
  - `load_owner_snapshot(conn)` (whole DB, all folder roots, explicit ORDER BY) for T-M4-11 and `apply_owner_changes(conn, changes, run_id, clock)`: updates the per-source owner phrases (`ItemSource` owner columns) and then asks `recompute_canonical` (T-M5-22) to rebuild the canonical `ItemOwner` rows from the latest present source; refreshes `MeetingAttendee.person_id` from the re-evaluated attendee sets; writes `owner_changed` events (`before`/`after` JSON) and the `W-OWNER-NOW-AMBIGUOUS` warnings (file, line from the first source; dedupe per item per run).
  - Provisional merge uses `repo.merge_person` (T-M4-07): rows repointed, provisional Person deleted, key kept as `PersonAlias` (G-30(d)); delete only when no other reference remains.
  - **H-05 guard:** the function must not write `ItemSource`, `ItemLink`, `ActionItem.status/completion_*`; a guard test compares a canonical dump of those tables before and after a downgrade. It also computes, for each downgraded item, the flag `merged_or_completed_under_earlier_resolution` (item has ≥2 sources attached by a `merged` event, holds Tier-1b evidence, or has a `completed_by` link) and hands it to the report (T-M5-15).
  - `updated_at` follows the normal `touch_item` rule (owner is a business field, so it changes only when the owner really changes).
- **Tests to write first (TDD):**
  - AC-05.17 at DB level: ingest a fixture (meeting without attendees, two known Priyas) so `Priya` is ambiguous; add roster alias `"Priya"` → one person; run only the re-resolution step: owner row `resolved`, provenance `alias`, one `owner_changed` event, `ItemSource` rows byte-identical; remove the alias: `ambiguous` again, event + warning.
  - Provisional `Priya N.` merged: no provisional Person row remains, alias row exists, `ItemOwner` repointed, `MeetingAttendee` repointed (AC-05.10).
  - H-05: Tier-1-merged item (two `unique_first_name` sources) downgraded by a new namesake: sources, status, links unchanged; owner `ambiguous`; flag set. Same for a Tier-1b-completed item (stays `done`).
  - Running twice: second run writes nothing (idempotent).
- **Done when:** tables above green; `grep` shows `repo.py` has no function that deletes `ItemSource` or `ItemLink` rows.

---

## Milestone M5 — Ingest service

Conventions: the ingest orchestrator is `src/actiontrack/services/ingest.py`; it consumes M2 `ParsedFile`/`ParsedItem` (with warnings and `E-*` outcomes, `description` and `task_text`), M3 date results (with phrase spans), and M4 resolver/registry. Per-file work is a single DB transaction; derived state is recomputed by idempotent functions so a crash at any point is repaired by the next run. **Every `ingest` ends with the whole-database passes of §10.7.9** (owner re-resolution; `W-STATUS-CONFLICT` check) even when every file was `unchanged`. **AC ownership for the v0.5/v0.6 additions:** AC-05.17 → T-M4-15 + T-M5-21 (end to end); AC-05.18 → T-M4-08/T-M4-09 (+ count in T-M5-14); AC-05.19 → T-M4-15 + T-M5-21; AC-07.15 → T-M5-22; AC-07.16 → T-M5-11; AC-08.9 → T-M5-04 (+ T-M5-19 real-process kill); AC-08.10 → T-M5-13 (no-recursive half) + T-M5-12 (restore half).

### T-M5-01 — Repository layer for ingest entities and item-ID allocation
- **Size:** M
- **Depends on:** M1 DB schema (migration 0001 per spec §7.2), M1 db connection/transaction helper
- **Parallel with:** T-M5-03, T-M5-06
- **Spec refs:** §7.2 (G-11), BR-11, BR-08, AC-08.8, AC-07.9, NFR-REL-01, NFR-DET-01
- **Files:** `src/actiontrack/store/repo.py`, `src/actiontrack/store/schema.sql`, `src/actiontrack/store/migrations/`, `tests/integration/test_repo.py`
- **Work:**
  - Typed functions for SourceFile, Meeting, MeetingAttendee, ActionItem, ItemOwner, ItemSource, ItemLink, ItemEvent, IngestRun, IngestWarning: insert/update/query, always with explicit `ORDER BY`.
  - `ActionItem.id` uses `INTEGER PRIMARY KEY AUTOINCREMENT` so ids are never reused even after rows were hypothetically removed (BR-11, AC-08.8). `format_item_id(n)` gives `AT-0004` (zero-pad to 4, grows beyond) and `parse_item_id` accepts `AT-12`/`12`/`at-12`.
  - **Link storage per §7.2:** a `possible_duplicate` link is symmetric and stored **once with `from_item_id < to_item_id`** (the v0.4 "new to existing" direction is withdrawn); `links_of(item_id)` reads both ends. `completed_by` is **directional**: `from` = item whose completion was inferred, `to` = evidence holder; it is never reordered. Unique on `(from_item_id, to_item_id, kind)` with `INSERT OR IGNORE`.
  - The schema additions this milestone needs are now spec text (G-11, so G-C1 is resolved): `ItemSource` per-source parse columns (`description`, `task_text`, `core_key`, `due_raw`, `due_raw_other`, `due_date`, `due_confidence`, `due_rule`, `due_reason`, owner phrases), `ActionItem.task_text`/`due_raw_other`, `ItemOwner.role`, `IngestRun` counters and `partial`. This task **verifies** migration 0001 against §7.2 (drift test) and records two Plan decisions: per-source owner phrases are stored as a JSON column `owners_json` (default adopted); and a nullable `ActionItem.completion_evidence_source_id` is added (default adopted, new gap G-C21: §10.6 says the evidence "is recorded" but §7.2 has no field; flag to M1). No `config_fingerprint` columns exist (the v0.4 workaround is withdrawn).
  - No business logic here (NFR-MAINT-01); no function commits (the caller owns the transaction).
- **Tests to write first (TDD):**
  - Insert 3 items, delete the last row by hand, insert a fourth: id is 4 not 3 (AC-08.8 mechanism).
  - Duplicate `insert_link` twice leaves one row; a `possible_duplicate` inserted as (9, 3) is stored as (3, 9) and `links_of` returns the pair from either side; a `completed_by` (9 -> 3) stays (9, 3).
  - Rolling back a transaction leaves no rows in any table; foreign keys enforced (insert source for missing item fails).
  - `parse_item_id("at-12") == 12`; `format_item_id(12) == "AT-0012"`; `format_item_id(123456) == "AT-123456"`.
  - Schema drift: every column named in §7.2 for the entities above exists (test reads a constant list copied from the spec).
- **Done when:** all repo functions covered; migrations apply on an empty DB and are re-runnable; no function calls `commit`.

### T-M5-02 — Item event writer and `updated_at` discipline
- **Size:** S
- **Depends on:** T-M5-01
- **Parallel with:** T-M5-03, T-M5-06
- **Spec refs:** §7.2 ItemEvent, FR-03.5, BR-08, BR-15, BR-18 (`ACTIONTRACK_NOW`), §10.6 merge effect, §10.7.2
- **Files:** `src/actiontrack/store/repo.py`, `src/actiontrack/services/ingest.py`, `tests/unit/test_events.py`
- **Work:**
  - `record_event(conn, item_id, kind, before, after, run_id, ts)` for `created, source_added, source_removed, merged, updated_from_source, status_changed, owner_changed, manual_done` (exactly the §7.2 enum; reopening is a `status_changed` event); `before/after` are canonical JSON (sorted keys). `ts` comes from the injected `Clock` (so `ACTIONTRACK_NOW` makes stored timestamps reproducible, AC-09.4).
  - `touch_item(conn, item_id, changes)` is the **only** place that updates business columns; it sets `updated_at` only when a business field actually changed (description, `task_text`, owner, due fields incl. `due_raw_other`, status, completion fields, `source_state`), so a no-op re-ingest leaves rows byte-identical (BR-15, AC-08.1).
  - Events are idempotent per `(item_id, kind, run_id, payload hash)` so a retried file after a crash cannot double-log.
- **Tests to write first (TDD):**
  - `touch_item` with identical values: no UPDATE issued, `updated_at` unchanged, no event.
  - A real change writes exactly one event with before/after JSON and bumps `updated_at` to the injected clock; with a fixed `ACTIONTRACK_NOW` two runs give identical timestamps.
  - Replaying the same events inside a second transaction (simulated retry) adds none.
- **Done when:** a grep test confirms no module other than `repo.py` writes `action_item` business columns.

### T-M5-03 — Chronological file ordering and the file plan
- **Size:** S
- **Depends on:** M2 discovery, M3 date resolver (meeting date)
- **Parallel with:** T-M5-01, T-M5-06
- **Spec refs:** FR-01.1, AC-01.1, BR-18, NFR-DET-01
- **Files:** `src/actiontrack/services/ingest.py`, `tests/unit/test_ordering.py`
- **Work:**
  - `order_files(parsed_files) -> list` sorted by (meeting date ascending, null last; then folder-relative path, ordinal Unicode code-point comparison, `/` separators). No `locale`, no `str.lower()`.
  - Failed/empty files carry no meeting date and sort with the null group but are never persisted as items; the plan lists them separately for reporting.
  - Parse-all-then-order: the plan is built after every file has been read and parsed (failures isolated first, FR-01.8).
- **Tests to write first (TDD):**
  - AC-01.1: `a.md`, `B.MD` sort `B.MD` before `a.md` (ordinal), `c.txt` excluded (dot-prefixed `.draft.md` is skipped by M2 discovery, not here).
  - Sample order check: `2026-09-30-q4-offsite.md` precedes `2026-09-30-standup.md` (same date, path order); `2026-10-03-sales-pipeline.md` (date field invalid, null) and `untitled.md` are last, in that order, after `2026-10-14-acme-renewal-wrapup.md`.
  - Shuffle the input list 20 times with a fixed seed: identical output.
- **Done when:** ordering is a pure function with the above table and property test green.

### T-M5-04 — Run lifecycle, process lock and interrupted-run detection (owns AC-08.9)
- **Size:** M
- **Depends on:** T-M5-01, M1 clock, M1 db connection, **T-M1-11 (`store/lock.py`, consumed, not re-implemented)**
- **Parallel with:** T-M5-05
- **Spec refs:** §10.7.6, §10.7.7, G-14, W-5, NFR-REL-02, AC-08.5 (previous run reported `interrupted`), AC-08.9, SC-67, exit code 4 (DB locked)
- **Files:** `src/actiontrack/services/ingest.py`, `src/actiontrack/store/db.py`, `tests/integration/test_run_lifecycle.py`, `tests/integration/test_ingest_lock.py`
- **Work:**
  - Start (non-dry): take the **process-level lock `<db>.lock`** (OS file lock from `store/lock.py`, auto-released on process death, a stale lock file is harmless) for the whole ingest; a second writer (`ingest`, and by the same helper `done`, `followup` row writes, `--record-draft` in M6/M7) that cannot take it exits 4 "another actiontrack process is using this database" without touching any row. `--dry-run` and read-only commands do **not** take the lock (§10.7.7).
  - Then create `IngestRun(status=in_progress, started_at, folder_root, dry_run=0)` in its own committed transaction. folder_root is the canonical absolute path (G-C12 residual).
  - Interruption: any earlier run still `in_progress` is set to `interrupted` and reported ("previous run #N was interrupted at file …") **only after the lock was acquired**, i.e. no live process owns the DB; an `in_progress` run whose owner is alive is a concurrent run and is never marked (§10.7.6). Per-file transactions release SQLite's lock between files, so SQLite locking alone cannot give this (G-C13 lock half resolved by G-14).
  - Map `sqlite3.OperationalError: database is locked/busy` (busy timeout default 2 s, config-free constant) to a typed `DbLocked` error for exit 4.
  - Finish (in T-M5-14's final transaction): `status=complete` if no file failed, `partial` if ≥1 file failed (now spec: §4.2, §7.2, G-25), `finished_at`, counters.
- **Tests to write first (TDD):**
  - AC-08.9 / SC-67 (two real processes): process A holds the lock mid-ingest (hook blocks after file k); process B `ingest` exits 4 and the DB file bytes are unchanged; process C simulating `done` via the shared helper also exits 4.
  - Kill process A (`os._exit` / terminate): lock released by the OS; the next `ingest` acquires it, marks A's `in_progress` run `interrupted`, reports it, and completes; A's already-committed files are `unchanged`.
  - A live `in_progress` run is never marked `interrupted` by a concurrent process (B fails to lock; run row still `in_progress`).
  - Insert an `in_progress` run row with no lock held; start a run: old row becomes `interrupted`, the new report carries the notice.
  - Lock released on normal and exceptional exit (context manager); stale lock file after a hard kill is not mistaken for a live holder; `--dry-run` runs fine while another process holds the lock.
- **Done when:** lifecycle tests green on Windows; documented in the module that `done` takes only a short immediate transaction; AC-08.9 test names reference the criterion.

### T-M5-05 — Per-file transaction engine: read/parse failures, hash skip, empty/failed classification
- **Size:** M
- **Depends on:** T-M5-01, T-M5-02, T-M5-03, T-M5-04, M2 reader + parser (E-codes)
- **Parallel with:** T-M5-06
- **Spec refs:** FR-01.8, BR-17, NFR-REL-01, §10.7.1, §10.7.8, §11, §4.2 report definitions (Files line), AC-01.8, AC-01.11, AC-08.1, AC-08.7, AC-09.5, G-28, G-C11, G-C18
- **Files:** `src/actiontrack/services/ingest.py`, `src/actiontrack/store/repo.py`, `tests/integration/test_file_engine.py`
- **Work:**
  - For each planned file: classify without opening a transaction when possible. Compare SHA-256 with the last **committed** ingest (`SourceFile.content_sha256`): equal and previous status `ok` gives `unchanged` (no row touched; skipping a file never skips the whole-database passes of §10.7.9, which T-M5-14 always runs). `empty` and `failed` are **re-classified every run** and are never "unchanged" (§4.2): `discovered = ingested + unchanged + empty + failed` is asserted as an invariant on every report.
  - Config/roster changes need no per-file fingerprint any more (the whole-DB re-resolution handles owners, G-08). Residual (new gap G-C27): a change of `date_order`, `dup_window_days` or `fuzzy_threshold` does not alter any file hash and is therefore **not** re-applied to unchanged files; default adopted: documented limitation (re-ingest into an empty DB to re-derive), no fingerprint.
  - Open one transaction per changed/new file: upsert SourceFile and Meeting, process attendees (T-M4-06/07), items (T-M5-07 onward), warnings, and as the **last statement** set `content_sha256`, `size_bytes`, `mtime`, `last_ingested_run`. A crash before commit leaves the old hash, so the file is retried (no partial state, NFR-REL-01).
  - Exceptions inside a file transaction: roll back (including `registry.restore()`), record `SourceFile.status=failed` and an `E-*` code in a tiny follow-up transaction, never touch that file's existing items (§10.7.8). Classification default for persistence/other exceptions: `E-INTERNAL-PARSE` (G-C18 still open: §11 defines it for parser exceptions only); `sqlite3.OperationalError` locked/corrupt aborts the whole run with exit 4.
  - Source files are opened read-only only (BR-01); record mtime/size from `stat`, never `utime`.
- **Tests to write first (TDD):**
  - Ingest twice on a 3-file temp folder: second run all `unchanged`, DB business tables identical via dump diff (AC-08.1).
  - AC-08.1 / SC-25 on the sample counts: folder of 3 readable + 1 empty + 1 binary: second report is `unchanged 3 + empty 1 + failed 1 = discovered 5`, exit 5 both times, business tables identical (the full 33+1+1 sample is asserted in T-M5-20).
  - Folder with 5 good files and 1 binary `.md` (copy of `scratch.md`): 5 ingested, failure `E-BINARY`, DB has 0 items for the bad file, run continues (AC-01.8).
  - File ingested OK then replaced by a binary of the same name: items retained, `SourceFile.status=failed`, items not `source_state=missing` (AC-08.7, §10.7.8).
  - Forced exception in file 2 of 3: file 2 leaves zero rows, files 1 and 3 committed.
  - Stat of source files (sha256, size, mtime) identical before and after the run (AC-01.11).
  - File over `max_file_bytes=1024` reports `E-TOO-LARGE`, others unaffected (AC-09.5).
- **Done when:** the tests above green; no code path writes while processing an `unchanged` file.

### T-M5-06 — Description core (from `task_text`), token splitting and Jaccard (pure matching primitives)
- **Size:** M
- **Depends on:** M2 parse result model (`description`, `task_text`), M3 date locator (phrase spans incl. non-due), T-M4-03/04 (owner and co-owner spans, via the M2 `task_text` builder), M1 skeleton
- **Parallel with:** T-M5-01, T-M5-02
- **Spec refs:** §10.6 (description core), §6.1, §8.2 (`description` vs `task_text`), §8.3 (token splitting), G-12, NFR-MAINT-02, NFR-TEST-01, AC-01.19, AC-07.1–07.10, AC-07.12, AC-07.13
- **Files:** `src/actiontrack/domain/matching.py`, `src/actiontrack/domain/rules.py`, `tests/unit/test_matching_core.py`
- **Work:**
  - `core_key(task_text) -> CoreResult(tokens, key)`: the core is built from the item's **`task_text`** (the owner phrase, `with`-co-owner phrase, due phrases, non-due date phrases such as `from 9 Oct`, and orphaned separators are already removed by the M2 `task_text` builder using M3/M4 spans; this task does not re-locate spans). Apply §8.3 comparison normalisation then **token splitting exactly as §8.3 defines it (G-12; replaces the v0.4 default of G-C10)**: apostrophes deleted (`don't`→`dont`, `Anita's`→`anitas`); split on whitespace **and on any character that is not a Unicode letter or digit**; empty tokens dropped (so `go/no-go`→`go no go`, `10%`→`10`, `API-review`→`api review`, `welcome-email`→`welcome email`). Stop-words (`the a an to and of for please`) are removed afterwards from the comparison tokens only; `key` = tokens joined by one space.
  - If `task_text` fell back to `description` (it would have been empty, §8.2), the core is computed from `description` (documented edge).
  - `has_recurrence_word(tokens)` from the rule table (`weekly daily monthly every each recurring standing regular`).
  - `jaccard(a, b)` on token **sets**; returns 0 for two empty sets.
- **Tests to write first (TDD)** (expected `key` values; each input shown as the full line, `task_text` produced by the M2 builder):
  - `Priya to send the revised quote to Acme by Friday` (`2026-09-01-quote-portal-kickoff.md:9`), `Priya N. to send revised quote to Acme — by Friday` (`2026-09-08-quote-portal-weekly.md:9`), `Priya to send the revised quote to Acme` (`2026-09-15-quote-portal-weekly.md:9`) all give `send revised quote acme` (4 tokens).
  - `Karan to freeze invoice edits from 9 Oct` (`2026-09-29-billing-cutover-planning.md:12` and `2026-10-08-billing-go-no-go.md:11`) gives `freeze invoice edits` (exactly 3 tokens; AC-07.12 depends on `from 9 Oct` being removed from `task_text`, §8.3's own example).
  - `Anita to share first wireframes by Thursday` vs `Anita to share updated wireframes by Thursday`: Jaccard 0.5; `…updated wireframes…` pair across 09-15/09-22: identical keys.
  - `Meera to ask Acme for their procurement contact` vs `Meera to send Acme the procurement form`: Jaccard under 0.3 (non-match).
  - `Lena to collect 5 customer quotes for the landing page by end of month` and `…by 2 Oct`: identical keys (due phrases removed regardless of form).
  - `Sam to cut cloud costs by 10% — proposal by 2026-10-09`: `10%` is not a due phrase; only `by 2026-10-09` is removed from `task_text`; tokens `cut cloud costs by 10 proposal` (`10%`→`10`, AC-06.7 trap).
  - Token splitting table straight from §8.3: `don't`→`dont`, `Anita's CTO`→`anitas cto`, `go/no-go`→`go no go`, `10%`→`10`, `API-review`→`api review`, `welcome-email copy`→`welcome email copy` (3 tokens).
  - Recurrence: `Send weekly status report` flagged (AC-07.7); `Review pricing proposal` vs `Review pricing model` Jaccard < 0.85 (AC-07.10).
  - No-owner item (task_text = whole description minus due) removes nothing else.
- **Done when:** the key table covers all 18 clusters of Appendix A.4 (one assertion per cluster member); functions are pure and import no DB code; the token-splitting table is the single place the rule is encoded (grep check).

### T-M5-07 — Persist a newly seen file: meeting, attendees, items, owners, source anchors (no matching yet)
- **Size:** L
- **Depends on:** T-M5-01, T-M5-02, T-M5-05, T-M5-06, T-M4-07, T-M4-09, T-M4-10, M2 parse result model, M3 date resolver
- **Parallel with:** -
- **Spec refs:** FR-01.4, FR-01.6, FR-01.9, FR-01.12, BR-04, BR-10, BR-19, BR-20, BR-21, BR-11, §7.2, §8.1 (future-date warning in the ingest service), §10.1, AC-01.2, AC-01.3, AC-01.9, AC-01.18 (persistence half), AC-01.19, AC-05.4, AC-05.5, AC-05.7, AC-06.6, AC-09.6, SC-01, SC-02, SC-05, SC-06, SC-10, SC-63, SC-64, SC-69
- **Files:** `src/actiontrack/services/ingest.py`, `src/actiontrack/store/repo.py`, `tests/integration/test_persist_file.py`
- **Work:**
  - Map `ParsedFile` to rows: `Meeting` (date, `date_source`, title), `MeetingAttendee` (raw name, `person_id` nullable, `email_hint`), warnings to `IngestWarning` (code, line, message, run). `W-MEETING-DATE-FUTURE` is **computed here** (meeting date > today in the configured zone, injected clock) by calling the M2 helper `is_future(meeting_date, today)` (§8.1; parsers stay pure).
  - For each `ParsedItem` in document order: resolve owners via M4 (all mentions; `ordinal`, `role` owner/with), store `owner_raw`, status, `candidates`, provenance (§7.2 enum); due fields from M3 (`due_raw`, `due_date`, `due_confidence`, `due_rule`, `due_reason`, `due_raw_other`) and their warnings (`W-DUE-BEFORE-MEETING`, `W-DATE-NOT-DUE`); no-owner gives `unassigned` with `owner_raw=null` and `W-OWNER-MISSING`; non-person gives unassigned with raw kept.
  - **Text fields (G-12, FR-01.12, AC-01.19):** store `description` (full cleaned, NFC, owner/due phrases kept) **and** `task_text` on both `ActionItem` and the per-source columns; `ItemSource.raw_text` is **verbatim and not NFC-normalised**; `block_hash` = SHA-256 over the **verbatim** `raw_text` (LF-joined for multi-line) (the v0.4 "NFC-normalised" hash is withdrawn, §7.2).
  - `ItemSource` anchoring: `line_start/line_end`, `raw_text`, `section_heading`, `checkbox_state`, `occurrence_index` = 0-based index among sources in that file with equal `block_hash`, `present=1`, `first_seen_run=last_seen_run=run`, plus the per-source parse columns of §7.2 (`description`, `task_text`, `core_key`, due fields, owner phrases JSON).
  - New `ActionItem` creation is wired as a **callable stage** `create_item(...)` used when the matcher (T-M5-08..10) finds nothing: `description`/`task_text` = those of the earliest present source (here the only source; canonical selection later by T-M5-22), `description_core_key`, `status` from checkbox (BR-10; `[x]` gives done, origin `note`, `completed_at` null, completion evidence = that source), `source_state=present`, event `created`, ids allocated by AUTOINCREMENT.
  - Until the matcher exists, this task's tests call `create_item` for every item (matching disabled via a flag).
- **Tests to write first (TDD):**
  - `2026-09-01-quote-portal-kickoff.md` in isolation: 4 items; item 1 owner Priya Nair (`attendee_in_meeting`), due 2026-09-04 relative, `ItemSource` line 9, section `Action items`, 4 `MeetingAttendee` rows (AC-01.2).
  - AC-01.19: `- [ ] Priya to send the revised quote by Friday.` stores `description` = `Priya to send the revised quote by Friday.` and `task_text` = `Send the revised quote`; `Lena (with Priya) to update the plan` stores `task_text` = `Update the plan`; a line containing NFD text stores `raw_text` byte-for-byte while `description` is NFC and `block_hash` hashes the verbatim bytes.
  - `2026-09-08-quote-portal-weekly.md:12` `[x] Rahul to set up the staging environment`: status done, origin note, `completed_at` NULL (AC-01.3).
  - `2026-10-03-sales-pipeline.md`: meeting date NULL, `W-MEETING-DATE-INVALID` stored (and no `W-MEETING-DATE-MISSING`), `Meera … by Friday` due unresolved reason `no_meeting_date` (SC-49, AC-06.7); `untitled.md`: both items imported, `W-MEETING-DATE-MISSING` + `W-ATTENDEES-MISSING`, `Priya` ambiguous (SC-50, AC-01.9). SC-10 pair (`by tomorrow` unresolved / `by 2026-11-05` explicit).
  - `W-MEETING-DATE-FUTURE`: note dated after the injected today emits it from the service, none for a past date.
  - SC-63/SC-64 persistence: a note dated 2026-10-12 with `by 9 Oct` stores 2026-10-09 with `W-DUE-BEFORE-MEETING`; a note dated 2026-12-20 with `by Jan 6` stores 2027-01-06; `Meera to demo the portal to Acme on 2026-10-14` stores `explicit`.
  - SC-69 / AC-01.18 persistence half: a folder with `.draft.md`, `.hidden/x.md`, a note with unclosed `<!--`, `- [ ]` without text and an unreadable subdirectory persists only the normal files, stores `W-HTML-COMMENT-UNCLOSED`, `W-CHECKBOX-EMPTY`, `W-DIR-UNREADABLE`, exit stays 0 (checked with T-M5-18).
  - `2026-09-18-design-review.md`: line 12 gives 2 `ItemOwner` rows; line 13 gives owner Lena + `with` Priya Nair; `Everyone`/`TBD` unassigned with `owner_raw` kept (AC-05.4, AC-05.5).
  - Item with no owner phrase (synthetic `- [ ] Send the quote by Friday`): `unassigned`, `owner_raw` NULL, `W-OWNER-MISSING`, stored (SC-05, AC-05.7).
  - Two identical lines in one file: `occurrence_index` 0 and 1 (precondition for AC-08).
  - Unicode owner `Zoë Åström` round-trips unchanged (SC-30).
- **Done when:** with matching disabled, the 33 readable sample files persist 117 `ItemSource` rows and 117 `ActionItem` rows (AC-01.14 counts: 104 open, 13 done) with zero provisional people.

### T-M5-22 — Canonical field selection for multi-source items (owns AC-07.15) (new)
- **Size:** M
- **Depends on:** T-M5-07, T-M5-08
- **Parallel with:** T-M5-09, T-M5-10
- **Spec refs:** §10.6.1 (last two rows), §10.7.3, §7.2 (ActionItem note, ItemSource per-source columns), BR-09, G-05, D-15, AC-07.15, AC-08.3, AC-08.10
- **Files:** `src/actiontrack/domain/canonical.py` **(new; pure)**, `src/actiontrack/store/repo.py`, `src/actiontrack/services/ingest.py`, `tests/unit/test_canonical.py`, `tests/integration/test_canonical_db.py`
- **Work:**
  - Pure `canonical_fields(item, sources) -> CanonicalFields`: (1) `description` and `task_text` = those of the **earliest present source**; (2) canonical `due_*` (`due_raw`, `due_date`, `due_confidence`, `due_rule`, `due_reason`, `due_raw_other`), **owners** (the ordered owner phrases with role/person/status/provenance) and the note-derived **status** follow the **latest present source**; (3) ordering is one ascending key `(meeting_date, null last; folder-relative path, ordinal; line_start)`, so earliest = first and latest = last (a source with an unknown meeting date therefore sorts as the latest; default adopted for the unstated null case, new gap G-C25); (4) a source with `present=false` is ignored while any present source exists; when no source is present the same rule is applied over all sources (item is `SM`, nothing else changes); (5) a Tier-1b/manual completion is not altered by (2): status is left to the §10.7.3 rules of T-M5-11.
  - `recompute_canonical(conn, item_id, run_id)`: called after every merge, pairing update, source removal/restoration and re-resolution (T-M4-15); writes through `touch_item` so `updated_at` moves only if something changed; events `updated_from_source` for description/due changes and `owner_changed` for owner changes (before/after).
  - Rebuilds the canonical `ItemOwner` rows from the owner phrases of the latest present source (ordinal and role preserved).
- **Tests to write first (TDD):**
  - AC-07.15 exactly: merged item with sources dated 2026-09-29 (due none) and 2026-10-08 (due 2026-10-09): canonical due, owners and note-derived status are those of the 10-08 source, `description` is that of the 09-29 source; marking the 10-08 source `present=false` makes the canonical fields follow 09-29; restoring it switches back.
  - Same-date sources order by path, then line (ordinal comparison, `B.md` before `a.md`).
  - Sample: `Karan to freeze invoice edits from 9 Oct` merged item: description from `2026-09-29-billing-cutover-planning.md:12`, due none, owner Karan Joshi; editing only the 10-08 line's wording changes **no** `description` but updates that source's own columns (replaces the v0.4 "only the canonical source drives" default).
  - All sources absent: fields keep following the ordering over all sources; `source_state=missing` set by T-M5-13.
  - Idempotent: recompute twice, second writes nothing.
- **Done when:** AC-07.15 test green; `canonical.py` is the only place that selects fields from sources (grep); `status` handling defers to T-M5-11.

### T-M5-08 — Tier-1 auto-merge and merge effects (§10.6, §10.6.1)
- **Size:** M
- **Depends on:** T-M5-06, T-M5-07, T-M5-02, T-M5-22
- **Parallel with:** -
- **Spec refs:** §10.6 Tier-1 conditions 1–6, §10.6.1 table, FR-07.1, FR-07.2, BR-07, BR-08, AC-07.1, AC-07.5, AC-07.8, AC-07.9, AC-07.12, AC-05.16, AC-05.19 (merge survives later downgrade), SC-03, SC-47 (precondition)
- **Files:** `src/actiontrack/domain/matching.py`, `src/actiontrack/services/ingest.py`, `tests/unit/test_tier1.py`, `tests/integration/test_tier1_merge.py`
- **Work:**
  - Pure `is_tier1(new_source, existing_item, cfg)`: all six conditions on the `task_text` core: identical core token sequence; ≥3 tokens and no recurrence word; every owner on both sides `resolved` and **person-id sets identical** (`role=with` co-owners included, default adopted; the spec says "every owner" but does not mention `with`, G-C8 residual); due compatible (equal resolved `due_date`, or both `due_confidence=none`; a pair where either side is `unresolved` is **not** compatible unless the dates are equal, default adopted, G-C22); status compatible (both open, both done, existing open + new done); new source from a **different file than every existing source** (present or not) and meeting date within `dup_window_days` of the existing item's **latest source** meeting date, absolute difference, 60 inclusive (default adopted); unknown date on either side fails the rule.
  - Candidate lookup by `core_key` index (not a table scan); several candidates: choose the lowest item id (deterministic).
  - Merge effect: attach `ItemSource` to the existing item, event `merged` + `source_added`; then `recompute_canonical` (T-M5-22: description earliest present, due/owner/status latest present). Existing open + new `[x]` with same due or both undated: item becomes done, origin `note`, `completed_at` null, event `status_changed`, and the new source is recorded as the completion evidence (§10.6.1 row 5, needed by the reopen rule, T-M5-11). When the new `[x]` source is **not** the latest source (late-arriving older note), the explicit completion of row 5 still applies and the evidence is that source (default adopted, G-C25).
  - Never delete or rewrite any source row (BR-08); count outcome `merged` (Tier-1) for the report. **A merge is never undone** by a later owner downgrade (H-05): there is no split code path (checked in T-M5-21).
- **Tests to write first (TDD):**
  - SC-03/AC-07.1: two synthetic files `2026-10-14-a.md`, `2026-10-15-b.md` same owner/due, both open: one item, two sources, event `merged`.
  - Sample: `Karan to freeze invoice edits from 9 Oct` in `2026-09-29-billing-cutover-planning.md:12` and `2026-10-08-billing-go-no-go.md:11` merge into one item with two sources (AC-07.12); the first line of cluster is source 1.
  - Negative table (each must NOT be Tier-1): due differs; one due null; owner differs; owner ambiguous on either side (AC-07.8); same file; 59 vs 61 days apart (boundary 60 inclusive); meeting date null; core under 3 tokens; recurrence word (`Send weekly status report`, AC-07.7 precondition); existing done + new open.
  - AC-07.5 (synthetic): existing open `Rahul to set up staging by 2026-09-08` and a later `[x] Rahul to set up staging by 2026-09-08` in another file (same due) merge into one item that becomes done (origin `note`); ItemSource count equals sources ingested (AC-07.9). The sample's dateless `[x]` variant is Tier-1b, not Tier-1.
  - AC-05.16: Priya Nair and Priya Shah with identical text in two files are not merged.
- **Done when:** all negatives are assertion-by-assertion; merge never reduces `ItemSource` row count (checked after every test).

### T-M5-09 — Possible-duplicate links and non-matches (§10.6 otherwise-clause, spec precedence)
- **Size:** M
- **Depends on:** T-M5-08
- **Parallel with:** T-M5-10 (shared tests for precedence)
- **Spec refs:** §10.6 possible-duplicate rules, precedence sentence and non-matches, §10.6.1, §7.2 ItemLink, FR-07.2, BR-07, G-27, AC-07.2, AC-07.3, AC-07.4, AC-07.6, AC-07.7, AC-07.10, AC-07.13, AC-05.16, SC-04, SC-31, SC-32, SC-39
- **Files:** `src/actiontrack/domain/matching.py`, `src/actiontrack/services/ingest.py`, `tests/unit/test_links.py`, `tests/integration/test_links_sample.py`
- **Work:**
  - `classify_pair(new_source, existing_item, cfg) -> Link|None` runs after Tier-1 and Tier-1b fail. Identical core: exactly **one** stored reason by the **spec precedence (G-27; replaces the v0.4 default of G-C9):** `owner_differs` > `status_differs` > `due_differs` > `same_file` > `recurrence` > `outside_window` > `fuzzy`; plus `owner_unresolved` "when an owner on either side is not `resolved` and no higher reason applies"; an **unknown meeting date counts as `outside_window`**. Interpretation adopted for the one unstated point (new gap G-C22): `owner_differs` requires both sides' owners `resolved` with different person sets; `owner_unresolved` ranks **below** all the listed reasons except that it replaces `fuzzy` (it is the last-resort reason). `status_differs` = existing done + new open. `due_differs` includes one-null-one-set and `unresolved` vs a resolved date.
  - Not identical: `fuzzy` iff Jaccard ≥ `fuzzy_threshold` **and** the same resolved owner set; Jaccard-only or different-owner similar text yields no link (§10.6 non-matches).
  - Per pair one link row stored **once with `from_item_id < to_item_id`** (T-M5-01); idempotent insert; links never alter items; both items reported (`D?` on both ends).
  - Candidate pruning: index by `core_key` for identical-core and by owner person-id bucket (plus the unresolved bucket) for fuzzy, to keep the 100 files / 10 MiB budget (NFR-PERF-01).
  - Core shorter than 3 tokens with identical core falls to the same reason precedence (documented; it cannot reach Tier-1).
  - AC-05.16 vs AC-07.3 is settled by the spec (G-27): identical text for two different resolved people is **linked `owner_differs`** and never merged.
- **Tests to write first (TDD):**
  - Sample: `Anita to share updated wireframes by Thursday` (`2026-09-15-quote-portal-weekly.md:10`, due 2026-09-17) and `2026-09-22-quote-portal-weekly.md:9` (due 2026-09-24): two open items + `due_differs`; `Anita to share first wireframes` (09-01) linked to neither (AC-07.13).
  - Sample due-differs pairs (A.4): pagination (09-15 none / 09-22 Wed), schema mapping (09-03 Mon / 09-17 none), SSO (09-14 none / 09-24 Mon), one-pager (09-28 Fri / 10-14 none), customer quotes (09-14 end of month / 09-24 2 Oct): 5 links, reason `due_differs` (plus wireframes: 6 outside Tier-1b clusters).
  - Non-links: `usage report` pull vs share (09-10 vs 09-28), `ask Acme for procurement contact` vs `send procurement form` (AC-07.10 analogue).
  - Synthetic reasons: same-file duplicates (AC-07.6, `same_file`), recurrence (AC-07.7), owner differs Rahul/Priya (AC-07.3, SC-39), existing done + new open (AC-07.4, SC-32: new item created, old stays done, `status_differs`), unresolved owner both sides identical text (`owner_unresolved`).
  - **Precedence table (one pair per adjacent reason pair):** owner differs + due differs → `owner_differs`; existing done/new open + due differs → `status_differs`; due differs + same file → `due_differs`; same file + recurrence word → `same_file`; recurrence + outside window → `recurrence`; outside window + owner unresolved → `outside_window`; unknown meeting date → `outside_window`; ambiguous owner + otherwise identical → `owner_unresolved`; namesakes with identical text → `owner_differs` (AC-05.16).
  - Fuzzy: Jaccard 0.9 same owner links `fuzzy`; Jaccard 0.9 different owners none.
  - Symmetric storage: link created from (new=9, existing=3) is stored (3, 9); re-ingesting a changed file does not duplicate existing links.
- **Done when:** link table covers every reason value in §7.2 (`due_differs owner_differs status_differs same_file fuzzy recurrence outside_window owner_unresolved`); precedence encoded as one ordered constant and tested with a pair satisfying two reasons for every adjacent step.

### T-M5-10 — Tier-1b completion match (dateless `[x]` closes earlier open copies; holder vs `CI`)
- **Size:** M
- **Depends on:** T-M5-08, T-M5-09, T-M5-22
- **Parallel with:** -
- **Spec refs:** §10.6 Tier-1b (G-31), §10.6.1 rows 5–6, §6.4 (`CI`), §7.2 ItemLink (`completed_by`), D-05, R-16, FR-07.1, BR-07, BR-08, AC-07.11, AC-07.14, AC-05.19, SC-47, Appendix A.4 clusters 1–11
- **Files:** `src/actiontrack/domain/matching.py`, `src/actiontrack/services/ingest.py`, `tests/unit/test_tier1b.py`, `tests/integration/test_tier1b_sample.py`
- **Work:**
  - Applies to a new `[x]` source when Tier-1 failed: core identical, ≥3 tokens, no recurrence word, owners resolved and identical on both sides, different file, within `dup_window_days`, and done source's meeting date ≥ the open item's **latest source** meeting date; due ignored. Items with an unresolved owner never match (AC-07.14). Matches **every** open item satisfying this; manual-done items are not open and never match.
  - **Evidence holder (spec, resolves G-C8):** the most recent matching item — latest source meeting date, then **highest item id**. The done source is attached to the holder. Every matching item becomes `done`, `completion_origin=note`, `completed_at` NULL, event `status_changed` quoting evidence (`file:line`, raw text). **Every matching item other than the holder** gets an `ItemLink completed_by` (from = that item, to = holder) and shows `CI`; the holder carries its own `[x]` source, is done by direct evidence, has **no** `completed_by` link and is **not** flagged `CI` (the v0.4 "holder also shows CI" default and the "no self-link" workaround are both withdrawn). When there is a single matching item it is the holder: done, no link, no `CI`.
  - Record the completion evidence (the `[x]` source id and its meeting date) on every completed item via `ActionItem.completion_evidence_source_id` (G-C21) so the reopen rule of §10.7.3 (T-M5-11) can test it; dependents point at the holder's evidence source.
  - Nothing is reopened or deleted here. `recompute_canonical` (T-M5-22) runs for the holder after the source attach; completion status is not overridden by it.
  - Count outcome: Tier-1b attachments are reported as **merged-duplicates** (spec §4.2, confirmed; T-M5-15 follows).
- **Tests to write first (TDD)** (sample, files fed chronologically):
  - Cluster 1 (AC-07.11, SC-47): 09-01 (due 09-04), 09-08 (due 09-11, linked `due_differs`), 09-15 `[x]` dateless: both items done via Tier-1b; the **09-08 item holds the evidence and has no `CI`**; the **09-01 item is done with `CI` and a `completed_by` link to the 09-08 item**; three `ItemSource` rows retained overall; `list --owner "Priya Nair" --status open` data excludes both.
  - Single-match cluster (`Set up staging environment`, 09-01 open ISO due / 09-08 `[x]`): the 09-01 item is the holder: done, no `completed_by`, no `CI`.
  - Every other cluster of A.4 rows marked Tier-1b (cloud budget, staging, export invoices, welcome copy, acceptance tests, renewal call, procurement form, deploy beta, Q4 cloud spend, notify customers): all matching items done; across the whole sample exactly **11** evidence holders and **5** `CI` items (the five 2-open-item clusters: revised quote, cloud budget, welcome copy, renewal call, deploy beta) and 5 `completed_by` links; counts per cluster as in T-M5-20.
  - Tie-break: two matching open items with the same latest source meeting date: holder is the one with the higher item id.
  - Negative (AC-07.14): earlier-dated `[x]` file ingested first then later open item; owner ambiguous; owner unassigned; recurrence word; different owners (`Priya Nair` open vs `Priya Shah` done); window 61 days; same file.
  - Manual-done item never matched or altered.
- **Done when:** the 11 sample clusters end exactly as A.4 states (`CI` only where A.4 says so); no source row deleted; negative table green.

### T-M5-11 — Status derivation, sticky manual done and the §10.7.3 reopen rule (owns AC-07.16)
- **Size:** M
- **Depends on:** T-M5-08, T-M5-10, T-M5-22, T-M5-02
- **Parallel with:** T-M5-12
- **Spec refs:** BR-09, BR-10, BR-21, FR-03.7, §10.7.3, §10.7.9(b), §10.6.1 (status row), W-STATUS-REOPENED, W-STATUS-CONFLICT, AC-03.6, AC-07.16, AC-01.3, SC-18, SC-66, G-05, G-08, H-06
- **Files:** `src/actiontrack/domain/status.py` **(new)**, `src/actiontrack/services/ingest.py`, `tests/unit/test_status.py`, `tests/integration/test_reopen_rule.py`
- **Work:**
  - Pure `derive_status(item, sources, evidence) -> (status, origin)`: **manual done is sticky and always wins** (ingestion never reopens it); else a note-derived completion (item holds `completion_evidence_source_id`, i.e. Tier-1/Tier-1b/`[x]` evidence) stays `done` until the reopen rule fires; else the note-derived status follows the **latest present source** (`[x]` done/origin `note`, `[ ]` open; T-M5-22 ordering). `completed_at` stays NULL for note-origin completion.
  - **Reopen rule (spec §10.7.3, replaces the v0.4 G-C7 defaults):** a note-derived completion (`completion_origin=note`, including Tier-1b) is reopened (status `open`, `completed_at` null, event `status_changed`, warning `W-STATUS-REOPENED`) **only** by a `[ ]` for the **same source key** as the completion evidence — the same `ItemSource` row as tracked across edits by the pairing rules (T-M5-12 (a)–(c)) — that is **later-dated** than the evidence: the note's meeting date is later than the evidence note's date, or equal to it when the evidence file itself was edited so the evidence line now reads `[ ]` instead of `[x]`. Implementation: the pairing step (T-M5-12) reports "evidence source row now `[ ]`"; `maybe_reopen(item, evidence_row, new_meeting_date)` applies the date test. When an evidence holder is reopened **every item whose completion was inferred from it** (`completed_by` links to the holder) is reopened too, each with its own `status_changed` event and `W-STATUS-REOPENED`. An unrelated edit — to another source of the item, or to the file holding an inferred-complete item's own earlier `[ ]` source (even an in-place edit that leaves it `[ ]`) — never reopens a completed item. Removal of the evidence line (`present=false`) is **not** a `[ ]` and does not reopen (default adopted for an unstated case, new gap G-C24; the item may become `SM`).
  - `W-STATUS-CONFLICT` (spec §10.7.9(b), resolves G-C3): one warning per **manual-done** item whose latest present source is `[ ]`, emitted by the whole-database pass at the end of **every** ingest (also when the note file is unchanged), once per run per item; implemented as `manual_done_conflicts(conn)` called by T-M5-14 step 4. A manual-done item with a `[x]` latest source emits nothing.
  - Existing done (manual or note) + new open restatement in another file is never merged (handled in T-M5-09, `status_differs`).
  - `done` command write path is M6; provide `repo.set_manual_done(item_id, ts)` used by tests only.
- **Tests to write first (TDD):**
  - AC-03.6/SC-18: manual done on an item; edited copy of its note still showing `[ ]`: stays done, `W-STATUS-CONFLICT`; **unchanged-note variant** also warns (via the whole-DB pass, no file touched), once per run.
  - **AC-07.16(a)/SC-66:** quote-portal 09-01/09-08/09-15 ingested (all done); an unrelated word edited in the 09-01 note (the file holding the `CI` item's own `[ ]` source) and re-ingested: both items stay `done`, no event, no warning. Same with an edit that rewrites the 09-01 line in place and leaves it `[ ]`.
  - **AC-07.16(b)/SC-66:** the 09-15 evidence line changed from `[x]` to `[ ]` and re-ingested: the 09-08 item (holder) **and** the 09-01 item (`completed_by` it) reopen, each with a `status_changed` event and `W-STATUS-REOPENED`; plus a plain note-`[x]` item whose own line is flipped to `[ ]` in the same file reopens; a second `[x]` source elsewhere is not the evidence and does not keep it closed unless it is the evidence (assert the chosen reading).
  - **AC-07.16(c):** manual done item: no note change ever reopens it; only `W-STATUS-CONFLICT`.
  - Later-dated reading: a `[ ]` for the same source row in a file whose meeting date was edited to a later date reopens; edited to an earlier date does not (literal §10.7.3; flagged in notes).
  - Evidence line deleted: item stays done, no `W-STATUS-REOPENED` (G-C24 default).
  - BR-21: `[x]` items imported as done (13 in the sample) and absent from the "open" derivation.
- **Done when:** table of transitions green; the derivation function is the only place that sets `status`; the AC-07.16 (a)(b)(c) tests carry the criterion id in their names.

### T-M5-12 — Changed-file reconciliation: pairing (including `present=false` rows), edits in place, moved lines, removed and restored lines (owns the restore half of AC-08.10)
- **Size:** L
- **Depends on:** T-M5-07, T-M5-08, T-M5-11, T-M5-22, T-M5-02
- **Parallel with:** T-M5-13
- **Spec refs:** §10.7.2, §10.7.3, §10.6.1, FR-08.1, BR-16, BR-11, G-24, AC-08.2, AC-08.3, AC-08.6, AC-08.8, AC-08.10, SC-26, SC-68
- **Files:** `src/actiontrack/services/ingest.py`, `src/actiontrack/domain/matching.py`, `tests/unit/test_pairing.py`, `tests/integration/test_changed_file.py`
- **Work:**
  - `pair_entries(old_sources, new_entries) -> (pairs, new_only, old_only)` over **all of the file's existing ItemSource rows, including `present=false` ones** (spec §10.7.2 as amended by G-24; this replaces the v0.4 separate "revive pass"): (a) same `block_hash` (verbatim raw text) and same `occurrence_index` (line numbers may differ: the plain "moved line"); (b) same cleaned description at a different line; (c) one-to-one pairing of the remainder with the same `section_heading` and token Jaccard ≥ 0.6 on the cleaned description, greedy by highest score then document order. Deterministic tie-breaks. Default adopted for an unstated priority: `present=true` rows are paired first, `present=false` rows only for what remains, so an identical present line is never stolen by a restored one.
  - Pairs (a)/(b): update line range only (and raw/hash when (b)). Pairs (c): update the source's own `description`/`task_text`/owner/due/status columns **from the note** with event `updated_from_source` (before/after) and `owner_changed` when the owner differs; same `ActionItem.id` retained. The item's canonical fields are then rebuilt by `recompute_canonical` (T-M5-22): description earliest present source, due/owner/status latest present source. Editing a non-canonical source therefore updates only that source row, and the item changes only if the rule makes that source canonical.
  - **A paired `present=false` row is restored:** `present=true`, `last_seen_run` updated, same item, event `source_added` (note: restored), no new item and **no `same_file` link** (AC-08.10, SC-68); item `source_state` recomputed (T-M5-13) so `SM` clears. It is classified `updated` in the report (source_state changed).
  - Unpaired new entries go through Tier-1, Tier-1b, links (T-M5-08..10). Unpaired old entries: `present=false`, event `source_removed`, item `source_state` recomputed in T-M5-14.
  - When an evidence row is paired and its checkbox became `[ ]`, hand it to `maybe_reopen` (T-M5-11).
  - No re-matching for paired entries (documented simplification). `last_seen_run` updated for paired sources.
- **Tests to write first (TDD):**
  - AC-08.2/SC-26: change `confirm cloud budget with finance` to `confirm the cloud budget with finance team` in a copy of `2026-09-15-quote-portal-weekly.md`: same item id, description updated, one `updated_from_source` event.
  - AC-08.6: move the line `Rahul to fix the pagination bug` from position 4 to position 1: same id, only line numbers change, no events.
  - AC-08.3: delete a line: item remains, source `present=0`, event `source_removed`, `source_state=missing` after the final pass; id never reused after a new item is added (AC-08.8).
  - **AC-08.10 / SC-68 (restore):** delete a task line, re-ingest, restore the **same** line in a later edit, re-ingest: same ActionItem id, the old ItemSource row has `present=1`, **no new item, no `same_file` link**, `SM` cleared; restored with small wording change pairs by (c) too.
  - Present row not stolen: identical line kept and an identical line removed+restored in one file pair by occurrence index.
  - Greedy pairing: two similar lines in one section edited: pairing is one-to-one by highest score; ties by document order.
  - Edit of a non-canonical source of a merged item (cluster `freeze invoice edits`, edit the earlier 09-29 line vs the later 10-08 line) follows T-M5-22 selection (earliest description, latest due/owner).
- **Done when:** AC-08.2/08.3/08.6/08.8/08.10 tests green; re-ingesting the changed file twice is idempotent (second run no events).

### T-M5-13 — Deleted, renamed and failed-after-success files; `--no-recursive` scope; `source_state` derivation (owns the no-recursive half of AC-08.10)
- **Size:** M
- **Depends on:** T-M5-05, T-M5-12
- **Parallel with:** T-M5-12
- **Spec refs:** §10.7.4, §10.7.5 (scope rule, G-24), §10.7.8, BR-16, W-FILE-MISSING, FR-08.1, AC-08.3, AC-08.4, AC-08.7, AC-08.10, SC-27, SC-68, G-C12 (residual)
- **Files:** `src/actiontrack/services/ingest.py`, `src/actiontrack/store/repo.py`, `tests/integration/test_missing_files.py`
- **Work:**
  - `mark_missing(conn, folder_root, discovered_rel_paths, scan_scope)`: SourceFiles under the same canonical `folder_root` that were previously `ok/empty`, are **inside this run's scan scope** and are not discovered become `status=missing`, their sources `present=0`, warning `W-FILE-MISSING` (file path). **Scope rule (spec §10.7.5, no longer a default):** under `--no-recursive` only top-level files (rel_path without `/`) are in scope, so previously ingested nested files are **not** marked missing merely because they were not scanned and emit no `W-FILE-MISSING`. A top-level file deleted under `--no-recursive` is still marked. Files from another `folder_root` are never touched.
  - A file that is discovered but failed this run (E-*) is **not** missing and its items are untouched (§10.7.8, AC-08.7).
  - `recompute_source_state(conn, folder_root)`: item `source_state=missing` iff it has sources and none is `present` (flag `SM`); clears automatically when a source is present again (restore case: T-M5-12; rename case: new file's sources attach via Tier-1/1b or link, rule 6 counts the missing old source as "different file").
  - Canonical `folder_root`: absolute, `resolve()`d, case-folded comparison key on Windows, stored with `/` separators (NFR-PORT-01). Still not specified by the spec (G-C12 residual): defaults as stated.
  - Items never deleted; status unchanged; still listed.
- **Tests to write first (TDD):**
  - AC-08.4/SC-27: ingest 3 files, delete one: SourceFile `missing`, items retained with `SM`, `W-FILE-MISSING` reported, exit 0.
  - **AC-08.10 / SC-68 (scope):** ingest a folder recursively (nested files present), then `ingest <folder> --no-recursive`: **no** nested SourceFile is `missing`, no `W-FILE-MISSING`, no item gets `SM`, nested files' sources stay `present=1`; sample variant: ingest a temp parent holding the sample set one level down recursively, then again with `--no-recursive` (0 discovered, hint), leaves all 35 files untouched. A nested file deleted while running `--no-recursive` stays untouched; the next recursive run marks it missing.
  - Rename file (delete + add same content): new file's items merge (Tier-1 / Tier-1b) or link, old source `present=0`, `SM` cleared on merged items, report mentions both.
  - AC-08.7: replace a good file with a binary: `failed`, not `missing`, exit 5 later.
  - Two folder roots in one DB: ingesting root B never marks root A files missing.
- **Done when:** the five integration tests green; `recompute_source_state` is idempotent.

### T-M5-14 — End-of-run finalisation transaction: whole-database passes (re-resolution, conflict check), derived state, run summary
- **Size:** M
- **Depends on:** T-M4-11, T-M4-15, T-M5-04, T-M5-11, T-M5-13, T-M5-22
- **Parallel with:** T-M5-15
- **Spec refs:** §10.3 step 6, §10.7.4–6, §10.7.9 (a)(b), §4.2 (run status, counters), NFR-REL-01, NFR-REL-02, AC-08.5, AC-05.8, AC-05.17, AC-05.18 (count), AC-03.6, W-OWNER-NOW-AMBIGUOUS, G-08, G-C11
- **Files:** `src/actiontrack/services/ingest.py`, `tests/integration/test_finalise.py`
- **Work:**
  - One transaction after the last file, in this order: (1) `mark_missing` (this folder root, in-scope files); (2) **whole-database owner re-resolution** over **all folder roots**: `load_owner_snapshot` → `re_resolve` (T-M4-11) → `apply_owner_changes` (T-M4-15) with `owner_changed` events and `W-OWNER-NOW-AMBIGUOUS` warnings; (3) `recompute_source_state` for every folder root with changes and `recompute_canonical` for affected items; (4) **`W-STATUS-CONFLICT` check over the whole DB** (`manual_done_conflicts`, T-M5-11); (5) recompute person display names; (6) set the IngestRun `status` (`complete` or `partial`), `finished_at`, counters.
  - **Steps 2 and 4 run on every ingest, including a run where every file was `unchanged` and regardless of file hashes** (§10.7.9, G-08); under `--dry-run` they are computed on the in-memory copy and reported but nothing is written (T-M5-17).
  - Every step is idempotent and state-derived so that a crash before this transaction is repaired by the next run.
  - `resolved_via_attendance` is a **state count** computed here: owner rows currently with provenance `attendee_in_meeting` (namesake groups only, AC-05.18; same predicate as T-M4-09), not a run delta.
  - Failures in this transaction abort with exit 4 (no partial finalisation).
- **Tests to write first (TDD):**
  - Two runs back to back: second run executes finalisation and changes nothing (business tables identical).
  - Crash injected after file k but before finalisation (fault hook): next run's final DB equals an uninterrupted run (AC-08.5 precondition).
  - **Re-resolution on unchanged folder (AC-05.17):** all files `unchanged`, roster gains an alias for the ambiguous token: finalisation turns the owner `resolved` (provenance `alias`) with an `owner_changed` event; the run report still shows `unchanged 33`. (End-to-end acceptance in T-M5-21.)
  - `attendee_disambiguation=false` over an existing DB: all `attendee_in_meeting` rows become ambiguous in step 2 (AC-05.8).
  - Downgrade scenario of T-M4-11 end to end through the orchestrator.
  - `W-STATUS-CONFLICT` emitted in an all-`unchanged` run for a manual-done item with `[ ]` note (AC-03.6).
  - `partial` status when one file failed, `complete` otherwise; counters equal the report's run-scoped numbers.
- **Done when:** finalisation is one function with the six ordered steps and the above tests green.

### T-M5-15 — Ingest report model and counting rules (spec §4.2 definitions)
- **Size:** M
- **Depends on:** T-M5-07, T-M5-08, T-M5-09, T-M5-10, T-M5-14, T-M4-12, T-M4-15
- **Parallel with:** T-M5-14, T-M5-17
- **Spec refs:** §4.2 (report definitions, G-25), FR-01.7, AC-01.7, AC-01.13, AC-05.14, AC-05.19 (report note), §10.3 step 6 (H-05 note), §10.3 (resolved via attendance count), W-5, G-C11
- **Files:** `src/actiontrack/domain/models.py`, `src/actiontrack/services/ingest.py`, `tests/unit/test_report_model.py`
- **Work:**
  - `IngestReport` dataclass: header (run id, folder, as-of date, interrupted-run notices); `files` counts (`discovered, ingested, unchanged, empty, failed`, plus missing/skipped-symlink notices); `items` counts; five sections (imported/new, duplicates & possible duplicates, unresolved owners, missing/unresolved due dates, parsing failures) plus `warnings` grouped by code with total; `resolved_via_attendance`; run `status` (`complete`/`partial`/`interrupted`); `exit_code`.
  - **Count definitions are now spec text (§4.2; G-C11 resolved):** Files line is run-scoped with `discovered = ingested + unchanged + empty + failed` (empty and failed counted every run, never `unchanged`). Items line: each parsed source from an *ingested* file is classified exactly once: `new` (ActionItem created), `merged-duplicates` (attached by Tier-1 **or Tier-1b**), `updated` (paired, a business field changed — including a restored `present=false` source), `unchanged` (paired, nothing changed); `new + merged + updated + unchanged` = sources imported. `possible-duplicates` = new `possible_duplicate` links this run. `already-done-in-note` = sources with `[x]` imported this run (orthogonal). Sources of `unchanged` files are not counted.
  - **Sections are state-scoped over the current database and cover *open* items only** (§4.2): "Unresolved owners", "Missing/unresolved due dates", "Possible duplicates" equal what `list` would show, i.e. **all folder roots** (the v0.4 "ingested folder root only" default is withdrawn) and done items are excluded; the heading counts are state counts. Run-scoped counts are labelled as such. `resolved via attendance: N` is a state count of owner rows with provenance `attendee_in_meeting`.
  - Row records carry `item_id`, `file:line` (rel path, `/`), raw text (untruncated; truncation/control-character stripping happens in rendering), reason text, candidate full names (T-M4-12 hook), `due_raw` + reason (`unresolved "next week" (vague)`), and link reasons/due values for pairs.
  - Unresolved owners include unassigned (no owner/non-person), ambiguous, and provisional (`unverified`), each tagged.
  - **H-05 note (AC-05.19):** every owner downgraded to `ambiguous` in this run (`W-OWNER-NOW-AMBIGUOUS`) whose item was merged or completed under the earlier resolution carries the text "merged/completed under an earlier owner resolution" (flag from T-M4-15) in the warning message and in the JSON row `notes`; if the item is still open it also appears in the Unresolved owners row. Because a Tier-1b-completed item is `done` and the sections are open-only, the note cannot live only in that section (new gap G-C23); default adopted: the renderer prints these downgrade lines under the Warnings heading even without `--verbose` (flag for confirmation).
  - Deterministic ordering: sections by item id; failures by path.
- **Tests to write first (TDD):**
  - Hand-built DB fixture mirroring the §4.2 example: sections and counts as in the example text.
  - Partition tests: for any run, `new+merged+updated+unchanged == sources imported`; `discovered == ingested+unchanged+empty+failed`.
  - Second run on unchanged folder: items delta all zero but unresolved-owner section still lists the same open rows (state scope); a done ambiguous item is **not** in the section; rows from a second folder root **are** (matches `list`).
  - AC-05.14 count: ambiguous rows in the section equal `group_ambiguous(open)` count.
  - H-05: a Tier-1b-completed item downgraded to ambiguous yields the note text in the warning and JSON, status `done` retained.
  - Report on the full sample is verified in T-M5-20.
- **Done when:** model + counting functions pure (take a DB connection and run id) and covered; the definitions are written in the module docstring and referenced by T-M5-16/20.

### T-M5-16 — Render the ingest report (table and JSON) with golden tests
- **Size:** M
- **Depends on:** T-M5-15, M1 CLI shell (streams, `--format`, `--quiet/--verbose`)
- **Parallel with:** T-M5-17
- **Spec refs:** §4.2, FR-01.7, AC-01.7, AC-09.4 (determinism scope), AC-09.8, AC-09.9, NFR-LOG-02, NFR-TEST-03, NFR-USE-02, SEC-08, §11 (message must say what/where/next)
- **Files:** `src/actiontrack/report/table.py`, `src/actiontrack/report/json.py`, `tests/golden/ingest_*.txt|json`, `tests/unit/test_report_render.py`
- **Work:**
  - Table renderer reproduces the §4.2 layout; empty sections print `none` (AC-01.7); summary line printed (also under `--quiet`, AC-09.9: `--quiet` suppresses warning/info diagnostics but never the summary line nor errors); "Warnings (n): see --verbose" and verbose expansion (except the H-05 downgrade lines of T-M5-15); final line `Exit code 5 (2 files failed)` when applicable; `--no-recursive` hint line (AC-01.13) from report data; run-scoped counts labelled as such.
  - Strip control characters (including ESC sequences) from every note-derived string at render time (AC-09.8, SEC-08); truncate long raw text to fit 120 columns unless `--wide`.
  - JSON renderer: stable key order, all report fields, no truncation.
  - Paths shown with `/` on every OS; no absolute path except the folder header (AC-09.4); dates as `YYYY-MM-DD`. Determinism scope per AC-09.4: byte-identity is asserted **excluding the run number and the absolute `folder=` value**; goldens are compared after masking those two fields (and stored timestamps only with `ACTIONTRACK_NOW`).
  - Result data to stdout, warnings/diagnostics to stderr.
- **Tests to write first (TDD):**
  - Golden for the §4.2-style fixture (table and JSON) with Windows and POSIX path inputs producing byte-identical output (run number and `folder=` masked).
  - A task text containing `\x1b[31m` renders without the raw escape (AC-09.8).
  - `--quiet` suppresses diagnostics but still prints the summary line and errors; empty report prints all five `none` sections.
- **Done when:** goldens committed and byte-stable across two runs with `--today` fixed.

### T-M5-17 — `--dry-run` behaviour (no writes of any kind, no lock)
- **Size:** S
- **Depends on:** T-M5-05, T-M5-14, T-M5-04
- **Parallel with:** T-M5-16
- **Spec refs:** FR-01.10, AC-01.10, SC-40, BR-01, §4.2, §10.7.7 (`--dry-run` takes no write lock), §10.7.9 (passes computed, nothing written), G-C13 (residual)
- **Files:** `src/actiontrack/services/ingest.py`, `src/actiontrack/store/db.py`, `tests/integration/test_dry_run.py`
- **Work:**
  - Dry-run opens the real DB **read-only** (`mode=ro`, no `-wal/-shm` creation; if absent, start from an empty in-memory schema) and copies it with `sqlite3.backup` into `:memory:`, then runs the exact same pipeline (including **both whole-database passes** of finalisation) against the copy; nothing is written to disk: no DB, no run record, no `<db>.lock` file, no `.actiontrack` directory, no `.bak`. It does not take the process lock (spec §10.7.7).
  - Report still prints; run id shown is "next id" read from the copy (documented; spec silent, G-C13 residual); interrupted-run marking, missing-file marking, re-resolution and conflict warnings happen only in the copy and are shown as "would".
  - Exit code logic identical to a real run (exit 5 still applies).
  - Coordinate with M1 on journal mode so a read-only open of a WAL database creates no side files (verify in test).
- **Tests to write first (TDD):**
  - AC-01.10: DB file byte-identical (SHA-256) before and after, or still absent; directory listing unchanged; report printed (SC-40).
  - Dry-run on a DB with a stale `in_progress` run leaves that row `in_progress`.
  - Dry-run runs while another process holds `<db>.lock` (no exit 4) and creates no lock file.
  - Dry-run after a roster alias change reports the owner re-resolution it would do (`owner_changed` listed) while the DB stays byte-identical.
  - Dry-run then real run produce the same item counts as a real run alone.
  - Sample folder dry-run exits 5 and writes nothing.
- **Done when:** a filesystem snapshot test (all files under the DB directory, including hidden) is equal before and after.

### T-M5-18 — Exit-code logic and environment errors for `ingest`
- **Size:** S
- **Depends on:** T-M5-05, T-M5-15, M1 CLI shell exit-code map
- **Parallel with:** T-M5-16, T-M5-17
- **Spec refs:** §4.1 exit codes, §11, AC-01.8, AC-01.12, AC-01.13, AC-01.18 (exit half), AC-08.7, AC-08.9 (exit 4), W-NO-FILES, SC-09, SC-41, SC-69
- **Files:** `src/actiontrack/services/ingest.py`, `src/actiontrack/cli.py`, `tests/integration/test_ingest_exit_codes.py`
- **Work:**
  - `IngestOutcome(report, exit_code)`; precedence: environment errors (folder missing or not a directory or unreadable, DB unusable/locked/corrupt, **process lock held by another actiontrack process**, config invalid) give 4 **before any write** (AC-01.12); else ≥1 `failed` file (any `E-*`, including a previously OK file now failing) gives 5; else 0. `W-*` never changes the code (including `W-DIR-UNREADABLE`, `W-CHECKBOX-*`, `W-OWNER-NOW-AMBIGUOUS`).
  - Folder with zero `.md`: exit 0 with `W-NO-FILES`; with `--no-recursive` and `.md` files in subfolders, the message states the count and suggests removing the flag (needs the subfolder count from M2 discovery; AC-01.13 expects "35").
  - Final report line `Exit code N (reason)` data; stderr gets error text; no stack trace unless `--verbose` (NFR-USE-01).
- **Tests to write first (TDD):**
  - Nonexistent folder: exit 4, DB not created (AC-01.12); folder that is a file: exit 4.
  - 3 valid + 1 binary + 1 empty: 3 ingested, 1 empty, 1 failed, exit 5 (SC-09).
  - Temp parent folder holding the sample set one level down, with `--no-recursive`: 0 files, exit 0, hint mentions 35 `.md` files (AC-01.13).
  - Locked DB or lock held by another process: exit 4 and no run row (AC-08.9; process-level proof lives in T-M5-04).
  - SC-69 folder (dot-files, unclosed comment, empty checkbox, unreadable subdirectory): exit 0 with the warnings only.
- **Done when:** the table of (situation, exit code) in the test equals spec §4.1 for codes 0, 4, 5.

### T-M5-19 — Fault-injection hooks and invariant/property tests (AC-08.5, AC-08.7, plan test strategy)
- **Size:** M
- **Depends on:** T-M5-14, T-M5-12, T-M5-13
- **Parallel with:** T-M5-18
- **Spec refs:** AC-08.1, AC-08.5, AC-08.7, AC-08.8, AC-08.9 (real-process kill), AC-07.9, NFR-REL-01, NFR-REL-02, BR-11, BR-15, SC-28, SC-67, §10.7.10, R-18, plan section 4 (fault injection, property tests)
- **Files:** `src/actiontrack/services/ingest.py`, `tests/integration/test_fault_injection.py`, `tests/integration/test_invariants.py`
- **Work:**
  - `FaultHooks` object injected into `run_ingest(..., hooks=None)` (never exposed in the CLI): callbacks `after_file(index, rel_path)`, `before_commit(rel_path)`, `before_finalise()`, `on_read(rel_path)` (can raise `OSError` to simulate an unreadable file mid-run). A simulated kill raises `SimulatedCrash(BaseException)` so no `except Exception` handler (or the run-status update) executes.
  - One real-process test: spawn `python -m actiontrack ingest` in a subprocess with a hook env var honoured only under `pytest` (guarded), `os._exit` after file k, then rerun; the rerun must acquire `<db>.lock`, mark the abandoned run `interrupted` (AC-08.9 recovery half; the lock-contention half is T-M5-04).
  - Invariant tests over randomised folder permutations (seeded): idempotent re-ingest; `ItemSource` count never decreases; ids never reused; final state independent of how the run was split by a crash point (compare canonical DB dump of "interrupted at k then rerun" vs "uninterrupted" for every k from 0..n).
- **Tests to write first (TDD):**
  - AC-08.5/SC-28: crash after file k for every k over the sample's readable files: after rerun, DB dump equals the uninterrupted dump (IngestRun rows excluded except that the earlier run is `interrupted`), no duplicate items.
  - Crash inside a file transaction (`before_commit`): file leaves no rows, retried on rerun.
  - AC-08.7 via `on_read` raising for a previously ingested file: items retained, file `failed`, exit 5.
  - Property: 25 shuffled copies of the sample folder (same content, different file creation order) produce identical DB dumps.
- **Done when:** all crash points green; the cross-run order-dependence limitation (newly added **older** note in a later run, documented in spec §10.7.10 and R-18, **not** a defect) is captured as an explicit, named xfail test with the expected divergence documented. (The v0.4 second half of G-C14, line removed and restored, is now an ordinary requirement, tested in T-M5-12.)

### T-M5-20 — End-to-end sample-folder suite (SC-41–51, AC-07.11–07.13, Appendix A.4)
- **Size:** L
- **Depends on:** T-M5-16, T-M5-17, T-M5-18, T-M5-19, T-M4-14, T-M5-21
- **Parallel with:** -
- **Spec refs:** SC-41–SC-51, SC-47, SC-53, AC-01.13, AC-01.14, AC-01.15, AC-01.17, AC-05.1, AC-05.2, AC-05.14, AC-05.18, AC-07.9, AC-07.11, AC-07.12, AC-07.13, AC-08.1 (G-28), Appendix A.2–A.4
- **Files:** `tests/fixtures/notes/` (copy), `tests/golden/ingest_sample_report.txt|json`, `tests/integration/test_sample_end_to_end.py`
- **Work:**
  - Ingest the fixture copy (recursive default; parent and child path variants of the temp layout) with the Appendix C roster and `--today 2026-10-22`; assert the report and DB against the expectations below; store the table and JSON reports as goldens (run number and `folder=` masked, AC-09.4); second run asserted separately (see test list).
  - Hand-derived expectations (derived from spec rules by the author of this task list; **verify, do not trust blindly**; any mismatch is either a bug or a new spec finding): 117 `ItemSource` rows; 105 `ActionItem` rows (117 minus 11 Tier-1b attachments minus 1 Tier-1 merge); 18 done (16 closed via Tier-1b plus the two standalone `[x]`: `Karan to revoke access…` 10-09, `Lena to update the Acme account notes` 10-10) and 87 open; of the 16 Tier-1b-closed items **11 are evidence holders (no `CI`) and 5 carry `CI`** with 5 `completed_by` links (G-31); 11 `possible_duplicate` links (5 inside Tier-1b clusters before completion: revised quote, cloud budget, welcome copy, renewal call, deploy beta; 6 outside: wireframes, pagination, schema mapping, SSO, one-pager, customer quotes); 7 ambiguous and 5 unassigned owners (12 unresolved owners, 0 provisional; the report section lists **open** items only, so the section count is 12 minus any of those that are done); `resolved via attendance` 36 (provenance `attendee_in_meeting`, Priya/Rahul only); Items line delta `new 105 | merged-duplicates 12 | possible-duplicates 11 | already-done-in-note 13` under the T-M5-15 definitions.
  - Files line: discovered 35, ingested 33, empty 1, failed 1 (`scratch.md` `E-BINARY`), `.txt` ignored, exit 5 (AC-01.13, SC-41).
- **Tests to write first (TDD):**
  - SC-41: file counts and exit 5; `<tmp>` vs `<tmp>/notes` give the same result; `--no-recursive` on the parent gives the hint.
  - SC-42/43: blockquoted `Karan to refund affected customers by Friday` not imported, real `Karan to refund affected customers` (09-21:17) imported; fenced and commented checkboxes absent, 4 real items in `2026-10-09-security-review.md`, `Karan … revoke access` done (the 3 ignored-region warnings present).
  - SC-44/45/46/48/50/51/52/53: ambiguous and resolved cases per T-M4-14 persisted in DB; Person rows exactly 9, zero provisional; SC-44's "4 ambiguous Priya items may belong to Priya Shah over the full sample" is asserted from `owner_matches_person` data (the single-file "1 item" variant uses a one-file run of `2026-09-17-billing-migration-sync.md`). All names asserted as `Priya Shah` (no `Priya Sharma` anywhere in fixtures or goldens, G-29).
  - SC-47/AC-07.11: revised-quote cluster done, 3 sources, the **09-08 item holds the evidence without `CI`, the 09-01 item has `CI`** (not "done with `CI`" on both). All A.4 rows asserted individually: Tier-1b clusters (11), Tier-1 `freeze invoice edits` (1 item, 2 sources), due-differs clusters (6 links), non-links (`first wireframes`, `usage report`, `procurement contact`).
  - SC-49/SC-64: `2026-10-03-sales-pipeline.md` gives `W-MEETING-DATE-INVALID`, `Meera … by Friday` unresolved `no_meeting_date`; `Meera to demo the portal to Acme on 2026-10-14` in `2026-10-06-quote-portal-weekly.md` due 2026-10-14 `explicit`.
  - AC-08.1 / SC-25 on the sample (G-28): second run reports **discovered 35 = unchanged 33 + empty 1 + failed 1**, exit 5, business tables byte-identical, item delta zero; whole-database passes ran and changed nothing.
  - AC-05.18: `resolved via attendance` equals the count of `attendee_in_meeting` rows (36) and no `Sam/Anita/Meera/Karan/Lena` row has that provenance.
  - AC-01.11: SHA-256, size and mtime of all 36 sample copies unchanged.
- **Done when:** all assertions green and goldens committed; any divergence from the hand-derived numbers is recorded in the integrator notes before the expectation is changed.

### T-M5-21 — Whole-database passes acceptance: alias/config changes on an unchanged folder, merges survive downgrade (owns AC-05.17, AC-05.19, SC-65, SC-72) (new)
- **Size:** M
- **Depends on:** T-M5-14, T-M4-15, T-M5-10, T-M5-08, T-M5-11
- **Parallel with:** T-M5-19
- **Spec refs:** AC-05.17, AC-05.8 (existing database), AC-05.19, AC-03.6 (unchanged note), §10.3 step 6, §10.7.9, H-05, G-08, W-6, SC-65, SC-72, SC-18
- **Files:** `tests/integration/test_whole_db_passes.py`, `tests/fixtures/whole_db/`
- **Work:** orchestrator-level acceptance tests (real `run_ingest`, temp DB, temp config/roster files); no new production code beyond fixes the tests expose.
  - **AC-05.17 / SC-65a:** ingest a fixture folder in which `Priya` in a meeting without an attendee list is ambiguous (two known Priyas). Run `ingest` again over the **unchanged** folder after adding a roster alias that maps the token to exactly one person: report shows all files `unchanged`, the owner is `resolved` with provenance `alias`, one `owner_changed` event, **no note file touched** (hash/size/mtime unchanged) and **no ItemSource row changed** (dump diff). Remove the alias, re-run: owner back to `ambiguous` with `W-OWNER-NOW-AMBIGUOUS`.
  - **AC-05.8 / SC-65b:** on the existing sample DB set `attendee_disambiguation = false` and re-run over the unchanged folder: every `attendee_in_meeting` item becomes `ambiguous` with an `owner_changed` event and `W-OWNER-NOW-AMBIGUOUS`; set back to `true`: they resolve again.
  - **AC-05.19 / SC-72:** (i) Tier-1b case: ingest the revised-quote notes, then add a third person `Priya Narang` to the roster so `Priya N.` becomes ambiguous; re-run: the 09-08 and 09-01 items stay `done` with all 3 sources, `completed_by` link and `CI` unchanged, owner `ambiguous`, report/JSON carry "merged/completed under an earlier owner resolution". (ii) Tier-1 case: two sources of one `unique_first_name` owner merged in a no-roster run, then a namesake attendee arrives: item keeps both sources, owner `ambiguous`, no split. No reopen, no split anywhere.
  - **AC-03.6 / SC-18 unchanged note:** manual done (via `repo.set_manual_done`), note still `[ ]`, file unchanged: each ingest run emits one `W-STATUS-CONFLICT`; status stays done.
  - Idempotence: a further run changes nothing (business tables identical, zero events).
- **Tests to write first (TDD):** the bullets above are the tests; write them red against the finalisation of T-M5-14 before fixing.
- **Done when:** all scenarios green; test names carry the AC/SC ids so the traceability gate (T-M9-05) finds them.

---

## Part C — notes for the integrator

### (a) Acceptance criteria believed covered by Part C tasks (spec v0.6)

- **AC-01:** 01.1 (ordering half, T-M5-03), 01.2, 01.3, 01.9 (persistence half) (T-M5-07), 01.7 (T-M5-15/16), 01.8 (T-M5-05, T-M5-18), 01.10 (T-M5-17), 01.11 (T-M5-05, T-M5-20), 01.12 (T-M5-18), 01.13 (T-M5-18/20), 01.14, 01.15 (persistence), 01.17 (T-M5-20), 01.18 (persistence and exit halves, T-M5-07/18), 01.19 (storage, T-M5-07; core from `task_text`, T-M5-06). Parsing halves belong to M2 (AC-01.4–01.6, 01.16, 01.20).
- **AC-03.6** (ingest side: T-M5-11 pure + T-M5-14 whole-DB pass; acceptance T-M5-21).
- **AC-05:** 05.1, 05.2, 05.3, 05.4, 05.5, 05.6, 05.7, 05.9, 05.10, 05.11, 05.13, 05.14, 05.15 (predicate; list rendering is M6), 05.16 (T-M4-12, T-M5-08/09) in M4/M5 as before; **newly owned:** **05.8** on an existing DB (T-M4-11 pure, T-M5-14, T-M5-21), **05.17** (T-M4-11, T-M4-15, T-M5-14, acceptance **T-M5-21**), **05.18** (provenance `initial_form`: T-M4-08; `unique_first_name` vs `attendee_in_meeting` and 36-count: **T-M4-09**, T-M4-14, state count T-M5-14/20), **05.19** (data guard **T-M4-15**, report note T-M5-15, acceptance **T-M5-21**). AC-05.12 only via the shared `identity_key` (M1 owns the config test).
- **AC-02.6 / AC-04.6** only the resolver half (T-M4-13); CLI half belongs to M6/M7.
- **AC-06.6/06.7/06.8/06.10** persistence of due fields and warnings only (T-M5-07); resolver is M3.
- **AC-07:** 07.1–07.14 (T-M5-06, 08, 09, 10, 20) as before; **newly owned:** **07.15** (canonical fields: **T-M5-22**, used by T-M5-08/12), **07.16** (reopen rule: **T-M5-11**, evidence from T-M5-10, pairing from T-M5-12).
- **AC-08:** 08.1–08.8 (T-M5-05, 12, 13, 14, 19); **newly owned:** **08.9** (**T-M5-04**, real-process kill in T-M5-19; lock module is T-M1-11), **08.10** (no-recursive half **T-M5-13**, restore half **T-M5-12**).
- **AC-09:** 09.4 (determinism parts, T-M5-16/19), 09.5 (T-M5-05), 09.6 (T-M4-07, T-M5-07), 09.8 (T-M5-16), 09.9 (`--quiet` summary, T-M5-16).
- **SC:** 01–06, 08–10, 18, 25–28, 30–32, 39–53, 55 (M4 side), 63, 64, 69 (persistence halves, T-M5-07/18), **65 (T-M5-21), 66 (T-M5-11), 67 (T-M5-04), 68 (T-M5-12/13), 72 (T-M5-21)**.
- Items **not** covered here and flagged: SC-44's "followup excludes it" half (M7), SC-54/56/62 (M7), SC-57–61/71 (M7/M8), SC-70 (M3/M6), AC-05.14's `list --ambiguous` CLI half (M6), `done` taking `<db>.lock` (M6 uses the T-M1-11 helper).

### (b) Gap register G-C1…G-C19 against spec v0.6, plus new findings

**Method note.** The v0.4 walk-through of Appendix A.3/A.4 is unchanged in substance (117 checkboxes, 104 open, 13 done; clusters and the 7 ambiguous owners come out as the spec states). Changes in v0.6 that alter task numbers: the evidence holder carries no `CI` (11 holders, 5 `CI` items), the attendance count is 36 only if provenance is `attendee_in_meeting` strictly inside namesake groups, and token splitting now splits on hyphens (core of `welcome-email copy` is 3 tokens, which matters for the ≥3-token Tier-1b rule).

| Gap | Status vs spec v0.6 | Reference / remaining default |
|---|---|---|
| **G-C1** data model fields | **RESOLVED** (G-11, §7.2) | Residual choices recorded in T-M5-01: per-source owner phrases as JSON (Plan decision), plus new column `completion_evidence_source_id` (see G-C21). |
| **G-C2** re-resolution vs unchanged files | **RESOLVED** (G-08, D-16, §10.3 step 6, §10.7.9, AC-05.17) | Residual (new G-C27): `date_order`, `dup_window_days`, `fuzzy_threshold` changes are still not re-applied to unchanged files. |
| **G-C3** AC-03.6 vs hash skip | **RESOLVED** (G-08, §10.7.9(b), AC-03.6) | T-M5-11, T-M5-14. |
| **G-C4** provenance enum and attendance inflation | **RESOLVED** (G-26, §7.2, §10.3 3a "only when \|C\|>=2", AC-05.18) | T-M4-02, T-M4-08, T-M4-09. |
| **G-C5** step 4 under-specified | **RESOLVED** (G-30, §10.3 step 4, §10.2a(c)) | T-M4-06, T-M4-10. |
| **G-C6** identity for non-full names, provisional after aliasing | **PARTLY RESOLVED**: keys and alias-keeping RESOLVED (G-30, §10.2a(a)–(d)); **OPEN**: qualifier slug format (`priya-nair-sales` default) | T-M4-01. See also G-C28. |
| **G-C7** multi-source semantics | **RESOLVED** (G-05, D-15, §10.6.1, §10.7.3, AC-07.15/07.16) | Residual under-specification: G-C24, G-C25. |
| **G-C8** Tier-1b details | **RESOLVED** for holder, tie-break (highest item id), `CI` and counting (G-31, §10.6, §6.4); **OPEN**: whether `with` co-owners count in "owner person sets" (default: included) | T-M5-08, T-M5-10. |
| **G-C9** link-reason precedence | **RESOLVED** (G-27, §10.6, AC-05.16 reworded) | Residual (new G-C22): position of `owner_unresolved`, unresolved-vs-unresolved due dates. |
| **G-C10** description vs `task_text`, tokenisation | **RESOLVED** (G-12, §7.2, §8.2, §8.3, FR-01.12, AC-01.19) | T-M5-06, T-M5-07. |
| **G-C11** report semantics, `partial`, SC-25 on sample | **RESOLVED** (G-25, G-28, §4.2, §7.2) | T-M5-15, T-M5-05, T-M5-20. |
| **G-C12** `--no-recursive` and folder identity | **PARTLY RESOLVED**: `--no-recursive` RESOLVED (G-24, §10.7.5, AC-08.10); **OPEN**: canonicalisation of `folder_root` (case, `\\?\`, symlinks) on Windows | T-M5-13 defaults. |
| **G-C13** concurrency and dry-run mechanics | **PARTLY RESOLVED**: process lock RESOLVED (G-14, §10.7.7, AC-08.9); **OPEN**: dry-run run-number display and read-only WAL side-files (plan/M1 matter) | T-M5-04, T-M5-17. |
| **G-C14** order dependence and re-added lines | **RESOLVED** (G-24: `present=false` rows in pairing, §10.7.2; §10.7.10 documents the older-note limitation, R-18) | T-M5-12, T-M5-19 (named xfail). |
| **G-C15** owner extraction | **PARTLY RESOLVED**: (i) clause location order RESOLVED (G-23), (ii) `Name:` only for known/`@` names RESOLVED, (iii) caseless names RESOLVED by token definition, (v) `W-ATTENDEE-NAME-INCOMPLETE` in Appendix B RESOLVED (G-20); **OPEN**: (iv) whether `W-OWNER-MISSING` fires for non-person owners (default: no) | T-M4-03. |
| **G-C16** merges persist after owner downgrade | **RESOLVED** (H-05, §10.3 step 6, AC-05.19, SC-72) | T-M4-15, T-M5-21. |
| **G-C17** naming (`Priya Sharma`), SC-44 count | **RESOLVED** (G-29) | T-M5-20 uses Priya Shah, single-file SC-44. |
| **G-C18** persistence-exception classification | **OPEN** (§11 still defines `E-INTERNAL-PARSE` for parser exceptions only; default kept) | T-M5-05. |
| **G-C19** misc | **PARTLY RESOLVED**: `Person.origin` for aliased provisional no longer needed (row deleted, G-30(d)); **OPEN**: `Person.verified` for attendee-origin (default false), roster has no `display_name` (derived), `dup_window_days` boundary (60 inclusive, absolute difference) | T-M4-05/06, T-M5-08. |

**New findings against spec v0.6 (defaults adopted in the tasks; please confirm):**
- **G-C20 Attendee re-evaluation.** §10.3 step 6 re-evaluates "every stored owner phrase … with its meeting's stored attendee list" but does not say whether the stored list's own person resolution is recomputed. Default: the pass rebuilds each meeting's `A` from stored raw names (T-M4-06 function) against current K (T-M4-11, T-M4-15).
- **G-C21 Completion evidence storage.** §10.6 / §10.7.3 require the evidence ("the `[x]` source's key and meeting date") to be recorded, but §7.2 has no field. Default: nullable `ActionItem.completion_evidence_source_id` (T-M5-01, T-M5-10); alternative is to derive it from `ItemEvent` JSON. M1 must add the column or confirm the alternative.
- **G-C22 Link precedence gaps.** §10.6 gives the order `owner_differs > status_differs > due_differs > same_file > recurrence > outside_window > fuzzy` and says `owner_unresolved` applies "when an owner … is not resolved and no higher reason applies" without placing it in the order, and does not say how `owner_differs` behaves when one owner is ambiguous, nor whether an `unresolved` due compares equal to another `unresolved`. Defaults in T-M5-09/T-M5-08 (`owner_unresolved` last; `owner_differs` only for two resolved owners; unresolved due compatible only if both `none`).
- **G-C23 AC-05.19 vs open-only report sections.** AC-05.19/SC-72 require the report to carry "merged/completed under an earlier owner resolution", but a Tier-1b-completed item is `done` and §4.2 sections list **open** items only, so that item has no section row. Default: the note rides on the `W-OWNER-NOW-AMBIGUOUS` warning line (printed even without `--verbose`) and in JSON `notes` (T-M5-15/16).
- **G-C24 Reopen rule corners.** §10.7.3 reopens only on a `[ ]` of the same source row that is "later-dated". A source key is one `ItemSource` row in one file, so "later-dated than the evidence" can only occur when that file's own meeting date is edited upward (equal date covers the plain edit); there is no cross-file reopen, although the G-05 summary ("a later-dated note's `[ ]`") reads as if there were. Deletion of the evidence line is not addressed: default stays done (T-M5-11). Edge: edit + earlier date does not reopen (literal reading).
- **G-C25 Ordering of undated sources and merge completion.** §10.6.1 says "meeting date — null last" for the latest-source order; for "earliest present source" the null placement is unstated (default: same ascending key, so an undated source is the latest). Tier-1 row 5 (existing open + new `[x]`) makes the item done even when the new source is not the latest source, which conflicts with "note-derived status follows the latest present source" in the late-arriving-older-note case; default: row 5 wins, evidence recorded (T-M5-22, T-M5-08).
- **G-C26 BR-20 vs §10.1 non-person set.** BR-20 lists "all" as a non-person word; §10.1's observed/extra set does not. Default: `All` included in the table (T-M4-02). Also `Everyone: …` (non-person label with colon, not a known person) is no longer an owner clause under the new rule-1 gate; it falls through to "no owner" (T-M4-03).
- **G-C27 Config changes that do not alter file hashes.** G-08 covers owner re-resolution and `W-STATUS-CONFLICT`, but `date_order`, `dup_window_days` and `fuzzy_threshold` changes cannot take effect for unchanged files (FR-09, §10.7.1). Default: documented limitation, rebuild into an empty DB (T-M5-05).
- **G-C28 Auto-alias from provisional merge vs BR-06.** §10.2a(d) keeps the merged provisional's name key as a `PersonAlias` "so later occurrences resolve directly". For a bare first-name provisional (`Priya`) merged into the only known Priya, that alias would later pin every bare `Priya` to that person even after a namesake appears, contradicting BR-06 and §10.3 step 3. Default: such auto-aliases (first-name-only or initial-form keys shared by ≥2 people) are recorded but ignored by step 1 (T-M4-05).

Sample/spec hygiene: no sample line exercises provisional people, diacritics, qualifiers, roster aliases that map to two people, or `W-ATTENDEE-NAME-INCOMPLETE`; synthetic fixtures cover them (T-M4-14, T-M5-21). The plan header should be checked for its spec version.

---

## Re-sync log (spec v0.6)

Re-read before editing: spec §4.2, §6.4, §7.2, §8.1–8.3, §10 (all), §10.7, Appendix A.3/A.4/B/C/E (incl. E.2), AC-01/03/05/07/08/09, SC-41–72; index §4.4; plan §2.3/lock lines. No application code written; no other file touched.

**Changed tasks (rewritten in place, IDs unchanged), with reason:**
- T-M4-01: G-30 corner cases (single-token key, initial form has no identity key, `provisional_key`, fold applied identically to roster/attendee/owner).
- T-M4-02: provenance enum now exactly spec §7.2 (`initial_form`, `provisional` added by G-26); `Person` fields; `All` per BR-20.
- T-M4-03: G-23 rule order locates the clause; leading `Name:` only for known/`@` names; spans feed `task_text` (G-12).
- T-M4-04: rules 4–6 operate inside the located clause (G-23); co-owner span removal for `task_text`.
- T-M4-05: G-30(d) provisional merge keeps alias; single-token/initial provisional keys; auto-alias vs BR-06 default (G-C28).
- T-M4-06: attendees pass steps 1, 2, 4 before creating a Person (G-30); `W-ATTENDEE-NAME-INCOMPLETE` now in Appendix B (G-20); function reused by re-resolution.
- T-M4-07: schema additions are spec text (G-11), verify instead of request; `merge_person` primitive.
- T-M4-08: provenance `initial_form` (G-26); roster alias of bare first name (AC-05.17); initial form has no identity.
- T-M4-09: 3a only when |C|>=2, `unique_first_name` otherwise (G-26, AC-05.18); G-C4 constant removed.
- T-M4-10: step 4 now specified (G-30); provisional key rules (G-30).
- T-M4-11: rewritten as the mandatory whole-DB re-resolution pass (G-08, AC-05.8, AC-05.17); no switch/fingerprint; no-undo contract (H-05); attendee refresh default (G-C20).
- T-M4-12: `--ambiguous` default open, report hook open-only (G-19, G-25, AC-05.14).
- T-M4-13: clarified initial-form query behaviour per §10.5 (no content change otherwise).
- T-M4-14: provenance distribution assertions (AC-05.18), label-prefix synthetic cases, open-vs-all counts.
- T-M5-01: link storage `from < to`, `completed_by` directional, schema verified against §7.2, `completion_evidence_source_id` (G-C21), fingerprint columns withdrawn.
- T-M5-02: event enum exactly §7.2, `ACTIONTRACK_NOW`, new business columns in `touch_item`.
- T-M5-03: no behaviour change; test note on dot-files only.
- T-M5-04: process lock `<db>.lock` via `store/lock.py` (G-14, AC-08.9, SC-67); interrupted only after lock acquired; dry-run takes no lock.
- T-M5-05: report definitions (empty/failed never unchanged, G-25/G-28), fingerprint withdrawn, G-C27 residual, whole-DB passes not skipped.
- T-M5-06: core from `task_text`; §8.3 token splitting (hyphen/apostrophe rules changed, G-12).
- T-M5-07: `description` vs `task_text`, verbatim `raw_text`/`block_hash` (G-12/G-22), `W-MEETING-DATE-FUTURE` computed in service (G-20), SC-63/64/69 persistence, AC-01.19.
- T-M5-08: Tier-1 on `task_text` core; merge goes through canonical-field selection; evidence recorded; H-05 no-undo; due-compat/with defaults.
- T-M5-09: spec link-reason precedence (G-27), symmetric storage; precedence tests per adjacent pair.
- T-M5-10: evidence holder not flagged `CI`, `completed_by` only from non-holders, tie by highest item id, evidence recorded, counted as merged-duplicates (G-31); 11 holders/5 `CI`.
- T-M5-11: rewritten: spec reopen rule (G-05, AC-07.16), manual sticky, `W-STATUS-CONFLICT` as whole-DB pass (G-08).
- T-M5-12: `present=false` rows in pairing (G-24), no separate revive pass, canonical rebuild after edits, restore half of AC-08.10.
- T-M5-13: `--no-recursive` scope rule is spec (G-24), AC-08.10/SC-68 tests.
- T-M5-14: finalisation runs whole-DB re-resolution and conflict check on every ingest, all folder roots; counters; attendance state count.
- T-M5-15: spec §4.2 definitions; sections state-scoped, open-only, whole DB (equal to `list`); H-05 report note (G-C23).
- T-M5-16: AC-09.4 determinism masking, `--quiet` keeps summary (AC-09.9), run-scoped labels.
- T-M5-17: no lock in dry-run, both whole-DB passes computed on the copy.
- T-M5-18: lock-held exit 4, SC-69 exit expectations, W-* never change code.
- T-M5-19: real-process kill covers AC-08.9 recovery; xfail now cites §10.7.10/R-18; restore case no longer a gap.
- T-M5-20: `CI` only on non-holders (5), holders 11, SC-65–72 cross-refs, 33+1+1 second-run report, Priya Shah naming, open-only section counts, AC-05.18 count.

**New tasks:** T-M4-15 (persist re-resolution outcomes), T-M5-21 (whole-DB passes acceptance), T-M5-22 (canonical field selection).

**ACs newly owned (previously no task):**
- AC-05.17 → T-M4-11 (pure), T-M4-15 (DB), T-M5-14 (orchestrator), **T-M5-21 (acceptance)**
- AC-05.18 → **T-M4-09** (provenance and 36-count), T-M4-08 (`initial_form`), T-M4-14, T-M5-14/20 (state count)
- AC-05.19 → **T-M4-15** (guard/flag), T-M5-15 (report note), **T-M5-21 (acceptance)**
- AC-07.15 → **T-M5-22**
- AC-07.16 → **T-M5-11** (with T-M5-10 evidence, T-M5-12 pairing)
- AC-08.9 → **T-M5-04** (with T-M5-19 real-process kill; lock module T-M1-11)
- AC-08.10 → **T-M5-13** (no-recursive half) and **T-M5-12** (restore half)
- Also newly pinned: AC-05.8 on an existing DB (T-M5-21), AC-03.6 unchanged-note (T-M5-11/14/21), AC-01.19 (T-M5-07), SC-63/64/69 persistence halves (T-M5-07), SC-65 (T-M5-21), SC-66 (T-M5-11), SC-67 (T-M5-04), SC-68 (T-M5-12/13), SC-72 (T-M5-21).

**Gap register:** G-C1, G-C2, G-C3, G-C4, G-C5, G-C7, G-C9, G-C10, G-C11, G-C14, G-C16, G-C17 RESOLVED; G-C6, G-C8, G-C12, G-C13, G-C15, G-C19 PARTLY RESOLVED (residuals listed); G-C18 OPEN; new G-C20–G-C28 OPEN (defaults adopted).
