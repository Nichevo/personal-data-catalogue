"""The Power BI report register on the pages: the context strip at the top of a report, the box on a data file
that makes it the register, the Register view under Find › Power BI, and a page per register row."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import register as R
from pdcat.store import Catalogue

from .common import asset_button, cached, go, icon, keep, restore


@cached
def _entries(cat: Catalogue):
    return R.entries(cat)


@cached
def _by_report(cat: Catalogue):
    return R.rows_by_report(cat)


def register_strip(cat: Catalogue, key: str, asset: dict) -> None:
    """Key context from the register at the top of a report page: owner, audience, app, status, dates, link."""
    e = _by_report(cat).get(key)
    if not e:
        return
    with st.container(border=True):
        parts = [f"**{label}:** {value}" for label, value in R.strip_line(e)]
        st.markdown("📇 **In the report register** · " + " · ".join(parts) if parts else "📇 **In the report register**")
        bits = []
        if e.get("dataset"):
            bits.append(f"dataset **{e['dataset']}**")
        if e.get("tags"):
            bits.append(f"tags {e['tags']}")
        if e.get("url"):
            bits.append(f"[open in Power BI]({e['url']})")
        c1, c2 = st.columns([5, 1.3], vertical_alignment="center")
        if bits:
            c1.caption(" · ".join(bits))
        c2.button("Register entry", key=f"regstrip-{key}", on_click=go, args=(e["key"],), type="tertiary",
                  help="The row itself: change which report it matches, keep notes")


def use_as_register_box(cat: Catalogue, key: str, asset: dict) -> None:
    """On a data file: make it the Power BI report register, or stop. Shows the field mapping to confirm."""
    columns = [c["name"] for c in cat.columns(key)]
    current = R.register_key(cat)
    if current == key:
        with st.container(border=True):
            n = len(_entries(cat))
            matched = sum(1 for e in _entries(cat) if e.get("report"))
            st.markdown(f"📇 **This is the Power BI report register** · {n} rows, {matched} matched to scanned reports")
            c1, c2 = st.columns([1, 1])
            if c1.button("Read it again now", key=f"reg-refresh-{key}"):
                R.refresh(cat)
                st.rerun()
            if c2.button("Stop using it as the register", key=f"reg-stop-{key}"):
                R.set_register(cat, None)
                R.refresh(cat)
                st.rerun()
            with st.expander("Fields"):
                _mapping_form(cat, key, columns, R.stored(cat).get("mapping") or {})
        return
    looks = R.guess_mapping(columns)
    if "name" not in looks or not ({"owner", "uid", "app", "dataset"} & set(looks)):
        return
    with st.expander("📇 Use as the Power BI report register", expanded=current is None):
        st.caption("This file looks like a list of published reports with their owners. As the register, each "
                   "row is matched to a scanned .pbix by name, the owner and description are shown at the top of "
                   "the report's page, and rows are searchable by owner, audience, app and status."
                   + (" It replaces the current register." if current else ""))
        _mapping_form(cat, key, columns, looks, make=True)


def _mapping_form(cat: Catalogue, key: str, columns: list[str], mapping: dict[str, str], make: bool = False) -> None:
    options = ["(none)"] + columns
    with st.form(f"reg-map-{key}"):
        picks = {}
        cols = st.columns(2)
        for i, (field, label) in enumerate(R.LABELS.items()):
            have = mapping.get(field, "(none)")
            picks[field] = cols[i % 2].selectbox(label, options, index=options.index(have) if have in options else 0,
                                                 key=f"reg-map-{key}-{field}")
        if st.form_submit_button("Use as the register" if make else "Save fields"):
            chosen = {f: c for f, c in picks.items() if c != "(none)"}
            if "name" not in chosen:
                st.error("Pick the column that holds the report name.")
            else:
                R.set_register(cat, key, chosen)
                n = R.refresh(cat)
                st.success(f"{n} register rows read.")
                st.rerun()


def register_view(cat: Catalogue) -> None:
    """Find › Power BI › Register: every row with filters by owner, audience, app and status, and the unmatched."""
    rows = _entries(cat)
    if not rows:
        if R.register_key(cat):
            st.info("The register file has no rows the app could read. Open the file and check its fields.")
            a = cat.get_asset(R.register_key(cat))
            if a:
                asset_button(R.register_key(cat), a["name"], a["kind"], "reg-open-file")
        else:
            st.info("No register yet. Scan the folder that holds your report list (a CSV with one row per "
                    "published report: name, owner, audience, app, status, dataset…), open that data file and pick "
                    "**Use as the Power BI report register**.")
        return
    facets = R.facets(cat)
    c0, c1, c2, c3, c4 = st.columns([2.2, 1, 1, 1, 1])
    restore("reg-text")
    text = c0.text_input("Filter", key="reg-text", placeholder="any word: name, owner, tags…",
                         label_visibility="collapsed")
    keep("reg-text")
    picks = {}
    for col, field in ((c1, "owner"), (c2, "audience"), (c3, "app"), (c4, "status")):
        picks[field] = col.selectbox(R.LABELS[field], ["(all)"] + facets[field], key=f"reg-{field}")
    shown = []
    for e in rows:
        if any(v != "(all)" and str(e.get(f, "")) != v for f, v in picks.items()):
            continue
        if text and text.lower() not in " ".join(str(v) for v in e.values()).lower():
            continue
        shown.append(e)
    names = {r["key"]: r["name"] for r in cat.conn.execute("SELECT key, name FROM assets WHERE kind = 'report'")}
    matched = sum(1 for e in shown if e.get("report"))
    st.caption(f"{len(shown)} of {len(rows)} rows · {matched} matched to a scanned report. Pick a row to open it.")
    table = [{"name": e["name"], "owner": e.get("owner", ""), "audience": e.get("audience", ""),
              "app": e.get("app", ""), "status": e.get("status", ""), "workspace": e.get("workspace", ""),
              "scanned report": names.get(e.get("report") or "", "") or ("" if e.get("report") else "— not matched"),
              "dataset": e.get("dataset", ""), "tags": e.get("tags", ""),
              "modified": str(e.get("modified", ""))[:10]} for e in shown]
    event = st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key="reg-table")
    sel = event.selection.rows if event else []
    if sel:
        go(shown[sel[0]]["key"])
        st.rerun()
    missing = [e for e in rows if not e.get("report")]
    if missing:
        with st.expander(f"Not matched to a scanned report ({len(missing)})"):
            st.caption("No scanned .pbix has this name. Scan the folder that holds it, or open the row and pick "
                       "the report by hand.")
            for e in missing[:200]:
                st.button(f"📇 {e['name']}" + (f" · {e['owner']}" if e.get("owner") else ""), key=f"regmiss-{e['key']}",
                          on_click=go, args=(e["key"],), type="tertiary")
    a = cat.get_asset(R.register_key(cat) or "")
    if a:
        st.caption("Register file:")
        asset_button(a["key"], a["name"], a["kind"], "reg-open-file")


def register_page(cat: Catalogue, key: str) -> None:
    e = R.entry(cat, key)
    if not e:
        st.warning("This register row no longer exists.")
        return
    st.title(f"{icon('report_ref')} {e['name']}")
    st.caption("register entry · Power BI report register" + f"  \n`{key}`")
    if cat.get_asset(key)["status"] == "missing":
        st.warning("This row was not in the register the last time it was read.")
    if e.get("description"):
        st.markdown(e["description"])
    parts = [f"**{label}:** {value}" for label, value in R.strip_line(e)]
    if parts:
        st.markdown(" · ".join(parts))
    if e.get("url"):
        st.markdown(f"[Open in Power BI]({e['url']})")
    with st.container(border=True):
        report = e.get("report")
        a = cat.get_asset(report) if report else None
        how = {"name": "same name", "words": "same name apart from version marks", "start": "name starts the same",
               "manual": "picked by hand"}.get(e.get("how") or "", "")
        if a:
            st.markdown(f"**Scanned report:** matched by {how}" if how else "**Scanned report**")
            asset_button(report, a["name"], "report", f"reg-report-{key}")
        else:
            st.markdown("**Scanned report:** none matched" + (" (picked by hand)" if how == "manual" else ""))
        reports = cat.conn.execute("SELECT key, name, source FROM assets WHERE kind = 'report' AND status = 'active' "
                                   "ORDER BY name").fetchall()
        options = ["(none)"] + [r["key"] for r in reports]
        labels = {r["key"]: f"{r['name']} ({r['source']})" for r in reports}
        cands = [c for c in e.get("candidates") or [] if c != report]
        if cands:
            st.caption("Could also be: " + ", ".join(labels.get(c, c) for c in cands[:5]))
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        pick = c1.selectbox("Change the match", options, index=options.index(report) if report in options else 0,
                            format_func=lambda k: labels.get(k, k), key=f"reg-pick-{key}")
        if c2.button("Save", key=f"reg-save-{key}"):
            R.set_match(cat, key, None if pick == "(none)" else pick)
            st.rerun()
        if e.get("how") == "manual" and st.button("Back to matching by name", key=f"reg-auto-{key}"):
            R.forget_match(cat, key)
            st.rerun()
    other = {k: v for k, v in e.items() if k not in R.FIELDS and k not in ("key", "report", "how", "candidates",
                                                                              "register", "added_in_rescan",
                                                                              "changed_at", "previous_modified")}
    fields = [(R.LABELS[f], e[f]) for f in ("uid", "dataset", "dataset_id", "tags") if e.get(f)]
    fields += [(k, v) for k, v in other.items() if v]
    if fields:
        st.dataframe(pd.DataFrame(fields, columns=["field", "value"]), hide_index=True, width="stretch")
    from .detail import notes_section
    notes_section(cat, key, [])
