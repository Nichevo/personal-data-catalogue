"""Ways to organise long suggestion lists, so thousands of rows become a short list of groups.

Each suggestion gets a group for every "Organise by" choice of its kind (`views`), and, where the same
suggestion repeats on many items, a "same" signature so the repeats show as one row ("Plant is SAP WERKS ·
in 312 tables") that you accept or reject in one go:

  * SAP meanings: by column name, SAP field, SAP area (sales, purchasing, planning...), table or file, and
    folder or database. The same column name meaning the same field is one suggestion.
  * Metrics found in code: by first word of the name (Sales…, Forecast…), kind of metric (accuracy and
    error, rates and ratios...), the script, report or query it was found in, its folder, and where found
    (Python, SQL, Power BI).
  * Anything else: by folder or database, and by the group the triage gave it.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from .pathbits import file_name, joined, short_folder, split_name
from .store import Catalogue

# SAP field or table -> the area of SAP it belongs to (general SAP knowledge, the same in every company).
SAP_AREAS: dict[str, tuple[str, ...]] = {
    "Material master": ("MATNR", "MTART", "MATKL", "MAKTX", "MEINS", "CHARG", "MARA", "MARC", "MARD", "MAKT", "MBEW"),
    "Organisation (plants, company, sales org)": ("WERKS", "LGORT", "BUKRS", "VKORG", "VTWEG", "SPART", "EKORG",
                                                  "T001W", "T001L"),
    "Sales and delivery (SD)": ("KUNNR", "VBELN", "POSNR", "KWMENG", "LFIMG", "NETWR", "EDATU", "WADAT_IST", "KNA1",
                                "VBAK", "VBAP", "VBEP", "LIKP", "LIPS", "VBRK", "VBRP"),
    "Purchasing": ("LIFNR", "EBELN", "EBELP", "LFA1", "EKKO", "EKPO", "EKET", "EKBE"),
    "Inventory and stock movements": ("BWART", "LABST", "MENGE", "BUDAT", "MKPF", "MSEG", "MATDOC"),
    "Planning and production (MRP, PP)": ("DISPO", "DISMM", "PLIFZ", "EISBE", "BSTMI", "RESB", "PLAF", "AFKO", "AFPO"),
    "Finance and controlling": ("PRCTR", "KOSTL", "WAERS"),
    "Warehouse (EWM/WM)": ("LGNUM", "LGPLA", "HUIDENT"),
}
_AREA_OF = {name: area for area, names in SAP_AREAS.items() for name in names}

# Kinds of metric, by the words in the name; the first match wins.
METRIC_KINDS: list[tuple[str, re.Pattern]] = [
    ("Forecast accuracy and error", re.compile(r"mape|wape|mase|rmse|mse|mae|bias|accura|error|fva|tracking", re.I)),
    ("Service and delivery", re.compile(r"fill.?rate|otif|service|on.?time|backorder|stock.?out|late", re.I)),
    ("Stock and coverage", re.compile(r"stock|inventor|days.?on.?hand|doh|dos|coverage|turns|soh", re.I)),
    ("Time", re.compile(r"lead.?time|cycle.?time|days|weeks|hours|duration|age", re.I)),
    ("Money and margin", re.compile(r"margin|cost|price|revenue|sales.?value|value|profit|spend|amount|€|\$", re.I)),
    ("Growth and change", re.compile(r"growth|change|vs|delta|diff|\bly\b|ytd|mtd|prior|last.?year", re.I)),
    ("Rates, ratios and shares", re.compile(r"rate|ratio|share|pct|percent|%|utili[sz]|yield", re.I)),
    ("Counts and totals", re.compile(r"count|total|sum|number|qty|quantity|units|volume", re.I)),
]
OTHER = "Other"
VIEWS = {
    "sap": ("Column name", "SAP field", "SAP area", "Table or file", "Folder or database"),
    "metric": ("First word", "Kind of metric", "Found in", "Folder", "Where found"),
}
DEFAULT_VIEWS = ("Folder or database", "Group")


def views(kind: str) -> tuple[str, ...]:
    return VIEWS.get(kind, DEFAULT_VIEWS)


def sap_area(name: str | None) -> str:
    return _AREA_OF.get((name or "").upper(), OTHER)


def metric_kind(name: str | None) -> str:
    for label, pat in METRIC_KINDS:
        if pat.search(name or ""):
            return label
    return OTHER


def first_word(name: str | None) -> str:
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", name or "").replace("_", " ").split()
    words = [w for w in words if not re.fullmatch(r"[\W\d]+", w)]
    return words[0].capitalize() if words else OTHER


def homes(cat: Catalogue, keys: Iterable[str]) -> dict[str, tuple[str, str]]:
    """key -> (the item's short name, its folder or database), for grouping. A measure's home is its report
    (file name) and that report's folder; a table's is its database on its source; a file's is its folder."""
    keys = [k for k in dict.fromkeys(keys) if k]
    out: dict[str, tuple[str, str]] = {}
    rows: dict[str, dict[str, Any]] = {}
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        for r in cat.conn.execute(f"SELECT key, kind, name, source, parent, attrs FROM assets WHERE key IN "
                                  f"({','.join('?' * len(chunk))})", chunk):
            rows[r["key"]] = dict(r)
    parents = [r["parent"] for r in rows.values() if r["kind"] == "measure" and r.get("parent")]
    for i in range(0, len(parents), 500):
        chunk = parents[i:i + 500]
        for r in cat.conn.execute(f"SELECT key, kind, name, source, parent, attrs FROM assets WHERE key IN "
                                  f"({','.join('?' * len(chunk))})", chunk):
            rows.setdefault(r["key"], dict(r))
    for k in keys:
        if k.startswith("query://"):
            out[k] = ("SQL library", "SQL library")
            continue
        r = rows.get(k)
        if r is None:
            out[k] = (k.rsplit("/", 1)[-1], OTHER)
            continue
        if r["kind"] == "measure" and rows.get(r.get("parent") or ""):
            r = rows[r["parent"]]
        if r["kind"] in ("table", "view", "procedure"):
            attrs = json.loads(r["attrs"] or "{}")
            db = attrs.get("database") or (r.get("parent") or "").rsplit("/", 1)[-1]
            out[k] = (r["name"], f"🛢️ {r['source']} › {db}" if db else f"🛢️ {r['source']}")
            continue
        file, rel = split_name(r["name"], r["kind"])
        out[k] = (file, f"🗂️ {short_folder(joined(r['source'], rel), 3)}")
    return out


def organise(cat: Catalogue, kind: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows with "by" ({Organise by choice: group}) and "same" (a signature shared by repeats, or None)."""
    subjects = [r.get("subject") or "" for r in rows]
    if kind == "metric":
        subjects += [s for r in rows for s in (r["payload"].get("sources") or [])[:1]]
    home = homes(cat, subjects)
    out = []
    for r in rows:
        p = r.get("payload") or {}
        subj = r.get("subject") or ""
        item, folder = home.get(subj, ("", OTHER))
        if kind == "sap":
            field = p.get("sap_name") or ""
            col = p.get("column")
            by = {"Column name": f"{col} → {field}" if col else f"Table names with {field}",
                  "SAP field": r.get("group") or f"SAP {field}", "SAP area": sap_area(field),
                  "Table or file": item or OTHER, "Folder or database": folder}
            same = f"{col.lower()}→{field}" if col else None
        elif kind == "metric":
            name = p.get("name") or r.get("title") or ""
            src = (p.get("sources") or [subj])[0]
            item, folder = home.get(src, (item, folder))
            by = {"First word": first_word(name), "Kind of metric": metric_kind(name), "Found in": item or OTHER,
                  "Folder": folder, "Where found": r.get("group") or OTHER}
            same = None
        else:
            by = {"Folder or database": folder, "Group": r.get("group") or OTHER}
            same = None
        out.append({**r, "by": by, "same": same, "item": file_name(item)})
    return out
