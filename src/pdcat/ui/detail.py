"""One item's page: what it is, what you know about it, its columns and values, what uses it, notes."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from pdcat import curation
from pdcat.review import open_tasks
from pdcat.store import NOTE_TYPES, Catalogue

from .common import (NOTE_ICONS, RELATION_LABELS, asset_button, badges, go, icon, kind_label, note_meta,
                     result_list, size_label)

TABLE_LIKE = ("table", "view", "data_file", "file_series")


def detail(cat: Catalogue, key: str) -> None:
    st.button("← Back", on_click=go, args=(None,))
    from pdcat.followups import viewed
    viewed(cat, key)
    from . import pages_for_key
    special = pages_for_key(key)
    if special:
        special(cat, key)
        from .item_boxes import history_box
        history_box(cat, key)
        return
    asset = cat.get_asset(key)
    if asset is None:
        # Notes can be attached to files and unmatched tables that aren't scanned assets.
        st.title(f"{icon('note')} {key}")
        st.caption("Not a scanned item. You can still keep notes on it.")
        notes_section(cat, key, [])
        return

    attrs = asset["attrs"]
    info = curation.item_info(cat, key)
    st.title(f"{icon(asset['kind'])} {asset['name']}")
    meta = [kind_label(asset["kind"]), asset["source"]]
    if attrs.get("database"):
        meta.append(attrs["database"])
    if attrs.get("row_count") is not None:
        approx = "" if attrs.get("row_count_exact", True) else "≈ "
        meta.append(f"{approx}{attrs['row_count']:,} rows")
    if attrs.get("size_bytes") is not None:
        meta.append(size_label(attrs["size_bytes"]))
    if attrs.get("modified"):
        meta.append(f"modified {str(attrs['modified'])[:16].replace('T', ' ')} UTC")
    if attrs.get("lines"):
        meta.append(f"{attrs['lines']} lines")
    meta += badges(info)
    if asset["kind"] in ("data_file", "file_series", "workbook", "report", "model"):
        from pdcat.freshness import badge as fresh_badge
        meta.append(fresh_badge(cat, key))
    from pdcat.folderroles import role_of, stored
    role = role_of(attrs.get("full_path") or attrs.get("path"), stored(cat))
    if role:
        meta.append(f"📂 in a {role} folder")
    st.caption(" · ".join(str(m) for m in meta if m) + f"  \n`{key}`")
    from .item_boxes import groups_box, item_actions, suggestions_box
    item_actions(cat, key, info)
    groups_box(cat, key)
    if info.get("lifecycle") in ("deprecated", "retired"):
        msg = f"This is marked **{info['lifecycle']}**."
        if info.get("replaced_by"):
            msg += f" Use **{info['replaced_by']}** instead."
        st.warning(msg)
    if info.get("trust") == "do not use":
        st.error("Marked **don't use**. See the notes for why.")
    if asset["status"] == "missing":
        st.warning("Not found in the latest scan. It may have been dropped, renamed or deleted.")
    if attrs.get("imported_from"):
        st.info(f"From {attrs['imported_from']}'s scan, merged from a share file. Scan the source yourself to "
                "replace it with your own.", icon="📥")
    if attrs.get("parse_error"):
        st.warning(f"Couldn't fully read this file: {attrs['parse_error']}")
    if attrs.get("read_error"):
        st.warning(f"Couldn't read this data file: {attrs['read_error']}")
    if attrs.get("path") and asset["kind"] in ("data_file", "folder", "project", "workbook", "report"):
        st.caption(f"📍 `{attrs['path']}`")
    if info.get("purpose"):
        st.markdown(f"**Purpose:** {info['purpose']}")
    if info.get("grain"):
        st.markdown(f"**One row per:** {info['grain']}")
    if asset["description"]:
        st.markdown(asset["description"])
    facts = [f"🔄 refreshed {info['refresh']}" if info.get("refresh") else "",
             f"👤 owner {info['owner']}" if info.get("owner") else ""]
    if any(facts):
        st.caption(" · ".join(f for f in facts if f))

    from .extras import item_extras_top
    item_extras_top(cat, key, asset)
    if asset["kind"] in ("report", "measure", "workbook"):
        from .office_pages import office_section
        office_section(cat, key, asset)

    suggestions_box(cat, key)
    mine = open_tasks(cat, asset_key=key, limit=1000)
    if mine:
        from .review_pages import task_card
        with st.container(border=True):
            st.markdown(f"**📋 To review ({len(mine)})**")
            for t in mine:
                task_card(cat, t, show_link=False)
    if asset["kind"] == "sql_snippet":
        from .sources import snippet_form
        st.code(attrs.get("sql", ""), language="sql")
        if attrs.get("tags"):
            st.caption(" ".join(f"#{t.strip()}" for t in attrs["tags"].split(",") if t.strip()))
        with st.expander("Edit or delete this SQL"):
            snippet_form(cat, asset)

    about_form(cat, key, asset, info)
    columns = cat.columns(key)
    notes_section(cat, key, [c["name"] for c in columns])

    if columns and not cat.profile(key):
        columns_section(cat, key, columns)

    if asset["kind"] in TABLE_LIKE:
        profile_section(cat, key, asset)
        compare_section(cat, key)

    lineage_section(cat, key, asset)
    from .sql_pages import queries_in_box, renames_box, using_table_box
    renames_box(cat, key)
    if asset["kind"] in ("table", "view", "procedure"):
        using_table_box(cat, key)
    if asset["kind"] in ("script", "notebook", "sql_script", "workbook", "report", "access_db", "view", "procedure"):
        queries_in_box(cat, key)

    from .extras import item_extras_bottom
    item_extras_bottom(cat, key, asset)

    if asset["kind"] in ("script", "notebook", "sql_script"):
        from .item_boxes import called_by_box, unknown_files_box
        from .sources_extra import script_runs_box
        script_runs_box(cat, key)
        called_by_box(cat, key)
        unknown_files_box(cat, key)
    if asset["kind"] == "access_db":
        from .sources_extra import access_section
        access_section(cat, key, asset)
    if asset["kind"] in ("script", "notebook") and attrs.get("function_info"):
        from .code import functions_section
        functions_section(cat, key, asset)

    if asset["kind"] in ("database", "project"):
        from .naming_box import naming_box
        naming_box(cat, key)
    if asset["kind"] == "project":
        from .code import diagram_block, environment_section
        environment_section(cat, asset)
        from .sources_extra import project_env_box
        project_env_box(cat, key)
        with st.expander("Code map: how this connects to your libraries and data"):
            diagram_block(cat, asset["source"], f"proj-{key}")

    children = [c for c in cat.children(key) if c["kind"] != "series_file"]
    if children:
        st.subheader("Contains")
        result_list([{**dict(c), "source": "", "note_count": 0} for c in children], "child")

    from .item_boxes import history_box
    history_box(cat, key)
    with st.expander("Details"):
        if attrs.get("definition"):
            st.code(attrs["definition"], language="sql")
        for label, field in (("Imports", "imports"), ("Functions", "functions"), ("Classes", "classes")):
            if attrs.get(field):
                st.markdown(f"**{label}:** " + ", ".join(f"`{x}`" for x in attrs[field]))
        hidden = ("definition", "imports", "functions", "classes", "function_info", "import_map", "environment",
                  "import_details", "queries", "measures", "model", "files", "pages", "tables", "relationships",
                  "sheets", "connections", "pivots", "vba_sql", "expression", "used_on", "unknown_files", "runs",
                  "write_patterns", "full_path", "git", "linked", "sql_strings")
        rest = {k: v for k, v in attrs.items() if k not in hidden and v not in (None, "", [])}
        st.json(rest, expanded=False)
        st.caption(f"First seen {asset['first_seen'][:10]} · last seen {asset['last_seen'][:10]}")


def about_form(cat: Catalogue, key: str, asset: dict | None, info: dict) -> None:
    """Purpose, grain, refresh, status, trust, owner and sensitivity: what you know, kept through rescans."""
    filled = sum(bool(info.get(f)) for f in ("purpose", "grain", "owner", "refresh"))
    with st.expander("✏️ About this item" + ("" if filled else " (not filled in yet)"), expanded=False):
        from pdcat.drafts import draft_info
        draft = draft_info(cat, key) if not filled else {}
        if draft:
            st.caption("Drafted from what the scan found. Check it, change it and save.")
        with st.form(f"about-{key}"):
            purpose = st.text_area("Purpose: what it's for and when to use it", value=info.get("purpose") or
                                   draft.get("purpose", ""), height=80)
            c1, c2 = st.columns(2)
            grain = c1.text_input("Grain: one row per…", value=info.get("grain") or draft.get("grain", ""),
                                  placeholder="store per day")
            refresh_opts = curation.REFRESH
            current = info.get("refresh") or draft.get("refresh", "")
            refresh = c2.selectbox("Refreshed", refresh_opts,
                                   index=refresh_opts.index(current) if current in refresh_opts else 0)
            c3, c4, c5 = st.columns(3)
            lifecycle = c3.selectbox("Status", curation.LIFECYCLE,
                                     index=curation.LIFECYCLE.index(info.get("lifecycle") or ""))
            trust = c4.selectbox("Trust", curation.TRUST, index=curation.TRUST.index(info.get("trust") or ""),
                                 format_func=lambda t: {"": "", "trusted": "✅ trusted", "caution": "⚠️ caution",
                                                        "do not use": "🚫 don't use"}[t])
            owner = c5.text_input("Owner", value=info.get("owner") or "", placeholder="who to ask")
            replaced_by = st.text_input("Replaced by (if deprecated)", value=info.get("replaced_by") or "")
            c6, c7 = st.columns(2)
            freshness = ""
            cols = [c["name"] for c in cat.columns(key)]
            if cols:
                opts = ["", *cols]
                cur = info.get("freshness_column") or ""
                freshness = c6.selectbox("Main date column (for freshness)", opts,
                                         index=opts.index(cur) if cur in opts else 0)
            sensitive = c7.checkbox("🔒 Whole item is sensitive", value=bool(info.get("sensitive")),
                                    help="Values are never stored or shown, and it's left out of every export.")
            if st.form_submit_button("Save", type="primary"):
                curation.set_item_info(cat, key, purpose=purpose, grain=grain, refresh=refresh, lifecycle=lifecycle,
                                       trust=trust, owner=owner, replaced_by=replaced_by,
                                       freshness_column=freshness, sensitive=sensitive)
                st.toast("Saved")
                st.rerun()


def columns_section(cat: Catalogue, key: str, columns) -> None:
    st.subheader("Columns")
    col_notes: dict[str, list[str]] = {}
    for n in cat.notes(key):
        _, _, col = n["asset_key"].partition("#")
        if col:
            col_notes.setdefault(col, []).append(n["body"])
    mine = curation.column_info(cat, key)
    from pdcat.keys import columns_with_keys
    keyed = columns_with_keys(cat, key)
    df = pd.DataFrame([{
        "column": c["name"], "type": c["data_type"], "nullable": bool(c["nullable"]),
        "key": _key_badge(keyed.get(c["name"])),
        "description": (mine.get(c["name"], {}).get("description") or c["description"] or ""),
        "sensitive": bool(mine.get(c["name"], {}).get("sensitive")),
        "notes": " | ".join(col_notes.get(c["name"], [])),
    } for c in columns])
    column_editor(cat, key, df)


def _key_badge(k: dict | None) -> str:
    if not k:
        return ""
    return f"🔑 {k['key']}" if k["state"] == "confirmed" else f"🔑? {k['key']}"


def column_editor(cat: Catalogue, key: str, df: pd.DataFrame) -> None:
    """Edit descriptions and sensitivity in the table itself; Save writes only what changed.
    Also where a column is marked as a company-wide key."""
    from .keys_pages import mark_key_form
    mark_key_form(cat, key, list(df["column"]))
    edited = st.data_editor(df, hide_index=True, width="stretch", key=f"coled-{key}",
                            disabled=[c for c in df.columns if c not in ("description", "sensitive")],
                            column_config={"sensitive": st.column_config.CheckboxColumn(
                                "🔒", help="Sensitive: values are never stored, shown or exported")})
    changed = [(r["column"], r["description"], r["sensitive"]) for (_, r), (_, o) in
               zip(edited.iterrows(), df.iterrows()) if r["description"] != o["description"]
               or r["sensitive"] != o["sensitive"]]
    if changed:
        c1, c2 = st.columns([1, 3])
        also = c2.checkbox("Also describe same-named columns elsewhere that have no description", value=True,
                           key=f"coled-everywhere-{key}")
        if c1.button(f"Save {len(changed)} change(s)", type="primary", key=f"coled-save-{key}"):
            everywhere = False
            for col, desc, sens in changed:
                curation.set_column_info(cat, key, col, description=desc, sensitive=bool(sens), rebuild=False)
                if also and desc:
                    curation.describe_everywhere(cat, col, desc, only_empty=True)
                    everywhere = True
            if not everywhere:  # describe_everywhere already rebuilt the search index
                cat.reindex_search(key)
            st.session_state.pop(f"coled-{key}", None)
            st.rerun()


def profile_section(cat: Catalogue, key: str, asset: dict) -> None:
    profile = cat.profile(key)
    st.subheader("Columns and values")
    if not profile:
        if asset["kind"] in ("table", "view"):
            from .scanning import profile_request_button
            st.caption("No value profile yet. It only reads, at the pace set on the Settings page.")
            profile_request_button(cat, key)
        return
    st.caption(f"Measured {profile[0]['profiled_at'][:16].replace('T', ' ')} UTC · {profile[0]['method']}")
    descriptions = {c["name"]: c["description"] for c in cat.columns(key)}
    mine = curation.column_info(cat, key)
    for name, m in mine.items():
        if m.get("description"):
            descriptions[name] = m["description"]
    df = profile_frame(profile, descriptions)
    from pdcat.keys import columns_with_keys
    keyed = columns_with_keys(cat, key)
    df.insert(2, "key", [_key_badge(keyed.get(p["column_name"])) for p in profile])
    hints = [", ".join(h["text"] for h in json.loads(p["hints"] or "[]")) for p in profile]
    if any(hints):
        df["type check"] = hints
    df["sensitive"] = [bool(mine.get(p["column_name"], {}).get("sensitive")) for p in profile]
    column_editor(cat, key, df)
    charted = [p for p in profile if p["histogram"]]
    if charted:
        from .charts import range_chart
        with st.expander(f"Range charts ({len(charted)} columns)"):
            cols = st.columns(3)
            for i, p in enumerate(charted):
                with cols[i % 3]:
                    st.caption(f"**{p['column_name']}** · {p['data_type'] or ''}")
                    range_chart(p)


def profile_frame(profile, descriptions: dict | None = None) -> pd.DataFrame:
    rows = []
    for p in profile:
        n = p["row_count"] or 0
        row = {
            "column": p["column_name"], "type": p["data_type"],
            "nulls": f"{p['null_count']:,} ({p['null_count'] / n:.0%})" if n and p["null_count"] is not None else "",
            "distinct": p["distinct_count"], "min": p["min_value"] or "", "max": p["max_value"] or "",
            "mean": f"{p['mean_value']:,.3f}".rstrip("0").rstrip(".") if p["mean_value"] is not None else "",
            "most common": ", ".join(f"{v} ({c:,})" for v, c in json.loads(p["top_values"])),
        }
        if descriptions is not None:
            row["description"] = descriptions.get(p["column_name"]) or ""
        rows.append(row)
    return pd.DataFrame(rows)


def compare_section(cat: Catalogue, key: str) -> None:
    """Side by side with another table or file, e.g. the SQL table a CSV extract was made from."""
    if not cat.profile(key):
        return
    profiled = sorted(cat.profiled_keys() - {key})
    if not profiled:
        return
    upstream = [k for k in cat.upstream(key) if k in profiled]
    names = {k: (cat.get_asset(k) or {}).get("name", k) for k in profiled}
    options = upstream + [k for k in profiled if k not in upstream]
    with st.expander("Compare with another table or file" + (" (suggested: where this data comes from)" if upstream else "")):
        other = st.selectbox("Compare with", options, format_func=lambda k: ("⬆️ " if k in upstream else "") + names[k],
                             key=f"compare-{key}")
        if other:
            left, right = cat.profile(key), cat.profile(other)
            st.caption(f"Rows: **{left[0]['row_count'] or 0:,}** here vs **{right[0]['row_count'] or 0:,}** in "
                       f"{names[other]}")
            st.dataframe(compare_frame(left, right), hide_index=True, width="stretch")


def compare_frame(left, right) -> pd.DataFrame:
    by_name = {p["column_name"].lower(): p for p in right}
    rows, seen = [], set()

    def rng(p):
        return f"{p['min_value']} → {p['max_value']}" if p and p["min_value"] is not None else ""

    def nulls(p):
        n = p["row_count"] or 0 if p else 0
        return f"{p['null_count'] / n:.0%}" if p and n and p["null_count"] is not None else ""

    for p in left:
        q = by_name.get(p["column_name"].lower())
        seen.add(p["column_name"].lower())
        rows.append({"column": p["column_name"], "in": "both" if q else "here only",
                     "type here": p["data_type"], "type there": q["data_type"] if q else "",
                     "range here": rng(p), "range there": rng(q),
                     "nulls here": nulls(p), "nulls there": nulls(q),
                     "distinct here": p["distinct_count"], "distinct there": q["distinct_count"] if q else None})
    for q in right:
        if q["column_name"].lower() not in seen:
            rows.append({"column": q["column_name"], "in": "there only", "type here": "", "type there": q["data_type"],
                         "range here": "", "range there": rng(q), "nulls here": "", "nulls there": nulls(q),
                         "distinct here": None, "distinct there": q["distinct_count"]})
    return pd.DataFrame(rows)


def lineage_section(cat: Catalogue, key: str, asset: dict) -> None:
    from pdcat.links import links_for
    uses, made_by, read_by = links_for(cat, key, asset)
    if not uses and not made_by and not read_by:
        if asset["kind"] in TABLE_LIKE:
            from .extras import manual_link_form
            with st.expander("Link the script that creates or loads this"):
                manual_link_form(cat, key)
        return
    if uses:
        st.subheader("Uses")
        seen = set()
        for i, l in enumerate(uses):
            ident = (l["relation"], l["dst_key"] or l["dst_ref"])
            if ident in seen:
                continue
            seen.add(ident)
            rel = RELATION_LABELS.get(l["relation"], l["relation"])
            c1, c2, c3, c4 = st.columns([1, 4, 2, 0.4])
            c1.caption(rel)
            with c2:
                if l["dst_key"]:
                    link_button(l["dst_key"], l["dst_name"] or l["dst_key"], l["dst_kind"] or "table", f"uses-{i}",
                                l["unimportant"])
                    if l["relation"] == "imports" and l["evidence"]:
                        st.caption(f"uses {l['evidence']}")
                else:
                    kind, _, name = l["dst_ref"].partition(":")
                    what = "file" if kind == "file" else "unmatched table"
                    st.button(f"{'📄' if kind == 'file' else '❔'} {name}", key=f"uses-{i}", type="tertiary",
                              on_click=go, args=(l["dst_ref"],), help=f"{what} · {l['evidence'] or ''}")
            c3.caption(_how(l))
            with c4:
                link_menu(cat, l, l["src_key"], l["relation"], l["dst_key"] or l["dst_ref"], f"uses-m-{i}")
    left, right = st.columns(2)
    with left:
        if made_by or asset["kind"] in TABLE_LIKE:
            st.subheader("Created or loaded by")
            if not made_by:
                st.caption("Nothing found.")
            for i, l in enumerate(made_by):
                a, b = st.columns([6, 1])
                with a:
                    link_button(l["src_key"], l["src_name"] or l["src_key"], l["src_kind"] or "script", f"madeby-{i}",
                                l["unimportant"])
                    st.caption(_how(l))
                with b:
                    link_menu(cat, l, l["src_key"], l["relation"], key, f"madeby-m-{i}")
            if asset["kind"] in TABLE_LIKE:
                from .extras import manual_link_form
                with st.popover("➕ Link a script"):
                    manual_link_form(cat, key)
    with right:
        if read_by:
            st.subheader("Read by" if asset["kind"] in TABLE_LIKE else "Used by")
            for i, l in enumerate(read_by):
                rel = RELATION_LABELS.get(l["relation"], l["relation"]).lower()
                a, b = st.columns([6, 1])
                with a:
                    link_button(l["src_key"], l["src_name"] or l["src_key"], l["src_kind"] or "script", f"readby-{i}",
                                l["unimportant"])
                    st.caption(f"{rel} {l['evidence']}" if l["relation"] == "imports" and l["evidence"] else _how(l))
                with b:
                    link_menu(cat, l, l["src_key"], l["relation"], key, f"readby-m-{i}")


def link_button(key: str, label: str, kind: str, widget_key: str, unimportant: bool) -> None:
    if unimportant:
        st.button(f":gray[{icon(kind)} {label} · not important]", key=widget_key, on_click=go, args=(key,),
                  type="tertiary", help="Marked not important: left out of diagrams, grouping and counts")
    else:
        asset_button(key, label, kind, widget_key)


def link_menu(cat: Catalogue, l: dict, src: str, relation: str, dst: str, widget_key: str) -> None:
    """Remove a link the scanner got wrong, or mark one that's right but noise as not important."""
    from pdcat.links import hide_link, set_link_mark
    if relation == "imports":
        return
    with st.popover("⋯", help="Remove this link, or mark it not important"):
        if l.get("unimportant"):
            if st.button("Important again", key=f"{widget_key}-imp"):
                set_link_mark(cat, src, relation, dst, None)
                st.rerun()
        elif st.button("Not important", key=f"{widget_key}-unimp",
                       help="Right but noise: stays listed, greyed out, and is left out of diagrams and counts"):
            set_link_mark(cat, src, relation, dst, "unimportant")
            st.rerun()
        if st.button("Remove: this link is wrong", key=f"{widget_key}-rm",
                     help="Kept out through rescans. Undo from the edit history or Settings → Removed links"):
            hide_link(cat, src, relation, dst)
            st.rerun()


def _how(l) -> str:
    keys = l.keys()
    how = l["how"] if "how" in keys else None
    ev = l["evidence"] or ""
    note = l["match_note"] if "match_note" in keys and l["match_note"] else None
    return " · ".join(b for b in (how, ev, f"⚠️ {note}" if note else None) if b)


def notes_section(cat: Catalogue, key: str, column_names: list[str]) -> None:
    from pdcat.staleness import stale_reasons
    st.subheader("Notes")
    notes = [n for n in cat.notes(key) if not n["archived"]]
    archived = [n for n in cat.notes(key) if n["archived"]]
    reasons = stale_reasons(cat, key)
    for n in notes:
        _, _, col = n["asset_key"].partition("#")
        resolved = n["note_type"] == "issue" and n["status"] == "resolved"
        with st.container(border=True):
            label = NOTE_TYPES[n["note_type"]] + (" (resolved)" if resolved else "")
            st.caption(f"{NOTE_ICONS[n['note_type']]} **{label}**" + (f" · column **{col}**" if col else ""))
            if n["id"] in reasons:
                st.warning("May be out of date: " + "; ".join(reasons[n["id"]]), icon="🕰️")
            if n["note_type"] == "example":
                st.code(n["body"], language=None)
            else:
                st.markdown(f"~~{n['body']}~~" if resolved and "\n" not in n["body"] else n["body"])
            meta = note_meta(n)
            if n["confirmed_at"]:
                meta += f" · confirmed {n['confirmed_at'][:10]}"
            if n["import_batch"]:
                meta += " · imported"
            st.caption(meta)
            b1, b2, b3, b4, _ = st.columns([1, 1.2, 1, 1, 2])
            if n["note_type"] == "issue":
                if b2.button("Reopen" if resolved else "Mark resolved", key=f"status-{n['id']}"):
                    cat.set_issue_status(n["id"], "open" if resolved else "resolved")
                    st.rerun()
            if b3.button("✓ Still true", key=f"confirm-{n['id']}", help="Mark this note as checked today"):
                cat.confirm_note(n["id"])
                st.rerun()
            if b4.button("Archive", key=f"archive-{n['id']}", help="Keep it, but out of the way"):
                cat.archive_note(n["id"], True)
                st.rerun()
            with b1.popover("Edit"):
                with st.form(f"edit-{n['id']}"):
                    kinds = list(NOTE_TYPES)
                    note_type = st.radio("Type", kinds, index=kinds.index(n["note_type"]),
                                         format_func=NOTE_TYPES.get, horizontal=True)
                    body = st.text_area("Note", value=n["body"], height=150)
                    tags = st.text_input("Tags", value=n["tags"])
                    save, delete = st.columns(2)
                    if save.form_submit_button("Save", type="primary") and body.strip():
                        cat.update_note(n["id"], body, tags, note_type)
                        st.rerun()
                    if delete.form_submit_button("Delete"):
                        cat.delete_note(n["id"])
                        st.rerun()
    if archived:
        with st.expander(f"Archived notes ({len(archived)})"):
            for n in archived:
                st.markdown(n["body"])
                st.caption(note_meta(n))
                if st.button("Restore", key=f"unarchive-{n['id']}"):
                    cat.archive_note(n["id"], False)
                    st.rerun()

    with st.form(f"add-note-{key}", clear_on_submit=True):
        c1, c2 = st.columns([2, 3])
        note_type = c1.radio("Type", list(NOTE_TYPES), format_func=NOTE_TYPES.get, horizontal=True,
                             help="Data issue: something wrong or odd in the data, tracked until resolved. "
                                  "Example values: paste sample rows, value ranges or codes; layout is kept.")
        about = "This item"
        if column_names:
            about = c2.selectbox("About", ["This item", *column_names])
        body = st.text_area("Add a note", placeholder="What is this for? Gotchas, refresh timing, a data problem, "
                            "pasted example values… (Markdown works)", height=120)
        tags = st.text_input("Tags", placeholder="comma separated, e.g. forecasting, slow, needs-check")
        if st.form_submit_button("Add note", type="primary"):
            if body.strip():
                target = key if about == "This item" else f"{key}#{about}"
                cat.add_note(target, body, tags, note_type)
                st.toast("Note saved")
                st.rerun()
            else:
                st.warning("Write something first.")
