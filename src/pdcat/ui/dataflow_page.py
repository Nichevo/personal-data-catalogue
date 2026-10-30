"""The data flow page: an ETL-style diagram of where data comes from and the path it follows."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat.export import rows_to_csv
from pdcat.store import Catalogue

from .common import go

SUMMARY_NAMES = {"api": "API", "web_scraping": "scraped site", "web_file": "web download",
                 "database": "database connection", "cloud_storage": "cloud store", "sharepoint": "SharePoint site",
                 "sap": "SAP system", "table": "table", "file": "file"}


def data_flow_page(cat: Catalogue) -> None:
    st.title("Data flow")
    st.caption("Where your data comes from (APIs, scraped web sites, databases, files, cloud storage, SAP) and "
               "the path it follows through your scripts and reports to the tables and files they make.")
    sources = [r[0] for r in cat.conn.execute(
        "SELECT DISTINCT source FROM assets WHERE kind IN ('script', 'notebook', 'sql_script', 'report', 'workbook', "
        "'access_db') AND status = 'active' ORDER BY source")]
    if not sources:
        st.info("Nothing scanned yet. Add a Python project or a data folder and scan it.")
        return
    choice = st.selectbox("Start from", ["Everything", *sources], key="flow-start",
                          help="Starting from one project or folder, the diagram also follows its data upstream to "
                               "whatever writes it and downstream to whatever reads what it writes.")
    flow_block(cat, None if choice == "Everything" else choice, "flow")


def flow_block(cat: Catalogue, project: str | None, key: str, focus: str | None = None) -> None:
    """The diagram for everything, one project or folder, or (focus) one script or report, with its downloads
    and the table of outside sources."""
    from pdcat.codemap import to_dot, to_mermaid
    from pdcat.dataflow import flow_elements, flow_graph, source_rows, summary

    from .graphview import graph_view
    c1, c2 = st.columns([2, 1])
    level = "files" if focus else c1.radio(
        "Detail", ["files", "projects"], horizontal=True, key=f"{key}-level",
        format_func={"files": "Every script", "projects": "One box per project"}.get)
    view = c1.radio("Draw as", ["boxes", "subway"], horizontal=True, key=f"{key}-view",
                    format_func={"boxes": "Boxes and arrows", "subway": "Subway map"}.get,
                    help="The subway map draws each table, file or outside source as a coloured line running "
                         "down the page through the scripts and reports that use it.")
    show_old = c2.checkbox("Show old items", value=False, key=f"{key}-old",
                           help="Items marked old are hidden unless this is ticked.")
    g = flow_graph(cat, project, level=level, show_old=show_old, focus=focus)
    if not g.nodes:
        st.caption("This doesn't read or write any data the catalogue knows about." if focus
                   else "No data found flowing in or out yet.")
        return
    counts = summary(g)
    if counts:
        st.markdown("**Starts from** " + " · ".join(f"{n} {SUMMARY_NAMES.get(k, k)}{'' if n == 1 else 's'}" for k, n in
                                                    sorted(counts.items(), key=lambda kv: -kv[1])))
    name = (focus or "").rsplit("/", 1)[-1].rsplit(".", 1)[0] or project or "all"
    if view == "subway":
        subway_block(cat, g, key, name)
    else:
        graph_view(cat, flow_elements(g), key=f"{key}-{project or 'all'}-{level}", name=f"dataflow-{name}",
                   height=420 if focus else 620)
        st.caption("Data flows left to right. On the left are where it starts: 🌐 APIs, 🕸️ scraped web sites, "
                   "🛢️ database connections and tables, ⬇️ web downloads, ☁️ cloud storage, 📎 SharePoint, 🏭 SAP and "
                   "input files. Then the code and reports that use it (blue boxes, orange for reports), and the tables "
                   "and files they write. Dashed arrows read, red arrows write. Click a box to open it.")
    dot, mermaid = to_dot(g), to_mermaid(g)
    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇️ Mermaid (.mmd)", mermaid, f"dataflow-{name}.mmd", "text/plain", key=f"{key}-mmd",
                       help="Paste into GitHub, Azure DevOps, Confluence or mermaid.live")
    d2.download_button("⬇️ Graphviz (.dot)", dot, f"dataflow-{name}.dot", "text/plain", key=f"{key}-dot")
    with d3.popover("Copy Mermaid"):
        st.code(mermaid, language=None)

    rows = source_rows(cat, project, used_by=focus)
    st.subheader("Where data comes from")
    if not rows:
        st.caption("No APIs, web scraping, database connections, cloud storage or SAP connections found in the code "
                   "or reports yet. Tables and files your code reads are on the diagram.")
        return
    st.caption("Found in the code (requests, httpx, urllib, BeautifulSoup, Selenium, Scrapy, pyodbc, SQLAlchemy, "
               "pyrfc, boto3…) and in Power Query. Addresses leave out query strings, user names and passwords.")
    shown = [{k: v for k, v in r.items() if k not in ("key", "used_by_key")} for r in rows]
    picked = st.dataframe(pd.DataFrame(shown), hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row", key=f"{key}-sources")
    st.download_button("⬇️ Data sources (.csv)", rows_to_csv(shown), "data-sources.csv", "text/csv",
                       key=f"{key}-csv")
    sel = picked.selection.rows if picked else []
    if sel:
        r = rows[sel[0]]
        c1, c2 = st.columns(2)
        c1.button(f"Open {r['source']}", on_click=go, args=(r["key"],), key=f"{key}-open-src")
        c2.button(f"Open {r['used by']}", on_click=go, args=(r["used_by_key"],), key=f"{key}-open-user")


def subway_block(cat: Catalogue, g, key: str, name: str) -> None:
    """The flow as a subway map: plain SVG, with a download."""
    from pdcat.subway import subway_svg, table_rows

    from .graphview import show_svg
    svg = subway_svg(g, table_rows(cat))
    show_svg(svg, key=f"{key}-subway")
    st.caption("Each coloured line is one table (purple and blue), file (green) or outside source (orange). "
               "▶ is where it comes in or is first written, the stations are the scripts and reports that use "
               "it, top to bottom in run order, and ■ is where it ends. Dashed lines are data not in the "
               "catalogue yet. Click a name to open it.")
    st.download_button("⬇️ Subway map (.svg)", svg, f"subway-{name}.svg", "image/svg+xml", key=f"{key}-svg")
