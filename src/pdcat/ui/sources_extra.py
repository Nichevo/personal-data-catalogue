"""Version 2.1 source pages: the schedule list, installed conda environments, Access databases, and the
schedule and git boxes on script pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import condaenvs, schedules
from pdcat.store import Catalogue

from .common import asset_button, go


# ---- schedules -------------------------------------------------------------------

def schedules_page(cat: Catalogue) -> None:
    st.title("Schedules")
    st.caption("Bring in a CSV or Excel workbook listing scheduled jobs (a Task Scheduler export, a job list or a "
               "sheet you keep). "
               "Each job is matched to the scripts it runs; script pages then show when they run. In a terminal, "
               "`schtasks /query /fo csv /v > tasks.csv` exports Windows Task Scheduler.")
    up = st.file_uploader("Schedule list (CSV or Excel)", type=["csv", "txt", "xlsx", "xlsm"], key="sched-up",
                          help="An old .xls workbook: open it in Excel and save it as .xlsx first.")
    if up is not None:
        if up.name.lower().endswith((".xlsx", ".xlsm")):
            from pdcat.excelimport import sheet_names
            try:
                sheets = sheet_names(up.getvalue())
            except Exception as e:  # a damaged or password-protected workbook
                st.error(f"Couldn't read that workbook ({type(e).__name__}). Save it again from Excel, or as CSV.")
                return
            best = next((i for i, n in enumerate(sheets) if "sched" in n.lower()), 0)
            sheet = st.selectbox("Sheet", sheets, index=best, key="sched-sheet") if len(sheets) > 1 else sheets[0]
            cols, rows = schedules.read_sheet(up.getvalue(), sheet)
        else:
            cols, rows = schedules.read_csv(up.getvalue())
        if not rows:
            st.warning("No rows found in that file.")
        else:
            guess = schedules.guess_columns(cols)
            st.caption(f"{len(rows)} rows, {len(cols)} columns. Say which columns hold what.")
            opts = ["(none)", *cols]
            with st.form("sched-map"):
                c1, c2 = st.columns(2)
                name = c1.selectbox("Job name", opts, index=opts.index(guess["name"]) if guess["name"] in opts else 0)
                cmd = c2.selectbox("Command or script it runs", opts,
                                   index=opts.index(guess["command"]) if guess["command"] in opts else 0)
                timing = st.multiselect("When it runs (one or more columns)", cols,
                                        default=[c.strip() for c in (guess["timing"] or "").split(",") if c.strip()
                                                 and c.strip() in cols])
                st.dataframe(pd.DataFrame(rows[:5]), hide_index=True, width="stretch")
                if st.form_submit_button(f"Import {len(rows)} jobs", type="primary"):
                    n = schedules.import_rows(cat, rows, {"name": None if name == "(none)" else name,
                                                          "command": None if cmd == "(none)" else cmd,
                                                          "timing": ", ".join(timing)}, up.name)
                    st.toast(f"Imported {n} jobs")
                    st.rerun()
    jobs = schedules.jobs(cat)
    if not jobs:
        return
    linked = [j for j in jobs if any(l["state"] == "confirmed" for l in j["links"])]
    likely = [j for j in jobs if j not in linked and j["links"]]
    none = [j for j in jobs if not j["links"]]
    tabs = st.tabs([f"Linked ({len(linked)})", f"To confirm ({len(likely)})", f"Unmatched ({len(none)})"])
    scripts = [dict(r) for r in cat.conn.execute(
        "SELECT key, name, source FROM assets WHERE kind IN ('script', 'notebook', 'sql_script', 'project') "
        "AND status = 'active' ORDER BY source, name")]
    labels = {s["key"]: f"{s['source']} / {s['name']}" for s in scripts}
    for tab, group in zip(tabs, (linked, likely, none)):
        with tab:
            for j in group:
                with st.container(border=True):
                    c1, c2 = st.columns([5, 1])
                    c1.markdown(f"**{j['name']}**" + (f" · {j['timing']}" if j["timing"] else ""))
                    c1.caption(f"`{(j['command'] or '')[:200]}`")
                    if c2.button("Delete", key=f"sjdel-{j['uid']}"):
                        schedules.delete_job(cat, j["uid"])
                        st.rerun()
                    for l in j["links"]:
                        a, b, c = st.columns([4, 3, 1.4])
                        with a:
                            asset_button(l["asset_key"], l["name"] or l["asset_key"], l["kind"] or "script",
                                         f"sjl-{j['uid']}-{l['asset_key']}")
                        b.caption(("✓ " if l["state"] == "confirmed" else "? ") + (l["how"] or ""))
                        with c:
                            x, y = st.columns(2)
                            if l["state"] != "confirmed" and x.button("✓", key=f"sjok-{j['uid']}-{l['asset_key']}"):
                                schedules.decide(cat, j["uid"], l["asset_key"], "confirmed")
                                st.rerun()
                            if y.button("✗", key=f"sjno-{j['uid']}-{l['asset_key']}", help="Not this script"):
                                schedules.decide(cat, j["uid"], l["asset_key"], "rejected")
                                st.rerun()
                    pick = st.selectbox("Link a script by hand", list(labels), format_func=labels.get, index=None,
                                        key=f"sjpick-{j['uid']}", placeholder="Link a script by hand…",
                                        label_visibility="collapsed")
                    if pick:
                        schedules.decide(cat, j["uid"], pick, "confirmed")
                        st.rerun()


def script_runs_box(cat: Catalogue, key: str) -> None:
    from pdcat.gitinfo import history
    jobs = schedules.schedules_for(cat, key)
    for j in jobs:
        st.info(f"⏰ Runs on a schedule: **{j['name']}**" + (f" · {j['timing']}" if j["timing"] else ""), icon="⏰")
    commits = history(cat, key)
    if commits:
        last = commits[0]
        st.caption(f"🕘 Last changed {last['at'][:10]} by {last['author']}: {last['message']}")
        with st.expander(f"Change log from git ({len(commits)})"):
            st.dataframe(pd.DataFrame([{"when": c["at"][:16].replace("T", " "), "who": c["author"],
                                        "message": c["message"], "commit": c["commit_id"][:8]} for c in commits]),
                         hide_index=True, width="stretch")


# ---- conda environments --------------------------------------------------------------

def envs_page(cat: Catalogue) -> None:
    st.title("Conda environments")
    st.caption("The conda environments installed on this machine, read from their conda-meta folders (conda "
               "isn't run). Each lists every package with its exact version and channel, including ones pip "
               "installed. Link a project to the environment it runs in from the project's page.")
    c1, c2 = st.columns([1, 3])
    if c1.button("Read environments now", type="primary"):
        with st.spinner("Reading conda-meta folders …"):
            n = condaenvs.scan_envs(cat)
        st.toast(f"Found {n} environment(s)")
        st.rerun()
    with c2.popover("Look somewhere else too"):
        extra = st.text_area("Other conda folders (one per line)", value="\n".join(cat.setting("conda_paths", [])),
                             help=r"For example D:\tools\miniconda3. Its envs folder is read too.")
        if st.button("Save", key="conda-paths-save"):
            cat.set_setting("conda_paths", [l.strip().strip('"') for l in extra.splitlines() if l.strip()])
            st.toast("Saved")
    rows = condaenvs.envs(cat)
    if not rows:
        st.info("None read yet. Press the button, or they're read with every scan.")
        return
    for e in rows:
        c1, c2 = st.columns([3, 3])
        with c1:
            st.button(f"🐍 {e['name']}", key=f"env-{e['name']}", on_click=go, args=(f"env://{e['name']}",),
                      type="tertiary")
        c2.caption(f"Python {e['python'] or '?'} · {len(e['packages'])} packages · `{e['path']}`")


def env_page(cat: Catalogue, key: str) -> None:
    name = key.removeprefix("env://")
    e = condaenvs.get_env(cat, name)
    if e is None:
        st.warning("This environment isn't catalogued any more.")
        return
    st.title(f"🐍 {e['name']}")
    st.caption(f"conda environment · Python {e['python'] or '?'} · {len(e['packages'])} packages · `{e['path']}` · "
               f"read {(e['scanned_at'] or '')[:16].replace('T', ' ')} UTC")
    users = [r[0] for r in cat.conn.execute("SELECT project_key FROM project_envs WHERE env_name = ?", (name,))]
    if users:
        st.markdown("**Projects that run in it:**")
        for i, u in enumerate(users):
            asset_button(u, u.removeprefix("py://"), "project", f"envp-{i}")
    q = st.text_input("Filter packages", key=f"envq-{name}")
    df = pd.DataFrame(e["packages"])
    if q and not df.empty:
        df = df[df["name"].str.contains(q, case=False)]
    st.dataframe(df, hide_index=True, width="stretch")


def project_env_box(cat: Catalogue, project_key: str) -> None:
    rows = condaenvs.envs(cat)
    if not rows:
        return
    names = [e["name"] for e in rows]
    current = condaenvs.project_env(cat, project_key)
    st.markdown("**Installed environment**")
    c1, c2 = st.columns([2, 3])
    opts = ["(not linked)", *names]
    pick = c1.selectbox("Runs in", opts, index=opts.index(current["env_name"]) if current and current["env_name"] in opts
                        else 0, key=f"penv-{project_key}", label_visibility="collapsed")
    if (pick if pick != opts[0] else None) != (current or {}).get("env_name"):
        condaenvs.set_project_env(cat, project_key, None if pick == opts[0] else pick)
        st.rerun()
    if current:
        c2.caption(f"Linked {'by you' if current['how'] == 'you' else 'from ' + current['how']}")
        diff = condaenvs.compare_installed(cat, project_key, current["env_name"])
        bad = [d for d in diff if d["status"] != "ok"]
        st.caption(f"{len(diff)} packages compared: {len(bad)} need a look." if diff else "Nothing to compare.")
        if diff:
            st.dataframe(pd.DataFrame(sorted(diff, key=lambda d: d["status"] == "ok")), hide_index=True,
                         width="stretch")


# ---- Access databases ------------------------------------------------------------------

def access_section(cat: Catalogue, key: str, asset: dict) -> None:
    a = asset["attrs"]
    if a.get("read_error"):
        st.warning(f"Couldn't read inside this file: {a['read_error']}")
    if a.get("queries"):
        st.subheader(f"Saved queries ({len(a['queries'])})")
        for q in a["queries"]:
            with st.expander(q["name"]):
                st.code(q["sql"], language="sql")
    if a.get("linked"):
        st.subheader(f"Linked tables ({len(a['linked'])})")
        st.dataframe(pd.DataFrame([{"table": l["name"], "points to": l["source_table"],
                                    "connection": l["connect"][:200], "ODBC": l["odbc"]} for l in a["linked"]]),
                     hide_index=True, width="stretch")
    if a.get("tables"):
        st.subheader(f"Local tables ({len(a['tables'])})")
        st.dataframe(pd.DataFrame([{"table": t["name"], "rows": t["rows"], "columns": len(t["columns"])}
                                   for t in a["tables"]]), hide_index=True, width="stretch")


def excel_import_page(cat: Catalogue) -> None:
    import pandas as pd

    from pdcat import excelimport as X
    st.title("Import from Excel")
    st.caption("Bring in SQL queries, metrics or knowledge notes kept in a workbook (or a CSV). Pick the sheet, say "
               "what each row is, match the columns, check the preview, then import. Each item remembers the "
               "workbook it came from.")
    up = st.file_uploader("Workbook", type=["xlsx", "xlsm", "csv"], key="xi-file")
    if up is None:
        return
    data = up.getvalue()
    try:
        sheets = X.sheet_names(data, up.name)
    except Exception as e:
        st.error(f"Couldn't open the workbook: {type(e).__name__}: {e}")
        return
    sheet = st.selectbox("Sheet", sheets, key="xi-sheet")
    try:
        rows = X.read_sheet(data, sheet, up.name)
    except Exception as e:
        st.error(f"Couldn't read that sheet: {type(e).__name__}: {e}")
        return
    if not rows:
        st.info("That sheet is empty.")
        return
    mode = st.radio("What's on the sheet", ["One item per row", "Free text: import the whole sheet as one note"],
                    horizontal=True, key="xi-mode")
    if mode.startswith("Free"):
        title = st.text_input("Note title", value=f"{up.name.rsplit('.', 1)[0]}: {sheet}", key="xi-title")
        st.text("\n".join(" · ".join(c for c in r if c.strip()) for r in rows[:20]))
        if st.button("Import as one knowledge note", type="primary", key="xi-whole"):
            uid = X.import_whole_sheet(cat, rows, title, up.name, sheet)
            go(f"kb://{uid}")
            st.rerun()
        return
    c1, c2 = st.columns(2)
    row_type = c1.selectbox("Each row is", list(X.ROW_TYPES), format_func=lambda t: X.ROW_TYPES[t][0], key="xi-type")
    header_row = c2.number_input("Header row", min_value=1, max_value=min(len(rows), 20), value=1, key="xi-header") - 1
    columns, records = X.as_records(rows, int(header_row))
    guess = X.guess_mapping(row_type, columns)
    st.markdown("**Match columns to fields**")
    mapping = {}
    cols = st.columns(3)
    for i, field in enumerate(X.ROW_TYPES[row_type][1]):
        opts = ["(none)", *columns]
        cur = guess.get(field)
        pick = cols[i % 3].selectbox(field + (" *" if field in X.REQUIRED[row_type] else ""), opts,
                                     index=opts.index(cur) if cur in opts else 0, key=f"xi-map-{row_type}-{field}")
        mapping[field] = None if pick == "(none)" else pick
    items = X.preview(row_type, records, mapping)
    st.caption(f"{len(items)} of {len(records)} rows have the required fields (marked *).")
    if items:
        st.dataframe(pd.DataFrame(items[:50]), hide_index=True, width="stretch")
        if st.button(f"Import {len(items)} {X.ROW_TYPES[row_type][0].split(' (')[0].lower()}(s)", type="primary",
                     key="xi-go"):
            n = X.import_rows(cat, row_type, items, up.name, sheet)
            st.success(f"Imported {n}.")
