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
    from pdcat.review import counts_by_rule, done_recently
    st.title("To review")
    st.caption("Things that need your eyes, most important first: what your code uses, big tables and tables "
               "with open issues come first. Since 2.3 data problems come as one item per table, and an answer "
               "can stick for the pattern everywhere. Fixed problems drop off by themselves.")
    ensure_fresh(cat)
    all_open = open_tasks(cat, limit=100_000)
    counts = {c: sum(t["category"] == c for t in all_open) for c in CATEGORY_LABELS}
    cols = st.columns(len(counts) + 2)
    cols[0].metric("Open", len(all_open))
    for col, (c, n) in zip(cols[1:], counts.items()):
        col.metric(CATEGORY_LABELS[c], n)
    cols[-1].metric("Done this week", done_recently(cat))
    by_rule = counts_by_rule(cat)
    if by_rule:
        with st.expander(f"By check ({len(by_rule)} checks with open items)", expanded=len(all_open) > 50):
            st.caption("Pick a row to see just that check. Settings › Review switches checks off.")
            df = pd.DataFrame([{"check": r["label"], "type": r["category"], "open": r["open"]} for r in by_rule])
            event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                                 key="review-by-rule")
            sel = event.selection.rows if event else []
            if sel:
                st.session_state["review-rule"] = by_rule[sel[0]]["rule"]
                st.session_state["review-rule-box"] = by_rule[sel[0]]["rule"]  # the selectbox below reads this
                st.session_state.pop("review-by-rule", None)
                st.rerun()
    top = [t for t in all_open if t["category"] != "docs"][:5] or all_open[:5]
    if top and len(all_open) > 20:
        with st.expander("Start here: the five most important", expanded=False):
            for t in top:
                task_card(cat, t)

    f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 1.6, 1.2], vertical_alignment="bottom")
    category = f1.selectbox("Type", ["All", *CATEGORY_LABELS], format_func=lambda c: CATEGORY_LABELS.get(c, c))
    rules = ["All", *RULES]
    picked_rule = st.session_state.get("review-rule", "All")
    if st.session_state.get("review-rule-box") not in rules:
        st.session_state["review-rule-box"] = picked_rule if picked_rule in rules else "All"
    rule = f2.selectbox("Check", rules, format_func=lambda r: RULES[r][0] if r in RULES else r, key="review-rule-box")
    st.session_state["review-rule"] = rule
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
    c0, c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.4, 1.2, 3])
    label = f" ({len(picked)})" if picked else ""

    def bulk(status: str) -> None:
        set_tasks_status(cat, picked, status)
        st.session_state.pop("review-table", None)

    # Clicking a cell only selects it, so the way to an item is: tick its row, then Open (or the title below).
    one = by_id[picked[0]] if len(picked) == 1 else None
    c0.button("↗ Open", disabled=one is None, on_click=go, args=(one["asset_key"] if one else None,), type="primary",
              help="Open the ticked item's page" if one else "Tick one row to open its item")
    c1.button(f"✓ Done{label}", disabled=not picked, on_click=bulk, args=("done",))
    c2.button(f"💤 Snooze 30 days{label}", disabled=not picked, on_click=bulk, args=("snoozed",))
    c3.button(f"Dismiss{label}", disabled=not picked, on_click=bulk, args=("dismissed",),
              help="Not a problem; don't show these again")
    c4.caption("Tick a row (the box at its left) to open its item or act on it. Ticked rows show below in full.")
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
        opens = not t["asset_key"].startswith(("sqlrule://", "table:(no database)")) and \
            not (t["rule"] == "unmatched" and t["id"] == f"unmatched:{t['asset_key'][6:]}")  # a server or database group
        if show_link and opens:
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
        answered = rule_actions(cat, t)
        if answered:
            return
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


def rule_actions(cat: Catalogue, t: dict) -> bool:
    """The answers that fit this kind of item (and stick for the pattern where they can). Returns True when an
    answer was given and the page is about to redraw."""
    from pdcat import reviewanswers as RA
    tid, key, rule = t["id"], t["asset_key"], t["rule"]
    if rule == "data_type":
        return _data_type_actions(cat, t)
    if rule == "sensitive":
        return _sensitive_actions(cat, t)
    if rule == "odd_values":
        return _odd_value_actions(cat, t)
    if rule in ("columns", "undocumented"):
        return _description_actions(cat, t)
    if rule == "unmatched" and key != "table:(no database)" and tid == f"unmatched:{key[6:]}":
        prefix = key[6:]
        if st.button(f"{prefix} isn't catalogued on purpose: don't ask again", key=f"ign-{tid}"):
            RA.ignore_prefix(cat, prefix)
            set_task_status(cat, tid, "dismissed")
            st.rerun()
            return True
        st.caption("Or scan that server or database (➕ Add → Sources) and this clears itself.")
    if rule == "sql_check" and key.startswith("sqlrule://"):
        from pdcat.sqlchecks import RULES as SQL_RULES
        from pdcat.sqlchecks import accept_rule_everywhere, findings_by_rule
        srule = key[len("sqlrule://"):]
        g = findings_by_rule(cat).get(srule)
        if g:
            with st.expander(f"The {len(g['queries'])} queries"):
                for i, q in enumerate(g["queries"][:40]):
                    c1, c2 = st.columns([3, 2])
                    with c1:
                        asset_button(f"query://{q['uid']}", q["name"], "sql_query", f"sqc-{tid}-{i}")
                    c2.caption(q["where"] + (": " + ", ".join(a for a in q["abouts"] if a) if any(q["abouts"]) else ""))
        if st.button(f"Accept “{SQL_RULES[srule][1]}” everywhere: it's fine in our SQL", key=f"sqlok-{tid}"):
            n = accept_rule_everywhere(cat, srule)
            set_task_status(cat, tid, "dismissed")
            st.toast(f"Marked fine in {n} place(s)")
            st.rerun()
            return True
    return False


def _data_type_actions(cat: Catalogue, t: dict) -> bool:
    from pdcat import reviewanswers as RA
    from pdcat.profiling import HINT_TEXT
    from pdcat.review import type_hints_for
    tid, key = t["id"], t["asset_key"]
    found = type_hints_for([dict(p) for p in cat.profile(key)], key, RA.hints_ok(cat), RA.hints_dismissed(cat))
    if not found:
        return False
    st.dataframe(pd.DataFrame([{"column": h["column"], "declared": h["declared"] or "", "looks like": h["text"]}
                               for h in found]), hide_index=True, width="stretch")
    c1, c2 = st.columns(2)
    with c1:
        codes = sorted({h["code"] for h in found})
        code = st.selectbox("This pattern is our standard everywhere", codes, format_func=lambda c: HINT_TEXT.get(c, c),
                            key=f"dtcode-{tid}", help="For example dates kept as YYYYMMDD whole numbers. Never raised "
                            "again, in any table. Settings › Review lists these.")
        if st.button("Our standard: never raise it again", key=f"dtstd-{tid}"):
            RA.mark_hint_standard(cat, code)
            st.rerun()
            return True
    with c2:
        picked = st.multiselect("By design for these columns", [h["column"] for h in found],
                                default=[h["column"] for h in found], key=f"dtcols-{tid}")
        if st.button("By design for the ticked columns", key=f"dtcol-{tid}"):
            RA.dismiss_hints(cat, key, [(h["column"], h["code"]) for h in found if h["column"] in picked])
            if len(picked) == len(found):
                set_task_status(cat, tid, "dismissed")
            st.rerun()
            return True
    return False


def _sensitive_actions(cat: Catalogue, t: dict) -> bool:
    from pdcat import reviewanswers as RA
    from pdcat.curation import sensitive_suggestions
    tid, key = t["id"], t["asset_key"]
    cols = [c["name"] for c in sensitive_suggestions(cat) if c["asset_key"] == key]
    if not cols:
        return False
    usually = RA.names_usually(cat, "yes")
    picked = st.multiselect("Columns", cols, default=[c for c in cols if c.lower() in usually] or cols,
                            key=f"sens-{tid}", help="Sensitive columns keep no values: nothing is stored, shown or "
                            "exported for them.")
    c1, c2, c3 = st.columns(3)
    if c1.button("Mark ticked sensitive", key=f"sensyes-{tid}", type="primary", disabled=not picked):
        RA.answer_sensitive(cat, key, picked, sensitive=True)
        RA.answer_sensitive(cat, key, [c for c in cols if c not in picked], sensitive=False)
        set_task_status(cat, tid, "done")
        st.rerun()
        return True
    if c2.button("Whole table is sensitive", key=f"senstab-{tid}",
                 help="Every value in this table is purged and never exported"):
        RA.answer_table_sensitive(cat, key, cols, sensitive=True)
        set_task_status(cat, tid, "done")
        st.rerun()
        return True
    if c3.button("None of these are sensitive", key=f"sensno-{tid}"):
        RA.answer_table_sensitive(cat, key, cols, sensitive=False)
        set_task_status(cat, tid, "dismissed")
        st.rerun()
        return True
    st.caption(f"A column name answered the same way {RA.NAME_ANSWERS_NEEDED} times becomes the default for that name "
               "everywhere.")
    return False


def _odd_value_actions(cat: Catalogue, t: dict) -> bool:
    from pdcat import reviewanswers as RA
    tid = t["id"]
    import re
    values = sorted({m.group(1) for m in re.finditer(r"has only one value \(([^)]*)\)", t.get("detail") or "")})
    if not values:
        return False
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    v = c1.selectbox("A value that means “nothing” in our data", values, key=f"ph-{tid}")
    if c2.button("It's a placeholder everywhere", key=f"phok-{tid}",
                 help="A column that holds only this value isn't odd, in any table. Settings › Review lists them."):
        RA.add_placeholder(cat, v)
        st.rerun()
        return True
    return False


def _description_actions(cat: Catalogue, t: dict) -> bool:
    """Suggested descriptions for the table and its columns, accepted here in one go."""
    from pdcat import describedrafts as DD
    tid, key = t["id"], t["asset_key"]
    if t["rule"] == "undocumented":
        sug = DD.table_suggestion(cat, key)
        if not sug:
            return False
        text = st.text_input("Suggested purpose", value=sug["text"], key=f"tsug-{tid}", help=sug["source"])
        grain = st.text_input("One row per", value=sug["grain"] or "", key=f"tgrain-{tid}") if sug["grain"] else ""
        if st.button("Accept", key=f"tacc-{tid}", type="primary"):
            DD.accept_table(cat, key, text, grain or None)
            set_task_status(cat, tid, "done")
            st.rerun()
            return True
        return False
    return suggestions_editor(cat, key, tid)


def suggestions_editor(cat: Catalogue, key: str, widget: str, rows: list[dict] | None = None) -> bool:
    """A table of suggested column descriptions with tick boxes; Accept writes the ticked ones (and, if asked,
    every other undescribed column with the same name). Returns True after accepting."""
    from pdcat import describedrafts as DD
    rows = DD.column_suggestions(cat, key) if rows is None else rows
    if not rows:
        return False
    st.caption(f"Suggested descriptions for {len(rows)} column(s). Edit the text, untick what's wrong, then Accept.")
    df = pd.DataFrame([{"accept": True, "column": r["column"], "description": r["text"], "from": r["source"]}
                       for r in rows])
    edited = st.data_editor(df, hide_index=True, width="stretch", key=f"sugged-{widget}",
                            column_config={"accept": st.column_config.CheckboxColumn(width="small"),
                                           "column": st.column_config.TextColumn(disabled=True),
                                           "from": st.column_config.TextColumn(disabled=True),
                                           "description": st.column_config.TextColumn(width="large")},
                            disabled=["column", "from"])
    c1, c2 = st.columns([1, 2], vertical_alignment="center")
    everywhere = c2.checkbox("Also apply each to every other undescribed column with the same name",
                             key=f"sugall-{widget}")
    if c1.button("Accept ticked", key=f"sugacc-{widget}", type="primary"):
        picks = {str(r["column"]): str(r["description"]) for _, r in edited.iterrows() if r["accept"]}
        n = DD.accept_columns(cat, key, picks, everywhere=everywhere)
        st.toast(f"Described {n} column(s)")
        st.rerun()
        return True
    return False


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
