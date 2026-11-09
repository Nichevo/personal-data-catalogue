"""Which SQL Server tables get profiled, and how often: once, weekly, monthly or never.

A rule only puts a table in the profiling queue when it is due (after a scan, or from the Profiling page). The
queue waits for your approval before anything is sent, at the same controlled pace as before, unless you've said
in the settings that a rule is your approval. Weekly rules can be pinned to a day of the week (e.g. every Monday);
otherwise they come round 7 days after the last profile. Data files are profiled each time a scan reads them, so
rules are for tables and views.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import Catalogue, now

RULES = {"once": "Once", "weekly": "Weekly", "monthly": "Monthly", "never": "Never"}
EVERY_DAYS = {"weekly": 7, "monthly": 30}
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKLY_DAY = "profile_weekly_day"        # setting: None (every 7 days) or 0-6 (Monday-Sunday, local time)
RULE_APPROVES = "profile_rules_approve"  # setting: a rule's tables go into the queue already approved


def weekly_day(cat: Catalogue) -> int | None:
    day = cat.setting(WEEKLY_DAY)
    return day if isinstance(day, int) and 0 <= day <= 6 else None


def rule_approves(cat: Catalogue) -> bool:
    return bool(cat.setting(RULE_APPROVES, False))


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


def next_due(rule: str, seen: str | None, day: int | None = None) -> datetime | None:
    """When a table with this rule is next due, given when it was last profiled or asked for (`seen`). None means
    never again. A weekly rule pinned to a day is due from the start of that day (local time) in the week after
    `seen`, and stays due until it is queued, so a Monday without a scan is caught up by the next scan."""
    if rule == "never":
        return None
    if seen is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if rule == "once":
        return None
    last = _parse(seen)
    if rule == "weekly" and day is not None:
        local = _local(last)
        ahead = (day - local.weekday()) % 7 or 7
        start = (local + timedelta(days=ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(timezone.utc)
    return last + timedelta(days=EVERY_DAYS[rule])


def _seen(cat: Catalogue) -> dict[str, tuple[str, str | None]]:
    """{table: (rule, latest of last profile and last request)} for every table with a rule."""
    last = _last_profiled(cat)
    out = {}
    for key, rule, asked in cat.conn.execute("SELECT asset_key, rule, last_requested FROM profile_rules"):
        out[key] = (rule, max(filter(None, (last.get(key), asked)), default=None))
    return out


def due(cat: Catalogue, at: datetime | None = None) -> list[str]:
    """Tables whose rule says they should be profiled now and that aren't already waiting in the queue."""
    at = at or datetime.now(timezone.utc)
    day = weekly_day(cat)
    waiting = {r[0] for r in cat.conn.execute("SELECT asset_key FROM profile_queue WHERE state IN ('queued', "
                                              "'approved')")}
    active = {r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE status = 'active' AND kind IN ('table', "
                                             "'view')")}
    out = []
    for key, (rule, seen) in _seen(cat).items():
        if key in waiting or key not in active:
            continue
        when = next_due(rule, seen, day)
        if when is not None and when <= at:
            out.append(key)
    return sorted(out)


def _local(t: datetime) -> datetime:
    """This computer's time zone (the day a weekly rule is pinned to is a local day)."""
    return t.astimezone()


def _parse(ts: str) -> datetime:
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def request_due(cat: Catalogue) -> int:
    """After a scan: put tables that are due in the queue (waiting for your approval, or already approved when
    the setting says a rule is your approval)."""
    from .pacing import request_profiles
    keys = due(cat)
    if not keys:
        return 0
    n = request_profiles(cat, keys, approve=rule_approves(cat))
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
    seen = _seen(cat)
    day = weekly_day(cat)
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
                    "next_due": _due_label(seen.get(r["key"]), day, queue.get(r["key"])),
                    "queue": queue.get(r["key"], ""), "used": r["key"] in used})
    return out


def _due_label(rule_seen: tuple[str, str | None] | None, day: int | None, queued: str | None) -> str:
    """'' (no rule), 'now', 'in the queue', 'never' or a date such as 'Mon 16 Nov'."""
    if not rule_seen:
        return ""
    if queued in ("queued", "approved"):
        return "in the queue"
    when = next_due(rule_seen[0], rule_seen[1], day)
    if when is None:
        return "never" if rule_seen[0] == "never" else "done"
    if when <= datetime.now(timezone.utc):
        return "now"
    return _local(when).strftime("%a %d %b")
