"""Helpers shared by every page of the app: opening the catalogue, navigation, caching, labels."""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

import streamlit as st

from pdcat.store import Catalogue

KIND_ICONS = {"table": "🗃️", "view": "👁️", "procedure": "⚙️", "database": "🛢️", "project": "📁",
              "script": "🐍", "notebook": "📓", "sql_script": "📜", "sql_snippet": "✍️", "note": "📝",
              "data_file": "📄", "folder": "🗂️", "file_series": "🗓️", "package": "📦", "report": "📊",
              "workbook": "📗", "model": "🧠", "measure": "🧮", "bproject": "🧭", "knowledge": "💡",
              "question": "❓", "metric": "📏", "feature": "🧬", "key": "🔑", "group": "👪", "query": "🧾",
              "environment": "🐍", "access_db": "🗄️", "document": "📑",
              "data_source": "🔌", "pbi_table": "🧊", "pq_query": "🔁"}
KIND_LABELS = {"data_file": "data file", "sql_snippet": "pasted SQL", "sql_script": "SQL file",
               "file_series": "file series", "bproject": "business project", "workbook": "Excel workbook",
               "report": "Power BI report", "model": "ML model", "access_db": "Access database",
               "query": "SQL query", "environment": "conda environment",
               "data_source": "data source", "pbi_table": "model table",
               "pq_query": "Power Query"}
NOTE_ICONS = {"note": "📝", "issue": "⚠️", "example": "🔢"}
RELATION_LABELS = {"reads": "Reads", "writes": "Writes", "calls": "Calls", "imports": "Imports", "runs": "Runs"}
LIFECYCLE_BADGES = {"experimental": "🧪 experimental", "deprecated": "⛔ deprecated", "retired": "🪦 retired"}
TRUST_BADGES = {"trusted": "✅ trusted", "caution": "⚠️ use with caution", "do not use": "🚫 don't use"}


def _app_args() -> argparse.Namespace:
    """What `pdcat ui` passed after "--": the catalogue file and the config file."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("PDCAT_DB"))
    parser.add_argument("--config", default=os.environ.get("PDCAT_CONFIG"))
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args


def db_path() -> str:
    db = _app_args().db
    if not db:
        st.error("No catalogue given. Start the app with `pdcat ui`.")
        st.stop()
    return db


def config_path() -> str | None:
    return _app_args().config


def open_catalogue() -> Catalogue:
    # A fresh connection per run keeps SQLite happy with Streamlit's threads; it is cheap.
    return Catalogue(db_path())


def go(key: str | None, fn: str | None = None) -> None:
    if key:
        st.query_params["asset"] = key
    else:
        st.query_params.pop("asset", None)
    if fn:
        st.query_params["fn"] = fn
        st.session_state[f"zoom-{key}"] = fn
    else:
        st.query_params.pop("fn", None)
        st.session_state.pop(f"zoom-{key}", None)


def page_title(text: str) -> None:
    """A page's title, left out when the page is shown as one view of a combined page."""
    if not st.session_state.get("_sub_page"):
        st.title(text)


def go_page(name: str) -> None:
    """Open a menu page by name (use as a button's on_click). Old page names open their new home."""
    from .main import ALIASES, sections
    page, view = ALIASES.get(name, (name, None))
    section = next((s for s, pages in sections().items() if page in pages), None)
    if section is None:
        return
    st.query_params.pop("asset", None)
    st.query_params.pop("fn", None)
    st.session_state["nav-section"] = section
    st.session_state[f"nav-{section}"] = page
    st.session_state.pop(f"nav-item-{section}", None)
    if view:
        st.session_state[f"sub-{page}"] = view


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def icon(kind: str) -> str:
    return KIND_ICONS.get(kind, "•")


def asset_button(key: str, label: str, kind: str, widget_key: str, help: str | None = None) -> None:
    st.button(f"{icon(kind)} {label}", key=widget_key, on_click=go, args=(key,), type="tertiary", help=help)


def cached(fn: Callable) -> Callable:
    """Cache a read-only query between clicks. The cache is keyed on the catalogue's revision,
    so anything you change (a scan, a note) shows straight away. The function must return
    plain data (dicts, lists, frames), not sqlite rows."""
    name = f"{fn.__module__}.{fn.__qualname__}"

    @st.cache_data(show_spinner=False, max_entries=128)
    def run(fn_name: str, db: str, revision: int, *args: Any, **kwargs: Any) -> Any:
        # fn_name is part of the cache key: every cached function shares this inner function's code.
        cat = Catalogue(db)
        try:
            return fn(cat, *args, **kwargs)
        finally:
            cat.close()

    @functools.wraps(fn)
    def wrapper(cat: Catalogue, *args: Any, **kwargs: Any) -> Any:
        return run(name, str(cat.path), cat.revision(), *args, **kwargs)

    return wrapper


@contextmanager
def timed(label: str):
    """Measure a step for the optional timing panel (Settings → Show timings)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        st.session_state.setdefault("_timings", []).append((label, (time.perf_counter() - start) * 1000))


def badges(info: dict | None) -> list[str]:
    """Status, trust and sensitivity labels from an item's curated info."""
    if not info:
        return []
    out = []
    if info.get("lifecycle") in LIFECYCLE_BADGES:
        out.append(LIFECYCLE_BADGES[info["lifecycle"]])
    if info.get("trust") in TRUST_BADGES:
        out.append(TRUST_BADGES[info["trust"]])
    if info.get("sensitive"):
        out.append("🔒 sensitive")
    if info.get("copy_of"):
        out.append("📑 copy")
    if info.get("relevance") == "not relevant":
        out.append("🙈 not relevant")
    elif info.get("relevance") == "old":
        out.append("🗄️ old")
    return out


def size_label(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


def note_meta(n) -> str:
    when = n["updated_at"][:16].replace("T", " ")
    tags = " ".join(f"#{t.strip()}" for t in (n["tags"] or "").split(",") if t.strip())
    who = n["author"] if "author" in n.keys() and n["author"] else ""
    return f"{when} UTC" + (f" · {who}" if who else "") + (f" · {tags}" if tags else "")


def first_line(text: str | None, limit: int = 200) -> str:
    return (text or "").strip().splitlines()[0][:limit] if (text or "").strip() else ""


def loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def row_get(r, field: str, default: Any = None) -> Any:
    """Read a field from a dict or a sqlite row that may not have it."""
    try:
        return r[field] if field in r.keys() else default
    except AttributeError:
        return r.get(field, default)


RESULT_PAGE = 200


def result_list(rows, prefix: str, extra=None) -> None:
    if not rows:
        st.info("Nothing here yet.")
        return
    # Thousands of rows of buttons take seconds to draw, so show them a page at a time.
    shown = st.session_state.get(f"{prefix}-shown", RESULT_PAGE)
    for i, r in enumerate(rows[:shown]):
        left, right = st.columns([4, 2])
        with left:
            asset_button(r["key"], r["name"], r["kind"], f"{prefix}-{i}")
            if r["description"]:
                st.caption(first_line(r["description"]))
        with right:
            bits = [kind_label(r["kind"]), r["source"]] if r["source"] else [kind_label(r["kind"])]
            if row_get(r, "note_count"):
                bits.append(f"📝 {r['note_count']}")
            if row_get(r, "status") == "missing":
                bits.append("⚠️ missing")
            if extra:
                bits.append(extra(r))
            st.caption(" · ".join(b for b in bits if b))
    if len(rows) > shown:
        st.button(f"Show {min(RESULT_PAGE, len(rows) - shown)} more (of {len(rows) - shown} not shown)",
                  key=f"{prefix}-more", on_click=lambda: st.session_state.update({f"{prefix}-shown": shown + RESULT_PAGE}))


def lazy_download(label: str, build: Callable[[Catalogue], bytes | str], file_name: str, mime: str, key: str,
                  help: str | None = None) -> None:
    """A download button whose file is only built when it is clicked. v1 rebuilt every export on
    every click, which is what made the app slow after a scan."""
    db = db_path()

    def run() -> bytes | str:
        cat = Catalogue(db)  # runs on another thread, so it needs its own connection
        try:
            return build(cat)
        finally:
            cat.close()

    st.download_button(label, run, file_name, mime, key=key, on_click="ignore", help=help)
