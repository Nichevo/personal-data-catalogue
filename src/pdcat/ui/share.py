"""Getting things out of the catalogue: full exports, notes to share, the Copilot pack."""
from __future__ import annotations

import streamlit as st

from pdcat.export import to_csv_zip, to_json
from pdcat.store import Catalogue

from .common import lazy_download


def export_page(cat: Catalogue) -> None:
    st.title("Export")
    st.caption("Everything in the catalogue: tables, columns, links, notes, profiles, functions, packages and "
               "what you know about each item. Sensitive values are never included. The files are built when "
               "you click, so this page stays quick.")
    c1, c2 = st.columns(2)
    with c1:
        lazy_download("⬇️ CSV files (zip)", to_csv_zip, "catalogue-export.zip", "application/zip", "export-csv")
    with c2:
        lazy_download("⬇️ JSON", to_json, "catalogue-export.json", "application/json", "export-json")
