"""How things grow over time: row counts, files in folders, items in the catalogue, scans, and profiles.

After each scan `record_scan` keeps a reading of every row count that is new or has moved, the number of items
of each kind, how many are described, and the files and bytes in each folder of a folder source (counting files
that were only listed, not opened, and the files in log folders). Profiles keep a compact copy of each column
when it moved (`keep_profile_history`), so a profile isn't lost when the table is profiled again.

Everything is small on purpose: one row when a number moves, nothing copied from the data itself. Old readings
are thinned by `thin_history` (kept in full for 90 days, then weekly, then monthly after a year).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .store import Catalogue, now

FILE_KINDS = ("data_file", "report", "workbook", "access_db", "document", "script", "notebook", "sql_script")
KEEP_FULL_DAYS = 90       # readings younger than this are all kept (Settings: history_full_days)
KEEP_WEEKLY_DAYS = 365    # then one a week up to a year, then one a month


# ---- after each scan ---------------------------------------------------------------------------------

def record_scan(cat: Catalogue, source: str, at: str | None = None) -> dict[str, int]:
    """Save this scan's growth readings for one source. Returns how many of each were written."""
    at = at or now()
    rows = _row_count_readings(cat, source, at)
    counts = _source_counts(cat, source)
    with cat.conn:
        cat.conn.executemany("INSERT OR REPLACE INTO row_counts (asset_key, at, rows) VALUES (?, ?, ?)", rows)
        cat.conn.executemany("INSERT OR REPLACE INTO scan_counts (at, source, what, n, bytes) VALUES (?, ?, ?, ?, ?)",
                             [(at, source, what, n, b) for what, (n, b) in counts.items()])
    return {"row_counts": len(rows), "counts": len(counts)}


def _latest_rows(cat: Catalogue, keys: list[str] | None = None) -> dict[str, int]:
    sql = ("SELECT r.asset_key, r.rows FROM row_counts r JOIN (SELECT asset_key, MAX(at) at FROM row_counts "
           "GROUP BY asset_key) m ON m.asset_key = r.asset_key AND m.at = r.at")
    return {r[0]: r[1] for r in cat.conn.execute(sql)}


def _row_count_readings(cat: Catalogue, source: str, at: str) -> list[tuple[str, str, int]]:
    last = _latest_rows(cat)
    out = []
    for r in cat.conn.execute("SELECT key, kind, attrs FROM assets WHERE source = ? AND status = 'active' AND kind IN "
                              "('table', 'view', 'data_file', 'file_series')", (source,)):
        a = json.loads(r["attrs"] or "{}")
        n = a.get("row_count") if r["kind"] != "file_series" else a.get("total_rows") or a.get("row_count")
        if isinstance(n, (int, float)) and n >= 0 and last.get(r["key"]) != int(n):
            out.append((r["key"], at, int(n)))
    return out


def _source_counts(cat: Catalogue, source: str) -> dict[str, tuple[int, int | None]]:
    out: dict[str, list] = defaultdict(lambda: [0, 0])
    described = 0
    info = {r[0] for r in cat.conn.execute(
        "SELECT asset_key FROM item_info WHERE COALESCE(purpose, '') != ''")}
    for r in cat.conn.execute("SELECT key, kind, name, description, attrs FROM assets WHERE source = ? AND "
                              "status = 'active'", (source,)):
        out[f"kind:{r['kind']}"][0] += 1
        if (r["description"] or "").strip() or r["key"] in info:
            described += 1
        if not (r["key"].startswith("files://") or r["key"].startswith("py://")):
            continue
        a = json.loads(r["attrs"] or "{}")
        if a.get("log_folder"):
            files, size = int(a.get("files") or 0), int(a.get("size") or 0)
        elif r["kind"] in FILE_KINDS and "#" not in r["key"]:
            files, size = 1, int(a.get("size_bytes") or 0)
        else:
            continue
        name = (r["name"] or "").replace("\\", "/").strip("/")
        parts = name.split("/")[:-1] if not a.get("log_folder") else name.split("/")
        for depth in range(len(parts) + 1):
            folder = "/".join(parts[:depth]) or "."
            out[f"folder:{folder}"][0] += files
            out[f"folder:{folder}"][1] += size
    result = {k: (v[0], v[1] or None) for k, v in out.items()}
    result["described"] = (described, None)
    return result


# ---- profiles ----------------------------------------------------------------------------------------

_KEEP = ("rows", "nulls", "distinct", "min", "max", "mean")


def keep_profile_history(cat: Catalogue, asset_key: str, columns: list[dict[str, Any]], at: str) -> int:
    """Called inside save_profile's transaction: keep a compact copy of each column whose numbers moved since the
    last copy (the first profile is kept too). Top values and charts are only kept for the latest profile."""
    last: dict[str, tuple] = {}
    for r in cat.conn.execute(
            "SELECT h.* FROM profile_history h JOIN (SELECT column_name, MAX(at) at FROM profile_history WHERE "
            "asset_key = ? GROUP BY column_name) m ON m.column_name = h.column_name AND m.at = h.at "
            "WHERE h.asset_key = ?", (asset_key, asset_key)):
        last[r["column_name"].lower()] = (r["row_count"], r["null_count"], r["distinct_count"], r["min_value"],
                                          r["max_value"], r["mean_value"])
    if not last:   # profiled before 2.9: keep that profile as the first copy, so nothing done so far is lost
        for r in cat.conn.execute("SELECT * FROM profiles WHERE asset_key = ?", (asset_key,)):
            row = (r["row_count"], r["null_count"], r["distinct_count"], r["min_value"], r["max_value"], r["mean_value"])
            cat.conn.execute("INSERT OR IGNORE INTO profile_history (asset_key, column_name, at, row_count, null_count, "
                             "distinct_count, min_value, max_value, mean_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (asset_key, r["column_name"], r["profiled_at"], *row))
            last[r["column_name"].lower()] = row
    rows = []
    for c in columns:
        mean = c.get("mean")
        now_row = (c.get("rows"), c.get("nulls"), c.get("distinct"), _text(c.get("min")), _text(c.get("max")),
                   float(mean) if isinstance(mean, (int, float)) else None)
        if last.get(str(c["name"]).lower()) != now_row:
            rows.append((asset_key, c["name"], at, *now_row))
    cat.conn.executemany("INSERT OR REPLACE INTO profile_history (asset_key, column_name, at, row_count, null_count, "
                         "distinct_count, min_value, max_value, mean_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _text(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)[:200]


def profile_history(cat: Catalogue, key: str, column: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM profile_history WHERE asset_key = ?" + (" AND lower(column_name) = ?" if column else "")
    return [dict(r) for r in cat.conn.execute(sql + " ORDER BY column_name, at",
                                              (key, column.lower()) if column else (key,))]


def profile_drift(cat: Catalogue, since: str | None = None, keys: set[str] | None = None,
                  null_points: float = 10.0, distinct_share: float = 0.5) -> list[dict[str, Any]]:
    """Columns whose profile moved a lot between two profiles: null share up or down by `null_points` percentage
    points, distinct values up or down by half, or a date column whose latest value stopped moving.
    [{"asset_key", "column", "at", "detail"}], newest first."""
    out = []
    prev: dict[tuple[str, str], dict] = {}
    for r in cat.conn.execute("SELECT * FROM profile_history ORDER BY asset_key, column_name, at"):
        k = (r["asset_key"], r["column_name"])
        p = prev.get(k)
        prev[k] = dict(r)
        if not p or (keys is not None and r["asset_key"] not in keys) or (since and r["at"] < since):
            continue
        notes = []
        if p["row_count"] and r["row_count"] and p["null_count"] is not None and r["null_count"] is not None:
            a, b = 100 * p["null_count"] / p["row_count"], 100 * r["null_count"] / r["row_count"]
            if abs(b - a) >= null_points:
                notes.append(f"null share {a:.0f}% → {b:.0f}%")
        if p["distinct_count"] and r["distinct_count"] is not None and p["distinct_count"] >= 10:
            change = (r["distinct_count"] - p["distinct_count"]) / p["distinct_count"]
            if abs(change) >= distinct_share:
                notes.append(f"distinct values {p['distinct_count']:,} → {r['distinct_count']:,}")
        if _is_date(p["max_value"]) and p["max_value"] == r["max_value"] and p["row_count"] != r["row_count"]:
            notes.append(f"latest date stayed at {p['max_value'][:10]} although rows changed")
        if notes:
            out.append({"asset_key": r["asset_key"], "column": r["column_name"], "at": r["at"],
                        "detail": f"{r['column_name']}: " + "; ".join(notes)})
    return sorted(out, key=lambda d: d["at"], reverse=True)


def _is_date(v: Any) -> bool:
    return isinstance(v, str) and bool(re.match(r"^\d{4}-\d{2}-\d{2}", v))


# ---- backfill from what 2.8 and earlier stored -------------------------------------------------------

def backfill(cat: Catalogue) -> dict[str, int]:
    """Once, on a catalogue made before 2.9: turn the change history's "1,200 → 1,350" row-count text into
    readings, and rebuild items-per-kind growth from the dates items were first seen. Safe to run again."""
    if cat.setting("growth_backfilled"):
        return {}
    readings: list[tuple[str, str, int]] = []
    first: dict[str, tuple[str, int]] = {}
    for r in cat.conn.execute("SELECT asset_key, at, detail FROM schema_history WHERE change = 'row count' "
                              "ORDER BY at"):
        m = re.match(r"^\s*([\d,]+)\s*→\s*([\d,]+)", r["detail"] or "")
        if not m:
            continue
        before, after = (int(x.replace(",", "")) for x in m.groups())
        if r["asset_key"] not in first:
            first[r["asset_key"]] = (r["at"], before)
        readings.append((r["asset_key"], r["at"], after))
    # the count before the first change was true from the item's first sighting
    seen = {r[0]: r[1] for r in cat.conn.execute("SELECT key, first_seen FROM assets")}
    for key, (at, before) in first.items():
        start = seen.get(key)
        if not start or start >= at:   # a second before the change, so the two readings don't collide
            start = (_parse(at) - timedelta(seconds=1)).isoformat(timespec="seconds")
        readings.append((key, start, before))
    # items with a count that never changed: one reading from when they were first seen
    have = {k for k, _, _ in readings}
    for r in cat.conn.execute("SELECT key, kind, attrs, first_seen FROM assets WHERE status = 'active' AND kind IN "
                              "('table', 'view', 'data_file')"):
        if r["key"] in have:
            continue
        n = json.loads(r["attrs"] or "{}").get("row_count")
        if isinstance(n, (int, float)):
            readings.append((r["key"], r["first_seen"], int(n)))
    # items per kind and source, by the day they were first seen (dropped items count until they were dropped)
    dropped = {r[0]: r[1] for r in cat.conn.execute(
        "SELECT asset_key, MIN(at) FROM schema_history WHERE change = 'dropped' GROUP BY asset_key")}
    per_day: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in cat.conn.execute("SELECT key, kind, source, first_seen FROM assets"):
        day = (r["first_seen"] or "")[:10]
        if day:
            per_day[(day, r["source"], f"kind:{r['kind']}")] += 1
        if r["key"] in dropped:
            per_day[(dropped[r["key"]][:10], r["source"], f"kind:{r['kind']}")] -= 1
    running: dict[tuple[str, str], int] = defaultdict(int)
    counts = []
    for (day, source, what), n in sorted(per_day.items()):
        running[(source, what)] += n
        counts.append((f"{day}T00:00:00+00:00", source, what, running[(source, what)], None))
    with cat.conn:
        cat.conn.executemany("INSERT OR IGNORE INTO row_counts (asset_key, at, rows) VALUES (?, ?, ?)", readings)
        cat.conn.executemany("INSERT OR IGNORE INTO scan_counts (at, source, what, n, bytes) VALUES (?, ?, ?, ?, ?)",
                             counts)
    cat.set_setting("growth_backfilled", now(), quiet=True)
    return {"row_counts": len(readings), "counts": len(counts)}


def backfill_count(cat: Catalogue) -> int:
    """The after-scan step: backfill once, then nothing."""
    return sum(backfill(cat).values())


# ---- series for the graphs ---------------------------------------------------------------------------

def row_series(cat: Catalogue, keys: list[str], since: str | None = None) -> list[dict[str, Any]]:
    """[{"key", "at", "rows"}] for these items, oldest first; the reading just before `since` is included so a
    line starts at the right height."""
    out = []
    for k in keys:
        rows = [dict(r) for r in cat.conn.execute("SELECT asset_key key, at, rows FROM row_counts WHERE asset_key = ? "
                                                  "ORDER BY at", (k,))]
        if since:
            before = [r for r in rows if r["at"] < since]
            rows = before[-1:] + [r for r in rows if r["at"] >= since]
        out += rows
    return out


def growers(cat: Catalogue, since: str, limit: int = 10, keys: set[str] | None = None
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(biggest growers, biggest shrinkers) since a date: [{"key", "name", "from", "to", "change"}], by share."""
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    for r in cat.conn.execute("SELECT asset_key, at, rows FROM row_counts ORDER BY at"):
        if keys is not None and r[0] not in keys:
            continue
        if r[1] < since:
            first[r[0]] = r[2]
        else:
            first.setdefault(r[0], r[2])
        last[r[0]] = r[2]
    names = {r[0]: r[1] for r in cat.conn.execute("SELECT key, name FROM assets WHERE status = 'active'")}
    moves = []
    for k, a in first.items():
        b = last.get(k, a)
        if k in names and a != b and a > 0:
            moves.append({"key": k, "name": names[k], "from": a, "to": b, "change": (b - a) / a})
    moves.sort(key=lambda m: m["change"])
    return list(reversed([m for m in moves if m["change"] > 0]))[:limit], [m for m in moves if m["change"] < 0][:limit]


def jumps(cat: Catalogue, keys: list[str], share: float = 0.5) -> list[dict[str, Any]]:
    """Readings where a count jumped or dropped by more than `share` from the reading before."""
    out = []
    for k in keys:
        prev = None
        for r in cat.conn.execute("SELECT at, rows FROM row_counts WHERE asset_key = ? ORDER BY at", (k,)):
            if prev and prev > 0 and abs(r[1] - prev) / prev > share:
                out.append({"key": k, "at": r[0], "from": prev, "to": r[1]})
            prev = r[1]
    return out


def folder_series(cat: Catalogue, source: str | None = None, depth: int | None = 1,
                  since: str | None = None) -> list[dict[str, Any]]:
    """[{"at", "source", "folder", "files", "bytes"}] per scan. `depth` 0 = each source's top, 1 = its top
    folders, None = every folder."""
    sql = "SELECT at, source, what, n, bytes FROM scan_counts WHERE what LIKE 'folder:%'"
    args: list = []
    if source:
        sql += " AND source = ?"
        args.append(source)
    if since:
        sql += " AND at >= ?"
        args.append(since)
    out = []
    for r in cat.conn.execute(sql + " ORDER BY at", args):
        folder = r["what"][len("folder:"):]
        d = 0 if folder == "." else folder.count("/") + 1
        if depth is not None and d != depth:
            continue
        out.append({"at": r["at"], "source": r["source"], "folder": folder, "files": r["n"], "bytes": r["bytes"]})
    return out


def folder_table(cat: Catalogue, days: int = 30, depth: int | None = 1, source: str | None = None
                 ) -> list[dict[str, Any]]:
    """Each folder now: files, files added over the last `days`, per week, and size."""
    since = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in folder_series(cat, source, depth):
        by[(r["source"], r["folder"])].append(r)
    out = []
    for (src, folder), rows in by.items():
        latest = rows[-1]
        before = [r for r in rows if r["at"] < since]
        start = before[-1] if before else rows[0]
        span_days = max(1.0, (_parse(latest["at"]) - _parse(start["at"])).total_seconds() / 86400) \
            if start is not latest else None
        added = latest["files"] - start["files"]
        out.append({"source": src, "folder": folder, "files": latest["files"], "added": added,
                    "per_week": round(added / span_days * 7) if span_days else None, "bytes": latest["bytes"],
                    "last_scan": latest["at"]})
    return sorted(out, key=lambda r: -(r["added"] or 0))


def catalogue_series(cat: Catalogue, since: str | None = None) -> list[dict[str, Any]]:
    """[{"day", "kind", "items"}] for the whole catalogue (every source), plus kind "described": the latest count of
    each source on each day, summed."""
    latest: dict[tuple[str, str, str], int] = {}
    for r in cat.conn.execute("SELECT at, source, what, n FROM scan_counts WHERE what LIKE 'kind:%' OR "
                              "what = 'described' ORDER BY at"):
        latest[(r["at"][:10], r["source"], r["what"])] = r["n"]
    days = sorted({d for d, _, _ in latest})
    carry: dict[tuple[str, str], int] = {}
    out = []
    for day in days:
        for (d, src, what), n in latest.items():
            if d == day:
                carry[(src, what)] = n
        totals: dict[str, int] = defaultdict(int)
        for (src, what), n in carry.items():
            totals[what.removeprefix("kind:")] += n
        if not since or day >= since[:10]:
            out += [{"day": day, "kind": k, "items": n} for k, n in sorted(totals.items())]
    return out


def scan_series(cat: Catalogue, limit: int = 200) -> list[dict[str, Any]]:
    """[{"at", "source", "items", "minutes"}] for each finished scan, newest first."""
    out = []
    for r in cat.conn.execute("SELECT source, started_at, finished_at, summary FROM scans WHERE finished_at IS NOT NULL "
                              "ORDER BY id DESC LIMIT ?", (limit,)):
        try:
            s = json.loads(r["summary"] or "{}")
        except ValueError:
            s = {}
        items = sum(v for k, v in s.items() if isinstance(v, int) and k in _ITEM_KINDS)
        minutes = None
        try:
            minutes = round((_parse(r["finished_at"]) - _parse(r["started_at"])).total_seconds() / 60, 1)
        except (TypeError, ValueError):
            pass
        out.append({"at": r["finished_at"], "source": r["source"], "items": items, "minutes": minutes,
                    "stopped": bool(s.get("stopped"))})
    return out


_ITEM_KINDS = {"table", "view", "procedure", "data_file", "file_series", "report", "workbook", "script", "notebook",
               "sql_script", "access_db", "document", "folder", "data_source", "measure"}


def _parse(ts: str) -> datetime:
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t if t.tzinfo else t.astimezone()


# ---- keeping it small --------------------------------------------------------------------------------

def thin_history(cat: Catalogue, full_days: int | None = None, weekly_days: int | None = None,
                 today: date | None = None) -> dict[str, int]:
    """Keep every reading for `full_days`, then the last one of each week up to `weekly_days`, then the last one
    of each month. Row counts, scan counts and profile copies are thinned; the change history itself is not."""
    full_days = int(full_days if full_days is not None else cat.setting("history_full_days", KEEP_FULL_DAYS))
    weekly_days = int(weekly_days if weekly_days is not None else cat.setting("history_weekly_days", KEEP_WEEKLY_DAYS))
    today = today or date.today()
    full_cut = (today - timedelta(days=full_days)).isoformat()
    week_cut = (today - timedelta(days=weekly_days)).isoformat()

    def bucket(at: str) -> str | None:
        day = at[:10]
        if day >= full_cut:
            return None   # kept
        d = date.fromisoformat(day)
        if day >= week_cut:
            return f"w{d.isocalendar()[0]}-{d.isocalendar()[1]}"
        return f"m{d.year}-{d.month}"

    removed = {}
    for table, group in (("row_counts", ("asset_key",)), ("scan_counts", ("source", "what")),
                         ("profile_history", ("asset_key", "column_name"))):
        cols = ", ".join(group)
        rows = cat.conn.execute(f"SELECT rowid, {cols}, at FROM {table} WHERE at < ? ORDER BY {cols}, at",
                                (full_cut,)).fetchall()
        keep_last: dict[tuple, int] = {}
        for r in rows:
            b = bucket(r["at"])
            keep_last[(*[r[c] for c in group], b)] = r["rowid"]
        keep = set(keep_last.values())
        drop = [(r["rowid"],) for r in rows if r["rowid"] not in keep]
        with cat.conn:
            cat.conn.executemany(f"DELETE FROM {table} WHERE rowid = ?", drop)
        removed[table] = len(drop)
    return removed
