"""Draft gateway interface, allow-list guard, request model and canonical hashing (T-M7-06/07).

Public API
----------
``DraftRequest(to, subject, body, include_signature)``  frozen; exactly the four connector arguments
    (no cc/bcc/attachments/reply-to exist, GM-03). ``to_create_draft_args()`` -> the ``create_draft``
    argument object (``to`` is a one-element list).
``DraftResult(status, draft_id, thread_id, view_url, detail)``  ``created`` needs a non-empty id (GM-07).
``DraftGateway``  protocol: ``channel`` attribute, ``create_draft(request) -> DraftResult``.
``guard_tool(name)``  raises ``ToolNotAllowed`` for any tool outside the hard-coded allow-list
    (``allowlist.ALLOWED_TOOLS``: ``create_draft`` and ``get_draft`` only) before any I/O (GM-02).
``canonical_json / args_sha256 / item_ids_hash``  the only serialiser used for both hashes (FR-04.11).
``validate_address(text)``  one syntactically valid address, or ``ValueError``.

No send-capable tool is referenced here except in ``REFUSED_TOOL_NAMES`` (documentation of the
refusal, used by tests); the guard itself is a pure allow-list test.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Protocol

from actiontrack.errors import ActionTrackError, ExitCode
from actiontrack.gmail.allowlist import ALLOWED_TOOLS

#: Names that must always be refused (documentation + test fixture; never called).
REFUSED_TOOL_NAMES: tuple[str, ...] = (
    "send_message",
    "reply",
    "forward",
    "update_draft",
    "delete_draft",
    "trash_message",
    "trash_thread",
    "untrash_message",
    "untrash_thread",
    "mark_message_spam",
    "mark_thread_spam",
    "unmark_message_spam",
    "unmark_thread_spam",
)


class ToolNotAllowed(ActionTrackError):
    exit_code = ExitCode.INTERNAL_ERROR


def guard_tool(name: str) -> str:
    """Return ``name`` when it is exactly an allowed tool, else raise ``ToolNotAllowed``."""
    if not isinstance(name, str) or name not in ALLOWED_TOOLS:
        raise ToolNotAllowed(
            f"Gmail tool {name!r} is not on the allow-list",
            next_step="only create_draft/get_draft are permitted",
        )
    return name


_ADDRESS = re.compile(r"^[^\s@,;<>\"()\[\]\\]+@[^\s@,;<>\"()\[\]\\]+\.[^\s@,;<>\"()\[\]\\]+$")


def validate_address(text: str) -> str:
    """One conservative single address: single ``@``, no whitespace/``,``/``;``/``<``/``>``/CR/LF."""
    value = text.strip() if isinstance(text, str) else ""
    if not value or value.count("@") != 1 or not _ADDRESS.match(value) or parseaddr(value)[1] != value:
        raise ValueError(f"not a single valid email address: {text!r}")
    return value


def _one_line(text: str) -> str:
    return " ".join(re.sub(r"[\r\n]+", " ", text).split())


@dataclass(frozen=True, slots=True)
class DraftRequest:
    to: str
    subject: str
    body: str
    include_signature: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "to", validate_address(self.to))
        object.__setattr__(self, "subject", _one_line(self.subject))
        if not self.subject:
            raise ValueError("subject must not be empty")
        if not self.body.strip():
            raise ValueError("body must not be empty")

    def to_create_draft_args(self) -> dict[str, object]:
        return {
            "to": [self.to],
            "subject": self.subject,
            "body": self.body,
            "includeSignature": bool(self.include_signature),
        }


STATUSES = ("created", "failed", "unknown", "pending_handoff")


@dataclass(frozen=True, slots=True)
class DraftResult:
    status: str
    draft_id: str | None = None
    thread_id: str | None = None
    view_url: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown draft status {self.status!r}")
        if self.status == "created" and not (isinstance(self.draft_id, str) and self.draft_id):
            raise ValueError("a created draft needs a non-empty draft id (GM-07)")


class DraftGateway(Protocol):
    channel: str

    def create_draft(self, request: DraftRequest) -> DraftResult: ...


def canonical_json(obj: object) -> str:
    """UTF-8 text, sorted keys, ``,``/``:`` separators, non-ASCII not escaped (FR-04.11)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def args_sha256(create_draft_args: dict[str, object]) -> str:
    return _sha256(canonical_json(create_draft_args))


def item_ids_hash(
    item_ids: Iterable[int], ambiguous_item_ids: Iterable[int] = (), include_ambiguous: bool = False
) -> str:
    """Hash of ids only (not text or dates); the flag is part of it (AC-04.18)."""
    return _sha256(
        canonical_json(
            {
                "ambiguous_item_ids": sorted(int(i) for i in ambiguous_item_ids),
                "include_ambiguous": bool(include_ambiguous),
                "item_ids": sorted(int(i) for i in item_ids),
            }
        )
    )
