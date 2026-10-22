"""T-M2-16: persistence-order key."""

import datetime as dt
import random

from actiontrack import codes
from actiontrack.domain.models import FileFailure, MeetingMeta, ParsedFile, ingest_order_key

D = dt.date


def _pf(rel, date=None, status="ok"):
    meeting = MeetingMeta(date, "date_line", None, None) if date else None
    failure = FileFailure(codes.E_BINARY, "m") if status == "failed" else None
    return ParsedFile(rel, status, failure, meeting=meeting)


def _sample():
    return [
        _pf("2026-09-30-standup.md", D(2026, 9, 30)),
        _pf("untitled.md", status="failed"),
        _pf("2026-09-01-quote-portal-kickoff.md", D(2026, 9, 1)),
        _pf("2026-09-30-q4-offsite.md", D(2026, 9, 30)),
        _pf("scratch.md", status="failed"),
        _pf("2026-10-03-sales-pipeline.md"),
        _pf("2026-09-09-notes-final-v2.md", status="empty"),
        _pf("2026-09-15-x.md", D(2026, 9, 15)),
    ]


def test_sample_order():
    names = [p.rel_path for p in sorted(_sample(), key=ingest_order_key)]
    assert names[0] == "2026-09-01-quote-portal-kickoff.md"
    assert names[1:4] == ["2026-09-15-x.md", "2026-09-30-q4-offsite.md", "2026-09-30-standup.md"]
    assert names[4:] == [
        "2026-09-09-notes-final-v2.md", "2026-10-03-sales-pipeline.md", "scratch.md", "untitled.md",
    ]


def test_date_beats_path():
    files = [_pf("2026-09-02-b.md", D(2026, 9, 2)), _pf("zzz.md", D(2026, 9, 1))]
    assert [p.rel_path for p in sorted(files, key=ingest_order_key)] == ["zzz.md", "2026-09-02-b.md"]


def test_path_is_ordinal():
    files = [_pf("a.md"), _pf("B.md")]
    assert [p.rel_path for p in sorted(files, key=ingest_order_key)] == ["B.md", "a.md"]


def test_shuffle_stable():
    base = sorted(_sample(), key=ingest_order_key)
    rng = random.Random(1234)
    for _ in range(20):
        items = _sample()
        rng.shuffle(items)
        assert sorted(items, key=ingest_order_key) == base
