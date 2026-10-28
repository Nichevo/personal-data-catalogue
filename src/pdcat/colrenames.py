"""Column renames in lineage: `rename(columns={"Units": "qty"})` in Python, or `SELECT Units AS qty`
in SQL, links the two columns.

A rename links the column in the table or file it came from to the column in the table or file
the script writes (when that file has a column with the new name), or to the script itself when
the renamed column doesn't reach a catalogued output. Both names are shown with the script and
line, the new column is offered the old one's description as a suggestion, and column lineage
follows the chain through every rename.
"""
from __future__ import annotations

import json
from typing import Any

from . import suggestions
from .store import Catalogue

MAX_DEPTH = 10


def _columns(cat: Catalogue) -> dict[str, dict[str, str]]:
    """item -> {lower column name: column name}."""
    out: dict[str, dict[str, str]] = {}
    for r in cat.conn.execute("SELECT asset_key, name FROM columns"):
        out.setdefault(r[0], {})[r[1].lower()] = r[1]
    return out


def _io(cat: Catalogue) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    reads: dict[str, set[str]] = {}
    writes: dict[str, set[str]] = {}
    for r in cat.conn.execute("SELECT src_key, relation, dst_key FROM links WHERE dst_key IS NOT NULL AND "
                              "relation IN ('reads', 'writes')"):
        (reads if r[1] == "reads" else writes).setdefault(r[0], set()).add(r[2])
    return reads, writes


def find_renames(cat: Catalogue) -> int:
    """Work out every rename link again, and suggest descriptions for renamed columns. Returns how many links."""
    cols = _columns(cat)
    reads, writes = _io(cat)
    links: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}

    def add(src: str, sc: str, dst: str, dc: str, how: str, evidence: str, source: str) -> None:
        if src == dst and sc.lower() == dc.lower():
            return
        links.setdefault((src, cols.get(src, {}).get(sc.lower(), sc), dst, cols.get(dst, {}).get(dc.lower(), dc)),
                         (how, evidence, source))

    def outputs(item: str, column: str) -> list[str]:
        return [d for d in sorted(writes.get(item, ())) if column.lower() in cols.get(d, {})]

    # SQL: SELECT Units AS qty
    from .sqllibrary import all_with_places
    for q in all_with_places(cat):
        renames = q["analysis"].get("renames") or []
        if not renames:
            continue
        by_name = {t["name"]: t for t in q["tables"]}
        written = [t["key"] for t in q["tables"] if t.get("relation") == "writes" and t.get("key")]
        for r in renames:
            src = (by_name.get(r.get("table") or "") or {}).get("key")
            if not src:
                continue
            for p in q["places"] or [{"item": None, "location": "", "source": ""}]:
                evidence = f"{p['item']} ({p['location']})" if p["item"] else q["name"]
                targets = [w for w in written if w != src] or (outputs(p["item"], r["alias"]) if p["item"] else [])
                for dst in targets or ([p["item"]] if p["item"] else []):
                    add(src, r["column"], dst, r["alias"], "alias", evidence, p.get("source") or "")
    # Python: df.rename(columns={...})
    for row in cat.conn.execute("SELECT key, source, attrs FROM assets WHERE status = 'active' AND kind IN "
                                "('script', 'notebook')"):
        renames = json.loads(row["attrs"]).get("column_renames") or []
        for r in renames:
            srcs = [s for s in sorted(reads.get(row["key"], ())) if r["old"].lower() in cols.get(s, {})] or [row["key"]]
            dsts = outputs(row["key"], r["new"]) or [row["key"]]
            where = f"line {r['line']}" + (f" of cell {r['cell']}" if r.get("cell") else "")
            for s in srcs:
                for d in dsts:
                    add(s, r["old"], d, r["new"], "rename", f"{row['key']} ({where})", row["source"])
    with cat.conn:
        cat.conn.execute("DELETE FROM column_links WHERE how IN ('alias', 'rename')")
        cat.conn.executemany(
            "INSERT OR IGNORE INTO column_links (src_item, src_column, dst_item, dst_column, how, evidence, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", [(*k, *v) for k, v in links.items()])
    _suggest_descriptions(cat)
    return len(links)


def _descriptions(cat: Catalogue) -> dict[tuple[str, str], str]:
    out = {(r[0], r[1].lower()): r[2] for r in cat.conn.execute(
        "SELECT asset_key, name, description FROM columns WHERE description IS NOT NULL AND description != ''")}
    out.update({(r[0], r[1].lower()): r[2] for r in cat.conn.execute(
        "SELECT asset_key, column_name, description FROM column_info WHERE description IS NOT NULL AND "
        "description != ''")})
    return out


def _suggest_descriptions(cat: Catalogue) -> int:
    desc = _descriptions(cat)
    cols = _columns(cat)
    found = []
    for r in cat.conn.execute("SELECT * FROM column_links"):
        old = desc.get((r["src_item"], r["src_column"].lower()))
        if not old or desc.get((r["dst_item"], r["dst_column"].lower())):
            continue
        if r["dst_column"].lower() not in cols.get(r["dst_item"], {}):
            continue  # the new name only exists inside the script
        src_name = (cat.get_asset(r["src_item"]) or {}).get("name", r["src_item"])
        dst_name = (cat.get_asset(r["dst_item"]) or {}).get("name", r["dst_item"])
        found.append({"id": suggestions.make_id("rename", f"{r['dst_item']}#{r['dst_column']}"),
                      "subject": r["dst_item"], "title": f"{dst_name}.{r['dst_column']}: renamed from "
                                                         f"{src_name}.{r['src_column']}",
                      "detail": f"Use its description: “{old}”. Renamed in {r['evidence']}.",
                      "payload": {"key": r["dst_item"], "column": r["dst_column"], "description": old}})
    return suggestions.sync(cat, "rename", found)


@suggestions.handler("rename")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    from .curation import set_column_info
    set_column_info(cat, p["key"], p["column"], description=p["description"])


def renames_for(cat: Catalogue, key: str) -> dict[str, list[dict[str, Any]]]:
    """Renames into this item's columns ("from") and out of them ("to")."""
    return {"from": [dict(r) for r in cat.conn.execute(
                "SELECT * FROM column_links WHERE dst_item = ? ORDER BY dst_column", (key,))],
            "to": [dict(r) for r in cat.conn.execute(
                "SELECT * FROM column_links WHERE src_item = ? ORDER BY src_column", (key,))]}


def column_lineage(cat: Catalogue, key: str, column: str) -> dict[str, list[dict[str, Any]]]:
    """Follow a column through every rename: {"upstream": [...], "downstream": [...]}, each step
    {"item", "column", "how", "evidence", "depth"}."""
    def walk(direction: str) -> list[dict[str, Any]]:
        here, there = ("dst", "src") if direction == "up" else ("src", "dst")
        out, seen = [], {(key, column.lower())}
        frontier = [(key, column)]
        for depth in range(1, MAX_DEPTH + 1):
            nxt = []
            for item, col in frontier:
                for r in cat.conn.execute(
                        f"SELECT * FROM column_links WHERE {here}_item = ? AND lower({here}_column) = ?",
                        (item, col.lower())):
                    step = (r[f"{there}_item"], r[f"{there}_column"])
                    if (step[0], step[1].lower()) in seen:
                        continue
                    seen.add((step[0], step[1].lower()))
                    out.append({"item": step[0], "column": step[1], "how": r["how"], "evidence": r["evidence"],
                                "depth": depth})
                    nxt.append(step)
            if not nxt:
                break
            frontier = nxt
        return out
    return {"upstream": walk("up"), "downstream": walk("down")}
