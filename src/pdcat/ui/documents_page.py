"""Reference documents: the list on a business project, a page per document, and "In documents" on items."""
from __future__ import annotations

import streamlit as st

from pdcat import documents as D
from pdcat.store import Catalogue

from .common import asset_button, go, icon

MAX_SHOWN = 30_000


def documents_section(cat: Catalogue, owner: str) -> None:
    st.subheader("Reference documents")
    docs = D.documents_for(cat, owner)
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
    docs = D.documents_mentioning(cat, key)
    if not docs:
        return
    st.caption("📄 Named in: " + ", ".join(d["title"] for d in docs[:5]))
    cols = st.columns(min(len(docs), 4))
    for i, d in enumerate(docs[:4]):
        cols[i].button(f"📄 {d['title']}", key=f"indoc-{key}-{i}", on_click=go, args=(f"doc://{d['uid']}",),
                       type="tertiary")
