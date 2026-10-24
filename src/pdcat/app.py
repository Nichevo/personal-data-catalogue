"""Streamlit front end: search the catalogue, open a table or script, and add notes.

Started by `pdcat ui`, which passes the catalogue path after "--".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from pdcat.export import rows_to_csv, to_csv_zip, to_json
from pdcat.store import MANUAL_SOURCE, NOTE_TYPES, Catalogue

KIND_ICONS = {"table": "🗃️", "view": "👁️", "procedure": "⚙️", "database": "🛢️", "project": "📁",
              "script": "🐍", "notebook": "📓", "sql_script": "📜", "sql_snippet": "✍️", "note": "📝",
              "data_file": "📄", "folder": "🗂️"}
NOTE_ICONS = {"note": "📝", "issue": "⚠️", "example": "🔢"}
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
        view = st.radio("View", ["Search", "Add SQL", "Data folders", "Data issues", "Needs notes",
                                 "Recent notes", "Unmatched tables"],
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

    if view == "Add SQL":
        st.title("Add SQL")
        st.caption("Paste a query or snippet you want to remember. It becomes searchable, and the tables it "
                   "reads and writes are linked, so it shows up under \"Used by\" on those tables.")
        snippet_form(cat)
        return
    if view == "Data folders":
        folders_page(cat)
        return

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
    elif view == "Data issues":
        st.title("Data issues")
        st.caption("Open data issues logged on tables, columns and files. Resolve them from the item's page.")
        issues = cat.open_issues()
        if not issues:
            st.success("No open data issues.")
        for i, n in enumerate(issues):
            base, _, column = n["asset_key"].partition("#")
            with st.container(border=True):
                label = (n["asset_name"] or base) + (f" › {column}" if column else "")
                asset_button(base, label, n["asset_kind"] or "note", f"issue-{i}")
                st.markdown(n["body"])
                st.caption(_note_meta(n))
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
        approx = "" if attrs.get("row_count_exact", True) else "≈ "
        meta.append(f"{approx}{attrs['row_count']:,} rows")
    if attrs.get("size_bytes") is not None:
        meta.append(_size(attrs["size_bytes"]))
    if attrs.get("modified"):
        meta.append(f"modified {attrs['modified'][:16].replace('T', ' ')} UTC")
    if attrs.get("lines"):
        meta.append(f"{attrs['lines']} lines")
    st.caption(" · ".join(str(m) for m in meta) + f"  \n`{key}`")
    if asset["status"] == "missing":
        st.warning("Not found in the latest scan. It may have been dropped, renamed or deleted.")
    if attrs.get("parse_error"):
        st.warning(f"Couldn't fully read this file: {attrs['parse_error']}")
    if attrs.get("read_error"):
        st.warning(f"Couldn't read this data file: {attrs['read_error']}")
    if attrs.get("path") and asset["kind"] in ("data_file", "folder", "project"):
        st.caption(f"📍 `{attrs['path']}`")
    if asset["description"]:
        st.markdown(asset["description"])
    if asset["kind"] == "sql_snippet":
        st.code(attrs.get("sql", ""), language="sql")
        if attrs.get("tags"):
            st.caption(" ".join(f"#{t.strip()}" for t in attrs["tags"].split(",") if t.strip()))
        with st.expander("Edit or delete this SQL"):
            snippet_form(cat, asset)

    columns = cat.columns(key)
    notes_section(cat, key, [c["name"] for c in columns])

    if columns and not cat.profile(key):
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

    if asset["kind"] in ("table", "view", "data_file"):
        profile_section(cat, key, asset)
        compare_section(cat, key)

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


def profile_section(cat: Catalogue, key: str, asset: dict) -> None:
    profile = cat.profile(key)
    st.subheader("Columns and values")
    if not profile:
        if asset["kind"] in ("table", "view"):
            st.caption(f"No value profile yet. Run `pdcat profile {asset['name']}` (or `pdcat profile --used`) "
                       "to measure ranges, nulls and common values. It only reads.")
        return
    st.caption(f"Measured {profile[0]['profiled_at'][:16].replace('T', ' ')} UTC · {profile[0]['method']}")
    descriptions = {c["name"]: c["description"] for c in cat.columns(key)}
    st.dataframe(profile_frame(profile, descriptions), hide_index=True, width="stretch")


def profile_frame(profile, descriptions: dict | None = None) -> pd.DataFrame:
    rows = []
    for p in profile:
        n = p["row_count"] or 0
        row = {
            "column": p["column_name"], "type": p["data_type"],
            "nulls": f"{p['null_count']:,} ({p['null_count'] / n:.0%})" if n and p["null_count"] is not None else "",
            "distinct": p["distinct_count"], "min": p["min_value"], "max": p["max_value"],
            "mean": round(p["mean_value"], 3) if p["mean_value"] is not None else None,
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


def folders_page(cat: Catalogue) -> None:
    from pdcat.scan_files import scan_folder

    st.title("Data folders")
    st.caption("Point the catalogue at any folder and it records every CSV and Parquet file in it: size, "
               "modified date, row count, columns and value ranges. Re-scan whenever the files change.")
    for i, f in enumerate(cat.folders()):
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 1, 1])
            with c1:
                asset_button(f"files://{f['name']}", f["name"], "folder", f"folder-{i}")
                st.caption(f"`{f['path']}`" + (" · includes subfolders" if f["recursive"] else ""))
            if c2.button("Re-scan", key=f"rescan-{i}"):
                _run_folder_scan(cat, scan_folder, f["name"], f["path"], bool(f["recursive"]))
            if c3.button("Remove", key=f"remove-{i}", help="Forget this folder and its files (notes are kept)"):
                cat.remove_folder(f["name"])
                st.rerun()

    st.subheader("Add a folder")
    with st.form("add-folder"):
        path = st.text_input("Folder path", placeholder=r"C:\Users\you\data\extracts",
                             help="Tip: in File Explorer, Shift + right-click the folder → Copy as path.")
        c1, c2 = st.columns([2, 1])
        name = c1.text_input("Short name", placeholder="e.g. forecast-extracts")
        recursive = c2.checkbox("Include subfolders", value=True)
        submitted = st.form_submit_button("Add and scan", type="primary")
    if submitted:
        path = path.strip().strip('"')
        name = (name.strip() or Path(path).name or "folder").replace("/", "-")
        taken = set(cat.sources()) - {f["name"] for f in cat.folders()}
        if not path:
            st.warning("Paste a folder path first.")
        elif name in taken or name == MANUAL_SOURCE:
            st.warning(f"The name '{name}' is already used by another source. Pick another.")
        elif not Path(path).is_dir():
            st.error(f"Can't find a folder at {path}")
        else:
            cat.add_folder(name, path, recursive)
            _run_folder_scan(cat, scan_folder, name, path, recursive)


def _run_folder_scan(cat: Catalogue, scan_folder, name: str, path: str, recursive: bool) -> None:
    with st.spinner(f"Scanning {path} …"):
        try:
            counts = scan_folder(cat, name, path, recursive)
        except Exception as e:
            st.error(f"Scan failed: {type(e).__name__}: {e}")
            return
    msg = f"Scanned {counts['files']} file(s)" + (f", {counts['errors']} couldn't be read" if counts["errors"] else "")
    st.toast(msg)
    st.rerun()


def snippet_form(cat: Catalogue, existing: dict | None = None) -> None:
    attrs = existing["attrs"] if existing else {}
    databases = cat.databases()
    options = ["Not sure / any", *databases]
    current = attrs.get("database") or ""
    with st.form(f"snippet-{existing['key'] if existing else 'new'}", clear_on_submit=existing is None):
        title = st.text_input("Title", value=existing["name"] if existing else "",
                              placeholder="e.g. Weekly sales by store for the forecast")
        sql = st.text_area("SQL", value=attrs.get("sql", ""), height=220,
                           placeholder="SELECT ... FROM dbo.SalesDaily ...")
        description = st.text_area("What it's for (optional)", value=(existing or {}).get("description") or "",
                                   height=80, placeholder="Why it matters, when to use it, gotchas… (Markdown works)")
        c1, c2 = st.columns(2)
        database = c1.selectbox("Runs against", options,
                                index=options.index(current) if current in options else 0,
                                help="Where bare table names like \"Sales\" live, so they link to the right table.")
        tags = c2.text_input("Tags", value=attrs.get("tags", ""), placeholder="comma separated")
        save_col, delete_col = st.columns([1, 1])
        saved = save_col.form_submit_button("Save SQL", type="primary")
        deleted = delete_col.form_submit_button("Delete") if existing else False
    if saved:
        if not sql.strip():
            st.warning("Paste some SQL first.")
            return
        key = cat.save_snippet(title, sql, description=description, tags=tags,
                               database=None if database == options[0] else database,
                               key=existing["key"] if existing else None)
        go(key)
        st.rerun()
    if deleted:
        cat.delete_snippet(existing["key"])
        go(None)
        st.rerun()


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
        resolved = n["note_type"] == "issue" and n["status"] == "resolved"
        with st.container(border=True):
            label = NOTE_TYPES[n["note_type"]] + (" (resolved)" if resolved else "")
            st.caption(f"{NOTE_ICONS[n['note_type']]} **{label}**" + (f" · column **{col}**" if col else ""))
            if n["note_type"] == "example":
                st.code(n["body"], language=None)
            else:
                st.markdown(f"~~{n['body']}~~" if resolved and "\n" not in n["body"] else n["body"])
            st.caption(_note_meta(n))
            b1, b2, _ = st.columns([1, 1, 4])
            if n["note_type"] == "issue":
                if b2.button("Reopen" if resolved else "Mark resolved", key=f"status-{n['id']}"):
                    cat.set_issue_status(n["id"], "open" if resolved else "resolved")
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


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


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
