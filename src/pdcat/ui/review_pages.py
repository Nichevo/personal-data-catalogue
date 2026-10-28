"""To review (a compact table by default, cards on request) and Issue hotspots."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat.export import rows_to_csv
from pdcat.review import (
    BY_DESIGN,
    CATEGORY_LABELS,
    RULES,
    ensure_fresh,
    hotspots,
    open_tasks,
    set_task_status,
    set_tasks_status,
)
from pdcat.store import NOTE_TYPES, Catalogue

from .common import asset_button, cached, go, page_title

CATEGORY_ICONS = {"docs": "📝", "change": "🔄", "warning": "⚠️"}
SORTS = {"Most important first": ("priority", False), "Most used first": ("used by", False),
         "By item": ("item", True), "By check": ("check", True)}


def review_page(cat: Catalogue) -> None:
    st.title("To review")
    st.caption("Things that need your eyes, most important first: what your code uses, big tables and tables "
               "with open issues come first. Tick rows to mark them done, snooze them for 30 days or dismiss "
               "them in one go. Fixed problems drop off by themselves.")
    ensure_fresh(cat)
    all_open = open_tasks(cat, limit=100_000)
    counts = {c: sum(t["category"] == c for t in all_open) for c in CATEGORY_LABELS}
    cols = st.columns(len(counts) + 1)
    cols[0].metric("Open", len(all_open))
    for col, (c, n) in zip(cols[1:], counts.items()):
        col.metric(CATEGORY_LABELS[c], n)

    f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 1.6, 1.2], vertical_alignment="bottom")
    category = f1.selectbox("Type", ["All", *CATEGORY_LABELS], format_func=lambda c: CATEGORY_LABELS.get(c, c))
    rule = f2.selectbox("Check", ["All", *RULES], format_func=lambda r: RULES[r][0] if r in RULES else r)
    sort = f3.selectbox("Sort", list(SORTS))
    only_used = f4.checkbox("Only items my code uses", help="Handy on a large database: start with what you rely on.")
    cards = f5.toggle("Cards", value=False, help="Show each item as a card with its full text and buttons.")
    tasks = open_tasks(cat, category=None if category == "All" else category,
                       rule=None if rule == "All" else rule, only_used=only_used, limit=100_000)
    if not tasks:
        st.success("Nothing to review here.")
        return
    st.download_button(f"⬇️ Download this list ({len(tasks)} items, CSV)",
                       rows_to_csv([{k: t[k] for k in ("title", "detail", "rule", "asset_key", "priority", "used_by")}
                                    for t in tasks]), "review-list.csv", "text/csv")
    if cards:
        card_list(cat, tasks)
    else:
        compact_table(cat, tasks, sort)


def compact_table(cat: Catalogue, tasks: list[dict], sort: str) -> None:
    rows = [{"": CATEGORY_ICONS.get(t["category"], "•"), "item": t["asset_name"] or t["asset_key"],
             "what": t["title"], "check": RULES.get(t["rule"], (t["rule"],))[0], "used by": t["used_by"] or 0,
             "detail": (t["detail"] or "")[:160], "priority": t["priority"], "id": t["id"]} for t in tasks]
    col, asc = SORTS[sort]
    df = pd.DataFrame(rows).sort_values(col, ascending=asc, kind="stable", ignore_index=True)
    by_id = {t["id"]: t for t in tasks}
    event = st.dataframe(df.drop(columns=["id", "priority"]), hide_index=True, width="stretch",
                         on_select="rerun", selection_mode="multi-row", key="review-table",
                         column_config={"": st.column_config.TextColumn(width="small"),
                                        "what": st.column_config.TextColumn(width="large"),
                                        "used by": st.column_config.NumberColumn(width="small")})
    picked = [df.loc[i, "id"] for i in (event.selection.rows if event else [])]
    c1, c2, c3, c4 = st.columns([1.2, 1.4, 1.2, 4])
    label = f" ({len(picked)})" if picked else ""

    def bulk(status: str) -> None:
        set_tasks_status(cat, picked, status)
        st.session_state.pop("review-table", None)

    c1.button(f"✓ Done{label}", disabled=not picked, on_click=bulk, args=("done",))
    c2.button(f"💤 Snooze 30 days{label}", disabled=not picked, on_click=bulk, args=("snoozed",))
    c3.button(f"Dismiss{label}", disabled=not picked, on_click=bulk, args=("dismissed",),
              help="Not a problem; don't show these again")
    c4.caption("Tick rows to act on them. Ticked rows open below with their full text.")
    for tid in picked[:10]:
        task_card(cat, by_id[tid])
    if len(picked) > 10:
        st.caption(f"…and {len(picked) - 10} more ticked.")


def card_list(cat: Catalogue, tasks: list[dict]) -> None:
    shown = st.session_state.get("review_shown", 30)
    for t in tasks[:shown]:
        task_card(cat, t)
    if len(tasks) > shown:
        if st.button(f"Show more ({len(tasks) - shown} left)"):
            st.session_state["review_shown"] = shown + 30
            st.rerun()


def task_card(cat: Catalogue, t: dict, show_link: bool = True) -> None:
    tid = t["id"]
    with st.container(border=True):
        label, category, _ = RULES.get(t["rule"], (t["rule"], "warning", 0))
        icon_ = CATEGORY_ICONS.get(category, "•")
        if show_link:
            st.button(f"{icon_} {t['title']}", key=f"open-{tid}", on_click=go, args=(t["asset_key"],), type="tertiary")
        else:
            st.markdown(f"{icon_} {t['title']}")
        meta = [label]
        if t.get("used_by"):
            meta.append(f"used by {t['used_by']} file(s)")
        if t.get("source"):
            meta.append(t["source"])
        st.caption(" · ".join(meta))
        if t["detail"]:
            st.write(t["detail"])
        c1, c2, c3, c4 = st.columns(4)
        with c1.popover("💬 Comment"):
            with st.form(f"comment-{tid}", clear_on_submit=True):
                note_type = st.radio("Type", list(NOTE_TYPES), format_func=NOTE_TYPES.get, horizontal=True,
                                     index=1 if category == "warning" else 0)
                body = st.text_area("Your comment", height=120,
                                    placeholder="What you found, what it means, what to watch for…")
                done = st.checkbox("Mark this task done", value=True)
                if st.form_submit_button("Save", type="primary") and body.strip():
                    cat.add_note(t["asset_key"], body, "review", note_type)
                    if done:
                        set_task_status(cat, tid, "done")
                    st.rerun()
        if c2.button("✓ Done", key=f"done-{tid}"):
            set_task_status(cat, tid, "done")
            st.rerun()
        if c3.button("💤 Snooze 30 days", key=f"snooze-{tid}"):
            set_task_status(cat, tid, "snoozed")
            st.rerun()
        if c4.button("By design" if t["rule"] in BY_DESIGN else "Dismiss", key=f"dismiss-{tid}",
                     help="Not a problem; don't show this again"):
            set_task_status(cat, tid, "dismissed")
            st.rerun()


@cached
def _hotspots(cat: Catalogue):
    return hotspots(cat)


def hotspots_page(cat: Catalogue) -> None:
    page_title("Issue hotspots")
    st.caption("Where problems cluster. Score = 5 × open data issues + 3 × automatic warnings (odd values, "
               "files that differ from their source, stale files) + 2 × disappeared + 1 × undocumented.")
    ensure_fresh(cat)
    areas, items = _hotspots(cat)
    if not len(areas):
        st.info("Nothing scanned yet.")
        return
    st.subheader("Areas")
    st.caption("Schemas for SQL tables; top-level folders for files and scripts.")
    st.dataframe(areas, hide_index=True, width="stretch")
    st.subheader("Worst items")
    if not len(items):
        st.success("No hotspots: no open issues or warnings.")
        return
    for i, r in items.head(30).iterrows():
        c1, c2 = st.columns([3, 2])
        with c1:
            asset_button(r["key"], r["name"], r["kind"], f"hot-{i}")
            st.caption(r["area"])
        bits = [f"score {r['score']}"]
        if r["open issues"]:
            bits.append(f"⚠️ {r['open issues']} open issue(s)")
        if r["warnings"]:
            bits.append(f"{r['warnings']} warning(s)")
        if r["missing"]:
            bits.append("disappeared")
        if r["undocumented"]:
            bits.append("undocumented")
        c2.caption(" · ".join(bits))
