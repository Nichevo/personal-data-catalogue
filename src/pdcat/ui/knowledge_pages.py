"""Knowledge notes, the question library and the metric glossary."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import knowledge as K
from pdcat.store import Catalogue

from .common import asset_button, first_line, go


def _table_options(cat: Catalogue) -> dict[str, str]:
    return {r["key"]: f"{r['name']} ({r['source']})" for r in cat.conn.execute(
        "SELECT key, name, source FROM assets WHERE kind IN ('table', 'view', 'data_file', 'file_series', 'report', "
        "'measure') AND status = 'active' ORDER BY name")}


# ---- knowledge -------------------------------------------------------------------

def knowledge_page(cat: Catalogue) -> None:
    st.title("Knowledge")
    st.caption("What you know that isn't a table or a script: business rules, definitions, how-tos, history, quirks. "
               "A note can stand alone. Give it trigger words and it shows as \"Related knowledge\" wherever they "
               "appear: in searches, and on tables, columns and scripts that mention them.")
    with st.expander("➕ New knowledge note", expanded=not cat.conn.execute("SELECT 1 FROM knowledge").fetchone()):
        knowledge_form(cat, None)
    sections = ["All", *K.SECTIONS]
    section = st.radio("Section", sections, horizontal=True, label_visibility="collapsed")
    notes = K.list_knowledge(cat, None if section == "All" else section)
    if not notes:
        st.info("No knowledge notes here yet.")
    for k in notes:
        with st.container(border=True):
            st.button(f"💡 {k['title']}", key=f"kbl-{k['uid']}", on_click=go, args=(f"kb://{k['uid']}",),
                      type="tertiary")
            st.caption(" · ".join(b for b in (k["section"], f"triggers: {k['triggers']}" if k["triggers"] else "",
                                              k["author"]) if b))
            st.markdown(first_line(k["body"], 400))


def knowledge_form(cat: Catalogue, k: dict | None) -> None:
    with st.form(f"kb-form-{k['uid'] if k else 'new'}", clear_on_submit=k is None):
        title = st.text_input("Title", value=(k or {}).get("title", ""),
                              placeholder="Freight is always sea freight unless marked AIR")
        body = st.text_area("Details (Markdown works)", value=(k or {}).get("body", ""), height=160)
        c1, c2, c3 = st.columns(3)
        opts = ["", *K.SECTIONS]
        current = (k or {}).get("section") or ""
        section = c1.selectbox("Section", opts, index=opts.index(current) if current in opts else 0)
        triggers = c2.text_input("Trigger words", value=(k or {}).get("triggers", ""),
                                 placeholder="freight, shipping, carrier",
                                 help="Comma separated. The note shows wherever these words appear.")
        tags = c3.text_input("Tags", value=(k or {}).get("tags", ""))
        if st.form_submit_button("Save", type="primary"):
            try:
                uid = K.save_knowledge(cat, title, body, section=section, tags=tags, triggers=triggers,
                                       uid=(k or {}).get("uid"))
                go(f"kb://{uid}")
                st.rerun()
            except ValueError as e:
                st.error(str(e))


def knowledge_item_page(cat: Catalogue, key: str) -> None:
    if key.startswith("q://"):
        question_page(cat, key)
        return
    if key.startswith("metric://"):
        metric_page(cat, key)
        return
    k = K.get_knowledge(cat, key.removeprefix("kb://"))
    if not k:
        st.warning("This knowledge note no longer exists.")
        return
    st.title(f"💡 {k['title']}")
    st.caption(" · ".join(b for b in (k["section"], k["author"], f"updated {k['updated_at'][:10]}",
                                      f"confirmed {k['confirmed_at'][:10]}" if k.get("confirmed_at") else "") if b))
    st.markdown(k["body"] or "_No details yet._")
    if k["triggers"]:
        st.caption(f"Shows wherever these appear: {k['triggers']}")
    c1, c2 = st.columns([1, 5])
    if c1.button("✓ Still true"):
        K.mark_confirmed(cat, k["uid"])
        st.rerun()
    with st.expander("✏️ Edit"):
        knowledge_form(cat, k)
        if st.button("Delete this note"):
            K.delete_knowledge(cat, k["uid"])
            go(None)
            st.rerun()
    st.subheader("Also show it on")
    tables = _table_options(cat)
    for i, p in enumerate(k["pinned"]):
        c1, c2 = st.columns([4, 1])
        with c1:
            asset_button(p, tables.get(p, p), "table", f"pin-{i}")
        if c2.button("Unpin", key=f"unpin-{i}"):
            with cat.conn:
                import json
                cat.conn.execute("UPDATE knowledge SET pinned = ? WHERE uid = ?",
                                 (json.dumps([x for x in k["pinned"] if x != p]), k["uid"]))
            cat.changed()
            st.rerun()
    with st.form(f"pin-{k['uid']}"):
        item = st.selectbox("Item", list(tables), format_func=tables.get, index=None, placeholder="Pick an item…")
        if st.form_submit_button("Pin") and item:
            K.pin(cat, k["uid"], item)
            st.rerun()
    if k["excluded"]:
        st.caption("Hidden on: " + ", ".join(tables.get(x, x) for x in k["excluded"]))


# ---- questions -------------------------------------------------------------------

def questions_page(cat: Catalogue) -> None:
    st.title("Question library")
    st.caption("Real questions analysts ask, with the tables and query that answer them and the caveats. "
               "They show on the tables they use, and go into the Copilot pack as worked examples.")
    with st.expander("➕ New question"):
        question_form(cat, None)
    qs = K.list_questions(cat)
    if not qs:
        st.info("No questions yet.")
    for q in qs:
        with st.container(border=True):
            st.button(f"❓ {q['question']}", key=f"ql-{q['uid']}", on_click=go, args=(f"q://{q['uid']}",),
                      type="tertiary")
            if q["answer"]:
                st.caption(first_line(q["answer"], 300))


def question_form(cat: Catalogue, q: dict | None) -> None:
    tables = _table_options(cat)
    with st.form(f"q-form-{q['uid'] if q else 'new'}", clear_on_submit=q is None):
        question = st.text_input("Question", value=(q or {}).get("question", ""),
                                 placeholder="What were weekly sales by region last quarter?")
        answer = st.text_area("How to answer it", value=(q or {}).get("answer", ""), height=100)
        sql = st.text_area("Query", value=(q or {}).get("sql", ""), height=140,
                           help="The tables it reads are linked automatically.")
        picked = st.multiselect("Tables (if not in the query)", list(tables), default=[t for t in (q or {}).get(
            "tables", []) if t in tables], format_func=tables.get)
        caveats = st.text_area("Caveats", value=(q or {}).get("caveats", ""), height=70)
        tags = st.text_input("Tags", value=(q or {}).get("tags", ""))
        if st.form_submit_button("Save", type="primary"):
            try:
                from pdcat.knowledge import _resolve_tables
                from pdcat.sqlrefs import extract_refs
                found = _resolve_tables(cat, [r.name for r in extract_refs(sql)]) if sql.strip() else []
                uid = K.save_question(cat, question, answer=answer, sql=sql, caveats=caveats, tags=tags,
                                      tables=list(dict.fromkeys(found + picked)), uid=(q or {}).get("uid"))
                go(f"q://{uid}")
                st.rerun()
            except ValueError as e:
                st.error(str(e))


def question_page(cat: Catalogue, key: str) -> None:
    q = K.get_question(cat, key.removeprefix("q://"))
    if not q:
        st.warning("This question no longer exists.")
        return
    st.title(f"❓ {q['question']}")
    st.caption(" · ".join(b for b in (q["author"], f"updated {q['updated_at'][:10]}", q["tags"]) if b))
    if q["answer"]:
        st.markdown(q["answer"])
    if q["sql"]:
        st.code(q["sql"], language="sql")
    if q["caveats"]:
        st.warning(q["caveats"])
    if q["tables"]:
        st.subheader("Uses")
        for i, t in enumerate(q["tables"]):
            a = cat.get_asset(t)
            asset_button(t, a["name"] if a else t, a["kind"] if a else "table", f"qt-{i}")
    with st.expander("✏️ Edit"):
        question_form(cat, q)
        if st.button("Delete this question"):
            K.delete_question(cat, q["uid"])
            go(None)
            st.rerun()


# ---- metrics ---------------------------------------------------------------------

def metrics_page(cat: Catalogue) -> None:
    st.title("Metric glossary")
    st.caption("What each business number means, how it's worked out, where it comes from and who owns it. "
               "Power BI measures can be turned into metrics from their page.")
    with st.expander("➕ New metric"):
        metric_form(cat, None)
    from pdcat import suggestions
    waiting = len(suggestions.open_suggestions(cat, "metric"))
    if waiting:
        with st.expander(f"💡 Suggested metrics ({waiting})", expanded=not K.list_metrics(cat)):
            st.caption("Calculations found in your code, SQL and reports that aren't in the glossary yet. Tick the "
                       "ones to add; rejected ones don't come back.")
            from .groups_page import suggestion_picker
            suggestion_picker(cat, "metric", prefix="mp")
    rows = K.list_metrics(cat)
    if not rows:
        st.info("No metrics yet.")
        return
    df = pd.DataFrame([{"metric": m["name"], "definition": first_line(m["definition"], 160), "owner": m["owner"],
                        "sources": len(m["sources"])} for m in rows])
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key="metrics-table")
    sel = event.selection.rows if event else []
    if sel:
        go(f"metric://{rows[sel[0]]['uid']}")
        st.rerun()


def metric_form(cat: Catalogue, m: dict | None, prefill: dict | None = None) -> None:
    tables = _table_options(cat)
    base = {**(prefill or {}), **(m or {})}
    with st.form(f"m-form-{m['uid'] if m else 'new'}", clear_on_submit=m is None):
        name = st.text_input("Name", value=base.get("name", ""), placeholder="Forecast accuracy")
        definition = st.text_area("What it means", value=base.get("definition", ""), height=90)
        formula = st.text_area("How it's worked out", value=base.get("formula", ""), height=80,
                               placeholder="1 - SUM(ABS(actual - forecast)) / SUM(actual)")
        sources = st.multiselect("Comes from", list(tables), default=[s for s in base.get("sources", []) if s in tables],
                                 format_func=tables.get)
        c1, c2 = st.columns(2)
        owner = c1.text_input("Owner", value=base.get("owner", ""))
        tags = c2.text_input("Tags", value=base.get("tags", ""))
        caveats = st.text_area("Caveats", value=base.get("caveats", ""), height=60)
        if st.form_submit_button("Save", type="primary"):
            try:
                uid = K.save_metric(cat, name, definition=definition, formula=formula, sources=sources, owner=owner,
                                    caveats=caveats, tags=tags, from_measure=base.get("from_measure", ""),
                                    uid=(m or {}).get("uid"))
                go(f"metric://{uid}")
                st.rerun()
            except ValueError as e:
                st.error(str(e))


def metric_page(cat: Catalogue, key: str) -> None:
    m = K.get_metric(cat, key.removeprefix("metric://"))
    if not m:
        st.warning("This metric no longer exists.")
        return
    st.title(f"📏 {m['name']}")
    st.caption(" · ".join(b for b in (f"owner {m['owner']}" if m["owner"] else "", m["tags"],
                                      f"updated {m['updated_at'][:10]}") if b))
    if m["definition"]:
        st.markdown(m["definition"])
    if m["formula"]:
        st.code(m["formula"], language=None)
    if m["caveats"]:
        st.warning(m["caveats"])
    if m["from_measure"]:
        st.caption(f"From Power BI measure `{m['from_measure']}`")
    if m["sources"]:
        st.subheader("Comes from")
        for i, s in enumerate(m["sources"]):
            a = cat.get_asset(s)
            asset_button(s, a["name"] if a else s, a["kind"] if a else "table", f"ms-{i}")
    with st.expander("✏️ Edit"):
        metric_form(cat, m)
        if st.button("Delete this metric"):
            K.delete_metric(cat, m["uid"])
            go(None)
            st.rerun()
