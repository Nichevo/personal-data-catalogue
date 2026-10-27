"""Adding things by hand: pasted SQL and folders of data files."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from pdcat.store import MANUAL_SOURCE, Catalogue

from .common import asset_button, go


def add_sql_page(cat: Catalogue) -> None:
    st.title("Add SQL")
    st.caption("Paste a query or snippet you want to remember. It becomes searchable, and the tables it "
               "reads and writes are linked, so it shows up under \"Read by\" on those tables.")
    snippet_form(cat)


def folders_page(cat: Catalogue) -> None:
    from pdcat.scan_files import scan_folder

    st.title("Data folders")
    st.caption("Point the catalogue at any folder and it records every CSV and Parquet file in it (size, "
               "modified date, row count, columns and value ranges), every Power BI report (.pbix, .pbit, .pbip: "
               "pages, visuals, measures and what they load) and every Excel workbook (sheets, Power Query, "
               "Microsoft Query connections, pivot tables and SQL in macros). Re-scan whenever the files change.")
    for i, f in enumerate(cat.folders()):
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 1, 1])
            with c1:
                asset_button(f"files://{f['name']}", f["name"], "folder", f"folder-{i}")
                st.caption(f"`{f['path']}`" + (" · includes subfolders" if f["recursive"] else ""))
            if c2.button("Re-scan", key=f"rescan-{i}"):
                _run_folder_scan(cat, scan_folder, f["name"], f["path"], bool(f["recursive"]))
            if c3.button("Remove", key=f"remove-{i}", help="Forget this folder and its files (notes are kept)"):
                cat.remove_folder(f["name"])
                st.rerun()

    st.subheader("Add a folder")
    with st.form("add-folder"):
        path = st.text_input("Folder path", placeholder=r"C:\Users\you\data\extracts",
                             help="Tip: in File Explorer, Shift + right-click the folder → Copy as path.")
        c1, c2 = st.columns([2, 1])
        name = c1.text_input("Short name", placeholder="e.g. forecast-extracts")
        recursive = c2.checkbox("Include subfolders", value=True)
        submitted = st.form_submit_button("Add and scan", type="primary")
    if submitted:
        path = path.strip().strip('"')
        name = (name.strip() or Path(path).name or "folder").replace("/", "-")
        taken = set(cat.sources()) - {f["name"] for f in cat.folders()}
        if not path:
            st.warning("Paste a folder path first.")
        elif name in taken or name == MANUAL_SOURCE:
            st.warning(f"The name '{name}' is already used by another source. Pick another.")
        elif not Path(path).is_dir():
            st.error(f"Can't find a folder at {path}")
        elif cat.folder_overlaps(path) and not st.session_state.get(f"overlap-ok-{path}"):
            st.session_state[f"overlap-ok-{path}"] = True
            st.warning(f"This overlaps a folder you already added ({', '.join(cat.folder_overlaps(path))}), so the "
                       "same files would be catalogued twice. Press Add and scan again to add it anyway.")
        else:
            cat.add_folder(name, path, recursive)
            _run_folder_scan(cat, scan_folder, name, path, recursive)


def _run_folder_scan(cat: Catalogue, scan_folder, name: str, path: str, recursive: bool) -> None:
    with st.spinner(f"Scanning {path} …"):
        try:
            counts = scan_folder(cat, name, path, recursive)
        except Exception as e:
            st.error(f"Scan failed: {type(e).__name__}: {e}")
            return
    msg = f"Scanned {counts['files']} file(s)" + (f" ({counts['series']} dated series)" if counts.get("series") else "") \
        + (f", {counts['reports']} Power BI report(s)" if counts.get("reports") else "") \
        + (f", {counts['workbooks']} Excel workbook(s)" if counts.get("workbooks") else "") \
        + (f", {counts['errors']} couldn't be read" if counts["errors"] else "")
    from pdcat.postscan import after_scan
    after_scan(cat)
    st.toast(msg)
    st.rerun()


def snippet_form(cat: Catalogue, existing: dict | None = None) -> None:
    attrs = existing["attrs"] if existing else {}
    databases = cat.databases()
    options = ["Not sure / any", *databases]
    current = attrs.get("database") or ""
    with st.form(f"snippet-{existing['key'] if existing else 'new'}", clear_on_submit=existing is None):
        title = st.text_input("Title", value=existing["name"] if existing else "",
                              placeholder="e.g. Weekly sales by store for the forecast")
        sql = st.text_area("SQL", value=attrs.get("sql", ""), height=220,
                           placeholder="SELECT ... FROM dbo.SalesDaily ...")
        description = st.text_area("What it's for (optional)", value=(existing or {}).get("description") or "",
                                   height=80, placeholder="Why it matters, when to use it, gotchas… (Markdown works)")
        c1, c2 = st.columns(2)
        database = c1.selectbox("Runs against", options,
                                index=options.index(current) if current in options else 0,
                                help="Where bare table names like \"Sales\" live, so they link to the right table.")
        tags = c2.text_input("Tags", value=attrs.get("tags", ""), placeholder="comma separated")
        save_col, delete_col = st.columns([1, 1])
        saved = save_col.form_submit_button("Save SQL", type="primary")
        deleted = delete_col.form_submit_button("Delete") if existing else False
    if saved:
        if not sql.strip():
            st.warning("Paste some SQL first.")
            return
        key = cat.save_snippet(title, sql, description=description, tags=tags,
                               database=None if database == options[0] else database,
                               key=existing["key"] if existing else None)
        go(key)
        st.rerun()
    if deleted:
        cat.delete_snippet(existing["key"])
        go(None)
        st.rerun()



def add_library(cat: Catalogue, name: str, path: str) -> None:
    from pdcat.config import PythonSource
    from pdcat.scan_python import scan_project

    path = path.strip().strip('"')
    name = (name.strip() or Path(path).name).replace("/", "-")
    taken = set(cat.sources()) - {l["name"] for l in cat.libraries()}
    if not path or not Path(path).is_dir():
        st.error(f"Can't find a folder at {path or '(empty)'}")
        return
    if name in taken or name == MANUAL_SOURCE:
        st.warning(f"The name '{name}' is already used by another source.")
        return
    cat.add_library(name, path)
    with st.spinner(f"Scanning {path} …"):
        counts = scan_project(cat, PythonSource(name=name, path=Path(path), role="library", data_files=False))
    from pdcat.postscan import after_scan
    after_scan(cat)
    st.toast(f"Scanned {counts['files']} file(s) in {name}")
    st.rerun()
