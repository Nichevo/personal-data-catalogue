"""Company-wide keys: the list, one key's page, and marking a column as a key."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import keys as K
from pdcat.store import Catalogue

from .common import asset_button, go, page_title


def keys_page(cat: Catalogue) -> None:
    st.title("Company-wide keys")
    st.caption("Identifiers such as PlantID that tie data together. Mark one from a table's page "
               "(\"Mark as company-wide key\"), or accept a suggestion below. Columns with the same name or an "
               "alias are suggested until you confirm them.")
    active = K.list_keys(cat)
    if active:
        for i, k in enumerate(active):
            with st.container(border=True):
                c1, c2 = st.columns([3, 2])
                c1.button(f"🔑 {k['name']}", key=f"key-{i}", on_click=go, args=(f"key://{k['name']}",),
                          type="tertiary")
                if k["description"]:
                    c1.caption(k["description"])
                bits = [f"{k['columns']} column(s)"]
                if k["suggested_columns"]:
                    bits.append(f"{k['suggested_columns']} to confirm")
                if k["aliases"]:
                    bits.append("also called " + ", ".join(k["aliases"]))
                c2.caption(" · ".join(bits))
    else:
        st.info("No company-wide keys yet.")
    suggested = K.list_keys(cat, "suggested")
    if suggested:
        st.subheader(f"Suggested ({len(suggested)})")
        st.caption("Key-like column names found in many places after the last scan. Tick any that aren't real keys, "
                   "then accept all the others.")
        from .bulk import bulk_decide
        bulk_decide(suggested, prefix="keysugg", row_id=lambda k: k["name"], text=lambda k: f"{k['name']} "
                    f"{k['evidence'] or ''}", render=lambda k: st.markdown(f"**{k['name']}** · {k['evidence'] or ''}"),
                    accept=lambda k: K.accept_key(cat, k["name"]), reject=lambda k: K.reject_key(cat, k["name"]),
                    noun="key")
    waiting = [c for k in active for c in K.key_columns(cat, k["name"], "suggested")] if active else []
    if waiting:
        with st.expander(f"Columns to confirm across all keys ({len(waiting)})", expanded=not suggested):
            key_columns_picker(cat, waiting, "allkc")
    rejected = K.list_keys(cat, "rejected")
    if rejected:
        with st.expander(f"Rejected ({len(rejected)})"):
            for i, k in enumerate(rejected):
                c1, c2 = st.columns([4, 1])
                c1.caption(k["name"])
                if c2.button("Restore", key=f"restore-key-{i}"):
                    K.accept_key(cat, k["name"])
                    st.rerun()


def key_page(cat: Catalogue, key: str) -> None:
    name = key.removeprefix("key://")
    k = K.get_key(cat, name)
    if not k:
        st.warning(f"No key called {name}.")
        return
    st.title(f"🔑 {k['name']}")
    st.caption("Company-wide key" + (f" · master list: {k['master_key']}.{k['master_column']}" if k["master_key"] else ""))
    if k["description"]:
        st.markdown(k["description"])
    for problem in K.checks(cat, k["name"]):
        st.warning(problem)
    confirmed = K.key_columns(cat, k["name"], "confirmed")
    from pdcat.sapterms import column_sap_names, meaning
    saps = sorted({column_sap_names(cat, c["asset_key"]).get(c["column_name"]) for c in confirmed} - {None})
    if saps:
        st.caption("In SAP: " + ", ".join(f"`{s}` ({meaning(s)})" if meaning(s) else f"`{s}`" for s in saps))
    with st.expander("✏️ Edit this key"):
        with st.form(f"edit-key-{name}"):
            new_name = st.text_input("Name", value=k["name"])
            desc = st.text_area("What it identifies", value=k["description"] or "", height=80)
            fmt = st.text_input("Format", value=k["format"] or "", placeholder="e.g. 4 digits, zero-padded")
            aliases = st.text_input("Also called (comma separated)", value=", ".join(k["aliases"]),
                                    placeholder="Plant_No, SiteID")
            options = [(c["asset_key"], c["column_name"]) for c in confirmed]
            current = (k["master_key"], k["master_column"])
            master = st.selectbox("Master list of values", options, index=options.index(current)
                                  if current in options else None, format_func=lambda o: f"{o[0]} · {o[1]}")
            if st.form_submit_button("Save", type="primary"):
                try:
                    target = K.update_key(cat, k["name"], new_name=new_name, description=desc, format=fmt,
                                          aliases=aliases.split(","), master=master)
                    go(f"key://{target}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        if st.button("Delete this key", help="Removes the key and its badges; the tables are untouched"):
            K.delete_key(cat, k["name"])
            go(None)
            st.rerun()
    st.subheader(f"Carried by ({len(confirmed)})")
    for i, c in enumerate(confirmed):
        c1, c2, c3 = st.columns([3, 2, 1])
        with c1:
            asset_button(c["asset_key"], f"{c['item'] or c['asset_key']} · {c['column_name']}", c["kind"] or "table",
                         f"kc-{i}")
        c2.caption(" · ".join(b for b in (c["data_type"], c["source"],
                                          "⭐ master" if (c["asset_key"], c["column_name"]) ==
                                          (k["master_key"], k["master_column"]) else "") if b))
        if c3.button("Unlink", key=f"unlink-{i}"):
            K.unlink_column(cat, c["asset_key"], c["column_name"])
            st.rerun()
    suggested = K.key_columns(cat, k["name"], "suggested")
    if suggested:
        st.subheader(f"Probably carries it ({len(suggested)})")
        key_columns_picker(cat, suggested, f"kc-{name}", confirmed=confirmed)
    from .detail import notes_section
    notes_section(cat, key, [])


def key_columns_picker(cat: Catalogue, suggested: list[dict], prefix: str,
                       confirmed: list[dict] | None = None) -> None:
    """Columns that probably carry a key: marked likely / check / unlikely by data type, confirmed in bulk."""
    from pdcat.triage import key_column_bands

    from .bulk import bulk_decide
    by_key: dict[str, list[dict]] = {}
    for c in suggested:
        by_key.setdefault(c["key_name"], []).append(c)
    rows = []
    for name, cols in by_key.items():
        conf = confirmed if confirmed is not None else K.key_columns(cat, name, "confirmed")
        for c, (band, why, group) in zip(cols, key_column_bands(conf, cols), strict=True):
            rows.append({**c, "band": band, "why": why, "group": group})
    rows.sort(key=lambda r: ("likely", "check", "unlikely").index(r["band"]))

    def render(c: dict) -> None:
        asset_button(c["asset_key"], f"{c['item'] or c['asset_key']} · {c['column_name']}", c["kind"] or "table",
                     f"{prefix}-b-{c['asset_key']}-{c['column_name']}")
        bits = [f"🔑 {c['key_name']}" if confirmed is None else "", c.get("data_type") or "", c.get("source") or ""]
        if any(bits):
            st.caption(" · ".join(b for b in bits if b))

    bulk_decide(rows, prefix=prefix, row_id=lambda c: f"{c['key_name']}|{c['asset_key']}|{c['column_name']}",
                text=lambda c: f"{c['key_name']} {c['item'] or ''} {c['asset_key']} {c['column_name']} "
                               f"{c.get('data_type') or ''}",
                render=render, noun="column", accept_word="Confirm", reject_word="Not this",
                accept=lambda c: K.set_column_state(cat, c["asset_key"], c["column_name"], "confirmed"),
                reject=lambda c: K.set_column_state(cat, c["asset_key"], c["column_name"], "rejected"))


def mark_key_form(cat: Catalogue, asset_key: str, columns: list[str]) -> None:
    existing = [k["name"] for k in K.list_keys(cat)]
    with st.popover("🔑 Mark as company-wide key"):
        with st.form(f"markkey-{asset_key}"):
            column = st.selectbox("Column", columns)
            name = st.selectbox("Key", ["(new key, named after the column)", *existing])
            master = st.checkbox("This table is the master list of values", value=True)
            if st.form_submit_button("Mark", type="primary"):
                made = K.mark_key(cat, column if name.startswith("(new") else name, asset_key, column, master=master)
                st.toast(f"{column} carries {made}")
                st.rerun()


def sensitive_page(cat: Catalogue) -> None:
    from pdcat import curation
    page_title("Sensitive data")
    st.caption("Columns and items marked sensitive: their values are never stored, shown or exported (counts are "
               "kept). Below, columns whose names suggest personal data; mark them or dismiss them.")
    marked = cat.conn.execute(
        "SELECT ci.asset_key, ci.column_name, a.name, a.kind FROM column_info ci LEFT JOIN assets a "
        "ON a.key = ci.asset_key WHERE ci.sensitive = 1 ORDER BY a.name, ci.column_name").fetchall()
    whole = cat.conn.execute("SELECT i.asset_key, a.name, a.kind FROM item_info i LEFT JOIN assets a ON a.key = "
                             "i.asset_key WHERE i.sensitive = 1").fetchall()
    if marked or whole:
        rows = [{"item": r["name"] or r["asset_key"], "column": r["column_name"]} for r in marked]
        rows += [{"item": r["name"] or r["asset_key"], "column": "(whole item)"} for r in whole]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("Nothing marked sensitive yet.")
    sugg = curation.sensitive_suggestions(cat)
    if sugg:
        st.subheader(f"Possibly sensitive ({len(sugg)})")
        if st.button(f"🔒 Mark all {len(sugg)} sensitive"):
            for c in sugg:
                curation.set_column_info(cat, c["asset_key"], c["name"], sensitive=True, rebuild=False)
            cat.rebuild_search()
            st.rerun()
        for i, c in enumerate(sugg[:200]):
            c1, c2, c3 = st.columns([4, 1, 1])
            with c1:
                asset_button(c["asset_key"], f"{c['item']} · {c['name']}", c["kind"], f"sens-{i}")
            if c2.button("🔒 Sensitive", key=f"sensy-{i}"):
                curation.set_column_info(cat, c["asset_key"], c["name"], sensitive=True)
                st.rerun()
            if c3.button("Not sensitive", key=f"sensn-{i}"):
                curation.dismiss_sensitive_suggestion(cat, c["asset_key"], c["name"])
                st.rerun()
