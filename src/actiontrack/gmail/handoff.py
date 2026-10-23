"""Handoff channel (phase 1): file format, atomic writer and ``HandoffGateway`` (T-M7-10/11).

actiontrack never calls Gmail on this channel. ``HandoffGateway.create_draft`` writes
``<handoff_dir>/<UTC timestamp>-<person-id>.json`` and returns ``pending_handoff``; the host
session's connector then creates the draft from ``create_draft_args`` (create_draft only).

File structure (exactly these keys): ``version, channel, created_at, person_id, item_ids,
item_ids_hash, as_of, create_draft_args{to[1], subject, body, includeSignature}, args_sha256``.

This module imports nothing network-capable.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from actiontrack.errors import EnvError
from actiontrack.gmail.gateway import DraftRequest, DraftResult, args_sha256, canonical_json
from actiontrack.io import write_private_file

HANDOFF_VERSION = 1


def safe_person_slug(person_id: str) -> str:
    """ASCII, filename-safe slug; non-ASCII ids fall back to a hex digest prefix of the NFC name."""
    nfc = unicodedata.normalize("NFC", person_id)
    if nfc.isascii() and re.fullmatch(r"[A-Za-z0-9._-]+", nfc):
        return nfc
    return "p-" + hashlib.sha1(nfc.encode("utf-8")).hexdigest()[:10]


def build_handoff_document(
    *,
    created_at: datetime,
    person_id: str,
    item_ids: list[int],
    item_ids_hash: str,
    as_of: date,
    request: DraftRequest,
) -> dict[str, object]:
    args = request.to_create_draft_args()
    return {
        "version": HANDOFF_VERSION,
        "channel": "handoff",
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "person_id": person_id,
        "item_ids": sorted(item_ids),
        "item_ids_hash": item_ids_hash,
        "as_of": as_of.isoformat(),
        "create_draft_args": args,
        "args_sha256": args_sha256(args),
    }


def write_handoff_file(handoff_dir: Path, created_at: datetime, person_id: str, document: dict[str, object]) -> Path:
    """Atomic write (temp file in the same directory + ``os.replace``). Any OS failure -> ``EnvError``
    (exit 4); no partial file or temp file remains."""
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    slug = safe_person_slug(person_id)
    text = canonical_json(document) + "\n"
    tmp: Path | None = None
    try:
        handoff_dir.mkdir(parents=True, exist_ok=True)
        target = handoff_dir / f"{stamp}-{slug}.json"
        n = 2
        while target.exists():
            target = handoff_dir / f"{stamp}-{slug}-{n}.json"
            n += 1
        tmp = handoff_dir / (target.name + ".tmp")
        write_private_file(tmp, text)
        os.replace(tmp, target)
        tmp = None
        return target
    except OSError as exc:
        raise EnvError(
            f"cannot write the handoff file: {exc.strerror or exc}",
            where=str(handoff_dir),
            next_step="choose a writable [gmail] handoff_dir; nothing was prepared",
        ) from exc
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


class HandoffGateway:
    channel = "handoff"

    def __init__(
        self,
        handoff_dir: Path,
        *,
        created_at: datetime,
        person_id: str,
        item_ids: list[int],
        item_ids_hash: str,
        as_of: date,
    ) -> None:
        self.handoff_dir = Path(handoff_dir)
        self.created_at = created_at
        self.person_id = person_id
        self.item_ids = list(item_ids)
        self.item_ids_hash = item_ids_hash
        self.as_of = as_of
        self.document: dict[str, object] | None = None
        self.calls: list[object] = []  # always empty: no connector call is ever made

    def create_draft(self, request: DraftRequest) -> DraftResult:
        doc = build_handoff_document(
            created_at=self.created_at,
            person_id=self.person_id,
            item_ids=self.item_ids,
            item_ids_hash=self.item_ids_hash,
            as_of=self.as_of,
            request=request,
        )
        path = write_handoff_file(self.handoff_dir, self.created_at, self.person_id, doc)
        self.document = doc
        return DraftResult("pending_handoff", detail=str(path))
