"""Page layout: a section picker and a page list in the sidebar, an item page when one is open."""
from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from pdcat import __version__
from pdcat.store import Catalogue, now

from . import code, review_pages, search, settings_page, share, sources
from .arrowsafe import install as install_arrow_fallback
from .arrowsafe import sidebar_note
from .common import go, go_page, icon, kind_label, open_catalogue, timed
from .detail import detail

Page = Callable[[Catalogue], None]


def combined(title: str, caption: str, views: dict[str, Page]) -> Page:
    """One menu page made of several views, picked with a row of buttons. Only the picked view runs."""
    def page(cat: Catalogue) -> None:
        st.title(title)
        if caption:
            st.caption(caption)
        view = st.radio(title, list(views), horizontal=True, key=f"sub-{title}", label_visibility="collapsed")
        st.session_state["_sub_page"] = True
        try:
            views[view](cat)
        finally:
            st.session_state["_sub_page"] = False
    return page


def sections() -> dict[str, dict[str, Page]]:
    from . import (
        bproject_pages,
        curation_pages,
        dataflow_page,
        erd_page,
        groups_page,
        home_page,
        keys_pages,
        knowledge_pages,
        lists,
        menu_guide,
        ml_pages,
        scan_log,
        scanning,
        setup_page,
        sources_extra,
        sql_pages,
    )
    return {
        "🏠 Home": {"Home": home_page.home_page, "Menu guide": menu_guide.menu_guide_page},
        "🔎 Find": {"Search": search.search_page, "SQL queries": sql_pages.queries_page},
        "📚 Knowledge": {"Knowledge": knowledge_pages.knowledge_page, "Business projects": bproject_pages.bprojects_page,
                        "Question library": knowledge_pages.questions_page,
                        "Metric glossary": knowledge_pages.metrics_page, "ML model features": ml_pages.features_page,
                        "SAP links": knowledge_pages.sap_page, "Company-wide keys": keys_pages.keys_page},
        "📋 Review": {
            "To review": combined("To review", "", {
                "All checks": review_pages.review_page, "Data issues": search.data_issues_page,
                "Needs notes": search.needs_notes_page, "Unmatched tables": search.unmatched_page,
                "Unlinked data": lists.unlinked_page, "Duplicates": lists.duplicates_page}),
            "Suggestions": groups_page.suggestions_page,
            "Health": combined("Health", "How well the catalogue is described, and where problems gather.", {
                "Coverage": curation_pages.coverage_page, "Issue hotspots": review_pages.hotspots_page}),
            "What's new": combined("What's new", "", {
                "Recent notes": search.recent_notes_page, "Recent changes": lists.changes_page}),
            "Bulk edit": combined("Bulk edit", "", {
                "Describe columns": curation_pages.describe_columns_page,
                "Edit many items": curation_pages.bulk_edit_page}),
        },
        "🗺️ Map": {"Code map": code.code_map_page, "Data flow": dataflow_page.data_flow_page,
                   "Database diagrams": erd_page.erd_page,
                   "Packages": code.packages_page},
        "➕ Add": {
            "Sources": combined("Sources", "Where the catalogue reads from: SQL you paste, folders of data and code, "
                                           "and SQL Server databases.", {
                "Add SQL": sources.add_sql_page, "Data folders": sources.folders_page,
                "SQL scanning": scanning.scanning_page, "Scan log": scan_log.scan_log_page}),
            "Schedules": sources_extra.schedules_page, "Conda environments": sources_extra.envs_page,
            "Import from Excel": sources_extra.excel_import_page},
        "📤 Share": {"Share with colleagues": share.sharing_page, "Copilot pack": share.copilot_page,
                    "Export": share.export_page},
        "⚙️ Settings": {"Settings": settings_page.settings_page, "Sensitive data": keys_pages.sensitive_page,
                       "Get started": setup_page.setup_page},
    }


# Pages that became a view of a combined page: old name -> (page, view).
ALIASES = {
    "Data issues": ("To review", "Data issues"), "Needs notes": ("To review", "Needs notes"),
    "Unmatched tables": ("To review", "Unmatched tables"), "Unlinked data": ("To review", "Unlinked data"),
    "Duplicates": ("To review", "Duplicates"), "Coverage": ("Health", "Coverage"),
    "Issue hotspots": ("Health", "Issue hotspots"), "Recent notes": ("What's new", "Recent notes"),
    "Recent changes": ("What's new", "Recent changes"), "Describe columns": ("Bulk edit", "Describe columns"),
    "Edit many items": ("Bulk edit", "Edit many items"), "Add SQL": ("Sources", "Add SQL"),
    "Data folders": ("Sources", "Data folders"), "SQL scanning": ("Sources", "SQL scanning"),
    "Scan log": ("Sources", "Scan log"),
    "Feature dictionary": ("ML model features", None),
}


NEEDS_DATA = {"Search", "Packages", "Health", "Bulk edit"}


def leave_item() -> None:
    """Picking a page from the menu closes the item page that is open, so the page shows."""
    st.query_params.pop("asset", None)
    st.query_params.pop("fn", None)


def pick_from_item(section: str) -> None:
    """A page picked while an item page is open: open that page."""
    st.session_state[f"nav-{section}"] = st.session_state.pop(f"nav-item-{section}")
    leave_item()


def search_from_sidebar() -> None:
    """The sidebar search box opens the Search page with those words. The box empties after each search, so the
    same words can be searched again from any page."""
    text = st.session_state.get("sidebar-search", "")
    st.session_state["sidebar-search"] = ""
    if text.strip():
        st.session_state["search-text"] = text.strip()
        go_page("Search")


def recently_viewed(cat: Catalogue) -> None:
    from pdcat.followups import kind_of, recent
    current = st.query_params.get("asset")
    rows = [r for r in recent(cat, limit=7) if r["item"] != current][:6]
    if rows:
        with st.expander("🕘 Recently viewed", expanded=False):
            for i, r in enumerate(rows):
                st.button(f"{icon(kind_of(r['item'], r['kind']))} {r['name']}", key=f"recent-{i}", on_click=go,
                          args=(r["item"],), type="tertiary")


def sidebar(cat: Catalogue) -> tuple[str, Page]:
    from pdcat.review import count_open
    secs = sections()
    with st.sidebar:
        st.header("Data catalogue")
        with st.form("sidebar-search-form", clear_on_submit=True, border=False):
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            c1.text_input("Search", key="sidebar-search", placeholder="🔎 Search everything",
                          label_visibility="collapsed")
            c2.form_submit_button("🔎", on_click=search_from_sidebar, help="Search (or press Enter)")
        section = st.selectbox("Section", list(secs), key="nav-section", label_visibility="collapsed",
                               on_change=leave_item)
        pages = secs[section]
        if st.query_params.get("asset"):
            # On an item page no menu page is ticked, so clicking any page, even the one you came from, opens it.
            st.radio("Page", list(pages), index=None, key=f"nav-item-{section}", label_visibility="collapsed",
                     on_change=pick_from_item, args=(section,))
            page = st.session_state.get(f"nav-{section}") or next(iter(pages))
        else:
            page = st.radio("Page", list(pages), key=f"nav-{section}", label_visibility="collapsed",
                            on_change=leave_item)
        recently_viewed(cat)
        sidebar_note()
        st.button("📖 Menu guide", key="menu-guide-link", on_click=go_page, args=("Menu guide",), type="tertiary",
                  help="What every page in the menu is for")
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
    st.session_state["_last_visit"] = cat.setting("last_visit")
    cat.set_setting("last_visit", now(), quiet=True)
    from pdcat.backup import maybe_backup
    try:
        made = maybe_backup(cat)
        if made:
            st.toast(f"Backed up to {made.name}")
    except Exception as e:  # a missing network drive must not stop the app
        st.session_state["_backup_error"] = f"{type(e).__name__}: {e}"


def main() -> None:
    st.set_page_config(page_title="Data catalogue", page_icon="🗃️", layout="wide")
    install_arrow_fallback()  # tables fall back to plain HTML when pyarrow's DLLs won't load
    st.session_state["_timings"] = []
    cat = open_catalogue()
    try:
        session_start(cat)
        if st.session_state.get("_upgraded"):
            st.success(f"Upgraded your catalogue to version {__version__}. Every note was kept, and an untouched "
                       f"copy of the old file is at `{st.session_state['_upgraded']}`.", icon="✅")
            st.button("OK", on_click=lambda: st.session_state.pop("_upgraded", None))
        if st.session_state.get("_backup_error"):
            st.warning(f"Couldn't make the scheduled backup: {st.session_state.pop('_backup_error')}")
        key = st.query_params.get("asset")
        with timed("sidebar"):
            name, page = sidebar(cat)
        if key:
            with timed("item page"):
                detail(cat, key)
            from pdcat.followups import viewed
            viewed(cat, key)
        elif name in NEEDS_DATA and not cat.stats()["kinds"] and not cat.conn.execute(
                "SELECT 1 FROM knowledge LIMIT 1").fetchone():
            from .setup_page import setup_page
            setup_page(cat)
        else:
            with timed(name):
                page(cat)
        if cat.setting("show_timings", False):
            with st.sidebar.expander("⏱️ Timings", expanded=True):
                for label, ms in st.session_state["_timings"]:
                    st.caption(f"{label}: {ms:,.0f} ms")
    finally:
        cat.close()
