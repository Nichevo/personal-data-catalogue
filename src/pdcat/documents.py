"""Reference documents on business projects: design documents (Word, PDF, Markdown, text) kept with
every version.

Add a new version at any time; older ones are kept, newest first, with their dates. The text is
read out (Word without any extra package; PDF with the optional pypdf), searchable, and checked
for the tables, scripts and files it names, which are highlighted and listed as links. Those items
show the document on their own pages, and SQL in the document goes into the SQL query library.
"""
from __future__ import annotations

import io
import re
import uuid
import zipfile
from typing import Any

from .store import Catalogue, now

TYPES = ("docx", "pdf", "md", "markdown", "txt", "text", "sql")
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


def _docx_text(data: bytes) -> str:
    """Paragraphs of a .docx, read straight from its XML (python-docx isn't needed)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    paras = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        p = re.sub(r"<w:tab/>", "\t", p)
        p = re.sub(r"<w:br/>", "\n", p)
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
        paras.append(_unescape(text))
    return "\n".join(paras)[:MAX_TEXT]


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def add_document(cat: Catalogue, owner: str, title: str, file_name: str, data: bytes, mime: str = "") -> str:
    uid = uuid.uuid4().hex[:12]
    with cat.conn:
        cat.conn.execute("INSERT INTO documents (uid, owner, title, kind, created_at, author) VALUES "
                         "(?, ?, ?, 'reference', ?, ?)", (uid, owner, title.strip() or file_name, now(), cat.author()))
    add_version(cat, uid, file_name, data, mime)
    return uid


def add_version(cat: Catalogue, uid: str, file_name: str, data: bytes, mime: str = "") -> int:
    text, _ = extract_text(file_name, data)
    with cat.conn:
        v = (cat.conn.execute("SELECT MAX(version) FROM document_versions WHERE doc_uid = ?", (uid,)).fetchone()[0]
             or 0) + 1
        cat.conn.execute("INSERT INTO document_versions (doc_uid, version, file_name, mime, content, text, added_at, "
                         "author) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (uid, v, file_name, mime, data, text, now(), cat.author()))
    cat.rebuild_search()
    return v


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
            "'script', 'notebook', 'sql_script', 'data_file', 'file_series', 'workbook', 'report')"):
        names = {r["name"].lower()}
        if r["kind"] in ("table", "view", "procedure"):
            names.add(r["name"].split(".")[-1].lower())
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
        if "." not in name and "/" not in name and name not in words:
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
