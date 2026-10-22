"""Deterministic JSON renderer (T-M1-08, BR-18, NFR-DET-01, AC-09.6, AC-09.8).

This module is ``actiontrack.report.json``; it imports the standard library as
``import json as _json`` with absolute imports, so it never shadows stdlib ``json``.
Pure: returns a string, never writes to a stream (use ``io.emit``).

Public API
----------
``render_json(obj) -> str``   UTF-8 text, ``ensure_ascii=False``, 2-space indent,
    ``,``/``: `` separators, terminated by ``\\n``.
    **Caller supplies ordering**: ``sort_keys`` is NOT used. Dicts are emitted in
    insertion order, so builders (list/ingest/done/followup) construct dicts in their
    documented key order and pass lists already sorted. ``set``/``frozenset`` and other
    unordered or unknown types raise ``TypeError``; so do non-str dict keys, naive
    datetimes and NaN/inf.
    ``date`` -> ``YYYY-MM-DD``; aware ``datetime`` -> UTC ``YYYY-MM-DDTHH:MM:SSZ``
    (fractional seconds only if non-zero); ``Enum`` -> its value; ``None`` -> ``null``.
    DEL, C1 (U+007F-U+009F), U+2028/2029 and lone surrogates are ESCAPED as ``\\uXXXX``
    (data fidelity for machine output, unlike the table renderer which strips them).
``envelope(as_of, timezone, items) -> dict``   the list envelope ``{as_of, timezone, items}``
    in exactly that key order; empty result gives ``"items": []`` (spec 4.3).
No clock is read here: timestamps are byte-stable only when callers feed stable values
(``ACTIONTRACK_NOW``).
"""

from __future__ import annotations

import enum
import json as _json
import math
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from datetime import timezone as _tz
from typing import Any

_ESCAPE = re.compile("[\u007f-\u009f  \ud800-\udfff]")


def _esc(m: re.Match[str]) -> str:
    return f"\\u{ord(m.group()):04x}"


def _normalise(obj: Any) -> Any:
    if isinstance(obj, enum.Enum):
        return _normalise(obj.value)
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, str):
        return str(obj)
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise TypeError("NaN/inf is not representable in deterministic JSON")
        return obj
    if isinstance(obj, datetime):
        if obj.tzinfo is None or obj.utcoffset() is None:
            raise TypeError("naive datetime: timestamps must be timezone-aware (UTC)")
        utc = obj.astimezone(_tz.utc)
        fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if utc.microsecond else "%Y-%m-%dT%H:%M:%SZ"
        return utc.strftime(fmt)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        out = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"JSON object keys must be str, got {type(k).__name__}")
            out[str(k)] = _normalise(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_normalise(v) for v in obj]
    raise TypeError(
        f"cannot render {type(obj).__name__} deterministically "
        "(sets and unordered types are rejected)"
    )


def render_json(obj: Any) -> str:
    text = _json.dumps(
        _normalise(obj),
        ensure_ascii=False,
        indent=2,
        separators=(",", ": "),
        allow_nan=False,
    )
    return _ESCAPE.sub(_esc, text) + "\n"


def envelope(as_of: date | str, timezone: str, items: Iterable[Any]) -> dict[str, Any]:
    return {"as_of": as_of, "timezone": timezone, "items": list(items)}
