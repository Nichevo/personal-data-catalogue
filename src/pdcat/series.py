"""Dated file series: files that differ only by a date, such as sales_2026-01.csv,
sales_2026-02.csv… or 2026/01/sales.csv, become one catalogue entry instead of
dozens of near-copies.

The date can be in the file name, in the folders, or split between them
(2026/sales_01.csv). When the folders and the file name disagree, the file
name wins and the mismatch is flagged. The series shows its pattern, how often
a file arrives, the first and last dates, missing periods and each file's
rows. The newest file is profiled in full; older ones only have their rows
and header read. A file whose columns differ from the rest is flagged as a
layout change.

Grouping can be switched off on the Settings page, or for one series ("Split
this series").
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], start=1)}
MONTH_NAMES = {**MONTHS, **{n: i for i, n in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], start=1)}, "sept": 9}
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_Y = r"(?P<y>(?:19|20)\d{2})"
_SEP = r"[-_. ]?"
# Most specific first. Each pattern must not touch other digits (no 7-digit runs).
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ymd", re.compile(rf"(?<!\d){_Y}{_SEP}(?P<m>0[1-9]|1[0-2]){_SEP}(?P<d>0[1-9]|[12]\d|3[01])(?!\d)")),
    ("dmy", re.compile(rf"(?<!\d)(?P<d>0[1-9]|[12]\d|3[01])[-_.](?P<m>0[1-9]|1[0-2])[-_.]{_Y}(?!\d)")),
    ("yq", re.compile(rf"(?<!\d){_Y}{_SEP}q(?P<q>[1-4])(?!\d)", re.I)),
    ("qy", re.compile(rf"(?<![a-z\d])q(?P<q>[1-4]){_SEP}{_Y}(?!\d)", re.I)),
    ("ym", re.compile(rf"(?<!\d){_Y}{_SEP}(?P<m>0[1-9]|1[0-2])(?!\d)")),
    ("my", re.compile(rf"(?<!\d)(?P<m>0[1-9]|1[0-2])[-_.]{_Y}(?!\d)")),
    ("mony", re.compile(rf"(?<![a-z]){_MON}{_SEP}{_Y}(?!\d)", re.I)),
    ("ymon", re.compile(rf"(?<!\d){_Y}{_SEP}{_MON}(?![a-z])", re.I)),
    ("y", re.compile(rf"(?<![\d]){_Y}(?!\d)")),
]
PLACEHOLDER = {"ymd": "{yyyy}{mm}{dd}", "dmy": "{dd}{mm}{yyyy}", "yq": "{yyyy}Q{q}", "qy": "Q{q}{yyyy}",
               "ym": "{yyyy}{mm}", "my": "{mm}{yyyy}", "mony": "{mon}{yyyy}", "ymon": "{yyyy}{mon}", "y": "{yyyy}"}
LONE_MONTH = re.compile(r"(?<![\d])(?P<m>0[1-9]|1[0-2])(?![\d])")


@dataclass
class DatedPath:
    rel: str
    template: str
    year: int
    month: int | None = None
    day: int | None = None
    quarter: int | None = None
    mismatch: str | None = None

    @property
    def granularity(self) -> str:
        return "day" if self.day else "month" if self.month else "quarter" if self.quarter else "year"

    @property
    def date(self) -> date:
        month = self.month or ((self.quarter - 1) * 3 + 1 if self.quarter else 1)
        return date(self.year, month, self.day or 1)


def _find(part: str) -> tuple[str, dict[str, int]] | None:
    for name, rx in PATTERNS:
        m = rx.search(part)
        if not m:
            continue
        g = m.groupdict()
        found = {"y": int(g["y"])}
        if g.get("m"):
            found["m"] = int(g["m"])
        if g.get("mon"):
            found["m"] = MONTH_NAMES.get(g["mon"].lower(), MONTH_NAMES.get(g["mon"].lower()[:3]))
        if g.get("d"):
            found["d"] = int(g["d"])
        if g.get("q"):
            found["q"] = int(g["q"])
        if "d" in found:
            try:
                date(found["y"], found["m"], found["d"])
            except ValueError:
                continue
        template = part[:m.start()] + PLACEHOLDER[name] + part[m.end():]
        return template, found
    return None


def parse(rel: str) -> DatedPath | None:
    """Find the date in a file's path (folders and name). None when there isn't one."""
    parts = rel.replace("\\", "/").split("/")
    folders, name = parts[:-1], parts[-1]
    stem, dot, ext = name.rpartition(".")
    stem = stem or name
    in_name = _find(stem)
    folder_found: dict[str, int] = {}
    folder_templates = []
    for f in folders:
        hit = _find(f)
        if hit:
            folder_templates.append(hit[0])
            for k, v in hit[1].items():
                folder_found.setdefault(k, v)
        elif re.fullmatch(r"0[1-9]|1[0-2]", f) and "y" in folder_found and "m" not in folder_found:
            folder_templates.append("{mm}")  # 2026/01/
            folder_found["m"] = int(f)
        elif re.fullmatch(r"0[1-9]|[12]\d|3[01]", f) and "m" in folder_found and "d" not in folder_found:
            folder_templates.append("{dd}")  # 2026/01/15/
            folder_found["d"] = int(f)
        else:
            folder_templates.append(f)
    name_template = stem
    found = dict(folder_found)
    mismatch = None
    if in_name:
        name_template, from_name = in_name
        for k, v in from_name.items():
            if k in folder_found and folder_found[k] != v:
                mismatch = (f"folder says {_label(folder_found)} but the file name says {_label(from_name)}; "
                            "going by the file name")
            found[k] = v
    elif "y" in folder_found and "m" not in folder_found:
        lone = LONE_MONTH.search(stem)
        if lone:  # 2026/sales_01.csv
            found["m"] = int(lone["m"])
            name_template = stem[:lone.start()] + "{mm}" + stem[lone.end():]
    if "y" not in found:
        return None
    if "m" in found and found["m"] is None:
        return None
    template = "/".join([*folder_templates, name_template + (f".{ext}" if dot else "")])
    try:
        dp = DatedPath(rel, template, found["y"], found.get("m"), found.get("d"), found.get("q"), mismatch)
        dp.date  # validates
    except (ValueError, TypeError):
        return None
    return dp


def _label(d: dict[str, int]) -> str:
    return "-".join(str(d[k]).zfill(2) for k in ("y", "m", "d") if k in d) + (f" Q{d['q']}" if "q" in d else "")


@dataclass
class Series:
    template: str
    members: list[DatedPath] = field(default_factory=list)

    @property
    def granularity(self) -> str:
        order = ["day", "month", "quarter", "year"]
        return min((m.granularity for m in self.members), key=order.index)

    def sorted(self) -> list[DatedPath]:
        return sorted(self.members, key=lambda m: (m.date, m.rel))


def group(rels: list[str], min_files: int = 2) -> tuple[list[Series], list[str]]:
    """Split paths into dated series and the files that stand alone."""
    by_template: dict[str, Series] = {}
    alone = []
    for rel in rels:
        dp = parse(rel)
        if dp is None:
            alone.append(rel)
            continue
        by_template.setdefault(dp.template.lower(), Series(dp.template)).members.append(dp)
    series = []
    for s in by_template.values():
        dates = {m.date for m in s.members}
        if len(s.members) >= min_files and len(dates) >= 2:
            series.append(s)
        else:
            alone += [m.rel for m in s.members]
    return series, alone


def frequency(dates: list[date], granularity: str) -> str:
    dates = sorted(set(dates))
    if len(dates) < 2:
        return granularity_label(granularity)
    gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]))
    median = gaps[(len(gaps) - 1) // 2]  # lower median, so one missing period doesn't change the answer
    if median <= 1:
        return "daily"
    if 5 <= median <= 8:
        return "weekly"
    if 27 <= median <= 32:
        return "monthly"
    if 85 <= median <= 95:
        return "quarterly"
    if 360 <= median <= 370:
        return "yearly"
    return f"about every {median} days"


def granularity_label(g: str) -> str:
    return {"day": "daily", "month": "monthly", "quarter": "quarterly", "year": "yearly"}[g]


def missing_periods(dates: list[date], freq: str, limit: int = 60) -> list[str]:
    """Expected periods between the first and last file that have no file."""
    have = set(dates)
    if not have:
        return []
    first, last = min(have), max(have)
    out: list[str] = []
    if freq == "monthly":
        months = {(d.year, d.month) for d in have}
        y, m = first.year, first.month
        while (y, m) <= (last.year, last.month) and len(out) < limit:
            if (y, m) not in months:
                out.append(f"{y}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    elif freq == "quarterly":
        qs = {(d.year, (d.month - 1) // 3 + 1) for d in have}
        y, q = first.year, (first.month - 1) // 3 + 1
        while (y, q) <= (last.year, (last.month - 1) // 3 + 1) and len(out) < limit:
            if (y, q) not in qs:
                out.append(f"{y} Q{q}")
            y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    elif freq == "yearly":
        out = [str(y) for y in range(first.year, last.year + 1) if y not in {d.year for d in have}][:limit]
    elif freq in ("daily", "weekly"):
        step = timedelta(days=1 if freq == "daily" else 7)
        d = first
        while d <= last and len(out) < limit:
            if freq == "daily" and d not in have and d.weekday() < 5:
                out.append(d.isoformat())
            elif freq == "weekly" and not any(abs((d - h).days) <= 3 for h in have):
                out.append(d.isoformat())
            d += step
    return out


def series_key(folder_key: str, template: str) -> str:
    return f"{folder_key}/~series/{template}".lower()


def describe(s: Series) -> dict[str, Any]:
    ordered = s.sorted()
    dates = [m.date for m in ordered]
    freq = frequency(dates, s.granularity)
    return {"pattern": s.template, "frequency": freq, "granularity": s.granularity,
            "first": _period(ordered[0]), "last": _period(ordered[-1]), "file_count": len(ordered),
            "missing": missing_periods(dates, freq),
            "mismatches": [{"file": m.rel, "detail": m.mismatch} for m in ordered if m.mismatch]}


def _period(m: DatedPath) -> str:
    if m.granularity == "day":
        return m.date.isoformat()
    if m.granularity == "month":
        return f"{m.year}-{m.month:02d}"
    if m.granularity == "quarter":
        return f"{m.year} Q{m.quarter}"
    return str(m.year)


def period_of(rel: str) -> str:
    dp = parse(rel)
    return _period(dp) if dp else ""


def layout_changes(files: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Files whose columns differ from the most common layout in the series."""
    layouts: dict[tuple, int] = defaultdict(int)
    for f in files:
        if f.get("columns"):
            layouts[tuple(c.lower() for c in f["columns"])] += 1
    if len(layouts) < 2:
        return []
    usual = max(layouts.items(), key=lambda kv: kv[1])[0]
    out = []
    for f in files:
        cols = tuple(c.lower() for c in f.get("columns") or [])
        if cols and cols != usual:
            added = [c for c in f["columns"] if c.lower() not in usual]
            removed = [c for c in usual if c not in cols]
            bits = []
            if added:
                bits.append("added " + ", ".join(added[:6]))
            if removed:
                bits.append("missing " + ", ".join(removed[:6]))
            if not bits:
                bits.append("columns in a different order")
            out.append({"file": f["rel"], "detail": "; ".join(bits)})
    return out


def series_files(cat) -> list[dict[str, Any]]:
    """One row per file in every series, for the export."""
    import json
    rows = []
    for r in cat.conn.execute("SELECT key, attrs FROM assets WHERE kind = 'file_series' AND status = 'active'"):
        attrs = json.loads(r["attrs"])
        for f in attrs.get("files", []):
            rows.append({"series_key": r["key"], "file": f["rel"], "period": f.get("period"), "rows": f.get("rows"),
                         "size_bytes": f.get("size"), "modified": f.get("modified"),
                         "layout_differs": bool(f.get("layout_differs"))})
    return rows
