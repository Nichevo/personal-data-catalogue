"""How tables relate, for ERDs and join hints.

Three kinds of evidence, strongest first:

  * declared keys read from SQL Server (primary keys and foreign keys)
  * joins found in your SQL: views, procedures, pasted SQL and queries in code
    ("JOIN dbo.Store st ON st.StoreId = s.StoreId")
  * matching key names: a column called StoreId in one table and the primary
    key StoreId in another, or a company-wide key shared by both

Declared keys are drawn solid; the others are suggestions, drawn dashed until
you confirm them. Rejected suggestions stay hidden through rescans.

Reading every view and procedure is the slow part on a big database, so what
each one joins is saved (table relationship_reads) with a fingerprint of its
SQL. A rescan only reads the ones that changed, the joins found are saved every
few objects (stopping keeps them), and a run stops reading after READ_BUDGET
seconds; the rest are read next time. Name matching only looks at key-like
columns, and skips pairs whose types can't join (a text code against a number).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from typing import Any

from .scanprogress import Report, quiet
from .store import Catalogue, now

READ_BUDGET = 120  # seconds of reading SQL per run; what's left is read on the next run
SAVE_EVERY = 2.0  # seconds between saves of what has been read, so stopping keeps it
READER_VERSION = "1"  # change when joins_in_sql changes, so everything is read again
KEY_LIKE = re.compile(r"(id|key|code|no)$", re.I)

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


SQL_KINDS = ("view", "procedure", "sql_snippet", "sql_script", "script", "notebook")


def _texts(attrs: dict[str, Any]) -> list[str]:
    texts = [attrs.get("definition") or "", attrs.get("sql") or "", *attrs.get("queries", [])]
    return [t for t in texts if isinstance(t, str) and t and "join" in t.lower()]


def _fingerprint(texts: list[str]) -> str:
    h = hashlib.sha1(READER_VERSION.encode())
    for t in texts:
        h.update(t.encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()


def read_joins(cat: Catalogue, report: Report = quiet, budget: float | None = None) -> dict[str, Any]:
    """Find the joins in every view, procedure and query, reading only what changed since last time.
    Returns {"joins": {asset key: [(table a, column a, table b, column b), …]}, "names": {key: name},
    "hints": {key: database}, "read": n, "reused": n, "left": n}."""
    budget = READ_BUDGET if budget is None else budget
    cached = {r["asset_key"]: (r["text_hash"], r["joins"])
              for r in cat.conn.execute("SELECT asset_key, text_hash, joins FROM relationship_reads")}
    # Only rows that mention a join are loaded; LIKE ignores case for plain letters.
    marks = ",".join("?" * len(SQL_KINDS))
    rows = cat.conn.execute(f"SELECT key, name, attrs FROM assets WHERE kind IN ({marks}) AND status = 'active' "
                            "AND attrs LIKE '%join%'", SQL_KINDS).fetchall()
    joins: dict[str, list[tuple[str, str, str, str]]] = {}
    names: dict[str, str] = {}
    hints: dict[str, str | None] = {}
    todo: list[tuple[str, str, list[str]]] = []
    reused = 0
    for r in rows:
        attrs = json.loads(r["attrs"])
        texts = _texts(attrs)
        if not texts:
            continue
        names[r["key"]] = r["name"]
        hints[r["key"]] = (attrs.get("database") or "").split("/")[-1] or None
        fp = _fingerprint(texts)
        old = cached.get(r["key"])
        if old and old[0] == fp:
            joins[r["key"]] = [tuple(j) for j in json.loads(old[1])]
            reused += 1
        else:
            if old:  # changed: keep last time's joins unless it is read again in this run
                joins[r["key"]] = [tuple(j) for j in json.loads(old[1])]
            todo.append((r["key"], fp, texts))
    began = last_save = last_report = time.monotonic()
    pending: list[tuple[str, str, str, str]] = []
    read = 0

    def save() -> None:
        if pending:
            with cat.conn:
                cat.conn.executemany("INSERT OR REPLACE INTO relationship_reads (asset_key, text_hash, joins, read_at) "
                                     "VALUES (?, ?, ?, ?)", pending)
            pending.clear()

    for i, (key, fp, texts) in enumerate(todo):
        t = time.monotonic()
        if t - began >= budget:  # >=: on Windows the clock moves in 15 ms steps
            break
        if t - last_report >= 0.5 or i == 0:
            report(f"reading the SQL of {names[key]} ({i + 1} of {len(todo)} changed since last time)", i, len(todo))
            last_report = t
        found = [j for text in texts for j in joins_in_sql(text)]
        joins[key] = found
        pending.append((key, fp, json.dumps(found), now()))
        read += 1
        if time.monotonic() - last_save >= SAVE_EVERY:
            save()
            last_save = time.monotonic()
    save()
    gone = set(cached) - {r["key"] for r in rows}
    if gone:
        with cat.conn:
            cat.conn.executemany("DELETE FROM relationship_reads WHERE asset_key = ?", [(k,) for k in gone])
    return {"joins": joins, "names": names, "hints": hints, "read": read, "reused": reused,
            "left": len(todo) - read}


def _type_family(data_type: str | None) -> str | None:
    """Rough family of a column type, to skip name matches that can't join. None when unknown."""
    t = (data_type or "").lower()
    if not t:
        return None
    if re.match(r"(big|small|tiny)?int|bit\b", t) or re.match(r"(decimal|numeric)\(\d+,\s*0\)", t):
        return "whole"
    if re.match(r"(decimal|numeric|float|real|money|smallmoney)", t):
        return "number"
    if re.match(r"n?(var)?char|text|ntext|string|object|sysname", t):
        return "text"
    if re.match(r"(date|time|smalldatetime|datetime)", t):
        return "date"
    if t == "uniqueidentifier":
        return "guid"
    return None


def _can_join(a: str | None, b: str | None) -> bool:
    fa, fb = _type_family(a), _type_family(b)
    return fa is None or fb is None or fa == fb


def find_suggestions(cat: Catalogue, report: Report = quiet, budget: float | None = None) -> int:
    """Suggest relationships from joins in SQL and from matching key names. Keeps your confirmations
    and rejections. Returns how many suggestions there are now. `report(text, done, total)` hears how far
    the reading of SQL has got."""
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

    read = read_joins(cat, report, budget)
    resolved: dict[tuple[str, str | None], str | None] = {}

    def resolve(name: str, hint: str | None) -> str | None:
        if (name, hint) not in resolved:
            resolved[(name, hint)] = _resolve(name, by_name, tables, hint)
        return resolved[(name, hint)]

    for key, pairs in read["joins"].items():
        hint = read["hints"].get(key)
        for a, ac, b, bc in pairs:
            ka, kb = resolve(a, hint), resolve(b, hint)
            if ka and kb:
                add(ka, ac, kb, bc, "join", f"joined in {read['names'].get(key, key)}")
    report("matching key names across tables", None, None)

    # Matching names: a key-like column named like another table's single-column primary key, of a type
    # that can join it. Only key-like columns of active tables and views are loaded.
    pk_owner: dict[tuple[str, str, str], str] = {}
    for k, t in tables.items():
        if len(t["pk"]) == 1 and KEY_LIKE.search(t["pk"][0]):
            pk_owner[(t["source"], t["database"], t["pk"][0].lower())] = k
    if pk_owner:
        wanted = {name for (_, _, name) in pk_owner}
        cols = {(r["asset_key"], r["name"].lower()): (r["name"], r["data_type"]) for r in cat.conn.execute(
            "SELECT c.asset_key, c.name, c.data_type FROM columns c JOIN assets a ON a.key = c.asset_key "
            "WHERE a.kind IN ('table', 'view') AND a.status = 'active'") if r["name"].lower() in wanted}
        for (asset_key, low), (name, data_type) in cols.items():
            t = tables.get(asset_key)
            if not t:
                continue
            owner = pk_owner.get((t["source"], t["database"], low))
            if not owner or owner == asset_key or [c.lower() for c in t["pk"]] == [low]:
                continue
            pk_col = tables[owner]["pk"][0]
            if not _can_join(data_type, cols.get((owner, low), (None, None))[1]):
                continue
            add(asset_key, name, owner, pk_col, "name", f"{name} matches the primary key of {tables[owner]['name']}")

    declared = {(r["src_key"], r["src_columns"].lower(), r["dst_key"], r["dst_columns"].lower())
                for r in cat.conn.execute("SELECT * FROM relationships WHERE how = 'fk'")}
    decided = {(r["src_key"], r["src_columns"].lower(), r["dst_key"], r["dst_columns"].lower()): r["state"]
               for r in cat.conn.execute("SELECT * FROM relationships WHERE how != 'fk' AND state IN "
                                         "('confirmed', 'rejected')")}
    rows = []
    for (a, ac, b, bc), (how, ev) in found.items():
        low = (a, ac.lower(), b, bc.lower())
        if low in declared or (b, bc.lower(), a, ac.lower()) in declared:
            continue
        if low in decided or (b, bc.lower(), a, ac.lower()) in decided:
            continue
        rows.append((a, ac, b, bc, how, ev))
    with cat.conn:
        cat.conn.execute("DELETE FROM relationships WHERE how != 'fk' AND state = 'suggested'")
        cat.conn.executemany("INSERT OR IGNORE INTO relationships (src_key, src_columns, dst_key, dst_columns, how, "
                             "state, evidence) VALUES (?, ?, ?, ?, ?, 'suggested', ?)", rows)
    cat.set_setting("relationship_reading", {"read": read["read"], "reused": read["reused"], "left": read["left"],
                                             "at": now()}, quiet=True)
    if read["left"]:
        report(f"  relationship suggestions: {read['left']} changed views or procedures left to read next time "
               f"(stopped after {READ_BUDGET if budget is None else budget:.0f}s)", None, None)
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
