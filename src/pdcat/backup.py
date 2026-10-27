"""Backups of the catalogue file, under your control.

Off until you choose a backup folder on the Settings page. Then the catalogue
copies itself there on a schedule (when the app opens, daily or weekly) and
keeps the newest few copies. "Back up now" works whether or not the schedule
is on. Each copy is a complete catalogue, made with SQLite's own backup so it
is safe while the app is running, plus a small JSON file of your notes that
can be read without the tool.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .store import Catalogue, now

FREQUENCIES = {"app open": timedelta(0), "daily": timedelta(days=1), "weekly": timedelta(days=7)}
DEFAULTS = {"backup_folder": "", "backup_every": "daily", "backup_keep": 14}
PREFIX = "catalogue-"

# What goes in the readable notes file beside each backup.
NOTE_TABLES = {
    "notes": "SELECT uid, asset_key, note_type, status, body, tags, author, created_at, updated_at FROM notes",
    "item_info": "SELECT * FROM item_info",
    "column_info": "SELECT * FROM column_info",
    "knowledge": "SELECT * FROM knowledge",
    "questions": "SELECT * FROM questions",
    "metrics": "SELECT * FROM metrics",
    "keys": "SELECT * FROM keys WHERE state = 'active'",
    "business_projects": "SELECT * FROM bprojects",
}


def settings(cat: Catalogue) -> dict[str, Any]:
    return {k: cat.setting(k, v) for k, v in DEFAULTS.items()}


def backup_now(cat: Catalogue, folder: str | Path | None = None) -> Path:
    """Copy the catalogue into the backup folder now. Returns the new file."""
    folder = Path(folder or settings(cat)["backup_folder"]).expanduser()
    if not str(folder).strip() or str(folder) == ".":
        raise ValueError("Choose a backup folder on the Settings page first.")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"{PREFIX}{stamp}.db"
    dest = sqlite3.connect(str(target))
    try:
        cat.conn.backup(dest)
    finally:
        dest.close()
    target.with_suffix(".notes.json").write_text(json.dumps(notes_snapshot(cat), indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
    cat.set_setting("last_backup_at", now())
    prune(folder, int(settings(cat)["backup_keep"]))
    return target


def notes_snapshot(cat: Catalogue) -> dict[str, list[dict]]:
    return {name: [dict(r) for r in cat.conn.execute(sql)] for name, sql in NOTE_TABLES.items()}


def due(cat: Catalogue) -> bool:
    s = settings(cat)
    if not str(s["backup_folder"]).strip():
        return False
    last = cat.setting("last_backup_at")
    if not last:
        return True
    gap = FREQUENCIES.get(s["backup_every"], FREQUENCIES["daily"])
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) >= gap


def maybe_backup(cat: Catalogue) -> Path | None:
    """Back up if a folder is set and a backup is due. Errors are returned to the caller to show, never raised."""
    if not due(cat):
        return None
    return backup_now(cat)


def list_backups(folder: str | Path) -> list[dict[str, Any]]:
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob(f"{PREFIX}*.db"), reverse=True):
        info: dict[str, Any] = {"path": path, "file": path.name, "size": path.stat().st_size,
                                "made": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            info["notes"] = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            info["items"] = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            conn.close()
        except sqlite3.Error as e:
            info["error"] = str(e)
        out.append(info)
    return out


def prune(folder: Path, keep: int) -> None:
    if keep < 1:
        return
    for path in sorted(folder.glob(f"{PREFIX}*.db"), reverse=True)[keep:]:
        path.unlink(missing_ok=True)
        path.with_suffix(".notes.json").unlink(missing_ok=True)


def restore(cat: Catalogue, backup: str | Path) -> Path:
    """Replace the catalogue's contents with a backup. The current contents are saved first
    (as a backup named "before-restore"), so a restore can itself be undone. Reopen the
    catalogue afterwards."""
    backup = Path(backup)
    safety = backup.parent / f"before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    dest = sqlite3.connect(str(safety))
    try:
        cat.conn.backup(dest)
    finally:
        dest.close()
    src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        src.backup(cat.conn)
    finally:
        src.close()
    return safety
