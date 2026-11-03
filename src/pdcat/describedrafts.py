"""Suggested descriptions for tables and columns that have none, from what the catalogue already knows.

A column's suggestion comes from, in this order:

  * the same column name described elsewhere (describe once, apply everywhere);
  * a definition found in a design document ("PlantID  Identifier of the plant", in a Word or
    Excel table);
  * a comment in SQL or a rename in code (the description suggestions that already exist);
  * the SAP field it is named after (WERKS: plant);
  * the name itself, read with the abbreviation list and the usual naming patterns: CustOrderQty
    becomes "Customer order quantity", IsActive "Whether active", LoadDate "Load date".

A table's suggestion comes from the names it is given elsewhere (`FROM dbo.T_MAT01 AS Materials`,
a Power Query called Materials in a report), else its own name (dim_, fact_, vw_, stg_ prefixes and the
words after them), plus what its columns are renamed to, its key column (one row per plant) and what
loads it. Every suggestion says where it
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
    for kind in ("doc_definition", "description"):  # a document's definition wins over a code comment
        for s in open_suggestions(cat, kind, subject=key):
            p = s.get("payload") or {}
            if p.get("column") and p.get("text") and p["column"].lower() not in out:
                where = (s.get("detail") or "").removeprefix("Use ").removesuffix(" as the column's description.")
                out[p["column"].lower()] = (p["text"], where.rstrip(".") or "a comment")
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


GENERIC_NAMES = {"src", "source", "tgt", "target", "temp", "tmp", "base", "main", "data", "tbl", "table", "sub",
                 "cte", "new", "old", "prev", "cur", "curr", "this", "that", "other", "result", "results", "output",
                 "input", "final", "query", "step", "changed", "type", "navigation", "promoted", "headers", "list",
                 "df", "dataframe", "inner", "outer", "left", "right", "self", "stage", "staging"}
MAX_FOUND = 5


def _plain(name: str) -> str:
    return name.strip().strip("[]\"`'").split(".")[-1].strip("[]\"`'")


def _tells_more(found: str, table_name: str, abbrevs: dict[str, str]) -> bool:
    """Whether a name a table is given elsewhere says something its own name doesn't: not a one-letter
    alias, initials, a generic word (src, cte, Query1) or the same words again."""
    sq = squash(found)
    if len(sq) < 4 or sq.isdigit() or re.fullmatch(r"[a-z]{1,3}\d*", sq):
        return False
    words = [w for w in _words(found, abbrevs) if not w.isdigit()]
    if not words or all(w in GENERIC_NAMES for w in words) or re.fullmatch(r"(query|table|sheet)\d+", sq):
        return False
    own = _words(table_name, abbrevs)
    if sq == squash(table_name) or {w.rstrip("s") for w in words} <= {w.rstrip("s") for w in own}:
        return False
    initials = "".join(w[0] for w in own if w)
    return sq != initials


def names_found(cat: Catalogue, key: str, table_name: str | None = None) -> list[dict[str, Any]]:
    """The names a table is given elsewhere, most used first: [{"name", "count", "where"}].

    From SQL aliases (`FROM dbo.T_MAT01 AS Materials`) and Power Query queries that load it in a report or
    workbook (the query name is what the table is called there). One-letter aliases, initials and generic
    words are left out."""
    a = cat.get_asset(key) or {}
    table_name = table_name or _plain(a.get("name") or "")
    abbrevs = cat.abbreviations()
    found: dict[str, dict[str, Any]] = {}

    def add(name: str, where: str) -> None:
        name = _plain(name)
        if not name or not _tells_more(name, table_name, abbrevs):
            return
        hit = found.setdefault(squash(name), {"name": name, "count": 0, "where": []})
        hit["count"] += 1
        if where not in hit["where"]:
            hit["where"].append(where)

    try:
        from .sqllibrary import all_with_places
        for q in all_with_places(cat, table=key):
            for t in q["tables"]:
                if t.get("key") == key and t.get("alias"):
                    add(t["alias"], f"query {q['name']}")
    except Exception:  # the SQL library is a nice-to-have here, never a reason to fail
        pass
    for link in cat.links_to(key):
        m = re.fullmatch(r"Power Query “(.+)”", link["how"] or "")
        if m:
            file = re.split(r"[/\\]", link["src_name"] or link["src_key"] or "")[-1]
            add(m.group(1), file or "a report")
    return sorted(found.values(), key=lambda f: (-f["count"], f["name"].lower()))[:MAX_FOUND]


def renamed_columns(cat: Catalogue, key: str) -> list[str]:
    """What this table's columns are renamed to in code and SQL (MATNR AS Material), most used first."""
    counts: dict[str, list] = {}
    for r in cat.conn.execute("SELECT src_column, dst_column FROM column_links WHERE src_item = ? AND "
                              "how IN ('rename', 'alias')", (key,)):
        if squash(r[0]) == squash(r[1]) or len(squash(r[1])) < 3:
            continue
        hit = counts.setdefault(squash(r[1]), [r[1], 0])
        hit[1] += 1
    return [v[0] for v in sorted(counts.values(), key=lambda v: (-v[1], v[0].lower()))]


def table_suggestion(cat: Catalogue, key: str) -> dict[str, Any] | None:
    """{"text", "grain", "source", "names"} for a table or view with no description, or None.

    A name the table is given elsewhere (an alias in SQL, a Power Query in a report) wins over its own name
    when there is one, since T_MAT01 AS Materials says more than T_MAT01. What its columns are renamed to
    is added as "Has ...", and what loads it as "Loaded by ..."."""
    a = cat.get_asset(key)
    if a is None or a["kind"] not in ("table", "view", "data_file", "file_series"):
        return None
    name = a["name"].split(".")[-1] if a["kind"] in ("table", "view") else a["name"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    abbrevs = cat.abbreviations()
    words = _words(name, abbrevs)
    found = names_found(cat, key, name)
    renamed = renamed_columns(cat, key)
    if not words and not found:
        return None
    prefix = words[0] if len(words) >= 2 and words[0] in TABLE_PREFIXES else None
    text, sources = None, []
    if found:
        best = found[0]
        what = " ".join(_words(best["name"], abbrevs))
        text = TABLE_PREFIXES[prefix].format(rest=what) if prefix else what
        uses = f"{best['count']} place{'s' if best['count'] != 1 else ''}"
        sources.append(f"called {best['name']} in {uses} ({', '.join(best['where'][:2])})")
    elif prefix:
        text = TABLE_PREFIXES[prefix].format(rest=" ".join(words[1:]))
    elif len(words) >= 2 or squash(" ".join(words)) != squash(name):
        text = " ".join(words)
    if not text and renamed:  # a name that says nothing, but its columns are renamed into words
        text = name
    if not text:
        return None
    text = _sentence(text)
    if not found:
        sources.append("from the name")
    if renamed:
        shown = [" ".join(_words(c, abbrevs)) for c in renamed[:4]]
        text += ". Has " + (", ".join(shown[:-1]) + " and " + shown[-1] if len(shown) > 1 else shown[0])
        sources.append("what its columns are renamed to")
    loaders = []
    for link in cat.links_to(key):
        if link["relation"] in ("writes", "creates") and link["src_name"]:
            loaders.append(link["src_name"].rsplit("/", 1)[-1])
    if loaders:
        text += f". Loaded by {', '.join(sorted(set(loaders))[:3])}"
        sources.append("what loads it")
    grain_words = words[1:] if prefix else words
    if found:
        grain_words = _words(found[0]["name"], abbrevs)
    grain = _grain(cat, key, grain_words)
    source = sources[0] + (" and " + ", ".join(sources[1:]) if len(sources) > 1 else "")
    return {"text": text, "grain": grain, "source": source, "names": found}


def accept_table(cat: Catalogue, key: str, text: str, grain: str | None = None) -> None:
    from .curation import set_item_info
    fields: dict[str, Any] = {"purpose": text.strip()}
    if grain:
        fields["grain"] = grain.strip()
    set_item_info(cat, key, **fields)
