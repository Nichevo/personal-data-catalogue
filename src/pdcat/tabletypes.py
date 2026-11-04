"""What kind of table something is: a snapshot, a transaction table or a dimension (master data).

- **Snapshot**: the same rows copied again on each date (stock on hand every day). A date column such as
  SnapshotDate or ExtractDate with few distinct dates, each repeating the same keys, or a name like
  StockOnHand_Daily. Snapshot tables are where history lives for things SAP only keeps as "today".
- **Transaction**: one row per event (an order line, a goods receipt, a stock movement). A document number and a
  posting or created date with many distinct dates, and a row count that only grows.
- **Dimension**: one row per thing (a material, a plant, a vendor). One row per key, mostly text columns, master
  data names such as MARA, Vendors or dim_Plant.

The type is suggested from what scans and profiles already found (no queries to your servers), as a Likely or
Check suggestion with its reason. Accepting it, or picking a type on the item's page, saves it with the item.
Used by Find › Tables and datasets (a filter) and by ML opportunities (a snapshot table clears the "today only"
history gap).
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import suggestions
from .store import Catalogue

TYPES = ("snapshot", "transaction", "dimension")
LABELS = {"snapshot": "Snapshot", "transaction": "Transaction", "dimension": "Dimension", "": ""}
ICONS = {"snapshot": "📸", "transaction": "🧾", "dimension": "🏷️"}
KINDS = ("table", "view", "data_file", "file_series")

SNAPSHOT_COLUMN = re.compile(r"(snapshot|snap|extract|asof|as_of|load|report|reporting|stock|balance|capture|"
                             r"run|period|calendar)_?(date|day|dt|time|timestamp|week|month)$|^(snapshot|snapdate|"
                             r"asofdate|extractdate|loaddate|reportdate|calday|calweek|calmonth)$", re.I)
SNAPSHOT_NAME = re.compile(r"snapshot|snap\b|_snap|daily|weekly|monthly|_d$|_w$|_m$|history|hist\b|_hist|"
                           r"stockonhand|onhand|soh\b|balance|inventory|position", re.I)
DOCUMENT_COLUMN = re.compile(r"^(vbeln|ebeln|mblnr|belnr|aufnr|tknum|lgnum_tanum|tanum)$|(document|doc|order|"
                             r"invoice|delivery|receipt|movement|transaction|shipment|po)_?(no|num|number|id|key)$",
                             re.I)
EVENT_DATE = re.compile(r"^(budat|erdat|bldat|wadat_ist|lfdat|bedat|aedat|cpudt)$|(posting|created|document|order|"
                        r"delivery|receipt|movement|transaction|invoice|ship|goodsissue|gi)_?(date|dt|time|on)$",
                        re.I)
TRANSACTION_NAME = re.compile(r"transaction|movement|order|sales|deliver|receipt|invoice|billing|shipment|line|"
                              r"posting|journal|ledger|fact|events?\b|_f$|^f_", re.I)
DIMENSION_NAME = re.compile(r"^(dim|d)_|^dim[A-Z]|master|lookup|^lkp|^ref_|reference|^mara$|^marc$|^makt$|^t001|"
                            r"^kna1$|^lfa1$|vendors?$|suppliers?$|customers?$|plants?$|materials?$|products?$|"
                            r"locations?$|calendar$|dates?$|_dim$", re.I)
DATE_TYPES = ("date", "time")
TEXT_TYPES = ("char", "text", "string", "object")


def _base_name(name: str) -> str:
    """The table or file name without schema, database or folders."""
    name = re.split(r"[\\/]", name)[-1]
    name = name.rsplit(".", 1)[-1] if "." in name and not re.search(r"\.(csv|parquet|xlsx?|txt)$", name, re.I) \
        else re.sub(r"\.(csv|parquet|xlsx?|txt)$", "", name, flags=re.I)
    return name.strip("[]")


def guess(name: str, columns: list[dict[str, Any]], profiles: dict[str, dict[str, Any]],
          row_count: int | None, grew: bool | None = None) -> dict[str, Any] | None:
    """{"type", "band" (likely | check), "why"} for one table, or None when nothing points anywhere.

    `columns` are {"name", "data_type"}; `profiles` maps column name to its profile row (row_count,
    distinct_count, ...); `grew` is True when growth history shows the row count only going up."""
    base = _base_name(name)
    score = {t: 0.0 for t in TYPES}
    why: dict[str, list[str]] = {t: [] for t in TYPES}
    names = [c["name"] for c in columns]
    types = {c["name"]: (c.get("data_type") or "").lower() for c in columns}
    rows = row_count or max((p.get("row_count") or 0 for p in profiles.values()), default=0) or None

    snap_cols = [n for n in names if SNAPSHOT_COLUMN.search(n)]
    for n in snap_cols:
        p = profiles.get(n) or {}
        d, r = p.get("distinct_count"), p.get("row_count") or rows
        if d and r and d * 10 <= r:
            score["snapshot"] += 3
            why["snapshot"].append(f"{n} has {d:,} distinct dates over {r:,} rows")
            break
    else:
        if snap_cols:
            score["snapshot"] += 1.5
            why["snapshot"].append(f"a snapshot-style date column ({snap_cols[0]})")
    if SNAPSHOT_NAME.search(base):
        score["snapshot"] += 1.5
        why["snapshot"].append("the name sounds like a snapshot")

    doc_cols = [n for n in names if DOCUMENT_COLUMN.search(n)]
    date_cols = [n for n in names if EVENT_DATE.search(n)]
    if doc_cols:
        score["transaction"] += 1.5
        why["transaction"].append(f"a document number column ({doc_cols[0]})")
    if date_cols:
        p = profiles.get(date_cols[0]) or {}
        d = p.get("distinct_count")
        score["transaction"] += 1.5 if (d is None or d > 30) else 0.5
        why["transaction"].append(f"an event date ({date_cols[0]}" + (f", {d:,} distinct days)" if d else ")"))
    if TRANSACTION_NAME.search(base):
        score["transaction"] += 1
        why["transaction"].append("the name sounds like events")
    if grew:
        score["transaction"] += 1
        why["transaction"].append("its row count has only grown")

    if DIMENSION_NAME.search(base):
        score["dimension"] += 2
        why["dimension"].append("the name sounds like master data")
    if names:
        text_share = sum(any(t in types[n] for t in TEXT_TYPES) for n in names) / len(names)
        dated = any(any(t in types[n] for t in DATE_TYPES) for n in names)
        if text_share >= 0.6 and not dated:
            score["dimension"] += 1
            why["dimension"].append(f"{text_share:.0%} text columns and no dates")
    first = names[0] if names else None
    if first and rows:
        p = profiles.get(first) or {}
        if p.get("distinct_count") and p.get("distinct_count") >= (p.get("row_count") or rows) and not date_cols:
            score["dimension"] += 1.5
            why["dimension"].append(f"one row per {first}")
    if rows is not None and rows < 5000 and not date_cols and not snap_cols:
        score["dimension"] += 0.5
        why["dimension"].append(f"small ({rows:,} rows)")

    best = max(TYPES, key=lambda t: score[t])
    top = score[best]
    if top < 1.5:
        return None
    runner = max(score[t] for t in TYPES if t != best)
    band = "likely" if top >= 3 and top - runner >= 1.5 else "check"
    return {"type": best, "band": band, "why": "; ".join(why[best]), "score": round(top, 1)}


def table_type(cat: Catalogue, key: str) -> str:
    """The type saved for an item ("" when none)."""
    row = cat.conn.execute("SELECT table_type FROM item_info WHERE asset_key = ?", (key,)).fetchone()
    return (row[0] or "") if row else ""


def all_types(cat: Catalogue) -> dict[str, str]:
    return {r[0]: r[1] for r in cat.conn.execute(
        "SELECT asset_key, table_type FROM item_info WHERE table_type IS NOT NULL AND table_type != ''")}


def set_type(cat: Catalogue, key: str, value: str) -> None:
    if value and value not in TYPES:
        raise ValueError(f"unknown table type {value!r}")
    from .curation import set_item_info
    set_item_info(cat, key, table_type=value)


def _grew(cat: Catalogue) -> dict[str, bool]:
    """Items whose row count readings only ever went up (two readings or more)."""
    out: dict[str, list[int]] = {}
    try:
        for r in cat.conn.execute("SELECT asset_key, rows FROM row_counts ORDER BY asset_key, at"):
            out.setdefault(r[0], []).append(r[1] or 0)
    except Exception:  # an older catalogue without growth history
        return {}
    return {k: all(b >= a for a, b in zip(v, v[1:])) and v[-1] > v[0] for k, v in out.items() if len(v) >= 2}


def suggest_all(cat: Catalogue) -> int:
    """Suggest a type for every table and dataset that has none yet. Returns how many suggestions are open."""
    from .curation import hidden_keys
    have = set(all_types(cat))
    hidden = hidden_keys(cat, old=True)
    assets = {r[0]: (r[1], r[2]) for r in cat.conn.execute(
        f"SELECT key, name, attrs FROM assets WHERE status = 'active' AND kind IN ({', '.join('?' * len(KINDS))})",
        KINDS) if r[0] not in have and r[0] not in hidden}
    if not assets:
        return suggestions.sync(cat, "table_type", [])
    cols: dict[str, list[dict[str, Any]]] = {}
    for r in cat.conn.execute("SELECT asset_key, name, data_type FROM columns ORDER BY asset_key, ordinal"):
        if r[0] in assets:
            cols.setdefault(r[0], []).append({"name": r[1], "data_type": r[2]})
    profs: dict[str, dict[str, dict[str, Any]]] = {}
    for r in cat.conn.execute("SELECT asset_key, column_name, row_count, distinct_count FROM profiles"):
        if r[0] in assets:
            profs.setdefault(r[0], {})[r[1]] = {"row_count": r[2], "distinct_count": r[3]}
    grew = _grew(cat)
    from .pathbits import file_name
    found = []
    for key, (name, attrs_text) in assets.items():
        try:
            attrs = json.loads(attrs_text or "{}")
        except ValueError:
            attrs = {}
        g = guess(name, cols.get(key, []), profs.get(key, {}), attrs.get("row_count"), grew.get(key))
        if not g:
            continue
        shown = file_name(name, "data_file")
        found.append({"id": suggestions.make_id("table_type", key, g["type"]), "subject": key,
                      "title": f"{shown} is a {LABELS[g['type']].lower()} table",
                      "detail": f"{'Likely' if g['band'] == 'likely' else 'Check'}: {g['why']}.",
                      "score": g["score"],
                      "payload": {"key": key, "type": g["type"], "band": g["band"], "why": g["why"]}})
    return suggestions.sync(cat, "table_type", found)


@suggestions.handler("table_type")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    set_type(cat, p["key"], p["type"])
