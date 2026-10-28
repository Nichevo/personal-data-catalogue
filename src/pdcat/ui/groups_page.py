"""A group's page (a file family, script versions, a main table and its backup), comparing its members
side by side; and the Suggestions page where everything the catalogue suggests waits for a yes or no."""
from __future__ import annotations

import difflib
from pathlib import Path

import pandas as pd
import streamlit as st

from pdcat import groups, suggestions
from pdcat.store import Catalogue

from .common import asset_button, go, kind_label, size_label

ICONS = {"family": "👪", "versions": "🗂️", "backup": "🪞"}


def group_page(cat: Catalogue, key: str) -> None:
    g = groups.get(cat, key.removeprefix("group://"))
    if g is None:
        st.warning("This group no longer exists.")
        return
    st.title(f"{ICONS[g['type']]} {g['name']}")
    st.caption(f"{groups.TYPES[g['type']]} · {len(g['members'])} items · `{key}`")
    from .item_boxes import item_actions
    item_actions(cat, key)
    if g["type"] == "versions":
        st.caption("Only the current version shows on the code map, in search and in lineage. The others stay "
                   "listed here with their notes.")
    elif g["type"] == "family":
        st.caption("The same data at different grains. Each file stays its own item; the family brings them together.")
    else:
        st.caption("A main table and its copy elsewhere. Code that reads both reads one table and its history.")
    st.dataframe(compare(cat, g), hide_index=True, width="stretch")
    for i, m in enumerate(g["members"]):
        c1, c2, c3, c4 = st.columns([4, 2, 1.5, 1.5])
        with c1:
            asset_button(m["asset_key"], m["name"] or m["asset_key"], m["kind"] or "table", f"gm-{i}")
        label = c2.text_input("Label", value=m["label"] or "", key=f"gml-{g['uid']}-{i}", label_visibility="collapsed",
                              placeholder="daily, v3, backup…")
        if label != (m["label"] or ""):
            groups.set_label(cat, g["uid"], m["asset_key"], label)
            st.rerun()
        main_word = {"versions": "current", "backup": "main", "family": "main"}[g["type"]]
        if g["main_key"] == m["asset_key"]:
            c3.markdown(f"⭐ **{main_word}**")
        elif c3.button(f"Make {main_word}", key=f"gmm-{i}"):
            groups.set_main(cat, g["uid"], m["asset_key"])
            st.rerun()
        if c4.button("Take out", key=f"gmx-{i}", help="It isn't part of this group; it won't be suggested again"):
            groups.remove_member(cat, g["uid"], m["asset_key"])
            st.rerun()
    if g["type"] == "versions" and len(g["members"]) >= 2:
        what_changed(g)
    if st.button("Break up this group", help="Every item stays; only the grouping goes"):
        groups.delete(cat, g["uid"])
        go(None)
        st.rerun()


def compare(cat: Catalogue, g: dict) -> pd.DataFrame:
    """One row per member: where it lives, rows, date range, columns, who writes and reads it."""
    all_cols = {m["asset_key"]: {c["name"].lower() for c in cat.columns(m["asset_key"])} for m in g["members"]}
    shared = set.intersection(*all_cols.values()) if all_cols else set()
    rows = []
    for m in g["members"]:
        a = m["attrs"]
        key = m["asset_key"]
        prof = cat.profile(key)
        rng = _date_range(prof)
        writers = cat.conn.execute("SELECT COUNT(DISTINCT src_key) FROM links WHERE dst_key = ? AND relation = 'writes'",
                                   (key,)).fetchone()[0]
        readers = cat.conn.execute("SELECT COUNT(DISTINCT src_key) FROM links WHERE dst_key = ? AND relation != 'writes'",
                                   (key,)).fetchone()[0]
        only = sorted(all_cols[key] - shared)
        rows.append({
            "item": m["name"], "label": m["label"] or "", "main": "⭐" if g["main_key"] == key else "",
            "kind": kind_label(m["kind"] or ""), "where": a.get("server") or a.get("path") or m["source"] or "",
            "rows": a.get("row_count") if a.get("row_count") is not None else (prof[0]["row_count"] if prof else None),
            "date range": rng, "grain": a.get("grain_hint") or "",
            "modified": str(a.get("modified") or a.get("mtime_iso") or "")[:16].replace("T", " "),
            "size": size_label(a["size_bytes"]) if a.get("size_bytes") is not None else "",
            "columns": len(all_cols[key]), "only here": ", ".join(only[:8]) + (" …" if len(only) > 8 else ""),
            "written by": writers, "read by": readers, "status": m["status"] or "",
        })
    return pd.DataFrame(rows)


def _date_range(profile) -> str:
    for p in profile:
        t = (p["data_type"] or "").lower()
        if ("date" in t or "time" in t) and p["min_value"] and p["max_value"]:
            return f"{p['min_value'][:10]} → {p['max_value'][:10]}"
    return ""


def what_changed(g: dict) -> None:
    with st.expander("What changed between two versions"):
        names = {m["asset_key"]: m["name"] for m in g["members"]}
        keys = list(names)
        c1, c2 = st.columns(2)
        a = c1.selectbox("Older", keys, format_func=names.get, index=len(keys) - 1, key=f"vd-a-{g['uid']}")
        b = c2.selectbox("Newer", keys, format_func=names.get, index=0, key=f"vd-b-{g['uid']}")
        texts = []
        for k in (a, b):
            m = next(x for x in g["members"] if x["asset_key"] == k)
            path = m["attrs"].get("full_path")
            try:
                texts.append(Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if path else None)
            except OSError:
                texts.append(None)
        if None in texts:
            st.caption("The files can't be read from here (they may be on a drive this machine can't see). "
                       "Their functions are compared instead.")
            fa = set(next(x for x in g["members"] if x["asset_key"] == a)["attrs"].get("functions", []))
            fb = set(next(x for x in g["members"] if x["asset_key"] == b)["attrs"].get("functions", []))
            st.markdown(f"**Added:** {', '.join(sorted(fb - fa)) or 'none'}  \n**Removed:** {', '.join(sorted(fa - fb)) or 'none'}")
            return
        diff = list(difflib.unified_diff(texts[0], texts[1], names[a], names[b], lineterm="", n=2))
        st.code("\n".join(diff[:2000]) or "No differences.", language="diff")


# ---- the suggestions inbox -------------------------------------------------------

def suggestions_page(cat: Catalogue) -> None:
    st.title("Suggestions")
    st.caption("What the catalogue worked out and wants you to confirm. Accept to apply it, reject so it isn't "
               "suggested again. Nothing is changed until you accept.")
    counts = suggestions.counts(cat)
    if not counts:
        st.info("No suggestions waiting. New ones appear after scans.")
        return
    kinds = sorted(counts, key=lambda k: -counts[k])
    tabs = st.tabs([f"{suggestions.KIND_LABELS.get(k, k)} ({counts[k]})" for k in kinds])
    for kind, tab in zip(kinds, tabs):
        with tab:
            rows = suggestions.open_suggestions(cat, kind, limit=300)
            c1, c2, _ = st.columns([1.4, 1.4, 4])
            picked = []
            for s in rows:
                cc1, cc2 = st.columns([0.4, 8])
                if cc1.checkbox("pick", key=f"sp-{s['id']}", label_visibility="collapsed"):
                    picked.append(s["id"])
                with cc2:
                    st.markdown(s["title"])
                    if s.get("detail"):
                        st.caption(s["detail"])
                    if s["subject"] and not s["subject"].startswith(("rule:", "naming:")):
                        st.button("Open the item", key=f"spo-{s['id']}", on_click=go, args=(s["subject"],),
                                  type="tertiary")
            if c1.button(f"Accept {len(picked) or 'ticked'}", key=f"sacc-{kind}", disabled=not picked, type="primary"):
                for sid in picked:
                    suggestions.accept(cat, sid)
                st.rerun()
            if c2.button(f"Reject {len(picked) or 'ticked'}", key=f"srej-{kind}", disabled=not picked):
                for sid in picked:
                    suggestions.reject(cat, sid)
                st.rerun()
    with st.expander("Decided suggestions"):
        done = [dict(r) for r in cat.conn.execute(
            "SELECT id, kind, title, state, decided_by, updated_at FROM suggestions WHERE state IN ('accepted', 'rejected') "
            "ORDER BY updated_at DESC LIMIT 200")]
        for d in done:
            c1, c2 = st.columns([6, 1])
            c1.caption(f"{'✓' if d['state'] == 'accepted' else '✗'} {d['title']} · {d['decided_by'] or ''} · "
                       f"{(d['updated_at'] or '')[:10]}")
            if d["state"] == "rejected" and c2.button("Ask again", key=f"sre-{d['id']}"):
                suggestions.reopen(cat, d["id"])
                st.rerun()


def groups_list_page(cat: Catalogue) -> None:
    st.subheader("Groups")
    rows = groups.all_groups(cat)
    if not rows:
        st.caption("No groups yet. Families, script versions and backup pairs appear here once you accept them.")
        return
    for r in rows:
        st.button(f"{ICONS[r['type']]} {r['name']} ({r['members']} items)", key=f"gl-{r['uid']}", on_click=go,
                  args=(f"group://{r['uid']}",), type="tertiary")
