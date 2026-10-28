"""Adding things by hand: pasted SQL and folders of data files."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from pdcat.store import MANUAL_SOURCE, Catalogue

from .common import asset_button, go, page_title


def add_sql_page(cat: Catalogue) -> None:
    page_title("Add SQL")
    st.caption("Paste a query or snippet you want to remember. It becomes searchable, and the tables it "
               "reads and writes are linked, so it shows up under \"Read by\" on those tables.")
    snippet_form(cat)


def folders_page(cat: Catalogue) -> None:
    from pdcat.scan_files import scan_folder

    page_title("Data folders")
    st.caption("Point the catalogue at any folder and it records every CSV and Parquet file in it (size, "
               "modified date, row count, columns and value ranges), every Power BI report (.pbix, .pbit, .pbip: "
               "pages, visuals, measures and what they load) and every Excel workbook (sheets, Power Query, "
               "Microsoft Query connections, pivot tables and SQL in macros). Re-scan whenever the files change.")
    for i, f in enumerate(cat.folders()):
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 1, 1])
            with c1:
                asset_button(f"files://{f['name']}", f["name"], "folder", f"folder-{i}")
                from pdcat.folderroles import mode_of
                kind = {"master": " · master folder (each project folder is its own project)",
                        "single": " · a single project"}.get(mode_of(cat, f["path"]) or "", "")
                st.caption(f"`{f['path']}`" + (" · includes subfolders" if f["recursive"] else "") + kind)
            if c2.button("Re-scan", key=f"rescan-{i}"):
                _run_folder_scan(cat, scan_folder, f["name"], f["path"], bool(f["recursive"]))
            if c3.button("Remove", key=f"remove-{i}", help="Forget this folder and its files (notes are kept)"):
                for lib in cat.libraries():
                    if lib["from_folder"] == f["name"]:
                        cat.remove_library(lib["name"])
                cat.remove_folder(f["name"])
                st.rerun()
            code = [lib for lib in cat.libraries() if lib["from_folder"] == f["name"]]
            if code:
                st.caption("🐍 Code catalogued as " + ", ".join(f"**{lib['name']}**" for lib in code))
            roles_editor(cat, f["path"], f["name"])

    st.subheader("Add a folder")
    path = st.text_input("Folder path", placeholder=r"C:\Users\you\data\extracts", key="add-folder-path",
                         help="Tip: in File Explorer, Shift + right-click the folder → Copy as path.").strip().strip('"')
    found = None
    if path and Path(path).is_dir():
        from pdcat.folderroles import analyse, as_mode
        found = folder_preview(path, analyse(path))
        found = as_mode(found, path, st.session_state.get(f"add-folder-mode-{path}", found["mode"]))
    elif path:
        st.error(f"Can't find a folder at {path}")
    with st.form("add-folder"):
        c1, c2 = st.columns([2, 1])
        name = c1.text_input("Short name", placeholder="e.g. forecast-extracts")
        recursive = c2.checkbox("Include subfolders", value=True)
        with_code = st.checkbox("Also catalogue the Python code in it (scripts, notebooks, .sql files)", value=True,
                                help="As one code project for a single project, or one per project folder for a "
                                     "master folder.")
        submitted = st.form_submit_button("Add and scan", type="primary", disabled=found is None)
    if submitted and found is not None:
        name = (name.strip() or Path(path).name or "folder").replace("/", "-")
        taken = set(cat.sources()) - {f["name"] for f in cat.folders()}
        if name in taken or name == MANUAL_SOURCE:
            st.warning(f"The name '{name}' is already used by another source. Pick another.")
        elif cat.folder_overlaps(path) and not st.session_state.get(f"overlap-ok-{path}"):
            st.session_state[f"overlap-ok-{path}"] = True
            st.warning(f"This overlaps a folder you already added ({', '.join(cat.folder_overlaps(path))}), so the "
                       "same files would be catalogued twice. Press Add and scan again to add it anyway.")
        else:
            cat.add_folder(name, path, recursive)
            from pdcat.folderroles import save
            save(cat, path, found["roles"], confirmed=False, mode=found["mode"])
            if with_code:
                add_code_projects(cat, name, path, found)
            _run_folder_scan(cat, scan_folder, name, path, recursive)


MODES = {"master": "A master folder: each project folder in it is its own project",
         "single": "A single project: everything in it belongs together"}


def folder_preview(path: str, found: dict) -> dict:
    """What the app makes of a folder before it's added: master folder or single project, and why."""
    with st.container(border=True):
        st.markdown("**How this folder will be treated**")
        st.caption("A **master folder** holds several separate projects side by side (for example `forecasting/`, "
                   "`pricing/`, `stock/`, each with its own README, environment file or git repository). Each one "
                   "becomes its own code project, so the code map and search keep them apart. A **single project** is "
                   "one piece of work whose subfolders (`code/`, `data/`, `raw_data/`, `output/`, `archive/`…) "
                   "belong together; each subfolder gets a role instead. Either way, all the data files in the "
                   "folder are catalogued as one data folder.")
        if found["mode"] == "master":
            st.markdown(f"It looks like a **master folder**: {len(found['projects'])} project folders found.")
            for p in found["projects"]:
                st.caption(f"📁 {p}/ ({found['why'].get(p, '')})")
        else:
            st.markdown("It looks like a **single project**: fewer than two subfolders look like projects of their "
                        "own (a project folder has at least two of: README, environment.yml or requirements.txt, "
                        "pyproject.toml, setup.py, src/, or its own .git).")
        st.radio("Treat it as", list(MODES), index=list(MODES).index(found["mode"]), format_func=MODES.get,
                 key=f"add-folder-mode-{path}")
        roles = {k: v for k, v in found["roles"].items() if v != "project"}
        if roles:
            st.caption("Suggested roles for subfolders (you can change them after adding): " + ", ".join(
                f"`{k}/` {v}" for k, v in sorted(roles.items())[:12]) + (" …" if len(roles) > 12 else ""))
    return found


def add_code_projects(cat: Catalogue, name: str, path: str, found: dict) -> None:
    """Catalogue the Python code in a data folder: one project per project folder in a master folder."""
    from pdcat.config import PythonSource
    from pdcat.scan_python import scan_project
    root = Path(path)
    if not any(root.rglob("*.py")) and not any(root.rglob("*.ipynb")):
        return
    targets = [(f"{name}-{sub}", root / sub) for sub in found["projects"]] if found["mode"] == "master" \
        else [(f"{name}-code", root)]
    with st.spinner("Reading the code …"):
        for lib_name, lib_path in targets:
            cat.add_library(lib_name, str(lib_path), role="project", from_folder=name)
            try:
                scan_project(cat, PythonSource(name=lib_name, path=lib_path, role="project", data_files=False))
            except Exception as e:  # one unreadable project must not stop the folder
                st.warning(f"Couldn't read the code in {lib_path}: {type(e).__name__}: {e}")


def roles_editor(cat: Catalogue, path: str, source: str) -> None:
    """The folder's role (master folder or single project) and each subfolder's role, to confirm or change."""
    import pandas as pd

    from pdcat.folderroles import ROLES, analyse, mode_of, roles_under, save
    stored = roles_under(cat, path)
    pending = sum(not v.get("confirmed") for v in stored.values())
    mode = mode_of(cat, path)
    label = "📂 Folder roles" + (f" ({pending} to confirm)" if pending else f" ({len(stored)})" if stored else "")
    with st.expander(label, expanded=bool(pending)):
        if not stored and not mode:
            if st.button("Suggest roles for this folder's subfolders", key=f"roles-suggest-{source}"):
                found = analyse(path)
                save(cat, path, found["roles"], confirmed=False, mode=found["mode"])
                st.rerun()
            return
        modes = {"single": "A single project: its subfolders belong together",
                 "master": "A master folder: each project folder is its own project"}
        new_mode = st.radio("This folder is", list(modes), index=list(modes).index(mode or "single"),
                            format_func=modes.get, key=f"roles-mode-{source}")
        df = pd.DataFrame([{"folder": rel + "/", "role": v["role"],
                            "status": "confirmed" if v.get("confirmed") else "suggested"}
                           for rel, v in sorted(stored.items())])
        if df.empty:
            df = pd.DataFrame(columns=["folder", "role", "status"])
        edited = st.data_editor(df, hide_index=True, width="stretch", key=f"roles-ed-{source}", num_rows="dynamic",
                                column_config={"role": st.column_config.SelectboxColumn(
                                    "role", options=[r for r in ROLES if r], help="; ".join(
                                        f"{k}: {v}" for k, v in ROLES.items() if k)),
                                    "status": st.column_config.TextColumn(disabled=True)})
        st.caption("Roles apply to everything below the folder. Archive folders are treated as old (hidden from "
                   "the code map); ignored folders aren't catalogued from the next scan; raw data isn't expected "
                   "to be made by your scripts. Add a row to give any folder at any depth a role.")
        if st.button("Confirm these roles", key=f"roles-ok-{source}", type="primary"):
            roles = {str(r["folder"]).strip().strip("/"): r["role"] or "" for _, r in edited.iterrows()
                     if str(r["folder"]).strip()}
            gone = {rel: "" for rel in stored if rel not in roles}
            save(cat, path, {**gone, **roles}, confirmed=True, mode=new_mode)
            st.toast("Saved. Rescan the folder for ignored folders to drop out.")
            st.rerun()


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
        + (f", {counts['access']} Access database(s)" if counts.get("access") else "") \
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
        from pdcat import sqllibrary
        with st.spinner("Adding it to the SQL library…"):
            sqllibrary.build_library(cat)  # so its tables list it and its page can draw its diagram
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
