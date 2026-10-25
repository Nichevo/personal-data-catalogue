"""Get your data back out: everything in the catalogue as CSV files or one JSON file.

The export has seven tables: assets (tables, views, scripts, data files…),
columns, links (what reads/writes what), notes (including data issues),
profiles (value ranges per column), functions (every function in your code with
its docstring, calls and returns) and packages (what each project declares and
imports). Keys are stable, so the files can be
joined together or loaded into another catalogue later.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from .store import Catalogue

TABLES = {
    "assets": "SELECT key, kind, name, source, parent, status, description, attrs, first_seen, last_seen "
              "FROM assets ORDER BY key",
    "columns": "SELECT asset_key, ordinal, name, data_type, nullable, description FROM columns "
               "ORDER BY asset_key, ordinal",
    "links": "SELECT src_key, relation, COALESCE(dst_key, dst_ref) AS dst, dst_key IS NOT NULL AS matched, "
             "source, evidence FROM links ORDER BY src_key, relation, dst",
    "notes": "SELECT id, asset_key, note_type, status, body, tags, created_at, updated_at FROM notes "
             "ORDER BY asset_key, created_at",
    "profiles": "SELECT asset_key, ordinal, column_name, data_type, row_count, null_count, distinct_count, "
                "min_value, max_value, mean_value, top_values, method, profiled_at FROM profiles "
                "ORDER BY asset_key, ordinal",
}


def export_data(cat: Catalogue) -> dict[str, list[dict[str, Any]]]:
    data = {}
    for name, sql in TABLES.items():
        rows = [dict(r) for r in cat.conn.execute(sql)]
        if name == "assets":
            for r in rows:
                r["attrs"] = json.loads(r["attrs"])
        if name == "profiles":
            for r in rows:
                r["top_values"] = json.loads(r["top_values"])
        if name == "links":
            for r in rows:
                r["matched"] = bool(r["matched"])
        data[name] = rows
    data["functions"] = _functions(data["assets"])
    data["packages"] = _packages(cat)
    return data


def _functions(assets: list[dict]) -> list[dict[str, Any]]:
    rows = []
    for a in assets:
        for fn, f in (a["attrs"].get("function_info") or {}).items():
            rows.append({"asset_key": a["key"], "function": fn, "line": f.get("line"), "end_line": f.get("end_line"),
                         "parameters": ", ".join(p["name"] for p in f.get("params", [])),
                         "returns_annotation": f.get("returns_annotation", ""), "returns": f.get("returns", []),
                         "calls": f.get("calls", []), "doc": f.get("doc", "")})
    return rows


def _packages(cat: Catalogue) -> list[dict[str, Any]]:
    from .envinfo import project_environment
    rows = []
    for r in cat.conn.execute("SELECT source FROM assets WHERE kind = 'project' ORDER BY source"):
        env = project_environment(cat, r["source"])
        rows += [{"project": r["source"], "package": d["name"], "version": d["version"], "declared_in": d["file"],
                  "status": "declared" if d not in env["unused"] else "declared, not imported"} for d in env["deps"]]
        rows += [{"project": r["source"], "package": u["package"], "version": "", "declared_in": "",
                  "status": "imported, not declared"} for u in env["undeclared"]]
    return rows


def to_json(cat: Catalogue) -> str:
    return json.dumps(export_data(cat), indent=2, ensure_ascii=False, default=str)


def _csv_text(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v
                             for k, v in r.items()})
    return buf.getvalue()


def to_csv_zip(cat: Catalogue) -> bytes:
    """One CSV per table in a zip. UTF-8 with BOM so Excel shows accents correctly."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in export_data(cat).items():
            z.writestr(f"{name}.csv", "﻿" + _csv_text(rows))
    return buf.getvalue()


def rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    return ("﻿" + _csv_text(rows)).encode("utf-8")


def write_export(cat: Catalogue, out: Path, fmt: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path = out / "catalogue.json"
        path.write_text(to_json(cat), encoding="utf-8")
        return [path]
    paths = []
    for name, rows in export_data(cat).items():
        path = out / f"{name}.csv"
        path.write_text("﻿" + _csv_text(rows), encoding="utf-8", newline="")
        paths.append(path)
    return paths
