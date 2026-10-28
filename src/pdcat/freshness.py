"""Freshness of outputs, and items nobody has touched in a long time.

Every scan notes each output file's modified time (a file some script writes). Files that keep being
rewritten earn a "refreshing" badge and drop out of your way; ones that stop go on the To review
list, asking for a refresh or a note on whether they're still needed. Scripts and projects that
haven't changed or run in a long time are flagged the same way, so you can mark them old (hidden
from the code map and diagrams, notes kept).
"""
from __future__ import annotations

import json
import time
from statistics import median
from typing import Any

from .store import Catalogue

KEEP = 12              # modified times remembered per file
OLD_DAYS = 365         # scripts untouched this long are asked about
MIN_STOP_DAYS = 14


def _log(cat: Catalogue) -> dict[str, list[float]]:
    return cat.setting("freshness_log", {})


def outputs(cat: Catalogue) -> dict[str, float]:
    """Output files (written by some script) -> their modified time now."""
    out = {}
    for r in cat.conn.execute(
            "SELECT DISTINCT a.key, a.attrs FROM assets a JOIN links l ON l.dst_key = a.key AND l.relation = 'writes' "
            "WHERE a.status = 'active' AND a.kind IN ('data_file', 'file_series', 'workbook', 'report', 'model')"):
        a = json.loads(r["attrs"])
        m = a.get("mtime") or _series_mtime(a)
        if m:
            out[r["key"]] = float(m)
    return out


def _series_mtime(a: dict[str, Any]) -> float | None:
    files = a.get("files") or []
    times = [f.get("mtime") for f in files if f.get("mtime")]
    return max(times) if times else None


def track(cat: Catalogue) -> int:
    """Remember this scan's modified times."""
    log = _log(cat)
    current = outputs(cat)
    for key, m in current.items():
        seen = log.get(key, [])
        if not seen or abs(seen[-1] - m) > 1:
            log[key] = (seen + [m])[-KEEP:]
    log = {k: v for k, v in log.items() if k in current}
    cat.set_setting("freshness_log", log, quiet=True)
    return len(current)


def status(cat: Catalogue, key: str, log: dict | None = None, now: float | None = None) -> dict[str, Any] | None:
    """{"state": "refreshing" | "stopped" | "once", "every_days", "age_days"} for an output file."""
    times = sorted((log if log is not None else _log(cat)).get(key, []))
    if not times:
        return None
    now = now or time.time()
    age = (now - times[-1]) / 86400
    if len(times) < 2:
        return {"state": "once", "every_days": None, "age_days": round(age)}
    gaps = [(b - a) / 86400 for a, b in zip(times, times[1:])]
    every = median(gaps)
    limit = max(MIN_STOP_DAYS, 2.5 * every)
    return {"state": "refreshing" if age <= limit else "stopped", "every_days": round(every, 1),
            "age_days": round(age), "limit_days": round(limit)}


def badge(cat: Catalogue, key: str) -> str:
    s = status(cat, key)
    if not s:
        return ""
    if s["state"] == "refreshing":
        return f"🔄 refreshing about every {s['every_days']:g} days"
    if s["state"] == "stopped":
        return f"⏸️ stopped refreshing {s['age_days']} days ago"
    return ""


def review_tasks(cat: Catalogue, task) -> list:
    from .curation import hidden_keys
    from .folderroles import archived
    from .schedules import scheduled_keys
    out = []
    log = _log(cat)
    skip = hidden_keys(cat, old=True) | archived(cat)
    for key in log:
        if key in skip:
            continue
        s = status(cat, key, log)
        if s and s["state"] == "stopped":
            name = (cat.get_asset(key) or {}).get("name", key)
            out.append(task("not_refreshing", key, f"{name} stopped refreshing",
                            f"It was rewritten about every {s['every_days']:g} days but not for {s['age_days']} days. "
                            "Does the script still run? Refresh it, or note whether it's still needed, or mark it old.",
                            version=str(int(max(log[key])))))
    now = time.time()
    scheduled = scheduled_keys(cat)
    from .gitinfo import last_change
    git = last_change(cat)
    stale: dict[str, list[str]] = {}
    for r in cat.conn.execute("SELECT key, name, parent, attrs FROM assets WHERE status = 'active' AND kind IN "
                              "('script', 'notebook')"):
        if r["key"] in skip or r["key"] in scheduled or (r["parent"] and r["parent"] in skip):
            continue
        a = json.loads(r["attrs"])
        m = a.get("mtime")
        if not m:
            continue
        last = git.get(r["key"], {}).get("at")
        if (now - m) / 86400 >= OLD_DAYS and not (last and last >= _iso(now - OLD_DAYS * 86400)):
            stale.setdefault(r["parent"] or r["key"], []).append(r["name"])
    # One task per project, so a folder of old scripts is one question, not dozens.
    for parent, names in stale.items():
        total = cat.conn.execute("SELECT COUNT(*) FROM assets WHERE parent = ? AND status = 'active' AND kind IN "
                                 "('script', 'notebook')", (parent,)).fetchone()[0] or len(names)
        label = (cat.get_asset(parent) or {}).get("name", parent)
        what = "None of its scripts have" if len(names) == total else f"{len(names)} of its {total} scripts haven't"
        out.append(task("old_item", parent, f"{label}: {what} changed in a year",
                        ", ".join(sorted(names)[:8]) + (" …" if len(names) > 8 else "")
                        + ". Still used? Mark them (or the whole project) old to hide them from the code map; "
                          "their notes stay.", version=str(len(names))))
    return out


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()
