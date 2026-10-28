"""Access databases (.accdb, .mdb) found in data folders: their saved queries (name and SQL, which go in
the SQL query library), linked tables (what each points to: a SQL Server table, another Access file,
a spreadsheet; connection passwords removed) and local tables with their columns and row counts.

The file is opened read-only and no query is run. On Windows it uses the Access Database Engine
through DAO, from the optional pywin32 package (`pip install pywin32`); the engine's 32/64-bit
version must match Python's. Anywhere else, or without them, the file is still listed, with a note
saying what's needed to read inside it.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mquery import redact
from .sqlrefs import extract_refs
from .store import Catalogue

SUFFIXES = (".accdb", ".mdb")
ATTACHED = 0x40000000       # dbAttachedTable: linked to another Access file or a spreadsheet
ATTACHED_ODBC = 0x20000000  # dbAttachedODBC: linked to SQL Server through ODBC
SYSTEM = 0x80000002         # dbSystemObject
READER_VERSION = 1
FIELD_TYPES = {1: "yes/no", 2: "byte", 3: "integer", 4: "long", 5: "currency", 6: "single", 7: "double", 8: "date",
               9: "binary", 10: "text", 11: "OLE object", 12: "memo", 15: "GUID", 16: "big integer", 20: "decimal"}


def engine_status() -> tuple[bool, str]:
    """Whether Access files can be read here, and if not, what to install."""
    if sys.platform != "win32":
        return False, "Access databases can only be read on Windows."
    try:
        import win32com.client
    except ImportError:
        return False, "Install pywin32 to read Access databases: pip install pywin32"
    for prog in ("DAO.DBEngine.120", "DAO.DBEngine.36"):
        try:
            import win32com.client
            win32com.client.Dispatch(prog)
            return True, f"Using {prog}"
        except Exception:
            continue
    bits = "64-bit" if sys.maxsize > 2**32 else "32-bit"
    return False, (f"The Access Database Engine isn't installed for {bits} Python. Install the {bits} "
                   "Microsoft Access Database Engine (2016 Redistributable), the same bitness as Python.")


def read_access(path: Path) -> dict[str, Any]:
    import win32com.client
    engine = None
    for prog in ("DAO.DBEngine.120", "DAO.DBEngine.36"):
        try:
            engine = win32com.client.Dispatch(prog)
            break
        except Exception:
            continue
    if engine is None:
        raise RuntimeError(engine_status()[1])
    db = engine.OpenDatabase(str(path), False, True)  # not exclusive, read-only
    try:
        queries, linked, tables = [], [], []
        for q in db.QueryDefs:
            if q.Name.startswith("~"):  # hidden queries behind forms and reports
                continue
            queries.append({"name": q.Name, "sql": str(q.SQL or "").strip()})
        for t in db.TableDefs:
            if t.Name.startswith("MSys") or (t.Attributes & SYSTEM) == SYSTEM:
                continue
            fields = [{"name": f.Name, "data_type": FIELD_TYPES.get(f.Type, str(f.Type)), "ordinal": i + 1}
                      for i, f in enumerate(t.Fields)]
            connect = str(t.Connect or "")
            if connect:
                linked.append({"name": t.Name, "source_table": str(t.SourceTableName or ""),
                               "connect": redact(connect), "odbc": bool(t.Attributes & ATTACHED_ODBC),
                               "columns": fields})
            else:
                try:
                    count = int(t.RecordCount)
                except Exception:
                    count = None
                tables.append({"name": t.Name, "rows": count, "columns": fields})
        return {"queries": queries, "linked": linked, "tables": tables}
    finally:
        db.Close()


def link_target(link: dict[str, Any]) -> tuple[str, str] | None:
    """(relation ref, how) for what a linked table points at."""
    c = link["connect"]
    if link["odbc"] or c.upper().startswith("ODBC"):
        db = re.search(r"(?i)\bDATABASE=([^;]+)", c)
        table = link["source_table"].replace("[", "").replace("]", "")
        name = f"{db.group(1)}.{table}" if db and table.count(".") < 2 else table
        return f"table:{name.lower()}", "Access linked table (ODBC)"
    m = re.search(r"(?i)DATABASE=([^;]+)", c)
    if m:
        return f"file:{m.group(1)}", f"Access linked table ({c.split(';')[0] or 'Access'})"
    return None


def ingest_access(cat: Catalogue, path: Path, key: str, name: str, source: str, parent: str) -> str | None:
    """Catalogue one Access file. Returns an error message when it couldn't be read inside."""
    st = path.stat()
    previous = cat.get_asset(key)
    info: dict[str, Any]
    error = None
    if previous and previous["attrs"].get("mtime") == st.st_mtime and previous["attrs"].get("reader") == READER_VERSION \
            and not previous["attrs"].get("read_error"):
        info = previous["attrs"]
    else:
        ok, why = engine_status()
        if not ok:
            info = {"queries": [], "linked": [], "tables": [], "read_error": why}
        else:
            try:
                info = read_access(path)
            except Exception as e:
                info = {"queries": [], "linked": [], "tables": [], "read_error": f"{type(e).__name__}: {e}"}
        info.update({"path": str(path), "size_bytes": st.st_size, "mtime": st.st_mtime, "reader": READER_VERSION,
                     "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
    error = info.get("read_error")
    bits = [f"{len(info['queries'])} saved queries", f"{len(info['linked'])} linked tables",
            f"{len(info['tables'])} local tables"]
    cat.upsert_asset(key, "access_db", name, source, parent=parent,
                     description="Access database: " + ", ".join(bits) if not error else "Access database (not read)",
                     attrs=info)
    here = f"{source}/{path.stem}"  # names in its own queries mean its own tables first
    for q in info["queries"]:
        for r in extract_refs(q["sql"]):
            cat.add_link(key, r.relation, f"table:{r.name}", source, hint=here, evidence=f"query {q['name']}",
                         how="Access saved query")
    for link in info["linked"]:
        target = link_target(link)
        if target:
            cat.add_link(key, "reads", target[0], source, evidence=f"linked table {link['name']}", how=target[1])
    for t in info["tables"]:
        tkey = f"{key}/table/{t['name']}".lower()
        cat.upsert_asset(tkey, "table", t["name"], source, parent=key,
                         description=f"Local table in {path.name}",
                         attrs={"row_count": t["rows"], "object": t["name"].lower(), "schema": "", "database": path.stem,
                                "in_access": key})
        cat.set_columns(tkey, t["columns"])
    return error
