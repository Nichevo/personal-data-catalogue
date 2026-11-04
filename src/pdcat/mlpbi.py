"""When an ML opportunity's target is a Power BI measure (version 2.30): where its numbers come from, and the
report's other measures as feature ideas.

- **Original data sources**: the model columns the measure is built from (every step, through the measures and
  calculated columns it uses), the model tables they sit in, and what each table's Power Query reads: SQL tables,
  views and files (catalogued ones can be added to the opportunity). That is what a model would be trained on.
- **Other measures in the report**: each with the model tables it reads and its complexity. Measures the target
  is built from, or that are built on the target, are marked as leaking the answer; the rest are feature ideas,
  those reading the same tables first.

Everything is worked out from the scanned .pbix / .pbit / .pbip files. Nothing calls the Power BI service.
"""
from __future__ import annotations

import re
from typing import Any

from .store import Catalogue

MAX_OTHER = 40
_LABEL = re.compile(r"^(.*)\[(.*)\]$")


def _table_of(label: str) -> str:
    m = _LABEL.match(label)
    return m.group(1).strip("'") if m else ""


def _walk(u: dict[str, Any], start: str) -> tuple[set[str], set[str]]:
    """(measures, columns) a measure is built from, every step (through calculated columns too)."""
    meas, cols = set(), set()
    todo = [start]
    seen = {start}
    while todo:
        cur = todo.pop()
        item = u["measures"].get(cur) or u["columns"].get(cur) or {}
        for d in item.get("uses_measures", []):
            if d not in seen:
                seen.add(d)
                meas.add(d)
                todo.append(d)
        for d in item.get("uses_columns", []):
            if d not in seen:
                seen.add(d)
                cols.add(d)
                if (u["columns"].get(d) or {}).get("calculated"):
                    todo.append(d)
    return meas, cols


def _built_on(u: dict[str, Any], start: str) -> set[str]:
    """Measures built on this one, every step."""
    out: set[str] = set()
    todo = [start]
    while todo:
        cur = todo.pop()
        item = u["measures"].get(cur) or u["columns"].get(cur) or {}
        for d in item.get("used_by", []):
            if d not in out and d in u["measures"]:
                out.add(d)
                todo.append(d)
    return out


def _context(cat: Catalogue, measure_key: str) -> tuple[dict[str, Any], str, dict[str, Any], str] | None:
    """(measure asset, its label, the model's usage, model report key)."""
    from .pbimpact import _label, usage
    m = cat.get_asset(measure_key)
    if m is None or not m.get("parent"):
        return None
    model = m["parent"]
    u = usage(cat, model)
    lab = _label(m["attrs"].get("table", ""), m["attrs"].get("name") or m["name"])
    if lab not in u["measures"]:
        lab = next((k for k, v in u["measures"].items() if v["key"] == measure_key), lab)
    if lab not in u["measures"]:
        return None
    return m, lab, u, model


def measure_sources(cat: Catalogue, measure_key: str) -> dict[str, Any] | None:
    """Where a measure's numbers come from: {"measure", "report", "columns": [Table[Column]], "tables":
    [{"table", "columns": [...], "sources": [{"label", "key", "kind", "catalogued", "query"}]}], "sources":
    [unique catalogued and uncatalogued sources]}."""
    from .pathbits import file_name
    from .pbiobjects import table_key, table_view
    ctx = _context(cat, measure_key)
    if ctx is None:
        return None
    m, lab, u, model = ctx
    _meas, cols = _walk(u, lab)
    by_table: dict[str, list[str]] = {}
    for c in sorted(cols):
        t = (u["columns"].get(c) or {}).get("table") or _table_of(c)
        if t:
            by_table.setdefault(t, []).append((u["columns"].get(c) or {}).get("column") or c)
    own = _table_of(lab)
    if own and own in u["tables"] and own not in by_table:
        by_table[own] = []
    tables, seen, sources = [], set(), []
    for t, tcols in sorted(by_table.items()):
        v = table_view(cat, table_key(model, t)) or {}
        srcs = []
        for step in v.get("lineage") or []:
            for s in step["sources"]:
                srcs.append({**s, "query": step["query"]})
                ident = s.get("key") or s["label"]
                if ident not in seen:
                    seen.add(ident)
                    sources.append({**s, "query": step["query"], "model_table": t})
        tables.append({"table": t, "columns": tcols, "sources": srcs, "calculated": bool(v.get("calculated"))})
    rep = cat.get_asset(model) or {"name": model}
    return {"measure": m["attrs"].get("name") or m["name"], "label": lab,
            "report": file_name(rep["name"], "report"), "report_key": model,
            "columns": sorted(cols), "tables": tables, "sources": sources}


def other_measures(cat: Catalogue, measure_key: str, limit: int = MAX_OTHER) -> list[dict[str, Any]]:
    """The report's other measures as feature ideas: [{"key", "measure", "tables", "shared", "level", "detail",
    "relation", "leaks", "shown"}], measures that share the target's tables first. `relation` says when one is
    part of the target ("the target is built from it") or built on it ("built on the target"): both leak."""
    from .mlopps import complexity
    ctx = _context(cat, measure_key)
    if ctx is None:
        return []
    _m, lab, u, _model = ctx
    parts, cols = _walk(u, lab)
    target_tables = {(u["columns"].get(c) or {}).get("table") or _table_of(c) for c in cols} | {_table_of(lab)}
    target_tables.discard("")
    above = _built_on(u, lab)
    out = []
    for other, mm in u["measures"].items():
        if other == lab:
            continue
        _om, ocols = _walk(u, other)
        tables = sorted({(u["columns"].get(c) or {}).get("table") or _table_of(c) for c in ocols} - {""}) or \
            [_table_of(other)]
        shared = sorted(set(tables) & target_tables)
        relation = ("the target is built from it" if other in parts else
                    "built on the target" if other in above else "")
        level, why = complexity(mm["expression"], len(mm.get("visuals") or []))
        out.append({"key": mm["key"], "measure": _LABEL.match(other).group(2) if _LABEL.match(other) else other,
                    "label": other, "tables": tables, "shared": shared, "level": level, "detail": why,
                    "relation": relation, "leaks": bool(relation), "shown": bool(mm.get("visuals"))})
    rank = {"Complex": 0, "Medium": 1, "Simple": 2}
    out.sort(key=lambda r: (r["leaks"], not r["shared"], not r["shown"], rank.get(r["level"], 3),
                            r["measure"].lower()))
    return out[:limit]


def target_measure_keys(x: Any) -> list[str]:
    return [i["item"] for i in x.by_role("target", "measure")]
