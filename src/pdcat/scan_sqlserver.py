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

# Declared primary and foreign keys, for ERDs. One light query per database.
KEYS_SQL = """
SELECT 'pk' AS kind, s.name AS schema_name, o.name AS object_name, c.name AS column_name, ic.key_ordinal AS ordinal,
       NULL AS ref_schema, NULL AS ref_object, NULL AS ref_column, i.name AS constraint_name
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
JOIN sys.objects o ON o.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE i.is_primary_key = 1 AND o.is_ms_shipped = 0
UNION ALL
SELECT 'fk', s.name, o.name, c.name, fkc.constraint_column_id, rs.name, ro.name, rc.name, fk.name
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.objects o ON o.object_id = fk.parent_object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.columns c ON c.object_id = fkc.parent_object_id AND c.column_id = fkc.parent_column_id
JOIN sys.objects ro ON ro.object_id = fk.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
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


def objects_sql(definitions: bool = True) -> str:
    return OBJECTS_SQL if definitions else OBJECTS_SQL.replace("OBJECT_DEFINITION(o.object_id)", "NULL")


def plan_scan(cat: Catalogue, src: SqlServerSource, databases: list[str] | None = None) -> list[dict[str, str]]:
    """The queries a metadata scan would send, without connecting."""
    from .pacing import settings
    defs = bool(settings(cat)["sql_scan_definitions"])
    out = []
    for db in databases or src.databases:
        for purpose, sql in (("tables, views and procedures", objects_sql(defs)), ("columns", COLUMNS_SQL),
                             ("keys and relationships", KEYS_SQL)):
            out.append({"source": src.name, "database": db, "purpose": purpose, "query": sql.strip()})
    return out


def connect(src: SqlServerSource, database: str):
    import pyodbc  # imported here so the rest of the tool works without an ODBC driver
    return pyodbc.connect(connection_string(src, database), timeout=15, readonly=True)


def fetch(src: SqlServerSource, database: str, pacer, connector=None) -> dict[str, list[dict[str, Any]]]:
    """Read one database's catalogue views, one query at a time at the controlled pace (see pacing.py)."""
    conn = (connector or connect)(src, database)
    try:
        cur = conn.cursor()
        out = {}
        defs = bool(pacer.s["sql_scan_definitions"])
        for name, sql in (("objects", objects_sql(defs)), ("columns", COLUMNS_SQL), ("keys", KEYS_SQL)):
            try:
                cols, rows = pacer.query(cur, sql, purpose=f"scan {name}", database=database)
            except Exception:
                if name == "keys":  # older servers or limited rights: keys are a nice-to-have
                    out[name] = []
                    continue
                raise
            out[name] = [dict(zip(cols, row)) for row in rows]
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


def scan_server(cat: Catalogue, src: SqlServerSource, databases: list[str] | None = None,
                progress=print, connector=None) -> dict[str, int]:
    """Scan the source's databases (or just the ones named), one database at a time.

    Each database is fetched before anything is written, so a connection
    failure leaves its entry in the catalogue untouched.
    """
    from .pacing import Pacer
    pacer = Pacer(cat, src.name)
    totals: dict[str, int] = {}
    for db in databases or src.databases:
        progress(f"  {db} ...")
        data = fetch(src, db, pacer, connector)
        with cat.scan(src.name, prefix=f"sql://{src.name}/{db}"):
            for k, v in ingest(cat, src, db, data).items():
                totals[k] = totals.get(k, 0) + v
        from .relationships import ingest_keys
        totals["keys"] = totals.get("keys", 0) + ingest_keys(cat, src.name, db, data.get("keys", []))
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


def profile_tables(cat: Catalogue, src: SqlServerSource, keys: list[str], progress=print,
                   connector=None, pacer=None) -> dict[str, str]:
    """Profile scanned tables/views on this server, at the controlled pace.

    Counts and ranges come from one aggregate query: over the whole table, or
    over a TOP (N) sample when the table is bigger than the limit on the
    Settings page. Common values and range charts come from a smaller sample.
    Read-only, with NOLOCK so it never blocks other work. Returns {key: error}
    for any that failed; raises PacingStopped when the run stops early.
    """
    import pandas as pd

    from .pacing import Pacer, PacingStopped, _is_timeout, set_queue_state, settings
    from .profiling import combine_sql_profile, sql_profile_queries

    s = settings(cat)
    pacer = pacer or Pacer(cat, src.name)
    by_db: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        asset = cat.get_asset(key)
        if asset and asset["source"] == src.name and asset["kind"] in ("table", "view"):
            by_db.setdefault(asset["attrs"]["database"], []).append(asset)

    errors: dict[str, str] = {}
    done = 0
    try:
        for database, assets in by_db.items():
            conn = (connector or connect)(src, database)
            try:
                cur = conn.cursor()
                for asset in assets:
                    if done >= int(s["sql_max_tables_per_run"]):
                        raise PacingStopped(f"Reached the limit of {s['sql_max_tables_per_run']} tables for one run. "
                                            "The rest stay in the queue for next time.")
                    a = asset["attrs"]
                    rows = a.get("row_count") or 0
                    sample = int(s["sql_sample_rows"]) if asset["kind"] == "view" or rows > int(s["sql_sample_over_rows"]) \
                        else None
                    progress(f"  profiling {database}.{asset['name']}" + (f" (sample of {sample:,} rows)" if sample else ""))
                    columns = [dict(c) for c in cat.columns(asset["key"])]
                    agg_sql, top_sql = sql_profile_queries(a["schema"], a["object"], columns,
                                                           int(s["sql_top_values_rows"]), sample_for_aggregates=sample)
                    try:
                        names, got = pacer.query(cur, agg_sql, purpose=f"profile {asset['name']}: counts and ranges",
                                                 database=database)
                        agg = dict(zip(names, got[0]))
                        names, got = pacer.query(cur, top_sql, purpose=f"profile {asset['name']}: common values",
                                                 database=database)
                        frame = pd.DataFrame.from_records(got, columns=names)
                    except PacingStopped:
                        raise
                    except Exception as e:
                        errors[asset["key"]] = str(e)
                        set_queue_state(cat, [asset["key"]], "failed", str(e)[:500])
                        progress(f"    failed: {e}")
                        if _is_timeout(e) and s["sql_stop_on_timeout"]:
                            raise PacingStopped(f"{asset['name']} timed out, so the run stopped to go easy on the "
                                                "server. Raise the time limit or sample size on the Settings page, "
                                                "or skip this table.") from e
                        continue
                    profile = combine_sql_profile(columns, agg, frame)
                    method = (f"sample of {sample:,} rows for counts and ranges" if sample else
                              "whole table for counts and ranges") + f"; common values and charts from first {len(frame):,} rows"
                    cat.save_profile(asset["key"], profile, method)
                    set_queue_state(cat, [asset["key"]], "done")
                    done += 1
            finally:
                conn.close()
    finally:
        cat.rebuild_search()
    return errors


def run_queue(cat: Catalogue, sources: list[SqlServerSource], progress=print, connector=None) -> dict[str, Any]:
    """Profile every approved table in the queue. Returns {"done", "failed", "stopped"}."""
    from .pacing import Pacer, PacingStopped, queue
    cat.set_setting("sql_stop_requested", False, quiet=True)
    approved = [q["asset_key"] for q in queue(cat, ("approved",))]
    result: dict[str, Any] = {"done": 0, "failed": 0, "stopped": None, "left": 0}
    by_name = {s.name.lower(): s for s in sources}
    for name in sorted({k.split("/")[2] for k in approved if k.startswith("sql://")}):
        src = by_name.get(name)
        mine = [k for k in approved if k.startswith(f"sql://{name}/")]
        if src is None:
            progress(f"No [[sqlserver]] source called {name} in your config; skipping {len(mine)} table(s).")
            continue
        try:
            before = cat.conn.execute("SELECT COUNT(*) FROM profile_queue WHERE state = 'done'").fetchone()[0]
            errors = profile_tables(cat, src, mine, progress, connector, Pacer(cat, src.name))
            result["failed"] += len(errors)
        except PacingStopped as e:
            result["stopped"] = str(e)
            progress(str(e))
            break
        finally:
            after = cat.conn.execute("SELECT COUNT(*) FROM profile_queue WHERE state = 'done'").fetchone()[0]
            result["done"] += after - before
    result["left"] = len(queue(cat, ("approved",)))
    return result
