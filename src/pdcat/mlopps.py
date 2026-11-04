"""ML opportunities: which machine learning projects the catalogue's data could support, and how ready it is.

After each scan every recipe in `mlrecipes` is matched against the catalogue: columns by their SAP meaning and
by their names, tables by their SAP table and name, so each recipe gets an opportunity with candidate tables
and columns (as suggestions). Each opportunity is then scored on six readiness signals:

1. **Target**: a column (or a measure) that records the outcome to predict.
2. **History**: how far back the target table's dates go, from profiles (2 years by default).
3. **Grain**: the key columns a row is identified by (material and plant, a PO line...).
4. **Features**: other tables that join to the target table.
5. **Freshness**: whether the target table is kept up to date.
6. **Business pain**: Power BI measures that already show the problem.

Scores are a weighted share of the six (target and history count double by default; Settings). You can confirm
or override each signal, add your own opportunities, split one into several targets, and link opportunities
to business projects.

Everything here reads what scans already stored. Nothing is sent to SQL Server, Power BI or any other service,
and nothing is written anywhere but the catalogue file. The baseline query and "Use as target" give you SQL to
copy and run yourself.
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
import zipfile
from datetime import date, datetime, timedelta
from typing import Any

from . import mlrecipes as R
from .store import Catalogue, now

STATUSES = ["idea", "exploring", "pilot", "live", "parked"]
STATUS_LABELS = {"idea": "Idea", "exploring": "Exploring", "pilot": "Pilot", "live": "Live", "parked": "Parked"}
ROLES = ["target", "feature", "key", "date", "related", "source", ""]
ROLE_LABELS = {"target": "Target", "feature": "Feature", "key": "Key", "date": "Date", "related": "Related",
               "source": "Source", "": ""}
SIGNALS = [("target", "Target"), ("history", "History depth"), ("grain", "Grain"), ("features", "Features"),
           ("freshness", "Freshness"), ("pain", "Business pain")]
SIGNAL_HELP = {
    "target": "A column that records the outcome to predict (a date, a quantity, a yes or no).",
    "history": "How far back the target table's dates go. Seasonal patterns need two years or more.",
    "grain": "The key columns that identify a row at the level a decision is made.",
    "features": "Other tables that join to the target table, to explain it.",
    "freshness": "Whether the target table is refreshed regularly.",
    "pain": "Power BI measures that already show the problem, which makes the business case.",
}
STATES = ("ready", "partial", "missing")
STATE_VALUE = {"ready": 1.0, "partial": 0.5, "missing": 0.0}
STATE_LABELS = {"ready": "Found", "partial": "Partly found", "missing": "Missing"}
DEFAULT_WEIGHTS = {"target": 2, "history": 2, "grain": 1, "features": 1, "freshness": 1, "pain": 1}
DEFAULT_MIN_MONTHS = 24
BANDS = {"live": "Live", "ready": "Ready to start", "partial": "Partly ready", "notyet": "Not yet"}
READY_AT, PARTIAL_AT = 75, 45
TABLE_KINDS = ("table", "view", "data_file", "file_series")
MAX_TABLES = 6          # candidate tables suggested per recipe
MAX_MEASURES = 15       # related measures suggested per opportunity
LEAK = re.compile(r"actual|_ist$|^ist|received|delivered|posted|final|invoiced|closed|result|outcome|after|"
                  r"shipped|completed", re.I)
DAX_FUNCS = re.compile(r"\b(CALCULATE|CALCULATETABLE|FILTER|ALL|ALLEXCEPT|ALLSELECTED|SUMX|AVERAGEX|COUNTX|MAXX|MINX|"
                       r"DIVIDE|IF|SWITCH|DATEDIFF|DATEADD|SAMEPERIODLASTYEAR|TOTALYTD|RELATED|RELATEDTABLE|"
                       r"COUNTROWS|DISTINCTCOUNT|VALUES|SUMMARIZE|ADDCOLUMNS|KEEPFILTERS|USERELATIONSHIP|"
                       r"TREATAS|RANKX|LOOKUPVALUE|VAR)\b", re.I)


def squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def weights(cat: Catalogue) -> dict[str, float]:
    saved = cat.setting("ml_weights", {}) or {}
    return {k: float(saved.get(k, v)) for k, v in DEFAULT_WEIGHTS.items()}


def min_months(cat: Catalogue) -> int:
    return int(cat.setting("ml_min_history_months", DEFAULT_MIN_MONTHS) or DEFAULT_MIN_MONTHS)


# ---- opportunities --------------------------------------------------------------------------------------------

def _row(r) -> dict[str, Any]:
    d = dict(r)
    d["checks"] = json.loads(d.get("checks") or "{}")
    return d


def get(cat: Catalogue, uid: str | None) -> dict[str, Any] | None:
    if not uid:
        return None
    r = cat.conn.execute("SELECT * FROM ml_opportunities WHERE uid = ?", (uid,)).fetchone()
    return _row(r) if r else None


def list_opps(cat: Catalogue) -> list[dict[str, Any]]:
    """Every opportunity; those for recipes you switched off are left out."""
    on = set(R.all_recipes(cat))
    rows = [_row(r) for r in cat.conn.execute("SELECT * FROM ml_opportunities ORDER BY name, variant")]
    return [o for o in rows if not (o["auto"] and o["recipe"] and o["recipe"] not in on)]


FIELDS = ("name", "recipe", "parent", "variant", "predicts", "tool", "method", "status", "owner", "notes", "value",
          "effort", "baseline")


def save(cat: Catalogue, name: str, uid: str | None = None, **fields: Any) -> str:
    """Add or update an opportunity. Only a name is needed; fields not given keep their value."""
    if not (name or "").strip():
        raise ValueError("An opportunity needs a name.")
    bad = set(fields) - set(FIELDS) - {"checks", "auto"}
    if bad:
        raise ValueError(f"unknown field(s): {', '.join(sorted(bad))}")
    old = get(cat, uid) if uid else None
    uid = uid or uuid.uuid4().hex[:12]
    row = {f: (old or {}).get(f) for f in FIELDS}
    row.update({k: v for k, v in fields.items() if k in FIELDS})
    row["name"] = name.strip()
    row["status"] = row.get("status") or "idea"
    checks = fields.get("checks", (old or {}).get("checks") or {})
    auto = int(fields.get("auto", (old or {}).get("auto") or 0))
    ts = now()
    with cat.conn:
        cat.conn.execute(
            f"INSERT INTO ml_opportunities (uid, {', '.join(FIELDS)}, checks, auto, created_at, updated_at, author) "
            f"VALUES (?, {', '.join('?' * len(FIELDS))}, ?, ?, ?, ?, ?) ON CONFLICT(uid) DO UPDATE SET "
            f"{', '.join(f'{f} = excluded.{f}' for f in FIELDS)}, checks = excluded.checks, auto = excluded.auto, "
            "updated_at = excluded.updated_at",
            (uid, *(row[f] for f in FIELDS), json.dumps(checks), auto, (old or {}).get("created_at") or ts, ts,
             (old or {}).get("author") or cat.author()))
    cat.changed()
    return uid


def delete(cat: Catalogue, uid: str) -> None:
    """Delete an opportunity (and its splits' link to it: they stay, as opportunities of their own)."""
    with cat.conn:
        cat.conn.execute("DELETE FROM ml_opportunities WHERE uid = ?", (uid,))
        cat.conn.execute("DELETE FROM ml_items WHERE opp_uid = ?", (uid,))
        cat.conn.execute("DELETE FROM ml_links WHERE opp_uid = ?", (uid,))
        cat.conn.execute("UPDATE ml_opportunities SET parent = NULL WHERE parent = ?", (uid,))
    cat.changed()


def set_check(cat: Catalogue, uid: str, signal: str, *, confirmed: bool | None = None,
              override: str | None = None, note: str | None = None) -> None:
    """Tick a readiness signal as confirmed, or override what was found (override "" = back to automatic)."""
    o = get(cat, uid)
    if not o:
        return
    checks = dict(o["checks"])
    c = dict(checks.get(signal) or {})
    if confirmed is not None:
        c["confirmed"] = bool(confirmed)
    if override is not None:
        if override and override not in STATES:
            raise ValueError(f"unknown state {override!r}")
        c["override"] = override or None
    if note is not None:
        c["note"] = note
    checks[signal] = c
    save(cat, o["name"], uid, checks=checks)


def items(cat: Catalogue, uid: str, *, include_rejected: bool = False) -> list[dict[str, Any]]:
    rows = [dict(r) for r in cat.conn.execute("SELECT * FROM ml_items WHERE opp_uid = ? ORDER BY kind, item", (uid,))]
    return rows if include_rejected else [r for r in rows if r["state"] != "rejected"]


def set_item(cat: Catalogue, uid: str, item: str, *, kind: str | None = None, role: str | None = None,
             state: str = "confirmed", why: str | None = None, concept: str | None = None) -> None:
    """Add an item to an opportunity, or change its role or state (confirmed, suggested or rejected)."""
    if kind is None:
        kind = "column" if "#" in item else ("measure" if _is_measure(cat, item) else "table")
    old = cat.conn.execute("SELECT * FROM ml_items WHERE opp_uid = ? AND item = ?", (uid, item)).fetchone()
    old = dict(old) if old else {}
    with cat.conn:
        cat.conn.execute(
            "INSERT INTO ml_items (opp_uid, item, kind, role, state, why, concept, updated_at) VALUES (?, ?, ?, ?, ?, "
            "?, ?, ?) ON CONFLICT(opp_uid, item) DO UPDATE SET kind = excluded.kind, role = excluded.role, "
            "state = excluded.state, why = excluded.why, concept = excluded.concept, updated_at = excluded.updated_at",
            (uid, item, kind, role if role is not None else old.get("role"), state,
             why if why is not None else old.get("why"), concept if concept is not None else old.get("concept"),
             now()))
    cat.changed()


def remove_item(cat: Catalogue, uid: str, item: str) -> None:
    """Take an item out and don't suggest it again."""
    set_item(cat, uid, item, state="rejected")


def _is_measure(cat: Catalogue, key: str) -> bool:
    r = cat.conn.execute("SELECT kind FROM assets WHERE key = ?", (key,)).fetchone()
    return bool(r and r[0] == "measure")


def split(cat: Catalogue, uid: str, variant: str | None = None) -> str:
    """A new opportunity split from this one: it copies the name, recipe, tool, items, ticks, overrides, notes
    and business project links as they are now, and from then on is its own opportunity."""
    src = get(cat, uid)
    if not src:
        raise ValueError("No such opportunity.")
    root = get(cat, src["parent"]) if src["parent"] else None
    base = root or src
    siblings = [o for o in list_opps(cat) if o["parent"] == base["uid"]]
    letter = chr(ord("B") + len(siblings)) if len(siblings) < 25 else str(len(siblings) + 2)
    if not base.get("variant"):
        save(cat, base["name"], base["uid"], variant="Target A")
    new = save(cat, base["name"], None, recipe=src["recipe"], parent=base["uid"],
               variant=variant or f"Target {letter}", predicts=src["predicts"], tool=src["tool"],
               method=src["method"], status="idea", owner=src["owner"], notes=src["notes"], value=src["value"],
               effort=src["effort"], baseline=src["baseline"], checks=src["checks"], auto=0)
    with cat.conn:
        cat.conn.execute("INSERT INTO ml_items (opp_uid, item, kind, role, state, why, concept, updated_at) "
                         "SELECT ?, item, kind, role, state, why, concept, ? FROM ml_items WHERE opp_uid = ?",
                         (new, now(), uid))
        cat.conn.execute("INSERT OR IGNORE INTO ml_links (opp_uid, project_uid) SELECT ?, project_uid FROM ml_links "
                         "WHERE opp_uid = ?", (new, uid))
    cat.changed()
    return new


def splits_of(cat: Catalogue, uid: str) -> list[dict[str, Any]]:
    return [_row(r) for r in cat.conn.execute("SELECT * FROM ml_opportunities WHERE parent = ? ORDER BY variant",
                                              (uid,))]


def title(o: dict[str, Any]) -> str:
    return f"{o['name']} · {o['variant']}" if o.get("variant") else o["name"]


# ---- business projects ------------------------------------------------------------------------------------------

def projects(cat: Catalogue, uid: str) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        "SELECT p.uid, p.name, (SELECT COUNT(*) FROM bproject_members m WHERE m.project_uid = p.uid AND "
        "m.state = 'confirmed') AS members FROM ml_links l JOIN bprojects p ON p.uid = l.project_uid "
        "WHERE l.opp_uid = ? ORDER BY p.name", (uid,))]


def link_project(cat: Catalogue, uid: str, project_uid: str) -> int:
    """Link to a business project. Its tables that match the recipe or share key columns with the
    opportunity's tables are suggested as candidates. Returns how many were suggested."""
    with cat.conn:
        cat.conn.execute("INSERT OR IGNORE INTO ml_links (opp_uid, project_uid) VALUES (?, ?)", (uid, project_uid))
    cat.changed()
    return _suggest_from_projects(cat, uid)


def unlink_project(cat: Catalogue, uid: str, project_uid: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM ml_links WHERE opp_uid = ? AND project_uid = ?", (uid, project_uid))
    cat.changed()


def opps_for_project(cat: Catalogue, project_uid: str) -> list[dict[str, Any]]:
    return [_row(r) for r in cat.conn.execute(
        "SELECT o.* FROM ml_links l JOIN ml_opportunities o ON o.uid = l.opp_uid WHERE l.project_uid = ? "
        "ORDER BY o.name, o.variant", (project_uid,))]


def opps_for_item(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    """Opportunities that use a table (or one of its columns), confirmed or suggested."""
    return [_row(r) for r in cat.conn.execute(
        "SELECT DISTINCT o.* FROM ml_items i JOIN ml_opportunities o ON o.uid = i.opp_uid WHERE "
        "(i.item = ? OR i.item LIKE ? ESCAPE '\\') AND i.state != 'rejected' ORDER BY o.name",
        (key, key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "#%"))]


def _suggest_from_projects(cat: Catalogue, uid: str) -> int:
    have = {r["item"] for r in items(cat, uid, include_rejected=True)}
    mine_keys = _key_columns_of(cat, [i["item"] for i in items(cat, uid) if i["kind"] == "table"])
    added = 0
    for p in projects(cat, uid):
        for r in cat.conn.execute(
                "SELECT a.key FROM bproject_members m JOIN assets a ON a.key = m.asset_key WHERE m.project_uid = ? "
                f"AND m.state = 'confirmed' AND a.status = 'active' AND a.kind IN ({', '.join('?' * len(TABLE_KINDS))})",
                (p["uid"], *TABLE_KINDS)):
            key = r[0]
            if key in have:
                continue
            shared = mine_keys & _key_columns_of(cat, [key])
            if mine_keys and not shared:
                continue
            why = f"in business project {p['name']}" + (f", shares {', '.join(sorted(shared)[:3])}" if shared else "")
            set_item(cat, uid, key, kind="table", role="feature", state="suggested", why=why)
            added += 1
    return added


def _key_columns_of(cat: Catalogue, keys: list[str]) -> set[str]:
    """SAP key fields (or squashed key-like names) carried by these tables."""
    if not keys:
        return set()
    marks = ", ".join("?" * len(keys))
    sap = {r[0]: r[1] for r in cat.conn.execute(
        f"SELECT asset_key || '#' || column_name, sap_name FROM column_info WHERE asset_key IN ({marks}) AND "
        "sap_name IS NOT NULL AND sap_name != ''", keys)}
    out = set()
    from .sapterms import match_column
    key_fields = {"MATNR", "WERKS", "LGORT", "LIFNR", "KUNNR", "VBELN", "EBELN", "CHARG", "LGNUM", "TKNUM"}
    for r in cat.conn.execute(f"SELECT asset_key, name FROM columns WHERE asset_key IN ({marks})", keys):
        s = sap.get(f"{r[0]}#{r[1]}") or (match_column(r[1]) or (None,))[0]
        if s and s.upper() in key_fields:
            out.add(s.upper())
    return out


# ---- matching recipes to the catalogue -------------------------------------------------------------------------

class _Matcher:
    """Finds which recipe concepts each column is, in one pass over the columns."""

    def __init__(self, recipes: dict[str, dict[str, Any]]):
        self.by_sap: dict[str, list[tuple[str, str]]] = {}
        self.exact: dict[str, list[tuple[str, str]]] = {}
        contains: dict[str, list[tuple[str, str]]] = {}
        for rid, r in recipes.items():
            for c in r.get("concepts", []):
                ref = (rid, c["id"])
                for s in c.get("sap", []):
                    self.by_sap.setdefault(s.upper(), []).append(ref)
                    self.exact.setdefault(squash(s), []).append(ref)
                for n in c.get("names", []):
                    n = squash(n)
                    if not n:
                        continue
                    self.exact.setdefault(n, []).append(ref)
                    if len(n) >= 6:
                        contains.setdefault(n, []).append(ref)
        self.contains = contains
        words = sorted(contains, key=len, reverse=True)
        self.rx = re.compile("|".join(re.escape(w) for w in words)) if words else None

    def match(self, column: str, sap_name: str | None) -> dict[tuple[str, str], str]:
        """{(recipe, concept): how} for one column."""
        out: dict[tuple[str, str], str] = {}
        if sap_name:
            for ref in self.by_sap.get(sap_name.upper(), []):
                out[ref] = f"SAP {sap_name.upper()}"
        n = squash(column)
        for ref in self.exact.get(n, []):
            out.setdefault(ref, "its name")
        if self.rx and len(n) > 6:
            for m in self.rx.finditer(n):
                for ref in self.contains.get(m.group(0), []):
                    out.setdefault(ref, f"its name contains {m.group(0)}")
        return out


def _sap_for_columns(cat: Catalogue) -> dict[tuple[str, str], str]:
    """(table key, column) -> SAP field: what you confirmed, else what the open SAP suggestion says."""
    out: dict[tuple[str, str], str] = {}
    for r in cat.conn.execute("SELECT payload FROM suggestions WHERE kind = 'sap' AND state = 'open'"):
        try:
            p = json.loads(r[0] or "{}")
        except ValueError:
            continue
        if p.get("column") and p.get("sap_name"):
            out[(p["key"], p["column"])] = p["sap_name"]
    for r in cat.conn.execute("SELECT asset_key, column_name, sap_name FROM column_info WHERE sap_name IS NOT NULL "
                              "AND sap_name != ''"):
        out[(r[0], r[1])] = r[2]
    return out


def candidates(cat: Catalogue, recipes: dict[str, dict[str, Any]] | None = None
               ) -> dict[str, list[dict[str, Any]]]:
    """For each recipe, the best candidate tables: {"key", "score", "concepts": {concept: [(column, how)]},
    "why"}. Reads only what scans stored."""
    recipes = recipes if recipes is not None else R.all_recipes(cat)
    if not recipes:
        return {}
    from .curation import hidden_keys
    from .sapterms import match_column, match_table
    hidden = hidden_keys(cat, old=True)
    tables = {r[0]: r[1] for r in cat.conn.execute(
        f"SELECT key, name FROM assets WHERE status = 'active' AND kind IN ({', '.join('?' * len(TABLE_KINDS))})",
        TABLE_KINDS) if r[0] not in hidden}
    item_sap = {r[0]: r[1] for r in cat.conn.execute(
        "SELECT asset_key, sap_name FROM item_info WHERE sap_name IS NOT NULL AND sap_name != ''")}
    col_sap = _sap_for_columns(cat)
    m = _Matcher(recipes)
    found: dict[str, dict[str, dict[str, list[tuple[str, str]]]]] = {}  # recipe -> table -> concept -> columns
    for r in cat.conn.execute("SELECT asset_key, name FROM columns"):
        key, col = r[0], r[1]
        if key not in tables:
            continue
        sap = col_sap.get((key, col))
        if not sap:
            hit = match_column(col)
            sap = hit[0] if hit else None
        for (rid, cid), how in m.match(col, sap).items():
            found.setdefault(rid, {}).setdefault(key, {}).setdefault(cid, []).append((col, how))
    out: dict[str, list[dict[str, Any]]] = {}
    for rid, r in recipes.items():
        roles = {c["id"]: c.get("role") for c in r.get("concepts", [])}
        sap_tables = {t.upper() for t in r.get("sap_tables", [])}
        words = [squash(w) for w in r.get("table_words", []) if w]
        rows = []
        for key, concepts in found.get(rid, {}).items():
            name = tables[key]
            base = squash(re.split(r"[\\/.]", name)[-1] if not re.search(r"\.(csv|parquet|xlsx?)$", name, re.I)
                          else re.split(r"[\\/]", name)[-1])
            score = 0.0
            reasons = []
            targets = [c for c in concepts if roles.get(c) == "target"]
            score += 3 * len(targets) + 1.0 * (len(concepts) - len(targets))
            st = item_sap.get(key) or match_table(name)
            if st and st.upper() in sap_tables:
                score += 2
                reasons.append(f"an extract of SAP {st.upper()}")
            hit = next((w for w in words if w and w in base), None)
            if hit:
                score += 1
                reasons.append(f"its name has “{hit}”")
            if score < 3 or len(concepts) < 2 and not targets:
                continue
            labels = [next((c["label"] for c in r["concepts"] if c["id"] == cid), cid) for cid in concepts]
            reasons.insert(0, "has " + ", ".join(labels[:5]) + (" and more" if len(labels) > 5 else ""))
            rows.append({"key": key, "score": score, "concepts": concepts, "why": "; ".join(reasons),
                         "target": bool(targets)})
        rows.sort(key=lambda x: (-x["score"], tables[x["key"]].lower()))
        out[rid] = rows[:MAX_TABLES]
    return out


def refresh(cat: Catalogue) -> int:
    """After a scan: every recipe that is switched on gets its opportunity (once), and every opportunity with a
    recipe gets its candidate tables and columns suggested again. Your confirmed and rejected items are never
    touched. Returns how many opportunities were checked."""
    recipes = R.all_recipes(cat)
    have = {o["recipe"]: o for o in (_row(r) for r in cat.conn.execute(
        "SELECT * FROM ml_opportunities WHERE auto = 1"))}
    for rid, r in recipes.items():
        if rid not in have:
            save(cat, r["name"], f"r-{rid}"[:40], recipe=rid, predicts=r["predicts"], tool=r["tool"],
                 method=r["method"], auto=1)
    cands = candidates(cat, recipes)
    opps = [o for o in list_opps(cat) if o["recipe"] in recipes]
    ts = now()
    with cat.conn:
        for o in opps:
            r = recipes[o["recipe"]]
            roles = {c["id"]: c.get("role") or "feature" for c in r.get("concepts", [])}
            decided = {row[0] for row in cat.conn.execute(
                "SELECT item FROM ml_items WHERE opp_uid = ? AND state != 'suggested'", (o["uid"],))}
            want: dict[str, tuple[str, str, str, str]] = {}
            for t in cands.get(o["recipe"], []):
                want[t["key"]] = ("table", "target" if t["target"] else "feature", t["why"], "")
                for cid, cols in t["concepts"].items():
                    for col, how in cols[:2]:
                        want[f"{t['key']}#{col}"] = ("column", roles.get(cid, "feature"), f"looks like "
                                                     f"{_label(r, cid)} ({how})", cid)
            cat.conn.execute("DELETE FROM ml_items WHERE opp_uid = ? AND state = 'suggested' AND kind IN "
                             "('table', 'column') AND why NOT LIKE 'in business project%'", (o["uid"],))
            cat.conn.executemany(
                "INSERT OR IGNORE INTO ml_items (opp_uid, item, kind, role, state, why, concept, updated_at) "
                "VALUES (?, ?, ?, ?, 'suggested', ?, ?, ?)",
                [(o["uid"], item, k, role, why, cid, ts) for item, (k, role, why, cid) in want.items()
                 if item not in decided])
    for o in opps:
        if projects(cat, o["uid"]):
            _suggest_from_projects(cat, o["uid"])
    cat.set_setting("ml_checked_at", ts, quiet=True)
    return len(opps)


def _label(recipe: dict[str, Any], cid: str) -> str:
    c = R.concept(recipe, cid)
    return (c or {}).get("label", cid).lower()


def suggest_for(cat: Catalogue, uid: str) -> int:
    """Suggest candidates for one opportunity now (after you pick a recipe for it, say)."""
    o = get(cat, uid)
    if not o or not o.get("recipe"):
        return 0
    refresh(cat)
    return len([i for i in items(cat, uid) if i["state"] == "suggested"])


# ---- reading the catalogue for one opportunity -----------------------------------------------------------------

def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{8}", s):
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


def _span(prof: dict[str, Any] | None) -> tuple[date, date] | None:
    if not prof:
        return None
    a, b = _as_date(prof.get("min_value")), _as_date(prof.get("max_value"))
    if a and b and b >= a and a.year >= 1950 and b.year <= 2100:
        return a, b
    return None


def _months(a: date, b: date) -> float:
    return (b - a).days / 30.44


class _Ctx:
    """What one opportunity's checks read from the catalogue, loaded once."""

    def __init__(self, cat: Catalogue, o: dict[str, Any]):
        self.cat = cat
        self.o = o
        self.recipe = R.get_recipe(cat, o.get("recipe")) or {}
        self.items = items(cat, o["uid"])
        self.tables = [i for i in self.items if i["kind"] == "table"]
        self.columns = [i for i in self.items if i["kind"] == "column"]
        self.measures = [i for i in self.items if i["kind"] == "measure"]
        keys = {i["item"] for i in self.tables} | {i["item"].split("#", 1)[0] for i in self.columns}
        self.keys = sorted(keys)
        marks = ", ".join("?" * len(self.keys)) or "''"
        self.assets = {r["key"]: dict(r) for r in cat.conn.execute(
            f"SELECT key, kind, name, source, status, last_seen, attrs FROM assets WHERE key IN ({marks})", self.keys)}
        self.profiles: dict[tuple[str, str], dict[str, Any]] = {}
        for r in cat.conn.execute(f"SELECT * FROM profiles WHERE asset_key IN ({marks})", self.keys):
            self.profiles[(r["asset_key"], r["column_name"])] = dict(r)
        self.coltypes = {(r[0], r[1]): (r[2] or "") for r in cat.conn.execute(
            f"SELECT asset_key, name, data_type FROM columns WHERE asset_key IN ({marks})", self.keys)}
        self.info = {r["asset_key"]: dict(r) for r in cat.conn.execute(
            f"SELECT * FROM item_info WHERE asset_key IN ({marks})", self.keys)}
        self.col_sap = {(r[0], r[1]): r[2] for r in cat.conn.execute(
            f"SELECT asset_key, column_name, sap_name FROM column_info WHERE asset_key IN ({marks}) AND sap_name "
            "IS NOT NULL AND sap_name != ''", self.keys)}

    def name(self, key: str) -> str:
        from .pathbits import file_name
        a = self.assets.get(key)
        return file_name(a["name"], a["kind"]) if a else key

    def ordered(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Confirmed first, then suggested."""
        return sorted(rows, key=lambda i: (i["state"] != "confirmed", i["item"]))

    def by_role(self, role: str, kind: str = "column") -> list[dict[str, Any]]:
        pool = {"column": self.columns, "table": self.tables, "measure": self.measures}[kind]
        return self.ordered([i for i in pool if i.get("role") == role])

    def target_table(self) -> str | None:
        for i in self.by_role("target"):
            return i["item"].split("#", 1)[0]
        for i in self.by_role("target", "table"):
            return i["item"]
        return self.tables[0]["item"] if self.tables else None

    def column_label(self, item: str) -> str:
        key, col = item.split("#", 1)
        sap = self.col_sap.get((key, col))
        if not sap:
            from .sapterms import match_column
            hit = match_column(col)
            sap = hit[0] if hit and hit[0].lower() != squash(col) else None
        if sap and squash(sap) != squash(col):
            return f"{self.name(key)}.{col} (SAP: {sap})"
        return f"{self.name(key)}.{col}" + (" (SAP field)" if sap else "")

    def date_columns(self, key: str) -> list[str]:
        cols = {i["item"].split("#", 1)[1] for i in self.columns if i["item"].startswith(key + "#")
                and i.get("role") in ("date", "target")}
        cols |= {c for (k, c), t in self.coltypes.items() if k == key and any(x in t.lower() for x in ("date", "time"))}
        return sorted(cols)


# ---- the six signals --------------------------------------------------------------------------------------------

def _signal(sid: str, state: str, evidence: str) -> dict[str, Any]:
    return {"id": sid, "name": dict(SIGNALS)[sid], "auto": state, "state": state, "evidence": evidence}


def _target(x: _Ctx) -> dict[str, Any]:
    if x.recipe.get("kind") == "agent":
        n = x.cat.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        state = "ready" if n >= 20 else "partial" if n else "missing"
        return _signal("target", state, f"No number to predict; success is answers planners trust. {n} question(s) "
                                        "in the Question library to test it with" + ("" if n >= 20 else
                                                                                     " (20 or more is a good start)"))
    cols = x.by_role("target")
    if cols:
        more = f" (and {len(cols) - 1} more)" if len(cols) > 1 else ""
        return _signal("target", "ready", x.column_label(cols[0]["item"]) + more)
    ms = x.by_role("target", "measure")
    if ms:
        return _signal("target", "partial", f"Only a measure ({_measure_name(x.cat, ms[0]['item'])}); its logic would "
                                            "need a SQL view to train on (Use as target shows one)")
    if x.recipe.get("kind") == "unsupervised":
        from . import trackers
        tracked = [t for k in x.keys for t in trackers.trackers_of(x.cat, k)]
        if tracked:
            return _signal("target", "ready", "No labelled errors needed: the model compares settings with peers. "
                                              f"{len(tracked)} change tracker(s) show what changed")
        if x.columns:
            return _signal("target", "partial", "No labelled errors: start unsupervised, comparing each setting with "
                                                "similar materials")
    tt = x.by_role("target", "table")
    if tt:
        return _signal("target", "partial", f"{x.name(tt[0]['item'])} looks right, but no target column was found "
                                            "in it; tick one under Columns")
    return _signal("target", "missing", "No column found that records the outcome. Add a table, or tick a column "
                                        "as the target")


def _history(x: _Ctx, need: int) -> dict[str, Any]:
    if x.recipe.get("kind") == "agent":
        return _signal("history", "ready", "Not needed: it reads current data")
    key = x.target_table()
    if not key:
        return _signal("history", "missing", "No target table yet")
    from .tabletypes import table_type
    best: tuple[float, str, date, date] | None = None
    unprofiled = []
    for k in [key] + [t["item"] for t in x.tables if t["item"] != key]:
        ttype = (x.info.get(k) or {}).get("table_type") or table_type(x.cat, k)
        if k != key and ttype != "snapshot":
            continue
        cols = x.date_columns(k)
        if not cols:
            continue
        spans = [(c, _span(x.profiles.get((k, c)))) for c in cols]
        got = [(c, s) for c, s in spans if s]
        if not got:
            unprofiled.append(k)
            continue
        c, (a, b) = max(got, key=lambda cs: _months(*cs[1]))
        months = _months(a, b)
        if best is None or months > best[0]:
            best = (months, f"{x.name(k)}.{c}", a, b)
    if best:
        months, where, a, b = best
        years = months / 12
        text = f"{years:.1f} years in {where} ({a:%b %Y} to {b:%b %Y})"
        if months >= need:
            return _signal("history", "ready", text)
        if months >= need / 2:
            return _signal("history", "partial", text + f"; {need // 12} years or more is better for seasons")
        return _signal("history", "missing", text + f"; {need // 12} years or more is needed")
    if unprofiled:
        return _signal("history", "partial", f"Profile {x.name(unprofiled[0])} to measure how far back its dates go")
    ttype = (x.info.get(key) or {}).get("table_type") or table_type(x.cat, key)
    if ttype == "dimension":
        return _signal("history", "missing", f"{x.name(key)} keeps today only (a dimension); look for a snapshot table "
                                             "or a change tracker")
    return _signal("history", "missing", f"No date column found in {x.name(key)}")


def _grain(x: _Ctx) -> dict[str, Any]:
    grain = x.recipe.get("grain") or []
    if not grain:
        keys = x.by_role("key")
        if keys:
            return _signal("grain", "ready", ", ".join(x.column_label(k["item"]) for k in keys[:3]))
        return _signal("grain", "partial", "No grain set for this opportunity; tick its key columns")
    key = x.target_table()
    have = {i["concept"]: i for i in x.columns if i.get("concept") in grain and
            (not key or i["item"].startswith(key + "#"))}
    if not have:
        have = {i["concept"]: i for i in x.columns if i.get("concept") in grain}
    labels = {cid: (R.concept(x.recipe, cid) or {}).get("label", cid) for cid in grain}
    found = [x.column_label(have[c]["item"]) for c in grain if c in have]
    missing = [labels[c] for c in grain if c not in have]
    if not missing:
        return _signal("grain", "ready", "One row per " + ", ".join(found))
    if found:
        return _signal("grain", "partial", f"Found {', '.join(found)}; missing {', '.join(missing)}")
    return _signal("grain", "missing", "Missing " + ", ".join(missing))


def _features(x: _Ctx) -> dict[str, Any]:
    if x.recipe.get("kind") == "agent":
        if not x.keys:
            return _signal("features", "missing", "No tables yet")
        described = sum(1 for k in x.keys if (x.info.get(k) or {}).get("purpose") or
                        x.cat.conn.execute("SELECT COALESCE(description, '') FROM assets WHERE key = ?",
                                           (k,)).fetchone()[0])
        share = described / len(x.keys)
        state = "ready" if share >= 0.7 else "partial" if share >= 0.3 else "missing"
        return _signal("features", state, f"{share:.0%} of its tables are described; an agent is only as good as the "
                                          "meanings it is given (the Copilot pack exports them)")
    key = x.target_table()
    if not key:
        return _signal("features", "missing", "No target table yet")
    others = [t["item"] for t in x.tables if t["item"] != key]
    from .relationships import joins_for
    joined = {r["dst_key"] if r["src_key"] == key else r["src_key"] for r in joins_for(x.cat, key)}
    mine = _key_columns_of(x.cat, [key])
    linked = []
    for k in others:
        if k in joined:
            linked.append(f"{x.name(k)} (joins in your SQL)")
        elif mine & _key_columns_of(x.cat, [k]):
            linked.append(f"{x.name(k)} (shares {', '.join(sorted(mine & _key_columns_of(x.cat, [k]))[:2])})")
    feats = x.by_role("feature")
    if len(linked) >= 2:
        return _signal("features", "ready", f"{len(linked)} tables join: " + "; ".join(linked[:3]))
    if linked or feats:
        text = "; ".join(linked) if linked else f"{len(feats)} feature column(s) in the target table only"
        return _signal("features", "partial", text + ". More tables that join on the same keys would help")
    return _signal("features", "missing", "No other tables join to the target table yet")


def _freshness(x: _Ctx) -> dict[str, Any]:
    key = x.target_table()
    if not key:
        return _signal("freshness", "missing", "No target table yet")
    a = x.assets.get(key)
    info = x.info.get(key) or {}
    if not a or a["status"] != "active":
        return _signal("freshness", "missing", f"{x.name(key)} was not found in the last scan")
    if info.get("refresh"):
        return _signal("freshness", "ready", f"Refreshed {info['refresh']} (from its page)")
    rows = [r[0] for r in x.cat.conn.execute("SELECT rows FROM row_counts WHERE asset_key = ? AND at >= ? ORDER BY at",
                                             (key, (datetime.now() - timedelta(days=31)).isoformat()))]
    if len(rows) >= 2 and rows[-1] != rows[0]:
        return _signal("freshness", "ready", f"Row count moved in the last month ({rows[0]:,} to {rows[-1]:,})")
    try:
        seen = datetime.fromisoformat(a["last_seen"]).replace(tzinfo=None)
        recent = datetime.now() - seen < timedelta(days=31)
    except (TypeError, ValueError):
        recent = False
    if recent:
        return _signal("freshness", "partial", "Seen in a recent scan; set how often it refreshes on its page")
    return _signal("freshness", "missing", f"Not scanned for over a month (last seen {str(a['last_seen'])[:10]})")


def _pain(x: _Ctx, related: list[dict[str, Any]]) -> dict[str, Any]:
    ticked = [m for m in related if m["ticked"]]
    if ticked:
        return _signal("pain", "ready", "; ".join(f"{m['measure']} in {m['report']}" for m in ticked[:3]))
    if related:
        return _signal("pain", "partial", f"{len(related)} measure(s) may show it; tick the related ones below")
    return _signal("pain", "missing", "No report measures this yet")


def evaluate(cat: Catalogue, uid: str) -> dict[str, Any] | None:
    """Everything the opportunity's page shows: the six signals, score, band, related measures, baseline,
    leakage warnings, target quality, gaps and feature ideas."""
    o = get(cat, uid)
    if not o:
        return None
    x = _Ctx(cat, o)
    related = related_measures(cat, uid, x)
    need = min_months(cat)
    signals = [_target(x), _history(x, need), _grain(x), _features(x), _freshness(x), _pain(x, related)]
    for s in signals:
        c = o["checks"].get(s["id"]) or {}
        s["confirmed"] = bool(c.get("confirmed"))
        s["note"] = c.get("note") or ""
        if c.get("override") in STATES:
            s["state"] = c["override"]
            s["overridden"] = True
    score = _score(signals, weights(cat))
    return {"opp": o, "recipe": x.recipe, "signals": signals, "score": score, "band": band(o, score),
            "tables": _table_rows(x), "columns": _column_rows(x), "related": related,
            "baseline": baseline(cat, x, related), "leakage": leakage(cat, x), "quality": target_quality(cat, x),
            "gaps": gaps(cat, x), "projects": projects(cat, uid), "splits": splits_of(cat, uid),
            "parent": get(cat, o["parent"]) if o.get("parent") else None}


def _score(signals: list[dict[str, Any]], w: dict[str, float]) -> int:
    total = sum(w.get(s["id"], 1) for s in signals) or 1
    got = sum(w.get(s["id"], 1) * STATE_VALUE[s["state"]] for s in signals)
    return round(100 * got / total)


def band(o: dict[str, Any], score: int) -> str:
    if o.get("status") == "live":
        return "live"
    return "ready" if score >= READY_AT else "partial" if score >= PARTIAL_AT else "notyet"


def overview(cat: Catalogue) -> list[dict[str, Any]]:
    """One row per opportunity for the list page: title, score, band, status, hint, projects, parent."""
    out = []
    for o in list_opps(cat):
        e = evaluate(cat, o["uid"])
        if not e:
            continue
        weakest = min(e["signals"], key=lambda s: (STATE_VALUE[s["state"]], -weights(cat).get(s["id"], 1)))
        hint = (f"{weakest['name']}: {weakest['evidence']}" if weakest["state"] != "ready"
                else "All six signals found")
        out.append({"uid": o["uid"], "title": title(o), "name": o["name"], "variant": o.get("variant") or "",
                    "parent": o.get("parent"), "score": e["score"], "band": e["band"], "status": o["status"],
                    "hint": hint, "projects": [p["name"] for p in e["projects"]], "recipe": o.get("recipe"),
                    "example": bool(e["recipe"].get("example")), "tool": o.get("tool") or "",
                    "value": o.get("value"), "effort": o.get("effort"),
                    "tables": len(e["tables"]), "auto": o["auto"]})
    return out


def ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best score first, each split straight after the opportunity it came from."""
    tops = sorted([r for r in rows if not r["parent"] or r["parent"] not in {x["uid"] for x in rows}],
                  key=lambda r: (-r["score"], r["title"].lower()))
    out = []
    for t in tops:
        out.append(t)
        out.extend(sorted([r for r in rows if r["parent"] == t["uid"]], key=lambda r: r["variant"]))
    return out


def _table_rows(x: _Ctx) -> list[dict[str, Any]]:
    from .tabletypes import LABELS, table_type
    rows = []
    for i in x.ordered(x.tables):
        k = i["item"]
        a = x.assets.get(k) or {}
        attrs = json.loads(a.get("attrs") or "{}") if a else {}
        cols = x.date_columns(k)
        spans = [s for s in (_span(x.profiles.get((k, c))) for c in cols) if s]
        hist = ""
        if spans:
            a_, b_ = min(s[0] for s in spans), max(s[1] for s in spans)
            hist = f"{_months(a_, b_) / 12:.1f} yrs"
        elif not cols:
            hist = "today only"
        rows.append({"key": k, "name": x.name(k), "rows": attrs.get("row_count"), "history": hist,
                     "type": LABELS.get((x.info.get(k) or {}).get("table_type") or table_type(x.cat, k), ""),
                     "role": i.get("role") or "", "state": i["state"], "why": i.get("why") or ""})
    return rows


def _column_rows(x: _Ctx) -> list[dict[str, Any]]:
    return [{"item": i["item"], "column": x.column_label(i["item"]), "role": i.get("role") or "", "state": i["state"],
             "why": i.get("why") or ""} for i in x.ordered(x.columns)]


# ---- related Power BI measures ----------------------------------------------------------------------------------

def _measure_name(cat: Catalogue, key: str) -> str:
    r = cat.conn.execute("SELECT name FROM assets WHERE key = ?", (key,)).fetchone()
    return r[0] if r else key


def complexity(expression: str, pages: int = 0) -> tuple[str, str]:
    """(Complex | Medium | Simple, reason) for a DAX expression."""
    expr = expression or ""
    funcs = DAX_FUNCS.findall(expr)
    lines = len([ln for ln in expr.splitlines() if ln.strip()]) or (1 if expr.strip() else 0)
    refs = len(re.findall(r"\[[^\]]+\]", expr))
    filters = sum(1 for f in funcs if f.upper() in ("FILTER", "KEEPFILTERS", "ALL", "ALLEXCEPT", "ALLSELECTED"))
    points = len(funcs) + lines // 5 + refs // 3 + pages // 2
    level = "Complex" if points >= 6 else "Medium" if points >= 3 else "Simple"
    names = sorted({f.upper() for f in funcs})
    bits = [", ".join(names[:4]) if names else "", f"{filters} filter(s)" if filters else "",
            f"{lines} line(s)", f"{refs} reference(s)" if refs else "", f"on {pages} page(s)" if pages else ""]
    return level, " · ".join(b for b in bits if b)


def related_measures(cat: Catalogue, uid: str, x: _Ctx | None = None) -> list[dict[str, Any]]:
    """Power BI measures that may show this opportunity's problem: in reports that read its tables, or named
    like the recipe's measures. Each with complexity, reason and whether you ticked it as related."""
    from .textclean import as_text
    x = x or _Ctx(cat, get(cat, uid) or {"uid": uid})
    decided = {m["item"]: m for m in items(cat, uid, include_rejected=True) if m["kind"] == "measure"}
    keys = set(x.keys)
    reports_reading: dict[str, list[str]] = {}
    if keys:
        marks = ", ".join("?" * len(keys))
        for r in cat.conn.execute(f"SELECT src_key, dst_key FROM links WHERE dst_key IN ({marks})", sorted(keys)):
            reports_reading.setdefault(r[0], []).append(r[1])
    words = [w.lower() for w in x.recipe.get("measure_words", []) if w]
    rx = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.I) if words else None
    from .pathbits import file_name
    reports = {r[0]: file_name(r[1], "report") for r in cat.conn.execute(
        "SELECT key, name FROM assets WHERE kind IN ('report', 'workbook') AND status = 'active'")}
    out = []
    for r in cat.conn.execute("SELECT key, name, parent, attrs FROM assets WHERE kind = 'measure' AND "
                              "status = 'active'"):
        if r["parent"] not in reports:
            continue
        a = json.loads(r["attrs"] or "{}")
        name = (as_text(a.get("name")) or r["name"]).strip()
        expr = as_text(a.get("expression"))
        reads = reports_reading.get(r["parent"], [])
        word = rx.search(name) if rx else None
        if r["key"] not in decided and not reads and not word:
            continue
        mine = decided.get(r["key"])
        if mine and mine["state"] == "rejected":
            continue
        level, why = complexity(expr, len(a.get("used_on") or []))
        found = []
        if reads:
            found.append("its report reads " + ", ".join(x.name(k) for k in reads[:2]))
        if word:
            found.append(f"named like “{word.group(0)}”")
        out.append({"key": r["key"], "measure": name, "report": reports[r["parent"]], "report_key": r["parent"],
                    "dax": expr.strip(), "level": level, "detail": why, "found": "; ".join(found) or "you added it",
                    "ticked": bool(mine and mine["state"] == "confirmed"),
                    "target": bool(mine and mine.get("role") == "target"),
                    "score": (2 if mine else 0) + (2 if reads else 0) + (1 if word else 0) +
                             {"Complex": 1.5, "Medium": 1, "Simple": 0}[level]})
    out.sort(key=lambda m: (-m["score"], m["measure"].lower()))
    return out[:MAX_MEASURES]


def tick_measure(cat: Catalogue, uid: str, key: str, related: bool) -> None:
    if related:
        old = cat.conn.execute("SELECT role FROM ml_items WHERE opp_uid = ? AND item = ?", (uid, key)).fetchone()
        set_item(cat, uid, key, kind="measure", role=(old[0] if old and old[0] == "target" else "related"),
                 state="confirmed")
    else:
        with cat.conn:
            cat.conn.execute("DELETE FROM ml_items WHERE opp_uid = ? AND item = ?", (uid, key))
        cat.changed()


def use_as_target(cat: Catalogue, uid: str, key: str) -> str:
    """Mark a measure as the opportunity's target and return SQL to copy: a SELECT that you can save as a view
    yourself. pdcat never runs it or creates anything on your server."""
    set_item(cat, uid, key, kind="measure", role="target", state="confirmed")
    return target_sql(cat, uid, key)


def target_sql(cat: Catalogue, uid: str, key: str) -> str:
    from .textclean import as_text
    a = cat.get_asset(key) or {"name": key, "attrs": {}, "parent": None}
    rep = cat.get_asset(a.get("parent") or "") or {"name": "the report"}
    expr = as_text(a["attrs"].get("expression")).strip()
    o = get(cat, uid) or {"uid": uid}
    x = _Ctx(cat, o)
    table = x.target_table()
    grain = [i["item"].split("#", 1)[1] for i in x.by_role("key") if not table or i["item"].startswith(table + "#")]
    dates = [i["item"].split("#", 1)[1] for i in x.by_role("date") if not table or i["item"].startswith(table + "#")]
    group = [*grain[:3], *dates[:1]]
    tname = (x.assets.get(table) or {}).get("name", "<target table>") if table else "<target table>"
    dax = "\n".join(f"--   {ln}" for ln in expr.splitlines()) or "--   (no DAX stored)"
    cols = ",\n       ".join(group) if group else "<key columns>"
    return (f"-- Read-only: a SELECT you can save as a view yourself. pdcat never runs it or creates anything.\n"
            f"-- It rebuilds the logic of [{a['name']}] from {rep['name']} as one row per "
            f"{', '.join(group) or 'key'}.\n-- The measure's DAX, to translate into the expression below:\n{dax}\n"
            f"SELECT {cols},\n       /* translate the DAX above */ NULL AS target\nFROM {tname}\n"
            + (f"GROUP BY {', '.join(group)};" if group else ";"))


# ---- baseline, leakage, target quality, gaps --------------------------------------------------------------------

def baseline(cat: Catalogue, x: _Ctx, related: list[dict[str, Any]]) -> dict[str, Any]:
    """Today's answer that a model has to beat: a plan-versus-actual pair in the data (with a read-only query to
    measure it), a measure that already reports it, and the figure you typed in."""
    out: dict[str, Any] = {"typed": x.o.get("baseline") or "", "pair": None, "sql": "", "measure": None}
    pa = x.recipe.get("plan_actual")
    if pa:
        found = _baseline_sql(x, pa)
        if found:
            out["pair"], out["sql"] = found
    target_measures = [m for m in related if m["target"]] or [m for m in related if m["ticked"]]
    if target_measures:
        m = target_measures[0]
        out["measure"] = {"measure": m["measure"], "report": m["report"]}
    return out


def _baseline_sql(x: _Ctx, pa: dict[str, Any]) -> tuple[dict[str, str], str] | None:
    """A read-only query for the plan's error, from columns in one table or in two tables that join."""
    where: dict[str, list[tuple[str, str]]] = {}
    for i in x.ordered(x.columns):
        if i.get("concept"):
            k, c = i["item"].split("#", 1)
            where.setdefault(i["concept"], []).append((k, c))
    need = [pa["plan"], pa["actual"], *([pa["start"]] if pa.get("start") else [])]
    if not all(n in where for n in need):
        return None
    from .relationships import joins_for
    tname = {k: (x.assets.get(k) or {}).get("name", k) for k in x.keys}
    actual_k, actual = where[pa["actual"]][0]
    # each needed column: from the actual's table if it has it, else from a table that joins to it
    picked: dict[str, tuple[str, str]] = {pa["actual"]: (actual_k, actual)}
    joins: dict[str, str] = {}
    for n in need[:1] + need[2:]:
        same = [kc for kc in where[n] if kc[0] == actual_k]
        if same:
            picked[n] = same[0]
            continue
        for k, c in where[n]:
            rel = next((r for r in joins_for(x.cat, actual_k) if k in (r["src_key"], r["dst_key"])), None)
            if rel:
                mine = rel["src_columns"] if rel["src_key"] == actual_k else rel["dst_columns"]
                theirs = rel["dst_columns"] if rel["src_key"] == actual_k else rel["src_columns"]
                joins[k] = " AND ".join(f"a.{m.strip()} = p.{t.strip()}" for m, t in
                                        zip(mine.split(","), theirs.split(","), strict=False))
                picked[n] = (k, c)
                break
        if n not in picked:
            return None
    if len({k for k, _ in picked.values()} - {actual_k}) > 1:
        return None

    def ref(n: str) -> str:
        k, c = picked[n]
        return f"a.{c}" if k == actual_k else f"p.{c}"
    plan, act = ref(pa["plan"]), ref(pa["actual"])
    if pa.get("start"):
        expr = f"ABS(DATEDIFF(day, {ref(pa['start'])}, {act}) - {plan})"
        what = (f"planned {_label(x.recipe, pa['plan'])} ({picked[pa['plan']][1]}) against the actual days from "
                f"{picked[pa['start']][1]} to {actual}")
    elif pa.get("dates"):
        expr = f"DATEDIFF(day, {plan}, {act})"
        what = f"{actual} against {picked[pa['plan']][1]} (days late)"
    else:
        expr = f"ABS({act} - {plan})"
        what = f"{actual} against {picked[pa['plan']][1]}"
    other = next(iter(joins), None)
    sql = ("-- Read-only: today's error of the plan, the bar a model must beat. pdcat did not run it.\n"
           f"SELECT AVG(CAST({expr} AS float)) AS average_error, COUNT(*) AS rows_measured\n"
           f"FROM {tname.get(actual_k, actual_k)} AS a\n"
           + (f"JOIN {tname.get(other, other)} AS p ON {joins[other]}\n" if other else "")
           + f"WHERE {act} IS NOT NULL AND {plan} IS NOT NULL;")
    tables = x.name(actual_k) + (f" and {x.name(other)}" if other else "")
    return {"table": tables, "what": what, "unit": pa.get("unit", "")}, sql


def leakage(cat: Catalogue, x: _Ctx) -> list[str]:
    """Feature columns that may only be known after the event being predicted."""
    words = [squash(w) for w in x.recipe.get("leak_words", []) if w]
    out = []
    for i in x.columns:
        if i.get("role") not in ("feature", "date", "key"):
            continue
        col = i["item"].split("#", 1)[1]
        n = squash(col)
        hit = next((w for w in words if w and w in n), None)
        if hit or LEAK.search(col):
            out.append(f"{x.column_label(i['item'])} may only be known after the event (“{hit or LEAK.search(col)[0]}” "
                       "in its name). Use it only if it is known when the prediction is made.")
    for r in cat.conn.execute("SELECT name, sources, leakage_reason FROM features WHERE known_at_prediction = 'no' "
                              "AND state != 'rejected'"):
        srcs = json.loads(r[1] or "[]")
        if any(s in {i["item"] for i in x.columns} for s in srcs):
            out.append(f"ML feature {r[0]} is marked as not known at prediction time" +
                       (f": {r[2]}" if r[2] else ""))
    return out


def target_quality(cat: Catalogue, x: _Ctx) -> list[dict[str, str]]:
    """Blanks, a single value, outliers and recent changes on the target column, from its profile."""
    out = []
    for i in x.by_role("target")[:2]:
        k, c = i["item"].split("#", 1)
        p = x.profiles.get((k, c))
        label = x.column_label(i["item"])
        if not p:
            out.append({"level": "info", "text": f"{label} has no profile yet; profile it to check its values"})
            continue
        rows, nulls = p.get("row_count") or 0, p.get("null_count") or 0
        if rows and nulls / rows > 0.05:
            out.append({"level": "warn", "text": f"{label} is blank in {nulls / rows:.0%} of rows"})
        if p.get("distinct_count") == 1:
            out.append({"level": "warn", "text": f"{label} has one value only"})
        try:
            mean, top = float(p.get("mean_value")), float(p.get("max_value"))
            if mean > 0 and top > mean * 20:
                out.append({"level": "warn", "text": f"{label} has outliers: the largest value ({top:,.0f}) is over "
                                                     f"20 times the average ({mean:,.1f})"})
        except (TypeError, ValueError):
            pass
        try:
            from .growth import profile_drift
            drift = [d for d in profile_drift(cat, keys={k}) if d.get("column") == c]
            if drift:
                out.append({"level": "warn", "text": f"{label} changed recently: {drift[0]['detail']}"})
        except Exception:  # drift needs profile history; older catalogues have none
            pass
        if not any(o["text"].startswith(label) for o in out):
            out.append({"level": "ok", "text": f"{label}: {rows:,} rows, {nulls:,} blank, "
                                               f"{p.get('distinct_count') or 0:,} distinct values"})
    return out


def gaps(cat: Catalogue, x: _Ctx) -> list[str]:
    """Tables that keep today only, and where their history might be found."""
    from . import trackers
    from .tabletypes import all_types
    types = all_types(cat)
    out = []
    snapshots = [k for k, t in types.items() if t == "snapshot"]
    for t in x.tables:
        k = t["item"]
        if t.get("role") not in ("target", "feature"):
            continue
        if x.date_columns(k) and types.get(k) != "dimension":
            continue
        where = []
        tr = trackers.trackers_of(cat, k)
        if tr:
            where.append(f"change tracker {tr[0]['name']} holds its changes")
        mine = _key_columns_of(cat, [k])
        snaps = [s for s in snapshots if s != k and mine and mine & _key_columns_of(cat, [s])][:2]
        if snaps:
            from .pathbits import file_name
            names = [file_name(r[0], r[1]) for r in (cat.conn.execute("SELECT name, kind FROM assets WHERE key = ?",
                                                                      (s,)).fetchone() for s in snaps) if r]
            where.append("snapshot table(s) " + ", ".join(names) + " share its keys")
        out.append(f"{x.name(k)} keeps today only" + (": " + "; ".join(where) if where else
                                                      ". Look for a snapshot table, a change tracker or dated copies "
                                                      "in an archive folder."))
    return out


def shared_tables(cat: Catalogue) -> list[dict[str, Any]]:
    """Tables used by two or more opportunities, so their data prep is built once."""
    on = {o["uid"]: title(o) for o in list_opps(cat)}
    by: dict[str, set[str]] = {}
    for r in cat.conn.execute("SELECT opp_uid, item FROM ml_items WHERE kind = 'table' AND state != 'rejected'"):
        if r[0] in on:
            by.setdefault(r[1], set()).add(on[r[0]])
    from .pathbits import file_name
    names = {r[0]: file_name(r[1], r[2]) for r in cat.conn.execute("SELECT key, name, kind FROM assets")}
    out = [{"key": k, "table": names.get(k, k), "opportunities": sorted(v), "count": len(v)}
           for k, v in by.items() if len(v) >= 2]
    return sorted(out, key=lambda r: (-r["count"], r["table"].lower()))


def feature_ideas(cat: Catalogue, uid: str, limit: int = 10) -> list[dict[str, Any]]:
    """Tables that join to the target table, or share its keys, that no script of this opportunity (or of its
    business projects) reads yet. Most useful for a live model."""
    o = get(cat, uid)
    if not o:
        return []
    x = _Ctx(cat, o)
    key = x.target_table()
    if not key:
        return []
    scripts = {i["item"] for i in x.items if i["kind"] == "other"}
    for p in projects(cat, uid):
        scripts |= {r[0] for r in cat.conn.execute(
            "SELECT m.asset_key FROM bproject_members m JOIN assets a ON a.key = m.asset_key WHERE m.project_uid = ? "
            "AND m.state = 'confirmed' AND a.kind IN ('script', 'notebook', 'sql_script', 'project')", (p["uid"],))}
    scripts |= {r[0] for r in cat.conn.execute(
        "SELECT l.src_key FROM links l JOIN assets a ON a.key = l.src_key WHERE l.dst_key = ? AND l.relation = 'reads' "
        "AND a.kind IN ('script', 'notebook', 'sql_script')", (key,))}
    read = {r[0] for s in scripts for r in cat.conn.execute(
        "SELECT dst_key FROM links WHERE (src_key = ? OR src_key IN (SELECT key FROM assets WHERE parent = ?)) "
        "AND dst_key IS NOT NULL", (s, s))}
    have = {i["item"] for i in x.items}
    from .relationships import joins_for
    cands: dict[str, str] = {}
    for r in joins_for(cat, key):
        other = r["dst_key"] if r["src_key"] == key else r["src_key"]
        cands.setdefault(other, "joins in your SQL")
    mine = _key_columns_of(cat, [key])
    if mine:
        from .sapterms import match_column
        for r in cat.conn.execute("SELECT a.key, c.name FROM columns c JOIN assets a ON a.key = c.asset_key WHERE "
                                  f"a.status = 'active' AND a.kind IN ({', '.join('?' * len(TABLE_KINDS))})",
                                  TABLE_KINDS):
            if r[0] == key or r[0] in cands:
                continue
            hit = match_column(r[1])
            if hit and hit[0] in mine:
                cands[r[0]] = f"shares {hit[0]}"
            if len(cands) > 200:
                break
    from .pathbits import file_name
    out = []
    for k, why in cands.items():
        if k in read or k in have or k == key:
            continue
        a = cat.conn.execute("SELECT name, kind FROM assets WHERE key = ? AND status = 'active'", (k,)).fetchone()
        if a:
            out.append({"key": k, "table": file_name(a[0], a[1]), "why": why})
    joins_first = sorted(out, key=lambda r: (not r["why"].startswith("joins"), r["table"].lower()))
    return joins_first[:limit]


# ---- export pack ------------------------------------------------------------------------------------------------

def export_pack(cat: Catalogue, uid: str) -> bytes:
    """A zip for a data scientist: what the opportunity is, its readiness checks, tables and columns (with SAP
    names, types and profiles), related measures with DAX, the baseline, gaps, a starter query and notes."""
    e = evaluate(cat, uid)
    if not e:
        raise ValueError("No such opportunity.")
    o = e["opp"]
    x = _Ctx(cat, o)
    buf = io.BytesIO()

    def table_csv(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        s = io.StringIO()
        w = csv.DictWriter(s, fieldnames=list(rows[0]), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        return "﻿" + s.getvalue()

    dictionary = []
    for i in x.ordered(x.columns):
        k, c = i["item"].split("#", 1)
        p = x.profiles.get((k, c)) or {}
        dictionary.append({"table": x.name(k), "column": c, "sap_name": x.col_sap.get((k, c)) or "",
                           "shown_as": x.column_label(i["item"]), "role": i.get("role") or "", "state": i["state"],
                           "data_type": x.coltypes.get((k, c), ""), "rows": p.get("row_count") or "",
                           "blank": p.get("null_count") or "", "distinct": p.get("distinct_count") or "",
                           "min": p.get("min_value") or "", "max": p.get("max_value") or "",
                           "description": _col_desc(cat, k, c)})
    joins = []
    from .relationships import joins_for
    for k in x.keys:
        for r in joins_for(cat, k):
            if r["src_key"] in x.keys and r["dst_key"] in x.keys and r["src_key"] == k:
                joins.append({"from": x.name(r["src_key"]), "from_columns": r.get("src_columns") or "",
                              "to": x.name(r["dst_key"]), "to_columns": r.get("dst_columns") or "",
                              "state": r.get("state") or ""})
    lines = [f"# {title(o)}", "", o.get("predicts") or e["recipe"].get("predicts") or "", "",
             f"- Readiness: {e['score']} out of 100 ({BANDS[e['band']]})",
             f"- Status: {STATUS_LABELS.get(o['status'], o['status'])}",
             f"- Kind of tool: {R.TOOLS.get(o.get('tool') or '', ('', ''))[0]} ({o.get('method') or ''})",
             f"- Business projects: {', '.join(p['name'] for p in e['projects']) or 'none'}", "",
             "## Readiness checks", ""]
    for s in e["signals"]:
        lines.append(f"- **{s['name']}**: {STATE_LABELS[s['state']]}. {s['evidence']}"
                     + (" (confirmed)" if s["confirmed"] else ""))
    b = e["baseline"]
    lines += ["", "## Baseline to beat", ""]
    if b["typed"]:
        lines.append(f"- Typed in: {b['typed']}")
    if b["pair"]:
        lines.append(f"- Plan against actual in {b['pair']['table']}: {b['pair']['what']} (query in baseline.sql)")
    if b["measure"]:
        lines.append(f"- Reported today by {b['measure']['measure']} in {b['measure']['report']}")
    if not (b["typed"] or b["pair"] or b["measure"]):
        lines.append("- None found yet.")
    for head, rows in (("Leakage warnings", e["leakage"]), ("Gaps", e["gaps"]),
                       ("Target quality", [q["text"] for q in e["quality"]])):
        if rows:
            lines += ["", f"## {head}", "", *[f"- {r}" for r in rows]]
    if o.get("notes"):
        lines += ["", "## Notes", "", o["notes"]]
    lines += ["", "## Files", "", "- columns.csv: the columns, with SAP names, roles and profiles",
              "- tables.csv: candidate tables", "- joins.csv: how they join", "- measures.csv: related Power BI "
              "measures with DAX", "- starter.sql: a starting query at the chosen grain", "- baseline.sql: today's "
              "error of the plan, when a plan and actual pair was found", "",
              "Made by the personal data catalogue. All queries are read-only and were not run."]
    starter = _starter_sql(x)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", "\n".join(lines) + "\n")
        z.writestr("columns.csv", table_csv(dictionary))
        z.writestr("tables.csv", table_csv(e["tables"]))
        z.writestr("joins.csv", table_csv(joins))
        z.writestr("measures.csv", table_csv([{k: m[k] for k in ("measure", "report", "level", "detail", "found",
                                                                  "ticked", "target", "dax")} for m in e["related"]]))
        z.writestr("starter.sql", starter)
        if b["sql"]:
            z.writestr("baseline.sql", b["sql"] + "\n")
    return buf.getvalue()


def _col_desc(cat: Catalogue, key: str, column: str) -> str:
    from .curation import column_description
    try:
        return column_description(cat, key, column) or ""
    except Exception:
        return ""


def _starter_sql(x: _Ctx) -> str:
    key = x.target_table()
    if not key:
        return "-- No target table yet.\n"
    tname = (x.assets.get(key) or {}).get("name", key)
    cols = [i["item"].split("#", 1)[1] for i in x.ordered(x.columns) if i["item"].startswith(key + "#")
            and i.get("role") in ("key", "date", "target", "feature")]
    cols = list(dict.fromkeys(cols)) or ["*"]
    out = ["-- Read-only starting query for the target table. pdcat did not run it.",
           f"SELECT {', '.join(cols)}", f"FROM {tname} AS t"]
    from .relationships import joins_for
    n = 0
    for r in joins_for(x.cat, key):
        other = r["dst_key"] if r["src_key"] == key else r["src_key"]
        if other not in x.keys or n >= 3:
            continue
        n += 1
        mine = (r.get("src_columns") if r["src_key"] == key else r.get("dst_columns")) or ""
        theirs = (r.get("dst_columns") if r["src_key"] == key else r.get("src_columns")) or ""
        on = " AND ".join(f"t.{a.strip()} = j{n}.{b.strip()}" for a, b in zip(mine.split(","), theirs.split(","),
                                                                            strict=False) if a.strip() and b.strip())
        out.append(f"LEFT JOIN {(x.assets.get(other) or {}).get('name', other)} AS j{n} ON {on or '/* join columns */'}")
    return "\n".join(out) + ";\n"


# ---- after a scan -----------------------------------------------------------------------------------------------

def after_scan(cat: Catalogue) -> int:
    return refresh(cat)
