"""Read the shape of a SQL query: the tables it uses (with their aliases), the columns it uses from
each, the exact fields it joins on, and the columns it renames (`Units AS qty`).

Uses sqlglot when it is installed (`pip install sqlglot`), which understands T-SQL properly.
Without it, a regex pass gives the same fields less precisely, so nothing breaks: the app says
"install sqlglot for more exact SQL reading" where it matters.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from .sqlrefs import _IDENT, _KEYWORDS, extract_refs, normalise, strip_comments

try:  # optional
    import sqlglot
    from sqlglot import exp
except Exception:  # pragma: no cover - depends on what is installed
    sqlglot = None
    exp = None


VERSION = 2  # bump when the reading changes, so saved analyses are read again


def available() -> bool:
    return sqlglot is not None


def analyse(sql: str) -> dict[str, Any]:
    """{"tables": [{"name", "alias", "relation"}], "joins": [{"left_table", "left_column", "right_table",
    "right_column", "kind"}], "columns": {table: [columns]}, "renames": [{"table", "column", "alias"}],
    "select_star": bool, "parser": "sqlglot" | "regex", "error": str | None}. Never raises."""
    return dict(_analyse(sql or ""))


@lru_cache(maxsize=2048)
def _analyse(sql: str) -> tuple:
    out = None
    if sqlglot is not None:
        try:
            out = _with_sqlglot(sql)
        except Exception as e:  # sqlglot can't read every dialect quirk; fall back
            out = {**_with_regex(sql), "error": f"{type(e).__name__}: {str(e)[:200]}"}
    if out is None:
        out = _with_regex(sql)
    return tuple(out.items())


def _name(t) -> str | None:
    parts = [p for p in (t.catalog, t.db, t.name) if p]
    return normalise(".".join(parts)) if parts else None


def _with_sqlglot(sql: str) -> dict[str, Any]:
    statements = [s for s in sqlglot.parse(strip_comments(sql), read="tsql") if s is not None]
    rel = {(r.name): r.relation for r in extract_refs(sql)}
    ctes: set[str] = set()
    tables: dict[str, dict[str, Any]] = {}
    alias_to: dict[str, str] = {}
    columns: dict[str, set[str]] = {}
    joins: list[dict[str, Any]] = []
    renames: list[dict[str, Any]] = []
    select_star = False
    for st in statements:
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

    def column_in(side) -> "exp.Column | None":
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
            if t and c.name:
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


def _with_regex(sql: str) -> dict[str, Any]:
    text = strip_comments(sql)
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
