"""The Power BI map page: every report, how much is in it, and the data behind it."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat.export import rows_to_csv
from pdcat.store import Catalogue

from .common import go


def powerbi_map_page(cat: Catalogue) -> None:
    from pdcat.codemap import to_dot, to_mermaid
    from pdcat.dataflow import flow_elements
    from pdcat.powerbimap import pick_reports, report_map, reports

    from .graphview import graph_view
    st.title("Power BI map")
    st.caption("Every Power BI report, sized by how much is in it (pages, visuals and measures), with the tables "
               "and files it loads, the scripts that make them, and where their data comes from.")
    c1, c2 = st.columns(2)
    workbooks = c1.checkbox("Include Excel workbooks", value=False, key="pbimap-wb")
    show_old = c2.checkbox("Show old items", value=False, key="pbimap-old",
                           help="Older versions of a report are always folded into the current one.")
    every = reports(cat, workbooks=workbooks, show_old=show_old)
    if not every:
        st.info("No Power BI reports catalogued yet. Add the folder that holds them under ➕ Add → Sources → "
                "Data folders and scan it.")
        return
    f1, f2, f3 = st.columns([3, 1.4, 1.2], vertical_alignment="bottom")
    text = f1.text_input("Only reports whose name or folder contains", key="pbimap-text",
                         placeholder="e.g. sales, or a folder name", help="Several words: each must match.")
    flagged_only = f2.checkbox("Only those that need a look", key="pbimap-flagged")
    limit = int(f3.number_input("Show at most", 1, 1000, min(25, max(1, len(every))), key="pbimap-limit",
                                help="The biggest reports first. Raise it to see everything."))
    rows = pick_reports(every, text=text, flagged_only=flagged_only, limit=limit)
    if not rows:
        st.info(f"None of the {len(every)} reports match. Clear the filter to see them all.")
        return
    if len(rows) < len(every):
        st.caption(f"Showing {len(rows)} of {len(every)} reports.")
    flagged = [r for r in rows if r["flag"]]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Reports", len(rows))
    m2.metric("Visuals", sum(r["visuals"] for r in rows))
    m3.metric("Measures", sum(r["measures"] for r in rows))
    m4.metric("Need a look", len(flagged), help="Reports that load nothing catalogued, have no sources found, "
                                                  "or couldn't be read.")

    g = report_map(cat, workbooks=workbooks, show_old=show_old, rows=rows)
    tag = f"{workbooks}-{show_old}-{limit}-{flagged_only}-{abs(hash(text))}"
    graph_view(cat, flow_elements(g), key=f"pbimap-{tag}", name="powerbi-map")
    st.caption("Reports are on the right, bigger text for bigger reports; a red dashed border means it needs a "
               "look. To their left are the tables and files they load, the scripts that write those, and the "
               "APIs, databases and files those scripts read. Click a box to open it.")
    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇️ Mermaid (.mmd)", to_mermaid(g), "powerbi-map.mmd", "text/plain", key="pbimap-mmd")
    d2.download_button("⬇️ Graphviz (.dot)", to_dot(g), "powerbi-map.dot", "text/plain", key="pbimap-dot")
    with d3.popover("Copy Mermaid"):
        st.code(to_mermaid(g), language=None)

    st.subheader("Reports")
    st.caption("Biggest first. \"Catalogued\" is how many of the tables and files a report loads are in the "
               "catalogue; \"outside sources\" are SQL Servers, web addresses and SharePoint sites in its queries.")
    shown = [{k: v for k, v in r.items() if k not in ("key", "kind", "size")} for r in rows]
    picked = st.dataframe(pd.DataFrame(shown), hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row", key="pbimap-table")
    st.download_button("⬇️ Reports (.csv)", rows_to_csv(shown), "powerbi-reports.csv", "text/csv", key="pbimap-csv")
    sel = picked.selection.rows if picked else []
    if sel:
        st.button(f"Open {rows[sel[0]]['name']}", on_click=go, args=(rows[sel[0]]["key"],), key="pbimap-open")
