"""What table profiles say about an ML opportunity (version 2.30).

Profiles (Add › Sources › Profiling) hold, per column, the rows, blanks, distinct values, smallest and largest
value, average and most common values. This module turns them into:

- **column checks**: mostly blank, one value only, an ID-like feature, stand-in blanks (0, 00000000, N/A),
  blank or repeating keys, negative values in a quantity or duration target, a lopsided yes or no target;
- the **Data quality** readiness signal (how many of the opportunity's tables are profiled, and how many issues
  the profiles show);
- **profile coverage**: which of its tables have no profile, an old one, or are waiting in the queue, so they can
  be queued (they still wait for your approval on the Profiling page: nothing is sent to a server from here);
- the **newest date** in a table, for freshness;
- a **suggested effort** (1 to 5) when you haven't set one.

Everything reads the catalogue only.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from .store import Catalogue

OLD_PROFILE_DAYS = 180          # a profile older than this is shown as old
STALE_DATA_DAYS = 180           # newest date this far before the profile: the table may have stopped being filled
MOSTLY_BLANK = 0.5              # a column blank in this share of rows or more is "mostly blank"
TARGET_BLANK = 0.05             # a target blank in more than this share of rows is flagged
PLACEHOLDERS = {"", "0", "00000000", "0000-00-00", "1900-01-01", "9999-12-31", "99991231", "#", "-", "n/a", "na",
                "none", "null", "unknown", "?", "not assigned", "#n/a"}
SQL_KINDS = ("table", "view")


def _ratio(a: Any, b: Any) -> float | None:
    try:
        a, b = float(a or 0), float(b or 0)
    except (TypeError, ValueError):
        return None
    return a / b if b > 0 else None


def blank_share(p: dict[str, Any] | None) -> float | None:
    return _ratio((p or {}).get("null_count"), (p or {}).get("row_count")) if p else None


def top_values(p: dict[str, Any] | None) -> list[tuple[str, int]]:
    try:
        raw = json.loads((p or {}).get("top_values") or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for v in raw:
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            try:
                out.append((str(v[0]), int(v[1])))
            except (TypeError, ValueError):
                continue
    return out


def placeholder_share(p: dict[str, Any] | None) -> tuple[str, float] | None:
    """The biggest stand-in blank among the most common values (0, 00000000, N/A...), with its share of the
    non-blank rows."""
    if not p:
        return None
    filled = (p.get("row_count") or 0) - (p.get("null_count") or 0)
    if filled <= 0:
        return None
    best = None
    for v, n in top_values(p):
        if v.strip().lower() in PLACEHOLDERS:
            share = n / filled
            if best is None or share > best[1]:
                best = (v if v.strip() else "(empty text)", share)
    return best


def is_profiled_ok(p: dict[str, Any] | None) -> bool:
    """A profile exists and the column holds real values (not mostly blank, not one value only)."""
    if not p or not p.get("row_count"):
        return False
    b = blank_share(p) or 0.0
    return b < MOSTLY_BLANK and (p.get("distinct_count") or 0) != 1


def severe(p: dict[str, Any] | None) -> str | None:
    """Why a column can't be used as it is (mostly blank or a single value), or None."""
    if not p or not p.get("row_count"):
        return None
    b = blank_share(p) or 0.0
    if b >= MOSTLY_BLANK:
        return f"blank in {b:.0%} of rows"
    if p.get("distinct_count") == 1:
        return "one value only"
    ph = placeholder_share(p)
    if ph and ph[1] >= MOSTLY_BLANK:
        return f"“{ph[0]}” in {ph[1]:.0%} of filled rows (a stand-in for blank)"
    return None


def _days_between(a: date, b: date) -> int:
    return (b - a).days


def _parse_ts(ts: Any) -> date | None:
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (t.astimezone(timezone.utc) if t.tzinfo else t).date()


def newest_date(x: Any, key: str) -> tuple[date, str, date | None] | None:
    """(newest date, column, profiled on) among a table's date columns, from its profiles. Dates past 2100 (open
    ended validity) are left out by `_span`."""
    from .mlopps import _span
    best = None
    for c in x.date_columns(key):
        p = x.profiles.get((key, c))
        s = _span(p)
        if s and (best is None or s[1] > best[0]):
            best = (s[1], c, _parse_ts(p.get("profiled_at")))
    return best


# ---- column checks -------------------------------------------------------------------------------------------

def column_issues(x: Any) -> list[dict[str, str]]:
    """Problems the profiles show in the opportunity's feature, key and date columns, and its tables.
    [{"level": "warn" | "info", "item", "text"}]."""
    out: list[dict[str, str]] = []
    for i in x.ordered(x.columns):
        role = i.get("role") or ""
        if role not in ("feature", "key", "date"):
            continue
        k, c = i["item"].split("#", 1)
        p = x.profiles.get((k, c))
        if not p or not p.get("row_count"):
            continue
        label = x.column_label(i["item"])
        rows = p.get("row_count") or 0
        b = blank_share(p) or 0.0
        distinct = p.get("distinct_count") or 0
        bad = severe(p)
        if bad:
            out.append({"level": "warn", "item": i["item"], "text": f"{label} is {bad}: little to learn from"})
        elif role == "key" and b > 0.01:
            out.append({"level": "warn", "item": i["item"], "text": f"{label} is a key but is blank in {b:.0%} of "
                                                                   "rows; those rows won't join"})
        elif role == "feature" and rows >= 1000 and distinct >= 0.95 * (rows - (p.get("null_count") or 0)) \
                and not _numeric_type(x, k, c, p):
            out.append({"level": "warn", "item": i["item"], "text": f"{label} has a different value on almost every "
                                                                   f"row ({distinct:,} of {rows:,}): an ID, not a "
                                                                   "feature. Leave it out or use it to join"})
        else:
            ph = placeholder_share(p)
            if ph and ph[1] >= 0.2:
                out.append({"level": "info", "item": i["item"], "text": f"{label}: “{ph[0]}” in {ph[1]:.0%} of "
                                                                       "filled rows, probably a stand-in for blank"})
    for t in x.tables:
        k = t["item"]
        rows = _table_rows_from_profile(x, k)
        if rows == 0:
            out.append({"level": "warn", "item": k, "text": f"{x.name(k)} was empty when it was profiled"})
    out.extend(_grain_issues(x))
    return out


def _numeric_type(x: Any, key: str, column: str, p: dict[str, Any]) -> bool:
    t = (x.coltypes.get((key, column)) or p.get("data_type") or "").lower()
    return any(w in t for w in ("int", "decimal", "numeric", "float", "real", "money", "double"))


def _table_rows_from_profile(x: Any, key: str) -> int | None:
    rows = [p.get("row_count") for (k, _c), p in x.profiles.items() if k == key and p.get("row_count") is not None]
    return max(rows) if rows else None


def _grain_issues(x: Any) -> list[dict[str, str]]:
    """A single key column that should identify a row but repeats: the grain is finer than it looks."""
    keys = [i for i in x.columns if i.get("role") == "key"]
    by_table: dict[str, list[str]] = {}
    for i in keys:
        k, c = i["item"].split("#", 1)
        by_table.setdefault(k, []).append(c)
    out = []
    target = x.target_table()
    for k, cols in by_table.items():
        if k != target or len(cols) != 1:
            continue
        p = x.profiles.get((k, cols[0]))
        if not p or not p.get("row_count") or not p.get("distinct_count"):
            continue
        rows, distinct = p["row_count"], p["distinct_count"]
        if distinct < rows * 0.98 and rows >= 100:
            out.append({"level": "info", "item": f"{k}#{cols[0]}",
                        "text": f"{x.column_label(k + '#' + cols[0])} repeats ({distinct:,} values in {rows:,} rows, "
                                f"about {rows / distinct:.1f} rows each): a row is identified by more than this key"})
    return out


def target_checks(x: Any, tool: str) -> list[dict[str, str]]:
    """Profile checks on the target column beyond blanks: a lopsided yes or no, negative durations or quantities,
    a number with only a handful of values, stand-in blanks."""
    out = []
    for i in x.by_role("target")[:2]:
        k, c = i["item"].split("#", 1)
        p = x.profiles.get((k, c))
        if not p or not p.get("row_count"):
            continue
        label = x.column_label(i["item"])
        distinct = p.get("distinct_count") or 0
        tops = top_values(p)
        filled = (p.get("row_count") or 0) - (p.get("null_count") or 0)
        if tool in ("classification", "scoring") and tops and filled > 0 and distinct and distinct <= 10:
            rare = min(n for _v, n in tops) / filled
            if rare < 0.02:
                out.append({"level": "warn", "text": f"{label}: the rarest outcome is {rare:.1%} of rows. A model "
                                                     "needs enough of each; weigh the classes or collect more"})
        if tool in ("regression", "forecast"):
            try:
                lo = float(p.get("min_value"))
                if lo < 0:
                    out.append({"level": "warn", "text": f"{label} has negative values (smallest {lo:,.0f}); "
                                                         "returns, reversals or data errors to separate first"})
            except (TypeError, ValueError):
                pass
            if 1 < distinct <= 3:
                out.append({"level": "info", "text": f"{label} has only {distinct} values: it may be a yes or no "
                                                     "rather than a number to predict"})
        ph = placeholder_share(p)
        if ph and ph[1] >= 0.05:
            out.append({"level": "warn", "text": f"{label}: “{ph[0]}” in {ph[1]:.0%} of filled rows, probably "
                                                 "meaning not known yet; treat as blank"})
    return out


def same_blanks_as_target(x: Any) -> list[str]:
    """Feature columns in the target table blank in nearly the same share of rows as the target: they may be filled
    in at the same moment as the outcome, which leaks the answer."""
    out = []
    targets = x.by_role("target")[:1]
    if not targets:
        return out
    tk, tc = targets[0]["item"].split("#", 1)
    tb = blank_share(x.profiles.get((tk, tc)))
    if tb is None or tb < TARGET_BLANK:
        return out
    for i in x.columns:
        if i.get("role") != "feature":
            continue
        k, c = i["item"].split("#", 1)
        if k != tk or c == tc:
            continue
        b = blank_share(x.profiles.get((k, c)))
        if b is not None and abs(b - tb) <= 0.01:
            out.append(f"{x.column_label(i['item'])} is blank in {b:.0%} of rows, the same as the target "
                       f"({tb:.0%}): it may be filled in when the outcome is known. Check before using it")
    return out


# ---- coverage --------------------------------------------------------------------------------------------------

def coverage(x: Any) -> list[dict[str, Any]]:
    """For each table of the opportunity (not rejected): kind, rows, when it was profiled, how many of its picked
    columns are profiled, and its place in the profiling queue."""
    today = datetime.now(timezone.utc).date()
    marks = ", ".join("?" * len(x.keys)) or "''"
    queue = {r[0]: r[1] for r in x.cat.conn.execute(
        f"SELECT asset_key, state FROM profile_queue WHERE asset_key IN ({marks})", x.keys)}
    roles = {t["item"]: t.get("role") or "" for t in x.tables}
    out = []
    for k in x.keys:
        a = x.assets.get(k) or {}
        if not a:
            continue
        stamps = [p.get("profiled_at") for (kk, _c), p in x.profiles.items() if kk == k and p.get("profiled_at")]
        last = _parse_ts(max(stamps)) if stamps else None
        picked = [i["item"].split("#", 1)[1] for i in x.columns if i["item"].startswith(k + "#")]
        done = sum(1 for c in picked if (k, c) in x.profiles)
        q = queue.get(k) or ""
        if last:
            state = "old" if _days_between(last, today) > OLD_PROFILE_DAYS else "profiled"
        elif q in ("queued", "approved"):
            state = q
        elif q == "failed":
            state = "failed"
        else:
            state = "none"
        try:
            attrs = json.loads(a.get("attrs") or "{}")
        except (TypeError, ValueError):
            attrs = {}
        rows = attrs.get("row_count")
        if rows is None:
            rows = _table_rows_from_profile(x, k)
        out.append({"key": k, "name": x.name(k), "kind": a.get("kind") or "", "role": roles.get(k, "column"),
                    "rows": rows, "profiled": last.isoformat() if last else "", "state": state, "queue": q,
                    "columns": f"{done} of {len(picked)}" if picked else "",
                    "can_queue": a.get("kind") in SQL_KINDS and state in ("none", "old", "failed")})
    order = {"none": 0, "failed": 1, "old": 2, "queued": 3, "approved": 4, "profiled": 5}
    return sorted(out, key=lambda r: (order.get(r["state"], 9), r["name"].lower()))


STATE_LABELS = {"none": "not profiled", "failed": "profiling failed", "old": "old profile", "queued":
                "waiting for approval", "approved": "approved, runs next", "profiled": "profiled"}


def quality_signal(x: Any, cov: list[dict[str, Any]], issues: list[dict[str, str]],
                   target_warns: int) -> tuple[str, str]:
    """(state, evidence) for the Data quality signal."""
    if x.recipe.get("kind") == "agent":
        return "ready", "Not scored for an AI agent: it reads current data as it is"
    if not cov:
        return "missing", "No tables yet"
    usable = [r for r in cov if r["role"] in ("target", "feature", "column")] or cov
    profiled = [r for r in usable if r["state"] in ("profiled", "old")]
    warns = sum(1 for i in issues if i["level"] == "warn") + target_warns
    if not profiled:
        return "missing", (f"None of its {len(usable)} table(s) has a profile, so blanks, stand-ins and odd values "
                           "are unknown. Queue them under Data quality below")
    head = f"{len(profiled)} of {len(usable)} table(s) profiled"
    if len(profiled) < len(usable):
        return "partial", head + (f"; {warns} issue(s) so far" if warns else "") + ". Profile the rest to be sure"
    if warns >= 3 or target_warns:
        return "partial", head + f"; {warns} issue(s) to sort out first (see Data quality)"
    if warns:
        return "ready", head + f"; {warns} small issue(s) (see Data quality)"
    return "ready", head + "; no big issues found"


def suggested_effort(e: dict[str, Any]) -> tuple[int, list[str]]:
    """A rough effort (1 to 5) from what is still missing and the data issues found, with the reasons."""
    sig = {s["id"]: s["state"] for s in e["signals"]}
    reasons = []
    effort = 1.0
    for sid, words in (("target", "no clear target"), ("history", "not enough history"),
                       ("features", "few tables that join"), ("grain", "grain unclear")):
        if sig.get(sid) == "missing":
            effort += 1
            reasons.append(words)
        elif sig.get(sid) == "partial":
            effort += 0.5
    warns = sum(1 for i in e.get("issues", []) if i["level"] == "warn") + \
        sum(1 for q in e.get("quality", []) if q["level"] == "warn")
    if warns:
        effort += min(1.5, warns * 0.5)
        reasons.append(f"{warns} data issue(s)")
    unprofiled = sum(1 for r in e.get("coverage", []) if r["state"] in ("none", "failed"))
    if unprofiled:
        effort += 0.5
        reasons.append(f"{unprofiled} table(s) not profiled")
    if len(e.get("tables", [])) >= 5:
        effort += 0.5
        reasons.append(f"{len(e['tables'])} tables to prepare")
    return max(1, min(5, round(effort))), reasons


def tables_to_profile(cat: Catalogue) -> list[dict[str, Any]]:
    """SQL tables and views that opportunities (not parked) need and that have no profile, an old one, or failed:
    one row per table with the opportunities that need it, best opportunity first."""
    from .mlopps import _Ctx, list_opps, title
    by: dict[str, dict[str, Any]] = {}
    for o in list_opps(cat):
        if o.get("status") == "parked":
            continue
        x = _Ctx(cat, o)
        for r in coverage(x):
            if not r["can_queue"]:
                continue
            row = by.setdefault(r["key"], {**r, "opportunities": []})
            row["opportunities"].append(title(o))
            if r["role"] == "target":
                row["role"] = "target"
    rows = list(by.values())
    for r in rows:
        r["needed_by"] = len(r["opportunities"])
    return sorted(rows, key=lambda r: (r["role"] != "target", -r["needed_by"], r["name"].lower()))



# ---- deeper checks ---------------------------------------------------------------------------------------------

MAX_DEEPER = 8


def _sql_name(x: Any, key: str) -> tuple[str, str] | None:
    """([schema].[object], database) for a SQL table or view, or None for files."""
    from .profiling import _q
    a = x.assets.get(key) or {}
    if a.get("kind") not in SQL_KINDS:
        return None
    try:
        attrs = json.loads(a.get("attrs") or "{}")
    except (TypeError, ValueError):
        return None
    if not attrs.get("object"):
        return None
    return f"{_q(attrs.get('schema') or 'dbo')}.{_q(attrs['object'])}", attrs.get("database") or ""


def _header(db: str, what: str) -> str:
    return (f"-- {what}\n-- Read-only. pdcat did not run it" + (f"; run it in database {db}" if db else "") + ".\n")


def deeper_checks(x: Any) -> list[dict[str, Any]]:
    """Key facts the normal profile can't give, each with what it would settle and how to get it: a full-table
    profile to queue (`full_profile`: the table key) or a read-only query to copy (`sql`). Nothing is run here.
    [{"title", "why", "sql", "full_profile", "key"}]"""
    from .profiling import _q
    out: list[dict[str, Any]] = []
    if x.recipe.get("kind") == "agent":
        return out
    target = x.target_table()
    targets = x.by_role("target")
    # 1. date ranges measured on a sample only: history may be longer (or shorter) than it looks
    for k in [target] + [t["item"] for t in x.tables if t["item"] != target]:
        if not k:
            continue
        sampled = [c for c in x.date_columns(k) if "sample" in str((x.profiles.get((k, c)) or {}).get("method") or
                                                                   "").lower()
                   and not str((x.profiles.get((k, c)) or {}).get("method") or "").lower().startswith("whole")]
        if not sampled:
            continue
        name = _sql_name(x, k)
        method = (x.profiles.get((k, sampled[0])) or {}).get("method") or "a sample"
        sql = None
        if name:
            cols = ", ".join(f"MIN({_q(c)}) AS [{c} first], MAX({_q(c)}) AS [{c} last]" for c in sampled[:3])
            sql = _header(name[1], f"Exact date range of {x.name(k)}") + \
                f"SELECT COUNT_BIG(*) AS [rows], {cols}\nFROM {name[0]} WITH (NOLOCK);\n"
        out.append({"title": f"Exact date range of {x.name(k)}", "key": k, "full_profile": k if name else None,
                    "why": f"Its dates were profiled from {method.split(';')[0]}, so how far back it goes is only "
                           "an estimate, and History depth rests on it. A full-table profile or this one query "
                           "settles it (MIN and MAX of a date column is quick when the column is indexed).",
                    "sql": sql})
        break
    # 2. rows per month on the target table: gaps and the start of real history
    if target:
        name = _sql_name(x, target)
        dates = [c for c in x.date_columns(target)]
        if name and dates:
            c = next((i["item"].split("#", 1)[1] for i in targets if i["item"].split("#", 1)[1] in dates), None) or \
                dates[0]
            q = _q(c)
            out.append({"title": f"Rows per month in {x.name(target)}", "key": target, "full_profile": None,
                        "why": "A profile gives the first and last date only. Months with few or no rows (a system "
                               "change, an archive cut-off) shorten the history a model can really use.",
                        "sql": _header(name[1], f"Rows per month by {c}") +
                        f"SELECT YEAR({q}) AS [year], MONTH({q}) AS [month], COUNT_BIG(*) AS [rows]\n"
                        f"FROM {name[0]} WITH (NOLOCK)\nWHERE {q} IS NOT NULL\nGROUP BY YEAR({q}), MONTH({q})\n"
                        "ORDER BY 1, 2;\n"})
    # 3. is the grain really one row per key combination?
    grain = x.recipe.get("grain") or []
    if target and len(grain) >= 2:
        name = _sql_name(x, target)
        cols = []
        for cid in grain:
            hit = next((i["item"].split("#", 1)[1] for i in x.columns if i.get("concept") == cid
                        and i["item"].startswith(target + "#")), None)
            if hit:
                cols.append(hit)
        if name and len(cols) >= 2:
            keys = ", ".join(_q(c) for c in cols)
            out.append({"title": f"Is {x.name(target)} one row per {', '.join(cols)}?", "key": target,
                        "full_profile": None,
                        "why": "Profiles count distinct values per column, not per combination, so they can't show "
                               "whether rows repeat. Repeats change what one prediction means.",
                        "sql": _header(name[1], "Key combinations that repeat") +
                        f"SELECT COUNT_BIG(*) AS [repeating combinations], SUM(n) AS [rows in them]\n"
                        f"FROM (SELECT {keys}, COUNT_BIG(*) AS n FROM {name[0]} WITH (NOLOCK)\n"
                        f"      GROUP BY {keys} HAVING COUNT_BIG(*) > 1) AS d;\n"})
    # 4. the outcome's real spread for a yes or no / scoring target
    tool = x.o.get("tool") or x.recipe.get("tool") or ""
    if targets:
        k, c = targets[0]["item"].split("#", 1)
        name = _sql_name(x, k)
        p = x.profiles.get((k, c)) or {}
        if name and (not top_values(p) or "first" in str(p.get("method") or "")):
            q = _q(c)
            what = "outcomes" if tool in ("classification", "scoring") else "values"
            out.append({"title": f"How the target {c} is spread", "key": k, "full_profile": None,
                        "why": f"The common values in the profile come from the first rows only. The true share of "
                               f"each of the {what} shows whether there is enough of each to learn from.",
                        "sql": _header(name[1], f"Most common values of {c}") +
                        f"SELECT TOP (25) {q} AS [value], COUNT_BIG(*) AS [rows]\nFROM {name[0]} WITH (NOLOCK)\n"
                        f"GROUP BY {q}\nORDER BY COUNT_BIG(*) DESC;\n"})
    # 5. how many target rows find a match in each feature table that shares a key
    if target:
        tname = _sql_name(x, target)
        mine = {i.get("concept"): i["item"].split("#", 1)[1] for i in x.columns
                if i["item"].startswith(target + "#") and i.get("concept") and i.get("role") in ("key", "target",
                                                                                                 "feature", "date")}
        for t in x.tables:
            other = t["item"]
            if other == target or not tname or len(out) >= MAX_DEEPER:
                continue
            oname = _sql_name(x, other)
            theirs = {i.get("concept"): i["item"].split("#", 1)[1] for i in x.columns
                      if i["item"].startswith(other + "#") and i.get("concept") in mine
                      and i.get("role") == "key"}
            if not oname or not theirs or oname[1] != tname[1]:
                continue
            on = " AND ".join(f"j.{_q(theirs[cid])} = t.{_q(mine[cid])}" for cid in theirs)
            out.append({"title": f"How many {x.name(target)} rows match {x.name(other)}", "key": other,
                        "full_profile": None,
                        "why": "Matching key names suggest a join; this shows how many rows really find a partner. "
                               "Few matches mean the feature is blank for most predictions.",
                        "sql": _header(tname[1], f"Match rate of {x.name(target)} to {x.name(other)}") +
                        f"SELECT COUNT_BIG(*) AS [rows],\n       SUM(CASE WHEN EXISTS (SELECT 1 FROM {oname[0]} AS j "
                        f"WITH (NOLOCK) WHERE {on}) THEN 1 ELSE 0 END) AS [matched]\n"
                        f"FROM {tname[0]} AS t WITH (NOLOCK);\n"})
            break
    return out[:MAX_DEEPER]
