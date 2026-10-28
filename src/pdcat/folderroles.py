"""Folder roles: what each folder in a source is for.

When you add a folder the app looks at it. A *master folder* holds several projects: two or more
subfolders that each look like a project of their own (a .git folder, a README, environment.yml or
requirements, pyproject.toml or setup.py, a src folder). A *single project* keeps its subfolders
together, and each one gets a role from its name and what's in it:

  code      code/, src/, scripts/, notebooks/, sql/, lib/
  raw data  raw_data/, raw/, input/, inputs/, landing/: data that comes in from outside
  data      data/, processed/, interim/, clean/, staging/
  outputs   output/, outputs/, results/, exports/, reports/
  docs      docs/, doc/, documentation/
  archive   archive/, old/, backup/, bak/, legacy/, deprecated/: kept, but treated as old
  ignore    tmp/, temp/, cache/: not catalogued

Roles apply at any depth (forecasting/data/raw_data/ is raw data inside forecasting) and are kept by
folder path, so they survive rescans. You confirm or change each one; new subfolders that appear
later are suggested the same way.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import suggestions
from .scanprogress import has_files
from .store import Catalogue

ROLES = {"code": "Code", "raw data": "Raw data (comes in from outside)", "data": "Data", "outputs": "Outputs",
         "docs": "Docs", "archive": "Archive (treated as old)", "ignore": "Ignore (not catalogued)",
         "project": "A project of its own", "": "No particular role"}
NAME_ROLES: list[tuple[str, re.Pattern]] = [
    ("raw data", re.compile(r"^(raw[_ -]?data|raw|inputs?|landing|source[_ -]?data|incoming|external)$", re.I)),
    ("code", re.compile(r"^(code|src|source|scripts?|notebooks?|nb|sql|lib|libs|python|py|pipelines?|etl)$", re.I)),
    ("data", re.compile(r"^(data|datasets?|processed|interim|clean(ed)?|staging|intermediate|features)$", re.I)),
    ("outputs", re.compile(r"^(outputs?|results?|exports?|reports?|deliverables?|figures?|plots?|predictions?)$", re.I)),
    ("docs", re.compile(r"^(docs?|documentation|notes|wiki)$", re.I)),
    ("archive", re.compile(r"^(archive[ds]?|old|backups?|bak|legacy|deprecated|retired|_old|zz_?old)$", re.I)),
    ("ignore", re.compile(r"^(tmp|temp|cache|\.cache|scratch|__pycache__|\.ipynb_checkpoints|\.git|\.venv|venv|env|"
                          r"node_modules)$", re.I)),
]
PROJECT_MARKERS = (".git", "README.md", "README.txt", "README.rst", "readme.md", "environment.yml", "environment.yaml",
                   "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "src")
CODE_EXT = {".py", ".ipynb", ".sql", ".r", ".R"}
DATA_EXT = {".csv", ".parquet", ".xlsx", ".xls", ".txt", ".json", ".feather", ".pbix", ".accdb", ".mdb"}
MAX_DEPTH = 4


def norm(path: str | Path) -> str:
    return str(path).replace("\\", "/").rstrip("/").lower()


def role_from_name(name: str) -> str:
    for role, rx in NAME_ROLES:
        if rx.match(name):
            return role
    return ""


def _contents_role(folder: Path) -> str:
    code = data = 0
    try:
        for i, entry in enumerate(os.scandir(folder)):
            if i > 400:
                break
            if entry.is_file():
                ext = Path(entry.name).suffix
                code += ext in CODE_EXT
                data += ext.lower() in DATA_EXT
    except OSError:
        return ""
    if code >= 2 and code > data:
        return "code"
    if data >= 3 and data > 2 * code:
        return "data"
    return ""


def markers(folder: Path) -> list[str]:
    return [m for m in PROJECT_MARKERS if (folder / m).exists()]


def analyse(path: str | Path) -> dict[str, Any]:
    """{"mode": "master" | "single", "projects": [subfolder names], "roles": {relative path: role},
    "why": {relative path: reason}}."""
    root = Path(path).expanduser()
    subs = [d for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith(".")] if root.is_dir() else []
    looks = {d.name: markers(d) for d in subs}
    projects = [n for n, m in looks.items() if len(m) >= 2 or ".git" in m or "pyproject.toml" in m]
    mode = "master" if len(projects) >= 2 else "single"
    roles: dict[str, str] = {}
    why: dict[str, str] = {}
    for d in subs:
        if mode == "master" and d.name in projects:
            roles[d.name] = "project"
            why[d.name] = "has " + ", ".join(looks[d.name])
    for folder, depth in _walk(root):
        rel = folder.relative_to(root).as_posix()
        if rel in roles:
            continue
        role = role_from_name(folder.name)
        reason = f"named {folder.name}/" if role else ""
        if not role and depth == 1:
            role = _contents_role(folder)
            reason = f"mostly {role} files" if role else ""
        if role:
            roles[rel] = role
            why[rel] = reason
    return {"mode": mode, "projects": projects, "roles": roles, "why": why}


def as_mode(found: dict[str, Any], path: str | Path, mode: str) -> dict[str, Any]:
    """The analysis redone for the mode you picked. A master folder you name yourself takes every subfolder that
    holds code as a project; a single project drops the per-project roles."""
    if mode == found["mode"]:
        return found
    root = Path(path).expanduser()
    roles = {k: v for k, v in found["roles"].items() if v != "project"}
    why = {k: v for k, v in found["why"].items() if k in roles}
    projects: list[str] = []
    if mode == "master":
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            if d.is_dir() and not d.name.startswith(".") and role_from_name(d.name) not in ("ignore", "archive") and \
                    has_files(d, CODE_EXT):
                projects.append(d.name)
                roles[d.name] = "project"
                why[d.name] = "you said this is a master folder"
    return {"mode": mode, "projects": projects, "roles": roles, "why": why}


def _walk(root: Path):
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        if depth >= MAX_DEPTH:
            continue
        try:
            children = [Path(e.path) for e in os.scandir(folder) if e.is_dir()]
        except OSError:
            continue
        for c in sorted(children):
            yield c, depth + 1
            if role_from_name(c.name) not in ("ignore",):
                stack.append((c, depth + 1))


# ---- stored roles -----------------------------------------------------------------

def stored(cat: Catalogue) -> dict[str, dict[str, Any]]:
    """normalised absolute folder path -> {"role", "confirmed", "root"}."""
    return cat.setting("folder_roles", {})


def save(cat: Catalogue, root: str | Path, roles: dict[str, str], *, confirmed: bool = True,
         mode: str | None = None) -> None:
    """Keep roles for folders under `root` (relative paths). An empty role removes it."""
    table = stored(cat)
    base = norm(Path(root).expanduser())
    for rel, role in roles.items():
        k = f"{base}/{norm(rel)}" if rel else base
        if role:
            table[k] = {"role": role, "confirmed": confirmed, "root": base}
        else:
            table.pop(k, None)
    cat.set_setting("folder_roles", table)
    if mode:
        modes = cat.setting("folder_modes", {})
        modes[base] = mode
        cat.set_setting("folder_modes", modes)


def roles_under(cat: Catalogue, root: str | Path) -> dict[str, dict[str, Any]]:
    base = norm(Path(root).expanduser())
    return {k[len(base) + 1:]: v for k, v in stored(cat).items() if k.startswith(base + "/")}


def mode_of(cat: Catalogue, root: str | Path) -> str | None:
    return cat.setting("folder_modes", {}).get(norm(Path(root).expanduser()))


def role_of(path: str | None, table: dict[str, dict[str, Any]]) -> str:
    """The confirmed role of the deepest folder containing `path` ("" if none)."""
    if not path or not table:
        return ""
    p = norm(path)
    best, best_len = "", -1
    for k, v in table.items():
        if v.get("confirmed") and (p == k or p.startswith(k + "/")) and len(k) > best_len:
            best, best_len = v["role"], len(k)
    return best


def ignored_dirs(cat: Catalogue, root: str | Path) -> set[str]:
    """Relative folder paths under root marked "ignore", for the scanners to skip."""
    return {rel for rel, v in roles_under(cat, root).items() if v["role"] == "ignore" and v.get("confirmed")}


def item_roles(cat: Catalogue) -> dict[str, str]:
    """asset key -> role, for every item inside a folder with a confirmed role."""
    import json
    table = {k: v for k, v in stored(cat).items() if v.get("confirmed")}
    if not table:
        return {}
    out = {}
    for r in cat.conn.execute("SELECT key, attrs FROM assets WHERE status = 'active' AND kind NOT IN "
                              "('database', 'table', 'view', 'procedure') AND (attrs LIKE '%\"path\"%' OR attrs LIKE "
                              "'%\"full_path\"%')"):
        a = json.loads(r["attrs"])
        role = role_of(a.get("full_path") or a.get("path"), table)
        if role:
            out[r["key"]] = role
    return out


def archived(cat: Catalogue) -> set[str]:
    return {k for k, r in item_roles(cat).items() if r == "archive"}


def suggest_new(cat: Catalogue, root: str | Path, source: str) -> int:
    """After a rescan: subfolders with a likely role that aren't stored yet become suggestions."""
    known = roles_under(cat, root)
    found = analyse(root)
    out = []
    for rel, role in found["roles"].items():
        if rel in known or role == "project":
            continue
        out.append({"id": f"folder_role:{norm(Path(root))}/{norm(rel)}", "subject": f"files://{source}",
                    "title": f"{rel}/ in {source} looks like **{ROLES.get(role, role).lower()}**",
                    "detail": found["why"].get(rel, ""), "payload": {"root": str(root), "rel": rel, "role": role}})
    return suggestions.sync(cat, "folder_role", out, scope=f"files://{source}")


@suggestions.handler("folder_role")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    save(cat, p["root"], {p["rel"]: p["role"]})


def suggest_all(cat: Catalogue) -> int:
    """New subfolders in folders you already gave roles to."""
    n = 0
    for f in cat.folders():
        if mode_of(cat, f["path"]) and Path(f["path"]).is_dir():
            n += suggest_new(cat, f["path"], f["name"])
    return n
