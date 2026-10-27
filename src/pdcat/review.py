"""The review list: things in the catalogue that need your eyes, most important first.

Tasks are worked out from what is already catalogued (no extra queries against
your servers) and refreshed after every scan. Each has a stable id, so marking
one done, snoozing or dismissing it sticks across refreshes. A task whose
problem has gone away is closed automatically, and reopens if it comes back.

Priority leans towards what matters on a big database: things your own code
uses, big tables, and tables that already have open data issues.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from .store import Catalogue, now

# rule: (label, category, base priority)
RULES: dict[str, tuple[str, str, int]] = {
    "missing": ("Disappeared", "change", 35),
    "drift": ("File differs from its source", "warning", 35),
    "odd_values": ("Odd values", "warning", 30),
    "undocumented": ("Needs a description", "docs", 25),
    "new": ("New since last scan", "change", 25),
    "changed": ("Changed since last scan", "change", 20),
    "stale": ("File may be out of date", "warning", 15),
    "columns": ("Columns need descriptions", "docs", 12),
    "unmatched": ("Table in code not found", "warning", 10),
    "data_type": ("Data type doesn't fit the values", "warning", 18),
    "stale_note": ("Note may be out of date", "docs", 22),
    "sensitive": ("Possibly sensitive values", "warning", 20),
    "stale_data": ("Data looks out of date", "warning", 24),
    "layout_change": ("File layout changed", "change", 28),
}
# Checks where "dismiss" means "it's like this on purpose".
BY_DESIGN = {"data_type", "sensitive", "stale_data"}
CATEGORY_LABELS = {"docs": "Document", "change": "Check a change", "warning": "Possible data problem"}
QUANTITY_NAME = re.compile(r"(qty|quantity|units?|amount|price|cost|revenue|sales|count|weight|volume)", re.I)
STALE_DAYS = 90


@dataclass
class Task:
    id: str
    rule: str
    asset_key: str
    title: str
    detail: str
    priority: float


def refresh_tasks(cat: Catalogue) -> dict[str, int]:
    """Recompute the review list and merge it with your existing statuses."""
    found = {t.id: t for t in _candidates(cat)}
    ts = now()
    existing = {r["id"]: dict(r) for r in cat.conn.execute("SELECT * FROM review_tasks")}
    opened = closed = 0
    with cat.conn:
        for t in found.values():
            old = existing.get(t.id)
            if old is None:
                cat.conn.execute(
                    "INSERT INTO review_tasks (id, rule, asset_key, title, detail, priority, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                    (t.id, t.rule, t.asset_key, t.title, t.detail, t.priority, ts, ts))
                opened += 1
            else:
                status = "open" if old["status"] == "resolved" else old["status"]  # problem is back
                cat.conn.execute(
                    "UPDATE review_tasks SET title = ?, detail = ?, priority = ?, status = ?, "
                    "updated_at = CASE WHEN status = ? THEN updated_at ELSE ? END WHERE id = ?",
                    (t.title, t.detail, t.priority, status, status, ts, t.id))
        for task_id, old in existing.items():
            if task_id not in found and old["status"] in ("open", "snoozed"):
                cat.conn.execute("UPDATE review_tasks SET status = 'resolved', updated_at = ? WHERE id = ?", (ts, task_id))
                closed += 1
    return {"open": len(open_tasks(cat, limit=100_000)), "new": opened, "closed": closed}


def open_tasks(cat: Catalogue, *, rule: str | None = None, category: str | None = None,
               only_used: bool = False, limit: int = 200, asset_key: str | None = None) -> list[dict[str, Any]]:
    """Open tasks (including snoozed ones whose snooze has ended), highest priority first."""
    ts = now()
    rows = cat.conn.execute(
        f"""SELECT t.*, a.name AS asset_name, a.kind AS asset_kind, a.source AS source,
                  (SELECT COUNT(DISTINCT l.src_key) FROM links l WHERE l.dst_key = t.asset_key) AS used_by
           FROM review_tasks t LEFT JOIN assets a ON a.key = t.asset_key
           WHERE (t.status = 'open' OR (t.status = 'snoozed' AND t.snooze_until <= ?))
           {"AND t.asset_key = ?" if asset_key else ""}
           ORDER BY t.priority DESC, t.title""",
        (ts, asset_key) if asset_key else (ts,)).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        r["category"] = RULES.get(r["rule"], ("", "warning", 0))[1]
        if rule and r["rule"] != rule or category and r["category"] != category:
            continue
        if only_used and not r["used_by"]:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def set_task_status(cat: Catalogue, task_id: str, status: str, snooze_days: int = 30) -> None:
    if status not in ("open", "done", "snoozed", "dismissed"):
        raise ValueError("status must be open, done, snoozed or dismissed")
    until = (datetime.now(timezone.utc) + timedelta(days=snooze_days)).isoformat(timespec="seconds") \
        if status == "snoozed" else None
    with cat.conn:
        cat.conn.execute("UPDATE review_tasks SET status = ?, snooze_until = ?, updated_at = ? WHERE id = ?",
                         (status, until, now(), task_id))


def set_tasks_status(cat: Catalogue, task_ids: list[str], status: str, snooze_days: int = 30) -> None:
    for tid in task_ids:
        set_task_status(cat, tid, status, snooze_days)


def count_open(cat: Catalogue) -> int:
    """How many tasks are open, without loading them (for the sidebar)."""
    return cat.conn.execute(
        "SELECT COUNT(*) FROM review_tasks WHERE status = 'open' OR (status = 'snoozed' AND snooze_until <= ?)",
        (now(),)).fetchone()[0]


def ensure_fresh(cat: Catalogue) -> None:
    """Recompute the review list only when the catalogue changed since the last time."""
    rev = cat.revision()
    if cat.setting("tasks_revision") != rev:
        refresh_tasks(cat)
        cat.set_setting("tasks_revision", rev, quiet=True)


def task_counts(cat: Catalogue) -> dict[str, int]:
    return dict(Counter(t["category"] for t in open_tasks(cat, limit=100_000)))


# ---- hotspots ----------------------------------------------------------------

def area_of(asset: dict[str, Any]) -> str:
    """The 'area' an item belongs to: a schema for SQL objects, a top-level folder for files and scripts."""
    attrs = asset["attrs"] if isinstance(asset["attrs"], dict) else json.loads(asset["attrs"] or "{}")
    if asset["key"].startswith("sql://"):
        return f"{asset['source']} / {attrs.get('database', '?')} / {attrs.get('schema', '?')}"
    name = asset["name"]
    top = name.split("/", 1)[0] if "/" in name else "(top level)"
    return f"{asset['source']} / {top}"


def hotspots(cat: Catalogue) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score each area and item by open data issues, automatic warnings, missing docs and disappearances.

    Returns (areas, items), both sorted worst first.
    """
    issues: Counter = Counter()
    for n in cat.conn.execute("SELECT asset_key FROM notes WHERE note_type = 'issue' AND status = 'open'"):
        issues[n[0].split("#", 1)[0]] += 1
    warnings: Counter = Counter()
    docs: Counter = Counter()
    for t in open_tasks(cat, limit=100_000):
        if t["category"] == "warning":
            warnings[t["asset_key"]] += 1
        elif t["category"] == "docs":
            docs[t["asset_key"]] += 1

    items, areas = [], defaultdict(lambda: Counter())
    for a in cat.conn.execute(
            "SELECT key, kind, name, source, description, attrs, status FROM assets "
            "WHERE kind IN ('table', 'view', 'data_file', 'script', 'notebook', 'sql_script')"):
        a = dict(a)
        a["attrs"] = json.loads(a["attrs"])
        key = a["key"]
        missing = int(a["status"] == "missing")
        undocumented = int(not a["description"] and docs[key] > 0)
        score = 5 * issues[key] + 3 * warnings[key] + 2 * missing + undocumented
        area = area_of(a)
        areas[area]["items"] += 1
        areas[area]["open issues"] += issues[key]
        areas[area]["warnings"] += warnings[key]
        areas[area]["undocumented"] += undocumented
        areas[area]["missing"] += missing
        areas[area]["score"] += score
        if score:
            items.append({"key": key, "kind": a["kind"], "name": a["name"], "area": area, "score": score,
                          "open issues": issues[key], "warnings": warnings[key], "missing": bool(missing),
                          "undocumented": bool(undocumented)})
    area_df = pd.DataFrame([{"area": k, **v} for k, v in areas.items()])
    if len(area_df):
        area_df["% undocumented"] = (100 * area_df["undocumented"] / area_df["items"]).round(0).astype(int)
        area_df = area_df[["area", "score", "open issues", "warnings", "missing", "undocumented", "% undocumented", "items"]]
        area_df = area_df.sort_values(["score", "open issues", "items"], ascending=False, ignore_index=True)
    item_df = pd.DataFrame(items)
    if len(item_df):
        item_df = item_df.sort_values(["score", "open issues"], ascending=False, ignore_index=True)
    return area_df, item_df


# ---- rules -------------------------------------------------------------------

def _candidates(cat: Catalogue) -> list[Task]:
    conn = cat.conn
    assets = {r["key"]: {**dict(r), "attrs": json.loads(r["attrs"])} for r in conn.execute("SELECT * FROM assets")}
    used_by: Counter = Counter(dict(conn.execute(
        "SELECT dst_key, COUNT(DISTINCT src_key) FROM links WHERE dst_key IS NOT NULL GROUP BY dst_key").fetchall()))
    notes_on: Counter = Counter()
    noted_columns: dict[str, set[str]] = defaultdict(set)
    issues: Counter = Counter()
    for n in conn.execute("SELECT asset_key, note_type, status FROM notes"):
        base, _, col = n["asset_key"].partition("#")
        notes_on[base] += 1
        if col:
            noted_columns[base].add(col)
        if n["note_type"] == "issue" and n["status"] == "open":
            issues[base] += 1
    columns: dict[str, list[dict]] = defaultdict(list)
    for c in conn.execute("SELECT asset_key, name, description FROM columns"):
        columns[c["asset_key"]].append(dict(c))
    profiles: dict[str, list[dict]] = defaultdict(list)
    for p in conn.execute("SELECT * FROM profiles ORDER BY asset_key, ordinal"):
        profiles[p["asset_key"]].append(dict(p))

    def priority(rule: str, key: str) -> float:
        a = assets.get(key, {"attrs": {}})
        rows = a["attrs"].get("row_count") or 0
        return (RULES[rule][2] + 10 * min(used_by[key], 5) + 2 * math.log10(rows + 1) + 5 * min(issues[key], 3))

    def task(rule: str, key: str, title: str, detail: str = "", version: str = "") -> Task:
        tid = f"{rule}:{key}" + (f":{version}" if version else "")
        return Task(tid, rule, key, title, detail, round(priority(rule, key), 1))

    tasks: list[Task] = []
    for key, a in assets.items():
        kind, attrs, name = a["kind"], a["attrs"], a["name"]
        if kind in ("database", "folder", "project"):
            continue

        if a["status"] == "missing":
            if notes_on[key] or used_by[key]:
                what = []
                if notes_on[key]:
                    what.append(f"{notes_on[key]} note(s)")
                if used_by[key]:
                    what.append(f"used by {used_by[key]} item(s)")
                tasks.append(task("missing", key, f"{name} wasn't found in the latest scan",
                                  "It has " + " and ".join(what) + ". Was it renamed or dropped? Update or remove the notes."))
            continue

        if kind in ("table", "view") and not a["description"] and not notes_on[key]:
            rows = attrs.get("row_count")
            extra = f"{rows:,} rows. " if rows else ""
            use = f"Used by {used_by[key]} of your files." if used_by[key] else ""
            tasks.append(task("undocumented", key, f"Describe {name}", (extra + use).strip()))

        if kind in ("table", "view", "data_file") and used_by[key] and columns[key]:
            bare = [c["name"] for c in columns[key] if not c["description"] and c["name"] not in noted_columns[key]]
            if bare and len(bare) >= max(1, len(columns[key]) // 2):
                shown = ", ".join(bare[:8]) + (" …" if len(bare) > 8 else "")
                tasks.append(task("columns", key, f"{len(bare)} of {len(columns[key])} columns in {name} have no description",
                                  shown))

        if attrs.get("added_in_rescan") and kind in ("table", "view", "procedure", "data_file", "script",
                                                                   "notebook", "sql_script"):
            tasks.append(task("new", key, f"New: {name}", f"First seen {a['first_seen'][:10]}. What is it for?"))

        if attrs.get("changed_at"):
            tasks.append(task("changed", key, f"{name} changed",
                              f"Modified {str(attrs.get('modified'))[:16]} (was {str(attrs.get('previous_modified'))[:16]}). "
                              "Check your notes still hold.", version=str(attrs.get("modified"))))

        if profiles[key]:
            odd = _odd_values(profiles[key])
            if odd:
                tasks.append(task("odd_values", key, f"Odd values in {name}", "; ".join(odd)))

        if kind == "data_file":
            ups = [u for u in cat.upstream(key) if profiles.get(u)]
            if profiles[key] and ups:
                drift = _drift(profiles[key], profiles[ups[0]])
                if drift:
                    tasks.append(task("drift", key, f"{name} differs from {assets.get(ups[0], {}).get('name', ups[0])}",
                                      "; ".join(drift)))
            stale = _stale(attrs, [assets[u] for u in cat.upstream(key) if u in assets], used_by[key])
            if stale:
                tasks.append(task("stale", key, f"{name} may be out of date", stale))

    tasks += _v2_candidates(cat, assets, profiles, used_by, task)

    for r in cat.unresolved_refs():
        ref = r["dst_ref"]
        tasks.append(Task(f"unmatched:{ref}", "unmatched", ref, f"Table {ref[6:]} (used in code) not found",
                          f"Used by {r['used_by']} file(s), e.g. {r['example']}. A server you haven't added, "
                          "a typo, or a false match?", RULES["unmatched"][2] + 5 * min(r["used_by"], 5)))
    return tasks


def _v2_candidates(cat: Catalogue, assets: dict, profiles: dict, used_by: Counter, task) -> list[Task]:
    from .curation import sensitive_suggestions
    from .history import freshness
    from .staleness import all_stale

    out: list[Task] = []
    for key, prof in profiles.items():
        a = assets.get(key)
        if not a or a["status"] != "active":
            continue
        for p in prof:
            for h in json.loads(p.get("hints") or "[]"):
                out.append(task("data_type", key, f"{p['column_name']} in {a['name']}: {h['text']}",
                                f"Declared {p['data_type'] or '?'}. Fix the type at the source, note why, or "
                                "dismiss it as by design.", version=f"{p['column_name']}:{h['code']}"))
        f = freshness(cat, key)
        if f and f["stale"] and (used_by[key] or (a["attrs"].get("row_count") or 0) > 0):
            out.append(task("stale_data", key, f"{a['name']}: newest {f['column']} is {f['latest']}",
                            f"{f['age_days']} days old when last measured ({f['measured']}); expected within "
                            f"{f['limit_days']} days. Is the load still running?", version=f["latest"]))
    for n in all_stale(cat):
        a = assets.get(n["asset_key"])
        out.append(task("stale_note", n["asset_key"], f"Check a note on {a['name'] if a else n['asset_key']}",
                        "; ".join(n["reasons"]).capitalize() + ". Confirm it, edit it or archive it.",
                        version=str(n["note_id"])))
    for c in sensitive_suggestions(cat):
        out.append(task("sensitive", c["asset_key"], f"{c['name']} in {c['item']} may hold personal data",
                        "Mark it sensitive on the item's page (values are then never kept or exported), or dismiss.",
                        version=c["name"]))
    for key, a in assets.items():
        for change in a["attrs"].get("layout_changes", []):
            out.append(task("layout_change", key, f"{a['name']}: layout changed in {change['file']}",
                            change["detail"], version=change["file"]))
    return out


def _odd_values(profile: list[dict]) -> list[str]:
    found = []
    today = datetime.now(timezone.utc).date().isoformat()
    for p in profile:
        rows, nulls, col = p["row_count"] or 0, p["null_count"], p["column_name"]
        if not rows or nulls is None:
            continue
        if nulls == rows:
            found.append(f"{col} is always empty")
            continue
        if nulls / rows >= 0.5:
            found.append(f"{col} is {nulls / rows:.0%} empty")
        if p["distinct_count"] == 1 and rows > 1:
            found.append(f"{col} has only one value ({p['min_value'] if p['min_value'] is not None else 'same everywhere'})")
        lo = _num(p["min_value"])
        if lo is not None and lo < 0 and QUANTITY_NAME.search(col):
            found.append(f"{col} has negative values (min {p['min_value']})")
        dtype = (p["data_type"] or "").lower()
        if ("date" in dtype or "time" in dtype) and p["max_value"] and str(p["max_value"])[:10] > today:
            found.append(f"{col} has future dates (max {str(p['max_value'])[:10]})")
    return found


def _drift(file_profile: list[dict], source_profile: list[dict]) -> list[str]:
    src = {p["column_name"].lower(): p for p in source_profile}
    found = []
    for p in file_profile:
        s = src.get(p["column_name"].lower())
        if not s:
            continue
        col = p["column_name"]
        flo, fhi, slo, shi = _num(p["min_value"]), _num(p["max_value"]), _num(s["min_value"]), _num(s["max_value"])
        if None not in (flo, slo) and flo < slo:
            found.append(f"{col} goes below the source (min {p['min_value']} vs {s['min_value']})")
        if None not in (fhi, shi) and fhi > shi:
            found.append(f"{col} goes above the source (max {p['max_value']} vs {s['max_value']})")
        if p["row_count"] and s["row_count"] and p["null_count"] is not None and s["null_count"] is not None:
            fn, sn = p["null_count"] / p["row_count"], s["null_count"] / s["row_count"]
            if fn - sn >= 0.2:
                found.append(f"{col} is {fn:.0%} empty vs {sn:.0%} in the source")
    if file_profile and source_profile and (file_profile[0]["row_count"] or 0) > (source_profile[0]["row_count"] or 0) > 0:
        found.append(f"more rows than the source ({file_profile[0]['row_count']:,} vs {source_profile[0]['row_count']:,})")
    return found


def _stale(attrs: dict, upstream: list[dict], used_by: int) -> str | None:
    modified = attrs.get("modified")
    if not modified:
        return None
    newer = [u["name"] for u in upstream if u["attrs"].get("changed_at", "") > modified]
    if newer:
        return f"Its source changed after it was written ({', '.join(newer[:3])}). Re-run the script that makes it?"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(modified)
    if used_by and age.days > STALE_DAYS:
        return f"Last written {age.days} days ago but still read by your code."
    return None


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
