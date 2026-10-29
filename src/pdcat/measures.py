"""A Power BI report's measures as a table: what each uses, where it's shown, and whether it's used.

A measure is used when a visual in this file shows it, or when a used measure builds on it
(followed through every step). Reports connected to a published dataset can use measures this file
can't see, so the app says "not used in this file" rather than "unused".
"""
from __future__ import annotations

import json
import re
from typing import Any

from .store import Catalogue
from .textclean import as_text

_REF = re.compile(r"(?:'((?:[^']|'')+)'|([A-Za-z_][\w ]*?))?\[([^\]]+)\]")


def dax_refs(expression: str, measures: dict[str, str]) -> tuple[list[str], list[str]]:
    """(measures used, columns used) by a DAX expression. `measures` maps a measure's name (lower case) to
    its "Table[Name]" label. A bare [Name] is a measure when one has that name, else a column."""
    text = re.sub(r"//[^\n]*|--[^\n]*|/\*[\s\S]*?\*/", " ", as_text(expression))
    text = re.sub(r'"(?:[^"]|"")*"', '""', text)
    used_m, used_c = [], []
    for m in _REF.finditer(text):
        table = (m.group(1) or m.group(2) or "").replace("''", "'").strip()
        name = m.group(3).strip()
        if name.lower() in measures and (not table or measures[name.lower()].lower().startswith(table.lower() + "[")):
            label = measures[name.lower()]
            if label not in used_m:
                used_m.append(label)
        elif table:
            label = f"{table}[{name}]"
            if label not in used_c:
                used_c.append(label)
    return used_m, used_c


def measure_rows(cat: Catalogue, report_key: str) -> list[dict[str, Any]]:
    """One row per measure: name, table, folder, format, uses (columns, measures), pages and visuals,
    in glossary, used and why."""
    from .knowledge import list_metrics
    report = cat.get_asset(report_key)
    if report is None:
        return []
    a = report["attrs"]
    rows = [dict(m) for m in cat.children(report_key) if m["kind"] == "measure"]
    by_label = {}
    for r in rows:
        attrs = json.loads(r["attrs"] or "{}")
        r["attrs"] = attrs
        by_label[f"{attrs.get('table', '')}[{attrs.get('name', r['name'])}]"] = r
    names = {lab.split("[", 1)[1][:-1].lower(): lab for lab in by_label}
    visuals: dict[str, list[str]] = {}
    for p in a.get("pages", []):
        for v in p.get("visuals", []):
            for f in v.get("fields", []):
                if f["kind"] == "measure":
                    visuals.setdefault(f"{f['table']}[{f['name']}]", []).append(f"{p['name']}: {v['title'] or v['type']}")
    glossary = {m.get("from_measure") for m in list_metrics(cat) if m.get("from_measure")}
    uses: dict[str, list[str]] = {}
    for lab, r in by_label.items():
        um, uc = dax_refs(as_text(r["attrs"].get("expression")), names)
        uses[lab] = [x for x in um if x != lab]
        r["_columns"] = uc
    # used: shown by a visual, or built on by a used measure (every step)
    used: dict[str, str] = {lab: "shown in a visual" for lab in by_label if visuals.get(lab)}
    frontier = list(used)
    while frontier:
        nxt = []
        for lab in frontier:
            for dep in uses.get(lab, []):
                if dep in by_label and dep not in used:
                    used[dep] = f"used by {lab}"
                    nxt.append(dep)
        frontier = nxt
    out = []
    for lab, r in sorted(by_label.items(), key=lambda kv: kv[0].lower()):
        attrs = r["attrs"]
        out.append({"key": r["key"], "measure": attrs.get("name", r["name"]), "table": attrs.get("table", ""),
                    "folder": attrs.get("folder", "") or "", "format": attrs.get("format", "") or "",
                    "uses measures": ", ".join(uses.get(lab, [])), "uses columns": ", ".join(r["_columns"]),
                    "pages": ", ".join(attrs.get("used_on") or []), "visuals": len(visuals.get(lab, [])),
                    "shown in": "; ".join(visuals.get(lab, [])[:6]), "in glossary": r["key"] in glossary,
                    "used": lab in used, "why": used.get(lab, "not used in this file"),
                    "description": r.get("description") or ""})
    return out


def wireframe_svg(page: dict[str, Any], highlight: int | None = None, width: int = 760) -> str:
    """An SVG drawing of a report page: each visual in its place with its type and title."""
    pw, ph = (page.get("size") or [1280, 720])[:2]
    scale = width / (pw or 1280)
    h = int((ph or 720) * scale)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {h}" width="100%" '
             f'style="background:#fafafa;border:1px solid #ddd;border-radius:6px;font-family:sans-serif">']
    for i, v in enumerate(page.get("visuals", [])):
        x, y, w, vh = [c * scale for c in (v.get("pos") or [0, 0, 0, 0])]
        if w <= 0 or vh <= 0:
            continue
        on = i == highlight
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{vh:.0f}" rx="4" '
                     f'fill="{"#e8f0fe" if on else "#ffffff"}" stroke="{"#3b6fd8" if on else "#9aa3b2"}" '
                     f'stroke-width="{2 if on else 1}"/>')
        label = _esc(v.get("title") or "")
        kind = _esc(_type_label(v.get("type", "")))
        parts.append(f'<text x="{x + 6:.0f}" y="{y + 16:.0f}" font-size="11" fill="#555">{i + 1}. {kind}</text>')
        if label and vh > 30:
            parts.append(f'<text x="{x + 6:.0f}" y="{y + 31:.0f}" font-size="12" fill="#111">{label[:40]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _type_label(t: str) -> str:
    """columnChart -> column chart."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", t).lower()


# ---- the same measure in several reports ----------------------------------------------------------

def _dax_key(expression: str) -> str:
    """A DAX expression with comments, spacing and case taken out, so two copies compare equal."""
    text = re.sub(r"//[^\n]*|--[^\n]*|/\*[\s\S]*?\*/", " ", as_text(expression))
    return re.sub(r"\s+", "", text).lower()


def across_reports(cat: Catalogue) -> dict[str, list[dict[str, Any]]]:
    """Measures that recur across Power BI reports.

    "same_dax": one entry per expression found in more than one report, with every name it goes by and
    where; names that differ are worth agreeing on. "same_name": one entry per measure name that is worked
    out differently in different reports; those are the ones to check, because the same label on two
    reports then means two different numbers. Old versions of a report (the Versions groups) are left out,
    so a measure in a report and its earlier copies counts once."""
    old = {r[0] for r in cat.conn.execute(
        "SELECT m.asset_key FROM group_members m JOIN item_groups g ON g.uid = m.group_uid "
        "WHERE g.type = 'versions' AND m.asset_key != g.main_key")}
    reports = {r["key"]: r["name"] for r in cat.conn.execute(
        "SELECT key, name FROM assets WHERE kind = 'report' AND status = 'active'") if r["key"] not in old}
    by_dax: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for r in cat.conn.execute("SELECT key, name, parent, attrs FROM assets WHERE kind = 'measure' AND status = 'active'"):
        if r["parent"] not in reports:
            continue
        a = json.loads(r["attrs"] or "{}")
        expr = as_text(a.get("expression")).strip()
        if not expr:
            continue
        row = {"key": r["key"], "measure": r["name"], "name": (as_text(a.get("name")) or r["name"]).strip(),
               "expression": expr,
               "report": reports[r["parent"]], "report_key": r["parent"], "dax": _dax_key(expr)}
        by_dax.setdefault(row["dax"], []).append(row)
        by_name.setdefault(row["name"].lower(), []).append(row)
    same_dax = []
    for rows in by_dax.values():
        if len({x["report_key"] for x in rows}) < 2:
            continue
        names = sorted({x["name"] for x in rows}, key=str.lower)
        same_dax.append({"expression": rows[0]["expression"], "names": names, "reports": len({x["report_key"] for x in rows}),
                         "different_names": len(names) > 1, "measures": rows})
    same_name = []
    for rows in by_name.values():
        versions = {}
        for x in rows:
            versions.setdefault(x["dax"], []).append(x)
        if len(versions) < 2:
            continue
        same_name.append({"name": rows[0]["name"], "versions": len(versions),
                          "reports": len({x["report_key"] for x in rows}),
                          "variants": [{"expression": v[0]["expression"], "reports": sorted({x["report"] for x in v}),
                                        "measures": v} for v in versions.values()]})
    same_dax.sort(key=lambda e: (-e["different_names"], -e["reports"], e["names"][0].lower()))
    same_name.sort(key=lambda e: (-e["versions"], e["name"].lower()))
    return {"same_dax": same_dax, "same_name": same_name}
