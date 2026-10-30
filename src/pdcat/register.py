"""The Power BI report register (2.3): a list of published reports with their owners, kept outside this app
and brought in as a data file in a scanned folder (a CSV with one row per report: uniqueID, name, description,
owner, audience, tags, statusName, powerBIAppID, webUrl, dataset, datasetId, publishedDateTime,
modifiedDateTime, and whatever else is there).

You mark that data file as the register. Each row becomes a small item of its own (`reg://<id>`) so it can be
searched by owner, audience, app or status and have notes, and each row is matched to a scanned .pbix by name,
which you can change by hand. A matched report shows the row at the top of its page as key context, gets the
owner and the description filled in where it has none, and the dataset column tells the impact pages which
model a thin report uses. Nothing here calls the Power BI service.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .store import Catalogue
from .textclean import as_text

SETTING = "report_register"       # {"key": data file key, "mapping": {field: column}}
MATCHES = "register_matches"      # {reg key: report key or ""} set by hand
FILLED = "register_filled"        # {report key: {"owner": value, "purpose": value}} written by the register
SOURCE = "report register"        # the source name of the row items, so folder scans never mark them missing
PREFIX = "reg://"
MAX_ROWS = 20_000

# Field -> column names it is read from (any case). The first field name is the label shown.
FIELDS: dict[str, tuple[str, ...]] = {
    "uid": ("uniqueID", "id", "uid", "reportId", "report id", "report_id", "key"),
    "name": ("name", "report", "reportName", "report name", "title"),
    "description": ("description", "desc", "summary"),
    "owner": ("owner", "reportOwner", "owner name", "ownerName", "contact"),
    "audience": ("audience", "users", "consumers", "for"),
    "tags": ("tags", "keywords", "labels"),
    "status": ("statusName", "status", "state", "lifecycle"),
    "app": ("powerBIAppID", "app", "appName", "app name", "application"),
    "url": ("webUrl", "url", "link", "reportUrl"),
    "dataset": ("dataset", "datasetName", "dataset name", "semanticModel", "model"),
    "dataset_id": ("datasetId", "dataset id", "dataset_id", "semanticModelId"),
    "published": ("publishedDateTime", "published", "publishedDate", "created", "createdDateTime"),
    "modified": ("modifiedDateTime", "modified", "modifiedDate", "lastModified", "updated"),
    "workspace": ("workspace", "workspaceName", "workspace name", "group"),
}
LABELS = {"uid": "ID", "name": "Name", "description": "Description", "owner": "Owner", "audience": "Audience",
          "tags": "Tags", "status": "Status", "app": "App", "url": "Link", "dataset": "Dataset",
          "dataset_id": "Dataset ID", "published": "Published", "modified": "Modified", "workspace": "Workspace"}
STRIP = ("owner", "audience", "app", "status", "workspace", "published", "modified")  # the context strip


# ---- settings -------------------------------------------------------------------------------------

def stored(cat: Catalogue) -> dict[str, Any]:
    return cat.setting(SETTING, {})


def register_key(cat: Catalogue) -> str | None:
    return stored(cat).get("key") or None


def is_register(cat: Catalogue, key: str) -> bool:
    return register_key(cat) == key


def guess_mapping(columns: list[str]) -> dict[str, str]:
    """field -> column name, from the column names the file has (any case, spaces and underscores ignored)."""
    def squash(s: str) -> str:
        return re.sub(r"[\s_\-]+", "", s).lower()
    have = {squash(c): c for c in columns}
    out: dict[str, str] = {}
    for field, names in FIELDS.items():
        for n in names:
            if squash(n) in have and have[squash(n)] not in out.values():
                out[field] = have[squash(n)]
                break
    return out


def set_register(cat: Catalogue, key: str | None, mapping: dict[str, str] | None = None) -> None:
    """Use the data file `key` as the register (None to stop). Row items from a previous register are marked
    missing by the next refresh."""
    if not key:
        cat.set_setting(SETTING, {})
        return
    mapping = {f: c for f, c in (mapping or {}).items() if c}
    if not mapping:
        mapping = guess_mapping([c["name"] for c in cat.columns(key)])
    cat.set_setting(SETTING, {"key": key, "mapping": mapping})


# ---- reading the file ----------------------------------------------------------------------------

def read_rows(cat: Catalogue, key: str | None = None, mapping: dict[str, str] | None = None) -> list[dict[str, str]]:
    """The register's rows with the fields named as in FIELDS (other columns kept under their own names).
    An empty list when there is no register, the file is gone, or it can't be read."""
    key = key or register_key(cat)
    if not key:
        return []
    asset = cat.get_asset(key)
    if not asset:
        return []
    path = Path(asset["attrs"].get("path") or asset["attrs"].get("full_path") or "")
    if not path.is_file():
        return []
    mapping = mapping or stored(cat).get("mapping") or {}
    try:
        from .profiling import read_data_file
        df, _, _ = read_data_file(path, max_rows=MAX_ROWS)
    except Exception:
        return []
    if not mapping:
        mapping = guess_mapping([str(c) for c in df.columns])
    by_col = {c: f for f, c in mapping.items()}
    out = []
    for i, rec in enumerate(df.to_dict("records")):
        row: dict[str, str] = {}
        for col, val in rec.items():
            text = as_text(val).strip()
            field = by_col.get(str(col))
            if field:
                row[field] = text
            elif text:
                row[str(col)] = text
        if not row.get("name"):
            continue
        row["uid"] = row.get("uid") or _slug(row["name"]) or str(i + 1)
        out.append(row)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


# ---- matching rows to scanned reports -----------------------------------------------------------

def _clean(name: str) -> str:
    name = re.sub(r"\.(pbix|pbit|pbip)$", "", name.strip(), flags=re.I)
    return re.sub(r"[\s_\-]+", " ", name).strip().lower()


def _reports(cat: Catalogue) -> list[dict[str, Any]]:
    from .groups import older_versions
    old = older_versions(cat)
    rows = []
    for r in cat.conn.execute("SELECT key, name, source, attrs FROM assets WHERE kind = 'report' AND status = 'active'"):
        a = json.loads(r["attrs"])
        rows.append({"key": r["key"], "name": r["name"], "source": r["source"], "old": r["key"] in old,
                     "thick": bool(a.get("tables")), "live": (a.get("live") or {}).get("dataset") or ""})
    return rows


def match_rows(cat: Catalogue, rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """reg key -> {"report": report key or None, "how": "name" | "words" | "start" | "manual" | "",
    "candidates": [report keys]} for every row."""
    from .reportgroups import name_match, name_words
    reports = _reports(cat)
    by_clean: dict[str, list[dict]] = {}
    for r in reports:
        by_clean.setdefault(_clean(r["name"]), []).append(r)
    words = {r["key"]: name_words(Path(r["name"]).stem) for r in reports}
    manual = cat.setting(MATCHES, {})
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = PREFIX + row["uid"]
        if key in manual:
            out[key] = {"report": manual[key] or None, "how": "manual", "candidates": []}
            continue
        cands = by_clean.get(_clean(row["name"]), [])
        how = "name"
        if not cands:
            w = name_words(row["name"])
            same = [r for r in reports if words[r["key"]] == w and w]
            if same:
                cands, how = same, "words"
            else:
                starts = [r for r in reports if w and name_match(w, words[r["key"]]) in ("same", "prefix")]
                cands, how = starts, "start"
        current = [c for c in cands if not c["old"]] or cands
        if how == "start" and len(current) > 1:
            current = []   # a start that matches several reports is no match
        pick = current[0]["key"] if current else None
        out[key] = {"report": pick, "how": how if pick else "", "candidates": [c["key"] for c in cands]}
    return out


def set_match(cat: Catalogue, reg_key: str, report_key: str | None) -> None:
    """Match a row to a report by hand ("" or None: no report)."""
    manual = cat.setting(MATCHES, {})
    manual[reg_key] = report_key or ""
    cat.set_setting(MATCHES, manual)
    refresh(cat)


def forget_match(cat: Catalogue, reg_key: str) -> None:
    manual = cat.setting(MATCHES, {})
    if manual.pop(reg_key, None) is not None:
        cat.set_setting(MATCHES, manual)
    refresh(cat)


# ---- the refresh (after every scan, and after you change the register) ------------------------

def refresh(cat: Catalogue) -> int:
    """Read the register again: one item per row, matched to the scanned reports, with owners and descriptions
    copied to reports that have none. Returns the number of rows."""
    rows = read_rows(cat)
    matches = match_rows(cat, rows) if rows else {}
    with cat.scan(SOURCE):
        for row in rows:
            key = PREFIX + row["uid"]
            m = matches.get(key, {})
            attrs = {**row, "report": m.get("report"), "how": m.get("how", ""), "candidates": m.get("candidates", []),
                     "register": register_key(cat)}
            cat.upsert_asset(key, "report_ref", row["name"], SOURCE, description=row.get("description") or None,
                             attrs=attrs)
    _fill_reports(cat, rows, matches)
    cat.conn.commit()
    return len(rows)


def _fill_reports(cat: Catalogue, rows: list[dict[str, str]], matches: dict[str, dict[str, Any]]) -> None:
    """Owner and purpose on matched reports, only where empty or where the register wrote them last time."""
    from .curation import item_info, set_item_info
    filled = cat.setting(FILLED, {})
    by_key = {PREFIX + r["uid"]: r for r in rows}
    for reg_key, m in matches.items():
        report = m.get("report")
        row = by_key.get(reg_key)
        if not report or not row:
            continue
        info = item_info(cat, report)
        mine = filled.get(report, {})
        changes = {}
        for field, src in (("owner", "owner"), ("purpose", "description")):
            value = (row.get(src) or "").strip()
            current = (info.get(field) or "").strip()
            if value and (not current or current == mine.get(field)) and value != current:
                changes[field] = value
        if changes:
            set_item_info(cat, report, **changes)
            filled[report] = {**mine, **changes}
    cat.set_setting(FILLED, filled)


# ---- what the pages read -----------------------------------------------------------------------

def entries(cat: Catalogue) -> list[dict[str, Any]]:
    """Every register row item: its fields plus "key", "report" (the matched report key or None), "how"."""
    out = []
    for r in cat.conn.execute("SELECT key, name, description, attrs FROM assets WHERE kind = 'report_ref' AND "
                              "status = 'active' ORDER BY name"):
        a = json.loads(r["attrs"])
        out.append({**a, "key": r["key"], "name": r["name"], "description": r["description"] or a.get("description", "")})
    return out


def entry(cat: Catalogue, reg_key: str) -> dict[str, Any] | None:
    a = cat.get_asset(reg_key)
    if not a:
        return None
    return {**a["attrs"], "key": reg_key, "name": a["name"], "description": a["description"] or ""}


def rows_by_report(cat: Catalogue) -> dict[str, dict[str, Any]]:
    """report key -> its register row (the first when several rows name the same file)."""
    out: dict[str, dict[str, Any]] = {}
    for e in entries(cat):
        if e.get("report") and e["report"] not in out:
            out[e["report"]] = e
    return out


def for_report(cat: Catalogue, report_key: str) -> dict[str, Any] | None:
    r = cat.conn.execute("SELECT key FROM assets WHERE kind = 'report_ref' AND status = 'active' AND "
                         "attrs LIKE ? LIMIT 1", (f'%"report": "{report_key}"%',)).fetchone()
    return entry(cat, r["key"]) if r else None


def unmatched(cat: Catalogue) -> list[dict[str, Any]]:
    return [e for e in entries(cat) if not e.get("report")]


def dataset_models(cat: Catalogue) -> dict[str, str]:
    """dataset name (lower) and dataset id -> the scanned report that holds that model, from rows whose matched
    report has a model of its own. A thin report with a live connection to that dataset uses that model."""
    thick = {r["key"] for r in _reports(cat) if r["thick"]}
    out: dict[str, str] = {}
    for e in entries(cat):
        if e.get("report") in thick:
            for f in ("dataset", "dataset_id"):
                if e.get(f):
                    out.setdefault(str(e[f]).strip().lower(), e["report"])
    return out


def strip_line(e: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, value) pairs for the context strip: owner, audience, app, status, workspace, dates."""
    return [(LABELS[f], str(e[f])[:19].replace("T", " ") if f in ("published", "modified") else str(e[f]))
            for f in STRIP if e.get(f)]


def facets(cat: Catalogue) -> dict[str, list[str]]:
    """The distinct owners, audiences, apps, statuses and workspaces, for filters."""
    out: dict[str, set] = {f: set() for f in ("owner", "audience", "app", "status", "workspace")}
    for e in entries(cat):
        for f in out:
            if e.get(f):
                out[f].add(str(e[f]))
    return {f: sorted(v, key=str.lower) for f, v in out.items()}
