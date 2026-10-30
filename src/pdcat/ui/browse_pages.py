"""One-click lists under Find: Python code, Power BI, and tables and datasets. Each is the search table with
the kinds already picked, and a filter box."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat.store import Catalogue

from .common import go, keep, page_title, restore

LIMIT = 2000


def _browse(cat: Catalogue, name: str, kinds: tuple[str, ...], placeholder: str, group_by: str,
            old: bool = False) -> None:
    from pdcat import searchview as SV

    from .search import results_table
    tag = name.lower().replace(" ", "-")
    c1, c2 = st.columns([3, 1.3])
    restore(f"browse-text-{tag}")
    text = c1.text_input("Filter", placeholder=placeholder, key=f"browse-text-{tag}", label_visibility="collapsed")
    keep(f"browse-text-{tag}")
    group = c2.selectbox("Group by", SV.GROUP_BY, index=SV.GROUP_BY.index(group_by), key=f"browse-group-{tag}")
    rows = [dict(r) for r in cat.search(text, kinds=kinds, limit=LIMIT, old_versions=old)]
    if not rows:
        st.info("No matches." if text else f"No {name.lower()} catalogued yet. Add them under ➕ Add → Sources.")
        return
    if len(rows) == LIMIT:
        st.caption(f"Showing the first {LIMIT:,}. Type in the filter to narrow it down.")
    results_table(cat, SV.rows_for(cat, rows), group, key=f"browse-{tag}")


def filter_rows(cat: Catalogue, rows: list[dict], text: str) -> list[dict]:
    """Rows with every typed word (or what it stands for) anywhere in them, separators ignored, so 'stock' finds
    'OfficeStockonHand' and 'SOH' finds 'Stock on hand'. Names that start with the words come first."""
    from pdcat.searchwords import alternatives, squash, words
    groups = [alternatives(w, cat.abbreviations()) for w in words(text)]
    hits = [r for r in rows
            if all(any(squash(a) in squash(" ".join(str(v) for k, v in r.items() if k != "key")) for a in g)
                   for g in groups)]
    q = squash(text)
    return sorted(hits, key=lambda r: not squash(str(r.get("name", ""))).startswith(q))


def python_page(cat: Catalogue) -> None:
    page_title("Python")
    st.caption("Your Python projects, scripts and notebooks. Pick a row to open it.")
    old = _old_box("browse-old-py", "scripts")
    _browse(cat, "Python code", ("project", "script", "notebook"), "project, script or notebook name, or any word",
            "Folder or database", old)


def _old_box(key: str, what: str) -> bool:
    return st.checkbox("Show older versions", key=key,
                       help=f"Older versions of {what} are folded under the current one (\"+2 older\"). Tick to "
                            "list them too. A group's page lets you pick the current one or break the group up.")


def tables_page(cat: Catalogue) -> None:
    page_title("Tables and datasets")
    st.caption("Database tables and views, and the data files and file series in your folders. Pick a row to open it.")
    _browse(cat, "Tables and datasets", ("table", "view", "data_file", "file_series"), "table, file or column word",
            "Folder or database")


def powerbi_page(cat: Catalogue) -> None:
    from pdcat.pbiobjects import all_objects
    page_title("Power BI")
    c1, c2 = st.columns([3, 1.3], vertical_alignment="center")
    view = c1.radio("Show", ["Reports", "Model tables", "Queries", "Measures", "Register"], horizontal=True,
                    key="browse-pbi-view", label_visibility="collapsed")
    with c2:
        old = _old_box("browse-old-pbi", "a report (same folder, names that start the same)")
    if view == "Reports":
        st.caption("Every Power BI report, the current version of each. Pick a row to open it.")
        _browse(cat, "Power BI reports", ("report",), "report name or any word", "Folder or database", old)
        return
    if view == "Register":
        st.caption("The Power BI report register: published reports with their owners, audience, app and status, "
                   "each matched to the scanned .pbix of the same name.")
        from .register_pages import register_view
        register_view(cat)
        return
    if view == "Measures":
        st.caption("Every measure in every report. Pick a row to open it.")
        _browse(cat, "Measures", ("measure",), "measure name or DAX word", "Not at all", old)
        return
    what = "tables" if view == "Model tables" else "queries"
    st.caption(f"Every {'model table' if what == 'tables' else 'Power Query query'} in your "
               f"{'reports' if what == 'tables' else 'reports and Excel workbooks'}. Pick a row to open it.")
    restore(f"browse-pbi-{what}")
    text = st.text_input("Filter", placeholder="name, report or source", key=f"browse-pbi-{what}",
                         label_visibility="collapsed")
    keep(f"browse-pbi-{what}")
    rows = all_objects(cat, what, old_versions=old)
    if text:
        rows = filter_rows(cat, rows, text)
    if not rows:
        st.info("No matches." if text else "Nothing catalogued yet. Scan a folder with Power BI reports in it.")
        return
    st.caption(f"{len(rows)} {view.lower()}")
    df = pd.DataFrame([{k: v for k, v in r.items() if k != "key"} for r in rows])
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=f"browse-pbi-table-{what}")
    sel = event.selection.rows if event else []
    if sel:
        go(rows[sel[0]]["key"])
        st.rerun()
