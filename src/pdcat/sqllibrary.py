"""The SQL query library: every piece of SQL found anywhere, in one list.

SQL turns up in Python strings, .sql files, notebooks' %%sql cells, Excel connections, Power Query
native queries and macros, Power BI reports, Access saved queries and SQL you pasted in. The same
query is often copied into several places; it is stored once (by its text, ignoring case, spacing
and comments) with every place it's found. Each gets a name from where it was found (the variable
it's assigned to, the comment above it, the function or file), which you can change; your name
sticks. A query found nowhere on the latest scan is kept, marked missing.

Each query records the tables it uses and how: which columns, which joins, which renames. Table
pages use that to list "SQL using this table", the active ones first.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import PurePath
from typing import Any

from .mquery import redact
from .sqlrefs import looks_like_sql, strip_comments
from .store import Catalogue, now

RECENT_DAYS = 90
CODE_KINDS = ("script", "notebook", "sql_script")


def normalise_sql(sql: str) -> str:
    text = strip_comments(sql or "")
    text = re.sub(r"\s+", " ", text).strip().rstrip(";").strip()
    return text.lower()


def uid_for(sql: str) -> str:
    return hashlib.sha1(normalise_sql(sql).encode("utf-8")).hexdigest()[:16]


def _place(sql: str, item: str, location: str, hint: str | None, source: str, **extra: Any) -> dict[str, Any] | None:
    sql = (sql or "").strip()
    if not sql or not looks_like_sql(sql):
        return None
    return {"sql": redact(sql), "item": item, "location": location, "name_hint": (hint or "").strip() or None,
            "source": source, **extra}


def gather(cat: Catalogue) -> list[dict[str, Any]]:
    """Every place SQL is found: {"sql", "item", "location", "name_hint", "source"}."""
    out: list[dict[str, Any] | None] = []
    for r in cat.conn.execute(
            "SELECT key, kind, name, source, attrs FROM assets WHERE status = 'active' AND kind IN "
            "('script', 'notebook', 'sql_script', 'workbook', 'report', 'access_db', 'sql_snippet', 'view', "
            "'procedure')"):
        a = json.loads(r["attrs"])
        key, kind, src = r["key"], r["kind"], r["source"]
        stem = PurePath(r["name"]).stem
        if kind in CODE_KINDS:
            for q in a.get("sql_strings") or []:
                loc = "whole file" if q.get("whole_file") else (
                    f"cell {q['cell']}, line {q.get('line') or 1}" if q.get("cell") else f"line {q.get('line') or 0}")
                hint = q.get("name") or q.get("function") or stem
                out.append(_place(q["sql"], key, loc, hint, src, function=q.get("function"),
                                  built_from_variables=bool(q.get("built_from_variables"))))
        elif kind in ("workbook", "report"):
            for q in a.get("queries") or []:
                for i, sql in enumerate(q.get("sql") or []):
                    loc = f"Power Query {q.get('name', '')}" + (f" ({i + 1})" if i else "")
                    out.append(_place(sql, key, loc, q.get("name"), src))
            for c in a.get("connections") or []:
                if c.get("command") and c.get("type") != "Power Query":
                    out.append(_place(c["command"], key, f"connection {c.get('name', '')}", c.get("name"), src))
            for v in a.get("vba_sql") or []:
                out.append(_place(v.get("sql", ""), key, f"macro {v.get('module', '')}", v.get("module"), src))
        elif kind == "access_db":
            for q in a.get("queries") or []:
                out.append(_place(q.get("sql", ""), key, f"saved query {q.get('name', '')}", q.get("name"), src))
        elif kind == "sql_snippet":
            out.append(_place(a.get("sql", ""), key, "pasted SQL", r["name"], src))
        elif kind in ("view", "procedure") and a.get("definition"):
            out.append(_place(a["definition"], key, f"{kind} definition", r["name"], src,
                              database=f"{src}/{a.get('database', '')}".lower()))
    from .documents import all_latest, sql_blocks
    for d in all_latest(cat):
        for i, sql in enumerate(sql_blocks(d["text"] or "")):
            out.append(_place(sql, f"doc://{d['uid']}", f"SQL block {i + 1}", d["title"], "documents"))
    return [p for p in out if p]


def _hints(cat: Catalogue) -> dict[str, str]:
    """Item -> where its bare table names live ("<source>/<db>"), from the links scanning made."""
    out: dict[str, str] = {}
    for r in cat.conn.execute("SELECT src_key, hint FROM links WHERE hint IS NOT NULL AND hint != ''"):
        out.setdefault(r[0], r[1])
    return out


def _resolve_tables(cat: Catalogue, analysis: dict[str, Any], hint: str | None, index) -> list[dict[str, Any]]:
    out = []
    for t in analysis.get("tables", []):
        cands = cat.match_table(t["name"], hint, index)
        out.append({"name": t["name"], "alias": t.get("alias"), "relation": t.get("relation", "reads"),
                    "key": cands[0]["key"] if len(cands) == 1 else None,
                    "columns": analysis.get("columns", {}).get(t["name"], [])})
    return out


def _auto_name(places: list[dict[str, Any]], tables: list[dict[str, Any]], uid: str) -> str:
    # A name someone gave it (a variable, a comment, a saved query) beats a function or a file name.
    for p in places:
        h = p.get("name_hint")
        if h and h != p.get("function") and not re.match(r"^cell \d+$", h):
            return h
    for p in places:
        if p.get("name_hint"):
            return p["name_hint"]
    if tables:
        return f"{tables[0]['name']} query"
    return f"query {uid[:6]}"


def build_library(cat: Catalogue) -> int:
    """Gather SQL from everything catalogued into the query library. Returns how many queries it holds;
    `cat.setting("sql_library_counts")` has the details (new, places, missing)."""
    from .sqlparser import VERSION, analyse
    places = gather(cat)
    by_uid: dict[str, list[dict[str, Any]]] = {}
    for p in places:
        by_uid.setdefault(uid_for(p["sql"]), []).append(p)
    existing = {r["uid"]: dict(r) for r in cat.conn.execute("SELECT uid, name, named_by, analysis FROM sql_queries")}
    hints = _hints(cat)
    index = cat.table_index()
    ts = now()
    new = 0
    with cat.conn:
        for uid, ps in by_uid.items():
            ps.sort(key=lambda p: (p["item"], p["location"]))
            sql = max((p["sql"] for p in ps), key=len)
            old = existing.get(uid)
            analysis = json.loads(old["analysis"]) if old and old["analysis"] not in (None, "", "{}") else {}
            if analysis.get("version") != VERSION:  # read again with the latest reading (joins in WHERE, ...)
                analysis = analyse(sql)
            hint = next((p["database"] for p in ps if p.get("database")), None) or \
                next((hints[p["item"]] for p in ps if p["item"] in hints), None)
            if not hint:  # an Access query's bare names are that file's own tables first
                access = next((p for p in ps if p["location"].startswith("saved query")), None)
                if access:
                    hint = f"{access['source']}/{PurePath(access['item']).stem}"
            tables = _resolve_tables(cat, analysis, hint, index)
            name = old["name"] if old and old["named_by"] == "you" else _auto_name(ps, tables, uid)
            if old:
                cat.conn.execute("UPDATE sql_queries SET name = ?, sql = ?, tables = ?, analysis = ?, last_seen = ?, "
                                 "status = 'active' WHERE uid = ?",
                                 (name, sql, json.dumps(tables), json.dumps(analysis), ts, uid))
            else:
                new += 1
                cat.conn.execute("INSERT INTO sql_queries (uid, name, named_by, sql, tables, analysis, first_seen, "
                                 "last_seen, status) VALUES (?, ?, 'auto', ?, ?, ?, ?, ?, 'active')",
                                 (uid, name, sql, json.dumps(tables), json.dumps(analysis), ts, ts))
        cat.conn.execute("DELETE FROM sql_query_places")
        cat.conn.executemany(
            "INSERT OR IGNORE INTO sql_query_places (query_uid, item, location, name_hint, source) VALUES (?, ?, ?, ?, ?)",
            [(uid, p["item"], p["location"], p["name_hint"], p["source"]) for uid, ps in by_uid.items() for p in ps])
        gone = [u for u in existing if u not in by_uid]
        cat.conn.executemany("UPDATE sql_queries SET status = 'missing' WHERE uid = ?", [(u,) for u in gone])
        # Remember extra facts about each place (built from variables, function) for the checks.
        cat.set_setting("sql_place_facts", {f"{uid}|{p['item']}|{p['location']}": {
            k: p[k] for k in ("function", "built_from_variables") if p.get(k)}
            for uid, ps in by_uid.items() for p in ps if p.get("function") or p.get("built_from_variables")},
            quiet=True)
    cat.set_setting("sql_library_counts", {"queries": len(by_uid), "new": new, "places": len(places),
                                           "missing": len(gone)}, quiet=True)
    return len(by_uid)


def _row(r) -> dict[str, Any]:
    d = dict(r)
    d["tables"] = json.loads(d.get("tables") or "[]")
    if "analysis" in d:
        d["analysis"] = json.loads(d.get("analysis") or "{}")
    return d


def queries(cat: Catalogue, *, table: str | None = None, item: str | None = None, text: str | None = None,
            include_missing: bool = False) -> list[dict[str, Any]]:
    """The library, with how many places each query is in. Filter by a table key, an item it's in, or text."""
    where, params = [], []
    if not include_missing:
        where.append("q.status = 'active'")
    if item:
        where.append("q.uid IN (SELECT query_uid FROM sql_query_places WHERE item = ?)")
        params.append(item)
    if text:
        where.append("(q.name LIKE ? OR q.sql LIKE ?)")
        params += [f"%{text}%", f"%{text}%"]
    sql = ("SELECT q.uid, q.name, q.named_by, q.sql, q.tables, q.status, q.first_seen, q.last_seen, "
           "(SELECT COUNT(*) FROM sql_query_places p WHERE p.query_uid = q.uid) AS places FROM sql_queries q"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY q.name COLLATE NOCASE")
    out = [_row(r) for r in cat.conn.execute(sql, params)]
    if table:
        out = [q for q in out if any(t.get("key") == table for t in q["tables"])]
    return out


def get(cat: Catalogue, uid: str) -> dict[str, Any] | None:
    r = cat.conn.execute("SELECT * FROM sql_queries WHERE uid = ?", (uid,)).fetchone()
    if r is None:
        return None
    q = _row(r)
    facts = cat.setting("sql_place_facts", {})
    q["places"] = [{**dict(p), **facts.get(f"{uid}|{p['item']}|{p['location']}", {})} for p in cat.conn.execute(
        "SELECT * FROM sql_query_places WHERE query_uid = ? ORDER BY item, location", (uid,))]
    return q


def rename(cat: Catalogue, uid: str, name: str, *, record_edit: bool = True, named_by: str = "you") -> None:
    """Give a query your own name (an empty name goes back to the automatic one on the next build)."""
    old = cat.conn.execute("SELECT name, named_by FROM sql_queries WHERE uid = ?", (uid,)).fetchone()
    if old is None:
        raise KeyError(uid)
    name = name.strip()
    with cat.conn:
        if name:
            cat.conn.execute("UPDATE sql_queries SET name = ?, named_by = ? WHERE uid = ?", (name, named_by, uid))
        else:
            cat.conn.execute("UPDATE sql_queries SET named_by = 'auto' WHERE uid = ?", (uid,))
        if record_edit:
            from .edits import record
            record(cat, f"query://{uid}", "query name", old["name"], name or old["name"],
                   {"type": "query_name", "uid": uid, "old": old["name"], "old_by": old["named_by"]})
    cat.changed()


def all_with_places(cat: Catalogue, *, include_missing: bool = False, table: str | None = None) -> list[dict[str, Any]]:
    """Every query (or those using one table key) with its places, read in two passes."""
    where, params = [], []
    if not include_missing:
        where.append("status = 'active'")
    if table:  # a quick text filter first; checked exactly below
        where.append("instr(tables, ?) > 0")
        params.append(json.dumps(table))
    rows = [_row(r) for r in cat.conn.execute(
        "SELECT * FROM sql_queries" + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY name COLLATE NOCASE", params)]
    if table:
        rows = [q for q in rows if any(t.get("key") == table for t in q["tables"])]
    if not rows:
        return []
    facts = cat.setting("sql_place_facts", {})
    places: dict[str, list[dict[str, Any]]] = {}
    uids = {q["uid"] for q in rows}
    for p in cat.conn.execute("SELECT * FROM sql_query_places ORDER BY item, location"):
        if p["query_uid"] in uids:
            places.setdefault(p["query_uid"], []).append(
                {**dict(p), **facts.get(f"{p['query_uid']}|{p['item']}|{p['location']}", {})})
    return [{**q, "places": places.get(q["uid"], [])} for q in rows]


def queries_in(cat: Catalogue, item: str) -> list[dict[str, Any]]:
    """The queries found in one script, workbook, report or Access file, with where."""
    return [{**_row(r)} for r in cat.conn.execute(
        "SELECT q.uid, q.name, q.tables, q.status, p.location FROM sql_query_places p JOIN sql_queries q "
        "ON q.uid = p.query_uid WHERE p.item = ? ORDER BY p.location", (item,))]


def activity(cat: Catalogue, q: dict[str, Any], *, ctx: dict[str, Any] | None = None) -> list[str]:
    """Why a query looks in use: scheduled, writes files that keep refreshing, the current version of a
    script, calls a procedure that exists, or its file changed recently. Empty when nothing says so."""
    ctx = ctx if ctx is not None else context(cat)
    reasons = []
    items = [p["item"] for p in q["places"]] if "places" in q else [r[0] for r in cat.conn.execute(
        "SELECT item FROM sql_query_places WHERE query_uid = ?", (q["uid"],))]
    live = [i for i in items if i not in ctx["hidden"]]
    if any(i in ctx["scheduled"] for i in live):
        reasons.append("scheduled")
    if any(i in ctx["refreshing_writers"] for i in live):
        reasons.append("writes files that keep refreshing")
    if any(i in ctx["current_versions"] for i in live):
        reasons.append("current version of its script")
    if any(i in ctx["objects"] for i in live):
        reasons.append("a view or procedure that exists now")
    elif any(t.get("relation") == "calls" and t.get("key") for t in q["tables"]):
        reasons.append("calls a procedure that exists")
    if any(ctx["recent"].get(i) for i in live):
        reasons.append("changed recently")
    return reasons


def context(cat: Catalogue) -> dict[str, Any]:
    """What `activity` needs, worked out once per page."""
    from .curation import hidden_keys
    from .folderroles import archived
    from .groups import older_versions
    from .schedules import scheduled_keys
    hidden = hidden_keys(cat, old=True) | archived(cat) | older_versions(cat)
    from .freshness import _log, status
    log = _log(cat)
    refreshing = {k for k in log if (status(cat, k, log) or {}).get("state") == "refreshing"}
    writers: set[str] = set()
    if refreshing:
        writers = {r[0] for r in cat.conn.execute(
            f"SELECT DISTINCT src_key FROM links WHERE relation = 'writes' AND dst_key IN "
            f"({', '.join('?' * len(refreshing))})", tuple(refreshing))}
    current = {r[0] for r in cat.conn.execute("SELECT main_key FROM item_groups WHERE type = 'versions' "
                                              "AND main_key IS NOT NULL")}
    cutoff = time.time() - RECENT_DAYS * 86400
    recent = {}
    for r in cat.conn.execute("SELECT DISTINCT p.item, a.attrs FROM sql_query_places p JOIN assets a ON a.key = p.item"):
        m = json.loads(r["attrs"]).get("mtime")
        recent[r["item"]] = bool(m and m >= cutoff)
    from .gitinfo import last_change
    iso_cutoff = _iso(cutoff)
    for k, v in last_change(cat).items():
        if v.get("at") and v["at"] >= iso_cutoff:
            recent[k] = True
    objects = {r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE kind IN ('view', 'procedure') AND "
                                              "status = 'active'")}
    return {"hidden": hidden, "scheduled": scheduled_keys(cat), "refreshing_writers": writers, "objects": objects,
            "current_versions": current, "recent": recent}


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def using_table(cat: Catalogue, table_key: str) -> list[dict[str, Any]]:
    """Queries that use a table, active ones first: each with how it uses the table (reads or writes,
    which columns, what it joins to on which columns), why it looks active, and whether it looks old
    (everywhere it's found is marked old, an older version, archived, or it's missing)."""
    found = all_with_places(cat, include_missing=True, table=table_key)
    if not found:
        return []
    ctx = context(cat)
    out = []
    for q in found:
        mine = [t for t in q["tables"] if t.get("key") == table_key]
        names = {t["name"] for t in mine}
        joins = [j for j in q["analysis"].get("joins", []) if j["left_table"] in names or j["right_table"] in names]
        by_name = {t["name"]: t for t in q["tables"]}
        join_text = []
        for j in joins:
            here_left = j["left_table"] in names
            other = j["right_table"] if here_left else j["left_table"]
            ours, theirs = (j["left_column"], j["right_column"]) if here_left else (j["right_column"], j["left_column"])
            join_text.append({"other": other, "other_key": by_name.get(other, {}).get("key"), "column": ours,
                              "other_column": theirs, "kind": j["kind"]})
        reasons = activity(cat, q, ctx=ctx)
        from .sqlchecks import SEVERITY_ICONS, findings_for
        found = [f for f in findings_for(cat, q) if not f["ok"]]
        old = q["status"] == "missing" or bool(q["places"]) and all(p["item"] in ctx["hidden"] for p in q["places"])
        out.append({"uid": q["uid"], "name": q["name"], "status": q["status"], "relation": ", ".join(
            sorted({t["relation"] for t in mine})), "columns": sorted({c for t in mine for c in t["columns"]},
                                                                   key=str.lower),
                    "joins": join_text, "active": reasons, "old": old, "places": q["places"],
                    "checks": " ".join(f"{SEVERITY_ICONS[f['severity']]} {f['title']}" for f in found[:3])})
    out.sort(key=lambda r: (r["old"], -len(r["active"]), r["name"].lower()))
    return out


def field_elements(q: dict[str, Any], cat: Catalogue | None = None) -> list[dict[str, Any]]:
    """A field diagram for one query: each table with the columns the query uses, joins drawn between
    them labelled with their columns (plus the database's keys between them, dashed), and the columns
    it renames."""
    nodes, edges = [], []
    ids = {}
    for t in q["tables"]:
        nid = t.get("key") or f"sqlname:{t['name']}"
        ids[t["name"]] = nid
        cols = t.get("columns") or []
        label = "\n".join([t["name"] + ("" if t.get("key") else " (not scanned)")]
                          + [f"• {c}" for c in cols[:20]] + ([f"… {len(cols) - 20} more"] if len(cols) > 20 else []))
        data = {"id": nid, "label": label, "kind": "table" if t.get("key") else "unmatched"}
        if t.get("key"):
            data["key"] = t["key"]
        nodes.append({"data": data})
    for i, j in enumerate(q["analysis"].get("joins", [])):
        a, b = ids.get(j["left_table"]), ids.get(j["right_table"])
        if a and b:
            edges.append({"data": {"id": f"j{i}", "source": a, "target": b, "relation": "joins",
                                   "label": f"{j['left_column']} = {j['right_column']} ({j['kind']})"}})
    joined = {(e["data"]["source"], e["data"]["target"]) for e in edges}
    keys = {t["key"] for t in q["tables"] if t.get("key")}
    if cat is not None and len(keys) > 1:
        from .relationships import relationships
        for i, r in enumerate(relationships(cat, keys)):
            if r["state"] in ("declared", "confirmed") and (r["src_key"], r["dst_key"]) not in joined \
                    and (r["dst_key"], r["src_key"]) not in joined:
                edges.append({"data": {"id": f"k{i}", "source": r["src_key"], "target": r["dst_key"],
                                       "relation": "suggested", "label": f"key: {r['src_columns']} = {r['dst_columns']}"}})
    renames = q["analysis"].get("renames", [])
    if renames:
        nodes.append({"data": {"id": "sqlout", "label": "\n".join(
            ["Renamed in the result"] + [f"{r['column']} → {r['alias']}" for r in renames[:20]]), "kind": "query"}})
        for t in {r.get("table") for r in renames if r.get("table") in ids}:
            edges.append({"data": {"id": f"r-{t}", "source": ids[t], "target": "sqlout", "relation": "reads"}})
    return nodes + edges
