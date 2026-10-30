"""Power BI impact on the pages (2.3): which model a thin report uses, the reports connected to a model, what a
measure depends on and what needs it, column usage on a model table, and the "+N not used in scanned files"
switch the lists share."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import pbimpact as PI
from pdcat.store import Catalogue

from .common import asset_button, cached, go


@cached
def _matches(cat: Catalogue):
    return PI.model_matches(cat)


@cached
def _usage(cat: Catalogue, model_key: str):
    return PI.usage(cat, model_key)


@cached
def _unused(cat: Catalogue, what: str):
    return PI.unused_keys(cat, what)


@cached
def _chain(cat: Catalogue, measure_key: str):
    return PI.measure_chain(cat, measure_key)


def model_box(cat: Catalogue, key: str, asset: dict) -> None:
    """On a thin report: the model it uses, how it was matched, and a way to change it."""
    a = asset["attrs"]
    if not a.get("live") or a.get("tables"):
        return
    m = _matches(cat).get(key) or {"model": None, "how": "", "candidates": [], "dataset": ""}
    dataset = m.get("dataset") or a["live"].get("dataset") or "?"
    with st.container(border=True):
        model = cat.get_asset(m["model"]) if m.get("model") else None
        how = {"manual": "picked by hand", "register": "from the report register's dataset column",
               "name": "the dataset has the same name", "words": "the dataset has the same name apart from "
               "version marks"}.get(m.get("how") or "", "")
        if model:
            st.markdown(f"🔌 **Live connection to the dataset {dataset}** · model read from: ")
            asset_button(model["key"], model["name"], "report", f"model-{key}", help=f"Matched: {how}" if how else None)
            if how:
                st.caption(f"Matched because {how}. Measures and columns this report shows count as used in that "
                           "model, and its visuals are checked against that model's fields.")
        else:
            st.markdown(f"🔌 **Live connection to the dataset {dataset}** · its model lives in the Power BI service. "
                        "Scan the .pbix or .pbip that holds that model and pick it here, or by name it will match "
                        "itself.")
        thick = cat.conn.execute("SELECT key, name, source FROM assets WHERE kind = 'report' AND status = 'active' "
                                 "AND attrs LIKE '%\"tables\": [{%' ORDER BY name").fetchall()
        if thick:
            options = ["(none)"] + [r["key"] for r in thick]
            labels = {r["key"]: f"{r['name']} ({r['source']})" for r in thick}
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            pick = c1.selectbox("Model file", options, index=options.index(m["model"]) if m.get("model") in options
                                else 0, format_func=lambda k: labels.get(k, k), key=f"model-pick-{key}")
            if c2.button("Save", key=f"model-save-{key}"):
                PI.set_model(cat, key, None if pick == "(none)" else pick)
                st.rerun()
            if m.get("how") == "manual" and st.button("Back to matching by name", key=f"model-auto-{key}"):
                PI.forget_model(cat, key)
                st.rerun()


def connected_box(cat: Catalogue, key: str, asset: dict) -> None:
    """On a report with a model: the thin reports connected to it, and what is not used in scanned files."""
    if not asset["attrs"].get("tables"):
        return
    conn = PI.connected_reports(cat, key)
    u = _usage(cat, key)
    unused_m = [lab for lab, m in u["measures"].items() if not m["used"]]
    unused_c = [lab for lab, c in u["columns"].items() if not c["used"]]
    with st.container(border=True):
        if conn:
            st.markdown(f"🔗 **Connected reports ({len(conn)})** · thin reports whose live connection uses this model")
            cols = st.columns(min(len(conn), 4))
            for i, c in enumerate(conn[:8]):
                cols[i % len(cols)].button(f"📊 {c['name']}", key=f"conn-{key}-{i}", on_click=go, args=(c["key"],),
                                           type="tertiary")
        else:
            st.markdown("🔗 **Connected reports:** none scanned. A thin report with a live connection to this "
                        "model's dataset will show here once scanned.")
        used_m = len(u["measures"]) - len(unused_m)
        used_c = len(u["columns"]) - len(unused_c)
        st.caption(f"Across this file{' and its connected reports' if conn else ''}: {used_m} of {len(u['measures'])} "
                   f"measures and {used_c} of {len(u['columns'])} columns are used. "
                   f"{len(unused_m)} measures and {len(unused_c)} columns are {PI.NOT_USED}: a report that wasn't "
                   "scanned may still use them, so they are kept and searchable.")
        if unused_c and st.toggle(f"+{len(unused_c)} columns {PI.NOT_USED}", key=f"unusedc-{key}"):
            st.dataframe(pd.DataFrame([{"column": lab, "calculated": "yes" if u["columns"][lab]["calculated"] else ""}
                                       for lab in unused_c]), hide_index=True, width="stretch")


def measure_chain_box(cat: Catalogue, key: str) -> None:
    chain = _chain(cat, key)
    if chain is None:
        return
    with st.container(border=True):
        st.markdown(f"🧮 **{'Used' if chain['used'] else PI.NOT_USED.capitalize()}** · {chain['why']}")
        if chain["depends_on"]:
            st.markdown("**Depends on** (every step):")
            rows = [{"step": d["depth"], "what": d["label"], "kind": d["kind"], "via": d["via"]}
                    for d in chain["depends_on"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        if chain["needed_by"]:
            st.markdown("**Needed by:** " + ", ".join(f"{n['label']} ({n['kind']})" for n in chain["needed_by"][:12])
                        + (" …" if len(chain["needed_by"]) > 12 else ""))
            for i, n in enumerate([x for x in chain["needed_by"] if x.get("key")][:6]):
                asset_button(n["key"], n["label"], "measure", f"chain-{key}-{i}")
        if chain["affected"]:
            st.markdown(f"**If this changes:** {len(chain['affected'])} visual(s) in "
                        f"{len({a[0] for a in chain['affected']})} report(s)")
            st.dataframe(pd.DataFrame([{"report": r, "page": p, "visual": v} for r, p, v in chain["affected"]]),
                         hide_index=True, width="stretch")
        elif not chain["needed_by"]:
            st.caption(f"No scanned visual shows it and no other measure builds on it ({PI.NOT_USED}).")


def column_usage_frame(cat: Catalogue, model_key: str, table: str, columns: list[dict]) -> pd.DataFrame:
    """The columns table for a model table page, with what uses each column."""
    u = _usage(cat, model_key)
    rows = []
    for c in columns:
        info = u["columns"].get(f"{table}[{c['name']}]") or {}
        rows.append({"column": c["name"], "type": c["data_type"],
                     "calculated": c["expression"] or ("yes" if c["calculated"] else ""),
                     "used by": info.get("why", ""), "visuals": len(info.get("visuals") or []),
                     "in measures": ", ".join((info.get("in_measures") or [])[:5])})
    return pd.DataFrame(rows)


def unused_switch(cat: Catalogue, what: str, widget: str) -> set[str]:
    """A "+N not used in scanned files" toggle; returns the keys to hide (empty when showing everything)."""
    hide = _unused(cat, what)
    if not hide:
        return set()
    shown = st.toggle(f"+{len(hide)} {PI.NOT_USED}", value=PI.show_unused(cat), key=widget,
                      help="Nothing scanned reads or shows these. They stay in the catalogue and in search, and "
                           "come back here when a later scan finds a use.")
    if shown != PI.show_unused(cat):
        PI.set_show_unused(cat, shown)
    return set() if shown else hide
