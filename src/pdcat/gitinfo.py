"""Git history for scripts: who changed each one last, when, and the commit messages. Used for a
change log on script pages, for "not touched in a long time", and to suggest owners.

Runs `git log` once per repository (read-only). If git isn't installed, or a project isn't in a
repository, this is skipped quietly.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import suggestions
from .store import Catalogue

MAX_COMMITS = 2000
SEP = "\x1f"


def repo_root(path: Path) -> Path | None:
    for p in [path, *path.parents]:
        if (p / ".git").exists():
            return p
    return None


def read_log(root: Path, since_days: int = 1095) -> dict[str, list[dict[str, str]]]:
    """relative path (lower case, forward slashes) -> commits touching it, newest first."""
    git = shutil.which("git")
    if not git:
        return {}
    since = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
    try:
        out = subprocess.run([git, "-C", str(root), "log", f"--since={since}", f"-n{MAX_COMMITS}", "--name-only",
                              f"--format=@@{SEP}%H{SEP}%aI{SEP}%an{SEP}%s"],
                             capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    files: dict[str, list[dict[str, str]]] = defaultdict(list)
    current = None
    for line in out.stdout.splitlines():
        if line.startswith("@@" + SEP):
            _, commit, at, author, msg = (line.split(SEP) + ["", "", "", "", ""])[:5]
            current = {"commit": commit, "at": at, "author": author, "message": msg}
        elif line.strip() and current:
            files[line.strip().replace("\\", "/").lower()].append(current)
    return files


def scan_git(cat: Catalogue) -> int:
    """Read git history for every catalogued script inside a repository."""
    by_repo: dict[Path, list[tuple[str, str]]] = defaultdict(list)
    for r in cat.conn.execute("SELECT key, attrs FROM assets WHERE kind IN ('script', 'notebook', 'sql_script') "
                              "AND status = 'active'"):
        full = json.loads(r["attrs"]).get("full_path")
        if not full:
            continue
        root = repo_root(Path(full).parent)
        if root:
            by_repo[root].append((r["key"], Path(full).resolve().relative_to(root.resolve()).as_posix().lower()
                                  if Path(full).resolve().is_relative_to(root.resolve()) else ""))
    n = 0
    with cat.conn:
        for root, items in by_repo.items():
            log = read_log(root)
            if not log:
                continue
            for key, rel in items:
                commits = log.get(rel, [])
                cat.conn.execute("DELETE FROM git_history WHERE asset_key = ?", (key,))
                cat.conn.executemany(
                    "INSERT OR IGNORE INTO git_history (asset_key, commit_id, at, author, message) VALUES (?, ?, ?, ?, ?)",
                    [(key, c["commit"], c["at"], c["author"], c["message"]) for c in commits[:200]])
                n += bool(commits)
    suggest_owners(cat)
    return n


def history(cat: Catalogue, key: str, limit: int = 50) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        "SELECT * FROM git_history WHERE asset_key = ? ORDER BY at DESC LIMIT ?", (key, limit))]


def last_change(cat: Catalogue) -> dict[str, dict[str, str]]:
    """asset key -> its newest commit."""
    return {r["asset_key"]: dict(r) for r in cat.conn.execute(
        "SELECT asset_key, MAX(at) AS at, author, message FROM git_history GROUP BY asset_key")}


def suggest_owners(cat: Catalogue) -> int:
    """The person who made most of a script's commits in the last year, for scripts with no owner."""
    owned = {r[0] for r in cat.conn.execute("SELECT asset_key FROM item_info WHERE COALESCE(owner, '') != ''")}
    year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    by_key: dict[str, Counter] = defaultdict(Counter)
    for r in cat.conn.execute("SELECT asset_key, author FROM git_history WHERE at >= ?", (year_ago,)):
        if r["author"]:
            by_key[r["asset_key"]][r["author"]] += 1
    found = []
    for key, c in by_key.items():
        if key in owned:
            continue
        who, n = c.most_common(1)[0]
        total = sum(c.values())
        if n >= 2 and n / total >= 0.5:
            found.append({"id": f"owner:{key}", "subject": key, "title": f"Owner: **{who}**",
                          "detail": f"{n} of {total} commits in the last year", "score": n,
                          "payload": {"owner": who}})
    return suggestions.sync(cat, "owner", found)


@suggestions.handler("owner")
def _accept_owner(cat: Catalogue, p: dict[str, Any]) -> None:
    from .curation import set_item_info
    set_item_info(cat, p["_subject"], owner=p["owner"])
