"""Rules become checks: a rule written in plain words is checked against each new profile.

Write a rule on an item ("Units is never negative", "OrderId is unique", "loaded daily by 7am")
and it is read into a check. Each time the item is profiled the check runs on the profile, and a
failure goes on the To review list. Rules already sitting in notes or column descriptions are
offered as suggestions to turn into checks.

Rules it understands (a column name first, where it needs one):
  * never negative / is never below 0 / always positive / never zero
  * never null / never blank / always filled / required
  * is unique / has no duplicates
  * between A and B / at least N / at most N / never more than N / never less than N
  * one of A, B, C / is A or B
  * has at least N rows
  * loaded daily / weekly / monthly / every N days (optionally "by 7am"), checked against the
    newest date in the data, or a file's modified time

A rule it can't read is kept as written and shown as "not checked".
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import suggestions
from .store import Catalogue, now

SETTING = "rule_checks"
NUM = r"-?\d[\d,]*(?:\.\d+)?"
PERIOD_DAYS = {"daily": 1, "every day": 1, "each day": 1, "weekly": 7, "every week": 7, "each week": 7,
               "monthly": 31, "every month": 31, "each month": 31}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_rule(text: str, columns: list[str] | None = None) -> dict[str, Any]:
    """{"check": kind, "column": name or None, ...args}; check "text" when it can't be read."""
    t = " ".join((text or "").strip().rstrip(".").split())
    low = t.lower()
    out: dict[str, Any] = {"check": "text", "column": _column_in(t, columns or [])}

    m = re.search(r"\b(loaded|refreshed|updated|arrives?|lands?|runs?)\b.*?\b(daily|weekly|monthly|every day|each day|"
                  r"every week|each week|every month|each month|every (\d+) days?)\b", low) or \
        re.search(r"\b(daily|weekly|monthly)\b.*\b(load|refresh|update)", low)
    if m:
        n = re.search(r"every (\d+) days?", m.group(0))
        days = int(n.group(1)) if n else next((d for w, d in PERIOD_DAYS.items() if w in m.group(0)), 1)
        out.update(check="fresh", days=days)
        by = re.search(r"\bby (\d{1,2})(?::(\d\d))?\s*(am|pm)?\b", low)
        if by:
            hour = int(by.group(1)) % 12 + (12 if by.group(3) == "pm" else 0) if by.group(3) else int(by.group(1))
            out["by_hour"] = hour
        return out
    m = re.search(rf"\b(?:has|have|with) (?:at least|more than|over) ({NUM}) rows\b", low)
    if m:
        out.update(check="min_rows", value=_num(m.group(1)), column=None)
        return out
    if out["column"] is None:
        return out
    if re.search(r"\b(never|not|no) (negative|below (?:0|zero)|less than (?:0|zero))\b|\b(>= ?0|is (?:0|zero) or more)\b",
                 low):
        out.update(check="min", value=0.0, strict=False)
    elif re.search(r"\b(always|is) positive\b|\bnever (?:0|zero) or (?:less|negative)\b|\b(> ?0|above (?:0|zero))\b", low):
        out.update(check="min", value=0.0, strict=True)
    elif re.search(r"\bnever (?:0|zero)\b", low):
        out.update(check="not_value", value="0")
    elif re.search(r"\b(never|not|no) (null|nulls|blank|blanks|empty|missing)\b|\balways (filled|set|present)\b|"
                   r"\b(is )?(required|mandatory)\b|\bnot null\b", low):
        out.update(check="not_null")
    elif re.search(r"\b(is |are )?unique\b|\bno duplicates?\b|\bnever (?:repeats|duplicated)\b", low):
        out.update(check="unique")
    elif m := re.search(rf"\bbetween ({NUM}) and ({NUM})\b", low):
        out.update(check="range", low=_num(m.group(1)), high=_num(m.group(2)))
    elif m := re.search(rf"(?<!\w)(?:at most|never more than|no more than|not more than|up to|<= ?|max(?:imum)? (?:of )?)\s*({NUM})\b",
                        low):
        out.update(check="max", value=_num(m.group(1)), strict=False)
    elif m := re.search(rf"(?<!\w)(?:less than|below|under|< ?)\s*({NUM})\b", low):
        out.update(check="max", value=_num(m.group(1)), strict=True)
    elif m := re.search(rf"(?<!\w)(?:at least|never less than|no less than|not less than|>= ?|min(?:imum)? (?:of )?)\s*({NUM})\b",
                        low):
        out.update(check="min", value=_num(m.group(1)), strict=False)
    elif m := re.search(rf"(?<!\w)(?:more than|above|over|greater than|> ?)\s*({NUM})\b", low):
        out.update(check="min", value=_num(m.group(1)), strict=True)
    elif m := re.search(r"\b(?:is |are )?(?:one of|always one of|only|either)\s+(.+)$", t, re.I):
        values = _values(m.group(1))
        if values:
            out.update(check="one_of", values=values)
    elif m := re.search(rf"^\W*{re.escape(out['column'])}\W*\s+(?:is|are)\s+(.+?\bor\b.+)$", t, re.I):
        values = _values(m.group(1))
        if len(values) > 1:
            out.update(check="one_of", values=values)
    return out


def _values(text: str) -> list[str]:
    parts = re.split(r",\s*|\s+or\s+|\s+and\s+", text.strip())
    return [p.strip().strip("'\"“”‘’") for p in parts if p.strip().strip("'\"“”‘’")]


def _column_in(text: str, columns: list[str]) -> str | None:
    """The column a rule is about: a known column named in it (longest first), else the first word."""
    for c in sorted(columns, key=len, reverse=True):
        if re.search(rf"(?<![\w]){re.escape(c)}(?![\w])", text, re.I):
            return c
    m = re.match(r"^\W*\[?([A-Za-z_][\w]*)\]?\s+(?:is|are|has|must|should|never|always)\b", text, re.I)
    return m.group(1) if m else None


def describe(rule: dict[str, Any]) -> str:
    c = rule.get("column") or ""
    k = rule.get("check")
    if k == "min":
        return f"{c} {'>' if rule.get('strict') else '≥'} {rule['value']:g}"
    if k == "max":
        return f"{c} {'<' if rule.get('strict') else '≤'} {rule['value']:g}"
    if k == "range":
        return f"{c} between {rule['low']:g} and {rule['high']:g}"
    if k == "not_null":
        return f"{c} has no blanks"
    if k == "not_value":
        return f"{c} is never {rule['value']}"
    if k == "unique":
        return f"{c} is unique"
    if k == "one_of":
        return f"{c} is one of " + ", ".join(rule["values"])
    if k == "min_rows":
        return f"at least {rule['value']:g} rows"
    if k == "fresh":
        return f"newest data no older than {rule['days']} day(s)" + (
            f", by {rule['by_hour']}:00" if rule.get("by_hour") is not None else "")
    return "not checked (couldn't read it as a check)"


# ---- the checks you've written ------------------------------------------

def _all(cat: Catalogue) -> dict[str, dict[str, Any]]:
    return cat.setting(SETTING, {}) or {}


def checks_for(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    return sorted(({"uid": u, **c} for u, c in _all(cat).items() if c["key"] == key),
                  key=lambda c: c.get("created_at") or "")


def add_check(cat: Catalogue, key: str, text: str, source: str = "") -> str:
    columns = _columns(cat, key)
    uid = uuid.uuid4().hex[:10]
    rules = _all(cat)
    rules[uid] = {"key": key, "text": text.strip(), "rule": parse_rule(text, columns), "created_at": now(),
                  "author": cat.author(), "source": source}
    cat.set_setting(SETTING, rules)
    return uid


def _columns(cat: Catalogue, key: str) -> list[str]:
    return list(dict.fromkeys([c["name"] for c in cat.columns(key)] + [p["column_name"] for p in cat.profile(key)]))


def delete_check(cat: Catalogue, uid: str) -> None:
    rules = _all(cat)
    if rules.pop(uid, None) is not None:
        cat.set_setting(SETTING, rules)


# ---- running them -------------------------------------------------------

def _float(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _date(v: Any) -> datetime | None:
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    for fmt in (None, "%d/%m/%Y", "%Y%m%d"):
        try:
            d = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s[:10], fmt)
        except ValueError:
            continue
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def run_check(cat: Catalogue, check: dict[str, Any], profile: list[dict[str, Any]] | None = None,
              asset: dict[str, Any] | None = None) -> dict[str, Any]:
    """{"state": "pass" | "fail" | "unknown", "detail", "profiled_at"} against the latest profile."""
    rule = check["rule"]
    profile = [dict(p) for p in cat.profile(check["key"])] if profile is None else profile
    asset = asset if asset is not None else (cat.get_asset(check["key"]) or {})
    k = rule.get("check")
    when = max((p["profiled_at"] for p in profile), default=None)

    def res(state: str, detail: str) -> dict[str, Any]:
        return {"state": state, "detail": detail, "profiled_at": when}

    if k == "text":
        return res("unknown", "Not a check it can read. Reword it, for example “Units is never negative”.")
    if k == "fresh":
        return _fresh(rule, profile, asset, when)
    if not profile:
        return res("unknown", "Not profiled yet. Profile it and the check runs.")
    if k == "min_rows":
        rows = max((p["row_count"] or 0 for p in profile), default=0)
        return res("pass" if rows >= rule["value"] else "fail", f"{rows:,} rows.")
    col = (rule.get("column") or "").lower()
    p = next((x for x in profile if x["column_name"].lower() == col), None)
    if p is None:
        return res("unknown", f"No column {rule.get('column')} in the latest profile.")
    sampled = " (in the rows profiled)" if "first" in (p.get("method") or "") or "sample" in (p.get("method") or "") else ""
    if k == "not_null":
        n = p["null_count"] or 0
        return res("pass" if n == 0 else "fail", f"{n:,} blank value(s){sampled}." if n else f"No blanks{sampled}.")
    if k == "unique":
        rows, distinct, nulls = p["row_count"] or 0, p["distinct_count"] or 0, p["null_count"] or 0
        dupes = rows - nulls - distinct
        return res("pass" if dupes <= 0 else "fail",
                   f"{dupes:,} repeated value(s){sampled}." if dupes > 0 else f"Every value is different{sampled}.")
    if k in ("min", "max", "range"):
        lo, hi = _float(p["min_value"]), _float(p["max_value"])
        if lo is None or hi is None:
            return res("unknown", "The column has no numbers to check.")
        bad = []
        if k in ("min", "range"):
            limit = rule["value"] if k == "min" else rule["low"]
            if lo < limit or (k == "min" and rule.get("strict") and lo <= limit):
                bad.append(f"lowest value is {lo:g}")
        if k in ("max", "range"):
            limit = rule["value"] if k == "max" else rule["high"]
            if hi > limit or (k == "max" and rule.get("strict") and hi >= limit):
                bad.append(f"highest value is {hi:g}")
        return res("fail" if bad else "pass", ("; ".join(bad) if bad else f"Values run from {lo:g} to {hi:g}") +
                   f"{sampled}.")
    if k == "not_value":
        top = json.loads(p["top_values"] or "[]")
        hit = next((c for v, c in top if str(v).strip() in (rule["value"], rule["value"] + ".0")), None)
        if hit:
            return res("fail", f"{rule['value']} appears {hit:,} time(s){sampled}.")
        lo, hi = _float(p["min_value"]), _float(p["max_value"])
        if lo is not None and hi is not None and (lo > 0 or hi < 0):
            return res("pass", f"Values run from {lo:g} to {hi:g}{sampled}.")
        return res("pass" if top else "unknown", "Not among the most common values." if top else "No values to check.")
    if k == "one_of":
        allowed = {v.lower() for v in rule["values"]}
        top = json.loads(p["top_values"] or "[]")
        other = [str(v) for v, _ in top if v is not None and str(v).lower() not in allowed]
        if other:
            return res("fail", "Also has " + ", ".join(other[:5]) + (" …" if len(other) > 5 else "") + f"{sampled}.")
        if p["distinct_count"] and p["distinct_count"] > len(top):
            return res("unknown", f"Only the {len(top)} most common of {p['distinct_count']:,} values were kept, "
                                  "and those all pass.")
        return res("pass", f"Only {', '.join(rule['values'])}{sampled}.")
    return res("unknown", "Not a check it can read.")


def _fresh(rule: dict[str, Any], profile: list[dict[str, Any]], asset: dict[str, Any], when: str | None) -> dict[str, Any]:
    attrs = asset.get("attrs") or {}
    col = (rule.get("column") or "").lower()
    newest, what = None, ""
    if attrs.get("modified"):
        newest, what = _date(attrs["modified"]), "file modified"
        checked_at = datetime.now(timezone.utc)
    else:
        checked_at = _date(when) or datetime.now(timezone.utc)
    if newest is None:
        dated = [p for p in profile if (not col or p["column_name"].lower() == col) and _date(p["max_value"])
                 and re.search(r"date|time|day|period|week|month", f"{p['data_type']} {p['column_name']}", re.I)]
        if dated:
            p = max(dated, key=lambda x: _date(x["max_value"]))
            newest, what = _date(p["max_value"]), f"newest {p['column_name']}"
    if newest is None:
        return {"state": "unknown", "detail": "No date column or modified time to check.", "profiled_at": when}
    allowed = timedelta(days=rule["days"])
    by = rule.get("by_hour")
    if by is not None and checked_at.hour < by:
        allowed += timedelta(days=1)  # before the deadline, the previous load still counts
    # data dated by day (no time) describes the day before it was loaded
    if what.startswith("newest") and newest.hour == 0 and newest.minute == 0:
        allowed += timedelta(days=1)
    age = checked_at - newest
    days = age.total_seconds() / 86400
    detail = f"{what} {newest.date().isoformat()}, {days:.1f} day(s) before the check."
    return {"state": "pass" if age <= allowed else "fail", "detail": detail, "profiled_at": when}


def results_for(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    profile = [dict(p) for p in cat.profile(key)]
    asset = cat.get_asset(key) or {}
    return [{**c, "result": run_check(cat, c, profile, asset)} for c in checks_for(cat, key)]


def review_tasks(cat: Catalogue, task) -> list:
    """Failed checks: one review item per item, renewed with each profile."""
    by_key: dict[str, list[dict[str, Any]]] = {}
    for uid, c in _all(cat).items():
        by_key.setdefault(c["key"], []).append({"uid": uid, **c})
    out = []
    for key, checks in by_key.items():
        a = cat.get_asset(key)
        if not a or a["status"] != "active":
            continue
        profile = [dict(p) for p in cat.profile(key)]
        failed = []
        for c in checks:
            r = run_check(cat, c, profile, a)
            if r["state"] == "fail":
                failed.append((c, r))
        if failed:
            when = max((r["profiled_at"] or "" for _, r in failed), default="")
            detail = " ".join(f"“{c['text']}”: {r['detail']}" for c, r in failed)
            out.append(task("rule_check", key, f"{len(failed)} rule check(s) failed on {a['name']}", detail,
                            version=f"{when}:{'+'.join(sorted(c['uid'] for c, _ in failed))}"))
    return out


def summary(cat: Catalogue) -> dict[str, int]:
    out = {"pass": 0, "fail": 0, "unknown": 0}
    for key in {c["key"] for c in _all(cat).values()}:
        for r in results_for(cat, key):
            out[r["result"]["state"]] += 1
    return out


# ---- rules already written in notes -------------------------------------

RULE_WORDS = re.compile(r"\b(never|always|unique|no duplicates|must be|must not|required|mandatory|at least|at most|"
                        r"between \S+ and|one of|loaded (?:daily|weekly|monthly|every)|refreshed (?:daily|weekly|monthly|"
                        r"every))\b", re.I)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text or "") if 8 <= len(s.strip()) <= 200]


def find_rules(cat: Catalogue) -> int:
    """Rules sitting in notes and column descriptions, offered as checks to add."""
    from .curation import column_info
    have = {(c["key"], c["text"].lower()) for c in _all(cat).values()}
    found = []
    keys = [r[0] for r in cat.conn.execute(
        "SELECT key FROM assets WHERE status = 'active' AND kind IN ('table', 'view', 'data_file', 'file_series')")]
    for key in keys:
        columns = _columns(cat, key)
        texts: list[tuple[str, str | None, str]] = []
        for n in cat.notes(key):
            col = n["asset_key"].split("#", 1)[1] if "#" in n["asset_key"] else None
            texts.append((n["body"], col, "a note"))
        for col, info in column_info(cat, key).items():
            if info.get("description"):
                texts.append((info["description"], col, "the column description"))
        for c in cat.columns(key):
            if c["description"]:
                texts.append((c["description"], c["name"], "the column description"))
        for body, col, where in texts:
            for s in _sentences(body):
                if not RULE_WORDS.search(s):
                    continue
                text = s if not col or re.search(rf"(?<!\w){re.escape(col)}(?!\w)", s, re.I) else f"{col} {s[0].lower()}{s[1:]}"
                text = text.rstrip(".;")
                if (key, text.lower()) in have:
                    continue
                rule = parse_rule(text, columns)
                if rule["check"] == "text":
                    continue
                a = cat.get_asset(key) or {}
                found.append({"id": suggestions.make_id("check", key, text.lower()), "subject": key,
                              "title": f"{a.get('name', key)}: check “{text}”",
                              "detail": f"From {where}. It would check {describe(rule)} with each profile.",
                              "payload": {"key": key, "text": text, "source": where}})
    return suggestions.sync(cat, "check", found)


@suggestions.handler("check")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    add_check(cat, p["key"], p["text"], source=p.get("source", ""))
