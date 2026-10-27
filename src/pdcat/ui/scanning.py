"""Asking for SQL tables to be profiled, at a pace you control."""
from __future__ import annotations

import streamlit as st

from pdcat.store import Catalogue


def profile_request_button(cat: Catalogue, key: str) -> None:
    from pdcat.pacing import queue_state, request_profiles
    state = queue_state(cat, key)
    if state in ("queued", "approved"):
        st.caption(f"⏳ Waiting to be profiled ({state}). Run `pdcat profile --queue` or use the SQL scanning page.")
        return
    if st.button("📊 Profile this table", key=f"askprof-{key}",
                 help="Adds it to the queue. Nothing is sent to the server until you run the queue."):
        request_profiles(cat, [key])
        st.rerun()


def _sources(cat: Catalogue):
    from pdcat.config import find_config, load_config
    from .common import config_path
    try:
        return load_config(find_config(config_path())).sqlserver
    except Exception:
        return []


def scanning_page(cat: Catalogue) -> None:
    import pandas as pd

    from pdcat import pacing
    from pdcat.export import rows_to_csv

    st.title("SQL scanning")
    s = pacing.settings(cat)
    st.caption(f"Tables are only profiled when you ask, and queries go one at a time: {s['sql_pause_seconds']}s "
               f"apart, {s['sql_timeout_seconds']}s limit each, at most {s['sql_max_tables_per_run']} tables per run, "
               f"tables over {int(s['sql_sample_over_rows']):,} rows sampled"
               + (f", nothing during {s['sql_quiet_hours']}" if s["sql_quiet_hours"] else "")
               + ". Change these on the Settings page.")
    if pacing.in_quiet_hours(s["sql_quiet_hours"]):
        st.info(f"It's quiet hours ({s['sql_quiet_hours']}): runs won't send anything until they end.")
    last_stop = cat.setting("sql_last_stop")
    if last_stop:
        st.warning(f"Last run stopped: {last_stop}")

    c1, c2 = st.columns(2)
    if c1.button("➕ Queue every table my code uses",
                 help="Tables and views your scripts, pasted SQL, reports and workbooks read or write."):
        n = pacing.request_profiles(cat, pacing.used_tables(cat))
        st.toast(f"Queued {n} table(s)")
    q = pacing.queue(cat)
    if not q:
        st.info("Nothing waiting. Use \"Profile this table\" on a table's page, or the button above.")
    else:
        df = pd.DataFrame([{"table": r["name"] or r["asset_key"], "database": r["database"], "rows": r["row_count"],
                            "state": r["state"], "error": r["error"] or ""} for r in q])
        st.caption("Tick rows, then approve, skip or remove them. Only approved tables are profiled.")
        event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
                             key="queue-table")
        picked = [q[i]["asset_key"] for i in (event.selection.rows if event else [])]
        b1, b2, b3, b4 = st.columns(4)
        if b1.button(f"✓ Approve {'selected' if picked else 'all'}"):
            pacing.set_queue_state(cat, picked or [r["asset_key"] for r in q if r["state"] in ("queued", "failed")],
                                   "approved")
            st.rerun()
        if b2.button("Skip selected", disabled=not picked):
            pacing.set_queue_state(cat, picked, "skipped")
            st.rerun()
        approved = [r["asset_key"] for r in q if r["state"] == "approved"]
        with b3.popover("👁️ Show the queries"):
            plan = pacing.plan_profile(cat, approved or [r["asset_key"] for r in q])
            for p in plan[:40]:
                st.caption(f"{p['table']} · {p['purpose']}")
                st.code(p["query"], language="sql")
        sources = _sources(cat)
        run = b4.button(f"▶️ Run {len(approved)} approved", type="primary", disabled=not approved or not sources,
                        help=None if sources else "Start the app with `pdcat ui` so it can read your config.")
        if run:
            from pdcat.scan_sqlserver import run_queue
            cat.set_setting("sql_last_stop", None, quiet=True)
            box = st.status("Profiling…", expanded=True)
            st.button("⏹️ Stop", key="stop-run", help="Stops after the current query. Run again to carry on.")
            result = run_queue(cat, sources, progress=box.write)
            if result["stopped"]:
                cat.set_setting("sql_last_stop", result["stopped"], quiet=True)
            box.update(label=f"Profiled {result['done']} table(s), {result['failed']} failed, {result['left']} left",
                       state="complete" if not result["stopped"] else "error")

    with st.expander("Query log: every query sent to a server"):
        log = pacing.query_log(cat)
        if not log:
            st.caption("Nothing sent yet.")
        else:
            st.dataframe(pd.DataFrame([{k: r[k] for k in ("at", "source", "database", "purpose", "ms", "rows",
                                                          "status", "error")} for r in log]),
                         hide_index=True, width="stretch")
            st.download_button("⬇️ Query log (.csv)", rows_to_csv(log), "query-log.csv", "text/csv")
