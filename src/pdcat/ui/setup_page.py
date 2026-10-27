"""Get started: everything needed to set the catalogue up, without editing files."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from pdcat import firstrun
from pdcat.config import find_config, load_config
from pdcat.store import Catalogue

from .common import config_path


def _config():
    from pdcat.config import Config
    path = find_config(config_path())
    if not path.exists():  # created when you first save something here
        return path, Config(path=path, db_path=Path(), sqlserver=[], python=[]), None
    try:
        return path, load_config(path), None
    except Exception as e:
        return path, None, f"{type(e).__name__}: {e}"


def setup_page(cat: Catalogue) -> None:
    st.title("Get started")
    st.caption("Set up your catalogue in a few steps. Everything here is saved on your computer only.")
    path, cfg, err = _config()
    if err:
        st.error(f"Your settings file has a problem: {err}. File: `{path}`")
        return

    st.subheader("1. Your name")
    with st.form("setup-you"):
        author = st.text_input("Shown on your notes", value=cat.setting("author", "") or "", placeholder=cat.author())
        if st.form_submit_button("Save"):
            cat.set_setting("author", author.strip())
            st.toast("Saved")

    st.subheader("2. SQL Server")
    odbc = firstrun.odbc_drivers()
    if odbc["drivers"]:
        st.success(f"SQL Server driver found: {odbc['drivers'][0]}", icon="✅")
    else:
        st.warning("No SQL Server ODBC driver is installed, so SQL Server can't be scanned yet (files, Python "
                   f"projects and reports still work). Install **ODBC Driver 18 for SQL Server** from "
                   f"[Microsoft]({firstrun.DRIVER_URL}); your IT team may need to do it."
                   + (f"  \nDetail: {odbc['error']}" if odbc["error"] else ""), icon="⚠️")
    for s in cfg.sqlserver:
        st.caption(f"🛢️ **{s.name}**: {s.server} · {', '.join(s.databases)}")
    with st.expander("➕ Add a SQL Server", expanded=not cfg.sqlserver):
        with st.form("setup-sql"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Short name", placeholder="warehouse")
            server = c2.text_input("Server", placeholder=r"SERVER\INSTANCE")
            dbs = st.text_input("Databases (comma separated)", placeholder="SalesDB, ForecastDB")
            drivers = odbc["drivers"] or ["ODBC Driver 18 for SQL Server"]
            c3, c4 = st.columns(2)
            driver = c3.selectbox("Driver", drivers)
            trust = c4.checkbox("Trust the server's certificate", value=True,
                                help="Usually needed for servers inside the company network.")
            test, save = st.columns(2)
            do_test = test.form_submit_button("Test the connection")
            do_save = save.form_submit_button("Save", type="primary")
            databases = [d for d in dbs.split(",") if d.strip()]
            if do_test:
                if not (server.strip() and databases):
                    st.error("Give the server and a database to test.")
                else:
                    problem = firstrun.test_connection(server.strip(), databases[0].strip(), driver, trust)
                    st.success("Connected.") if problem is None else st.error(f"Couldn't connect: {problem}")
            if do_save:
                try:
                    firstrun.add_sqlserver(firstrun.ensure_config(path), name, server, databases, driver, trust)
                    st.toast(f"Added {name}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    if cfg.sqlserver:
        st.caption("Scan from the **SQL scanning** page (Add section): queries go one at a time, at the pace set "
                   "in Settings.")

    st.subheader("3. Python projects")
    for p in cfg.python:
        st.caption(f"{'📚' if p.role == 'library' else '📁'} **{p.name}**: `{p.path}`")
    with st.expander("➕ Add a Python project folder", expanded=not cfg.python):
        with st.form("setup-py"):
            c1, c2 = st.columns([1, 2])
            name = c1.text_input("Short name", placeholder="forecasting", key="py-name")
            folder = c2.text_input("Folder", placeholder=r"C:\Users\you\code\forecasting")
            dbopts = [""] + [f"{s.name}/{d}" for s in cfg.sqlserver for d in s.databases]
            default_db = st.selectbox("Database its SQL usually means (optional)", dbopts)
            role = st.radio("It is", ["project", "library"], horizontal=True,
                            format_func={"project": "a project", "library": "a library other projects import"}.get)
            if st.form_submit_button("Save", type="primary"):
                try:
                    firstrun.add_python(firstrun.ensure_config(path), name, folder, default_db, role)
                    st.toast(f"Added {name}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    if cfg.python:
        if st.button("Scan Python projects now"):
            from pdcat.postscan import after_scan
            from pdcat.scan_python import scan_project
            with st.spinner("Scanning…"):
                for p in cfg.python:
                    try:
                        scan_project(cat, p)
                    except Exception as e:
                        st.error(f"{p.name}: {type(e).__name__}: {e}")
                after_scan(cat)
            st.toast("Scanned")
            st.rerun()

    st.subheader("4. Data files, reports and workbooks")
    st.caption("Add folders of CSV, Parquet, Power BI and Excel files on the **Data folders** page (Add section).")
    st.subheader("5. Backups")
    st.caption("Off until you choose a folder on the **Settings** page, for example one OneDrive keeps in sync.")
    st.caption(f"Settings file: `{path}` · catalogue: `{Path(cat.path)}`")
