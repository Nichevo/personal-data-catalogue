"""Suggested descriptions for tables and columns that have none, from what the catalogue already knows.

A column's suggestion comes from, in this order:

  * the same column name described elsewhere (describe once, apply everywhere);
  * a definition found in a design document ("PlantID  Identifier of the plant", in a Word or
    Excel table);
  * a comment in SQL or a rename in code (the description suggestions that already exist);
  * the SAP field it is named after (WERKS: plant);
  * the name itself, read with the abbreviation list and the usual naming patterns: CustOrderQty
    becomes "Customer order quantity", IsActive "Whether active", LoadDate "Load date".

A table's suggestion comes from its name (dim_, fact_, vw_, stg_ prefixes and the words after
them), its key column (one row per plant) and what loads it. Every suggestion says where it
came from, and nothing is written until you accept it. No AI is involved.
"""
from __future__ import annotations

import re
from typing import Any

from .searchwords import split_name, squash
from .store import Catalogue

# last word (or first word for prefixes) -> how to read the rest of the name
SUFFIXES: dict[str, str] = {
    "id": "{rest} identifier", "key": "{rest} key", "nr": "{rest} number", "no": "{rest} number",
    "num": "{rest} number", "number": "{rest} number", "code": "{rest} code", "cd": "{rest} code",
    "date": "{rest} date", "dt": "{rest} date", "day": "{rest} day", "time": "{rest} time", "ts": "{rest} time",
    "timestamp": "{rest} time", "datetime": "{rest} date and time",
    "qty": "{rest} quantity", "quantity": "{rest} quantity", "units": "{rest} in units",
    "amt": "{rest} amount", "amount": "{rest} amount", "val": "{rest} value", "value": "{rest} value",
    "cnt": "{rest} count", "count": "{rest} count", "pct": "{rest} percentage", "percent": "{rest} percentage",
    "percentage": "{rest} percentage", "rate": "{rest} rate", "ratio": "{rest} ratio",
    "flag": "Whether {rest}", "ind": "Whether {rest}", "indicator": "Whether {rest}",
    "desc": "{rest} description", "description": "{rest} description", "txt": "{rest} text", "text": "{rest} text",
    "name": "{rest} name", "nm": "{rest} name", "type": "{rest} type", "typ": "{rest} type", "cat": "{rest} category",
    "category": "{rest} category", "status": "{rest} status", "stat": "{rest} status", "year": "{rest} year",
    "yr": "{rest} year", "month": "{rest} month", "mth": "{rest} month", "week": "{rest} week", "wk": "{rest} week",
    "uom": "{rest} unit of measure", "price": "{rest} price", "cost": "{rest} cost", "weight": "{rest} weight",
    "wgt": "{rest} weight", "vol": "{rest} volume", "volume": "{rest} volume", "lvl": "{rest} level",
    "level": "{rest} level", "grp": "{rest} group", "group": "{rest} group",
}
PREFIXES: dict[str, str] = {"is": "Whether {rest}", "has": "Whether it has {rest}", "fl": "Whether {rest}",
                            "nbr": "Number of {rest}", "num": "Number of {rest}", "cnt": "Count of {rest}",
                            "dt": "{rest} date", "avg": "Average {rest}", "min": "Lowest {rest}",
                            "max": "Highest {rest}", "tot": "Total {rest}", "total": "Total {rest}",
                            "sum": "Total {rest}"}
TABLE_PREFIXES: dict[str, str] = {"dim": "Dimension (master data): {rest}", "d": "Dimension (master data): {rest}",
                                  "fact": "Fact table: {rest}", "f": "Fact table: {rest}", "vw": "View: {rest}",
                                  "v": "View: {rest}", "stg": "Staging table: {rest}", "staging": "Staging table: {rest}",
                                  "tmp": "Temporary table: {rest}", "temp": "Temporary table: {rest}",
                                  "tbl": "{rest}", "t": "{rest}", "ref": "Reference data: {rest}",
                                  "lkp": "Lookup: {rest}", "lookup": "Lookup: {rest}", "agg": "Aggregate: {rest}",
                                  "hist": "History of {rest}", "log": "Log of {rest}", "bak": "Backup of {rest}",
                                  "raw": "Raw data: {rest}", "src": "Source data: {rest}"}
STOP = {"the", "of", "and", "a"}
MAX_TEXT = 200


def _words(name: str, abbrevs: dict[str, str]) -> list[str]:
    """Lower-case words of a name, abbreviations spelled out ("soh" -> "stock on hand")."""
    expand = {squash(k): v.lower() for k, v in abbrevs.items()}
    out: list[str] = []
    for w in split_name(name).split():
        w = w.lower()
        if not w or w.isdigit() and len(out) == 0:
            continue
        out.extend(expand.get(squash(w), w).split())
    return out


def _sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:1].upper() + text[1:]) if text else ""


def from_name(name: str, abbrevs: dict[str, str] | None = None) -> str | None:
    """A description read off a column name, or None when the name says too little (one plain word)."""
    words = _words(name, abbrevs or {})
    if not words:
        return None
    expanded = any(" " in v for v in (abbrevs or {}).values()) and squash(" ".join(words)) != squash(name)
    if len(words) >= 2 and words[-1] in SUFFIXES:
        rest = " ".join(w for w in words[:-1] if w not in STOP)
        return _sentence(SUFFIXES[words[-1]].format(rest=rest)) if rest else None
    if len(words) >= 2 and words[0] in PREFIXES:
        rest = " ".join(words[1:])
        return _sentence(PREFIXES[words[0]].format(rest=rest))
    if len(words) >= 2 or expanded:
        return _sentence(" ".join(words))
    return None


def _same_name(cat: Catalogue) -> dict[str, tuple[str, str]]:
    """Lower-case column name -> (description, item name) from any described column with that name."""
    out: dict[str, tuple[str, str]] = {}
    for r in cat.conn.execute(
            "SELECT lower(c.name) AS name, COALESCE(ci.description, c.description, '') AS description, a.name AS item "
            "FROM columns c JOIN assets a ON a.key = c.asset_key AND a.status = 'active' "
            "LEFT JOIN column_info ci ON ci.asset_key = c.asset_key AND ci.column_name = c.name "
            "WHERE COALESCE(ci.description, c.description, '') != '' ORDER BY a.name"):
        out.setdefault(r["name"], (r["description"], r["item"]))
    return out


def _from_suggestions(cat: Catalogue, key: str) -> dict[str, tuple[str, str]]:
    """Column -> (text, where) from open description suggestions on this item (SQL comments, renames,
    document definitions)."""
    from .suggestions import open_suggestions
    out = {}
    for s in open_suggestions(cat, "description", subject=key):
        p = s.get("payload") or {}
        if p.get("column") and p.get("text"):
            where = (s.get("detail") or "").removeprefix("Use ").removesuffix(" as the column's description.")
            out[p["column"].lower()] = (p["text"], where or "a comment")
    return out


def column_suggestions(cat: Catalogue, key: str, columns: list[dict] | None = None) -> list[dict[str, Any]]:
    """[{"column", "text", "source"}] for the columns of one item that have no description yet."""
    from .curation import column_info
    from .sapterms import meaning
    cols = [dict(c) for c in (columns if columns is not None else cat.columns(key))]
    if not cols:
        return []
    mine = column_info(cat, key)
    same = _same_name(cat)
    drafts = _from_suggestions(cat, key)
    abbrevs = cat.abbreviations()
    item = cat.get_asset(key) or {}
    out = []
    for c in cols:
        name = c["name"]
        if (mine.get(name) or {}).get("description") or c.get("description"):
            continue
        low = name.lower()
        hit = same.get(low)
        if hit and hit[1] != item.get("name"):
            out.append({"column": name, "text": hit[0][:MAX_TEXT], "source": f"same name in {hit[1]}"})
            continue
        if low in drafts:
            out.append({"column": name, "text": drafts[low][0][:MAX_TEXT], "source": drafts[low][1]})
            continue
        sap = (mine.get(name) or {}).get("sap_name") or ""
        text = meaning(sap) if sap else ""
        if not text and len(name) <= 6 and name.isupper():
            text = meaning(name)
        if text:
            out.append({"column": name, "text": _sentence(text), "source": f"SAP field {sap or name.upper()}"})
            continue
        text = from_name(name, abbrevs)
        if text:
            out.append({"column": name, "text": text, "source": "from the name"})
    return out


def accept_columns(cat: Catalogue, key: str, picks: dict[str, str], *, everywhere: bool = False) -> int:
    """Write the picked descriptions (column -> text). With `everywhere`, every other undescribed column with
    the same name gets it too. Returns how many columns were described."""
    from .curation import describe_everywhere, set_column_info
    n = 0
    for col, text in picks.items():
        if not text.strip():
            continue
        if everywhere:
            n += describe_everywhere(cat, col, text.strip(), only_empty=True)
            set_column_info(cat, key, col, description=text.strip(), rebuild=False)
        else:
            set_column_info(cat, key, col, description=text.strip(), rebuild=False)
            n += 1
    cat.rebuild_search()
    return n


# ---- tables -----------------------------------------------------------------------------------

def _grain(cat: Catalogue, key: str, words: list[str]) -> str | None:
    """"one row per plant" from a key column that is unique in the profile (or looks like one)."""
    profile = {p["column_name"].lower(): p for p in cat.profile(key)}
    for c in cat.columns(key):
        low = c["name"].lower()
        parts = _words(c["name"], {})
        if not parts or parts[-1] not in ("id", "key", "nr", "no", "code"):
            continue
        p = profile.get(low)
        unique = p and p["row_count"] and p["distinct_count"] == p["row_count"]
        if unique or not profile:
            rest = " ".join(parts[:-1]) or " ".join(words)
            return f"one row per {rest}" if rest else None
    return None


def table_suggestion(cat: Catalogue, key: str) -> dict[str, Any] | None:
    """{"text", "grain", "source"} for a table or view with no description, or None."""
    a = cat.get_asset(key)
    if a is None or a["kind"] not in ("table", "view", "data_file", "file_series"):
        return None
    name = a["name"].split(".")[-1] if a["kind"] in ("table", "view") else a["name"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    abbrevs = cat.abbreviations()
    words = _words(name, abbrevs)
    if not words:
        return None
    text = None
    if len(words) >= 2 and words[0] in TABLE_PREFIXES:
        text = TABLE_PREFIXES[words[0]].format(rest=" ".join(words[1:]))
    elif len(words) >= 2 or squash(" ".join(words)) != squash(name):
        text = " ".join(words)
    if not text:
        return None
    text = _sentence(text)
    loaders = []
    for link in cat.links_to(key):
        if link["relation"] in ("writes", "creates") and link["src_name"]:
            loaders.append(link["src_name"].rsplit("/", 1)[-1])
    if loaders:
        text += f". Loaded by {', '.join(sorted(set(loaders))[:3])}"
    grain = _grain(cat, key, words[1:] if words[0] in TABLE_PREFIXES else words)
    return {"text": text, "grain": grain, "source": "from the name" + (" and what loads it" if loaders else "")}


def accept_table(cat: Catalogue, key: str, text: str, grain: str | None = None) -> None:
    from .curation import set_item_info
    fields: dict[str, Any] = {"purpose": text.strip()}
    if grain:
        fields["grain"] = grain.strip()
    set_item_info(cat, key, **fields)
