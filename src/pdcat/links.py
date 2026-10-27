"""Links between scripts and data that you add or hide by hand, and the split of an
item's links into "uses", "created or loaded by" and "read by".

A link you add ("this script creates that CSV") is kept in its own table and
put back after every scan, so rescans never lose it. Hiding a link the scanner
found works the same way.
"""
from __future__ import annotations

from typing import Any

from .store import Catalogue, now

BY_HAND = "by hand"
WRITE_RELATIONS = ("writes",)


def apply_manual_links(cat: Catalogue) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM links WHERE source = ?", (BY_HAND,))
        for m in cat.conn.execute("SELECT * FROM manual_links").fetchall():
            if m["removed"]:
                cat.conn.execute("DELETE FROM links WHERE src_key = ? AND relation = ? AND dst_key = ?",
                                 (m["src_key"], m["relation"], m["dst_key"]))
            else:
                cat.conn.execute(
                    "INSERT OR REPLACE INTO links (src_key, relation, dst_ref, dst_key, source, hint, evidence, how) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?, 'added by hand')",
                    (m["src_key"], m["relation"], m["dst_key"], m["dst_key"], BY_HAND, m["author"] or ""))


def add_manual_link(cat: Catalogue, src_key: str, relation: str, dst_key: str) -> None:
    if relation not in ("reads", "writes"):
        raise ValueError("relation must be reads or writes")
    with cat.conn:
        cat.conn.execute(
            "INSERT INTO manual_links (src_key, relation, dst_key, removed, created_at, author) VALUES (?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(src_key, relation, dst_key) DO UPDATE SET removed = 0",
            (src_key, relation, dst_key, now(), cat.author()))
    apply_manual_links(cat)
    cat.changed()


def hide_link(cat: Catalogue, src_key: str, relation: str, dst_key: str) -> None:
    """Hide a link (one the scanner found wrongly, or one you added)."""
    with cat.conn:
        cat.conn.execute(
            "INSERT INTO manual_links (src_key, relation, dst_key, removed, created_at, author) VALUES (?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(src_key, relation, dst_key) DO UPDATE SET removed = 1",
            (src_key, relation, dst_key, now(), cat.author()))
    apply_manual_links(cat)
    cat.changed()


def links_for(cat: Catalogue, key: str, asset: dict | None) -> tuple[list, list, list]:
    """(uses, created or loaded by, read by) for one item."""
    is_project = asset is not None and asset["kind"] == "project"
    uses = cat.links_from_children(key) if is_project else cat.links_from(key)
    into = cat.links_to(key)
    made_by = [l for l in into if l["relation"] in WRITE_RELATIONS]
    read_by = [l for l in into if l["relation"] not in WRITE_RELATIONS]
    return list(uses), made_by, read_by


def unlinked_data(cat: Catalogue) -> list[dict[str, Any]]:
    """Tables and data files that nothing in the catalogue creates or loads. Your code may build them in a
    way the scanner can't follow; link the script by hand from the item's page."""
    return [dict(r) for r in cat.conn.execute(
        """SELECT a.key, a.kind, a.name, a.source, a.description, a.status,
                  (SELECT COUNT(DISTINCT l.src_key) FROM links l WHERE l.dst_key = a.key AND l.relation != 'writes')
                      AS read_by
           FROM assets a
           WHERE a.status = 'active' AND a.kind IN ('table', 'data_file', 'file_series')
             AND a.attrs NOT LIKE '%"in_series":%'
             AND NOT EXISTS (SELECT 1 FROM links l WHERE l.dst_key = a.key AND l.relation = 'writes')
           ORDER BY read_by DESC, a.name""")]


def code_items(cat: Catalogue) -> list[dict[str, Any]]:
    """Scripts, notebooks and SQL files, for picking the one that creates an item."""
    return [dict(r) for r in cat.conn.execute(
        "SELECT key, name, source, kind FROM assets WHERE kind IN ('script', 'notebook', 'sql_script', 'procedure', "
        "'sql_snippet') AND status = 'active' ORDER BY source, name")]
