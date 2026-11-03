"""Dated folder trees: walk only the newest weeks of folders named by date (2.8.1).

Daily files often sit in folders named by date, nested a few deep in any format:
Files/2026/05 2026/20260514/…, Files/2026/05/14/…, Files/2026-05/…, Files/May 2026/…
Walking every one of those folders on a network drive takes a long time on its own, before a single file is
read, and the old ones say nothing new. So when a folder's subfolders are mostly named by date, the scan:

1. finds the newest date by going down the newest subfolder at each level (a handful of folder listings),
2. when the deepest dated folders are days, walks only the folders from the last `dated_folders_days` days
   before that (31 by default, Settings), at every level: a year or month folder is walked when any part of it
   falls in that window (trees of month or year folders are small, and are walked in full),
3. leaves the older dated folders unopened, and says how many, from when to when.

Files catalogued from those folders by an earlier scan are kept as they were (not marked missing).
"Read every file" on a folder's card, or 0 days in Settings, walks everything.
"""
from __future__ import annotations

import calendar
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

DATED_FOLDERS_DAYS = 31   # days of dated folders walked, back from the newest (Settings: dated_folders_days)
MIN_DATED = 2             # a folder is a dated level when at least this many subfolders are named by date…
SHARE = 0.6               # …and they are most of its subfolders
_NUM = re.compile(r"^\d{1,2}$")


def span(name: str, parent: tuple[date, date] | None = None) -> tuple[date, date] | None:
    """(first day, last day) a folder named `name` covers, or None when its name isn't a date. A bare month or
    day number (2026/05/14) needs the parent's span."""
    from .series import _find
    hit = _find(name)
    if hit and re.sub(r"[Qq]?\{q\}|\{[a-z]+\}", "", hit[0]).strip(" -_.") == "":
        f = hit[1]  # the whole name is the date: 2026, 05 2026, 20260514, 2026-05, May 2026, 2026Q2
        y = f["y"]
        if "d" in f:
            d = date(y, f["m"], f["d"])
            return d, d
        if "m" in f:
            return date(y, f["m"], 1), date(y, f["m"], calendar.monthrange(y, f["m"])[1])
        if "q" in f:
            m = (f["q"] - 1) * 3 + 1
            return date(y, m, 1), date(y, m + 2, calendar.monthrange(y, m + 2)[1])
        return date(y, 1, 1), date(y, 12, 31)
    if parent and _NUM.match(name):
        n = int(name)
        first, last = parent
        if first.year == last.year and first.month == 1 and last.month == 12 and 1 <= n <= 12:
            return date(first.year, n, 1), date(first.year, n, calendar.monthrange(first.year, n)[1])
        if first.year == last.year and first.month == last.month and first.day == 1 and \
                1 <= n <= calendar.monthrange(first.year, first.month)[1]:
            d = date(first.year, first.month, n)
            return d, d
    return None


def dated_children(names: list[str], parent: tuple[date, date] | None = None) -> dict[str, tuple[date, date]]:
    """The subfolders named by date, when they make this folder a dated level (else {})."""
    spans = {n: s for n in names if (s := span(n, parent))}
    if len(spans) >= MIN_DATED and len(spans) >= SHARE * len(names):
        return spans
    return {}


def _subdirs(path: str) -> list[str]:
    try:
        return [e.name for e in os.scandir(path) if e.is_dir()]
    except OSError:
        return []


def newest_date(path: str, spans: dict[str, tuple[date, date]]) -> tuple[date, bool]:
    """Go down the newest dated subfolder at each level: (the newest day found or the end of the newest period,
    whether the deepest dated folders are days)."""
    today = date.today()

    def newest(spans: dict[str, tuple[date, date]]) -> tuple[date, date]:
        # A folder made ahead of time (or a typo like 20261230) mustn't count as the newest: start from the newest
        # one that has begun, so the real recent folders stay inside the window.
        started = [s for s in spans.values() if s[0] <= today]
        return max(started or spans.values(), key=lambda s: s[1])

    best = newest(spans)
    for _ in range(4):
        name = next(n for n, s in spans.items() if s == best)
        path = os.path.join(path, name)
        below = {n: s for n in _subdirs(path) if (s := span(n, best))}
        if not below:
            break
        spans, best = below, newest(below)
    return min(best[1], max(best[0], today)), best[0] == best[1]


@dataclass
class Tree:
    root: str                 # relative path of the folder whose subfolders are dated
    newest: date
    cutoff: date              # folders that end before this aren't walked
    skipped: int = 0
    skipped_first: date | None = None
    skipped_last: date | None = None
    skipped_rels: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {"folder": self.root or ".", "newest": self.newest.isoformat(), "from": self.cutoff.isoformat(),
                "skipped": self.skipped,
                "skipped_first": self.skipped_first.isoformat() if self.skipped_first else None,
                "skipped_last": self.skipped_last.isoformat() if self.skipped_last else None}


class DatedWalk:
    """Used by scanprogress.find_files: `prune(folder, rel, dirs)` gives back the subfolders to walk."""

    def __init__(self, days: int = DATED_FOLDERS_DAYS) -> None:
        self.days = days
        self.trees: list[Tree] = []
        self._spans: dict[str, tuple[date, date]] = {}  # relative folder -> its span, inside dated trees
        self._tree_of: dict[str, Tree] = {}

    def prune(self, folder: str, rel: str, dirs: list[str]) -> list[str]:
        if self.days <= 0 or not dirs:
            return dirs
        key = "" if rel == "." else rel
        tree = self._tree_of.get(key)
        if tree is not None:  # inside a dated tree any dated subfolder counts (2026 may only have 01 and 02 so far)
            spans = {n: s for n in dirs if (s := span(n, self._spans.get(key)))}
            for d in dirs:
                if d not in spans:  # …/20260514/archived stays in the tree, so deeper dated levels use its window
                    sub = f"{key}/{d}" if key else d
                    self._tree_of.setdefault(sub, tree)
                    if key in self._spans:
                        self._spans.setdefault(sub, self._spans[key])
        else:
            spans = dated_children(dirs)
        if not spans:
            return dirs
        if tree is None:  # the outermost dated level
            newest, daily = newest_date(folder, spans)
            # Only daily folders make crazy numbers; monthly or yearly ones (12 a year) are walked in full.
            tree = Tree(key, newest, newest - timedelta(days=self.days - 1) if daily else date.min)
            self.trees.append(tree)
        keep = []
        for d in dirs:
            sub = f"{key}/{d}" if key else d
            s = spans.get(d)
            if s is None:
                keep.append(d)  # not a dated folder: walk it as usual
                continue
            if s[1] < tree.cutoff:
                tree.skipped += 1
                tree.skipped_rels.append(sub)
                tree.skipped_first = min(tree.skipped_first or s[0], s[0])
                tree.skipped_last = max(tree.skipped_last or s[1], s[1])
                continue
            keep.append(d)
            self._spans[sub] = s
            self._tree_of[sub] = tree
        return keep

    def summaries(self) -> list[dict[str, Any]]:
        return [t.summary() for t in self.trees if t.skipped]

    def skipped_rels(self) -> list[str]:
        return [r for t in self.trees for r in t.skipped_rels]


def describe(rows: list[dict[str, Any]], limit: int = 5) -> list[str]:
    lines = []
    for r in rows[:limit]:
        lines.append(f"{r['folder']}/ is named by date: only folders from {r['from']} to {r['newest']} were walked; "
                     f"{r['skipped']:,} older dated folders ({r['skipped_first']} to {r['skipped_last']}) were "
                     "not opened.")
    if len(rows) > limit:
        lines.append(f"… and {len(rows) - limit} more dated folder trees.")
    return lines
