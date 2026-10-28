"""Naming rules on a database's or project's page: paste them, confirm the checks, see the audit."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat import naming
from pdcat.store import Catalogue

CHECK_LABELS = {"prefix": "starts with", "suffix": "ends with", "type_suffix": "date columns end with",
                "id_suffix": "ID columns end with", "case": "case", "no_spaces": "no spaces",
                "no_reserved": "no reserved words", "max_length": "at most N characters", "schema": "in schema",
                "text": "kept as text"}


def naming_box(cat: Catalogue, key: str) -> None:
    all_rules = naming.rules(cat, key, all_states=True)
    active = [r for r in all_rules if r["state"] == "active"]
    proposed = [r for r in all_rules if r["state"] == "proposed"]
    texts = [r for r in all_rules if r["state"] == "text"]
    label = "📐 Naming rules" + (f" ({len(active)} checked" + (f", {len(proposed)} to confirm" if proposed else "")
                                + ")" if all_rules else "")
    with st.expander(label, expanded=bool(proposed)):
        with st.form(f"nr-paste-{key}"):
            text = st.text_area("Paste the naming rules as written", value=naming.pasted_text(cat, key), height=140,
                                key=f"nr-text-{key}",
                                help="From an email or a standards document. The text is kept as it is; each rule "
                                     "the app recognises becomes a check for you to confirm.")
            if st.form_submit_button("Read the rules"):
                added = naming.save_text(cat, key, text)
                st.toast(f"{sum(1 for a in added if a['check_type'] != 'text')} check(s) found to confirm")
                st.rerun()
        if proposed:
            st.markdown("**Checks found in the text: confirm, edit or drop each**")
            for r in proposed:
                _rule_editor(cat, r)
        if texts:
            st.caption("Kept as text (not turned into a check): " + " · ".join(r["text"] for r in texts[:10]))
        if not active:
            st.caption("No checks yet. Paste rules above, or accept naming habits on the Suggestions page.")
            return
        results = naming.audit(cat, key)
        st.markdown("**Audit**")
        st.dataframe(pd.DataFrame([{"rule": r["label"], "pass": r["passed"], "break it": len(r["failed"]),
                                    "exceptions": len(r["exceptions"]), "as written": r["text"]} for r in results]),
                     hide_index=True, width="stretch")
        st.download_button("⬇️ Audit results (CSV)", naming.audit_csv(cat, key), "naming-audit.csv", "text/csv",
                           key=f"nr-csv-{key}")
        for r in results:
            if not r["failed"] and not r["exceptions"]:
                continue
            with st.popover(f"{r['label']}: {len(r['failed'])} break it"):
                for i, item in enumerate(r["failed"][:50]):
                    c1, c2 = st.columns([3, 1])
                    c1.markdown(f"`{item['name']}`" + (f" in {item['key'].rsplit('/', 1)[-1]}" if item["column"] else ""))
                    if c2.button("Exception", key=f"nr-ex-{r['uid']}-{i}", help="Agreed: this one is allowed"):
                        naming.add_exception(cat, r["uid"], item["key"], item["column"])
                        st.rerun()
                if len(r["failed"]) > 50:
                    st.caption(f"… and {len(r['failed']) - 50} more (in the CSV)")
                for i, item in enumerate(r["exceptions"]):
                    c1, c2 = st.columns([3, 1])
                    c1.caption(f"Exception: `{item['name']}`")
                    if c2.button("Undo", key=f"nr-unex-{r['uid']}-{i}"):
                        naming.remove_exception(cat, r["uid"], item["key"], item["column"])
                        st.rerun()
        with st.popover("Edit the checks"):
            for r in active:
                _rule_editor(cat, r)


def _rule_editor(cat: Catalogue, r: dict) -> None:
    c1, c2, c3, c4, c5 = st.columns([1.2, 1.4, 1.2, 0.8, 0.8])
    applies = c1.selectbox("Applies to", naming.APPLIES, index=naming.APPLIES.index(r["applies_to"])
                           if r["applies_to"] in naming.APPLIES else 0, key=f"nr-a-{r['uid']}",
                           label_visibility="collapsed")
    checks = [c for c in CHECK_LABELS if c != "text"]
    check = c2.selectbox("Check", checks, index=checks.index(r["check_type"]) if r["check_type"] in checks else 0,
                         format_func=CHECK_LABELS.get, key=f"nr-c-{r['uid']}", label_visibility="collapsed")
    if check == "case":
        cases = list(naming.CASES)
        value = c3.selectbox("Case", cases, index=cases.index(r["value"]) if r["value"] in cases else 0,
                             format_func=lambda c: naming.CASES[c][1], key=f"nr-v-{r['uid']}",
                             label_visibility="collapsed")
    elif check in ("no_spaces", "no_reserved"):
        value = ""
        c3.write("")
    else:
        shown = r["value"].split(":", 1)[1] if check == "type_suffix" and ":" in (r["value"] or "") else r["value"]
        value = c3.text_input("Value", value=shown or "", key=f"nr-v-{r['uid']}", label_visibility="collapsed")
        if check == "type_suffix":
            value = f"date:{value}"
    if c4.button("✓" if r["state"] == "proposed" else "Save", key=f"nr-ok-{r['uid']}",
                 help="Confirm this check" if r["state"] == "proposed" else "Save changes"):
        naming.update_rule(cat, r["uid"], applies_to=applies, check_type=check, value=value, state="active")
        st.rerun()
    if c5.button("✕", key=f"nr-del-{r['uid']}", help="Drop this check"):
        naming.delete_rule(cat, r["uid"])
        st.rerun()
    st.caption(f"“{r['text']}”" if r["text"] else "")
