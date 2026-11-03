"""Which SQL Server tables get profiled, and how often: once, weekly, monthly or never.

A rule only puts a table in the profiling queue when it is due (after a scan, or from the Profiling page). The
queue still waits for your approval before anything is sent, at the same controlled pace as before. Data files
are profiled each time a scan reads them, so rules are for tables and views.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import Catalogue, now

RULES = {"once": "Once", "weekly": "Weekly", "monthly": "Monthly", "never": "Never"}
EVERY_DAYS = {"weekly": 7, "monthly": 30}


def set_rule(cat: Catalogue, keys: Iterable[str], rule: str | None) -> int:
    """Give tables a rule (None removes it). Returns how many were set."""
    if rule is not None and rule not in RULES:
        raise ValueError(rule)
    keys = list(keys)
    with cat.conn:
        if rule is None:
            cat.conn.executemany("DELETE FROM profile_rules WHERE asset_key = ?", [(k,) for k in keys])
        else:
            cat.conn.executemany("INSERT INTO profile_rules (asset_key, rule, set_at) VALUES (?, ?, ?) "
                                 "ON CONFLICT(asset_key) DO UPDATE SET rule = excluded.rule, set_at = excluded.set_at",
                                 [(k, rule, now()) for k in keys])
    cat.changed()
    return len(keys)


def rules(cat: Catalogue) -> dict[str, str]:
    return {r[0]: r[1] for r in cat.conn.execute("SELECT asset_key, rule FROM profile_rules")}


def _last_profiled(cat: Catalogue) -> dict[str, str]:
    return {r[0]: r[1] for r in cat.conn.execute("SELECT asset_key, MAX(profiled_at) FROM profiles GROUP BY asset_key")}


def due(cat: Catalogue, at: datetime | None = None) -> list[str]:
    """Tables whose rule says they should be profiled now and that aren't already waiting in the queue."""
    at = at or datetime.now(timezone.utc)
    last = _last_profiled(cat)
    waiting = {r[0] for r in cat.conn.execute("SELECT asset_key FROM profile_queue WHERE state IN ('queued', "
                                              "'approved')")}
    active = {r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE status = 'active' AND kind IN ('table', "
                                             "'view')")}
    out = []
    for r in cat.conn.execute("SELECT asset_key, rule, last_requested FROM profile_rules"):
        key, rule, asked = r
        if key in waiting or key not in active or rule == "never":
            continue
        seen = max(filter(None, (last.get(key), asked)), default=None)
        if rule == "once":
            if not last.get(key) and not asked:
                out.append(key)
            continue
        if seen is None or _parse(seen) <= at - timedelta(days=EVERY_DAYS[rule]):
            out.append(key)
    return sorted(out)


def _parse(ts: str) -> datetime:
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def request_due(cat: Catalogue) -> int:
    """After a scan: put tables that are due in the queue (waiting for your approval)."""
    from .pacing import request_profiles
    keys = due(cat)
    if not keys:
        return 0
    n = request_profiles(cat, keys)
    with cat.conn:
        cat.conn.executemany("UPDATE profile_rules SET last_requested = ? WHERE asset_key = ?",
                             [(now(), k) for k in keys])
    return n


def profiling_list(cat: Catalogue) -> list[dict[str, Any]]:
    """Every scanned table and view: rows, its rule, when it was last profiled, how many profiles are kept, its
    place in the queue, and whether your code, SQL or reports use it."""
    from .pacing import used_tables
    last = _last_profiled(cat)
    rule = rules(cat)
    queue = {r[0]: r[1] for r in cat.conn.execute("SELECT asset_key, state FROM profile_queue")}
    kept = {r[0]: r[1] for r in cat.conn.execute("SELECT asset_key, COUNT(DISTINCT at) FROM profile_history "
                                                 "GROUP BY asset_key")}
    used = set(used_tables(cat))
    out = []
    for r in cat.conn.execute("SELECT key, kind, name, source, attrs FROM assets WHERE status = 'active' AND kind IN "
                              "('table', 'view') ORDER BY source, name"):
        a = json.loads(r["attrs"] or "{}")
        out.append({"key": r["key"], "kind": r["kind"], "name": r["name"], "source": r["source"],
                    "database": a.get("database", ""), "rows": a.get("row_count"), "rule": rule.get(r["key"], ""),
                    "last_profiled": (last.get(r["key"]) or "")[:10], "profiles_kept": kept.get(r["key"], 0),
                    "queue": queue.get(r["key"], ""), "used": r["key"] in used})
    return out
