"""Put dated readings into day, week or month buckets for the graphs, so a graph never shows hours.

Pure Python (no pandas): the graph pages turn the result into a data frame."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

UNITS = ("day", "week", "month")
UNIT_LABELS = {"day": "Day", "week": "Week", "month": "Month"}
# Vega date formats for the axis and tooltips.
AXIS_FORMAT = {"day": "%-d %b", "week": "%-d %b", "month": "%b %Y"}
TOOLTIP_FORMAT = {"day": "%a %-d %b %Y", "week": "week of %-d %b %Y", "month": "%B %Y"}
MAX_TICKS = 12


def to_date(at: Any) -> date:
    """The calendar day of an ISO time or date (the date as written, so no time-zone shift)."""
    if isinstance(at, date):
        return at
    return date.fromisoformat(str(at)[:10])


def auto_unit(first: Any, last: Any) -> str:
    """Day for up to about three months, week up to about 13 months, then month."""
    span = (to_date(last) - to_date(first)).days
    if span <= 92:
        return "day"
    if span <= 400:
        return "week"
    return "month"


def unit_for(dates: Iterable[Any], pick: str = "auto") -> str:
    """`pick` when it is a unit, else the automatic unit for the span of `dates`."""
    if pick in UNITS:
        return pick
    ds = [to_date(d) for d in dates if d]
    return auto_unit(min(ds), max(ds)) if ds else "day"


def start(at: Any, unit: str) -> date:
    """The first day of the bucket `at` falls in (weeks start on Monday)."""
    d = to_date(at)
    if unit == "week":
        return d - timedelta(days=d.weekday())
    if unit == "month":
        return d.replace(day=1)
    return d


def step(d: date, unit: str) -> date:
    if unit == "week":
        return d + timedelta(days=7)
    if unit == "month":
        return date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return d + timedelta(days=1)


def buckets(first: Any, last: Any, unit: str) -> list[date]:
    """Every bucket start from the one holding `first` to the one holding `last`."""
    d, end = start(first, unit), start(last, unit)
    out = []
    while d <= end:
        out.append(d)
        d = step(d, unit)
    return out


def ticks(first: Any, last: Any, unit: str, most: int = MAX_TICKS) -> list[date]:
    """Bucket starts to label on the axis: every bucket, or every n-th one so there are at most `most`."""
    all_ = buckets(first, last, unit)
    every = max(1, -(-len(all_) // most))
    return all_[::every]


def label(d: Any, unit: str) -> str:
    d = to_date(d)
    if unit == "month":
        return d.strftime("%b %Y")
    if unit == "week":
        return f"w/c {d.day} {d.strftime('%b %Y')}"
    return f"{d.day} {d.strftime('%b %Y')}"


def last_per_bucket(rows: list[dict[str, Any]], unit: str, at: str = "at", series: tuple[str, ...] = ()
                    ) -> list[dict[str, Any]]:
    """For readings (a count at a time): keep the last reading of each series in each bucket, with `at` set to
    the bucket start (an ISO date). Rows are taken in time order."""
    keep: dict[tuple, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: str(r[at])):
        b = start(r[at], unit)
        keep[(tuple(r.get(s) for s in series), b)] = {**r, at: b.isoformat()}
    return sorted(keep.values(), key=lambda r: (r[at], tuple(str(r.get(s)) for s in series)))


def sum_per_bucket(rows: list[dict[str, Any]], unit: str, value: str, at: str = "at", by: tuple[str, ...] = ()
                   ) -> list[dict[str, Any]]:
    """For events (a number of changes at a time): add up `value` per bucket and `by` columns. The result has
    `at` (the bucket start, ISO date), the `by` columns and `value`."""
    acc: dict[tuple, Any] = {}
    for r in rows:
        k = (start(r[at], unit), *(r.get(b) for b in by))
        acc[k] = acc.get(k, 0) + (r.get(value) or 0)
    return [{at: k[0].isoformat(), **dict(zip(by, k[1:])), value: n} for k, n in sorted(acc.items(), key=lambda kv:
                                                                                       tuple(map(str, kv[0])))]
