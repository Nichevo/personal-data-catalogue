"""About: what the app is, how its parts fit together (drawn as a subway map), and what changed in each version."""
from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from pdcat import __version__
from pdcat.codemap import Graph
from pdcat.store import Catalogue

# How information travels through the app. Lines are what you bring in (and what the app makes from it);
# stations are the parts of the app that use them. Same drawing as the data flow's subway map.
STATIONS = {
    "scan": "Scan (pdcat scan, Add › Sources)",
    "know": "Knowledge and item pages",
    "find": "Find (search, lists, summaries)",
    "review": "Review (checks, suggestions)",
    "home": "Home (follow-ups, what changed)",
    "map": "Map (code map, data flow, Power BI map, ERDs)",
    "share": "Share (export, Copilot pack, colleagues)",
}
LINES = {
    "sql": ("🛢️ SQL Server tables and views", "outside"),
    "python": ("Python projects and notebooks", "file"),
    "office": ("Power BI, Excel and Access files", "file"),
    "folders": ("Data folders (CSV, Parquet...)", "file"),
    "catalogue": ("The catalogue (one SQLite file)", "table"),
    "notes": ("Your notes, tags and answers", "table"),
    "todo": ("Follow-ups and To review items", "table"),
    "exports": ("CSV / JSON exports and packs", "file"),
}
ROUTES = [("sql", "scan", "reads"), ("python", "scan", "reads"), ("office", "scan", "reads"),
          ("folders", "scan", "reads"), ("scan", "catalogue", "writes"),
          ("catalogue", "know", "reads"), ("know", "notes", "writes"),
          ("catalogue", "find", "reads"), ("notes", "find", "reads"),
          ("catalogue", "review", "reads"), ("notes", "review", "reads"), ("review", "todo", "writes"),
          ("review", "notes", "writes"), ("todo", "home", "reads"), ("catalogue", "home", "reads"),
          ("catalogue", "map", "reads"), ("catalogue", "share", "reads"), ("notes", "share", "reads"),
          ("share", "exports", "writes")]


def app_map() -> Graph:
    g = Graph()
    for k, label in STATIONS.items():
        g.nodes[k] = {"label": label, "kind": "code", "group": None, "key": None}
    for k, (label, kind) in LINES.items():
        g.nodes[k] = {"label": label, "kind": kind, "group": None, "key": None}
    for a, b, rel in ROUTES:
        g.edges[(a, b, rel)]
    return g


def releases(readme: Path | None = None) -> list[tuple[str, str]]:
    """(version, notes) from the README's "New in version ..." sections, newest first as written there."""
    readme = readme or Path(__file__).resolve().parents[3] / "README.md"
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return []
    parts = re.split(r"^## New in version (\S+)\s*$", text, flags=re.M)
    out = []
    for version, body in zip(parts[1::2], parts[2::2]):
        out.append((version, re.split(r"^## ", body, maxsplit=1, flags=re.M)[0].strip()))
    # "Version 2" was the big rewrite; every later section is newer than it.
    return sorted(out, key=lambda r: r[0] == "2")


def about_page(cat: Catalogue) -> None:
    from pdcat.subway import subway_svg

    from .graphview import show_svg
    st.title("About")
    st.markdown(f"**Personal data catalogue, version {__version__}.** A catalogue of your own data that runs on "
                "your laptop: it reads your SQL Server tables, Python and SQL code, Power BI reports, Excel "
                "workbooks and data folders, keeps what it finds in one SQLite file, and lets you add what only "
                "you know. Nothing leaves your computer unless you export it.")
    st.subheader("How it fits together")
    st.caption("Each coloured line is something the app works with, running top to bottom through the parts of "
               "the app that use it. ▶ is where it comes in or is made, ■ where it ends.")
    show_svg(subway_svg(app_map(), width=760), key="about-map", max_height=1400)
    st.subheader("What's new")
    notes = releases()
    if not notes:
        st.caption("The release notes are in the README that came with the app.")
        return
    for i, (version, body) in enumerate(notes):
        with st.expander(f"Version {version}", expanded=i == 0):
            st.markdown(body)
