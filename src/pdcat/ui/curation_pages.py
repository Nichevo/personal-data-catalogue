"""Keeping the catalogue well documented: describe columns once, edit many items at a time,
and see how complete it is with what to document next."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import coverage, curation
from pdcat.store import Catalogue

from .common import asset_button, go, kind_label, page_title

EDIT_KINDS = ("table", "view", "data_file", "file_series", "script", "notebook", "sql_script", "procedure", "report",
              "workbook", "model")


def describe_columns_page(cat: Catalogue) -> None:
    page_title("Describe columns")
    st.caption("Column names that appear in several places. Describe one once and it's applied everywhere it has "
               "no description yet. Descriptions you've already written are kept unless you tick the box.")
    names = curation.undescribed_column_names(cat)
    if not names:
        st.success("Every column has a description.")
        return
    labels = {n["column_name"]: f"{n['example']} ({n['undescribed']} of {n['places']} without a description)"
              for n in names}
    pick = st.selectbox("Column", list(labels), format_func=labels.get)
    places = curation.same_named_columns(cat, pick)
    existing = sorted({p["description"] for p in places if p["description"]})
    if existing:
        st.caption("Already described as: " + " · ".join(f"“{d}”" for d in existing[:5]))
    with st.form(f"describe-{pick}"):
        text = st.text_area("Description", value=existing[0] if len(existing) == 1 else "", height=80)
        df = pd.DataFrame([{"apply": not p["description"], "item": p["item"], "kind": kind_label(p["kind"]),
                            "type": p["data_type"] or "", "current description": p["description"]} for p in places])
        edited = st.data_editor(df, hide_index=True, width="stretch", disabled=["item", "kind", "type",
                                                                                "current description"],
                                key=f"describe-grid-{pick}")
        overwrite = st.checkbox("Also replace descriptions already written for the ticked rows")
        if st.form_submit_button("Apply", type="primary") and text.strip():
            keys = [places[i]["asset_key"] for i, row in edited.iterrows() if row["apply"]]
            n = curation.describe_everywhere(cat, pick, text.strip(), only_empty=not overwrite, keys=keys)
            st.toast(f"Described {n} column(s)")
            st.rerun()


def bulk_edit_page(cat: Catalogue) -> None:
    page_title("Edit many items")
    st.caption("Fill in purpose, grain, refresh, status, trust and owner for many items at once, like a spreadsheet. "
               "Only the rows you change are saved.")
    sources = [r[0] for r in cat.conn.execute("SELECT DISTINCT source FROM assets WHERE status = 'active' "
                                              "ORDER BY 1")]
    c1, c2, c3 = st.columns(3)
    source = c1.selectbox("Source", ["All", *sources])
    kinds = c2.multiselect("Kinds", EDIT_KINDS, default=["table", "view", "data_file", "file_series"],
                           format_func=kind_label)
    only_empty = c3.checkbox("Only items with no purpose yet", value=False)
    marks = ",".join("?" * len(kinds)) or "''"
    sql = f"SELECT key, name, kind, source, description FROM assets WHERE status = 'active' AND kind IN ({marks})"
    params: list = list(kinds)
    if source != "All":
        sql += " AND source = ?"
        params.append(source)
    rows = cat.conn.execute(sql + " ORDER BY source, name LIMIT 2000", params).fetchall()
    info = curation.all_item_info(cat)
    if only_empty:
        rows = [r for r in rows if not (info.get(r["key"], {}).get("purpose") or r["description"])]
    if not rows:
        st.info("Nothing matches.")
        return
    fields = ("purpose", "grain", "refresh", "lifecycle", "trust", "owner")
    df = pd.DataFrame([{"key": r["key"], "item": r["name"], "kind": kind_label(r["kind"]),
                        **{f: info.get(r["key"], {}).get(f) or "" for f in fields}} for r in rows])
    edited = st.data_editor(
        df, hide_index=True, width="stretch", disabled=["key", "item", "kind"], key=f"bulk-{source}-{kinds}",
        column_config={"key": None,
                       "refresh": st.column_config.SelectboxColumn(options=curation.REFRESH),
                       "lifecycle": st.column_config.SelectboxColumn("status", options=curation.LIFECYCLE),
                       "trust": st.column_config.SelectboxColumn(options=curation.TRUST),
                       "purpose": st.column_config.TextColumn(width="large")})
    if st.button("Save changes", type="primary"):
        n = save_bulk(cat, df, edited, fields)
        st.toast(f"Saved {n} item(s)")
        st.rerun()


def save_bulk(cat: Catalogue, before: pd.DataFrame, after: pd.DataFrame, fields) -> int:
    n = 0
    for (_, old), (_, new) in zip(before.iterrows(), after.iterrows()):
        changed = {f: (new[f] or "") for f in fields if (new[f] or "") != (old[f] or "")}
        if changed:
            curation.set_item_info(cat, old["key"], **changed)
            n += 1
    return n


def coverage_page(cat: Catalogue) -> None:
    page_title("Coverage")
    st.caption("How well documented the catalogue is. Each item scores out of 100 for its description, grain, owner, "
               "refresh, column descriptions, notes and status. Weighted counts the items your code and reports use "
               "and the big ones for more.")
    s = coverage.summary(cat)
    if not s["items"]:
        st.info("Nothing catalogued yet.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Overall", f"{s['overall']}%")
    c2.metric("Weighted by use", f"{s['weighted']}%")
    c3.metric("Items", s["items"])
    st.subheader("Document these next")
    from pdcat.drafts import draft_info
    for i, r in enumerate(coverage.next_to_document(cat)):
        with st.container(border=True):
            a, b = st.columns([4, 1])
            with a:
                asset_button(r["key"], r["name"], r["kind"], f"cov-{i}", help=r["source"])
                why = [f"used by {r['used_by']}" if r["used_by"] else "", f"{r['rows']:,} rows" if r["rows"] else ""]
                st.caption(" · ".join(x for x in (f"{r['score']}%", *why, "missing " + ", ".join(r["missing"]))
                                      if x))
            b.button("Open", key=f"cov-open-{i}", on_click=go, args=(r["key"],))
            draft = draft_info(cat, r["key"]) if "description" in r["missing"] else {}
            if draft.get("purpose"):
                st.caption(f"Draft: {draft['purpose']}")
                if st.button("Use this draft", key=f"cov-draft-{i}"):
                    curation.set_item_info(cat, r["key"], **{k: v for k, v in draft.items()
                                                            if k in curation.INFO_FIELDS and v})
                    st.rerun()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("By source")
        st.dataframe(pd.DataFrame(s["by_source"]), hide_index=True, width="stretch",
                     column_config={"score": st.column_config.ProgressColumn(format="%d%%", min_value=0,
                                                                             max_value=100)})
    with c2:
        st.subheader("By kind")
        st.dataframe(pd.DataFrame([{**k, "kind": kind_label(k["kind"])} for k in s["by_kind"]]), hide_index=True,
                     width="stretch", column_config={"score": st.column_config.ProgressColumn(
                         format="%d%%", min_value=0, max_value=100)})
