"""Review › Slow SQL (version 2.10): every query with a slow pattern, by check and worst first.

The checks never run the SQL or connect to a server; "worst first" uses the row counts your SQL Server scans
already saved, so a SELECT * on a 50-million-row table comes before one on a small lookup table.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from pdcat import sqlchecks
from pdcat.store import Catalogue

from .common import asset_button, cached, go, page_title, selected_rows


@cached
def _slow(cat: Catalogue) -> dict[str, Any]:
    return sqlchecks.slow_sql(cat)


def _rows_text(n: int) -> str:
    if not n:
        return ""
    for size, word in ((1_000_000_000, "bn"), (1_000_000, "m"), (1_000, "k")):
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + word
    return str(n)


def slow_sql_page(cat: Catalogue) -> None:
    import pandas as pd
    page_title("Slow SQL")
    st.caption("SQL that may run slowly, from every query, view and procedure the catalogue found (scripts, .sql "
               "files, Excel, Power BI, Access and SQL you pasted). Nothing is run: these are patterns in the text, "
               "checked against your scanned tables. Worst first means the biggest table read (row counts from "
               "your scans), then the most checks tripped. Mark one fine and it isn't raised again.")
    with st.expander("Settings"):
        big = st.number_input("A table counts as big from this many rows", min_value=1_000, step=100_000,
                              value=int(cat.setting("slow_sql_big_rows", sqlchecks.BIG_TABLE_ROWS)),
                              help="Used by “Reads a big table with no filter”.", key="slow-big")
        if big != int(cat.setting("slow_sql_big_rows", sqlchecks.BIG_TABLE_ROWS)):
            cat.set_setting("slow_sql_big_rows", int(big))
            st.rerun()
    result = _slow(cat)
    queries, by_rule = result["queries"], result["by_rule"]
    if not queries:
        st.success("No slow patterns found in the SQL catalogued so far (or every one is marked fine). If you "
                   "haven't scanned yet, add sources under ➕ Add.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Queries", len(queries))
    c2.metric("Findings", sum(len(q["slow"]) for q in queries))
    c3.metric("On tables of 1m+ rows", sum(1 for q in queries if q["rows"] >= 1_000_000))
    view = st.radio("Show", ["Worst first", "By check"], horizontal=True, key="slow-view",
                    label_visibility="collapsed")
    if view == "Worst first":
        _worst_first(cat, queries, pd)
    else:
        _by_check(cat, by_rule)
    st.download_button("⬇️ Download every finding (CSV)", pd.DataFrame(sqlchecks.slow_csv_rows(result)).to_csv(
        index=False), "slow-sql.csv", "text/csv", key="slow-csv")


def _worst_first(cat: Catalogue, queries: list[dict[str, Any]], pd) -> None:
    rules = sorted({f["rule"] for q in queries for f in q["slow"]}, key=lambda r: sqlchecks.RULES[r][1])
    pick = st.selectbox("Check", ["(any)", *rules], key="slow-rule",
                        format_func=lambda r: r if r == "(any)" else sqlchecks.RULES[r][1])
    shown = [q for q in queries if pick == "(any)" or any(f["rule"] == pick for f in q["slow"])]
    df = pd.DataFrame([{"query": q["name"], "in": q["where"], "biggest table": q["biggest"],
                        "rows": _rows_text(q["rows"]), "checks": len(q["slow"]),
                        "what": "; ".join(f["title"] for f in q["slow"]), "places": len(q["places"])}
                       for q in shown])
    st.caption(f"{len(shown)} quer{'y' if len(shown) == 1 else 'ies'} · pick a row to open it and see each check "
               "with a reason and a fix")
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key="slow-list")
    sel = selected_rows(event, len(shown))
    if sel:
        go(f"query://{shown[sel[0]]['uid']}")
        st.rerun()


def _by_check(cat: Catalogue, by_rule: dict[str, dict[str, Any]]) -> None:
    for rule, g in by_rule.items():
        _, title, why, article = sqlchecks.RULES[rule]
        qs = g["queries"]
        with st.expander(f"🐢 {title} · {len(qs)} quer{'y' if len(qs) == 1 else 'ies'}"):
            st.caption(why + f" [More]({article})")
            for i, q in enumerate(qs[:40]):
                c1, c2, c3 = st.columns([3, 3, 1])
                with c1:
                    asset_button(f"query://{q['uid']}", q["name"], "sql_query", f"slow-{rule}-{i}")
                bits = [q["where"]] + ([f"{q['biggest']} ({_rows_text(q['rows'])} rows)"] if q["rows"] else []) \
                    + [a for a in q["abouts"] if a]
                c2.caption(" · ".join(b for b in bits if b))
                if c3.button("Fine here", key=f"slow-ok-{rule}-{i}", help="Don't flag this for this query again"):
                    for about in q["abouts"]:
                        sqlchecks.mark_ok(cat, q["uid"], rule, about)
                    st.rerun()
            if len(qs) > 40:
                st.caption(f"…and {len(qs) - 40} more. Use Worst first with this check picked, or the CSV.")
            if st.button(f"Accept “{title}” everywhere: it's fine in our SQL", key=f"slow-all-{rule}"):
                n = sqlchecks.accept_rule_everywhere(cat, rule)
                st.toast(f"Marked fine in {n} place(s)")
                st.rerun()
