"""Script versions: scripts in the same folder whose names differ only by a version or a date at the
end (forecast_v2.py, forecast_v3.py, forecast_20261001.py, forecast_old.py, "forecast - Copy.py")
are suggested as versions of one script. Power BI reports and Excel workbooks get the same treatment,
plus a match on their content when the names don't say (reportversions.py).

The current one is the one other code imports or runs, or the one a schedule runs, or else the
newest. When that isn't clear the suggestion leaves it for you to tick. Once accepted, only the
current version shows on the code map, in search and in lineage.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime

from . import groups, suggestions
from .store import Catalogue

SUFFIXES = [
    re.compile(r"[ _-]*-?\s*copy(\s*\(\d+\))?$", re.I),                       # "forecast - Copy", "x copy (2)"
    re.compile(r"\s*\(\d+\)$"),                                                 # "forecast (2)"
    re.compile(r"[ _.-]*v(er(sion)?)?[ _.-]?\d+([._]\d+)*[a-z]?$", re.I),       # _v2, -v1.3, version2
    re.compile(r"[ _.-]*(19|20)\d{2}[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])$"),  # 20261001, 2026-10-01
    re.compile(r"[ _.-]*(0[1-9]|[12]\d|3[01])[-_.](0[1-9]|1[0-2])[-_.](19|20)\d{2}$"),     # 01-10-2026
    re.compile(r"[ _.-]*(19|20)\d{2}[-_.]?(0[1-9]|1[0-2])$"),                   # 202610
    re.compile(r"[ _.-]+(old|new|final|latest|backup|bak|orig(inal)?|draft|test|wip|prev(ious)?|working|temp|"
               r"fixed|updated|clean|archive)$", re.I),
    re.compile(r"[ _-]\d{1,2}$"),                                               # forecast_2 (only with siblings)
]
WEAK = SUFFIXES[-1]


def base_name(stem: str) -> tuple[str, str]:
    """('forecast', 'v3') for 'forecast_v3'; strips several suffixes ('forecast_v2_old')."""
    label_parts = []
    current = stem
    for _ in range(4):
        for rx in SUFFIXES:
            m = rx.search(current)
            if m and m.start() > 0:
                label_parts.insert(0, current[m.start():].strip(" _-.()"))
                current = current[:m.start()]
                break
        else:
            break
    return current.rstrip(" _-."), " ".join(p for p in label_parts if p)


def _buckets(cat: Catalogue, skip: set[str]) -> dict[tuple, list[dict]]:
    """Scripts in one folder whose names are the same apart from a version or date at the end, keyed by
    (parent, folder, base name, extension). Buckets too thin to call versions are left out."""
    rows = [dict(r) for r in cat.conn.execute(
        "SELECT key, name, source, parent, kind, attrs FROM assets WHERE kind IN ('script', 'notebook', 'sql_script') "
        "AND status = 'active'")]
    by_base: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["key"] in skip:
            continue
        folder, _, file = r["name"].replace("\\", "/").rpartition("/")
        stem, dot, ext = file.rpartition(".")
        base, label = base_name(stem or file)
        r["label"] = label or "plain"
        r["weak"] = bool(label) and bool(WEAK.fullmatch(stem[len(base):] or "x"))
        by_base[(r["parent"], folder.lower(), base.lower(), ext.lower())].append(r)
    out = {}
    for k, members in by_base.items():
        if len(members) < 2:
            continue
        if all(m["weak"] or m["label"] == "plain" for m in members) and sum(m["weak"] for m in members) < 2:
            continue  # "load_1.py" next to "load.py" alone is too thin to call versions
        out[k] = members
    return out


def _group_name(members: list[dict], base: str, ext: str) -> str:
    return f"{members[0]['name'].rsplit('/', 1)[0] + '/' if '/' in members[0]['name'] else ''}{base}.{ext}"


def script_groups(cat: Catalogue, skip: set[str] = frozenset()) -> list[dict]:
    """Script versions grouped automatically (version 2.4.1), the same shape as reportgroups.find_groups.
    The current one is the one other code uses, or a schedule runs, or else the newest. When two or more are
    used by other code or run on a schedule, they may all be live: those stay a suggestion for you to decide."""
    used, scheduled = _used_by_others(cat), _scheduled(cat)
    out = []
    for (_, _, base, ext), members in _buckets(cat, skip).items():
        main, why = _pick_main(members, used, scheduled)
        if main is None:
            if sum(m["key"] in used for m in members) > 1 or sum(m["key"] in scheduled for m in members) > 1:
                continue
            best = max(members, key=lambda m: (json.loads(m["attrs"] or "{}").get("mtime") or 0,
                                               m["label"] == "plain", m["name"]))
            main, why = best["key"], "newest by date and name"
        out.append({"members": {m["key"]: m["label"] for m in sorted(members, key=lambda m: m["name"].lower())},
                    "main_key": main, "why": why, "kind": members[0]["kind"],
                    "name": _short(main, members),
                    "matched_by": "names that differ only by a version or date at the end"})
    return out


def find_versions(cat: Catalogue) -> int:
    excluded = groups.excluded(cat, "versions")
    in_group = {r[0] for r in cat.conn.execute(
        "SELECT m.asset_key FROM group_members m JOIN item_groups g ON g.uid = m.group_uid WHERE g.type = 'versions'")}
    used = _used_by_others(cat)
    scheduled = _scheduled(cat)
    found = []
    for (parent, folder, base, ext), members in _buckets(cat, excluded).items():
        if all(m["key"] in in_group for m in members):
            continue
        main, why = _pick_main(members, used, scheduled)
        name = _group_name(members, base, ext)
        detail = ", ".join(sorted(m["name"].rsplit("/", 1)[-1] for m in members))
        found.append({
            "id": f"versions:{parent}:{folder}/{base}.{ext}", "subject": main or members[0]["key"],
            "title": f"{len(members)} versions of **{name}**" + (f": current looks like **{_short(main, members)}** ({why})"
                                                                  if main else ": tick the current one"),
            "detail": detail, "score": len(members) + (2 if main else 0),
            "payload": {"type": "versions", "name": name, "main_key": main,
                        "members": {m["key"]: m["label"] for m in members}}})
    from .reportversions import find_report_versions
    found += find_report_versions(cat, in_group, excluded)
    return suggestions.sync(cat, "versions", found)


def _short(key: str, members: list[dict]) -> str:
    return next(m["name"].rsplit("/", 1)[-1] for m in members if m["key"] == key)


def _used_by_others(cat: Catalogue) -> set[str]:
    return {r[0] for r in cat.conn.execute(
        "SELECT DISTINCT dst_key FROM links WHERE relation IN ('imports', 'runs') AND dst_key IS NOT NULL")}


def _scheduled(cat: Catalogue) -> set[str]:
    return {r[0] for r in cat.conn.execute("SELECT DISTINCT asset_key FROM schedule_links WHERE state = 'confirmed'")}


def _pick_main(members: list[dict], used: set[str], scheduled: set[str]) -> tuple[str | None, str]:
    for pool, why in (([m for m in members if m["key"] in used], "other code uses it"),
                      ([m for m in members if m["key"] in scheduled], "a schedule runs it")):
        if len(pool) == 1:
            return pool[0]["key"], why
        if len(pool) > 1:
            return None, ""
    dated = []
    for m in members:
        a = json.loads(m["attrs"])
        dated.append((a.get("mtime") or 0, m["key"]))
    dated.sort(reverse=True)
    if dated[0][0] and (len(dated) == 1 or dated[0][0] - dated[1][0] > 60):
        when = datetime.fromtimestamp(dated[0][0]).strftime("%d %b %Y")
        return dated[0][1], f"newest, changed {when}"
    plain = [m for m in members if m["label"] == "plain"]
    if len(plain) == 1:
        return plain[0]["key"], "the name without a version"
    return None, ""


