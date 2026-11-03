"""Deciding many suggestions at once: sort into likely / check / unlikely, organise into groups (by column
name, folder, first word...), merge repeats, filter, tick the few that are wrong, and accept all the others."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from typing import Any

import streamlit as st

from pdcat.triage import BAND_LABELS, BANDS

PAGE = 50          # rows shown at a time
GROUP_PAGE = 25    # groups shown at a time
ORGANISE_FROM = 30  # fewer rows than this: just the list
GROUPS_FIRST = 200  # more rows than this: start on the groups, not the list
LIST = "List"
OTHER = "Other"


def _tick_key(prefix: str, rid: str) -> str:
    return f"{prefix}-t-{rid}"


def _members(r: dict) -> list[dict]:
    return r.get("members") or [r]


def merge_repeats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows sharing a "same" signature as one row with "members" (the first row's fields, best band first)."""
    seen: dict[str, dict] = {}
    out = []
    for r in rows:
        sig = r.get("same")
        if not sig:
            out.append(r)
            continue
        m = seen.get(sig)
        if m is None:
            m = seen[sig] = {**r, "members": [], "id": f"same:{sig}"}
            out.append(m)
        m["members"].append(r)
    for m in seen.values():
        if len(m["members"]) == 1:
            out[out.index(m)] = m["members"][0]
    return out


def bulk_decide(rows: list[dict[str, Any]], *, prefix: str, row_id: Callable[[dict], str],
                text: Callable[[dict], str], render: Callable[[dict], None],
                accept: Callable[[dict], None], reject: Callable[[dict], None] | None,
                accept_word: str = "Accept", reject_word: str = "Reject", noun: str = "suggestion",
                views: Sequence[str] = ()) -> None:
    """A list with band and "Organise by" pickers, a filter box and a tick box per row, and buttons for many rows.

    Rows may carry "band" (likely / check / unlikely), "why" and "group" (see pdcat.triage), and "by" (a group
    for each of `views`) and "same" (repeats share it) from pdcat.organise. With views, a long list opens on its
    groups: each can be opened, or accepted or rejected whole. "Accept all except ticked" is the fast path inside
    a group or list: tick the few that are wrong and accept every other row that matches, including rows further
    down not yet shown.
    """
    if not rows:
        st.caption("Nothing waiting.")
        return
    bands = Counter(r.get("band") for r in rows if r.get("band"))
    c_band, c_view = st.container(), st.container()
    if len(bands) > 1:
        options = ["all"] + [b for b in BANDS if bands.get(b)]
        band = c_band.segmented_control(
            "Show", options, key=f"{prefix}-band", default="all",
            format_func=lambda b: f"All ({len(rows)})" if b == "all" else f"{BAND_LABELS[b]} ({bands[b]})")
        if band and band != "all":
            rows = [r for r in rows if r.get("band") == band]
    view = None
    if views and len(rows) >= ORGANISE_FROM and any(r.get("by") for r in rows):
        view = c_view.segmented_control(
            "Organise by", [LIST, *views], key=f"{prefix}-view", default=views[0] if len(rows) > GROUPS_FIRST else LIST,
            help="Put the suggestions into groups (one line each) to look through or decide a group at a time.")
        view = None if view in (None, LIST) else view
    c_group, c_find = st.columns([1, 2])
    if not view:
        groups = Counter(r.get("group") for r in rows if r.get("group"))
        if len(groups) > 1 and not views:
            group = c_group.selectbox("Group", [None, *[g for g, _ in groups.most_common()]], key=f"{prefix}-group",
                                      format_func=lambda g: "All groups" if g is None else f"{g} ({groups[g]})",
                                      label_visibility="collapsed")
            if group:
                rows = [r for r in rows if r.get("group") == group]
    find = c_find.text_input("Filter", key=f"{prefix}-find", placeholder="Filter by any word",
                             label_visibility="collapsed")
    if find:
        words = find.lower().split()
        rows = [r for r in rows if all(w in text(r).lower() for w in words)]
    if not rows:
        st.caption("Nothing matches these choices.")
        return

    if view:
        opened = st.session_state.get(f"{prefix}-open")
        in_group = [r for r in rows if (r.get("by") or {}).get(view) == opened[1]] if \
            opened and opened[0] == view else []
        if not in_group:
            _overview(rows, view, prefix=prefix, accept=accept, reject=reject, accept_word=accept_word,
                      reject_word=reject_word, noun=noun)
            return
        c_group.button("← All groups", key=f"{prefix}-back", on_click=_open, args=(prefix, None), type="tertiary")
        st.markdown(f"**{opened[1]}** · {len(in_group)} {noun}(s)")
        rows = in_group

    if any(r.get("same") for r in rows):
        if st.toggle("Merge repeats", value=True, key=f"{prefix}-merge",
                     help="The same suggestion on many items (a Plant column meaning WERKS in 300 tables) shows as "
                          "one row; accepting or rejecting it does all of them."):
            rows = merge_repeats(rows)
    _decide_list(rows, prefix=prefix, row_id=row_id, render=render, accept=accept, reject=reject,
                 accept_word=accept_word, reject_word=reject_word, noun=noun)


def _decide_list(rows: list[dict], *, prefix: str, row_id: Callable[[dict], str], render: Callable[[dict], None],
                 accept: Callable[[dict], None], reject: Callable[[dict], None] | None, accept_word: str,
                 reject_word: str, noun: str) -> None:
    def rid(r: dict) -> str:
        return r["id"] if r.get("members") else row_id(r)

    ids = [rid(r) for r in rows]
    ticked = [r for r, i in zip(rows, ids, strict=True) if st.session_state.get(_tick_key(prefix, i))]
    others = [r for r, i in zip(rows, ids, strict=True) if not st.session_state.get(_tick_key(prefix, i))]
    n_others = sum(len(_members(r)) for r in others)
    n_ticked = sum(len(_members(r)) for r in ticked)
    total = sum(len(_members(r)) for r in rows)
    st.caption(f"{total} {noun}(s) shown by these choices"
               + (f" in {len(rows)} row(s)" if len(rows) != total else "")
               + f" · {n_ticked} ticked. Tick the ones that are wrong, then **{accept_word} all except ticked**; "
                 "or tick a few and act on just those.")

    c1, c2, c3, c4 = st.columns([2.2, 1.6, 1.6, 1.4])
    with c1.popover(f"{accept_word} all except ticked ({n_others})", disabled=not others, type="primary"):
        st.markdown(f"{accept_word} **{n_others}** {noun}(s)"
                    + (f", leaving the {n_ticked} ticked one(s) waiting" if ticked else "") + "?")
        if st.button(f"Yes, {accept_word.lower()} {n_others}", key=f"{prefix}-all-yes", type="primary"):
            _run(others, accept, f"{accept_word}ing")
            st.rerun()
    if c2.button(f"{accept_word} ticked ({n_ticked})", key=f"{prefix}-acc", disabled=not ticked):
        _run(ticked, accept, f"{accept_word}ing")
        st.rerun()
    if reject and c3.button(f"{reject_word} ticked ({n_ticked})", key=f"{prefix}-rej", disabled=not ticked,
                            help="They won't be suggested again"):
        _run(ticked, reject, f"{reject_word}ing")
        st.rerun()
    shown = st.session_state.get(f"{prefix}-shown", PAGE)
    visible = rows[:shown]
    all_ticked = bool(visible) and all(st.session_state.get(_tick_key(prefix, rid(r))) for r in visible)
    c4.button("Untick all" if all_ticked else "Tick all shown", key=f"{prefix}-tickall", on_click=_toggle_all,
              args=(prefix, [rid(r) for r in visible]))

    for r in visible:
        a, b = st.columns([0.4, 8])
        a.checkbox("tick", key=_tick_key(prefix, rid(r)), label_visibility="collapsed")
        with b:
            render(r)
            if r.get("members"):
                st.caption(repeats_line(r))
            if r.get("band"):
                st.caption(BAND_LABELS[r["band"]] + (f": {r['why']}" if r.get("why") else ""))
    if len(rows) > shown:
        st.button(f"Show {min(PAGE, len(rows) - shown)} more of {len(rows) - shown}", key=f"{prefix}-more",
                  on_click=_more, args=(f"{prefix}-shown", shown, PAGE))


def repeats_line(r: dict) -> str:
    """'🔁 The same on 312 items: Stock, Orders, Sales and 309 more' for a merged row."""
    items = list(dict.fromkeys(m.get("item") or "" for m in r["members"] if m.get("item")))
    n = len(r["members"])
    head = f"🔁 The same suggestion on {n} items"
    if not items:
        return head
    more = len(items) - 6
    return f"{head}: " + ", ".join(items[:6]) + (f" and {more} more" if more > 0 else "")


def group_rows(rows: list[dict], view: str) -> list[tuple[str, list[dict]]]:
    """[(group, rows)] for one Organise by choice, biggest first, "Other" last."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("by") or {}).get(view) or OTHER].append(r)
    return sorted(groups.items(), key=lambda kv: (kv[0] == OTHER, -len(kv[1]), kv[0].lower()))


def band_line(rows: list[dict]) -> str:
    bands = Counter(r.get("band") for r in rows if r.get("band"))
    return " · ".join(f"{BAND_LABELS[b].split()[0]} {bands[b]}" for b in BANDS if bands.get(b))


def _overview(rows: list[dict], view: str, *, prefix: str, accept: Callable[[dict], None],
              reject: Callable[[dict], None] | None, accept_word: str, reject_word: str, noun: str) -> None:
    """One line per group: how many, how likely, and Open / accept all / reject all (each asks once)."""
    groups = group_rows(rows, view)
    st.caption(f"{len(rows)} {noun}(s) in **{len(groups)}** group(s) by {view.lower()}. **Open** a group to look "
               f"through it and tick the wrong ones, or {accept_word.lower()} or {reject_word.lower()} a whole "
               "group at once. 🟢 likely · 🟡 check · 🔴 unlikely.")
    shown = st.session_state.get(f"{prefix}-gshown", GROUP_PAGE)
    pending = st.session_state.get(f"{prefix}-pending")  # (view, group, "accept" | "likely" | "reject")
    for i, (g, items) in enumerate(groups[:shown]):
        likely = [r for r in items if r.get("band") == "likely"]
        repeats = len({r["same"] for r in items if r.get("same")})
        c1, c2, c3, c4, c5 = st.columns([4.6, 0.9, 1.3, 1.3, 1.1], vertical_alignment="center")
        same = (" · the same suggestion on each" if repeats == 1 and len(items) > 1 else
                f" · {repeats} different" if 1 < repeats < len(items) else "")
        c1.markdown(f"**{g}** · {len(items)}{same}" + (f"  \n:gray[{band_line(items)}]" if band_line(items) else ""))
        c2.button("Open", key=f"{prefix}-go-{i}", on_click=_open, args=(prefix, (view, g)))
        c3.button(f"{accept_word} {len(items)}", key=f"{prefix}-ga-{i}", on_click=_pend,
                  args=(prefix, (view, g, "accept")))
        if likely and len(likely) < len(items):
            c4.button(f"Likely {len(likely)}", key=f"{prefix}-gl-{i}", on_click=_pend,
                      args=(prefix, (view, g, "likely")), help=f"{accept_word} only the 🟢 likely ones in this group")
        if reject:
            c5.button(f"{reject_word} {len(items)}", key=f"{prefix}-gr-{i}", on_click=_pend,
                      args=(prefix, (view, g, "reject")), type="tertiary")
        if pending and pending[:2] == (view, g):
            todo = likely if pending[2] == "likely" else items
            word = reject_word if pending[2] == "reject" else accept_word
            with st.container(border=True):
                st.markdown(f"{word} **{len(todo)}** {noun}(s) in **{g}**?"
                            + (" They won't be suggested again." if pending[2] == "reject" else ""))
                y, n = st.columns([1, 4])
                if y.button(f"Yes, {word.lower()} {len(todo)}", key=f"{prefix}-gy-{i}", type="primary"):
                    _run(todo, reject if pending[2] == "reject" else accept, f"{word}ing")
                    st.session_state.pop(f"{prefix}-pending", None)
                    st.rerun()
                n.button("Cancel", key=f"{prefix}-gn-{i}", on_click=_pend, args=(prefix, None))
    if len(groups) > shown:
        st.button(f"Show {min(GROUP_PAGE, len(groups) - shown)} more groups of {len(groups) - shown}",
                  key=f"{prefix}-gmore", on_click=_more, args=(f"{prefix}-gshown", shown, GROUP_PAGE))


def _open(prefix: str, which: tuple[str, str] | None) -> None:
    st.session_state[f"{prefix}-open"] = which
    st.session_state.pop(f"{prefix}-shown", None)
    st.session_state.pop(f"{prefix}-pending", None)


def _pend(prefix: str, which: tuple[str, str, str] | None) -> None:
    st.session_state[f"{prefix}-pending"] = which


def _more(key: str, shown: int, page: int) -> None:
    st.session_state[key] = shown + page


def _run(rows: list[dict], fn: Callable[[dict], None], verb: str) -> None:
    every = [m for r in rows for m in _members(r)]
    bar = st.progress(0.0, text=f"{verb} {len(every)}…") if len(every) > 20 else None
    for i, r in enumerate(every, 1):
        fn(r)
        if bar and (i % 10 == 0 or i == len(every)):
            bar.progress(i / len(every), text=f"{verb} {i} of {len(every)}…")


def _toggle_all(prefix: str, ids: list[str]) -> None:
    keys = [_tick_key(prefix, i) for i in ids]
    value = not all(st.session_state.get(k) for k in keys)
    for k in keys:
        st.session_state[k] = value
