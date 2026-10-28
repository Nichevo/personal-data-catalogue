"""Main and backup tables: the same table on two servers (a SQL Server table and its Azure SQL copy
with extra history, say). Pairs are suggested when a table with the same schema and name, and nearly
the same columns, turns up in two sources; you confirm, and pick the main one.

The suggested main one is the one more of your code reads, then the one with fewer rows (a backup
often keeps extra history). Confirmed pairs show side by side on both pages, and as one table on the
code map and in lineage.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from . import groups, suggestions
from .store import Catalogue

MIN_OVERLAP = 0.8   # share of the smaller table's columns the two must have in common


def find_backups(cat: Catalogue) -> int:
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in cat.conn.execute("SELECT key, name, source, attrs FROM assets WHERE kind = 'table' AND status = 'active'"):
        a = json.loads(r["attrs"])
        if a.get("in_access"):
            continue
        name = f"{a.get('schema', '')}.{a.get('object', '')}".lower()
        by_name[name].append({"key": r["key"], "name": r["name"], "source": r["source"], "rows": a.get("row_count"),
                              "server": a.get("server", "")})
    in_groups = {k for g in groups.all_groups(cat, "backup") for k in _members(cat, g["uid"])}
    readers = defaultdict(int)
    for r in cat.conn.execute("SELECT dst_key, COUNT(DISTINCT src_key) FROM links WHERE relation = 'reads' AND "
                              "dst_key IS NOT NULL GROUP BY dst_key"):
        readers[r[0]] = r[1]
    found = []
    for name, items in by_name.items():
        sources = {i["source"] for i in items}
        if len(items) != 2 or len(sources) != 2 or any(i["key"] in in_groups for i in items):
            continue
        a, b = items
        ca, cb = _cols(cat, a["key"]), _cols(cat, b["key"])
        if not ca or not cb:
            continue
        overlap = len(ca & cb) / max(min(len(ca), len(cb)), 1)  # a backup may add columns of its own
        if overlap < MIN_OVERLAP:
            continue
        main, other = sorted(items, key=lambda i: (-readers[i["key"]], i["rows"] if i["rows"] is not None else 1e18))
        extra = (cb - ca) if main is a else (ca - cb)
        names = {r[0].lower(): r[0] for r in cat.conn.execute("SELECT name FROM columns WHERE asset_key = ?",
                                                              (other["key"],))}
        only = sorted(names.get(c, c) for c in extra)
        detail = (f"{len(ca & cb)} columns in common" + (f"; only the backup has {', '.join(only[:5])}" if only else "")
                  + f". Main suggested: {main['source']} ({readers[main['key']]} script(s) read it).")
        found.append({"id": suggestions.make_id("backup", "|".join(sorted(i["key"] for i in items))),
                      "subject": main["key"], "title": f"{main['name']}: {main['source']} and {other['source']} look "
                                                       "like a main table and its backup",
                      "detail": detail, "score": round(overlap * 100),
                      "payload": {"type": "backup", "name": main["name"],
                                  "members": {main["key"]: f"main ({main['source']})",
                                              other["key"]: f"backup ({other['source']})"},
                                  "main_key": main["key"]}})
    return suggestions.sync(cat, "backup", found)


def _cols(cat: Catalogue, key: str) -> set[str]:
    return {r[0].lower() for r in cat.conn.execute("SELECT name FROM columns WHERE asset_key = ?", (key,))}


def _members(cat: Catalogue, uid: str) -> list[str]:
    return [r[0] for r in cat.conn.execute("SELECT asset_key FROM group_members WHERE group_uid = ?", (uid,))]


def pair_with(cat: Catalogue, main_key: str, backup_key: str) -> str:
    """Link two tables by hand as main and backup."""
    a, b = cat.get_asset(main_key), cat.get_asset(backup_key)
    return groups.create(cat, "backup", a["name"], {main_key: f"main ({a['source']})",
                                                    backup_key: f"backup ({b['source']})"}, main_key=main_key)
