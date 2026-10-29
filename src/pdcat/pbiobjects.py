"""The tables and Power Query queries inside a Power BI report or Excel workbook, as items of their own.

They aren't scanned assets: everything comes from what the report's scan already read, so their pages work
straight away without a rescan. A table's key is "pbitable://<report key>::<table>" and a query's key is
"pq://<report or workbook key>::<query>". Notes and follow-ups attach to those keys like to any other item.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .store import Catalogue
from .textclean import as_text

TABLE_PREFIX = "pbitable://"
QUERY_PREFIX = "pq://"
SEP = "::"


def table_key(parent: str, name: str) -> str:
    return f"{TABLE_PREFIX}{parent}{SEP}{name}"


def query_key(parent: str, name: str) -> str:
    return f"{QUERY_PREFIX}{parent}{SEP}{name}"


def split_key(key: str) -> tuple[str, str, str] | None:
    """("table" or "query", parent key, name), or None for any other key."""
    for prefix, kind in ((TABLE_PREFIX, "table"), (QUERY_PREFIX, "query")):
        if key.startswith(prefix):
            parent, sep, name = key[len(prefix):].partition(SEP)
            if sep and parent and name:
                return kind, parent, name
    return None


def is_object_key(key: str) -> bool:
    return split_key(key) is not None


def display_name(key: str) -> str | None:
    """"Sales (in Sales.pbix)" for lists like follow-ups and recently viewed."""
    parts = split_key(key)
    if parts is None:
        return None
    _, parent, name = parts
    file = re.split(r"[/\\]", parent)[-1]
    return f"{name} (in {file})"


def _queries(attrs: dict) -> list[dict[str, Any]]:
    return attrs.get("queries") or []


def _table_names(attrs: dict) -> list[str]:
    return [t["name"] for t in attrs.get("tables") or []]


def _split_end(end: str, tables: list[str]) -> tuple[str, str]:
    """"Sales Orders.StoreId" -> ("Sales Orders", "StoreId"), knowing table names may hold dots."""
    for t in sorted(tables, key=len, reverse=True):
        if end.startswith(t + "."):
            return t, end[len(t) + 1:]
    table, _, col = end.rpartition(".")
    return table, col


def query_sources(cat: Catalogue, parent: str, q: dict[str, Any]) -> list[dict[str, Any]]:
    """What one query reads: catalogued tables and files open their own page (key), the rest are listed."""
    how = f"Power Query “{q['name']}”"
    out, seen = [], set()
    for link in cat.links_from(parent):
        if link["how"] != how:
            continue
        ref = link["dst_ref"]
        label = link["dst_name"] or ref.partition(":")[2] or ref
        kind = link["dst_kind"] or ("data_file" if ref.startswith("file:") else "table")
        out.append({"label": label, "key": link["dst_key"] or ref, "kind": kind,
                    "catalogued": bool(link["dst_key"])})
        seen.add(ref.partition(":")[2].lower())
    for t in q.get("tables", []):
        if t.lower() not in seen:
            out.append({"label": t, "key": f"table:{t.lower()}", "kind": "table", "catalogued": False})
    for f in q.get("files", []):
        if f.lower() not in seen:
            out.append({"label": f, "key": f"file:{f}", "kind": "data_file", "catalogued": False})
    for kind, what in (("folders", "folder"), ("web", "web or SharePoint"), ("odbc", "ODBC")):
        for v in q.get(kind, []):
            out.append({"label": f"{v} ({what})", "key": None, "kind": "data_source", "catalogued": False})
    return out


def upstream(cat: Catalogue, parent: str, attrs: dict, name: str) -> list[dict[str, Any]]:
    """Every query this one builds on (every step), each with what it reads: the lineage back to the data."""
    by_name = {q["name"]: q for q in _queries(attrs)}
    out, todo, seen = [], [name], set()
    while todo:
        n = todo.pop(0)
        if n in seen or n not in by_name:
            continue
        seen.add(n)
        q = by_name[n]
        out.append({"query": n, "key": query_key(parent, n), "sources": query_sources(cat, parent, q),
                    "uses": list(q.get("uses", []))})
        todo += q.get("uses", [])
    return out


def _uses_parameter(q: dict, name: str) -> bool:
    body = as_text(q.get("expression"))
    pattern = r'#"' + re.escape(name.replace('"', '""')) + '"' if not re.fullmatch(r"[A-Za-z_]\w*", name) \
        else r"(?<![\w.#\"])" + re.escape(name) + r"(?![\w\"])"
    return bool(re.search(pattern, re.sub(r'(?<!#)"(?:[^"]|"")*"', '""', body)))


def query_view(cat: Catalogue, key: str) -> dict[str, Any] | None:
    """Everything a query's page shows. None when the key isn't a query; "missing" when the report no
    longer has it."""
    parts = split_key(key)
    if parts is None or parts[0] != "query":
        return None
    _, parent_key, name = parts
    parent = cat.get_asset(parent_key)
    if parent is None:
        return {"missing": True, "name": name, "parent": None}
    attrs = parent["attrs"]
    q = next((x for x in _queries(attrs) if x["name"] == name), None)
    base = {"name": name, "parent": {"key": parent_key, "name": parent["name"], "kind": parent["kind"]}}
    if q is None:
        return {**base, "missing": True}
    others = [x for x in _queries(attrs) if x["name"] != name]
    view: dict[str, Any] = {**base, "missing": False, "expression": as_text(q.get("expression")),
                            "parameter": q.get("parameter"), "sql": list(q.get("sql", [])),
                            "servers": list(q.get("servers", [])), "databases": list(q.get("databases", [])),
                            "sources": query_sources(cat, parent_key, q) if "parameter" not in q else [],
                            "uses": [{"name": n, "key": query_key(parent_key, n)} for n in q.get("uses", [])],
                            "used_by": [{"name": x["name"], "key": query_key(parent_key, x["name"])} for x in others
                                        if name in x.get("uses", []) or ("parameter" in q and _uses_parameter(x, name))],
                            "loads": []}
    if parent["kind"] == "report":
        if name in _table_names(attrs):
            view["loads"].append({"label": name, "key": table_key(parent_key, name), "kind": "table"})
    else:
        for s in attrs.get("sheets") or []:
            if name in (s.get("loaded_from") or []):
                view["loads"].append({"label": f"sheet {s['name']}", "key": None, "kind": "workbook"})
    view["lineage"] = upstream(cat, parent_key, attrs, name) if "parameter" not in q else []
    return view


def table_view(cat: Catalogue, key: str) -> dict[str, Any] | None:
    """Everything a model table's page shows: columns, measures, relationships, the query that loads it,
    the lineage back to the data, and the pages that show it."""
    parts = split_key(key)
    if parts is None or parts[0] != "table":
        return None
    _, report_key, name = parts
    report = cat.get_asset(report_key)
    if report is None:
        return {"missing": True, "name": name, "report": None}
    attrs = report["attrs"]
    t = next((x for x in attrs.get("tables") or [] if x["name"] == name), None)
    base = {"name": name, "report": {"key": report_key, "name": report["name"], "kind": report["kind"]}}
    if t is None:
        return {**base, "missing": True}
    tables = _table_names(attrs)
    measures = []
    for c in cat.children(report_key):
        if c["kind"] != "measure":
            continue
        m = json.loads(c["attrs"] or "{}")
        if m.get("table") == name:
            measures.append({"name": m.get("name") or c["name"], "key": c["key"],
                             "expression": as_text(m.get("expression")), "format": m.get("format") or "",
                             "folder": m.get("folder") or "", "description": c["description"] or ""})
    rels = []
    for r in attrs.get("relationships") or []:
        ft, fc = _split_end(r["from"], tables)
        tt, tc = _split_end(r["to"], tables)
        for mine, col, other, ocol, side in ((ft, fc, tt, tc, "many"), (tt, tc, ft, fc, "one")):
            if mine == name:
                rels.append({"column": col, "other": other, "other_column": ocol, "side": side,
                             "active": r.get("active", True) is not False,
                             "other_key": table_key(report_key, other) if other in tables else None})
                break
    query = next((q for q in _queries(attrs) if q["name"] == name and "parameter" not in q), None)
    used_on = []
    for p in attrs.get("pages") or []:
        for v in p.get("visuals", []):
            fields = [f["name"] for f in v.get("fields", []) if f.get("table") == name]
            if fields:
                used_on.append({"page": p["name"], "visual": v.get("title") or v.get("type") or "",
                                "fields": fields})
    return {**base, "missing": False, "calculated": bool(t.get("calculated")),
            "columns": [{"name": c["name"], "data_type": c.get("data_type") or "", "calculated": bool(c.get("calculated")),
                         "expression": as_text(c.get("expression"))} for c in t.get("columns", [])],
            "measures": sorted(measures, key=lambda m: m["name"].lower()), "relationships": rels,
            "query": {"name": query["name"], "key": query_key(report_key, query["name"])} if query else None,
            "lineage": upstream(cat, report_key, attrs, name) if query else [], "used_on": used_on}


def all_objects(cat: Catalogue, what: str, old_versions: bool = False) -> list[dict[str, Any]]:
    """Every model table ("tables") or Power Query query ("queries") in the scanned reports (and, for queries,
    workbooks), one row each, for the Power BI browse list. Older versions of a report are left out unless
    `old_versions`."""
    from .groups import older_versions
    kinds = ("report",) if what == "tables" else ("report", "workbook")
    skip = set() if old_versions else older_versions(cat)
    out = []
    for r in cat.conn.execute(f"SELECT key, name, kind, source, attrs FROM assets WHERE status = 'active' AND kind IN "
                              f"({','.join('?' * len(kinds))}) ORDER BY name", kinds):
        if r["key"] in skip:
            continue
        attrs = json.loads(r["attrs"] or "{}")
        if what == "tables":
            homes: dict[str, int] = {}
            for m in attrs.get("measures") or []:
                homes[m.get("table")] = homes.get(m.get("table"), 0) + 1
            for t in attrs.get("tables") or []:
                out.append({"key": table_key(r["key"], t["name"]), "name": t["name"], "in": r["name"],
                            "where": r["source"], "columns": len(t.get("columns", [])),
                            "measures": homes.get(t["name"], 0), "calculated": "yes" if t.get("calculated") else ""})
        else:
            for q in attrs.get("queries") or []:
                reads = q.get("tables", []) + q.get("files", []) + q.get("folders", []) + q.get("web", [])
                out.append({"key": query_key(r["key"], q["name"]), "name": q["name"], "in": r["name"],
                            "where": r["source"], "type": "parameter" if "parameter" in q else "query",
                            "reads": ", ".join(reads), "builds on": ", ".join(q.get("uses", []))})
    return out
