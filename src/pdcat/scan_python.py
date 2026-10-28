"""Scan a Python project folder: scripts, notebooks and .sql files.

For each file it records the module docstring, functions and imports, then
works out which tables and files the code reads and writes by looking at:
  * SQL in string literals (and f-strings, with the dynamic bits ignored)
  * pandas calls such as read_sql_table / to_sql / read_csv / to_parquet
  * open(), joblib.dump/load and save_model/load_model with a literal path
"""
from __future__ import annotations

import ast
import contextvars
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .config import PythonSource
from .sqlrefs import extract_refs, looks_like_sql
from .scan_files import ingest_files, iter_data_files
from .envinfo import read_environment
from .store import Catalogue

SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", ".env", "site-packages",
             "node_modules", ".ipynb_checkpoints", ".mypy_cache", ".pytest_cache", ".tox", "build", "dist"}

FILE_READERS = {"read_csv", "read_parquet", "read_excel", "read_json", "read_feather", "read_pickle",
                "read_table", "read_fwf", "read_hdf", "read_orc", "read_xml", "load_model"}
FILE_WRITERS = {"to_csv", "to_parquet", "to_excel", "to_json", "to_feather", "to_pickle", "to_hdf",
                "to_orc", "to_xml", "save_model", "savefig"}


@dataclass
class Found:
    """What one file uses: (relation, dst_ref, line) tuples."""
    refs: list[tuple[str, str, int]] = field(default_factory=list)
    description: str | None = None
    attrs: dict = field(default_factory=dict)
    how: dict[tuple[str, str], str] = field(default_factory=dict)  # how each link was found


def scan_project(cat: Catalogue, src: PythonSource) -> dict[str, int]:
    root = Path(src.path)
    if not root.is_dir():
        raise FileNotFoundError(f"Python project folder not found: {root}")
    project_key = f"py://{src.name}"
    skip = SKIP_DIRS | set(src.exclude)
    counts = {"files": 0, "data files": 0, "links": 0, "errors": 0}

    token = _CONFIG.set(read_config_values(root))
    try:
        return _scan_project(cat, src, root, project_key, skip, counts)
    finally:
        _CONFIG.reset(token)


def _scan_project(cat: Catalogue, src: PythonSource, root: Path, project_key: str, skip: set[str],
                  counts: dict[str, int]) -> dict[str, int]:
    with cat.scan(src.name):
        readme = _readme_summary(root)
        cat.upsert_asset(project_key, "project", src.name, src.name, description=readme,
                         attrs={"path": str(root), "role": src.role, "environment": read_environment(root)})
        from .folderroles import ignored_dirs
        from .scan_files import under
        ignored = ignored_dirs(cat, root)
        paths = sorted(p for p in _iter_files(root, skip) if not under(p.relative_to(root).as_posix(), ignored))
        token = _PROJECT.set(project_constants(root, paths))
        try:
            _scan_files(cat, src, root, project_key, paths, counts)
        finally:
            _PROJECT.reset(token)
        from .mlfeatures import scan_models
        counts["models"] = scan_models(cat, root, project_key, src.name, skip)
        if src.data_files:
            got = ingest_files(cat, root, sorted(iter_data_files(root, skip=skip)), project_key, src.name, src.max_rows)
            counts["data files"] += got["files"]
            counts["errors"] += got["errors"]
    return counts


def _scan_files(cat: Catalogue, src: PythonSource, root: Path, project_key: str, paths: list[Path],
                counts: dict[str, int]) -> None:
    for path in paths:
        rel = path.relative_to(root).as_posix()
        found = scan_file(path)
        try:
            st = path.stat()
            found.attrs["full_path"] = str(path)
            found.attrs["mtime"] = st.st_mtime
        except OSError:
            pass
        if path.suffix.lower() == ".py":
            found.attrs["modules"] = module_names(rel, root)
        kind = {".py": "script", ".ipynb": "notebook", ".sql": "sql_script"}[path.suffix.lower()]
        key = f"{project_key}/{rel}"
        cat.upsert_asset(key, kind, rel, src.name, parent=project_key,
                         description=found.description, attrs=found.attrs)
        for relation, raw_ref, line in found.refs:
            dst_ref = _normalise_ref(raw_ref)
            how = found.how.get((relation, raw_ref)) or ""
            if dst_ref != raw_ref:
                how = f"{how}, relative path" if how else "relative path"
            cat.add_link(key, relation, dst_ref, src.name, hint=src.default_database,
                         evidence=f"{rel}:{line}" if line else rel, how=how or None)
            counts["links"] += 1
        counts["files"] += 1
        counts["errors"] += bool(found.attrs.get("parse_error"))


def _iter_files(root: Path, skip: set[str]):
    for path in root.rglob("*"):
        if path.suffix.lower() not in (".py", ".ipynb", ".sql") or not path.is_file():
            continue
        if any(part in skip for part in path.relative_to(root).parts[:-1]):
            continue
        yield path


def _readme_summary(root: Path) -> str | None:
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        p = root / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            # Skip headings and badge lines; use the first paragraph of prose.
            prose = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", "[![", "<", "=", "---")))
            return _first_paragraph(prose, max_len=1000) or None
    return None


def scan_file(path: Path) -> Found:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".sql":
        found = Found(attrs={"lines": text.count("\n") + 1})
        found.refs = [(r.relation, f"table:{r.name}", 0) for r in extract_refs(text)]
        first = text.lstrip().splitlines()[0] if text.strip() else ""
        if first.startswith("--"):
            found.description = first.lstrip("- ").strip()
        if text.strip():
            found.attrs["sql_strings"] = [{"sql": text[:MAX_SQL_LEN], "line": 1, "name": path.stem, "function": None,
                                           "built_from_variables": False, "whole_file": True}]
        return found
    if suffix == ".ipynb":
        return _scan_notebook(text)
    return scan_python_source(text)


def _scan_notebook(text: str) -> Found:
    found = Found()
    try:
        nb = json.loads(text)
    except json.JSONDecodeError as e:
        found.attrs["parse_error"] = f"Not valid notebook JSON: {e}"
        return found
    cells = nb.get("cells", [])
    funcs, imports, errors, import_details, feature_rows, runs = [], set(), [], [], [], []
    unknown: list[dict] = []
    patterns: list[str] = []
    sql_strings: list[dict] = []
    merged: dict[str, dict] = {"function_info": {}, "import_map": {}, "function_refs": {}}
    for i, cell in enumerate(cells, start=1):
        source = "".join(cell.get("source", []))
        if cell.get("cell_type") == "markdown" and found.description is None and source.strip():
            found.description = _markdown_summary(source)
            continue
        if cell.get("cell_type") != "code":
            continue
        if cell.get("source") and source.lstrip().lower().startswith(("%%sql", "%sql")):
            body = source.split("\n", 1)[-1]
            found.refs += [(r.relation, f"table:{r.name}", 0) for r in extract_refs(body)]
            sql_strings.append({"sql": body[:MAX_SQL_LEN], "line": 0, "cell": i, "name": f"cell {i}",
                                "function": None, "built_from_variables": False})
            continue
        # %run other.py and !python other.py run another script.
        runs += [{"target": t, "line": 0, "how": f"notebook {how} (cell {i})"}
                 for ln in source.splitlines() for t, how in _magic_runs(ln.strip())]
        # Drop IPython magics and shell lines so the cell parses as Python.
        code = "\n".join("" if ln.lstrip().startswith(("%", "!")) else ln for ln in source.splitlines())
        cell_found = scan_python_source(code)
        if cell_found.attrs.get("parse_error"):
            errors.append(f"cell {i}")
        found.refs += [(rel, ref, 0) for rel, ref, _ in cell_found.refs]
        funcs += cell_found.attrs.get("functions", [])
        imports.update(cell_found.attrs.get("imports", []))
        import_details += cell_found.attrs.get("import_details", [])
        feature_rows += cell_found.attrs.get("feature_assignments", [])
        runs += [{**r, "line": 0, "how": f"{r['how']} (cell {i})"} for r in cell_found.attrs.get("runs", [])]
        unknown += [{**u, "cell": i} for u in cell_found.attrs.get("unknown_files", [])]
        sql_strings += [{**q, "cell": i} for q in cell_found.attrs.get("sql_strings", [])]
        patterns += cell_found.attrs.get("write_patterns", [])
        for name, value in merged.items():
            for k, v in cell_found.attrs.get(name, {}).items():
                if name == "function_info":
                    v = {**v, "cell": i}
                    k = f"(cell {i})" if k == "(script)" else k
                value[k] = v
    found.refs = list(dict.fromkeys(found.refs))
    found.attrs.update({"cells": len(cells), "functions": funcs, "imports": sorted(imports),
                        "import_details": import_details, **{k: v for k, v in merged.items() if v}})
    if feature_rows:
        found.attrs["feature_assignments"] = feature_rows
    if runs:
        found.attrs["runs"] = runs
    if unknown:
        found.attrs["unknown_files"] = unknown
    if sql_strings:
        found.attrs["sql_strings"] = sql_strings[:MAX_SQL]
    if patterns:
        found.attrs["write_patterns"] = sorted(set(patterns))
    if errors:
        found.attrs["parse_error"] = "Could not parse " + ", ".join(errors)
    return found


def scan_python_source(text: str) -> Found:
    found = Found(attrs={"lines": text.count("\n") + 1})
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        found.attrs["parse_error"] = f"line {e.lineno}: {e.msg}"
        return found

    doc = ast.get_docstring(tree)
    if doc:
        found.description = _first_paragraph(doc)
    found.attrs["functions"] = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    found.attrs["classes"] = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imports.add(node.module.split(".")[0])
    found.attrs["imports"] = sorted(imports)

    # Docstrings are prose, and the literal pieces inside an f-string are handled with the f-string.
    skip = _docstring_nodes(tree) | {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    consts = _module_constants(tree)
    env = build_env(tree, text)
    collected = _collect_refs(tree, skip, consts, env)
    found.refs = [(rel, ref, line) for (rel, ref), (line, _) in collected.items()]
    found.how = {k: how for k, (_, how) in collected.items() if how}

    # Which function touches which data, so a project calling one library function
    # can be traced to exactly the tables and files that function uses.
    function_refs = {}
    for name, node in _functions(tree):
        refs = sorted({(rel, ref) for (rel, ref) in _collect_refs(node, skip, consts, env)})
        if refs:
            function_refs[name] = [list(r) for r in refs]
    if function_refs:
        found.attrs["function_refs"] = function_refs
    from .mlfeatures import feature_assignments
    feats = feature_assignments(tree)
    if feats:
        found.attrs["feature_assignments"] = feats
    found.attrs["import_details"] = _import_details(tree)
    found.attrs["import_map"] = _import_map(tree)
    found.attrs["function_info"] = _function_info(tree)
    paths = _sys_path_additions(tree, consts)
    if paths:
        found.attrs["sys_path"] = paths
    runs = run_targets(tree, env)
    if runs:
        found.attrs["runs"] = runs
    if env.unknown:
        found.attrs["unknown_files"] = sorted(env.unknown.values(), key=lambda u: u["line"])
    if env.patterns:
        found.attrs["write_patterns"] = sorted(set(env.patterns))
    if env.sql:
        found.attrs["sql_strings"] = sorted(env.sql.values(), key=lambda q: q["line"])
    return found


def _collect_refs(root: ast.AST, skip: set[int], consts: dict[str, str], env: dict[str, ast.AST] | None = None
                  ) -> dict[tuple[str, str], tuple[int, str]]:
    """{(relation, ref): (line, how)} for everything this code reads and writes."""
    env = env if env is not None else {}
    refs: dict[tuple[str, str], tuple[int, str]] = {}
    for node in ast.walk(root):
        if isinstance(node, (ast.Constant, ast.JoinedStr)) and id(node) not in skip:
            s = _string_value(node, consts)
            if s and looks_like_sql(s):
                for r in extract_refs(s):
                    refs.setdefault((r.relation, f"table:{r.name}"), (node.lineno, "SQL in the code"))
                if isinstance(env, Env) and len(env.sql) < MAX_SQL:
                    env.sql.setdefault((node.lineno, s[:200]), {
                        "sql": s[:MAX_SQL_LEN], "line": node.lineno, "name": _sql_name(node, env),
                        "function": env.enclosing.get(id(node)),
                        "built_from_variables": isinstance(node, ast.JoinedStr) and "{}" in s})
            elif s and s.lstrip().lower().startswith("bcp "):
                for rel, ref in _bcp_refs(s.split()):
                    refs.setdefault((rel, ref), (node.lineno, "bcp command"))
        elif isinstance(node, (ast.List, ast.Tuple)) and node.elts and \
                isinstance(node.elts[0], ast.Constant) and str(node.elts[0].value).lower() in ("bcp", "bcp.exe"):
            parts = [_expr_value(e, env)[0] or "{}" for e in node.elts]
            for rel, ref in _bcp_refs(parts):
                refs.setdefault((rel, ref), (node.lineno, "bcp command"))
        elif isinstance(node, ast.Call):
            for relation, ref, how in _call_refs(node, env):
                refs.setdefault((relation, ref), (node.lineno, how))
    return refs


def _bcp_refs(parts: list[str]) -> list[tuple[str, str]]:
    """bcp dbo.Sales in sales.csv … loads the file into the table; out/queryout does the reverse."""
    if len(parts) < 4:
        return []
    table, direction, path = parts[1].strip('"'), parts[2].lower(), parts[3].strip('"')
    if direction == "in":
        return [("writes", f"table:{table.lower()}"), ("reads", f"file:{path}")]
    if direction == "out":
        return [("reads", f"table:{table.lower()}"), ("writes", f"file:{path}")]
    if direction == "queryout":
        return [(r.relation, f"table:{r.name}") for r in extract_refs(table)] + [("writes", f"file:{path}")]
    return []


# ---- following paths and names through variables, os.path.join, pathlib and config files ----

_CONFIG: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("pdcat_config_values", default={})
CONFIG_FILES = ("config.yaml", "config.yml", "config.json", "config.toml", "settings.yaml", "settings.yml",
                "settings.json", "settings.toml", "config.ini", "settings.ini", "paths.yaml", "paths.json")


def read_config_values(root: Path) -> dict[str, str]:
    """String values from the project's config files, by key ("sales_path" -> "data/sales.csv"),
    so cfg["sales_path"] in code can be followed to the file."""
    values: dict[str, str] = {}
    candidates = [root / n for n in CONFIG_FILES] + [root / "config" / n for n in CONFIG_FILES] + \
        list((root / "config").glob("*.y*ml")) if (root / "config").is_dir() else [root / n for n in CONFIG_FILES]
    for path in dict.fromkeys(candidates):
        if not path.is_file():
            continue
        try:
            data = _load_config_file(path)
        except Exception:
            continue
        _flatten(data, values)
    return values


def _load_config_file(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(text)
    if suffix == ".toml":
        import tomllib
        return tomllib.loads(text)
    if suffix == ".ini":
        import configparser
        cp = configparser.ConfigParser()
        cp.read_string(text)
        return {sec: dict(cp[sec]) for sec in cp.sections()}
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        from .envinfo import _mini_yaml
        return _mini_yaml(text)


def _flatten(data, out: dict[str, str]) -> None:
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, str):
                out.setdefault(str(k), v)
            else:
                _flatten(v, out)
    elif isinstance(data, list):
        for v in data:
            _flatten(v, out)


# ---- one script running another --------------------------------------------------

SUBPROCESS_CALLS = {"run", "call", "Popen", "check_call", "check_output"}


def run_targets(tree: ast.AST, env: dict[str, ast.AST]) -> list[dict]:
    """Other scripts this code starts: subprocess / os.system with "python x.py" or "-m pkg.mod",
    runpy, importlib.import_module and exec(open("x.py").read()). Each is {"target", "line", "how"};
    a target is a path ending in .py, or "module:dotted.name"."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
        first = node.args[0] if node.args else None
        found: list[tuple[str, str]] = []
        if owner == "subprocess" and name in SUBPROCESS_CALLS and first is not None:
            found = [(t, f"subprocess.{name}") for t in _command_targets(first, env)]
        elif owner == "os" and name in ("system", "popen", "startfile") and first is not None:
            found = [(t, f"os.{name}") for t in _command_targets(first, env)]
        elif owner == "runpy" and name in ("run_path", "run_module") and first is not None:
            value, _ = _expr_value(first, env)
            if value and "{}" not in value:
                found = [(value if name == "run_path" else f"module:{value}", f"runpy.{name}")]
        elif name == "import_module" and first is not None and not any(k.arg == "package" for k in node.keywords):
            value, _ = _expr_value(first, env)
            if value and "{}" not in value and not value.startswith("."):
                found = [(f"module:{value}", "importlib.import_module")]
        elif name == "exec" and isinstance(first, ast.Call) and isinstance(first.func, ast.Attribute) \
                and first.func.attr == "read" and isinstance(first.func.value, ast.Call):
            opened = first.func.value
            if isinstance(opened.func, ast.Name) and opened.func.id == "open" and opened.args:
                value, _ = _expr_value(opened.args[0], env)
                if value and value.endswith(".py"):
                    found = [(value, "exec(open(...))")]
        out += [{"target": t.replace("\\", "/"), "line": node.lineno, "how": how} for t, how in found]
    return out


def _command_targets(node: ast.AST, env: dict[str, ast.AST]) -> list[str]:
    """Scripts named in a command, given as a list (["python", "x.py"]) or as one string."""
    if isinstance(node, ast.Name) and node.id in env:
        node = env[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        words = [_expr_value(e, env)[0] or "" for e in node.elts]
    else:
        text, _ = _expr_value(node, env)
        if not text:
            return []
        try:
            words = shlex.split(text, posix=False)
        except ValueError:
            words = text.split()
    words = [w.strip("\"'") for w in words]
    return _script_words(words)


def _script_words(words: list[str]) -> list[str]:
    out = []
    for i, w in enumerate(words):
        if w == "-m" and i + 1 < len(words) and re.fullmatch(r"[\w.]+", words[i + 1]):
            out.append(f"module:{words[i + 1]}")
        elif w.lower().endswith((".py", ".ipynb")) and "{}" not in w:
            out.append(w)
    return out


def _magic_runs(line: str) -> list[tuple[str, str]]:
    if line.startswith("%run "):
        words = line.split()[1:]
        words = [w for w in words if not w.startswith("-")]
        return [(t, "%run") for t in _script_words([w.strip("\"'") for w in words[:1]])]
    if line.startswith("!") and re.match(r"!\s*(python|py)\b", line):
        return [(t, "!python") for t in _script_words([w.strip("\"'") for w in line[1:].split()])]
    return []


MAX_SQL = 200          # SQL strings kept per file
MAX_SQL_LEN = 20_000


def _sql_name(node: ast.AST, env: "Env") -> str | None:
    """A name for a SQL string: the variable holding it, or the comment just above it."""
    if id(node) in env.assigned_to:
        return env.assigned_to[id(node)]
    line = getattr(node, "lineno", 0)
    for back in (1, 2):
        if 0 < line - back <= len(env.lines):
            text = env.lines[line - back - 1].strip()
            if text.startswith("#") and len(text) > 2:
                return text.lstrip("# ").strip()[:80]
            if text:
                break
    return None


class Env(dict):
    """name -> the expression first assigned to it, plus what's needed to follow paths further:
    every assignment of a name (if/else branches), the function each node is in, what callers pass
    for each parameter, what helper functions return, and what was imported from where."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.alts: dict[str, list[ast.AST]] = {}
        self.enclosing: dict[int, str] = {}
        self.params: dict[tuple[str, str], list[tuple[ast.AST, str]]] = {}
        self.returns: dict[str, ast.AST] = {}
        self.imports: dict[str, str] = {}
        self.argparse: set[str] = set()
        self.withs: set[str] = set()
        self.unknown: dict[tuple, dict] = {}
        self.patterns: list[str] = []
        self.sql: dict[tuple, dict] = {}
        self.assigned_to: dict[int, str] = {}
        self.lines: list[str] = []


# module name -> {constant name: value}, for every module in the project being scanned
_PROJECT: contextvars.ContextVar[dict[str, dict[str, str]]] = contextvars.ContextVar("pdcat_project_consts", default={})


def _project_value(dotted: str) -> str | None:
    """settings.OUTPUT_DIR -> its value, when settings.py is in the project being scanned."""
    mod, _, name = dotted.rpartition(".")
    table = _PROJECT.get()
    if mod in table and name in table[mod]:
        return table[mod][name]
    for m, consts in table.items():  # "from src.settings import X" vs module "settings"
        if (m.endswith("." + mod) or mod.endswith("." + m)) and name in consts:
            return consts[name]
    return None


def project_constants(root: Path, files: list[Path]) -> dict[str, dict[str, str]]:
    """Module-level string constants of every .py file, by module name, so a path defined in
    settings.py can be followed into the scripts that import it."""
    out: dict[str, dict[str, str]] = {}
    for path in files:
        if path.suffix.lower() != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError):
            continue
        env = _assignment_env(tree)
        consts = {}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        v, _ = _expr_value(node.value, env)
                        if v is not None:
                            consts[t.id] = v
        if consts:
            for name in module_names(path.relative_to(root).as_posix(), root):
                out[name] = consts
    return out


def build_env(tree: ast.Module, text: str = "") -> Env:
    env = Env(_assignment_env(tree))
    env.lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    env.alts.setdefault(t.id, []).append(node.value)
                    env.assigned_to.setdefault(id(node.value), t.id)
                elif isinstance(t, ast.Attribute):  # self.out_path = "...", cfg.out = "..."
                    env.setdefault(_src(t, 200), node.value)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name) and isinstance(item.context_expr, ast.Call) \
                        and (_call_name(item.context_expr) or "").endswith("ExcelWriter"):
                    env.withs.add(item.optional_vars.id)
    env.imports = {k: v for k, v in _import_map(tree).items() if not v.startswith(".")}
    _argparse_defaults(tree, env)
    for name, fn in _functions(tree):
        for n in _own_nodes(fn):
            env.enclosing[id(n)] = name
        rets = [n.value for n in _own_nodes(fn) if isinstance(n, ast.Return) and n.value is not None]
        if len(rets) == 1 and "." not in name:
            env.returns.setdefault(name, rets[0])
    _caller_values(tree, env)
    return env


def _argparse_defaults(tree: ast.Module, env: Env) -> None:
    """parser.add_argument("--out", default="x.csv") makes args.out "x.csv"."""
    holders = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
               and (_call_name(n.value) or "").endswith("parse_args") for t in n.targets if isinstance(t, ast.Name)}
    holders = holders or {"args"}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        default = next((k.value for k in node.keywords if k.arg == "default"), None)
        dest = next((k.value.value for k in node.keywords if k.arg == "dest" and isinstance(k.value, ast.Constant)),
                    None)
        if dest is None:
            flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            long = [f for f in flags if f.startswith("--")] or flags
            dest = long[0].lstrip("-").replace("-", "_") if long else None
        if default is not None and dest:
            for h in holders:
                env.setdefault(f"{h}.{dest}", default)
                env.argparse.add(f"{h}.{dest}")


def _caller_values(tree: ast.Module, env: Env) -> None:
    """For each function defined here, what each call in this file passes for each parameter, plus the
    parameter's default: so `def save(df, path): df.to_csv(path)` called as save(df, "out/x.csv") is found."""
    defs = {name: fn for name, fn in _functions(tree)}
    simple = {name.rsplit(".", 1)[-1]: (name, fn) for name, fn in defs.items()}
    for name, fn in defs.items():
        a = fn.args
        positional = [*a.posonlyargs, *a.args]
        for arg, default in zip(positional[len(positional) - len(a.defaults):], a.defaults):
            env.params.setdefault((name, arg.arg), []).append((default, "default of the function argument"))
        for arg, default in zip(a.kwonlyargs, a.kw_defaults):
            if default is not None:
                env.params.setdefault((name, arg.arg), []).append((default, "default of the function argument"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func.id if isinstance(node.func, ast.Name) else \
            node.func.attr if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
            and node.func.value.id in ("self", "cls") else None
        if callee not in simple:
            continue
        qual, fn = simple[callee]
        positional = [x.arg for x in [*fn.args.posonlyargs, *fn.args.args]]
        if positional and positional[0] in ("self", "cls") and isinstance(node.func, ast.Attribute):
            positional = positional[1:]
        caller = env.enclosing.get(id(node))
        how = f"passed in by {caller or 'the script'}() at line {node.lineno}".replace("the script()", "the script")
        for i, value in enumerate(node.args):
            if i < len(positional) and not isinstance(value, ast.Starred):
                env.params.setdefault((qual, positional[i]), []).append((value, how))
        for k in node.keywords:
            if k.arg:
                env.params.setdefault((qual, k.arg), []).append((k.value, how))


def _assignment_env(tree: ast.AST) -> dict[str, ast.AST]:
    """name -> the expression first assigned to it, anywhere in the file."""
    env: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            env.setdefault(node.targets[0].id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            env.setdefault(node.target.id, node.value)
    return env


def _join(*parts: str) -> str:
    out = ""
    for p in parts:
        p = p.replace("\\", "/")
        out = p if not out or re.match(r"^([A-Za-z]:/|/)", p) else out.rstrip("/") + "/" + p.lstrip("/")
    return out


def _expr_value(node: ast.AST, env: dict[str, ast.AST], depth: int = 0) -> tuple[str | None, str]:
    """Work out a string an expression builds, as far as the code shows it. Parts that can't be known
    (a date, a loop variable) become "{}". Returns (value, how it was found)."""
    if depth > 8 or node is None:
        return None, ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, ""
    if isinstance(node, ast.JoinedStr):
        out, how = [], "f-string"
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                val, _ = _expr_value(v.value, env, depth + 1) if isinstance(v.value, (ast.Name, ast.Subscript)) \
                    else (None, "")
                out.append(val if val is not None and "{}" not in val else "{}")
        text = "".join(out)
        return text, "f-string pattern" if "{}" in text else how
    if isinstance(node, ast.Name):
        if node.id in env:
            val, how = _expr_value(env[node.id], env, depth + 1)
            return val, how or "variable"
        if isinstance(env, Env) and node.id in env.imports:
            got = _project_value(env.imports[node.id])
            if got is not None:
                return got, f"imported from {env.imports[node.id].rsplit('.', 1)[0]}"
        return None, ""
    if isinstance(node, ast.Attribute):
        dotted = _src(node, 200)
        if dotted in env:  # self.output_path = ..., args.out (argparse default), cfg.out
            val, how = _expr_value(env[dotted], env, depth + 1)
            return val, how or ("argparse default" if dotted in getattr(env, "argparse", set()) else "attribute")
        if isinstance(env, Env) and isinstance(node.value, ast.Name) and node.value.id in env.imports:
            got = _project_value(f"{env.imports[node.value.id]}.{node.attr}")
            if got is not None:
                return got, f"from {env.imports[node.value.id]}"
        return None, ""
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div, ast.Mod)):
        left, lh = _expr_value(node.left, env, depth + 1)
        if isinstance(node.op, ast.Mod) and left is not None:
            return re.sub(r"%[sdif]", "{}", left), "string formatting"
        right, rh = _expr_value(node.right, env, depth + 1)
        if left is None or right is None:
            return None, ""
        if isinstance(node.op, ast.Div):
            return _join(left, right), "pathlib path"
        return left + right, lh or rh
    if isinstance(node, ast.Subscript):
        key = node.slice.value if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) else None
        if key is not None:
            mapping = _resolve_name(node.value, env)
            if isinstance(mapping, ast.Dict):  # PATHS = {"out": "results/x.csv"}; PATHS["out"]
                for k, v in zip(mapping.keys, mapping.values):
                    if isinstance(k, ast.Constant) and k.value == key:
                        val, _ = _expr_value(v, env, depth + 1)
                        return val, "dictionary in the code"
            if key in _CONFIG.get():
                return _CONFIG.get()[key], "config file"
        return None, ""
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        args = [_expr_value(a, env, depth + 1)[0] for a in node.args]
        if name == "join" and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute) \
                and func.value.attr == "path" or name in ("joinpath",):
            base = [] if name == "join" else [_expr_value(func.value, env, depth + 1)[0]]
            parts = base + args
            if all(p is not None for p in parts) and parts:
                return _join(*parts), "os.path.join" if name == "join" else "pathlib path"
            return None, ""
        if name in ("Path", "PurePath", "WindowsPath", "PureWindowsPath", "PosixPath", "str", "abspath",
                    "normpath", "expanduser") and args and all(a is not None for a in args):
            return _join(*args), "pathlib path" if "Path" in name else "variable"
        if name in ("resolve", "absolute", "expanduser", "strip") and isinstance(func, ast.Attribute):
            return _expr_value(func.value, env, depth + 1)
        if name == "format" and isinstance(func, ast.Attribute):
            base, _ = _expr_value(func.value, env, depth + 1)
            return (re.sub(r"\{[^}]*\}", "{}", base), "string formatting") if base is not None else (None, "")
        if name == "get" and node.args and isinstance(node.args[0], ast.Constant) and \
                isinstance(node.args[0].value, str) and node.args[0].value in _CONFIG.get():
            return _CONFIG.get()[node.args[0].value], "config file"
        if isinstance(env, Env) and isinstance(func, ast.Name) and func.id in env.returns:
            val, _ = _expr_value(env.returns[func.id], env, depth + 1)  # a helper that returns the path
            return val, f"returned by {func.id}()" if val is not None else ""
    return None, ""


def _resolve_name(node: ast.AST, env, depth: int = 0) -> ast.AST | None:
    while isinstance(node, (ast.Name, ast.Attribute)) and depth < 6:
        k = node.id if isinstance(node, ast.Name) else _src(node, 200)
        if k not in env:
            return None
        node = env[k]
        depth += 1
    return node


def _normalise_ref(ref: str) -> str:
    """file:./data/../out/x.csv -> file:out/x.csv, so relative paths match the files they point at."""
    if not ref.startswith("file:"):
        return ref
    path = ref[5:].replace("\\", "/")
    if re.match(r"^([A-Za-z]:/|/|//)", path):
        return "file:" + path
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "file:" + "/".join(parts)


def _functions(tree: ast.AST, prefix: str = ""):
    """Every function, method and nested function, as ("name", node), ("Class.method", node)
    or ("outer.inner", node)."""
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{node.name}"
            yield name, node
            yield from _functions(node, f"{name}.")
        elif isinstance(node, ast.ClassDef):
            yield from _functions(node, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.If, ast.Try, ast.With)) and not prefix:
            yield from _functions(node, prefix)  # defs under if/try at module level


def _own_nodes(fn: ast.AST):
    """Nodes inside a function, not counting nested functions and classes (they get their own entry)."""
    found, stack = [], list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    # In reading order, so calls and returns are listed the way the code reads.
    return sorted(found, key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))


def _src(node: ast.AST | None, limit: int = 80) -> str:
    if node is None:
        return ""
    try:
        text = ast.unparse(node)
    except Exception:  # very old or odd syntax
        return "…"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _call_name(call: ast.Call) -> str | None:
    f = call.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
        return ".".join(reversed(parts))
    return None


def _describe(fn: ast.AST, name: str) -> dict:
    """Signature, docstring, what a function calls and what it returns."""
    a = fn.args
    params = []
    positional = [*a.posonlyargs, *a.args]
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    for arg, default in zip(positional, defaults):
        params.append({"name": arg.arg, "annotation": _src(arg.annotation), "default": _src(default)})
    if a.vararg:
        params.append({"name": f"*{a.vararg.arg}", "annotation": _src(a.vararg.annotation), "default": ""})
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        params.append({"name": arg.arg, "annotation": _src(arg.annotation), "default": _src(default)})
    if a.kwarg:
        params.append({"name": f"**{a.kwarg.arg}", "annotation": _src(a.kwarg.annotation), "default": ""})
    calls, returns, yields = [], [], False
    for node in _own_nodes(fn):
        if isinstance(node, ast.Call):
            c = _call_name(node)
            if c and c not in calls:
                calls.append(c)
        elif isinstance(node, ast.Return) and node.value is not None:
            r = _src(node.value)
            if r not in returns:
                returns.append(r)
        elif isinstance(node, (ast.Yield, ast.YieldFrom)):
            yields = True
    doc = ast.get_docstring(fn) or ""
    return {
        "params": params, "returns_annotation": _src(fn.returns), "returns": returns[:6], "yields": yields,
        "calls": calls[:60], "doc": doc[:3000], "line": fn.lineno, "end_line": getattr(fn, "end_lineno", fn.lineno),
        "is_async": isinstance(fn, ast.AsyncFunctionDef), "decorators": [_src(d, 40) for d in fn.decorator_list],
    }


def _function_info(tree: ast.Module) -> dict[str, dict]:
    info = {name: _describe(node, name) for name, node in _functions(tree)}
    # The script's own top-level code (e.g. under `if __name__ == "__main__":`) is its entry point.
    module_calls = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        for node in [stmt, *_own_nodes(stmt)]:
            if isinstance(node, ast.Call):
                c = _call_name(node)
                if c and c not in module_calls:
                    module_calls.append(c)
    if module_calls:
        info["(script)"] = {"params": [], "returns_annotation": "", "returns": [], "yields": False,
                            "calls": module_calls[:60], "doc": "Code that runs when the script itself is run.",
                            "line": 1, "end_line": len(tree.body), "is_async": False, "decorators": []}
    return info


def _import_map(tree: ast.Module) -> dict[str, str]:
    """Local name -> what it refers to: {"cc": "common.clean", "load_sales": "common.io.load_sales"}.
    Relative imports keep their leading dots."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            for a in node.names:
                if a.name != "*":
                    out[a.asname or a.name] = f"{base}.{a.name}" if base and not base.endswith(".") else f"{base}{a.name}"
    return out


def _import_details(tree: ast.Module) -> list[dict]:
    """Every import with the names used from it.

    `from lib.io import load` records "load"; for `import lib.io as lio` the
    attributes called on it (lio.load(...)) are recorded instead.
    """
    details = []
    aliases: dict[str, dict] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                d = {"module": a.name, "names": [], "level": 0, "line": node.lineno}
                details.append(d)
                aliases[a.asname or a.name] = d
        elif isinstance(node, ast.ImportFrom):
            details.append({"module": node.module or "", "names": [a.name for a in node.names if a.name != "*"],
                            "level": node.level, "line": node.lineno})
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            # lio.load or lib.io.load → the dotted base must be an imported name
            parts, base = [node.attr], node.value
            while isinstance(base, ast.Attribute):
                parts.append(base.attr)
                base = base.value
            if isinstance(base, ast.Name):
                dotted = ".".join([base.id, *reversed(parts[1:])])
                d = aliases.get(dotted)
                if d is not None and parts[0] not in d["names"]:
                    d["names"].append(parts[0])
    for d in details:
        d["names"] = sorted(d["names"])
    return details


def _sys_path_additions(tree: ast.Module, consts: dict[str, str]) -> list[str]:
    """Folders added with sys.path.append/insert: a common way to use a home-built library."""
    paths = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("append", "insert") and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "path" and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "sys" and node.args):
            value = _string_value(node.args[-1], consts)
            if value and "{" not in value:
                paths.append(value)
    return paths


def module_names(rel: str, root: Path) -> list[str]:
    """Import names a file can be reached by: the package-aware name (folders with
    __init__.py), plus its path from the project root with any leading 'src.' dropped."""
    parts = rel[:-3].split("/") if rel.endswith(".py") else []
    if not parts:
        return []
    names = []
    # Walk up while the parent folder is a package.
    i = len(parts) - 1
    while i > 0 and (root / "/".join(parts[:i]) / "__init__.py").exists():
        i -= 1
    for candidate in (parts[i:], parts[1:] if parts[0] == "src" else parts):
        name = ".".join(candidate)
        if name.endswith(".__init__"):
            name = name[: -len(".__init__")]
        if name and name != "__init__" and name not in names:
            names.append(name)
    return names


# Writers and readers beyond pandas: (owner names or None for any, function name) -> (relation, path argument)
EXTRA_IO: dict[str, tuple[str, int, tuple[str, ...]]] = {
    "ExcelWriter": ("writes", 0, ("path",)),
    "save": ("writes", 0, ("filename",)),          # openpyxl workbook.save (checked for an Excel path)
    "load_workbook": ("reads", 0, ("filename",)),
    "write_csv": ("writes", 0, ("file",)), "write_parquet": ("writes", 0, ("file",)),
    "write_excel": ("writes", 0, ("workbook",)), "write_ipc": ("writes", 0, ("file",)),
    "write_ndjson": ("writes", 0, ("file",)), "write_json": ("writes", 0, ("file",)),
    "scan_csv": ("reads", 0, ("source",)), "scan_parquet": ("reads", 0, ("source",)),
    "read_ipc": ("reads", 0, ("source",)), "read_delta": ("reads", 0, ("source",)),
}
NUMPY_IO = {"save": "writes", "savez": "writes", "savez_compressed": "writes", "savetxt": "writes",
            "load": "reads", "loadtxt": "reads", "genfromtxt": "reads", "fromfile": "reads"}
SHUTIL_IO = {"copy", "copy2", "copyfile", "move", "copytree"}
PATH_METHODS = {"write_text": "writes", "write_bytes": "writes", "read_text": "reads", "read_bytes": "reads",
                "touch": "writes"}
EXCEL = re.compile(r"\.(xlsx|xlsm|xls|xltx)$", re.I)


def _call_refs(call: ast.Call, env: dict[str, ast.AST] | None = None) -> list[tuple[str, str, str]]:
    """(relation, ref, how) for one call such as pd.read_csv(path) or df.to_sql("Sales", …).
    A path that can't be worked out is noted on env.unknown (when env is an Env), so the script page
    can ask which file it is."""
    env = env if env is not None else {}
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    if name is None:
        return []
    owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None

    def node_of(pos: int, *kw: str) -> ast.AST | None:
        for k in call.keywords:
            if k.arg in kw:
                return k.value
        return call.args[pos] if len(call.args) > pos else None

    def arg(pos: int, *kw: str) -> tuple[str | None, str]:
        n = node_of(pos, *kw)
        return _expr_value(n, env) if n is not None else (None, "")

    def files(relation: str, node: ast.AST | None, how: str, check=_looks_like_path) -> list[tuple[str, str, str]]:
        """Every path this argument can hold (both branches of an if, each caller of the function...)."""
        if node is None:
            return []
        out = []
        for value, found_how in _values(node, env, call):
            if value and check(value):
                out.append((relation, f"file:{value}", found_how or how))
                if "{}" in value and relation == "writes" and isinstance(env, Env):
                    env.patterns.append(value)
        if not out:
            _unknown(env, call, relation, how)
        return out

    if name in ("read_sql_table", "to_sql"):
        table, how = arg(0, "table_name", "name")
        if not table:
            return []
        schema, _ = arg(99, "schema")
        full = f"{schema}.{table}" if schema else table
        return [("reads" if name == "read_sql_table" else "writes", f"table:{full.lower()}",
                 how or f"pandas {name}")]

    if name == "open" and isinstance(func, ast.Name):
        mode = arg(1, "mode")[0] or "r"
        return files("writes" if any(c in mode for c in "wax+") else "reads", node_of(0, "file"), "open()")
    if name == "open" and isinstance(func, ast.Attribute) and _is_path_expr(func.value, env):
        mode = arg(0, "mode")[0] or "r"
        return files("writes" if any(c in mode for c in "wax+") else "reads", func.value, "Path.open()")
    if name in PATH_METHODS and isinstance(func, ast.Attribute) and _is_path_expr(func.value, env):
        return files(PATH_METHODS[name], func.value, f"Path.{name}()")
    if owner == "joblib" and name in ("dump", "load"):
        n = node_of(1, "filename") if name == "dump" else node_of(0, "filename")
        return files("writes" if name == "dump" else "reads", n, f"joblib.{name}", check=bool)
    if owner in ("np", "numpy") and name in NUMPY_IO:
        return files(NUMPY_IO[name], node_of(0, "file", "fname"), f"numpy {name}")
    if owner == "shutil" and name in SHUTIL_IO:
        return (files("reads", node_of(0, "src"), f"shutil.{name}")
                + files("writes", node_of(1, "dst"), f"shutil.{name}"))
    if name in FILE_READERS or name in FILE_WRITERS:
        n = node_of(0, "path", "path_or_buf", "filepath_or_buffer", "io", "excel_writer", "fname", "filename")
        if n is None:
            return []  # df.to_csv() with no path returns text
        if name in FILE_WRITERS and isinstance(n, ast.Name) and _is_writer_object(n, env):
            return []  # df.to_excel(writer, ...) writes into a pd.ExcelWriter, already counted
        return files("reads" if name in FILE_READERS else "writes", n, f"pandas {name}")
    if name in EXTRA_IO:
        relation, pos, kws = EXTRA_IO[name]
        if name == "save":  # only workbooks: model.save(), fig.save() and friends write other things
            n = node_of(pos, *kws)
            got = [(v, h) for v, h in (_values(n, env, call) if n is not None else []) if v and EXCEL.search(v)]
            return [("writes", f"file:{v}", h or "openpyxl save") for v, h in got]
        return files(relation, node_of(pos, *kws), name)
    return []


def _unknown(env, call: ast.Call, relation: str, how: str) -> None:
    if not isinstance(env, Env):
        return
    code = _src(call, 120)
    env.unknown.setdefault((call.lineno, code), {"line": call.lineno, "code": code, "relation": relation,
                                                  "call": how, "function": env.enclosing.get(id(call))})


def _is_path_expr(node: ast.AST, env) -> bool:
    """Path("x") / folder / "y", or a name assigned one: things whose .open() and .write_text() write files."""
    depth = 0
    while isinstance(node, ast.Name) and node.id in env and depth < 5:
        node = env[node.id]
        depth += 1
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return True
    if isinstance(node, ast.Call):
        n = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
        return n in ("Path", "PurePath", "WindowsPath", "joinpath", "with_suffix", "with_name", "resolve")
    if isinstance(node, ast.Attribute) and isinstance(env, Env):
        return _is_path_expr(env.get(_src(node, 200), ast.Constant(0)), env) if _src(node, 200) in env else False
    return False


def _is_writer_object(node: ast.Name, env) -> bool:
    v = env.get(node.id) if node.id in env else None
    if isinstance(node, ast.Name) and isinstance(env, Env) and node.id in env.withs:
        return True
    return isinstance(v, ast.Call) and _call_name(v) is not None and _call_name(v).split(".")[-1] == "ExcelWriter"


def _values(node: ast.AST, env, call: ast.Call | None = None) -> list[tuple[str | None, str]]:
    """All the values an argument can have: the one assignment, every branch of an if/else, or, for a
    function parameter, what each caller in the file passes (and its default)."""
    first = _expr_value(node, env)
    if not isinstance(env, Env) or not isinstance(node, ast.Name):
        return [first]
    out: list[tuple[str | None, str]] = []
    alts = env.alts.get(node.id, [])
    if len(alts) > 1:
        out += [(_expr_value(a, env)[0], "every branch of an if") for a in alts]
    fn = env.enclosing.get(id(call)) if call is not None else None
    if fn and (fn, node.id) in env.params:
        for value_node, how in env.params[(fn, node.id)]:
            v = _expr_value(value_node, env)[0]
            out.append((v, how))
    out = [o for o in dict.fromkeys(out) if o[0]]
    return out or [first]


def _string_value(node: ast.AST, consts: dict[str, str] | None = None) -> str | None:
    """A string literal's value. In f-strings, names of module-level string constants are filled
    in (so f"FROM {TABLE}" works) and any other expression becomes "{}"."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and consts and v.value.id in consts:
                out.append(consts[v.value.id])
            else:
                out.append("{}")
        return "".join(out)
    return None


def _module_constants(tree: ast.Module) -> dict[str, str]:
    consts = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            consts[node.targets[0].id] = node.value.value
    return consts


def _looks_like_path(s: str) -> bool:
    return "." in s.rsplit("/", 1)[-1] or "/" in s or "\\" in s


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _markdown_summary(md: str) -> str:
    """'# Title\nSome text' -> 'Title: Some text'."""
    lines = md.strip().splitlines()
    title = lines[0].lstrip("# ").strip() if lines[0].startswith("#") else None
    body = _first_paragraph("\n".join(lines[1:] if title else lines))
    return ": ".join(p for p in (title, body) if p)


def _first_paragraph(text: str, max_len: int = 500) -> str:
    para = text.strip().split("\n\n", 1)[0].strip()
    return para if len(para) <= max_len else para[: max_len - 1] + "…"
