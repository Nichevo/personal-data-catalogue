"""Settings: your name, backups, the pace of SQL Server queries, note re-checks, timings."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from pdcat import backup, pacing
from pdcat.store import Catalogue

from .common import size_label


def settings_page(cat: Catalogue) -> None:
    st.title("Settings")
    st.caption("Stored in the catalogue file itself, so they travel with it.")

    st.subheader("You")
    with st.form("settings-you"):
        author = st.text_input("Your name on notes", value=cat.setting("author", "") or "",
                               placeholder=cat.author(),
                               help="Shown on your notes, and on notes you share with colleagues.")
        if st.form_submit_button("Save"):
            cat.set_setting("author", author.strip())
            st.toast("Saved")

    backups_section(cat)

    st.subheader("SQL Server: pace and limits")
    st.caption("Every query the catalogue sends follows these. Lower them if your DBA asks; the query log on the "
               "SQL scanning page shows exactly what ran.")
    s = pacing.settings(cat)
    with st.form("settings-sql"):
        c1, c2, c3 = st.columns(3)
        pause = c1.number_input(pacing.LABELS["sql_pause_seconds"], 0.0, 600.0, float(s["sql_pause_seconds"]), 0.5)
        timeout = c2.number_input(pacing.LABELS["sql_timeout_seconds"], 5, 3600, int(s["sql_timeout_seconds"]), 5)
        cap = c3.number_input(pacing.LABELS["sql_max_tables_per_run"], 1, 10_000, int(s["sql_max_tables_per_run"]))
        c4, c5, c6 = st.columns(3)
        over = c4.number_input(pacing.LABELS["sql_sample_over_rows"], 0, 10**10, int(s["sql_sample_over_rows"]),
                               50_000)
        sample = c5.number_input(pacing.LABELS["sql_sample_rows"], 1000, 10**8, int(s["sql_sample_rows"]), 10_000)
        top = c6.number_input(pacing.LABELS["sql_top_values_rows"], 100, 10**7, int(s["sql_top_values_rows"]), 1000)
        quiet = st.text_input(pacing.LABELS["sql_quiet_hours"], value=s["sql_quiet_hours"])
        c7, c8 = st.columns(2)
        stop = c7.checkbox(pacing.LABELS["sql_stop_on_timeout"], value=bool(s["sql_stop_on_timeout"]))
        defs = c8.checkbox(pacing.LABELS["sql_scan_definitions"], value=bool(s["sql_scan_definitions"]),
                           help="Turn off to skip reading view and procedure code (fewer links between tables).")
        if st.form_submit_button("Save", type="primary"):
            for k, v in {"sql_pause_seconds": pause, "sql_timeout_seconds": int(timeout),
                         "sql_max_tables_per_run": int(cap), "sql_sample_over_rows": int(over),
                         "sql_sample_rows": int(sample), "sql_top_values_rows": int(top),
                         "sql_quiet_hours": quiet.strip(), "sql_stop_on_timeout": stop,
                         "sql_scan_definitions": defs}.items():
                cat.set_setting(k, v)
            st.toast("Saved")

    st.subheader("Data files")
    group = st.checkbox("Group files that differ only by a date into one dated series", 
                        value=bool(cat.setting("series_grouping", True)),
                        help="sales_2026-01.csv, sales_2026-02.csv… become one entry. Takes effect on the next scan.")
    if group != bool(cat.setting("series_grouping", True)):
        cat.set_setting("series_grouping", group)
        st.rerun()
    split = cat.setting("series_split", [])
    if split:
        with st.expander(f"Series you split ({len(split)})"):
            for k in split:
                c1, c2 = st.columns([4, 1])
                c1.caption(k)
                if c2.button("Group again", key=f"regroup-{k}"):
                    cat.set_setting("series_split", [x for x in split if x != k])
                    st.rerun()

    st.subheader("Notes")
    with st.form("settings-notes"):
        days = st.number_input("Ask me to re-check notes older than (days, 0 = never)", 0, 3650,
                               int(cat.setting("note_recheck_days", 0) or 0), 30,
                               help="Notes are always flagged when what they describe changes. This also flags "
                                    "notes nobody has confirmed for a while.")
        if st.form_submit_button("Save"):
            cat.set_setting("note_recheck_days", int(days))
            st.toast("Saved")

    st.subheader("App")
    show = st.checkbox("Show how long each page takes (in the sidebar)", value=bool(cat.setting("show_timings", False)))
    if show != bool(cat.setting("show_timings", False)):
        cat.set_setting("show_timings", show, quiet=True)
        st.rerun()
    upgraded = cat.setting("upgrade_backup")
    if upgraded:
        st.caption(f"Upgraded from version 1. The untouched copy made before the upgrade: `{upgraded}`")
    st.caption(f"Catalogue file: `{cat.path}`")


def backups_section(cat: Catalogue) -> None:
    st.subheader("Backups")
    s = backup.settings(cat)
    if not s["backup_folder"]:
        st.caption("Off. Choose a folder to turn them on, for example one that OneDrive keeps in sync.")
    with st.form("settings-backup"):
        folder = st.text_input("Backup folder", value=s["backup_folder"],
                               placeholder=r"C:\Users\you\OneDrive\catalogue-backups",
                               help="Leave empty to turn backups off.")
        c1, c2 = st.columns(2)
        every = c1.selectbox("How often", list(backup.FREQUENCIES),
                             index=list(backup.FREQUENCIES).index(s["backup_every"])
                             if s["backup_every"] in backup.FREQUENCIES else 1,
                             format_func={"app open": "every time the app opens", "daily": "daily",
                                          "weekly": "weekly"}.get)
        keep = c2.number_input("Copies to keep", 1, 1000, int(s["backup_keep"]))
        if st.form_submit_button("Save", type="primary"):
            folder = folder.strip().strip('"')
            if folder and not Path(folder).expanduser().parent.exists():
                st.error(f"Can't find {Path(folder).parent}")
            else:
                cat.set_setting("backup_folder", folder)
                cat.set_setting("backup_every", every)
                cat.set_setting("backup_keep", int(keep))
                st.toast("Saved")
                st.rerun()
    if not s["backup_folder"]:
        return
    c1, c2 = st.columns([1, 3])
    if c1.button("Back up now"):
        try:
            path = backup.backup_now(cat)
            st.toast(f"Backed up to {path.name}")
        except (OSError, ValueError) as e:
            st.error(f"Backup failed: {e}")
    last = cat.setting("last_backup_at")
    c2.caption(f"Last backup {last[:16].replace('T', ' ')} UTC" if last else "No backup yet.")
    copies = backup.list_backups(s["backup_folder"])
    if copies:
        with st.expander(f"Restore from a backup ({len(copies)})"):
            st.dataframe(pd.DataFrame([{"file": c["file"], "made": c["made"], "size": size_label(c["size"]),
                                        "items": c.get("items"), "notes": c.get("notes")} for c in copies]),
                         hide_index=True, width="stretch")
            pick = st.selectbox("Backup to restore", [c["file"] for c in copies])
            sure = st.checkbox("Replace everything in the catalogue with this backup. The current contents are "
                               "saved first, so this can be undone.")
            if st.button("Restore", disabled=not sure, type="primary"):
                chosen = next(c for c in copies if c["file"] == pick)
                safety = backup.restore(cat, chosen["path"])
                st.success(f"Restored {pick}. What was there before is saved as {safety.name}.")
