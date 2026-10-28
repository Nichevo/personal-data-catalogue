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


def called_by_box(cat: Catalogue, key: str) -> None:
    """Every other script that uses this one: imports it (and which of its functions) or runs it."""
    from pdcat.codemap import called_by
    rows = called_by(cat, key)
    if not rows:
        return
    st.subheader(f"Called by ({len(rows)})")
    from .common import asset_button
    for i, r in enumerate(rows):
        c1, c2 = st.columns([3, 4])
        with c1:
            asset_button(r["src_key"], r["src_name"], r["src_kind"], f"calledby-{i}")
        if r["relation"] == "imports":
            c2.caption(f"imports it · uses {r['evidence']}" if r["evidence"] else "imports it")
        else:
            c2.caption(f"runs it · {r['how'] or ''} · {r['evidence'] or ''}")


def unknown_files_box(cat: Catalogue, key: str) -> None:
    from pdcat.unknownfiles import answer, unknown_files
    rows = unknown_files(cat, key)
    if not rows:
        return
    files = [dict(r) for r in cat.conn.execute(
        "SELECT key, name, source, kind FROM assets WHERE kind IN ('data_file', 'file_series', 'workbook', 'report', "
        "'model', 'access_db') AND status = 'active' ORDER BY source, name")]
    labels = {f["key"]: f"{f['source']} / {f['name']}" for f in files}
    st.subheader(f"❔ Unknown files ({len(rows)})")
    st.caption("Files this code reads or writes whose path the scan couldn't work out. Say which file each one "
               "is and it's linked like any other, kept through rescans.")
    for u in rows:
        with st.container(border=True):
            where = f"line {u['line']}" + (f", cell {u['cell']}" if u.get("cell") else "") + \
                (f", in {u['function']}()" if u.get("function") else "")
            st.markdown(f"**{u['relation']}** · {where}")
            st.code(u["code"], language="python")
            c1, c2, c3 = st.columns([4, 1, 1.4])
            pick = c1.selectbox("Which file is it?", list(labels), format_func=labels.get, index=None,
                                key=f"uf-{u['id']}", placeholder="Pick the file…", label_visibility="collapsed")
            if c2.button("Link", key=f"ufl-{u['id']}", disabled=not pick, type="primary"):
                answer(cat, u, pick)
                st.rerun()
            if c3.button("Not a data file", key=f"ufx-{u['id']}"):
                answer(cat, u, None)
                st.rerun()


def backup_link_box(cat: Catalogue, key: str, asset: dict) -> None:
    """Link a table with its copy on another server, by hand."""
    if any(g and g["type"] == "backup" for g in groups.for_item(cat, key)):
        return
    obj = (asset["attrs"].get("object") or "").lower()
    others = [dict(r) for r in cat.conn.execute(
        "SELECT key, name, source FROM assets WHERE kind = 'table' AND status = 'active' AND key != ? AND source != ? "
        "AND lower(json_extract(attrs, '$.object')) = ? ORDER BY source", (key, asset["source"], obj))] if obj else []
    if not others:
        return
    with st.popover("🪞 Link its backup copy"):
        st.caption("The same table on another server. Mark which one is the main one.")
        pick = st.selectbox("Copy", [o["key"] for o in others],
                            format_func=lambda k: next(f"{o['name']} on {o['source']}" for o in others if o["key"] == k),
                            key=f"bk-pick-{key}")
        main = st.radio("Main one", ["this one", "the copy"], horizontal=True, key=f"bk-main-{key}")
        if st.button("Link them", key=f"bk-link-{key}", type="primary"):
            from pdcat.backups import pair_with
            uid = pair_with(cat, key, pick) if main == "this one" else pair_with(cat, pick, key)
            go(f"group://{uid}")
            st.rerun()


def rule_checks_box(cat: Catalogue, key: str) -> None:
    """Rules written in plain words, checked against the latest profile."""
    from pdcat import rulechecks
    results = rulechecks.results_for(cat, key)
    icons = {"pass": "✅", "fail": "❌", "unknown": "❔"}
    failed = sum(r["result"]["state"] == "fail" for r in results)
    label = f"✅ Rule checks ({len(results)})" + (f" · {failed} failing" if failed else "")
    with st.expander(label, expanded=bool(failed)):
        st.caption("Write a rule in plain words and it's checked each time this is profiled, for example "
                   "“Units is never negative”, “OrderId is unique”, “Status is one of Open, Closed”, "
                   "“loaded daily by 7am”. Failures go on the To review list.")
        for r in results:
            c1, c2, c3 = st.columns([0.4, 7, 1])
            c1.markdown(icons[r["result"]["state"]])
            c2.markdown(f"**{r['text']}**  \n{r['result']['detail']}")
            c2.caption(f"Checks: {rulechecks.describe(r['rule'])}"
                       + (f" · from {r['source']}" if r.get("source") else ""))
            if c3.button("Remove", key=f"rc-del-{r['uid']}", type="tertiary"):
                rulechecks.delete_check(cat, r["uid"])
                st.rerun()
        with st.form(f"rc-add-{key}", clear_on_submit=True):
            text = st.text_input("New rule", key=f"rc-text-{key}", placeholder="Units is never negative")
            if st.form_submit_button("Add the check") and text.strip():
                columns = rulechecks._columns(cat, key)
                rule = rulechecks.parse_rule(text, columns)
                rulechecks.add_check(cat, key, text)
                if rule["check"] == "text":
                    st.warning("Saved, but it couldn't be read as a check. Try rewording it.")
                st.rerun()
        waiting = suggestions.open_suggestions(cat, "check", subject=key)
        if waiting:
            st.caption("Rules found in the notes:")
            for s in waiting:
                c1, c2, c3 = st.columns([6, 1, 1])
                c1.markdown(f"“{s['payload']['text']}”  \n{s['detail']}")
                if c2.button("Add", key=f"rc-acc-{s['id']}"):
                    suggestions.accept(cat, s["id"])
                    st.rerun()
                if c3.button("No", key=f"rc-rej-{s['id']}", type="tertiary"):
                    suggestions.reject(cat, s["id"])
                    st.rerun()


def contains_table(cat: Catalogue, key: str, children: list) -> None:
    """A folder's contents as a sortable table with a filter box: series and script outputs as one line each."""
    import pandas as pd

    from pdcat.contents import contents_rows
    rows = contents_rows(cat, key, [dict(c) for c in children])
    find = st.text_input("Filter", key=f"contains-filter-{key}", placeholder="Filter by name, subfolder or type",
                         label_visibility="collapsed")
    if find:
        low = find.lower()
        rows = [r for r in rows if low in f"{r['name']} {r['subfolder']} {r['type']} {r['made by']}".lower()]
    st.caption(f"{len(rows)} line(s). Dated copies of the same data and files one script writes show as one line. "
               "Click a heading to sort, or a row to open it.")
    if not rows:
        return
    df = pd.DataFrame([{"subfolder": r["subfolder"] or "·", "name": r["name"], "type": r["type"], "detail": r["detail"],
                        "rows": r["rows"], "changed": r["changed"]} for r in rows])
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=f"contains-{key}", column_config={"rows": st.column_config.NumberColumn(format="%d")})
    sel = event.selection.rows if event and hasattr(event, "selection") else []
    if sel:
        go(rows[sel[0]]["key"])
        st.rerun()
