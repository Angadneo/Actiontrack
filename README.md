# actiontrack

A small, local command-line tool that turns Markdown meeting notes into tracked action items:

1. **`ingest`** reads a folder of `.md` meeting notes and stores every checkbox action item with its owner,
   due date (relative dates like "by Friday" resolved against the meeting date), status and source location.
2. **`list`** shows items, with filters (`--owner`, `--status open|done|overdue|all`, `--due-before`).
3. **`done`** marks an item done.
4. **`followup`** prepares a follow-up e-mail per person covering their open and overdue items, as a
   **handoff file** for a Gmail draft. **actiontrack never sends e-mail** and never calls Gmail itself.

Design stance: when the tool cannot be sure (an owner, a date, a duplicate) it keeps the item, flags what is
unresolved, and never guesses or discards. Your notes are read-only inputs.

## Requirements

- Windows, macOS or Linux, **Python 3.12+** (on Windows: `winget install Python.Python.3.12`)
- The only runtime dependency is `tzdata` (time zones on Windows)

## Install / run

```powershell
# Option A - install the command (isolated): needs `pipx`
pipx install .                    # then: actiontrack --help

# Option B - run from the source tree without installing
$env:PYTHONPATH = "src"
python -m actiontrack --help
```

## Quick start (about one minute)

The repository ships sample notes (`tests/fixtures/notes`) and an example config with a sample roster.

```powershell
.\scripts\demo.ps1               # runs the whole flow below in a temp folder
```

Or by hand:

```powershell
$cfg = "actiontrack.example.toml"        # roster + settings (copy to actiontrack.toml for your own)
python -m actiontrack --config $cfg --today 2026-10-22 ingest tests\fixtures\notes
python -m actiontrack --config $cfg --today 2026-10-22 list
python -m actiontrack --config $cfg --today 2026-10-22 list --owner "Priya Nair" --status overdue
python -m actiontrack --config $cfg --today 2026-10-22 done AT-0008
python -m actiontrack --config $cfg --today 2026-10-22 followup "Priya Nair" --dry-run   # preview only
python -m actiontrack --config $cfg --today 2026-10-22 followup "Priya Nair" --yes       # writes the handoff file
```

(`ingest` exits 5 on the sample because one file, `scratch.md`, is binary: it is skipped and reported, the
others are ingested. `--today` pins "today" for reproducible output; omit it normally.)

## Commands

| Command | What it does | Notable exit codes |
|---|---|---|
| `ingest <folder> [--no-recursive] [--dry-run]` | Parse all `.md` files (subfolders included), store items, print a report of imported items, duplicates, unresolved owners, missing dates, failures | 0 ok, 5 some file failed, 4 environment error |
| `list [--owner NAME] [--status ...] [--due-before DATE] [--ambiguous] [--wide] [--format json]` | Show items sorted by due date; `overdue` = open and due before today | 3 no database yet, 6 ambiguous owner name |
| `done <id>` | Mark an item done (`AT-0012`, `12` and `at-12` are all accepted); repeating it is harmless | 3 unknown id |
| `followup <owner> [--dry-run] [--yes] [--force]` | Preview the e-mail, then write a handoff file for a Gmail draft | 6 ambiguous owner, 7 no e-mail address, 9 cancelled |
| `followup --record-draft <file> --draft-id <id>` | Record that the host session created the draft | |
| `gmail-login` | Only needed for the future direct Google channel; a no-op for the handoff channel | |

Global options (before or after the command): `--config`, `--db` (default `./.actiontrack/actiontrack.db`),
`--today`, `--format table|json`, `--quiet`, `--verbose`, `--log-file`. Run any command with `--help`.

Full exit-code table: spec section 4.1 (`0` ok, `2` usage, `3` not found, `4` environment, `5` partial ingest,
`6` ambiguous owner, `7` follow-up precondition, `8` draft not created, `9` cancelled).

## Owners: First + Last name

People are identified by **first name + last name** (see the roster in the config). A bare first name shared by
two people ("Priya" with Priya Nair and Priya Shah) is only resolved when exactly one of them attended that
meeting; otherwise the item is flagged **ambiguous** (`?Priya (Nair | Shah)`, flag `OA`) and kept visible
(`list --ambiguous`). Queries such as `--owner Priya` are refused with the candidates listed.

## Follow-up drafts (never sent)

`followup` prints a preview and, once you confirm, writes a *handoff file* under `.actiontrack/handoff/` with the
exact arguments for a Gmail `create_draft` call (recipient from the roster, subject, plain-text body) and a
SHA-256 checksum. **No draft exists in Gmail until the Claude Code session that has the Gmail connector
creates it from that file** (using `create_draft` only; never send). Afterwards run
`followup --record-draft <file> --draft-id <id>` to record it. The handoff file contains the e-mail body:
keep `.actiontrack/` out of git (it is in `.gitignore`).

## Configuration

Copy `actiontrack.example.toml` to `actiontrack.toml` (git-ignored) or pass `--config`. Set `timezone`
(recommended), the roster (first name, last name, e-mail, aliases), and optional limits. Each person needs both
names; people without an e-mail can be tracked but not addressed by `followup`.

## Development

```powershell
python -m pytest -q              # ~8 minutes; no network needed (a socket guard blocks it)
```

- `CLAUDE.md` - architecture and conventions; `specs/` - requirements (`actiontrack-spec.md`), plan, and the
  task breakdown with every spec/plan decision.
- Tests that prove an acceptance criterion are tagged `@pytest.mark.ac("AC-xx.y")`.
- Sample notes in `samples/notes` + `notes/notes` are read-only fixtures; copies live in `tests/fixtures/notes`.

## Status

Implemented: `ingest`, `list`, `done`, `followup` (handoff channel). Not implemented yet: the direct Google
Gmail channel and `gmail-login` for it (phase 2, needs a Google Cloud project), host-instruction artifacts for
creating the draft, and some hardening/acceptance suites (see `specs/actiontrack-tasks.md`).
