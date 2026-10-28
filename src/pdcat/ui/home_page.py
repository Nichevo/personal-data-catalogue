"""The home page, which opens first: your follow-ups, five quick questions, what needs attention, and what
changed since your last visit."""
from __future__ import annotations

import streamlit as st

from pdcat import followups, quickq, suggestions
from pdcat.store import Catalogue

from .common import asset_button, go, go_page, icon

ATTENTION_RULES = ("rule_check", "not_refreshing", "sql_check", "stale_data", "layout_change")
MAX_LISTED = 8


def home_page(cat: Catalogue) -> None:
    st.title("Home")
    left, right = st.columns([1.1, 1])
    with left:
        follow_ups_box(cat)
        quick_questions_box(cat)
    with right:
        attention_box(cat)
        since_last_visit_box(cat)


def follow_ups_box(cat: Catalogue) -> None:
    rows = followups.follow_ups(cat)
    with st.container(border=True):
        st.markdown(f"**📌 Follow-ups** ({len(rows)})")
        if not rows:
            st.caption("Nothing pinned. Use “📌 Follow up” on any item page to keep it here until you're done.")
        for i, f in enumerate(rows):
            c1, c2 = st.columns([6, 1])
            with c1:
                asset_button(f["item"], f["name"], followups.kind_of(f["item"], f["kind"]), f"home-fu-{i}",
                             help=f"Pinned {f['created_at'][:10]}")
                if f["note"]:
                    st.caption(f["note"])
            if c2.button("Done", key=f"home-fu-done-{i}", type="tertiary", help="Take it off the list"):
                followups.unfollow(cat, f["item"])
                st.rerun()


def quick_questions_box(cat: Catalogue) -> None:
    questions = quickq.todays(cat)
    with st.container(border=True):
        st.markdown("**⚡ Five quick questions**")
        if not questions:
            st.caption("All answered for today. New ones come tomorrow.")
            return
        st.caption("The gaps worth filling most, each a minute or less.")
        for q in questions:
            st.markdown(q["title"])
            if q.get("detail"):
                st.caption(q["detail"])
            if q["type"] == "suggestion":
                c1, c2, c3, _ = st.columns([1, 1, 1.4, 2])
                if c1.button("Yes", key=f"qq-yes-{q['id']}", type="primary"):
                    suggestions.accept(cat, q["suggestion"])
                    st.rerun()
                if c2.button("No", key=f"qq-no-{q['id']}"):
                    suggestions.reject(cat, q["suggestion"])
                    st.rerun()
                if q["key"] and not q["key"].startswith(("rule:", "naming:")):
                    c3.button("Open it", key=f"qq-open-{q['id']}", on_click=go, args=(q["key"],), type="tertiary")
            else:
                with st.form(f"qq-form-{q['id']}", clear_on_submit=True, border=False):
                    c1, c2 = st.columns([5, 1])
                    text = c1.text_input("One line", key=f"qq-text-{q['id']}", label_visibility="collapsed",
                                         placeholder="What it holds, in a line")
                    if c2.form_submit_button("Save") and text.strip():
                        quickq.answer_describe(cat, q["key"], text)
                        st.rerun()
                st.button("Open it", key=f"qq-open-{q['id']}", on_click=go, args=(q["key"],), type="tertiary")


def attention_box(cat: Catalogue) -> None:
    from pdcat.review import RULES, ensure_fresh, open_tasks
    ensure_fresh(cat)
    tasks = [t for t in open_tasks(cat, limit=2000) if t["rule"] in ATTENTION_RULES]
    with st.container(border=True):
        st.markdown(f"**🚨 Needs attention** ({len(tasks)})")
        if not tasks:
            st.caption("No failing checks, and every output is still refreshing.")
        for i, t in enumerate(tasks[:MAX_LISTED]):
            label = RULES.get(t["rule"], (t["rule"],))[0]
            st.button(f"{icon(t['asset_kind'] or 'note')} {t['title']}", key=f"home-att-{i}", on_click=go,
                      args=(t["asset_key"],), type="tertiary", help=label)
        if len(tasks) > MAX_LISTED:
            st.button(f"All {len(tasks)} on To review", key="home-att-all", on_click=go_page, args=("To review",),
                      type="tertiary")


def since_last_visit_box(cat: Catalogue) -> None:
    from pdcat.history import recent_changes
    since = st.session_state.get("_last_visit")
    with st.container(border=True):
        st.markdown("**🆕 Since your last visit**")
        if not since:
            st.caption("This is your first visit. Next time this shows what changed in between.")
            return
        st.caption(f"Since {since[:16].replace('T', ' ')} UTC")
        changes = [c for c in recent_changes(cat, days=3650, limit=500) if c["at"] > since]
        notes = [dict(n) for n in cat.conn.execute("SELECT asset_key, body, updated_at FROM notes WHERE updated_at > ? "
                                                   "ORDER BY updated_at DESC LIMIT 50", (since,))]
        waiting = cat.conn.execute("SELECT COUNT(*) FROM suggestions WHERE state = 'open' AND created_at > ?",
                                   (since,)).fetchone()[0]
        if not (changes or notes or waiting):
            st.caption("Nothing new.")
            return
        if changes:
            st.markdown(f"{len(changes)} change(s) found by scans")
            for i, c in enumerate(changes[:MAX_LISTED]):
                st.button(f"{icon(c['kind'] or 'note')} {c['name'] or c['asset_key']}: {c['change']}",
                          key=f"home-ch-{i}", on_click=go, args=(c["asset_key"],), type="tertiary",
                          help=c["detail"] or None)
            if len(changes) > MAX_LISTED:
                st.button("All changes", key="home-ch-all", on_click=go_page, args=("Recent changes",),
                          type="tertiary")
        if notes:
            st.markdown(f"{len(notes)} note(s) added or changed")
            for i, n in enumerate(notes[:5]):
                st.button(f"📝 {n['body'][:80]}", key=f"home-n-{i}", on_click=go, args=(n["asset_key"].split("#")[0],),
                          type="tertiary")
        if waiting:
            st.button(f"{waiting} new suggestion(s)", key="home-sugg", on_click=go_page, args=("Suggestions",),
                      type="tertiary")
