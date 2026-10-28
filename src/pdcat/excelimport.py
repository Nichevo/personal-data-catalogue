"""Import from an Excel sheet: SQL queries, metrics or knowledge notes kept in a workbook.

Upload a workbook, pick the sheet and say what each row is: a SQL query (title, SQL, notes become
pasted SQL linked to the tables it uses, or question library entries), a metric (name, definition,
formula), or a knowledge note. Match columns to fields, preview, then import. Each item remembers
which workbook and sheet it came from. A sheet of free text can be imported whole as one note.

The workbook is read directly (no extra package): cell values only, formulas as their last result.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

from .scan_excel import NS, SharedStrings, _rels
from .store import Catalogue

ROW_TYPES = {
    "sql": ("SQL query (saved as pasted SQL)", ("title", "sql", "notes", "tags", "database")),
    "question": ("Question with its SQL (question library)", ("question", "sql", "answer", "tags")),
    "metric": ("Metric", ("name", "definition", "formula", "owner", "caveats", "tags")),
    "knowledge": ("Knowledge note", ("title", "body", "section", "tags", "triggers")),
}
REQUIRED = {"sql": ("sql",), "question": ("question",), "metric": ("name",), "knowledge": ("title",)}
MAX_ROWS = 5000

_GUESS = {
    "title": ("title", "name", "query name", "report", "description"),
    "sql": ("sql", "query", "sql query", "code", "statement", "script"),
    "notes": ("notes", "note", "comment", "comments", "description", "purpose"),
    "tags": ("tags", "tag", "category", "area", "topic"),
    "database": ("database", "db", "source"),
    "question": ("question", "ask", "business question"),
    "answer": ("answer", "explanation", "notes"),
    "name": ("name", "metric", "kpi", "measure"),
    "definition": ("definition", "description", "meaning"),
    "formula": ("formula", "calculation", "calc", "how calculated"),
    "owner": ("owner", "contact", "responsible"),
    "caveats": ("caveats", "notes", "comments", "caveat"),
    "body": ("body", "text", "note", "notes", "content", "details", "description"),
    "section": ("section", "category", "type"),
    "triggers": ("triggers", "keywords", "trigger words"),
}


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "A").group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def sheet_names(data: bytes, file_name: str = "") -> list[str]:
    if file_name.lower().endswith(".csv"):
        return ["(the CSV file)"]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return [s.get("name", "") for s in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{NS['m']}}}sheet")]


def read_sheet(data: bytes, sheet: str, file_name: str = "") -> list[list[str]]:
    """Rows of one sheet as lists of text, blank cells as ""."""
    if file_name.lower().endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [row for row in csv.reader(io.StringIO(text), dialect)][:MAX_ROWS + 1]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        wb_rels = _rels(z, "xl/workbook.xml")
        part = None
        for s in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{NS['m']}}}sheet"):
            if s.get("name") == sheet:
                part = wb_rels.get(s.get(f"{{{NS['r']}}}id"))
        if not part or part not in z.namelist():
            raise KeyError(sheet)
        strings = SharedStrings(z)
        rows: list[list[str]] = []
        with z.open(part) as f:
            for _, el in ET.iterparse(f, events=("end",)):
                if el.tag.rsplit("}", 1)[-1] != "row":
                    continue
                row: list[str] = []
                for c in el.findall("m:c", NS):
                    i = _col_index(c.get("r", ""))
                    while len(row) < i:
                        row.append("")
                    v = c.find("m:v", NS)
                    t = c.get("t")
                    if t == "s" and v is not None and (v.text or "").isdigit():
                        row.append(strings.get(int(v.text)))
                    elif t == "inlineStr":
                        row.append("".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t")))
                    elif t == "b" and v is not None:
                        row.append("TRUE" if v.text == "1" else "FALSE")
                    else:
                        row.append(v.text or "" if v is not None else "")
                rows.append(row)
                el.clear()
                if len(rows) > MAX_ROWS:
                    break
    return rows


def as_records(rows: list[list[str]], header_row: int = 0) -> tuple[list[str], list[dict[str, str]]]:
    """(column names, one dict per row) using one row as the header; empty rows are dropped."""
    if not rows:
        return [], []
    header = [h.strip() or f"column {i + 1}" for i, h in enumerate(rows[header_row])]
    out = []
    for r in rows[header_row + 1:]:
        if not any(c.strip() for c in r):
            continue
        out.append({h: (r[i] if i < len(r) else "").strip() for i, h in enumerate(header)})
    return header, out


def guess_mapping(row_type: str, columns: list[str]) -> dict[str, str | None]:
    low = {c.lower().strip(): c for c in columns}
    out: dict[str, str | None] = {}
    used: set[str] = set()
    for field in ROW_TYPES[row_type][1]:
        pick = None
        for name in _GUESS.get(field, (field,)):
            if name in low and low[name] not in used:
                pick = low[name]
                break
        out[field] = pick
        if pick:
            used.add(pick)
    return out


def preview(row_type: str, records: list[dict[str, str]], mapping: dict[str, str | None]) -> list[dict[str, str]]:
    """What would be imported: one dict of fields per row that has its required fields."""
    out = []
    for r in records:
        item = {f: (r.get(col) or "").strip() if col else "" for f, col in mapping.items()}
        if all(item.get(f) for f in REQUIRED[row_type]):
            out.append(item)
    return out


def import_rows(cat: Catalogue, row_type: str, items: list[dict[str, str]], workbook: str, sheet: str) -> int:
    """Import previewed rows. Each item records the workbook and sheet it came from."""
    from . import knowledge as K
    origin = f"{workbook} › {sheet}"
    n = 0
    for it in items:
        if row_type == "sql":
            key = cat.save_snippet(it.get("title") or _first_words(it["sql"]), it["sql"],
                                   description=it.get("notes", ""), tags=it.get("tags", ""),
                                   database=it.get("database") or None)
            a = cat.get_asset(key)
            cat.upsert_asset(key, "sql_snippet", a["name"], a["source"], description=a["description"],
                             attrs={**a["attrs"], "imported_from_workbook": origin})
            cat.conn.commit()
        elif row_type == "question":
            K.save_question(cat, it["question"], answer=_with_origin(it.get("answer", ""), origin), sql=it.get("sql", ""),
                            tags=it.get("tags", ""))
        elif row_type == "metric":
            K.save_metric(cat, it["name"], definition=it.get("definition", ""), formula=it.get("formula", ""),
                          owner=it.get("owner", ""), caveats=_with_origin(it.get("caveats", ""), origin),
                          tags=it.get("tags", ""))
        elif row_type == "knowledge":
            section = it.get("section") if it.get("section") in K.SECTIONS else ""
            K.save_knowledge(cat, it["title"], _with_origin(it.get("body", ""), origin), section=section,
                             tags=it.get("tags", ""), triggers=it.get("triggers", ""))
        n += 1
    return n


def import_whole_sheet(cat: Catalogue, rows: list[list[str]], title: str, workbook: str, sheet: str) -> str:
    """A sheet of free text as one knowledge note: each non-empty row becomes a line."""
    from . import knowledge as K
    lines = [" · ".join(c.strip() for c in r if c.strip()) for r in rows]
    body = "\n".join(line for line in lines if line)
    return K.save_knowledge(cat, title, _with_origin(body, f"{workbook} › {sheet}"))


def _with_origin(text: str, origin: str) -> str:
    return (text.rstrip() + "\n\n" if text.strip() else "") + f"_Imported from {origin}._"


def _first_words(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()[:60]
