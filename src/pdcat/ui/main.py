"""Page layout: the whole menu in the sidebar, grouped by section, and an item page when one is open."""
from __future__ import annotations

from typing import Callable

import streamlit as st

from pdcat.store import Catalogue

from . import code, review_pages, search, settings_page, share, sources
from .common import icon, kind_label, open_catalogue, timed
from .detail import detail

Page = Callable[[Catalogue], None]


def sections() -> dict[str, dict[str, Page]]:
    from . import (bproject_pages, curation_pages, erd_page, keys_pages, knowledge_pages, lists, ml_pages,
                   scanning, setup_page)
    return {
        "🔎 Find": {"Search": search.search_page, "Company-wide keys": keys_pages.keys_page,
                   "Packages": code.packages_page},
        "🗺️ Map": {"Code map": code.code_map_page, "Database diagrams": erd_page.erd_page},
        "📚 Knowledge": {"Knowledge": knowledge_pages.knowledge_page, "Business projects": bproject_pages.bprojects_page,
                        "Question library": knowledge_pages.questions_page,
                        "Metric glossary": knowledge_pages.metrics_page, "Feature dictionary": ml_pages.features_page},
        "📋 Review": {"Coverage": curation_pages.coverage_page, "Describe columns": curation_pages.describe_columns_page,
                     "Edit many items": curation_pages.bulk_edit_page, "To review": review_pages.review_page, "Issue hotspots": review_pages.hotspots_page,
                     "Data issues": search.data_issues_page, "Needs notes": search.needs_notes_page,
                     "Recent notes": search.recent_notes_page, "Unmatched tables": search.unmatched_page,
                     "Unlinked data": lists.unlinked_page, "Recent changes": lists.changes_page,
                     "Sensitive data": keys_pages.sensitive_page, "Duplicates": lists.duplicates_page},
        "➕ Add": {"Add SQL": sources.add_sql_page, "Data folders": sources.folders_page,
                  "SQL scanning": scanning.scanning_page},
        "📤 Share": {"Share with colleagues": share.sharing_page, "Copilot pack": share.copilot_page,
                    "Export": share.export_page},
        "⚙️ Settings": {"Settings": settings_page.settings_page, "Get started": setup_page.setup_page},
    }


NEEDS_DATA = {"Search", "Data issues", "Needs notes", "Recent notes", "Unmatched tables", "Packages", "Coverage",
              "Describe columns", "Edit many items"}


# Tight rows for the menu: Streamlit spaces buttons for forms, not for a list of links.
MENU_CSS = """<style>
[data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stVerticalBlock"] { gap: 0; }
[data-testid="stSidebar"] [data-testid="stExpander"] button { min-height: 1.9rem; padding: 0.1rem 0; }
[data-testid="stSidebar"] [data-testid="stExpander"] details > div { padding-top: 0.2rem; padding-bottom: 0.4rem; }
</style>"""


def open_page(name: str) -> None:
    """Show a menu page, leaving any item page that is open. The page name goes in the link, so a refresh
    or a bookmark comes back to it."""
    st.session_state["nav-page"] = name
    st.query_params.pop("asset", None)
    st.query_params.pop("fn", None)
    st.query_params["page"] = name


def current_page(secs: dict[str, dict[str, Page]]) -> str:
    names = {n for pages in secs.values() for n in pages}
    name = st.session_state.get("nav-page") or st.query_params.get("page")
    return name if name in names else "Search"


def sidebar(cat: Catalogue) -> tuple[str, Page]:
    from pdcat.review import count_open
    secs = sections()
    name = current_page(secs)
    on_item = bool(st.query_params.get("asset"))
    with st.sidebar:
        st.header("Data catalogue")
        st.html(MENU_CSS)
        # Every page is listed, so nothing hides behind a picker. Fold away the sections you don't use.
        for section, pages in secs.items():
            with st.expander(section, expanded=True):
                for page in pages:
                    here = page == name and not on_item
                    st.button(f"**{page}**" if here else page, key=f"nav-{page}", on_click=open_page,
                              args=(page,), type="tertiary", icon=":material/chevron_right:" if here else None)
        st.divider()
        stats = cat.stats()
        kinds = stats["kinds"]
        if kinds:
            st.caption(" · ".join(f"{icon(k)} {v} {kind_label(k)}" for k, v in sorted(kinds.items())))
        st.caption(f"📝 {stats['notes']} notes · 📋 {count_open(cat)} to review")
        for source, at in stats["last_scans"].items():
            st.caption(f"{source}: scanned {at[:16].replace('T', ' ')} UTC")
    return name, next(pages[name] for pages in secs.values() if name in pages)


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
