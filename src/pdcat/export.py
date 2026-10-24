"""Get your data back out: everything in the catalogue as CSV files or one JSON file.

The export has five tables: assets (tables, views, scripts, data files…),
columns, links (what reads/writes what), notes (including data issues) and
profiles (value ranges per column). Keys are stable, so the files can be
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
    return data


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
