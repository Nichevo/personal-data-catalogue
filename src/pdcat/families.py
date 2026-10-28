"""File families by grain: SOH_D, SOH_W and SOH_M are the same data (stock on hand) daily, weekly and
monthly. They are suggested as one family, "SOH", while each stays its own item; the family page
compares them side by side. Tables work the same way (dbo.Sales_Daily, dbo.Sales_Monthly).

Also here: files one script writes from a pattern (f"out/sales_{run}.csv") are grouped automatically,
even when the part that changes isn't a date the dated-series reader recognises.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from typing import Any

from . import groups, suggestions
from .store import Catalogue

GRAINS = {"d": "daily", "day": "daily", "daily": "daily", "dly": "daily",
          "w": "weekly", "wk": "weekly", "week": "weekly", "weekly": "weekly", "wkly": "weekly",
          "m": "monthly", "mth": "monthly", "mon": "monthly", "month": "monthly", "monthly": "monthly",
          "q": "quarterly", "qtr": "quarterly", "quarter": "quarterly", "quarterly": "quarterly",
          "y": "yearly", "yr": "yearly", "year": "yearly", "yearly": "yearly", "annual": "yearly",
          "h": "hourly", "hourly": "hourly"}
ORDER = ["hourly", "daily", "weekly", "monthly", "quarterly", "yearly"]
KINDS = ("data_file", "file_series", "table", "view", "workbook")


def _suffixes(cat: Catalogue) -> dict[str, str]:
    """Built-in grain suffixes plus your own (Settings: {"suffix": "grain"})."""
    return {**GRAINS, **{k.lower(): v for k, v in cat.setting("family_suffixes", {}).items()}}


def split(name: str, suffixes: dict[str, str]) -> tuple[str, str] | None:
    """('SOH', 'daily') for 'SOH_D'."""
    m = re.match(r"^(?P<base>.+?)[ _.-](?P<suffix>[A-Za-z]+)$", name)
    if not m:
        return None
    grain = suffixes.get(m["suffix"].lower())
    return (m["base"], grain) if grain else None


def find_families(cat: Catalogue) -> int:
    suffixes = _suffixes(cat)
    excluded = groups.excluded(cat, "family")
    grouped = {r[0] for r in cat.conn.execute(
        "SELECT m.asset_key FROM group_members m JOIN item_groups g ON g.uid = m.group_uid WHERE g.type = 'family'")}
    by_base: dict[tuple, list[tuple[str, str, str]]] = defaultdict(list)
    for r in cat.conn.execute(f"SELECT key, kind, name, source, attrs FROM assets WHERE status = 'active' AND "
                              f"kind IN ({', '.join('?' * len(KINDS))})", KINDS):
        if r["key"] in excluded:
            continue
        a = json.loads(r["attrs"])
        if a.get("in_series"):
            continue
        if r["kind"] in ("table", "view"):
            where, leaf, ext = f"{r['source']}/{a.get('database', '')}/{a.get('schema', '')}", a.get("object", r["name"]), ""
        else:
            folder, _, leaf = r["name"].replace("\\", "/").rpartition("/")
            leaf, _, ext = leaf.rpartition(".") if "." in leaf else (leaf, "", "")
            where = f"{r['source']}/{folder}"
        got = split(leaf, suffixes)
        if got:
            base, grain = got
            by_base[(where.lower(), base.lower(), ext.lower(), r["kind"] in ("table", "view"))].append(
                (r["key"], grain, base))
    found = []
    for (where, base, ext, is_table), members in by_base.items():
        grains = {g for _, g, _ in members}
        if len(members) < 2 or len(grains) < 2 or all(k in grouped for k, _, _ in members):
            continue
        name = members[0][2]
        members.sort(key=lambda m: ORDER.index(m[1]) if m[1] in ORDER else 99)
        found.append({
            "id": f"family:{where}/{base}.{ext}", "subject": members[0][0],
            "title": f"**{name}** family: " + ", ".join(sorted(grains, key=lambda g: ORDER.index(g) if g in ORDER else 99)),
            "detail": f"{len(members)} {'tables' if is_table else 'files'} with the same name and a grain suffix",
            "score": len(members),
            "payload": {"type": "family", "name": name, "main_key": None,
                        "members": {k: g for k, g, _ in members}}})
    return suggestions.sync(cat, "family", found)


def grain_check(cat: Catalogue, key: str) -> str:
    """How far apart the dates inside are (once profiled): 'daily', 'weekly', 'monthly'..."""
    for p in cat.profile(key):
        t = (p["data_type"] or "").lower()
        if not ("date" in t or "time" in t) or not p["min_value"] or not p["max_value"] or not p["distinct_count"]:
            continue
        try:
            lo, hi = date.fromisoformat(p["min_value"][:10]), date.fromisoformat(p["max_value"][:10])
        except ValueError:
            continue
        n = p["distinct_count"]
        if n < 2:
            continue
        gap = (hi - lo).days / (n - 1)
        for limit, label in ((0.5, "hourly"), (1.6, "daily"), (8.5, "weekly"), (35, "monthly"), (100, "quarterly"),
                             (400, "yearly")):
            if gap <= limit:
                return f"{label} ({p['column_name']} every {gap:.1f} days)"
        return f"every {gap:.0f} days"
    return ""


def group_script_outputs(cat: Catalogue) -> int:
    """Files a script writes from one pattern become one group that links back to the script."""
    files = []
    for r in cat.conn.execute("SELECT key, attrs FROM assets WHERE kind = 'data_file' AND status = 'active'"):
        a = json.loads(r["attrs"])
        if not a.get("in_series") and a.get("path"):
            files.append((r["key"], a["path"].replace("\\", "/").lower(), a.get("mtime") or 0))
    made = 0
    keep = set()
    for r in cat.conn.execute("SELECT key, name, attrs FROM assets WHERE kind IN ('script', 'notebook') AND "
                              "status = 'active' AND attrs LIKE '%write_patterns%'"):
        for pattern in json.loads(r["attrs"]).get("write_patterns", []):
            pat = pattern.replace("\\", "/").lower().lstrip("./")
            depth = pat.count("/") + 1
            rx = re.compile(fnmatch.translate(pat.replace("{}", "*")).replace(".*", "[^/]*"))
            hits = [f for f in files if rx.fullmatch("/".join(f[1].split("/")[-depth:]))]
            if len(hits) < 2:
                continue
            uid = "out-" + hashlib.sha1(f"{r['key']}|{pat}".encode()).hexdigest()[:10]
            keep.add(uid)
            newest = max(hits, key=lambda f: f[2])[0]
            groups.create(cat, "outputs", pattern.rsplit("/", 1)[-1], {k: None for k, _, _ in hits}, main_key=newest,
                          attrs={"pattern": pattern, "script": r["key"]}, uid=uid)
            made += 1
    with cat.conn:  # patterns that no longer match anything
        for g in groups.all_groups(cat, "outputs"):
            if g["uid"] not in keep:
                cat.conn.execute("DELETE FROM group_members WHERE group_uid = ?", (g["uid"],))
                cat.conn.execute("DELETE FROM item_groups WHERE uid = ?", (g["uid"],))
    return made


def family_rows(cat: Catalogue) -> dict[str, dict[str, Any]]:
    """asset key -> {"uid", "name", "type"} for items in a family or a script's output group."""
    out = {}
    for r in cat.conn.execute("SELECT m.asset_key, g.uid, g.name, g.type FROM group_members m JOIN item_groups g "
                              "ON g.uid = m.group_uid WHERE g.type IN ('family', 'outputs')"):
        out[r[0]] = {"uid": r[1], "name": r[2], "type": r[3]}
    return out
