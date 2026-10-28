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
from typing import Any, Callable, Iterable

from .store import Catalogue, now

KIND_LABELS = {
    "versions": "Script versions", "family": "File families", "backup": "Main and backup tables",
    "sap": "SAP meanings", "metric": "Metrics found in code", "naming": "Naming rules",
    "description": "Descriptions", "owner": "Owners", "rename": "Renamed columns", "label": "Report labels",
    "schedule": "Schedule matches", "series": "Files a script writes", "folder_role": "Folder roles",
    "join": "Usual joins", "check": "Checks from rules",
}

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
    """Handlers live next to the finders; import them so accepting works from any page."""
    import importlib
    for mod in ("groups", "sapterms", "metricfind", "naming", "drafts2", "schedules", "colrenames", "folderroles",
                "learned", "rulechecks", "gitinfo", "backups", "copilot_roundtrip", "labels"):
        try:
            importlib.import_module(f"pdcat.{mod}")
        except ImportError:
            pass
