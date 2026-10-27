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
