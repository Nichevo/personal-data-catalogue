"""How tables relate, for ERDs and join hints.

Three kinds of evidence, strongest first:

  * declared keys read from SQL Server (primary keys and foreign keys)
  * joins found in your SQL: views, procedures, pasted SQL and queries in code
    ("JOIN dbo.Store st ON st.StoreId = s.StoreId")
  * matching key names: a column called StoreId in one table and the primary
    key StoreId in another, or a company-wide key shared by both

Declared keys are drawn solid; the others are suggestions, drawn dashed until
you confirm them. Rejected suggestions stay hidden through rescans.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .store import Catalogue

JOIN_ON = re.compile(
    r"\bjoin\s+(?P<table>[\w\[\].\"]+)(?:\s+(?:as\s+)?(?P<alias>(?!on\b)\w+))?\s+on\s+(?P<cond>.+?)"
    r"(?=\bjoin\b|\bwhere\b|\bgroup\b|\border\b|\bunion\b|\bleft\b|\binner\b|\bright\b|\bfull\b|\bcross\b|\)|;|$)",
    re.I | re.S)
FROM_TABLE = re.compile(r"\bfrom\s+(?P<table>[\w\[\].\"]+)(?:\s+(?:as\s+)?(?P<alias>(?!where\b|join\b|left\b|inner\b"
                        r"|right\b|full\b|cross\b|group\b|order\b|with\b)\w+))?", re.I)
EQ = re.compile(r"(?P<a>\w+)\.\[?(?P<ac>\w+)\]?\s*=\s*(?P<b>\w+)\.\[?(?P<bc>\w+)\]?")


def _clean(name: str) -> str:
    return name.replace("[", "").replace("]", "").replace('"', "").lower()


def ingest_keys(cat: Catalogue, source: str, database: str, rows: list[dict[str, Any]]) -> int:
    """Store declared primary and foreign keys for one database."""
    from .scan_sqlserver import object_key
    pks: dict[str, list[str]] = defaultdict(list)
    fks: dict[tuple[str, str, str], list[tuple[str, str]]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["kind"], r.get("constraint_name") or "", r.get("ordinal") or 0)):
        key = object_key(source, database, r["schema_name"], r["object_name"])
        if r["kind"] == "pk":
            pks[key].append(r["column_name"])
        else:
            dst = object_key(source, database, r["ref_schema"], r["ref_object"])
            fks[(key, dst, r.get("constraint_name") or "")].append((r["column_name"], r["ref_column"]))
    prefix = f"sql://{source}/{database}/".lower()
    with cat.conn:
        for row in cat.conn.execute("SELECT key, attrs FROM assets WHERE key LIKE ?", (prefix + "%",)).fetchall():
            attrs = json.loads(row["attrs"])
            pk = pks.get(row["key"], [])
            if attrs.get("primary_key", []) != pk:
                attrs["primary_key"] = pk
                cat.conn.execute("UPDATE assets SET attrs = ? WHERE key = ?", (json.dumps(attrs, default=str), row["key"]))
        cat.conn.execute("DELETE FROM relationships WHERE how = 'fk' AND src_key LIKE ?", (prefix + "%",))
        for (src, dst, name), pairs in fks.items():
            cat.conn.execute(
                "INSERT OR REPLACE INTO relationships (src_key, src_columns, dst_key, dst_columns, how, state, evidence) "
                "VALUES (?, ?, ?, ?, 'fk', 'declared', ?)",
                (src, ", ".join(p[0] for p in pairs), dst, ", ".join(p[1] for p in pairs), f"foreign key {name}".strip()))
    return len(fks) + len(pks)


def _table_index(cat: Catalogue) -> tuple[dict[str, list[str]], dict[str, dict]]:
    by_name: dict[str, list[str]] = defaultdict(list)
    tables: dict[str, dict] = {}
    for r in cat.conn.execute("SELECT key, name, attrs FROM assets WHERE kind IN ('table', 'view') AND status = 'active'"):
        attrs = json.loads(r["attrs"])
        tables[r["key"]] = {"name": r["name"], "database": (attrs.get("database") or "").lower(),
                            "source": r["key"].split("/")[2], "pk": attrs.get("primary_key") or []}
        obj = (attrs.get("object") or r["name"].split(".")[-1]).lower()
        by_name[obj].append(r["key"])
        by_name[r["name"].lower()].append(r["key"])
    return by_name, tables


def _resolve(name: str, by_name: dict[str, list[str]], tables: dict[str, dict], hint_db: str | None) -> str | None:
    parts = _clean(name).split(".")
    cands = by_name.get(".".join(parts[-2:]), []) or by_name.get(parts[-1], [])
    if len(parts) >= 3:
        cands = [c for c in cands if tables[c]["database"] == parts[-3]] or cands
    if len(cands) > 1 and hint_db:
        cands = [c for c in cands if tables[c]["database"] == hint_db.lower()] or cands
    cands = list(dict.fromkeys(cands))
    return cands[0] if len(cands) == 1 else None


def joins_in_sql(sql: str) -> list[tuple[str, str, str, str]]:
    """(table a, column a, table b, column b) for every `a.x = b.y` in a JOIN … ON clause."""
    aliases: dict[str, str] = {}
    for m in FROM_TABLE.finditer(sql):
        t = _clean(m["table"])
        aliases[t.split(".")[-1]] = t
        if m["alias"]:
            aliases[m["alias"].lower()] = t
    out = []
    for m in JOIN_ON.finditer(sql):
        t = _clean(m["table"])
        aliases[t.split(".")[-1]] = t
        if m["alias"]:
            aliases[m["alias"].lower()] = t
    for m in JOIN_ON.finditer(sql):
        for e in EQ.finditer(m["cond"]):
            a, b = aliases.get(e["a"].lower()), aliases.get(e["b"].lower())
            if a and b and a != b:
                out.append((a, e["ac"], b, e["bc"]))
    return out


def find_suggestions(cat: Catalogue) -> int:
    """Suggest relationships from joins in SQL and from matching key names. Keeps your confirmations
    and rejections. Returns how many suggestions there are now."""
    by_name, tables = _table_index(cat)
    found: dict[tuple[str, str, str, str], tuple[str, str]] = {}

    def add(a: str, ac: str, b: str, bc: str, how: str, evidence: str) -> None:
        if a == b:
            return
        # Point from the table holding the foreign key to the one where it is the primary key, when known.
        if tables[a]["pk"] == [ac] and tables[b]["pk"] != [bc]:
            a, ac, b, bc = b, bc, a, ac
        ident = (a, ac, b, bc)
        rev = (b, bc, a, ac)
        if ident in found or rev in found:
            return
        found[ident] = (how, evidence)

    for r in cat.conn.execute("SELECT key, kind, name, attrs FROM assets WHERE status = 'active' AND kind IN "
                              "('view', 'procedure', 'sql_snippet', 'sql_script', 'script', 'notebook')"):
        attrs = json.loads(r["attrs"])
        texts = [attrs.get("definition") or "", attrs.get("sql") or "", *attrs.get("queries", [])]
        hint = (attrs.get("database") or "").split("/")[-1] or None
        for text in texts:
            if not text or "join" not in text.lower():
                continue
            for a, ac, b, bc in joins_in_sql(text):
                ka, kb = _resolve(a, by_name, tables, hint), _resolve(b, by_name, tables, hint)
                if ka and kb:
                    add(ka, ac, kb, bc, "join", f"joined in {r['name']}")

    # Matching names: a column named like another table's single-column primary key.
    pk_owner: dict[tuple[str, str, str], str] = {}
    for k, t in tables.items():
        if len(t["pk"]) == 1 and re.search(r"(id|key|code|no)$", t["pk"][0], re.I):
            pk_owner[(t["source"], t["database"], t["pk"][0].lower())] = k
    for c in cat.conn.execute("SELECT asset_key, name FROM columns"):
        t = tables.get(c["asset_key"])
        if not t:
            continue
        owner = pk_owner.get((t["source"], t["database"], c["name"].lower()))
        if owner and owner != c["asset_key"] and t["pk"] != [c["name"]]:
            add(c["asset_key"], c["name"], owner, tables[owner]["pk"][0], "name",
                f"{c['name']} matches the primary key of {tables[owner]['name']}")

    declared = {(r["src_key"], r["src_columns"].lower(), r["dst_key"], r["dst_columns"].lower())
                for r in cat.conn.execute("SELECT * FROM relationships WHERE how = 'fk'")}
    decided = {(r["src_key"], r["src_columns"].lower(), r["dst_key"], r["dst_columns"].lower()): r["state"]
               for r in cat.conn.execute("SELECT * FROM relationships WHERE how != 'fk' AND state IN "
                                         "('confirmed', 'rejected')")}
    with cat.conn:
        cat.conn.execute("DELETE FROM relationships WHERE how != 'fk' AND state = 'suggested'")
        for (a, ac, b, bc), (how, ev) in found.items():
            low = (a, ac.lower(), b, bc.lower())
            if low in declared or (b, bc.lower(), a, ac.lower()) in declared:
                continue
            if low in decided or (b, bc.lower(), a, ac.lower()) in decided:
                continue
            cat.conn.execute("INSERT OR IGNORE INTO relationships (src_key, src_columns, dst_key, dst_columns, how, "
                             "state, evidence) VALUES (?, ?, ?, ?, ?, 'suggested', ?)", (a, ac, b, bc, how, ev))
    cat.changed()
    return len(found)


def relationships(cat: Catalogue, keys: set[str] | None = None, *, include_rejected: bool = False
                  ) -> list[dict[str, Any]]:
    rows = [dict(r) for r in cat.conn.execute("SELECT * FROM relationships ORDER BY src_key, dst_key")]
    if not include_rejected:
        rows = [r for r in rows if r["state"] != "rejected"]
    if keys is not None:
        rows = [r for r in rows if r["src_key"] in keys and r["dst_key"] in keys]
    return rows


def set_relationship_state(cat: Catalogue, r: dict[str, Any], state: str) -> None:
    if state not in ("confirmed", "rejected", "suggested"):
        raise ValueError(state)
    with cat.conn:
        cat.conn.execute("UPDATE relationships SET state = ? WHERE src_key = ? AND src_columns = ? AND dst_key = ? "
                         "AND dst_columns = ?", (state, r["src_key"], r["src_columns"], r["dst_key"], r["dst_columns"]))
    cat.changed()


def add_relationship(cat: Catalogue, src_key: str, src_columns: str, dst_key: str, dst_columns: str) -> None:
    with cat.conn:
        cat.conn.execute("INSERT OR REPLACE INTO relationships (src_key, src_columns, dst_key, dst_columns, how, state, "
                         "evidence) VALUES (?, ?, ?, ?, 'manual', 'confirmed', ?)",
                         (src_key, src_columns, dst_key, dst_columns, f"added by {cat.author()}"))
    cat.changed()


def joins_for(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    """How to join this table to others: declared keys first, then confirmed, then suggestions."""
    order = {"declared": 0, "confirmed": 1, "suggested": 2}
    rows = [r for r in relationships(cat) if key in (r["src_key"], r["dst_key"])]
    return sorted(rows, key=lambda r: order.get(r["state"], 3))
