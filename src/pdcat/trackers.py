"""Change trackers: scanned files and tables that record changes made to another table.

A tracker is found by its name (a prefix you set, such as ``ChangesX_``, or words like changes, audit, history,
_CT) and by its columns. Two layouts are understood:

* **long**: one row per changed value, with a column naming the changed column and the value before and/or
  after (``PlantID, ColumnName, ValueBefore, ValueAfter, FieldName, Table, ChangeDate``; SAP CDPOS; audit logs).
  An empty value before means a new addition, an empty value after means a removal.
* **wide**: a copy of whole rows with a change date or operation (SQL Server CDC ``_CT`` tables, temporal
  history tables, snapshots). Rows are counted per day and operation.

Each tracker is linked to the table(s) it tracks: the name after the prefix, its Table column, a key column
both share (PlantID), and the columns it reports changes to. Links start as suggestions you confirm or reject.

"Read change summary" counts the changes per day, column and kind, and the keys each column changed for. Files
are read in chunks; after the first read only rows from the last change date read onwards are read again, so a
long file that keeps growing stays quick. Nothing is copied from the data except those counts and the keys.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import suggestions
from .scanprogress import Report, quiet
from .store import Catalogue, now

DEFAULT_PREFIXES = ["ChangesX_"]
KEY_CAP = 50_000          # keys kept per tracker for "keys affected"
CHUNK_ROWS = 200_000      # rows read from a file at a time
WIDE_COLUMN = "(rows)"    # the column name wide trackers count under

# Column jobs, by squashed name (lower case, no _ $ spaces or dashes).
ROLE_NAMES: dict[str, tuple[str, ...]] = {
    "date": ("changedate", "changedat", "changetime", "changedon", "changetimestamp", "modifieddate", "modifiedat",
             "modifiedon", "updatedat", "updatedate", "updatedon", "auditdate", "audittime", "logdate", "eventdate",
             "eventtime", "udate", "validfrom", "sysstarttime", "snapshotdate", "loaddate", "asofdate",
             "datechanged", "lastchanged", "timestamp"),
    "column": ("columnname", "column", "fname", "field", "attribute", "attributename", "changedcolumn",
               "columnchanged", "changedfield", "fieldchanged"),
    "label": ("fieldname", "fieldlabel", "fielddescription", "columnlabel", "friendlyname", "displayname"),
    "before": ("valuebefore", "oldvalue", "old", "valueold", "beforevalue", "previousvalue", "prevvalue",
               "fromvalue", "valold", "before"),
    "after": ("valueafter", "newvalue", "new", "valuenew", "aftervalue", "currentvalue", "tovalue", "valnew",
              "after"),
    "table": ("table", "tablename", "tabname", "objectname", "sourcetable", "trackedtable"),
    "operation": ("operation", "op", "action", "changetype", "chngind", "dmlaction", "rowstate", "changekind"),
    "user": ("changedby", "modifiedby", "updatedby", "username", "usnam", "user"),
    "key": ("tabkey", "objectid", "recordid", "rowkey", "primarykey", "keyvalue"),
}
NAME_WORDS = {"change", "changes", "changed", "audit", "audits", "history", "hist", "log", "cdc", "ct", "delta",
              "deltas", "snapshot", "snapshots", "cdhdr", "cdpos", "tracking", "tracker", "versions"}
_OPERATION_KIND = {"1": "removed", "2": "new", "3": "changed", "4": "changed", "d": "removed", "i": "new",
                   "u": "changed", "e": "removed", "delete": "removed", "insert": "new", "update": "changed",
                   "deleted": "removed", "inserted": "new", "updated": "changed"}


def squash(name: str) -> str:
    return re.sub(r"[\s_$\-.]+", "", str(name)).lower()


def prefixes(cat: Catalogue) -> list[str]:
    """The file and table name prefixes that mark a change tracker (Settings › Change trackers)."""
    got = cat.setting("tracker_prefixes", DEFAULT_PREFIXES)
    return [p for p in (got if isinstance(got, list) else str(got).split(",")) if str(p).strip()]


# ---- what a tracker looks like ------------------------------------------------------------------------

def shape_of(columns: list[str]) -> dict[str, Any]:
    """The job of each column: {"layout": "long"|"wide"|None, "date", "column", "label", "before", "after",
    "table", "operation", "user", "key"} (values are column names)."""
    by = {squash(c): c for c in columns}
    out: dict[str, Any] = {}
    for role, names in ROLE_NAMES.items():
        for n in names:
            if n in by and by[n] not in out.values():
                out[role] = by[n]
                break
    if "label" in out and "column" not in out:       # only a FieldName: that is the changed column
        out["column"] = out.pop("label")
    if "date" not in out:                             # a date-like name: ...Date / ...Time / ..._at
        for c in columns:
            s = squash(c)
            if c not in out.values() and re.search(r"(date|time|datetime|timestamp)$", s) and \
                    re.search(r"chang|modif|updat|audit|valid|snap|load|event|log", s):
                out["date"] = c
                break
    if "key" not in out:                              # the first other column named like an id or key
        used = set(out.values())
        for c in columns:
            if c not in used and re.search(r"(id|key|code|number|no|nr)$", squash(c)) and len(squash(c)) > 2:
                out["key"] = c
                break
    out["layout"] = "long" if "column" in out and ("before" in out or "after" in out) else \
        "wide" if ("date" in out or "operation" in out) else None
    return out


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def _stem(asset: dict[str, Any]) -> str:
    a = asset.get("attrs") or {}
    if asset["kind"] in ("table", "view"):
        return a.get("object") or asset["name"].split(".")[-1]
    name = Path(str(asset["name"]).replace("\\", "/")).name
    return name.rsplit(".", 1)[0] if "." in name else name


def tracked_name(cat: Catalogue, stem: str, pre: list[str] | None = None) -> tuple[str | None, str]:
    """(the tracked table's name from a tracker's own name, how it was found). ChangesX_TABLEPlant -> TABLEPlant;
    Plant_changes, PlantHistory, dbo_Plant_CT -> Plant."""
    for p in pre if pre is not None else prefixes(cat):
        if stem.lower().startswith(p.lower()) and len(stem) > len(p):
            return stem[len(p):].strip("_- "), f"the name after {p}"
    parts = re.split(r"(_|-|\s+|(?<=[a-z0-9])(?=[A-Z]))", stem)
    tokens = [t for t in parts if t and not re.fullmatch(r"_|-|\s+", t)]
    while tokens and tokens[-1].lower() in NAME_WORDS:
        tokens.pop()
    while tokens and tokens[0].lower() in NAME_WORDS:
        tokens.pop(0)
    if tokens and tokens[0].lower() == "dbo" and len(tokens) > 1:   # CDC capture instance: dbo_Plant_CT
        tokens.pop(0)
    rest = "_".join(tokens)
    if rest and rest.lower() != stem.lower():
        # keep the original joins (TABLEPlant_changes -> TABLEPlant, not TABLE_Plant)
        low = stem.lower()
        at = low.find(tokens[0].lower())
        end = low.rfind(tokens[-1].lower()) + len(tokens[-1])
        return stem[at:end].strip("_- "), "the name without the change words"
    return None, ""


def _is_candidate(stem: str, shape: dict[str, Any], pre: list[str]) -> str | None:
    """Why this looks like a tracker, or None."""
    low = stem.lower()
    for p in pre:
        if low.startswith(p.lower()):
            return f"named {p}…"
    words = set(_words(stem))
    if shape.get("layout") == "long" and ("before" in shape and "after" in shape or words & NAME_WORDS):
        return "a column for the changed column and the value before or after"
    if shape.get("layout") == "wide" and words & NAME_WORDS and ("operation" in shape or "date" in shape):
        return f"named like a change table ({', '.join(sorted(words & NAME_WORDS))}) with a change date or operation"
    return None


# ---- finding trackers (an after-scan step) ------------------------------------------------------------

def detect(cat: Catalogue, report: Report = quiet) -> int:
    """Find change trackers among scanned files and tables, and suggest the table(s) each one tracks.
    What you decided (a confirmed or rejected link, "not a tracker") is kept. Returns how many trackers there are."""
    pre = prefixes(cat)
    cols: dict[str, list[str]] = defaultdict(list)
    for r in cat.conn.execute("SELECT c.asset_key, c.name FROM columns c JOIN assets a ON a.key = c.asset_key "
                              "WHERE a.status = 'active' AND a.kind IN ('table', 'view', 'data_file') "
                              "ORDER BY c.asset_key, c.ordinal"):
        cols[r[0]].append(r[1])
    info = {r["tracker_key"]: dict(r) for r in cat.conn.execute("SELECT * FROM tracker_info")}
    decided = {r[0] for r in cat.conn.execute("SELECT DISTINCT tracker_key FROM trackers WHERE state = 'confirmed'")}
    found: dict[str, dict[str, Any]] = {}
    for r in cat.conn.execute("SELECT key, kind, name, source, attrs FROM assets WHERE status = 'active' AND kind IN "
                              "('table', 'view', 'data_file')"):
        if r["key"] not in cols:
            continue
        asset = {"key": r["key"], "kind": r["kind"], "name": r["name"], "attrs": json.loads(r["attrs"] or "{}")}
        stem = _stem(asset)
        shape = shape_of(cols[r["key"]])
        why = _is_candidate(stem, shape, pre)
        if why or r["key"] in decided:
            shape["why"] = why or "you linked it"
            shape["stem"] = stem
            found[r["key"]] = {"asset": asset, "shape": shape}
    index = cat.table_index()
    files = _file_stems(cat)
    ts = now()
    with cat.conn:
        for key, f in found.items():
            old = info.get(key) or {}
            if old.get("not_tracker"):
                continue
            shape = {**_loads(old.get("shape")), **f["shape"]}
            cat.conn.execute("INSERT INTO tracker_info (tracker_key, shape) VALUES (?, ?) ON CONFLICT(tracker_key) "
                             "DO UPDATE SET shape = excluded.shape", (key, json.dumps(shape)))
            _suggest_links(cat, key, f["asset"], shape, index, files, ts)
        gone = [k for k in info if k not in found and not info[k].get("not_tracker") and k not in decided]
        for k in gone:
            cat.conn.execute("DELETE FROM tracker_info WHERE tracker_key = ?", (k,))
            cat.conn.execute("DELETE FROM trackers WHERE tracker_key = ? AND state = 'suggested'", (k,))
    _label_suggestions(cat)
    return len([k for k in found if not (info.get(k) or {}).get("not_tracker")])


def _file_stems(cat: Catalogue) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for r in cat.conn.execute("SELECT key, name FROM assets WHERE status = 'active' AND kind IN ('data_file', "
                              "'file_series')"):
        name = Path(str(r["name"]).replace("\\", "/")).name
        out[(name.rsplit(".", 1)[0] if "." in name else name).lower()].append(r["key"])
    return out


def _suggest_links(cat: Catalogue, key: str, asset: dict, shape: dict, index: dict, files: dict, ts: str) -> None:
    stem = shape.get("stem") or _stem(asset)
    target, how = tracked_name(cat, stem)
    names: dict[str, list[str]] = defaultdict(list)       # name as the tracker writes it -> reasons
    if target:
        names[target].append(how)
    for v in shape.get("table_values") or []:              # from its Table column (after a read or a profile)
        names[str(v)].append(f"its {shape.get('table', 'Table')} column says {v}")
    if not names and shape.get("table"):
        for v in _top_values(cat, key, shape["table"]):
            names[v].append(f"its {shape['table']} column says {v}")
    changed_cols = {c.lower() for c in (shape.get("labels") or {})}
    cands: dict[tuple[str, str], dict[str, Any]] = {}
    for name, reasons in names.items():
        hits = [c["key"] for c in cat.match_table(name.lower(), index=index)] + files.get(name.lower(), [])
        for tk in hits:
            if tk == key:
                continue
            d = cands.setdefault((tk, name), {"score": 0, "reasons": []})
            d["score"] += 2 if any("after" in r or "column says" in r for r in reasons) else 1
            d["reasons"] += reasons
    for (tk, name), d in cands.items():
        tcols = {c["name"].lower() for c in cat.columns(tk)}
        k = shape.get("key")
        if k and k.lower() in tcols:
            d["score"] += 1
            d["reasons"].append(f"both have {k}")
        if changed_cols:
            inside = len(changed_cols & tcols)
            if inside:
                d["score"] += 1
                d["reasons"].append(f"{inside} of the {len(changed_cols)} columns it reports are in the table")
        if shape.get("layout") == "wide" and tcols:
            mine = {c.lower() for c in cat.columns(key) if c["name"].lower() not in tcols}
            share = len(tcols & {c["name"].lower() for c in cat.columns(key)}) / len(tcols)
            if share >= 0.5:
                d["score"] += 2
                d["reasons"].append(f"has {share:.0%} of the table's columns" + (" plus change columns" if mine else ""))
    decided = {r[0]: r[1] for r in cat.conn.execute("SELECT table_key, state FROM trackers WHERE tracker_key = ?",
                                                     (key,))}
    cat.conn.execute("DELETE FROM trackers WHERE tracker_key = ? AND state = 'suggested'", (key,))
    best = sorted(cands.items(), key=lambda kv: -kv[1]["score"])[:5]
    for (tk, name), d in best:
        if decided.get(tk) in ("confirmed", "rejected"):
            cat.conn.execute("UPDATE trackers SET name_value = COALESCE(name_value, ?) WHERE tracker_key = ? AND "
                             "table_key = ?", (name, key, tk))
            continue
        band = "likely" if d["score"] >= 3 else "check" if d["score"] == 2 else "unlikely"
        cat.conn.execute("INSERT OR REPLACE INTO trackers (tracker_key, table_key, state, band, reason, name_value, "
                         "updated_at) VALUES (?, ?, 'suggested', ?, ?, ?, ?)",
                         (key, tk, band, "; ".join(dict.fromkeys(d["reasons"])), name, ts))


def _top_values(cat: Catalogue, key: str, column: str) -> list[str]:
    row = cat.conn.execute("SELECT top_values FROM profiles WHERE asset_key = ? AND column_name = ?",
                           (key, column)).fetchone()
    vals = _loads(row[0], []) if row else []
    return [str(v[0]) for v in vals[:5] if isinstance(v, (list, tuple)) and v and v[0] not in (None, "")]


def _loads(text: Any, default: Any = None) -> Any:
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text) if text else ({} if default is None else default)
    except (TypeError, ValueError):
        return {} if default is None else default


# ---- your decisions -----------------------------------------------------------------------------------

def set_link(cat: Catalogue, tracker_key: str, table_key: str, state: str, name_value: str | None = None) -> None:
    """Confirm or reject a suggested link, or link a tracker to a table by hand (state "confirmed")."""
    if state not in ("confirmed", "rejected", "suggested"):
        raise ValueError(state)
    if name_value is None:
        row = cat.conn.execute("SELECT name_value FROM trackers WHERE tracker_key = ? AND table_key = ?",
                               (tracker_key, table_key)).fetchone()
        name_value = row[0] if row else None
    if name_value is None:
        a = cat.get_asset(table_key)
        name_value = (a["attrs"].get("object") or a["name"]) if a else table_key
        tracked = {r[0] for r in cat.conn.execute("SELECT DISTINCT table_name FROM tracker_counts WHERE "
                                                  "tracker_key = ?", (tracker_key,))}
        if tracked == {""}:     # it has no table column: every change belongs to this table
            name_value = ""
    with cat.conn:
        cat.conn.execute("INSERT INTO trackers (tracker_key, table_key, state, band, reason, name_value, updated_at) "
                         "VALUES (?, ?, ?, NULL, 'you linked it', ?, ?) ON CONFLICT(tracker_key, table_key) DO UPDATE "
                         "SET state = excluded.state, updated_at = excluded.updated_at", (tracker_key, table_key,
                                                                                         state, name_value, now()))
        cat.conn.execute("INSERT OR IGNORE INTO tracker_info (tracker_key) VALUES (?)", (tracker_key,))
    cat.changed()


def set_not_tracker(cat: Catalogue, key: str, value: bool = True) -> None:
    with cat.conn:
        cat.conn.execute("INSERT INTO tracker_info (tracker_key, not_tracker) VALUES (?, ?) ON CONFLICT(tracker_key) "
                         "DO UPDATE SET not_tracker = excluded.not_tracker", (key, int(value)))
        if value:
            cat.conn.execute("DELETE FROM trackers WHERE tracker_key = ? AND state = 'suggested'", (key,))
    cat.changed()


def set_watch(cat: Catalogue, key: str, columns: list[str] | None) -> None:
    """The columns you watch on this tracker (None goes back to the default: the two that change most)."""
    with cat.conn:
        cat.conn.execute("INSERT INTO tracker_info (tracker_key, watch) VALUES (?, ?) ON CONFLICT(tracker_key) "
                         "DO UPDATE SET watch = excluded.watch",
                         (key, None if columns is None else json.dumps(sorted(set(columns)))))
    cat.changed()


def watched(cat: Catalogue, key: str) -> list[str]:
    row = cat.conn.execute("SELECT watch FROM tracker_info WHERE tracker_key = ?", (key,)).fetchone()
    if row and row[0] is not None:
        return _loads(row[0], [])
    top = cat.conn.execute("SELECT column_name, SUM(n) n FROM tracker_counts WHERE tracker_key = ? GROUP BY "
                           "column_name ORDER BY n DESC LIMIT 2", (key,)).fetchall()
    return [r[0] for r in top]


def all_watched(cat: Catalogue) -> dict[str, set[str]]:
    """{tracker key: watched columns} for every tracker that has counts."""
    out: dict[str, set[str]] = {}
    explicit = {r[0]: _loads(r[1], []) for r in cat.conn.execute(
        "SELECT tracker_key, watch FROM tracker_info WHERE watch IS NOT NULL AND not_tracker = 0")}
    totals: dict[str, Counter] = defaultdict(Counter)
    for r in cat.conn.execute("SELECT tracker_key, column_name, SUM(n) FROM tracker_counts GROUP BY tracker_key, "
                              "column_name"):
        totals[r[0]][r[1]] = r[2]
    for k, c in totals.items():
        out[k] = set(explicit[k]) if k in explicit else {n for n, _ in c.most_common(2)}
    return out


# ---- listing ------------------------------------------------------------------------------------------

def list_trackers(cat: Catalogue, include_not: bool = False) -> list[dict[str, Any]]:
    """Every tracker with its layout, links and read state, plus how many changes it holds."""
    links: dict[str, list[dict]] = defaultdict(list)
    for r in cat.conn.execute("SELECT t.*, a.name table_name, a.kind table_kind FROM trackers t LEFT JOIN assets a ON "
                              "a.key = t.table_key ORDER BY CASE t.state WHEN 'confirmed' THEN 0 WHEN 'suggested' "
                              "THEN 1 ELSE 2 END, CASE t.band WHEN 'likely' THEN 0 WHEN 'check' THEN 1 ELSE 2 END"):
        links[r["tracker_key"]].append(dict(r))
    totals = {r[0]: (r[1], r[2], r[3]) for r in cat.conn.execute(
        "SELECT tracker_key, SUM(n), MAX(NULLIF(day, '')), MIN(NULLIF(day, '')) FROM tracker_counts GROUP BY tracker_key")}
    out = []
    for r in cat.conn.execute("SELECT i.*, a.name, a.kind, a.source, a.attrs FROM tracker_info i JOIN assets a ON "
                              "a.key = i.tracker_key WHERE a.status = 'active'" +
                              ("" if include_not else " AND i.not_tracker = 0") + " ORDER BY a.name"):
        n, last, first = totals.get(r["tracker_key"], (0, None, None))
        out.append({"key": r["tracker_key"], "name": r["name"], "kind": r["kind"], "source": r["source"],
                    "shape": _loads(r["shape"]), "not_tracker": bool(r["not_tracker"]), "read_at": r["read_at"],
                    "read_upto": r["read_upto"], "read_note": r["read_note"], "changes": n or 0, "last_day": last,
                    "first_day": first, "links": links.get(r["tracker_key"], []),
                    "path": _loads(r["attrs"]).get("path")})
    return out


def trackers_of(cat: Catalogue, table_key: str) -> list[dict[str, Any]]:
    """Confirmed and suggested trackers of one table: [{"tracker_key", "name", "state", "band", "name_value"}]."""
    return [dict(r) for r in cat.conn.execute(
        "SELECT t.tracker_key, a.name, t.state, t.band, t.reason, t.name_value FROM trackers t JOIN assets a ON "
        "a.key = t.tracker_key JOIN tracker_info i ON i.tracker_key = t.tracker_key WHERE t.table_key = ? AND "
        "t.state != 'rejected' AND i.not_tracker = 0 ORDER BY t.state, a.name", (table_key,))]


def is_tracker(cat: Catalogue, key: str) -> bool:
    row = cat.conn.execute("SELECT not_tracker FROM tracker_info WHERE tracker_key = ?", (key,)).fetchone()
    return bool(row) and not row[0]


def made_by(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    """Scripts that write this tracker (your own change-tracking code)."""
    return [dict(r) for r in cat.conn.execute(
        "SELECT DISTINCT l.src_key key, a.name FROM links l JOIN assets a ON a.key = l.src_key WHERE l.dst_key = ? "
        "AND l.relation = 'writes'", (key,))]


def _filter(table_name: str | None) -> tuple[str, list]:
    return ("", []) if table_name is None else (" AND table_name = ?", [table_name])


def column_counts(cat: Catalogue, key: str, since: str | None = None,
                  table_name: str | None = None) -> list[dict[str, Any]]:
    """Changes by column: [{"column", "label", "changes", "new", "changed", "removed", "share", "last", "keys",
    "watched"}], most changes first."""
    where, args = _filter(table_name)
    if since:
        where += " AND day >= ?"
        args.append(since[:10])
    per: dict[str, dict[str, Any]] = {}
    for r in cat.conn.execute(f"SELECT column_name, kind, SUM(n), MAX(NULLIF(day, '')) FROM tracker_counts WHERE "
                              f"tracker_key = ?{where} GROUP BY column_name, kind", [key, *args]):
        d = per.setdefault(r[0], {"column": r[0], "new": 0, "changed": 0, "removed": 0, "last": None})
        d[r[1]] = r[2]
        d["last"] = max(filter(None, (d["last"], r[3])), default=None)
    kwhere, kargs = _filter(table_name)
    if since:
        kwhere += " AND last_day >= ?"
        kargs.append(since[:10])
    keys = {r[0]: r[1] for r in cat.conn.execute(
        f"SELECT column_name, COUNT(*) FROM tracker_keys WHERE tracker_key = ?{kwhere} GROUP BY column_name",
        [key, *kargs])}
    shape = _loads((cat.conn.execute("SELECT shape FROM tracker_info WHERE tracker_key = ?", (key,)).fetchone()
                    or [None])[0])
    labels = shape.get("labels") or {}
    watch = set(watched(cat, key))
    total = sum(d["new"] + d["changed"] + d["removed"] for d in per.values()) or 1
    out = []
    for c, d in per.items():
        n = d["new"] + d["changed"] + d["removed"]
        out.append({**d, "label": labels.get(c) or c, "changes": n, "share": n / total, "keys": keys.get(c, 0),
                    "watched": c in watch})
    return sorted(out, key=lambda d: -d["changes"])


def daily(cat: Catalogue, key: str, columns: Iterable[str] | None = None, since: str | None = None,
          table_name: str | None = None) -> list[dict[str, Any]]:
    """[{"day", "column", "kind", "n"}] oldest first."""
    where, args = _filter(table_name)
    cols = list(columns) if columns is not None else None
    if cols is not None:
        if not cols:
            return []
        where += f" AND column_name IN ({', '.join('?' * len(cols))})"
        args += cols
    if since:
        where += " AND day >= ?"
        args.append(since[:10])
    return [dict(r) for r in cat.conn.execute(
        f"SELECT day, column_name AS column, kind, SUM(n) n FROM tracker_counts WHERE tracker_key = ? AND day != ''"
        f"{where} GROUP BY day, column_name, kind ORDER BY day", [key, *args])]


def table_summary(cat: Catalogue, table_key: str, since: str | None = None) -> list[dict[str, Any]]:
    """For a table's page: each linked tracker with its changes by column (watched first)."""
    out = []
    for t in trackers_of(cat, table_key):
        name = t["name_value"]
        tn = None if name is None else name
        tracked = {r[0] for r in cat.conn.execute("SELECT DISTINCT table_name FROM tracker_counts WHERE "
                                                  "tracker_key = ?", (t["tracker_key"],))}
        if tn is not None and tn not in tracked:
            match = [x for x in tracked if x.lower() == tn.lower()]
            tn = match[0] if match else ("" if tracked == {""} else tn)
        cols = column_counts(cat, t["tracker_key"], since, tn)
        out.append({**t, "table_name": tn, "columns": cols, "changes": sum(c["changes"] for c in cols)})
    return out


# ---- reading the changes ------------------------------------------------------------------------------

def read_changes(cat: Catalogue, key: str, report: Report = quiet, *, full: bool = False,
                 sources: list | None = None, connector=None) -> dict[str, Any]:
    """Count this tracker's changes (a file is read in chunks; a SQL table with one paced GROUP BY).
    After the first read only rows from the last change date read onwards are counted again, unless `full`.
    Returns {"rows", "changes", "from", "to", "note"}."""
    asset = cat.get_asset(key)
    if not asset:
        raise ValueError(f"{key} isn't in the catalogue")
    info = cat.conn.execute("SELECT * FROM tracker_info WHERE tracker_key = ?", (key,)).fetchone()
    shape = _loads(info["shape"]) if info else shape_of([c["name"] for c in cat.columns(key)])
    if not shape.get("layout"):
        shape.update(shape_of([c["name"] for c in cat.columns(key)]))
    if not shape.get("layout"):
        raise ValueError("Couldn't tell which columns hold the change date, the changed column or the values.")
    from .curation import is_sensitive, sensitive_columns
    if shape.get("key") and is_sensitive(sensitive_columns(cat), key, shape["key"]):
        shape = {**shape, "key": None}   # a sensitive key column: count changes, but keep no key values
    if asset["kind"] in ("table", "view"):
        result = _read_sql(cat, asset, shape, info, report, full, sources or [], connector)
    else:
        result = _read_file(cat, asset, shape, info, report, full)
    shape.update(result.pop("shape_extra", {}))
    with cat.conn:
        cat.conn.execute("INSERT INTO tracker_info (tracker_key, shape, read_at, read_upto, read_note) VALUES "
                         "(?, ?, ?, ?, ?) ON CONFLICT(tracker_key) DO UPDATE SET shape = excluded.shape, "
                         "read_at = excluded.read_at, read_upto = excluded.read_upto, read_note = excluded.read_note",
                         (key, json.dumps(shape), now(), result.get("to") or (info["read_upto"] if info else None),
                          result.get("note")))
    _relink(cat, key, asset, shape)
    _label_suggestions(cat)
    cat.changed()
    return result


def _relink(cat: Catalogue, key: str, asset: dict, shape: dict) -> None:
    with cat.conn:
        _suggest_links(cat, key, asset, shape, cat.table_index(), _file_stems(cat), now())


class _Counts:
    """Counts kept while reading: per (table, day, column, kind), keys per (table, column, key), and labels."""

    def __init__(self, keys_left: int) -> None:
        self.counts: Counter = Counter()
        self.keys: dict[tuple[str, str, str], list] = {}
        self.labels: dict[str, Counter] = defaultdict(Counter)
        self.tables: Counter = Counter()
        self.keys_left = keys_left
        self.capped = False
        self.rows = 0
        self.first: str | None = None
        self.last: str | None = None

    def add_keys(self, table: str, column: str, value: str, n: int, day: str) -> None:
        k = (table, column, value[:200])
        got = self.keys.get(k)
        if got is None:
            if self.keys_left <= 0:
                self.capped = True
                return
            self.keys_left -= 1
            self.keys[k] = [n, day]
        else:
            got[0] += n
            got[1] = max(got[1] or "", day or "") or None


def _kind(before: Any, after: Any) -> str:
    b = "" if before is None else str(before).strip()
    a = "" if after is None else str(after).strip()
    if b.lower() in ("nan", "none", "null"):
        b = ""
    if a.lower() in ("nan", "none", "null"):
        a = ""
    return "new" if not b and a else "removed" if b and not a else "changed"


def _days(values) -> Any:
    """A pandas Series of change dates as 'YYYY-MM-DD' text ('' when it isn't a date). UK-style 03/11/2026 is
    read day first when any value shows it must be (a first number over 12)."""
    import pandas as pd
    text = values.astype("string").fillna("").str.strip()
    sample = text[text != ""].head(500)
    dayfirst = bool(sample.str.match(r"^(1[3-9]|2\d|3[01])[/.-]\d{1,2}[/.-]\d{2,4}").any())
    iso = sample.str.match(r"^\d{4}-\d{2}-\d{2}").mean() if len(sample) else 0
    if iso > 0.9:
        out = text.str.slice(0, 10)
        return out.where(out.str.match(r"^\d{4}-\d{2}-\d{2}$"), "")
    try:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst, format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst)
    return parsed.dt.strftime("%Y-%m-%d").fillna("")


def _count_frame(df, shape: dict, c: _Counts, cut: str | None) -> None:
    """Add one chunk of rows to the counts. Rows before `cut` (a day) are skipped: they were counted by the last
    read. (Keys seen again on the cut day add to their count twice; which keys changed stays right.)"""
    import pandas as pd
    n = len(df)
    if not n:
        return
    day = _days(df[shape["date"]]) if shape.get("date") in df else pd.Series([""] * n, index=df.index)
    if cut:
        keep = (day >= cut) | (day == "")
        df, day = df[keep], day[keep]
        if not len(df):
            return
    table = df[shape["table"]].astype("string").fillna("").str.strip() if shape.get("table") in df else \
        pd.Series([""] * len(df), index=df.index)
    if shape["layout"] == "long":
        column = df[shape["column"]].astype("string").fillna("").str.strip()
        before = df[shape["before"]] if shape.get("before") in df else pd.Series([None] * len(df), index=df.index)
        after = df[shape["after"]] if shape.get("after") in df else pd.Series([None] * len(df), index=df.index)
        b = before.astype("string").fillna("").str.strip()
        a = after.astype("string").fillna("").str.strip()
        b = b.where(~b.str.lower().isin(["nan", "none", "null"]), "")
        a = a.where(~a.str.lower().isin(["nan", "none", "null"]), "")
        kind = pd.Series("changed", index=df.index)
        if shape.get("before") in df:
            kind = kind.mask((b == "") & (a != ""), "new")
        if shape.get("after") in df:
            kind = kind.mask((b != "") & (a == ""), "removed")
        if shape.get("label") in df:
            lab = df[shape["label"]].astype("string").fillna("").str.strip()
            for (col, label), m in pd.DataFrame({"c": column, "l": lab}).value_counts().items():
                if col and label:
                    c.labels[col][label] += int(m)
    else:
        column = pd.Series(WIDE_COLUMN, index=df.index)
        if shape.get("operation") in df:
            op = df[shape["operation"]].astype("string").fillna("").str.strip().str.lower()
            kind = op.map(lambda v: _OPERATION_KIND.get(v, "changed"))
        else:
            kind = pd.Series("changed", index=df.index)
    frame = pd.DataFrame({"t": table, "d": day, "c": column, "k": kind})
    for (t, d, col, k), m in frame.value_counts().items():
        c.counts[(t, d, col, k)] += int(m)
    for t, m in table.value_counts().items():
        c.tables[t] += int(m)
    if shape.get("key") in df and shape["layout"] == "long":
        kv = df[shape["key"]].astype("string").fillna("").str.strip()
        kf = pd.DataFrame({"t": table, "c": column, "v": kv, "d": day})
        kf = kf[kf["v"] != ""]
        if len(kf):
            g = kf.groupby(["t", "c", "v"], sort=False)["d"].agg(["size", "max"])
            for (t, col, v), row in g.iterrows():
                c.add_keys(t, col, v, int(row["size"]), row["max"] or None)
    days = day[day != ""]
    if len(days):
        lo, hi = days.min(), days.max()
        c.first = min(filter(None, (c.first, lo)))
        c.last = max(filter(None, (c.last, hi)))
    c.rows += len(df)


def _read_file(cat: Catalogue, asset: dict, shape: dict, info, report: Report, full: bool) -> dict[str, Any]:
    import pandas as pd

    from .profiling import _sniff_delimiter
    path = Path(asset["attrs"].get("path") or "")
    if not path.is_file():
        raise FileNotFoundError(f"Can't open {path}: is the folder still there?")
    st = path.stat()
    prior = _loads(shape.get("_read"))
    upto = info["read_upto"] if info else None
    incremental = bool(not full and upto and prior.get("size") and st.st_size >= prior["size"]
                       and st.st_mtime >= prior.get("mtime", 0))
    cut = upto[:10] if incremental else None
    usecols = [shape[r] for r in ("date", "column", "label", "before", "after", "table", "operation", "key")
               if shape.get(r)]
    c = _Counts(KEY_CAP - (_key_count(cat, asset["key"]) if incremental else 0))
    suffix = path.suffix.lower()
    total = asset["attrs"].get("row_count") or 0
    if suffix in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, usecols=lambda x: x in usecols, dtype=str)
        _count_frame(df, shape, c, cut)
    elif suffix == ".parquet":
        df = pd.read_parquet(path, columns=usecols)
        _count_frame(df, shape, c, cut)
    else:
        enc = "utf-8-sig"
        for enc in ("utf-8-sig", "cp1252"):
            try:
                with path.open(encoding=enc) as f:
                    head = f.read(64 * 1024)
                break
            except UnicodeDecodeError:
                continue
        sep = _sniff_delimiter(head)
        reader = pd.read_csv(path, sep=sep, encoding=enc, usecols=lambda x: x in usecols, dtype=str,
                             chunksize=CHUNK_ROWS, encoding_errors="replace", on_bad_lines="skip")
        done = 0
        for chunk in reader:
            _count_frame(chunk, shape, c, cut)
            done += len(chunk)
            report(f"read {done:,} rows" + (f" of about {total:,}" if total else ""), min(done, total or done),
                   total or done)
    _save_counts(cat, asset["key"], c, cut)
    extra = _extras(cat, asset["key"], shape, c)
    extra["_read"] = {"size": st.st_size, "mtime": st.st_mtime}
    note = _note(c, incremental, cut)
    return {"rows": c.rows, "changes": sum(c.counts.values()), "from": c.first, "to": c.last or upto, "note": note,
            "shape_extra": extra}


def _key_count(cat: Catalogue, key: str) -> int:
    return cat.conn.execute("SELECT COUNT(*) FROM tracker_keys WHERE tracker_key = ?", (key,)).fetchone()[0]


def _save_counts(cat: Catalogue, key: str, c: _Counts, cut: str | None) -> None:
    with cat.conn:
        if cut:   # the cut day and undated rows were counted again in full
            cat.conn.execute("DELETE FROM tracker_counts WHERE tracker_key = ? AND (day >= ? OR day = '')", (key, cut))
        else:
            cat.conn.execute("DELETE FROM tracker_counts WHERE tracker_key = ?", (key,))
            cat.conn.execute("DELETE FROM tracker_keys WHERE tracker_key = ?", (key,))
        cat.conn.executemany("INSERT INTO tracker_counts (tracker_key, table_name, day, column_name, kind, n) VALUES "
                             "(?, ?, ?, ?, ?, ?) ON CONFLICT DO UPDATE SET n = n + excluded.n",
                             [(key, t, d, col, k, n) for (t, d, col, k), n in c.counts.items()])
        cat.conn.executemany("INSERT INTO tracker_keys (tracker_key, table_name, column_name, key_value, n, last_day) "
                             "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO UPDATE SET n = n + excluded.n, last_day = "
                             "MAX(COALESCE(last_day, ''), COALESCE(excluded.last_day, ''))",
                             [(key, t, col, v, n, d) for (t, col, v), (n, d) in c.keys.items()])


def _extras(cat: Catalogue, key: str, shape: dict, c: _Counts) -> dict[str, Any]:
    labels = dict(shape.get("labels") or {})
    for col, counter in c.labels.items():
        labels[col] = counter.most_common(1)[0][0]
    tables = {r[0] for r in cat.conn.execute("SELECT DISTINCT table_name FROM tracker_counts WHERE tracker_key = ?",
                                             (key,))} - {""}
    out: dict[str, Any] = {"labels": labels, "table_values": sorted(tables)[:20]}
    if c.capped:
        out["keys_capped"] = True
    return out


def _note(c: _Counts, incremental: bool, cut: str | None) -> str:
    bits = [f"{c.rows:,} rows read" + (f" from {cut} onwards" if incremental else "")]
    if c.first:
        bits.append(f"changes from {c.first} to {c.last}")
    if c.capped:
        bits.append(f"keys affected counted for the first {KEY_CAP:,} keys only")
    return "; ".join(bits)


def _q(name: str) -> str:
    return "[" + str(name).replace("]", "]]") + "]"


def sql_queries(asset: dict, shape: dict, cut: str | None) -> list[tuple[str, str]]:
    """(purpose, query) pairs that count a SQL tracker's changes. Read-only, NOLOCK, grouped on the server."""
    a = asset["attrs"]
    obj = f"{_q(a.get('schema') or 'dbo')}.{_q(a.get('object') or asset['name'].split('.')[-1])}"
    day = f"CONVERT(date, {_q(shape['date'])})" if shape.get("date") else "CAST(NULL AS date)"
    table = f"CAST({_q(shape['table'])} AS nvarchar(256))" if shape.get("table") else "N''"
    where = f" WHERE {day} >= '{cut}'" if cut and shape.get("date") else ""
    if shape["layout"] == "long":
        col = f"CAST({_q(shape['column'])} AS nvarchar(256))"

        def empty(role: str) -> str:
            c = _q(shape[role])
            return f"({c} IS NULL OR LTRIM(RTRIM(CAST({c} AS nvarchar(4000)))) = '')"
        if shape.get("before") and shape.get("after"):
            kind = (f"CASE WHEN {empty('before')} AND NOT {empty('after')} THEN 'new' WHEN NOT {empty('before')} "
                    f"AND {empty('after')} THEN 'removed' ELSE 'changed' END")
        elif shape.get("before"):
            kind = f"CASE WHEN NOT {empty('before')} THEN 'changed' ELSE 'new' END"
        else:
            kind = f"CASE WHEN {empty('after')} THEN 'removed' ELSE 'changed' END"
    else:
        col = f"N'{WIDE_COLUMN}'"
        kind = f"CAST({_q(shape['operation'])} AS nvarchar(20))" if shape.get("operation") else "'changed'"
    out = [("change counts", f"SELECT {table} AS tbl, {day} AS day, {col} AS col, {kind} AS kind, COUNT(*) AS n "
                             f"FROM {obj} WITH (NOLOCK){where} GROUP BY {table}, {day}, {col}, {kind}")]
    if shape["layout"] == "long" and shape.get("label"):
        lab = f"CAST({_q(shape['label'])} AS nvarchar(256))"
        out.append(("field names", f"SELECT {col} AS col, {lab} AS label, COUNT(*) AS n FROM {obj} WITH (NOLOCK)"
                                   f"{where} GROUP BY {col}, {lab}"))
    if shape["layout"] == "long" and shape.get("key"):
        kv = f"CAST({_q(shape['key'])} AS nvarchar(200))"
        out.append(("keys affected", f"SELECT TOP ({KEY_CAP}) {table} AS tbl, {col} AS col, {kv} AS k, COUNT(*) AS n, "
                                     f"MAX({day}) AS last_day FROM {obj} WITH (NOLOCK){where} GROUP BY {table}, {col}, "
                                     f"{kv} ORDER BY COUNT(*) DESC"))
    return out


def _read_sql(cat: Catalogue, asset: dict, shape: dict, info, report: Report, full: bool, sources: list,
              connector) -> dict[str, Any]:
    from .pacing import Pacer
    from .scan_sqlserver import connect
    a = asset["attrs"]
    src = next((s for s in sources if s.name.lower() == asset["source"].lower()), None)
    if src is None:
        raise ValueError(f"No SQL Server source called {asset['source']} in your config.")
    upto = info["read_upto"] if info else None
    cut = upto[:10] if upto and not full and shape.get("date") else None
    queries = sql_queries(asset, shape, cut)
    pacer = Pacer(cat, src.name)
    conn = (connector or connect)(src, a.get("database", ""))
    c = _Counts(KEY_CAP)
    try:
        cur = conn.cursor()
        for i, (purpose, sql) in enumerate(queries):
            report(f"{purpose} ({i + 1} of {len(queries)})", i, len(queries))
            _, rows = pacer.query(cur, sql, purpose=f"change summary: {purpose} of {asset['name']}",
                                  database=a.get("database", ""))
            for r in rows:
                if purpose == "change counts":
                    t, d, col, k, n = r
                    d = str(d)[:10] if d else ""
                    k = _OPERATION_KIND.get(str(k).strip().lower(), str(k)) if shape["layout"] == "wide" else k
                    c.counts[(t or "", d, col or "", k if k in ("new", "changed", "removed") else "changed")] += int(n)
                    c.tables[t or ""] += int(n)
                    c.rows += int(n)
                    if d:
                        c.first = min(filter(None, (c.first, d)))
                        c.last = max(filter(None, (c.last, d)))
                elif purpose == "field names":
                    col, label, n = r
                    if col and label:
                        c.labels[str(col)][str(label)] += int(n)
                else:
                    t, col, k, n, d = r
                    if k not in (None, ""):
                        c.add_keys(t or "", col or "", str(k), int(n), str(d)[:10] if d else "")
    finally:
        conn.close()
    _save_counts(cat, asset["key"], c, cut)
    extra = _extras(cat, asset["key"], shape, c)
    return {"rows": c.rows, "changes": sum(c.counts.values()), "from": c.first, "to": c.last or upto,
            "note": _note(c, bool(cut), cut), "shape_extra": extra}


# ---- business names from FieldName ---------------------------------------------------------------------

def _label_suggestions(cat: Catalogue) -> int:
    """Where a tracker's FieldName differs from its ColumnName (an SAP-coded column with a friendlier name),
    suggest that name as the business name of the column in the linked table."""
    found = []
    named = {(r[0], r[1].lower()) for r in cat.conn.execute(
        "SELECT asset_key, column_name FROM column_info WHERE COALESCE(description, '') != ''")}
    for r in cat.conn.execute("SELECT i.tracker_key, i.shape, t.table_key, a.name FROM tracker_info i JOIN trackers t "
                              "ON t.tracker_key = i.tracker_key JOIN assets a ON a.key = t.table_key WHERE "
                              "i.not_tracker = 0 AND (t.state = 'confirmed' OR t.band = 'likely') AND "
                              "t.state != 'rejected'"):
        labels = (_loads(r[1]).get("labels") or {})
        cols = {c["name"].lower(): c["name"] for c in cat.columns(r[2])}
        for col, label in labels.items():
            if not label or squash(label) == squash(col) or col.lower() not in cols or (r[2], col.lower()) in named:
                continue
            real = cols[col.lower()]
            found.append({"id": suggestions.make_id("tracker_field", f"{r[2]}#{real}"), "subject": r[2],
                          "title": f"{r[3]}.{real}: “{label}”",
                          "detail": "The change tracker calls this column by this name (its FieldName).",
                          "payload": {"key": r[2], "column": real, "label": label, "tracker": r[0]}, "score": 0.8})
    return suggestions.sync(cat, "tracker_field", found)


@suggestions.handler("tracker_field")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    from .curation import set_column_extra
    set_column_extra(cat, p["key"], p["column"], "business_name", p["label"])


# ---- after a scan -------------------------------------------------------------------------------------

def after_scan(cat: Catalogue, report: Report = quiet) -> int:
    """Find trackers, then bring file trackers you've read before up to date (only the new rows are read).
    SQL trackers are only read when you ask, so no query is sent after a scan."""
    n = detect(cat, report)
    if not cat.setting("tracker_auto_read", True):
        return n
    for t in list_trackers(cat):
        if t["read_at"] and t["kind"] == "data_file":
            try:
                read_changes(cat, t["key"], report)
            except Exception as e:  # a moved or locked file must not stop the scan
                with cat.conn:
                    cat.conn.execute("UPDATE tracker_info SET read_note = ? WHERE tracker_key = ?",
                                     (f"Couldn't read it after the last scan: {type(e).__name__}: {e}", t["key"]))
    return n


# ---- the review list ----------------------------------------------------------------------------------

def review_tasks(cat: Catalogue, task) -> list:
    """A tracker that isn't linked to a table yet (with the best suggestion), for To review."""
    out = []
    for t in list_trackers(cat):
        if any(link["state"] == "confirmed" for link in t["links"]):
            continue
        best = next((link for link in t["links"] if link["state"] == "suggested"), None)
        detail = (f"Looks like it tracks {best['table_name'] or best['table_key']} ({best['reason']}). Confirm it "
                  "on Review › Change tracking › Change trackers." if best else
                  "No table matched its name. Link it by hand on Review › Change tracking › Change trackers, or "
                  "mark it as not a tracker.")
        out.append(task("tracker_link", t["key"], f"Which table does {t['name']} track?", detail))
    return out
