"""Reference documents: design documents (Word, PowerPoint, Excel, PDF, Markdown, text) kept with every version.

Add one by hand on a business project, or mark a folder with the "design documents" role (or a single file
in a folder) and the folder scan reads every document in it. Older versions are kept, newest first, with
their dates. The text is read out (Word, PowerPoint and Excel without any extra package; PDF with the optional
pypdf), searchable, and checked for the tables, measures, scripts and files it names, which are highlighted
and listed as links. Those items show the document on their own pages, SQL in the document goes into the SQL
query library, and a table of names and definitions in it becomes suggested descriptions.

A document can say what it *describes* (a business project, a Power BI model, a table): that item's page then
opens with the document at the top.
"""
from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any

from . import suggestions as _suggestions
from .store import Catalogue, now

TYPES = ("docx", "pptx", "xlsx", "xlsm", "pdf", "md", "markdown", "txt", "text", "sql")
DOC_SUFFIXES = tuple(f".{t}" for t in TYPES)
DESIGN_FILES = "design_files"   # paths of single files marked as design documents
MAX_SHEET_ROWS = 2000
MAX_TEXT = 2_000_000
MIN_NAME = 4   # shorter item names are too likely to match ordinary words


def pdf_available() -> bool:
    try:
        import pypdf  # noqa: F401
        return True
    except ImportError:
        return False


def extract_text(file_name: str, data: bytes) -> tuple[str, str | None]:
    """(text, a note when the text couldn't be read)."""
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext == "docx":
        try:
            return _docx_text(data), None
        except Exception as e:
            return "", f"Couldn't read the Word file ({type(e).__name__})."
    if ext == "pptx":
        try:
            return _pptx_text(data), None
        except Exception as e:
            return "", f"Couldn't read the PowerPoint file ({type(e).__name__})."
    if ext in ("xlsx", "xlsm"):
        try:
            return _xlsx_text(data), None
        except Exception as e:
            return "", f"Couldn't read the Excel file ({type(e).__name__})."
    if ext == "pdf":
        try:
            import pypdf
        except ImportError:
            return "", "Install pypdf to read the text of PDFs (pip install pypdf). The file is kept either way."
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            return "\n\n".join(p.extract_text() or "" for p in reader.pages)[:MAX_TEXT], None
        except Exception as e:
            return "", f"Couldn't read the PDF ({type(e).__name__})."
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)[:MAX_TEXT], None
        except UnicodeDecodeError:
            continue
    return "", "Couldn't read the text."


def _para_text(p: str) -> str:
    p = re.sub(r"<w:tab/>", "\t", p)
    p = re.sub(r"<w:br/>", "\n", p)
    return _unescape("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)))


def _docx_text(data: bytes) -> str:
    """Paragraphs of a .docx, read straight from its XML (python-docx isn't needed). A table row becomes one
    line with its cells separated by tabs, so a table of names and definitions can be read back."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    lines = []
    body = re.search(r"<w:body>(.*)</w:body>", xml, re.S)
    xml = body.group(1) if body else xml
    pos = 0
    for m in re.finditer(r"<w:tbl>.*?</w:tbl>", xml, re.S):
        for p in re.findall(r"<w:p[ >].*?</w:p>", xml[pos:m.start()], re.S):
            lines.append(_para_text(p))
        for row in re.findall(r"<w:tr[ >].*?</w:tr>", m.group(0), re.S):
            cells = [" ".join(_para_text(p) for p in re.findall(r"<w:p[ >].*?</w:p>", c, re.S)).strip()
                     for c in re.findall(r"<w:tc[ >].*?</w:tc>", row, re.S)]
            lines.append("\t".join(cells))
        pos = m.end()
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml[pos:], re.S):
        lines.append(_para_text(p))
    return "\n".join(lines)[:MAX_TEXT]


def _pptx_text(data: bytes) -> str:
    """Slide titles, text and notes of a .pptx, slide by slide, from its XML."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        for i, name in enumerate(slides, 1):
            xml = z.read(name).decode("utf-8", errors="replace")
            paras = [_unescape("".join(re.findall(r"<a:t>(.*?)</a:t>", p, re.S)))
                     for p in re.findall(r"<a:p>.*?</a:p>", xml, re.S)]
            out.append(f"--- Slide {i} ---")
            out += [p for p in paras if p.strip()]
            notes = name.replace("slides/slide", "notesSlides/notesSlide")
            if notes in names:
                nxml = z.read(notes).decode("utf-8", errors="replace")
                nparas = [_unescape("".join(re.findall(r"<a:t>(.*?)</a:t>", p, re.S)))
                          for p in re.findall(r"<a:p>.*?</a:p>", nxml, re.S)]
                nparas = [p for p in nparas if p.strip() and not p.strip().isdigit()]
                if nparas:
                    out.append("Notes: " + " ".join(nparas))
    return "\n".join(out)[:MAX_TEXT]


def _xlsx_text(data: bytes) -> str:
    """Each sheet of a workbook as lines of tab-separated cells (the first MAX_SHEET_ROWS rows), so a sheet of
    names and definitions reads back as a table."""
    from xml.etree import ElementTree as ET

    from .scan_excel import NS, SharedStrings, _rels
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        strings = SharedStrings(z)
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = _rels(z, "xl/workbook.xml")
        for sheet in wb.findall("m:sheets/m:sheet", NS):
            part = rels.get(sheet.get(f"{{{NS['r']}}}id"), "")
            if part not in z.namelist():
                continue
            out.append(f"--- Sheet {sheet.get('name')} ---")
            rows = 0
            with z.open(part) as f:
                for event, el in ET.iterparse(f, events=("end",)):
                    if el.tag.rsplit("}", 1)[-1] != "row":
                        continue
                    cells = []
                    for c in el.findall("m:c", NS):
                        v = c.find("m:v", NS)
                        if c.get("t") == "s" and v is not None and v.text and v.text.isdigit():
                            cells.append(strings.get(int(v.text)))
                        elif c.get("t") == "inlineStr":
                            cells.append("".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t")))
                        elif v is not None:
                            cells.append(v.text or "")
                        else:
                            cells.append("")
                    el.clear()
                    if any(cells):
                        out.append("\t".join(cells).rstrip("\t"))
                    rows += 1
                    if rows >= MAX_SHEET_ROWS:
                        out.append("…")
                        break
    return "\n".join(out)[:MAX_TEXT]


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def add_document(cat: Catalogue, owner: str, title: str, file_name: str, data: bytes, mime: str = "", *,
                 path: str | None = None, mtime: float | None = None, source: str | None = None,
                 rebuild: bool = True) -> str:
    uid = uuid.uuid4().hex[:12]
    with cat.conn:
        cat.conn.execute("INSERT INTO documents (uid, owner, title, kind, created_at, author, path, mtime, source) "
                         "VALUES (?, ?, ?, 'reference', ?, ?, ?, ?, ?)",
                         (uid, owner, title.strip() or file_name, now(), cat.author(), path, mtime, source))
    add_version(cat, uid, file_name, data, mime, rebuild=rebuild)
    return uid


def add_version(cat: Catalogue, uid: str, file_name: str, data: bytes, mime: str = "", *, mtime: float | None = None,
                rebuild: bool = True) -> int:
    text, _ = extract_text(file_name, data)
    with cat.conn:
        v = (cat.conn.execute("SELECT MAX(version) FROM document_versions WHERE doc_uid = ?", (uid,)).fetchone()[0]
             or 0) + 1
        cat.conn.execute("INSERT INTO document_versions (doc_uid, version, file_name, mime, content, text, added_at, "
                         "author) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (uid, v, file_name, mime, data, text, now(), cat.author()))
        if mtime is not None:
            cat.conn.execute("UPDATE documents SET mtime = ? WHERE uid = ?", (mtime, uid))
    if rebuild:
        cat.rebuild_search()
    return v


def set_describes(cat: Catalogue, uid: str, key: str | None) -> None:
    """Say what the document describes: a business project (bp://…), a report, a table, or nothing."""
    with cat.conn:
        cat.conn.execute("UPDATE documents SET describes = ? WHERE uid = ?", (key or None, uid))
    cat.changed()


def documents_describing(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    """Documents that describe this item (or were added to it), newest first."""
    return [dict(r) for r in cat.conn.execute(
        "SELECT d.*, (SELECT MAX(version) FROM document_versions v WHERE v.doc_uid = d.uid) AS versions, "
        "(SELECT MAX(added_at) FROM document_versions v WHERE v.doc_uid = d.uid) AS updated FROM documents d "
        "WHERE d.describes = ? OR d.owner = ? ORDER BY updated DESC", (key, key))]


# ---- documents in scanned folders --------------------------------------------------------------

def design_files(cat: Catalogue) -> set[str]:
    """Single files marked as design documents (normalised paths)."""
    return {_norm(p) for p in cat.setting(DESIGN_FILES, [])}


def mark_design_file(cat: Catalogue, path: str, marked: bool = True) -> None:
    have = {p for p in cat.setting(DESIGN_FILES, [])}
    key = str(path)
    have.add(key) if marked else have.discard(key)
    if not marked:
        have = {p for p in have if _norm(p) != _norm(key)}
    cat.set_setting(DESIGN_FILES, sorted(have))


def _norm(path: str) -> str:
    return str(path).replace("\\", "/").rstrip("/").lower()


def design_folders(cat: Catalogue, root: Path) -> list[Path]:
    """Folders under root with the confirmed "design" role (root itself included when it has it)."""
    import os

    from .folderroles import MAX_DEPTH, norm, role_from_name, stored
    base = norm(root)
    wanted = {k for k, v in stored(cat).items() if v.get("role") == "design" and v.get("confirmed")
              and (k == base or k.startswith(base + "/"))}
    if not wanted:
        return []
    out = [root] if base in wanted else []
    # stored keys are lower-cased, so walk the real folders (which may be Design/ or Specs/) and compare
    for dirpath, dirnames, _ in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        keep = []
        for d in sorted(dirnames):
            p = Path(dirpath) / d
            if norm(p) in wanted:
                out.append(p)
            if depth + 1 < MAX_DEPTH and not d.startswith(".") and role_from_name(d) != "ignore":
                keep.append(d)
        dirnames[:] = keep
        if len(out) >= len(wanted):
            break
    return out


def document_files(cat: Catalogue, root: Path) -> list[Path]:
    """Every document file a folder scan should read: those in design folders, plus single marked files."""
    from .folderroles import ignored_dirs
    from .scanprogress import find_files
    out: list[Path] = []
    seen: set[str] = set()
    for folder in design_folders(cat, root):
        if not folder.is_dir():
            continue
        found, _ = find_files(folder, DOC_SUFFIXES, True, ignored_dirs(cat, root))
        for paths in found.values():
            for p in paths:
                if p.name.startswith("~$"):
                    continue  # Office lock files
                if _norm(p) not in seen:
                    seen.add(_norm(p))
                    out.append(p)
    base = _norm(root)
    for marked in cat.setting(DESIGN_FILES, []):
        low = _norm(marked)
        if low.startswith(base + "/") and low not in seen and Path(marked).is_file():
            seen.add(low)
            out.append(Path(marked))
    return sorted(out)


def by_path(cat: Catalogue) -> dict[str, dict[str, Any]]:
    return {_norm(r["path"]): dict(r) for r in cat.conn.execute("SELECT * FROM documents WHERE path IS NOT NULL")}


def scan_folder_documents(cat: Catalogue, root: Path, source: str, owner: str, report=lambda *_: None) -> dict[str, int]:
    """Read the design documents of a scanned folder: new files become documents owned by the folder (or by what
    the folder's role says they describe), changed files become new versions. Returns counts."""
    files = document_files(cat, root)
    counts = {"documents": 0, "new": 0, "updated": 0, "errors": 0}
    if not files:
        return counts
    known = by_path(cat)
    for i, path in enumerate(files):
        report(f"Design document {path.relative_to(root).as_posix()}", i, len(files))
        try:
            st = path.stat()
            doc = known.get(_norm(path))
            if doc and doc.get("mtime") == st.st_mtime:
                counts["documents"] += 1
                continue
            data = path.read_bytes()
            if doc:
                add_version(cat, doc["uid"], path.name, data, mtime=st.st_mtime, rebuild=False)
                counts["updated"] += 1
            else:
                add_document(cat, owner, path.stem, path.name, data, path=str(path), mtime=st.st_mtime, source=source,
                             rebuild=False)
                counts["new"] += 1
            counts["documents"] += 1
        except Exception:  # one unreadable document must not stop the scan
            counts["errors"] += 1
    if counts["new"] or counts["updated"]:
        cat.rebuild_search()
    return counts


def documents_in(cat: Catalogue, source: str) -> list[dict[str, Any]]:
    """Documents read from one folder source."""
    return [dict(r) for r in cat.conn.execute(
        "SELECT d.*, (SELECT MAX(version) FROM document_versions v WHERE v.doc_uid = d.uid) AS versions, "
        "(SELECT MAX(added_at) FROM document_versions v WHERE v.doc_uid = d.uid) AS updated FROM documents d "
        "WHERE d.source = ? ORDER BY title COLLATE NOCASE", (source,))]


# ---- definitions in documents --------------------------------------------------------------------

MIN_DEFINITION = 8
MAX_NAME = 60
_DEFINITION_LINE = re.compile(rf"^\s*([A-Za-z_][\w .\[\]-]{{1,{MAX_NAME}}}?)\s*(?::|–|—| - )\s*(.{{{MIN_DEFINITION},}})$")


def definitions(text: str) -> list[tuple[str, str]]:
    """(name, definition) pairs from a document: rows of a table (cells separated by tabs) and lines like
    "PlantID: identifier of the plant" or "PlantID – identifier of the plant"."""
    out: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        if "\t" in line:
            cells = [c.strip() for c in line.split("\t")]
            cells = [c for c in cells if c]
            if len(cells) >= 2 and len(cells[0]) <= MAX_NAME and len(cells[1]) >= MIN_DEFINITION \
                    and not cells[0].lower() in ("name", "column", "field", "measure", "table"):
                out.append((cells[0], cells[1]))
            continue
        m = _DEFINITION_LINE.match(line)
        if m and " " not in m.group(1).strip().rstrip("]").split("[")[0]:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def suggest_definitions(cat: Catalogue) -> int:
    """Definitions in the latest version of every document, matched to undescribed columns (by name, anywhere)
    and to measures (by name), offered as suggestions of kind "doc_definition"."""
    from . import suggestions
    columns: dict[str, list[tuple[str, str, str]]] = {}
    for r in cat.conn.execute(
            "SELECT c.asset_key, c.name, a.name AS item FROM columns c JOIN assets a ON a.key = c.asset_key AND "
            "a.status = 'active' LEFT JOIN column_info ci ON ci.asset_key = c.asset_key AND ci.column_name = c.name "
            "WHERE COALESCE(ci.description, c.description, '') = ''"):
        columns.setdefault(r["name"].lower(), []).append((r["asset_key"], r["name"], r["item"]))
    measures: dict[str, list[tuple[str, str]]] = {}
    for r in cat.conn.execute("SELECT key, name, attrs FROM assets WHERE kind = 'measure' AND status = 'active'"):
        a = json.loads(r["attrs"] or "{}")
        name = str(a.get("name") or r["name"]).lower()
        measures.setdefault(name, []).append((r["key"], r["name"]))
        measures.setdefault(r["name"].lower(), []).append((r["key"], r["name"]))
    from .curation import all_item_info
    purposes = {k for k, v in all_item_info(cat).items() if v.get("purpose")}
    found = []
    for d in all_latest(cat):
        for name, text in definitions(d["text"] or ""):
            low = name.lower().strip("[]")
            text = text[:500]
            for key, col, item in columns.get(low, [])[:20]:
                found.append({"id": suggestions.make_id("doc_definition", f"{key}#{col}"), "subject": key,
                              "title": f"{item}.{col}: “{text[:120]}{'…' if len(text) > 120 else ''}”",
                              "detail": f"From the document {d['title']}.",
                              "payload": {"key": key, "column": col, "text": text, "doc": d["uid"]}})
            for key, label in measures.get(low, [])[:5]:
                if key in purposes:
                    continue
                found.append({"id": suggestions.make_id("doc_definition", key), "subject": key,
                              "title": f"{label}: “{text[:120]}{'…' if len(text) > 120 else ''}”",
                              "detail": f"From the document {d['title']}.",
                              "payload": {"key": key, "text": text, "doc": d["uid"]}})
    return suggestions.sync(cat, "doc_definition", found)


def _accept_definition(cat: Catalogue, p: dict[str, Any]) -> None:
    from .curation import set_column_info, set_item_info
    if p.get("column"):
        set_column_info(cat, p["key"], p["column"], description=p["text"])
    else:
        set_item_info(cat, p["key"], purpose=p["text"])


def rename(cat: Catalogue, uid: str, title: str) -> None:
    with cat.conn:
        cat.conn.execute("UPDATE documents SET title = ? WHERE uid = ?", (title.strip(), uid))
    cat.rebuild_search()


def delete(cat: Catalogue, uid: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM document_versions WHERE doc_uid = ?", (uid,))
        cat.conn.execute("DELETE FROM documents WHERE uid = ?", (uid,))
    cat.rebuild_search()


def documents_for(cat: Catalogue, owner: str) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        "SELECT d.*, (SELECT MAX(version) FROM document_versions v WHERE v.doc_uid = d.uid) AS versions, "
        "(SELECT MAX(added_at) FROM document_versions v WHERE v.doc_uid = d.uid) AS updated FROM documents d "
        "WHERE owner = ? ORDER BY title COLLATE NOCASE", (owner,))]


def get(cat: Catalogue, uid: str) -> dict[str, Any] | None:
    r = cat.conn.execute("SELECT * FROM documents WHERE uid = ?", (uid,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["versions"] = [dict(v) for v in cat.conn.execute(
        "SELECT doc_uid, version, file_name, mime, length(content) AS size, added_at, author FROM document_versions "
        "WHERE doc_uid = ? ORDER BY version DESC", (uid,))]
    return d


def version(cat: Catalogue, uid: str, number: int | None = None) -> dict[str, Any] | None:
    sql = "SELECT * FROM document_versions WHERE doc_uid = ? " + (
        "AND version = ?" if number else "ORDER BY version DESC LIMIT 1")
    r = cat.conn.execute(sql, (uid, number) if number else (uid,)).fetchone()
    return dict(r) if r else None


def _name_index(cat: Catalogue) -> dict[str, set[str]]:
    """Lower-case name -> item keys, for tables, views, procedures, scripts and files."""
    out: dict[str, set[str]] = {}
    for r in cat.conn.execute(
            "SELECT key, kind, name FROM assets WHERE status = 'active' AND kind IN ('table', 'view', 'procedure', "
            "'script', 'notebook', 'sql_script', 'data_file', 'file_series', 'workbook', 'report', 'measure')"):
        names = {r["name"].lower()}
        if r["kind"] in ("table", "view", "procedure"):
            names.add(r["name"].split(".")[-1].lower())
        elif r["kind"] == "measure":
            # "Sales[Net Units]" and "[Net Units]": the forms a design document writes a measure in
            names.add("[" + r["name"].lower().split("[", 1)[-1] if "[" in r["name"] else r["name"].lower())
        else:
            names.add(r["name"].rsplit("/", 1)[-1].lower())
        for n in names:
            if len(n) >= MIN_NAME:
                out.setdefault(n, set()).add(r["key"])
    return out


def mentions(cat: Catalogue, text: str, index: dict[str, set[str]] | None = None) -> list[dict[str, Any]]:
    """Catalogued items a text names: [{"key", "name", "count"}], most mentioned first."""
    index = _name_index(cat) if index is None else index
    if not text or not index:
        return []
    low = text.lower()
    words = set(re.findall(r"[\w.\[\]-]+", low))
    found: dict[str, dict[str, Any]] = {}
    for name, keys in index.items():
        if not any(c in name for c in "./[ ") and name not in words:
            continue  # quick skip for single-word names
        n = len(re.findall(rf"(?<![\w.]){re.escape(name)}(?![\w])", low))
        if n:
            for k in keys:
                f = found.setdefault(k, {"key": k, "name": name, "count": 0})
                f["count"] = max(f["count"], n)
    return sorted(found.values(), key=lambda f: (-f["count"], f["name"]))


def highlight(text: str, names: list[str]) -> str:
    """The text with item names in bold, for showing in the app."""
    if not names:
        return text
    rx = re.compile("|".join(rf"(?<![\w.]){re.escape(n)}(?![\w])" for n in sorted(set(names), key=len, reverse=True)),
                    re.I)
    return rx.sub(lambda m: f"**{m.group(0)}**", text)


def documents_mentioning(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    """Documents whose latest version names this item."""
    a = cat.get_asset(key)
    if a is None:
        return []
    names = {a["name"].lower()}
    names.add((a["name"].split(".")[-1] if a["kind"] in ("table", "view", "procedure")
               else a["name"].rsplit("/", 1)[-1]).lower())
    names = {n for n in names if len(n) >= MIN_NAME}
    out = []
    for r in cat.conn.execute(
            "SELECT d.uid, d.title, d.owner, v.text FROM documents d JOIN document_versions v ON v.doc_uid = d.uid "
            "AND v.version = (SELECT MAX(version) FROM document_versions x WHERE x.doc_uid = d.uid)"):
        low = (r["text"] or "").lower()
        if any(re.search(rf"(?<![\w.]){re.escape(n)}(?![\w])", low) for n in names):
            out.append({"uid": r["uid"], "title": r["title"], "owner": r["owner"]})
    return out


def sql_blocks(text: str) -> list[str]:
    """SQL in a document: fenced ```sql blocks, and paragraphs that read as SQL."""
    from .sqlrefs import looks_like_sql
    out = [m.group(1).strip() for m in re.finditer(r"```(?:sql|tsql)?\s*\n(.*?)```", text or "", re.S | re.I)]
    rest = re.sub(r"```.*?```", "", text or "", flags=re.S)
    for para in re.split(r"\n\s*\n", rest):
        p = para.strip()
        if re.match(r"(?i)^(select|with|insert|update|delete|merge|exec)\b", p) and looks_like_sql(p):
            out.append(p)
    return out


def all_latest(cat: Catalogue) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        "SELECT d.uid, d.title, d.owner, v.text FROM documents d JOIN document_versions v ON v.doc_uid = d.uid "
        "AND v.version = (SELECT MAX(version) FROM document_versions x WHERE x.doc_uid = d.uid)")]


_suggestions.handler("doc_definition")(_accept_definition)
