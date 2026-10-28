"""Follow-ups pinned to the home page, and the items you looked at recently."""
from __future__ import annotations

from typing import Any

from .store import Catalogue, now

RECENT_KEEP = 30


def follow(cat: Catalogue, item: str, note: str = "") -> None:
    with cat.conn:
        cat.conn.execute("INSERT INTO follow_ups (item, note, created_at) VALUES (?, ?, ?) ON CONFLICT(item) DO UPDATE "
                         "SET note = excluded.note", (item, note.strip() or None, now()))
    cat.changed()


def unfollow(cat: Catalogue, item: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM follow_ups WHERE item = ?", (item,))
    cat.changed()


def following(cat: Catalogue, item: str) -> dict[str, Any] | None:
    r = cat.conn.execute("SELECT * FROM follow_ups WHERE item = ?", (item,)).fetchone()
    return dict(r) if r else None


def follow_ups(cat: Catalogue) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        f"SELECT f.item, f.note, f.created_at, {_NAME} AS name, a.kind FROM follow_ups f "
        "LEFT JOIN assets a ON a.key = f.item ORDER BY f.created_at DESC")]


def viewed(cat: Catalogue, item: str) -> None:
    """Remember an item page was opened. Quiet: looking at things doesn't change the catalogue."""
    with cat.conn:
        cat.conn.execute("INSERT INTO recent_views (item, at) VALUES (?, ?) ON CONFLICT(item) DO UPDATE SET at = excluded.at",
                         (item, now()))
        cat.conn.execute("DELETE FROM recent_views WHERE item NOT IN "
                         "(SELECT item FROM recent_views ORDER BY at DESC LIMIT ?)", (RECENT_KEEP,))


def recent(cat: Catalogue, limit: int = 8) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        f"SELECT r.item, r.at, {_NAME.replace('f.item', 'r.item')} AS name, a.kind FROM recent_views r "
        "LEFT JOIN assets a ON a.key = r.item ORDER BY r.at DESC LIMIT ?", (limit,))]


# A display name for any key: scanned items, knowledge, business projects, groups...
_NAME = """COALESCE(a.name,
    (SELECT title FROM knowledge WHERE 'kb://' || uid = f.item),
    (SELECT question FROM questions WHERE 'q://' || uid = f.item),
    (SELECT name FROM metrics WHERE 'metric://' || uid = f.item),
    (SELECT name FROM features WHERE 'feature://' || uid = f.item),
    (SELECT name FROM bprojects WHERE 'bp://' || uid = f.item),
    (SELECT name FROM item_groups WHERE 'group://' || uid = f.item),
    (SELECT name FROM sql_queries WHERE 'query://' || uid = f.item),
    (SELECT title FROM documents WHERE 'doc://' || uid = f.item),
    f.item)"""


def kind_of(key: str, kind: str | None) -> str:
    if kind:
        return kind
    for prefix, k in (("kb://", "knowledge"), ("q://", "question"), ("metric://", "metric"), ("feature://", "feature"),
                      ("bp://", "bproject"), ("key://", "key"), ("pkg://", "package"), ("group://", "group"),
                      ("query://", "query"), ("env://", "environment"), ("doc://", "document")):
        if key.startswith(prefix):
            return k
    return "note"
