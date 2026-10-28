"""Suggested metrics: calculations found in your code and reports that aren't in the Metric glossary.

  * Python functions with metric-like names (`wape()`, `forecast_accuracy()`, `fill_rate()`), with
    what they return as the formula;
  * named calculations (`mape = (abs(a - f) / a).mean()`);
  * computed SQL columns (`SUM(shipped) / SUM(ordered) AS fill_rate`) in the SQL query library;
  * Power BI measures not yet in the glossary.

Each suggestion shows where it was found and the formula; you tick the ones to add, and the ones you
dismiss don't come back.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any

from . import suggestions
from .store import Catalogue

# Words that make a name a metric: forecast accuracy and error, service and inventory measures, rates.
_WORDS = (r"w?mape|s?mape|wape|mase|rmse|mse|mae|bias|accuracy|error|fill_?rate|otif|service_?level|"
          r"turns|days_?on_?hand|doh|dos|coverage|margin|rate|ratio|share|pct|percent|growth|yield|utili[sz]ation|"
          r"velocity|lead_?time|cycle_?time|stock_?outs?|backorders?|forecast_?value_?add|fva|tracking_?signal")
METRIC_NAME = re.compile(rf"(?:^|_)(?:{_WORDS})(?:$|_)", re.I)
MAX_FORMULA = 400


def _short(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= MAX_FORMULA else text[:MAX_FORMULA] + " …"


def python_candidates(tree: ast.Module) -> list[dict[str, Any]]:
    """Metric-like functions and named calculations in one module: [{"name", "formula", "line", "how"}]."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and METRIC_NAME.search(node.name) \
                and not node.name.startswith(("test_", "_test", "plot", "print", "log", "check")):
            rets = [n.value for n in ast.walk(node) if isinstance(n, ast.Return) and n.value is not None]
            formula = ast.unparse(rets[-1]) if rets else ""
            # a returned local name: show what it was computed as
            if rets and isinstance(rets[-1], ast.Name):
                for n in ast.walk(node):
                    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == rets[-1].id
                                                         for t in n.targets):
                        formula = ast.unparse(n.value)
            out.append({"name": node.name, "formula": _short(formula), "line": node.lineno, "how": "function",
                        "doc": (ast.get_docstring(node) or "").split("\n\n")[0][:300]})
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and METRIC_NAME.search(node.targets[0].id) and isinstance(node.value, (ast.BinOp, ast.Call)) \
                and _has_arithmetic(node.value):
            out.append({"name": node.targets[0].id, "formula": _short(ast.unparse(node.value)), "line": node.lineno,
                        "how": "calculation"})
    return out[:100]


def _has_arithmetic(node: ast.AST) -> bool:
    return any(isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Div, ast.Sub, ast.Mult)) for n in ast.walk(node)) \
        or any(isinstance(n, ast.Attribute) and n.attr in ("mean", "sum", "abs") for n in ast.walk(node))


def _select_items(sql: str) -> list[str]:
    """The items of every SELECT list, split at top-level commas."""
    out = []
    low = sql.lower()
    for m in re.finditer(r"\bselect\b", low):
        depth, i, start, items = 0, m.end(), m.end(), []
        while i < len(sql):
            ch = sql[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and ch == ",":
                items.append(sql[start:i])
                start = i + 1
            elif depth == 0 and re.match(r"\bfrom\b|\binto\b", low[i:i + 5]) and (i == 0 or not low[i - 1].isalnum()):
                break
            i += 1
        items.append(sql[start:i])
        out += [x.strip() for x in items if x.strip()]
    return out


def sql_candidates(sql: str) -> list[dict[str, Any]]:
    """Computed SQL columns that look like metrics: a ratio or aggregate given a name."""
    from .sqlrefs import strip_comments
    out = []
    for item in _select_items(strip_comments(sql or "")):
        m = re.match(r"^(.+?)\s+as\s+\[?(\w+)\]?$", item, re.I | re.S)
        if not m:
            continue
        expr, alias = m.group(1).strip(), m.group(2)
        expr = re.sub(r"^(?:distinct\s+)?(?:top\s*\(?\d+\)?\s+)?", "", expr, flags=re.I)
        ratio = "/" in expr and re.search(r"\b(sum|count|avg)\s*\(", expr, re.I)
        if ratio or (METRIC_NAME.search(alias) and re.search(r"[/*-]|\b(sum|avg|count)\s*\(", expr, re.I)):
            out.append({"name": alias, "formula": _short(expr), "how": "computed SQL column"})
    return out


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _pretty(name: str) -> str:
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).replace("_", " ").strip()
    acronyms = {"mape", "wape", "smape", "wmape", "mase", "rmse", "mse", "mae", "otif", "fva", "doh", "dos"}
    return " ".join(w.upper() if w.lower() in acronyms else w for w in words.split()).capitalize() \
        if not any(w.lower() in acronyms for w in words.split()) else \
        " ".join(w.upper() if w.lower() in acronyms else w.lower() for w in words.split())


def find_metrics(cat: Catalogue) -> int:
    from .knowledge import list_metrics
    have = {_norm(m["name"]) for m in list_metrics(cat)}
    linked = {m.get("from_measure") for m in list_metrics(cat) if m.get("from_measure")}
    found: dict[str, dict[str, Any]] = {}

    def add(name: str, formula: str, where: str, key: str, how: str, extra: dict | None = None) -> None:
        n = _norm(name)
        if not n or n in have:
            return
        sid = suggestions.make_id("metric", n)
        s = found.setdefault(sid, {"id": sid, "subject": key, "title": _pretty(name), "score": 0,
                                   "payload": {"name": _pretty(name), "formula": formula, "places": [],
                                               "sources": []}})
        p = s["payload"]
        if len(p["places"]) < 20 and where not in p["places"]:
            p["places"].append(where)
            s["score"] += 1
        if key not in p["sources"]:
            p["sources"].append(key)
        if extra:
            for k, v in extra.items():
                p.setdefault(k, v)

    for r in cat.conn.execute("SELECT key, name, attrs FROM assets WHERE status = 'active' AND kind IN "
                              "('script', 'notebook')"):
        for c in json.loads(r["attrs"]).get("metric_candidates") or []:
            add(c["name"], c["formula"], f"{r['name']} line {c['line']} ({c['how']})", r["key"], c["how"],
                {"definition": c.get("doc", "")} if c.get("doc") else None)
    from .sqllibrary import all_with_places
    for q in all_with_places(cat):
        for c in sql_candidates(q["sql"]):
            item = q["places"][0]["item"] if q["places"] else f"query://{q['uid']}"
            add(c["name"], c["formula"], f"SQL “{q['name']}”", item, c["how"])
    for r in cat.conn.execute("SELECT key, name, attrs, description FROM assets WHERE status = 'active' AND "
                              "kind = 'measure'"):
        if r["key"] in linked:
            continue
        a = json.loads(r["attrs"])
        add(a.get("name") or r["name"], a.get("expression", ""), f"Power BI measure {r['name']}", r["key"],
            "measure", {"from_measure": r["key"], "definition": r["description"] or ""})
    for s in found.values():
        s["detail"] = f"Formula: {s['payload']['formula'] or '(not read)'} · found in " + "; ".join(
            s["payload"]["places"][:4]) + (" …" if len(s["payload"]["places"]) > 4 else "")
    return suggestions.sync(cat, "metric", found.values())


@suggestions.handler("metric")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    from .knowledge import save_metric
    save_metric(cat, p["name"], definition=p.get("definition", ""), formula=p.get("formula", ""),
                sources=p.get("sources", [])[:10], from_measure=p.get("from_measure", ""))
