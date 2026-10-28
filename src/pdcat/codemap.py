"""How your Python projects and home-built libraries connect.

After a scan, every import in your code is matched to the catalogued file that
provides it, in the same project or in another project or library you've
added. That gives:

  * "imports" links, labelled with the functions used (load_sales, tidy…)
  * the tables and files reached through those chains, down to the function
    when the library function touches the data itself
  * imports that look home-built but aren't catalogued yet, with their folder
    when it can be found (editable installs, sys.path additions)
  * flow diagrams as Graphviz DOT (rendered in the app) and Mermaid (pastes into
    GitHub, Azure DevOps, Confluence and most wikis)
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import sysconfig
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .store import Catalogue

CODE_KINDS = ("script", "notebook")


# ---- resolving imports ---------------------------------------------------------

def resolve_imports(cat: Catalogue) -> int:
    """Rebuild every "imports" link from the scanned import details. Returns the number of links."""
    modules: dict[str, str] = {}  # dotted name -> asset key
    scripts = []
    for row in cat.conn.execute(
            "SELECT key, source, attrs FROM assets WHERE kind IN ('script', 'notebook') AND status = 'active'"):
        attrs = json.loads(row["attrs"])
        for name in attrs.get("modules", []):
            modules.setdefault(name, row["key"])
        if attrs.get("import_details"):
            scripts.append((row["key"], row["source"], attrs))

    links = []
    for key, source, attrs in scripts:
        own = (attrs.get("modules") or [""])[0]
        is_package = key.endswith("/__init__.py")
        targets: dict[str, set[str]] = defaultdict(set)
        for imp in attrs["import_details"]:
            absolute = _absolute(imp["module"], imp.get("level", 0), own, is_package)
            if absolute is None:
                continue
            names = imp.get("names", [])
            # "from pkg import submodule" imports a module, not a function.
            plain_names = []
            for n in names:
                sub = f"{absolute}.{n}" if absolute else n
                if sub in modules:
                    targets[modules[sub]]
                else:
                    plain_names.append(n)
            if not absolute:
                continue
            target = _longest_match(absolute, modules)
            if target and (plain_names or not names):
                targets[target].update(plain_names)
        for target, used in targets.items():
            if target != key:
                links.append((key, "imports", f"module:{target}", target, source, None,
                              ", ".join(sorted(used))))
    with cat.conn:
        cat.conn.execute("DELETE FROM links WHERE relation = 'imports'")
        cat.conn.executemany(
            "INSERT OR REPLACE INTO links (src_key, relation, dst_ref, dst_key, source, hint, evidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", links)
    resolve_runs(cat, modules)
    return len(links)


def resolve_runs(cat: Catalogue, modules: dict[str, str] | None = None) -> int:
    """Rebuild the "runs" links: one script starting another (subprocess, python -m, runpy, %run, !python).
    A path is matched in the script's own folder, then from its project's root, then by file name
    anywhere (flagged, since it may be the wrong copy)."""
    by_rel: dict[tuple[str, str], str] = {}
    by_name: dict[str, list[str]] = defaultdict(list)
    runners = []
    for row in cat.conn.execute("SELECT key, name, source, parent, attrs FROM assets "
                                "WHERE kind IN ('script', 'notebook') AND status = 'active'"):
        rel = row["name"].replace("\\", "/").lower()
        by_rel[(row["parent"] or "", rel)] = row["key"]
        by_name[rel.rsplit("/", 1)[-1]].append(row["key"])
        attrs = json.loads(row["attrs"])
        if attrs.get("runs"):
            runners.append((row["key"], row["name"], row["source"], row["parent"] or "", attrs["runs"]))
    if modules is None:
        modules = {}
        for row in cat.conn.execute("SELECT key, attrs FROM assets WHERE kind = 'script' AND status = 'active'"):
            for name in json.loads(row["attrs"]).get("modules", []):
                modules.setdefault(name, row["key"])
    links = []
    for key, rel, source, parent, runs in runners:
        folder = rel.replace("\\", "/").rsplit("/", 1)[0] if "/" in rel else ""
        for r in runs:
            target, how = r["target"], r["how"]
            dst = None
            if target.startswith("module:"):
                dst = modules.get(target[7:]) or _longest_match(target[7:], modules)
            else:
                t = target.replace("\\", "/").lower()
                while t.startswith("./"):
                    t = t[2:]
                for cand in (_norm_join(folder.lower(), t), t, t.rsplit("/", 1)[-1] if "/" in t else None):
                    if cand and (parent, cand) in by_rel:
                        dst = by_rel[(parent, cand)]
                        break
                if dst is None:
                    same = by_name.get(t.rsplit("/", 1)[-1], [])
                    if len(same) == 1:
                        dst, how = same[0], f"{how}, matched by file name in another folder"
                    elif len(same) > 1:
                        how = f"{how}, {len(same)} files have this name"
            if dst != key:
                links.append((key, "runs", f"script:{target}", dst, source, None,
                              f"{rel}:{r['line']}" if r.get("line") else rel, how))
    with cat.conn:
        cat.conn.execute("DELETE FROM links WHERE relation = 'runs' AND source != 'by hand'")
        cat.conn.executemany(
            "INSERT OR REPLACE INTO links (src_key, relation, dst_ref, dst_key, source, hint, evidence, how) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", links)
    return len(links)


def _norm_join(folder: str, rel: str) -> str:
    parts: list[str] = []
    for part in (folder.split("/") if folder else []) + rel.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def called_by(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    """Every other script that uses this one: imports it (and which functions), or runs it."""
    return [dict(r) for r in cat.conn.execute(
        "SELECT l.src_key, l.relation, l.evidence, l.how, a.name AS src_name, a.kind AS src_kind, a.source "
        "FROM links l JOIN assets a ON a.key = l.src_key WHERE l.dst_key = ? AND l.relation IN ('imports', 'runs') "
        "ORDER BY a.source, a.name", (key,))]


def _absolute(module: str, level: int, own: str, is_package: bool) -> str | None:
    if not level:
        return module
    if not own:
        return None
    package = own.split(".") if is_package else own.split(".")[:-1]
    if level - 1 > len(package):
        return None
    base = package[: len(package) - (level - 1)]
    return ".".join([*base, module] if module else base)


def _longest_match(dotted: str, modules: dict[str, str]) -> str | None:
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        key = modules.get(".".join(parts[:i]))
        if key:
            return key
    return None


# ---- home-built libraries that aren't catalogued -----------------------------

_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
_SITE_DIRS = {Path(p).resolve() for p in {sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"],
                                          sysconfig.get_paths()["stdlib"]} if p}


def uncatalogued_libraries(cat: Catalogue) -> list[dict[str, Any]]:
    """Top-level imports that don't resolve to anything catalogued and don't look like
    the standard library or an installed third-party package.

    Returns [{"name", "used_by", "path", "how"}], most used first. "path" is a folder
    to add when one can be found: from an editable install in this Python
    environment, or from a sys.path addition in your code.
    """
    modules = set()
    sys_paths: list[str] = []
    imported: dict[str, set[str]] = defaultdict(set)
    for row in cat.conn.execute(
            "SELECT key, attrs FROM assets WHERE kind IN ('script', 'notebook') AND status = 'active'"):
        attrs = json.loads(row["attrs"])
        modules.update(m.split(".")[0] for m in attrs.get("modules", []))
        sys_paths += attrs.get("sys_path", [])
        for imp in attrs.get("import_details", []):
            if imp.get("level"):
                continue
            top = imp["module"].split(".")[0]
            if top:
                imported[top].add(row["key"])

    from .libclass import THIRD, Classifier
    clf = Classifier(cat)
    out = []
    for name, users in imported.items():
        if name in modules or name in _STDLIB:
            continue
        path, how = _locate(name, sys_paths)
        if path is None and clf.classify(name) == THIRD:
            continue  # declared by a project or a well-known package: not yours
        if how == "installed package":
            continue
        out.append({"name": name, "used_by": len(users), "path": path, "how": how})
    return sorted(out, key=lambda r: (-r["used_by"], r["name"]))


def _locate(name: str, sys_paths: list[str]) -> tuple[str | None, str]:
    for p in sys_paths:
        folder = Path(p).expanduser()
        if (folder / name).is_dir() or (folder / f"{name}.py").is_file():
            return str(folder), "sys.path in your code"
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return None, "not installed here"
    origin = spec.origin or (list(spec.submodule_search_locations or []) or [None])[0]
    if not origin or origin in ("built-in", "frozen"):
        return None, "installed package"
    path = Path(origin).resolve()
    if any(site in path.parents for site in _SITE_DIRS):
        return None, "installed package"
    # An editable install points at your own source folder: suggest the project root.
    folder = path.parent if path.name != "__init__.py" else path.parent.parent
    if folder.name == "src":
        folder = folder.parent
    return str(folder), "editable install"


# ---- what data a project reaches through its libraries -------------------------

def reached_data(cat: Catalogue, project_key: str, max_depth: int = 5) -> list[dict[str, Any]]:
    """Tables and files used by the libraries a project calls, with the chain that gets there.

    Where a library function is named in the import and that function itself
    reads or writes data, only that function's data is counted. Otherwise the
    whole library file's data is.
    """
    assets = _assets(cat)
    starts = [k for k, a in assets.items() if a["parent"] == project_key and a["kind"] in CODE_KINDS]
    imports = _import_links(cat)
    rows, seen = [], set()

    # Breadth-first, so each table/file is shown with the shortest chain that reaches it.
    queue = [(s, [s], [assets[s]["name"]]) for s in starts]
    while queue:
        key, chain, labels = queue.pop(0)
        if len(chain) > max_depth:
            continue
        for target, used in imports.get(key, []):
            t = assets.get(target)
            if target in chain or not t:
                continue
            new_chain = [*chain, target]
            new_labels = [*labels, t["name"] + (f" ({', '.join(used)})" if used else "")]
            fn_refs = t["attrs"].get("function_refs", {})
            picked = [r for n in used for r in fn_refs.get(n, [])] if used else []
            refs = picked or [[l["relation"], l["dst_ref"]] for l in _data_links(cat, target)]
            for relation, ref in refs:
                dst = _resolve_ref(cat, target, relation, ref)
                ident = (chain[0], relation, dst or ref)
                if ident in seen:
                    continue
                seen.add(ident)
                rows.append({"relation": relation, "data": (assets.get(dst) or {}).get("name") or ref.split(":", 1)[-1],
                             "data_key": dst or ref, "via": " → ".join(new_labels),
                             "functions": ", ".join(used) if picked else "", "library": t["source"]})
            queue.append((target, new_chain, new_labels))
    return rows


def _assets(cat: Catalogue) -> dict[str, dict]:
    return {r["key"]: {**dict(r), "attrs": json.loads(r["attrs"])} for r in cat.conn.execute(
        "SELECT key, kind, name, source, parent, attrs, status FROM assets")}


def _import_links(cat: Catalogue) -> dict[str, list[tuple[str, list[str]]]]:
    out: dict[str, list] = defaultdict(list)
    for l in cat.conn.execute("SELECT src_key, dst_key, evidence FROM links WHERE relation = 'imports'"):
        out[l["src_key"]].append((l["dst_key"], [n for n in (l["evidence"] or "").split(", ") if n]))
    return out


def _data_links(cat: Catalogue, key: str) -> list:
    return cat.conn.execute(
        "SELECT relation, dst_ref, dst_key FROM links WHERE src_key = ? AND relation IN ('reads', 'writes', 'calls')",
        (key,)).fetchall()


def _resolve_ref(cat: Catalogue, src_key: str, relation: str, ref: str) -> str | None:
    row = cat.conn.execute("SELECT dst_key FROM links WHERE src_key = ? AND relation = ? AND dst_ref = ?",
                           (src_key, relation, ref)).fetchone()
    return row["dst_key"] if row else None


# ---- diagrams ----------------------------------------------------------------

@dataclass
class Graph:
    nodes: dict[str, dict] = field(default_factory=dict)   # id -> {label, kind, group}
    edges: dict[tuple[str, str, str], set[str]] = field(default_factory=lambda: defaultdict(set))  # (a, b, rel) -> labels


def _run_links(cat: Catalogue) -> dict[str, list[str]]:
    out: dict[str, list] = defaultdict(list)
    for l in cat.conn.execute("SELECT src_key, dst_key FROM links WHERE relation = 'runs' AND dst_key IS NOT NULL"):
        out[l["src_key"]].append(l["dst_key"])
    return out


def hidden_from_map(cat: Catalogue, show_old: bool = False) -> set[str]:
    """Items the code map leaves out: not relevant, older versions of scripts, and (unless show_old)
    items marked old or inside a project marked old."""
    from .curation import hidden_keys
    from .groups import older_versions
    hidden = hidden_keys(cat, old=not show_old) | older_versions(cat)
    if not show_old:
        from .folderroles import archived
        hidden |= archived(cat)  # inside a folder whose role is archive
    if hidden:
        old_parents = tuple(hidden)
        marks = ", ".join("?" * len(old_parents))
        hidden |= {r[0] for r in cat.conn.execute(f"SELECT key FROM assets WHERE parent IN ({marks})", old_parents)}
    return hidden


def build_graph(cat: Catalogue, project: str | None = None, *, level: str = "modules",
                include_data: bool = True, show_old: bool = False) -> Graph:
    """Graph of code (and optionally data) for one project/library source, or everything.

    level="modules": one node per file, grouped by project/library.
    level="projects": one node per project/library, edges summarised.
    Starting from a project, everything it reaches through imports is included.
    """
    assets = _assets(cat)
    imports = _import_links(cat)
    runs = _run_links(cat)
    hidden = hidden_from_map(cat, show_old)
    from .links import unimportant
    noise = unimportant(cat)
    backup_of = _backup_main(cat)
    code = {k for k, a in assets.items() if a["kind"] in CODE_KINDS and a["status"] == "active" and k not in hidden}
    if project:
        scope, frontier = set(), [k for k in code if assets[k]["source"] == project]
        while frontier:
            k = frontier.pop()
            if k in scope:
                continue
            scope.add(k)
            frontier += [t for t, _ in imports.get(k, []) if t in code]
            frontier += [t for t in runs.get(k, []) if t in code]
    else:
        scope = code

    g = Graph()
    roles = {a["source"]: a["attrs"].get("role", "project") for a in assets.values() if a["kind"] == "project"}

    def code_node(key: str) -> str:
        a = assets[key]
        if level == "projects":
            nid = f"src:{a['source']}"
            g.nodes[nid] = {"label": a["source"], "kind": roles.get(a["source"], "project"), "group": None}
        else:
            nid = key
            g.nodes[nid] = {"label": a["name"], "kind": "code", "group": a["source"]}
        return nid

    for k in scope:
        a_id = code_node(k)
        for target, used in imports.get(k, []):
            if target in scope:
                b_id = code_node(target)
                if a_id != b_id:
                    g.edges[(a_id, b_id, "imports")].update(used)
        for target in runs.get(k, []):
            if target in scope:
                b_id = code_node(target)
                if a_id != b_id:
                    g.edges[(a_id, b_id, "runs")]
        if include_data:
            for l in _data_links(cat, k):
                d = l["dst_key"] or l["dst_ref"]
                if d in hidden or (k, l["relation"], d) in noise:
                    continue
                if d in backup_of:  # a main table and its backup show as one
                    d = backup_of[d]
                da = assets.get(d)
                kind = (da["kind"] if da else ("file" if d.startswith("file:") else "table"))
                label = da["name"] if da else d.split(":", 1)[-1]
                if da and da["kind"] in CODE_KINDS:
                    continue
                if d in backup_of.values():
                    label = f"{label} (main + backup)"
                g.nodes[d] = {"label": label, "kind": "file" if kind in ("data_file", "file") else "table",
                              "group": None}
                g.edges[(a_id, d, l["relation"])]
    return g


def _backup_main(cat: Catalogue) -> dict[str, str]:
    """Each member of a main + backup pair -> the main table's key."""
    return {r[0]: r[1] for r in cat.conn.execute(
        "SELECT m.asset_key, g.main_key FROM group_members m JOIN item_groups g ON g.uid = m.group_uid "
        "WHERE g.type = 'backup' AND g.main_key IS NOT NULL")}


def _ids(g: Graph) -> dict[str, str]:
    return {k: f"n{i}" for i, k in enumerate(sorted(g.nodes))}


def _q(text: str) -> str:
    return text.replace("\\", "/").replace('"', "'")


def to_dot(g: Graph) -> str:
    ids = _ids(g)
    style = {
        "code": 'shape=box style="rounded,filled" fillcolor="#e8f0fe" color="#4a73c9"',
        "project": 'shape=box style="rounded,filled" fillcolor="#e8f0fe" color="#4a73c9" penwidth=2',
        "library": 'shape=component style=filled fillcolor="#f3e8fd" color="#8a4ac9"',
        "table": 'shape=cylinder style=filled fillcolor="#e6f4ea" color="#3b8a4e"',
        "file": 'shape=note style=filled fillcolor="#fef7e0" color="#b58a00"',
        "function": 'shape=box style="rounded,filled" fillcolor="#e8f0fe" color="#4a73c9"',
        "focus": 'shape=box style="rounded,filled,bold" fillcolor="#fde68a" color="#b45309" penwidth=2',
        "libfn": 'shape=box style="rounded,filled" fillcolor="#f3e8fd" color="#8a4ac9"',
        "external": 'shape=plain fontcolor="#666666"',
        "report": 'shape=box style="rounded,filled" fillcolor="#ffe7d6" color="#e07b39"',
        "workbook": 'shape=box style="rounded,filled" fillcolor="#ddf1e6" color="#1e7a46"',
        "access_db": 'shape=cylinder style=filled fillcolor="#f6e3e3" color="#a33a3a"',
        "api": 'shape=hexagon style=filled fillcolor="#e0f2fe" color="#0284c7"',
        "web_scraping": 'shape=octagon style=filled fillcolor="#fce7f3" color="#be185d"',
        "web_file": 'shape=note style=filled fillcolor="#e0f2fe" color="#0284c7"',
        "database": 'shape=cylinder style=filled fillcolor="#d1fae5" color="#047857" penwidth=2',
        "cloud_storage": 'shape=ellipse style=filled fillcolor="#ede9fe" color="#6d28d9"',
        "sharepoint": 'shape=folder style=filled fillcolor="#ccfbf1" color="#0f766e"',
        "sap": 'shape=box3d style=filled fillcolor="#fef3c7" color="#b45309"',
    }
    lines = ["digraph codemap {", '  rankdir=LR; graph [fontname="Helvetica" fontsize=11];',
             '  node [fontname="Helvetica" fontsize=10]; edge [fontname="Helvetica" fontsize=9 color="#666666"];']
    groups: dict[str | None, list[str]] = defaultdict(list)
    for k, n in g.nodes.items():
        groups[n["group"]].append(k)
    for i, (group, keys) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0]))):
        indent = "  "
        if group:
            lines += [f"  subgraph cluster_{i} {{", f'    label="{_q(group)}"; style="rounded,dashed"; color="#999999";']
            indent = "    "
        for k in sorted(keys):
            n = g.nodes[k]
            lines.append(f'{indent}{ids[k]} [label="{_q(n["label"])}" {style.get(n["kind"], "")}];')
        if group:
            lines.append("  }")
    for (a, b, rel), labels in sorted(g.edges.items()):
        attrs = []
        text = ", ".join(sorted(labels)) if rel in ("imports", "call") else rel
        if text:
            attrs.append(f'label="{_q(_short(text))}"')
        if rel == "writes":
            attrs.append('color="#c5221f" fontcolor="#c5221f"')
        elif rel in ("reads", "calls"):
            attrs.append('style=dashed')
        elif rel == "call":
            attrs.append('color="#4a73c9"')
        else:
            attrs.append('penwidth=1.5 color="#4a73c9"')
        lines.append(f"  {ids[a]} -> {ids[b]} [{' '.join(attrs)}];")
    lines.append("}")
    return "\n".join(lines)


def to_mermaid(g: Graph) -> str:
    ids = _ids(g)
    shape = {"code": ('["', '"]'), "project": ('["', '"]'), "library": ('[["', '"]]'),
             "table": ('[("', '")]'), "file": ('>"', '"]'), "function": ('("', '")'),
             "focus": ('(["', '"])'), "libfn": ('("', '")'), "external": ('["', '"]'),
             "report": ('("', '")'), "workbook": ('("', '")'), "access_db": ('[("', '")]'),
             "api": ('{{"', '"}}'), "web_scraping": ('{{"', '"}}'), "web_file": ('>"', '"]'),
             "database": ('[("', '")]'), "cloud_storage": ('(("', '"))'), "sharepoint": ('[/"', '"/]'),
             "sap": ('[["', '"]]')}
    lines = ["flowchart LR"]
    groups: dict[str | None, list[str]] = defaultdict(list)
    for k, n in g.nodes.items():
        groups[n["group"]].append(k)
    for i, (group, keys) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0]))):
        indent = "    "
        if group:
            lines.append(f'    subgraph g{i}["{_mq(group)}"]')
            indent = "        "
        for k in sorted(keys):
            n = g.nodes[k]
            o, c = shape.get(n["kind"], ('["', '"]'))
            lines.append(f"{indent}{ids[k]}{o}{_mq(n['label'])}{c}")
        if group:
            lines.append("    end")
    for (a, b, rel), labels in sorted(g.edges.items()):
        text = _short(", ".join(sorted(labels))) if rel in ("imports", "call") else rel
        arrow = "==>" if rel == "writes" else ("-.->" if rel in ("reads", "calls") else "-->")
        lines.append(f'    {ids[a]} {arrow}|"{_mq(text)}"| {ids[b]}' if text else f"    {ids[a]} {arrow} {ids[b]}")
    lines += ["    classDef table fill:#e6f4ea,stroke:#3b8a4e", "    classDef file fill:#fef7e0,stroke:#b58a00",
              "    classDef library fill:#f3e8fd,stroke:#8a4ac9", "    classDef libfn fill:#f3e8fd,stroke:#8a4ac9",
              "    classDef focus fill:#fde68a,stroke:#b45309,stroke-width:2px",
              "    classDef report fill:#ffe7d6,stroke:#e07b39", "    classDef workbook fill:#ddf1e6,stroke:#1e7a46",
              "    classDef api fill:#e0f2fe,stroke:#0284c7", "    classDef web_scraping fill:#fce7f3,stroke:#be185d",
              "    classDef web_file fill:#e0f2fe,stroke:#0284c7", "    classDef database fill:#d1fae5,stroke:#047857",
              "    classDef cloud_storage fill:#ede9fe,stroke:#6d28d9",
              "    classDef sharepoint fill:#ccfbf1,stroke:#0f766e", "    classDef sap fill:#fef3c7,stroke:#b45309"]
    for kind in ("table", "file", "library", "libfn", "focus", "report", "workbook", "api", "web_scraping",
                 "web_file", "database", "cloud_storage", "sharepoint", "sap"):
        members = [ids[k] for k, n in g.nodes.items() if n["kind"] == kind]
        if members:
            lines.append(f"    class {','.join(members)} {kind}")
    return "\n".join(lines)


def _mq(text: str) -> str:
    return re.sub(r'["<>]', "'", text.replace("\\", "/"))


def _short(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---- inside one script: functions and what they call -------------------------

_BUILTINS = set(dir(__import__("builtins")))


class CodeIndex:
    """Resolves the calls in a script's functions to functions in the same file or
    in another catalogued file (through its imports)."""

    def __init__(self, cat: Catalogue):
        self.cat = cat
        self.assets = {k: a for k, a in _assets(cat).items() if a["kind"] in CODE_KINDS and a["status"] == "active"}
        self.modules: dict[str, str] = {}
        for k, a in self.assets.items():
            for name in a["attrs"].get("modules", []):
                self.modules.setdefault(name, k)

    def functions(self, key: str) -> dict[str, dict]:
        return (self.assets.get(key) or {}).get("attrs", {}).get("function_info") or {}

    def resolve(self, key: str, call: str, scope: str | None = None) -> tuple:
        """("local", key, fn) | ("lib", key, fn) | ("other", call)."""
        info = self.functions(key)
        if scope and scope != "(script)":
            # inner() inside outer -> outer.inner; self.m() inside Class.x -> Class.m
            parts = scope.split(".")
            for i in range(len(parts), 0, -1):
                cand = ".".join([*parts[:i], call])
                if cand in info and cand != scope:
                    return ("local", key, cand)
            if call.startswith(("self.", "cls.")) and len(parts) > 1:
                cand = ".".join([*parts[:-1], call.split(".", 1)[1]])
                if cand in info:
                    return ("local", key, cand)
        if call in info:
            return ("local", key, call)
        attrs = self.assets.get(key, {}).get("attrs", {})
        head, _, rest = call.partition(".")
        target = (attrs.get("import_map") or {}).get(head)
        if target:
            level = len(target) - len(target.lstrip("."))
            own = (attrs.get("modules") or [""])[0]
            absolute = _absolute(target.lstrip("."), level, own, key.endswith("/__init__.py")) if level else target
            if absolute:
                dotted = f"{absolute}.{rest}" if rest else absolute
                parts = dotted.split(".")
                for i in range(len(parts) - 1, 0, -1):
                    mod_key = self.modules.get(".".join(parts[:i]))
                    if mod_key:
                        return ("lib", mod_key, ".".join(parts[i:]))
        return ("other", call)

    def calls(self, key: str, fn: str) -> list[tuple]:
        return [self.resolve(key, c, fn) for c in self.functions(key).get(fn, {}).get("calls", [])]

    def callers(self, key: str, fn: str) -> list[tuple[str, str]]:
        """(key, function) pairs that call key::fn, in this file and in files that import it."""
        importers = {key} | {r["src_key"] for r in self.cat.conn.execute(
            "SELECT src_key FROM links WHERE relation = 'imports' AND dst_key = ?", (key,))}
        out = []
        for k in sorted(importers):
            for name in self.functions(k):
                if (k, name) != (key, fn) and any(r[1:] == (key, fn) for r in self.calls(k, name) if r[0] != "other"):
                    out.append((k, name))
        return out

    def data(self, key: str, fn: str) -> list[dict]:
        refs = (self.assets.get(key) or {}).get("attrs", {}).get("function_refs", {}).get(fn, [])
        out = []
        for relation, ref in refs:
            dst = _resolve_ref(self.cat, key, relation, ref)
            out.append({"relation": relation, "ref": ref, "key": dst})
        return out


def _fn_id(key: str, fn: str) -> str:
    return f"{key}::{fn}"


def function_graph(cat: Catalogue, key: str, focus: str | None = None, *, depth: int = 2,
                   include_data: bool = True, include_other: bool = False,
                   index: CodeIndex | None = None) -> Graph:
    """Call graph inside one script.

    focus=None: the whole script, every function in it and the library functions it calls.
    focus="fn": that function, what it calls down to `depth` levels (following calls into
    other catalogued files too), and what calls it.
    """
    idx = index or CodeIndex(cat)
    assets = _assets(cat)
    g = Graph()

    def node(k: str, fn: str, *, is_focus: bool = False) -> str:
        nid = _fn_id(k, fn)
        a = idx.assets.get(k) or assets.get(k) or {"name": k, "source": ""}
        group = a["name"] if k == key else f"{a['source']}: {a['name']}"
        kind = "focus" if is_focus else ("function" if k == key else "libfn")
        if nid not in g.nodes or is_focus:
            g.nodes[nid] = {"label": fn, "kind": kind, "group": group}
        return nid

    def add_data(k: str, fn: str, nid: str) -> None:
        for d in idx.data(k, fn):
            dk = d["key"] or d["ref"]
            da = assets.get(dk)
            label = da["name"] if da else d["ref"].split(":", 1)[-1]
            is_file = (da["kind"] == "data_file") if da else d["ref"].startswith("file:")
            g.nodes[dk] = {"label": label, "kind": "file" if is_file else "table", "group": None}
            g.edges[(nid, dk, d["relation"])]

    def expand(k: str, fn: str, levels: int, seen: set) -> None:
        nid = _fn_id(k, fn)
        if include_data:
            add_data(k, fn, nid)
        if (k, fn) in seen:
            return
        seen.add((k, fn))
        for r in idx.calls(k, fn):
            if r[0] == "other":
                name = r[1]
                if include_other and name.split(".")[0] not in _BUILTINS:
                    g.nodes[f"ext:{name}"] = {"label": name, "kind": "external", "group": None}
                    g.edges[(nid, f"ext:{name}", "call")]
                continue
            _, tk, tfn = r
            tid = node(tk, tfn)
            g.edges[(nid, tid, "call")]
            if levels > 1:
                expand(tk, tfn, levels - 1, seen)
            elif include_data:
                add_data(tk, tfn, tid)

    if focus is None:
        seen: set = set()
        for fn in idx.functions(key):
            node(key, fn)
        for fn in idx.functions(key):
            expand(key, fn, 1, seen)
    else:
        fid = node(key, focus, is_focus=True)
        expand(key, focus, max(depth, 1), set())
        for ck, cfn in idx.callers(key, focus):
            g.edges[(node(ck, cfn), fid, "call")]
        g.nodes[fid]["kind"] = "focus"
    return g


# ---- the interactive viewer ---------------------------------------------------------

def _edge_label(labels: set[str], limit: int = 3) -> str:
    items = sorted(labels)
    return ", ".join(items[:limit]) + (f" +{len(items) - limit}" if len(items) > limit else "")


def cyto_elements(cat: Catalogue, project: str | None = None, *, level: str = "modules", include_data: bool = True,
                  include_packages: bool = False, show_old: bool = False) -> list[dict[str, Any]]:
    """Elements for the graph viewer: files grouped by project and sub-folder, tables grouped by
    database, files by folder, and third-party packages in one group (closed to start with)."""
    g = build_graph(cat, project, level=level, include_data=include_data, show_old=show_old)
    assets = _assets(cat)
    roles = {a["source"]: a["attrs"].get("role", "project") for a in assets.values() if a["kind"] == "project"}
    nodes: dict[str, dict[str, Any]] = {}
    count: dict[str, int] = defaultdict(int)

    def group(gid: str, label: str, parent: str | None = None, kind: str = "group") -> str:
        if gid not in nodes:
            nodes[gid] = {"data": {"id": gid, "label": label, "kind": kind, **({"parent": parent} if parent else {})}}
        return gid

    def place(nid: str, parent: str | None) -> None:
        while parent:
            count[parent] += 1
            parent = nodes[parent]["data"].get("parent")

    for nid, n in g.nodes.items():
        a = assets.get(nid)
        data: dict[str, Any] = {"id": nid, "label": n["label"]}
        parent = None
        if n["kind"] == "code" and a:
            role = roles.get(a["source"], "project")
            data["kind"] = "library" if role == "library" else "code"
            data["key"] = nid
            data["functions"] = bool(a["attrs"].get("function_info"))
            parent = group(f"grp:{a['source']}", ("📚 " if role == "library" else "📁 ") + a["source"])
            parts = a["name"].split("/")[:-1]
            for i in range(len(parts)):
                parent = group(f"grp:{a['source']}/{'/'.join(parts[:i + 1])}", parts[i] + "/", parent)
            data["label"] = a["name"].split("/")[-1]
        elif n["kind"] in ("project", "library"):
            data.update({"kind": n["kind"], "key": f"py://{n['label']}"})
        else:
            data["kind"] = "file" if n["kind"] == "file" else "table"
            if a:
                data["key"] = nid
                data["kind"] = {"view": "view", "report": "report", "workbook": "workbook"}.get(a["kind"], data["kind"])
                db = a["attrs"].get("database")
                if a["kind"] in ("table", "view") and db:
                    parent = group(f"grp:db:{a['source']}/{db}", f"🛢️ {db}")
                elif a["kind"] in ("data_file", "file_series", "workbook", "report"):
                    parent = group(f"grp:files:{a['source']}", f"🗂️ {a['source']}")
            else:
                parent = group("grp:unmatched", "Not catalogued")
        if parent:
            data["parent"] = parent
            place(nid, parent)
        nodes[nid] = {"data": data}
    edges = [{"data": {"id": f"e{i}", "source": a, "target": b, "relation": rel,
                       "label": _edge_label(labels) if rel == "imports" else "runs" if rel == "runs" else ""}}
             for i, ((a, b, rel), labels) in enumerate(g.edges.items())]
    if include_packages and level == "modules":
        from .libclass import THIRD, Classifier, normalise
        clf = Classifier(cat)
        pkg_group = group("grp:packages", "📦 Third-party packages", kind="group")
        nodes[pkg_group]["data"]["closed"] = True
        seen = set()
        for nid in list(g.nodes):
            a = assets.get(nid)
            if not a or a["kind"] not in CODE_KINDS:
                continue
            for imp in a["attrs"].get("import_details", []):
                if imp.get("level"):
                    continue
                top = imp["module"].split(".")[0]
                if clf.classify(top, a["source"]) != THIRD:
                    continue
                pkg = normalise(clf.package(top))
                pid = f"pkg://{pkg}"
                if pid not in nodes:
                    nodes[pid] = {"data": {"id": pid, "label": clf.package(top), "kind": "package", "key": pid,
                                           "parent": pkg_group}}
                    place(pid, pkg_group)
                if (nid, pid) not in seen:
                    seen.add((nid, pid))
                    edges.append({"data": {"id": f"p{len(edges)}", "source": nid, "target": pid,
                                           "relation": "uses", "label": ""}})
    for gid, c in count.items():
        nodes[gid]["data"]["count"] = c
    # Groups with nothing in them would draw as empty boxes.
    return [n for n in nodes.values() if not n["data"]["id"].startswith("grp:") or count.get(n["data"]["id"])] + edges
