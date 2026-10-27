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
        st.subheader(f"Pages ({len(a['pages'])})")
        for p in a["pages"]:
            with st.expander(f"📄 {p['name']} · {len(p['visuals'])} visuals"):
                st.dataframe(pd.DataFrame([{
                    "visual": v["title"] or v["type"], "type": v["type"],
                    "measures": ", ".join(f["name"] for f in v["fields"] if f["kind"] == "measure"),
                    "columns": ", ".join(f"{f['table']}.{f['name']}" for f in v["fields"] if f["kind"] == "column")}
                    for v in p["visuals"]]), hide_index=True, width="stretch")
    measures = [c for c in cat.children(key) if c["kind"] == "measure"]
    if measures:
        st.subheader(f"Measures ({len(measures)})")
        for i, m in enumerate(measures):
            asset_button(m["key"], m["name"], "measure", f"meas-{key}-{i}", help=m["description"] or None)
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
