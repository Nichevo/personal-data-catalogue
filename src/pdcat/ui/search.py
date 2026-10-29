"""The search page, and the plain lists (data issues, needs notes, recent notes, unmatched tables)."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from pdcat.export import rows_to_csv
from pdcat.store import Catalogue

from .common import (
    asset_button,
    badges,
    first_line,
    go,
    icon,
    keep,
    kind_label,
    note_meta,
    page_title,
    restore,
    result_list,
    size_label,
)

DETAILED = 15  # results shown with their matching columns; the rest stay one line each


def search_page(cat: Catalogue) -> None:
    from pdcat import searchview as SV
    st.title("Search")
    for k in ("search-text", "search-quick", "search-view", "search-group"):
        restore(k)
    text = st.text_input("Search", placeholder="table, column, script, note, rule or tag…", key="search-text",
                         label_visibility="collapsed")
    quick = st.radio("Show", ["Everything", *SV.QUICK_FILTERS], horizontal=True, key="search-quick",
                     label_visibility="collapsed")
    c1, c2, c3, c4 = st.columns([1.3, 1.6, 1.3, 1.3])
    view = c1.selectbox("View", ["Table", "Cards", "Columns only"], key="search-view",
                        help="Columns only: every matching column in every table and file, as one list.")
    group_by = c2.selectbox("Group by", SV.GROUP_BY, key="search-group", disabled=view != "Table")
    kinds = SV.QUICK_FILTERS.get(quick)
    kind_choices = [k for k in [*cat.kinds(), "knowledge", "question", "metric", "feature"]
                    if not kinds or k in kinds]
    restore("search-kind", ["Any kind", *kind_choices])
    kind = c3.selectbox("Kind", ["Any kind", *kind_choices], format_func=lambda k: k if k == "Any kind" else kind_label(k),
                        key="search-kind")
    sources = ["Any source", *cat.sources()]
    restore("search-source", sources)
    source = c4.selectbox("Source", sources, key="search-source")
    keep("search-text", "search-quick", "search-view", "search-group", "search-kind", "search-source")
    if view == "Columns only":
        columns_only(cat, text)
        return
    old = st.checkbox("Show older versions", key="search-old",
                      help="Older versions of a report, workbook or script are folded under the current one "
                           "(\"+2 older\"). Tick to list them too.")
    rows = [dict(r) for r in cat.search(text, kind=None if kind == "Any kind" else kind,
                                        source=None if source == "Any source" else source,
                                        kinds=kinds, limit=300, old_versions=old)]
    if not rows:
        st.info("No matches." if text else "Nothing here yet.")
        return
    related_knowledge(cat, text)
    metric_measure_box(cat, rows)
    if view == "Cards":
        for i, r in enumerate(rows):
            result_card(cat, r, text, i, detailed=bool(text) and i < DETAILED)
    else:
        results_table(cat, SV.rows_for(cat, rows), group_by)
    st.download_button(f"⬇️ Download these {len(rows)} results (CSV)",
                       rows_to_csv([{k: r.get(k) for k in ("key", "kind", "source", "name", "description",
                                                            "note_count")} for r in rows]),
                       "search-results.csv", "text/csv")


TABLE_COLUMNS = ["kind", "name", "where", "description", "matched on", "rows", "changed", "notes", "status",
                 "trust", "used by", "made by"]


def results_table(cat: Catalogue, rows: list[dict], group_by: str, key: str = "search-table") -> None:
    """Results as sortable tables, one per group. Picking a row opens it (a file family opens to its members)."""
    from pdcat import searchview as SV
    groups = SV.grouped(rows, group_by)
    st.caption(f"{len(rows)} result(s)" + (f" in {len(groups)} group(s)" if group_by != "Not at all" else "")
               + ". Click a column heading to sort, or a row to open it.")
    for gi, (head, items) in enumerate(groups):
        if head:
            st.markdown(f"**{head}** · {len(items)}")
        df = pd.DataFrame([{**{c: r.get(c) for c in TABLE_COLUMNS},
                            "kind": f"{icon(r['kind'])} {kind_label(r['kind']) if r['kind'] != 'group' else 'file family'}"}
                           for r in items])
        df = df[[c for c in TABLE_COLUMNS if c in ("kind", "name") or df[c].astype(str).str.strip().replace(
            {"0": "", "None": "", "nan": ""}).any()]]
        event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                             key=f"{key}-{gi}", column_config={
                                 "rows": st.column_config.NumberColumn(format="%d"),
                                 "description": st.column_config.TextColumn(width="large")})
        sel = event.selection.rows if event and hasattr(event, "selection") else []
        if sel:
            go(items[sel[0]]["key"])
            st.rerun()


def metric_measure_box(cat: Catalogue, rows: list[dict]) -> None:
    """A metric in the results shows the measures and SQL that calculate it; a measure shows its metric."""
    from pdcat import searchview as SV
    links = SV.metric_links(cat, rows[:50])
    if not links:
        return
    with st.container(border=True):
        for i, link in enumerate(links[:6]):
            st.markdown(f"**{link['from_name']}** {link['what']}:")
            cols = st.columns(min(4, len(link["to"])))
            for j, (key, name, kind) in enumerate(link["to"]):
                with cols[j % len(cols)]:
                    asset_button(key, name, kind, f"mm-{i}-{j}")


def result_card(cat: Catalogue, r: dict, text: str, i: int, *, detailed: bool) -> None:
    with st.container(border=detailed):
        left, right = st.columns([4, 2])
        with left:
            asset_button(r["key"], r["name"], r["kind"], f"search-{i}")
        measure = cat.get_asset(r["key"]) if r["kind"] == "measure" else None
        report = cat.get_asset(measure["parent"]) if measure and measure["parent"] else None
        where = f"{r['source']} › {report['name']}" if report else r.get("source")
        bits = [kind_label(r["kind"])] + ([where] if where else [])
        if r.get("note_count"):
            bits.append(f"📝 {r['note_count']}")
        if r.get("status") == "missing":
            bits.append("⚠️ missing")
        bits += badges(r)
        right.caption(" · ".join(bits))
        about = r.get("purpose") or first_line(r.get("description"))
        if not about and measure and measure["attrs"].get("expression"):
            from pdcat.textclean import as_text
            about = " ".join(as_text(measure["attrs"]["expression"]).split())[:200]
        if about:
            st.caption(about)
        if not detailed:
            return
        if r.get("snippet") and "**" in r["snippet"]:
            st.caption("Matched on: " + _clean_snippet(r["snippet"]))
        asset = cat.get_asset(r["key"]) if r["kind"] not in ("knowledge", "question", "metric", "feature",
                                                               "key", "bproject", "note") else None
        if asset:
            line = _facts_line(cat, asset)
            if line:
                st.caption(line)
            cols = cat.matching_columns(r["key"], text)
            for c in cols[:6]:
                column_line(c)
            if len(cols) > 6:
                st.caption(f"…and {len(cols) - 6} more matching columns")
            all_cols = cat.columns(r["key"])
            if all_cols and len(all_cols) > len(cols):
                with st.expander(f"All columns ({len(all_cols)})"):
                    st.dataframe(pd.DataFrame([{"column": c["name"], "type": c["data_type"],
                                                "description": c["description"] or ""} for c in all_cols]),
                                 hide_index=True, width="stretch")


def _clean_snippet(snippet: str) -> str:
    # The index stores names twice (as written and split into words); show it once.
    return " ".join(snippet.split())[:300]


def _facts_line(cat: Catalogue, asset: dict) -> str:
    attrs = asset["attrs"]
    bits = []
    if attrs.get("row_count") is not None:
        bits.append(f"{'' if attrs.get('row_count_exact', True) else '≈ '}{attrs['row_count']:,} rows")
    if attrs.get("size_bytes") is not None:
        bits.append(size_label(attrs["size_bytes"]))
    bits.append(f"last scanned {asset['last_seen'][:10]}")
    makers = [l for l in cat.links_to(asset["key"]) if l["relation"] == "writes"]
    if makers:
        names = ", ".join(dict.fromkeys(l["src_name"] or l["src_key"] for l in makers))
        bits.append(f"created or loaded by {names}")
    return " · ".join(bits)


def column_line(c: dict) -> None:
    """One matching column with its details: type, description, nulls, distinct, range and common values."""
    p = c.get("profile")
    bits = [f"`{c['name']}`", c.get("data_type") or ""]
    if c.get("sensitive"):
        bits.append("🔒 sensitive")
    if p:
        n = p["row_count"] or 0
        if n and p["null_count"] is not None:
            bits.append(f"{p['null_count'] / n:.0%} empty")
        if p["distinct_count"] is not None:
            bits.append(f"{p['distinct_count']:,} distinct")
        if p["min_value"] is not None:
            bits.append(f"{p['min_value']} → {p['max_value']}")
        top = json.loads(p["top_values"] or "[]")
        if top:
            bits.append("common: " + ", ".join(str(v) for v, _ in top[:4]))
    st.markdown("↳ " + " · ".join(b for b in bits if b) + (f"  \n&nbsp;&nbsp;&nbsp;{c['description']}"
                                                           if c.get("description") else ""))
    if p and p.get("histogram"):
        from .charts import range_chart
        range_chart(p, height=70)


def columns_only(cat: Catalogue, text: str) -> None:
    if not text.strip():
        st.caption("Type a column name or a word from its description.")
        return
    rows = cat.search_columns(text)
    if not rows:
        st.info("No matching columns.")
        return
    table = []
    for r in rows:
        n = r["row_count"] or 0
        hidden = bool(r["sensitive"])
        table.append({"column": r["column_name"], "in": r["item"], "kind": kind_label(r["kind"]),
                      "source": r["source"], "type": r["data_type"], "description": r["description"] or "",
                      "empty": f"{r['null_count'] / n:.0%}" if n and r["null_count"] is not None else "",
                      "distinct": r["distinct_count"],
                      "min": "🔒" if hidden else (r["min_value"] or ""), "max": "🔒" if hidden else (r["max_value"] or ""),
                      "common values": "🔒" if hidden else ", ".join(str(v) for v, _ in json.loads(r["top_values"] or "[]")[:4]),
                      "key": r["asset_key"]})
    st.caption(f"{len(table)} matching column(s). Pick a row to open its table or file.")
    df = pd.DataFrame(table)
    event = st.dataframe(df.drop(columns=["key"]), hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key="colsearch")
    sel = event.selection.rows if event and hasattr(event, "selection") else []
    if sel:
        go(table[sel[0]]["key"])
        st.rerun()
    st.download_button("⬇️ These columns (CSV)", rows_to_csv([{k: v for k, v in t.items()} for t in table]),
                       "columns.csv", "text/csv")


def related_knowledge(cat: Catalogue, text: str) -> None:
    """Knowledge notes whose trigger words appear in the search."""
    if not text.strip():
        return
    from pdcat.knowledge import triggered
    hits = triggered(cat, text)
    if hits:
        with st.container(border=True):
            st.markdown("**💡 Related knowledge**")
            for k in hits[:5]:
                st.button(f"💡 {k['title']}", key=f"kbhit-{k['uid']}", on_click=go, args=(f"kb://{k['uid']}",),
                          type="tertiary")
                st.caption(first_line(k["body"], 240))


# ---- plain lists ---------------------------------------------------------------

def data_issues_page(cat: Catalogue) -> None:
    page_title("Data issues")
    st.caption("Open data issues logged on tables, columns and files. Resolve them from the item's page.")
    issues = cat.open_issues()
    if not issues:
        st.success("No open data issues.")
    for i, n in enumerate(issues):
        _note_card(n, f"issue-{i}")


def needs_notes_page(cat: Catalogue) -> None:
    page_title("Needs notes")
    st.caption("Tables and views with no description and no notes, the ones your code uses most first.")
    result_list(cat.needs_documentation(), "todo",
                extra=lambda r: f"used by {r['used_by']} file(s)" if r["used_by"] else "")


def recent_notes_page(cat: Catalogue) -> None:
    page_title("Recent notes")
    notes = cat.recent_notes()
    if not notes:
        st.info("No notes yet. Open any table or script to add one.")
    for i, n in enumerate(notes):
        _note_card(n, f"recent-note-{i}")


def _note_card(n, key: str) -> None:
    base, _, column = n["asset_key"].partition("#")
    with st.container(border=True):
        label = (n["asset_name"] or base) + (f" › {column}" if column else "")
        asset_button(base, label, n["asset_kind"] or "note", key)
        st.markdown(n["body"])
        st.caption(note_meta(n))


def unmatched_page(cat: Catalogue) -> None:
    page_title("Unmatched tables")
    st.caption("Table names found in your code that don't match any scanned table. They may live on a server "
               "or in a database you haven't added, be built while the code runs, or be a false positive from the "
               "SQL detector. A rescan after an upgrade re-reads your SQL with the latest detector.")
    rows = cat.unmatched_reasons()
    if rows:
        why = pd.Series([_why_group(r["why"]) for r in rows]).value_counts()
        st.markdown(" · ".join(f"**{n}** {label}" for label, n in why.items()))
        st.dataframe(pd.DataFrame([{"table": r["table"], "likely why": r["why"], "used by files": r["used_by"],
                                    "example": r["example"]} for r in rows]),
                     hide_index=True, width="stretch")
    else:
        st.success("Every table name in your code matched a scanned table.")


def _why_group(why: str) -> str:
    """'in database SalesDB, not scanned' -> 'in databases not scanned', for the summary line."""
    if why.startswith("in database"):
        return "in databases not scanned"
    if why.startswith("on server"):
        return "on servers not scanned"
    return why

