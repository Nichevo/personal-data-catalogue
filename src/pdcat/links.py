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
                cat.conn.execute("DELETE FROM links WHERE src_key = ? AND relation = ? AND (dst_key = ? OR dst_ref = ?)",
                                 (m["src_key"], m["relation"], m["dst_key"], m["dst_key"]))
            else:
                cat.conn.execute(
                    "INSERT OR REPLACE INTO links (src_key, relation, dst_ref, dst_key, source, hint, evidence, how) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?, 'added by hand')",
                    (m["src_key"], m["relation"], m["dst_key"], m["dst_key"], BY_HAND, m["author"] or ""))


def _manual_state(cat: Catalogue, src_key: str, relation: str, dst_key: str) -> str:
    r = cat.conn.execute("SELECT removed FROM manual_links WHERE src_key = ? AND relation = ? AND dst_key = ?",
                         (src_key, relation, dst_key)).fetchone()
    return "none" if r is None else "removed" if r[0] else "added"


def add_manual_link(cat: Catalogue, src_key: str, relation: str, dst_key: str, *, record_edit: bool = True) -> None:
    if relation not in ("reads", "writes", "runs"):
        raise ValueError("relation must be reads, writes or runs")
    was = _manual_state(cat, src_key, relation, dst_key)
    with cat.conn:
        cat.conn.execute(
            "INSERT INTO manual_links (src_key, relation, dst_key, removed, created_at, author) VALUES (?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(src_key, relation, dst_key) DO UPDATE SET removed = 0",
            (src_key, relation, dst_key, now(), cat.author()))
        if record_edit:
            from .edits import record
            record(cat, dst_key, "link added", None, f"{src_key} {relation} {dst_key}",
                   {"type": "manual_link", "src": src_key, "relation": relation, "dst": dst_key, "was": was})
    apply_manual_links(cat)
    cat.changed()


def hide_link(cat: Catalogue, src_key: str, relation: str, dst_key: str, *, record_edit: bool = True) -> None:
    """Remove a link (one the scanner found wrongly, or one you added). Kept through rescans."""
    was = _manual_state(cat, src_key, relation, dst_key)
    with cat.conn:
        cat.conn.execute(
            "INSERT INTO manual_links (src_key, relation, dst_key, removed, created_at, author) VALUES (?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(src_key, relation, dst_key) DO UPDATE SET removed = 1, created_at = excluded.created_at, "
            "author = excluded.author",
            (src_key, relation, dst_key, now(), cat.author()))
        if record_edit:
            from .edits import record
            record(cat, dst_key, "link removed", f"{src_key} {relation} {dst_key}", None,
                   {"type": "manual_link", "src": src_key, "relation": relation, "dst": dst_key, "was": was})
    apply_manual_links(cat)
    cat.changed()


def forget_manual_link(cat: Catalogue, src_key: str, relation: str, dst_key: str) -> None:
    """Drop a hand-made link or removal, so the link is back to what the scanner finds.
    A removed link only comes back with the next scan of its source."""
    with cat.conn:
        cat.conn.execute("DELETE FROM manual_links WHERE src_key = ? AND relation = ? AND dst_key = ?",
                         (src_key, relation, dst_key))
    apply_manual_links(cat)
    cat.changed()


# ---- how much a link matters ------------------------------------------------------

def set_link_mark(cat: Catalogue, src_key: str, relation: str, dst_key: str, mark: str | None, *,
                  record_edit: bool = True) -> None:
    """mark="unimportant": the link is right but noise (a script that only reads a lookup table). It stays
    listed, greyed out, and is left out of grouping, diagrams, lineage and "used by" counts. None clears it."""
    old = cat.conn.execute("SELECT mark FROM link_marks WHERE src_key = ? AND relation = ? AND dst_key = ?",
                           (src_key, relation, dst_key)).fetchone()
    old = old[0] if old else None
    with cat.conn:
        if mark:
            cat.conn.execute(
                "INSERT INTO link_marks (src_key, relation, dst_key, mark, created_at, author) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(src_key, relation, dst_key) DO UPDATE SET mark = excluded.mark, "
                "created_at = excluded.created_at, author = excluded.author",
                (src_key, relation, dst_key, mark, now(), cat.author()))
        else:
            cat.conn.execute("DELETE FROM link_marks WHERE src_key = ? AND relation = ? AND dst_key = ?",
                             (src_key, relation, dst_key))
        if record_edit:
            from .edits import record
            record(cat, dst_key, "link mark", old, mark,
                   {"type": "link_mark", "src": src_key, "relation": relation, "dst": dst_key, "old": old})
    cat.changed()


def unimportant(cat: Catalogue) -> set[tuple[str, str, str]]:
    return {(r[0], r[1], r[2]) for r in cat.conn.execute(
        "SELECT src_key, relation, dst_key FROM link_marks WHERE mark = 'unimportant'")}


def changed_links(cat: Catalogue) -> list[dict[str, Any]]:
    """Every link you removed or marked not important, newest first, for the list that undoes them."""
    rows = [{**dict(r), "change": "removed"} for r in cat.conn.execute(
        "SELECT m.src_key, m.relation, m.dst_key, m.created_at, m.author, s.name AS src_name, d.name AS dst_name "
        "FROM manual_links m LEFT JOIN assets s ON s.key = m.src_key LEFT JOIN assets d ON d.key = m.dst_key "
        "WHERE m.removed = 1")]
    rows += [{**dict(r), "change": "not important"} for r in cat.conn.execute(
        "SELECT m.src_key, m.relation, m.dst_key, m.created_at, m.author, s.name AS src_name, d.name AS dst_name "
        "FROM link_marks m LEFT JOIN assets s ON s.key = m.src_key LEFT JOIN assets d ON d.key = m.dst_key")]
    return sorted(rows, key=lambda r: r["created_at"] or "", reverse=True)


def links_for(cat: Catalogue, key: str, asset: dict | None) -> tuple[list, list, list]:
    """(uses, created or loaded by, read by) for one item."""
    is_project = asset is not None and asset["kind"] == "project"
    uses = cat.links_from_children(key) if is_project else cat.links_from(key)
    into = cat.links_to(key)
    noise = unimportant(cat)

    def mark(rows) -> list[dict[str, Any]]:
        out = []
        for l in rows:
            d = dict(l)
            d["unimportant"] = (d["src_key"], d["relation"], d.get("dst_key") or d["dst_ref"]) in noise
            out.append(d)
        return out
    made_by = [l for l in mark(into) if l["relation"] in WRITE_RELATIONS]
    read_by = [l for l in mark(into) if l["relation"] not in WRITE_RELATIONS]
    return mark(uses), made_by, read_by


def unlinked_data(cat: Catalogue) -> list[dict[str, Any]]:
    """Tables and data files that nothing in the catalogue creates or loads. Your code may build them in a
    way the scanner can't follow; link the script by hand from the item's page. Files in raw data folders
    come from outside, so they aren't listed, nor are items marked not relevant."""
    from .curation import hidden_keys
    from .folderroles import item_roles
    skip = hidden_keys(cat) | {k for k, r in item_roles(cat).items() if r in ("raw data", "archive")}
    return [r for r in _unlinked(cat) if r["key"] not in skip]


def _unlinked(cat: Catalogue) -> list[dict[str, Any]]:
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
