"""Business projects: groups of tables, scripts, files and reports that serve one purpose."""
from __future__ import annotations

import streamlit as st

from pdcat import bprojects as B
from pdcat.store import Catalogue

from .common import asset_button, first_line, go, kind_label


def _item_options(cat: Catalogue) -> dict[str, str]:
    return {r["key"]: f"{r['name']} ({kind_label(r['kind'])}, {r['source']})" for r in cat.conn.execute(
        "SELECT key, name, kind, source FROM assets WHERE status = 'active' AND kind NOT IN ('series_file', "
        "'database', 'function', 'column') ORDER BY name")}


def bprojects_page(cat: Catalogue) -> None:
    st.title("Business projects")
    st.caption("Group what serves one purpose (\"Demand forecasting\", \"Freight costs\"): its tables, scripts, files, "
               "reports and knowledge. Add a few members and related items are suggested for you to accept.")
    projects = B.list_projects(cat)
    with st.expander("➕ New business project", expanded=not projects):
        project_form(cat, None)
    for p in projects:
        with st.container(border=True):
            st.button(f"🧭 {p['name']}", key=f"bpl-{p['uid']}", on_click=go, args=(f"bp://{p['uid']}",),
                      type="tertiary")
            st.caption(" · ".join(b for b in (p["status"] or "", f"owner {p['owner']}" if p["owner"] else "",
                                              f"{p['members']} items") if b))
            if p["purpose"]:
                st.markdown(first_line(p["purpose"], 300))


def project_form(cat: Catalogue, p: dict | None) -> None:
    with st.form(f"bp-form-{p['uid'] if p else 'new'}", clear_on_submit=p is None):
        name = st.text_input("Name", value=(p or {}).get("name", ""), placeholder="Demand forecasting")
        purpose = st.text_area("Purpose", value=(p or {}).get("purpose") or "", height=80)
        c1, c2 = st.columns(2)
        current = (p or {}).get("status") or ""
        status = c1.selectbox("Status", B.STATUSES, index=B.STATUSES.index(current) if current in B.STATUSES else 0)
        owner = c2.text_input("Owner", value=(p or {}).get("owner") or "")
        notes = st.text_area("Notes", value=(p or {}).get("notes") or "", height=80)
        if st.form_submit_button("Save", type="primary"):
            try:
                uid = B.save_project(cat, name, purpose=purpose, status=status, owner=owner, notes=notes,
                                     uid=(p or {}).get("uid"))
                go(f"bp://{uid}")
                st.rerun()
            except ValueError as e:
                st.error(str(e))


def bproject_page(cat: Catalogue, key: str) -> None:
    uid = key.removeprefix("bp://")
    p = B.get_project(cat, uid)
    if not p:
        st.warning("This business project no longer exists.")
        return
    st.title(f"🧭 {p['name']}")
    st.caption(" · ".join(b for b in (p["status"] or "", f"owner {p['owner']}" if p["owner"] else "", p["author"],
                                      f"updated {p['updated_at'][:10]}") if b))
    if p["purpose"]:
        st.markdown(p["purpose"])
    if p["notes"]:
        st.markdown(p["notes"])
    with st.expander("✏️ Edit"):
        project_form(cat, p)
        if st.button("Delete this business project"):
            B.delete_project(cat, uid)
            go(None)
            st.rerun()

    st.subheader("Items")
    have = B.members(cat, uid)
    if not have:
        st.caption("Nothing in it yet. Add a few items below.")
    for i, m in enumerate(have):
        c1, c2 = st.columns([5, 1])
        with c1:
            asset_button(m["key"], m["name"], m["kind"], f"bpm-{i}", help=m["source"])
        if c2.button("Remove", key=f"bprm-{i}"):
            B.remove_member(cat, uid, m["key"])
            st.rerun()
    options = _item_options(cat)
    with st.form(f"bp-add-{uid}", clear_on_submit=True):
        picked = st.multiselect("Add items", [k for k in options if k not in {m["key"] for m in have}],
                                format_func=options.get, placeholder="Pick tables, scripts, files…")
        if st.form_submit_button("Add") and picked:
            for k in picked:
                B.set_member(cat, uid, k)
            st.rerun()

    if have or p["purpose"]:
        st.subheader("Suggested")
        if st.button("Find related items", help="What its scripts read and write, scripts that read its tables, and "
                                                "items whose names share the project's words"):
            st.session_state[f"bp-sugg-{uid}"] = B.suggest(cat, uid)
        sugg = st.session_state.get(f"bp-sugg-{uid}")
        if sugg is None:
            stored = B.members(cat, uid, "suggested")
            sugg = [{**s, "why": ""} for s in stored]
        for i, s in enumerate(sugg):
            c1, c2, c3 = st.columns([5, 1, 1])
            with c1:
                asset_button(s["key"], s["name"], s["kind"], f"bps-{i}", help=s.get("why") or None)
                if s.get("why"):
                    st.caption(s["why"])
            if c2.button("Add", key=f"bpsa-{i}"):
                B.set_member(cat, uid, s["key"])
                st.session_state[f"bp-sugg-{uid}"] = [x for x in sugg if x["key"] != s["key"]]
                st.rerun()
            if c3.button("Not this", key=f"bpsr-{i}"):
                B.set_member(cat, uid, s["key"], "rejected")
                st.session_state[f"bp-sugg-{uid}"] = [x for x in sugg if x["key"] != s["key"]]
                st.rerun()


def projects_box(cat: Catalogue, key: str) -> None:
    """On an item's page: the business projects it belongs to, and a way to add it to one."""
    mine = B.projects_for(cat, key)
    projects = B.list_projects(cat)
    if not projects:
        return
    line = st.columns([4, 2])
    with line[0]:
        if mine:
            st.caption("Part of business project" + ("s" if len(mine) > 1 else "") + ":")
            for p in mine:
                st.button(f"🧭 {p['name']}", key=f"bpbox-{key}-{p['uid']}", on_click=go, args=(f"bp://{p['uid']}",),
                          type="tertiary")
    others = {p["uid"]: p["name"] for p in projects if p["uid"] not in {m["uid"] for m in mine}}
    if others:
        pick = line[1].selectbox("Add to business project", list(others), format_func=others.get, index=None,
                                 placeholder="Add to a business project…", key=f"bpadd-{key}",
                                 label_visibility="collapsed")
        if pick:
            B.set_member(cat, pick, key)
            st.session_state.pop(f"bpadd-{key}", None)
            st.rerun()
