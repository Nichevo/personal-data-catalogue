"""Scan a Python project folder: scripts, notebooks and .sql files.

For each file it records the module docstring, functions and imports, then
works out which tables and files the code reads and writes by looking at:
  * SQL in string literals (and f-strings, with the dynamic bits ignored)
  * pandas calls such as read_sql_table / to_sql / read_csv / to_parquet
  * open(), joblib.dump/load and save_model/load_model with a literal path
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import PythonSource
from .sqlrefs import extract_refs, looks_like_sql
from .scan_files import ingest_data_file, iter_data_files
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


def scan_project(cat: Catalogue, src: PythonSource) -> dict[str, int]:
    root = Path(src.path)
    if not root.is_dir():
        raise FileNotFoundError(f"Python project folder not found: {root}")
    project_key = f"py://{src.name}"
    skip = SKIP_DIRS | set(src.exclude)
    counts = {"files": 0, "data files": 0, "links": 0, "errors": 0}

    with cat.scan(src.name):
        readme = _readme_summary(root)
        cat.upsert_asset(project_key, "project", src.name, src.name, description=readme,
                         attrs={"path": str(root), "role": src.role, "environment": read_environment(root)})
        for path in sorted(_iter_files(root, skip)):
            rel = path.relative_to(root).as_posix()
            found = scan_file(path)
            if path.suffix.lower() == ".py":
                found.attrs["modules"] = module_names(rel, root)
            kind = {".py": "script", ".ipynb": "notebook", ".sql": "sql_script"}[path.suffix.lower()]
            key = f"{project_key}/{rel}"
            cat.upsert_asset(key, kind, rel, src.name, parent=project_key,
                             description=found.description, attrs=found.attrs)
            for relation, dst_ref, line in found.refs:
                cat.add_link(key, relation, dst_ref, src.name, hint=src.default_database,
                             evidence=f"{rel}:{line}" if line else rel)
                counts["links"] += 1
            counts["files"] += 1
            counts["errors"] += bool(found.attrs.get("parse_error"))
        if src.data_files:
            for path in sorted(iter_data_files(root, skip=skip)):
                rel = path.relative_to(root).as_posix()
                error = ingest_data_file(cat, path, f"{project_key}/{rel}", rel, src.name, project_key, src.max_rows)
                counts["data files"] += 1
                counts["errors"] += bool(error)
    return counts


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
    funcs, imports, errors, import_details = [], set(), [], []
    merged: dict[str, dict] = {"function_info": {}, "import_map": {}, "function_refs": {}}
    for i, cell in enumerate(cells, start=1):
        source = "".join(cell.get("source", []))
        if cell.get("cell_type") == "markdown" and found.description is None and source.strip():
            found.description = _markdown_summary(source)
            continue
        if cell.get("cell_type") != "code":
            continue
        if cell.get("source") and source.lstrip().lower().startswith(("%%sql", "%sql")):
            found.refs += [(r.relation, f"table:{r.name}", 0) for r in extract_refs(source.split("\n", 1)[-1])]
            continue
        # Drop IPython magics and shell lines so the cell parses as Python.
        code = "\n".join("" if ln.lstrip().startswith(("%", "!")) else ln for ln in source.splitlines())
        cell_found = scan_python_source(code)
        if cell_found.attrs.get("parse_error"):
            errors.append(f"cell {i}")
        found.refs += [(rel, ref, 0) for rel, ref, _ in cell_found.refs]
        funcs += cell_found.attrs.get("functions", [])
        imports.update(cell_found.attrs.get("imports", []))
        import_details += cell_found.attrs.get("import_details", [])
        for name, value in merged.items():
            for k, v in cell_found.attrs.get(name, {}).items():
                if name == "function_info":
                    v = {**v, "cell": i}
                    k = f"(cell {i})" if k == "(script)" else k
                value[k] = v
    found.refs = list(dict.fromkeys(found.refs))
    found.attrs.update({"cells": len(cells), "functions": funcs, "imports": sorted(imports),
                        "import_details": import_details, **{k: v for k, v in merged.items() if v}})
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
    found.refs = [(rel, ref, line) for (rel, ref), line in _collect_refs(tree, skip, consts).items()]

    # Which function touches which data, so a project calling one library function
    # can be traced to exactly the tables and files that function uses.
    function_refs = {}
    for name, node in _functions(tree):
        refs = sorted({(rel, ref) for (rel, ref) in _collect_refs(node, skip, consts)})
        if refs:
            function_refs[name] = [list(r) for r in refs]
    if function_refs:
        found.attrs["function_refs"] = function_refs
    found.attrs["import_details"] = _import_details(tree)
    found.attrs["import_map"] = _import_map(tree)
    found.attrs["function_info"] = _function_info(tree)
    paths = _sys_path_additions(tree, consts)
    if paths:
        found.attrs["sys_path"] = paths
    return found


def _collect_refs(root: ast.AST, skip: set[int], consts: dict[str, str]) -> dict[tuple[str, str], int]:
    refs: dict[tuple[str, str], int] = {}
    for node in ast.walk(root):
        if isinstance(node, (ast.Constant, ast.JoinedStr)) and id(node) not in skip:
            s = _string_value(node, consts)
            if s and looks_like_sql(s):
                for r in extract_refs(s):
                    refs.setdefault((r.relation, f"table:{r.name}"), node.lineno)
        elif isinstance(node, ast.Call):
            for relation, ref in _call_refs(node):
                refs.setdefault((relation, ref), node.lineno)
    return refs


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


def _call_refs(call: ast.Call) -> list[tuple[str, str]]:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    if name is None:
        return []
    owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None

    def arg(pos: int, *kw: str) -> str | None:
        for k in call.keywords:
            if k.arg in kw:
                return _string_value(k.value)
        return _string_value(call.args[pos]) if len(call.args) > pos else None

    if name in ("read_sql_table", "to_sql"):
        table = arg(0, "table_name", "name")
        if not table or "{" in table:
            return []
        schema = arg(99, "schema")
        full = f"{schema}.{table}" if schema else table
        return [("reads" if name == "read_sql_table" else "writes", f"table:{full.lower()}")]

    if name == "open":
        path = arg(0, "file")
        mode = arg(1, "mode") or "r"
        if path:
            return [("writes" if any(c in mode for c in "wax+") else "reads", f"file:{path}")]
        return []
    if owner == "joblib" and name in ("dump", "load"):
        path = arg(1, "filename") if name == "dump" else arg(0, "filename")
        return [("writes" if name == "dump" else "reads", f"file:{path}")] if path else []
    if name in FILE_READERS or name in FILE_WRITERS:
        path = arg(0, "path", "path_or_buf", "filepath_or_buffer", "io", "excel_writer", "fname", "filename")
        if path and _looks_like_path(path):
            return [("reads" if name in FILE_READERS else "writes", f"file:{path}")]
    return []


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
