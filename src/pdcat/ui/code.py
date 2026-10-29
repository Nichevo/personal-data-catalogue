"""Code pages: the code map, a script's functions, a project's environment, and third-party packages."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from pdcat.export import rows_to_csv
from pdcat.store import Catalogue

from .common import asset_button, cached, go
from .sources import add_library


@cached
def _overview(cat: Catalogue):
    from pdcat.envinfo import libraries_overview
    return libraries_overview(cat)


@cached
def _packages(cat: Catalogue, include_stdlib: bool = False):
    from pdcat.libclass import packages_used
    return packages_used(cat, include_stdlib=include_stdlib)


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

    st.subheader("Packages across projects")
    overview = _overview(cat)
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
                    add_library(cat, m["name"], path)
    unplaced = [m for m in missing if not m["path"]]
    if unplaced:
        with st.expander(f"Imports I couldn't place ({len(unplaced)})"):
            st.caption("Not declared by any scanned project, not a well-known package and not installed here. "
                       "If one is yours, add its folder below.")
            st.write(", ".join(f"`{m['name']}` ({m['used_by']})" for m in unplaced))
    with st.expander("Add another library folder"):
        with st.form("addlib-manual"):
            c1, c2 = st.columns([1, 2])
            name = c1.text_input("Name")
            path = c2.text_input("Folder", placeholder=r"C:\Users\you\code\common-utils")
            if st.form_submit_button("Add and scan library"):
                add_library(cat, name, path)
    for lib in cat.libraries():
        c1, c2 = st.columns([4, 1])
        c1.caption(f"📚 {lib['name']} · `{lib['path']}`")
        if c2.button("Remove", key=f"rmlib-{lib['name']}"):
            cat.remove_library(lib["name"])
            st.rerun()


def diagram_block(cat: Catalogue, project: str | None, key: str) -> None:
    from pdcat.codemap import build_graph, cyto_elements, to_dot, to_mermaid

    from .graphview import graph_view
    c1, c2, c3 = st.columns([2, 1.2, 1.2])
    level = c1.radio("Detail", ["projects", "modules"], horizontal=True, key=f"{key}-level",
                     format_func={"projects": "Projects and libraries", "modules": "Every file"}.get)
    include_data = c2.checkbox("Tables and files", value=True, key=f"{key}-data")
    packages = c3.checkbox("Third-party packages", value=False, key=f"{key}-pkgs", disabled=level != "modules")
    show_old = st.checkbox("Show old items", value=False, key=f"{key}-old",
                           help="Projects, scripts and files marked old are hidden unless this is ticked. Older "
                                "versions of scripts and items marked not relevant are always left out.")
    elements = cyto_elements(cat, project, level=level, include_data=include_data, include_packages=packages,
                             show_old=show_old)
    if not elements:
        st.caption("Nothing to draw yet.")
        return
    graph_view(cat, elements, key=f"{key}-{project or 'all'}-{level}", name=f"codemap-{project or 'all'}")
    st.caption("Blue boxes are your code (purple for libraries), green barrels are tables, yellow tags are files, "
               "grey boxes are packages. Blue arrows are imports labelled with the functions used; \"runs\" arrows "
               "start another script; dashed arrows read data; red arrows write it. Groups (projects, folders, databases) open and close with a click.")
    g = build_graph(cat, project, level=level, include_data=include_data, show_old=show_old)
    dot, mermaid = to_dot(g), to_mermaid(g)
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


def packages_page(cat: Catalogue) -> None:
    from pdcat.libclass import CLASS_LABELS
    st.title("Packages")
    st.caption("Third-party packages your code imports, sorted out from your own code and home-built libraries. "
               "A package counts as third-party when a scanned project declares it, when it's a well-known "
               "package, or when it's installed here. Pick a row to see who uses it.")
    show_std = st.checkbox("Include the Python standard library", value=False)
    rows = _packages(cat, show_std)
    if not rows:
        st.info("No third-party imports found.")
        return
    df = pd.DataFrame(rows)
    df["kind"] = df["kind"].map(CLASS_LABELS)
    event = st.dataframe(df.drop(columns=["key"]), hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key="packages-table")
    sel = event.selection.rows if event else []
    if sel:
        go(rows[sel[0]]["key"])
        st.rerun()
    st.download_button("⬇️ Packages (.csv)", rows_to_csv([{k: v for k, v in r.items() if k != "key"} for r in rows]),
                       "packages.csv", "text/csv")


def package_page(cat: Catalogue, key: str) -> None:
    from pdcat import curation
    from pdcat.libclass import docs_url, package_detail

    from .detail import about_form, notes_section
    d = package_detail(cat, key)
    st.title(f"📦 {d['package']}")
    docs = docs_url(d["package"])
    st.caption(" · ".join(b for b in ("third-party package", d["purpose"], f"imported by {len(d['files'])} file(s)",
                                      f"[{'documentation' if 'pypi.org' not in docs else 'on PyPI'}]({docs})") if b)
               + f"  \n`{key}`")
    about_form(cat, key, None, curation.item_info(cat, key))
    if d["versions"]:
        st.subheader("Declared versions")
        st.dataframe(pd.DataFrame(d["versions"]), hide_index=True, width="stretch")
    if d["names"]:
        st.subheader("What your code uses from it")
        st.write(", ".join(f"`{n}` ({c})" for n, c in d["names"][:60]))
    st.subheader("Imported by")
    for i, f in enumerate(d["files"]):
        c1, c2 = st.columns([3, 3])
        with c1:
            asset_button(f["key"], f"{f['project']} / {f['file']}", "script", f"pkguse-{i}")
        c2.caption(f["uses"])
    notes_section(cat, key, [])
