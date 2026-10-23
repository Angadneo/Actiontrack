"""Plain-text follow-up email: subject and body (spec 12.4, T-M7-04/05).

Pure: no I/O, no clock, no config, no DB, no gateway. ``render_email`` returns ``(subject, body)``;
``render_email_full`` also returns the truncation facts the CLI must state.

Subject: counts of the recipient's own (non-ambiguous) items only, never note text (GM-04).
Body order: Overdue (most overdue first), Upcoming (due ascending, today included), No due date set;
ties by item id. At most ``MAX_OWN_ITEMS`` (50) own lines; the optional "may be yours" section has its
own cap of ``MAX_AMBIGUOUS_ITEMS`` (20) and never counts toward the subject or the 50-line cap.
Inserted fields are single-lined (CR/LF collapsed); line endings are ``\\n``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MAX_OWN_ITEMS = 50
MAX_AMBIGUOUS_ITEMS = 20
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # fixed table, never locale dependent


@dataclass(frozen=True, slots=True)
class EmailItem:
    item_id: int
    task_text: str
    due_date: date | None
    due_raw: str | None
    meeting_title: str | None
    meeting_date: date | None
    fallback_title: str = ""  # file stem when the meeting has no title


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    body: str
    open_count: int
    overdue_count: int
    own_listed: int
    own_more: int
    ambiguous_listed: int
    ambiguous_more: int


def single_line(text: str) -> str:
    return " ".join(re.sub(r"[\r\n]+", " ", text or "").split())


def _source(item: EmailItem) -> str:
    title = single_line(item.meeting_title or "") or single_line(item.fallback_title) or "unknown meeting"
    when = item.meeting_date.isoformat() if item.meeting_date else "date unknown"
    return f"From: {title}, {when}."


def _is_overdue(item: EmailItem, as_of: date) -> bool:
    return item.due_date is not None and item.due_date < as_of


def _sort_key(item: EmailItem) -> tuple[date, int]:
    return (item.due_date or date.max, item.item_id)


def render_email_full(
    full_name: str,
    items: list[EmailItem],
    as_of: date,
    ambiguous: list[EmailItem] | None = None,
) -> RenderedEmail:
    overdue = sorted((i for i in items if _is_overdue(i, as_of)), key=_sort_key)
    upcoming = sorted((i for i in items if i.due_date is not None and i.due_date >= as_of), key=_sort_key)
    undated = sorted((i for i in items if i.due_date is None), key=lambda i: i.item_id)
    subject = f"Follow-up: your open action items ({len(items)} open, {len(overdue)} overdue)"
    name = single_line(full_name)

    shown_overdue = overdue[:MAX_OWN_ITEMS]
    shown_upcoming = upcoming[: max(0, MAX_OWN_ITEMS - len(shown_overdue))]
    shown_undated = undated[: max(0, MAX_OWN_ITEMS - len(shown_overdue) - len(shown_upcoming))]
    listed = len(shown_overdue) + len(shown_upcoming) + len(shown_undated)
    more = len(items) - listed

    lines: list[str] = [
        f"Hi {name},",
        "",
        f"Here is a summary of your open action items from recent meetings (as of {as_of.isoformat()}).",
    ]

    def section(title: str, rows: list[str]) -> None:
        if rows:
            lines.extend(["", f"{title} ({len(rows)})", *rows])

    section(
        "Overdue",
        [
            f"- {single_line(i.task_text)} — was due {i.due_date.isoformat()} "  # type: ignore[union-attr]
            f"({(as_of - i.due_date).days} day{'' if (as_of - i.due_date).days == 1 else 's'} overdue). {_source(i)}"  # type: ignore[operator]
            for i in shown_overdue
        ],
    )
    section(
        "Upcoming",
        [
            f"- {single_line(i.task_text)} — due {i.due_date.isoformat()} "  # type: ignore[union-attr]
            f"({_WEEKDAYS[i.due_date.weekday()]}). {_source(i)}"  # type: ignore[union-attr]
            for i in shown_upcoming
        ],
    )
    section(
        "No due date set",
        [
            f"- {single_line(i.task_text)} — "
            + (
                f'due date unclear in notes ("{single_line(i.due_raw)}"). '
                if i.due_raw and single_line(i.due_raw)
                else "no due date in notes. "
            )
            + _source(i)
            for i in shown_undated
        ],
    )
    if more > 0:
        lines.extend(["", f'and {more} more — run `actiontrack list --owner "{name}"`'])

    amb_listed = amb_more = 0
    if ambiguous:
        amb = sorted(ambiguous, key=lambda i: i.item_id)
        shown = amb[:MAX_AMBIGUOUS_ITEMS]
        amb_listed, amb_more = len(shown), len(amb) - len(shown)
        lines.extend(["", "Items that may be yours — please confirm"])
        lines.extend(f"- {single_line(i.task_text)} — {_source(i)}" for i in shown)
        if amb_more:
            lines.append(
                f'and {amb_more} more items that may be yours — run '
                f'`actiontrack list --owner "{name}" --include-ambiguous`'
            )

    lines.extend(
        [
            "",
            "Could you reply with a status or a revised date for any item? "
            "If something is already done, please let me know.",
            "",
            "Thanks",
        ]
    )
    return RenderedEmail(subject, "\n".join(lines), len(items), len(overdue), listed, more, amb_listed, amb_more)


def render_email(
    full_name: str, items: list[EmailItem], as_of: date, ambiguous: list[EmailItem] | None = None
) -> tuple[str, str]:
    r = render_email_full(full_name, items, as_of, ambiguous)
    return r.subject, r.body
