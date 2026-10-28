"""Report labels: the names Power BI reports show for columns, suggested as business names for the
SQL columns behind them.

A model column renamed in Power Query (`Table.RenameColumns(..., {{"Units", "Units sold"}})`), a field
renamed in a visual, and the title of a visual that shows a single column all say what people call
that column. The SQL column behind it is found through the report's Power Query source table (and
the rename, if there is one). Each becomes a suggestion; accepting it sets the column's business
name, which shows on its table page and is searchable.
"""
from __future__ import annotations

import re
from typing import Any

from . import suggestions
from .store import Catalogue

_RENAME = re.compile(r"Table\.RenameColumns\s*\(")
_PAIR = re.compile(r'\{\s*"((?:[^"]|"")+)"\s*,\s*"((?:[^"]|"")+)"\s*\}')


def m_renames(expression: str) -> dict[str, str]:
    """new name -> old name, from every Table.RenameColumns in a Power Query expression."""
    out: dict[str, str] = {}
    for m in _RENAME.finditer(expression or ""):
        depth, i, start = 0, m.end(), m.end()
        while i < len(expression):
            ch = expression[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            i += 1
        for old, new in _PAIR.findall(expression[start:i]):
            out[new.replace('""', '"')] = old.replace('""', '"')
    return out


def _same(a: str, b: str) -> bool:
    return re.sub(r"[\s_]", "", a).lower() == re.sub(r"[\s_]", "", b).lower()


def report_labels(cat: Catalogue, report_key: str) -> list[dict[str, Any]]:
    """[{"key", "column", "label", "where"}] for one report."""
    a = (cat.get_asset(report_key) or {}).get("attrs") or {}
    sources: dict[str, tuple[str, dict[str, str]]] = {}   # model table -> (SQL table key, new -> old)
    for q in a.get("queries", []):
        if "parameter" in q or not q.get("tables"):
            continue
        cands = cat.match_table(q["tables"][0].lower(), (q.get("databases") or [None])[0])
        if len(cands) == 1:
            sources[q["name"]] = (cands[0]["key"], m_renames(q.get("expression", "")))
    if not sources:
        return []
    columns = {}
    for key, _ in sources.values():
        columns[key] = {r[0].lower(): r[0] for r in cat.conn.execute("SELECT name FROM columns WHERE asset_key = ?",
                                                                      (key,))}

    def sql_column(table: str, model_col: str) -> tuple[str, str] | None:
        if table not in sources:
            return None
        key, renames = sources[table]
        old = renames.get(model_col, model_col)
        real = columns[key].get(old.lower())
        return (key, real) if real else None

    out = []
    for table, (key, renames) in sources.items():
        for new, old in renames.items():
            hit = sql_column(table, new)
            if hit and not _same(hit[1], new):
                out.append({"key": hit[0], "column": hit[1], "label": new, "where": f"renamed in Power Query “{table}”"})
    for p in a.get("pages", []):
        for v in p.get("visuals", []):
            for ref, label in (v.get("labels") or {}).items():
                m = re.match(r"^(?:\w+\()?([^.()]+)\.([^()]+)\)?$", ref)
                if not m:
                    continue
                hit = sql_column(m.group(1), m.group(2))
                if hit and not _same(hit[1], label):
                    out.append({"key": hit[0], "column": hit[1], "label": label,
                                "where": f"field name in a visual on “{p['name']}”"})
            cols = [f for f in v.get("fields", []) if f["kind"] == "column"]
            if v.get("title") and len(v.get("fields", [])) == 1 and len(cols) == 1:
                hit = sql_column(cols[0]["table"], cols[0]["name"])
                if hit and not _same(hit[1], v["title"]):
                    out.append({"key": hit[0], "column": hit[1], "label": v["title"],
                                "where": f"title of a visual on “{p['name']}”"})
    return out


def find_labels(cat: Catalogue) -> int:
    """Suggest business names for SQL columns from every report's labels."""
    named = {(r[0], r[1].lower()) for r in cat.conn.execute(
        "SELECT asset_key, column_name FROM column_info WHERE business_name IS NOT NULL AND business_name != ''")}
    found: dict[str, dict[str, Any]] = {}
    for r in cat.conn.execute("SELECT key, name FROM assets WHERE kind = 'report' AND status = 'active'"):
        for lab in report_labels(cat, r["key"]):
            if (lab["key"], lab["column"].lower()) in named:
                continue
            sid = suggestions.make_id("label", f"{lab['key']}#{lab['column']}", lab["label"].lower())
            s = found.setdefault(sid, {
                "id": sid, "subject": lab["key"],
                "title": f"{lab['column']} is “{lab['label']}”",
                "payload": {"key": lab["key"], "column": lab["column"], "label": lab["label"], "where": []},
                "score": 0})
            where = f"{r['name']}: {lab['where']}"
            if where not in s["payload"]["where"]:
                s["payload"]["where"].append(where)
                s["score"] += 1
    for s in found.values():
        table = (cat.get_asset(s["subject"]) or {}).get("name", s["subject"])
        s["title"] = f"{table}.{s['payload']['column']} is called “{s['payload']['label']}”"
        s["detail"] = "Use it as the column's business name. Seen in " + "; ".join(s["payload"]["where"][:4])
    return suggestions.sync(cat, "label", found.values())


@suggestions.handler("label")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    from .curation import set_column_extra
    set_column_extra(cat, p["key"], p["column"], "business_name", p["label"])
