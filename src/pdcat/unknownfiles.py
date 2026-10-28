"""Files a script reads or writes whose path the scan couldn't work out (`df.to_csv(filepath)` where
`filepath` comes from somewhere the scan can't follow). Each one is listed on the script's page as
"Unknown file" with its line and code, goes on the To review list, and you can say which file it is:
that adds a link kept through rescans."""
from __future__ import annotations

import json
from typing import Any

from .store import Catalogue


def _id(key: str, u: dict) -> str:
    return f"{key}|{u.get('cell') or ''}|{u['line']}|{u['code']}"


def unknown_files(cat: Catalogue, key: str | None = None) -> list[dict[str, Any]]:
    """[{"script", "script_name", "line", "code", "relation", "call", "function", "id"}] still to be answered."""
    done = cat.setting("unknown_files_answered", {})
    sql = "SELECT key, name, attrs FROM assets WHERE kind IN ('script', 'notebook') AND status = 'active' " \
          "AND attrs LIKE '%unknown_files%'"
    params: tuple = ()
    if key:
        sql += " AND key = ?"
        params = (key,)
    out = []
    for r in cat.conn.execute(sql, params):
        for u in json.loads(r["attrs"]).get("unknown_files", []):
            uid = _id(r["key"], u)
            if uid not in done:
                out.append({**u, "script": r["key"], "script_name": r["name"], "id": uid})
    return out


def answer(cat: Catalogue, entry: dict[str, Any], dst_key: str | None) -> None:
    """Say which file it is (dst_key), or None for "not a data file, ignore it"."""
    if dst_key:
        from .links import add_manual_link
        add_manual_link(cat, entry["script"], entry["relation"], dst_key)
    done = cat.setting("unknown_files_answered", {})
    done[entry["id"]] = dst_key or "ignore"
    cat.set_setting("unknown_files_answered", done)


def review_tasks(cat: Catalogue, task) -> list:
    by_script: dict[str, list[dict]] = {}
    for u in unknown_files(cat):
        by_script.setdefault(u["script"], []).append(u)
    out = []
    for key, items in by_script.items():
        lines = "; ".join(f"line {u['line']}: {u['code']}" for u in items[:4]) + (" …" if len(items) > 4 else "")
        out.append(task("unknown_file", key, f"{len(items)} file(s) in {items[0]['script_name']} the scan couldn't name",
                        f"{lines}. Say which file each one is on the script's page.", version=str(len(items))))
    for r in cat.conn.execute("SELECT l.src_key, l.dst_ref, l.dst_key, l.match_note, a.name FROM links l "
                              "JOIN assets a ON a.key = l.src_key WHERE l.match_note LIKE 'found in another folder%'"):
        out.append(task("wrong_folder", r["src_key"], f"{r['name']} names {r['dst_ref'][5:]}, found in another folder",
                        f"{r['match_note']}. Is it the right file? Remove the link from the script's page if not.",
                        version=r["dst_ref"]))
    return out
