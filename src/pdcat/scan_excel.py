"""Excel workbooks that get their data from somewhere: Power Query, Microsoft
Query (ODBC and OLE DB connections), pivot tables, and SQL in VBA macros.

A workbook is read as the zip of XML files it is; Excel isn't needed. For
each workbook the catalogue keeps its sheets (with their header row), its
queries and connections, which sheet each one loads into, its pivot tables
and their fields, and the SQL found in its macros. Links go to the SQL
Server tables and files these read, so a table's page lists the workbooks
that use it.

Passwords in connection strings are never stored.

Macros are read from the workbook's vbaProject.bin. If the optional olefile
package is installed (pip install olefile) each module is read exactly;
otherwise a best-effort search of the file is used, which can miss modules.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from . import mquery
from .sqlrefs import extract_refs, looks_like_sql

READER_VERSION = 1
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm", ".xltx", ".xltm")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
CONN_TYPES = {"1": "ODBC", "2": "DAO", "3": "file", "4": "web", "5": "OLE DB", "6": "text", "7": "ADO", "8": "DSP"}


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Relationship id -> target part, for one part."""
    folder, name = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", name + ".rels")
    if rels_name not in z.namelist():
        return {}
    out = {}
    for r in ET.fromstring(z.read(rels_name)).findall("rel:Relationship", NS):
        target = r.get("Target", "")
        out[r.get("Id")] = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
            posixpath.join(folder, target))
    return out


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    out = []
    for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
        out.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    return out


def _first_row(z: zipfile.ZipFile, part: str, strings: list[str]) -> tuple[list[str], str]:
    """The header row and used range of a sheet, reading no further than the first row."""
    header, dims = [], ""
    with z.open(part) as f:
        for event, el in ET.iterparse(f, events=("end",)):
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "dimension":
                dims = el.get("ref", "")
            elif tag == "row":
                for c in el.findall("m:c", NS):
                    v = c.find("m:v", NS)
                    if c.get("t") == "s" and v is not None and v.text and v.text.isdigit():
                        idx = int(v.text)
                        header.append(strings[idx] if idx < len(strings) else "")
                    elif c.get("t") == "inlineStr":
                        header.append("".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t")))
                    elif v is not None:
                        header.append(v.text or "")
                break
    return [h for h in header if h], dims


def _conn_parts(connection: str) -> dict[str, str]:
    parts = {}
    for bit in connection.split(";"):
        k, _, v = bit.partition("=")
        if k.strip():
            parts[k.strip().lower()] = v.strip().strip('"')
    return parts


def read_connections(z: zipfile.ZipFile) -> list[dict[str, Any]]:
    if "xl/connections.xml" not in z.namelist():
        return []
    out = []
    for c in ET.fromstring(z.read("xl/connections.xml")).findall("m:connection", NS):
        db = c.find("m:dbPr", NS)
        text = c.find("m:textPr", NS)
        web = c.find("m:webPr", NS)
        conn = mquery.redact(db.get("connection", "")) if db is not None else ""
        parts = _conn_parts(conn)
        kind = CONN_TYPES.get(c.get("type", ""), c.get("type", ""))
        if "microsoft.mashup.oledb" in conn.lower():
            kind = "Power Query"
        item = {"id": c.get("id"), "name": c.get("name", ""), "type": kind, "description": c.get("description", ""),
                "connection": conn, "command": db.get("command", "") if db is not None else "",
                "server": parts.get("server") or parts.get("data source", ""),
                "database": parts.get("database") or parts.get("initial catalog", ""),
                "dsn": parts.get("dsn", "")}
        if kind == "Power Query":
            item["query"] = parts.get("location", "")
            item["server"] = item["database"] = ""
        if text is not None:
            item["file"] = text.get("sourceFile", "")
        if web is not None:
            item["url"] = web.get("url", "")
        out.append({k: v for k, v in item.items() if v})
    return out


def read_pivots(z: zipfile.ZipFile, connections: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for name in sorted(n for n in z.namelist() if re.match(r"xl/pivotCache/pivotCacheDefinition\d+\.xml$", n)):
        root = ET.fromstring(z.read(name))
        src = root.find("m:cacheSource", NS)
        item: dict[str, Any] = {"fields": [f.get("name", "") for f in root.iter(f"{{{NS['m']}}}cacheField")]}
        if src is not None:
            if src.get("connectionId"):
                item["source"] = f"connection “{connections.get(src.get('connectionId'), src.get('connectionId'))}”"
            ws = src.find("m:worksheetSource", NS)
            if ws is not None:
                item["source"] = ws.get("name") or f"{ws.get('sheet', '')}!{ws.get('ref', '')}"
            if src.get("type") == "external" and "source" not in item:
                item["source"] = "an external source"
        out.append(item)
    return out


def read_sheets(z: zipfile.ZipFile, connections: dict[str, str]) -> list[dict[str, Any]]:
    """Sheets in order, with their header row and the queries that load into them."""
    if "xl/workbook.xml" not in z.namelist():
        return []
    wb_rels = _rels(z, "xl/workbook.xml")
    strings = _shared_strings(z)
    sheets = []
    for s in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{NS['m']}}}sheet"):
        part = wb_rels.get(s.get(f"{{{NS['r']}}}id"), "")
        sheet: dict[str, Any] = {"name": s.get("name", ""), "hidden": s.get("state") in ("hidden", "veryHidden")}
        if part in z.namelist():
            try:
                sheet["header"], sheet["range"] = _first_row(z, part, strings)
            except ET.ParseError:
                pass
            loads = []
            for target in _rels(z, part).values():
                if "/tables/" in target and target in z.namelist():
                    for qt in _rels(z, target).values():
                        loads += _query_table_connection(z, qt, connections)
                elif "/queryTables/" in target:
                    loads += _query_table_connection(z, target, connections)
            if loads:
                sheet["loaded_from"] = sorted(set(loads))
        sheets.append(sheet)
    return sheets


def _query_table_connection(z: zipfile.ZipFile, part: str, connections: dict[str, str]) -> list[str]:
    if part not in z.namelist():
        return []
    cid = ET.fromstring(z.read(part)).get("connectionId")
    return [connections.get(cid, cid)] if cid else []


# ---- VBA ----------------------------------------------------------------------------------

def decompress_vba(data: bytes, start: int = 0) -> bytes:
    """MS-OVBA decompression of one compressed container starting at `start`."""
    if data[start] != 1:
        raise ValueError("not a compressed container")
    out = bytearray()
    pos = start + 1
    while pos + 2 <= len(data):
        header = int.from_bytes(data[pos:pos + 2], "little")
        if (header >> 12) & 0x07 != 0b011:
            break
        chunk_end = min(pos + (header & 0x0FFF) + 3, len(data))
        pos += 2
        chunk_start = len(out)
        if not header >> 15:  # stored uncompressed
            out += data[pos:pos + 4096]
            pos += 4096
            continue
        while pos < chunk_end:
            flags = data[pos]
            pos += 1
            for bit in range(8):
                if pos >= chunk_end:
                    break
                if not (flags >> bit) & 1:
                    out.append(data[pos])
                    pos += 1
                    continue
                if pos + 2 > chunk_end:
                    pos = chunk_end
                    break
                token = int.from_bytes(data[pos:pos + 2], "little")
                pos += 2
                bit_count = max((len(out) - chunk_start - 1).bit_length(), 4)
                length_mask = 0xFFFF >> bit_count
                length = (token & length_mask) + 3
                offset = (token >> (16 - bit_count)) + 1
                if offset > len(out) - chunk_start:
                    raise ValueError("bad copy token")
                for _ in range(length):
                    out.append(out[-offset])
    return bytes(out)


def _modules_in(data: bytes) -> list[str]:
    """Module source code found in a stream (or the whole file): each compressed container that
    decompresses to text starting with "Attribute VB_"."""
    found = []
    for m in re.finditer(rb"\x01.[\xb0-\xbf]", data, re.S):
        try:
            text = decompress_vba(data, m.start())
        except (ValueError, IndexError):
            continue
        if text.startswith(b"Attribute VB_"):
            found.append(text.decode("cp1252", errors="replace"))
    return found


def vba_modules(blob: bytes) -> tuple[list[dict[str, str]], bool]:
    """[{"module", "code"}] from a vbaProject.bin, and whether the exact reader (olefile) was used."""
    modules: list[dict[str, str]] = []
    try:
        import olefile  # optional
    except ImportError:
        olefile = None
    if olefile is not None:
        try:
            ole = olefile.OleFileIO(blob)
            for entry in ole.listdir():
                if len(entry) >= 2 and entry[-2].upper() == "VBA" and entry[-1] not in ("dir", "_VBA_PROJECT") \
                        and not entry[-1].startswith("__SRP"):
                    for code in _modules_in(ole.openstream(entry).read()):
                        modules.append({"module": entry[-1], "code": code})
            return modules, True
        except Exception:
            modules = []
    for code in _modules_in(blob):
        name = re.search(r'Attribute VB_Name = "([^"]+)"', code)
        modules.append({"module": name.group(1) if name else "?", "code": code})
    return modules, False


def sql_in_vba(code: str) -> list[str]:
    """SQL text built in VBA: string literals joined with &, across _ line continuations and
    `sql = sql & "..."` lines."""
    text = re.sub(r"\s_\r?\n\s*", " ", code)
    built: dict[str, str] = {}
    loose = []
    for line in text.splitlines():
        line = re.sub(r"(^|\s)'.*$", "", line) if '"' not in line else line
        literals = [s[1:-1].replace('""', '"') for s in re.findall(r'"(?:[^"]|"")*"', line)]
        if not literals:
            continue
        joined = " ".join(literals)
        m = re.match(r"\s*(?:Let\s+)?(\w+)\s*=\s*(\w+)?", line)
        if m and m.group(2) and m.group(2).lower() == m.group(1).lower():
            built[m.group(1).lower()] = built.get(m.group(1).lower(), "") + " " + joined
        elif m:
            built[m.group(1).lower()] = joined
        else:
            loose.append(joined)
    out = []
    for sql in [*built.values(), *loose]:
        sql = re.sub(r"\s+", " ", sql).strip()
        if looks_like_sql(sql) and sql not in out:
            out.append(sql)
    return out


# ---- whole workbooks ------------------------------------------------------------------------

def read_workbook(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"format": path.suffix.lower().lstrip("."), "sheets": [], "queries": [],
                           "connections": [], "pivots": [], "vba_sql": [], "not_read": []}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        out["connections"] = read_connections(z)
        by_id = {c["id"]: c["name"] for c in out["connections"] if c.get("id")}
        out["sheets"] = read_sheets(z, by_id)
        out["pivots"] = read_pivots(z, by_id)
        for n in sorted(x for x in names if re.match(r"customXml/item\d+\.xml$", x)):
            blob = mquery.mashup_from_custom_xml(z.read(n).decode("utf-8", errors="replace").replace("\x00", ""))
            if not blob:
                try:
                    blob = mquery.mashup_from_custom_xml(z.read(n).decode("utf-16"))
                except UnicodeDecodeError:
                    blob = None
            if blob:
                try:
                    section = mquery.package_from_mashup(blob).get("Formulas/Section1.m")
                    if section:
                        out["queries"] = mquery.read_queries(section)
                except (ValueError, zipfile.BadZipFile):
                    out["not_read"].append("The Power Query queries couldn't be unpacked.")
                break
        if "xl/vbaProject.bin" in names:
            modules, exact = vba_modules(z.read("xl/vbaProject.bin"))
            out["macros"] = [m["module"] for m in modules]
            for m in modules:
                for sql in sql_in_vba(m["code"]):
                    out["vba_sql"].append({"module": m["module"], "sql": sql})
            if not exact:
                out["not_read"].append("Macros were searched without olefile, so some may be missed. "
                                       "pip install olefile to read them exactly.")
    return out


def has_data_sources(info: dict[str, Any]) -> bool:
    return bool(info["queries"] or info["connections"] or info["vba_sql"]
                or any(p.get("source") for p in info["pivots"]))


def find_workbooks(root: Path, recursive: bool = True, skip: set[str] | None = None) -> list[Path]:
    pattern = root.rglob("*") if recursive else root.glob("*")
    return sorted(p for p in pattern if p.suffix.lower() in WORKBOOK_SUFFIXES and p.is_file()
                  and not p.name.startswith("~$")
                  and not (skip and any(part in skip for part in p.relative_to(root).parts[:-1])))


def ingest_workbook(cat, path: Path, key: str, name: str, source: str, parent: str) -> str | None:
    from datetime import datetime, timezone

    from .duplicates import fingerprint
    from .scan_powerbi import link_queries
    st = path.stat()
    previous = cat.get_asset(key)
    if previous and previous["attrs"].get("size_bytes") == st.st_size and previous["attrs"].get("mtime") == \
            st.st_mtime and previous["attrs"].get("reader") == READER_VERSION:
        info = previous["attrs"]
    else:
        try:
            info = read_workbook(path)
        except Exception as e:  # damaged or password-protected (encrypted workbooks aren't zips)
            info = {"format": path.suffix.lower().lstrip("."), "sheets": [], "queries": [], "connections": [],
                    "pivots": [], "vba_sql": [], "not_read": [],
                    "read_error": "Encrypted or password-protected" if isinstance(e, zipfile.BadZipFile)
                    else f"{type(e).__name__}: {e}"}
        info.update({"path": str(path), "size_bytes": st.st_size, "mtime": st.st_mtime, "reader": READER_VERSION,
                     "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                     "checksum": fingerprint(path)})
    bits = [f"{len(info['sheets'])} sheets"]
    for label, n in (("Power Query queries", len([q for q in info["queries"] if "parameter" not in q])),
                     ("connections", len([c for c in info["connections"] if c.get("type") != "Power Query"])),
                     ("pivot tables", len(info["pivots"])), ("SQL statements in macros", len(info["vba_sql"]))):
        if n:
            bits.append(f"{n} {label}")
    cat.upsert_asset(key, "workbook", name, source, parent=parent, description="Excel workbook: " + ", ".join(bits),
                     attrs=info)
    cols = []
    for s in info["sheets"]:
        cols += [{"name": h if len(info["sheets"]) == 1 else f"{s['name']}.{h}", "ordinal": len(cols)}
                 for h in s.get("header", [])]
    cat.set_columns(key, list({c["name"]: c for c in cols}.values()))
    link_queries(cat, key, source, info["queries"])
    for c in info["connections"]:
        if c.get("command") and looks_like_sql(c["command"]):
            for r in extract_refs(c["command"]):
                db = c.get("database")
                ref = f"{db}.{r.name}" if db and r.name.count(".") <= 1 else r.name
                cat.add_link(key, r.relation, f"table:{ref.lower()}", source, hint=db or None,
                             how=f"{c['type']} connection “{c['name']}”")
        if c.get("file"):
            cat.add_link(key, "reads", f"file:{c['file']}", source, how=f"text connection “{c['name']}”")
    for v in info["vba_sql"]:
        for r in extract_refs(v["sql"]):
            cat.add_link(key, r.relation, f"table:{r.name}", source, how=f"SQL in macro {v['module']}")
    return info.get("read_error")
