"""Power BI impact, worked out from the .pbix / .pbit / .pbip files alone (2.3). Nothing here calls the
Power BI service.

A *thin report* has a live connection to a published dataset and no model of its own; the file that holds that
model is matched by the dataset's name (or by the report register's dataset column, or by hand). With the match
known, the model's measures and columns are looked up across every connected report:

  * which visuals, in which reports, show a measure or a column;
  * which measures and calculated columns a measure needs, step by step, and which need it;
  * columns and measures nothing scanned shows, labelled "not used in scanned files" (never "safe to
    delete": a report that wasn't scanned may still use them);
  * visuals that name a field the model no longer has: "check" items that say which model version was read.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .measures import dax_refs
from .store import Catalogue
from .textclean import as_text

MATCHES = "model_matches"     # {thin report key: model report key or ""} picked by hand
SHOW_UNUSED = "show_unused"   # lists show the "not used in scanned files" items too
NOT_USED = "not used in scanned files"


# ---- which model a thin report uses ---------------------------------------------------------------

def _reports(cat: Catalogue) -> list[dict[str, Any]]:
    from .groups import older_versions
    old = older_versions(cat)
    out = []
    for r in cat.conn.execute("SELECT key, name, source, attrs FROM assets WHERE kind = 'report' AND status = 'active' "
                              "ORDER BY name"):
        a = json.loads(r["attrs"])
        out.append({"key": r["key"], "name": r["name"], "stem": Path(r["name"]).stem, "source": r["source"],
                    "old": r["key"] in old, "thick": bool(a.get("tables")),
                    "dataset": as_text((a.get("live") or {}).get("dataset")).strip() if a.get("live") else "",
                    "live": bool(a.get("live"))})
    return out


def _clean(name: str) -> str:
    import re
    return re.sub(r"[\s_\-]+", " ", name.strip()).lower()


def model_matches(cat: Catalogue) -> dict[str, dict[str, Any]]:
    """thin report key -> {"model": report key or None, "how": "manual" | "register" | "name" | "words" | "",
    "candidates": [report keys], "dataset": the dataset name the file names}."""
    from .reportgroups import name_words
    reports = _reports(cat)
    thick = [r for r in reports if r["thick"]]
    manual = cat.setting(MATCHES, {})
    try:
        from .register import dataset_models
        by_dataset = dataset_models(cat)
    except Exception:
        by_dataset = {}
    by_clean: dict[str, list[dict]] = defaultdict(list)
    for r in thick:
        by_clean[_clean(r["stem"])].append(r)
    words = {r["key"]: name_words(r["stem"]) for r in thick}
    out: dict[str, dict[str, Any]] = {}
    for r in reports:
        if not r["live"] or r["thick"]:
            continue
        base = {"dataset": r["dataset"], "candidates": []}
        if r["key"] in manual:
            out[r["key"]] = {**base, "model": manual[r["key"]] or None, "how": "manual"}
            continue
        ds = r["dataset"].lower()
        if ds and by_dataset.get(ds):
            out[r["key"]] = {**base, "model": by_dataset[ds], "how": "register"}
            continue
        cands = by_clean.get(_clean(r["dataset"]), []) if r["dataset"] else []
        how = "name"
        if not cands and r["dataset"]:
            w = name_words(r["dataset"])
            cands = [t for t in thick if w and words[t["key"]] == w]
            how = "words"
        current = [c for c in cands if not c["old"]] or cands
        pick = current[0]["key"] if current else None
        out[r["key"]] = {**base, "model": pick, "how": how if pick else "", "candidates": [c["key"] for c in cands]}
    return out


def set_model(cat: Catalogue, thin_key: str, model_key: str | None) -> None:
    manual = cat.setting(MATCHES, {})
    manual[thin_key] = model_key or ""
    cat.set_setting(MATCHES, manual)


def forget_model(cat: Catalogue, thin_key: str) -> None:
    manual = cat.setting(MATCHES, {})
    if manual.pop(thin_key, None) is not None:
        cat.set_setting(MATCHES, manual)


def connected_reports(cat: Catalogue, model_key: str) -> list[dict[str, Any]]:
    """The thin reports matched to this model: [{"key", "name", "how"}]."""
    names = {r["key"]: r["name"] for r in cat.conn.execute("SELECT key, name FROM assets WHERE kind = 'report'")}
    return [{"key": k, "name": names.get(k, k), "how": m["how"]} for k, m in model_matches(cat).items()
            if m.get("model") == model_key]


# ---- what the model's measures and columns are used by ----------------------------------------

def _label(table: str, name: str) -> str:
    return f"{table}[{name}]"


def usage(cat: Catalogue, model_key: str) -> dict[str, Any]:
    """Everything the model's measures and columns are used by, across the model's own file and every
    connected report. {"reports": [{"key", "name"}], "measures": {Table[Name]: {...}}, "columns": {Table[Column]:
    {...}}, "tables": {name: {"used": bool, "columns_used": n, "columns": n}}}.

    A measure entry: key, used, why, visuals [(report, page, visual)], uses_measures, uses_columns, used_by
    (measures and calculated columns built on it). A column entry: used, why, visuals, in_measures, in_columns
    (calculated columns built on it), in_relationships, calculated."""
    model = cat.get_asset(model_key)
    if model is None:
        return {"reports": [], "measures": {}, "columns": {}, "tables": {}}
    a = model["attrs"]
    reports = [{"key": model_key, "name": model["name"]}] + [{"key": c["key"], "name": c["name"]}
                                                           for c in connected_reports(cat, model_key)]
    measures: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    for c in cat.children(model_key):
        if c["kind"] != "measure":
            continue
        m = json.loads(c["attrs"] or "{}")
        lab = _label(m.get("table", ""), m.get("name") or c["name"])
        names[(m.get("name") or c["name"]).lower()] = lab
        measures[lab] = {"key": c["key"], "expression": as_text(m.get("expression")), "visuals": [], "used_by": [],
                         "uses_measures": [], "uses_columns": [], "used": False, "why": NOT_USED}
    columns: dict[str, dict[str, Any]] = {}
    tables: dict[str, dict[str, Any]] = {}
    for t in a.get("tables") or []:
        tables[t["name"]] = {"used": False, "columns": len(t.get("columns") or []), "columns_used": 0}
        for col in t.get("columns") or []:
            columns[_label(t["name"], col["name"])] = {
                "table": t["name"], "column": col["name"], "calculated": bool(col.get("calculated")),
                "expression": as_text(col.get("expression")), "visuals": [], "in_measures": [], "in_columns": [],
                "in_relationships": [], "used": False, "why": NOT_USED}
    lower_cols = {k.lower(): k for k in columns}
    lower_meas = {k.lower(): k for k in measures}

    def col_key(label: str) -> str | None:
        return lower_cols.get(label.lower())

    # what each measure and calculated column is built from
    for lab, m in measures.items():
        um, uc = dax_refs(m["expression"], names)
        m["uses_measures"] = [x for x in um if x != lab and x in measures]
        m["uses_columns"] = [col_key(x) or x for x in uc]
        for x in m["uses_measures"]:
            measures[x]["used_by"].append(lab)
        for x in m["uses_columns"]:
            if x in columns:
                columns[x]["in_measures"].append(lab)
    for lab, c in columns.items():
        if c["calculated"] and c["expression"]:
            um, uc = dax_refs(c["expression"], names)
            c["uses_measures"] = [x for x in um if x in measures]
            c["uses_columns"] = [col_key(x) or x for x in uc if (col_key(x) or x) != lab]
            for x in c["uses_measures"]:
                measures[x]["used_by"].append(lab)
            for x in c["uses_columns"]:
                if x in columns:
                    columns[x]["in_columns"].append(lab)
    for r in a.get("relationships") or []:
        for end in (r.get("from", ""), r.get("to", "")):
            t, _, col = str(end).rpartition(".")
            k = col_key(_label(t, col))
            if k:
                columns[k]["in_relationships"].append(f"{r.get('from')} → {r.get('to')}")
    # visuals, in every connected report
    for rep in reports:
        asset = cat.get_asset(rep["key"]) if rep["key"] != model_key else model
        if asset is None:
            continue
        for p in asset["attrs"].get("pages") or []:
            for v in p.get("visuals") or []:
                where = (rep["name"], p.get("name", ""), v.get("title") or v.get("type") or "")
                for f in v.get("fields") or []:
                    lab = _label(f.get("table", ""), f.get("name", ""))
                    if f.get("kind") == "measure":
                        hit = lower_meas.get(lab.lower()) or names.get(f.get("name", "").lower())
                        if hit:
                            measures[hit]["visuals"].append(where)
                    else:
                        hit = col_key(lab)
                        if hit:
                            columns[hit]["visuals"].append(where)
    # used: shown somewhere, or needed by something used (every step)
    for lab, m in measures.items():
        if m["visuals"]:
            m["used"], m["why"] = True, "shown in " + _where(m["visuals"], reports)
    for lab, c in columns.items():
        if c["visuals"]:
            c["used"], c["why"] = True, "shown in " + _where(c["visuals"], reports)
        elif c["in_relationships"]:
            c["used"], c["why"] = True, "in a relationship"
    frontier = [lab for lab, m in measures.items() if m["used"]] + [lab for lab, c in columns.items() if c["used"]]
    while frontier:
        nxt = []
        for lab in frontier:
            item = measures.get(lab) or columns[lab]
            for dep in item.get("uses_measures", []):
                if not measures[dep]["used"]:
                    measures[dep]["used"], measures[dep]["why"] = True, f"needed by {lab}"
                    nxt.append(dep)
            for dep in item.get("uses_columns", []):
                if dep in columns and not columns[dep]["used"]:
                    columns[dep]["used"], columns[dep]["why"] = True, f"needed by {lab}"
                    nxt.append(dep)
        frontier = nxt
    for lab, c in columns.items():
        t = tables.get(c["table"])
        if t and c["used"]:
            t["columns_used"] += 1
            t["used"] = True
    for lab, m in measures.items():
        if m["used"]:
            t = tables.get(lab.split("[", 1)[0])
            if t:
                t["used"] = True
    return {"reports": reports, "measures": measures, "columns": columns, "tables": tables}


def _where(visuals: list[tuple], reports: list[dict]) -> str:
    n = len(visuals)
    reps = sorted({v[0] for v in visuals})
    if len(reports) <= 1:
        return f"{n} visual(s)"
    return f"{n} visual(s) in {', '.join(reps[:3])}" + (" …" if len(reps) > 3 else "")


def model_of(cat: Catalogue, report_key: str) -> str | None:
    """The report that holds the model this report uses: itself when it has one, else its matched model."""
    a = cat.get_asset(report_key)
    if a is None:
        return None
    if a["attrs"].get("tables"):
        return report_key
    return (model_matches(cat).get(report_key) or {}).get("model")


def connected_usage(cat: Catalogue, report_key: str) -> dict[str, list[str]]:
    """Measure label -> where connected reports (not this file) show it. Empty when nothing is connected."""
    if not connected_reports(cat, report_key):
        return {}
    u = usage(cat, report_key)
    out: dict[str, list[str]] = {}
    for lab, m in u["measures"].items():
        other = [f"{r}: {p}: {v}" for r, p, v in m["visuals"] if r != u["reports"][0]["name"]]
        if other:
            out[lab] = other
    return out


# ---- the chain behind one measure ------------------------------------------------------------------

def measure_chain(cat: Catalogue, measure_key: str) -> dict[str, Any] | None:
    """What one measure depends on (every step), what needs it, where it is shown, and what would change with it."""
    m = cat.get_asset(measure_key)
    if m is None or not m["parent"]:
        return None
    u = usage(cat, m["parent"])
    a = m["attrs"]
    lab = _label(a.get("table", ""), a.get("name") or m["name"])
    me = u["measures"].get(lab)
    if me is None:
        return None
    steps: list[dict[str, Any]] = []
    seen = {lab}
    frontier = [(lab, 0)]
    while frontier:
        cur, depth = frontier.pop(0)
        item = u["measures"].get(cur) or u["columns"].get(cur) or {}
        for dep in item.get("uses_measures", []):
            if dep not in seen:
                seen.add(dep)
                steps.append({"label": dep, "kind": "measure", "depth": depth + 1, "via": cur,
                              "key": u["measures"][dep]["key"]})
                frontier.append((dep, depth + 1))
        for dep in item.get("uses_columns", []):
            if dep not in seen:
                seen.add(dep)
                col = u["columns"].get(dep)
                steps.append({"label": dep, "kind": "calculated column" if col and col["calculated"] else "column",
                              "depth": depth + 1, "via": cur, "key": None})
                if col and col["calculated"]:
                    frontier.append((dep, depth + 1))
    needed_by: list[dict[str, Any]] = []
    seen = {lab}
    frontier = [lab]
    while frontier:
        cur = frontier.pop(0)
        item = u["measures"].get(cur) or u["columns"].get(cur) or {}
        for user in item.get("used_by", []) + item.get("in_measures", []) + item.get("in_columns", []):
            if user not in seen:
                seen.add(user)
                needed_by.append({"label": user, "kind": "measure" if user in u["measures"] else "calculated column",
                                  "key": (u["measures"].get(user) or {}).get("key")})
                frontier.append(user)
    visuals = list(me["visuals"])
    for n in needed_by:
        visuals += (u["measures"].get(n["label"]) or u["columns"].get(n["label"]) or {}).get("visuals", [])
    affected = sorted({(r, p, v) for r, p, v in visuals})
    return {"label": lab, "depends_on": steps, "needed_by": needed_by, "shown_in": me["visuals"],
            "affected": affected, "reports": u["reports"], "used": me["used"], "why": me["why"]}


# ---- lists: what is not used in scanned files -------------------------------------------------------

def show_unused(cat: Catalogue) -> bool:
    return bool(cat.setting(SHOW_UNUSED, False))


def set_show_unused(cat: Catalogue, value: bool) -> None:
    cat.set_setting(SHOW_UNUSED, bool(value))


def unused_keys(cat: Catalogue, what: str) -> set[str]:
    """Keys of the items nothing scanned uses: "measures", "tables" (model tables), "queries", "sql" (database
    tables, views, data files and series nothing reads), "reports" (never: a report is always shown)."""
    from .pbiobjects import query_key, table_key
    out: set[str] = set()
    if what == "sql":
        for r in cat.conn.execute("SELECT key FROM assets WHERE status = 'active' AND kind IN ('table', 'view', "
                                  "'data_file', 'file_series') AND key NOT IN (SELECT dst_key FROM links WHERE "
                                  "dst_key IS NOT NULL)"):
            out.add(r["key"])
        return out
    for r in cat.conn.execute("SELECT key, attrs FROM assets WHERE kind = 'report' AND status = 'active'"):
        a = json.loads(r["attrs"])
        if not a.get("tables"):
            continue
        u = usage(cat, r["key"])
        if what == "measures":
            out |= {m["key"] for m in u["measures"].values() if not m["used"]}
        elif what == "tables":
            out |= {table_key(r["key"], t) for t, v in u["tables"].items() if not v["used"]}
        elif what == "queries":
            loaded = set(u["tables"])
            used_by_query = {x for q in a.get("queries") or [] for x in q.get("uses", [])}
            for q in a.get("queries") or []:
                if "parameter" in q or q["name"] in loaded or q["name"] in used_by_query:
                    continue
                out.add(query_key(r["key"], q["name"]))
    return out


def column_rows(cat: Catalogue, old_versions: bool = False) -> list[dict[str, Any]]:
    """Every model column in every scanned model, with what uses it, for the Power BI › Columns list."""
    from .groups import older_versions
    from .pbiobjects import table_key
    skip = set() if old_versions else older_versions(cat)
    out = []
    for r in cat.conn.execute("SELECT key, name, source, attrs FROM assets WHERE kind = 'report' AND status = 'active' "
                              "ORDER BY name"):
        if r["key"] in skip or not json.loads(r["attrs"]).get("tables"):
            continue
        u = usage(cat, r["key"])
        for lab, c in u["columns"].items():
            out.append({"key": table_key(r["key"], c["table"]), "column": c["column"], "table": c["table"],
                        "in": r["name"], "where": r["source"], "calculated": "yes" if c["calculated"] else "",
                        "used": c["used"], "used by": c["why"], "visuals": len(c["visuals"]),
                        "in measures": ", ".join(c["in_measures"][:6]),
                        "reports": len({v[0] for v in c["visuals"]})})
    return out


# ---- visuals that name a field the model no longer has --------------------------------------------

def broken_visuals(cat: Catalogue) -> list[dict[str, Any]]:
    """Fields visuals ask for that the model read from the file doesn't have, per report."""
    matches = model_matches(cat)
    models: dict[str, dict] = {}
    out = []
    for r in cat.conn.execute("SELECT key, name, attrs FROM assets WHERE kind = 'report' AND status = 'active'"):
        a = json.loads(r["attrs"])
        model_key = r["key"] if a.get("tables") else (matches.get(r["key"]) or {}).get("model")
        if not model_key:
            continue
        if model_key not in models:
            m = cat.get_asset(model_key)
            if m is None or not m["attrs"].get("tables"):
                continue
            ma = m["attrs"]
            cols = {(t["name"].lower(), c["name"].lower()) for t in ma["tables"] for c in t.get("columns") or []}
            tabs = {t["name"].lower() for t in ma["tables"]}
            meas = {(mm.get("table", "").lower(), mm.get("name", "").lower()) for mm in ma.get("measures") or []}
            meas_names = {n for _, n in meas}
            version = str(ma.get("modified") or "")[:10]
            models[model_key] = {"name": m["name"], "cols": cols, "tabs": tabs, "meas": meas, "meas_names": meas_names,
                                 "version": version, "has_columns": any(t.get("columns") for t in ma["tables"])}
        mod = models[model_key]
        for p in a.get("pages") or []:
            for v in p.get("visuals") or []:
                for f in v.get("fields") or []:
                    table, name, kind = f.get("table", "").lower(), f.get("name", "").lower(), f.get("kind")
                    if not table or not name:
                        continue
                    if kind == "measure":
                        ok = (table, name) in mod["meas"] or name in mod["meas_names"] or not mod["meas"]
                    else:
                        ok = (table, name) in mod["cols"] or (not mod["has_columns"] and table in mod["tabs"])
                    if not ok:
                        out.append({"report_key": r["key"], "report": r["name"], "page": p.get("name", ""),
                                    "visual": v.get("title") or v.get("type") or "", "field": f"{f['table']}[{f['name']}]",
                                    "kind": kind, "model_key": model_key, "model": mod["name"],
                                    "model_version": mod["version"], "own": model_key == r["key"]})
    return out


def review_tasks(cat: Catalogue, task) -> list:
    """One "check" item per report whose visuals name fields the model doesn't have."""
    by_report: dict[str, list[dict]] = defaultdict(list)
    for b in broken_visuals(cat):
        by_report[b["report_key"]].append(b)
    out = []
    for key, rows in by_report.items():
        fields = sorted({b["field"] for b in rows})
        first = rows[0]
        where = "its own model" if first["own"] else f"the model {first['model']}"
        when = f" as read on {first['model_version']}" if first["model_version"] else ""
        title = f"{first['report']}: {len(fields)} field(s) its visuals use are not in {where}{when}"
        detail = "; ".join(f"{b['page']} › {b['visual']}: {b['field']}" for b in rows[:8]) + (" …" if len(rows) > 8 else "")
        detail += (". The field may have been renamed or removed in the model, or the visual may be broken. If the "
                   "model file you scanned is older than the published one, scan the newer file."
                   if not first["own"] else ". The field may have been renamed or removed; the visual would show an "
                   "error.")
        out.append(task("broken_visual", key, title, detail))
    return out
