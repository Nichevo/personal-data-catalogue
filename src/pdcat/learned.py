"""Learn from your queries: how each table is usually used, from every query in the SQL library.

For a table it counts, across queries (each counted once however many places it's copied to), the
columns used, the columns filtered on in WHERE, and the joins to other tables with their columns.
The table page shows that as "How this table is usually used", which you can save as a note. Joins
that come up in several queries but aren't confirmed yet are suggested as confirmed joins, which
then help the database diagrams and join hints.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from . import suggestions
from .store import Catalogue

MIN_QUERIES = 3   # a join used in at least this many queries is suggested


def _where_columns(sql: str, aliases: dict[str, str]) -> list[tuple[str, str]]:
    """(table name, column) pairs filtered on in WHERE clauses."""
    from .sqlchecks import _code
    code = _code(sql)
    out = []
    for m in re.finditer(r"\bwhere\b(.*?)(?=\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\bunion\b|\)\s*(?:as\s+)?\w+\s|$)",
                         code):
        for c in re.finditer(r"(?:\b(\w+)\.)?\[?(\w+)\]?\s*(?:=|<>|!=|>=|<=|>|<|\bin\b|\blike\b|\bbetween\b|\bis\b)",
                             m.group(1)):
            alias, col = c.group(1), c.group(2)
            if col in ("and", "or", "not", "null") or col.isdigit():
                continue
            table = aliases.get(alias) if alias else (next(iter(aliases.values())) if len(set(aliases.values())) == 1
                                                      else None)
            if table:
                out.append((table, col))
    return out


def usage(cat: Catalogue, table_key: str) -> dict[str, Any]:
    """{"queries": n, "columns": [(col, n)], "filters": [(col, n)], "joins": [{"other", "other_key", "on", "n"}]}."""
    from .sqllibrary import all_with_places
    cols: Counter = Counter()
    filters: Counter = Counter()
    joins: Counter = Counter()
    other_keys: dict[str, str | None] = {}
    n = 0
    for q in all_with_places(cat, table=table_key):
        mine = {t["name"] for t in q["tables"] if t.get("key") == table_key}
        n += 1
        for t in q["tables"]:
            if t["name"] in mine:
                cols.update({c.lower(): 1 for c in t["columns"]})
        by_name = {t["name"]: t for t in q["tables"]}
        aliases = {}
        for t in q["tables"]:
            aliases[(t.get("alias") or t["name"].split(".")[-1]).lower()] = t["name"]
            aliases.setdefault(t["name"].split(".")[-1].lower(), t["name"])
        filters.update({c: 1 for tn, c in set(_where_columns(q["sql"], aliases)) if tn in mine})
        seen = set()
        for j in q["analysis"].get("joins", []):
            if j["left_table"] in mine:
                other, on = j["right_table"], f"{j['left_column']} = {j['right_column']}"
            elif j["right_table"] in mine:
                other, on = j["left_table"], f"{j['right_column']} = {j['left_column']}"
            else:
                continue
            if (other, on.lower()) in seen:
                continue
            seen.add((other, on.lower()))
            joins[(other, on.lower())] += 1
            other_keys[other] = by_name.get(other, {}).get("key")
    names = _column_names(cat, table_key)
    return {"queries": n,
            "columns": [(names.get(c, c), k) for c, k in cols.most_common(15)],
            "filters": [(names.get(c, c), k) for c, k in filters.most_common(10)],
            "joins": [{"other": o, "other_key": other_keys.get(o), "on": on, "n": k}
                      for (o, on), k in joins.most_common(10)]}


def _column_names(cat: Catalogue, key: str) -> dict[str, str]:
    return {r[0].lower(): r[0] for r in cat.conn.execute("SELECT name FROM columns WHERE asset_key = ?", (key,))}


def usage_note(cat: Catalogue, table_key: str) -> str:
    u = usage(cat, table_key)
    if not u["queries"]:
        return ""
    lines = [f"How this table is usually used (from {u['queries']} queries in the SQL library):"]
    if u["columns"]:
        lines.append("- Columns used most: " + ", ".join(f"{c} ({n})" for c, n in u["columns"][:10]))
    if u["filters"]:
        lines.append("- Usually filtered on: " + ", ".join(f"{c} ({n})" for c, n in u["filters"][:6]))
    for j in u["joins"][:6]:
        lines.append(f"- Joined to {j['other']} on {j['on']} ({j['n']} queries)")
    return "\n".join(lines)


def learn_joins(cat: Catalogue) -> int:
    """Suggest confirming joins that several queries use but nobody has confirmed."""
    from .relationships import relationships
    from .sqllibrary import all_with_places
    counts: Counter = Counter()
    for q in all_with_places(cat):
        keys = {t["name"]: t.get("key") for t in q["tables"]}
        seen = set()
        for j in q["analysis"].get("joins", []):
            a, b = keys.get(j["left_table"]), keys.get(j["right_table"])
            if not a or not b or a == b:
                continue
            pair = (a, j["left_column"].lower(), b, j["right_column"].lower())
            if pair > (b, pair[3], a, pair[1]):
                pair = (b, pair[3], a, pair[1])
            if pair not in seen:
                seen.add(pair)
                counts[pair] += 1
    settled = set()
    for r in relationships(cat, include_rejected=True):
        if r["state"] in ("declared", "confirmed", "rejected"):
            a, ac, b, bc = r["src_key"], r["src_columns"].lower(), r["dst_key"], r["dst_columns"].lower()
            settled |= {(a, ac, b, bc), (b, bc, a, ac)}
    found = []
    for (a, ac, b, bc), n in counts.items():
        if n < MIN_QUERIES or (a, ac, b, bc) in settled:
            continue
        an, bn = (cat.get_asset(a) or {}).get("name", a), (cat.get_asset(b) or {}).get("name", b)
        found.append({"id": suggestions.make_id("join", f"{a}|{ac}", f"{b}|{bc}"), "subject": a,
                      "title": f"{an} joins {bn} on {ac} = {bc}",
                      "detail": f"Used in {n} queries in the SQL library. Confirm it as a join for diagrams and hints.",
                      "score": n, "payload": {"src_key": a, "src_columns": ac, "dst_key": b, "dst_columns": bc,
                                              "queries": n}})
    return suggestions.sync(cat, "join", found)


@suggestions.handler("join")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    from .relationships import add_relationship, relationships, set_relationship_state
    want = {(p["src_key"], p["src_columns"], p["dst_key"], p["dst_columns"]),
            (p["dst_key"], p["dst_columns"], p["src_key"], p["src_columns"])}
    for r in relationships(cat, include_rejected=True):
        if (r["src_key"], r["src_columns"].lower(), r["dst_key"], r["dst_columns"].lower()) in want:
            set_relationship_state(cat, r, "confirmed")
            return
    names_a, names_b = _column_names(cat, p["src_key"]), _column_names(cat, p["dst_key"])
    add_relationship(cat, p["src_key"], names_a.get(p["src_columns"], p["src_columns"]), p["dst_key"],
                     names_b.get(p["dst_columns"], p["dst_columns"]))
