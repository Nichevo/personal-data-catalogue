"""Every change in one list: what scans found, report changes, row counts, your edits, git commits, changes your
change trackers recorded on the columns you watch, and profiles that moved a lot.

`feed` gathers them for a time window; `apply_filters` narrows them (what, where, business project, tag, report
group, gold standards, followed items, text, and "hide noise"); `group` counts them by one or two fields.
Saved views are filter sets kept in Settings under a name.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .store import Catalogue, split_tags

CATEGORIES = {
    "scan": "Scan changes (new, dropped, columns)",
    "report": "Report and workbook changes",
    "rows": "Row counts",
    "tracked": "Tracked changes (watched columns)",
    "profile": "Profile moved",
    "edit": "Your edits",
    "commit": "Git commits",
}
SCAN_CHANGES = ("new item", "came back", "dropped", "column added", "column removed", "column retyped")
NOISE_SHARE = 0.05       # row-count moves smaller than this are noise
GROUP_FIELDS = {"day": "Day", "week": "Week", "month": "Month", "category": "What", "change": "Change",
                "kind": "Kind of item", "source": "Where", "item": "Item", "project": "Business project",
                "folder": "Folder", "column": "Column"}


def since_days(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def feed(cat: Catalogue, since: str, until: str | None = None, categories: set[str] | None = None
         ) -> list[dict[str, Any]]:
    """Changes since `since` (ISO time), newest first: [{"at", "item", "name", "kind", "source", "category",
    "change", "detail", "n", "column"}]."""
    want = categories or set(CATEGORIES)
    rows: list[dict[str, Any]] = []
    until = until or "9999"
    if want & {"scan", "report", "rows"}:
        for r in cat.conn.execute(
                "SELECT h.asset_key, h.at, h.change, h.detail, a.name, a.kind, a.source FROM schema_history h "
                "LEFT JOIN assets a ON a.key = h.asset_key WHERE h.at >= ? AND h.at <= ? ORDER BY h.at DESC, h.id DESC",
                (since, until)):
            cat_ = "rows" if r["change"] == "row count" else "scan" if r["change"] in SCAN_CHANGES else "report"
            if cat_ in want:
                rows.append({"at": r["at"], "item": r["asset_key"], "name": r["name"] or r["asset_key"],
                             "kind": r["kind"] or "", "source": r["source"] or "", "category": cat_,
                             "change": r["change"], "detail": r["detail"] or "", "n": 1, "column": _column_of(r)})
    if "edit" in want:
        for r in cat.conn.execute(
                "SELECT e.at, e.item, e.what, e.old_value, e.new_value, e.author, a.name, a.kind, a.source FROM edits e "
                "LEFT JOIN assets a ON a.key = CASE WHEN instr(e.item, '#') > 0 THEN substr(e.item, 1, "
                "instr(e.item, '#') - 1) ELSE e.item END WHERE e.at >= ? AND e.at <= ? AND e.undone = 0 "
                "ORDER BY e.at DESC", (since, until)):
            item, _, column = r["item"].partition("#")
            new = (r["new_value"] or "").replace("\n", " ")
            rows.append({"at": r["at"], "item": item, "name": (r["name"] or item) + (f".{column}" if column else ""),
                         "kind": r["kind"] or _pseudo_kind(item), "source": r["source"] or "", "category": "edit",
                         "change": r["what"], "detail": (new[:160] + ("…" if len(new) > 160 else "")) +
                         (f" ({r['author']})" if r["author"] else ""), "n": 1, "column": column})
    if "commit" in want:
        for r in cat.conn.execute(
                "SELECT g.asset_key, g.at, g.author, g.message, a.name, a.kind, a.source FROM git_history g JOIN "
                "assets a ON a.key = g.asset_key WHERE g.at >= ? AND g.at <= ? ORDER BY g.at DESC", (since, until)):
            rows.append({"at": r["at"], "item": r["asset_key"], "name": r["name"], "kind": r["kind"],
                         "source": r["source"], "category": "commit", "change": "commit",
                         "detail": f"{(r['message'] or '').splitlines()[0][:160] if r['message'] else ''}"
                                   f"{' (' + r['author'] + ')' if r['author'] else ''}", "n": 1, "column": ""})
    if "tracked" in want:
        rows += tracked_rows(cat, since, until)
    if "profile" in want:
        from .growth import profile_drift
        names = _names(cat)
        for d in profile_drift(cat, since):
            if d["at"] > until:
                continue
            n = names.get(d["asset_key"], {})
            rows.append({"at": d["at"], "item": d["asset_key"], "name": n.get("name", d["asset_key"]),
                         "kind": n.get("kind", ""), "source": n.get("source", ""), "category": "profile",
                         "change": "profile moved", "detail": d["detail"], "n": 1, "column": d["column"]})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


def _column_of(r) -> str:
    if (r["change"] or "").startswith("column ") and r["detail"]:
        return r["detail"].split(" ")[0]
    return ""


def _pseudo_kind(item: str) -> str:
    return {"bp": "bproject", "kb": "knowledge", "q": "question", "metric": "metric"}.get(item.split("://")[0], "")


def _names(cat: Catalogue) -> dict[str, dict[str, str]]:
    return {r[0]: {"name": r[1], "kind": r[2], "source": r[3]}
            for r in cat.conn.execute("SELECT key, name, kind, source FROM assets")}


def tracked_rows(cat: Catalogue, since: str, until: str = "9999", watched_only: bool = True
                 ) -> list[dict[str, Any]]:
    """One row per tracker, tracked table, day and column: the changes a tracker recorded, put on the table it
    tracks (a confirmed link, else a likely one) or on the tracker itself."""
    from .trackers import all_watched
    watch = all_watched(cat) if watched_only else None
    links: dict[tuple[str, str], str] = {}
    for r in cat.conn.execute("SELECT tracker_key, table_key, state, band, name_value FROM trackers WHERE state != "
                              "'rejected' ORDER BY CASE state WHEN 'confirmed' THEN 1 ELSE 0 END"):
        if r["state"] == "confirmed" or r["band"] == "likely":
            links[(r["tracker_key"], (r["name_value"] or "").lower())] = r["table_key"]
    single = defaultdict(set)
    for (tk, _), table in links.items():
        single[tk].add(table)
    names = _names(cat)
    labels = {r[0]: (json.loads(r[1] or "{}").get("labels") or {}) for r in cat.conn.execute(
        "SELECT tracker_key, shape FROM tracker_info WHERE not_tracker = 0")}
    per: dict[tuple, Counter] = defaultdict(Counter)
    for r in cat.conn.execute("SELECT tracker_key, table_name, day, column_name, kind, n FROM tracker_counts WHERE "
                              "day != '' AND day >= ? AND day <= ?", (since[:10], until[:10])):
        if r[0] not in labels:
            continue
        if watch is not None and r[3] not in watch.get(r[0], set()):
            continue
        per[(r[0], r[1], r[2], r[3])][r[4]] += r[5]
    out = []
    for (tk, table, day, column), kinds in per.items():
        target = links.get((tk, table.lower())) or (next(iter(single[tk])) if len(single[tk]) == 1 else None)
        item = target or tk
        n = names.get(item, {})
        tname = names.get(tk, {}).get("name", tk)
        label = labels.get(tk, {}).get(column) or column
        bits = [f"{kinds[k]:,} {k}" for k in ("changed", "new", "removed") if kinds.get(k)]
        out.append({"at": f"{day}T23:59:59+00:00", "item": item, "name": n.get("name", item),
                    "kind": n.get("kind", ""), "source": n.get("source", ""), "category": "tracked",
                    "change": f"{label} changed" if label != "(rows)" else "rows changed",
                    "detail": ", ".join(bits) + f" (from {tname})", "n": sum(kinds.values()), "column": column,
                    "tracker": tk})
    return out


# ---- filters ------------------------------------------------------------------------------------------

def apply_filters(cat: Catalogue, rows: list[dict[str, Any]], f: dict[str, Any]) -> list[dict[str, Any]]:
    """f: {"categories": [...], "sources": [...], "kinds": [...], "project": uid, "tag": text, "group": uid,
    "gold": bool, "followed": bool, "text": str, "hide_noise": bool}"""
    out = rows
    if f.get("categories"):
        cats = set(f["categories"])
        out = [r for r in out if r["category"] in cats]
    if f.get("sources"):
        src = set(f["sources"])
        out = [r for r in out if r["source"] in src]
    if f.get("kinds"):
        kinds = set(f["kinds"])
        out = [r for r in out if r["kind"] in kinds]
    keys: set[str] | None = None
    if f.get("project"):
        keys = project_keys(cat, f["project"])
    if f.get("tag"):
        keys = _and(keys, tag_keys(cat, f["tag"]))
    if f.get("group"):
        keys = _and(keys, {r[0] for r in cat.conn.execute("SELECT asset_key FROM group_members WHERE group_uid = ?",
                                                          (f["group"],))})
    if f.get("gold"):
        keys = _and(keys, gold_related(cat))
    if f.get("followed"):
        keys = _and(keys, {r[0] for r in cat.conn.execute("SELECT item FROM follow_ups")})
    if keys is not None:
        out = [r for r in out if r["item"] in keys or _parent_in(cat, r["item"], keys)]
    if f.get("text"):
        t = f["text"].lower()
        out = [r for r in out if t in (r["name"] or "").lower() or t in (r["detail"] or "").lower()
               or t in (r["change"] or "").lower()]
    if f.get("hide_noise", True):
        noise = noise_keys(cat)
        out = [r for r in out if r["item"] not in noise and not _small_row_move(r)]
    return out


def _and(a: set[str] | None, b: set[str]) -> set[str]:
    return b if a is None else a & b


_PARENTS: dict[int, dict[str, str]] = {}


def _parent_in(cat: Catalogue, key: str, keys: set[str]) -> bool:
    """Measures, Power BI tables and the like count as part of their report."""
    rev = cat.revision()
    parents = _PARENTS.get(rev)
    if parents is None:
        _PARENTS.clear()
        parents = _PARENTS[rev] = {r[0]: r[1] for r in cat.conn.execute(
            "SELECT key, parent FROM assets WHERE parent IS NOT NULL")}
    return parents.get(key) in keys


def project_keys(cat: Catalogue, uid: str) -> set[str]:
    return {r[0] for r in cat.conn.execute("SELECT asset_key FROM bproject_members WHERE project_uid = ? AND "
                                           "state = 'confirmed'", (uid,))}


def tag_keys(cat: Catalogue, tag: str) -> set[str]:
    t = tag.strip().lstrip("#").lower()
    out = set()
    for r in cat.conn.execute("SELECT asset_key, tags FROM notes WHERE COALESCE(tags, '') != ''"):
        if t in {x.lower() for x in split_tags(r[1])}:
            out.add(r[0].split("#")[0])
    return out


def tags(cat: Catalogue) -> list[str]:
    seen: set[str] = set()
    for r in cat.conn.execute("SELECT tags FROM notes WHERE COALESCE(tags, '') != ''"):
        seen |= {x.strip() for x in split_tags(r[0]) if x.strip()}
    return sorted(seen, key=str.lower)


def gold_related(cat: Catalogue) -> set[str]:
    """Gold standards and the items you marked as differing from one on purpose."""
    keys = {r[0] for r in cat.conn.execute("SELECT key FROM gold_standards")}
    keys |= {r[0] for r in cat.conn.execute("SELECT item_key FROM gold_ok")}
    return keys


def noise_keys(cat: Catalogue) -> set[str]:
    """Items whose changes are noise: marked not relevant or old, and older versions in a version group."""
    from .curation import hidden_keys
    keys = hidden_keys(cat, old=True)
    keys |= {r[0] for r in cat.conn.execute(
        "SELECT m.asset_key FROM group_members m JOIN item_groups g ON g.uid = m.group_uid WHERE g.type = 'versions' "
        "AND g.main_key IS NOT NULL AND m.asset_key != g.main_key")}
    return keys


def _small_row_move(r: dict[str, Any]) -> bool:
    if r["category"] != "rows":
        return False
    try:
        a, b = (int(x.replace(",", "").strip()) for x in r["detail"].split("→"))
    except ValueError:
        return False
    return a > 0 and abs(b - a) / a < NOISE_SHARE


# ---- grouping -----------------------------------------------------------------------------------------

def group_value(cat: Catalogue, r: dict[str, Any], field: str, projects: dict[str, str] | None = None) -> str:
    day = r["at"][:10]
    if field == "day":
        return day
    if field == "week":
        d = date.fromisoformat(day)
        monday = d - timedelta(days=d.weekday())
        return f"week of {monday.isoformat()}"
    if field == "month":
        return day[:7]
    if field == "category":
        return CATEGORIES.get(r["category"], r["category"])
    if field == "item":
        return r["name"]
    if field == "project":
        return (projects or {}).get(r["item"], "(none)")
    if field == "folder":
        from .pathbits import joined, split_name
        _, folder = split_name(r["name"], r["kind"])
        return joined(r["source"], folder) or r["source"] or "(none)"
    if field == "column":
        return r.get("column") or "(none)"
    return str(r.get(field) or "(none)")


def project_names(cat: Catalogue) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in cat.conn.execute("SELECT m.asset_key, p.name FROM bproject_members m JOIN bprojects p ON p.uid = "
                              "m.project_uid WHERE m.state = 'confirmed' ORDER BY p.name"):
        out[r[0]] = f"{out[r[0]]}, {r[1]}" if r[0] in out else r[1]
    return out


def group(cat: Catalogue, rows: list[dict[str, Any]], by: list[str]) -> list[dict[str, Any]]:
    """Counts by one or two fields: [{field: value, ..., "changes", "items", "latest"}], biggest first (by date
    when grouped by time)."""
    projects = project_names(cat) if "project" in by else None
    acc: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        k = tuple(group_value(cat, r, f, projects) for f in by)
        d = acc.setdefault(k, {"changes": 0, "items": set(), "latest": r["at"]})
        d["changes"] += r.get("n", 1)
        d["items"].add(r["item"])
        d["latest"] = max(d["latest"], r["at"])
    out = [{**{GROUP_FIELDS[f]: v for f, v in zip(by, k)}, "changes": d["changes"], "items": len(d["items"]),
            "latest": d["latest"][:16].replace("T", " ")} for k, d in acc.items()]
    if by and by[0] in ("day", "week", "month"):
        out.sort(key=lambda d: (d[GROUP_FIELDS[by[0]]], -d["changes"]), reverse=True)
    else:
        out.sort(key=lambda d: -d["changes"])
    return out


# ---- saved views and "since your last visit" ---------------------------------------------------------

def saved_views(cat: Catalogue) -> dict[str, dict[str, Any]]:
    got = cat.setting("change_views", {})
    return got if isinstance(got, dict) else {}


def save_view(cat: Catalogue, name: str, f: dict[str, Any]) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Give the view a name.")
    views = saved_views(cat)
    views[name] = f
    cat.set_setting("change_views", views, quiet=True)


def delete_view(cat: Catalogue, name: str) -> None:
    views = saved_views(cat)
    views.pop(name, None)
    cat.set_setting("change_views", views, quiet=True)


def since_last_visit(cat: Catalogue) -> tuple[int, str | None]:
    """(changes since you last opened Change tracking, when that was), noise left out."""
    last = cat.setting("changes_seen_at")
    if not last:
        return 0, None
    rows = apply_filters(cat, feed(cat, last, categories={"scan", "report", "tracked", "profile"}),
                         {"hide_noise": True})
    return sum(r.get("n", 1) for r in rows), last


def mark_seen(cat: Catalogue) -> None:
    cat.set_setting("changes_seen_at", datetime.now(timezone.utc).isoformat(timespec="seconds"), quiet=True)


# ---- report changes over time ------------------------------------------------------------------------

def report_parts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report changes per week and part: [{"week", "part", "changes"}]."""
    acc: Counter = Counter()
    for r in rows:
        if r["category"] != "report":
            continue
        d = date.fromisoformat(r["at"][:10])
        week = (d - timedelta(days=d.weekday())).isoformat()
        acc[(week, r["change"].split(" ")[0])] += 1
    return [{"week": w, "part": p, "changes": n} for (w, p), n in sorted(acc.items())]


def volume(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Changes per week by what changed: [{"week", "what", "changes"}]."""
    acc: Counter = Counter()
    for r in rows:
        d = date.fromisoformat(r["at"][:10])
        week = (d - timedelta(days=d.weekday())).isoformat()
        acc[(week, CATEGORIES.get(r["category"], r["category"]))] += r.get("n", 1)
    return [{"week": w, "what": c, "changes": n} for (w, c), n in sorted(acc.items())]
