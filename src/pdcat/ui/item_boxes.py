"""Version 2.1 boxes on item pages: follow up, not relevant / old, groups the item is in, suggestions
about it, and its edit history with undo."""
from __future__ import annotations

import streamlit as st

from pdcat import curation, edits, followups, groups, suggestions
from pdcat.store import Catalogue

from .common import go


def item_actions(cat: Catalogue, key: str, info: dict | None = None) -> None:
    """A row of small buttons under the title: follow up, not relevant, old."""
    info = info if info is not None else curation.item_info(cat, key)
    fol = followups.following(cat, key)
    c1, c2, c3, _ = st.columns([1.3, 1.3, 1.3, 4])
    with c1.popover("📌 Following" if fol else "📌 Follow up",
                    help="Pin this to the home page so you can get back to it"):
        note = st.text_input("Where you were (optional)", value=(fol or {}).get("note") or "", key=f"fu-note-{key}")
        if st.button("Save" if fol else "Follow up", key=f"fu-{key}", type="primary"):
            followups.follow(cat, key, note)
            st.rerun()
        if fol and st.button("Done: stop following", key=f"unfu-{key}"):
            followups.unfollow(cat, key)
            st.rerun()
    if key.startswith(("kb://", "q://", "metric://", "feature://", "key://", "bp://", "group://", "query://")):
        return
    rel = info.get("relevance") or ""
    if rel == "not relevant":
        if c2.button("↩️ Relevant again", key=f"rel-{key}"):
            curation.set_relevance(cat, key, "")
            st.rerun()
    elif c2.button("🙈 Not relevant", key=f"rel-{key}",
                   help="Hide from search, the review list, coverage, diagrams and exports. Scans keep it hidden."):
        curation.set_relevance(cat, key, "not relevant")
        st.rerun()
    if rel == "old":
        if c3.button("↩️ Not old", key=f"old-{key}"):
            curation.set_relevance(cat, key, "")
            st.rerun()
    elif rel != "not relevant" and c3.button("🗄️ Mark old", key=f"old-{key}",
                                             help="Kept with its notes, but hidden from the code map and diagrams"):
        curation.set_relevance(cat, key, "old")
        st.rerun()
    if rel == "not relevant":
        st.info("Marked **not relevant**: hidden from search, review, coverage, diagrams and exports.", icon="🙈")
    elif rel == "old":
        st.info("Marked **old**: hidden from the code map and diagrams unless you choose to show old items.", icon="🗄️")


def groups_box(cat: Catalogue, key: str) -> None:
    for g in groups.for_item(cat, key):
        if g is None:
            continue
        me = next((m for m in g["members"] if m["asset_key"] == key), {})
        others = [m for m in g["members"] if m["asset_key"] != key]
        if g["type"] == "versions":
            if g["main_key"] == key:
                text = f"🗂️ Current version of **{g['name']}**. {len(others)} older version(s) are hidden from the code map."
            elif g["main_key"]:
                text = "🗂️ An **older version** of a script; the current one is shown on the code map."
            else:
                text = f"🗂️ One of {len(g['members'])} versions of **{g['name']}**. Pick the current one on its page."
        elif g["type"] == "family":
            text = (f"👪 Part of the **{g['name']}** family" + (f" ({me['label']})" if me.get("label") else "")
                    + ": " + ", ".join(f"{m['name']} ({m['label'] or '?'})" for m in others[:5]))
        else:
            role = "the **main** table" if g["main_key"] == key else "the **backup**" if g["main_key"] else "one of"
            text = f"🪞 This is {role} of the pair **{g['name']}**"
        c1, c2 = st.columns([5, 1])
        c1.markdown(text)
        c2.button("Open", key=f"grp-{key}-{g['uid']}", on_click=go, args=(f"group://{g['uid']}",))


def suggestions_box(cat: Catalogue, key: str) -> None:
    found = suggestions.open_suggestions(cat, subject=key)
    if not found:
        return
    with st.container(border=True):
        st.markdown(f"**💡 Suggestions ({len(found)})**")
        for s in found[:8]:
            suggestion_row(cat, s, f"isug-{key}")


def suggestion_row(cat: Catalogue, s: dict, prefix: str) -> None:
    c1, c2, c3 = st.columns([6, 1, 1])
    c1.markdown(s["title"])
    if s.get("detail"):
        c1.caption(s["detail"])
    if c2.button("✓", key=f"{prefix}-ok-{s['id']}", help="Accept"):
        suggestions.accept(cat, s["id"])
        st.rerun()
    if c3.button("✗", key=f"{prefix}-no-{s['id']}", help="Reject: it won't be suggested again"):
        suggestions.reject(cat, s["id"])
        st.rerun()


def history_box(cat: Catalogue, key: str) -> None:
    rows = edits.history(cat, key)
    if not rows:
        return
    with st.expander(f"🕘 Edit history ({len(rows)})"):
        for e in rows[:50]:
            c1, c2 = st.columns([6, 1])
            when = e["at"][:16].replace("T", " ")
            what = e["what"].replace("item_info.", "")
            old = (e["old_value"] or "∅")[:120]
            new = (e["new_value"] or "∅")[:120]
            c1.caption(f"{when} UTC · {e['author'] or ''} · **{what}**: {old} → {new}"
                       + (" · undone" if e["undone"] else ""))
            if not e["undone"] and c2.button("Undo", key=f"undo-{e['id']}"):
                st.toast(edits.undo(cat, e["id"]))
                st.rerun()
