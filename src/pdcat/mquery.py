"""Power Query (M) as used by Power BI and Excel: unpacking the DataMashup that
holds the queries, splitting it into queries, and reading what each query
connects to.

From each query we read the SQL Server server and database
(Sql.Database("server", "db")), the tables it navigates to
(Source{[Schema="dbo", Item="Sales"]}[Data]), native SQL
(Sql.Database(..., [Query="SELECT ..."]), Value.NativeQuery), ODBC and OLE DB
queries, and files and folders (File.Contents, Folder.Files, SharePoint,
web addresses). Parameters (a query that is just "text" meta [...]) are
substituted where other queries use them.
"""
from __future__ import annotations

import base64
import io
import re
import struct
import zipfile
from typing import Any

from .sqlrefs import extract_refs, looks_like_sql

M_STRING = r'"(?:[^"]|"")*"'
_ESCAPES = {"lf": "\n", "cr": "\r", "tab": "\t", "cr,lf": "\r\n", "#": "#"}


def unquote(s: str) -> str:
    """An M text literal ("...", with "" for a quote and #(lf) escapes) as plain text."""
    s = s[1:-1].replace('""', '"')
    return re.sub(r"#\(([^)]*)\)", lambda m: _ESCAPES.get(m.group(1).lower().replace(" ", ""), m.group(0)), s)


def package_from_mashup(data: bytes) -> dict[str, str]:
    """The text files inside a DataMashup ({"Formulas/Section1.m": "..."}). Raises ValueError if it isn't one."""
    if len(data) < 8:
        raise ValueError("too short")
    version, length = struct.unpack_from("<II", data, 0)
    if version != 0 or length > len(data) - 8:
        raise ValueError("not a DataMashup")
    with zipfile.ZipFile(io.BytesIO(data[8:8 + length])) as z:
        return {n: z.read(n).decode("utf-8-sig", errors="replace") for n in z.namelist()
                if n.endswith((".m", ".xml")) and z.getinfo(n).file_size < 20 * 1024 * 1024}


def mashup_from_custom_xml(text: str) -> bytes | None:
    """Excel keeps the DataMashup base64-encoded in a customXml part."""
    m = re.search(r"<DataMashup[^>]*>\s*([A-Za-z0-9+/=\s]+?)\s*</DataMashup>", text)
    return base64.b64decode(re.sub(r"\s+", "", m.group(1))) if m else None


def split_section(text: str) -> dict[str, str]:
    """Section1.m as {query name: expression}."""
    out = {}
    parts = re.split(r"(?m)^\s*shared\s+(#" + M_STRING + r"|[A-Za-z_][\w.]*)\s*=", text)
    for name, body in zip(parts[1::2], parts[2::2]):
        name = unquote(name[1:]) if name.startswith("#") else name
        out[name] = body.strip().rstrip(";").strip()
    return out


def _arg_values(args: str, params: dict[str, str]) -> list[str | None]:
    """The leading arguments of a call (the text after its opening bracket) that are text or
    parameters, in order. Stops at the first argument that is anything else."""
    values: list[str | None] = []
    token = re.compile(r"\s*(" + M_STRING + r"|#" + M_STRING + r"|[A-Za-z_][\w.]*)\s*([,)])")
    pos = 0
    while True:
        m = token.match(args, pos)
        if not m:
            break
        tok = m.group(1)
        if tok.startswith('"'):
            values.append(unquote(tok))
        elif tok.startswith("#"):
            values.append(params.get(unquote(tok[1:])))
        else:
            values.append(params.get(tok))
        if m.group(2) == ")":
            break
        pos = m.end()
    return values


def _calls(body: str, name: str):
    """The text after each "name(" in the expression."""
    for m in re.finditer(r"\b" + name + r"\s*\(", body):
        yield body[m.end():]


def parameters(queries: dict[str, str]) -> dict[str, str]:
    """Queries that are a single text value, as Power BI parameters are."""
    out = {}
    for name, body in queries.items():
        m = re.fullmatch(r"(" + M_STRING + r")(\s+meta\s*\[.*\])?", body.strip(), re.S)
        if m:
            out[name] = unquote(m.group(1))
    return out


def analyse(body: str, params: dict[str, str] | None = None, others: list[str] | None = None) -> dict[str, Any]:
    """What one query connects to."""
    params = params or {}
    found: dict[str, Any] = {"servers": [], "databases": [], "tables": [], "sql": [], "files": [], "folders": [],
                             "web": [], "odbc": [], "uses": []}

    def add(kind: str, value: str | None) -> None:
        if value and value not in found[kind]:
            found[kind].append(value)

    db = None
    for rest in _calls(body, r"Sql\.Databases?"):
        vals = _arg_values(rest, params)
        add("servers", vals[0] if vals else None)
        if len(vals) > 1:
            db = vals[1]
            add("databases", db)
    for m in re.finditer(r"\{\s*\[\s*Name\s*=\s*(" + M_STRING + r")", body):
        if not db and re.search(r"Sql\.Databases", body):
            db = unquote(m.group(1))
            add("databases", db)
    for m in re.finditer(r"\[\s*Schema\s*=\s*(" + M_STRING + r")\s*,\s*Item\s*=\s*(" + M_STRING + r")\s*\]", body):
        name = f"{unquote(m.group(1))}.{unquote(m.group(2))}"
        add("tables", f"{db}.{name}" if db else name)
    # Native SQL: [Query="..."] options, Value.NativeQuery(x, "..."), Odbc.Query(dsn, "...")
    sqls = [unquote(m.group(1)) for m in re.finditer(r"\bQuery\s*=\s*(" + M_STRING + r")", body)]
    sqls += [unquote(m.group(1)) for m in re.finditer(r"\bValue\.NativeQuery\s*\([^,]+,\s*(" + M_STRING + r")", body)]
    for m in re.finditer(r"\b(Odbc|OleDb)\.(Query|DataSource)\s*\((" + M_STRING + r")(?:\s*,\s*(" + M_STRING + r"))?",
                         body):
        add("odbc", redact(unquote(m.group(3))))
        if m.group(4):
            sqls.append(unquote(m.group(4)))
    for sql in sqls:
        if looks_like_sql(sql):
            add("sql", sql)
            for r in extract_refs(sql):
                if r.relation == "reads":
                    parts = r.name.split(".")
                    add("tables", f"{db}.{r.name}" if db and len(parts) <= 2 else r.name)
    for kind, fn in (("files", r"File\.Contents"), ("folders", r"Folder\.(?:Files|Contents)"),
                     ("web", r"(?:SharePoint\.(?:Files|Contents|Tables)|Web\.Contents|OData\.Feed)")):
        for rest in _calls(body, fn):
            add(kind, (_arg_values(rest, params) or [None])[0])
    code = re.sub(r'(?<!#)' + M_STRING, '""', body)  # text can't refer to another query
    for other in others or []:
        pattern = r'#"' + re.escape(other.replace('"', '""')) + '"' if not re.fullmatch(r"[A-Za-z_]\w*", other) \
            else r"(?<![\w.#\"])" + re.escape(other) + r"(?![\w\"])"
        if other not in params and re.search(pattern, code):
            add("uses", other)
    return {k: v for k, v in found.items() if v}


def read_queries(section_text: str) -> list[dict[str, Any]]:
    """Every query in a Section1.m with what it connects to. Parameters are listed with their value."""
    queries = split_section(section_text)
    params = parameters(queries)
    out = []
    for name, body in queries.items():
        q = {"name": name, "expression": body}
        if name in params:
            q["parameter"] = params[name]
        else:
            q.update(analyse(body, params, [n for n in queries if n != name]))
        out.append(q)
    return out


def redact(connection: str) -> str:
    """A connection string without its password."""
    return re.sub(r"(?i)\b(password|pwd)\s*=\s*(\{[^}]*\}|[^;]*)", r"\1=***", connection or "")


def table_refs(q: dict[str, Any]) -> list[str]:
    """"table:..." references for the links table, lower case, without the server."""
    return [f"table:{t.lower()}" for t in q.get("tables", [])]
