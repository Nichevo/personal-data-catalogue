"""Reference documents: the list on a business project, a page per document, and "In documents" on items."""
from __future__ import annotations

import streamlit as st

from pdcat import documents as D
from pdcat.store import Catalogue

from .common import asset_button, cached, go, icon

MAX_SHOWN = 30_000


def documents_section(cat: Catalogue, owner: str) -> None:
    st.subheader("Reference documents")
    docs = D.documents_describing(cat, owner)
    for i, d in enumerate(docs):
        c1, c2 = st.columns([4, 2])
        with c1:
            st.button(f"📄 {d['title']}", key=f"doc-{owner}-{i}", on_click=go, args=(f"doc://{d['uid']}",),
                      type="tertiary")
        c2.caption(f"{d['versions']} version(s) · updated {(d['updated'] or '')[:10]}")
    if not docs:
        st.caption("Design documents, specs and notes (Word, PDF, Markdown or text) kept with every version.")
    with st.form(f"doc-add-{owner}", clear_on_submit=True):
        up = st.file_uploader("Add a document", type=list(D.TYPES), key=f"doc-up-{owner}")
        title = st.text_input("Title (optional)")
        if st.form_submit_button("Add") and up is not None:
            uid = D.add_document(cat, owner, title or up.name.rsplit(".", 1)[0], up.name, up.getvalue(), up.type or "")
            go(f"doc://{uid}")
            st.rerun()
    if not D.pdf_available():
        st.caption("PDF text is read with the optional pypdf package (`pip install pypdf`); PDFs are kept either way.")


def design_documents_box(cat: Catalogue, key: str, *, title: str = "📑 Design documents") -> None:
    """The documents that describe this item (business project, report, model, table), at the top of its page."""
    docs = D.documents_describing(cat, key)
    if not docs:
        return
    with st.container(border=True):
        st.markdown(f"**{title}** ({len(docs)})")
        for i, d in enumerate(docs[:8]):
            c1, c2 = st.columns([4, 2])
            with c1:
                st.button(f"📄 {d['title']}", key=f"ddoc-{key}-{i}", on_click=go, args=(f"doc://{d['uid']}",),
                          type="tertiary")
            c2.caption(f"{d['versions']} version(s) · updated {(d['updated'] or '')[:10]}"
                       + (f" · from {d['source']}" if d.get("source") else ""))
        if len(docs) > 8:
            st.caption(f"…and {len(docs) - 8} more.")


def folder_documents_box(cat: Catalogue, key: str, asset: dict) -> None:
    """On a folder's page: the design documents read from it, and a way to mark single files as design documents
    (whole folders get the role in Folder roles)."""
    from pathlib import Path

    source = asset["source"]
    docs = D.documents_in(cat, source)
    root = Path(asset["attrs"].get("path") or "")
    with st.expander(f"📑 Design documents ({len(docs)})", expanded=False):
        st.caption("Word, PowerPoint, Excel, PDF, Markdown and text files read as reference documents: their text is "
                   "searchable, the tables and measures they name link to them, and tables of names and "
                   "definitions become suggested descriptions. Give a whole folder the *design* role in Folder roles "
                   "(Sources page), or mark single files here.")
        for i, d in enumerate(docs[:30]):
            c1, c2 = st.columns([4, 2])
            with c1:
                st.button(f"📄 {d['title']}", key=f"fdoc-{key}-{i}", on_click=go, args=(f"doc://{d['uid']}",),
                          type="tertiary")
            c2.caption(f"{d['versions']} version(s) · " + (d["describes"] or "describes nothing yet"))
        if not root.is_dir():
            return
        if not st.toggle("Mark single files", key=f"mark-doc-look-{key}",
                         help="Lists the Word, PowerPoint, Excel, PDF, Markdown and text files in this folder that "
                              "aren't read yet (walks the folder once)."):
            return
        candidates = [Path(p) for p in _candidate_files(cat, str(root), bool(asset["attrs"].get("recursive", True)))]
        if not candidates:
            st.caption("No other document files in this folder." if docs else "No document files in this folder.")
            return
        labels = {str(p): p.relative_to(root).as_posix() for p in candidates[:500]}
        picked = st.multiselect(f"Mark files as design documents ({len(candidates)} in the folder)", list(labels),
                                format_func=labels.get, key=f"mark-doc-{key}")
        if picked and st.button("Mark and read them now", key=f"mark-doc-go-{key}", type="primary"):
            for path in picked:
                D.mark_design_file(cat, path)
            counts = D.scan_folder_documents(cat, root, source, key)
            st.toast(f"Read {counts['new'] + counts['updated']} document(s)")
            st.rerun()


def describes_picker(cat: Catalogue, d: dict) -> None:
    """What the document describes: a business project, a Power BI report or model, or a table (any item key)."""
    from pdcat.bprojects import list_projects
    options: dict[str, str] = {"": "(nothing in particular)"}
    for p in list_projects(cat):
        options[f"bp://{p['uid']}"] = f"🧭 {p['name']}"
    for r in cat.conn.execute("SELECT key, name FROM assets WHERE kind = 'report' AND status = 'active' ORDER BY name"):
        options[r["key"]] = f"📊 {r['name']}"
    current = d.get("describes") or ""
    if current and current not in options:
        a = cat.get_asset(current)
        options[current] = f"{icon(a['kind'])} {a['name']}" if a else current
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    pick = c1.selectbox("Describes", list(options), index=list(options).index(current) if current in options else 0,
                        format_func=options.get, key=f"desc-pick-{d['uid']}",
                        help="The item this document is the design of. Its page then opens with this document at "
                             "the top.")
    other = c2.text_input("…or any item key", key=f"desc-key-{d['uid']}", placeholder="sql://… or files://…")
    chosen = other.strip() or pick
    if chosen != current and st.button("Save what it describes", key=f"desc-save-{d['uid']}"):
        D.set_describes(cat, d["uid"], chosen or None)
        st.rerun()


def document_page(cat: Catalogue, key: str) -> None:
    uid = key.removeprefix("doc://")
    d = D.get(cat, uid)
    if d is None:
        st.warning("This document no longer exists.")
        return
    st.title(f"{icon('document')} {d['title']}")
    latest = D.version(cat, uid)
    owner = d["owner"]
    st.caption(f"Version {latest['version']} of {len(d['versions'])} · added {latest['added_at'][:10]}"
               + (f" by {latest['author']}" if latest.get("author") else "") + f"  \n`{key}`")
    if owner.startswith("bp://"):
        from pdcat.bprojects import get_project
        p = get_project(cat, owner.removeprefix("bp://"))
        if p:
            st.button(f"🧭 {p['name']}", on_click=go, args=(owner,), type="tertiary", key=f"doc-owner-{uid}")
    elif cat.get_asset(owner):
        a = cat.get_asset(owner)
        asset_button(owner, f"In {a['name']}", a["kind"], f"doc-owner-{uid}")
    if d.get("path"):
        st.caption(f"📍 `{d['path']}` · read by the folder scan; a changed file becomes a new version.")
    describes_picker(cat, d)
    if d.get("describes") and d["describes"] != owner:
        a = cat.get_asset(d["describes"])
        if a:
            asset_button(d["describes"], f"Describes {a['name']}", a["kind"], f"doc-desc-{uid}")
        elif d["describes"].startswith("bp://"):
            from pdcat.bprojects import get_project
            p = get_project(cat, d["describes"].removeprefix("bp://"))
            if p:
                st.button(f"Describes 🧭 {p['name']}", on_click=go, args=(d["describes"],), type="tertiary",
                          key=f"doc-desc-{uid}")
    text, note = latest["text"] or "", None
    if not text:
        _, note = D.extract_text(latest["file_name"] or "", latest["content"] or b"")
    found = D.mentions(cat, text)
    if found:
        st.markdown(f"**Items it names ({len(found)})**")
        cols = st.columns(3)
        for i, f in enumerate(found[:60]):
            a = cat.get_asset(f["key"]) or {}
            with cols[i % 3]:
                asset_button(f["key"], a.get("name", f["name"]), a.get("kind", "note"), f"docm-{uid}-{i}",
                             help=f"named {f['count']} time(s)")
    st.download_button("⬇️ Download this version", latest["content"] or b"", latest["file_name"] or "document",
                       latest["mime"] or "application/octet-stream", key=f"doc-dl-{uid}")
    with st.expander("Text", expanded=bool(text)):
        if note:
            st.info(note)
        elif text:
            shown = text[:MAX_SHOWN]
            st.markdown(D.highlight(shown, [f["name"] for f in found]).replace("\n", "  \n"))
            if len(text) > MAX_SHOWN:
                st.caption(f"Showing the first {MAX_SHOWN:,} characters; search covers all of it.")
    with st.expander(f"Versions ({len(d['versions'])})"):
        for v in d["versions"]:
            c1, c2 = st.columns([4, 1])
            c1.caption(f"v{v['version']} · {v['file_name']} · {v['added_at'][:16].replace('T', ' ')}"
                       + (f" · {v['author']}" if v.get("author") else "") + f" · {(v['size'] or 0) // 1024:,} KB")
            full = D.version(cat, uid, v["version"])
            c2.download_button("⬇️", full["content"] or b"", v["file_name"] or "document",
                               v["mime"] or "application/octet-stream", key=f"doc-dl-{uid}-{v['version']}")
        with st.form(f"doc-ver-{uid}", clear_on_submit=True):
            up = st.file_uploader("Add a new version", type=list(D.TYPES), key=f"doc-ver-up-{uid}")
            if st.form_submit_button("Add version") and up is not None:
                D.add_version(cat, uid, up.name, up.getvalue(), up.type or "")
                st.rerun()
    with st.expander("Rename or delete"):
        new = st.text_input("Title", value=d["title"], key=f"doc-title-{uid}")
        c1, c2 = st.columns(2)
        if c1.button("Save title", key=f"doc-ren-{uid}"):
            D.rename(cat, uid, new)
            st.rerun()
        if c2.button("Delete the document and all its versions", key=f"doc-del-{uid}"):
            D.delete(cat, uid)
            go(owner if owner.startswith("bp://") else None)
            st.rerun()


def in_documents_box(cat: Catalogue, key: str) -> None:
    design_documents_box(cat, key)
    docs = [d for d in D.documents_mentioning(cat, key) if d.get("describes") != key and d.get("owner") != key]
    if not docs:
        return
    st.caption("📄 Named in: " + ", ".join(d["title"] for d in docs[:5]))
    cols = st.columns(min(len(docs), 4))
    for i, d in enumerate(docs[:4]):
        cols[i].button(f"📄 {d['title']}", key=f"indoc-{key}-{i}", on_click=go, args=(f"doc://{d['uid']}",),
                       type="tertiary")


@cached
def _candidate_files(cat: Catalogue, root: str, recursive: bool) -> list[str]:
    """Document files in a folder that aren't read as documents yet (as strings, so the cache can keep them)."""
    from pathlib import Path

    from pdcat.folderroles import ignored_dirs
    from pdcat.scanprogress import find_files
    found, _ = find_files(Path(root), D.DOC_SUFFIXES, recursive, ignored_dirs(cat, root))
    known = D.by_path(cat)
    marked = D.design_files(cat)
    return sorted(str(p) for paths in found.values() for p in paths
                  if not p.name.startswith("~$") and D._norm(p) not in known and D._norm(p) not in marked)
