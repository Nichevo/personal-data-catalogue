"""Pull table references out of SQL text.

This is a pragmatic regex pass, not a full SQL parser. It finds the tables a
query reads from and writes to, skips temp tables, table variables, CTE names
and anything built dynamically (shown as "{}" in f-strings).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# One identifier part: [bracketed], "quoted" or a plain word. Up to four parts: server.db.schema.name
_PART = r'(?:\[[^\]\n]+\]|"[^"\n]+"|[#@{}\w]+)'
_IDENT = rf"{_PART}(?:\s*\.\.?\s*{_PART}){{0,3}}"  # ".." = default schema

_WRITE_PATTERNS = [
    rf"\binsert\s+(?:into\s+)?(?P<t>{_IDENT})",
    rf"\bselect\b[^;]*?\binto\s+(?P<t>{_IDENT})",
    rf"\bupdate\s+(?P<t>{_IDENT})\s+set\b",
    rf"\bdelete\s+(?:from\s+)?(?P<t>{_IDENT})",
    rf"\bmerge\s+(?:into\s+)?(?P<t>{_IDENT})",
    rf"\btruncate\s+table\s+(?P<t>{_IDENT})",
    rf"\bcreate\s+table\s+(?P<t>{_IDENT})",
    rf"\bdrop\s+table\s+(?:if\s+exists\s+)?(?P<t>{_IDENT})",
]
_READ_PATTERN = rf"\b(?:from|join)\s+(?P<t>{_IDENT})"
_MERGE_SOURCE = rf"\bmerge\b[^;]*?\busing\s+(?P<t>{_IDENT})"
_CALL_PATTERN = rf"\bexec(?:ute)?\s+(?P<t>{_IDENT})"
_CTE_PATTERN = r"(?:\bwith|,)\s*(\w+)\s*(?:\([^)]*\))?\s+as\s*\("
_ALIAS_PATTERN = rf"\b(?:from|join|update|into)\s+(?P<t>{_IDENT})\s+(?:as\s+)?(?P<a>\w+)"
_KEYWORDS = {"where", "on", "join", "inner", "left", "right", "full", "outer", "cross", "group", "order",
             "having", "union", "set", "with", "as", "select", "values", "output", "using", "when", "pivot",
             "unpivot", "apply", "except", "intersect", "option", "for", "go", "and", "or", "then", "into"}

_SQL_SHAPE = re.compile(
    r"\bselect\b[\s\S]*?\bfrom\b|\binsert\s+into\b|\bupdate\b[\s\S]+?\bset\b|\bdelete\s+from\b"
    r"|\bmerge\b[\s\S]*?\busing\b|\btruncate\s+table\b|\bcreate\s+table\b|\bexec(?:ute)?\s+[\w\[]",
    re.IGNORECASE,
)
# Words that follow FROM in English or in Python rather than in SQL.
_NOT_TABLES = {"the", "a", "an", "this", "that", "these", "those", "each", "which", "here", "there",
               "it", "them", "set", "values", "select", "where", "dual", "openquery", "openrowset",
               "unnest", "lateral", "string_split", "openjson"}


@dataclass(frozen=True)
class SqlRef:
    relation: str  # "reads", "writes" or "calls"
    name: str      # normalised, lower-case, dot-separated, e.g. "dbo.sales"


def looks_like_sql(text: str) -> bool:
    return bool(_SQL_SHAPE.search(text))


# Templates around SQL: dbt/Jinja ({{ ref('sales') }}, {% if %}), SQLCMD ($(Db)) and ${var} substitutions.
_JINJA_COMMENT = re.compile(r"\{#[\s\S]*?#\}")
_JINJA_BLOCK = re.compile(r"\{%-?[\s\S]*?-?%\}")
_JINJA_REF = re.compile(r"""\{\{-?\s*ref\s*\(\s*(?:['"][^'"]*['"]\s*,\s*)?['"](?P<t>[^'"]+)['"]\s*\)\s*-?\}\}""", re.I)
_JINJA_SOURCE = re.compile(
    r"""\{\{-?\s*source\s*\(\s*['"](?P<s>[^'"]+)['"]\s*,\s*['"](?P<t>[^'"]+)['"]\s*\)\s*-?\}\}""", re.I)
_JINJA_EXPR = re.compile(r"\{\{[\s\S]*?\}\}")
_SUBST_VAR = re.compile(r"\$\(\s*\w+\s*\)|\$\{\s*[\w.]+\s*\}")
_FORMAT_FIELD = re.compile(r"\{[\w.!:\[\]]*\}")  # "{schema}", "{0}", "{}" from str.format
# Cursor steps name the cursor, not a table: FETCH NEXT FROM c INTO @id
_CURSOR_FETCH = re.compile(r"\bfetch\s+(?:(?:next|prior|first|last|absolute\s+\S+|relative\s+\S+)\s+)?"
                           r"(?:from\s+)?(?:global\s+)?[@\w]+", re.I)


def expand_templates(sql: str) -> tuple[str, bool]:
    """Fill in what templates can tell (dbt `{{ ref('sales') }}` -> sales, `{{ source('erp', 'orders') }}` ->
    erp.orders), drop template logic (`{% if %}`), and turn every other substitution (`{{ var }}`, `$(Db)`,
    `${env}`, `{schema}`) into "{}". Returns the text and whether it held template variables (not counting
    plain "{}", which stays as strict as before)."""
    if "{" not in sql and "$" not in sql:
        return sql, False
    text = _JINJA_COMMENT.sub(" ", sql)
    text = _JINJA_BLOCK.sub(" ", text)
    text = _JINJA_REF.sub(lambda m: m["t"], text)
    text = _JINJA_SOURCE.sub(lambda m: f"{m['s']}.{m['t']}", text)
    templated = text != sql
    text, n = _JINJA_EXPR.subn("{}", text)
    text, m = _SUBST_VAR.subn("{}", text)
    text = _FORMAT_FIELD.sub("{}", text)
    return text, bool(templated or n or m)


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*[\s\S]*?\*/", " ", sql)
    return re.sub(r"--[^\n]*", " ", sql)


def normalise(ident: str, dynamic: bool = False) -> str | None:
    """'[dbo].[Sales]' -> 'dbo.sales'. Returns None for temp tables, variables and dynamic names.

    With dynamic=True (SQL that code builds from variables it couldn't fill in), an unknown database or schema
    is left out ("{}.sales" -> "sales") and a partly known name is kept as a pattern ("dbo.sales_{}")."""
    parts = [p.strip().strip("[]\"").strip() for p in re.split(r"\s*\.\s*", ident.strip())]
    if not parts or any(not p for p in parts):
        # "db..table" means the default schema
        parts = [p if p else "dbo" for p in parts]
    if dynamic:
        if not parts[-1].replace("{}", "").strip("_-"):
            return None  # the whole name is a variable
        unknown = [i for i, p in enumerate(parts[:-1]) if "{" in p]
        if unknown:
            parts = parts[unknown[-1] + 1:]  # "{}.sales" and "db.{}.sales" -> "sales"
    if any(p.startswith(("#", "@")) for p in parts) or any("{" in p or "}" in p for p in parts[:-1]) \
            or (not dynamic and ("{" in parts[-1] or "}" in parts[-1])):
        return None
    if len(parts) == 1 and parts[0].lower() in _NOT_TABLES:
        return None
    if parts[-1].isdigit():
        return None
    return ".".join(p.lower() for p in parts)


def extract_refs(sql: str, dynamic: bool = False) -> list[SqlRef]:
    """Return the distinct tables read, written and procedures called by a piece of SQL. dynamic: see normalise."""
    sql, templated = expand_templates(sql)
    dynamic = dynamic or templated  # a template variable is a name filled in later, like a Python variable
    sql = _CURSOR_FETCH.sub(" ", strip_comments(sql))
    ctes = {m.group(1).lower() for m in re.finditer(_CTE_PATTERN, sql, re.IGNORECASE)}
    # "UPDATE f SET ... FROM dbo.Forecast f" writes to dbo.Forecast, so map aliases back to tables.
    aliases = {}
    for m in re.finditer(_ALIAS_PATTERN, sql, re.IGNORECASE):
        alias = m.group("a").lower()
        if alias not in _KEYWORDS and (target := normalise(m.group("t"), dynamic)):
            aliases[alias] = target
    refs: dict[tuple[str, str], None] = {}
    write_spans: set[int] = set()

    def add(relation: str, ident: str) -> None:
        name = normalise(ident, dynamic)
        if name and name != aliases.get(name, name):
            name = aliases[name]
        if name and name not in ctes:
            refs[(relation, name)] = None

    for pattern in _WRITE_PATTERNS:
        for m in re.finditer(pattern, sql, re.IGNORECASE):
            write_spans.add(m.start("t"))
            add("writes", m.group("t"))
    for m in re.finditer(_READ_PATTERN, sql, re.IGNORECASE):
        if m.start("t") not in write_spans:
            add("reads", m.group("t"))
    for m in re.finditer(_MERGE_SOURCE, sql, re.IGNORECASE):
        add("reads", m.group("t"))
    for m in re.finditer(_CALL_PATTERN, sql, re.IGNORECASE):
        add("calls", m.group("t"))
    return [SqlRef(rel, name) for rel, name in refs]
