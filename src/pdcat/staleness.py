"""Notes that may no longer be true.

A note is flagged when what it describes changed after it was written (or last
confirmed): its column was removed or retyped, the table's structure changed,
or the script it is about was rewritten. Optionally, every note older than a
set number of days is flagged for a re-check (Settings → Notes). Confirming a
note ("Still true") clears the flag until the next change.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .store import Catalogue


def _checked_at(n) -> str:
    return max(n["updated_at"] or "", n["confirmed_at"] or "")


def stale_reasons(cat: Catalogue, key: str) -> dict[int, list[str]]:
    """{note id: [reasons]} for the notes on one item and its columns."""
    asset = cat.get_asset(key)
    if asset is None:
        return {}
    columns = {c["name"].lower() for c in cat.columns(key)}
    history = cat.conn.execute("SELECT at, change, detail FROM schema_history WHERE asset_key = ? ORDER BY at",
                               (key,)).fetchall()
    recheck_days = int(cat.setting("note_recheck_days", 0) or 0)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=recheck_days)).isoformat() if recheck_days else None
    changed_at = str(asset["attrs"].get("changed_at") or "")
    out: dict[int, list[str]] = {}
    for n in cat.notes(key):
        if n["archived"]:
            continue
        reasons = []
        checked = _checked_at(n)
        _, _, col = n["asset_key"].partition("#")
        if col and columns and col.lower() not in columns:
            reasons.append(f"column {col} is no longer there")
        for h in history:
            if h["at"] <= checked:
                continue
            detail = h["detail"] or ""
            if col and h["change"] == "column retyped" and detail.lower().startswith(col.lower() + " "):
                reasons.append(f"{detail}")
            elif not col and h["change"] in ("column added", "column removed", "column retyped"):
                reasons.append(f"structure changed ({h['change']}: {detail})")
        if asset["kind"] in ("script", "notebook", "sql_script", "procedure", "view") and changed_at > checked:
            reasons.append("it was changed after this note was written")
        if cutoff and checked < cutoff:
            reasons.append(f"not checked for over {recheck_days} days")
        if reasons:
            out[n["id"]] = list(dict.fromkeys(reasons))[:4]
    return out


def all_stale(cat: Catalogue) -> list[dict[str, Any]]:
    """Every note that may be out of date, for the review list."""
    keys = {r[0].split("#", 1)[0] for r in cat.conn.execute("SELECT asset_key FROM notes WHERE archived = 0")}
    out = []
    for key in keys:
        for note_id, reasons in stale_reasons(cat, key).items():
            out.append({"note_id": note_id, "asset_key": key, "reasons": reasons})
    return out
