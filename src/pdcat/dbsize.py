"""How big the catalogue file is, what takes the space, and tidying it up.

Tidy up thins the growth readings and profile copies (every reading for 90 days, then one a week to a year, then
one a month), trims the query log and the change history of noise-sized row counts older than a year, and then
compacts the file (VACUUM) so the space is given back to the disk.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import Catalogue

WARN_BYTES = 1024 ** 3        # 1 GB
KEEP_QUERY_LOG = 20_000       # newest queries kept in the query log

LABELS = {
    "assets": "Items", "columns": "Columns", "links": "Links", "notes": "Notes", "profiles": "Profiles (latest)",
    "profile_history": "Profile history", "schema_history": "Change history", "row_counts": "Row counts over time",
    "scan_counts": "Scan and folder counts", "tracker_counts": "Tracked changes (counts)",
    "tracker_keys": "Tracked changes (keys)", "query_log": "Query log", "suggestions": "Suggestions",
    "search_idx": "Search index", "edits": "Your edits", "sql_queries": "SQL query library",
    "sql_check_cache": "SQL check cache", "relationships": "Relationships",
}


def file_size(cat: Catalogue) -> int:
    """The catalogue file plus its write-ahead log."""
    total = 0
    for suffix in ("", "-wal"):
        try:
            total += os.path.getsize(str(cat.path) + suffix)
        except OSError:
            pass
    return total


def table_sizes(cat: Catalogue) -> list[dict[str, Any]]:
    """[{"table", "label", "rows", "bytes"}] biggest first. Bytes come from SQLite's dbstat when it's built in
    (indexes counted with their table); otherwise only rows are given."""
    tables = [r[0] for r in cat.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE "
                                             "'sqlite_%' AND name NOT LIKE 'search_idx_%'")]
    sizes: dict[str, int] = {}
    try:
        owner = {r[0]: r[1] for r in cat.conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type IN "
                                                      "('table', 'index')")}
        for name, n in cat.conn.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"):
            t = owner.get(name, name)
            if t.startswith("search_idx"):
                t = "search_idx"
            sizes[t] = sizes.get(t, 0) + (n or 0)
    except Exception:   # dbstat isn't compiled into every SQLite
        sizes = {}
    out = []
    for t in tables:
        try:
            rows = cat.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception:
            continue
        out.append({"table": t, "label": LABELS.get(t, t.replace("_", " ")), "rows": rows, "bytes": sizes.get(t)})
    out.sort(key=lambda d: (-(d["bytes"] or 0), -d["rows"]))
    return out


def tidy_up(cat: Catalogue, *, vacuum: bool = True) -> dict[str, Any]:
    """Thin old readings, trim the query log, then compact the file. Returns what was removed and the sizes."""
    from .growth import thin_history
    before = file_size(cat)
    removed = thin_history(cat)
    with cat.conn:
        cut = cat.conn.execute("SELECT id FROM query_log ORDER BY id DESC LIMIT 1 OFFSET ?",
                               (KEEP_QUERY_LOG,)).fetchone()
        if cut:
            removed["query_log"] = cat.conn.execute("DELETE FROM query_log WHERE id <= ?", (cut[0],)).rowcount
        year = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat(timespec="seconds")
        removed["old row-count changes"] = _thin_row_count_history(cat, year)
    if vacuum:
        cat.conn.commit()
        cat.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cat.conn.execute("VACUUM")
        cat.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cat.set_setting("tidied_at", datetime.now(timezone.utc).isoformat(timespec="seconds"), quiet=True)
    return {"removed": removed, "before": before, "after": file_size(cat)}


def _thin_row_count_history(cat: Catalogue, before: str) -> int:
    """Row-count lines in the change history older than `before`: keep the last one of each month per item (the
    row-count readings keep the numbers for the graphs)."""
    rows = cat.conn.execute("SELECT id, asset_key, at FROM schema_history WHERE change = 'row count' AND at < ? "
                            "ORDER BY asset_key, at", (before,)).fetchall()
    keep: dict[tuple[str, str], int] = {}
    for r in rows:
        keep[(r["asset_key"], r["at"][:7])] = r["id"]
    kept = set(keep.values())
    drop = [(r["id"],) for r in rows if r["id"] not in kept]
    cat.conn.executemany("DELETE FROM schema_history WHERE id = ?", drop)
    return len(drop)


def yearly_estimate(cat: Catalogue) -> dict[str, int]:
    """Roughly how much 2.9's history adds per year at the current scan rate, in bytes."""
    scans = cat.conn.execute("SELECT COUNT(*) FROM scans WHERE started_at >= ?", (
        (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),)).fetchone()[0] or 4
    tables = cat.conn.execute("SELECT COUNT(*) FROM assets WHERE status = 'active' AND kind IN ('table', 'view', "
                              "'data_file')").fetchone()[0]
    folders = cat.conn.execute("SELECT COUNT(DISTINCT what) FROM scan_counts WHERE what LIKE 'folder:%'").fetchone()[0]
    profiled = cat.conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    per_year = scans * 12
    moved = 0.3       # share of row counts that move between two scans
    return {"row counts": int(tables * moved * min(per_year, 52 + 12) * 40),
            "folder counts": int(folders * min(per_year, 52 + 12) * 60),
            "profile copies": int(profiled * 0.5 * 24 * 90)}
