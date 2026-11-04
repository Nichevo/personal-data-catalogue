"""2.26: change graphs go per day, week or month, never per hour."""
from datetime import date

from pdcat import changes
from pdcat import timebuckets as tb


def test_auto_unit_by_span():
    assert tb.auto_unit("2026-11-04T09:00:00", "2026-11-04T17:00:00") == "day"
    assert tb.auto_unit("2026-08-01", "2026-11-01") == "day"
    assert tb.auto_unit("2026-01-01", "2026-11-01") == "week"
    assert tb.auto_unit("2024-01-01", "2026-11-01") == "month"
    assert tb.unit_for(["2024-01-01", "2026-11-01"], "day") == "day"
    assert tb.unit_for([], "auto") == "day"


def test_bucket_starts_and_ranges():
    assert tb.start("2026-11-04T23:59:59+00:00", "day") == date(2026, 11, 4)
    assert tb.start("2026-11-04", "week") == date(2026, 11, 2)  # Monday
    assert tb.start("2026-11-04", "month") == date(2026, 11, 1)
    assert tb.buckets("2026-11-20", "2027-02-03", "month") == [date(2026, 11, 1), date(2026, 12, 1),
                                                               date(2027, 1, 1), date(2027, 2, 1)]
    ticks = tb.ticks("2026-01-01", "2026-12-31", "day")
    assert 1 < len(ticks) <= tb.MAX_TICKS and ticks[0] == date(2026, 1, 1)
    assert tb.label("2026-11-02", "week") == "w/c 2 Nov 2026"
    assert tb.label("2026-11-02", "month") == "Nov 2026"


def test_readings_keep_last_of_each_bucket():
    rows = [{"at": "2026-11-04T08:00:00", "rows": 10, "item": "A"},
            {"at": "2026-11-04T09:00:00", "rows": 12, "item": "A"},
            {"at": "2026-11-04T10:00:00", "rows": 5, "item": "B"},
            {"at": "2026-11-05T08:00:00", "rows": 15, "item": "A"}]
    out = tb.last_per_bucket(rows, "day", series=("item",))
    assert [(r["at"], r["item"], r["rows"]) for r in out] == [("2026-11-04", "A", 12), ("2026-11-04", "B", 5),
                                                             ("2026-11-05", "A", 15)]
    month = tb.last_per_bucket(rows, "month", series=("item",))
    assert {(r["item"], r["rows"]) for r in month} == {("A", 15), ("B", 5)}


def test_change_counts_add_up_per_bucket():
    rows = [{"at": "2026-11-04T08:00:00", "category": "report", "change": "page added", "n": 1},
            {"at": "2026-11-04T19:00:00", "category": "report", "change": "page removed", "n": 1},
            {"at": "2026-11-20T10:00:00", "category": "rows", "change": "row count", "n": 3}]
    assert changes.report_parts(rows, "day") == [{"at": "2026-11-04", "part": "page", "changes": 2}]
    vol = changes.volume(rows, "month")
    assert sum(r["changes"] for r in vol) == 5 and {r["at"] for r in vol} == {"2026-11-01"}
