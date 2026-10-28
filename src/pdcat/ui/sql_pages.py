"""The SQL query library: every query found anywhere, a page per query, and "SQL using this table"."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import sqllibrary
from pdcat.store import Catalogue

from .common import asset_button, cached, go, icon


@cached
def _library(cat: Catalogue) -> list[dict]:
    return sqllibrary.queries(cat, include_missing=True)


def queries_page(cat: Catalogue) -> None:
    st.title("SQL queries")
    st.caption("Every piece of SQL found in your scripts, .sql files, notebooks, Excel, Power BI, Access and the SQL "
               "you pasted in. A query copied into several places is listed once, with all its places. Click one "
               "to see it, rename it, and see the tables and fields it uses.")
    rows = _library(cat)
    if not rows:
        st.info("No SQL found yet. Scan some code, workbooks or reports, or paste SQL under Add → Add SQL. "
                "If you've scanned already, build the library now.")
        if st.button("Build the SQL library"):
            sqllibrary.build_library(cat)
            st.rerun()
        return
    c1, c2, c3 = st.columns([2, 2, 1])
    text = c1.text_input("Find", placeholder="name or SQL text", key="sqlq-text")
    tables = sorted({t["name"] for q in rows for t in q["tables"]})
    table = c2.selectbox("Using table", ["(any)", *tables], key="sqlq-table")
    missing = c3.checkbox("Show missing", help="Queries not found on the latest scan", key="sqlq-missing")
    shown = [q for q in rows if (missing or q["status"] == "active")
             and (table == "(any)" or any(t["name"] == table for t in q["tables"]))
             and (not text or text.lower() in q["name"].lower() or text.lower() in q["sql"].lower())]
    st.caption(f"{len(shown)} of {len(rows)} queries · checks: ❗ may give wrong results, ⚠️ risky, 🐢 may be slow")
    from pdcat.sqlchecks import RULES, SEVERITY_ICONS, check_sql

    def checks(sql: str) -> str:
        sev = [RULES[r][0] for r, _ in check_sql(sql)]
        return " ".join(f"{SEVERITY_ICONS[s]}{sev.count(s)}" for s in ("wrong", "risky", "slow") if s in sev)
    df = pd.DataFrame([{"query": q["name"], "tables": ", ".join(t["name"] for t in q["tables"][:6])
                        + (" …" if len(q["tables"]) > 6 else ""), "tables used": len(q["tables"]),
                        "places": q["places"], "status": q["status"],
                        "checks": checks(q["sql"]),
                        "named by": "you" if q["named_by"] == "you" else "auto"} for q in shown])
    if df.empty:
        st.info("Nothing matches.")
        return
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key="sqlq-list")
    sel = event.selection.rows if event else []
    if sel:
        go(f"query://{shown[sel[0]]['uid']}")
        st.rerun()
    st.download_button("⬇️ Download the list (CSV)", df.to_csv(index=False), "sql-queries.csv", "text/csv")


def query_page(cat: Catalogue, key: str) -> None:
    uid = key.removeprefix("query://")
    q = sqllibrary.get(cat, uid)
    if q is None:
        st.warning("This query isn't in the library any more.")
        return
    st.title(f"{icon('query')} {q['name']}")
    found = len({p['item'] for p in q['places']})
    st.caption(f"SQL query · found in {found} place(s)" + (" · named by you" if q["named_by"] == "you" else "")
               + f"  \n`{key}`")
    from .item_boxes import item_actions
    item_actions(cat, key)
    if q["status"] == "missing":
        st.warning("Not found on the latest scan: the script or file it was in may have changed.")
    with st.popover("✏️ Rename"):
        name = st.text_input("Name", value=q["name"], key=f"qname-{uid}")
        if st.button("Save name", key=f"qsave-{uid}", type="primary"):
            sqllibrary.rename(cat, uid, name)
            st.rerun()
        st.caption("Leave it empty to go back to the automatic name on the next scan.")
    reasons = sqllibrary.activity(cat, q)
    if reasons:
        st.caption("✅ Looks in use: " + ", ".join(reasons))
    st.code(q["sql"], language="sql")
    parser = q["analysis"].get("parser")
    if parser == "regex":
        from pdcat.sqlparser import available
        if not available():
            st.caption("Read with a simple pass. Install sqlglot (`pip install sqlglot`) for more exact SQL reading.")

    st.subheader("Where it's found")
    for i, p in enumerate(q["places"]):
        a = cat.get_asset(p["item"])
        label = f"{a['name'] if a else p['item']} · {p['location']}"
        if p.get("function"):
            label += f" · in {p['function']}()"
        asset_button(p["item"], label, a["kind"] if a else "note", f"qp-{uid}-{i}")
        if p.get("built_from_variables"):
            st.caption("⚠️ Built from variables here (an f-string or + joining). Check where those values come from.")

    st.subheader("Tables and fields")
    if not q["tables"]:
        st.caption("No tables found in this SQL.")
    for i, t in enumerate(q["tables"]):
        cols = ", ".join(t["columns"]) if t["columns"] else "no named columns (or SELECT *)"
        what = {"reads": "reads", "writes": "writes", "calls": "calls"}.get(t["relation"], t["relation"])
        line = f"**{t['name']}**" + (f" as `{t['alias']}`" if t.get("alias") else "") + f" · {what} · {cols}"
        if t.get("key"):
            c1, c2 = st.columns([1, 3])
            with c1:
                asset_button(t["key"], t["name"], "table", f"qt-{uid}-{i}")
            c2.markdown(line)
        else:
            st.markdown(line + " · _not scanned_")
    joins = q["analysis"].get("joins", [])
    if joins:
        st.markdown("**Joins:** " + "; ".join(f"`{j['left_table']}.{j['left_column']}` = "
                                               f"`{j['right_table']}.{j['right_column']}` ({j['kind']})"
                                               for j in joins))
    renames = q["analysis"].get("renames", [])
    if renames:
        st.markdown("**Renames:** " + ", ".join(f"`{r['column']}` → `{r['alias']}`" for r in renames))
    if q["analysis"].get("select_star"):
        st.caption("Uses SELECT *: the columns it gets depend on the table.")
    if q["tables"]:
        with st.expander("Field diagram", expanded=len(q["tables"]) > 1):
            from .graphview import graph_view
            graph_view(cat, sqllibrary.field_elements(q), key=f"qdiag-{uid}", height=420, name=f"query-{uid[:6]}")
    _findings_box(cat, q)


def _findings_box(cat: Catalogue, q: dict) -> None:
    try:
        from pdcat import sqlchecks
    except ImportError:
        return
    findings = sqlchecks.findings_for(cat, q)
    if not findings:
        return
    st.subheader(f"Checks ({len(findings)})")
    for i, f in enumerate(findings):
        with st.container(border=True):
            st.markdown(f"{sqlchecks.SEVERITY_ICONS.get(f['severity'], '•')} **{f['title']}**  \n{f['detail']}")
            c1, c2 = st.columns([1, 3])
            if f.get("ok"):
                c1.caption("Marked fine here")
                if c2.button("Check it again", key=f"qf-undo-{q['uid']}-{i}"):
                    sqlchecks.mark_ok(cat, q["uid"], f["rule"], f.get("about", ""), ok=False)
                    st.rerun()
            elif c1.button("Fine here", key=f"qf-ok-{q['uid']}-{i}", help="Don't flag this for this query again"):
                sqlchecks.mark_ok(cat, q["uid"], f["rule"], f.get("about", ""))
                st.rerun()
            if f.get("article"):
                c2.markdown(f"[Why this matters]({f['article']})")


def using_table_box(cat: Catalogue, key: str) -> None:
    """On a table page: the SQL that uses it, active first, old ones greyed."""
    rows = sqllibrary.using_table(cat, key)
    if not rows:
        return
    active = sum(1 for r in rows if not r["old"])
    with st.expander(f"🧾 SQL using this table ({len(rows)}{'' if active == len(rows) else f', {active} active'})",
                     expanded=False):
        from pdcat.learned import usage_note
        note = usage_note(cat, key)
        if note and len(rows) > 1:
            st.markdown(note.replace("\n", "  \n"))
            if st.button("Save this as a note", key=f"usage-note-{key}"):
                cat.add_note(key, note, "usage")
                st.rerun()
            st.divider()
        for i, r in enumerate(rows):
            how = r["relation"] + (f" · columns: {', '.join(r['columns'][:12])}" if r["columns"] else "")
            if r["joins"]:
                how += " · joins " + "; ".join(f"{j['other']} on {j['column']} = {j['other_column']}"
                                               for j in r["joins"][:4])
            where = ", ".join(sorted({p["item"].rsplit("/", 1)[-1] for p in r["places"]})[:3])
            c1, c2 = st.columns([2, 3])
            with c1:
                st.button(("🗄️ " if r["old"] else "🧾 ") + r["name"], key=f"ut-{key}-{i}", type="tertiary",
                          on_click=go, args=(f"query://{r['uid']}",))
            text = f"{how}  \nin {where}" + (f" · ✅ {', '.join(r['active'])}" if r["active"] else "")
            c2.caption(text if not r["old"] else f":gray[{text} · looks old]")


def queries_in_box(cat: Catalogue, key: str) -> None:
    """On a script, workbook, report or Access page: the SQL found in it."""
    rows = sqllibrary.queries_in(cat, key)
    if not rows:
        return
    with st.expander(f"🧾 SQL in this file ({len(rows)})"):
        for i, r in enumerate(rows):
            tables = ", ".join(t["name"] for t in r["tables"][:5])
            c1, c2 = st.columns([2, 3])
            with c1:
                st.button(f"🧾 {r['name']}", key=f"qi-{key}-{i}", type="tertiary", on_click=go,
                          args=(f"query://{r['uid']}",))
            c2.caption(f"{r['location']} · {tables or 'no tables'}")


def renames_box(cat: Catalogue, key: str) -> None:
    """Columns renamed on the way in or out of this item, and a column's path through every rename."""
    from pdcat.colrenames import column_lineage, renames_for
    r = renames_for(cat, key)
    if not r["from"] and not r["to"]:
        return
    with st.expander(f"🔀 Renamed columns ({len(r['from']) + len(r['to'])})"):
        def name(k: str) -> str:
            return (cat.get_asset(k) or {}).get("name", k)
        for x in r["from"]:
            st.markdown(f"`{x['dst_column']}` was `{x['src_column']}` in **{name(x['src_item'])}** · "
                        f"{x['how']} in {x['evidence']}")
        for x in r["to"]:
            st.markdown(f"`{x['src_column']}` becomes `{x['dst_column']}` in **{name(x['dst_item'])}** · "
                        f"{x['how']} in {x['evidence']}")
        cols = sorted({x["dst_column"] for x in r["from"]} | {x["src_column"] for x in r["to"]}, key=str.lower)
        col = st.selectbox("Follow a column through every rename", cols, key=f"ren-col-{key}")
        if col:
            chain = column_lineage(cat, key, col)
            for label, steps in (("Comes from", chain["upstream"]), ("Goes on to", chain["downstream"])):
                if steps:
                    st.caption(label + ": " + " → ".join(f"{name(s['item'])}.{s['column']}" for s in steps))
