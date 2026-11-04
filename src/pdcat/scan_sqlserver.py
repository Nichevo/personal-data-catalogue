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

# Tables SQL Server tools make for themselves, such as dbo.sysdiagrams, carry this property.
TOOLS_FILTER = ("\n  AND NOT EXISTS (SELECT 1 FROM sys.extended_properties tp WHERE tp.class = 1 "
                "AND tp.major_id = o.object_id AND tp.minor_id = 0 AND tp.name = 'microsoft_database_tools_support')")

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


def objects_sql(definitions: bool = True, skip_tool_tables: bool = True) -> str:
    sql = OBJECTS_SQL if definitions else OBJECTS_SQL.replace("OBJECT_DEFINITION(o.object_id)", "NULL")
    return sql.rstrip() + TOOLS_FILTER + "\n" if skip_tool_tables else sql


def columns_sql(skip_tool_tables: bool = True) -> str:
    return COLUMNS_SQL.rstrip() + TOOLS_FILTER + "\n" if skip_tool_tables else COLUMNS_SQL


def plan_scan(cat: Catalogue, src: SqlServerSource, databases: list[str] | None = None) -> list[dict[str, str]]:
    """The queries a metadata scan would send, without connecting."""
    from .pacing import settings
    s = settings(cat)
    defs, skip = bool(s["sql_scan_definitions"]), bool(s["sql_skip_tool_tables"])
    out = []
    for db in databases or src.databases:
        for purpose, sql in (("tables, views and procedures", objects_sql(defs, skip)), ("columns", columns_sql(skip)),
                             ("keys and relationships", KEYS_SQL)):
            out.append({"source": src.name, "database": db, "purpose": purpose, "query": sql.strip()})
    return out


def connect(src: SqlServerSource, database: str):
    import pyodbc  # imported here so the rest of the tool works without an ODBC driver
    conn = pyodbc.connect(connection_string(src, database), timeout=15, readonly=True)
    safe_decoding(conn, pyodbc)
    return conn


def safe_decoding(conn, pyodbc) -> None:
    """Read char/varchar text without stopping on bytes that aren't UTF-8 (see profiling.safe_text)."""
    from .profiling import safe_text
    for sql_type in (pyodbc.SQL_CHAR, pyodbc.SQL_VARCHAR, pyodbc.SQL_LONGVARCHAR):
        try:
            conn.add_output_converter(sql_type, safe_text)
        except Exception:  # an odd driver: keep pyodbc's own decoding
            pass


def fetch(src: SqlServerSource, database: str, pacer, connector=None) -> dict[str, list[dict[str, Any]]]:
    """Read one database's catalogue views, one query at a time at the controlled pace (see pacing.py)."""
    conn = (connector or connect)(src, database)
    try:
        cur = conn.cursor()
        out = {}
        defs, skip = bool(pacer.s["sql_scan_definitions"]), bool(pacer.s.get("sql_skip_tool_tables", True))
        for name, sql in (("objects", objects_sql(defs, skip)), ("columns", columns_sql(skip)), ("keys", KEYS_SQL)):
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
                   connector=None, pacer=None, max_tables: int | None = None) -> dict[str, str]:
    """Profile scanned tables/views on this server, at the controlled pace. `max_tables` overrides the
    "Most tables profiled per run" setting for this run only.

    Counts and ranges come from one aggregate query: over the whole table, or
    over a TOP (N) sample when the table is bigger than the limit on the
    Settings page. Common values and range charts come from a smaller sample.
    Read-only, with NOLOCK so it never blocks other work.

    One table never stops the run: a column the server can't read (a view that
    casts bad text to a number, say) is found and left out, the rest of the
    table is profiled, and a table that fails as a whole is marked failed while
    the run moves on. Returns {key: error} for tables that failed; raises
    PacingStopped when the run stops early (time-out, run limit, Stop button).
    """
    from .pacing import Pacer, PacingStopped, _is_timeout, set_queue_state, settings

    s = settings(cat)
    limit = int(max_tables) if max_tables else int(s["sql_max_tables_per_run"])
    pacer = pacer or Pacer(cat, src.name)
    by_db: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        asset = cat.get_asset(key)
        if asset and asset["source"] == src.name and asset["kind"] in ("table", "view"):
            by_db.setdefault(asset["attrs"]["database"], []).append(asset)

    errors: dict[str, str] = {}
    done = 0

    def failed(asset: dict[str, Any], e: BaseException | str) -> None:
        text = e if isinstance(e, str) else f"{type(e).__name__}: {e}" if isinstance(e, UnicodeError) else str(e)
        errors[asset["key"]] = text
        set_queue_state(cat, [asset["key"]], "failed", text[:500])
        progress(f"    failed: {text}")

    try:
        for database, assets in by_db.items():
            try:
                conn = (connector or connect)(src, database)
            except Exception as e:  # this database can't be reached: its tables fail, the others carry on
                progress(f"  could not connect to {database}: {e}")
                for asset in assets:
                    failed(asset, f"Could not connect to {database}: {e}")
                continue
            try:
                cur = conn.cursor()
                for asset in assets:
                    if done >= limit:
                        raise PacingStopped(f"Reached the limit of {limit} tables for one run. "
                                            "The rest stay in the queue for next time.")
                    try:
                        _profile_one(cat, pacer, cur, asset, database, s, progress)
                    except PacingStopped:
                        raise
                    except Exception as e:
                        failed(asset, e)
                        if _is_timeout(e) and s["sql_stop_on_timeout"]:
                            raise PacingStopped(f"{asset['name']} timed out, so the run stopped to go easy on the "
                                                "server. Raise the time limit or sample size on the Settings page, "
                                                "or skip this table.") from e
                        continue
                    done += 1
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    finally:
        cat.rebuild_search()
    return errors


# How many extra queries one table may take to find the columns the server can't read.
MAX_RETRY_QUERIES = 40


def _profile_one(cat: Catalogue, pacer, cur, asset: dict[str, Any], database: str, s: dict[str, Any],
                 progress) -> None:
    """Profile one table or view and save it. Raises when nothing at all could be read."""
    import pandas as pd

    from .pacing import set_queue_state
    from .profiling import combine_sql_profile, sql_aggregates, sql_sample

    a = asset["attrs"]
    rows = a.get("row_count") or 0
    sample = int(s["sql_sample_rows"]) if asset["kind"] == "view" or rows > int(s["sql_sample_over_rows"]) else None
    progress(f"  profiling {database}.{asset['name']}" + (f" (sample of {sample:,} rows)" if sample else ""))
    columns = [dict(c) for c in cat.columns(asset["key"])]
    schema, obj, top_rows = a["schema"], a["object"], int(s["sql_top_values_rows"])
    problems: dict[str, str] = {}
    budget = [MAX_RETRY_QUERIES]

    def query(sql: str, purpose: str):
        return pacer.query(cur, sql, purpose=f"profile {asset['name']}: {purpose}", database=database)

    def agg_of(part: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
        names, got = query(sql_aggregates(schema, obj, part, sample),
                           "counts and ranges" if len(part) == len(columns) else f"counts and ranges, {_cols(part)}")
        return dict(zip(names, got[0]))

    indexed = list(enumerate(columns))
    try:
        agg = agg_of(indexed)
    except Exception as e:
        if _stops(e):
            raise
        # Is it the table, or some columns? A plain row count answers that in one query.
        names, got = query(sql_aggregates(schema, obj, [], sample), "row count only")
        agg = dict(zip(names, got[0]))
        progress(f"    some columns can't be read ({_first_line(e)}); finding which")
        for part in _split_until_read(indexed, agg_of, problems, budget, lambda p: p[1]["name"], e):
            agg.update(part)

    good = [c["name"] for c in columns if c["name"] not in problems]
    try:
        names, got = query(sql_sample(schema, obj, top_rows), "common values")
        frame = pd.DataFrame.from_records(got, columns=names)
    except Exception as e:
        if _stops(e):
            raise
        if not problems:
            progress(f"    some columns can't be read for common values ({_first_line(e)}); finding which")

        def sample_of(part: list[str]) -> pd.DataFrame:
            names, got = query(sql_sample(schema, obj, top_rows, part), f"common values, {_cols(part)}")
            return pd.DataFrame.from_records(got, columns=names)

        whole = len(good) == len(columns)  # SELECT * already failed for exactly these columns
        parts = _split_until_read(good, sample_of, problems, budget, lambda n: n, e if whole else None) if good else []
        frame = pd.concat([p.reset_index(drop=True) for p in parts], axis=1) if parts else pd.DataFrame()

    profile = combine_sql_profile(columns, agg, frame, problems)
    method = (f"sample of {sample:,} rows for counts and ranges" if sample else
              "whole table for counts and ranges") + f"; common values and charts from first {len(frame):,} rows"
    note = None
    if problems:
        method += f"; {len(problems)} column(s) could not be read"
        note = "Could not read " + "; ".join(f"{c}: {_first_line(m)}" for c, m in problems.items())
        progress(f"    {len(problems)} column(s) left out: {', '.join(problems)}")
    cat.save_profile(asset["key"], profile, method)
    set_queue_state(cat, [asset["key"]], "done", note[:500] if note else None)


def _split_until_read(items: list, read, problems: dict[str, str], budget: list[int], name_of, first_error=None):
    """Read `items` in halves, and halves of halves, until the ones that fail on their own are found.
    Each failing item goes into `problems` with the server's message. Returns what the reads gave.
    `first_error` says `items` as a whole already failed, so it isn't sent again."""
    out = []
    todo = [items]
    errors = {id(items): first_error} if first_error is not None else {}
    while todo:
        part = todo.pop(0)
        if len(part) == 1 and id(part) in errors:  # already failed on its own
            problems[name_of(part[0])] = _first_line(errors[id(part)])
            continue
        if id(part) not in errors:
            if budget[0] <= 0:
                for item in part:
                    problems[name_of(item)] = "not read: too many columns failed, so pdcat stopped looking"
                continue
            budget[0] -= 1
            try:
                out.append(read(part))
                continue
            except Exception as e:
                if _stops(e):
                    raise
                if len(part) == 1:
                    problems[name_of(part[0])] = _first_line(e)
                    continue
        half = len(part) // 2
        todo += [part[:half], part[half:]]
    return out


def _stops(e: BaseException) -> bool:
    """Errors that end this table's attempt rather than pointing at a column: the Stop button, run limits and
    time-outs (a time-out on a slow server is not fixed by sending more queries)."""
    from .pacing import PacingStopped, _is_timeout
    return isinstance(e, PacingStopped) or _is_timeout(e)


def _first_line(e) -> str:
    text = f"{type(e).__name__}: {e}" if isinstance(e, UnicodeError) else str(e)
    return text.strip().splitlines()[0][:300] if text.strip() else type(e).__name__


def _cols(part: list) -> str:
    names = [p[1]["name"] if isinstance(p, tuple) else p for p in part]
    return names[0] if len(names) == 1 else f"{len(names)} columns from {names[0]}"


def run_queue(cat: Catalogue, sources: list[SqlServerSource], progress=print, connector=None,
              max_tables: int | None = None) -> dict[str, Any]:
    """Profile every approved table in the queue, up to `max_tables` (default: the setting) in this run.
    Returns {"done", "failed", "stopped"}."""
    from .pacing import Pacer, PacingStopped, queue
    cat.set_setting("sql_stop_requested", False, quiet=True)
    approved = [q["asset_key"] for q in queue(cat, ("approved",))]
    result: dict[str, Any] = {"done": 0, "failed": 0, "stopped": None, "left": 0, "skipped": 0}
    by_name = {s.name.lower(): s for s in sources}
    for name in sorted({k.split("/")[2] for k in approved if k.startswith("sql://")}):
        src = by_name.get(name)
        mine = [k for k in approved if k.startswith(f"sql://{name}/")]
        if src is None:
            progress(f"No [[sqlserver]] source called {name} in your config; skipping {len(mine)} table(s).")
            result["skipped"] += len(mine)
            continue
        try:
            before = cat.conn.execute("SELECT COUNT(*) FROM profile_queue WHERE state = 'done'").fetchone()[0]
            left = max_tables - result["done"] if max_tables else None
            if left is not None and left <= 0:
                result["stopped"] = (f"Reached the limit of {max_tables} tables for one run. "
                                     "The rest stay in the queue for next time.")
                break
            errors = profile_tables(cat, src, mine, progress, connector, Pacer(cat, src.name), max_tables=left)
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
