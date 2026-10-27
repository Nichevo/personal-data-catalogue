"""Lists that point at gaps: data nothing creates, and more as the catalogue grows."""
from __future__ import annotations

import streamlit as st

from pdcat.links import unlinked_data
from pdcat.store import Catalogue

from .common import result_list


def unlinked_page(cat: Catalogue) -> None:
    st.title("Unlinked data")
    st.caption("Tables and data files that nothing in the catalogue creates or loads. The script may build the "
               "name in a way the scanner can't follow, or the data comes from outside your code. Open one and "
               "use \"Link a script\" to record who makes it; the link is kept through rescans.")
    rows = unlinked_data(cat)
    if not rows:
        st.success("Every table and file has something that creates it.")
        return
    result_list([{**r, "note_count": 0} for r in rows], "unlinked",
                extra=lambda r: f"read by {r['read_by']}" if r["read_by"] else "")


def changes_page(cat: Catalogue) -> None:
    import pandas as pd

    from pdcat.history import recent_changes
    st.title("Recent changes")
    st.caption("What changed between scans: new and dropped items, columns added, removed or retyped.")
    days = st.select_slider("Over the last", [7, 30, 90, 365], value=30, format_func=lambda d: f"{d} days")
    rows = recent_changes(cat, days)
    if not rows:
        st.info("No changes recorded. Changes are recorded from the second scan of a source onwards.")
        return
    df = pd.DataFrame([{"when": r["at"][:16].replace("T", " "), "item": r["name"] or r["asset_key"],
                        "change": r["change"], "detail": r["detail"], "key": r["asset_key"]} for r in rows])
    event = st.dataframe(df.drop(columns=["key"]), hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key="changes-table")
    sel = event.selection.rows if event else []
    if sel:
        from .common import go
        go(rows[sel[0]]["asset_key"])
        st.rerun()
