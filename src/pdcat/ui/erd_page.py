"""Entity relationship diagrams, one database at a time, split into subject areas."""
from __future__ import annotations

import streamlit as st

from pdcat import erd
from pdcat.store import Catalogue

from .graphview import graph_view


def erd_page(cat: Catalogue) -> None:
    st.title("Database diagrams")
    st.caption("Tables and how they join (foreign keys, confirmed joins, and suggested ones dashed), grouped into "
               "subject areas worked out from the joins. Tables joined to almost everything are marked shared. "
               "Rename areas and move tables below; your changes are kept.")
    dbs = [d for d in erd.databases(cat) if d["tables"]]
    if not dbs:
        st.info("No SQL Server databases scanned yet.")
        return
    labels = {d["key"]: f"{d['name']} ({d['tables']} tables)" for d in dbs}
    db = st.selectbox("Database", list(labels), format_func=labels.get)
    areas = erd.ensure_areas(cat, db)
    names = {a["uid"]: f"{a['name']} ({len(a['members'])})" for a in areas}
    pick = st.selectbox("Subject area", ["all", *names], format_func=lambda u: "All areas" if u == "all" else names[u])
    area = None if pick == "all" else pick
    elements = erd.erd_elements(cat, db, area)
    graph_view(cat, elements, key=f"erd-{db}-{pick}", direction="DOWN", name=f"erd-{db.rsplit('/', 1)[-1]}")
    c1, c2 = st.columns(2)
    stem = db.rsplit("/", 1)[-1] + ("" if area is None else "-" + names[area].split(" (")[0])
    c1.download_button("⬇️ Mermaid ERD (.mmd)", erd.to_mermaid(cat, db, area), f"erd-{stem}.mmd", "text/plain",
                       help="Paste into GitHub, Azure DevOps, Confluence or mermaid.live")
    with c2.popover("Copy Mermaid"):
        st.code(erd.to_mermaid(cat, db, area, columns=False), language=None)

    with st.expander("✏️ Edit subject areas"):
        tables = {t["key"]: t["name"] for t in erd.tables(cat, db).values()}
        with st.form(f"erd-move-{db}"):
            moving = st.multiselect("Move tables", list(tables), format_func=tables.get)
            target = st.selectbox("to area", list(names), format_func=names.get)
            if st.form_submit_button("Move") and moving:
                erd.move_tables(cat, moving, target)
                st.rerun()
        with st.form(f"erd-rename-{db}"):
            c1, c2 = st.columns(2)
            which = c1.selectbox("Rename area", list(names), format_func=names.get)
            new = c2.text_input("New name")
            if st.form_submit_button("Rename") and new.strip():
                erd.rename_area(cat, which, new)
                st.rerun()
        with st.form(f"erd-new-{db}", clear_on_submit=True):
            name = st.text_input("New area")
            if st.form_submit_button("Add area") and name.strip():
                erd.new_area(cat, db, name)
                st.rerun()
        if st.button("Work out the areas again", help="Areas and moves you made by hand are kept"):
            erd.suggest_areas(cat, db)
            st.rerun()
