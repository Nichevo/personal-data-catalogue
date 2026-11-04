"""Gold standards (version 2.6): the box on report, measure and SQL pages, and the Review › Gold standards page."""
from __future__ import annotations

import json
from typing import Any

import streamlit as st

from pdcat import gold
from pdcat.store import Catalogue

from .common import go, page_title, selected_rows

VERDICT = {"differs": ("❌", "differs"), "name": ("✏️", "same calculation, other name"),
           "ok": ("👌", "OK to differ"), "same": ("✅", "matches")}
SHOW = 25


def _kind_icon(item: dict[str, Any]) -> str:
    return {"measure": "🧮", "report": "📊", "sql": "🧾"}.get(item.get("kind", ""), "•")


def result_view(res: dict[str, Any]) -> None:
    """How one item differs from a gold standard: a line per part that differs, then the text diff."""
    for s in res.get("sections", []):
        if s.get("text"):
            st.markdown(f"- **{s['title']}:** {s['text']}")
            continue
        bits = []
        if s.get("missing"):
            bits.append("missing " + ", ".join(f"`{x}`" for x in s["missing"][:12])
                        + (f" and {len(s['missing']) - 12} more" if len(s["missing"]) > 12 else ""))
        if s.get("extra"):
            bits.append("extra " + ", ".join(f"`{x}`" for x in s["extra"][:12])
                        + (f" and {len(s['extra']) - 12} more" if len(s["extra"]) > 12 else ""))
        if s.get("changed"):
            bits.append("changed " + ", ".join(f"`{x}`" for x in s["changed"][:12])
                        + (f" and {len(s['changed']) - 12} more" if len(s["changed"]) > 12 else ""))
        st.markdown(f"- **{s['title']}:** " + "; ".join(bits))
    if res.get("text_diff"):
        st.caption("Lines starting with − are in the gold standard, + in this one.")
        st.code(res["text_diff"], language="diff")


def _mark_popover(cat: Catalogue, key: str, row: dict[str, Any] | None) -> None:
    with st.popover("⭐ Gold standard" if row else "☆ Mark as gold standard",
                    help="The one to trust. Related reports, measures and SQL are checked against it."):
        note = st.text_input("Why this is the standard (optional)", value=(row or {}).get("note") or "",
                             key=f"gold-note-{key}")
        if st.button("Save note" if row else "Mark as gold standard", key=f"gold-mark-{key}", type="primary"):
            gold.mark(cat, key, note)
            st.rerun()
        if row and st.button("Remove gold standard", key=f"gold-unmark-{key}"):
            gold.unmark(cat, key)
            st.rerun()


def gold_box(cat: Catalogue, key: str) -> None:
    """On a report, measure, view, procedure or SQL query page: mark it as the gold standard, and see what
    differs from it (or, for other items, the gold standard it differs from)."""
    try:
        kind = gold.kind_of(cat, key)
    except Exception:  # an older catalogue without the gold tables open read-only, say
        return
    if kind is None:
        return
    row = gold.get(cat, key)
    _mark_popover(cat, key, row)
    if row:
        own_box(cat, key, row)
        return
    via = gold.gold_for(cat, key)
    if via:
        rep = gold.load(cat, via["key"])
        c1, c2 = st.columns([5, 1])
        c1.info(f"In the gold standard report **{rep['name'] if rep else via['key']}**, so this measure counts as "
                "a gold standard too. Measures with the same name in other reports are checked against it.", icon="⭐")
        c2.button("Open report", key=f"gold-via-{key}", on_click=go, args=(via["key"],))
        r = gold.related(cat, via["key"])
        mine = [x for x in r["measures"] if x["standard"]["key"] == key]
        if mine:
            related_list(cat, via["key"], mine, f"{key}-via")
    for i, a in enumerate(gold.against(cat, key)):
        against_banner(cat, key, a, i)


def own_box(cat: Catalogue, key: str, row: dict[str, Any]) -> None:
    r = gold.related(cat, key)
    who = " · ".join(x for x in (f"marked {str(row.get('marked_at') or '')[:10]}", row.get("author") or "") if x)
    text = "**Gold standard**" + (f": {row['note']}" if row.get("note") else "") + f"  \n{who}"
    if r["gold"] is None or r["gold"].get("missing"):
        st.warning(text + "  \nThis item wasn't found on the latest scan, so nothing can be checked against it.")
        return
    rel = r["related"] + r["measures"]
    n_diff = sum(x["verdict"] == "differs" for x in rel)
    st.success(text + "  \n" + (f"{len(rel)} related item(s): {n_diff} differ." if rel else
                                "Nothing related found yet.")
               + f"  \nCompared with {gold.scope_label(r['scope'])}"
               + (f"; {len(r['left_out'])} related report(s) not compared." if r["left_out"] else "."), icon="⭐")
    scope_box(cat, key, r)
    if r["changed"]:
        with st.container(border=True):
            st.warning(f"The gold standard itself changed since it was marked: {r['changed']['summary']}.", icon="⚠️")
            result_view(r["changed"])
            if st.button("It changed on purpose: use it as it is now", key=f"gold-accept-{key}"):
                gold.accept_change(cat, key)
                st.rerun()
    for c in r["conflicts"]:
        st.warning(f"Gold standards disagree on **{c['name']}**: "
                   + "; ".join(f"{s['where']} ({s['via']})" for s in c["standards"])
                   + ". Mark the measure you trust as the gold standard to settle it.", icon="⚖️")
    if r["related"]:
        st.markdown("**Related " + {"report": "reports", "measure": "measures", "sql": "SQL"}[r["gold"]["kind"]]
                    + f" ({len(r['related'])})**")
        related_list(cat, key, r["related"], f"{key}-rel")
    if r["measures"]:
        st.markdown(f"**Its measures in other reports ({len(r['measures'])})**")
        related_list(cat, key, r["measures"], f"{key}-ms")


def _candidates(cat: Catalogue, key: str) -> dict[str, str]:
    """Every report (or, for SQL, query) that could be compared with a gold standard: key -> label."""
    every = gold.related(cat, key, everything=True)
    old = gold.old_reports(cat)
    keys = {gold.compared_key(x["item"]) for x in every["related"] + every["measures"]}
    out = {}
    for k in keys:
        a = cat.conn.execute("SELECT name, attrs FROM assets WHERE key = ?", (k,)).fetchone()
        if a is None:
            item = next((x["item"] for x in every["related"] if x["item"]["key"] == k), {})
            out[k] = item.get("name") or k
            continue
        path = str((json.loads(a["attrs"] or "{}") or {}).get("path") or "")
        folder = path.replace("\\", "/").rsplit("/", 1)[0] if "/" in path.replace("\\", "/") else ""
        out[k] = a["name"] + (f" · {folder}" if folder else "") + (" · old" if k in old else "")
    return dict(sorted(out.items(), key=lambda kv: kv[1].lower()))


def scope_box(cat: Catalogue, key: str, r: dict[str, Any]) -> None:
    """Choose which reports this gold standard is compared with (version 2.27)."""
    sc = r["scope"]
    noun = "queries" if r["gold"]["kind"] == "sql" else "reports"
    with st.expander(f"🎯 Compare with: {gold.scope_label(sc)}"):
        cands = _candidates(cat, key)
        if not cands:
            st.caption(f"No related {noun} found yet.")
            return
        modes = list(gold.SCOPES)
        mode = st.radio("Compare this gold standard with", modes, index=modes.index(sc["mode"]),
                        format_func=gold.SCOPES.get, key=f"gold-scope-mode-{key}",
                        help="Old = an older version in a Versions group, a report marked 🗄️ old or 🙈 not "
                             "relevant, or one in a folder whose role is archive.")
        if mode == "picked":
            picked = st.multiselect(f"Only these {noun}", list(cands), format_func=cands.get,
                                    default=[k for k in sc["picked"] if k in cands], key=f"gold-scope-pick-{key}")
            skip = sc["skip"]
        else:
            picked = sc["picked"]
            skip = st.multiselect(f"Leave out these {noun}", list(cands), format_func=cands.get,
                                  default=[k for k in sc["skip"] if k in cands], key=f"gold-scope-skip-{key}")
            skip += [k for k in sc["skip"] if k not in cands]  # keep ones not related right now
        st.caption(f"{len(cands)} related {noun} found. Items you leave out drop off Review › Gold standards "
                   "and the To review list too.")
        if st.button("Save", key=f"gold-scope-save-{key}", type="primary"):
            gold.set_scope(cat, key, mode, skip, picked)
            st.rerun()


def related_list(cat: Catalogue, gold_key: str, rows: list[dict[str, Any]], wkey: str) -> None:
    """Differing items each in an expander with how they differ; matching ones in one line."""
    differ = [x for x in rows if x["verdict"] != "same"]
    same = [x for x in rows if x["verdict"] == "same"]
    for i, x in enumerate(differ[:SHOW]):
        ic, label = VERDICT[x["verdict"]]
        it = x["item"]
        std_key = (x.get("standard") or {}).get("gold_key", gold_key)
        title = f"{ic} {_kind_icon(it)} {it['name']}" + (f" · {it['where']}" if it.get("where") else "") + \
            f" · {x['why']} · {x['result']['summary'] or label}"
        with st.expander(title):
            if x.get("standard") and x["standard"]["key"] != gold_key:
                st.caption(f"Compared with {x['standard']['where']} › {x['standard']['name']} ({x['standard']['via']}).")
            result_view(x["result"])
            c1, c2, c3 = st.columns(3)
            c1.button("Open", key=f"gold-open-{wkey}-{i}", on_click=go, args=(it["key"],))
            _ok_buttons(cat, std_key, it["key"], x.get("ok"), x["verdict"], c2, f"{wkey}-{i}")
            _leave_out_buttons(cat, gold_key, it, c3, f"{wkey}-{i}")
    if len(differ) > SHOW:
        st.caption(f"…and {len(differ) - SHOW} more. The To review list has them all (Differs from the gold standard).")
    if same:
        st.caption("✅ Match: " + ", ".join(f"{x['item']['name']} ({x['item'].get('where', '')})" for x in same[:12])
                   + (f" and {len(same) - 12} more" if len(same) > 12 else ""))


def _leave_out_buttons(cat: Catalogue, gold_key: str, item: dict[str, Any], col, wkey: str) -> None:
    """Stop comparing this report with the gold standard, or mark the report as an old version everywhere."""
    target = gold.compared_key(item)
    what = "this report" if item.get("kind") in ("measure", "report") else "this item"
    if col.button(f"🚫 Don't compare {what}", key=f"gold-skip-{wkey}",
                  help="Leave it out of this gold standard's comparisons (🎯 Compare with, to undo)."):
        gold.leave_out(cat, gold_key, target)
        st.rerun()
    if item.get("kind") in ("measure", "report") and col.button(
            "🗄️ Mark report old", key=f"gold-old-{wkey}",
            help="An old version: left out of every gold standard and hidden from the Power BI lists. "
                 "↩️ Not old on the report's page brings it back."):
        from pdcat import curation
        curation.set_relevance(cat, target, "old")
        st.rerun()


def _ok_buttons(cat: Catalogue, gold_key: str, item_key: str, ok: dict | None, verdict: str, col, wkey: str) -> None:
    if verdict == "ok":
        col.caption("👌 OK to differ" + (f": {ok['note']}" if ok and ok.get("note") else ""))
        if col.button("Not OK any more", key=f"gold-unok-{wkey}"):
            gold.clear_ok(cat, gold_key, item_key)
            st.rerun()
    else:
        with col.popover("👌 OK to differ", help="It differs on purpose. Shown again if it changes."):
            note = st.text_input("Why (optional)", key=f"gold-oknote-{wkey}")
            if st.button("Save", key=f"gold-ok-{wkey}", type="primary"):
                gold.ok_to_differ(cat, gold_key, item_key, note)
                st.rerun()


def against_banner(cat: Catalogue, key: str, a: dict[str, Any], i: int) -> None:
    g = a["gold"]
    name = f"{g['where']} › {g['name']}" if g.get("kind") == "measure" else g["name"]
    via = " (in a gold standard report)" if a["via"] == "in a gold report" else ""
    if a["verdict"] == "same":
        st.success(f"✅ Matches the gold standard **{name}**{via}.", icon="⭐")
        return
    ic, label = VERDICT[a["verdict"]]
    text = f"{ic} {'Differs from' if a['verdict'] == 'differs' else 'Compared with'} the gold standard **{name}**{via}: " \
           f"{a['result']['summary'] or label}."
    with st.container(border=True):
        (st.warning if a["verdict"] == "differs" else st.info)(text, icon="⭐")
        with st.expander("How it differs"):
            result_view(a["result"])
        c1, c2 = st.columns(2)
        c1.button("Open the gold standard", key=f"gold-open-std-{key}-{i}", on_click=go, args=(g["key"],))
        _ok_buttons(cat, a["gold_key"], key, a.get("ok"), a["verdict"], c2, f"ag-{key}-{i}")


def gold_page(cat: Catalogue) -> None:
    import pandas as pd
    page_title("Gold standards")
    st.caption("Reports, measures and SQL you marked as the one to trust, and how many related items differ from "
               "them. To mark one, open a Power BI report, a measure, an SQL query or a view and press "
               "**☆ Mark as gold standard**. Measures across reports (Map menu) has a button for it too.")
    rows = gold.overview(cat)
    if not rows:
        st.info("No gold standards yet. Mark a report, measure or query you trust; then every report copy, "
                "same-named measure or look-alike query that differs from it is listed here and in To review.")
        return
    n_diff = sum(r["differs"] for r in rows)
    st.markdown(f"**{len(rows)}** gold standard(s) · **{n_diff}** related item(s) differ")
    table = pd.DataFrame([{"": "⚠️" if r["changed"] or r["gone"] else "", "gold standard": r["name"],
                           "kind": gold.KIND_LABELS.get(r["kind"], r["kind"]), "in": r["where"],
                           "differ": r["differs"], "other name": r["name_only"], "OK to differ": r["ok"],
                           "match": r["same"], "compared with": r["scope"], "not compared": r["left_out"],
                           "note": r["note"],
                           "changed since marked": r["changed"] or ("not found on the latest scan" if r["gone"] else "")}
                          for r in rows])
    pick = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                        key="gold-table")
    sel = selected_rows(pick, len(rows))
    if sel:
        go(rows[sel[0]]["key"])
        st.rerun()
    st.caption("Pick a row to open the gold standard. Below: what differs, per gold standard.")
    for j, r in enumerate(rows):
        d = r["details"]
        rel = d["related"] + d["measures"]
        if not any(x["verdict"] != "same" for x in rel) and not r["changed"] and not d["conflicts"]:
            continue
        st.subheader(f"⭐ {r['name']}")
        if r["changed"]:
            st.warning(f"Changed since marked: {r['changed']}. Open it to see how, and accept the change if it's "
                       "on purpose.", icon="⚠️")
        for c in d["conflicts"]:
            st.warning(f"Gold standards disagree on **{c['name']}**.", icon="⚖️")
        related_list(cat, r["key"], rel, f"page-{j}")
