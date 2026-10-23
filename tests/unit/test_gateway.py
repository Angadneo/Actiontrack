"""Gateway layer: allow-list, DraftRequest, canonical hashing, FakeGateway (T-M7-06/07)."""

from __future__ import annotations

import hashlib

import pytest

from actiontrack.gmail.allowlist import ALLOWED_MCP_HOST, ALLOWED_TOOLS
from actiontrack.gmail.fake import FakeGateway
from actiontrack.gmail.gateway import (
    REFUSED_TOOL_NAMES,
    DraftRequest,
    DraftResult,
    ToolNotAllowed,
    args_sha256,
    canonical_json,
    guard_tool,
    item_ids_hash,
    validate_address,
)

REFUSED = [
    *REFUSED_TOOL_NAMES,
    "create_label",
    "list_drafts",
    "",
    "CREATE_DRAFT",
    "create_draft ",
    "get_message",
]


def test_allow_list_constants():
    assert ALLOWED_TOOLS == {"create_draft", "get_draft"}
    assert ALLOWED_MCP_HOST == "gmailmcp.googleapis.com"


@pytest.mark.parametrize("name", REFUSED)
def test_guard_refuses_everything_else(name):
    with pytest.raises(ToolNotAllowed):
        guard_tool(name)


@pytest.mark.parametrize("name", ["create_draft", "get_draft"])
def test_guard_accepts_allowed(name):
    assert guard_tool(name) == name


@pytest.mark.parametrize("name", REFUSED_TOOL_NAMES)
def test_fake_gateway_refuses_before_recording(name):
    gw = FakeGateway()
    with pytest.raises(ToolNotAllowed):
        gw.call_tool(name, {})
    assert gw.calls == []


def test_fake_gateway_one_create_call():
    gw = FakeGateway()
    res = gw.create_draft(DraftRequest("a@b.com", "S", "B"))
    assert res.status == "created" and res.draft_id
    assert [c[0] for c in gw.calls] == ["create_draft"]
    gw.assert_only_allowed_calls()
    assert FakeGateway("failed").create_draft(DraftRequest("a@b.com", "S", "B")).status == "failed"
    assert FakeGateway("unknown").create_draft(DraftRequest("a@b.com", "S", "B")).status == "unknown"


def test_created_needs_id():
    with pytest.raises(ValueError):
        DraftResult("created", "")
    with pytest.raises(ValueError):
        DraftResult("created")
    assert DraftResult("pending_handoff").draft_id is None


def test_request_args_keys_exactly():
    args = DraftRequest("a@b.com", "S\r\nBcc: x@y.com", "B", True).to_create_draft_args()
    assert set(args) == {"to", "subject", "body", "includeSignature"}
    assert args["to"] == ["a@b.com"] and args["includeSignature"] is True
    assert "\n" not in args["subject"] and "\r" not in args["subject"]


@pytest.mark.parametrize(
    "bad", ["a@b.com, c@d.com", "x@y.com\r\nBcc: z@z.com", "a@b.com;c@d.com", "<a@b.com>", "no-at", "a b@c.com", ""]
)
def test_address_validation_rejects(bad):
    with pytest.raises(ValueError):
        validate_address(bad)
    with pytest.raises(ValueError):
        DraftRequest(bad, "S", "B")


def test_canonical_json_vector():
    args = {"to": ["a@b.com"], "subject": "S", "body": "x 李雷", "includeSignature": False}
    text = canonical_json(args)
    assert text == '{"body":"x 李雷","includeSignature":false,"subject":"S","to":["a@b.com"]}'
    assert args_sha256(args) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert args_sha256(args) == args_sha256(dict(reversed(list(args.items()))))


def test_item_ids_hash_properties():
    base = item_ids_hash([3, 1, 2])
    assert base == item_ids_hash([1, 2, 3])
    assert base != item_ids_hash([1, 2, 3], [], True)  # the flag is part of the hash
    assert item_ids_hash([1], [5], True) != item_ids_hash([1], [6], True)
    assert item_ids_hash([1, 2], [], False) != item_ids_hash([1, 2, 3], [], False)
