"""Page layout: a section picker and a page list in the sidebar, an item page when one is open."""
from __future__ import annotations

from typing import Callable

import streamlit as st

from pdcat.store import Catalogue

from . import code, review_pages, search, settings_page, share, sources
from .common import icon, kind_label, open_catalogue, timed
from .detail import detail

Page = Callable[[Catalogue], None]


def sections() -> dict[str, dict[str, Page]]:
    from . import keys_pages, lists, scanning
    return {
        "🔎 Find": {"Search": search.search_page, "Company-wide keys": keys_pages.keys_page,
                   "Packages": code.packages_page},
        "📋 Review": {"To review": review_pages.review_page, "Issue hotspots": review_pages.hotspots_page,
                     "Data issues": search.data_issues_page, "Needs notes": search.needs_notes_page,
                     "Recent notes": search.recent_notes_page, "Unmatched tables": search.unmatched_page,
                     "Unlinked data": lists.unlinked_page, "Recent changes": lists.changes_page,
                     "Sensitive data": keys_pages.sensitive_page, "Duplicates": lists.duplicates_page},
        "🗺️ Map": {"Code map": code.code_map_page},
        "➕ Add": {"Add SQL": sources.add_sql_page, "Data folders": sources.folders_page,
                  "SQL scanning": scanning.scanning_page},
        "📤 Share": {"Export": share.export_page},
        "⚙️ Settings": {"Settings": settings_page.settings_page},
    }


NEEDS_DATA = {"Search", "Data issues", "Needs notes", "Recent notes", "Unmatched tables", "Packages"}


def sidebar(cat: Catalogue) -> tuple[str, Page]:
    from pdcat.review import count_open
    secs = sections()
    with st.sidebar:
        st.header("Data catalogue")
        section = st.selectbox("Section", list(secs), key="nav-section", label_visibility="collapsed")
        pages = secs[section]
        page = st.radio("Page", list(pages), key=f"nav-{section}", label_visibility="collapsed")
        st.divider()
        stats = cat.stats()
        kinds = stats["kinds"]
        if kinds:
            st.caption(" · ".join(f"{icon(k)} {v} {kind_label(k)}" for k, v in sorted(kinds.items())))
        st.caption(f"📝 {stats['notes']} notes · 📋 {count_open(cat)} to review")
        for source, at in stats["last_scans"].items():
            st.caption(f"{source}: scanned {at[:16].replace('T', ' ')} UTC")
    return page, pages[page]


def session_start(cat: Catalogue) -> None:
    """Once per browser session: tell you about an upgrade, and take a backup if one is due."""
    if cat.upgrade_backup:
        cat.set_setting("upgrade_backup", str(cat.upgrade_backup), quiet=True)
        st.session_state["_upgraded"] = str(cat.upgrade_backup)
    if st.session_state.get("_session_started"):
        return
    st.session_state["_session_started"] = True
    from pdcat.backup import maybe_backup
    try:
        made = maybe_backup(cat)
        if made:
            st.toast(f"Backed up to {made.name}")
    except Exception as e:  # a missing network drive must not stop the app
        st.session_state["_backup_error"] = f"{type(e).__name__}: {e}"


def main() -> None:
    st.set_page_config(page_title="Data catalogue", page_icon="🗃️", layout="wide")
    st.session_state["_timings"] = []
    cat = open_catalogue()
    try:
        session_start(cat)
        if st.session_state.get("_upgraded"):
            st.success("Upgraded your catalogue to version 2. Every note was kept, and an untouched copy of the "
                       f"old file is at `{st.session_state['_upgraded']}`.", icon="✅")
            st.button("OK", on_click=lambda: st.session_state.pop("_upgraded", None))
        if st.session_state.get("_backup_error"):
            st.warning(f"Couldn't make the scheduled backup: {st.session_state.pop('_backup_error')}")
        key = st.query_params.get("asset")
        with timed("sidebar"):
            name, page = sidebar(cat)
        if key:
            with timed("item page"):
                detail(cat, key)
        elif name in NEEDS_DATA and not cat.stats()["kinds"] and not cat.conn.execute(
                "SELECT 1 FROM knowledge LIMIT 1").fetchone():
            search.empty_catalogue()
        else:
            with timed(name):
                page(cat)
        if cat.setting("show_timings", False):
            with st.sidebar.expander("⏱️ Timings", expanded=True):
                for label, ms in st.session_state["_timings"]:
                    st.caption(f"{label}: {ms:,.0f} ms")
    finally:
        cat.close()
