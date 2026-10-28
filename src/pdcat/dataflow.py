"""The data flow diagram: an ETL-style picture of where data comes from and the path it takes.

Left to right: the sources (APIs, scraped web sites, database connections, cloud storage,
SAP, and the tables and files nothing you catalogued writes), then the scripts, notebooks,
SQL files, reports and workbooks that read them, the tables and files those write, and on
to whatever reads those in turn. The layout is worked out automatically.

Starting from one project, the diagram follows the data both ways: upstream to whatever
writes the tables and files the project reads, and downstream to whatever reads what the
project writes (a Power BI report on a table a script loads, say), a few steps each way.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .codemap import Graph, _assets, _backup_main, hidden_from_map
from .datasources import SOURCE_TYPES
from .store import Catalogue

PROCESS_KINDS = ("script", "notebook", "sql_script", "report", "workbook", "access_db")
CODE = ("script", "notebook", "sql_script")
GROUP_NAMES = {"api": "🌐 APIs and web services", "web_scraping": "🕸️ Web scraping", "web_file": "⬇️ Files from the web",
               "database": "🛢️ Database connections", "cloud_storage": "☁️ Cloud storage",
               "sharepoint": "📎 SharePoint", "sap": "🏭 SAP"}


def flow_graph(cat: Catalogue, project: str | None = None, *, level: str = "files", show_old: bool = False,
               hops: int = 3, focus: str | None = None, start: set[str] | None = None,
               downstream: bool = True) -> Graph:
    """The flow for one project or folder source (followed `hops` steps up- and downstream), or everything.
    focus: one script, notebook or report instead of a project, drawn highlighted.
    start: any set of scripts and reports to start from instead (the Power BI map starts from every report);
    downstream=False follows the data upstream only.
    level="projects" shows each project as one box instead of one per script."""
    assets = _assets(cat)
    hidden = hidden_from_map(cat, show_old)
    from .links import unimportant
    noise = unimportant(cat)
    backup_of = _backup_main(cat)
    processes = {k for k, a in assets.items() if a["kind"] in PROCESS_KINDS and a["status"] == "active"
                 and k not in hidden}

    reads: dict[str, set[str]] = defaultdict(set)    # process -> data it reads
    writes: dict[str, set[str]] = defaultdict(set)   # process -> data it writes
    runs: dict[str, set[str]] = defaultdict(set)
    readers: dict[str, set[str]] = defaultdict(set)  # data -> processes reading it
    writers: dict[str, set[str]] = defaultdict(set)
    for l in cat.conn.execute("SELECT src_key, relation, dst_ref, dst_key FROM links "
                              "WHERE relation IN ('reads', 'writes', 'runs')"):
        src = l["src_key"]
        if src not in processes:
            continue
        if l["relation"] == "runs":
            if l["dst_key"] in processes and l["dst_key"] != src:
                runs[src].add(l["dst_key"])
            continue
        d = l["dst_key"] or l["dst_ref"]
        if d in hidden or (src, l["relation"], d) in noise or d in processes:
            continue
        d = backup_of.get(d, d)
        a = assets.get(d)
        if a and a["kind"] in ("script", "notebook", "project", "measure"):
            continue
        if l["relation"] == "reads":
            reads[src].add(d)
            readers[d].add(src)
        else:
            writes[src].add(d)
            writers[d].add(src)

    if project or focus or start is not None:
        if start is not None:
            start = set(start) & processes
        else:
            start = {focus} & processes if focus else {k for k in processes if assets[k]["source"] == project}
        scope = set(start)
        # Downstream only from what is downstream, and upstream only from what is upstream, so a report
        # reading the project's output doesn't pull in everything else that report reads.
        steps = [lambda p: {w for d in reads[p] for w in writers[d]}]
        if downstream:
            steps.append(lambda p: {r for d in writes[p] for r in readers[d]} | runs[p])
        for step in steps:
            frontier = set(start)
            for _ in range(hops):
                frontier = {q for p in frontier for q in step(p)} - scope
                scope |= frontier
    else:
        scope = {k for k in processes if reads[k] or writes[k] or runs[k]}

    g = Graph()
    roles = {a["source"]: a["attrs"].get("role", "project") for a in assets.values() if a["kind"] == "project"}

    def process_node(key: str) -> str:
        a = assets[key]
        if a["kind"] in CODE:
            if level == "projects":
                nid = f"src:{a['source']}"
                g.nodes[nid] = {"label": a["source"], "kind": roles.get(a["source"], "project"), "group": None,
                                "key": f"py://{a['source']}"}
                return nid
            g.nodes[key] = {"label": a["name"], "kind": "code", "group": ("📁 " + a["source"]), "key": key}
        else:
            g.nodes[key] = {"label": a["name"], "kind": a["kind"], "group": "📊 Reports and workbooks"
                            if a["kind"] in ("report", "workbook") else None, "key": key}
        return key

    def data_node(d: str) -> str:
        a = assets.get(d)
        if a and a["kind"] == "data_source":
            t = a["attrs"].get("source_type", "api")
            nid = "ext:" + a["attrs"].get("identity", f"{t}:{a['name'].lower()}")
            if nid not in g.nodes:
                g.nodes[nid] = {"label": f"{SOURCE_TYPES.get(t, ('•',))[0]} {a['name']}", "kind": t,
                                "group": GROUP_NAMES.get(t), "key": d, "source_type": t}
            return nid
        if a:
            kind = {"table": "table", "view": "table", "procedure": "table", "data_file": "file",
                    "file_series": "file", "folder": "file"}.get(a["kind"], a["kind"])
            g.nodes.setdefault(d, {"label": a["name"], "kind": kind, "group": None, "key": d,
                                   "database": a["attrs"].get("database")})
        else:
            kind, name = d.split(":", 1) if ":" in d else ("table", d)
            parts = name.split(".")
            g.nodes.setdefault(d, {"label": name, "kind": "file" if kind == "file" else "table", "group": None,
                                   "key": None, "uncatalogued": True,
                                   "database": parts[-3] if kind == "table" and len(parts) >= 3 else None})
        return d

    for p in sorted(scope):
        pid = process_node(p)
        for d in reads[p]:
            g.edges[(data_node(d), pid, "reads")]
        for d in writes[p]:
            g.edges[(pid, data_node(d), "writes")]
        for t in runs[p]:
            if t in scope:
                tid = process_node(t)
                if tid != pid:
                    g.edges[(pid, tid, "runs")]

    # Code that touches no data itself (it may call a library that does) has nothing to show here.
    incoming = {b for (_, b, _) in g.edges}
    outgoing = {a for (a, _, _) in g.edges}
    for nid in [n for n in g.nodes if n not in incoming and n not in outgoing]:
        del g.nodes[nid]
    if focus in g.nodes:
        g.nodes[focus]["focus"] = True
    # Tables and files nothing here writes are where the data starts: group them on the left.
    for nid, n in g.nodes.items():
        n["role"] = "source" if nid not in incoming else ("output" if nid not in outgoing else "middle")
        if n["kind"] == "table" and n["role"] == "source":
            n["group"] = f"🛢️ {n['database']}" if n.get("database") else "🛢️ Tables"
        elif n["kind"] == "file" and n["role"] == "source":
            n["group"] = "📄 Input files"
    return g


def flow_elements(g: Graph) -> list[dict[str, Any]]:
    """Elements for the graph viewer, one level of groups."""
    nodes: dict[str, dict] = {}
    count: dict[str, int] = defaultdict(int)
    for nid, n in g.nodes.items():
        data: dict[str, Any] = {"id": nid, "label": n["label"], "kind": n["kind"]}
        if n.get("key"):
            data["key"] = n["key"]
        for extra in ("focus", "weight", "flag"):
            if n.get(extra):
                data[extra] = n[extra]
        if n["group"]:
            gid = f"grp:flow:{n['group']}"
            nodes.setdefault(gid, {"data": {"id": gid, "label": n["group"], "kind": "group"}})
            data["parent"] = gid
            count[gid] += 1
        nodes[nid] = {"data": data}
    for gid, c in count.items():
        nodes[gid]["data"]["count"] = c
    edges = [{"data": {"id": f"f{i}", "source": a, "target": b, "relation": rel,
                       "label": "runs" if rel == "runs" else ""}}
             for i, (a, b, rel) in enumerate(sorted(g.edges))]
    return list(nodes.values()) + edges


def summary(g: Graph) -> dict[str, int]:
    """How many of each kind of source the diagram starts from: {"api": 2, "table": 5, ...}."""
    out: dict[str, int] = defaultdict(int)
    for n in g.nodes.values():
        if n.get("role") == "source" and n["kind"] not in (*PROCESS_KINDS, "code", "project", "library"):
            out[n["kind"]] += 1
    return dict(out)


def source_rows(cat: Catalogue, project: str | None = None, used_by: str | None = None) -> list[dict[str, Any]]:
    """Every outside source with what uses it, for the table under the diagram and its CSV.
    used_by: only the sources one script or report uses."""
    rows = []
    sql = ("SELECT s.key, s.name, s.source, s.attrs, l.src_key, l.relation, l.evidence, l.how, c.name AS used_by "
           "FROM assets s JOIN links l ON l.dst_key = s.key LEFT JOIN assets c ON c.key = l.src_key "
           "WHERE s.kind = 'data_source' AND s.status = 'active'")
    args: tuple = ()
    if project:
        sql += " AND s.source = ?"
        args = (project,)
    if used_by:
        sql += " AND l.src_key = ?"
        args += (used_by,)
    for r in cat.conn.execute(sql + " ORDER BY s.name, c.name", args):
        a = json.loads(r["attrs"])
        t = a.get("source_type", "")
        rows.append({"type": SOURCE_TYPES.get(t, ("", t))[1], "source": r["name"],
                     "engine": a.get("engine", ""), "addresses": ", ".join(a.get("addresses", [])),
                     "project or folder": r["source"], "used by": r["used_by"] or r["src_key"],
                     "reads or writes": r["relation"], "where": r["evidence"] or "", "how found": r["how"] or "",
                     "key": r["key"], "used_by_key": r["src_key"]})
    return rows
