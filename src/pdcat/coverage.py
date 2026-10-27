"""How well documented the catalogue is, and what to document next.

Each table, view, file, series, script, report and workbook gets a score out
of 100 for what you've filled in: a description or purpose, grain, owner,
refresh, column descriptions, notes and status. "Document these next" picks
the least documented items that matter most: the ones your code and reports
use, and the big ones.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Any

from .store import Catalogue

ITEM_KINDS = ("table", "view", "data_file", "file_series", "script", "notebook", "sql_script", "procedure",
              "report", "workbook", "model")
TABLE_KINDS = ("table", "view", "data_file", "file_series")


def scores(cat: Catalogue) -> list[dict[str, Any]]:
    info = {r["asset_key"]: dict(r) for r in cat.conn.execute("SELECT * FROM item_info")}
    notes = Counter(r[0].split("#", 1)[0] for r in cat.conn.execute("SELECT asset_key FROM notes WHERE archived = 0"))
    used = Counter(dict(cat.conn.execute("SELECT dst_key, COUNT(DISTINCT src_key) FROM links WHERE dst_key IS NOT NULL "
                                         "GROUP BY dst_key").fetchall()))
    cols: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in cat.conn.execute("SELECT c.asset_key, COALESCE(ci.description, c.description, '') != '' AS described "
                              "FROM columns c LEFT JOIN column_info ci ON ci.asset_key = c.asset_key AND "
                              "ci.column_name = c.name"):
        cols[c[0]][0] += 1
        cols[c[0]][1] += c[1]
    marks = ",".join(f"'{k}'" for k in ITEM_KINDS)
    out = []
    for a in cat.conn.execute(f"SELECT key, kind, name, source, description, attrs FROM assets WHERE status = 'active' "
                              f"AND kind IN ({marks})"):
        i = info.get(a["key"], {})
        attrs = json.loads(a["attrs"])
        is_table = a["kind"] in TABLE_KINDS
        parts = {
            "description": 30 if (a["description"] or i.get("purpose")) else 0,
            "grain": (10 if i.get("grain") else 0) if is_table else None,
            "owner": 10 if i.get("owner") else 0,
            "refresh": (5 if i.get("refresh") else 0) if is_table else None,
            "columns": (round(25 * cols[a["key"]][1] / cols[a["key"]][0]) if cols[a["key"]][0] else 0) if is_table
            else None,
            "notes": 10 if notes[a["key"]] else 0,
            "status": 10 if (i.get("lifecycle") or i.get("trust")) else 0,
        }
        possible = sum({"description": 30, "grain": 10, "owner": 10, "refresh": 5, "columns": 25, "notes": 10,
                        "status": 10}[k] for k, v in parts.items() if v is not None)
        got = sum(v for v in parts.values() if v)
        score = round(100 * got / possible) if possible else 0
        rows = attrs.get("row_count") or 0
        weight = 1 + 2 * min(used[a["key"]], 10) + math.log10(rows + 1)
        missing = [k for k, v in parts.items() if v == 0]
        out.append({"key": a["key"], "kind": a["kind"], "name": a["name"], "source": a["source"], "score": score,
                    "used_by": used[a["key"]], "rows": rows, "weight": round(weight, 2), "missing": missing})
    return out


def summary(cat: Catalogue) -> dict[str, Any]:
    rows = scores(cat)
    if not rows:
        return {"overall": 0, "weighted": 0, "items": 0, "by_source": [], "by_kind": []}
    weighted = sum(r["score"] * r["weight"] for r in rows) / sum(r["weight"] for r in rows)
    by_source: dict[str, list[int]] = defaultdict(list)
    by_kind: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        by_source[r["source"]].append(r["score"])
        by_kind[r["kind"]].append(r["score"])
    return {"overall": round(sum(r["score"] for r in rows) / len(rows)), "weighted": round(weighted),
            "items": len(rows),
            "by_source": sorted(({"source": s, "items": len(v), "score": round(sum(v) / len(v))}
                                 for s, v in by_source.items()), key=lambda x: x["score"]),
            "by_kind": sorted(({"kind": k, "items": len(v), "score": round(sum(v) / len(v))}
                               for k, v in by_kind.items()), key=lambda x: x["score"])}


def next_to_document(cat: Catalogue, n: int = 5) -> list[dict[str, Any]]:
    rows = [r for r in scores(cat) if r["score"] < 80]
    rows.sort(key=lambda r: -(100 - r["score"]) * r["weight"])
    return rows[:n]
