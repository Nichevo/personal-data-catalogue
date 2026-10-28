"""Old versions of Power BI reports and Excel workbooks, and how a report changed over time.

Reports get copied: Sales.pbix, Sales_v2.pbix, "Sales - Copy.pbix", archive/Sales.pbix. When the names say so
they are grouped like script versions (versions.py). When they don't, two reports that share most of their
pages, measures, tables and sources are still suggested as versions of one report, with the newest-looking one
as the current version.

Each time a scan reads a changed report it keeps a short revision list on the item (date, what was added,
removed or changed) and writes the changes to the item's history, so the report's page shows how it moved.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from .store import Catalogue, now

VERSIONED_KINDS = ("report", "workbook")
SIMILAR = 0.6       # share of pages, measures, tables and sources two reports must have in common
MIN_ITEMS = 3       # a report with fewer things in it than this is too thin to match by content
KEEP_REVISIONS = 25
OLD_FOLDER_WORDS = ("archive", "archived", "old", "backup", "backups", "bak", "legacy", "previous", "prev")
OLD_LABEL_WORDS = ("old", "backup", "bak", "orig", "original", "prev", "previous", "archive", "copy")

FACET_LABELS = {"pages": "page", "visuals": "visual", "measures": "measure", "tables": "table",
                "sources": "source", "sheets": "sheet", "queries": "query", "connections": "connection",
                "pivots": "pivot table"}


# ---- what is in a report --------------------------------------------------------------------------

def facets(attrs: dict[str, Any], kind: str) -> dict[str, dict[str, str]]:
    """facet -> {name: detail}: the parts of a report or workbook that can be compared."""
    out: dict[str, dict[str, str]] = {}
    if kind == "report":
        out["pages"] = {p.get("name", ""): f"{len(p.get('visuals', []))} visuals" for p in attrs.get("pages", [])}
        out["visuals"] = {f"{p.get('name', '')} › {v.get('title') or v.get('type', '')}": v.get("type", "")
                          for p in attrs.get("pages", []) for v in p.get("visuals", [])}
        out["measures"] = {f"{m.get('table', '')}[{m.get('name', '')}]": (m.get("expression") or "").strip()
                           for m in attrs.get("measures", [])}
        out["tables"] = {t.get("name", ""): ", ".join(c.get("name", "") for c in t.get("columns", []))
                         for t in attrs.get("tables", [])}
        out["sources"] = {ref: "" for q in attrs.get("queries", [])
                          for ref in [*q.get("tables", []), *q.get("files", []), *q.get("folders", [])]}
    else:
        out["sheets"] = {s.get("name", ""): ", ".join(s.get("header", [])) for s in attrs.get("sheets", [])}
        out["queries"] = {q.get("name", ""): (q.get("expression") or "").strip() for q in attrs.get("queries", [])}
        out["connections"] = {c.get("name", ""): (c.get("command") or "").strip()
                              for c in attrs.get("connections", []) if c.get("type") != "Power Query"}
        out["pivots"] = {p.get("name") or f"pivot {i + 1}": ", ".join(p.get("fields", []))
                         for i, p in enumerate(attrs.get("pivots", []))}
    return {k: {n: d for n, d in v.items() if n} for k, v in out.items()}


def _content(attrs: dict[str, Any], kind: str) -> set[str]:
    f = facets(attrs, kind)
    return {f"{facet}:{name}" for facet, items in f.items() if facet != "visuals" for name in items}


def similarity(a: dict[str, Any], b: dict[str, Any], kind: str) -> float:
    """Share of pages, measures, tables and sources (sheets, queries for workbooks) two items have in common."""
    ca, cb = _content(a, kind), _content(b, kind)
    if min(len(ca), len(cb)) < MIN_ITEMS:
        return 0.0
    score = len(ca & cb) / len(ca | cb)
    # Two different reports on one dataset share its measures and tables but not their pages (sheets).
    front = "pages" if kind == "report" else "sheets"
    pa, pb = set(facets(a, kind).get(front, {})), set(facets(b, kind).get(front, {}))
    if pa and pb:
        score = min(score, len(pa & pb) / len(pa | pb))
    return score


def diff(older: dict[str, Any], newer: dict[str, Any], kind: str) -> dict[str, dict[str, list[str]]]:
    """facet -> {"added": [...], "removed": [...], "changed": [...]} between two versions."""
    fa, fb = facets(older, kind), facets(newer, kind)
    out: dict[str, dict[str, list[str]]] = {}
    for facet in fb:
        a, b = fa.get(facet, {}), fb[facet]
        d = {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
             "changed": sorted(n for n in set(a) & set(b) if a[n] != b[n] and facet not in ("pages", "visuals"))}
        if any(d.values()):
            out[facet] = d
    return out


def summary(changes: dict[str, dict[str, list[str]]]) -> str:
    """'+2 measures, 1 measure changed, −1 page' from a diff; '' when nothing changed."""
    bits = []
    for facet, d in changes.items():
        label = FACET_LABELS.get(facet, facet)
        for sign, which in (("+", "added"), ("−", "removed")):
            if d[which]:
                bits.append(f"{sign}{len(d[which])} {label}{'s' if len(d[which]) != 1 else ''}")
        if d["changed"]:
            bits.append(f"{len(d['changed'])} {label}{'s' if len(d['changed']) != 1 else ''} changed")
    return ", ".join(bits)


def record_revision(cat: Catalogue, key: str, previous: dict[str, Any] | None, info: dict[str, Any],
                    kind: str) -> None:
    """Keep the change from the last read of this file on the item (info["revisions"]) and in its history.
    Runs inside the scan's transaction, before the item is saved."""
    revisions = list((previous or {}).get("revisions") or [])
    entry: dict[str, Any] = {"at": now(), "modified": info.get("modified"), "size_bytes": info.get("size_bytes"),
                             "counts": counts(info, kind)}
    if previous and previous.get("modified"):
        changes = diff(previous, info, kind)
        entry["changes"] = changes
        entry["summary"] = summary(changes)
        rows = [(key, entry["at"], f"{FACET_LABELS.get(facet, facet)} {which}", name)
                for facet, d in changes.items() for which in ("added", "removed", "changed") for name in d[which]]
        if rows:
            cat.conn.executemany("INSERT INTO schema_history (asset_key, at, change, detail) VALUES (?, ?, ?, ?)",
                                 rows[:200])
    revisions.append(entry)
    info["revisions"] = revisions[-KEEP_REVISIONS:]


def counts(attrs: dict[str, Any], kind: str) -> dict[str, int]:
    f = facets(attrs, kind)
    return {facet: len(items) for facet, items in f.items()}


# ---- versions of one report -----------------------------------------------------------------------

def find_report_versions(cat: Catalogue, in_group: set[str], excluded: set[str]) -> list[dict[str, Any]]:
    """Suggestions (for versions.find_versions to store) grouping copies of one report or workbook."""
    from .versions import WEAK, base_name
    rows = [dict(r) for r in cat.conn.execute(
        "SELECT key, name, source, parent, kind, attrs FROM assets WHERE kind IN ('report', 'workbook') "
        "AND status = 'active'")]
    rows = [r for r in rows if r["key"] not in excluded]
    for r in rows:
        r["attrs"] = json.loads(r["attrs"])
        path = PurePosixPath(r["name"].replace("\\", "/"))
        base, label = base_name(path.stem)
        r.update({"folder": str(path.parent).lower(), "base": base.lower(), "ext": path.suffix.lower(),
                  "label": label or "plain", "weak": bool(label) and bool(WEAK.fullmatch(path.stem[len(base):] or "x")),
                  "old_place": any(part.lower() in OLD_FOLDER_WORDS for part in path.parent.parts)})
    parent: dict[str, str] = {r["key"]: r["key"] for r in rows}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    # 1. names that differ only by a version, date or "copy", in the same folder and source
    by_name: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_name[(r["source"], r["folder"], r["base"], r["ext"], r["kind"])].append(r)
    by_name_matched: set[str] = set()
    for members in by_name.values():
        if len(members) < 2:
            continue
        if all(m["weak"] or m["label"] == "plain" for m in members) and sum(m["weak"] for m in members) < 2:
            continue
        for m in members[1:]:
            union(members[0]["key"], m["key"])
        by_name_matched.update(m["key"] for m in members)
    # 2. the same report under a different name or in another folder: most of the content in common
    content_matched: set[str] = set()
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)
    for members in by_kind.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if find(a["key"]) == find(b["key"]):
                    continue
                if similarity(a["attrs"], b["attrs"], a["kind"]) >= SIMILAR:
                    union(a["key"], b["key"])
                    content_matched.update((a["key"], b["key"]))
    clusters: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        clusters[find(r["key"])].append(r)
    found = []
    for members in clusters.values():
        if len(members) < 2 or all(m["key"] in in_group for m in members):
            continue
        members.sort(key=lambda m: m["name"].lower())
        main, why = pick_main(members)
        main_row = next((m for m in members if m["key"] == main), members[0])
        name = PurePosixPath(main_row["name"].replace("\\", "/")).name
        by_content = [m for m in members if m["key"] in content_matched]
        how = ("the same pages, measures and sources" if len(by_content) == len(members)
               else "their names" if not by_content else "their names and the same content")
        label = "versions" if main_row["kind"] == "report" else "copies"
        title = (f"{len(members)} {label} of **{name}** (matched by {how})"
                 + (f": current looks like **{PurePosixPath(main_row['name']).name}** ({why})" if main
                    else ": tick the current one"))
        found.append({
            "id": f"versions:{main_row['source']}:{main_row['kind']}:{min(m['key'] for m in members)}",
            "subject": main or members[0]["key"],
            "title": title, "detail": ", ".join(m["name"] for m in members),
            "score": len(members) + (2 if main else 0) + (1 if by_content else 0),
            "payload": {"type": "versions", "name": name, "main_key": main,
                        "members": {m["key"]: m["label"] for m in members},
                        "attrs": {"kind": main_row["kind"], "matched_by": how}}})
    return found


def pick_main(members: list[dict]) -> tuple[str | None, str]:
    """The current version: the newest file that isn't in an archive folder or named as old; else the plain name."""
    live = [m for m in members if not m["old_place"]
            and not any(w in m["label"].lower().split() for w in OLD_LABEL_WORDS)] or members
    dated = sorted(((m["attrs"].get("mtime") or 0, m["key"]) for m in live), reverse=True)
    if dated[0][0] and (len(dated) == 1 or dated[0][0] - dated[1][0] > 60):
        when = datetime.fromtimestamp(dated[0][0]).strftime("%d %b %Y")
        where = "" if len(live) == len(members) else ", the others look archived or named as old"
        return dated[0][1], f"newest, changed {when}{where}"
    plain = [m for m in live if m["label"] == "plain"]
    if len(plain) == 1:
        return plain[0]["key"], "the name without a version"
    return None, ""


# ---- for the pages -------------------------------------------------------------------------------

def timeline(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    """Versions of this report (its group, oldest first) with what changed from each to the next, then the
    revisions scans saw of the current file. Empty when the report stands alone and was only read once."""
    from . import groups
    asset = cat.get_asset(key)
    if not asset:
        return []
    kind = asset["kind"]
    members: list[dict[str, Any]] = []
    for g in groups.for_item(cat, key):
        if g and g["type"] == "versions":
            members = [m for m in g["members"] if m["kind"] in VERSIONED_KINDS]
            break
    if not members:
        members = [{"asset_key": key, "name": asset["name"], "attrs": asset["attrs"], "label": None}]
    members.sort(key=lambda m: (m["attrs"].get("mtime") or 0, m["name"]))
    out = []
    prev: dict[str, Any] | None = None
    for m in members:
        a = m["attrs"]
        changes = diff(prev, a, kind) if prev else {}
        out.append({"key": m["asset_key"], "name": m["name"], "label": m.get("label") or "",
                    "modified": (a.get("modified") or "")[:10], "counts": counts(a, kind),
                    "changes": changes, "summary": summary(changes) if prev else "first version",
                    "current": m["asset_key"] == key})
        prev = a
    return out if len(out) > 1 else []
