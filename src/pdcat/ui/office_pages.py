"""What a Power BI report, a measure or an Excel workbook shows on its page."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat.store import Catalogue

from .common import asset_button


def office_section(cat: Catalogue, key: str, asset: dict) -> None:
    {"report": report_section, "measure": measure_section, "workbook": workbook_section}[asset["kind"]](
        cat, key, asset)


def _not_read(attrs: dict) -> None:
    for msg in attrs.get("not_read", []):
        st.info(msg, icon="ℹ️")


def queries_block(attrs: dict, key: str) -> None:
    queries = [q for q in attrs.get("queries", []) if "parameter" not in q]
    params = [q for q in attrs.get("queries", []) if "parameter" in q]
    if not queries:
        return
    st.subheader(f"Queries ({len(queries)})")
    rows = []
    for q in queries:
        rows.append({"query": q["name"],
                     "server": ", ".join(q.get("servers", [])), "database": ", ".join(q.get("databases", [])),
                     "reads": ", ".join(q.get("tables", []) + q.get("files", []) + q.get("folders", [])
                                        + q.get("web", []) + q.get("odbc", [])),
                     "uses queries": ", ".join(q.get("uses", []))})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if params:
        st.caption("Parameters: " + ", ".join(f"{p['name']} = {p['parameter']}" for p in params))
    with st.expander("Query code (M)"):
        pick = st.selectbox("Query", [q["name"] for q in queries], key=f"mq-{key}")
        q = next(q for q in queries if q["name"] == pick)
        st.code(q["expression"], language="fsharp")
        for sql in q.get("sql", []):
            st.code(sql, language="sql")


def report_section(cat: Catalogue, key: str, asset: dict) -> None:
    a = asset["attrs"]
    if a.get("live"):
        st.info(f"Live connection to the published dataset **{a['live'].get('dataset') or '?'}**: its model lives in "
                "the Power BI service. Scan that dataset's .pbix or .pbip to catalogue its tables and measures.",
                icon="🔌")
    _not_read(a)
    if a.get("read_with"):
        st.caption("Read from: " + ", ".join(a["read_with"]))
    if a.get("pages"):
        pages_section(cat, key, a)
    measures_table(cat, key)
    if a.get("tables"):
        st.subheader(f"Model tables ({len(a['tables'])})")
        st.dataframe(pd.DataFrame([{"table": t["name"], "columns": len(t["columns"]),
                                    "calculated": "yes" if t.get("calculated") else "",
                                    "calculated columns": ", ".join(c["name"] for c in t["columns"] if c["calculated"])}
                                   for t in a["tables"]]), hide_index=True, width="stretch")
    if a.get("relationships"):
        with st.expander(f"Relationships ({len(a['relationships'])})"):
            st.dataframe(pd.DataFrame([{"from": r["from"], "to": r["to"], "active": "" if r.get("active", True)
                                        else "inactive"} for r in a["relationships"]]), hide_index=True,
                         width="stretch")
    queries_block(a, key)


def pages_section(cat: Catalogue, key: str, a: dict) -> None:
    from pdcat import screenshots
    from pdcat.measures import wireframe_svg
    st.subheader(f"Pages ({len(a['pages'])})")
    for pi, p in enumerate(a["pages"]):
        with st.expander(f"📄 {p['name']} · {len(p['visuals'])} visuals"):
            visuals = p["visuals"]
            has_layout = any((v.get("pos") or [0, 0, 0, 0])[2] for v in visuals)
            pick = None
            if visuals:
                labels = [f"{i + 1}. {v['title'] or v['type']}" for i, v in enumerate(visuals)]
                pick = st.selectbox("Visual", range(len(visuals)), format_func=labels.__getitem__, index=None,
                                    placeholder="Pick a visual to see its fields", key=f"vis-{key}-{pi}")
            if has_layout:
                st.markdown(wireframe_svg(p, pick), unsafe_allow_html=True)
                st.caption("A wireframe drawn from the page layout: Power BI draws the charts when the report opens, "
                           "so there's no picture of them in the file. Add a screenshot below to keep one.")
            elif visuals:
                st.caption("Rescan the report to draw a wireframe of this page.")
            if pick is not None:
                v = visuals[pick]
                st.markdown(f"**{v['title'] or v['type']}** · {v['type']}")
                ms = [f["name"] for f in v["fields"] if f["kind"] == "measure"]
                cs = [f"{f['table']}.{f['name']}" for f in v["fields"] if f["kind"] == "column"]
                if ms:
                    st.caption("Measures: " + ", ".join(ms))
                if cs:
                    st.caption("Columns: " + ", ".join(cs))
            st.dataframe(pd.DataFrame([{
                "visual": v["title"] or v["type"], "type": v["type"],
                "measures": ", ".join(f["name"] for f in v["fields"] if f["kind"] == "measure"),
                "columns": ", ".join(f"{f['table']}.{f['name']}" for f in v["fields"] if f["kind"] == "column")}
                for v in visuals]), hide_index=True, width="stretch")
            for shot in screenshots.for_page(cat, key, p["name"]):
                st.image(str(shot["path"]), caption=shot["caption"] or None)
                if st.button("Remove screenshot", key=f"shot-rm-{key}-{pi}-{shot['file']}"):
                    screenshots.remove(cat, key, p["name"], shot["file"])
                    st.rerun()
            up = st.file_uploader("Add a screenshot of this page or a visual", type=["png", "jpg", "jpeg", "gif", "webp"],
                                  key=f"shot-{key}-{pi}",
                                  help="Kept on your machine next to the catalogue; left out of share files and "
                                       "exports unless you tick them in, since screenshots can show real numbers.")
            if up is not None and st.button("Save screenshot", key=f"shot-save-{key}-{pi}"):
                screenshots.add(cat, key, p["name"], up.getvalue(), up.type or "image/png")
                st.rerun()


def measures_table(cat: Catalogue, key: str) -> None:
    from pdcat.measures import measure_rows
    rows = measure_rows(cat, key)
    if not rows:
        return
    unused = sum(1 for r in rows if not r["used"])
    st.subheader(f"Measures ({len(rows)})")
    c1, c2, c3, c4 = st.columns([1.4, 1.4, 1.2, 1.4])
    group = c1.selectbox("Group by", ["(none)", "home table", "display folder"], key=f"mgroup-{key}")
    text = c2.text_input("Filter", placeholder="name or DAX uses", key=f"mfilter-{key}")
    builds = c3.checkbox("Uses another measure", key=f"mbuild-{key}")
    show_unused = c4.toggle(f"Show {unused} not used in this file", key=f"munused-{key}") if unused else True
    shown = [r for r in rows if (show_unused or r["used"]) and (not builds or r["uses measures"])
             and (not text or text.lower() in (r["measure"] + " " + r["uses measures"] + " " + r["uses columns"]).lower())]
    if group != "(none)":
        field = "table" if group == "home table" else "folder"
        shown.sort(key=lambda r: (r[field].lower(), r["measure"].lower()))
    cols = ["measure", "table", "folder", "format", "uses measures", "uses columns", "pages", "visuals", "in glossary",
            "why"]
    if group == "home table":
        cols.remove("table")
        cols.insert(0, "table")
    elif group == "display folder":
        cols.remove("folder")
        cols.insert(0, "folder")
    if not shown:
        st.caption("Nothing matches.")
        return
    df = pd.DataFrame(shown)[cols].rename(columns={"why": "used"})
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=f"mtable-{key}")
    if unused and not show_unused:
        st.caption(f"{unused} measure(s) not used in this file are hidden. Reports connected to a published dataset "
                   "can use measures this file can't see, so they may still be in use elsewhere.")
    sel = event.selection.rows if event else []
    if sel:
        from .common import go
        go(shown[sel[0]]["key"])
        st.rerun()


def measure_section(cat: Catalogue, key: str, asset: dict) -> None:
    from pdcat.knowledge import list_metrics
    a = asset["attrs"]
    if a.get("expression"):
        st.code(a["expression"], language="sql")
    elif a.get("from_visuals_only"):
        st.caption("Seen in the report's visuals; its DAX is in the compressed model (see the report's page).")
    bits = [f"format {a['format']}" if a.get("format") else "", f"folder {a['folder']}" if a.get("folder") else ""]
    if any(bits):
        st.caption(" · ".join(b for b in bits if b))
    if a.get("used_on"):
        st.caption("Shown on pages: " + ", ".join(a["used_on"]))
    report = cat.get_asset(asset["parent"]) if asset["parent"] else None
    if report:
        asset_button(report["key"], report["name"], "report", f"mrep-{key}")
    linked = [m for m in list_metrics(cat) if m.get("from_measure") == key]
    if linked:
        for m in linked:
            st.button(f"📏 In the metric glossary as {m['name']}", key=f"mlink-{m['uid']}",
                      on_click=_go_metric, args=(m["uid"],), type="tertiary")
    else:
        with st.expander("📏 Add to the metric glossary"):
            from .knowledge_pages import metric_form
            name = a.get("name") or asset["name"]
            metric_form(cat, None, prefill={"name": name, "definition": asset["description"] or "",
                                            "formula": a.get("expression", ""), "from_measure": key,
                                            "sources": [asset["parent"]] if asset["parent"] else []})


def _go_metric(uid: str) -> None:
    from .common import go
    go(f"metric://{uid}")


def workbook_section(cat: Catalogue, key: str, asset: dict) -> None:
    a = asset["attrs"]
    _not_read(a)
    if a.get("sheets"):
        st.subheader(f"Sheets ({len(a['sheets'])})")
        st.dataframe(pd.DataFrame([{"sheet": s["name"] + (" (hidden)" if s.get("hidden") else ""),
                                    "used range": s.get("range", ""), "loaded from": ", ".join(s.get("loaded_from", [])),
                                    "header": ", ".join(s.get("header", [])[:12])} for s in a["sheets"]]),
                     hide_index=True, width="stretch")
    queries_block(a, key)
    conns = [c for c in a.get("connections", []) if c.get("type") != "Power Query"]
    if conns:
        st.subheader(f"Connections ({len(conns)})")
        for c in conns:
            with st.container(border=True):
                st.markdown(f"**{c['name']}** · {c.get('type', '')}"
                            + (" (Microsoft Query)" if c.get("type") == "ODBC" else ""))
                st.caption(" · ".join(x for x in (c.get("server"), c.get("database"), c.get("dsn") and f"DSN {c['dsn']}",
                                                  c.get("file"), c.get("url")) if x))
                if c.get("command"):
                    st.code(c["command"], language="sql")
    if a.get("pivots"):
        st.subheader(f"Pivot tables ({len(a['pivots'])})")
        st.dataframe(pd.DataFrame([{"from": p.get("source", ""), "fields": ", ".join(p["fields"])}
                                   for p in a["pivots"]]), hide_index=True, width="stretch")
    if a.get("vba_sql"):
        st.subheader(f"SQL in macros ({len(a['vba_sql'])})")
        for v in a["vba_sql"]:
            st.caption(f"Module {v['module']}")
            st.code(v["sql"], language="sql")
    elif a.get("macros"):
        st.caption("Macros: " + ", ".join(a["macros"]) + " (no SQL found in them)")
