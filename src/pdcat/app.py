"""Streamlit front end: search the catalogue, open a table or script, and add notes.

Started by `pdcat ui`, which passes the catalogue path after "--".
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
import streamlit as st

from pdcat.export import rows_to_csv, to_csv_zip, to_json
from pdcat.store import Catalogue

KIND_ICONS = {"table": "🗃️", "view": "👁️", "procedure": "⚙️", "database": "🛢️", "project": "📁",
              "script": "🐍", "notebook": "📓", "sql_script": "📜", "note": "📝"}
RELATION_LABELS = {"reads": "Reads", "writes": "Writes", "calls": "Calls"}


def db_path() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("PDCAT_DB"))
    args, _ = parser.parse_known_args(sys.argv[1:])
    if not args.db:
        st.error("No catalogue given. Start the app with `pdcat ui`.")
        st.stop()
    return args.db


def open_catalogue() -> Catalogue:
    # A fresh connection per run keeps SQLite happy with Streamlit's threads; it is cheap.
    return Catalogue(db_path())


def go(key: str | None) -> None:
    if key:
        st.query_params["asset"] = key
    else:
        st.query_params.pop("asset", None)


def icon(kind: str) -> str:
    return KIND_ICONS.get(kind, "•")


def asset_button(key: str, label: str, kind: str, widget_key: str) -> None:
    st.button(f"{icon(kind)} {label}", key=widget_key, on_click=go, args=(key,), type="tertiary")


def result_list(rows, prefix: str, extra=None) -> None:
    if not rows:
        st.info("Nothing here yet.")
        return
    for i, r in enumerate(rows):
        left, right = st.columns([4, 2])
        with left:
            asset_button(r["key"], r["name"], r["kind"], f"{prefix}-{i}")
            if r["description"]:
                st.caption(r["description"].splitlines()[0][:200])
        with right:
            bits = [r["kind"], r["source"]] if r["source"] else [r["kind"]]
            if r["note_count"]:
                bits.append(f"📝 {r['note_count']}")
            if r["status"] == "missing":
                bits.append("⚠️ missing")
            if extra:
                bits.append(extra(r))
            st.caption(" · ".join(b for b in bits if b))


# ---- home -----------------------------------------------------------------

def home(cat: Catalogue) -> None:
    stats = cat.stats()
    with st.sidebar:
        st.header("Data catalogue")
        view = st.radio("View", ["Search", "Needs notes", "Recent notes", "Unmatched tables"],
                        label_visibility="collapsed")
        st.divider()
        kinds = stats["kinds"]
        if kinds:
            st.caption(" · ".join(f"{icon(k)} {v} {k}" for k, v in sorted(kinds.items())))
        st.caption(f"📝 {stats['notes']} notes")
        for source, at in stats["last_scans"].items():
            st.caption(f"{source}: scanned {at[:16].replace('T', ' ')} UTC")
        if stats["kinds"] or stats["notes"]:
            st.divider()
            st.caption("Export everything: tables, columns, links and your notes")
            st.download_button("⬇️ CSV files (zip)", to_csv_zip(cat), "catalogue-export.zip",
                               "application/zip", width="stretch")
            st.download_button("⬇️ JSON", to_json(cat), "catalogue-export.json",
                               "application/json", width="stretch")

    if not stats["kinds"]:
        st.title("Your catalogue is empty")
        st.write("Run `pdcat scan` in a terminal to scan the databases and projects in your config, then refresh.")
        return

    if view == "Search":
        st.title("Search")
        c1, c2, c3 = st.columns([4, 1, 1])
        text = c1.text_input("Search", placeholder="table, column, script, note or tag…", label_visibility="collapsed")
        kind = c2.selectbox("Kind", ["Any kind", *cat.kinds()], label_visibility="collapsed")
        source = c3.selectbox("Source", ["Any source", *cat.sources()], label_visibility="collapsed")
        rows = cat.search(text, kind=None if kind == "Any kind" else kind,
                          source=None if source == "Any source" else source)
        if rows:
            st.download_button(f"⬇️ Download these {len(rows)} results (CSV)",
                               rows_to_csv([dict(r) for r in rows]), "search-results.csv", "text/csv")
        result_list(rows, "search")
    elif view == "Needs notes":
        st.title("Needs notes")
        st.caption("Tables and views with no description and no notes, the ones your code uses most first.")
        result_list(cat.needs_documentation(), "todo",
                    extra=lambda r: f"used by {r['used_by']} file(s)" if r["used_by"] else "")
    elif view == "Recent notes":
        st.title("Recent notes")
        notes = cat.recent_notes()
        if not notes:
            st.info("No notes yet. Open any table or script to add one.")
        for i, n in enumerate(notes):
            base, _, column = n["asset_key"].partition("#")
            with st.container(border=True):
                label = (n["asset_name"] or base) + (f" › {column}" if column else "")
                asset_button(base, label, n["asset_kind"] or "note", f"recent-{i}")
                st.markdown(n["body"])
                st.caption(_note_meta(n))
    else:
        st.title("Unmatched tables")
        st.caption("Table names found in your code that don't match any scanned table. They may live on a server "
                   "you haven't added, be built dynamically, or be a false positive from the SQL detector.")
        rows = cat.unresolved_refs()
        if rows:
            st.dataframe(pd.DataFrame([{"table": r["dst_ref"][6:], "used by files": r["used_by"],
                                        "example": r["example"]} for r in rows]),
                         hide_index=True, width="stretch")
        else:
            st.success("Every table name in your code matched a scanned table.")


# ---- detail ---------------------------------------------------------------

def detail(cat: Catalogue, key: str) -> None:
    st.button("← Back to search", on_click=go, args=(None,))
    asset = cat.get_asset(key)
    if asset is None:
        # Notes can be attached to files and unmatched tables that aren't scanned assets.
        st.title(f"{icon('note')} {key}")
        st.caption("Not a scanned item. You can still keep notes on it.")
        notes_section(cat, key, [])
        return

    attrs = asset["attrs"]
    st.title(f"{icon(asset['kind'])} {asset['name']}")
    meta = [asset["kind"], asset["source"]]
    if attrs.get("database"):
        meta.append(attrs["database"])
    if attrs.get("row_count") is not None:
        meta.append(f"{attrs['row_count']:,} rows")
    if attrs.get("lines"):
        meta.append(f"{attrs['lines']} lines")
    st.caption(" · ".join(str(m) for m in meta) + f"  \n`{key}`")
    if asset["status"] == "missing":
        st.warning("Not found in the latest scan. It may have been dropped, renamed or deleted.")
    if attrs.get("parse_error"):
        st.warning(f"Couldn't fully read this file: {attrs['parse_error']}")
    if asset["description"]:
        st.markdown(asset["description"])

    columns = cat.columns(key)
    notes_section(cat, key, [c["name"] for c in columns])

    if columns:
        st.subheader("Columns")
        col_notes: dict[str, list[str]] = {}
        for n in cat.notes(key):
            _, _, col = n["asset_key"].partition("#")
            if col:
                col_notes.setdefault(col, []).append(n["body"])
        st.dataframe(pd.DataFrame([{
            "column": c["name"], "type": c["data_type"], "nullable": bool(c["nullable"]),
            "description": c["description"] or "", "notes": " | ".join(col_notes.get(c["name"], [])),
        } for c in columns]), hide_index=True, width="stretch")

    lineage_section(cat, key, asset)

    children = cat.children(key)
    if children:
        st.subheader("Contains")
        result_list([{**dict(c), "source": "", "note_count": 0} for c in children], "child")

    with st.expander("Details"):
        if attrs.get("definition"):
            st.code(attrs["definition"], language="sql")
        for label, field in (("Imports", "imports"), ("Functions", "functions"), ("Classes", "classes")):
            if attrs.get(field):
                st.markdown(f"**{label}:** " + ", ".join(f"`{x}`" for x in attrs[field]))
        rest = {k: v for k, v in attrs.items()
                if k not in ("definition", "imports", "functions", "classes") and v not in (None, "", [])}
        st.json(rest, expanded=False)
        st.caption(f"First seen {asset['first_seen'][:10]} · last seen {asset['last_seen'][:10]}")


def lineage_section(cat: Catalogue, key: str, asset: dict) -> None:
    uses = cat.links_from_children(key) if asset["kind"] == "project" else cat.links_from(key)
    used_by = cat.links_to(key)
    if not uses and not used_by:
        return
    left, right = st.columns(2)
    with left:
        st.subheader("Uses")
        if not uses:
            st.caption("Nothing found.")
        seen = set()
        for i, l in enumerate(uses):
            ident = (l["relation"], l["dst_key"] or l["dst_ref"])
            if ident in seen:
                continue
            seen.add(ident)
            rel = RELATION_LABELS.get(l["relation"], l["relation"])
            if l["dst_key"]:
                c1, c2 = st.columns([1, 4])
                c1.caption(rel)
                with c2:
                    asset_button(l["dst_key"], l["dst_name"] or l["dst_key"], l["dst_kind"] or "table", f"uses-{i}")
            else:
                kind, _, name = l["dst_ref"].partition(":")
                what = "file" if kind == "file" else "unmatched table"
                c1, c2 = st.columns([1, 4])
                c1.caption(rel)
                with c2:
                    st.button(f"{'📄' if kind == 'file' else '❔'} {name}", key=f"uses-{i}", type="tertiary",
                              on_click=go, args=(l["dst_ref"],), help=f"{what} · {l['evidence'] or ''}")
    with right:
        st.subheader("Used by")
        if not used_by:
            st.caption("Nothing found.")
        for i, l in enumerate(used_by):
            rel = RELATION_LABELS.get(l["relation"], l["relation"]).lower()
            c1, c2 = st.columns([4, 2])
            with c1:
                asset_button(l["src_key"], l["src_name"] or l["src_key"], l["src_kind"] or "script", f"usedby-{i}")
            c2.caption(f"{rel} · {l['evidence'] or ''}")


def notes_section(cat: Catalogue, key: str, column_names: list[str]) -> None:
    st.subheader("Notes")
    notes = cat.notes(key)
    for n in notes:
        _, _, col = n["asset_key"].partition("#")
        with st.container(border=True):
            if col:
                st.caption(f"Column **{col}**")
            st.markdown(n["body"])
            st.caption(_note_meta(n))
            with st.popover("Edit"):
                with st.form(f"edit-{n['id']}"):
                    body = st.text_area("Note", value=n["body"], height=150)
                    tags = st.text_input("Tags", value=n["tags"])
                    save, delete = st.columns(2)
                    if save.form_submit_button("Save", type="primary") and body.strip():
                        cat.update_note(n["id"], body, tags)
                        st.rerun()
                    if delete.form_submit_button("Delete"):
                        cat.delete_note(n["id"])
                        st.rerun()

    with st.form(f"add-note-{key}", clear_on_submit=True):
        about = "This item"
        if column_names:
            about = st.selectbox("About", ["This item", *column_names])
        body = st.text_area("Add a note", placeholder="What is this for? Gotchas, refresh timing, owner, "
                            "how the forecast uses it… (Markdown works)", height=120)
        tags = st.text_input("Tags", placeholder="comma separated, e.g. forecasting, slow, needs-check")
        if st.form_submit_button("Add note", type="primary"):
            if body.strip():
                target = key if about == "This item" else f"{key}#{about}"
                cat.add_note(target, body, tags)
                st.toast("Note saved")
                st.rerun()
            else:
                st.warning("Write something first.")


def _note_meta(n) -> str:
    when = n["updated_at"][:16].replace("T", " ")
    tags = " ".join(f"#{t.strip()}" for t in n["tags"].split(",") if t.strip())
    return f"{when} UTC" + (f" · {tags}" if tags else "")


def main() -> None:
    st.set_page_config(page_title="Data catalogue", page_icon="🗃️", layout="wide")
    cat = open_catalogue()
    try:
        key = st.query_params.get("asset")
        if key:
            detail(cat, key)
        else:
            home(cat)
    finally:
        cat.close()


main()
