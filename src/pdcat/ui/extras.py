"""Boxes that appear on an item's page when they have something to say: related knowledge,
company-wide keys, file series, change history, questions, features."""
from __future__ import annotations

import streamlit as st

from pdcat.store import Catalogue

from .common import first_line, go


def item_extras_top(cat: Catalogue, key: str, asset: dict) -> None:
    from pdcat.history import freshness
    from pdcat.knowledge import item_text, pin, triggered
    f = freshness(cat, key) if asset["kind"] in ("table", "view", "data_file", "file_series") else None
    if f:
        line = f"🕒 Newest {f['column']}: **{f['latest']}** ({f['age_days']} days old when measured {f['measured']})"
        if f["stale"]:
            st.warning(line + f". Expected within {f['limit_days']} days: is the load still running?")
        else:
            st.caption(line)
    hits = triggered(cat, item_text(cat, key), key)
    if hits:
        with st.container(border=True):
            st.markdown("**💡 Related knowledge**")
            for k in hits[:6]:
                c1, c2 = st.columns([5, 1])
                c1.button(f"💡 {k['title']}", key=f"kb-{key}-{k['uid']}", on_click=go, args=(f"kb://{k['uid']}",),
                          type="tertiary")
                c1.caption(first_line(k["body"], 300))
                if c2.button("Not here", key=f"kbx-{key}-{k['uid']}", help="Don't show this note on this item"):
                    pin(cat, k["uid"], key, exclude=True)
                    st.rerun()


def item_extras_bottom(cat: Catalogue, key: str, asset: dict) -> None:
    from pdcat.history import history
    from pdcat.keys import join_hints
    from pdcat.knowledge import list_questions
    from pdcat.relationships import joins_for, set_relationship_state
    hints = join_hints(cat, key)
    joins = joins_for(cat, key) if asset["kind"] in ("table", "view") else []
    if hints or joins:
        st.subheader("Joins to")
        for i, h in enumerate(hints):
            c1, c2 = st.columns([3, 3])
            with c1:
                st.button(f"🔑 {h['other_name'] or h['other']}", key=f"jh-{key}-{i}", on_click=go, args=(h["other"],),
                          type="tertiary")
            c2.caption(f"on {h['column']} = {h['other_column']} (company-wide key {h['key']})")
        for i, r in enumerate(joins):
            other, mine, theirs = (r["dst_key"], r["src_columns"], r["dst_columns"]) if r["src_key"] == key else \
                (r["src_key"], r["dst_columns"], r["src_columns"])
            c1, c2, c3 = st.columns([3, 3, 1.4])
            with c1:
                st.button(f"🗃️ {other.split('/')[-1]}", key=f"rel-{key}-{i}", on_click=go, args=(other,),
                          type="tertiary")
            label = {"declared": "foreign key", "confirmed": "confirmed", "suggested": "suggested"}[r["state"]] \
                if r["state"] in ("declared", "confirmed", "suggested") else r["state"]
            c2.caption(f"on {mine} = {theirs} · {label} · {r['evidence'] or ''}")
            if r["state"] == "suggested":
                with c3:
                    a, b = st.columns(2)
                    if a.button("✓", key=f"relok-{key}-{i}", help="Confirm this join"):
                        set_relationship_state(cat, r, "confirmed")
                        st.rerun()
                    if b.button("✗", key=f"relno-{key}-{i}", help="Not a real join; don't suggest it again"):
                        set_relationship_state(cat, r, "rejected")
                        st.rerun()
    changes = history(cat, key)
    if changes:
        with st.expander(f"Change history ({len(changes)})"):
            import pandas as pd
            st.dataframe(pd.DataFrame([{"when": c["at"][:16].replace("T", " "), "change": c["change"],
                                        "detail": c["detail"]} for c in changes]), hide_index=True, width="stretch")
    qs = list_questions(cat, table_key=key)
    if qs:
        st.subheader("Questions this answers")
        for q in qs:
            st.button(f"❓ {q['question']}", key=f"q-{key}-{q['uid']}", on_click=go, args=(f"q://{q['uid']}",),
                      type="tertiary")


def manual_link_form(cat: Catalogue, key: str) -> None:
    from pdcat.links import add_manual_link, code_items
    items = code_items(cat)
    if not items:
        st.caption("No scripts catalogued yet.")
        return
    labels = {i["key"]: f"{i['source']} / {i['name']}" for i in items}
    with st.form(f"manual-link-{key}"):
        src = st.selectbox("Script, notebook or SQL", list(labels), format_func=labels.get, index=None,
                           placeholder="Pick the one that creates or loads this…")
        relation = st.radio("It", ["writes", "reads"], horizontal=True,
                            format_func={"writes": "creates or loads this", "reads": "reads this"}.get)
        if st.form_submit_button("Add link", type="primary") and src:
            add_manual_link(cat, src, relation, key)
            st.toast("Linked. The link is kept through rescans.")
            st.rerun()
