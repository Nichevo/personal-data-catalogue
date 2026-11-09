"""Review › Change tracking: every change in one list, graphs over time, and your change trackers.
Also the Profiling list (Add › Sources) and Settings › Database size."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st

from pdcat import changes as ch
from pdcat import growth, trackers
from pdcat import timebuckets as tb
from pdcat.store import Catalogue

from .common import go, go_page, icon, page_title, selected_rows, selection_key, size_label

WINDOWS = {7: "7 days", 30: "30 days", 90: "90 days", 365: "a year", 3650: "all time"}
FILTER_KEYS = ("categories", "sources", "kinds", "project", "tag", "group", "gold", "followed", "text", "hide_noise")
PALETTE = ["#4a73c9", "#e07b39", "#3a9d6e", "#b5487a", "#8a6bd1", "#c9a227", "#4fa3b8", "#7d7d7d"]


def change_tracking_page(cat: Catalogue) -> None:
    from .main import combined
    _first_use(cat)
    combined("Change tracking", "Every change in one place: what scans found, report changes, row counts, your "
                                "edits, commits and the changes your change trackers recorded.", {
        "All changes": all_changes_view, "Graphs": graphs_view, "Change trackers": trackers_view})(cat)
    ch.mark_seen(cat)


def _first_use(cat: Catalogue) -> None:
    """Once, on a catalogue from before 2.9: turn the old change history into readings for the graphs."""
    if cat.setting("growth_backfilled"):
        return
    with st.spinner("Setting up change tracking from your change history (once)…"):
        growth.backfill(cat)
        trackers.detect(cat)


# ---- All changes --------------------------------------------------------------------------------------

def _filters(cat: Catalogue) -> tuple[int, dict[str, Any], list[str]]:
    views = ch.saved_views(cat)
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 2.5, 1.2])
        if views:
            picked = c1.selectbox("Saved view", ["(none)", *views], key="chg-view")
            if picked != "(none)" and st.session_state.get("_chg_loaded") != picked:
                for k, v in views[picked].items():
                    st.session_state[f"chg-{k}"] = v
                st.session_state["_chg_loaded"] = picked
                st.rerun()
        _default("chg-days", 30)
        _default("chg-categories", [k for k in ch.CATEGORIES if k != "rows"])
        _default("chg-hide_noise", True)
        days = c1.select_slider("Over the last", list(WINDOWS), format_func=WINDOWS.get, key="chg-days")
        cats = c2.multiselect("What", list(ch.CATEGORIES), format_func=ch.CATEGORIES.get, key="chg-categories")
        text = c3.text_input("Contains", key="chg-text", placeholder="name or detail")
        c4, c5, c6, c7 = st.columns(4)
        sources = c4.multiselect("Where", cat.sources(), key="chg-sources")
        kinds = c5.multiselect("Kind of item", [k for k in cat.kinds()], key="chg-kinds")
        projects = {r[0]: r[1] for r in cat.conn.execute("SELECT uid, name FROM bprojects ORDER BY name")}
        project = c6.selectbox("Business project", ["", *projects], format_func=lambda u: projects.get(u, "(any)"),
                               key="chg-project")
        tag_list = ch.tags(cat)
        tag = c7.selectbox("Tag", ["", *tag_list], format_func=lambda t: t or "(any)", key="chg-tag")
        c8, c9, c10, c11 = st.columns(4)
        groups = {r[0]: r[1] for r in cat.conn.execute(
            "SELECT g.uid, g.name FROM item_groups g WHERE g.type = 'versions' AND EXISTS (SELECT 1 FROM group_members "
            "m JOIN assets a ON a.key = m.asset_key WHERE m.group_uid = g.uid AND a.kind IN ('report', 'workbook')) "
            "ORDER BY g.name")}
        group_uid = c8.selectbox("Report group", ["", *groups], format_func=lambda u: groups.get(u, "(any)"),
                                 key="chg-group")
        gold = c9.toggle("Gold standards only", key="chg-gold")
        followed = c10.toggle("Followed items only", key="chg-followed", help="Items pinned with 📌 Follow up")
        noise = c11.toggle("Hide noise", key="chg-hide_noise",
                           help="Leaves out items marked not relevant or old, older versions in a version group, and "
                                f"row counts that moved less than {ch.NOISE_SHARE:.0%}.")
        g1, g2, g3 = st.columns([1, 1, 2])
        fields = ["", *ch.GROUP_FIELDS]
        by1 = g1.selectbox("Group by", fields, format_func=lambda f: ch.GROUP_FIELDS.get(f, "(no grouping)"),
                           key="chg-by1")
        by2 = g2.selectbox("then by", fields, format_func=lambda f: ch.GROUP_FIELDS.get(f, "(nothing)"),
                           key="chg-by2", disabled=not by1)
        with g3.popover("💾 Save this view"):
            name = st.text_input("Name", key="chg-save-name", placeholder="e.g. Plant changes this month")
            if st.button("Save", key="chg-save"):
                try:
                    ch.save_view(cat, name, {k: st.session_state.get(f"chg-{k}") for k in FILTER_KEYS} |
                                 {"days": days, "by1": by1, "by2": by2})
                    st.toast(f"Saved {name}")
                except ValueError as e:
                    st.error(str(e))
            if views:
                gone = st.selectbox("Delete a view", ["", *views], key="chg-del")
                if gone and st.button("Delete", key="chg-del-btn"):
                    ch.delete_view(cat, gone)
                    st.rerun()
    f = {"categories": cats, "sources": sources, "kinds": kinds, "project": project, "tag": tag, "group": group_uid,
         "gold": gold, "followed": followed, "text": text, "hide_noise": noise}
    by = [b for b in (by1, by2 if by1 else "") if b]
    return days, f, by


def _default(key: str, value: Any) -> None:
    """A widget's first value, set through session state so a saved view can replace it without a warning."""
    if key not in st.session_state:
        st.session_state[key] = value


def all_changes_view(cat: Catalogue) -> None:
    import pandas as pd

    from pdcat.export import rows_to_csv
    days, f, by = _filters(cat)
    rows = _feed(cat, days, tuple(sorted(f["categories"] or ch.CATEGORIES)))
    shown = ch.apply_filters(cat, rows, f)
    total = sum(r.get("n", 1) for r in shown)
    st.caption(f"{total:,} change{'s' if total != 1 else ''} on {len({r['item'] for r in shown}):,} items"
               + (f" ({len(rows) - len(shown):,} rows left out by the filters)" if len(rows) != len(shown) else ""))
    if not shown:
        st.info("No changes match. Changes are recorded from the second scan of a source onwards; tracked changes "
                "appear once a change tracker's summary has been read (Change trackers above).")
        return
    if by:
        grouped = ch.group(cat, shown, by)
        st.dataframe(pd.DataFrame(grouped), hide_index=True, width="stretch")
        csv_rows = grouped
    else:
        df = pd.DataFrame([{"when": r["at"][:16].replace("T", " "), "item": r["name"], "kind": r["kind"],
                            "what": ch.CATEGORIES[r["category"]].split(" (")[0], "change": r["change"],
                            "detail": r["detail"], "changes": r.get("n", 1), "where": r["source"]}
                           for r in shown[:5000]])
        if len(shown) > 5000:
            st.caption("Showing the newest 5,000. Narrow the filters or group them to see the rest.")
        event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                             key="chg-table")
        sel = selected_rows(event, len(shown))
        if sel:
            go(shown[sel[0]]["item"])
            st.rerun()
        csv_rows = [{k: v for k, v in r.items() if k != "tracker"} for r in shown]
    st.download_button("⬇️ Download as CSV", rows_to_csv(csv_rows), file_name="changes.csv", mime="text/csv")


def _feed(cat: Catalogue, days: int, cats: tuple[str, ...]) -> list[dict[str, Any]]:
    """The feed, kept for the session until the catalogue changes (filters run on it without reading again)."""
    key = ("_chg_feed", cat.revision(), days, cats, datetime.now(timezone.utc).strftime("%Y-%m-%d %H"))
    got = st.session_state.get("_chg_feed")
    if got and got[0] == key:
        return got[1]
    rows = ch.feed(cat, ch.since_days(days), categories=set(cats))
    st.session_state["_chg_feed"] = (key, rows)
    return rows


# ---- Graphs -------------------------------------------------------------------------------------------

GRAPHS = ("Table growth", "Files in folders", "Report changes", "Catalogue growth", "Scans", "Change volume",
          "Tracked changes")


PER = {"auto": "Auto", **tb.UNIT_LABELS}


def graphs_view(cat: Catalogue) -> None:
    c1, c2, c3 = st.columns([3, 1, 1])
    graph = c1.radio("Graph", GRAPHS, horizontal=True, key="chg-graph", label_visibility="collapsed")
    days = c2.select_slider("Over the last", list(WINDOWS), value=90, format_func=WINDOWS.get, key="chg-gdays")
    c3.selectbox("Per", list(PER), format_func=PER.get, key="chg-per",
                 help="How long each point or bar covers. Auto: days for up to three months, weeks up to about a "
                      "year, then months. Never shorter than a day.")
    since = ch.since_days(days)
    {"Table growth": _table_growth, "Files in folders": _folders, "Report changes": _report_changes,
     "Catalogue growth": _catalogue_growth, "Scans": _scans, "Change volume": _volume,
     "Tracked changes": _tracked}[graph](cat, since)


def _unit(dates, pick: str | None = None) -> str:
    """The bucket for these dates: the Per pick on the Graphs tab, or the automatic one for their span."""
    return tb.unit_for(dates, pick or st.session_state.get("chg-per", "auto"))


def _per_caption(unit: str, what: str = "point") -> None:
    auto = st.session_state.get("chg-per", "auto") not in tb.UNITS
    st.caption(f"One {what} per {unit}{' (picked for the time shown; change it with Per)' if auto else ''}.")


def _local(d: str) -> str:
    """A bucket start as a local midnight, so the graph doesn't move it across a day."""
    return f"{d[:10]}T00:00:00"


def _time_x(x: str, unit: str, dates, *, exact: bool = False):
    """A time axis labelled in days, weeks or months only: tick marks at bucket starts, at most 12."""
    import altair as alt
    ds = [d for d in dates if d]
    values = [alt.DateTime(year=d.year, month=d.month, date=d.day) for d in tb.ticks(min(ds), max(ds), unit)] \
        if ds else alt.Undefined
    axis = alt.Axis(values=values, format=tb.AXIS_FORMAT[unit], labelOverlap=True)
    return alt.X(f"{x}:T", title=None, axis=axis), alt.Tooltip(
        f"{x}:T", title="when" if exact else unit, format="%-d %b %Y %H:%M" if exact else tb.TOOLTIP_FORMAT[unit])


def _line(df, x: str, y: str, color: str, *, unit: str, height: int = 320, points=None, y_title: str | None = None):
    """Readings over time, one point per series per bucket (the last reading in it)."""
    import altair as alt
    import pandas as pd
    df = pd.DataFrame(tb.last_per_bucket(df.to_dict("records"), unit, at=x, series=(color,)))
    if not len(df):
        return
    xenc, xtip = _time_x(x, unit, df[x].tolist())
    df[x] = df[x].map(_local)
    chart = alt.Chart(df).mark_line(point=True, interpolate="step-after").encode(
        x=xenc, y=alt.Y(f"{y}:Q", title=y_title or y),
        color=alt.Color(f"{color}:N", title=None, scale=alt.Scale(range=PALETTE),
                        legend=alt.Legend(orient="bottom", columns=3, labelLimit=320)),
        tooltip=[alt.Tooltip(f"{color}:N"), xtip, alt.Tooltip(f"{y}:Q", format=",")])
    if points is not None and len(points):
        points = points.assign(**{x: points[x].map(lambda a: _local(tb.start(a, unit).isoformat()))})
        marks = alt.Chart(points).mark_point(shape="triangle-up", size=120, filled=True, color="#c0392b").encode(
            x=f"{x}:T", y=f"{y}:Q", tooltip=["label:N"])
        chart = chart + marks
    st.altair_chart(chart.properties(height=height), width="stretch")


def _bars(rows: list[dict[str, Any]], x: str, y: str, color: str, *, unit: str, height: int = 300):
    """Counts per bucket, stacked by `color`; every bucket from the first count to today has a slot, so quiet
    days, weeks or months show as gaps."""
    import altair as alt
    import pandas as pd
    rows = tb.sum_per_bucket(rows, unit, y, at=x, by=(color,))
    if not rows:
        return
    slots = tb.buckets(rows[0][x], max(datetime.now().date().isoformat(), rows[-1][x]), unit)
    order = [tb.label(d, unit) for d in slots]
    df = pd.DataFrame([{**r, "when": tb.label(r[x], unit)} for r in rows])
    chart = alt.Chart(df).mark_bar().encode(
        x=alt.X("when:O", title=None, sort=order, scale=alt.Scale(domain=order),
                axis=alt.Axis(labelAngle=-45, labelOverlap=True, labelExpr=_short_label(unit))),
        y=alt.Y(f"sum({y}):Q", title=y),
        color=alt.Color(f"{color}:N", title=None, scale=alt.Scale(range=PALETTE),
                        legend=alt.Legend(orient="bottom", columns=3, labelLimit=320)),
        tooltip=[alt.Tooltip(f"{color}:N"), alt.Tooltip("when:O", title=unit), alt.Tooltip(f"sum({y}):Q", format=",")])
    st.altair_chart(chart.properties(height=height), width="stretch")
    _per_caption(unit, "bar")


def _scan_time(at: str) -> str:
    """A scan's start as local time without a zone, the way the graph reads it."""
    try:
        return datetime.fromisoformat(at).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return at[:19]


def _short_label(unit: str) -> str:
    """Axis labels without the year for days and weeks (the tooltip has it)."""
    if unit == "month":
        return "datum.label"
    return "replace(replace(datum.label, 'w/c ', ''), / \\d{4}$/, '')"


def _table_growth(cat: Catalogue, since: str) -> None:
    import pandas as pd
    up, down = growth.growers(cat, since, limit=10)
    names = {r[0]: r[1] for r in cat.conn.execute("SELECT DISTINCT r.asset_key, a.name FROM row_counts r JOIN "
                                                  "assets a ON a.key = r.asset_key WHERE a.status = 'active'")}
    if not names:
        st.info("No row counts over time yet. They are kept from 2.9 on, after each scan (and from your change "
                "history for earlier scans).")
        return
    default = [m["key"] for m in (up[:3] + down[:2])] or list(names)[:5]
    keys = st.multiselect("Tables and files", list(names), default=default, format_func=lambda k: names.get(k, k),
                          key="chg-growth-keys")
    if keys:
        series = growth.row_series(cat, keys, since)
        df = pd.DataFrame([{"at": r["at"][:19], "rows": r["rows"], "item": names.get(r["key"], r["key"])}
                           for r in series])
        jumps = [j for j in growth.jumps(cat, keys) if j["at"] >= since]
        pts = pd.DataFrame([{"at": j["at"][:19], "rows": j["to"], "label": f"{names.get(j['key'])}: {j['from']:,} → "
                                                                          f"{j['to']:,}"} for j in jumps])
        if len(df):
            unit = _unit(df["at"])
            _line(df, "at", "rows", "item", unit=unit, points=pts)
            _per_caption(unit)
        if jumps:
            st.caption(f"▲ {len(jumps)} jump(s) of more than half up or down.")
    c1, c2 = st.columns(2)
    for col, title, rows in ((c1, "Growing most", up), (c2, "Shrinking most", down)):
        with col:
            st.markdown(f"**{title}**")
            if not rows:
                st.caption("None in this window.")
            for i, m in enumerate(rows):
                st.button(f"{m['name']}: {m['from']:,} → {m['to']:,} ({m['change']:+.0%})", key=f"gr-{title}-{i}",
                          on_click=go, args=(m["key"],), type="tertiary")


def _folders(cat: Catalogue, since: str) -> None:
    import pandas as pd
    sources = sorted({r[0] for r in cat.conn.execute("SELECT DISTINCT source FROM scan_counts WHERE what LIKE "
                                                     "'folder:%'")})
    if not sources:
        st.info("No folder counts yet. They are kept after each scan of a data or code folder, from 2.9 on.")
        return
    c1, c2, c3 = st.columns(3)
    src = c1.selectbox("Folder source", ["", *sources], format_func=lambda s: s or "(every source)",
                       key="chg-fold-src")
    depth_label = c2.selectbox("Folders", ["Top folders", "Each source as a whole", "Every folder"],
                               key="chg-fold-depth")
    depth = {"Top folders": 1, "Each source as a whole": 0, "Every folder": None}[depth_label]
    measure = c3.radio("Show", ["files", "size"], horizontal=True, key="chg-fold-measure")
    table = growth.folder_table(cat, days=max(1, (datetime.now(timezone.utc) - datetime.fromisoformat(since))
                                              .days), depth=depth, source=src or None)
    top = [(r["source"], r["folder"]) for r in table[:8]]
    series = [r for r in growth.folder_series(cat, src or None, depth, since) if (r["source"], r["folder"]) in top]
    if series:
        df = pd.DataFrame([{"at": r["at"][:19], "files": r["files"],
                            "size": round((r["bytes"] or 0) / 1024 ** 2, 1),
                            "folder": f"{r['source']}/{r['folder']}" if r["folder"] != "." else r["source"]}
                           for r in series])
        unit = _unit(df["at"])
        _line(df, "at", measure, "folder", unit=unit, y_title="files" if measure == "files" else "size (MB)")
        st.caption(f"One point per {unit}. The 8 folders that gained most files in this window. Files only listed (history folders, old "
                   "dated folders) and files in log folders count too.")
    st.dataframe(pd.DataFrame([{"folder": f"{r['source']}/{r['folder']}" if r["folder"] != "." else r["source"],
                                "files": r["files"], "added": r["added"], "per week": r["per_week"],
                                "size": size_label(r["bytes"] or 0), "last scan": r["last_scan"][:10]}
                               for r in table]), hide_index=True, width="stretch")


def _report_changes(cat: Catalogue, since: str) -> None:
    import pandas as pd
    rows = ch.apply_filters(cat, ch.feed(cat, since, categories={"report"}), {"hide_noise": True})
    parts = ch.report_parts(rows, _unit([r["at"] for r in rows]))
    if not parts:
        st.info("No report or workbook changes in this window. A change is recorded when a scan reads a report "
                "that changed since the last scan.")
        return
    _bars(parts, "at", "changes", "part", unit=_unit([r["at"] for r in rows]))
    busiest = ch.group(cat, rows, ["item"])[:15]
    st.markdown("**Changed most**")
    st.dataframe(pd.DataFrame(busiest), hide_index=True, width="stretch")


def _catalogue_growth(cat: Catalogue, since: str) -> None:
    import pandas as pd
    rows = growth.catalogue_series(cat, since)
    if not rows:
        st.info("No counts yet. They are kept after each scan from 2.9 on (and rebuilt from when items were first "
                "seen).")
        return
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["kind"]] = r["items"]
    kinds = sorted(totals, key=lambda k: -totals[k])
    picked = st.multiselect("Kinds", kinds, default=[k for k in kinds if k != "described"][:5] +
                            (["described"] if "described" in kinds else []), key="chg-cat-kinds")
    df = pd.DataFrame([r for r in rows if r["kind"] in picked])
    if len(df):
        unit = _unit(df["day"])
        _line(df, "day", "items", "kind", unit=unit)
        st.caption(f"One point per {unit}. “described” counts items with a description or a purpose you wrote.")


def _scans(cat: Catalogue, since: str) -> None:
    import altair as alt
    import pandas as pd
    rows = [r for r in growth.scan_series(cat) if r["at"] >= since]
    if not rows:
        st.info("No scans in this window.")
        return
    df = pd.DataFrame([{**r, "at": _scan_time(r["at"])} for r in rows])
    xenc, xtip = _time_x("at", _unit([r["at"] for r in rows]), [tb.to_date(r["at"]) for r in rows], exact=True)
    chart = alt.Chart(df).mark_circle(size=70).encode(
        x=xenc, y=alt.Y("minutes:Q"), color=alt.Color("source:N", scale=alt.Scale(range=PALETTE),
                                                     legend=alt.Legend(orient="bottom")),
        size=alt.Size("items:Q", legend=None), tooltip=["source", xtip, "minutes", alt.Tooltip("items:Q", format=",")])
    st.altair_chart(chart.properties(height=300), width="stretch")
    st.caption("Each dot is a scan: how long it took, sized by how many items it found.")


def _volume(cat: Catalogue, since: str) -> None:
    rows = ch.apply_filters(cat, ch.feed(cat, since), {"hide_noise": True})
    unit = _unit([r["at"] for r in rows])
    vol = ch.volume(rows, unit)
    if not vol:
        st.info("No changes in this window.")
        return
    _bars(vol, "at", "changes", "what", unit=unit)


def _tracked(cat: Catalogue, since: str) -> None:
    rows = ch.tracked_rows(cat, since)
    if not rows:
        st.info("No tracked changes yet. Read a change tracker's summary under Change trackers; the columns you "
                "watch show here.")
        return
    _bars([{"day": r["at"][:10], "changes": r["n"], "what": f"{r['name']} · {r['change']}"} for r in rows],
          "day", "changes", "what", unit=_unit([r["at"] for r in rows]))


# ---- Change trackers ----------------------------------------------------------------------------------

def trackers_view(cat: Catalogue) -> None:
    st.caption("Files and tables that record changes to another table: one row per changed value (like "
               "ChangesX_TABLEPlant.csv) or copies of whole rows with a change date (CDC, history tables). Link each "
               "one to the table it tracks, then read its change summary.")
    with st.expander("⚙️ How trackers are found"):
        pre = ", ".join(trackers.prefixes(cat))
        new = st.text_input("Name prefixes that mark a tracker (comma between them)", pre, key="trk-prefixes",
                            help="The part of the name before the tracked table's name, e.g. ChangesX_ in "
                                 "ChangesX_TABLEPlant.csv. Names with words like changes, audit, history or _CT "
                                 "are looked at too.")
        auto = st.toggle("Bring file trackers you've read up to date after each scan",
                         value=bool(cat.setting("tracker_auto_read", True)), key="trk-auto",
                         help="Only the rows from the last change date read onwards are read again. SQL trackers "
                              "are only read when you press Read.")
        if st.button("Save and look again", key="trk-save"):
            cat.set_setting("tracker_prefixes", [p.strip() for p in new.split(",") if p.strip()])
            cat.set_setting("tracker_auto_read", auto, quiet=True)
            n = trackers.detect(cat)
            st.toast(f"{n} tracker(s) found")
            st.rerun()
    found = trackers.list_trackers(cat)
    hidden = [t for t in trackers.list_trackers(cat, include_not=True) if t["not_tracker"]]
    if not found:
        st.info("No change trackers found yet. Scan the folder they're in, or add your file name prefix above.")
    unlinked = sum(1 for t in found if not any(link["state"] == "confirmed" for link in t["links"]))
    if found:
        st.caption(f"{len(found)} tracker(s)" + (f", {unlinked} not linked to a table yet" if unlinked else ""))
    names = {t["key"]: t["name"] for t in found}
    pick = st.selectbox("Tracker", list(names), format_func=lambda k: names[k], key="trk-pick") if found else None
    for t in found:
        if t["key"] == pick:
            tracker_card(cat, t)
    if hidden:
        with st.expander(f"Marked as not a tracker ({len(hidden)})"):
            for i, t in enumerate(hidden):
                c1, c2 = st.columns([4, 1])
                c1.caption(t["name"])
                if c2.button("It is one", key=f"trk-undo-{i}"):
                    trackers.set_not_tracker(cat, t["key"], False)
                    trackers.detect(cat)
                    st.rerun()


def tracker_card(cat: Catalogue, t: dict[str, Any]) -> None:
    import pandas as pd
    s = t["shape"]
    k = t["key"]
    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        c1.button(f"{icon(t['kind'])} {t['name']}", key=f"trk-open-{k}", on_click=go, args=(k,), type="tertiary")
        if c2.button("Not a tracker", key=f"trk-not-{k}", help="Stop treating this as a change tracker"):
            trackers.set_not_tracker(cat, k, True)
            st.rerun()
        roles = [f"{label} **{s[r]}**" for r, label in (
            ("date", "date"), ("column", "changed column"), ("label", "field name"), ("before", "before"),
            ("after", "after"), ("table", "table"), ("key", "key"), ("operation", "operation")) if s.get(r)]
        layout = {"long": "one row per changed value", "wide": "copies of whole rows"}.get(s.get("layout"), "unknown")
        st.caption(f"Layout: {layout} · " + " · ".join(roles) + (f" · found because: {s['why']}" if s.get("why") else ""))
        made = trackers.made_by(cat, k)
        if made:
            st.caption("Made by " + ", ".join(m["name"] for m in made))
        if s.get("link_error"):
            st.warning(f"Couldn't suggest a table for this tracker last time ({s['link_error']}). Link it by hand "
                       "below; the other trackers were still linked.")
        _links_box(cat, t)
        read_col, note_col = st.columns([1, 3])
        label = "🔄 Read change summary" if not t["read_at"] else "🔄 Read new changes"
        if read_col.button(label, key=f"trk-read-{k}", type="primary" if not t["read_at"] else "secondary",
                           help="Counts changes per day and column. A file is read in chunks; after the first time "
                                "only rows from the last change date onwards are read. A SQL table gets one paced, "
                                "read-only GROUP BY query (in the query log)."):
            _read(cat, t, full=False)
        if t["read_at"]:
            note_col.caption(f"Read {t['read_at'][:16].replace('T', ' ')} · {t['read_note'] or ''}")
            if note_col.button("Read it all again", key=f"trk-full-{k}", type="tertiary"):
                _read(cat, t, full=True)
        if not t["changes"]:
            return
        st.markdown(f"**Changes by column** · {t['changes']:,} changes from {t['first_day']} to {t['last_day']}")
        window = st.select_slider("Over the last", list(WINDOWS), value=3650, format_func=WINDOWS.get,
                                  key=f"trk-days-{k}")
        since = ch.since_days(window) if window != 3650 else None
        cols = trackers.column_counts(cat, k, since)
        df = pd.DataFrame([{"⭐": "⭐" if c["watched"] else "", "column": c["column"],
                            "name": c["label"] if c["label"] != c["column"] else "", "changes": c["changes"],
                            "share": f"{c['share']:.1%}", "changed": c["changed"], "new": c["new"],
                            "removed": c["removed"], "keys affected": c["keys"], "last": c["last"]} for c in cols])
        st.dataframe(df, hide_index=True, width="stretch", key=f"trk-cols-{k}")
        everything = [c["column"] for c in trackers.column_counts(cat, k)]
        now_watched = [c for c in trackers.watched(cat, k) if c in everything]
        labels = s.get("labels") or {}
        picked = st.multiselect("⭐ Columns you watch", everything, default=now_watched, key=f"trk-watch-{k}",
                                format_func=lambda c: f"{c} ({labels[c]})" if labels.get(c) and labels[c] != c else c,
                                help="Watched columns show in All changes, the graphs and the table's History tab. "
                                     "Until you pick, the two that change most are watched.")
        if sorted(picked) != sorted(now_watched):
            trackers.set_watch(cat, k, picked)
            st.rerun()
        if s.get("keys_capped"):
            st.caption(f"Keys affected are counted for the first {trackers.KEY_CAP:,} keys only.")
        days = trackers.daily(cat, k, picked or None, since)
        if days:
            _bars([{"day": d["day"], "changes": d["n"], "column": labels.get(d["column"]) or d["column"]}
                   for d in days], "day", "changes", "column", unit=_unit([d["day"] for d in days]), height=240)


def _read(cat: Catalogue, t: dict[str, Any], full: bool) -> None:
    from .scanning import _sources
    bar = st.progress(0.0, text="Reading…")

    def report(text: str, done: int | None = None, total: int | None = None) -> None:
        bar.progress(min(1.0, (done or 0) / total) if total else 0.0, text=text.strip())
    try:
        r = trackers.read_changes(cat, t["key"], report, full=full, sources=_sources(cat))
        bar.empty()
        st.toast(f"{r['changes']:,} changes counted from {r['rows']:,} rows")
        st.rerun()
    except Exception as e:
        bar.empty()
        st.error(f"Couldn't read it: {type(e).__name__}: {e}")


def _links_box(cat: Catalogue, t: dict[str, Any]) -> None:
    k = t["key"]
    links = [link for link in t["links"] if link["state"] != "rejected"]
    if not links:
        st.caption("Not linked to a table yet.")
    for i, link in enumerate(links):
        c1, c2, c3 = st.columns([3, 3, 1.2])
        mark = {"confirmed": "✅", "suggested": {"likely": "🟢", "check": "🟡"}.get(link["band"] or "", "⚪")}[
            link["state"]]
        c1.button(f"{mark} tracks {link['table_name'] or link['table_key']}", key=f"trk-l-{k}-{i}", on_click=go,
                  args=(link["table_key"],), type="tertiary")
        c2.caption(link["reason"] or "")
        if link["state"] == "suggested":
            a, b = c3.columns(2)
            if a.button("✓", key=f"trk-ok-{k}-{i}", help="Yes, it tracks this table"):
                trackers.set_link(cat, k, link["table_key"], "confirmed")
                st.rerun()
            if b.button("✗", key=f"trk-no-{k}-{i}", help="No; don't suggest it again"):
                trackers.set_link(cat, k, link["table_key"], "rejected")
                st.rerun()
        elif c3.button("Unlink", key=f"trk-un-{k}-{i}", type="tertiary"):
            trackers.set_link(cat, k, link["table_key"], "rejected")
            st.rerun()
    with st.popover("🔗 Link to a table by hand"):
        text = st.text_input("Table name", key=f"trk-find-{k}")
        if text:
            hits = [dict(h) for h in cat.search(text, limit=20, kinds=("table", "view", "data_file"))]
            for j, h in enumerate(hits[:10]):
                if st.button(f"{icon(h['kind'])} {h['name']}", key=f"trk-pickt-{k}-{j}"):
                    trackers.set_link(cat, k, h["key"], "confirmed")
                    st.rerun()


# ---- the item page: History tab -----------------------------------------------------------------------

def item_history_box(cat: Catalogue, key: str, asset: dict) -> None:
    """Row counts over time, the changes trackers recorded for this table, and, on a tracker, its summary."""
    import pandas as pd
    series = growth.row_series(cat, [key])
    if len(series) >= 2:
        with st.container(border=True):
            st.markdown("**Rows over time**")
            df = pd.DataFrame([{"at": r["at"][:19], "rows": r["rows"], "item": asset["name"]} for r in series])
            jumps = growth.jumps(cat, [key])
            pts = pd.DataFrame([{"at": j["at"][:19], "rows": j["to"], "label": f"{j['from']:,} → {j['to']:,}"}
                                for j in jumps])
            _line(df, "at", "rows", "item", unit=_unit(df["at"], "auto"), height=180, points=pts)
    if asset["kind"] in ("table", "view", "data_file"):
        summary = trackers.table_summary(cat, key)
        for t in summary:
            with st.container(border=True):
                state = "" if t["state"] == "confirmed" else " (suggested link)"
                st.markdown(f"**Tracked changes** from {t['name']}{state}: {t['changes']:,}")
                watched = [c for c in t["columns"] if c["watched"]] or t["columns"][:2]
                if watched:
                    st.caption(" · ".join(f"⭐ {c['label']}: {c['changes']:,} (last {c['last'] or '?'})"
                                          for c in watched))
                others = [c for c in t["columns"] if c not in watched][:8]
                if others:
                    st.caption("Also: " + ", ".join(f"{c['label']} {c['changes']:,}" for c in others))
                if not t["columns"]:
                    st.caption("Its change summary hasn't been read yet.")
                st.button("Open in Change tracking", key=f"hist-trk-{key}-{t['tracker_key']}",
                          on_click=_open_tracker, args=(t["tracker_key"],), type="tertiary")
    if trackers.is_tracker(cat, key):
        with st.container(border=True):
            st.markdown("**This is a change tracker**")
            cols = trackers.column_counts(cat, key)
            if cols:
                st.caption(" · ".join(f"{c['label']}: {c['changes']:,}" for c in cols[:8]))
            st.button("Open in Change tracking", key=f"hist-self-{key}", on_click=_open_tracker, args=(key,),
                      type="tertiary")


def _open_tracker(key: str) -> None:
    st.session_state["trk-pick"] = key
    st.session_state["sub-Change tracking"] = "Change trackers"
    go_page("Change tracking")


def profile_history_box(cat: Catalogue, key: str) -> None:
    """On the Columns tab: how each column's profile moved over the profiles kept."""
    import pandas as pd
    rows = growth.profile_history(cat, key)
    if len({r["at"] for r in rows}) < 2:
        return
    drift = {(d["column"], d["at"]): d["detail"] for d in growth.profile_drift(cat, keys={key})}
    with st.expander(f"Profile history ({len({r['at'] for r in rows})} profiles kept)"):
        columns = sorted({r["column_name"] for r in rows}, key=str.lower)
        col = st.selectbox("Column", columns, key=f"ph-col-{key}")
        mine = [r for r in rows if r["column_name"] == col]
        st.dataframe(pd.DataFrame([{"profiled": r["at"][:10], "rows": r["row_count"], "nulls": r["null_count"],
                                    "distinct": r["distinct_count"], "min": r["min_value"], "max": r["max_value"],
                                    "mean": r["mean_value"], "moved": drift.get((col, r["at"]), "")}
                                   for r in reversed(mine)]), hide_index=True, width="stretch")


# ---- Add › Sources › Profiling ------------------------------------------------------------------------

def profiling_page(cat: Catalogue) -> None:
    import pandas as pd

    from pdcat import profilerules
    page_title("Profiling")
    st.caption("Pick the tables to profile and how often. A rule only adds a table to the queue when it's due "
               "(checked after each scan); the queue waits for your approval under SQL scanning (unless you say "
               "below that a rule is your approval), and runs at the same careful pace. Every profile is kept in a "
               "compact form, so you can see how a column moved.")
    _profiling_timing(cat, profilerules)
    rows = profilerules.profiling_list(cat)
    if not rows:
        st.info("No SQL Server tables scanned yet.")
        return
    c1, c2, c3, c4 = st.columns(4)
    text = c1.text_input("Contains", key="prof-text")
    only_used = c2.toggle("Only tables my code uses", key="prof-used")
    dbs = sorted({r["database"] for r in rows if r["database"]})
    db = c3.selectbox("Database", ["", *dbs], format_func=lambda d: d or "(all)", key="prof-db")
    rule_f = c4.selectbox("Rule", ["", "(none)", *profilerules.RULES], key="prof-rule",
                          format_func=lambda r: profilerules.RULES.get(r, r or "(any)"))
    shown = [r for r in rows if (not text or text.lower() in r["name"].lower()) and (not only_used or r["used"])
             and (not db or r["database"] == db)
             and (not rule_f or (r["rule"] == "" if rule_f == "(none)" else r["rule"] == rule_f))]
    df = pd.DataFrame([{"table": r["name"], "database": r["database"], "rows": r["rows"], "used by code": r["used"],
                        "rule": profilerules.RULES.get(r["rule"], ""), "last profiled": r["last_profiled"],
                        "next due": r["next_due"], "profiles kept": r["profiles_kept"], "queue": r["queue"]}
                       for r in shown])
    st.caption(f"{len(shown):,} of {len(rows):,} tables. Tick rows, then pick a rule.")
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
                         key=selection_key("prof-table", [r["key"] for r in shown]))
    picked = [shown[i]["key"] for i in selected_rows(event, len(shown))]
    b1, b2, b3, b4 = st.columns([2, 1, 1, 1])
    rule = b1.selectbox("Rule for the ticked tables", list(profilerules.RULES), format_func=profilerules.RULES.get,
                        key="prof-set")
    if b2.button(f"Set for {len(picked)}" if picked else "Set", disabled=not picked, key="prof-set-btn"):
        profilerules.set_rule(cat, picked, rule)
        st.toast(f"{len(picked)} table(s) set to {profilerules.RULES[rule].lower()}")
        st.rerun()
    if b3.button("Clear the rule", disabled=not picked, key="prof-clear"):
        profilerules.set_rule(cat, picked, None)
        st.rerun()
    if b4.button("Queue ticked now", disabled=not picked, key="prof-queue-now",
                 help="Puts the ticked tables in the queue today, whatever their rule says. They still wait for your "
                      "approval under SQL scanning. A weekly rule then counts from this profile."):
        from pdcat.pacing import request_profiles
        n = request_profiles(cat, picked)
        st.toast(f"Queued {n}; approve them under SQL scanning" if n else "Already waiting in the queue")
        st.rerun()
    due = profilerules.due(cat)
    if due:
        c1, c2 = st.columns([3, 1])
        c1.caption(f"{len(due)} table(s) are due now.")
        if c2.button("Queue them now", key="prof-due"):
            n = profilerules.request_due(cat)
            st.toast(f"Queued {n}; approve them under SQL scanning")
            st.rerun()
    drift = growth.profile_drift(cat, ch.since_days(90))
    if drift:
        with st.expander(f"Profiles that moved a lot in the last 90 days ({len(drift)})"):
            names = {r["key"]: r["name"] for r in rows}
            for i, d in enumerate(drift[:50]):
                st.button(f"{names.get(d['asset_key'], d['asset_key'])} · {d['detail']} ({d['at'][:10]})",
                          key=f"prof-drift-{i}", on_click=go, args=(d["asset_key"],), type="tertiary")


def _profiling_timing(cat: Catalogue, profilerules) -> None:
    """When weekly rules come round, and whether a rule counts as your approval."""
    day = profilerules.weekly_day(cat)
    approves = profilerules.rule_approves(cat)
    label = (f"Weekly on {profilerules.DAYS[day]}" if day is not None else "Weekly every 7 days") \
        + (", a rule is your approval" if approves else ", approve under SQL scanning")
    with st.expander(f"When rules run: {label}"):
        options = [None, *range(7)]
        pick = st.selectbox("Weekly rules are due", options, index=options.index(day), key="prof-weekday",
                            format_func=lambda d: "7 days after the last profile" if d is None
                            else f"every {profilerules.DAYS[d]}",
                            help="Pinned to a day, a weekly table is due from the start of that day (this computer's "
                                 "time), even if its last profile was fewer than 7 days ago. If no scan runs that "
                                 "day, the next scan catches it up.")
        ok = st.checkbox("A rule is my approval: tables with a rule go into the queue already approved",
                         value=approves, key="prof-rule-approves",
                         help="So the scheduled scan (Settings › Scan on a schedule), which runs `pdcat scan` then "
                              "`pdcat profile --queue`, profiles them without you approving each week. Tables you "
                              "queue by hand still wait for you.")
        st.caption("A run profiles at most the 'Most tables profiled per run' in Settings (default 25). If more "
                   "tables come due on the same day, the rest stay approved and go in the next run.")
        if pick != day or ok != approves:
            cat.set_setting(profilerules.WEEKLY_DAY, pick)
            cat.set_setting(profilerules.RULE_APPROVES, ok)
            st.rerun()


# ---- Settings › Database size -------------------------------------------------------------------------

def database_size_page(cat: Catalogue) -> None:
    import pandas as pd

    from pdcat import dbsize
    st.title("Database size")
    size = dbsize.file_size(cat)
    st.metric("Catalogue file", size_label(size))
    if size >= dbsize.WARN_BYTES:
        st.warning("The catalogue is over 1 GB. Tidy up below; if it stays big, the table list shows what takes "
                   "the space.")
    st.caption(f"`{cat.path}`")
    sizes = dbsize.table_sizes(cat)
    df = pd.DataFrame([{"what": s["label"], "rows": s["rows"],
                        "size": size_label(s["bytes"]) if s["bytes"] is not None else ""} for s in sizes[:25]])
    st.dataframe(df, hide_index=True, width="stretch")
    est = dbsize.yearly_estimate(cat)
    st.caption("Change tracking adds roughly " + ", ".join(f"{size_label(v)} a year for {k}" for k, v in est.items())
               + ", before thinning. Change trackers keep counts per day and column, never the data itself.")
    with st.container(border=True):
        st.markdown("**🧹 Tidy up**")
        c1, c2 = st.columns(2)
        full = c1.number_input("Keep every reading for (days)", 30, 3650,
                               int(cat.setting("history_full_days", growth.KEEP_FULL_DAYS)), key="db-full")
        weekly = c2.number_input("Then one a week up to (days)", 60, 3650,
                                 int(cat.setting("history_weekly_days", growth.KEEP_WEEKLY_DAYS)), key="db-weekly")
        st.caption("After that, one reading a month is kept. Thins row counts over time, folder counts and profile "
                   f"copies, keeps the newest {dbsize.KEEP_QUERY_LOG:,} queries in the query log, keeps one row-count "
                   "line a month in change history older than a year, then compacts the file. Your notes, "
                   "descriptions and decisions are never touched. It can take a minute on a big catalogue, and "
                   "needs no scan to be running.")
        last = cat.setting("tidied_at")
        if last:
            st.caption(f"Last tidied {last[:16].replace('T', ' ')} UTC")
        if st.button("Tidy up now", type="primary", key="db-tidy"):
            cat.set_setting("history_full_days", int(full), quiet=True)
            cat.set_setting("history_weekly_days", int(max(weekly, full)), quiet=True)
            with st.spinner("Tidying up…"):
                try:
                    r = dbsize.tidy_up(cat)
                    removed = ", ".join(f"{v:,} {k.replace('_', ' ')}" for k, v in r["removed"].items() if v)
                    st.success(f"{size_label(r['before'])} → {size_label(r['after'])}. Removed " +
                               (removed or "nothing") + ".")
                except Exception as e:
                    st.error(f"Couldn't tidy up: {type(e).__name__}: {e}. If a scan is running, try again after it.")


def recent_changes_shortcut(cat: Catalogue) -> None:
    """What's new › Recent changes: the newest changes, and the way into Change tracking."""
    st.button("📈 Open Change tracking: every change with filters, grouping and graphs", key="recent-open-chg",
              on_click=go_page, args=("Change tracking",), type="primary")
    from .lists import changes_page
    changes_page(cat)
