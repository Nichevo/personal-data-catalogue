"""The Power BI map: every report, how much is in it, and where its data comes from.

Each report is one box, drawn bigger the more pages, visuals and measures it has, with the
tables, files and file series its queries load on its left, then the scripts that write
those, and the APIs, databases and other sources those scripts read. Older versions of a
report are folded into the current one (the box says how many). A report whose queries
load nothing the catalogue knows about is flagged: either its sources aren't catalogued yet
or it couldn't be read.

The table beside the map lists the same reports with their counts, so you can see which
reports carry the most and which rest on data nobody has described.
"""
from __future__ import annotations

import json
from typing import Any

from .codemap import Graph, hidden_from_map
from .dataflow import flow_graph
from .store import Catalogue


def reports(cat: Catalogue, *, workbooks: bool = False, show_old: bool = False,
            old_versions: bool = False) -> list[dict[str, Any]]:
    """One row per current report (and workbook, when asked): what is in it and what it loads. Older versions
    are folded into the current one ("+2 older") unless `old_versions`, which lists them as reports of their own."""
    from .groups import older_versions
    from .reportversions import counts
    old = older_versions(cat)
    hidden = hidden_from_map(cat, show_old) - (old if old_versions else set())
    if old_versions:
        old = set()
    kinds = ("report", "workbook") if workbooks else ("report",)
    marks = ", ".join("?" * len(kinds))
    versions: dict[str, int] = {}
    for r in cat.conn.execute("SELECT g.main_key, COUNT(*) FROM group_members m JOIN item_groups g "
                              "ON g.uid = m.group_uid WHERE g.type = 'versions' AND g.main_key IS NOT NULL "
                              "AND m.asset_key != g.main_key GROUP BY g.main_key"):
        versions[r[0]] = 0 if old_versions else r[1]
    notes = dict(cat.conn.execute("SELECT asset_key, COUNT(*) FROM notes GROUP BY asset_key").fetchall())
    rows = []
    for a in cat.conn.execute(f"SELECT key, kind, name, source, attrs FROM assets WHERE kind IN ({marks}) "
                              "AND status = 'active' ORDER BY name", kinds):
        if a["key"] in hidden or a["key"] in old:
            continue
        attrs = json.loads(a["attrs"])
        c = counts(attrs, a["kind"])
        links = cat.conn.execute(
            "SELECT l.dst_ref, l.dst_key, d.kind FROM links l LEFT JOIN assets d ON d.key = l.dst_key "
            "WHERE l.src_key = ? AND l.relation = 'reads'", (a["key"],)).fetchall()
        data = [l for l in links if l["kind"] != "data_source"]
        known = [l for l in data if l["dst_key"]]
        outside = [l for l in links if l["kind"] == "data_source"]
        if attrs.get("read_error"):
            flag = "couldn't be read"
        elif attrs.get("live"):
            flag = ""  # a live connection to a published dataset loads nothing itself
        elif not links:
            flag = "no sources found"
        elif data and not known:
            flag = "loads nothing catalogued"
        else:
            flag = ""
        size = c.get("pages", 0) + c.get("visuals", 0) + c.get("measures", 0) if a["kind"] == "report" else \
            c.get("sheets", 0) + c.get("queries", 0) + c.get("pivots", 0)
        rows.append({
            "key": a["key"], "kind": a["kind"], "name": a["name"], "folder": a["source"],
            "pages": c.get("pages", c.get("sheets", 0)), "visuals": c.get("visuals", 0),
            "measures": c.get("measures", 0), "size": size,
            "tables and files": len(data), "catalogued": len(known), "outside sources": len(outside),
            "older versions": versions.get(a["key"], 0), "notes": notes.get(a["key"], 0),
            "live connection": (attrs.get("live") or {}).get("dataset") or ("yes" if attrs.get("live") else ""),
            "modified": (attrs.get("modified") or "")[:10], "flag": flag,
        })
    return sorted(rows, key=lambda r: (-r["size"], r["name"].lower()))


def pick_reports(rows: list[dict[str, Any]], *, text: str = "", flagged_only: bool = False,
                 limit: int | None = None) -> list[dict[str, Any]]:
    """The reports to show: those whose name or folder holds `text`, only flagged ones if asked, and at most
    `limit` of them (the biggest first, which is how `reports` orders them)."""
    words = [w for w in text.lower().split() if w]
    out = [r for r in rows if all(w in f"{r['name']} {r['folder']}".lower() for w in words)
           and (r["flag"] or not flagged_only)]
    return out[:limit] if limit else out


def report_map(cat: Catalogue, *, workbooks: bool = False, show_old: bool = False, hops: int = 3,
               rows: list[dict[str, Any]] | None = None, old_versions: bool = False) -> Graph:
    """Reports on the right, everything that feeds them to the left (up to `hops` steps back). `rows` limits
    the map to those reports (from `pick_reports`); by default every current report is drawn."""
    rows = reports(cat, workbooks=workbooks, show_old=show_old, old_versions=old_versions) if rows is None else rows
    g = flow_graph(cat, start={r["key"] for r in rows}, downstream=False, show_old=show_old, hops=hops)
    biggest = max([r["size"] for r in rows] or [1]) or 1
    for r in rows:
        n = g.nodes.get(r["key"])
        if n is None:  # loads nothing at all: still show it, on its own
            n = g.nodes[r["key"]] = {"label": r["name"], "kind": r["kind"], "key": r["key"], "role": "output"}
        n["group"] = "📊 Power BI reports" if r["kind"] == "report" else "📗 Excel workbooks"
        bits = [_count(r["pages"], "page"), _count(r["visuals"], "visual"), _count(r["measures"], "measure")] \
            if r["kind"] == "report" else [_count(r["pages"], "sheet")]
        if r["older versions"]:
            bits.append(f"+{r['older versions']} older")
        n["label"] = f"{r['name']}\n" + " · ".join(bits) + (f"\n⚠️ {r['flag']}" if r["flag"] else "")
        n["weight"] = round(100 * r["size"] / biggest) or 1
        if r["flag"]:
            n["flag"] = True
    return g


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"
