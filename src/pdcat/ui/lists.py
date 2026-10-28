"""Lists that point at gaps: data nothing creates, and more as the catalogue grows."""
from __future__ import annotations

import streamlit as st

from pdcat.links import unlinked_data
from pdcat.store import Catalogue

from .common import page_title, result_list


def unlinked_page(cat: Catalogue) -> None:
    page_title("Unlinked data")
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
    page_title("Recent changes")
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


def duplicates_page(cat: Catalogue) -> None:
    import pandas as pd

    from pdcat.duplicates import add_drive_alias, drive_aliases, report

    from .common import asset_button, size_label
    page_title("Duplicates")
    st.caption("The same file in more than one place, folders that are copies of each other, sources that overlap, "
               "and files named like copies. Mapped drives count as their network path, so S:\\ and "
               "\\\\server\\share are the same place.")
    r = report(cat)
    if not any(r.values()):
        st.success("No duplicates found.")
    if r["overlapping_sources"]:
        st.subheader("Sources that overlap")
        st.caption("The same files are catalogued twice. Remove one of the folders under Add › Sources › Data folders.")
        st.dataframe(pd.DataFrame([{"source": o["a"], "path": o["a_path"], "and": o["b"], "path ": o["b_path"],
                                    "how": o["how"]} for o in r["overlapping_sources"]]), hide_index=True,
                     width="stretch")
    if r["copied_folders"]:
        st.subheader("Folders that are copies")
        st.dataframe(pd.DataFrame([{"folder": c["a"], "and": c["b"], "how": c["how"], "same files": c["shared_files"],
                                    "files in first": c["a_files"], "files in second": c["b_files"]}
                                   for c in r["copied_folders"]]), hide_index=True, width="stretch")
    if r["same_file"]:
        st.subheader(f"Same file in two places ({len(r['same_file'])})")
        for i, g in enumerate(r["same_file"][:100]):
            with st.container(border=True):
                st.caption(f"{size_label(g['size'])} · identical contents")
                for j, f in enumerate(g["files"]):
                    asset_button(f["series"] or f["key"], f"{f['name']} · {f['path']}", "data_file", f"dup-{i}-{j}")
    if r["named_copies"]:
        st.subheader("Named like copies")
        for i, n in enumerate(r["named_copies"]):
            c1, c2 = st.columns(2)
            with c1:
                asset_button(n["copy_key"], n["copy"], "data_file", f"named-{i}")
            if n["original_key"]:
                with c2:
                    asset_button(n["original_key"], f"original: {n['original']}", "data_file", f"orig-{i}")
    st.subheader("Mapped drives")
    aliases = drive_aliases(cat)
    if aliases:
        st.caption(" · ".join(f"{d} = {u}" for d, u in aliases.items()))
    with st.form("drive-alias"):
        c1, c2 = st.columns([1, 3])
        drive = c1.text_input("Drive", placeholder="S:")
        unc = c2.text_input("Network path", placeholder=r"\\fileserver\shared")
        if st.form_submit_button("Add"):
            try:
                add_drive_alias(cat, drive, unc)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
