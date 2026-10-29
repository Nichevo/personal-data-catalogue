"""Search results as a table: one row per result with the facts worth sorting on, file families as one row,
quick filters by kind, grouping, and the metric ↔ measure cross-links."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .store import Catalogue

QUICK_FILTERS: dict[str, tuple[str, ...]] = {
    "Tables": ("table", "view", "procedure", "database"),
    "Files": ("data_file", "file_series", "folder"),
    "Code": ("script", "notebook", "sql_script", "sql_snippet", "query", "package", "project", "model"),
    "Reports and measures": ("report", "workbook", "measure", "access_db"),
    "Metrics": ("metric",),
    "Knowledge": ("knowledge", "question", "feature", "key", "bproject", "document"),
}
GROUP_BY = ("Not at all", "Script that creates it", "Family", "Folder or database", "Business project")
NOT_LINKED = {"Script that creates it": "Not linked to a script", "Family": "Not in a family",
              "Folder or database": "", "Business project": "Not in a business project"}


def _snippet(s: str | None) -> str:
    s = " ".join((s or "").split())
    return s[:200] if "**" in s else ""


def rows_for(cat: Catalogue, results: list[dict[str, Any]], *, families_as_one: bool = True) -> list[dict[str, Any]]:
    """Table rows for search results. Members of one file family become a single row for the family."""
    keys = [r["key"] for r in results]
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    assets = {r["key"]: dict(r) for r in cat.conn.execute(
        f"SELECT key, kind, name, source, parent, status, last_seen, attrs FROM assets WHERE key IN ({marks})", keys)}
    used_by = dict(cat.conn.execute(
        f"SELECT dst_key, COUNT(DISTINCT src_key) FROM links WHERE dst_key IN ({marks}) AND relation = 'reads' "
        "GROUP BY dst_key", keys).fetchall())
    makers: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in cat.conn.execute(
            f"SELECT l.dst_key, l.src_key, a.name FROM links l LEFT JOIN assets a ON a.key = l.src_key "
            f"WHERE l.dst_key IN ({marks}) AND l.relation = 'writes'", keys):
        makers[r[0]].append((r[1], r[2] or r[1]))
    families: dict[str, tuple[str, str]] = {}
    for r in cat.conn.execute(
            f"SELECT m.asset_key, g.uid, g.name FROM group_members m JOIN item_groups g ON g.uid = m.group_uid "
            f"WHERE g.type = 'family' AND m.asset_key IN ({marks})", keys):
        families[r[0]] = (r[1], r[2])
    projects: dict[str, list[str]] = defaultdict(list)
    for r in cat.conn.execute(
            f"SELECT m.asset_key, p.name FROM bproject_members m JOIN bprojects p ON p.uid = m.project_uid "
            f"WHERE m.state = 'confirmed' AND m.asset_key IN ({marks})", keys):
        projects[r[0]].append(r[1])

    # A measure lives in a report: show the report, not just the scan group, and find its folder from it.
    parent_keys = list({a["parent"] for a in assets.values() if a["kind"] == "measure" and a.get("parent")})
    reports: dict[str, dict[str, Any]] = {}
    if parent_keys:
        reports = {p["key"]: {"name": p["name"], "path": json.loads(p["attrs"] or "{}").get("path") or ""}
                   for p in cat.conn.execute(f"SELECT key, name, attrs FROM assets WHERE key IN "
                                             f"({','.join('?' * len(parent_keys))})", parent_keys)}

    out: list[dict[str, Any]] = []
    family_rows: dict[str, dict[str, Any]] = {}
    for r in results:
        a = assets.get(r["key"]) or {}
        attrs = json.loads(a["attrs"]) if a.get("attrs") else {}
        report = reports.get(a.get("parent") or "") if r["kind"] == "measure" else None
        row = {
            "key": r["key"], "kind": r["kind"], "name": r["name"],
            "where": _where(r, a, attrs, report),
            "description": _description(r, attrs),
            "matched on": _snippet(r.get("snippet")).replace("**", ""),
            "rows": attrs.get("row_count"),
            "changed": (attrs.get("modified") or a.get("last_seen") or "")[:10],
            "notes": r.get("note_count") or 0,
            "status": r.get("lifecycle") or ("missing" if r.get("status") == "missing" else ""),
            "trust": r.get("trust") or "",
            "used by": used_by.get(r["key"], 0),
            "made by": ", ".join(dict.fromkeys(n.rsplit("/", 1)[-1] for _, n in makers.get(r["key"], []))),
            "_makers": [k for k, _ in makers.get(r["key"], [])],
            "_family": families.get(r["key"]),
            "_projects": projects.get(r["key"], []),
            "_folder": _folder(r, a, {"path": report["path"]} if report else attrs),
        }
        fam = row["_family"]
        if families_as_one and fam:
            f = family_rows.get(fam[0])
            if f is None:
                f = {**row, "key": f"group://{fam[0]}", "kind": "group", "name": fam[1], "_members": [], "notes": 0,
                     "used by": 0}
                family_rows[fam[0]] = f
                out.append(f)
            f["_members"].append(row)
            f["notes"] += row["notes"]
            f["used by"] = max(f["used by"], row["used by"])
            continue
        out.append(row)
    for f in family_rows.values():
        grains = [m["name"].rsplit("/", 1)[-1] for m in f["_members"]]
        labels = _grain_labels(cat, f["key"].removeprefix("group://"))
        f["name"] = f"{f['name']}: " + ", ".join(labels or grains)
        f["description"] = f"File family · {len(f['_members'])} matching"
    return out


def _grain_labels(cat: Catalogue, uid: str) -> list[str]:
    return [r[0] for r in cat.conn.execute(
        "SELECT label FROM group_members WHERE group_uid = ? AND coalesce(label, '') != '' ORDER BY label", (uid,))]


def _description(r: dict[str, Any], attrs: dict[str, Any]) -> str:
    text = r.get("purpose") or (r.get("description") or "").split("\n", 1)[0]
    if not text and r["kind"] == "measure" and attrs.get("expression"):
        from .textclean import as_text
        text = " ".join(as_text(attrs["expression"]).split())  # the DAX on one line
    return text[:200]


def _where(r: dict[str, Any], a: dict[str, Any], attrs: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    if report:
        return f"{r.get('source') or ''} › {report['name']}"
    if r["kind"] in ("table", "view", "procedure"):
        return f"{r.get('source') or ''} › {attrs.get('database') or (a.get('parent') or '').rsplit('/', 1)[-1]}"
    path = attrs.get("path") or attrs.get("full_path") or ""
    if path:
        return f"{r.get('source') or ''} › {str(path).rsplit('/', 1)[0] if '/' in str(path) else path}"
    return r.get("source") or ""


def _folder(r: dict[str, Any], a: dict[str, Any], attrs: dict[str, Any]) -> str:
    if r["kind"] in ("table", "view", "procedure"):
        return f"🛢️ {attrs.get('database') or (a.get('parent') or r.get('source') or '').rsplit('/', 1)[-1]}"
    path = str(attrs.get("path") or attrs.get("full_path") or a.get("name") or "")
    if "/" in path:
        return f"🗂️ {r.get('source') or ''}/{path.rsplit('/', 1)[0]}"
    return f"🗂️ {r.get('source') or 'other'}"


def grouped(rows: list[dict[str, Any]], by: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """[(group heading, rows)] for a Group by choice; rows with nothing to group by come last."""
    if by == "Not at all":
        return [("", rows)]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if by == "Script that creates it":
            heads = [f"🐍 {m}" for m in r["made by"].split(", ") if m] if r["made by"] else []
        elif by == "Family":
            heads = [f"👪 {r['_family'][1]}"] if r["_family"] else ([f"👪 {r['name'].split(':')[0]}"]
                                                                   if r["kind"] == "group" else [])
        elif by == "Folder or database":
            heads = [r["_folder"]] if r["_folder"] else []
        else:
            heads = [f"🧭 {p}" for p in r["_projects"]]
        for h in heads[:1] or [NOT_LINKED[by] or "Other"]:
            groups[h].append(r)
    last = NOT_LINKED[by] or "Other"
    return sorted(groups.items(), key=lambda kv: (kv[0] == last, -len(kv[1]), kv[0].lower()))


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def metric_links(cat: Catalogue, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For metrics in the results, the measures and SQL queries that calculate them; for measures, their metric.
    [{"from": key, "from_name", "to": [(key, name, kind)]}]"""
    out = []
    metrics = [dict(r) for r in cat.conn.execute("SELECT uid, name, formula, sources FROM metrics")]
    by_norm = {_norm(m["name"]): m for m in metrics}
    for r in rows:
        if r["kind"] == "metric":
            m = next((x for x in metrics if f"metric://{x['uid']}" == r["key"]), None)
            if not m:
                continue
            n = _norm(m["name"])
            to = [(x["key"], x["name"], "measure") for x in cat.conn.execute(
                "SELECT key, name FROM assets WHERE kind = 'measure' AND status = 'active'") if _norm(x["name"]) == n]
            for s in json.loads(m["sources"] or "[]"):
                a = cat.get_asset(s)
                if a and a["kind"] == "measure" and all(t[0] != s for t in to):
                    to.append((s, a["name"], "measure"))
            to += [(f"query://{q['uid']}", q["name"], "query") for q in cat.conn.execute(
                "SELECT uid, name, sql FROM sql_queries WHERE status = 'active'")
                if _norm(q["name"]) == n or re.search(rf"(?i)\bas\s+\[?{re.escape(m['name'].replace(' ', '_'))}\]?\b",
                                                      q["sql"] or "")]
            if to:
                out.append({"from": r["key"], "from_name": r["name"], "to": to[:8], "what": "calculated by"})
        elif r["kind"] == "measure":
            m = by_norm.get(_norm(r["name"]))
            if m:
                out.append({"from": r["key"], "from_name": r["name"], "what": "is the metric",
                            "to": [(f"metric://{m['uid']}", m["name"], "metric")]})
    return out
