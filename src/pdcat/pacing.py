"""A slow, controlled way to send queries to SQL Server.

Every query the catalogue sends goes through a Pacer, which:

  * sends one query at a time and waits a few seconds between them
  * gives each query a time limit, and by default stops the whole run on the
    first time-out (a sign the server is busy)
  * never runs during your quiet hours (e.g. the morning load window)
  * stops after a set number of tables per run; the rest wait for next time
  * can be stopped from the app; a later run picks up where it left off
  * writes every query to a log you can show a DBA

Big tables are profiled from a sample (TOP N rows) instead of a full scan.
All the numbers are on the Settings page. Profiling only happens for tables
you asked for and approved: "Profile this table" or "Profile tables my code
uses" add them to a queue, and nothing is sent until the queue is approved and
run. `pdcat scan --plan` and `pdcat profile --plan` show exactly which queries
would run, without connecting.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

from .store import Catalogue, now

DEFAULTS: dict[str, Any] = {
    "sql_pause_seconds": 3,
    "sql_timeout_seconds": 60,
    "sql_sample_over_rows": 500_000,
    "sql_sample_rows": 100_000,
    "sql_top_values_rows": 10_000,
    "sql_max_tables_per_run": 25,
    "sql_quiet_hours": "",
    "sql_stop_on_timeout": True,
    "sql_scan_definitions": True,
}
LABELS = {
    "sql_pause_seconds": "Pause between queries (seconds)",
    "sql_timeout_seconds": "Time limit per query (seconds)",
    "sql_sample_over_rows": "Sample tables bigger than (rows)",
    "sql_sample_rows": "Sample size for big tables (rows)",
    "sql_top_values_rows": "Rows read for common values",
    "sql_max_tables_per_run": "Most tables profiled per run",
    "sql_quiet_hours": "Quiet hours (no queries), e.g. 07:00-09:30, 16:00-17:00",
    "sql_stop_on_timeout": "Stop the run on the first time-out",
    "sql_scan_definitions": "Read view and procedure definitions when scanning",
}


class PacingStopped(RuntimeError):
    """The run stopped early on purpose: a time-out, quiet hours, the per-run cap, or you pressed Stop."""


def settings(cat: Catalogue) -> dict[str, Any]:
    return {k: cat.setting(k, v) for k, v in DEFAULTS.items()}


def in_quiet_hours(spec: str, at: datetime | None = None) -> bool:
    at = at or datetime.now()
    minutes = at.hour * 60 + at.minute
    for part in (spec or "").split(","):
        part = part.strip()
        if "-" not in part:
            continue
        a, b = (p.strip() for p in part.split("-", 1))
        try:
            start = int(a.split(":")[0]) * 60 + int((a.split(":") + ["0"])[1])
            end = int(b.split(":")[0]) * 60 + int((b.split(":") + ["0"])[1])
        except ValueError:
            continue
        if start <= end and start <= minutes < end or start > end and (minutes >= start or minutes < end):
            return True
    return False


@dataclass
class Pacer:
    cat: Catalogue
    source: str = ""
    sleep: Callable[[float], None] | None = None
    clock: Callable[[], datetime] = datetime.now

    def __post_init__(self) -> None:
        self.s = settings(self.cat)
        self._last = 0.0
        self.sent = 0

    def check(self) -> None:
        if self.cat.setting("sql_stop_requested", False):
            raise PacingStopped("Stopped from the app. Run again to carry on where it left off.")
        if in_quiet_hours(self.s["sql_quiet_hours"], self.clock()):
            raise PacingStopped(f"Quiet hours ({self.s['sql_quiet_hours']}): no queries are sent now.")

    def query(self, cursor, sql: str, *, purpose: str, database: str = "") -> tuple[list[str], list[tuple]]:
        """Run one query at the controlled pace and log it. Returns (column names, rows)."""
        self.check()
        wait = float(self.s["sql_pause_seconds"]) - (time.monotonic() - self._last)
        if self.sent and wait > 0:
            (self.sleep or time.sleep)(wait)
        try:
            cursor.connection.timeout = int(self.s["sql_timeout_seconds"])
        except Exception:
            pass
        started = time.monotonic()
        status, error, rows, names = "ok", None, [], []
        try:
            cursor.execute(sql)
            names = [d[0] for d in cursor.description] if cursor.description else []
            rows = [tuple(r) for r in cursor.fetchall()] if cursor.description else []
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            status = "timeout" if _is_timeout(e) else "error"
            raise
        finally:
            ms = int((time.monotonic() - started) * 1000)
            self._last = time.monotonic()
            self.sent += 1
            log_query(self.cat, self.source, database, purpose, sql, len(rows), ms, status, error)
            if status == "timeout" and self.s["sql_stop_on_timeout"]:
                self.cat.set_setting("sql_last_stop", f"Time-out after {ms / 1000:.0f}s on: {purpose}", quiet=True)
        return names, rows


def _is_timeout(e: Exception) -> bool:
    text = str(e).lower()
    return "timeout" in text or "hyt00" in text or "timed out" in text


def log_query(cat: Catalogue, source: str, database: str, purpose: str, query: str, rows: int, ms: int,
              status: str, error: str | None = None) -> None:
    with cat.conn:
        cat.conn.execute("INSERT INTO query_log (at, source, database, purpose, query, rows, ms, status, error) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (now(), source, database, purpose, query, rows, ms, status, error))


def query_log(cat: Catalogue, limit: int = 500) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute("SELECT * FROM query_log ORDER BY id DESC LIMIT ?", (limit,))]


# ---- the profiling queue -------------------------------------------------------

def request_profiles(cat: Catalogue, keys: Iterable[str], *, approve: bool = False) -> int:
    """Ask for tables to be profiled. Nothing is sent to the server until the queue is approved and run."""
    n = 0
    with cat.conn:
        for k in keys:
            cur = cat.conn.execute(
                "INSERT INTO profile_queue (asset_key, state, requested_at) VALUES (?, ?, ?) "
                "ON CONFLICT(asset_key) DO UPDATE SET state = excluded.state, requested_at = excluded.requested_at, "
                "error = NULL WHERE profile_queue.state NOT IN ('queued', 'approved')",
                (k, "approved" if approve else "queued", now()))
            n += cur.rowcount
    cat.changed()
    return n


def used_tables(cat: Catalogue) -> list[str]:
    """Tables and views your code, pasted SQL, reports or workbooks use."""
    return [r[0] for r in cat.conn.execute(
        """SELECT DISTINCT l.dst_key FROM links l JOIN assets a ON a.key = l.dst_key
           JOIN assets s ON s.key = l.src_key
           WHERE a.kind IN ('table', 'view') AND a.status = 'active'
             AND s.kind NOT IN ('table', 'view', 'procedure') ORDER BY l.dst_key""")]


def queue(cat: Catalogue, states: tuple[str, ...] = ("queued", "approved", "failed")) -> list[dict[str, Any]]:
    marks = ", ".join("?" * len(states))
    rows = cat.conn.execute(
        f"""SELECT q.*, a.name, a.source, a.attrs FROM profile_queue q LEFT JOIN assets a ON a.key = q.asset_key
            WHERE q.state IN ({marks}) ORDER BY q.state, a.name""", states).fetchall()
    import json
    out = []
    for r in rows:
        d = dict(r)
        attrs = json.loads(d.pop("attrs") or "{}")
        d["row_count"] = attrs.get("row_count")
        d["database"] = attrs.get("database")
        out.append(d)
    return out


def queue_state(cat: Catalogue, key: str) -> str | None:
    row = cat.conn.execute("SELECT state FROM profile_queue WHERE asset_key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_queue_state(cat: Catalogue, keys: Iterable[str], state: str, error: str | None = None) -> None:
    if state not in ("queued", "approved", "done", "failed", "skipped"):
        raise ValueError(state)
    with cat.conn:
        for k in keys:
            cat.conn.execute("UPDATE profile_queue SET state = ?, error = ?, finished_at = CASE WHEN ? IN "
                             "('done', 'failed', 'skipped') THEN ? ELSE finished_at END WHERE asset_key = ?",
                             (state, error, state, now(), k))
    cat.changed()


def plan_profile(cat: Catalogue, keys: list[str]) -> list[dict[str, Any]]:
    """The queries profiling these tables would send, without connecting."""
    from .profiling import sql_profile_queries
    s = settings(cat)
    out = []
    for key in keys:
        a = cat.get_asset(key)
        if not a or a["kind"] not in ("table", "view"):
            continue
        attrs = a["attrs"]
        rows = attrs.get("row_count") or 0
        sample = int(s["sql_sample_rows"]) if a["kind"] == "view" or rows > int(s["sql_sample_over_rows"]) else None
        agg, top = sql_profile_queries(attrs["schema"], attrs["object"], [dict(c) for c in cat.columns(key)],
                                       int(s["sql_top_values_rows"]), sample_for_aggregates=sample)
        how = f"sample of {sample:,} rows" if sample else "whole table"
        out += [{"table": key, "database": attrs.get("database"), "purpose": f"counts and ranges ({how})", "query": agg},
                {"table": key, "database": attrs.get("database"), "purpose": "common values", "query": top}]
    return out
