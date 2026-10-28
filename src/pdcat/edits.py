"""Edit history: who changed what and when, on notes, descriptions, item details and links, with undo.

Each change records the old and new value and how to put the old one back. Undo applies that and
marks the change undone (the undo is itself recorded, so it can be undone too).
"""
from __future__ import annotations

import json
from typing import Any

from .store import Catalogue, now


def record(cat: Catalogue, item: str, what: str, old: Any, new: Any, target: dict[str, Any]) -> None:
    if old == new or (not old and not new):
        return
    cat.conn.execute(
        "INSERT INTO edits (at, author, item, what, old_value, new_value, target) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now(), cat.author(), item, what, _text(old), _text(new), json.dumps(target, default=str)))


def _text(v: Any) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v, default=str)


def history(cat: Catalogue, item: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    if item is None:
        rows = cat.conn.execute("SELECT * FROM edits ORDER BY id DESC LIMIT ?", (limit,))
    else:
        base = item.split("#", 1)[0]
        rows = cat.conn.execute("SELECT * FROM edits WHERE item = ? OR substr(item, 1, ?) = ? ORDER BY id DESC LIMIT ?",
                                (base, len(base) + 1, base + "#", limit))
    return [dict(r) for r in rows]


def undo(cat: Catalogue, edit_id: int) -> str:
    """Put the old value back. Returns a sentence saying what was done."""
    e = cat.conn.execute("SELECT * FROM edits WHERE id = ?", (edit_id,)).fetchone()
    if e is None:
        raise KeyError(edit_id)
    if e["undone"]:
        return "Already undone."
    t = json.loads(e["target"] or "{}")
    kind = t.get("type")
    from . import curation
    if kind == "item_info":
        curation.set_item_info(cat, t["key"], **{t["field"]: t.get("old") if t.get("old") is not None else ""})
        done = f"{t['field']} put back"
    elif kind == "column_info":
        curation.set_column_info(cat, t["key"], t["column"], description=t.get("old") or "")
        done = f"description of {t['column']} put back"
    elif kind == "note_update":
        cat.update_note(t["id"], t["body"], t.get("tags", ""), t.get("note_type"))
        done = "note put back as it was"
    elif kind == "note_add":
        cat.delete_note(t["id"])
        done = "note removed"
    elif kind == "note_delete":
        row = t["row"]
        with cat.conn:
            cat.conn.execute(
                "INSERT INTO notes (asset_key, body, tags, created_at, updated_at, note_type, status, uid, author, "
                "confirmed_at, archived) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["asset_key"], row["body"], row["tags"], row["created_at"], now(), row["note_type"],
                 row["status"], row["uid"], row["author"], row.get("confirmed_at"), row.get("archived", 0)))
        cat.reindex_search(row["asset_key"])
        done = "note restored"
    elif kind == "manual_link":
        from .links import add_manual_link, forget_manual_link, hide_link
        if t["was"] == "none":
            forget_manual_link(cat, t["src"], t["relation"], t["dst"])
        elif t["was"] == "removed":
            hide_link(cat, t["src"], t["relation"], t["dst"], record_edit=False)
        else:
            add_manual_link(cat, t["src"], t["relation"], t["dst"], record_edit=False)
        done = "link put back as it was"
    elif kind == "link_mark":
        from .links import set_link_mark
        set_link_mark(cat, t["src"], t["relation"], t["dst"], t.get("old"), record_edit=False)
        done = "link mark put back"
    elif kind == "column_extra":
        curation.set_column_extra(cat, t["key"], t["column"], t["field"], t.get("old"))
        done = f"{t['field'].replace('_', ' ')} of {t['column']} put back"
    elif kind == "query_name":
        from .sqllibrary import rename
        rename(cat, t["uid"], t.get("old") or "", record_edit=False, named_by=t.get("old_by", "auto"))
        done = "query name put back"
    else:
        return "This change can't be undone automatically."
    with cat.conn:
        cat.conn.execute("UPDATE edits SET undone = 1 WHERE id = ?", (edit_id,))
    cat.changed()
    return f"Undone: {done}."
