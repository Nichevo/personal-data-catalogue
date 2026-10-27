"""Knowledge that stands on its own: business rules, how-tos, history, the questions
analysts ask and the metrics they mean.

A knowledge note needn't be attached to anything ("Freight is always sea freight").
Give it trigger words and it shows up in a "Related knowledge" box wherever they
appear: in a search, on a table or column whose name or notes mention them, or
on a script that uses such a table. Pin it to items to show it there regardless,
or exclude items where it doesn't apply.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .store import Catalogue, now, split_tags

SECTIONS = ["Business rules", "Definitions", "How-to", "History", "Data quirks", "Other"]


def _uid() -> str:
    return uuid.uuid4().hex


def _words(text: str) -> list[str]:
    return [w.strip().lower() for w in re.split(r"[,;\n]", text or "") if w.strip()]


# ---- knowledge notes -----------------------------------------------------------

def save_knowledge(cat: Catalogue, title: str, body: str = "", *, section: str = "", tags: str = "",
                   triggers: str = "", pinned: list[str] | None = None, excluded: list[str] | None = None,
                   uid: str | None = None) -> str:
    if not title.strip():
        raise ValueError("A knowledge note needs a title.")
    ts = now()
    old = get_knowledge(cat, uid) if uid else None
    uid = uid or _uid()
    with cat.conn:
        cat.conn.execute(
            """INSERT INTO knowledge (uid, title, body, section, tags, triggers, pinned, excluded, author,
                   created_at, updated_at, confirmed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid) DO UPDATE SET title = excluded.title, body = excluded.body,
                   section = excluded.section, tags = excluded.tags, triggers = excluded.triggers,
                   pinned = excluded.pinned, excluded = excluded.excluded, updated_at = excluded.updated_at,
                   confirmed_at = excluded.confirmed_at""",
            (uid, title.strip(), body.rstrip(), section or None, ", ".join(split_tags(tags)),
             ", ".join(_words(triggers)),
             json.dumps(pinned if pinned is not None else (old or {}).get("pinned", [])),
             json.dumps(excluded if excluded is not None else (old or {}).get("excluded", [])),
             (old or {}).get("author") or cat.author(), (old or {}).get("created_at") or ts, ts, ts))
    cat.rebuild_search()
    return uid


def get_knowledge(cat: Catalogue, uid: str | None) -> dict[str, Any] | None:
    row = cat.conn.execute("SELECT * FROM knowledge WHERE uid = ?", (uid,)).fetchone()
    return _k(row) if row else None


def _k(row) -> dict[str, Any]:
    k = dict(row)
    k["pinned"] = json.loads(k["pinned"] or "[]")
    k["excluded"] = json.loads(k["excluded"] or "[]")
    return k


def list_knowledge(cat: Catalogue, section: str | None = None) -> list[dict[str, Any]]:
    sql, params = "SELECT * FROM knowledge", ()
    if section:
        sql, params = sql + " WHERE section = ?", (section,)
    return [_k(r) for r in cat.conn.execute(sql + " ORDER BY COALESCE(section, 'zz'), title", params)]


def delete_knowledge(cat: Catalogue, uid: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM knowledge WHERE uid = ?", (uid,))
    cat.rebuild_search()


def pin(cat: Catalogue, uid: str, key: str, *, exclude: bool = False) -> None:
    k = get_knowledge(cat, uid)
    if not k:
        return
    field = "excluded" if exclude else "pinned"
    other = "pinned" if exclude else "excluded"
    keys = [x for x in k[field] if x != key] + [key]
    with cat.conn:
        cat.conn.execute(f"UPDATE knowledge SET {field} = ?, {other} = ? WHERE uid = ?",
                         (json.dumps(keys), json.dumps([x for x in k[other] if x != key]), uid))
    cat.changed()


def triggered(cat: Catalogue, text: str, key: str | None = None) -> list[dict[str, Any]]:
    """Knowledge notes that apply to some text (a search, or an item's name, columns and notes),
    plus those pinned to the item. Excluded items never show the note."""
    low = (text or "").lower()
    out = []
    for k in list_knowledge(cat):
        if key and key in k["excluded"]:
            continue
        if key and key in k["pinned"]:
            out.append(k)
            continue
        for w in _words(k["triggers"]):
            if re.search(rf"(?<![a-z0-9]){re.escape(w)}", low):
                out.append(k)
                break
    return out


def item_text(cat: Catalogue, key: str) -> str:
    """The words that knowledge triggers are matched against for one item."""
    a = cat.get_asset(key)
    parts = [key]
    if a:
        parts += [a["name"], a["description"] or ""]
    parts += [c["name"] for c in cat.columns(key)]
    parts += [n["body"] for n in cat.notes(key)]
    # A script inherits the words of the tables and files it uses.
    for l in cat.links_from(key):
        if l["dst_key"] and l["relation"] in ("reads", "writes"):
            parts.append(l["dst_name"] or l["dst_key"])
    return " ".join(parts)


def mark_confirmed(cat: Catalogue, uid: str) -> None:
    with cat.conn:
        cat.conn.execute("UPDATE knowledge SET confirmed_at = ? WHERE uid = ?", (now(), uid))
    cat.changed()


# ---- questions -----------------------------------------------------------------

def save_question(cat: Catalogue, question: str, *, answer: str = "", sql: str = "", tables: list[str] | None = None,
                  caveats: str = "", tags: str = "", uid: str | None = None) -> str:
    if not question.strip():
        raise ValueError("Write the question first.")
    ts = now()
    if sql.strip() and tables is None:
        from .sqlrefs import extract_refs
        tables = _resolve_tables(cat, [r.name for r in extract_refs(sql)])
    old = cat.conn.execute("SELECT author, created_at FROM questions WHERE uid = ?", (uid,)).fetchone() if uid else None
    uid = uid or _uid()
    with cat.conn:
        cat.conn.execute(
            """INSERT INTO questions (uid, question, answer, sql, tables, caveats, tags, author, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid) DO UPDATE SET question = excluded.question, answer = excluded.answer,
                   sql = excluded.sql, tables = excluded.tables, caveats = excluded.caveats, tags = excluded.tags,
                   updated_at = excluded.updated_at""",
            (uid, question.strip(), answer.strip(), sql.strip(), json.dumps(tables or []), caveats.strip(),
             ", ".join(split_tags(tags)), old["author"] if old else cat.author(), old["created_at"] if old else ts, ts))
    cat.rebuild_search()
    return uid


def _resolve_tables(cat: Catalogue, names: list[str]) -> list[str]:
    keys = []
    for name in names:
        obj = name.split(".")[-1].lower()
        for r in cat.conn.execute("SELECT key FROM assets WHERE kind IN ('table', 'view') AND key LIKE ?",
                                  (f"%.{obj}",)):
            if r["key"] not in keys:
                keys.append(r["key"])
    return keys


def list_questions(cat: Catalogue, table_key: str | None = None) -> list[dict[str, Any]]:
    out = []
    for r in cat.conn.execute("SELECT * FROM questions ORDER BY question"):
        q = dict(r)
        q["tables"] = json.loads(q["tables"] or "[]")
        if table_key and table_key not in q["tables"]:
            continue
        out.append(q)
    return out


def get_question(cat: Catalogue, uid: str) -> dict[str, Any] | None:
    rows = [q for q in list_questions(cat) if q["uid"] == uid]
    return rows[0] if rows else None


def delete_question(cat: Catalogue, uid: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM questions WHERE uid = ?", (uid,))
    cat.rebuild_search()


# ---- metrics -------------------------------------------------------------------

def save_metric(cat: Catalogue, name: str, *, definition: str = "", formula: str = "", sources: list[str] | None = None,
                owner: str = "", caveats: str = "", tags: str = "", from_measure: str = "",
                uid: str | None = None) -> str:
    if not name.strip():
        raise ValueError("A metric needs a name.")
    ts = now()
    old = cat.conn.execute("SELECT author, created_at FROM metrics WHERE uid = ?", (uid,)).fetchone() if uid else None
    uid = uid or _uid()
    with cat.conn:
        cat.conn.execute(
            """INSERT INTO metrics (uid, name, definition, formula, sources, owner, caveats, tags, from_measure,
                   author, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid) DO UPDATE SET name = excluded.name, definition = excluded.definition,
                   formula = excluded.formula, sources = excluded.sources, owner = excluded.owner,
                   caveats = excluded.caveats, tags = excluded.tags, from_measure = excluded.from_measure,
                   updated_at = excluded.updated_at""",
            (uid, name.strip(), definition.strip(), formula.strip(), json.dumps(sources or []), owner.strip(),
             caveats.strip(), ", ".join(split_tags(tags)), from_measure or None,
             old["author"] if old else cat.author(), old["created_at"] if old else ts, ts))
    cat.rebuild_search()
    return uid


def list_metrics(cat: Catalogue) -> list[dict[str, Any]]:
    out = []
    for r in cat.conn.execute("SELECT * FROM metrics ORDER BY name"):
        m = dict(r)
        m["sources"] = json.loads(m["sources"] or "[]")
        out.append(m)
    return out


def get_metric(cat: Catalogue, uid: str) -> dict[str, Any] | None:
    rows = [m for m in list_metrics(cat) if m["uid"] == uid]
    return rows[0] if rows else None


def delete_metric(cat: Catalogue, uid: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM metrics WHERE uid = ?", (uid,))
    cat.rebuild_search()
