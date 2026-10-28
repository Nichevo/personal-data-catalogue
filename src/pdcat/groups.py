"""Items that belong together while each stays its own item:

  * family:   the same data at different grains (SOH_D, SOH_W, SOH_M)
  * versions: copies of one script (forecast_v2.py, forecast_v3.py); only the main one shows on
              the code map, in search and in lineage
  * backup:   a main table and its copy elsewhere (SQL Server and an Azure backup with more history)
  * outputs:  the files one script writes from one pattern (f"out/sales_{run}.csv"), made automatically

A group has a page of its own ("group://<uid>") that compares its members side by side.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from . import suggestions
from .store import Catalogue, now

TYPES = {"family": "File family", "versions": "Script versions", "backup": "Main and backup",
         "outputs": "Files one script writes"}


def create(cat: Catalogue, type_: str, name: str, members: dict[str, str | None], *, main_key: str | None = None,
           attrs: dict[str, Any] | None = None, uid: str | None = None) -> str:
    if type_ not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    uid = uid or uuid.uuid4().hex[:12]
    with cat.conn:
        cat.conn.execute(
            "INSERT INTO item_groups (uid, type, name, main_key, attrs, created_at, author) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET name = excluded.name, main_key = excluded.main_key, attrs = excluded.attrs",
            (uid, type_, name, main_key, json.dumps(attrs or {}, default=str), now(), cat.author()))
        # An item is in at most one group of each type.
        for key in members:
            cat.conn.execute("DELETE FROM group_members WHERE asset_key = ? AND group_uid IN "
                             "(SELECT uid FROM item_groups WHERE type = ? AND uid != ?)", (key, type_, uid))
        cat.conn.execute("DELETE FROM group_members WHERE group_uid = ?", (uid,))
        cat.conn.executemany("INSERT INTO group_members (group_uid, asset_key, label) VALUES (?, ?, ?)",
                             [(uid, k, v) for k, v in members.items()])
        _drop_empty(cat)
    cat.changed()
    return uid


def _drop_empty(cat: Catalogue) -> None:
    cat.conn.execute("DELETE FROM item_groups WHERE uid NOT IN (SELECT group_uid FROM group_members "
                     "GROUP BY group_uid HAVING COUNT(*) >= 2)")
    cat.conn.execute("DELETE FROM group_members WHERE group_uid NOT IN (SELECT uid FROM item_groups)")


def get(cat: Catalogue, uid: str) -> dict[str, Any] | None:
    r = cat.conn.execute("SELECT * FROM item_groups WHERE uid = ?", (uid,)).fetchone()
    if r is None:
        return None
    g = dict(r)
    g["attrs"] = json.loads(g["attrs"] or "{}")
    g["members"] = [dict(m) for m in cat.conn.execute(
        "SELECT m.asset_key, m.label, a.name, a.kind, a.source, a.status, a.attrs FROM group_members m "
        "LEFT JOIN assets a ON a.key = m.asset_key WHERE m.group_uid = ? ORDER BY m.label, a.name", (uid,))]
    for m in g["members"]:
        m["attrs"] = json.loads(m["attrs"] or "{}")
    return g


def all_groups(cat: Catalogue, type_: str | None = None) -> list[dict[str, Any]]:
    where, params = ("WHERE g.type = ?", (type_,)) if type_ else ("", ())
    return [dict(r) for r in cat.conn.execute(
        f"SELECT g.uid, g.type, g.name, g.main_key, COUNT(m.asset_key) AS members FROM item_groups g "
        f"JOIN group_members m ON m.group_uid = g.uid {where} GROUP BY g.uid ORDER BY g.type, g.name", params)]


def for_item(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    return [get(cat, r[0]) for r in cat.conn.execute(
        "SELECT group_uid FROM group_members WHERE asset_key = ?", (key,))]


def membership(cat: Catalogue) -> dict[str, list[tuple[str, str, str]]]:
    """asset key -> [(group uid, type, group name)], for lists that show groups as one row."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    for r in cat.conn.execute("SELECT m.asset_key, g.uid, g.type, g.name FROM group_members m "
                              "JOIN item_groups g ON g.uid = m.group_uid"):
        out.setdefault(r[0], []).append((r[1], r[2], r[3]))
    return out


def set_main(cat: Catalogue, uid: str, key: str) -> None:
    with cat.conn:
        cat.conn.execute("UPDATE item_groups SET main_key = ? WHERE uid = ?", (key, uid))
        cat.conn.execute("UPDATE item_groups SET attrs = json_set(attrs, '$.main_by', 'you') WHERE uid = ?", (uid,))
    cat.changed()


def set_label(cat: Catalogue, uid: str, key: str, label: str) -> None:
    with cat.conn:
        cat.conn.execute("UPDATE group_members SET label = ? WHERE group_uid = ? AND asset_key = ?",
                         (label.strip() or None, uid, key))
    cat.changed()


def remove_member(cat: Catalogue, uid: str, key: str) -> None:
    """Take an item out of a group ("these aren't really versions"). A group left with one item goes,
    and the suggestion that made it won't come back."""
    g = get(cat, uid)
    with cat.conn:
        cat.conn.execute("DELETE FROM group_members WHERE group_uid = ? AND asset_key = ?", (uid, key))
        if g and g["main_key"] == key:
            cat.conn.execute("UPDATE item_groups SET main_key = NULL WHERE uid = ?", (uid,))
        _drop_empty(cat)
    if g and g["attrs"].get("suggestion"):
        excluded = cat.setting("group_excluded", [])
        cat.set_setting("group_excluded", [*excluded, [g["type"], key]], quiet=True)
    cat.changed()


def delete(cat: Catalogue, uid: str) -> None:
    g = get(cat, uid)
    with cat.conn:
        cat.conn.execute("DELETE FROM group_members WHERE group_uid = ?", (uid,))
        cat.conn.execute("DELETE FROM item_groups WHERE uid = ?", (uid,))
    if g and g["attrs"].get("suggestion"):
        suggestions._set_state(cat, g["attrs"]["suggestion"], "rejected")
    cat.changed()


def excluded(cat: Catalogue, type_: str) -> set[str]:
    """Items you took out of a suggested group of this type, so finders leave them alone."""
    return {k for t, k in cat.setting("group_excluded", []) if t == type_}


def older_versions(cat: Catalogue) -> set[str]:
    """Scripts that are older versions of another: hidden from the code map, search and lineage."""
    out = set()
    for r in cat.conn.execute("SELECT m.asset_key, g.main_key FROM group_members m JOIN item_groups g "
                              "ON g.uid = m.group_uid WHERE g.type = 'versions' AND g.main_key IS NOT NULL"):
        if r[0] != r[1]:
            out.add(r[0])
    return out


def main_of(cat: Catalogue, key: str, type_: str) -> str | None:
    r = cat.conn.execute("SELECT g.main_key FROM group_members m JOIN item_groups g ON g.uid = m.group_uid "
                         "WHERE m.asset_key = ? AND g.type = ?", (key, type_)).fetchone()
    return r[0] if r else None


def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    create(cat, p["type"], p["name"], p["members"], main_key=p.get("main_key"),
           attrs={**p.get("attrs", {}), "suggestion": p["_id"]}, uid=p.get("uid"))


for _kind in ("versions", "family", "backup"):
    suggestions.handler(_kind)(_accept)
