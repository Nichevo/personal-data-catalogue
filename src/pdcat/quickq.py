"""Five quick questions: a short daily list of the highest-value gaps, each answerable in under a minute.

Most are a suggestion to confirm or reject (a description drafted from a comment, a SAP meaning, a
usual join); the rest ask for one line about a table, file or script that a lot of things use but
nobody has described. The list is picked once a day, so it doesn't change while you work through
it; answered questions drop off and aren't replaced until tomorrow.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from . import suggestions
from .store import Catalogue

COUNT = 5
# suggestion kinds that are quick to judge, most useful first
QUICK_KINDS = ("description", "label", "sap", "check", "join", "rename", "backup", "metric", "naming")
SETTING = "quick_questions"


def _candidates(cat: Catalogue) -> list[dict[str, Any]]:
    from .curation import item_info
    out: list[dict[str, Any]] = []
    used = {r[0]: r[1] for r in cat.conn.execute(
        "SELECT dst_key, COUNT(DISTINCT src_key) FROM links WHERE dst_key IS NOT NULL GROUP BY dst_key")}
    for r in cat.conn.execute(
            "SELECT key, name, kind, description FROM assets WHERE status = 'active' AND kind IN "
            "('table', 'view', 'data_file', 'file_series', 'script', 'notebook', 'report') "
            "AND coalesce(description, '') = ''"):
        n = used.get(r["key"], 0)
        if n < 2 or (item_info(cat, r["key"]) or {}).get("purpose"):
            continue
        out.append({"id": f"describe:{r['key']}", "type": "describe", "key": r["key"], "score": 10 + n,
                    "title": f"What is {r['name'].rsplit('/', 1)[-1]}?",
                    "detail": f"{n} items use it and it has no description yet. One line is enough."})
    for rank, kind in enumerate(QUICK_KINDS):
        for s in suggestions.open_suggestions(cat, kind, limit=20):
            out.append({"id": f"suggestion:{s['id']}", "type": "suggestion", "suggestion": s["id"],
                        "key": s["subject"], "score": 30 - 2 * rank, "title": s["title"], "detail": s.get("detail")})
    return sorted(out, key=lambda q: (-q["score"], q["id"]))


def todays(cat: Catalogue, today: str | None = None) -> list[dict[str, Any]]:
    """Today's questions still waiting for an answer."""
    today = today or date.today().isoformat()
    saved = cat.setting(SETTING, {}) or {}
    if saved.get("date") != today:
        picked, seen_keys = [], set()
        for q in _candidates(cat):
            if q["key"] in seen_keys:
                continue  # one question per item a day
            picked.append(q)
            seen_keys.add(q["key"])
            if len(picked) >= COUNT:
                break
        saved = {"date": today, "questions": picked}
        cat.set_setting(SETTING, saved, quiet=True)
    return [q for q in saved["questions"] if not _answered(cat, q)]


def _answered(cat: Catalogue, q: dict[str, Any]) -> bool:
    if q["type"] == "suggestion":
        return suggestions.state(cat, q["suggestion"]) != "open"
    from .curation import item_info
    a = cat.get_asset(q["key"])
    return not a or bool(a["description"]) or bool((item_info(cat, q["key"]) or {}).get("purpose"))


def answer_describe(cat: Catalogue, key: str, text: str) -> None:
    from .curation import set_item_info
    set_item_info(cat, key, purpose=text.strip())  # recorded in the edit history, with undo
