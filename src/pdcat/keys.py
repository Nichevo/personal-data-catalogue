"""Company-wide keys such as PlantID: the identifiers that tie data together across
databases, files and teams. You stay in control of them:

  * "Mark as company-wide key" on a column makes a key, with that table offered
    as the master list of values
  * after each scan, column names that turn up in many places are suggested as
    keys; accept or reject them (rejected ones stay gone)
  * columns elsewhere with the same name or an alias are suggested as carrying
    the key until you confirm them

Each key gets badges on its columns, join hints between the items that carry
it, and checks: types or widths that differ, and values outside the master's
range.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .store import Catalogue, now

KEY_NAME = re.compile(r"(id|code|key|no|num|number)$", re.I)
MIN_PLACES = 3


def norm(name: str) -> str:
    return re.sub(r"[\s_\-.]+", "", name or "").lower()


def _key(cat: Catalogue, name: str) -> dict[str, Any] | None:
    row = cat.conn.execute("SELECT * FROM keys WHERE lower(name) = lower(?)", (name,)).fetchone()
    if not row:
        return None
    k = dict(row)
    k["aliases"] = json.loads(k["aliases"] or "[]")
    return k


def get_key(cat: Catalogue, name: str) -> dict[str, Any] | None:
    return _key(cat, name)


def list_keys(cat: Catalogue, state: str = "active") -> list[dict[str, Any]]:
    out = []
    for r in cat.conn.execute("SELECT * FROM keys WHERE state = ? ORDER BY name", (state,)):
        k = dict(r)
        k["aliases"] = json.loads(k["aliases"] or "[]")
        k["columns"] = cat.conn.execute("SELECT COUNT(*) FROM key_columns WHERE key_name = ? AND state = 'confirmed'",
                                        (k["name"],)).fetchone()[0]
        k["suggested_columns"] = cat.conn.execute(
            "SELECT COUNT(*) FROM key_columns WHERE key_name = ? AND state = 'suggested'", (k["name"],)).fetchone()[0]
        out.append(k)
    return out


def mark_key(cat: Catalogue, name: str, asset_key: str, column: str, *, master: bool = True,
             description: str = "") -> str:
    """Make `column` of `asset_key` a company-wide key (or add it to an existing one)."""
    name = name.strip() or column
    ts = now()
    existing = _key(cat, name)
    with cat.conn:
        if existing:
            name = existing["name"]
            cat.conn.execute("UPDATE keys SET state = 'active', updated_at = ? WHERE name = ?", (ts, name))
            if master or not existing["master_key"]:
                cat.conn.execute("UPDATE keys SET master_key = ?, master_column = ? WHERE name = ?",
                                 (asset_key, column, name))
        else:
            cat.conn.execute(
                "INSERT INTO keys (name, state, description, master_key, master_column, aliases, evidence, created_at, "
                "updated_at, author) VALUES (?, 'active', ?, ?, ?, '[]', ?, ?, ?, ?)",
                (name, description or None, asset_key if master else None, column if master else None,
                 "marked by hand", ts, ts, cat.author()))
        _set_column(cat, name, asset_key, column, "confirmed", "marked by hand")
    suggest_columns(cat, name)
    cat.rebuild_search()
    return name


def _set_column(cat: Catalogue, name: str, asset_key: str, column: str, state: str, how: str) -> None:
    cat.conn.execute(
        "INSERT INTO key_columns (key_name, asset_key, column_name, state, how, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_key, column_name) DO UPDATE SET key_name = excluded.key_name, state = excluded.state, "
        "how = excluded.how, updated_at = excluded.updated_at", (name, asset_key, column, state, how, now()))


def suggest_columns(cat: Catalogue, name: str) -> int:
    """Suggest every column with the key's name or one of its aliases (never overriding your decisions)."""
    k = _key(cat, name)
    if not k:
        return 0
    wanted = {norm(k["name"]), *(norm(a) for a in k["aliases"])}
    if k["master_column"]:
        wanted.add(norm(k["master_column"]))
    decided = {(r["asset_key"], r["column_name"].lower()) for r in cat.conn.execute(
        "SELECT asset_key, column_name FROM key_columns WHERE state IN ('confirmed', 'rejected') OR key_name != ?",
        (k["name"],))}
    n = 0
    with cat.conn:
        for c in cat.conn.execute("SELECT c.asset_key, c.name FROM columns c JOIN assets a ON a.key = c.asset_key "
                                  "AND a.status = 'active'").fetchall():
            if norm(c["name"]) in wanted and (c["asset_key"], c["name"].lower()) not in decided:
                _set_column(cat, k["name"], c["asset_key"], c["name"], "suggested", "same name")
                n += 1
    return n


def suggest_keys(cat: Catalogue) -> int:
    """After a scan: key-like column names found in many tables and files become suggested keys."""
    places: dict[str, set[str]] = defaultdict(set)
    groups: dict[str, set[str]] = defaultdict(set)
    shown: dict[str, str] = {}
    for c in cat.conn.execute("SELECT c.asset_key, c.name, a.source, a.attrs FROM columns c JOIN assets a "
                              "ON a.key = c.asset_key AND a.status = 'active' AND a.kind IN ('table', 'view', "
                              "'data_file', 'file_series')"):
        if not KEY_NAME.search(c["name"]) or len(c["name"]) < 4:
            continue
        n = norm(c["name"])
        places[n].add(c["asset_key"])
        groups[n].add(f"{c['source']}/{json.loads(c['attrs']).get('database', '')}")
        shown.setdefault(n, c["name"])
    n_new = 0
    with cat.conn:
        cat.conn.execute("DELETE FROM keys WHERE state = 'suggested'")
        known = {norm(r["name"]) for r in cat.conn.execute("SELECT name FROM keys")} | \
            {norm(a) for r in cat.conn.execute("SELECT aliases FROM keys") for a in json.loads(r["aliases"] or "[]")}
        for n, keys in places.items():
            if len(keys) >= MIN_PLACES and len(groups[n]) >= 2 and n not in known:
                cat.conn.execute("INSERT INTO keys (name, state, aliases, evidence, created_at, updated_at) "
                                 "VALUES (?, 'suggested', '[]', ?, ?, ?)",
                                 (shown[n], f"in {len(keys)} tables and files across {len(groups[n])} databases "
                                            "or folders", now(), now()))
                n_new += 1
    for k in list_keys(cat):
        suggest_columns(cat, k["name"])
    return n_new


def accept_key(cat: Catalogue, name: str) -> None:
    with cat.conn:
        cat.conn.execute("UPDATE keys SET state = 'active', updated_at = ?, author = ? WHERE name = ?",
                         (now(), cat.author(), name))
    suggest_columns(cat, name)
    cat.rebuild_search()


def reject_key(cat: Catalogue, name: str) -> None:
    with cat.conn:
        cat.conn.execute("UPDATE keys SET state = 'rejected', updated_at = ? WHERE name = ?", (now(), name))
        cat.conn.execute("DELETE FROM key_columns WHERE key_name = ? AND state = 'suggested'", (name,))
    cat.rebuild_search()


def set_column_state(cat: Catalogue, asset_key: str, column: str, state: str) -> None:
    if state not in ("confirmed", "suggested", "rejected"):
        raise ValueError(state)
    with cat.conn:
        cat.conn.execute("UPDATE key_columns SET state = ?, updated_at = ? WHERE asset_key = ? AND column_name = ?",
                         (state, now(), asset_key, column))
    cat.changed()


def unlink_column(cat: Catalogue, asset_key: str, column: str) -> None:
    set_column_state(cat, asset_key, column, "rejected")


def update_key(cat: Catalogue, name: str, *, new_name: str | None = None, description: str | None = None,
               format: str | None = None, aliases: list[str] | None = None,
               master: tuple[str, str] | None = None) -> str:
    k = _key(cat, name)
    if not k:
        raise ValueError(f"No key called {name}")
    target = (new_name or k["name"]).strip()
    with cat.conn:
        if target != k["name"]:
            if _key(cat, target):
                raise ValueError(f"There is already a key called {target}")
            cat.conn.execute("UPDATE keys SET name = ? WHERE name = ?", (target, k["name"]))
            cat.conn.execute("UPDATE key_columns SET key_name = ? WHERE key_name = ?", (target, k["name"]))
        cat.conn.execute(
            "UPDATE keys SET description = ?, format = ?, aliases = ?, master_key = ?, master_column = ?, updated_at = ? "
            "WHERE name = ?",
            (description if description is not None else k["description"],
             format if format is not None else k["format"],
             json.dumps([a.strip() for a in (aliases if aliases is not None else k["aliases"]) if a.strip()]),
             master[0] if master else k["master_key"], master[1] if master else k["master_column"], now(), target))
    suggest_columns(cat, target)
    cat.rebuild_search()
    return target


def delete_key(cat: Catalogue, name: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM keys WHERE name = ?", (name,))
        cat.conn.execute("DELETE FROM key_columns WHERE key_name = ?", (name,))
    cat.rebuild_search()


def key_columns(cat: Catalogue, name: str, state: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT kc.*, a.name AS item, a.kind, a.source, c.data_type FROM key_columns kc
             LEFT JOIN assets a ON a.key = kc.asset_key
             LEFT JOIN columns c ON c.asset_key = kc.asset_key AND c.name = kc.column_name
             WHERE kc.key_name = ?""" + (" AND kc.state = ?" if state else "") + " ORDER BY a.name"
    return [dict(r) for r in cat.conn.execute(sql, (name, state) if state else (name,))]


def columns_with_keys(cat: Catalogue, asset_key: str) -> dict[str, dict[str, str]]:
    """{column: {"key", "state"}} for one item, for badges."""
    return {r["column_name"]: {"key": r["key_name"], "state": r["state"]} for r in cat.conn.execute(
        "SELECT kc.column_name, kc.key_name, kc.state FROM key_columns kc JOIN keys k ON k.name = kc.key_name "
        "AND k.state = 'active' WHERE kc.asset_key = ? AND kc.state != 'rejected'", (asset_key,))}


def join_hints(cat: Catalogue, asset_key: str, limit: int = 20) -> list[dict[str, Any]]:
    """Other items that share a confirmed company-wide key with this one."""
    mine = {c: v["key"] for c, v in columns_with_keys(cat, asset_key).items() if v["state"] == "confirmed"}
    out = []
    for col, name in mine.items():
        for other in key_columns(cat, name, "confirmed"):
            if other["asset_key"] != asset_key:
                out.append({"key": name, "column": col, "other": other["asset_key"], "other_name": other["item"],
                            "other_column": other["column_name"], "other_kind": other["kind"]})
    return out[:limit]


def checks(cat: Catalogue, name: str) -> list[str]:
    """Types and widths that differ between the columns carrying a key, and values outside the master's range."""
    k = _key(cat, name)
    if not k:
        return []
    cols = key_columns(cat, name, "confirmed")
    found = []
    types = {(c["data_type"] or "?").lower() for c in cols if c["data_type"]}
    if len(types) > 1:
        detail = ", ".join(f"{c['item']} {c['data_type']}" for c in cols if c["data_type"])
        found.append(f"Types differ: {detail}")
    if k["master_key"]:
        master = cat.conn.execute("SELECT * FROM profiles WHERE asset_key = ? AND lower(column_name) = lower(?)",
                                  (k["master_key"], k["master_column"])).fetchone()
        if master and master["min_value"] is not None:
            lo, hi = _num(master["min_value"]), _num(master["max_value"])
            for c in cols:
                if c["asset_key"] == k["master_key"]:
                    continue
                p = cat.conn.execute("SELECT * FROM profiles WHERE asset_key = ? AND lower(column_name) = lower(?)",
                                     (c["asset_key"], c["column_name"])).fetchone()
                if not p or p["min_value"] is None:
                    continue
                plo, phi = _num(p["min_value"]), _num(p["max_value"])
                if None not in (lo, hi, plo, phi) and (plo < lo or phi > hi):
                    found.append(f"{c['item']}.{c['column_name']} has values outside the master list "
                                 f"({p['min_value']}–{p['max_value']} vs {master['min_value']}–{master['max_value']})")
                if p["distinct_count"] and master["distinct_count"] and p["distinct_count"] > master["distinct_count"]:
                    found.append(f"{c['item']}.{c['column_name']} has more distinct values ({p['distinct_count']:,}) "
                                 f"than the master list ({master['distinct_count']:,})")
    return found


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
