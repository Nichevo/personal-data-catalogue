"""A busy item's connections as one table with filters, instead of three long lists of links."""
from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import streamlit as st

from pdcat.connections import ORDER, connection_rows, counts, filter_rows
from pdcat.store import Catalogue

from .common import go, kind_label, selected_rows, selection_key

KIND_WORDS = {"unmatched": "unmatched table"}


def _kind(k: str) -> str:
    return KIND_WORDS.get(k, kind_label(k)) if k else "(unknown)"


def connections_table(cat: Catalogue, key: str, asset: dict, links: tuple[list, list, list]) -> None:
    """Every link of the item in one sortable table: filter by connection (read, loaded by...), type,
    folder or project and name; tick rows to open one, mark them not important or remove them; save as CSV."""
    rows = connection_rows(cat, key, asset, links)
    by_conn = counts(rows, "connection")
    summary = ", ".join(f"{n:,} {w.lower()}" for w, n in sorted(by_conn.items(),
                                                                 key=lambda x: ORDER.index(x[0]) if x[0] in ORDER else 99))
    st.subheader(f"Uses and used by ({len(rows):,})")
    st.caption(summary + ". Filter below, click a heading to sort, tick rows to open or mark them.")

    conns = sorted(by_conn, key=lambda w: ORDER.index(w) if w in ORDER else 99)
    picked_conns = st.pills("Connection", conns, selection_mode="multi", key=f"conn-dir-{key}",
                            format_func=lambda w: f"{w} ({by_conn[w]:,})",
                            help="Pick one or more; none picked shows all")
    c1, c2, c3 = st.columns([2, 2, 3])
    by_kind = counts(rows, "kind")
    kinds = c1.multiselect("Type", sorted(by_kind, key=lambda k: -by_kind[k]), key=f"conn-kind-{key}",
                           format_func=lambda k: f"{_kind(k)} ({by_kind[k]:,})", placeholder="All types")
    by_place = counts(rows, "place")
    places = c2.multiselect("Folder or project", sorted(by_place, key=lambda p: (-by_place[p], p)),
                            key=f"conn-place-{key}", format_func=lambda p: f"{p} ({by_place[p]:,})",
                            placeholder="Anywhere")
    words = c3.text_input("Name", key=f"conn-find-{key}", placeholder="Filter by name, path or how it's used")
    n_unimp = sum(r["not_important"] for r in rows)
    important_only = False
    if n_unimp:
        important_only = st.toggle(f"Hide the {n_unimp:,} marked not important", key=f"conn-imp-{key}")
    shown = filter_rows(rows, connections=picked_conns, kinds=kinds, places=places, words=words,
                        important_only=important_only)
    report_keys = {r["key"] for r in rows if r["kind"] == "report"}
    reg: dict = {}
    if report_keys:
        from pdcat.register import rows_by_report

        from .register_pages import register_filters
        reg = {k: e for k, e in rows_by_report(cat).items() if k in report_keys}
        if reg:
            only = register_filters(cat, f"conn-{key}", report_keys)
            if only is not None:
                shown = [r for r in shown if r["key"] in only]  # a register filter keeps matching reports only
    if len(shown) != len(rows):
        st.caption(f"Showing {len(shown):,} of {len(rows):,}.")
    if not shown:
        st.info("No connections match these filters.")
        return

    is_project = any(r["via"] for r in shown)
    df = pd.DataFrame([{
        "connection": r["connection"],
        "name": r["name"],
        "type": _kind(r["kind"]),
        "in": r["in"],
        "source": r["source"],
        **({"via": r["via"]} if is_project else {}),
        **({"owner": (reg.get(r["key"]) or {}).get("owner", ""),
            "status": (reg.get(r["key"]) or {}).get("status", "")} if reg else {}),
        "how": r["how"],
        "not important": "✓" if r["not_important"] else "",
    } for r in shown])
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
                         key=selection_key(f"conn-table-{key}", (f"{r['src']}|{r['relation']}|{r['dst']}" for r in shown)),
                         column_config={"how": st.column_config.TextColumn(width="large"),
                                        "not important": st.column_config.TextColumn(width="small")})
    sel = [shown[i] for i in selected_rows(event, len(shown))]
    a, b = st.columns([3, 2])
    with b:
        csv = pd.DataFrame([{k: r[k] for k in ("connection", "name", "kind", "in", "source", "path", "via", "how",
                                                "not_important", "key")}
                            | ({f"register {f}": (reg.get(r["key"]) or {}).get(f, "")
                                for f in ("owner", "audience", "app", "status", "workspace")} if reg else {})
                            for r in shown]).to_csv(index=False)
        st.download_button(f"⬇️ Save these {len(shown):,} as CSV", csv, file_name="connections.csv",
                           mime="text/csv", key=f"conn-csv-{key}")
    with a:
        if sel:
            _actions(cat, key, sel)
        else:
            st.caption("Tick a row to open it, or tick several to mark them not important at once.")


def _actions(cat: Catalogue, key: str, sel: list[dict]) -> None:
    from pdcat.links import hide_link, set_link_mark
    markable = [r for r in sel if r["relation"] != "imports"]  # import links come from the code, not marked
    cols = st.columns(3)
    if len(sel) == 1 and sel[0]["key"]:
        r = sel[0]
        cols[0].button(f"Open {r['name']}", key=f"conn-open-{key}", type="primary", on_click=go, args=(r["key"],))
    if markable:
        if all(r["not_important"] for r in markable):
            if cols[1].button("Important again", key=f"conn-imp-again-{key}"):
                for r in markable:
                    set_link_mark(cat, r["src"], r["relation"], r["dst"], None)
                st.rerun()
        elif cols[1].button(f"Not important ({len(markable)})" if len(markable) > 1 else "Not important",
                            key=f"conn-unimp-{key}",
                            help="Right but noise: stays listed and is left out of diagrams and counts"):
            for r in markable:
                if not r["not_important"]:
                    set_link_mark(cat, r["src"], r["relation"], r["dst"], "unimportant")
            st.rerun()
        with cols[2].popover("Remove…", help="Remove links the scanner got wrong"):
            st.caption(f"Remove {len(markable)} link(s). Kept out through rescans. Undo from the edit history or "
                       "Settings → Removed links.")
            if st.button("Remove: these links are wrong", key=f"conn-rm-{key}", type="primary"):
                for r in markable:
                    hide_link(cat, r["src"], r["relation"], r["dst"])
                st.rerun()


def wants_table(links: tuple[list, list, list]) -> bool:
    """Short lists stay as clickable lists; past this many links the table with filters is easier."""
    from .item_tabs import LIMIT
    uses, made_by, read_by = links
    return len({(l["relation"], l.get("dst_key") or l["dst_ref"], l["src_key"]) for l in uses}) + len(made_by) + \
        len(read_by) > LIMIT



def _words_match(text: str, words: str) -> bool:
    low = text.lower()
    return all(w in low for w in (words or "").lower().split())


def renames_table(cat: Catalogue, key: str, r: dict) -> Callable[[str], str]:
    """Renamed columns as one table: which way, the column here and there, the other item, how and where.
    Returns a function that gives an item's name, for the rename path below the table."""
    keys = {x["src_item"] for x in r["from"]} | {x["dst_item"] for x in r["to"]}
    names = {row["key"]: row["name"] for row in _names(cat, keys)}

    def name(k: str) -> str:
        if k not in names:
            names[k] = (cat.get_asset(k) or {}).get("name", k)
        return names[k]

    rows = [{"way": "Comes in", "column here": x["dst_column"], "column there": x["src_column"],
             "other item": name(x["src_item"]), "how": x["how"] or "", "found in": x["evidence"] or "",
             "key": x["src_item"]} for x in r["from"]]
    rows += [{"way": "Goes out", "column here": x["src_column"], "column there": x["dst_column"],
              "other item": name(x["dst_item"]), "how": x["how"] or "", "found in": x["evidence"] or "",
              "key": x["dst_item"]} for x in r["to"]]
    rows.sort(key=lambda x: (x["way"], x["column here"].lower(), x["other item"].lower()))
    st.caption("A column here that has another name in the item it comes from or goes to. Comes in: it had the "
               "other name before; goes out: it gets the other name there.")
    c1, c2, c3 = st.columns([1.4, 2, 3])
    ways = c1.pills("Way", ["Comes in", "Goes out"], selection_mode="multi", key=f"ren-way-{key}")
    by_item: dict[str, int] = {}
    for x in rows:
        by_item[x["other item"]] = by_item.get(x["other item"], 0) + 1
    items = c2.multiselect("Other item", sorted(by_item, key=lambda n: (-by_item[n], n.lower())),
                           key=f"ren-item-{key}", format_func=lambda n: f"{n} ({by_item[n]})", placeholder="Any")
    words = c3.text_input("Column", key=f"ren-find-{key}", placeholder="Filter by column name, how or where")
    shown = [x for x in rows if (not ways or x["way"] in ways) and (not items or x["other item"] in items)
             and _words_match(f"{x['column here']} {x['column there']} {x['how']} {x['found in']}", words)]
    if len(shown) != len(rows):
        st.caption(f"Showing {len(shown):,} of {len(rows):,}.")
    if not shown:
        st.info("No renamed columns match these filters.")
        return name
    df = pd.DataFrame([{k: v for k, v in x.items() if k != "key"} for x in shown])
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=selection_key(f"ren-table-{key}", (f"{x['key']}|{x['column here']}|{x['way']}"
                                                                for x in shown)))
    a, b = st.columns([3, 2])
    b.download_button(f"⬇️ Save these {len(shown):,} as CSV", df.to_csv(index=False), file_name="renamed-columns.csv",
                      mime="text/csv", key=f"ren-csv-{key}")
    sel = selected_rows(event, len(shown))
    if sel:
        x = shown[sel[0]]
        a.button(f"Open {x['other item']}", key=f"ren-open-{key}", type="primary", on_click=go, args=(x["key"],))
    else:
        a.caption("Tick a row to open the other item.")
    return name


def _names(cat: Catalogue, keys: set[str]) -> list:
    keys_l = [k for k in keys if k]
    out = []
    for i in range(0, len(keys_l), 500):
        chunk = keys_l[i:i + 500]
        out += cat.conn.execute(f"SELECT key, name FROM assets WHERE key IN ({','.join('?' * len(chunk))})",
                                chunk).fetchall()
    return out


def sql_using_table(cat: Catalogue, key: str, rows: list[dict]) -> None:
    """The SQL that uses a table, as one table: reads or writes, columns, joins, where it's found, checks.
    Filters by how it's used, a column it uses, where it is, and words; old SQL can be hidden."""
    def where(r: dict) -> list[str]:
        return sorted({p["item"].rsplit("/", 1)[-1] for p in r["places"]})

    rels: dict[str, int] = {}
    cols: dict[str, int] = {}
    for r in rows:
        for rel in (r["relation"] or "").split(", "):
            if rel:
                rels[rel] = rels.get(rel, 0) + 1
        for c in r["columns"]:
            cols[c] = cols.get(c, 0) + 1
    c1, c2, c3 = st.columns([1.6, 2, 3])
    picked = c1.pills("How", sorted(rels), selection_mode="multi", key=f"ut-rel-{key}",
                      format_func=lambda x: f"{x} ({rels[x]})")
    picked_cols = c2.multiselect("Uses column", sorted(cols, key=lambda c: (-cols[c], c.lower())),
                                 key=f"ut-col-{key}", format_func=lambda c: f"{c} ({cols[c]})", placeholder="Any")
    words = c3.text_input("Name", key=f"ut-find-{key}", placeholder="Filter by name, file, join or check")
    n_old = sum(r["old"] for r in rows)
    hide_old = st.toggle(f"Hide the {n_old} that look old", key=f"ut-old-{key}") if n_old else False
    shown = []
    for r in rows:
        if hide_old and r["old"]:
            continue
        if picked and not set((r["relation"] or "").split(", ")) & set(picked):
            continue
        if picked_cols and not set(r["columns"]) & set(picked_cols):
            continue
        joins = "; ".join(f"{j['other']} on {j['column']} = {j['other_column']}" for j in r["joins"])
        if not _words_match(f"{r['name']} {' '.join(where(r))} {joins} {r.get('checks') or ''}", words):
            continue
        shown.append((r, joins))
    if len(shown) != len(rows):
        st.caption(f"Showing {len(shown):,} of {len(rows):,}.")
    if not shown:
        st.info("No SQL matches these filters.")
        return
    df = pd.DataFrame([{
        "name": ("🗄️ " if r["old"] else "") + r["name"],
        "how": r["relation"],
        "columns": ", ".join(r["columns"]),
        "joins": joins,
        "found in": ", ".join(where(r)),
        "active": ", ".join(r["active"]),
        "checks": r.get("checks") or "",
        "looks old": "✓" if r["old"] else "",
    } for r, joins in shown])
    event = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=selection_key(f"ut-table-{key}", (r["uid"] for r, _ in shown)))
    a, b = st.columns([3, 2])
    b.download_button(f"⬇️ Save these {len(shown):,} as CSV", df.to_csv(index=False), file_name="sql-using-table.csv",
                      mime="text/csv", key=f"ut-csv-{key}")
    sel = selected_rows(event, len(shown))
    if sel:
        r = shown[sel[0]][0]
        a.button(f"Open {r['name']}", key=f"ut-open-{key}", type="primary", on_click=go, args=(f"query://{r['uid']}",))
    else:
        a.caption("Tick a row to open the query (its checks, places and diagram).")
