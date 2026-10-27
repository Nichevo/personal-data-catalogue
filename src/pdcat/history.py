"""What changed between scans, and how fresh the data is.

Each scan is compared with the one before: new and dropped tables, files and
scripts; columns added, removed or retyped; row counts. The changes are kept,
so an item's page can show its history and notes written before a change can
be flagged for a re-check.

Freshness is the latest date in an item's main date column (picked on its
page, or guessed), measured by the last profile. It is compared with how often
the item is meant to refresh.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from .store import Catalogue, now

TRACKED_KINDS = ("table", "view", "procedure", "data_file", "script", "notebook", "sql_script", "file_series",
                 "report", "workbook")
STALE_AFTER_DAYS = {"daily": 3, "weekly": 10, "monthly": 40, "quarterly": 100}
DEFAULT_STALE_DAYS = 60


def snapshot(cat: Catalogue, source: str, exact: str = "%", inside: str = "%") -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for a in cat.conn.execute("SELECT key, kind, name, status, attrs FROM assets WHERE source = ? "
                              "AND (key LIKE ? OR key LIKE ?)", (source, exact, inside)):
        if a["kind"] not in TRACKED_KINDS:
            continue
        attrs = json.loads(a["attrs"])
        out[a["key"]] = {"kind": a["kind"], "name": a["name"], "status": a["status"],
                         "rows": attrs.get("row_count"), "columns": {}}
    if out:
        for c in cat.conn.execute("SELECT c.asset_key, c.name, c.data_type FROM columns c JOIN assets a "
                                  "ON a.key = c.asset_key WHERE a.source = ?", (source,)):
            if c["asset_key"] in out:
                out[c["asset_key"]]["columns"][c["name"]] = c["data_type"] or ""
    return out


def record_changes(cat: Catalogue, before: dict, after: dict, *, first_scan: bool = False) -> int:
    """Write the differences between two snapshots to the history. Runs inside the scan's transaction."""
    if first_scan:
        return 0
    ts = now()
    rows = []
    for key, a in after.items():
        b = before.get(key)
        if a["status"] != "active":
            if b and b["status"] == "active":
                rows.append((key, ts, "dropped", f"{a['name']} wasn't found"))
            continue
        if b is None or b["status"] != "active":
            rows.append((key, ts, "new item" if b is None else "came back", a["name"]))
            continue
        bc, ac = {k.lower(): (k, v) for k, v in b["columns"].items()}, {k.lower(): (k, v) for k, v in a["columns"].items()}
        if bc or ac:
            for low, (name, typ) in ac.items():
                if low not in bc:
                    rows.append((key, ts, "column added", f"{name} {typ}".strip()))
                elif (bc[low][1] or "") != (typ or "") and bc[low][1] and typ:
                    rows.append((key, ts, "column retyped", f"{name} {bc[low][1]} → {typ}"))
            for low, (name, typ) in bc.items():
                if low not in ac:
                    rows.append((key, ts, "column removed", f"{name} {typ}".strip()))
        if a["rows"] is not None and b["rows"] is not None and a["rows"] != b["rows"]:
            rows.append((key, ts, "row count", f"{b['rows']:,} → {a['rows']:,}"))
    cat.conn.executemany("INSERT INTO schema_history (asset_key, at, change, detail) VALUES (?, ?, ?, ?)", rows)
    return len(rows)


def history(cat: Catalogue, key: str, limit: int = 200) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        "SELECT at, change, detail FROM schema_history WHERE asset_key = ? ORDER BY at DESC, id DESC LIMIT ?",
        (key, limit))]


def recent_changes(cat: Catalogue, days: int = 30, limit: int = 500) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc).timestamp() - days * 86400
    cutoff = datetime.fromtimestamp(since, timezone.utc).isoformat(timespec="seconds")
    return [dict(r) for r in cat.conn.execute(
        """SELECT h.asset_key, h.at, h.change, h.detail, a.name, a.kind FROM schema_history h
           LEFT JOIN assets a ON a.key = h.asset_key WHERE h.at >= ? AND h.change != 'row count'
           ORDER BY h.at DESC, h.id DESC LIMIT ?""", (cutoff, limit))]


def row_count_trend(cat: Catalogue, key: str) -> list[tuple[str, int]]:
    out = []
    for r in cat.conn.execute("SELECT at, detail FROM schema_history WHERE asset_key = ? AND change = 'row count' "
                              "ORDER BY at", (key,)):
        try:
            out.append((r["at"][:10], int(r["detail"].split("→")[-1].strip().replace(",", ""))))
        except ValueError:
            continue
    return out


# ---- freshness -------------------------------------------------------------------

def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(text[:n], fmt).date()
        except ValueError:
            continue
    return None


def freshness(cat: Catalogue, key: str, info: dict | None = None) -> dict[str, Any] | None:
    """{"column", "latest", "age_days", "stale", "limit_days"} or None when there's no date to go on."""
    from .curation import item_info
    info = info if info is not None else item_info(cat, key)
    profile = [dict(p) for p in cat.profile(key)]
    if not profile:
        return None
    chosen = info.get("freshness_column")
    candidates = [p for p in profile if p["column_name"] == chosen] if chosen else [
        p for p in profile if any(t in (p["data_type"] or "").lower() for t in ("date", "time"))]
    best = None
    today = datetime.now(timezone.utc).date()
    for p in candidates:
        d = _as_date(p["max_value"])
        if d and d <= today and (best is None or d > best[1]):
            best = (p["column_name"], d)
    if best is None:
        return None
    age = (today - best[1]).days
    limit = STALE_AFTER_DAYS.get(info.get("refresh") or "", int(cat.setting("stale_days", DEFAULT_STALE_DAYS)))
    return {"column": best[0], "latest": best[1].isoformat(), "age_days": age, "stale": age > limit,
            "limit_days": limit, "measured": profile[0]["profiled_at"][:10]}
