"""Get your data back out: everything in the catalogue as CSV files or one JSON file.

The export has a table for each kind of thing: assets (tables, views, scripts,
data files…), columns, links (what reads/writes what), notes (including data
issues), profiles (value ranges per column), functions (every function in your
code with its docstring, calls and returns), packages (what each project
declares and imports), and what you know: item_info (purpose, grain, owner,
status…), column_info, knowledge, questions, metrics, features, keys,
business projects, relationships, series files and change history. Keys are
stable, so the files can be joined together or loaded into another catalogue.

Sensitive columns and items never have values in the export, and example-value
notes on them are left out.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from .series import series_files
from .store import Catalogue

TABLES = {
    "assets": "SELECT key, kind, name, source, parent, status, description, attrs, first_seen, last_seen "
              "FROM assets ORDER BY key",
    "columns": "SELECT asset_key, ordinal, name, data_type, nullable, description FROM columns "
               "ORDER BY asset_key, ordinal",
    "links": "SELECT src_key, relation, COALESCE(dst_key, dst_ref) AS dst, dst_key IS NOT NULL AS matched, "
             "source, evidence FROM links ORDER BY src_key, relation, dst",
    "notes": "SELECT id, uid, asset_key, note_type, status, body, tags, author, created_at, updated_at, "
             "confirmed_at, archived FROM notes ORDER BY asset_key, created_at",
    "profiles": "SELECT asset_key, ordinal, column_name, data_type, row_count, null_count, distinct_count, "
                "min_value, max_value, mean_value, top_values, histogram, percentiles, hints, method, profiled_at "
                "FROM profiles ORDER BY asset_key, ordinal",
    "item_info": "SELECT * FROM item_info ORDER BY asset_key",
    "column_info": "SELECT * FROM column_info ORDER BY asset_key, column_name",
    "knowledge": "SELECT * FROM knowledge ORDER BY title",
    "questions": "SELECT * FROM questions ORDER BY question",
    "metrics": "SELECT * FROM metrics ORDER BY name",
    "features": "SELECT * FROM features WHERE state != 'rejected' ORDER BY project, name",
    "keys": "SELECT * FROM keys WHERE state = 'active' ORDER BY name",
    "key_columns": "SELECT * FROM key_columns WHERE state = 'confirmed' ORDER BY key_name, asset_key",
    "business_projects": "SELECT * FROM bprojects ORDER BY name",
    "business_project_members": "SELECT * FROM bproject_members WHERE state = 'confirmed' ORDER BY project_uid",
    "relationships": "SELECT * FROM relationships WHERE state != 'rejected' ORDER BY src_key, dst_key",
    "change_history": "SELECT asset_key, at, change, detail FROM schema_history ORDER BY at, id",
}
BOM = "\ufeff"  # Excel reads a UTF-8 CSV correctly only when it starts with this
JSON_COLUMNS = {"top_values", "histogram", "percentiles", "hints", "pinned", "excluded", "tables", "sources",
                "models", "importance", "aliases"}


def export_data(cat: Catalogue) -> dict[str, list[dict[str, Any]]]:
    from .curation import is_sensitive, sensitive_columns
    hidden = sensitive_columns(cat)
    data = {}
    for name, sql in TABLES.items():
        rows = [dict(r) for r in cat.conn.execute(sql)]
        for r in rows:
            for col in JSON_COLUMNS & set(r):
                if isinstance(r[col], str):
                    try:
                        r[col] = json.loads(r[col])
                    except ValueError:
                        pass
        if name == "assets":
            for r in rows:
                r["attrs"] = json.loads(r["attrs"])
        if name == "links":
            for r in rows:
                r["matched"] = bool(r["matched"])
        if name == "notes":
            def keep(n: dict) -> bool:
                base, _, col = n["asset_key"].partition("#")
                return not (n["note_type"] == "example" and is_sensitive(hidden, base, col or None))
            rows = [n for n in rows if keep(n)]
        if name == "profiles":
            rows = [r if not is_sensitive(hidden, r["asset_key"], r["column_name"]) else
                    {**r, "min_value": None, "max_value": None, "mean_value": None, "top_values": [],
                     "histogram": None, "percentiles": None} for r in rows]
        data[name] = rows
    data["functions"] = _functions(data["assets"])
    data["packages"] = _packages(cat)
    data["series_files"] = series_files(cat)
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


def to_csv_zip(cat: Catalogue, *, screenshots: bool = False) -> bytes:
    """One CSV per table in a zip. UTF-8 with BOM so Excel shows accents correctly. Report screenshots (which can
    show real numbers) go in only when asked for, as a screenshots folder and an index CSV."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in export_data(cat).items():
            z.writestr(f"{name}.csv", BOM + _csv_text(rows))
        if screenshots:
            from . import screenshots as S
            rows = S.index_rows(cat)
            if rows:
                z.writestr("screenshots.csv", BOM + _csv_text(rows))
                for r in rows:
                    z.write(S.folder(cat) / r["file"], f"screenshots/{r['file']}")
    return buf.getvalue()


def rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    return (BOM + _csv_text(rows)).encode("utf-8")


def write_export(cat: Catalogue, out: Path, fmt: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path = out / "catalogue.json"
        path.write_text(to_json(cat), encoding="utf-8")
        return [path]
    paths = []
    for name, rows in export_data(cat).items():
        path = out / f"{name}.csv"
        path.write_text(BOM + _csv_text(rows), encoding="utf-8", newline="")
        paths.append(path)
    return paths
