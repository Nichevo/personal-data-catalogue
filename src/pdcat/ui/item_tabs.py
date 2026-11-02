"""Keeping item pages tidy: a summary line, the same five tabs on every item page (only the open one is drawn,
and the app remembers your last tab for each kind of item), and long lists cut to the first few with a filter."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

TABS = ("overview", "data", "connections", "inside", "history")
LIMIT = 10
_MEMORY = "_item_tab"   # {kind: tab}; not a widget key, so it survives pages that don't draw the tab bar


def remembered(kind: str) -> str | None:
    return st.session_state.get(_MEMORY, {}).get(kind)


def remember(kind: str, tab: str) -> None:
    st.session_state.setdefault(_MEMORY, {})[kind] = tab


def tab_bar(key: str, kind: str, labels: dict[str, str]) -> str:
    """Draw the tabs (labels: tab -> label, in order; tabs without content are left out) and return the open one."""
    options = [t for t in TABS if t in labels]
    want = remembered(kind)
    if want not in options:
        want = options[0]
    wkey = f"item-tab-{key}"
    if st.session_state.get(wkey) not in options or st.session_state.pop(f"{wkey}-jump", False):
        st.session_state[wkey] = want

    def changed() -> None:
        if st.session_state.get(wkey):
            remember(kind, st.session_state[wkey])

    picked = st.segmented_control("Section", options, key=wkey, format_func=labels.get, on_change=changed,
                                  label_visibility="collapsed")
    return picked or want


def jump(key: str, kind: str, tab: str) -> None:
    """Open a tab from a summary chip."""
    remember(kind, tab)
    st.session_state[f"item-tab-{key}-jump"] = True


def summary_chips(key: str, kind: str, chips: list[tuple[str, str, bool]]) -> None:
    """One line of counts ("42 columns", "2 to review"...); clicking one opens the tab where it lives."""
    chips = [c for c in chips if c[0]]
    if not chips:
        return
    cols = st.columns(len(chips) + 1, gap="small")
    for i, (label, tab, warn) in enumerate(chips):
        cols[i].button(f":orange[{label}]" if warn else label, key=f"chip-{key}-{i}", type="tertiary",
                       on_click=jump, args=(key, kind, tab), help="Open the tab where this is")


def limited(rows: list[Any], key: str, *, text: Callable[[Any], str] | None = None, limit: int = LIMIT,
            noun: str = "") -> tuple[list[Any], Callable[[], None]]:
    """(rows to draw, footer) for a long list: the first `limit` rows and, with `text`, a filter box (drawn now).
    Call footer() after the list to draw its "Show all" / "Show fewer" button."""
    none = (lambda: None)
    if len(rows) <= limit:
        return rows, none
    if text is not None:
        find = st.text_input("Filter", key=f"lim-find-{key}", placeholder=f"Filter the {len(rows)} {noun}".strip(),
                             label_visibility="collapsed")
        if find:
            words = find.lower().split()
            rows = [r for r in rows if all(w in text(r).lower() for w in words)]
            if len(rows) <= limit:
                return rows, none
    flag = f"lim-all-{key}"
    total = len(rows)

    def footer() -> None:
        if st.session_state.get(flag):
            st.button(f"Show only the first {limit}", key=f"lim-less-{key}", type="tertiary",
                      on_click=_set, args=(flag, False))
        else:
            st.button(f"Show all {total}", key=f"lim-more-{key}", type="tertiary", on_click=_set, args=(flag, True))

    return (rows if st.session_state.get(flag) else rows[:limit]), footer


def _set(k: str, v: Any) -> None:
    st.session_state[k] = v
