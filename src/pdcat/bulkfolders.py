"""Adding many folders at once: a pasted list of paths, the subfolders of a parent folder, or a folder list
file (one path per line, or CSV with path, name and subfolders columns). Each becomes a row you can look over
(name, file count, size, overlaps with folders already added, how it will be treated) before one Add and scan
runs them one after another. The current folder list exports the same way, so a colleague can add the same
folders.
"""
from __future__ import annotations

import csv
import io
import os
import re
from pathlib import Path
from typing import Any

from .store import MANUAL_SOURCE, Catalogue

MAX_COUNT = 5000     # files counted per folder in the preview before saying "5000+"
MAX_SUBFOLDERS = 200


def parse_paths(text: str) -> list[str]:
    """Paths from pasted text: one per line, quotes and bullets stripped, blanks and repeats dropped."""
    out: list[str] = []
    for line in (text or "").splitlines():
        p = line.strip().strip('"').strip("'").lstrip("-•* ").strip()
        if p and p not in out and not p.startswith("#"):
            out.append(p)
    return out


def parse_list_file(data: bytes | str) -> list[dict[str, Any]]:
    """Rows from a folder list file: a plain list of paths, or CSV with a header (path, name, subfolders)."""
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    head = [h.strip().lower() for h in lines[0].split(",")]
    if "path" in head:
        rows = []
        for r in csv.DictReader(io.StringIO(text)):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            if r.get("path"):
                rows.append({"path": r["path"].strip('"'), "name": r.get("name", ""),
                             "recursive": (r.get("subfolders") or r.get("recursive") or "yes").lower()
                             not in ("no", "false", "0", "n")})
        return rows
    return [{"path": p, "name": "", "recursive": True} for p in parse_paths(text)]


def subfolders(parent: str) -> list[str]:
    """The folders directly inside a parent, hidden and tool folders left out."""
    root = Path(parent).expanduser()
    if not root.is_dir():
        return []
    try:
        subs = sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "__", "$")))
    except OSError:
        return []
    return [str(d) for d in subs[:MAX_SUBFOLDERS]]


def _count(root: Path, suffixes: set[str], recursive: bool) -> tuple[int, int, bool]:
    """(files, bytes, capped) for the data, Power BI, Excel and Access files under a folder, stopping at MAX_COUNT."""
    from .scanprogress import ENV_MARKERS
    files = total = 0
    for folder, dirs, names in os.walk(root, onerror=lambda e: None):
        if folder != str(root) and any(m in dirs or m in names for m in ENV_MARKERS):
            dirs[:] = []
            continue
        for n in names:
            if Path(n).suffix.lower() in suffixes:
                files += 1
                try:
                    total += os.stat(os.path.join(folder, n)).st_size
                except OSError:
                    pass
                if files >= MAX_COUNT:
                    return files, total, True
        if not recursive:
            break
    return files, total, False


def suggest_name(path: str, taken: set[str]) -> str:
    base = re.sub(r"[/\\]+", "-", Path(path).name or path.rstrip("/\\").split(os.sep)[-1] or "folder").strip("-") \
        or "folder"
    name, i = base, 2
    while name in taken:
        name, i = f"{base}-{i}", i + 1
    return name


def preview(cat: Catalogue, rows: list[dict[str, Any]], *, count: bool = True) -> list[dict[str, Any]]:
    """One row per folder to add: whether it exists, a free name, file count and size, folders it overlaps and
    how it would be treated (master folder or single project)."""
    from .profiling import DATA_SUFFIXES
    from .scan_access import SUFFIXES as ACCESS_SUFFIXES
    from .scan_excel import WORKBOOK_SUFFIXES
    from .scan_powerbi import REPORT_SUFFIXES
    suffixes = {s.lower() for s in (*DATA_SUFFIXES, *REPORT_SUFFIXES, ".pbip", *WORKBOOK_SUFFIXES, *ACCESS_SUFFIXES)}
    taken = (set(cat.sources()) | {f["name"] for f in cat.folders()} | {MANUAL_SOURCE})
    out = []
    seen: set[str] = set()
    for r in rows:
        path = str(r.get("path") or "").strip().strip('"')
        if not path:
            continue
        norm = path.replace("\\", "/").rstrip("/").lower()
        if norm in seen:
            continue
        seen.add(norm)
        p = Path(path).expanduser()
        exists = p.is_dir()
        name = (r.get("name") or "").strip() or suggest_name(path, taken)
        if name in taken:
            name = suggest_name(name, taken)
        taken.add(name)
        row: dict[str, Any] = {"add": exists, "name": name, "path": path, "subfolders": bool(r.get("recursive", True)),
                               "exists": exists, "files": None, "size": "", "overlaps": "", "treated as": ""}
        if exists:
            row["overlaps"] = ", ".join(cat.folder_overlaps(path))
            if count:
                files, total, capped = _count(p, suffixes, row["subfolders"])
                row["files"] = f"{files:,}+" if capped else files
                row["size"] = f"{total / 1024 ** 2:,.0f} MB" if total < 1024 ** 3 else f"{total / 1024 ** 3:,.1f} GB"
            from .folderroles import analyse
            found = analyse(path)
            row["treated as"] = f"master folder ({len(found['projects'])} projects)" if found["mode"] == "master" \
                else "single project"
        out.append(row)
    return out


def export_list(cat: Catalogue) -> str:
    """The folders already added, as a CSV folder list file (path, name, subfolders)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["path", "name", "subfolders"])
    for f in cat.folders():
        w.writerow([f["path"], f["name"], "yes" if f["recursive"] else "no"])
    return buf.getvalue()
