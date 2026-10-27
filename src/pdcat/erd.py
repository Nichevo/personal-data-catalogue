"""Entity relationship diagrams for SQL Server databases, split into subject areas.

A database often holds several groups of tables that have little to do with
each other. The tables are grouped automatically into subject areas: tables
joined to each other (foreign keys, joins confirmed or found in code,
shared company-wide keys, views over tables) end up together, and tables
with no joins are grouped by schema and by the first word of their name.
Tables joined to almost everything (a date or store table) are "shared":
they sit in the area they're most joined to and are drawn in the others too.

You can rename areas, move tables between them and add your own areas; what
you change is kept when the areas are suggested again.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from typing import Any

from .store import Catalogue, now

STOP = {"tbl", "vw", "v", "t", "dim", "fact", "stg", "tmp", "temp", "the", "and", "of", "by"}


def databases(cat: Catalogue) -> list[dict[str, Any]]:
    return [{"key": r["key"], "name": r["name"], "tables": r["n"]} for r in cat.conn.execute(
        "SELECT d.key, d.name, (SELECT COUNT(*) FROM assets t WHERE t.parent = d.key AND t.kind IN ('table', 'view') "
        "AND t.status = 'active') AS n FROM assets d WHERE d.kind = 'database' AND d.status = 'active' ORDER BY d.name")]


def tables(cat: Catalogue, db_key: str) -> dict[str, dict[str, Any]]:
    return {r["key"]: {**dict(r), "attrs": json.loads(r["attrs"])} for r in cat.conn.execute(
        "SELECT key, kind, name, attrs FROM assets WHERE kind IN ('table', 'view') AND status = 'active' AND "
        "(parent = ? OR key LIKE ?) ORDER BY name", (db_key, db_key + "/%"))}


def _edges(cat: Catalogue, keys: set[str]) -> dict[tuple[str, str], float]:
    from .keys import key_columns, list_keys
    w: dict[tuple[str, str], float] = defaultdict(float)

    def add(a: str, b: str, weight: float) -> None:
        if a != b and a in keys and b in keys:
            w[tuple(sorted((a, b)))] += weight

    for r in cat.conn.execute("SELECT src_key, dst_key, state FROM relationships WHERE state != 'rejected'"):
        add(r["src_key"], r["dst_key"], {"declared": 3, "confirmed": 3}.get(r["state"], 1))
    for r in cat.conn.execute("SELECT src_key, dst_key FROM links WHERE dst_key IS NOT NULL AND relation = 'reads'"):
        add(r["src_key"], r["dst_key"], 2)  # a view over a table
    for k in list_keys(cat):
        where = [c["asset_key"] for c in key_columns(cat, k["name"], "confirmed") if c["asset_key"] in keys]
        if 2 <= len(where) <= 12:  # keys found everywhere say little about grouping
            for i, a in enumerate(where):
                for b in where[i + 1:]:
                    add(a, b, 0.5)
    return w


def _words(name: str) -> list[str]:
    name = name.split(".")[-1]
    parts = re.split(r"[_\s]+|(?<=[a-z])(?=[A-Z])", name)
    return [p.lower() for p in parts if p and p.lower() not in STOP and not p.isdigit()]


def _name_for(members: list[str], items: dict[str, dict], taken: set[str]) -> str:
    words = Counter(w for k in members for w in dict.fromkeys(_words(items[k]["name"])))
    schema = Counter(items[k]["attrs"].get("schema", "") for k in members).most_common(1)[0][0]
    base = words.most_common(1)[0][0].title() if words and words.most_common(1)[0][1] >= 2 else \
        (items[members[0]]["name"].split(".")[-1] if len(members) == 1 else f"{schema} tables")
    name, i = base, 2
    while name.lower() in taken:
        name, i = f"{base} {i}", i + 1
    taken.add(name.lower())
    return name


def suggest_areas(cat: Catalogue, db_key: str) -> list[dict[str, Any]]:
    """Work out subject areas again, keeping areas and placements you made by hand."""
    items = tables(cat, db_key)
    keys = set(items)
    manual_areas = {r["uid"]: r["name"] for r in cat.conn.execute(
        "SELECT uid, name FROM subject_areas WHERE database_key = ? AND manual = 1", (db_key,))}
    pinned = {r["asset_key"]: r["area_uid"] for r in cat.conn.execute(
        "SELECT m.asset_key, m.area_uid FROM area_members m JOIN subject_areas a ON a.uid = m.area_uid "
        "WHERE a.database_key = ? AND m.manual = 1", (db_key,))}
    edges = _edges(cat, keys)
    nbrs: dict[str, dict[str, float]] = defaultdict(dict)
    for (a, b), wt in edges.items():
        nbrs[a][b] = wt
        nbrs[b][a] = wt
    hub_limit = max(6, len(keys) // 4)
    hubs = {k for k in keys if len(nbrs[k]) >= hub_limit}
    # Label propagation over the non-hub tables.
    label = {k: k for k in keys}
    free = sorted(k for k in keys if k not in hubs and k not in pinned)
    for k, area in pinned.items():
        label[k] = f"area:{area}"
    for _ in range(20):
        changed = False
        for k in free:
            scores: dict[str, float] = defaultdict(float)
            for n, wt in nbrs[k].items():
                if n not in hubs:
                    scores[label[n]] += wt
            if scores:
                best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                if best != label[k]:
                    label[k] = best
                    changed = True
        if not changed:
            break
    groups: dict[str, list[str]] = defaultdict(list)
    for k in keys - hubs:
        groups[label[k]].append(k)
    # Lone tables: by schema and the first word of the name.
    lone = [g for g, m in groups.items() if len(m) == 1 and not g.startswith("area:") and not nbrs[m[0]]]
    for g in lone:
        k = groups.pop(g)[0]
        words = _words(items[k]["name"])
        groups[f"lone:{items[k]['attrs'].get('schema', '')}:{words[0] if words else ''}"].append(k)
    for g in [g for g, m in groups.items() if g.startswith("lone:") and len(m) < 2]:
        k = groups.pop(g)[0]
        groups[f"lone:{items[k]['attrs'].get('schema', '')}:"].append(k)
    member_area = {k: g for g, m in groups.items() for k in m}
    shared: dict[str, str] = {}
    for h in hubs:
        if h in pinned:
            groups[f"area:{pinned[h]}"].append(h)
            continue
        votes = Counter(member_area[n] for n in nbrs[h] if n in member_area)
        home = votes.most_common(1)[0][0] if votes else f"lone:{items[h]['attrs'].get('schema', '')}:"
        groups[home].append(h)
        shared[h] = home
    with cat.conn:
        old = [r["uid"] for r in cat.conn.execute("SELECT uid FROM subject_areas WHERE database_key = ? AND manual = 0",
                                                  (db_key,))]
        for uid in old:
            cat.conn.execute("DELETE FROM subject_areas WHERE uid = ?", (uid,))
            cat.conn.execute("DELETE FROM area_members WHERE area_uid = ?", (uid,))
        for uid in manual_areas:
            cat.conn.execute("DELETE FROM area_members WHERE area_uid = ? AND manual = 0", (uid,))
        taken = {n.lower() for n in manual_areas.values()}
        for g, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if g.startswith("area:"):
                uid = g[5:]
            else:
                uid = uuid.uuid4().hex
                cat.conn.execute("INSERT INTO subject_areas (uid, database_key, name, manual, created_at) VALUES "
                                 "(?, ?, ?, 0, ?)", (uid, db_key, _name_for(members, items, taken), now()))
            for k in members:
                cat.conn.execute("INSERT OR REPLACE INTO area_members (area_uid, asset_key, shared, manual) VALUES "
                                 "(?, ?, ?, ?)", (uid, k, int(k in shared), int(k in pinned)))
    cat.changed()
    return areas(cat, db_key)


def areas(cat: Catalogue, db_key: str) -> list[dict[str, Any]]:
    out = []
    for a in cat.conn.execute("SELECT * FROM subject_areas WHERE database_key = ? ORDER BY name", (db_key,)):
        members = [dict(r) for r in cat.conn.execute(
            "SELECT m.asset_key AS key, m.shared, m.manual, t.name FROM area_members m LEFT JOIN assets t ON "
            "t.key = m.asset_key WHERE m.area_uid = ? ORDER BY t.name", (a["uid"],))]
        out.append({**dict(a), "members": members})
    out.sort(key=lambda a: -len(a["members"]))
    return out


def ensure_areas(cat: Catalogue, db_key: str) -> list[dict[str, Any]]:
    have = areas(cat, db_key)
    placed = {m["key"] for a in have for m in a["members"]}
    if not have or set(tables(cat, db_key)) - placed:
        return suggest_areas(cat, db_key)
    return have


def rename_area(cat: Catalogue, uid: str, name: str) -> None:
    if not name.strip():
        raise ValueError("An area needs a name.")
    with cat.conn:
        cat.conn.execute("UPDATE subject_areas SET name = ?, manual = 1 WHERE uid = ?", (name.strip(), uid))
    cat.changed()


def new_area(cat: Catalogue, db_key: str, name: str) -> str:
    if not name.strip():
        raise ValueError("An area needs a name.")
    uid = uuid.uuid4().hex
    with cat.conn:
        cat.conn.execute("INSERT INTO subject_areas (uid, database_key, name, manual, created_at) VALUES (?, ?, ?, 1, ?)",
                         (uid, db_key, name.strip(), now()))
    cat.changed()
    return uid


def move_tables(cat: Catalogue, keys: list[str], area_uid: str) -> None:
    db = cat.conn.execute("SELECT database_key FROM subject_areas WHERE uid = ?", (area_uid,)).fetchone()
    if not db:
        raise ValueError("No such area")
    with cat.conn:
        for k in keys:
            cat.conn.execute("DELETE FROM area_members WHERE asset_key = ? AND area_uid IN (SELECT uid FROM "
                             "subject_areas WHERE database_key = ?)", (k, db[0]))
            cat.conn.execute("INSERT INTO area_members (area_uid, asset_key, shared, manual) VALUES (?, ?, 0, 1)",
                             (area_uid, k))
        cat.conn.execute("UPDATE subject_areas SET manual = 1 WHERE uid = ?", (area_uid,))
    cat.changed()


def delete_area(cat: Catalogue, uid: str) -> None:
    """Remove an area; its tables are placed again next time areas are suggested."""
    with cat.conn:
        cat.conn.execute("DELETE FROM subject_areas WHERE uid = ?", (uid,))
        cat.conn.execute("DELETE FROM area_members WHERE area_uid = ?", (uid,))
    cat.changed()


def _rels(cat: Catalogue, keys: set[str]) -> list[dict[str, Any]]:
    from .relationships import relationships
    return relationships(cat, keys)


def _key_cols(cat: Catalogue, key: str, attrs: dict, rels: list[dict]) -> tuple[list[str], list[str]]:
    pk = list(attrs.get("primary_key") or [])
    fk = []
    for r in rels:
        if r["src_key"] == key:
            fk += [c for c in r["src_columns"].split(",") if c and c not in pk and c not in fk]
    return pk, fk


def erd_elements(cat: Catalogue, db_key: str, area_uid: str | None = None) -> list[dict[str, Any]]:
    items = tables(cat, db_key)
    all_areas = ensure_areas(cat, db_key)
    chosen = [a for a in all_areas if area_uid in (None, a["uid"])]
    keys = {m["key"] for a in chosen for m in a["members"] if m["key"] in items}
    if area_uid:  # also draw tables in other areas that this area's tables join to
        for r in _rels(cat, set(items)):
            if r["src_key"] in keys or r["dst_key"] in keys:
                keys |= {r["src_key"], r["dst_key"]}
    rels = _rels(cat, keys)
    nodes = []
    placed = set()
    for a in chosen:
        gid = f"grp:area:{a['uid']}"
        inside = [m for m in a["members"] if m["key"] in keys]
        if not inside:
            continue
        nodes.append({"data": {"id": gid, "label": a["name"], "kind": "group", "count": len(inside), "open": True}})
        for m in inside:
            nodes.append(_table_node(cat, m["key"], items[m["key"]], rels, gid, m["shared"]))
            placed.add(m["key"])
    others = keys - placed
    if others:
        nodes.append({"data": {"id": "grp:area:other", "label": "In other areas", "kind": "group",
                               "count": len(others)}})
        nodes += [_table_node(cat, k, items[k], rels, "grp:area:other", False) for k in sorted(others)]
    edges = [{"data": {"id": f"r{i}", "source": r["src_key"], "target": r["dst_key"],
                       "relation": r["state"], "label": f"{r['src_columns']} = {r['dst_columns']}"}}
             for i, r in enumerate(rels) if r["src_key"] in keys and r["dst_key"] in keys]
    return nodes + edges


def _table_node(cat: Catalogue, key: str, t: dict, rels: list[dict], parent: str, shared: bool) -> dict:
    pk, fk = _key_cols(cat, key, t["attrs"], rels)
    lines = [t["name"] + (" (shared)" if shared else "")]
    lines += [f"🔑 {c}" for c in pk] + [f"→ {c}" for c in fk[:6]]
    return {"data": {"id": key, "label": "\n".join(lines), "kind": t["kind"], "key": key, "parent": parent}}


def _mid(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def to_mermaid(cat: Catalogue, db_key: str, area_uid: str | None = None, *, columns: bool = True) -> str:
    items = tables(cat, db_key)
    keys = {e["data"]["id"] for e in erd_elements(cat, db_key, area_uid) if "source" not in e["data"]} & set(items)
    rels = _rels(cat, keys)
    out = ["erDiagram"]
    for k in sorted(keys):
        t = items[k]
        pk, fk = _key_cols(cat, k, t["attrs"], rels)
        cols = cat.columns(k) if columns else []
        if cols:
            out.append(f"    {_mid(t['name'])} {{")
            for c in cols:
                ty = re.sub(r"[^A-Za-z0-9_]", "_", (c["data_type"] or "unknown").split("(")[0])
                mark = " PK" if c["name"] in pk else (" FK" if c["name"] in fk else "")
                out.append(f"        {ty} {_mid(c['name'])}{mark}")
            out.append("    }")
        else:
            out.append(f"    {_mid(t['name'])}")
    for r in rels:
        if r["src_key"] in keys and r["dst_key"] in keys:
            line = "}o--||" if r["state"] in ("declared", "confirmed") else "}o..||"
            out.append(f"    {_mid(items[r['src_key']]['name'])} {line} {_mid(items[r['dst_key']]['name'])} : "
                       f"\"{r['src_columns']}\"")
    return "\n".join(out) + "\n"
