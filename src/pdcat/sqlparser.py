"""Read the shape of a SQL query: the tables it uses (with their aliases), the columns it uses from
each, the exact fields it joins on, and the columns it renames (`Units AS qty`).

Uses sqlglot when it is installed (`pip install sqlglot`), which understands T-SQL properly.
Without it, a regex pass gives the same fields less precisely, so nothing breaks: the app says
"install sqlglot for more exact SQL reading" where it matters.

Before sqlglot sees the SQL, `sqlclean.prepare` tidies a copy: placeholders and template variables
become plain names, script-only lines (TRY/CATCH, cursors, PRINT) go, and GO batches are read one at a
time. Whatever sqlglot still can't read (a statement it would turn into a generic "Command") is read by
the regex pass instead, so its tables are still found. Each batch has a time limit, so one odd query
can't hold up a scan; sqlglot's warnings are kept out of the console and counted instead.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from functools import lru_cache
from typing import Any

from .sqlclean import MARK, prepare, split_statements, unmark
from .sqlrefs import _IDENT, _KEYWORDS, expand_templates, extract_refs, looks_like_sql, normalise, strip_comments

try:  # optional
    import sqlglot
    from sqlglot import exp
    logging.getLogger("sqlglot").setLevel(logging.ERROR)  # "falling back to parsing as a 'Command'" is counted
except Exception:  # pragma: no cover - depends on what is installed
    sqlglot = None
    exp = None


VERSION = 3  # bump when the reading changes, so saved analyses are read again
PARSE_SECONDS = 5.0     # sqlglot's time for one query, across its batches; the rest is read by the regex pass
MAX_EXACT_CHARS = 200_000  # longer SQL is read by the regex pass straight away
_DML = re.compile(r"\b(?:select|insert|update|delete|merge|with)\b", re.I)


def available() -> bool:
    return sqlglot is not None


def analyse(sql: str, exact: bool = True) -> dict[str, Any]:
    """{"tables": [{"name", "alias", "relation"}], "joins": [{"left_table", "left_column", "right_table",
    "right_column", "kind"}], "columns": {table: [columns]}, "renames": [{"table", "column", "alias"}],
    "select_star": bool, "parser": "sqlglot" | "mixed" | "regex", "error": str | None,
    "fallbacks": pieces read by the regex pass, "commands": statements sqlglot could only read as a generic
    Command, "timed_out": bool, "cleaned": what was tidied first}. exact=False skips sqlglot. Never raises."""
    return dict(_analyse(sql or "", exact))


@lru_cache(maxsize=2048)
def _analyse(sql: str, exact: bool) -> tuple:
    try:
        cleaned = prepare(sql)
    except Exception:  # tidying is a help, never a reason to read nothing
        cleaned = None
    text = cleaned.text if cleaned else strip_comments(sql)
    rel = {r.name: r.relation for r in extract_refs(text, dynamic=True)}
    out = None
    if sqlglot is not None and exact and cleaned and len(sql) <= MAX_EXACT_CHARS:
        try:
            out = _with_sqlglot(cleaned, rel)
        except Exception as e:  # sqlglot can't read every dialect quirk; fall back
            out = {**_with_regex(text, rel), "fallbacks": 1, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    if out is None:
        out = _with_regex(text, rel)
    for inner in (cleaned.strings if cleaned else [])[:20]:  # dynamic SQL: SET @sql = 'SELECT ... FROM ...'
        found = _with_regex(expand_templates(inner)[0], {r.name: r.relation for r in extract_refs(inner, True)})
        out = _merge(out, found, keep_parser=True)
    out.setdefault("fallbacks", 0)
    out.setdefault("commands", 0)
    out.setdefault("timed_out", False)
    out["cleaned"] = cleaned.notes if cleaned else []
    return tuple(out.items())


def _timed(fn, seconds: float):
    """fn() if it finishes within `seconds`; TimeoutError if not (the reading is then left to finish on its own,
    in the background, and its result is ignored)."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # handed back to the caller below
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(max(seconds, 0.01))
    if t.is_alive():
        raise TimeoutError(f"took over {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _parse(text: str) -> list:
    return [s for s in sqlglot.parse(text, read="tsql") if s is not None]


def _rescue(command) -> list | None:
    """A statement sqlglot read only as a Command: read again from its first SELECT/INSERT/... onwards."""
    text = command.sql(dialect="tsql")
    m = _DML.search(text)
    if not m or m.start() == 0:
        return None
    piece = text[m.start():].rstrip(" ;")
    while piece.endswith(")") and piece.count(")") > piece.count("("):
        piece = piece[:-1].rstrip()
    try:
        got = _parse(piece)
    except Exception:
        return None
    return None if not got or any(isinstance(g, exp.Command) for g in got) else got


def _with_sqlglot(cleaned, rel: dict[str, str]) -> dict[str, Any]:
    deadline = time.monotonic() + PARSE_SECONDS
    statements: list = []
    leftovers: list[str] = []  # pieces for the regex pass
    commands = 0
    timed_out = False
    error = None
    for batch in cleaned.batches:
        left = deadline - time.monotonic()
        if left <= 0:
            timed_out = True
            leftovers.append(batch)
            continue
        try:
            got = _timed(lambda b=batch: _parse(b), left)
        except TimeoutError:
            timed_out = True
            leftovers.append(batch)
            continue
        except Exception as e:  # read the batch statement by statement instead
            error = error or f"{type(e).__name__}: {str(e)[:200]}"
            got = []
            for piece in split_statements(batch):
                try:
                    got += _timed(lambda p=piece: _parse(p), max(deadline - time.monotonic(), 0.01))
                except TimeoutError:
                    timed_out = True
                    leftovers.append(piece)
                except Exception:
                    leftovers.append(piece)
        for st in got:
            if isinstance(st, exp.Command):
                commands += 1
                again = _rescue(st)
                if again:
                    statements += again
                elif _DML.search(st.sql(dialect="tsql")):
                    leftovers.append(st.sql(dialect="tsql"))
            else:
                statements.append(st)
    out = _read_statements(statements, rel)
    if leftovers:
        out = _merge(out, _with_regex(unmark(";\n".join(leftovers)), rel))
        out["parser"] = "mixed" if statements else "regex"
    out.update({"fallbacks": len(leftovers), "commands": commands, "timed_out": timed_out, "error": error})
    return out


def _merge(a: dict[str, Any], b: dict[str, Any], keep_parser: bool = False) -> dict[str, Any]:
    """Two readings of parts of the same SQL, as one."""
    tables = {t["name"]: t for t in a["tables"]}
    for t in b["tables"]:
        tables.setdefault(t["name"], t)
    columns = {k: set(v) for k, v in a["columns"].items()}
    for k, v in b["columns"].items():
        columns.setdefault(k, set()).update(v)
    out = {**a, "tables": list(tables.values()), "joins": _dedupe(a["joins"] + b["joins"]),
           "columns": {k: sorted(v, key=str.lower) for k, v in columns.items()},
           "renames": a["renames"] + [r for r in b["renames"] if r not in a["renames"]],
           "select_star": a["select_star"] or b["select_star"]}
    if not keep_parser and a.get("parser") != b.get("parser"):
        out["parser"] = "mixed"
    return out


def _name(t) -> str | None:
    this = t.this
    if isinstance(this, exp.Parameter) or (isinstance(this, exp.Identifier) and (
            this.args.get("temporary") or this.args.get("global_") or this.args.get("global"))):
        return None  # @table variables, #temp and ##global temp tables
    if isinstance(t.parent, exp.Use):
        return None  # USE MyDb names a database
    parts = [p for p in (t.catalog, t.db, t.name) if p]
    if not parts:
        return None
    name = ".".join(parts)
    if looks_like_sql(name):
        return None  # EXEC ('INSERT INTO ...'): the SQL in the string is read on its own
    if MARK in name:  # {schema}.Sales: the known part of the name, as for SQL built from variables
        name = normalise(unmark(name), dynamic=True)
        return None if not name or "{" in name else name
    return normalise(name)


def _created(st) -> set[str]:
    """The procedure, function or view a CREATE/ALTER statement makes: not a table it uses."""
    out: set[str] = set()
    for c in st.find_all(exp.Create):
        if str(c.args.get("kind") or "").upper() in ("PROCEDURE", "PROC", "FUNCTION", "VIEW") and c.this is not None:
            t = c.this if isinstance(c.this, exp.Table) else c.this.find(exp.Table)
            if t is not None and (name := _name(t)):
                out.add(name)
    return out


def _read_statements(statements: list, rel: dict[str, str]) -> dict[str, Any]:
    ctes: set[str] = set()
    tables: dict[str, dict[str, Any]] = {}
    alias_to: dict[str, str] = {}
    columns: dict[str, set[str]] = {}
    joins: list[dict[str, Any]] = []
    renames: list[dict[str, Any]] = []
    select_star = False
    for st in statements:
        ctes |= _created(st)
        for cte in st.find_all(exp.CTE):
            ctes.add(cte.alias_or_name.lower())
        for t in st.find_all(exp.Table):
            name = _name(t)
            if not name or name in ctes:
                continue
            tables.setdefault(name, {"name": name, "alias": t.alias or None, "relation": rel.get(name, "reads")})
            alias_to[(t.alias or t.name).lower()] = name
            alias_to.setdefault(t.name.lower(), name)
        if any(isinstance(s, exp.Star) for s in st.find_all(exp.Star)):
            select_star = True
    for st in statements:  # (SELECT ... FROM dbo.Product) p and WITH p AS (...): p stands for the table inside
        for sub in [*st.find_all(exp.Subquery), *st.find_all(exp.CTE)]:
            inside = {_name(t) for t in sub.this.find_all(exp.Table)} - ctes - {None} if sub.this else set()
            if sub.alias and len(inside) == 1:
                alias_to.setdefault(sub.alias.lower(), inside.pop())
    single = next(iter(tables)) if len(tables) == 1 else None

    def table_of(col) -> str | None:
        if col.table:
            return alias_to.get(col.table.lower())
        return single

    def column_in(side) -> exp.Column | None:
        """s.Material, or the one column inside LTRIM(s.Material) or CAST(s.Material AS int)."""
        if isinstance(side, exp.Column):
            return side
        cols = list(side.find_all(exp.Column)) if side is not None else []
        return cols[0] if len(cols) == 1 else None

    def add_joins(cond, kind: str) -> None:
        for eq in cond.find_all(exp.EQ):
            l, r = column_in(eq.left), column_in(eq.right)
            if l is not None and r is not None:
                lt, rt = table_of(l), table_of(r)
                if lt and rt and lt != rt:
                    joins.append({"left_table": lt, "left_column": l.name, "right_table": rt,
                                  "right_column": r.name, "kind": kind})

    for st in statements:
        for c in st.find_all(exp.Column):
            t = table_of(c)
            if t and c.name and not c.name.startswith(MARK):
                columns.setdefault(t, set()).add(c.name)
        for j in st.find_all(exp.Join):
            on = j.args.get("on")
            if on is not None:
                add_joins(on, (j.side or j.kind or "inner").lower())
        for w in st.find_all(exp.Where):  # FROM a, b WHERE a.x = b.x
            add_joins(w, "where")
        for a in st.find_all(exp.Alias):
            if isinstance(a.this, exp.Column) and a.alias and a.alias.lower() != a.this.name.lower():
                renames.append({"table": table_of(a.this), "column": a.this.name, "alias": a.alias})
    return {"tables": list(tables.values()), "joins": _dedupe(joins),
            "columns": {k: sorted(v, key=str.lower) for k, v in columns.items()}, "renames": renames,
            "select_star": select_star, "parser": "sqlglot", "error": None, "version": VERSION}


_TABLE_ALIAS = re.compile(rf"\b(?:from|join)\s+(?P<t>{_IDENT})(?:\s+(?:as\s+)?(?P<a>\w+))?", re.I)
_JOIN_EQ = re.compile(r"(?P<la>[\w\]\[]+)\.(?P<lc>[\w\]\[]+)\s*\)?\s*=\s*(?:\w+\s*\(\s*)?(?P<ra>[\w\]\[]+)\."
                      r"(?P<rc>[\w\]\[]+)")
_ON_BLOCK = re.compile(r"\bon\b(?P<cond>.*?)(?=\bjoin\b|\bwhere\b|\bgroup\b|\border\b|\bunion\b|\bleft\b|"
                       r"\binner\b|\bright\b|\bfull\b|\bcross\b|\bouter\b|$)", re.I | re.S)
_DOTTED = re.compile(r"(?<![\w\].])(?P<a>\[?\w+\]?)\.(?P<c>\[?\w+\]?)(?![\w(])")
_RENAME = re.compile(r"(?:(?P<a>\[?\w+\]?)\.)?(?P<c>\[?\w+\]?)\s+as\s+(?P<n>\[?\w+\]?)", re.I)


def _with_regex(sql: str, rel: dict[str, str] | None = None) -> dict[str, Any]:
    text = strip_comments(sql)
    if rel is None:
        rel = {r.name: r.relation for r in extract_refs(sql)}
    tables: dict[str, dict[str, Any]] = {}
    alias_to: dict[str, str] = {}
    for m in _TABLE_ALIAS.finditer(text):
        name = normalise(m.group("t"))
        if not name or (name not in rel and "." not in name and name in _KEYWORDS):
            continue
        alias = m.group("a")
        if alias and alias.lower() in _KEYWORDS:
            alias = None
        tables.setdefault(name, {"name": name, "alias": alias, "relation": rel.get(name, "reads")})
        alias_to[(alias or name.split(".")[-1]).lower()] = name
        alias_to.setdefault(name.split(".")[-1], name)
    for name, r in rel.items():
        if r != "calls":
            tables.setdefault(name, {"name": name, "alias": None, "relation": r})
            alias_to.setdefault(name.split(".")[-1], name)

    def unq(x: str) -> str:
        return x.strip("[]")

    for m in re.finditer(r"\(\s*select\b(?P<body>(?:[^()]|\([^()]*\))*)\)\s*(?:as\s+)?(?P<a>\w+)", text, re.I):
        inside = {normalise(t["t"]) for t in _TABLE_ALIAS.finditer(m["body"])} - {None}
        if len(inside) == 1 and m["a"].lower() not in _KEYWORDS:  # (SELECT ... FROM dbo.Product) p
            alias_to.setdefault(m["a"].lower(), inside.pop())
    # comma lists: FROM dbo.Sales s, dbo.Product p WHERE s.ProductID = p.ProductID
    for m in re.finditer(r"\bfrom\b(?P<list>[^()]*?)(?=\bwhere\b|\bgroup\b|\border\b|\bjoin\b|\bunion\b|\)|;|$)",
                         text, re.I | re.S):
        if "," not in m["list"]:
            continue
        for part in m["list"].split(","):
            got = re.fullmatch(rf"\s*(?P<t>{_IDENT})(?:\s+(?:as\s+)?(?P<a>\w+))?\s*", part, re.I)
            name = normalise(got["t"]) if got else None
            if not name or name in _KEYWORDS:
                continue
            alias = got["a"] if got["a"] and got["a"].lower() not in _KEYWORDS else None
            tables.setdefault(name, {"name": name, "alias": alias, "relation": rel.get(name, "reads")})
            alias_to[(alias or name.split(".")[-1]).lower()] = name
            alias_to.setdefault(name.split(".")[-1], name)

    joins = []
    on_spans = []
    for block in _ON_BLOCK.finditer(text):
        on_spans.append(block.span("cond"))
    for m in _JOIN_EQ.finditer(text):
        lt, rt = alias_to.get(unq(m["la"]).lower()), alias_to.get(unq(m["ra"]).lower())
        if lt and rt and lt != rt:
            in_on = any(a <= m.start() < b for a, b in on_spans)
            joins.append({"left_table": lt, "left_column": unq(m["lc"]), "right_table": rt,
                          "right_column": unq(m["rc"]), "kind": "inner" if in_on else "where"})
    columns: dict[str, set[str]] = {}
    for m in _DOTTED.finditer(text):
        t = alias_to.get(unq(m["a"]).lower())
        if t and unq(m["c"]).lower() not in {x.split(".")[-1] for x in tables}:
            columns.setdefault(t, set()).add(unq(m["c"]))
    single = next(iter(tables)) if len(tables) == 1 else None
    renames = []
    select = re.search(r"\bselect\b(?P<s>.*?)\bfrom\b", text, re.I | re.S)
    if select:
        for m in _RENAME.finditer(select.group("s")):
            col, new = unq(m["c"]), unq(m["n"])
            if col.lower() != new.lower() and not col.isdigit() and col.lower() not in _KEYWORDS:
                renames.append({"table": alias_to.get(unq(m["a"]).lower()) if m["a"] else single,
                                "column": col, "alias": new})
    return {"tables": list(tables.values()), "joins": _dedupe(joins),
            "columns": {k: sorted(v, key=str.lower) for k, v in columns.items()}, "renames": renames,
            "select_star": bool(re.search(r"\bselect\s+(?:top\s*\(?\d+\)?\s+)?(?:distinct\s+)?(?:\w+\.)?\*", text, re.I)),
            "parser": "regex", "error": None, "version": VERSION}


def _dedupe(joins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for j in joins:
        k = tuple(sorted([(j["left_table"], j["left_column"].lower()), (j["right_table"], j["right_column"].lower())]))
        if k not in seen:
            seen.add(k)
            out.append(j)
    return out
