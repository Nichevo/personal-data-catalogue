"""A folder's "Contains" list, tidied: dated copies of the same data as one line per series, files one
script writes from one pattern as one line, and the rest by subfolder and type."""
from __future__ import annotations

import json
from typing import Any

from .store import Catalogue

TYPE_NAMES = {"data_file": "data file", "file_series": "dated series", "group": "script output",
              "script": "script", "notebook": "notebook", "sql_script": "SQL file", "report": "Power BI report",
              "workbook": "Excel workbook", "access_db": "Access database", "project": "code project",
              "model": "ML model", "document": "document"}


def contents_rows(cat: Catalogue, parent: str, children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per thing in the folder: {"key", "name", "subfolder", "type", "detail", "rows", "changed", "made by"}."""
    keys = [c["key"] for c in children]
    outputs: dict[str, dict[str, Any]] = {}
    if keys:
        marks = ",".join("?" * len(keys))
        for r in cat.conn.execute(
                f"SELECT m.asset_key, g.uid, g.name, g.attrs FROM group_members m JOIN item_groups g ON g.uid = m.group_uid "
                f"WHERE g.type = 'outputs' AND m.asset_key IN ({marks})", keys):
            outputs[r[0]] = {"uid": r[1], "name": r[2], "attrs": json.loads(r[3] or "{}")}
    script_names = {}
    rows: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}
    for c in children:
        attrs = c["attrs"] if isinstance(c["attrs"], dict) else json.loads(c["attrs"] or "{}")
        name = c["name"]
        sub, _, base = name.replace("\\", "/").rpartition("/")
        changed = (attrs.get("modified") or "")[:10]
        if c["kind"] == "data_file" and c["key"] in outputs:
            g = outputs[c["key"]]
            row = grouped.get(g["uid"])
            if row is None:
                script = g["attrs"].get("script") or ""
                if script and script not in script_names:
                    a = cat.get_asset(script)
                    script_names[script] = a["name"].rsplit("/", 1)[-1] if a else script
                row = {"key": f"group://{g['uid']}", "name": g["attrs"].get("pattern") or g["name"], "subfolder": sub,
                       "type": TYPE_NAMES["group"], "files": 0, "rows": None, "changed": "",
                       "made by": script_names.get(script, ""), "first": None, "last": None}
                grouped[g["uid"]] = row
                rows.append(row)
            row["files"] += 1
            row["changed"] = max(row["changed"], changed)
            continue
        row = {"key": c["key"], "name": base or name, "subfolder": sub, "type": TYPE_NAMES.get(c["kind"], c["kind"]),
               "files": None, "rows": attrs.get("row_count"), "changed": changed, "made by": ""}
        if c["kind"] == "file_series":
            row.update(name=attrs.get("pattern") or base or name, files=attrs.get("file_count"),
                       changed=attrs.get("last") or changed)
            row["detail"] = f"{attrs.get('file_count', '?')} files · {attrs.get('first', '?')} to {attrs.get('last', '?')}"
        elif c["kind"] == "data_file" and "." in base:
            row["type"] = f"{base.rsplit('.', 1)[-1].lower()} file"
        rows.append(row)
    for r in rows:
        if r.get("detail"):
            continue
        if r["type"] == TYPE_NAMES["group"]:
            r["detail"] = f"{r['files']} files" + (f" · written by {r['made by']}" if r["made by"] else "")
        else:
            r["detail"] = ""
    return sorted(rows, key=lambda r: (r["subfolder"].lower(), r["type"] not in ("dated series", "script output"),
                                       r["type"], r["name"].lower()))
