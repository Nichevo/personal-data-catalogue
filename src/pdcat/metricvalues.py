"""Likely values for metrics: the range a metric usually takes, from the profiles of the columns it is built on.

A metric's formula names its inputs (Table.Column, Table[Column], or a bare column of one of the tables it comes
from). Each input column already has a value profile from scans (min, max, percentiles). From those:

  * a metric that is one column added up, averaged or counted takes that column's range as it is;
  * a ratio or difference of two inputs gets an estimate from their percentiles: the middle from the medians,
    the ends from the quartiles, labelled "estimated";
  * anything else lists the inputs' ranges, so a reader still knows what size the numbers are.

A "Usual range" you type on the metric page, with a note ("below 0.7 means a load failed"), wins over the
estimate and is marked as yours. The range shows on the metric page, in search and in the Copilot pack, so a
plausible answer can be told from a wrong one without opening Power BI.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .store import Catalogue

_REF = re.compile(r"(?:'([^']+)'|([A-Za-z_][\w ]*?))\s*(?:\.|\[)\s*([A-Za-z_][\w ]*)\]?")
_WORD = re.compile(r"[A-Za-z_][\w]*")
AGGREGATES = {"sum", "avg", "average", "mean", "count", "distinctcount", "countrows", "min", "max", "total"}
FUNCTIONS = AGGREGATES | {"abs", "divide", "calculate", "filter", "if", "iferror", "coalesce", "round", "sumx", "averagex",
                          "blank", "isblank", "and", "or", "not", "values", "all", "related", "case", "when", "then",
                          "else", "end", "null", "nullif", "cast", "as", "float", "int", "over", "partition", "by"}


def _percentiles(p: dict) -> dict[str, float] | None:
    raw = p.get("percentiles")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    out = {}
    for k, v in raw.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            return None  # dates: no arithmetic
    return out or None


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def inputs(cat: Catalogue, metric: dict[str, Any]) -> list[dict[str, Any]]:
    """The profiled columns a metric's formula names, each with its profile: [{"table", "column", "key",
    "profile"}]. Columns are looked up in the metric's source tables first, then anywhere by table name."""
    formula = metric.get("formula") or ""
    sources = metric.get("sources") or []
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(key: str, column: str) -> None:
        if (key, column.lower()) in seen:
            return
        p = cat.conn.execute("SELECT * FROM profiles WHERE asset_key = ? AND lower(column_name) = lower(?)",
                             (key, column)).fetchone()
        if p is None:
            return
        seen.add((key, column.lower()))
        a = cat.get_asset(key)
        found.append({"table": a["name"] if a else key, "column": p["column_name"], "key": key, "profile": dict(p)})

    for m in _REF.finditer(formula):
        table = (m.group(1) or m.group(2) or "").strip()
        column = m.group(3).strip()
        if table.lower() in FUNCTIONS or not column:
            continue
        for key in sources:
            a = cat.get_asset(key)
            if a and a["name"].lower().split(".")[-1] == table.lower().split(".")[-1]:
                add(key, column)
        for r in cat.conn.execute("SELECT key FROM assets WHERE status = 'active' AND kind IN ('table', 'view', "
                                  "'data_file', 'file_series') AND lower(name) LIKE ? LIMIT 3",
                                  (f"%{table.lower()}",)):
            add(r["key"], column)
    bare = {w.lower() for w in _WORD.findall(re.sub(_REF, " ", formula))} - FUNCTIONS
    for key in sources:
        for p in cat.profile(key):
            if p["column_name"].lower() in bare:
                add(key, p["column_name"])
    return found


def _range_text(lo: float | None, hi: float | None, mid: float | None = None) -> str:
    def f(v: float) -> str:
        return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.3g}"
    if lo is None or hi is None:
        return ""
    text = f"{f(lo)} to {f(hi)}"
    if mid is not None:
        text += f" (middle about {f(mid)})"
    return text


def estimate(cat: Catalogue, metric: dict[str, Any]) -> dict[str, Any] | None:
    """{"kind": "measured" | "estimated" | "inputs", "text", "low", "high", "middle", "inputs": [...]} or None
    when nothing profiled is named."""
    ins = inputs(cat, metric)
    if not ins:
        return None
    formula = (metric.get("formula") or "").strip()
    ranges = []
    for i in ins:
        p = i["profile"]
        pct = _percentiles(p)
        lo, hi = (pct.get("p1"), pct.get("p99")) if pct else (_num(p.get("min_value")), _num(p.get("max_value")))
        ranges.append({"label": f"{i['table'].split('/')[-1]}.{i['column']}", "low": lo, "high": hi,
                       "middle": pct.get("p50") if pct else None, "q1": pct.get("p25") if pct else lo,
                       "q3": pct.get("p75") if pct else hi, "measured": p.get("profiled_at", "")[:10]})
    usable = [r for r in ranges if r["low"] is not None and r["high"] is not None]
    out: dict[str, Any] = {"inputs": ranges}
    low = formula.lower()
    if len(usable) == 1 and len(ins) == 1:
        r = usable[0]
        agg = next((a for a in AGGREGATES if re.search(rf"\b{a}\s*\(", low)), None)
        if agg in ("count", "distinctcount", "countrows"):
            rows = ins[0]["profile"].get("row_count") or 0
            out.update(kind="measured", low=0, high=rows, middle=None,
                       text=f"a count up to {rows:,} ({r['label']} rows when measured {r['measured']})")
        elif agg in ("sum", "total"):
            rows = ins[0]["profile"].get("row_count") or 0
            mean = _num(ins[0]["profile"].get("mean_value"))
            total = mean * rows if mean is not None else None
            out.update(kind="estimated", low=r["low"], high=total, middle=None,
                       text=f"a total of {r['label']} values; one value is {_range_text(r['low'], r['high'], r['middle'])}"
                            + (f", about {total:,.0f} over all {rows:,} rows" if total is not None else ""))
        else:
            out.update(kind="measured", low=r["low"], high=r["high"], middle=r["middle"],
                       text=f"typically {_range_text(r['low'], r['high'], r['middle'])} (from {r['label']}, measured "
                            f"{r['measured']})")
        return out
    if len(usable) == 2 and len(ins) == 2:
        a, b = usable
        is_ratio = "/" in formula or "divide(" in low
        is_diff = bool(re.search(r"\s-\s|-\s*\(|\)\s*-", formula)) and not is_ratio
        if is_ratio and b["q1"] and b["q3"] and b["q1"] > 0:
            lo, hi = a["q1"] / b["q3"], a["q3"] / b["q1"]
            mid = a["middle"] / b["middle"] if a["middle"] and b["middle"] else None
            out.update(kind="estimated", low=lo, high=hi, middle=mid,
                       text=f"estimated {_range_text(lo, hi, mid)} (a ratio of {a['label']} to {b['label']}, from "
                            "their percentiles; not measured)")
            return out
        if is_diff:
            lo, hi = a["q1"] - b["q3"], a["q3"] - b["q1"]
            mid = a["middle"] - b["middle"] if a["middle"] is not None and b["middle"] is not None else None
            out.update(kind="estimated", low=lo, high=hi, middle=mid,
                       text=f"estimated {_range_text(lo, hi, mid)} ({a['label']} minus {b['label']}, from their "
                            "percentiles; not measured)")
            return out
    out.update(kind="inputs", low=None, high=None, middle=None,
               text="inputs: " + "; ".join(f"{r['label']} {_range_text(r['low'], r['high'], r['middle'])}"
                                            for r in usable))
    return out


def usual_values(cat: Catalogue, metric: dict[str, Any]) -> dict[str, Any] | None:
    """What to show for a metric: your typed range first, else the estimate. {"text", "source", "note"}."""
    if metric.get("usual_range"):
        return {"text": metric["usual_range"], "source": "yours", "note": metric.get("range_note") or "",
                "estimate": None}
    est = estimate(cat, metric)
    if est is None:
        return None
    return {"text": est["text"], "source": est["kind"], "note": metric.get("range_note") or "", "estimate": est}


def set_usual_range(cat: Catalogue, uid: str, usual_range: str, note: str = "") -> None:
    with cat.conn:
        cat.conn.execute("UPDATE metrics SET usual_range = ?, range_note = ? WHERE uid = ?",
                         (usual_range.strip() or None, note.strip() or None, uid))
    cat.rebuild_search()
