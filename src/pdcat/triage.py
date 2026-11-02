"""Sorting long suggestion lists into likely / check / unlikely, so you can accept the sure ones in one go.

Each row gets a band, a short reason and a group (e.g. the SAP field, where a metric was found, or the key a
column would carry). The reasons use how strong the match is and what you accepted or rejected before: if you
said no to WERKS on three columns called Plant_Name, the next Plant_Name is put under "unlikely".
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .store import Catalogue

BANDS = ("likely", "check", "unlikely")
BAND_LABELS = {"likely": "🟢 Likely", "check": "🟡 Check", "unlikely": "🔴 Unlikely"}
_JUNK_NAME = re.compile(r"^(?:[a-z]|col\d*|x\d*|y\d*|tmp\w*|temp\w*|test\w*|new\w*|calc\w*|value\d*|\w*\d{2,})$")


def _decisions(cat: Catalogue, kind: str) -> list[dict[str, Any]]:
    import json
    return [{"state": r[0], "payload": json.loads(r[1] or "{}"), "title": r[2]} for r in cat.conn.execute(
        "SELECT state, payload, title FROM suggestions WHERE kind = ? AND state IN ('accepted', 'rejected')", (kind,))]


def _learned(decided: list[dict], sig) -> tuple[Counter, Counter]:
    yes, no = Counter(), Counter()
    for d in decided:
        s = sig(d)
        if s:
            (yes if d["state"] == "accepted" else no)[s] += 1
    return yes, no


def _from_history(sig: str | None, yes: Counter, no: Counter, what: str) -> tuple[str, str] | None:
    if not sig:
        return None
    y, n = yes.get(sig, 0), no.get(sig, 0)
    if n >= 1 and n > y:
        return "unlikely", f"you rejected {n} {what} before"
    if y >= 1 and y > n:
        return "likely", f"you accepted {y} {what} before"
    return None


def triage(cat: Catalogue, kind: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of the rows with band, why and group added, likely first (then by score)."""
    fn = {"sap": _sap, "metric": _metric}.get(kind, _generic)
    out = [{**r, **dict(zip(("band", "why", "group"), fn_row, strict=True))} for r, fn_row in
           zip(rows, fn(cat, kind, rows), strict=True)]
    out.sort(key=lambda r: (BANDS.index(r["band"]), -(r.get("score") or 0)))
    return out


def _sap(cat: Catalogue, kind: str, rows: list[dict]) -> list[tuple[str, str, str]]:
    def sig(d: dict) -> str | None:
        p = d["payload"]
        return f"{(p.get('column') or '').lower()}→{p.get('sap_name')}" if p.get("column") else None

    yes, no = _learned(_decisions(cat, kind), sig)
    field_yes, field_no = _learned(_decisions(cat, kind), lambda d: d["payload"].get("sap_name"))
    out = []
    for r in rows:
        p = r["payload"]
        field = p.get("sap_name") or ""
        group = f"SAP {field}" if p.get("column") else f"SAP table {field}"
        hist = _from_history(sig({"payload": p}), yes, no, f"“{p.get('column')}” as {field}")
        if hist:
            out.append((*hist, group))
            continue
        how = r.get("detail") or ""
        if "is the SAP field name" in how:
            band, why = "likely", "the column is named exactly like the SAP field"
        elif "contains the SAP field name" in how:
            band, why = "check", "the SAP field name is part of the column name"
        elif not p.get("column"):
            band, why = "check", "the table name contains the SAP table name"
        else:
            band, why = "check", "an English name usually meaning this field"
        n_no, n_yes = field_no.get(field, 0), field_yes.get(field, 0)
        if band == "check" and n_no >= 3 and n_no > 2 * n_yes:
            band, why = "unlikely", f"you rejected {field} {n_no} times before"
        elif band == "check" and n_yes >= 3 and n_yes > 2 * n_no:
            band, why = "likely", f"you accepted {field} {n_yes} times before"
        out.append((band, why, group))
    return out


def _metric(cat: Catalogue, kind: str, rows: list[dict]) -> list[tuple[str, str, str]]:
    def words(d: dict) -> str | None:
        name = re.sub(r"[^a-z0-9 ]", " ", (d["payload"].get("name") or d.get("title") or "").lower()).split()
        return name[-1] if name else None  # "forecast accuracy" -> "accuracy", "stock value" -> "value"

    yes, no = _learned(_decisions(cat, kind), words)
    out = []
    for r in rows:
        p = r["payload"]
        places = p.get("places") or []
        if p.get("from_measure"):
            group = "Power BI measures"
        elif any("SQL" in w for w in places):
            group = "Computed SQL columns"
        else:
            group = "Python code"
        name = re.sub(r"[^a-z0-9]", "", (p.get("name") or r["title"]).lower())
        hist = _from_history(words({"payload": p, "title": r["title"]}), yes, no,
                             f"metrics ending in “{words({'payload': p, 'title': r['title']})}”")
        if _JUNK_NAME.match(name) or len(name) <= 2:
            band, why = "unlikely", "the name looks like a working column, not a business number"
        elif hist:
            band, why = hist
        elif not p.get("formula"):
            band, why = "unlikely", "the formula couldn't be read"
        elif len(places) >= 3:
            band, why = "likely", f"found in {len(places)} places"
        elif p.get("from_measure") and p.get("definition"):
            band, why = "likely", "a Power BI measure with a description"
        elif p.get("from_measure"):
            band, why = "check", "a Power BI measure"
        else:
            band, why = "check", "found in one place" if len(places) <= 1 else f"found in {len(places)} places"
        out.append((band, why, group))
    return out


def _generic(cat: Catalogue, kind: str, rows: list[dict]) -> list[tuple[str, str, str]]:
    scores = sorted((r.get("score") or 0) for r in rows)
    if not scores or scores[0] == scores[-1]:
        return [("check", "", "") for _ in rows]
    hi = scores[int(len(scores) * 2 / 3)]
    lo = scores[int(len(scores) / 3)]
    return [("likely" if (r.get("score") or 0) >= hi and hi > lo else
             "unlikely" if (r.get("score") or 0) < lo else "check", "a stronger match than most" if
             (r.get("score") or 0) >= hi and hi > lo else "", "") for r in rows]


def key_column_bands(confirmed: list[dict], suggested: list[dict]) -> list[tuple[str, str, str]]:
    """For columns that probably carry a company-wide key: same data type as the confirmed ones is likely."""
    def base(t: str | None) -> str:
        return re.sub(r"\(.*", "", (t or "").lower()).strip()

    types = Counter(base(c.get("data_type")) for c in confirmed if c.get("data_type"))
    usual = types.most_common(1)[0][0] if types else ""
    out = []
    for c in suggested:
        t = base(c.get("data_type"))
        group = c.get("key_name") or ""
        if not usual or not t:
            out.append(("check", "no data type to compare", group))
        elif t == usual:
            out.append(("likely", f"same data type as the confirmed columns ({usual})", group))
        elif {t, usual} <= {"int", "bigint", "smallint", "tinyint", "numeric", "decimal"} or \
                {t, usual} <= {"varchar", "nvarchar", "char", "nchar", "string", "object"}:
            out.append(("check", f"similar data type ({t}, usually {usual})", group))
        else:
            out.append(("unlikely", f"different data type ({t}, usually {usual})", group))
    return out
