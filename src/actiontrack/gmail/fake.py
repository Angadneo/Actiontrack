"""In-memory ``DraftGateway`` for tests (T-M7-07). Every call is routed through ``guard_tool``."""

from __future__ import annotations

from actiontrack.gmail.allowlist import ALLOWED_TOOLS
from actiontrack.gmail.gateway import DraftRequest, DraftResult, guard_tool


class FakeGateway:
    channel = "fake"

    def __init__(self, outcome: str = "created", draft_id: str = "r-fake-1") -> None:
        """``outcome``: ``created`` | ``failed`` | ``unknown`` | ``no_id`` (success without an id)."""
        self.outcome = outcome
        self.draft_id = draft_id
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(self, tool: str, args: dict[str, object]) -> None:
        guard_tool(tool)  # before anything is recorded or done
        self.calls.append((tool, args))

    def create_draft(self, request: DraftRequest) -> DraftResult:
        self.call_tool("create_draft", request.to_create_draft_args())
        if self.outcome == "created":
            return DraftResult("created", self.draft_id, thread_id="t-fake-1", view_url="https://example.invalid/draft")
        if self.outcome == "unknown":
            return DraftResult("unknown", detail="timeout")
        if self.outcome == "no_id":
            return DraftResult("failed", detail="response without an extractable id")
        return DraftResult("failed", detail="connector error")

    def assert_only_allowed_calls(self) -> None:
        bad = [t for t, _ in self.calls if t not in ALLOWED_TOOLS]
        assert not bad, f"disallowed tool calls: {bad}"
