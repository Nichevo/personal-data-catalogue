"""Installed conda environments, read from each environment's conda-meta folder (one JSON file per
package) without running conda. Each environment records every package with its exact version,
build and channel, plus packages pip installed into it (from their .dist-info folders).

A project can be linked to the environment it runs in (guessed from the name in its
environment.yml, or picked by you), and the project page then compares what's installed with what
the code imports and what environment.yml asks for.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .envinfo import TOOLING, _STDLIB, normalise, package_for, project_environment
from .store import Catalogue, now


def candidate_bases() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    names = ("miniconda3", "anaconda3", "Miniconda3", "Anaconda3", "miniforge3", "Miniforge3", "mambaforge",
             "Mambaforge", "micromamba")
    bases = [home / n for n in names] + [home / "AppData" / "Local" / n for n in names]
    for env_var in ("CONDA_ROOT", "CONDA_PREFIX", "MAMBA_ROOT_PREFIX"):
        if os.environ.get(env_var):
            bases.append(Path(os.environ[env_var]))
    for root in (os.environ.get("ProgramData"), "/opt"):
        if root:
            bases += [Path(root) / n for n in names]
    return bases


def find_envs(extra: list[str] | None = None) -> dict[str, Path]:
    """name -> folder of every conda environment found on this machine."""
    paths: list[Path] = []
    listed = Path(os.environ.get("USERPROFILE") or Path.home()) / ".conda" / "environments.txt"
    if listed.is_file():
        paths += [Path(line.strip()) for line in listed.read_text(errors="replace").splitlines() if line.strip()]
    for base in candidate_bases() + [Path(e) for e in extra or []]:
        paths.append(base)
        if (base / "envs").is_dir():
            paths += [p for p in (base / "envs").iterdir() if p.is_dir()]
    user_envs = Path(os.environ.get("USERPROFILE") or Path.home()) / ".conda" / "envs"
    if user_envs.is_dir():
        paths += [p for p in user_envs.iterdir() if p.is_dir()]
    out: dict[str, Path] = {}
    for p in paths:
        try:
            if (p / "conda-meta").is_dir():
                name = "base" if (p / "envs").is_dir() and p.parent.name != "envs" else p.name
                out.setdefault(name if name not in out else f"{name} ({p})", p)
        except OSError:
            continue
    return out


def read_env(path: Path) -> dict[str, Any]:
    pkgs: dict[str, dict[str, Any]] = {}
    for f in (path / "conda-meta").glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if not d.get("name"):
            continue
        channel = d.get("channel") or d.get("schannel") or ""
        channel = re.sub(r"^https?://[^/]+/", "", channel).split("/")[0] if "/" in channel else channel
        pkgs[normalise(d["name"])] = {"name": d["name"], "version": d.get("version", ""), "build": d.get("build", ""),
                                      "channel": channel or "conda", "via": "conda"}
    for site in [path / "Lib" / "site-packages", *(path / "lib").glob("python3*/site-packages")]:
        if not site.is_dir():
            continue
        for info in site.glob("*.dist-info"):
            meta = info / "METADATA"
            name = version = None
            try:
                for line in meta.read_text(encoding="utf-8", errors="replace").splitlines()[:30]:
                    if line.startswith("Name:"):
                        name = line[5:].strip()
                    elif line.startswith("Version:"):
                        version = line[8:].strip()
            except OSError:
                continue
            installer = (info / "INSTALLER").read_text(errors="replace").strip() if (info / "INSTALLER").exists() else ""
            if name and normalise(name) not in pkgs:
                pkgs[normalise(name)] = {"name": name, "version": version or "", "build": "", "channel": "pypi",
                                         "via": installer or "pip"}
    python = pkgs.get("python", {}).get("version", "")
    return {"python": python, "packages": sorted(pkgs.values(), key=lambda p: p["name"].lower())}


def scan_envs(cat: Catalogue, extra: list[str] | None = None) -> int:
    extra = list(extra or []) + list(cat.setting("conda_paths", []))
    found = find_envs(extra)
    with cat.conn:
        for name, path in found.items():
            env = read_env(path)
            cat.conn.execute(
                "INSERT INTO conda_envs (name, path, python, packages, scanned_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET path = excluded.path, python = excluded.python, "
                "packages = excluded.packages, scanned_at = excluded.scanned_at",
                (name, str(path), env["python"], json.dumps(env["packages"]), now()))
    guess_project_envs(cat)
    cat.changed()
    return len(found)


def envs(cat: Catalogue) -> list[dict[str, Any]]:
    return [{**dict(r), "packages": json.loads(r["packages"])} for r in cat.conn.execute(
        "SELECT * FROM conda_envs ORDER BY name")]


def get_env(cat: Catalogue, name: str) -> dict[str, Any] | None:
    r = cat.conn.execute("SELECT * FROM conda_envs WHERE name = ?", (name,)).fetchone()
    return {**dict(r), "packages": json.loads(r["packages"])} if r else None


def guess_project_envs(cat: Catalogue) -> None:
    """Link projects to the environment named in their environment.yml, unless you picked one."""
    names = {r[0] for r in cat.conn.execute("SELECT name FROM conda_envs")}
    with cat.conn:
        for r in cat.conn.execute("SELECT key, attrs FROM assets WHERE kind = 'project'").fetchall():
            env = json.loads(r["attrs"]).get("environment") or {}
            for c in env.get("conda_envs", []):
                if c.get("name") in names:
                    cat.conn.execute("INSERT INTO project_envs (project_key, env_name, how) VALUES (?, ?, ?) "
                                     "ON CONFLICT(project_key) DO UPDATE SET env_name = excluded.env_name "
                                     "WHERE project_envs.how != 'you'", (r["key"], c["name"], "environment.yml name"))
                    break


def set_project_env(cat: Catalogue, project_key: str, env_name: str | None) -> None:
    with cat.conn:
        if env_name:
            cat.conn.execute("INSERT INTO project_envs (project_key, env_name, how) VALUES (?, ?, 'you') "
                             "ON CONFLICT(project_key) DO UPDATE SET env_name = excluded.env_name, how = 'you'",
                             (project_key, env_name))
        else:
            cat.conn.execute("DELETE FROM project_envs WHERE project_key = ?", (project_key,))
    cat.changed()


def project_env(cat: Catalogue, project_key: str) -> dict[str, Any] | None:
    r = cat.conn.execute("SELECT * FROM project_envs WHERE project_key = ?", (project_key,)).fetchone()
    return dict(r) if r else None


def compare_installed(cat: Catalogue, project_key: str, env_name: str) -> list[dict[str, Any]]:
    """One row per package the project imports or declares, or that's installed and imported:
    what's installed, what's asked for, and whether they agree."""
    env = get_env(cat, env_name)
    if env is None:
        return []
    installed = {normalise(p["name"]): p for p in env["packages"]}
    source = project_key.removeprefix("py://")
    proj = project_environment(cat, source)
    declared = {normalise(d["name"]): d for d in proj["deps"]}
    imported: dict[str, str] = {}
    for row in proj["third_party"] + proj["undeclared"]:
        imported.setdefault(normalise(row["package"]), row["module"])
    rows = []
    for pkg in sorted(set(declared) | set(imported)):
        if pkg in TOOLING or pkg in _STDLIB:
            continue
        inst = installed.get(pkg) or installed.get(normalise(package_for(imported.get(pkg, pkg))))
        want = declared.get(pkg, {}).get("version") or ""
        status = "ok"
        if not inst:
            status = "missing: not installed"
        elif want and not _satisfies(inst["version"], want):
            status = f"different version: wants {want}"
        elif pkg not in declared:
            status = "installed but not declared"
        rows.append({"package": declared.get(pkg, {}).get("name") or (inst or {}).get("name") or pkg,
                     "imported as": imported.get(pkg, ""), "declared": want or ("yes" if pkg in declared else ""),
                     "installed": inst["version"] if inst else "", "channel": inst["channel"] if inst else "",
                     "status": status})
    return rows


def _satisfies(version: str, spec: str) -> bool:
    """A light check of '>=2.0', '==1.5.*', '1.26', '<3,>=2'. Unclear specs count as satisfied."""
    def parts(v: str) -> tuple:
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.+-]", v)[:4])
    try:
        for clause in spec.replace(" ", "").split(","):
            m = re.match(r"^(==|>=|<=|!=|~=|>|<|=)?(.+)$", clause)
            if not m:
                continue
            op, want = m.group(1) or "==", m.group(2)
            if want.endswith(".*") or op in ("=", "==") and "*" in want:
                if not version.startswith(want.rstrip("*").rstrip(".")):
                    return False
                continue
            a, b = parts(version), parts(want)
            ok = {"==": a[:len(b)] == b, "=": a[:len(b)] == b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b,
                  "!=": a[:len(b)] != b, "~=": a >= b and a[:max(1, len(b) - 1)] == b[:max(1, len(b) - 1)]}[op]
            if not ok:
                return False
        return True
    except Exception:
        return True
