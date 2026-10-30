"""Summaries under Find: one table each for Power BI files, database tables and Python projects, with counts,
a group-by, totals per group and overall, and a CSV download. Picking a row opens the item."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import summaries as S
from pdcat.export import rows_to_csv
from pdcat.store import Catalogue

from .common import cached, go, keep, restore


@cached
def _pbi(cat: Catalogue, old: bool):
    return S.powerbi_rows(cat, old_versions=old)


@cached
def _tables(cat: Catalogue):
    return S.table_rows(cat)


@cached
def _python(cat: Catalogue):
    return S.python_rows(cat)


def _show(cat: Catalogue, rows: list[dict], group_by: str, count_keys: tuple[str, ...], label: str, widget: str,
          file_name: str) -> None:
    if not rows:
        st.info("Nothing to sum up yet.")
        return
    restore(f"{widget}-filter")
    text = st.text_input("Filter", key=f"{widget}-filter", placeholder="any word in the row",
                         label_visibility="collapsed")
    keep(f"{widget}-filter")
    if text:
        from .browse_pages import filter_rows
        rows = filter_rows(cat, [{k: v for k, v in r.items() if k != "pages_detail"} for r in rows], text)
    table = S.with_totals(rows, group_by, count_keys, label)
    st.caption(f"{len(rows)} row(s). Totals follow each group and the whole table. Pick a row to open it.")
    df = pd.DataFrame(table)
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=f"{widget}-table")
    sel = event.selection.rows if event else []
    if sel:
        picked = table[sel[0]]
        name = picked.get(label)
        hit = next((r for r in rows if r.get(label) == name and not str(name).startswith("Total")), None)
        if hit and hit.get("key"):
            go(hit["key"])
            st.rerun()
    st.download_button("⬇️ Download (CSV)", rows_to_csv(table), file_name, "text/csv", key=f"{widget}-csv")


def powerbi_summary(cat: Catalogue) -> None:
    st.caption("One row per Power BI file: connected reports (thin reports matched to its model), pages, visuals, "
               "tables, columns, measures, relationships, queries and data sources. Group by folder, report group, "
               "business project, or by app, owner and workspace from the report register.")
    c1, c2, c3 = st.columns([1.5, 1, 1], vertical_alignment="center")
    group = c1.selectbox("Group by", S.PBI_GROUPS, key="sum-pbi-group")
    pages = c2.toggle("Split into pages", key="sum-pbi-pages", help="One row per report page with its visuals")
    old = c3.checkbox("Show older versions", key="sum-pbi-old")
    rows = _pbi(cat, old)
    if pages:
        _show(cat, S.powerbi_page_rows(rows), group, ("visuals", "measures", "columns"), "page", "sum-pbi-pages",
              "powerbi-pages-summary.csv")
    else:
        _show(cat, rows, group, S.COUNT_KEYS_PBI, "report", "sum-pbi", "powerbi-summary.csv")


def tables_summary(cat: Catalogue) -> None:
    st.caption("One row per table, view, data file or file series: columns, rows, company-wide keys, relationships, "
               "how many scripts and reports use it, described columns, notes and the last change seen.")
    group = st.selectbox("Group by", S.TABLE_GROUPS, key="sum-tab-group")
    _show(cat, _tables(cat), group, S.COUNT_KEYS_TABLE, "table", "sum-tab", "tables-summary.csv")


def python_summary(cat: Catalogue) -> None:
    st.caption("One row per Python project: scripts, notebooks, functions, lines, tables and files read and written, "
               "and imports split into your own modules, home-built libraries and third-party packages.")
    group = st.selectbox("Group by", S.PYTHON_GROUPS, key="sum-py-group")
    _show(cat, _python(cat), group, S.COUNT_KEYS_PYTHON, "project", "sum-py", "python-summary.csv")
