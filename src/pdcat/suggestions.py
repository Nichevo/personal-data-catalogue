"""Things the catalogue works out and you decide on: script versions, file families, SAP meanings,
metrics found in code, naming rules, descriptions, owners...

Each finder hands its suggestions to `sync`, which keeps one row per suggestion with a stable id.
What you decide sticks: an accepted or rejected suggestion is never asked again, even when the
next scan finds it again. Suggestions a scan no longer finds are marked "gone" and drop out of
view. Accepting one runs the handler registered for its kind, which does whatever the suggestion
said (makes the group, writes the description...).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from .store import Catalogue, now

KIND_LABELS = {
    "versions": "Versions", "family": "File families", "backup": "Main and backup tables",
    "sap": "SAP meanings", "metric": "Metrics found in code", "naming": "Naming rules",
    "description": "Descriptions", "owner": "Owners", "rename": "Renamed columns", "label": "Report labels",
    "schedule": "Schedule matches", "series": "Files a script writes", "folder_role": "Folder roles",
    "join": "Usual joins", "check": "Checks from rules", "copilot": "From Copilot",
    "doc_definition": "Definitions in documents",
}

# For each kind: what the suggestion is, and what Accept changes. Shown above each list and on each card.
KIND_HELP = {
    "versions": ("Scripts, Power BI reports or workbooks that look like copies of one thing (train.py, train_v2.py, "
                 "train_old.py; Sales.pbix next to Sales - Copy.pbix, or the same report under another name).",
                 "groups them as versions of one item and marks the newest-looking one as the current one, so "
                 "searches and diagrams show that one first. No file is changed."),
    "family": ("Data files that hold the same data at different levels (daily, weekly, by store, by region).",
               "groups them as one family, so each file's page lists the others."),
    "backup": ("Two tables with the same name and columns in different places: one looks like the main one, "
               "the other like a backup or copy.",
               "marks which is the main table and which the backup, so searches point at the main one."),
    "sap": ("Columns or tables whose names match SAP fields or tables (WERKS, MATNR, MARC...).",
            "records the SAP name on that column or table, so it can be found by either name and the Copilot "
            "pack can explain it."),
    "metric": ("Calculations in your code or Power BI measures that look like business metrics.",
               "adds the metric, with its formula and where it was found, to the Metric glossary."),
    "naming": ("Naming patterns most of your tables or columns already follow.",
               "adds the pattern as a naming rule. New names that break it then show on To review."),
    "description": ("A description drafted from the item itself (its docstring, comments or file name).",
                    "saves that text as the item's purpose. You can change it later on the item's page."),
    "owner": ("Who looks like the owner, from who made the most commits to it in git.",
              "fills in the item's owner, so you know who to ask about it."),
    "rename": ("A column that is the same data as a column in another item, under a new name.",
               "adds a description to the column saying where it comes from and what it was called there."),
    "label": ("A friendlier name for a column, taken from the label a report shows for it.",
              "saves that label as the column's business name, so searching for the label finds the column."),
    "schedule": ("Scripts matched to rows of your schedule sheet.",
                 "records when that script runs."),
    "series": ("Several files one script writes from the same name pattern (a file per day or per region).",
               "groups the files under that pattern and links the group to the script."),
    "folder_role": ("A new subfolder in a folder you set roles for, and what it looks like (code, data, archive...).",
                    "gives the subfolder that role. Archive and ignore folders drop out of searches."),
    "join": ("Two tables your SQL often joins on the same columns.",
             "records the join between the tables, so the diagrams draw it and the Copilot pack knows it."),
    "check": ("A rule in a note or description that can be turned into a check on the data.",
              "adds the check to the item. It runs when you profile the item and shows failures on To review."),
    "copilot": ("A description or naming rule your Copilot agent drafted from the gaps pack.",
                "saves the description on the item or column, or turns the rule into checkable naming rules."),
    "doc_definition": ("A name and its definition found in a design document (a table of fields, or a line like "
                       "“PlantID: identifier of the plant”), matched to a column or measure with no description.",
                       "saves that definition as the column's description or the measure's purpose."),
}


def help_for(kind: str) -> tuple[str, str]:
    return KIND_HELP.get(kind, ("Something the catalogue worked out.", "applies it."))


Handler = Callable[[Catalogue, dict[str, Any]], None]
_HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Register what accepting a suggestion of this kind does."""
    def wrap(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn
    return wrap


def make_id(kind: str, subject: str, detail: str = "") -> str:
    return f"{kind}:{subject}:{detail}" if detail else f"{kind}:{subject}"


def sync(cat: Catalogue, kind: str, found: Iterable[dict[str, Any]], *, scope: str | None = None) -> int:
    """Store what a finder found this time. Each dict has id, subject, title and optionally detail,
    payload and score. Open suggestions of this kind (within `scope`, a subject prefix) that weren't
    found again are marked gone. Returns how many are open."""
    found = list(found)
    ts = now()
    ids = set()
    with cat.conn:
        for s in found:
            sid = s.get("id") or make_id(kind, s["subject"])
            ids.add(sid)
            cat.conn.execute(
                "INSERT INTO suggestions (id, kind, subject, title, detail, payload, score, state, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "title = excluded.title, detail = excluded.detail, payload = excluded.payload, "
                "score = excluded.score, updated_at = excluded.updated_at, "
                "state = CASE WHEN suggestions.state = 'gone' THEN 'open' ELSE suggestions.state END",
                (sid, kind, s["subject"], s["title"], s.get("detail"), json.dumps(s.get("payload") or {}, default=str),
                 float(s.get("score") or 0), ts, ts))
        where, params = "kind = ? AND state = 'open'", [kind]
        if scope:
            where += " AND substr(subject, 1, ?) = ?"
            params += [len(scope), scope]
        stale = [r[0] for r in cat.conn.execute(f"SELECT id FROM suggestions WHERE {where}", params)
                 if r[0] not in ids]
        cat.conn.executemany("UPDATE suggestions SET state = 'gone', updated_at = ? WHERE id = ?",
                             [(ts, i) for i in stale])
    return sum(1 for r in cat.conn.execute("SELECT id FROM suggestions WHERE kind = ? AND state = 'open'", (kind,))
               if r[0] in ids)


def add(cat: Catalogue, kind: str, subject: str, title: str, *, detail: str | None = None,
        payload: dict | None = None, score: float = 0, ident: str | None = None) -> str:
    """Add or refresh a single suggestion without touching the others of its kind."""
    sid = ident or make_id(kind, subject)
    ts = now()
    with cat.conn:
        cat.conn.execute(
            "INSERT INTO suggestions (id, kind, subject, title, detail, payload, score, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
            "detail = excluded.detail, payload = excluded.payload, score = excluded.score, "
            "updated_at = excluded.updated_at, "
            "state = CASE WHEN suggestions.state = 'gone' THEN 'open' ELSE suggestions.state END",
            (sid, kind, subject, title, detail, json.dumps(payload or {}, default=str), score, ts, ts))
    return sid


def _row(r) -> dict[str, Any]:
    d = dict(r)
    d["payload"] = json.loads(d["payload"] or "{}")
    return d


def open_suggestions(cat: Catalogue, kind: str | None = None, subject: str | None = None,
                     limit: int = 1000) -> list[dict[str, Any]]:
    where, params = ["state = 'open'"], []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if subject:
        where.append("subject = ?")
        params.append(subject)
    return [_row(r) for r in cat.conn.execute(
        f"SELECT * FROM suggestions WHERE {' AND '.join(where)} ORDER BY score DESC, title LIMIT ?", (*params, limit))]


def get(cat: Catalogue, sid: str) -> dict[str, Any] | None:
    r = cat.conn.execute("SELECT * FROM suggestions WHERE id = ?", (sid,)).fetchone()
    return _row(r) if r else None


def state(cat: Catalogue, sid: str) -> str | None:
    r = cat.conn.execute("SELECT state FROM suggestions WHERE id = ?", (sid,)).fetchone()
    return r[0] if r else None


def decided(cat: Catalogue, kind: str) -> dict[str, str]:
    """id -> accepted | rejected, so finders can skip what you already decided."""
    return {r[0]: r[1] for r in cat.conn.execute(
        "SELECT id, state FROM suggestions WHERE kind = ? AND state IN ('accepted', 'rejected')", (kind,))}


def accept(cat: Catalogue, sid: str, payload: dict[str, Any] | None = None) -> None:
    """Do what the suggestion says (with your edits in `payload`, if any) and remember you accepted it."""
    s = get(cat, sid)
    if s is None:
        raise KeyError(sid)
    data = {**s["payload"], **(payload or {})}
    fn = _HANDLERS.get(s["kind"])
    if fn is None:
        _load_handlers()
        fn = _HANDLERS.get(s["kind"])
    if fn:
        fn(cat, {**data, "_subject": s["subject"], "_id": sid})
    _set_state(cat, sid, "accepted", data)


def reject(cat: Catalogue, sid: str) -> None:
    _set_state(cat, sid, "rejected")


def reopen(cat: Catalogue, sid: str) -> None:
    _set_state(cat, sid, "open")


def _set_state(cat: Catalogue, sid: str, new: str, payload: dict | None = None) -> None:
    with cat.conn:
        if payload is not None:
            cat.conn.execute("UPDATE suggestions SET state = ?, payload = ?, updated_at = ?, decided_by = ? WHERE id = ?",
                             (new, json.dumps(payload, default=str), now(), cat.author(), sid))
        else:
            cat.conn.execute("UPDATE suggestions SET state = ?, updated_at = ?, decided_by = ? WHERE id = ?",
                             (new, now(), cat.author(), sid))
    cat.changed()


def counts(cat: Catalogue) -> dict[str, int]:
    return dict(cat.conn.execute("SELECT kind, COUNT(*) FROM suggestions WHERE state = 'open' GROUP BY kind"))


def _load_handlers() -> None:
    """Handlers live next to the finders (each registers with @handler); import them so accepting works from
    any page. Imported here, not at the top, because those modules import this one."""
    from . import (  # noqa: F401
        colrenames,
        commentdrafts,
        copilot_roundtrip,
        folderroles,
        gitinfo,
        groups,
        labels,
        learned,
        metricfind,
        naming,
        rulechecks,
        sapterms,
        tablepairs,
    )
