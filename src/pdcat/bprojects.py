"""Business projects ("Demand forecasting", "Freight costs") that group the tables,
scripts, files, reports and knowledge they use, with a purpose, status and owner.

Members can be added by hand. Once a project has a few, items connected to them
are suggested: what its scripts read and write, the scripts that read its
tables, and items whose names share the project's words. Accept or reject
suggestions; rejected ones stay hidden.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from .store import Catalogue, now

STATUSES = ["", "idea", "active", "paused", "finished"]
STOP = {"the", "and", "for", "of", "to", "a", "in", "data", "project", "model", "report"}


def save_project(cat: Catalogue, name: str, *, purpose: str = "", status: str = "", owner: str = "",
                 notes: str = "", uid: str | None = None) -> str:
    if not name.strip():
        raise ValueError("A business project needs a name.")
    ts = now()
    old = get_project(cat, uid) if uid else None
    uid = uid or uuid.uuid4().hex
    with cat.conn:
        cat.conn.execute(
            """INSERT INTO bprojects (uid, name, purpose, status, owner, notes, created_at, updated_at, author)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid) DO UPDATE SET name = excluded.name, purpose = excluded.purpose,
                   status = excluded.status, owner = excluded.owner, notes = excluded.notes,
                   updated_at = excluded.updated_at""",
            (uid, name.strip(), purpose.strip(), status or None, owner.strip(), notes.strip(),
             (old or {}).get("created_at") or ts, ts, (old or {}).get("author") or cat.author()))
    cat.rebuild_search()
    return uid


def get_project(cat: Catalogue, uid: str | None) -> dict[str, Any] | None:
    row = cat.conn.execute("SELECT * FROM bprojects WHERE uid = ?", (uid,)).fetchone()
    return dict(row) if row else None


def list_projects(cat: Catalogue) -> list[dict[str, Any]]:
    out = []
    for r in cat.conn.execute("SELECT * FROM bprojects ORDER BY name"):
        p = dict(r)
        p["members"] = cat.conn.execute("SELECT COUNT(*) FROM bproject_members WHERE project_uid = ? AND state = "
                                        "'confirmed'", (p["uid"],)).fetchone()[0]
        out.append(p)
    return out


def delete_project(cat: Catalogue, uid: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM bprojects WHERE uid = ?", (uid,))
        cat.conn.execute("DELETE FROM bproject_members WHERE project_uid = ?", (uid,))
    cat.rebuild_search()


def set_member(cat: Catalogue, uid: str, key: str, state: str = "confirmed") -> None:
    if state not in ("confirmed", "suggested", "rejected"):
        raise ValueError(state)
    with cat.conn:
        cat.conn.execute("INSERT INTO bproject_members (project_uid, asset_key, state) VALUES (?, ?, ?) "
                         "ON CONFLICT(project_uid, asset_key) DO UPDATE SET state = excluded.state", (uid, key, state))
    cat.changed()


def remove_member(cat: Catalogue, uid: str, key: str) -> None:
    set_member(cat, uid, key, "rejected")


def members(cat: Catalogue, uid: str, state: str = "confirmed") -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        """SELECT m.asset_key AS key, COALESCE(a.name, m.asset_key) AS name, COALESCE(a.kind, 'note') AS kind,
                  a.source, a.description FROM bproject_members m LEFT JOIN assets a ON a.key = m.asset_key
           WHERE m.project_uid = ? AND m.state = ? ORDER BY a.kind, a.name""", (uid, state))]


def projects_for(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        "SELECT p.uid, p.name FROM bproject_members m JOIN bprojects p ON p.uid = m.project_uid "
        "WHERE m.asset_key = ? AND m.state = 'confirmed' ORDER BY p.name", (key,))]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text or "").lower())
            if w not in STOP}


def suggest(cat: Catalogue, uid: str, limit: int = 40) -> list[dict[str, Any]]:
    """Items connected to the project's members, or sharing its words. Stored as suggestions."""
    p = get_project(cat, uid)
    if not p:
        return []
    have = {m["key"] for m in members(cat, uid)}
    decided = {r[0] for r in cat.conn.execute("SELECT asset_key FROM bproject_members WHERE project_uid = ?", (uid,))}
    scores: dict[str, tuple[float, str]] = {}

    def add(key: str, score: float, why: str) -> None:
        if key in decided or key is None:
            return
        old = scores.get(key)
        if not old or old[0] < score:
            scores[key] = (score, why)

    for k in have:
        for l in cat.conn.execute("SELECT dst_key, relation FROM links WHERE src_key = ? AND dst_key IS NOT NULL", (k,)):
            add(l["dst_key"], 3, f"{l['relation']} by a member")
        for l in cat.conn.execute("SELECT src_key, relation FROM links WHERE dst_key = ?", (k,)):
            add(l["src_key"], 2.5, f"{l['relation']} a member")
        for c in cat.conn.execute("SELECT key FROM assets WHERE parent = ? AND status = 'active'", (k,)):
            add(c["key"], 2, "inside a member")
    words = _words(f"{p['name']} {p['purpose'] or ''}")
    if words:
        for a in cat.conn.execute("SELECT key, name, description FROM assets WHERE status = 'active' AND kind NOT IN "
                                  "('series_file', 'database', 'folder')"):
            hits = words & _words(f"{a['name']} {a['description'] or ''}")
            if hits:
                add(a["key"], 1 + 0.5 * len(hits), "name mentions " + ", ".join(sorted(hits)))
    ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])[:limit]
    with cat.conn:
        cat.conn.execute("DELETE FROM bproject_members WHERE project_uid = ? AND state = 'suggested'", (uid,))
        for key, _ in ranked:
            cat.conn.execute("INSERT OR IGNORE INTO bproject_members (project_uid, asset_key, state) VALUES (?, ?, "
                             "'suggested')", (uid, key))
    names = {r["key"]: dict(r) for r in cat.conn.execute("SELECT key, name, kind, source FROM assets")}
    return [{"key": k, "why": why, **names.get(k, {"name": k, "kind": "note", "source": ""})}
            for k, (_, why) in ranked]
