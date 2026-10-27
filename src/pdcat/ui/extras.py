"""Boxes that appear on an item's page when they have something to say: related knowledge,
company-wide keys, file series, change history, questions, features."""
from __future__ import annotations

import streamlit as st

from pdcat.store import Catalogue

from .common import first_line, go


def item_extras_top(cat: Catalogue, key: str, asset: dict) -> None:
    from pdcat.knowledge import item_text, pin, triggered
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
    from pdcat.knowledge import list_questions
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
