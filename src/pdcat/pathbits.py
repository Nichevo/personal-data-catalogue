"""File names and folders apart: a scanned file's name is its path inside the scanned folder
("Finance/Monthly/Sales.pbix"), which buries the file name in long lists. These helpers split it into the
file name and its folder, and shorten a long folder to its last parts ("…/Monthly/Sales") for tables, keeping
the full folder for a hover or a "Full folder paths" switch."""
from __future__ import annotations

# Kinds whose name is never a path, even when it has a slash in it ("Sales / Units" is a measure's name).
NOT_PATHS = frozenset({"table", "view", "procedure", "database", "query", "metric", "knowledge", "question",
                       "feature", "key", "bproject", "note", "group", "measure", "column", "sql_snippet",
                       "pbi_table", "pq_query", "data_source"})
KEEP = 2  # folder parts kept when shortening


def _clean(text: str | None) -> str:
    return str(text or "").replace("\\", "/").rstrip("/")


def split_name(name: str | None, kind: str = "") -> tuple[str, str]:
    """("Sales.pbix", "Finance/Monthly") for "Finance/Monthly/Sales.pbix"; (name, "") when it has no folder."""
    n = _clean(name)
    if kind in NOT_PATHS or "/" not in n or "://" in n:
        return str(name or ""), ""
    folder, _, file = n.rpartition("/")
    return file, folder


def file_name(name: str | None, kind: str = "") -> str:
    return split_name(name, kind)[0]


def short_folder(folder: str | None, keep: int = KEEP) -> str:
    """The last `keep` parts of a folder, "…/" in front when there were more."""
    parts = [p for p in _clean(folder).split("/") if p]
    if len(parts) <= keep:
        return "/".join(parts)
    return "…/" + "/".join(parts[-keep:])


def joined(*parts: str | None) -> str:
    return "/".join(p for p in (_clean(x) for x in parts) if p)


def parent_folder(path: str | None) -> str:
    """The folder a full path is in ("C:/Data/Finance" for "C:/Data/Finance/Sales.pbix")."""
    p = _clean(path)
    return p.rpartition("/")[0] if "/" in p else ""


def place(row) -> dict[str, str]:
    """{"folder": short folder, "_full_folder": full folder} for an assets row (name, source, attrs) of a file,
    for the Power BI lists: the folder inside the scanned folder, and the folder on disk."""
    import json
    attrs = row["attrs"]
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs or "{}")
        except ValueError:
            attrs = {}
    _, rel = split_name(row["name"], "report")
    folder = joined(row["source"], rel)
    return {"folder": short_folder(folder), "_full_folder": parent_folder((attrs or {}).get("path")) or folder}
