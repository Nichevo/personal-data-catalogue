"""Pages for the model tables and Power Query queries inside a Power BI report or Excel workbook."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import pbiobjects
from pdcat.store import Catalogue

from .common import asset_button, go, icon


def _parent_line(parent: dict, what: str) -> None:
    c1, c2 = st.columns([1, 3])
    c1.caption(what)
    with c2:
        asset_button(parent["key"], parent["name"], parent["kind"], f"pbi-parent-{parent['key']}")


def _missing(view: dict, key: str, what: str) -> None:
    st.title(f"{icon('note')} {view['name']}")
    if view.get("report") or view.get("parent"):
        parent = view.get("report") or view.get("parent")
        st.warning(f"The latest scan of **{parent['name']}** has no {what} called **{view['name']}**. It may have "
                   "been renamed or removed.")
        asset_button(parent["key"], parent["name"], parent["kind"], f"pbi-gone-{key}")
    else:
        st.warning(f"The file this {what} was in isn't in the catalogue any more.")


def _sources(rows: list[dict], widget: str) -> None:
    for i, s in enumerate(rows):
        if s["key"]:
            label = s["label"] + ("" if s["catalogued"] else " (not catalogued yet)")
            asset_button(s["key"], label, s["kind"], f"{widget}-{i}")
        else:
            st.markdown(f"{icon(s['kind'])} {s['label']}")


def _lineage(lineage: list[dict], widget: str) -> None:
    """Each query in the chain, with what it reads, back to the data."""
    st.subheader("Lineage")
    if not lineage:
        st.caption("Nothing found that this reads from.")
        return
    st.caption("Where the data comes from: this query and every query it builds on, each with what it reads.")
    for i, step in enumerate(lineage):
        with st.container(border=True):
            asset_button(step["key"], step["query"], "pq_query", f"{widget}-q-{i}")
            if step["uses"]:
                st.caption("Builds on: " + ", ".join(step["uses"]))
            if step["sources"]:
                _sources(step["sources"], f"{widget}-s-{i}")
            elif not step["uses"]:
                st.caption("Reads nothing the scan could see (typed-in data or a calculation).")


def _notes(cat: Catalogue, key: str, columns: list[str]) -> None:
    from .detail import notes_section
    notes_section(cat, key, columns)


def table_page(cat: Catalogue, key: str) -> None:
    view = pbiobjects.table_view(cat, key)
    if view is None or view["missing"]:
        _missing(view or {"name": key}, key, "table")
        _notes(cat, key, [])
        return
    st.title(f"{icon('pbi_table')} {view['name']}")
    kind = "Calculated table (DAX)" if view["calculated"] else "Model table"
    st.caption(f"{kind} · {len(view['columns'])} columns · {len(view['measures'])} measures  \n`{key}`")
    from .item_boxes import item_actions
    item_actions(cat, key)
    _parent_line(view["report"], "In report")

    st.subheader(f"Columns ({len(view['columns'])})")
    if view["columns"]:
        from .impact_pages import column_usage_frame
        df = column_usage_frame(cat, view["report"]["key"], view["name"], view["columns"])
        unused = int((df["used by"] == "not used in scanned files").sum()) if "used by" in df else 0
        if unused:
            st.caption(f"{unused} of {len(df)} columns are not used in scanned files (no visual, measure, calculated "
                       "column or relationship in this model or its connected reports). They are kept: a report "
                       "that wasn't scanned may use them.")
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.caption("No columns read. A .pbix needs pbixray to read its model.")

    if view["measures"]:
        st.subheader(f"Measures ({len(view['measures'])})")
        st.caption("Measures whose home table is this one. Pick a row to open it.")
        event = st.dataframe(pd.DataFrame([{"measure": m["name"], "DAX": m["expression"], "format": m["format"],
                                            "folder": m["folder"]} for m in view["measures"]]),
                             hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                             key=f"pbit-m-{key}")
        sel = event.selection.rows if event else []
        if sel:
            go(view["measures"][sel[0]]["key"])
            st.rerun()

    st.subheader(f"Relationships ({len(view['relationships'])})")
    if not view["relationships"]:
        st.caption("Not related to any other table in the model.")
    for i, r in enumerate(view["relationships"]):
        c1, c2 = st.columns([2, 3])
        side = "many side" if r["side"] == "many" else "one side"
        c1.markdown(f"`{r['column']}` → `{r['other']}.{r['other_column']}`")
        with c2:
            label = f"{r['other']} ({side}" + ("" if r["active"] else ", inactive") + ")"
            if r["other_key"]:
                asset_button(r["other_key"], label, "pbi_table", f"pbit-r-{key}-{i}")
            else:
                st.caption(label)

    st.subheader("Source")
    if view["calculated"]:
        st.caption("Worked out with DAX inside the model, from the other tables.")
    elif view["query"]:
        c1, c2 = st.columns([1, 3])
        c1.caption("Loaded by query")
        with c2:
            asset_button(view["query"]["key"], view["query"]["name"], "pq_query", f"pbit-q-{key}")
        lineage = next((s for s in view["lineage"] if s["query"] == view["query"]["name"]), None)
        if lineage and lineage["sources"]:
            _sources(lineage["sources"], f"pbit-src-{key}")
    else:
        st.caption("No Power Query found for this table. A .pbix needs pbixray to read its queries, or the table "
                   "may be typed in.")
    if view["query"]:
        _lineage(view["lineage"], f"pbit-lin-{key}")

    if view["used_on"]:
        st.subheader(f"Shown on pages ({len({u['page'] for u in view['used_on']})})")
        st.dataframe(pd.DataFrame([{"page": u["page"], "visual": u["visual"], "fields": ", ".join(u["fields"])}
                                   for u in view["used_on"]]), hide_index=True, width="stretch")
    _notes(cat, key, [c["name"] for c in view["columns"]])


def query_page(cat: Catalogue, key: str) -> None:
    view = pbiobjects.query_view(cat, key)
    if view is None or view["missing"]:
        _missing(view or {"name": key}, key, "query")
        _notes(cat, key, [])
        return
    parent = view["parent"]
    st.title(f"{icon('pq_query')} {view['name']}")
    what = "Power Query parameter" if view["parameter"] is not None else "Power Query"
    st.caption(f"{what} · in {'report' if parent['kind'] == 'report' else 'workbook'} {parent['name']}  \n`{key}`")
    from .item_boxes import item_actions
    item_actions(cat, key)
    _parent_line(parent, "In report" if parent["kind"] == "report" else "In workbook")

    if view["parameter"] is not None:
        st.markdown(f"**Value:** `{view['parameter']}`")
    bits = [f"server {', '.join(view['servers'])}" if view["servers"] else "",
            f"database {', '.join(view['databases'])}" if view["databases"] else ""]
    if any(bits):
        st.caption(" · ".join(b for b in bits if b))

    st.subheader("Code (M)")
    st.code(view["expression"] or "(empty)", language="fsharp")
    for i, sql in enumerate(view["sql"]):
        st.caption("SQL sent to the database" + (f" ({i + 1})" if len(view["sql"]) > 1 else ""))
        st.code(sql, language="sql")

    if view["parameter"] is None:
        st.subheader("Reads")
        if view["sources"]:
            _sources(view["sources"], f"pq-src-{key}")
        else:
            st.caption("Reads no table or file directly." + (" See the queries it builds on below."
                                                              if view["uses"] else ""))
    for title, rows, widget in (("Builds on", view["uses"], "pq-use"), ("Used by", view["used_by"], "pq-by")):
        if rows:
            st.subheader(f"{title} ({len(rows)})")
            for i, r in enumerate(rows):
                asset_button(r["key"], r["name"], "pq_query", f"{widget}-{key}-{i}")

    st.subheader("Loads")
    if view["loads"]:
        for i, r in enumerate(view["loads"]):
            if r["key"]:
                asset_button(r["key"], r["label"], "pbi_table", f"pq-load-{key}-{i}")
            else:
                st.markdown(f"{icon(r['kind'])} {r['label']}")
    else:
        st.caption("Not loaded as a table of its own: other queries build on it, or it is connection only.")
    if view["uses"]:
        _lineage(view["lineage"], f"pq-lin-{key}")
    _notes(cat, key, [])
