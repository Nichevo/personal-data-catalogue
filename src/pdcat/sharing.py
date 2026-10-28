"""Sharing what you know with colleagues, and merging in what they share with you.

A share file (JSON) holds your notes, item and column descriptions,
knowledge, questions, metrics, features, company-wide keys, business
projects, confirmed joins and hand-made links. It can also hold your scans
of chosen sources, for a colleague who can't scan them themselves.

Merging someone's file:

  1. Preview. Each of their sources is matched to one of yours (same name, or
     the same server and database, or the same folder); you can change the
     matches. The preview counts what is new, what is the same, and where you
     both wrote something different.
  2. Merge. New things are added with their author kept ("from Sam"). Where
     you both wrote something different, yours is kept unless you tick
     "take theirs". Empty fields of yours are filled from theirs.
  3. Undo. Every merge is one batch that can be undone in one click.

Scans in a share file: a source you haven't scanned is added as their scan,
marked as imported (your own scan of it later replaces it). For a source you
have scanned too, yours is kept and only items you don't have are added.

Sensitive values never go into a share file.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .store import Catalogue, now

FORMAT = "pdcat-share"
VERSION = 1
NO_SOURCE_SCHEMES = {"kb", "q", "metric", "feature", "key", "bp", "pkg", "manual"}

# table: (primary key columns, columns holding item keys, columns holding JSON lists of item keys)
SECTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "notes": (("uid",), ("asset_key",), ()),
    "item_info": (("asset_key",), ("asset_key", "copy_of"), ()),
    "column_info": (("asset_key", "column_name"), ("asset_key",), ()),
    "knowledge": (("uid",), (), ("pinned", "excluded")),
    "questions": (("uid",), (), ("tables",)),
    "metrics": (("uid",), ("from_measure",), ("sources",)),
    "features": (("uid",), ("built_by",), ("sources", "models")),
    "keys": (("name",), ("master_key",), ()),
    "key_columns": (("asset_key", "column_name"), ("asset_key",), ()),
    "bprojects": (("uid",), (), ()),
    "bproject_members": (("project_uid", "asset_key"), ("asset_key",), ()),
    "relationships": (("src_key", "src_columns", "dst_key", "dst_columns"), ("src_key", "dst_key"), ()),
    "manual_links": (("src_key", "relation", "dst_key"), ("src_key", "dst_key"), ()),
}
SELECT = {
    "notes": "SELECT * FROM notes WHERE archived = 0",
    "features": "SELECT * FROM features WHERE state = 'confirmed'",
    "keys": "SELECT * FROM keys WHERE state = 'active'",
    "key_columns": "SELECT * FROM key_columns WHERE state IN ('confirmed', 'rejected')",
    "bproject_members": "SELECT * FROM bproject_members WHERE state IN ('confirmed', 'rejected')",
    "relationships": "SELECT * FROM relationships WHERE state IN ('confirmed', 'rejected')",
}
# Fields that are bookkeeping, not content: ignored when deciding whether two rows differ.
BOOKKEEPING = {"id", "import_batch", "updated_at", "created_at", "author", "confirmed_at"}


def _columns(cat: Catalogue, table: str) -> list[str]:
    return [r["name"] for r in cat.conn.execute(f"PRAGMA table_info({table})")]


# ---- making a share file -------------------------------------------------------------------

def source_list(cat: Catalogue) -> list[dict[str, Any]]:
    """Your sources, with what identifies them (server and databases, or a folder path)."""
    out: dict[str, dict[str, Any]] = {}
    for r in cat.conn.execute("SELECT key, kind, source, attrs FROM assets WHERE kind IN ('database', 'folder', "
                              "'project') AND status = 'active' AND json_extract(attrs, '$.imported_from') IS NULL"):
        a = json.loads(r["attrs"])
        s = out.setdefault(r["source"], {"name": r["source"], "kind": r["kind"], "databases": []})
        if r["kind"] == "database":
            s["kind"] = "sqlserver"
            s["server"] = a.get("server", "")
            s["databases"].append(a.get("database") or r["key"].rsplit("/", 1)[-1])
        else:
            s["path"] = a.get("path", "")
    return list(out.values())


def make_share(cat: Catalogue, *, scans: list[str] | None = None, author: str | None = None,
               screenshots: bool = False) -> dict[str, Any]:
    from .curation import is_sensitive, sensitive_columns
    hidden = sensitive_columns(cat)
    pack: dict[str, Any] = {"format": FORMAT, "version": VERSION, "author": author or cat.author(), "made_at": now(),
                            "sources": source_list(cat)}
    for table in SECTIONS:
        rows = [dict(r) for r in cat.conn.execute(SELECT.get(table, f"SELECT * FROM {table}"))]
        for r in rows:
            r.pop("id", None)
            r.pop("import_batch", None)
        if table == "notes":
            rows = [n for n in rows if not (n["note_type"] == "example" and is_sensitive(
                hidden, n["asset_key"].partition("#")[0], n["asset_key"].partition("#")[2] or None))]
        pack[table] = rows
    pack["scans"] = {}
    for source in scans or []:
        assets = [dict(r) for r in cat.conn.execute("SELECT key, kind, name, source, parent, description, attrs, "
                                                    "first_seen, last_seen FROM assets WHERE source = ? AND status = "
                                                    "'active'", (source,))]
        keys = [a["key"] for a in assets]
        marks = ",".join("?" * len(keys)) or "''"
        columns = [dict(r) for r in cat.conn.execute(f"SELECT * FROM columns WHERE asset_key IN ({marks})", keys)]
        links = [dict(r) for r in cat.conn.execute("SELECT src_key, relation, dst_ref, dst_key, source, hint, evidence, "
                                                   "how FROM links WHERE source = ?", (source,))]
        profiles = []
        for r in cat.conn.execute(f"SELECT * FROM profiles WHERE asset_key IN ({marks})", keys):
            p = dict(r)
            if is_sensitive(hidden, p["asset_key"], p["column_name"]):
                p.update({"min_value": None, "max_value": None, "mean_value": None, "top_values": "[]",
                          "histogram": None, "percentiles": None})
            profiles.append(p)
        for a in assets:
            if is_sensitive(hidden, a["key"]):
                a["attrs"] = json.dumps({k: v for k, v in json.loads(a["attrs"]).items() if k != "sample"})
        pack["scans"][source] = {"assets": assets, "columns": columns, "links": links, "profiles": profiles}
    if screenshots:
        from .screenshots import for_share
        pack["screenshots"] = for_share(cat)
    return pack


def _open_question(q: dict[str, Any]) -> bool:
    return not (q.get("answer") or "").strip() and not (q.get("sql") or "").strip()


def questions_share(cat: Catalogue) -> dict[str, Any]:
    """A share file holding only your open questions (no answer and no SQL yet), for a colleague to answer.
    They merge it like any share file, answer the questions in their question library, and send the answers back."""
    rows = [dict(r) for r in cat.conn.execute("SELECT * FROM questions WHERE coalesce(author, '') IN (?, '')",
                                              (cat.author(),))]
    rows = [{k: v for k, v in r.items() if k != "import_batch"} for r in rows if _open_question(r)]
    return {"format": FORMAT, "version": VERSION, "author": cat.author(), "made_at": now(), "purpose": "questions",
            "sources": source_list(cat), "questions": rows}


def answers_share(cat: Catalogue) -> dict[str, Any]:
    """Answers to questions colleagues asked you: their questions that now have an answer or SQL in your library."""
    me = cat.author()
    rows = [dict(r) for r in cat.conn.execute("SELECT * FROM questions WHERE coalesce(author, '') NOT IN (?, '') "
                                              "AND import_batch IS NOT NULL", (me,))]
    rows = [{k: v for k, v in r.items() if k != "import_batch"} for r in rows if not _open_question(r)]
    return {"format": FORMAT, "version": VERSION, "author": me, "made_at": now(), "purpose": "answers",
            "sources": source_list(cat), "questions": rows}


def asked_of_me(cat: Catalogue) -> list[dict[str, Any]]:
    """Questions colleagues sent you, answered or not."""
    return [dict(r) for r in cat.conn.execute(
        "SELECT * FROM questions WHERE coalesce(author, '') NOT IN (?, '') AND import_batch IS NOT NULL "
        "ORDER BY created_at DESC", (cat.author(),))]


def to_share_json(cat: Catalogue, scans: list[str] | None = None, screenshots: bool = False) -> str:
    return json.dumps(make_share(cat, scans=scans, screenshots=screenshots), indent=1, ensure_ascii=False, default=str)


def read_share(text: str | bytes) -> dict[str, Any]:
    pack = json.loads(text)
    if not isinstance(pack, dict) or pack.get("format") != FORMAT:
        raise ValueError("This isn't a catalogue share file.")
    if pack.get("version", 1) > VERSION:
        raise ValueError("This share file was made by a newer version of the catalogue. Update yours first.")
    return pack


# ---- matching their sources to yours ----------------------------------------------------------

def suggest_mapping(cat: Catalogue, pack: dict[str, Any]) -> dict[str, str]:
    """Their source name -> yours. Same server and database first, then same folder, then same name.
    Their name is kept when nothing of yours matches (their items arrive under their name)."""
    from .duplicates import canonical, drive_aliases
    mine = source_list(cat)
    saved = {r["their_source"]: r["my_source"] for r in cat.conn.execute(
        "SELECT their_source, my_source FROM source_map WHERE author = ?", (pack.get("author", ""),))}
    aliases = drive_aliases(cat)
    out = {}
    for s in pack.get("sources", []):
        name = s["name"]
        if name in saved:
            out[name] = saved[name]
            continue
        match = None
        if s.get("kind") == "sqlserver" and s.get("server"):
            dbs = {d.lower() for d in s.get("databases", [])}
            match = next((m["name"] for m in mine if m.get("kind") == "sqlserver" and m.get("server", "").lower() ==
                          s["server"].lower() and dbs & {d.lower() for d in m["databases"]}), None)
        elif s.get("path"):
            match = next((m["name"] for m in mine if m.get("path") and canonical(m["path"], aliases) ==
                          canonical(s["path"], aliases)), None)
        if not match and any(m["name"] == name for m in mine):
            match = name
        out[name] = match or name
    return out


def save_mapping(cat: Catalogue, author: str, mapping: dict[str, str]) -> None:
    with cat.conn:
        for theirs, mine in mapping.items():
            cat.conn.execute("INSERT OR REPLACE INTO source_map (author, their_source, my_source) VALUES (?, ?, ?)",
                             (author, theirs, mine))


def remap_key(key: str | None, mapping: dict[str, str]) -> str | None:
    if not key or not mapping:
        return key
    m = re.match(r"^([a-z]+)://([^/#:]+)(.*)$", key, re.S)
    if not m or m.group(1) in NO_SOURCE_SCHEMES:
        return key
    scheme, source, rest = m.groups()
    lower = {k.lower(): v for k, v in mapping.items()}
    target = mapping.get(source) or lower.get(source.lower())
    if not target or target == source:
        return key
    return f"{scheme}://{target.lower() if scheme == 'sql' else target}{rest}"


def _remap_row(table: str, row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    _, key_cols, list_cols = SECTIONS[table]
    row = dict(row)
    for c in key_cols:
        if row.get(c):
            if c == "built_by":
                script, sep, fn = row[c].partition("::")
                row[c] = remap_key(script, mapping) + sep + fn
            else:
                base, sep, col = row[c].partition("#")
                row[c] = remap_key(base, mapping) + sep + col
    for c in list_cols:
        if row.get(c):
            items = json.loads(row[c]) if isinstance(row[c], str) else row[c]
            out = []
            for it in items:
                base, sep, col = str(it).partition("#")
                out.append(remap_key(base, mapping) + sep + col)
            row[c] = json.dumps(out)
    if table == "features" and row.get("project"):
        row["project"] = mapping.get(row["project"], row["project"])
    return row


# ---- preview and merge -------------------------------------------------------------------------

def _pk(table: str, row: dict[str, Any]) -> tuple:
    return tuple(row.get(c) for c in SECTIONS[table][0])


def _existing(cat: Catalogue, table: str, row: dict[str, Any]) -> dict[str, Any] | None:
    cols = SECTIONS[table][0]
    r = cat.conn.execute(f"SELECT * FROM {table} WHERE " + " AND ".join(f"{c} = ?" for c in cols),
                         _pk(table, row)).fetchone()
    return dict(r) if r else None


def _differs(mine: dict[str, Any], theirs: dict[str, Any]) -> list[str]:
    return [k for k, v in theirs.items() if k not in BOOKKEEPING and k in mine and (v or None) != (mine[k] or None)
            and v not in (None, "", "[]", "{}")]


def preview(cat: Catalogue, pack: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """What a merge would do: per section, how many are new, the same, or different (with the differences)."""
    out: dict[str, Any] = {"sections": {}, "conflicts": [], "scans": {}}
    for table in SECTIONS:
        counts = {"new": 0, "same": 0, "fills": 0, "different": 0}
        for raw in pack.get(table, []):
            row = _remap_row(table, raw, mapping)
            mine = _existing(cat, table, row)
            if mine is None:
                counts["new"] += 1
                continue
            diff = _differs(mine, row)
            if not diff:
                counts["same"] += 1
            elif all(not mine.get(k) for k in diff):
                counts["fills"] += 1
            else:
                counts["different"] += 1
                out["conflicts"].append({"table": table, "pk": list(_pk(table, row)), "fields": [
                    {"field": k, "mine": mine.get(k), "theirs": row.get(k)} for k in diff if mine.get(k)]})
        out["sections"][table] = counts
    for source, scan in (pack.get("scans") or {}).items():
        target = mapping.get(source, source)
        have = {r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE source = ?", (target,))}
        keys = [remap_key(a["key"], mapping) for a in scan["assets"]]
        out["scans"][source] = {"into": target, "items": len(keys), "new": sum(k not in have for k in keys),
                                "you_scanned": bool(have)}
    return out


def merge(cat: Catalogue, pack: dict[str, Any], mapping: dict[str, str], *, take_theirs: set[tuple] | bool = False,
          file_name: str = "") -> dict[str, Any]:
    """Merge a share file. take_theirs: True for every difference, or a set of (table, *pk) to take theirs for.
    Returns a summary; the merge can be undone with undo(cat, summary["batch"])."""
    author = pack.get("author", "")
    added: list[list] = []
    replaced: list[list] = []
    counts: dict[str, int] = {}
    with cat.conn:
        batch = cat.conn.execute("INSERT INTO import_batches (file, author, imported_at, summary) VALUES (?, ?, ?, "
                                 "'{}')", (file_name, author, now())).lastrowid
        for table in SECTIONS:
            cols = set(_columns(cat, table))
            n = 0
            for raw in pack.get(table, []):
                row = {k: v for k, v in _remap_row(table, raw, mapping).items() if k in cols}
                if "import_batch" in cols:
                    row["import_batch"] = batch
                if table in ("notes",) and not row.get("author"):
                    row["author"] = author
                mine = _existing(cat, table, row)
                if mine is None:
                    _insert(cat, table, row)
                    added.append([table, list(_pk(table, row))])
                    n += 1
                    continue
                diff = _differs(mine, row)
                if not diff:
                    continue
                take = take_theirs is True or (isinstance(take_theirs, set) and (table, *_pk(table, row)) in
                                               take_theirs)
                if table == "keys":
                    a = set(json.loads(mine.get("aliases") or "[]")) | set(json.loads(row.get("aliases") or "[]"))
                    row["aliases"] = json.dumps(sorted(a))
                new = dict(mine)
                for k in diff:
                    if take or not mine.get(k):
                        new[k] = row[k]
                if table == "keys":
                    new["aliases"] = row["aliases"]
                if new != mine:
                    replaced.append([table, mine])
                    _insert(cat, table, new)
                    n += 1
            counts[table] = n
        scans = {}
        for source, scan in (pack.get("scans") or {}).items():
            scans[source] = _merge_scan(cat, scan, mapping, author, batch, added)
        summary = {"counts": counts, "scans": scans, "added": added, "replaced": replaced}
        if pack.get("screenshots"):
            from .screenshots import merge_shared
            summary["screenshots"] = merge_shared(cat, pack["screenshots"], lambda k: remap_key(k, mapping))
        cat.conn.execute("UPDATE import_batches SET summary = ? WHERE id = ?", (json.dumps(summary, default=str), batch))
    save_mapping(cat, author, mapping)
    cat.resolve_refs()
    from .links import apply_manual_links
    apply_manual_links(cat)
    cat.rebuild_search()
    return {"batch": batch, **summary}


def _insert(cat: Catalogue, table: str, row: dict[str, Any]) -> None:
    cols = list(row)
    cat.conn.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     [row[c] for c in cols])


def _merge_scan(cat: Catalogue, scan: dict[str, Any], mapping: dict[str, str], author: str, batch: int,
                added: list) -> dict[str, int]:
    have = {r[0] for r in cat.conn.execute("SELECT key FROM assets")}
    new_keys = set()
    for a in scan["assets"]:
        key = remap_key(a["key"], mapping)
        if key in have:
            continue
        attrs = json.loads(a["attrs"]) if isinstance(a["attrs"], str) else a["attrs"]
        attrs.update({"imported_from": author, "import_batch": batch})
        cat.conn.execute("INSERT INTO assets (key, kind, name, source, parent, description, attrs, status, first_seen, "
                         "last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                         (key, a["kind"], a["name"], mapping.get(a["source"], a["source"]),
                          remap_key(a["parent"], mapping), a["description"], json.dumps(attrs), a["first_seen"],
                          a["last_seen"]))
        new_keys.add(key)
        added.append(["assets", [key]])
    for c in scan["columns"]:
        key = remap_key(c["asset_key"], mapping)
        if key in new_keys:
            cat.conn.execute("INSERT OR REPLACE INTO columns (asset_key, name, ordinal, data_type, nullable, "
                             "description) VALUES (?, ?, ?, ?, ?, ?)", (key, c["name"], c.get("ordinal"),
                                                                        c.get("data_type"), c.get("nullable"),
                                                                        c.get("description")))
    pcols = set(_columns(cat, "profiles"))
    for p in scan["profiles"]:
        key = remap_key(p["asset_key"], mapping)
        if key in new_keys:
            _insert(cat, "profiles", {**{k: v for k, v in p.items() if k in pcols}, "asset_key": key})
    for l in scan["links"]:
        src = remap_key(l["src_key"], mapping)
        if src in new_keys:
            cat.conn.execute("INSERT OR IGNORE INTO links (src_key, relation, dst_ref, dst_key, source, hint, evidence, "
                             "how) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (src, l["relation"], l["dst_ref"], None, mapping.get(l["source"], l["source"]), l["hint"],
                              l["evidence"], l["how"]))
    return {"added": len(new_keys), "kept_yours": len(scan["assets"]) - len(new_keys)}


# ---- batches ----------------------------------------------------------------------------------

def batches(cat: Catalogue) -> list[dict[str, Any]]:
    out = []
    for r in cat.conn.execute("SELECT * FROM import_batches ORDER BY id DESC"):
        b = dict(r)
        s = json.loads(b["summary"] or "{}")
        b["counts"] = s.get("counts", {})
        b["scans"] = s.get("scans", {})
        out.append(b)
    return out


def undo(cat: Catalogue, batch: int) -> None:
    row = cat.conn.execute("SELECT summary, undone FROM import_batches WHERE id = ?", (batch,)).fetchone()
    if not row or row["undone"]:
        return
    s = json.loads(row["summary"] or "{}")
    with cat.conn:
        for table, pk in s.get("added", []):
            if table == "assets":
                key = pk[0]
                for t, col in (("assets", "key"), ("columns", "asset_key"), ("profiles", "asset_key"),
                               ("links", "src_key")):
                    cat.conn.execute(f"DELETE FROM {t} WHERE {col} = ?", (key,))
                continue
            cols = SECTIONS[table][0]
            cat.conn.execute(f"DELETE FROM {table} WHERE " + " AND ".join(f"{c} = ?" for c in cols), pk)
        for table, old in reversed(s.get("replaced", [])):
            _insert(cat, table, old)
        cat.conn.execute("UPDATE import_batches SET undone = 1 WHERE id = ?", (batch,))
    cat.resolve_refs()
    cat.rebuild_search()
