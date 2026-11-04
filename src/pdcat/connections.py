"""An item's connections as one list of rows, for the filterable table on its Connections tab.

A busy table can have hundreds of scripts and reports reading it, which made the old lists too long to use.
Each row is one link, seen from the item: what it reads or writes ("Reads", "Writes"), and what reads or
loads it ("Read by", "Loaded by"). The other end's type, where it lives and the scanner's evidence come
with it, so the table can be filtered and saved as CSV.
"""
from __future__ import annotations

import json
from typing import Any

from .store import Catalogue

# Seen from the item: (link from this item, link to this item).
WORDS = {
    "reads": ("Reads", "Read by"),
    "writes": ("Writes", "Loaded by"),
    "calls": ("Calls", "Called by"),
    "imports": ("Imports", "Imported by"),
    "runs": ("Runs", "Run by"),
}
# The order the connection kinds are offered in the filter.
ORDER = ["Loaded by", "Read by", "Called by", "Imported by", "Run by", "Reads", "Writes", "Calls", "Imports", "Runs"]


def connection_word(relation: str, outgoing: bool) -> str:
    out, into = WORDS.get(relation, (relation.capitalize(), f"{relation} (by)"))
    return out if outgoing else into


def _how(l: dict) -> str:
    how, ev, note = l.get("how"), l.get("evidence") or "", l.get("match_note")
    return " · ".join(b for b in (how, ev, f"⚠️ {note}" if note else None) if b)


def _others(cat: Catalogue, keys: set[str]) -> dict[str, dict[str, Any]]:
    """Name, kind, scan source, the folder/project/database it sits in, and its path, for each key."""
    out: dict[str, dict[str, Any]] = {}
    keys = {k for k in keys if k}
    rows = list(keys)
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in cat.conn.execute(
                f"""SELECT a.key, a.kind, a.name, a.source, a.attrs, p.name AS parent_name
                    FROM assets a LEFT JOIN assets p ON p.key = a.parent WHERE a.key IN ({marks})""", chunk):
            try:
                attrs = json.loads(r["attrs"] or "{}")
            except ValueError:
                attrs = {}
            out[r["key"]] = {"kind": r["kind"], "name": r["name"], "source": r["source"] or "",
                             "in": r["parent_name"] or "", "path": attrs.get("path") or ""}
    return out


def connection_rows(cat: Catalogue, key: str, asset: dict | None, links: tuple[list, list, list]) -> list[dict]:
    """One row per link of the item: the uses (from the item, or from the scripts inside a project),
    then what loads it and what reads it. Each row keeps what the link menu needs (src, relation, dst)."""
    uses, made_by, read_by = links
    is_project = asset is not None and asset.get("kind") == "project"
    seen: set[tuple] = set()
    picked: list[tuple[dict, bool]] = []
    for l in uses:
        ident = (l["relation"], l.get("dst_key") or l["dst_ref"], l["src_key"] if is_project else "")
        if ident not in seen:
            seen.add(ident)
            picked.append((l, True))
    picked += [(l, False) for l in made_by] + [(l, False) for l in read_by]
    others = _others(cat, {(l.get("dst_key") if out else l["src_key"]) or "" for l, out in picked}
                     | ({l["src_key"] for l, _ in picked} if is_project else set()))
    rows = []
    for l, outgoing in picked:
        other = (l.get("dst_key") if outgoing else l["src_key"]) or ""
        info = others.get(other)
        if info:
            name, kind = info["name"], info["kind"]
        elif outgoing and not l.get("dst_key"):
            ref_kind, _, name = (l.get("dst_ref") or "").partition(":")
            kind = "file" if ref_kind == "file" else "unmatched"
            other = l.get("dst_ref") or ""
        else:
            name = l.get("src_name") or other
            kind = l.get("src_kind") or ""
        via = ""
        if is_project and outgoing:
            via = (others.get(l["src_key"]) or {}).get("name") or l["src_key"]
        rows.append({
            "connection": connection_word(l["relation"], outgoing),
            "name": name or other,
            "kind": kind,
            "in": (info or {}).get("in", ""),
            "source": (info or {}).get("source", ""),
            "path": (info or {}).get("path", ""),
            "via": via,
            "how": _how(l),
            "not_important": bool(l.get("unimportant")),
            "key": other,
            "src": l["src_key"],
            "relation": l["relation"],
            "dst": l.get("dst_key") or l.get("dst_ref") or "",
        })
    order = {w: i for i, w in enumerate(ORDER)}
    rows.sort(key=lambda r: (order.get(r["connection"], 99), r["not_important"], r["name"].lower()))
    return rows


def filter_rows(rows: list[dict], *, connections: list[str] | None = None, kinds: list[str] | None = None,
                places: list[str] | None = None, words: str = "", important_only: bool = False) -> list[dict]:
    """The rows matching every filter given. `words`: each word must appear in the name, place, path or how."""
    want = [w for w in (words or "").lower().split() if w]
    out = []
    for r in rows:
        if connections and r["connection"] not in connections:
            continue
        if kinds and r["kind"] not in kinds:
            continue
        if places and place(r) not in places:
            continue
        if important_only and r["not_important"]:
            continue
        text = f"{r['name']} {r['in']} {r['source']} {r['path']} {r['via']} {r['how']}".lower()
        if any(w not in text for w in want):
            continue
        out.append(r)
    return out


def place(r: dict) -> str:
    """Where the other item lives, for the "Folder or project" filter: its scan source and what it sits in."""
    return " / ".join(p for p in (r["source"], r["in"]) if p) or "(unknown)"


def counts(rows: list[dict], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        v = place(r) if field == "place" else r[field]
        out[v] = out.get(v, 0) + 1
    return out
