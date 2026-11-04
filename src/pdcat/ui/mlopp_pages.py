"""Knowledge › ML opportunities (version 2.21): the list of opportunities ranked by data readiness, each
opportunity's page (checklist, tables, related measures, baseline, splits, business projects), and the recipes."""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pdcat import bprojects as B
from pdcat import mlopps as M
from pdcat import mlrecipes as R
from pdcat.store import Catalogue

from .common import cached, go, lazy_download, page_title

STATE_ICON = {"ready": "🟦", "partial": "🟧", "missing": "⬜"}
BAND_ICON = {"live": "🟢", "ready": "🔵", "partial": "🟠", "notyet": "⚪"}
LEVEL_ICON = {"Complex": "🟧", "Medium": "🟦", "Simple": "⬜"}


@cached
def _overview(cat: Catalogue) -> list[dict[str, Any]]:
    return M.ordered(M.overview(cat))


@cached
def _evaluate(cat: Catalogue, uid: str) -> dict[str, Any] | None:
    return M.evaluate(cat, uid)


def _tool_label(tool: str | None) -> str:
    return R.TOOLS.get(tool or "", ("", ""))[0]


# ---- the list page -------------------------------------------------------------------------------------------

def ml_opportunities_page(cat: Catalogue) -> None:
    page_title("ML opportunities")
    st.caption("Supply chain machine learning projects matched against what the catalogue has scanned. Each one is "
               "scored on six readiness signals: a target, enough history, the right grain, tables that join, "
               "freshness, and a business pain a report already measures. Everything is read from the catalogue; "
               "nothing is sent to your servers.")
    when = cat.setting("ml_checked_at")
    tab_list, tab_chart, tab_shared, tab_recipes = st.tabs(["Opportunities", "Value and readiness", "Shared tables",
                                                            "Recipes"])
    with tab_list:
        _list(cat, when)
    with tab_chart:
        _value_chart(cat)
    with tab_shared:
        _shared(cat)
    with tab_recipes:
        recipes_section(cat)


def _list(cat: Catalogue, when: str | None) -> None:
    c1, c2 = st.columns([4, 1])
    c1.caption(f"Checked after the scan of {when[:16].replace('T', ' ')}." if when else
               "Not checked yet: it runs after each scan, or now.")
    if c2.button("Check now", help="Match the recipes against the catalogue again (reads the catalogue only)"):
        with st.spinner("Checking the recipes…"):
            M.refresh(cat)
        st.rerun()
    with st.expander("➕ New opportunity"):
        new_form(cat)
    rows = _overview(cat)
    if not rows:
        st.info("No opportunities yet. Run a scan, press Check now, or add one above.")
        return
    counts = {b: sum(1 for r in rows if r["band"] == b) for b in M.BANDS}
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Opportunities", len(rows))
    t2.metric("Ready to start", counts["ready"])
    t3.metric("Partly ready", counts["partial"])
    t4.metric("Live", counts["live"])
    show = st.segmented_control("Show", ["All", *M.BANDS.values()], default="All", key="mlo-filter",
                                label_visibility="collapsed") or "All"
    want = {v: k for k, v in M.BANDS.items()}.get(show)
    shown = [r for r in rows if want is None or r["band"] == want]
    if not shown:
        st.caption("None in this group.")
    for r in shown:
        with st.container(border=True):
            a, b = st.columns([6, 1])
            indent = "↳ " if r["parent"] else ""
            label = r["variant"] if r["parent"] and r["variant"] else r["title"]
            a.button(f"{indent}{BAND_ICON[r['band']]} {label}", key=f"mlo-open-{r['uid']}", type="tertiary",
                     on_click=go, args=(f"mlo://{r['uid']}",))
            b.markdown(f"**{r['score']}**")
            st.progress(r["score"] / 100)
            bits = [M.BANDS[r["band"]], _tool_label(r["tool"]), "example" if r["example"] else "",
                    ("in " + ", ".join(r["projects"])) if r["projects"] else "",
                    M.STATUS_LABELS.get(r["status"], r["status"]) if r["status"] != "idea" else ""]
            st.caption(" · ".join(x for x in bits if x) + f"  \n{r['hint']}")


def new_form(cat: Catalogue, prefix: str = "mlo-new") -> None:
    recipes = R.all_recipes(cat)
    with st.form(prefix, clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", placeholder="Customer order cancellations")
        start = c2.selectbox("Start from", ["", *recipes], format_func=lambda k: recipes[k]["name"] if k else "Blank")
        predicts = st.text_area("What it predicts or does", height=68,
                                placeholder="Which open orders a customer is likely to cancel in the next 2 weeks")
        c3, c4 = st.columns(2)
        tool = c3.selectbox("Kind of tool", list(R.TOOLS), format_func=_tool_label)
        opts = _table_options(cat)
        picked = c4.multiselect("Add tables", list(opts), format_func=opts.get, placeholder="Search the catalogue")
        st.caption("Only a name is needed. After saving, pdcat suggests candidate tables and fills in the checklist.")
        if st.form_submit_button("Save opportunity", type="primary"):
            if not name.strip():
                st.warning("Give it a name.")
                return
            r = recipes.get(start) or {}
            uid = M.save(cat, name, recipe=start or None, predicts=predicts or r.get("predicts"),
                         tool=tool or r.get("tool"), method=R.TOOLS.get(tool or "", ("", ""))[1] or r.get("method"))
            for k in picked:
                M.set_item(cat, uid, k, kind="table", role="feature", state="confirmed")
            if start:
                M.suggest_for(cat, uid)
            go(f"mlo://{uid}")
            st.rerun()


@cached
def _table_options(cat: Catalogue) -> dict[str, str]:
    from pdcat.pathbits import file_name
    return {r[0]: f"{file_name(r[1], r[2])} ({r[3]})" for r in cat.conn.execute(
        "SELECT key, name, kind, source FROM assets WHERE status = 'active' AND kind IN ('table', 'view', 'data_file', "
        "'file_series') ORDER BY name LIMIT 20000")}


def _value_chart(cat: Catalogue) -> None:
    rows = [r for r in _overview(cat) if r["value"]]
    st.caption("Rough value (1 to 5) against data readiness. Set value and effort on each opportunity's page; bigger "
               "dots take more effort. Top right is where to start.")
    if not rows:
        st.info("No opportunity has a value yet.")
        return
    import altair as alt
    df = pd.DataFrame([{"opportunity": r["title"], "readiness": r["score"], "value": r["value"],
                        "effort": r["effort"] or 3, "group": M.BANDS[r["band"]]} for r in rows])
    chart = alt.Chart(df).mark_circle(opacity=0.8).encode(
        x=alt.X("readiness:Q", scale=alt.Scale(domain=[0, 100]), title="Data readiness (out of 100)"),
        y=alt.Y("value:Q", scale=alt.Scale(domain=[0, 5.5]), title="Value (1 to 5)"),
        size=alt.Size("effort:Q", scale=alt.Scale(domain=[1, 5], range=[80, 800]), title="Effort"),
        color=alt.Color("group:N", title=""),
        tooltip=["opportunity", "readiness", "value", "effort"]).properties(height=420)
    text = alt.Chart(df).mark_text(align="left", dx=10, fontSize=12).encode(x="readiness:Q", y="value:Q",
                                                                             text="opportunity:N")
    st.altair_chart(chart + text, width="stretch")


def _shared(cat: Catalogue) -> None:
    st.caption("Tables that several opportunities would use: build their data preparation once.")
    rows = M.shared_tables(cat)
    if not rows:
        st.info("No table is used by more than one opportunity yet.")
        return
    for i, r in enumerate(rows[:100]):
        a, b = st.columns([3, 5])
        a.button(r["table"], key=f"mlo-shared-{i}", type="tertiary", on_click=go, args=(r["key"],))
        b.caption(f"{r['count']} opportunities: " + ", ".join(r["opportunities"]))


# ---- one opportunity ---------------------------------------------------------------------------------------------

def opportunity_page(cat: Catalogue, key: str) -> None:
    uid = key.removeprefix("mlo://")
    e = _evaluate(cat, uid)
    if not e:
        st.warning("This opportunity no longer exists.")
        return
    o = e["opp"]
    st.title(M.title(o))
    a, b = st.columns([5, 1])
    with a:
        if o.get("predicts"):
            st.markdown(o["predicts"])
        bits = [f"{BAND_ICON[e['band']]} {M.BANDS[e['band']]}", M.STATUS_LABELS.get(o["status"], o["status"]),
                f"recipe: {e['recipe']['name']}" if e["recipe"] else "your own", f"owner {o['owner']}" if o.get("owner")
                else ""]
        st.caption(" · ".join(x for x in bits if x))
        if e["parent"]:
            st.button(f"↰ Split from {M.title(e['parent'])}", key=f"mlo-parent-{uid}", type="tertiary", on_click=go,
                      args=(f"mlo://{e['parent']['uid']}",))
        for s in e["splits"]:
            st.button(f"↳ {s.get('variant') or s['name']}", key=f"mlo-split-{s['uid']}", type="tertiary",
                      on_click=go, args=(f"mlo://{s['uid']}",))
    b.metric("Readiness", f"{e['score']}", help="Out of 100. Target and history count double by default (Settings).")
    _actions(cat, uid, o)
    _tool_box(o, e)
    _projects_row(cat, uid, e)
    _checklist(cat, uid, e)
    _tables(cat, uid, e)
    _measures(cat, uid, e)
    _baseline(cat, uid, o, e)
    _warnings(e)
    _feature_ideas(cat, uid)


def _actions(cat: Catalogue, uid: str, o: dict[str, Any]) -> None:
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    with c1:
        lazy_download("📦 Data scientist pack", lambda c: M.export_pack(c, uid), f"ml-opportunity-{uid}.zip",
                      "application/zip", key=f"mlo-pack-{uid}",
                      help="A zip with the checks, columns (with SAP names and profiles), joins, related measures, "
                           "baseline and starter SQL")
    if c2.button("✂️ Split into a new target", key=f"mlo-split-btn-{uid}",
                 help="A copy with its own target: it starts with this one's tables, ticks and links, then goes its "
                      "own way"):
        new = M.split(cat, uid)
        go(f"mlo://{new}")
        st.rerun()
    status = c3.selectbox("Status", M.STATUSES, index=M.STATUSES.index(o["status"]) if o["status"] in M.STATUSES
                          else 0, format_func=M.STATUS_LABELS.get, key=f"mlo-status-{uid}",
                          label_visibility="collapsed")
    if status != o["status"]:
        M.save(cat, o["name"], uid, status=status)
        st.rerun()
    with c4.popover("✏️ Edit"):
        _edit_form(cat, uid, o)


def _edit_form(cat: Catalogue, uid: str, o: dict[str, Any]) -> None:
    recipes = R.all_recipes(cat, include_off=True)
    with st.form(f"mlo-edit-{uid}"):
        name = st.text_input("Name", value=o["name"])
        variant = st.text_input("Target name (for splits)", value=o.get("variant") or "",
                                placeholder="Target B: late or on time")
        predicts = st.text_area("What it predicts or does", value=o.get("predicts") or "", height=68)
        keys = ["", *recipes]
        recipe = st.selectbox("Recipe it is checked against", keys, index=keys.index(o["recipe"])
                              if o.get("recipe") in keys else 0,
                              format_func=lambda k: recipes[k]["name"] if k else "None")
        tools = list(R.TOOLS)
        c1, c2 = st.columns(2)
        tool = c1.selectbox("Kind of tool", tools, index=tools.index(o["tool"]) if o.get("tool") in tools else 0,
                            format_func=_tool_label)
        method = c2.text_input("Method", value=o.get("method") or "", placeholder="LightGBM regression")
        c3, c4, c5 = st.columns(3)
        owner = c3.text_input("Owner", value=o.get("owner") or "")
        value = c4.number_input("Value (1 to 5)", 0, 5, int(o.get("value") or 0), help="0 = not set")
        effort = c5.number_input("Effort (1 to 5)", 0, 5, int(o.get("effort") or 0), help="0 = not set")
        notes = st.text_area("Notes", value=o.get("notes") or "", height=80)
        if st.form_submit_button("Save", type="primary"):
            M.save(cat, name, uid, variant=variant or None, predicts=predicts, recipe=recipe or None, tool=tool,
                   method=method or R.TOOLS[tool][1], owner=owner, value=value or None, effort=effort or None,
                   notes=notes)
            if recipe and recipe != o.get("recipe"):
                M.suggest_for(cat, uid)
            st.rerun()
    if st.button("Delete this opportunity", key=f"mlo-del-{uid}"):
        M.delete(cat, uid)
        go(None)
        st.rerun()


def _tool_box(o: dict[str, Any], e: dict[str, Any]) -> None:
    with st.container(border=True):
        a, b = st.columns([2, 5])
        a.caption("Kind of tool")
        a.markdown(f"**{_tool_label(o.get('tool')) or 'Not sure yet'}**  \n{o.get('method') or ''}")
        b.caption("Why this fits")
        b.markdown(e["recipe"].get("why") or "Pick a kind of tool under Edit. LightGBM suits forecasts and tabular "
                                               "predictions; an AI agent suits questions in plain language.")


def _projects_row(cat: Catalogue, uid: str, e: dict[str, Any]) -> None:
    st.markdown("**Business projects**")
    linked = e["projects"]
    cols = st.columns(max(1, min(len(linked), 4)) + 1)
    for i, p in enumerate(linked[:4]):
        with cols[i]:
            st.button(f"🧭 {p['name']} ({p['members']})", key=f"mlo-bp-{uid}-{p['uid']}", type="tertiary",
                      on_click=go, args=(f"bp://{p['uid']}",))
            if st.button("Unlink", key=f"mlo-bpu-{uid}-{p['uid']}"):
                M.unlink_project(cat, uid, p["uid"])
                st.rerun()
    with cols[-1]:
        options = {p["uid"]: p["name"] for p in B.list_projects(cat) if p["uid"] not in {x["uid"] for x in linked}}
        choice = st.selectbox("Link to", ["", *options, "+new"], key=f"mlo-bp-pick-{uid}",
                              format_func=lambda k: options.get(k, "+ New business project" if k == "+new" else
                                                                "Pick a business project…"),
                              label_visibility="collapsed")
        if choice == "+new":
            with st.form(f"mlo-bp-new-{uid}", clear_on_submit=True):
                name = st.text_input("New business project")
                if st.form_submit_button("Create and link") and name.strip():
                    M.link_project(cat, uid, B.save_project(cat, name))
                    st.rerun()
        elif choice:
            n = M.link_project(cat, uid, choice)
            st.session_state.pop(f"mlo-bp-pick-{uid}", None)
            st.toast(f"Linked; {n} of its tables suggested")
            st.rerun()
    st.caption("Datasets in a linked business project that share keys with this opportunity's tables are suggested "
               "as candidates, and the opportunity shows on the project's page.")


def _checklist(cat: Catalogue, uid: str, e: dict[str, Any]) -> None:
    st.subheader("Readiness checklist")
    labels = {"": "Automatic", **{s: M.STATE_LABELS[s] for s in M.STATES}}
    for s in e["signals"]:
        with st.container(border=True):
            a, b, c = st.columns([6, 1.3, 1.6])
            a.markdown(f"{STATE_ICON[s['state']]} **{s['name']}** · {M.STATE_LABELS[s['state']]}"
                       + (" (set by you)" if s.get("overridden") else ""))
            a.caption(s["evidence"] + (f"  \nNote: {s['note']}" if s.get("note") else ""))
            ok = b.checkbox("Confirmed", value=s["confirmed"], key=f"mlo-ok-{uid}-{s['id']}",
                            help=M.SIGNAL_HELP[s["id"]])
            if ok != s["confirmed"]:
                M.set_check(cat, uid, s["id"], confirmed=ok)
                st.rerun()
            cur = s["state"] if s.get("overridden") else ""
            pick = c.selectbox("Set", list(labels), index=list(labels).index(cur), format_func=labels.get,
                               key=f"mlo-ov-{uid}-{s['id']}", label_visibility="collapsed")
            if pick != cur:
                M.set_check(cat, uid, s["id"], override=pick)
                st.rerun()


def _tables(cat: Catalogue, uid: str, e: dict[str, Any]) -> None:
    st.subheader("Candidate tables")
    st.caption("Picked from SAP meanings, column names and joins in your SQL. Suggested rows become yours when you "
               "change their role or set them to confirmed; rejected ones aren't suggested again.")
    states = ["confirmed", "suggested", "rejected"]
    roles = [r for r in M.ROLES if r]
    if e["tables"]:
        df = pd.DataFrame([{"key": t["key"], "table": t["name"], "rows": t["rows"], "history": t["history"],
                            "type": t["type"], "role": t["role"] or "feature", "state": t["state"], "why": t["why"]}
                           for t in e["tables"]])
        edited = st.data_editor(df, hide_index=True, width="stretch", key=f"mlo-tables-{uid}",
                                disabled=["key", "table", "rows", "history", "type", "why"],
                                column_config={"key": None,
                                               "rows": st.column_config.NumberColumn(format="localized"),
                                               "role": st.column_config.SelectboxColumn(options=roles),
                                               "state": st.column_config.SelectboxColumn(options=states),
                                               "why": st.column_config.TextColumn("why it was picked", width="large")})
        if _save_edits(cat, uid, df, edited, "table"):
            st.rerun()
    else:
        st.caption("No tables yet.")
    if e["columns"]:
        with st.expander(f"Columns ({len(e['columns'])}): friendly name with the SAP original"):
            df = pd.DataFrame([{"item": c["item"], "column": c["column"], "role": c["role"] or "feature",
                                "state": c["state"], "why": c["why"]} for c in e["columns"]])
            edited = st.data_editor(df, hide_index=True, width="stretch", key=f"mlo-cols-{uid}",
                                    disabled=["item", "column", "why"],
                                    column_config={"item": None,
                                                   "role": st.column_config.SelectboxColumn(options=roles),
                                                   "state": st.column_config.SelectboxColumn(options=states),
                                                   "why": st.column_config.TextColumn(width="large")})
            if _save_edits(cat, uid, df, edited, "column", key_col="item"):
                st.rerun()
    opts = _table_options(cat)
    with st.form(f"mlo-add-{uid}", clear_on_submit=True):
        c1, c2, c3 = st.columns([4, 1.5, 1])
        picked = c1.multiselect("Add tables", [k for k in opts if k not in {t["key"] for t in e["tables"]}],
                                format_func=opts.get, placeholder="Search the catalogue",
                                label_visibility="collapsed")
        role = c2.selectbox("Role", roles, index=roles.index("feature"), label_visibility="collapsed")
        if c3.form_submit_button("Add") and picked:
            for k in picked:
                M.set_item(cat, uid, k, kind="table", role=role, state="confirmed")
            st.rerun()


def _save_edits(cat: Catalogue, uid: str, before: pd.DataFrame, after: pd.DataFrame, kind: str,
                key_col: str = "key") -> bool:
    changed = False
    for (_, old), (_, new) in zip(before.iterrows(), after.iterrows(), strict=False):
        if (old["role"], old["state"]) != (new["role"], new["state"]):
            state = new["state"]
            if state == "suggested" and old["role"] != new["role"]:
                state = "confirmed"  # changing a suggestion's role makes it yours
            M.set_item(cat, uid, old[key_col], kind=kind, role=new["role"], state=state)
            changed = True
    return changed


def _measures(cat: Catalogue, uid: str, e: dict[str, Any]) -> None:
    rel = e["related"]
    st.subheader("Related Power BI reports and measures")
    st.caption(f"{sum(m['ticked'] for m in rel)} of {len(rel)} ticked as related. Found because their report reads "
               "this opportunity's tables or their name matches the recipe. Complex measures often hold business "
               "logic worth turning into a target.")
    if not rel:
        st.caption("None found yet.")
        return
    for i, m in enumerate(rel):
        with st.container(border=True):
            a, b, c = st.columns([1.2, 6, 1.6])
            ticked = a.checkbox("Related", value=m["ticked"], key=f"mlo-m-{uid}-{i}")
            if ticked != m["ticked"]:
                M.tick_measure(cat, uid, m["key"], ticked)
                st.rerun()
            b.markdown(f"**{m['measure']}** in {m['report']} · {LEVEL_ICON[m['level']]} {m['level']}"
                       + (" · 🎯 target" if m["target"] else ""))
            b.caption(f"{m['detail']}  \n{m['found']}")
            if m["dax"]:
                with b.expander("DAX"):
                    st.code(m["dax"], language="sql")
            if c.button("Use as target", key=f"mlo-tgt-{uid}-{i}",
                        help="Marks it as the target and shows SQL you can save as a view yourself (never run here)"):
                st.session_state[f"mlo-sql-{uid}"] = M.use_as_target(cat, uid, m["key"])
                st.rerun()
    sql = st.session_state.get(f"mlo-sql-{uid}")
    if sql:
        st.caption("Copy this, translate the DAX into the expression, and save it as a view yourself. "
                   "pdcat never runs it or creates anything on your server.")
        st.code(sql, language="sql")


def _baseline(cat: Catalogue, uid: str, o: dict[str, Any], e: dict[str, Any]) -> None:
    st.subheader("Baseline to beat")
    b = e["baseline"]
    st.caption("How good today's answer already is. A model has to do better than this to be worth it.")
    if b["pair"]:
        st.markdown(f"Plan against actual in **{b['pair']['table']}**: {b['pair']['what']}.")
        st.caption("Run this read-only query yourself and type the result below.")
        st.code(b["sql"], language="sql")
    if b["measure"]:
        st.markdown(f"Reported today by **{b['measure']['measure']}** in {b['measure']['report']}.")
    if not (b["pair"] or b["measure"]):
        st.caption("No plan-against-actual pair or related measure found. Type today's figure if you know it.")
    with st.form(f"mlo-base-{uid}"):
        typed = st.text_input("Today's figure", value=b["typed"],
                              placeholder="Planned lead time is off by 6.2 days on average")
        if st.form_submit_button("Save"):
            M.save(cat, o["name"], uid, baseline=typed)
            st.rerun()


def _warnings(e: dict[str, Any]) -> None:
    if e["leakage"]:
        st.subheader("Leakage check")
        for w in e["leakage"]:
            st.warning(w, icon="⚠️")
    if e["quality"]:
        st.subheader("Target quality")
        for q in e["quality"]:
            (st.warning if q["level"] == "warn" else st.caption)(q["text"])
    if e["gaps"]:
        st.subheader("Gaps and where to look")
        for g in e["gaps"]:
            st.markdown(f"- {g}")


def _feature_ideas(cat: Catalogue, uid: str) -> None:
    st.subheader("Feature ideas")
    st.caption("Tables that join to the target table, or share its keys, that no script of this opportunity or its "
               "business projects reads yet. Most useful for a live model.")
    if st.button("Look for feature ideas", key=f"mlo-ideas-{uid}"):
        st.session_state[f"mlo-ideas-{uid}"] = M.feature_ideas(cat, uid)
    for i, r in enumerate(st.session_state.get(f"mlo-ideas-{uid}") or []):
        a, b = st.columns([5, 1])
        a.button(r["table"], key=f"mlo-idea-{uid}-{i}", type="tertiary", on_click=go, args=(r["key"],), help=r["why"])
        a.caption(r["why"])
        if b.button("Add", key=f"mlo-idea-add-{uid}-{i}"):
            M.set_item(cat, uid, r["key"], kind="table", role="feature", state="confirmed",
                       why=f"feature idea: {r['why']}")
            st.session_state[f"mlo-ideas-{uid}"] = [x for x in st.session_state[f"mlo-ideas-{uid}"]
                                                     if x["key"] != r["key"]]
            st.rerun()


# ---- recipes -------------------------------------------------------------------------------------------------

def _csv(values: list[str]) -> str:
    return ", ".join(values or [])


def _split(text: str) -> list[str]:
    return [w.strip() for w in (text or "").split(",") if w.strip()]


def recipes_section(cat: Catalogue) -> None:
    st.caption("The standard supply chain projects pdcat checks for, and what each one looks for. Switch off the ones "
               "you don't need, change their words, or add your own. Your changes are kept in the catalogue.")
    recipes = R.all_recipes(cat, include_off=True)
    for rid, r in recipes.items():
        with st.container(border=True):
            a, b = st.columns([6, 1])
            a.markdown(f"**{r['name']}**" + (" · example" if r.get("example") else "")
                       + (" · changed" if r.get("changed") and r.get("builtin") else "")
                       + ("" if r.get("builtin") else " · yours"))
            a.caption(f"{r['predicts']}  \n{_tool_label(r['tool'])}: {r.get('method') or ''}")
            on = b.toggle("On", value=r["on"], key=f"mlr-on-{rid}")
            if on != r["on"]:
                R.set_on(cat, rid, on)
                st.rerun()
            with st.expander("What it looks for"):
                _recipe_form(cat, rid, r)
    with st.expander("➕ Add your own recipe"):
        with st.form("mlr-new", clear_on_submit=True):
            name = st.text_input("Name", placeholder="Customer order cancellations")
            predicts = st.text_input("What it predicts")
            target = st.text_input("Target column names (comma separated)", placeholder="cancelled, rejectionreason")
            target_sap = st.text_input("Target SAP fields", placeholder="ABGRU")
            keys = st.text_input("Key SAP fields", placeholder="VBELN, MATNR, KUNNR")
            tool = st.selectbox("Kind of tool", list(R.TOOLS), format_func=_tool_label)
            if st.form_submit_button("Add recipe", type="primary") and name.strip():
                rid = R.new_recipe_id(cat, name)
                concepts = [{"id": "target", "label": "Target", "role": "target", "sap": _split(target_sap.upper()),
                             "names": _split(target)}]
                for k in _split(keys.upper()):
                    concepts.append({"id": k.lower(), "label": k, "role": "key", "sap": [k], "names": []})
                R.save_recipe(cat, rid, {"name": name.strip(), "predicts": predicts, "tool": tool,
                                         "method": R.TOOLS[tool][1], "concepts": concepts,
                                         "grain": [c["id"] for c in concepts if c["role"] == "key"][:2]})
                M.refresh(cat)
                st.rerun()


def _recipe_form(cat: Catalogue, rid: str, r: dict[str, Any]) -> None:
    with st.form(f"mlr-edit-{rid}"):
        name = st.text_input("Name", value=r["name"])
        predicts = st.text_input("What it predicts", value=r.get("predicts") or "")
        c1, c2 = st.columns(2)
        sap_tables = c1.text_input("SAP tables", value=_csv(r.get("sap_tables")))
        table_words = c2.text_input("Words in table names", value=_csv(r.get("table_words")))
        measure_words = st.text_input("Words in measure names", value=_csv(r.get("measure_words")))
        st.caption("Columns it looks for (role · SAP fields · friendly names):")
        edits = []
        for c in r.get("concepts", []):
            a, b, d = st.columns([1.5, 1.5, 4])
            a.markdown(f"**{c['label']}** · {c['role']}")
            sap = b.text_input("SAP", value=_csv(c.get("sap")), key=f"mlr-{rid}-{c['id']}-sap",
                               label_visibility="collapsed")
            names = d.text_input("Names", value=_csv(c.get("names")), key=f"mlr-{rid}-{c['id']}-names",
                                 label_visibility="collapsed")
            edits.append((c, sap, names))
        c1, c2 = st.columns(2)
        save = c1.form_submit_button("Save", type="primary")
        reset = c2.form_submit_button("Back to the built-in recipe" if r.get("builtin") else "Delete this recipe")
    if save:
        new = {k: v for k, v in r.items() if k not in ("id", "on", "changed", "builtin")}
        new.update({"name": name.strip() or r["name"], "predicts": predicts, "sap_tables": _split(sap_tables.upper()),
                    "table_words": _split(table_words), "measure_words": _split(measure_words),
                    "concepts": [{**c, "sap": _split(sap.upper()), "names": _split(names)} for c, sap, names in edits]})
        R.save_recipe(cat, rid, new)
        st.toast("Saved. Press Check now on the Opportunities tab to see its effect.")
        st.rerun()
    if reset:
        R.reset_recipe(cat, rid)
        st.rerun()


# ---- boxes on other pages --------------------------------------------------------------------------------------

def opportunities_box(cat: Catalogue, *, project_uid: str | None = None, item_key: str | None = None) -> None:
    """On a business project's page (its linked opportunities) or a table's page (opportunities that use it)."""
    try:
        opps = M.opps_for_project(cat, project_uid) if project_uid else M.opps_for_item(cat, item_key or "")
    except Exception:  # an older catalogue open read-only, without the ML tables
        return
    if not opps:
        return
    st.markdown("**ML opportunities**")
    for o in opps:
        st.button(f"🤖 {M.title(o)}", key=f"mlo-box-{project_uid or item_key}-{o['uid']}", type="tertiary",
                  on_click=go, args=(f"mlo://{o['uid']}",))
