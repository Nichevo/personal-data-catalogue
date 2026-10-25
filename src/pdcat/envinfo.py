"""The environment a Python project expects: which packages it declares, in which
files, and how that compares with what its code actually imports.

Reads requirements*.txt (and a requirements/ folder), conda environment.yml,
pyproject.toml (PEP 621 and Poetry), setup.cfg, setup.py and Pipfile. Nothing is
installed or run; the files are only read.
"""
from __future__ import annotations

import ast
import configparser
import re
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

# Import name -> package name, where the two differ.
ALIASES = {
    "sklearn": "scikit-learn", "cv2": "opencv-python", "yaml": "pyyaml", "PIL": "pillow",
    "bs4": "beautifulsoup4", "dateutil": "python-dateutil", "dotenv": "python-dotenv",
    "docx": "python-docx", "pptx": "python-pptx", "jwt": "pyjwt", "Crypto": "pycryptodome",
    "OpenSSL": "pyopenssl", "win32com": "pywin32", "win32api": "pywin32", "pythoncom": "pywin32",
    "skimage": "scikit-image", "attr": "attrs", "google": "google-api-core", "lightgbm": "lightgbm",
    "mpl_toolkits": "matplotlib", "pkg_resources": "setuptools", "sqlalchemy": "sqlalchemy",
    "magic": "python-magic", "serial": "pyserial", "usb": "pyusb", "fitz": "pymupdf",
    "statsmodels": "statsmodels", "tables": "tables", "Levenshtein": "python-levenshtein",
}
# Packages that are tools, not imports, so "declared but not imported" isn't worth showing.
TOOLING = {
    "pip", "setuptools", "wheel", "python", "pytest", "pytest-cov", "black", "ruff", "flake8", "mypy",
    "isort", "pylint", "pre-commit", "ipykernel", "jupyter", "jupyterlab", "notebook", "ipython",
    "nbconvert", "nbformat", "twine", "build", "tox", "nox", "coverage", "sphinx", "conda", "mamba",
    "pyodbc-driver", "hatchling", "poetry-core", "flit-core", "setuptools-scm", "bandit", "pip-tools",
}
ENV_FILES = ("environment.yml", "environment.yaml", "pyproject.toml", "setup.cfg", "setup.py", "Pipfile")
_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _split(spec: str) -> tuple[str, str]:
    """'pandas>=2.0 ; python_version>"3.8"' -> ("pandas", ">=2.0")."""
    spec = spec.split(";")[0].split("#")[0].strip()
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$", spec)
    if not m:
        return "", ""
    return m.group(1), m.group(3).strip().replace(" ", "")


def _dep(spec: str, file: str, manager: str, **extra) -> dict | None:
    name, version = _split(spec)
    if not name:
        return None
    return {"name": name, "version": version, "file": file, "manager": manager, **extra}


def parse_requirements(text: str, file: str) -> tuple[list[dict], list[str]]:
    deps, editable = [], []
    for raw in text.splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-e ", "--editable ")):
            editable.append(line.split(None, 1)[1].strip())
            continue
        if line.startswith("-"):  # -r other.txt, --index-url …
            continue
        if line.startswith((".", "/", "file:")) or re.match(r"^[A-Za-z]:[\\/]", line):
            editable.append(line)
            continue
        if "@" in line and "://" in line:  # name @ git+https://…
            line = line.split("@")[0]
        d = _dep(line, file, "pip")
        if d:
            deps.append(d)
    return deps, editable


def parse_conda(text: str, file: str) -> dict[str, Any]:
    out: dict[str, Any] = {"deps": [], "editable": [], "channels": [], "name": None, "python": None}
    if yaml is not None:
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            data = None
    else:
        data = None
    if not isinstance(data, dict):
        data = _mini_yaml(text)
    out["name"] = data.get("name")
    out["channels"] = [str(c) for c in data.get("channels") or []]
    for item in data.get("dependencies") or []:
        if isinstance(item, dict) and "pip" in item:
            deps, editable = parse_requirements("\n".join(str(x) for x in item["pip"] or []), file)
            out["deps"] += deps
            out["editable"] += editable
        elif isinstance(item, str):
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$", item.split("::")[-1].strip())
            if not m:
                continue
            name, version = m.groups()
            version = version.strip()
            if version.startswith("=") and not version.startswith("=="):
                version = version[1:]  # conda's "pandas=2.1" means any 2.1.x
            if normalise(name) == "python":
                out["python"] = version.strip() or "any"
                continue
            out["deps"].append({"name": name, "version": version, "file": file, "manager": "conda"})
    return out


def _mini_yaml(text: str) -> dict:
    """Enough of YAML for a typical environment.yml when PyYAML isn't installed."""
    data: dict[str, Any] = {}
    section, pip = None, None
    for raw in text.splitlines():
        line = raw.split(" #")[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "-")):
            key, _, value = line.partition(":")
            section, pip = key.strip(), None
            data[section] = value.strip() or []
            continue
        item = line.strip()
        if not item.startswith("-"):
            continue
        item = item[1:].strip()
        if item.rstrip(":") == "pip" and item.endswith(":"):
            pip = []
            data.setdefault(section, []).append({"pip": pip})
        elif pip is not None and len(raw) - len(raw.lstrip()) > 2:
            pip.append(item)
        elif isinstance(data.get(section), list):
            pip = None
            data[section].append(item)
    return data


def parse_pyproject(text: str, file: str) -> dict[str, Any]:
    out: dict[str, Any] = {"deps": [], "python": None, "name": None}
    if tomllib is None:
        return out
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return out
    project = data.get("project") or {}
    out["name"] = project.get("name")
    out["python"] = project.get("requires-python")
    for spec in project.get("dependencies") or []:
        if d := _dep(spec, file, "pip"):
            out["deps"].append(d)
    for group, specs in (project.get("optional-dependencies") or {}).items():
        for spec in specs:
            if d := _dep(spec, file, "pip", group=group):
                out["deps"].append(d)
    poetry = (data.get("tool") or {}).get("poetry") or {}
    out["name"] = out["name"] or poetry.get("name")
    sections = [("", poetry.get("dependencies") or {}), ("dev", poetry.get("dev-dependencies") or {})]
    sections += [(g, v.get("dependencies") or {}) for g, v in (poetry.get("group") or {}).items()]
    for group, table in sections:
        for name, version in table.items():
            if normalise(name) == "python":
                out["python"] = out["python"] or str(version)
                continue
            v = version.get("version", "") if isinstance(version, dict) else str(version)
            out["deps"].append({"name": name, "version": "" if v == "*" else v, "file": file, "manager": "poetry",
                                **({"group": group} if group else {})})
    return out


def parse_setup_cfg(text: str, file: str) -> list[dict]:
    cp = configparser.ConfigParser()
    try:
        cp.read_string(text)
    except configparser.Error:
        return []
    raw = cp.get("options", "install_requires", fallback="")
    return [d for line in raw.splitlines() if line.strip() and (d := _dep(line, file, "pip"))]


def parse_setup_py(text: str, file: str) -> list[dict]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "install_requires" and isinstance(node.value, (ast.List, ast.Tuple)):
            return [d for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    and (d := _dep(e.value, file, "pip"))]
    return []


def parse_pipfile(text: str, file: str) -> list[dict]:
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    out = []
    for section, group in (("packages", ""), ("dev-packages", "dev")):
        for name, version in (data.get(section) or {}).items():
            v = version.get("version", "") if isinstance(version, dict) else str(version)
            out.append({"name": name, "version": "" if v == "*" else v, "file": file, "manager": "pipenv",
                        **({"group": group} if group else {})})
    return out


def read_environment(root: Path, skip: set[str] = frozenset()) -> dict[str, Any]:
    """Everything the project's environment files declare. Only files in the project
    folder itself and a requirements/ subfolder are read."""
    env: dict[str, Any] = {"files": [], "deps": [], "editable": [], "python": [], "conda_envs": []}
    candidates = sorted(root.glob("requirements*.txt")) + sorted((root / "requirements").glob("*.txt"))
    candidates += [root / f for f in ENV_FILES if (root / f).is_file()]
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        env["files"].append(rel)
        name = path.name.lower()
        if name.endswith(".txt"):
            deps, editable = parse_requirements(text, rel)
            env["deps"] += deps
            env["editable"] += editable
        elif name in ("environment.yml", "environment.yaml"):
            c = parse_conda(text, rel)
            env["deps"] += c["deps"]
            env["editable"] += c["editable"]
            env["conda_envs"].append({"file": rel, "name": c["name"], "channels": c["channels"]})
            if c["python"]:
                env["python"].append({"file": rel, "version": c["python"]})
        elif name == "pyproject.toml":
            p = parse_pyproject(text, rel)
            env["deps"] += p["deps"]
            if p["python"]:
                env["python"].append({"file": rel, "version": p["python"]})
        elif name == "setup.cfg":
            env["deps"] += parse_setup_cfg(text, rel)
        elif name == "setup.py":
            env["deps"] += parse_setup_py(text, rel)
        elif name == "pipfile":
            env["deps"] += parse_pipfile(text, rel)
    return env


def package_for(module: str) -> str:
    """Best guess at the package that provides an import, e.g. sklearn -> scikit-learn."""
    if module in ALIASES:
        return ALIASES[module]
    try:
        from importlib.metadata import packages_distributions
        dists = packages_distributions().get(module)
        if dists:
            return dists[0]
    except Exception:  # metadata can be odd in some environments
        pass
    return module


def compare(env: dict[str, Any], imported: dict[str, list[str]], home_built: set[str]) -> dict[str, Any]:
    """Match imports against declared packages.

    imported: top-level module -> files that import it (stdlib already removed or not).
    home_built: top-level modules that come from your own catalogued code.
    """
    declared = {normalise(d["name"]) for d in env["deps"]}
    used, undeclared = [], []
    for module, files in sorted(imported.items()):
        if module in _STDLIB or module in home_built or not module:
            continue
        pkg = package_for(module)
        row = {"module": module, "package": pkg, "files": len(files), "examples": ", ".join(sorted(files)[:3])}
        if normalise(pkg) in declared or normalise(module) in declared:
            used.append(row)
        else:
            undeclared.append(row)
    imported_pkgs = {normalise(package_for(m)) for m in imported} | {normalise(m) for m in imported}
    unused = [d for d in env["deps"] if normalise(d["name"]) not in imported_pkgs
              and normalise(d["name"]) not in TOOLING and not d.get("group")]
    return {"third_party": used, "undeclared": undeclared, "unused": unused}


def project_environment(cat, source: str) -> dict[str, Any]:
    """Declared environment plus how it compares with the project's imports, for one
    scanned project or library."""
    import json
    row = cat.conn.execute("SELECT attrs FROM assets WHERE key = ?", (f"py://{source}",)).fetchone()
    env = (json.loads(row["attrs"]).get("environment") if row else None) or \
        {"files": [], "deps": [], "editable": [], "python": [], "conda_envs": []}
    imported: dict[str, list[str]] = {}
    home_built: set[str] = set()
    for r in cat.conn.execute("SELECT name, source, attrs FROM assets WHERE kind IN ('script', 'notebook') "
                              "AND status = 'active'"):
        attrs = json.loads(r["attrs"])
        home_built.update(m.split(".")[0] for m in attrs.get("modules", []))
        if r["source"] == source:
            for m in attrs.get("imports", []):
                imported.setdefault(m, []).append(r["name"])
            # A sibling module imported as a plain top-level name (scripts run from their folder).
            home_built.add(Path(r["name"]).stem)
    return {**env, **compare(env, imported, home_built)}


def libraries_overview(cat) -> list[dict[str, str]]:
    """One row per third-party package, one column per project/library: the version it
    declares, or "imported" when the code imports it without declaring it."""
    sources = [r["source"] for r in cat.conn.execute(
        "SELECT source FROM assets WHERE kind = 'project' AND status = 'active' ORDER BY source")]
    table: dict[str, dict[str, str]] = {}
    for source in sources:
        env = project_environment(cat, source)
        for d in env["deps"]:
            if normalise(d["name"]) in TOOLING:
                continue
            row = table.setdefault(normalise(d["name"]), {"package": d["name"]})
            versions = row.setdefault(source, [])
            if (d["version"] or "any") not in versions:
                versions.append(d["version"] or "any")
        for u in env["undeclared"]:
            row = table.setdefault(normalise(u["package"]), {"package": u["package"]})
            row.setdefault(source, ["imported, not declared"])
    return [{"package": r["package"], **{s: " · ".join(r.get(s, [])) for s in sources}}
            for _, r in sorted(table.items())]
