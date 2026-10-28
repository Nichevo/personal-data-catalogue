"""An export OpenMetadata can load: your SQL Server databases, schemas, tables,
views and columns with your descriptions, the metric glossary and company-wide
keys as a glossary, and lineage between tables through the scripts that read
one and write another.

The zip holds entities.json (OpenMetadata "create" requests, in the order to
send them) and load_openmetadata.py, which sends them to your OpenMetadata
server's API:

    set OPENMETADATA_TOKEN=<bot token>
    python load_openmetadata.py https://openmetadata.example:8585

Data files, reports and code are not exported: OpenMetadata models those
through its own connectors.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

from .store import Catalogue

TYPES = {"int": "INT", "bigint": "BIGINT", "smallint": "SMALLINT", "tinyint": "TINYINT", "bit": "BOOLEAN",
         "decimal": "DECIMAL", "numeric": "NUMERIC", "money": "DECIMAL", "smallmoney": "DECIMAL", "float": "FLOAT",
         "real": "FLOAT", "date": "DATE", "datetime": "DATETIME", "datetime2": "DATETIME", "smalldatetime": "DATETIME",
         "datetimeoffset": "TIMESTAMPZ", "time": "TIME", "char": "CHAR", "nchar": "CHAR", "varchar": "VARCHAR",
         "nvarchar": "VARCHAR", "text": "TEXT", "ntext": "TEXT", "uniqueidentifier": "UUID", "binary": "BINARY",
         "varbinary": "VARBINARY", "image": "BLOB", "xml": "XML", "sql_variant": "VARIANT", "geography": "GEOGRAPHY",
         "geometry": "GEOMETRY", "timestamp": "BINARY", "rowversion": "BINARY", "hierarchyid": "UNKNOWN"}
SIZED = {"CHAR", "VARCHAR", "BINARY", "VARBINARY"}


def column_type(data_type: str | None) -> dict[str, Any]:
    t = (data_type or "").lower()
    m = re.match(r"(\w+)\s*(?:\((max|\d+)(?:,\s*\d+)?\))?", t)
    base, size = (m.group(1), m.group(2)) if m else (t, None)
    om = TYPES.get(base, "UNKNOWN")
    if om in SIZED and size == "max":
        om = "TEXT" if "char" in base else "BLOB"
    out = {"dataType": om, "dataTypeDisplay": data_type or "unknown"}
    if om in SIZED:
        out["dataLength"] = int(size) if size and size.isdigit() else 1
    return out


def _name(s: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", s)[:120] or "unnamed"


def entities(cat: Catalogue) -> list[dict[str, Any]]:
    from .curation import all_item_info
    info = all_item_info(cat)
    col_info: dict[str, dict[str, str]] = {}
    for r in cat.conn.execute("SELECT asset_key, column_name, description FROM column_info WHERE description != ''"):
        col_info.setdefault(r["asset_key"], {})[r["column_name"]] = r["description"]
    out: list[dict[str, Any]] = []
    services, dbs, schemas = set(), set(), set()
    fqn: dict[str, str] = {}
    for r in cat.conn.execute("SELECT * FROM assets WHERE kind IN ('table', 'view') AND status = 'active' AND "
                              "key LIKE 'sql://%' ORDER BY key"):
        a = json.loads(r["attrs"])
        service, db, schema = _name(r["source"]), _name(a.get("database", "")), _name(a.get("schema", "dbo"))
        if service not in services:
            services.add(service)
            out.append({"type": "databaseService", "body": {"name": service, "serviceType": "Mssql",
                                                            "description": "From the personal data catalogue"}})
        if (service, db) not in dbs:
            dbs.add((service, db))
            out.append({"type": "database", "body": {"name": db, "service": service}})
        if (service, db, schema) not in schemas:
            schemas.add((service, db, schema))
            out.append({"type": "databaseSchema", "body": {"name": schema, "database": f"{service}.{db}"}})
        i = info.get(r["key"], {})
        desc = "\n\n".join(x for x in (i.get("purpose"), r["description"],
                                       f"One row per {i['grain']}." if i.get("grain") else "",
                                       f"Refreshed {i['refresh']}." if i.get("refresh") else "") if x)
        cols = []
        for c in cat.columns(r["key"]):
            col = {"name": _name(c["name"]), **column_type(c["data_type"])}
            d = col_info.get(r["key"], {}).get(c["name"]) or c["description"]
            if d:
                col["description"] = d
            cols.append(col)
        body = {"name": _name(a.get("object", r["name"])), "databaseSchema": f"{service}.{db}.{schema}",
                "tableType": "View" if r["kind"] == "view" else "Regular", "columns": cols or
                [{"name": "unknown", "dataType": "UNKNOWN", "dataTypeDisplay": "unknown"}]}
        if desc:
            body["description"] = desc
        if r["kind"] == "view" and a.get("definition"):
            body["schemaDefinition"] = a["definition"]
        fqn[r["key"]] = f"{service}.{db}.{schema}.{body['name']}"
        out.append({"type": "table", "body": body})
    # Glossary: metrics and company-wide keys.
    from .keys import list_keys
    from .knowledge import list_metrics
    terms = [{"name": _name(m["name"]), "description": "\n\n".join(x for x in (m["definition"], m["formula"] and
                                                                              f"Formula: {m['formula']}",
                                                                              m["caveats"]) if x) or m["name"]}
             for m in list_metrics(cat)]
    terms += [{"name": _name(k["name"]), "description": (k["description"] or f"Company-wide key {k['name']}")
               + (f"\n\nAlso called: {', '.join(k['aliases'])}" if k["aliases"] else ""),
               "synonyms": k["aliases"]} for k in list_keys(cat)]
    if terms:
        out.append({"type": "glossary", "body": {"name": "Data catalogue", "description":
                                                 "Metrics and company-wide keys from the personal data catalogue"}})
        out += [{"type": "glossaryTerm", "body": {**t, "glossary": "Data catalogue"}} for t in terms]
    # Lineage: a script that reads A and writes B gives A -> B.
    edges = set()
    for r in cat.conn.execute("SELECT r.src_key AS script, r.dst_key AS src, w.dst_key AS dst, a.name AS name FROM "
                              "links r JOIN links w ON w.src_key = r.src_key AND w.relation = 'writes' JOIN assets a ON "
                              "a.key = r.src_key WHERE r.relation = 'reads'"):
        if r["src"] in fqn and r["dst"] in fqn and r["src"] != r["dst"] and (r["src"], r["dst"]) not in edges:
            edges.add((r["src"], r["dst"]))
            out.append({"type": "lineage", "body": {"from": fqn[r["src"]], "to": fqn[r["dst"]],
                                                    "description": f"via {r['name']}"}})
    return out


LOADER = r'''"""Load entities.json into OpenMetadata.

    set OPENMETADATA_TOKEN=<bot JWT token>
    python load_openmetadata.py https://your-openmetadata:8585 [--dry-run]

The token can also go after the address, but then it is kept in your command
history. Creates or updates (PUT) each entity, so it is safe to run again.
Needs the requests package (pip install requests).
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import requests

PATHS = {"databaseService": "services/databaseServices", "database": "databases",
         "databaseSchema": "databaseSchemas", "table": "tables", "glossary": "glossaries",
         "glossaryTerm": "glossaryTerms"}


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    token = args[1] if len(args) > 1 else os.environ.get("OPENMETADATA_TOKEN", "")
    if not args or not (token or dry):
        sys.exit(__doc__)
    host = args[0].rstrip("/")
    api = f"{host}/api/v1"
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    items = json.loads((Path(__file__).parent / "entities.json").read_text(encoding="utf-8"))
    done = failed = 0
    for item in items:
        kind, body = item["type"], item["body"]
        if dry:
            print(kind, body.get("name") or f"{body.get('from')} -> {body.get('to')}")
            continue
        if kind == "lineage":
            ids = []
            for name in (body["from"], body["to"]):
                r = s.get(f"{api}/tables/name/{quote(name, safe='')}", timeout=60)
                ids.append(r.json()["id"] if r.ok else None)
            if None in ids:
                failed += 1
                continue
            r = s.put(f"{api}/lineage", json={"edge": {
                "fromEntity": {"id": ids[0], "type": "table"}, "toEntity": {"id": ids[1], "type": "table"},
                "lineageDetails": {"description": body.get("description", "")}}}, timeout=60)
        else:
            r = s.put(f"{api}/{PATHS[kind]}", json=body, timeout=60)
        if r.ok:
            done += 1
        else:
            failed += 1
            print(f"{kind} {body.get('name', '')}: {r.status_code} {r.text[:200]}")
    print(f"{done} sent, {failed} failed")


if __name__ == "__main__":
    main()
'''


def to_zip(cat: Catalogue) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("entities.json", json.dumps(entities(cat), indent=1, ensure_ascii=False))
        z.writestr("load_openmetadata.py", LOADER)
        z.writestr("README.txt", __doc__)
    return buf.getvalue()
