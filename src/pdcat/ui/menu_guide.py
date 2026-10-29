"""Menu guide: one line on what each menu page is for, with a button to open it."""
from __future__ import annotations

import streamlit as st

from pdcat.store import Catalogue

from .common import go_page

SECTIONS = {
    "🏠 Home": "Where the app opens: your follow-ups and what needs you today.",
    "🔎 Find": "Look things up.",
    "📚 Knowledge": "What you know that a scan can't find: business rules, projects, questions, metrics, model "
                   "inputs, SAP links and keys.",
    "📋 Review": "Work lists: what needs your attention, and what's missing or changed.",
    "🗺️ Map": "Diagrams of how code and tables connect.",
    "➕ Add": "Bring more into the catalogue beyond what `pdcat scan` reads.",
    "📤 Share": "Get what you know out to colleagues, Copilot or other tools.",
    "⚙️ Settings": "Set up and adjust the catalogue.",
}

GUIDE = {
    "Home": "Your follow-ups, five quick questions, failing checks and outputs that stopped refreshing, and what "
            "changed since your last visit.",
    "Menu guide": "This page.",
    "Search": "Find tables, columns, scripts, files, reports and notes by any word, as a table you can sort, filter "
              "by kind and group by script, family, folder or business project.",
    "Tables and datasets": "Every database table and view, and every data file and file series, as one list to "
                           "filter and sort. One click to open any of them.",
    "Python": "Every Python project, script and notebook, grouped by folder. One click to open any of them.",
    "Power BI": "Every report, model table, Power Query query and measure, each as a list to filter. One click "
                "to open any of them.",
    "SQL queries": "Every SQL query found in code, workbooks, reports and documents, with where it's used, the "
                   "tables it touches and likely mistakes.",
    "Knowledge": "Business rules, definitions, how-tos and quirks, linked to the tables and scripts they're about. "
                 "Starter packs load general knowledge to build on.",
    "Business projects": "Group the tables, scripts, files, reports and design documents that serve one purpose, "
                         "such as demand forecasting.",
    "Question library": "Questions analysts ask, with the tables and query that answer them and the caveats.",
    "Metric glossary": "What each business number means, how it's worked out, where it comes from and who owns it, "
                       "plus metrics found in code and reports to add.",
    "ML model features": "The inputs to your machine learning models: what each means, the models using it, the "
                         "script building it and the table and column it comes from.",
    "SAP links": "Tables and columns matched to SAP tables and fields, and what still uses APO.",
    "Company-wide keys": "Identifiers such as PlantID that tie data together across tables, with join hints and "
                         "checks against the master list.",
    "Abbreviations": "Short forms search understands both ways (SOH = Stock on hand), starting with common supply "
                     "chain terms. Add your own.",
    "To review": "Everything that needs your eyes, most important first, with views for data issues, items needing "
                 "notes, unmatched tables, unlinked data and duplicates.",
    "Suggestions": "Things the catalogue worked out for you to confirm: groups, descriptions, SAP meanings, joins, "
                   "naming habits, checks and Copilot's answers.",
    "Health": "How well documented the catalogue is (coverage) and where problems cluster (issue hotspots).",
    "What's new": "The latest notes, and what changed between scans.",
    "Bulk edit": "Describe a column once for everywhere it appears, or edit many items at once like a spreadsheet.",
    "Code map": "How your projects and home-built libraries connect, and which tables and files they read and "
                "write.",
    "Data flow": "Where your data comes from (APIs, scraped web sites, databases, files, cloud storage, SAP) and "
                 "the path it follows through scripts and reports to the tables and files they make.",
    "Power BI map": "Every Power BI report sized by its pages, visuals and measures, with the data behind it, and "
                    "the reports that load nothing catalogued.",
    "Database diagrams": "Tables and how they join, grouped into subject areas, with their fields if you like.",
    "Packages": "Third-party packages your code imports (lightgbm, pandas...), kept apart from your own code.",
    "Measures across reports": "Power BI measures that recur across reports: the same DAX under different names, "
                               "and the same name worked out differently.",
    "Sources": "Paste SQL, add data folders, and choose which SQL tables to measure and how gently.",
    "Schedules": "Import a list of scheduled jobs so scripts show when they run.",
    "Conda environments": "The conda environments on this machine and which projects use them.",
    "Import from Excel": "Bring in SQL queries, metrics or knowledge notes kept in a workbook.",
    "Share with colleagues": "Swap notes and knowledge with a colleague as one file, or send only your open "
                             "questions for them to answer.",
    "Copilot pack": "Write what the catalogue knows as documents a Microsoft 365 Copilot agent can use, test it, "
                    "and let it draft missing descriptions.",
    "Export": "Everything in the catalogue as CSV or JSON files, for Excel, Power BI or another tool.",
    "Settings": "Your name, backups, the weekly scheduled scan, SQL pacing and limits, and the 2.1 switches.",
    "Sensitive data": "Columns and items marked sensitive. Their values are never stored, shown or exported.",
    "Get started": "Set up step by step: your SQL Servers, Python projects and data folders, then the first scan.",
}


def menu_guide_page(cat: Catalogue) -> None:
    from .main import sections
    st.title("Menu guide")
    st.caption("What each page in the menu is for. Pick a section in the sidebar's drop-down, then a page under "
               "it, or use Open here. To open a table, script or file, use the search box or a link on any page.")
    for section, pages in sections().items():
        st.subheader(section)
        if SECTIONS.get(section):
            st.caption(SECTIONS[section])
        for page in pages:
            left, right = st.columns([6, 1], vertical_alignment="center")
            left.markdown(f"**{page}**: {GUIDE.get(page, '')}")
            right.button("Open", key=f"guide-{page}", on_click=go_page, args=(page,))
