"""History folders: archived, backup and dated copies that a scan lists without opening (2.8).

Reporting folders often keep every past file: FreightFiles/2026/05 2026/20260414/archived/report_20260414.xlsx
and thousands like it. Opening each one takes a second or two on a network drive and says nothing new: the
layout is the same as the newest file. So a folder scan treats these as history:

* A folder named like an archive (archive, archived, old, backup, history, previous, superseded …) is history,
  at any depth, unless you gave it another role. Settings: `history_by_name` (on).
* A folder you gave the role "archive" in Folder roles is history; any other role you give it wins over its name.
* In history, data files and workbooks that differ only by a date become one dated series and only the newest
  file of each is opened (Settings: `history_read_newest`, 1); the older ones are listed from the file system
  (name, size, modified) without being opened. Stand-alone files in history are opened as usual, except in a
  big folder (sampled by date), where they are listed too.
* The scan log and the folder's card say how many files were listed, per history folder, with their date range.

"Read every file" on a folder's card turns all of this off for that folder.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import Catalogue

HISTORY_NAME = re.compile(r"^(archive[ds]?|_?archive|old|_old|zz_?old|olds?er|backups?|bak|legacy|deprecated|retired|"
                          r"history|historic(al)?|hist|previous|prev|superseded|past|prior)$", re.I)
HISTORY_READ_NEWEST = 1  # files opened per dated series inside history folders (Settings: history_read_newest)


class History:
    """Which files under a scanned folder are history, and why. `why(rel)` is None for files that aren't."""

    def __init__(self, cat: Catalogue, root: Path, source: str) -> None:
        from .folderroles import roles_under
        self.on = source not in cat.setting("read_all_sources", [])
        self.by_name = bool(cat.setting("history_by_name", True))
        # Confirmed roles you gave folders under root: "archive" makes history, any other role makes "not history".
        self.roles = {rel.lower(): v["role"] for rel, v in roles_under(cat, root).items() if v.get("confirmed")}
        self._cache: dict[str, tuple[str, str] | None] = {}

    def folder(self, rel: str) -> tuple[str, str] | None:
        """(history folder's relative path, why) for a file's relative path, or None."""
        if not self.on:
            return None
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if parent not in self._cache:
            self._cache[parent] = self._folder(parent)
        return self._cache[parent]

    def _folder(self, parent: str) -> tuple[str, str] | None:
        if not parent:
            return None
        parts = parent.split("/")
        # The deepest folder with a role you confirmed decides; otherwise the outermost history-named folder.
        for i in range(len(parts), 0, -1):
            role = self.roles.get("/".join(parts[:i]).lower())
            if role:
                return ("/".join(parts[:i]), "you gave it the role archive") if role == "archive" else None
        if self.by_name:
            for i, p in enumerate(parts):
                if HISTORY_NAME.match(p):
                    return "/".join(parts[:i + 1]), f"named {p}/"
        return None

    def why(self, rel: str) -> str | None:
        hit = self.folder(rel)
        return hit[1] if hit else None


def summarise(history: History, rels: list[str], stats: dict[str, Any], opened: set[str]) -> list[dict[str, Any]]:
    """Per history folder: how many files, how many were opened, the oldest and newest modified dates and the
    file types. Folders with the same shape (2026/01/archived, 2026/02/archived …) are rolled into one line."""
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"files": 0, "opened": 0, "first": None, "last": None,
                                                             "types": defaultdict(int), "folders": set(), "why": ""})
    for rel in rels:
        hit = history.folder(rel)
        if not hit:
            continue
        folder, why = hit
        shape = re.sub(r"\d", "#", folder)  # dated folders roll up: 2026/05 2026/20260414/archived -> ####/…
        g = groups[shape]
        g["files"] += 1
        g["opened"] += rel in opened
        g["folders"].add(folder)
        g["why"] = why
        g["types"][Path(rel).suffix.lower().lstrip(".") or "?"] += 1
        st = stats.get(rel)
        if st is not None:
            when = datetime.fromtimestamp(st.st_mtime, timezone.utc).date().isoformat()
            g["first"] = min(g["first"] or when, when)
            g["last"] = max(g["last"] or when, when)
    out = []
    for shape, g in sorted(groups.items(), key=lambda kv: -kv[1]["files"]):
        folders = sorted(g["folders"])
        out.append({"folder": folders[-1], "folders": len(folders),
                    "files": g["files"], "opened": g["opened"], "listed": g["files"] - g["opened"],
                    "first_modified": g["first"], "last_modified": g["last"], "why": g["why"],
                    "types": dict(sorted(g["types"].items(), key=lambda kv: -kv[1]))})
    return out


def describe(rows: list[dict[str, Any]], limit: int = 5) -> list[str]:
    """One line per history folder, for the scan's progress note and the folder card."""
    lines = []
    for r in rows[:limit]:
        where = f"{r['folder']}/" + (f" and {r['folders'] - 1:,} folders like it" if r["folders"] > 1 else "")
        span = f", modified {r['first_modified']} to {r['last_modified']}" if r.get("first_modified") else ""
        types = ", ".join(f"{n:,} {t}" for t, n in list(r["types"].items())[:3])
        lines.append(f"{where}: {r['files']:,} files ({types}){span}; {r['opened']:,} opened, "
                     f"{r['listed']:,} listed. History because it is {r['why']}.")
    if len(rows) > limit:
        lines.append(f"… and {len(rows) - limit} more history folder(s).")
    return lines
