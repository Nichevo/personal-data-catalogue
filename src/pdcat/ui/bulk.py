"""Deciding many suggestions at once: sort into likely / check / unlikely, filter, tick the few that are wrong,
and accept all the others."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

import streamlit as st

from pdcat.triage import BAND_LABELS, BANDS

PAGE = 50


def _tick_key(prefix: str, rid: str) -> str:
    return f"{prefix}-t-{rid}"


def bulk_decide(rows: list[dict[str, Any]], *, prefix: str, row_id: Callable[[dict], str],
                text: Callable[[dict], str], render: Callable[[dict], None],
                accept: Callable[[dict], None], reject: Callable[[dict], None] | None,
                accept_word: str = "Accept", reject_word: str = "Reject", noun: str = "suggestion") -> None:
    """A list with band and group pickers, a filter box and a tick box per row, and buttons for many rows at once.

    Rows may carry "band" (likely / check / unlikely), "why" and "group" (see pdcat.triage). "Accept all except
    ticked" is the fast path: show the likely ones, tick the few that are wrong, and accept every other row that
    matches, including rows further down not yet shown.
    """
    if not rows:
        st.caption("Nothing waiting.")
        return
    bands = Counter(r.get("band") for r in rows if r.get("band"))
    if len(bands) > 1:
        options = ["all"] + [b for b in BANDS if bands.get(b)]
        band = st.segmented_control(
            "Show", options, key=f"{prefix}-band", default="all",
            format_func=lambda b: f"All ({len(rows)})" if b == "all" else f"{BAND_LABELS[b]} ({bands[b]})")
        if band and band != "all":
            rows = [r for r in rows if r.get("band") == band]
    groups = Counter(r.get("group") for r in rows if r.get("group"))
    c_group, c_find = st.columns([1, 2])
    if len(groups) > 1:
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
    ids = [row_id(r) for r in rows]
    ticked = [r for r, rid in zip(rows, ids, strict=True) if st.session_state.get(_tick_key(prefix, rid))]
    others = [r for r, rid in zip(rows, ids, strict=True) if not st.session_state.get(_tick_key(prefix, rid))]
    st.caption(f"{len(rows)} {noun}(s) shown by these choices · {len(ticked)} ticked. Tick the ones that are wrong, "
               f"then **{accept_word} all except ticked**; or tick a few and act on just those.")

    c1, c2, c3, c4 = st.columns([2.2, 1.6, 1.6, 1.4])
    with c1.popover(f"{accept_word} all except ticked ({len(others)})", disabled=not others, type="primary"):
        st.markdown(f"{accept_word} **{len(others)}** {noun}(s)"
                    + (f", leaving the {len(ticked)} ticked one(s) waiting" if ticked else "") + "?")
        if st.button(f"Yes, {accept_word.lower()} {len(others)}", key=f"{prefix}-all-yes", type="primary"):
            _run(others, accept, f"{accept_word}ing")
            st.rerun()
    if c2.button(f"{accept_word} ticked ({len(ticked)})", key=f"{prefix}-acc", disabled=not ticked):
        _run(ticked, accept, f"{accept_word}ing")
        st.rerun()
    if reject and c3.button(f"{reject_word} ticked ({len(ticked)})", key=f"{prefix}-rej", disabled=not ticked,
                            help="They won't be suggested again"):
        _run(ticked, reject, f"{reject_word}ing")
        st.rerun()
    shown = st.session_state.get(f"{prefix}-shown", PAGE)
    visible = rows[:shown]
    all_ticked = bool(visible) and all(st.session_state.get(_tick_key(prefix, row_id(r))) for r in visible)
    c4.button("Untick all" if all_ticked else "Tick all shown", key=f"{prefix}-tickall", on_click=_toggle_all,
              args=(prefix, visible, row_id))

    for r in visible:
        a, b = st.columns([0.4, 8])
        a.checkbox("tick", key=_tick_key(prefix, row_id(r)), label_visibility="collapsed")
        with b:
            render(r)
            if r.get("band"):
                st.caption(BAND_LABELS[r["band"]] + (f": {r['why']}" if r.get("why") else ""))
    if len(rows) > shown:
        st.button(f"Show {min(PAGE, len(rows) - shown)} more of {len(rows) - shown}", key=f"{prefix}-more",
                  on_click=_more, args=(prefix, shown))


def _more(prefix: str, shown: int) -> None:
    st.session_state[f"{prefix}-shown"] = shown + PAGE


def _run(rows: list[dict], fn: Callable[[dict], None], verb: str) -> None:
    bar = st.progress(0.0, text=f"{verb} {len(rows)}…") if len(rows) > 20 else None
    for i, r in enumerate(rows, 1):
        fn(r)
        if bar and (i % 10 == 0 or i == len(rows)):
            bar.progress(i / len(rows), text=f"{verb} {i} of {len(rows)}…")


def _toggle_all(prefix: str, rows: list[dict], row_id: Callable[[dict], str]) -> None:
    keys = [_tick_key(prefix, row_id(r)) for r in rows]
    value = not all(st.session_state.get(k) for k in keys)
    for k in keys:
        st.session_state[k] = value
