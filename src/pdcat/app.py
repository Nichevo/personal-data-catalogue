"""Streamlit front end: search the catalogue, open a table or script, and add notes.

Started by `pdcat ui`, which passes the catalogue path after "--".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from pdcat.export import rows_to_csv, to_csv_zip, to_json
from pdcat.review import CATEGORY_LABELS, RULES, hotspots, open_tasks, refresh_tasks, set_task_status
from pdcat.store import MANUAL_SOURCE, NOTE_TYPES, Catalogue

KIND_ICONS = {"table": "🗃️", "view": "👁️", "procedure": "⚙️", "database": "🛢️", "project": "📁",
              "script": "🐍", "notebook": "📓", "sql_script": "📜", "sql_snippet": "✍️", "note": "📝",
              "data_file": "📄", "folder": "🗂️"}
KIND_LABELS = {"data_file": "data file", "sql_snippet": "pasted SQL", "sql_script": "SQL file"}
NOTE_ICONS = {"note": "📝", "issue": "⚠️", "example": "🔢"}
RELATION_LABELS = {"reads": "Reads", "writes": "Writes", "calls": "Calls", "imports": "Imports"}


def db_path() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("PDCAT_DB"))
    args, _ = parser.parse_known_args(sys.argv[1:])
    if not args.db:
        st.error("No catalogue given. Start the app with `pdcat ui`.")
        st.stop()
    return args.db


def open_catalogue() -> Catalogue:
    # A fresh connection per run keeps SQLite happy with Streamlit's threads; it is cheap.
    return Catalogue(db_path())


def go(key: str | None, fn: str | None = None) -> None:
    if key:
        st.query_params["asset"] = key
    else:
        st.query_params.pop("asset", None)
    if fn:
        st.query_params["fn"] = fn
        st.session_state[f"zoom-{key}"] = fn
    else:
        st.query_params.pop("fn", None)
        st.session_state.pop(f"zoom-{key}", None)


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def icon(kind: str) -> str:
    return KIND_ICONS.get(kind, "•")


def asset_button(key: str, label: str, kind: str, widget_key: str) -> None:
    st.button(f"{icon(kind)} {label}", key=widget_key, on_click=go, args=(key,), type="tertiary")


def result_list(rows, prefix: str, extra=None) -> None:
    if not rows:
        st.info("Nothing here yet.")
        return
    for i, r in enumerate(rows):
        left, right = st.columns([4, 2])
        with left:
            asset_button(r["key"], r["name"], r["kind"], f"{prefix}-{i}")
            if r["description"]:
                st.caption(r["description"].splitlines()[0][:200])
        with right:
            bits = [kind_label(r["kind"]), r["source"]] if r["source"] else [kind_label(r["kind"])]
            if r["note_count"]:
                bits.append(f"📝 {r['note_count']}")
            if r["status"] == "missing":
                bits.append("⚠️ missing")
            if extra:
                bits.append(extra(r))
            st.caption(" · ".join(b for b in bits if b))


# ---- home -----------------------------------------------------------------

def home(cat: Catalogue) -> None:
    stats = cat.stats()
    with st.sidebar:
        st.header("Data catalogue")
        n_review = len(open_tasks(cat, limit=100_000))
        view = st.radio("View", ["Search", "To review", "Issue hotspots", "Code map", "Add SQL", "Data folders",
                                 "Data issues", "Needs notes", "Recent notes", "Unmatched tables"],
                        label_visibility="collapsed")
        st.divider()
        kinds = stats["kinds"]
        if kinds:
            st.caption(" · ".join(f"{icon(k)} {v} {kind_label(k)}" for k, v in sorted(kinds.items())))
        st.caption(f"📝 {stats['notes']} notes · 📋 {n_review} to review")
        for source, at in stats["last_scans"].items():
            st.caption(f"{source}: scanned {at[:16].replace('T', ' ')} UTC")
        if stats["kinds"] or stats["notes"]:
            st.divider()
            st.caption("Export everything: tables, columns, links and your notes")
            st.download_button("⬇️ CSV files (zip)", to_csv_zip(cat), "catalogue-export.zip",
                               "application/zip", width="stretch")
            st.download_button("⬇️ JSON", to_json(cat), "catalogue-export.json",
                               "application/json", width="stretch")

    if view == "To review":
        review_page(cat)
        return
    if view == "Code map":
        code_map_page(cat)
        return
    if view == "Issue hotspots":
        hotspots_page(cat)
        return
    if view == "Add SQL":
        st.title("Add SQL")
        st.caption("Paste a query or snippet you want to remember. It becomes searchable, and the tables it "
                   "reads and writes are linked, so it shows up under \"Used by\" on those tables.")
        snippet_form(cat)
        return
    if view == "Data folders":
        folders_page(cat)
        return

    if not stats["kinds"]:
        st.title("Your catalogue is empty")
        st.write("Run `pdcat scan` in a terminal to scan the databases and projects in your config, then refresh.")
        return

    if view == "Search":
        st.title("Search")
        c1, c2, c3 = st.columns([4, 1, 1])
        text = c1.text_input("Search", placeholder="table, column, script, note or tag…", label_visibility="collapsed")
        kind = c2.selectbox("Kind", ["Any kind", *cat.kinds()], label_visibility="collapsed",
                            format_func=lambda k: k if k == "Any kind" else kind_label(k))
        source = c3.selectbox("Source", ["Any source", *cat.sources()], label_visibility="collapsed")
        rows = cat.search(text, kind=None if kind == "Any kind" else kind,
                          source=None if source == "Any source" else source)
        if rows:
            st.download_button(f"⬇️ Download these {len(rows)} results (CSV)",
                               rows_to_csv([dict(r) for r in rows]), "search-results.csv", "text/csv")
        result_list(rows, "search")
    elif view == "Data issues":
        st.title("Data issues")
        st.caption("Open data issues logged on tables, columns and files. Resolve them from the item's page.")
        issues = cat.open_issues()
        if not issues:
            st.success("No open data issues.")
        for i, n in enumerate(issues):
            base, _, column = n["asset_key"].partition("#")
            with st.container(border=True):
                label = (n["asset_name"] or base) + (f" › {column}" if column else "")
                asset_button(base, label, n["asset_kind"] or "note", f"issue-{i}")
                st.markdown(n["body"])
                st.caption(_note_meta(n))
    elif view == "Needs notes":
        st.title("Needs notes")
        st.caption("Tables and views with no description and no notes, the ones your code uses most first.")
        result_list(cat.needs_documentation(), "todo",
                    extra=lambda r: f"used by {r['used_by']} file(s)" if r["used_by"] else "")
    elif view == "Recent notes":
        st.title("Recent notes")
        notes = cat.recent_notes()
        if not notes:
            st.info("No notes yet. Open any table or script to add one.")
        for i, n in enumerate(notes):
            base, _, column = n["asset_key"].partition("#")
            with st.container(border=True):
                label = (n["asset_name"] or base) + (f" › {column}" if column else "")
                asset_button(base, label, n["asset_kind"] or "note", f"recent-{i}")
                st.markdown(n["body"])
                st.caption(_note_meta(n))
    else:
        st.title("Unmatched tables")
        st.caption("Table names found in your code that don't match any scanned table. They may live on a server "
                   "you haven't added, be built dynamically, or be a false positive from the SQL detector.")
        rows = cat.unresolved_refs()
        if rows:
            st.dataframe(pd.DataFrame([{"table": r["dst_ref"][6:], "used by files": r["used_by"],
                                        "example": r["example"]} for r in rows]),
                         hide_index=True, width="stretch")
        else:
            st.success("Every table name in your code matched a scanned table.")


# ---- detail ---------------------------------------------------------------

def detail(cat: Catalogue, key: str) -> None:
    st.button("← Back to search", on_click=go, args=(None,))
    asset = cat.get_asset(key)
    if asset is None:
        # Notes can be attached to files and unmatched tables that aren't scanned assets.
        st.title(f"{icon('note')} {key}")
        st.caption("Not a scanned item. You can still keep notes on it.")
        notes_section(cat, key, [])
        return

    attrs = asset["attrs"]
    st.title(f"{icon(asset['kind'])} {asset['name']}")
    meta = [kind_label(asset["kind"]), asset["source"]]
    if attrs.get("database"):
        meta.append(attrs["database"])
    if attrs.get("row_count") is not None:
        approx = "" if attrs.get("row_count_exact", True) else "≈ "
        meta.append(f"{approx}{attrs['row_count']:,} rows")
    if attrs.get("size_bytes") is not None:
        meta.append(_size(attrs["size_bytes"]))
    if attrs.get("modified"):
        meta.append(f"modified {attrs['modified'][:16].replace('T', ' ')} UTC")
    if attrs.get("lines"):
        meta.append(f"{attrs['lines']} lines")
    st.caption(" · ".join(str(m) for m in meta) + f"  \n`{key}`")
    if asset["status"] == "missing":
        st.warning("Not found in the latest scan. It may have been dropped, renamed or deleted.")
    if attrs.get("parse_error"):
        st.warning(f"Couldn't fully read this file: {attrs['parse_error']}")
    if attrs.get("read_error"):
        st.warning(f"Couldn't read this data file: {attrs['read_error']}")
    if attrs.get("path") and asset["kind"] in ("data_file", "folder", "project"):
        st.caption(f"📍 `{attrs['path']}`")
    if asset["description"]:
        st.markdown(asset["description"])
    mine = [t for t in open_tasks(cat, limit=100_000) if t["asset_key"] == key]
    if mine:
        with st.container(border=True):
            st.markdown(f"**📋 To review ({len(mine)})**")
            for t in mine:
                task_card(cat, t, show_link=False)
    if asset["kind"] == "sql_snippet":
        st.code(attrs.get("sql", ""), language="sql")
        if attrs.get("tags"):
            st.caption(" ".join(f"#{t.strip()}" for t in attrs["tags"].split(",") if t.strip()))
        with st.expander("Edit or delete this SQL"):
            snippet_form(cat, asset)

    columns = cat.columns(key)
    notes_section(cat, key, [c["name"] for c in columns])

    if columns and not cat.profile(key):
        st.subheader("Columns")
        col_notes: dict[str, list[str]] = {}
        for n in cat.notes(key):
            _, _, col = n["asset_key"].partition("#")
            if col:
                col_notes.setdefault(col, []).append(n["body"])
        st.dataframe(pd.DataFrame([{
            "column": c["name"], "type": c["data_type"], "nullable": bool(c["nullable"]),
            "description": c["description"] or "", "notes": " | ".join(col_notes.get(c["name"], [])),
        } for c in columns]), hide_index=True, width="stretch")

    if asset["kind"] in ("table", "view", "data_file"):
        profile_section(cat, key, asset)
        compare_section(cat, key)

    lineage_section(cat, key, asset)

    if asset["kind"] in ("script", "notebook") and attrs.get("function_info"):
        functions_section(cat, key, asset)

    if asset["kind"] == "project":
        environment_section(cat, asset)
        with st.expander("Code map: how this connects to your libraries and data"):
            diagram_block(cat, asset["source"], f"proj-{key}")

    children = cat.children(key)
    if children:
        st.subheader("Contains")
        result_list([{**dict(c), "source": "", "note_count": 0} for c in children], "child")

    with st.expander("Details"):
        if attrs.get("definition"):
            st.code(attrs["definition"], language="sql")
        for label, field in (("Imports", "imports"), ("Functions", "functions"), ("Classes", "classes")):
            if attrs.get(field):
                st.markdown(f"**{label}:** " + ", ".join(f"`{x}`" for x in attrs[field]))
        hidden = ("definition", "imports", "functions", "classes", "function_info", "import_map", "environment")
        rest = {k: v for k, v in attrs.items() if k not in hidden and v not in (None, "", [])}
        st.json(rest, expanded=False)
        st.caption(f"First seen {asset['first_seen'][:10]} · last seen {asset['last_seen'][:10]}")


def code_map_page(cat: Catalogue) -> None:
    from pdcat.codemap import reached_data, uncatalogued_libraries

    st.title("Code map")
    st.caption("How your projects and home-built libraries connect, which library functions each project calls, "
               "and the tables and files reached through those chains.")
    projects = cat.conn.execute(
        "SELECT source, attrs FROM assets WHERE kind = 'project' AND status = 'active' ORDER BY source").fetchall()
    if not projects:
        st.info("No Python projects scanned yet. Add them to your config and run `pdcat scan`.")
        return
    roles = {p["source"]: json.loads(p["attrs"]).get("role", "project") for p in projects}
    choice = st.selectbox("Start from", ["Everything", *roles],
                          format_func=lambda s: s if s == "Everything" else f"{'📚' if roles[s] == 'library' else '📁'} {s}")
    project = None if choice == "Everything" else choice
    diagram_block(cat, project, "codemap")
    scripts = cat.conn.execute(
        "SELECT key, name, source FROM assets WHERE kind IN ('script', 'notebook') AND status = 'active' "
        + ("AND source = ? " if project else "") + "ORDER BY source, name", (project,) if project else ()).fetchall()
    if scripts:
        c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
        target = c1.selectbox("Zoom into a script's functions", [s["key"] for s in scripts], index=None,
                              placeholder="Pick a script…", key="codemap-zoom",
                              format_func={s["key"]: f"{s['source']} / {s['name']}" for s in scripts}.get)
        c2.button("🔍 Open", disabled=target is None, on_click=go, args=(target,), key="codemap-open")

    if project:
        rows = reached_data(cat, f"py://{project}")
        st.subheader("Data reached through libraries")
        if rows:
            st.caption("Tables and files your code doesn't touch directly but reaches by calling library code. "
                       "Where the library function itself uses the data, only that function's data is listed.")
            st.dataframe(pd.DataFrame([{"": "writes" if r["relation"] == "writes" else r["relation"],
                                        "table or file": r["data"], "via": r["via"], "library": r["library"]}
                                       for r in rows]), hide_index=True, width="stretch")
        else:
            st.caption("None found.")

    from pdcat.envinfo import libraries_overview
    st.subheader("Packages across projects")
    overview = libraries_overview(cat)
    if overview:
        st.caption("Third-party packages each project declares (requirements.txt, environment.yml, pyproject.toml…) "
                   "with the version asked for, plus packages imported without being declared. Spot projects "
                   "pinned to different versions of the same package.")
        st.dataframe(pd.DataFrame(overview), hide_index=True, width="stretch")
        st.download_button("⬇️ Packages (.csv)", rows_to_csv(overview), "packages.csv", "text/csv",
                           key="codemap-packages")
    else:
        st.caption("No third-party packages found.")

    st.subheader("Home-built libraries not catalogued yet")
    missing = uncatalogued_libraries(cat)
    located = [m for m in missing if m["path"]]
    if not located:
        st.caption("No uncatalogued home-built libraries found (from editable installs or sys.path in your code).")
    for i, m in enumerate(located):
        with st.container(border=True):
            st.markdown(f"**{m['name']}** · imported by {m['used_by']} file(s)")
            st.caption(f"Found via {m['how']}: `{m['path']}`")
            with st.form(f"addlib-{i}"):
                path = st.text_input("Library folder", value=m["path"], key=f"libpath-{i}")
                if st.form_submit_button("Add and scan library"):
                    _add_library(cat, m["name"], path)
    unplaced = [m for m in missing if not m["path"]]
    if unplaced:
        with st.expander(f"Imports I couldn't place ({len(unplaced)})"):
            st.caption("Not installed in the Python running this app, so these are probably packages from another "
                       "environment. If one is yours, add its folder below.")
            st.write(", ".join(f"`{m['name']}` ({m['used_by']})" for m in unplaced))
    with st.expander("Add another library folder"):
        with st.form("addlib-manual"):
            c1, c2 = st.columns([1, 2])
            name = c1.text_input("Name")
            path = c2.text_input("Folder", placeholder=r"C:\Users\you\code\common-utils")
            if st.form_submit_button("Add and scan library"):
                _add_library(cat, name, path)
    for lib in cat.libraries():
        c1, c2 = st.columns([4, 1])
        c1.caption(f"📚 {lib['name']} · `{lib['path']}`")
        if c2.button("Remove", key=f"rmlib-{lib['name']}"):
            cat.remove_library(lib["name"])
            st.rerun()


def _add_library(cat: Catalogue, name: str, path: str) -> None:
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
    refresh_tasks(cat)
    st.toast(f"Scanned {counts['files']} file(s) in {name}")
    st.rerun()


def diagram_block(cat: Catalogue, project: str | None, key: str) -> None:
    from pdcat.codemap import build_graph, to_dot, to_mermaid

    c1, c2 = st.columns([2, 2])
    level = c1.radio("Detail", ["projects", "modules"], horizontal=True, key=f"{key}-level",
                     format_func={"projects": "Projects and libraries", "modules": "Every file"}.get)
    include_data = c2.checkbox("Show tables and files", value=True, key=f"{key}-data")
    g = build_graph(cat, project, level=level, include_data=include_data)
    if not g.nodes:
        st.caption("Nothing to draw yet.")
        return
    if len(g.nodes) > 150:
        st.warning(f"{len(g.nodes)} boxes is a lot to draw. Pick a single project or switch to "
                   "'Projects and libraries'.")
    dot, mermaid = to_dot(g), to_mermaid(g)
    st.graphviz_chart(dot, width="stretch")
    st.caption("Blue boxes are your code (📚 purple for libraries), green cylinders are tables, yellow notes are "
               "files. Solid blue arrows are imports labelled with the functions used; dashed arrows read data; "
               "red arrows write it.")
    d1, d2, d3 = st.columns(3)
    name = project or "all"
    d1.download_button("⬇️ Mermaid (.mmd)", mermaid, f"codemap-{name}.mmd", "text/plain", key=f"{key}-mmd",
                       help="Paste into GitHub, Azure DevOps, Confluence or mermaid.live")
    d2.download_button("⬇️ Graphviz (.dot)", dot, f"codemap-{name}.dot", "text/plain", key=f"{key}-dot")
    with d3.popover("Copy Mermaid"):
        st.code(mermaid, language=None)


WHOLE = "(whole script)"


def _first_line(text: str) -> str:
    return (text or "").strip().split("\n\n")[0].replace("\n", " ")


def _signature(fn: str, info: dict) -> str:
    params = []
    for p in info.get("params", []):
        text = p["name"] + (f": {p['annotation']}" if p["annotation"] else "")
        if p["default"]:
            text += f" = {p['default']}" if p["annotation"] else f"={p['default']}"
        params.append(text)
    ret = f" -> {info['returns_annotation']}" if info.get("returns_annotation") else ""
    deco = "".join(f"@{d}\n" for d in info.get("decorators", []))
    prefix = "async def" if info.get("is_async") else "def"
    return f"{deco}{prefix} {fn.rsplit('.', 1)[-1]}({', '.join(params)}){ret}"


def functions_section(cat: Catalogue, key: str, asset: dict) -> None:
    """Zoomable view of the functions inside one script: the whole script, or one
    function with what it calls, what it returns and what calls it."""
    from pdcat.codemap import CodeIndex, function_graph, to_dot, to_mermaid

    info = asset["attrs"]["function_info"]
    zoom_key, table_key = f"zoom-{key}", f"{key}-fntable"
    if st.session_state.get(zoom_key) not in [WHOLE, *info]:
        wanted = st.query_params.get("fn")
        st.session_state[zoom_key] = wanted if wanted in info else WHOLE

    def sync() -> None:
        fn = st.session_state[zoom_key]
        if fn == WHOLE:
            st.query_params.pop("fn", None)
        else:
            st.query_params["fn"] = fn

    def focus(fn: str) -> None:
        st.session_state[zoom_key] = fn
        st.session_state.pop(table_key, None)
        sync()

    def picked() -> None:
        sel = st.session_state[table_key].selection.rows
        if sel:
            focus(list(info)[sel[0]])

    st.subheader("Inside this script")
    c1, c2, c3, c4 = st.columns([3, 2, 1.4, 1.4])
    options = [WHOLE, *info]
    fn = c1.selectbox("Zoom", options, key=zoom_key, on_change=sync,
                      format_func=lambda f: "🔭 Whole script" if f == WHOLE else f"🔍 {f}")
    depth = c2.slider("Levels of sub-functions", 1, 5, 2, key=f"{key}-depth", disabled=fn == WHOLE,
                      help="How far to follow calls from the chosen function, into your libraries too.")
    include_data = c3.checkbox("Tables and files", value=True, key=f"{key}-fndata")
    include_other = c4.checkbox("Other calls", value=False, key=f"{key}-fnother",
                                help="Calls into packages like pandas, shown as plain text.")
    idx = CodeIndex(cat)
    focus_fn = None if fn == WHOLE else fn
    g = function_graph(cat, key, focus_fn, depth=depth, include_data=include_data,
                       include_other=include_other, index=idx)
    if g.nodes:
        dot = to_dot(g)
        st.graphviz_chart(dot, width="stretch")
        st.caption("Blue boxes are functions in this file, purple ones are in your other files and libraries"
                   + (", the yellow one is the function you zoomed into" if focus_fn else "")
                   + ". Arrows point from caller to called; dashed arrows read data, red arrows write it.")
        d1, d2, d3 = st.columns([1, 1, 2])
        stem = Path(asset["name"]).stem + (f"-{focus_fn}" if focus_fn else "")
        d1.download_button("⬇️ Mermaid", to_mermaid(g), f"{stem}.mmd", "text/plain", key=f"{key}-fnmmd")
        d2.download_button("⬇️ Graphviz", dot, f"{stem}.dot", "text/plain", key=f"{key}-fndot")
        if focus_fn:
            d3.button("🔭 Zoom out to the whole script", key=f"{key}-zoomout", on_click=focus, args=(WHOLE,))
        elif asset["parent"]:
            d3.button("🔭 Zoom out to the whole project", key=f"{key}-toproject", on_click=go,
                      args=(asset["parent"],))

    if focus_fn is None:
        rows = [{"function": name, "line": f.get("line"), "parameters": ", ".join(p["name"] for p in f["params"]),
                 "returns": f.get("returns_annotation") or ("yields" if f.get("yields") else
                                                              "; ".join(f.get("returns", [])[:2])),
                 "calls": len(f.get("calls", [])), "what it does": _first_line(f.get("doc", ""))}
                for name, f in info.items()]
        st.caption("Pick a row to zoom into that function.")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", on_select=picked,
                     selection_mode="single-row", key=table_key)
        return

    f = info[focus_fn]
    with st.container(border=True):
        where = f"cell {f['cell']}, " if f.get("cell") else ""
        st.markdown(f"**{focus_fn}** · {where}lines {f['line']}–{f['end_line']}")
        if focus_fn not in ("(script)",) and not focus_fn.startswith("(cell"):
            st.code(_signature(focus_fn, f), language="python")
        if f.get("doc"):
            st.markdown(f["doc"])
        else:
            st.caption("No docstring.")
        left, right = st.columns(2)
        with left:
            st.markdown("**Calls**")
            others = []
            for i, r in enumerate(idx.calls(key, focus_fn)):
                if r[0] == "local":
                    st.button(f"🔍 {r[2]}", key=f"{key}-call-{i}", type="tertiary", on_click=focus, args=(r[2],),
                              help=_first_line(info.get(r[2], {}).get("doc", "")) or None)
                elif r[0] == "lib":
                    target = idx.assets.get(r[1], {})
                    doc = _first_line(idx.functions(r[1]).get(r[2], {}).get("doc", ""))
                    st.button(f"📚 {r[2]} · {target.get('source', '')}/{target.get('name', '')}",
                              key=f"{key}-call-{i}", type="tertiary", on_click=go, args=(r[1], r[2]),
                              help=doc or None)
                else:
                    others.append(r[1])
            if others:
                st.caption("Other: " + ", ".join(f"`{o}`" for o in others))
            if not f.get("calls"):
                st.caption("Calls nothing.")
        with right:
            st.markdown("**Returns**")
            if f.get("returns_annotation"):
                st.caption(f"Declared type: `{f['returns_annotation']}`")
            if f.get("yields"):
                st.caption("Yields values (a generator).")
            for r in f.get("returns", []):
                st.code(r, language="python")
            if not (f.get("returns") or f.get("yields")):
                st.caption("Nothing (returns None).")
            data = idx.data(key, focus_fn)
            if data:
                st.markdown("**Data**")
                for i, d in enumerate(data):
                    name = d["ref"].split(":", 1)[-1]
                    st.button(f"{'📄' if d['ref'].startswith('file:') else '🗃️'} {d['relation']} {name}",
                              key=f"{key}-data-{i}", type="tertiary", on_click=go, args=(d["key"] or d["ref"],))
            callers = idx.callers(key, focus_fn)
            st.markdown("**Called by**")
            if not callers:
                st.caption("Nothing found in your catalogued code.")
            for i, (ck, cfn) in enumerate(callers):
                if ck == key:
                    st.button(f"🔍 {cfn}", key=f"{key}-caller-{i}", type="tertiary", on_click=focus, args=(cfn,))
                else:
                    a = idx.assets.get(ck, {})
                    st.button(f"🐍 {cfn} · {a.get('source', '')}/{a.get('name', '')}", key=f"{key}-caller-{i}",
                              type="tertiary", on_click=go, args=(ck, cfn))


def environment_section(cat: Catalogue, asset: dict) -> None:
    from pdcat.envinfo import project_environment

    env = project_environment(cat, asset["source"])
    st.subheader("Environment and libraries")
    if env["files"]:
        st.caption("Read from " + ", ".join(f"`{f}`" for f in env["files"]))
    else:
        st.caption("No requirements.txt, environment.yml, pyproject.toml, setup.py or Pipfile found, "
                   "so everything below comes from the imports in the code.")
    facts = [f"Python {p['version']} ({p['file']})" for p in env["python"]]
    facts += [f"conda env **{c['name'] or '?'}**" + (f" · channels {', '.join(c['channels'])}" if c["channels"] else "")
              for c in env["conda_envs"]]
    facts += [f"local install `{e}`" for e in env["editable"]]
    if facts:
        st.markdown("  \n".join(facts))
    tabs = st.tabs([f"Declared ({len(env['deps'])})", f"Imported, not declared ({len(env['undeclared'])})",
                    f"Declared, not imported ({len(env['unused'])})"])
    with tabs[0]:
        if env["deps"]:
            st.dataframe(pd.DataFrame([{"package": d["name"], "version": d["version"], "from": d["file"],
                                        "via": d["manager"], "group": d.get("group", "")} for d in env["deps"]]),
                         hide_index=True, width="stretch")
        else:
            st.caption("Nothing declared.")
    with tabs[1]:
        if env["undeclared"]:
            st.caption("Third-party imports with no matching entry in the environment files. Worth adding so the "
                       "project can be rebuilt.")
            st.dataframe(pd.DataFrame(env["undeclared"]).rename(columns={"examples": "imported in"}),
                         hide_index=True, width="stretch")
        else:
            st.caption("Every third-party import is declared.")
    with tabs[2]:
        if env["unused"]:
            st.caption("Declared but never imported in the code (tools like pytest are left out). Could be a "
                       "leftover, or a plug-in that's loaded indirectly.")
            st.dataframe(pd.DataFrame([{"package": d["name"], "version": d["version"], "from": d["file"]}
                                       for d in env["unused"]]), hide_index=True, width="stretch")
        else:
            st.caption("Nothing unused.")


def review_page(cat: Catalogue) -> None:
    st.title("To review")
    st.caption("Things that need your eyes, most important first: what your code uses, big tables and tables "
               "with open issues come first. Comment on an item, mark it done, snooze it for 30 days or dismiss it. "
               "The list refreshes after every scan; fixed problems drop off by themselves.")
    refresh_tasks(cat)
    all_open = open_tasks(cat, limit=100_000)
    counts = {c: sum(t["category"] == c for t in all_open) for c in CATEGORY_LABELS}
    cols = st.columns(len(counts) + 1)
    cols[0].metric("Open", len(all_open))
    for col, (c, n) in zip(cols[1:], counts.items()):
        col.metric(CATEGORY_LABELS[c], n)

    f1, f2, f3 = st.columns([2, 2, 2])
    category = f1.selectbox("Type", ["All", *CATEGORY_LABELS], format_func=lambda c: CATEGORY_LABELS.get(c, c))
    rule = f2.selectbox("Check", ["All", *RULES], format_func=lambda r: RULES[r][0] if r in RULES else r)
    only_used = f3.checkbox("Only items my code uses", help="Handy on a large database: start with what you rely on.")
    tasks = open_tasks(cat, category=None if category == "All" else category,
                       rule=None if rule == "All" else rule, only_used=only_used, limit=100_000)
    if not tasks:
        st.success("Nothing to review here.")
        return
    shown = st.session_state.get("review_shown", 30)
    st.download_button(f"⬇️ Download this list ({len(tasks)} items, CSV)",
                       rows_to_csv([{k: t[k] for k in ("title", "detail", "rule", "asset_key", "priority", "used_by")}
                                    for t in tasks]), "review-list.csv", "text/csv")
    for t in tasks[:shown]:
        task_card(cat, t)
    if len(tasks) > shown:
        if st.button(f"Show more ({len(tasks) - shown} left)"):
            st.session_state["review_shown"] = shown + 30
            st.rerun()


def task_card(cat: Catalogue, t: dict, show_link: bool = True) -> None:
    tid = t["id"]
    with st.container(border=True):
        label, category, _ = RULES.get(t["rule"], (t["rule"], "warning", 0))
        icon_ = {"docs": "📝", "change": "🔄", "warning": "⚠️"}.get(category, "•")
        if show_link:
            base = t["asset_key"]
            st.button(f"{icon_} {t['title']}", key=f"open-{tid}", on_click=go, args=(base,), type="tertiary")
        else:
            st.markdown(f"{icon_} {t['title']}")
        meta = [label]
        if t.get("used_by"):
            meta.append(f"used by {t['used_by']} file(s)")
        if t.get("source"):
            meta.append(t["source"])
        st.caption(" · ".join(meta))
        if t["detail"]:
            st.write(t["detail"])
        c1, c2, c3, c4 = st.columns(4)
        with c1.popover("💬 Comment"):
            with st.form(f"comment-{tid}", clear_on_submit=True):
                note_type = st.radio("Type", list(NOTE_TYPES), format_func=NOTE_TYPES.get, horizontal=True,
                                     index=1 if category == "warning" else 0)
                body = st.text_area("Your comment", height=120,
                                    placeholder="What you found, what it means, what to watch for…")
                done = st.checkbox("Mark this task done", value=True)
                if st.form_submit_button("Save", type="primary") and body.strip():
                    cat.add_note(t["asset_key"], body, "review", note_type)
                    if done:
                        set_task_status(cat, tid, "done")
                    st.rerun()
        if c2.button("✓ Done", key=f"done-{tid}"):
            set_task_status(cat, tid, "done")
            st.rerun()
        if c3.button("💤 Snooze 30 days", key=f"snooze-{tid}"):
            set_task_status(cat, tid, "snoozed")
            st.rerun()
        if c4.button("Dismiss", key=f"dismiss-{tid}", help="Not a problem; don't show this again"):
            set_task_status(cat, tid, "dismissed")
            st.rerun()


def hotspots_page(cat: Catalogue) -> None:
    st.title("Issue hotspots")
    st.caption("Where problems cluster. Score = 5 × open data issues + 3 × automatic warnings (odd values, "
               "files that differ from their source, stale files) + 2 × disappeared + 1 × undocumented.")
    refresh_tasks(cat)
    areas, items = hotspots(cat)
    if not len(areas):
        st.info("Nothing scanned yet.")
        return
    st.subheader("Areas")
    st.caption("Schemas for SQL tables; top-level folders for files and scripts.")
    st.dataframe(areas, hide_index=True, width="stretch")
    st.subheader("Worst items")
    if not len(items):
        st.success("No hotspots: no open issues or warnings.")
        return
    for i, r in items.head(30).iterrows():
        c1, c2 = st.columns([3, 2])
        with c1:
            asset_button(r["key"], r["name"], r["kind"], f"hot-{i}")
            st.caption(r["area"])
        bits = [f"score {r['score']}"]
        if r["open issues"]:
            bits.append(f"⚠️ {r['open issues']} open issue(s)")
        if r["warnings"]:
            bits.append(f"{r['warnings']} warning(s)")
        if r["missing"]:
            bits.append("disappeared")
        if r["undocumented"]:
            bits.append("undocumented")
        c2.caption(" · ".join(bits))


def profile_section(cat: Catalogue, key: str, asset: dict) -> None:
    profile = cat.profile(key)
    st.subheader("Columns and values")
    if not profile:
        if asset["kind"] in ("table", "view"):
            st.caption(f"No value profile yet. Run `pdcat profile {asset['name']}` (or `pdcat profile --used`) "
                       "to measure ranges, nulls and common values. It only reads.")
        return
    st.caption(f"Measured {profile[0]['profiled_at'][:16].replace('T', ' ')} UTC · {profile[0]['method']}")
    descriptions = {c["name"]: c["description"] for c in cat.columns(key)}
    st.dataframe(profile_frame(profile, descriptions), hide_index=True, width="stretch")


def profile_frame(profile, descriptions: dict | None = None) -> pd.DataFrame:
    rows = []
    for p in profile:
        n = p["row_count"] or 0
        row = {
            "column": p["column_name"], "type": p["data_type"],
            "nulls": f"{p['null_count']:,} ({p['null_count'] / n:.0%})" if n and p["null_count"] is not None else "",
            "distinct": p["distinct_count"], "min": p["min_value"] or "", "max": p["max_value"] or "",
            "mean": f"{p['mean_value']:,.3f}".rstrip("0").rstrip(".") if p["mean_value"] is not None else "",
            "most common": ", ".join(f"{v} ({c:,})" for v, c in json.loads(p["top_values"])),
        }
        if descriptions is not None:
            row["description"] = descriptions.get(p["column_name"]) or ""
        rows.append(row)
    return pd.DataFrame(rows)


def compare_section(cat: Catalogue, key: str) -> None:
    """Side by side with another table or file, e.g. the SQL table a CSV extract was made from."""
    if not cat.profile(key):
        return
    profiled = sorted(cat.profiled_keys() - {key})
    if not profiled:
        return
    upstream = [k for k in cat.upstream(key) if k in profiled]
    names = {k: (cat.get_asset(k) or {}).get("name", k) for k in profiled}
    options = upstream + [k for k in profiled if k not in upstream]
    with st.expander("Compare with another table or file" + (" (suggested: where this data comes from)" if upstream else "")):
        other = st.selectbox("Compare with", options, format_func=lambda k: ("⬆️ " if k in upstream else "") + names[k],
                             key=f"compare-{key}")
        if other:
            left, right = cat.profile(key), cat.profile(other)
            st.caption(f"Rows: **{left[0]['row_count'] or 0:,}** here vs **{right[0]['row_count'] or 0:,}** in "
                       f"{names[other]}")
            st.dataframe(compare_frame(left, right), hide_index=True, width="stretch")


def compare_frame(left, right) -> pd.DataFrame:
    by_name = {p["column_name"].lower(): p for p in right}
    rows, seen = [], set()

    def rng(p):
        return f"{p['min_value']} → {p['max_value']}" if p and p["min_value"] is not None else ""

    def nulls(p):
        n = p["row_count"] or 0 if p else 0
        return f"{p['null_count'] / n:.0%}" if p and n and p["null_count"] is not None else ""

    for p in left:
        q = by_name.get(p["column_name"].lower())
        seen.add(p["column_name"].lower())
        rows.append({"column": p["column_name"], "in": "both" if q else "here only",
                     "type here": p["data_type"], "type there": q["data_type"] if q else "",
                     "range here": rng(p), "range there": rng(q),
                     "nulls here": nulls(p), "nulls there": nulls(q),
                     "distinct here": p["distinct_count"], "distinct there": q["distinct_count"] if q else None})
    for q in right:
        if q["column_name"].lower() not in seen:
            rows.append({"column": q["column_name"], "in": "there only", "type here": "", "type there": q["data_type"],
                         "range here": "", "range there": rng(q), "nulls here": "", "nulls there": nulls(q),
                         "distinct here": None, "distinct there": q["distinct_count"]})
    return pd.DataFrame(rows)


def folders_page(cat: Catalogue) -> None:
    from pdcat.scan_files import scan_folder

    st.title("Data folders")
    st.caption("Point the catalogue at any folder and it records every CSV and Parquet file in it: size, "
               "modified date, row count, columns and value ranges. Re-scan whenever the files change.")
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
    msg = f"Scanned {counts['files']} file(s)" + (f", {counts['errors']} couldn't be read" if counts["errors"] else "")
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


def lineage_section(cat: Catalogue, key: str, asset: dict) -> None:
    uses = cat.links_from_children(key) if asset["kind"] == "project" else cat.links_from(key)
    used_by = cat.links_to(key)
    if not uses and not used_by:
        return
    left, right = st.columns(2)
    with left:
        st.subheader("Uses")
        if not uses:
            st.caption("Nothing found.")
        seen = set()
        for i, l in enumerate(uses):
            ident = (l["relation"], l["dst_key"] or l["dst_ref"])
            if ident in seen:
                continue
            seen.add(ident)
            rel = RELATION_LABELS.get(l["relation"], l["relation"])
            if l["dst_key"]:
                c1, c2 = st.columns([1, 4])
                c1.caption(rel)
                with c2:
                    asset_button(l["dst_key"], l["dst_name"] or l["dst_key"], l["dst_kind"] or "table", f"uses-{i}")
                    if l["relation"] == "imports" and l["evidence"]:
                        st.caption(f"uses {l['evidence']}")
            else:
                kind, _, name = l["dst_ref"].partition(":")
                what = "file" if kind == "file" else "unmatched table"
                c1, c2 = st.columns([1, 4])
                c1.caption(rel)
                with c2:
                    st.button(f"{'📄' if kind == 'file' else '❔'} {name}", key=f"uses-{i}", type="tertiary",
                              on_click=go, args=(l["dst_ref"],), help=f"{what} · {l['evidence'] or ''}")
    with right:
        st.subheader("Used by")
        if not used_by:
            st.caption("Nothing found.")
        for i, l in enumerate(used_by):
            rel = RELATION_LABELS.get(l["relation"], l["relation"]).lower()
            c1, c2 = st.columns([4, 2])
            with c1:
                asset_button(l["src_key"], l["src_name"] or l["src_key"], l["src_kind"] or "script", f"usedby-{i}")
            c2.caption(f"{rel} {l['evidence']}" if l["relation"] == "imports" and l["evidence"]
                       else f"{rel} · {l['evidence'] or ''}")


def notes_section(cat: Catalogue, key: str, column_names: list[str]) -> None:
    st.subheader("Notes")
    notes = cat.notes(key)
    for n in notes:
        _, _, col = n["asset_key"].partition("#")
        resolved = n["note_type"] == "issue" and n["status"] == "resolved"
        with st.container(border=True):
            label = NOTE_TYPES[n["note_type"]] + (" (resolved)" if resolved else "")
            st.caption(f"{NOTE_ICONS[n['note_type']]} **{label}**" + (f" · column **{col}**" if col else ""))
            if n["note_type"] == "example":
                st.code(n["body"], language=None)
            else:
                st.markdown(f"~~{n['body']}~~" if resolved and "\n" not in n["body"] else n["body"])
            st.caption(_note_meta(n))
            b1, b2, _ = st.columns([1, 1, 4])
            if n["note_type"] == "issue":
                if b2.button("Reopen" if resolved else "Mark resolved", key=f"status-{n['id']}"):
                    cat.set_issue_status(n["id"], "open" if resolved else "resolved")
                    st.rerun()
            with b1.popover("Edit"):
                with st.form(f"edit-{n['id']}"):
                    kinds = list(NOTE_TYPES)
                    note_type = st.radio("Type", kinds, index=kinds.index(n["note_type"]),
                                         format_func=NOTE_TYPES.get, horizontal=True)
                    body = st.text_area("Note", value=n["body"], height=150)
                    tags = st.text_input("Tags", value=n["tags"])
                    save, delete = st.columns(2)
                    if save.form_submit_button("Save", type="primary") and body.strip():
                        cat.update_note(n["id"], body, tags, note_type)
                        st.rerun()
                    if delete.form_submit_button("Delete"):
                        cat.delete_note(n["id"])
                        st.rerun()

    with st.form(f"add-note-{key}", clear_on_submit=True):
        c1, c2 = st.columns([2, 3])
        note_type = c1.radio("Type", list(NOTE_TYPES), format_func=NOTE_TYPES.get, horizontal=True,
                             help="Data issue: something wrong or odd in the data, tracked until resolved. "
                                  "Example values: paste sample rows, value ranges or codes; layout is kept.")
        about = "This item"
        if column_names:
            about = c2.selectbox("About", ["This item", *column_names])
        body = st.text_area("Add a note", placeholder="What is this for? Gotchas, refresh timing, a data problem, "
                            "pasted example values… (Markdown works)", height=120)
        tags = st.text_input("Tags", placeholder="comma separated, e.g. forecasting, slow, needs-check")
        if st.form_submit_button("Add note", type="primary"):
            if body.strip():
                target = key if about == "This item" else f"{key}#{about}"
                cat.add_note(target, body, tags, note_type)
                st.toast("Note saved")
                st.rerun()
            else:
                st.warning("Write something first.")


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


def _note_meta(n) -> str:
    when = n["updated_at"][:16].replace("T", " ")
    tags = " ".join(f"#{t.strip()}" for t in n["tags"].split(",") if t.strip())
    return f"{when} UTC" + (f" · {tags}" if tags else "")


def main() -> None:
    st.set_page_config(page_title="Data catalogue", page_icon="🗃️", layout="wide")
    cat = open_catalogue()
    try:
        key = st.query_params.get("asset")
        if key:
            detail(cat, key)
        else:
            home(cat)
    finally:
        cat.close()


main()
