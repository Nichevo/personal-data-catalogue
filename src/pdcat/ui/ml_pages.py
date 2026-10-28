"""ML model features: the inputs to your machine learning models, and the models found."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import mlfeatures as M
from pdcat.store import Catalogue

from .common import asset_button, first_line, go

KNOWN = ["unknown", "yes", "no"]
STATUS = ["", "in use", "experimental", "retired"]


def _projects(cat: Catalogue) -> list[str]:
    return [r[0] for r in cat.conn.execute("SELECT DISTINCT source FROM assets WHERE kind = 'project' ORDER BY 1")]


def features_page(cat: Catalogue) -> None:
    st.title("ML model features")
    st.caption("The inputs to your machine learning models. For each one: what it means, the table and column "
               "it comes from, the script that builds it, whether it's known at prediction time, and which models "
               "use it. Suggestions come from your feature-building code and saved LightGBM models.")
    projects = _projects(cat)
    project = st.selectbox("Project", ["All", *projects], label_visibility="collapsed") if projects else "All"
    project = None if project == "All" else project
    tab_dict, tab_sugg, tab_checks, tab_models = st.tabs(["Dictionary", "Suggested", "Checks", "Models"])
    with tab_dict:
        feats = M.list_features(cat, project=project)
        with st.expander("➕ New feature", expanded=False):
            feature_form(cat, None, project)
        if not feats:
            st.info("No features in the dictionary yet. Confirm some suggestions, or add one.")
        else:
            features_list(cat, feats)
    with tab_sugg:
        sugg = M.list_features(cat, state="suggested", project=project)
        c1, c2 = st.columns([1, 4])
        if c1.button("Look again", help="Re-read feature code and model files already in the catalogue"):
            n = M.suggest_features(cat)
            st.toast(f"{n} suggestions")
            st.rerun()
        if not sugg:
            st.caption("No suggestions. They appear after a project scan finds feature code or model files.")
        else:
            if c2.button(f"Confirm all {len(sugg)}"):
                for f in sugg:
                    M.set_state(cat, f["uid"], "confirmed")
                st.rerun()
        for f in sugg:
            with st.container(border=True):
                a, b, c = st.columns([5, 1, 1])
                a.markdown(f"**{f['name']}** · {f['project']}")
                bits = [f"`{f['expression']}`" if f["expression"] else "",
                        f"window {f['window']}" if f["window"] else "",
                        f"encoding {f['encoding']}" if f["encoding"] else "",
                        f"in {len(f['models'])} model(s)" if f["models"] else ""]
                a.caption(" · ".join(x for x in bits if x))
                if f["known_at_prediction"] == "no":
                    a.warning(f"Possible leakage: {f['leakage_reason'] or 'uses future values'}")
                if b.button("Confirm", key=f"fok-{f['uid']}"):
                    M.set_state(cat, f["uid"], "confirmed")
                    st.rerun()
                if c.button("Not a feature", key=f"fno-{f['uid']}"):
                    M.set_state(cat, f["uid"], "rejected")
                    st.rerun()
    with tab_checks:
        checks_section(cat, project)
    with tab_models:
        models_section(cat, project)


MAX_LISTED = 200


def features_list(cat: Catalogue, feats: list[dict]) -> None:
    """One row per feature: the feature, the models using it, the script building it and where it comes from,
    each a link."""
    find = st.text_input("Filter", placeholder="Filter by name, model, script or table", key="features-filter",
                         label_visibility="collapsed")
    names: dict[str, str] = {}

    def name(k: str) -> str:
        if k not in names:
            a = cat.get_asset(k)
            names[k] = a["name"].rsplit("/", 1)[-1] if a else k
        return names[k]

    rows = []
    for f in feats:
        script = (f["built_by"] or "").partition("::")[0]
        text = " ".join([f["name"], f["project"] or "", f["description"] or "", name(script) if script else "",
                         *(name(m) for m in f["models"]), *(name(s.partition("#")[0]) + " " + s for s in f["sources"])])
        if not find or find.lower() in text.lower():
            rows.append(f)
    st.caption(f"{len(rows)} feature(s)" + (f", showing the first {MAX_LISTED}" if len(rows) > MAX_LISTED else ""))
    head = st.columns([2.2, 2.5, 2, 2.5])
    for h, label in zip(head, ("Feature", "Used by models", "Built in", "Comes from")):
        h.markdown(f"**{label}**")
    for i, f in enumerate(rows[:MAX_LISTED]):
        c1, c2, c3, c4 = st.columns([2.2, 2.5, 2, 2.5])
        c1.button(f"🧬 {f['name']}", key=f"fl-f-{i}", on_click=go, args=(f"feature://{f['uid']}",), type="tertiary",
                  help=first_line(f["description"], 200) or None)
        flags = [f["window"] or "", "⚠️ may leak" if f["known_at_prediction"] == "no" else "", f["status"] or ""]
        if any(flags):
            c1.caption(" · ".join(x for x in flags if x))
        with c2:
            for j, m in enumerate(f["models"][:6]):
                st.button(f"🤖 {name(m)}", key=f"fl-m-{i}-{j}", on_click=go, args=(m,), type="tertiary")
            if len(f["models"]) > 6:
                st.caption(f"and {len(f['models']) - 6} more")
            if not f["models"]:
                st.caption("no saved model")
        with c3:
            if f["built_by"]:
                script, _, fn = f["built_by"].partition("::")
                st.button(f"🐍 {name(script)}" + (f" › {fn}()" if fn else ""), key=f"fl-s-{i}", on_click=go,
                          args=(script, fn or None), type="tertiary")
        with c4:
            for j, src in enumerate(f["sources"][:4]):
                k, _, col = src.partition("#")
                st.button(f"🗄️ {name(k)}" + (f" · {col}" if col else ""), key=f"fl-c-{i}-{j}", on_click=go,
                          args=(k,), type="tertiary")


def checks_section(cat: Catalogue, project: str | None) -> None:
    c = M.checks(cat, project)
    if not any(c.values()):
        st.success("Nothing to flag in the confirmed features.")
        return
    if c["leakage"]:
        st.markdown("**Possible leakage**: not known when the prediction is made")
        for f in c["leakage"]:
            st.button(f"⚠️ {f['name']}: {f['leakage_reason'] or 'uses future values'}", key=f"chk-l-{f['uid']}",
                      on_click=go, args=(f"feature://{f['uid']}",), type="tertiary")
    if c["unused"]:
        st.markdown("**Not used by any saved model**")
        st.caption(", ".join(f["name"] for f in c["unused"]))
    if c["duplicates"]:
        st.markdown("**Look like duplicates**")
        for g in c["duplicates"]:
            st.caption(" = ".join(sorted({f["name"] for f in g})))
    if c["drift"]:
        st.markdown("**Changed since the baseline**")
        for d in c["drift"]:
            st.button(f"📉 {d['feature']['name']}: {d['detail']}", key=f"chk-d-{d['feature']['uid']}", on_click=go,
                      args=(f"feature://{d['feature']['uid']}",), type="tertiary")


def models_section(cat: Catalogue, project: str | None) -> None:
    rows = M.models(cat, project)
    if not rows:
        st.caption("No model files found. LightGBM models saved with save_model() (text or JSON) are read; "
                   "pickles are listed but never opened.")
        return
    for i, m in enumerate(rows):
        with st.container(border=True):
            asset_button(m["key"], m["name"], "model", f"mdl-{i}", help=m["project"])
            st.caption(" · ".join(x for x in (m.get("format"), m.get("objective"),
                                              f"{len(m.get('features', []))} features",
                                              f"{m['trees']} trees" if m.get("trees") else "") if x))
            if m.get("note"):
                st.caption(m["note"])
            imp = m.get("importance") or {}
            if imp:
                top = sorted(imp.items(), key=lambda kv: -kv[1])[:15]
                st.bar_chart(pd.DataFrame(top, columns=["feature", "importance"]).set_index("feature"),
                             horizontal=True, height=min(400, 30 * len(top) + 40))


def feature_form(cat: Catalogue, f: dict | None, project: str | None = None) -> None:
    f = f or {}
    with st.form(f"feat-form-{f.get('uid', 'new')}", clear_on_submit=not f):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", value=f.get("name", ""), placeholder="sales_lag_7")
        proj = c2.text_input("Project", value=f.get("project") or project or "")
        description = st.text_area("What it means", value=f.get("description") or "", height=70)
        c3, c4, c5 = st.columns(3)
        entity = c3.text_input("Entity", value=f.get("entity") or "", placeholder="store")
        grain = c4.text_input("Grain", value=f.get("grain") or "", placeholder="store per day")
        window = c5.text_input("Lag or window", value=f.get("window") or "", placeholder="lag 7 days")
        expression = st.text_input("How it's built", value=f.get("expression") or "")
        c6, c7, c8 = st.columns(3)
        known = c6.selectbox("Known at prediction time?", KNOWN,
                             index=KNOWN.index(f.get("known_at_prediction") or "unknown"))
        missing = c7.text_input("Missing values", value=f.get("missing") or "", placeholder="filled with 0")
        encoding = c8.text_input("Encoding", value=f.get("encoding") or "", placeholder="one-hot")
        leakage = st.text_input("Why it might leak", value=f.get("leakage_reason") or "")
        sources = st.text_input("Comes from (table#column, comma separated)", value=", ".join(f.get("sources") or []))
        c9, c10 = st.columns(2)
        status = c9.selectbox("Status", STATUS, index=STATUS.index(f.get("status") or ""))
        owner = c10.text_input("Owner", value=f.get("owner") or "")
        if st.form_submit_button("Save", type="primary"):
            try:
                uid = M.save_feature(cat, name, f.get("uid"), project=proj.strip(), description=description,
                                     entity=entity, grain=grain, window=window, expression=expression,
                                     known_at_prediction=known, missing=missing, encoding=encoding,
                                     leakage_reason=leakage, status=status, owner=owner, state="confirmed",
                                     sources=[s.strip() for s in sources.split(",") if s.strip()])
                go(f"feature://{uid}")
                st.rerun()
            except ValueError as e:
                st.error(str(e))


def feature_page(cat: Catalogue, key: str) -> None:
    f = M.get_feature(cat, key.removeprefix("feature://"))
    if not f:
        st.warning("This feature no longer exists.")
        return
    st.title(f"🧬 {f['name']}")
    st.caption(" · ".join(x for x in (f["project"], f["state"] if f["state"] != "confirmed" else "",
                                      f["status"] or "", f"owner {f['owner']}" if f["owner"] else "",
                                      f"updated {f['updated_at'][:10]}") if x))
    if f["state"] == "suggested":
        c1, c2 = st.columns([1, 5])
        if c1.button("Confirm", type="primary"):
            M.set_state(cat, f["uid"], "confirmed")
            st.rerun()
    if f["description"]:
        st.markdown(f["description"])
    if f["known_at_prediction"] == "no":
        st.warning(f"Possible leakage: {f['leakage_reason'] or 'uses values not known at prediction time'}")
    facts = {"Entity": f["entity"], "Grain": f["grain"], "Lag or window": f["window"], "Missing values": f["missing"],
             "Encoding": f["encoding"], "Known at prediction": f["known_at_prediction"], "Type": f["data_type"]}
    shown = {k: v for k, v in facts.items() if v}
    if shown:
        st.dataframe(pd.DataFrame(shown.items(), columns=["", "value"]), hide_index=True, width="content")
    if f["expression"]:
        st.code(f["expression"], language="python")
    if f["built_by"]:
        script, _, fn = f["built_by"].partition("::")
        st.button(f"🐍 Built in {script.split('/')[-1]}" + (f" › {fn}()" if fn else ""), on_click=go,
                  args=(script, fn or None), type="tertiary")
    if f["sources"]:
        st.subheader("Comes from")
        for i, s in enumerate(f["sources"]):
            k, _, col = s.partition("#")
            a = cat.get_asset(k)
            asset_button(k, f"{a['name'] if a else k}" + (f" · {col}" if col else ""), a["kind"] if a else "table",
                         f"fs-{i}")
    if f["models"]:
        st.subheader("Used by")
        for i, m in enumerate(f["models"]):
            a = cat.get_asset(m)
            imp = f["importance"].get(m)
            asset_button(m, (a["name"] if a else m) + (f" · importance {imp:g}" if imp is not None else ""), "model",
                         f"fm-{i}")
    if f["sources"]:
        if f["baseline"]:
            d = M.drift_for(cat, f)
            st.caption(f"Baseline taken {f['baseline']['at'][:10]}." + (f" Since then: {d}" if d else
                                                                        " No change worth flagging."))
        if st.button("Set baseline from the current profile", help="Later profiles of the source column are "
                                                                   "compared with this"):
            st.toast("Baseline saved" if M.set_baseline(cat, f["uid"]) else "The source column hasn't been profiled")
            st.rerun()
    with st.expander("✏️ Edit"):
        feature_form(cat, f)
        if st.button("Delete this feature"):
            M.delete_feature(cat, f["uid"])
            go(None)
            st.rerun()
