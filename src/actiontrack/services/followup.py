"""``followup`` command, handoff channel (spec 4.5, FR-04.*, 12; milestone M7 happy flow).

Public API
----------
``followup_handler(ctx: CliContext) -> int | None``
    Register as ``cli.HANDLERS['followup']``. Dispatches to ``record_draft`` (``--record-draft``) or
    ``run_followup`` (``<owner>``).
``run_followup(ctx, gateway=None) -> int``
    resolve -> select -> render -> repeat check -> preview -> gate -> gateway -> record -> report.
    ``gateway`` (tests) replaces the channel gateway; otherwise the handoff gateway is built.
``record_draft(ctx) -> int``
    ``--record-draft <file> --draft-id <id> [--status created|failed]`` (FR-04.12).
``resolve_target / select_items / confirm`` building blocks; ``stdin_is_tty`` and ``read_answer`` are the
    injectable console hooks (tests monkeypatch them).

Exit codes: 0 prepared/created/dry-run/no open items, 2 usage (also: no TTY and no ``--yes``, repeat
without ``--force``, modified handoff file), 3 unknown owner / file / row, 4 handoff dir unwritable,
6 ambiguous owner, 7 no recipient, 8 gateway failure/unknown, 9 cancelled.

actiontrack never calls Gmail on the handoff channel; note text is data (GM-11). The body is never
stored in the database (GM-10).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

from pathlib import Path
from typing import TYPE_CHECKING

from actiontrack import io
from actiontrack.domain.models import OwnerStatus, Person, format_item_id
from actiontrack.errors import (
    CancelledByUser,
    DraftNotCreatedError,
    EnvError,
    NotFoundError,
    PreconditionError,
    UsageError,
)
from actiontrack.gmail.gateway import (
    DraftGateway,
    DraftRequest,
    DraftResult,
    args_sha256,
    item_ids_hash,
    validate_address,
)
from actiontrack.gmail.handoff import HandoffGateway
from actiontrack.resolution.people import PeopleRegistry, has_recipient, resolve_person_query
from actiontrack.services.email_template import (
    MAX_AMBIGUOUS_ITEMS,
    MAX_OWN_ITEMS,
    EmailItem,
    RenderedEmail,
    render_email_full,
)
from actiontrack.store import followup_queries as q
from actiontrack.store.db import Database
from actiontrack.store.repo import Repo

if TYPE_CHECKING:
    from actiontrack.cli import CliContext

OPEN_DB = getattr(Database, "open")  # noqa: B009  (the text-open lint flags a literal ``.open(``)

REPEAT_STATUSES = ("created", "created_by_host", "pending_handoff", "unknown")


# ------------------------------------------------------------------------------ console hooks
def stdin_is_tty() -> bool:
    stream = sys.stdin
    return bool(stream is not None and stream.isatty())


def read_answer(prompt: str) -> str:
    """Show ``prompt`` and read one line; EOF returns an empty answer (which declines)."""
    io.emit(prompt, end="")
    line = sys.stdin.readline() if sys.stdin is not None else ""
    return line.strip()


def _yes(answer: str) -> bool:
    return answer.strip().casefold() in ("y", "yes")


# ------------------------------------------------------------------------------ selection
@dataclass(frozen=True)
class AmbiguousItem:
    email_item: EmailItem
    candidate_names: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    own: list[EmailItem]
    ambiguous: list[AmbiguousItem]

    @property
    def own_ids(self) -> list[int]:
        return sorted(i.item_id for i in self.own)

    @property
    def ambiguous_ids(self) -> list[int]:
        return sorted(a.email_item.item_id for a in self.ambiguous)


def select_items(repo: Repo, registry: PeopleRegistry, person_id: str) -> Selection:
    """Open items of ``person_id`` (resolved/provisional owner rows, co-owners included) and, apart,
    the open items whose ambiguous owner candidates contain the person (BR-14)."""
    sources = q.first_sources(repo)
    own: list[EmailItem] = []
    ambiguous: list[AmbiguousItem] = []
    for item in repo.list_action_items(status="open"):
        owners = repo.list_item_owners(item.id)
        definite = any(o.owner_status != OwnerStatus.AMBIGUOUS.value and o.person_id == person_id for o in owners)
        maybe = [o for o in owners if o.owner_status == OwnerStatus.AMBIGUOUS.value and person_id in o.candidates]
        if not definite and not maybe:
            continue
        title, mdate, stem = sources.get(item.id, (None, None, ""))
        ei = EmailItem(
            item_id=item.id,
            task_text=item.task_text or item.description,
            due_date=item.due_date,
            due_raw=item.due_raw,
            meeting_title=title,
            meeting_date=mdate,
            fallback_title=stem,
        )
        if definite:
            own.append(ei)
        else:
            names = tuple(sorted({registry.full_name(c) if c in registry else c for o in maybe for c in o.candidates}))
            ambiguous.append(AmbiguousItem(ei, names))
    return Selection(own, ambiguous)


# ------------------------------------------------------------------------------ resolution
@dataclass(frozen=True)
class Target:
    person: Person
    full_name: str
    recipient: str | None
    recipient_source: str  # "--to" | "roster" | "none"


def resolve_target(registry: PeopleRegistry, query: str, to_flag: str | None, dry_run: bool) -> Target:
    person = resolve_person_query(query, registry)  # exit 3 / 6
    full_name = registry.full_name(person.id)
    if to_flag is not None:
        try:
            return Target(person, full_name, validate_address(to_flag), "--to")
        except ValueError as exc:
            raise UsageError(f"--to must be exactly one valid email address: {exc}") from exc
    if has_recipient(person) and str(person.email).strip():
        try:
            return Target(person, full_name, validate_address(str(person.email)), "roster")
        except ValueError:
            pass
    if dry_run:
        return Target(person, full_name, None, "none")
    raise PreconditionError(
        f'No email configured for "{full_name}".',
        next_step="Add email to roster or pass --to <address>.",
    )


# ------------------------------------------------------------------------------ helpers
def _json_mode(ctx: CliContext) -> bool:
    return ctx.format == "json"


def _say(ctx: CliContext, text: str = "") -> None:
    """Human text: stdout in table mode, stderr in json mode (stdout then carries one object)."""
    (io.diag if _json_mode(ctx) else io.emit)(text)


def _emit_json(obj: dict[str, object]) -> None:
    io.emit(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def _rel(path: Path | str) -> str:
    try:
        return os.path.relpath(path)
    except ValueError:  # different drive on Windows
        return str(path)


def _open_writer(ctx: CliContext) -> Database:
    return OPEN_DB(ctx.db_path, create=False, lock=True, clock=ctx.run.clock)


def _open_reader(ctx: CliContext) -> Database:
    if not ctx.db_path.exists():
        raise NotFoundError("database not found", where=str(ctx.db_path), next_step="run `actiontrack ingest` first")
    return OPEN_DB(ctx.db_path, readonly=True, clock=ctx.run.clock)


# ------------------------------------------------------------------------------ main flow
def followup_handler(ctx: CliContext) -> int | None:
    if getattr(ctx.args, "record_draft", None):
        return record_draft(ctx)
    return run_followup(ctx)


def _repeat_message(row) -> str:  # type: ignore[no-untyped-def]
    if row["status"] == "pending_handoff":
        return f"a handoff was already prepared on {row['created_at']}; its draft has not been recorded"
    if row["status"] == "unknown":
        return "an earlier attempt has unknown status - check Gmail Drafts first"
    return f"a draft was already created on {row['created_at']} (id {row['gmail_draft_id'] or '-'})"


def confirm(ctx: CliContext, repeat: object | None, channel: str) -> None:
    """Confirmation gate (FR-04.8, FR-04.9). Returns when the user (or ``--yes``/``--force``) agrees;
    raises ``CancelledByUser`` (exit 9) when declined and ``UsageError`` (exit 2) when it cannot ask."""
    args = ctx.args
    tty = stdin_is_tty()
    if repeat is not None:
        _say(ctx, f"Warning: {_repeat_message(repeat)}")
        if not tty:
            if not (args.yes and args.force):
                raise UsageError(
                    "repeat draft: add --force to proceed" if args.yes else "no terminal to confirm a repeat draft",
                    next_step="re-run with --yes --force to create it again",
                )
            return
        if not _yes(read_answer("Create another draft for the same items? [y/N] ")):
            raise CancelledByUser()
    if not args.yes:
        if not tty:
            raise UsageError("no terminal to confirm on", next_step="add --yes to proceed without a prompt")
        prompt = "Prepare Gmail draft handoff? [y/N] " if channel == "handoff" else "Create Gmail draft? [y/N] "
        if not _yes(read_answer(prompt)):
            raise CancelledByUser()


def run_followup(ctx: CliContext, gateway: DraftGateway | None = None) -> int:
    args = ctx.args
    as_of = ctx.run.today
    dry_run = bool(args.dry_run)
    include_ambiguous = bool(args.include_ambiguous)
    gmail = ctx.config.gmail

    db = _open_reader(ctx)
    try:
        repo = Repo(db, ctx.run.clock)
        registry = repo.load_registry(ctx.config.roster)
        target = resolve_target(registry, args.owner, args.to, dry_run)
        sel = select_items(repo, registry, target.person.id)
        name = target.full_name

        amb_lines = [
            f"  {format_item_id(a.email_item.item_id)}  {a.email_item.task_text}  ({' | '.join(a.candidate_names)})"
            for a in sel.ambiguous
        ]

        def summary() -> None:
            if sel.ambiguous:
                _say(ctx, f"{len(sel.ambiguous)} items with ambiguous owner may belong to {name}")
                for line in amb_lines:
                    _say(ctx, line)

        pid = target.person.id
        if not sel.own:
            _say(ctx, f"no open items for {name}")
            summary()
            if _json_mode(ctx):
                _emit_json({"status": "no_open_items", "person_id": pid, "item_ids": []})
            return 0

        amb_for_email = [a.email_item for a in sel.ambiguous] if include_ambiguous else None
        rendered: RenderedEmail = render_email_full(name, sel.own, as_of, amb_for_email)
        used_amb_ids = sel.ambiguous_ids if include_ambiguous else []
        ids_hash = item_ids_hash(sel.own_ids, used_amb_ids, include_ambiguous)
        repeat = None if dry_run else q.repeat_row(repo, pid, ids_hash, REPEAT_STATUSES)
        pending_other = (not dry_run) and q.has_other_pending(repo, pid, ids_hash)
    finally:
        db.close()

    # ---- preview (always printed; stdout in table mode)
    email = target.recipient or "(none - dry run)"
    _say(ctx, f"Owner: {name} <{target.person.email or 'no email'}>   Items: {rendered.open_count} open ({rendered.overdue_count} overdue)")
    _say(ctx, f"To: {email} ({'from --to' if target.recipient_source == '--to' else 'roster email' if target.recipient_source == 'roster' else 'no recipient'})")
    summary()
    if rendered.own_more:
        _say(ctx, f"Note: only {MAX_OWN_ITEMS} of {rendered.open_count} items are listed; {rendered.own_more} more are not in the email")
    if rendered.ambiguous_more:
        _say(ctx, f"Note: only {MAX_AMBIGUOUS_ITEMS} ambiguous items are listed; {rendered.ambiguous_more} more are not in the email")
    _say(ctx, "--- DRAFT PREVIEW (not yet created) ---")
    _say(ctx, f"Subject: {rendered.subject}")
    _say(ctx, "")
    _say(ctx, rendered.body)
    _say(ctx, "")

    if dry_run:
        if _json_mode(ctx):
            _emit_json(
                {"status": "dry_run", "person_id": pid, "item_ids": sel.own_ids, "item_ids_hash": ids_hash}
            )
        return 0

    assert target.recipient is not None
    request = DraftRequest(target.recipient, rendered.subject, rendered.body, bool(gmail.include_signature))
    created_at = ctx.run.clock.now_utc()
    if gateway is None:
        if gmail.channel != "handoff":
            raise DraftNotCreatedError(
                f"the {gmail.channel!r} channel is not available in this build; Draft NOT created",
                next_step='set [gmail] channel = "handoff"',
            )
        gateway = HandoffGateway(
            gmail.handoff_dir,
            created_at=created_at,
            person_id=pid,
            item_ids=sel.own_ids,
            item_ids_hash=ids_hash,
            as_of=as_of,
        )
    channel = gateway.channel

    if pending_other:
        io.diag("note: a handoff for this owner is still pending (its draft was not recorded)")
    try:
        confirm(ctx, repeat, channel)
    except CancelledByUser:
        if _json_mode(ctx):
            _emit_json({"status": "cancelled", "person_id": pid})
        raise

    wdb = _open_writer(ctx)
    try:
        wrepo = Repo(wdb, ctx.run.clock)
        if channel == "handoff":
            _refuse_notes_dir(wrepo, Path(gmail.handoff_dir))
        try:
            result: DraftResult = gateway.create_draft(request)
        except (EnvError, UsageError):
            raise
        except Exception as exc:  # noqa: BLE001 - outcome after a sent request is unknown, never retried
            if channel == "handoff":
                raise
            result = DraftResult("unknown", detail=str(exc))
        values: dict[str, object] = {
            "person_id": pid,
            "channel": channel,
            "item_ids_hash": ids_hash,
            "item_ids": json.dumps(sel.own_ids),
            "include_ambiguous": int(include_ambiguous),
            "ambiguous_item_ids": json.dumps(used_amb_ids),
            "source": "actiontrack",
            "status": result.status,
            "created_at": wrepo.now_iso(),
            "gmail_draft_id": result.draft_id,
            "thread_id": result.thread_id,
            "view_url": result.view_url,
        }
        if channel == "handoff":
            values["handoff_path"] = os.path.abspath(str(result.detail))
            values["args_sha256"] = args_sha256(request.to_create_draft_args())
        with wdb.transaction():
            wrepo.insert_row("followup_draft", values)
    finally:
        wdb.close()

    return _report(ctx, result, channel, pid, sel.own_ids, ids_hash, request)


def _refuse_notes_dir(repo: Repo, handoff_dir: Path) -> None:
    target = Path(os.path.realpath(handoff_dir))
    for folder_root in q.folder_roots(repo):
        root = Path(os.path.realpath(folder_root))
        if target == root or root in target.parents:
            raise EnvError(
                "handoff_dir is inside an ingested notes folder",
                where=str(handoff_dir),
                next_step="choose a [gmail] handoff_dir outside your notes",
            )


def _report(
    ctx: CliContext,
    result: DraftResult,
    channel: str,
    pid: str,
    item_ids: list[int],
    ids_hash: str,
    request: DraftRequest,
) -> int:
    from actiontrack.errors import EXIT_HANDOFF_PREPARED

    if result.status == "pending_handoff":
        path = str(result.detail)
        sha = args_sha256(request.to_create_draft_args())
        if _json_mode(ctx):
            _emit_json(
                {
                    "status": "pending_handoff",
                    "person_id": pid,
                    "item_ids": item_ids,
                    "item_ids_hash": ids_hash,
                    "handoff_file": path,
                    "args_sha256": sha,
                }
            )
        else:
            io.emit("Handoff prepared. Draft NOT yet created in Gmail.")
            io.emit(f"Handoff file: {_rel(path)}")
            io.emit("Next: have the host session's Gmail connector call create_draft with the file's create_draft_args unchanged,")
            io.emit("      then run: actiontrack followup --record-draft <file> --draft-id <id>")
            io.emit("Ask the Claude session to create the draft from this file with create_draft only; never send.")
            io.emit("NOT SENT. Nothing exists in Gmail yet.")
        io.diag("hint: add .actiontrack/ to .gitignore; the handoff file holds the recipient and the email body")
        return int(EXIT_HANDOFF_PREPARED)
    if result.status == "created":
        if _json_mode(ctx):
            _emit_json(
                {"status": "created", "person_id": pid, "item_ids": item_ids, "item_ids_hash": ids_hash, "draft_id": result.draft_id}
            )
        else:
            io.emit(f"Draft created (id: {result.draft_id}, thread: {result.thread_id or '-'}). Open: {result.view_url or '-'}")
            io.emit("NOT SENT. Review and send manually from Gmail Drafts.")
        return 0
    if _json_mode(ctx):
        _emit_json({"status": result.status, "person_id": pid, "item_ids": item_ids, "item_ids_hash": ids_hash})
    if result.status == "unknown":
        raise DraftNotCreatedError(
            "Draft status unknown - check Gmail Drafts before re-running", next_step="no automatic retry was made"
        )
    raise DraftNotCreatedError("Draft NOT created", where=result.detail, next_step="no automatic retry was made")


# ------------------------------------------------------------------------------ --record-draft
_DRAFT_ID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-")


def record_draft(ctx: CliContext) -> int:
    args = ctx.args
    for flag, given in (
        ("<owner>", args.owner),
        ("--to", args.to),
        ("--include-ambiguous", args.include_ambiguous),
        ("--dry-run", args.dry_run),
        ("--yes", args.yes),
        ("--force", args.force),
    ):
        if given:
            raise UsageError(f"--record-draft cannot be combined with {flag}")
    status = args.status or "created"
    draft_id = args.draft_id
    if status == "created":
        if not draft_id:
            raise UsageError("--status created needs --draft-id <id>")
        if len(draft_id) > 128 or not set(draft_id) <= _DRAFT_ID_CHARS:
            raise UsageError("--draft-id must be 1-128 characters of A-Z a-z 0-9 _ . : -")
    elif draft_id:
        raise UsageError("--status failed does not take --draft-id")

    path = Path(args.record_draft)
    if not path.is_file():
        raise NotFoundError("handoff file not found", where=str(path))
    try:
        doc = json.loads(io.read_text_utf8(path))
        call_args = doc["create_draft_args"]
        file_sha = doc["args_sha256"]
        if doc["version"] != 1 or not isinstance(call_args, dict):
            raise ValueError("unsupported handoff file")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise UsageError(f"not a valid handoff file: {exc}", where=str(path)) from exc

    db = _open_writer(ctx)
    try:
        repo = Repo(db, ctx.run.clock)
        abs_path = os.path.abspath(path)
        row = q.draft_by_path(repo, abs_path)
        if row is None:
            row = q.draft_by_args_sha(repo, file_sha)
        if row is None:
            raise NotFoundError("no prepared handoff matches this file", where=str(path))
        if args_sha256(call_args) != file_sha or file_sha != row["args_sha256"]:
            raise UsageError("Handoff file was modified after it was prepared; refusing to record", where=str(path))
        if row["status"] != "pending_handoff":
            raise UsageError(f"this handoff was already recorded ({row['status']})", where=str(path))
        values: dict[str, object] = {"recorded_at": repo.now_iso(), "source": "host"}
        if status == "created":
            values.update(status="created_by_host", gmail_draft_id=draft_id)
        else:
            values.update(status="failed_by_host")
        with db.transaction():
            repo.update_row("followup_draft", "id", row["id"], values)
        pid = row["person_id"]
    finally:
        db.close()

    new_status = "created_by_host" if status == "created" else "failed_by_host"
    if _json_mode(ctx):
        _emit_json(
            {"status": new_status, "person_id": pid, "draft_id": draft_id, "handoff_file": str(abs_path), "args_sha256": file_sha}
        )
    elif status == "created":
        io.emit(f"Recorded draft {draft_id} (created by host connector; not verified by actiontrack)")
    else:
        io.emit("Recorded host failure. No draft was created.")
    return 0


__all__ = ["followup_handler", "run_followup", "record_draft", "select_items", "resolve_target", "confirm"]