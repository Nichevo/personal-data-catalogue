"""Summary tables: one row per Power BI file, database table or Python project with its counts, grouped the way
you choose, with totals per group and overall. Every count comes from what scans already stored, so the pages
cost nothing to open. The Power BI table can split each file into its pages.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from .store import Catalogue

PBI_GROUPS = ("Folder", "Subfolder", "Report group", "Business project", "App", "Owner", "Workspace", "(none)")
TABLE_GROUPS = ("Database", "Schema", "Subject area", "Business project", "Source", "(none)")
PYTHON_GROUPS = ("Folder", "Business project", "Role", "(none)")
COUNT_KEYS_PBI = ("connected reports", "pages", "visuals", "tables", "columns", "calculated columns", "measures",
                  "relationships", "queries", "data sources", "size MB")
COUNT_KEYS_TABLE = ("columns", "rows", "keys", "relationships", "used by scripts", "used by reports", "described columns",
                    "notes")
COUNT_KEYS_PYTHON = ("scripts", "notebooks", "functions", "lines", "tables read", "tables written", "files read",
                     "files written", "own imports", "home-built imports", "third-party imports")


def _attrs(r) -> dict:
    a = r["attrs"]
    return json.loads(a) if isinstance(a, str) else (a or {})


def _bproject_of(cat: Catalogue) -> dict[str, str]:
    """asset key -> business project name (the first one it belongs to)."""
    out: dict[str, str] = {}
    for r in cat.conn.execute("SELECT m.asset_key, p.name FROM bproject_members m JOIN bprojects p ON p.uid = m.project_uid "
                              "WHERE m.state = 'confirmed' ORDER BY p.name"):
        out.setdefault(r["asset_key"], r["name"])
    return out


def _report_group_of(cat: Catalogue) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in cat.conn.execute("SELECT m.asset_key, g.name FROM group_members m JOIN item_groups g ON g.uid = m.group_uid "
                              "WHERE g.type = 'versions'"):
        out[r["asset_key"]] = r["name"]
    return out


def _subfolder(name: str) -> str:
    return name.rsplit("/", 1)[0] if "/" in name else "(top level)"


# ---- Power BI ------------------------------------------------------------------------------------

def powerbi_rows(cat: Catalogue, *, old_versions: bool = False) -> list[dict[str, Any]]:
    """One row per .pbix / .pbit / .pbip with its counts and the fields the groupings use."""
    from .groups import older_versions
    skip = set() if old_versions else older_versions(cat)
    bprojects = _bproject_of(cat)
    groups = _report_group_of(cat)
    sources = Counter(r[0] for r in cat.conn.execute("SELECT src_key FROM links WHERE dst_key IN "
                                                      "(SELECT key FROM assets WHERE kind = 'data_source')"))
    try:
        from .pbimpact import model_matches
        matches = model_matches(cat)
    except Exception:  # the impact module is optional to this page
        matches = {}
    connected: Counter = Counter(m.get("model") for m in matches.values() if m.get("model"))
    try:
        from .register import rows_by_report
        register = rows_by_report(cat)
    except Exception:
        register = {}
    out = []
    for r in cat.conn.execute("SELECT key, name, source, attrs FROM assets WHERE kind = 'report' AND status = 'active' "
                              "ORDER BY name"):
        if r["key"] in skip:
            continue
        a = _attrs(r)
        tables = a.get("tables") or []
        columns = sum(len(t.get("columns") or []) for t in tables)
        calculated = sum(1 for t in tables for c in (t.get("columns") or []) if c.get("calculated"))
        pages = a.get("pages") or []
        reg = register.get(r["key"]) or {}
        row = {"key": r["key"], "report": r["name"], "Folder": r["source"], "Subfolder": _subfolder(r["name"]),
               "Report group": groups.get(r["key"], ""), "Business project": bprojects.get(r["key"], ""),
               "App": reg.get("app", ""), "Owner": reg.get("owner", ""), "Workspace": reg.get("workspace", ""),
               "model": "live connection" if a.get("live") else ("own" if tables else "not read"),
               "connected reports": connected.get(r["key"], 0), "pages": len(pages),
               "visuals": sum(len(p.get("visuals") or []) for p in pages), "tables": len(tables), "columns": columns,
               "calculated columns": calculated, "measures": len(a.get("measures") or []),
               "relationships": len(a.get("relationships") or []),
               "queries": sum(1 for q in a.get("queries") or [] if "parameter" not in q),
               "data sources": sources.get(r["key"], 0),
               "size MB": round((a.get("size_bytes") or 0) / 1024 ** 2, 1),
               "modified": str(a.get("modified") or "")[:10], "pages_detail": pages}
        out.append(row)
    return out


def powerbi_page_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same split into pages: one row per page with its visuals and the measures and columns they use."""
    out = []
    for r in rows:
        for p in r.get("pages_detail") or []:
            visuals = p.get("visuals") or []
            measures = {f["name"] for v in visuals for f in v.get("fields") or [] if f.get("kind") == "measure"}
            columns = {f"{f.get('table')}.{f.get('name')}" for v in visuals for f in v.get("fields") or []
                       if f.get("kind") == "column"}
            out.append({"key": r["key"], "report": r["report"], "page": p.get("name", ""), "visuals": len(visuals),
                        "measures": len(measures), "columns": len(columns),
                        "types": ", ".join(sorted(Counter(v.get("type") or "?" for v in visuals))[:6]),
                        **{g: r.get(g, "") for g in PBI_GROUPS if g != "(none)"}})
    return out


# ---- tables and views -----------------------------------------------------------------------------

def table_rows(cat: Catalogue) -> list[dict[str, Any]]:
    from .curation import hidden_keys
    hidden = hidden_keys(cat)
    bprojects = _bproject_of(cat)
    areas: dict[str, str] = {}
    for r in cat.conn.execute("SELECT m.asset_key, a.name FROM area_members m JOIN subject_areas a ON a.uid = m.area_uid"):
        areas.setdefault(r["asset_key"], r["name"])
    columns = Counter(r[0] for r in cat.conn.execute("SELECT asset_key FROM columns"))
    described = Counter(r[0] for r in cat.conn.execute(
        "SELECT c.asset_key FROM columns c LEFT JOIN column_info ci ON ci.asset_key = c.asset_key AND ci.column_name = c.name "
        "WHERE COALESCE(ci.description, c.description, '') != ''"))
    keys = Counter(r[0] for r in cat.conn.execute("SELECT asset_key FROM key_columns WHERE state = 'confirmed'"))
    rels: Counter = Counter()
    for r in cat.conn.execute("SELECT src_key, dst_key FROM relationships WHERE state IN ('declared', 'confirmed')"):
        rels[r[0]] += 1
        rels[r[1]] += 1
    notes = Counter(r[0].split("#", 1)[0] for r in cat.conn.execute("SELECT asset_key FROM notes WHERE archived = 0"))
    by_scripts: Counter = Counter()
    by_reports: Counter = Counter()
    for r in cat.conn.execute("SELECT l.dst_key, s.kind FROM links l JOIN assets s ON s.key = l.src_key "
                              "WHERE l.dst_key IS NOT NULL"):
        if r["kind"] in ("report", "workbook"):
            by_reports[r["dst_key"]] += 1
        else:
            by_scripts[r["dst_key"]] += 1
    last_change = {r[0]: r[1] for r in cat.conn.execute("SELECT asset_key, MAX(at) FROM schema_history GROUP BY asset_key")}
    out = []
    for r in cat.conn.execute("SELECT key, kind, name, source, attrs FROM assets WHERE status = 'active' AND kind IN "
                              "('table', 'view', 'data_file', 'file_series') ORDER BY name"):
        if r["key"] in hidden:
            continue
        a = _attrs(r)
        schema = a.get("schema") or (r["name"].split(".")[0] if r["kind"] in ("table", "view") and "." in r["name"] else "")
        out.append({"key": r["key"], "table": r["name"], "kind": r["kind"], "Database": a.get("database") or "",
                    "Schema": schema, "Subject area": areas.get(r["key"], ""),
                    "Business project": bprojects.get(r["key"], ""), "Source": r["source"],
                    "columns": columns.get(r["key"], 0), "rows": a.get("row_count") or 0, "keys": keys.get(r["key"], 0),
                    "relationships": rels.get(r["key"], 0), "used by scripts": by_scripts.get(r["key"], 0),
                    "used by reports": by_reports.get(r["key"], 0), "described columns": described.get(r["key"], 0),
                    "notes": notes.get(r["key"], 0), "last change": (last_change.get(r["key"]) or "")[:10]})
    return out


# ---- Python projects ------------------------------------------------------------------------------

def python_rows(cat: Catalogue) -> list[dict[str, Any]]:
    from .libclass import HOME, OWN, THIRD, Classifier
    bprojects = _bproject_of(cat)
    classifier = Classifier(cat)
    scripts: dict[str, list] = defaultdict(list)
    for r in cat.conn.execute("SELECT key, kind, parent, source, attrs FROM assets WHERE status = 'active' AND kind IN "
                              "('script', 'notebook', 'sql_script')"):
        scripts[r["parent"]].append(r)
    links: dict[str, Counter] = defaultdict(Counter)
    for r in cat.conn.execute("SELECT l.src_key, l.relation, l.dst_ref, s.parent FROM links l JOIN assets s ON s.key = l.src_key "
                              "WHERE s.kind IN ('script', 'notebook', 'sql_script')"):
        what = "tables" if r["dst_ref"].startswith("table:") else "files" if r["dst_ref"].startswith("file:") else None
        if what and r["relation"] in ("reads", "writes"):
            links[r["parent"]][f"{what} {'read' if r['relation'] == 'reads' else 'written'}"] += 1
    last_change = {r[0]: r[1] for r in cat.conn.execute("SELECT asset_key, MAX(at) FROM schema_history GROUP BY asset_key")}
    out = []
    for r in cat.conn.execute("SELECT key, name, source, attrs FROM assets WHERE status = 'active' AND kind = 'project' "
                              "ORDER BY name"):
        a = _attrs(r)
        mine = scripts.get(r["key"], [])
        imports: Counter = Counter()
        functions = lines = 0
        newest = ""
        for s in mine:
            sa = _attrs(s)
            functions += len(sa.get("functions") or [])
            lines += int(sa.get("lines") or 0)
            newest = max(newest, str(sa.get("modified") or ""), last_change.get(s["key"]) or "")
            for imp in sa.get("imports") or []:
                imports[classifier.classify(imp.split(".")[0], r["source"])] += 1
        env = a.get("environment") or {}
        out.append({"key": r["key"], "project": r["name"], "Folder": a.get("path") or r["source"],
                    "Business project": bprojects.get(r["key"], ""), "Role": a.get("role") or "project",
                    "scripts": sum(1 for s in mine if s["kind"] in ("script", "sql_script")),
                    "notebooks": sum(1 for s in mine if s["kind"] == "notebook"), "functions": functions, "lines": lines,
                    **{k: links[r["key"]].get(k, 0) for k in ("tables read", "tables written", "files read", "files written")},
                    "own imports": imports.get(OWN, 0), "home-built imports": imports.get(HOME, 0),
                    "third-party imports": imports.get(THIRD, 0),
                    "environment file": ", ".join(env.get("files") or []), "last change": newest[:10]})
    return out


# ---- grouping and totals --------------------------------------------------------------------------

def with_totals(rows: list[dict[str, Any]], group_by: str, count_keys: tuple[str, ...], label: str
                ) -> list[dict[str, Any]]:
    """Rows sorted by group with a totals row after each group and one at the end. Hidden fields (key,
    pages_detail) are dropped; the group column comes first."""
    keep = [k for k in (rows[0] if rows else {}) if k not in ("key", "pages_detail")]
    if group_by == "(none)":
        shown = [{k: r.get(k, "") for k in keep} for r in rows]
        return [*shown, _total(rows, count_keys, label, "Total", keep)] if rows else []
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[str(r.get(group_by) or "(none)")].append(r)
    out = []
    for g in sorted(groups, key=lambda x: (x == "(none)", x.lower())):
        for r in groups[g]:
            out.append({group_by: g, **{k: r.get(k, "") for k in keep if k != group_by}})
        out.append({group_by: g, **_total(groups[g], count_keys, label, f"Total ({len(groups[g])})", [k for k in keep if k != group_by])})
    out.append({group_by: "", **_total(rows, count_keys, label, f"Total ({len(rows)})", [k for k in keep if k != group_by])})
    return out


def _total(rows: list[dict], count_keys: tuple[str, ...], label: str, text: str, keep: list[str]) -> dict[str, Any]:
    t: dict[str, Any] = {k: "" for k in keep}
    t[label] = text
    for k in count_keys:
        if k in keep:
            total = sum(float(r.get(k) or 0) for r in rows)
            t[k] = round(total, 1) if k == "size MB" else int(total)
    return t
