"""Scan SQL Server databases you can reach with your own login.

Reads tables, views and stored procedures with their columns, descriptions
(MS_Description extended properties), row counts and definitions. Uses only
catalogue views, so it needs no special permissions beyond what you already
have: objects you cannot see are simply not listed.
"""
from __future__ import annotations

from typing import Any

from .config import SqlServerSource
from .sqlrefs import extract_refs
from .store import Catalogue

OBJECTS_SQL = """
SELECT s.name AS schema_name, o.name AS object_name, o.type AS object_type,
       o.create_date, o.modify_date,
       CAST(ep.value AS nvarchar(max)) AS description,
       (SELECT SUM(p.rows) FROM sys.partitions p
         WHERE p.object_id = o.object_id AND p.index_id IN (0, 1)) AS row_count,
       OBJECT_DEFINITION(o.object_id) AS definition
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.extended_properties ep
       ON ep.class = 1 AND ep.major_id = o.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
WHERE o.type IN ('U', 'V', 'P') AND o.is_ms_shipped = 0
"""

COLUMNS_SQL = """
SELECT s.name AS schema_name, o.name AS object_name, c.name AS column_name, c.column_id,
       TYPE_NAME(c.user_type_id) AS type_name, c.max_length, c.precision, c.scale, c.is_nullable,
       CAST(ep.value AS nvarchar(max)) AS description
FROM sys.columns c
JOIN sys.objects o ON o.object_id = c.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.extended_properties ep
       ON ep.class = 1 AND ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
"""

KINDS = {"U": "table", "V": "view", "P": "procedure"}
MAX_DEFINITION = 50_000


def connection_string(src: SqlServerSource, database: str) -> str:
    parts = [
        f"DRIVER={{{src.driver}}}",
        f"SERVER={src.server}",
        f"DATABASE={database}",
        f"Encrypt={'yes' if src.encrypt else 'no'}",
        f"TrustServerCertificate={'yes' if src.trust_server_certificate else 'no'}",
    ]
    if "authentication=" not in src.extra.lower() and "uid=" not in src.extra.lower():
        parts.append("Trusted_Connection=yes")  # your Windows login
    s = ";".join(parts) + ";"
    return s + src.extra if src.extra else s


def fetch(src: SqlServerSource, database: str) -> dict[str, list[dict[str, Any]]]:
    import pyodbc  # imported here so the rest of the tool works without an ODBC driver

    conn = pyodbc.connect(connection_string(src, database), timeout=15, readonly=True)
    try:
        cur = conn.cursor()
        out = {}
        for name, sql in (("objects", OBJECTS_SQL), ("columns", COLUMNS_SQL)):
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            out[name] = [dict(zip(cols, row)) for row in cur.fetchall()]
        return out
    finally:
        conn.close()


def object_key(source: str, database: str, schema: str, name: str) -> str:
    return f"sql://{source}/{database}/{schema}.{name}".lower()


def ingest(cat: Catalogue, src: SqlServerSource, database: str, data: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Write one database's metadata into the catalogue. Call inside `cat.scan(src.name)`."""
    db_key = f"sql://{src.name}/{database}".lower()
    cat.upsert_asset(db_key, "database", f"{src.name}/{database}", src.name,
                     attrs={"server": src.server, "database": database})
    counts = {"table": 0, "view": 0, "procedure": 0, "links": 0}

    for o in data["objects"]:
        kind = KINDS.get(o["object_type"].strip())
        if not kind:
            continue
        key = object_key(src.name, database, o["schema_name"], o["object_name"])
        attrs = {
            "server": src.server, "database": database, "schema": o["schema_name"], "object": o["object_name"],
            "row_count": o.get("row_count"), "created": o.get("create_date"), "modified": o.get("modify_date"),
        }
        definition = o.get("definition")
        if definition:
            attrs["definition"] = definition[:MAX_DEFINITION]
        cat.upsert_asset(key, kind, f"{o['schema_name']}.{o['object_name']}", src.name,
                         parent=db_key, description=o.get("description"), attrs=attrs)
        counts[kind] += 1
        # Views and procedures: which tables do they read and write?
        if definition and kind in ("view", "procedure"):
            for ref in extract_refs(definition):
                if ref.name in (f"{o['schema_name']}.{o['object_name']}".lower(), o["object_name"].lower()):
                    continue  # "CREATE VIEW x AS" names itself
                cat.add_link(key, ref.relation, f"table:{ref.name}", src.name,
                             hint=f"{src.name}/{database}", evidence=f"definition of {o['schema_name']}.{o['object_name']}")
                counts["links"] += 1

    by_object: dict[str, list[dict[str, Any]]] = {}
    for c in data["columns"]:
        key = object_key(src.name, database, c["schema_name"], c["object_name"])
        by_object.setdefault(key, []).append({
            "name": c["column_name"],
            "ordinal": c["column_id"],
            "data_type": _type_label(c),
            "nullable": int(bool(c["is_nullable"])),
            "description": c.get("description"),
        })
    for key, cols in by_object.items():
        cat.set_columns(key, cols)
    return counts


def scan_server(cat: Catalogue, src: SqlServerSource) -> dict[str, int]:
    # Fetch everything first so a connection failure leaves the catalogue untouched.
    fetched = {db: fetch(src, db) for db in src.databases}
    totals: dict[str, int] = {}
    with cat.scan(src.name):
        for db, data in fetched.items():
            for k, v in ingest(cat, src, db, data).items():
                totals[k] = totals.get(k, 0) + v
    return totals


def _type_label(c: dict[str, Any]) -> str:
    t = c["type_name"]
    if t in ("varchar", "char", "varbinary", "binary"):
        return f"{t}({'max' if c['max_length'] == -1 else c['max_length']})"
    if t in ("nvarchar", "nchar"):
        return f"{t}({'max' if c['max_length'] == -1 else c['max_length'] // 2})"
    if t in ("decimal", "numeric"):
        return f"{t}({c['precision']},{c['scale']})"
    return t


def profile_tables(cat: Catalogue, src: SqlServerSource, keys: list[str], sample_rows: int = 10_000,
                   timeout: int = 300, progress=print) -> dict[str, str]:
    """Profile scanned tables/views on this server: whole-table counts and ranges, top values from a sample.

    Read-only, and uses NOLOCK so it never blocks other work. Returns {key: error} for any that failed.
    """
    import pandas as pd
    import pyodbc

    from .profiling import combine_sql_profile, sql_profile_queries

    by_db: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        asset = cat.get_asset(key)
        if asset and asset["source"] == src.name and asset["kind"] in ("table", "view"):
            by_db.setdefault(asset["attrs"]["database"], []).append(asset)

    errors = {}
    for database, assets in by_db.items():
        conn = pyodbc.connect(connection_string(src, database), timeout=15, readonly=True)
        conn.timeout = timeout
        try:
            cur = conn.cursor()
            for asset in assets:
                a = asset["attrs"]
                progress(f"  profiling {database}.{asset['name']} ...")
                columns = [dict(c) for c in cat.columns(asset["key"])]
                try:
                    agg_sql, sample_sql = sql_profile_queries(a["schema"], a["object"], columns, sample_rows)
                    cur.execute(agg_sql)
                    names = [d[0] for d in cur.description]
                    agg = dict(zip(names, cur.fetchone()))
                    cur.execute(sample_sql)
                    names = [d[0] for d in cur.description]
                    sample = pd.DataFrame.from_records([tuple(r) for r in cur.fetchall()], columns=names)
                except pyodbc.Error as e:
                    errors[asset["key"]] = str(e)
                    progress(f"    failed: {e}")
                    continue
                profile = combine_sql_profile(columns, agg, sample)
                cat.save_profile(asset["key"], profile,
                                 f"whole table for counts and ranges; mean and top values from first {len(sample):,} rows")
        finally:
            conn.close()
    cat.rebuild_search()
    return errors
