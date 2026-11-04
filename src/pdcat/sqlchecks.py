"""Checks on the SQL in the query library: things that can give wrong results, run slowly, or are risky.

Each check is a simple pattern on the SQL text (comments and string contents are ignored where it
matters), so it can be wrong; a finding you mark "fine here" is not raised again for that query.
Every check says why it matters and links to the matching SQL Server article in Microsoft's docs
(the SQL Server good-practice starter pack in Knowledge carries the same topics).

Wrong-result and risky findings go on the To review list, one item per check; slow-pattern findings show on
the query's page and, together and worst first (by the size of the tables read), on Review › Slow SQL (2.10).
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .sqlrefs import strip_comments
from .store import Catalogue, now

DOCS = "https://learn.microsoft.com/en-us/sql/"
SEVERITY_ICONS = {"wrong": "❗", "risky": "⚠️", "slow": "🐢"}
SEVERITY_LABELS = {"wrong": "may give wrong results", "risky": "risky", "slow": "may be slow"}

# rule -> (severity, title, why, article)
RULES: dict[str, tuple[str, str, str, str]] = {
    "nolock": ("wrong", "Reads with NOLOCK",
               "NOLOCK (READ UNCOMMITTED) can read rows twice, skip rows, or read changes that are later rolled "
               "back, so totals can be off while the table is being written.",
               DOCS + "t-sql/queries/hints-transact-sql-table"),
    "null_compare": ("wrong", "Compares with = NULL",
                     "`= NULL` and `<> NULL` are never true. Use IS NULL or IS NOT NULL.",
                     DOCS + "t-sql/queries/is-null-transact-sql"),
    "not_in_subquery": ("wrong", "NOT IN with a subquery",
                        "If the subquery returns any NULL, NOT IN returns no rows at all. NOT EXISTS is safer.",
                        DOCS + "t-sql/language-elements/in-transact-sql"),
    "left_join_where": ("wrong", "LEFT JOIN filtered in WHERE",
                        "A WHERE condition on the right-hand table of a LEFT JOIN drops the rows with no match, "
                        "so it behaves like an INNER JOIN. Move the condition into the ON clause if you meant "
                        "to keep them.", DOCS + "relational-databases/performance/joins"),
    "top_without_order": ("wrong", "TOP without ORDER BY",
                          "Without ORDER BY, which rows TOP returns can change from run to run.",
                          DOCS + "t-sql/queries/top-transact-sql"),
    "between_datetime": ("wrong", "BETWEEN on dates",
                         "BETWEEN '2026-01-01' AND '2026-01-31' misses anything after midnight on the last day "
                         "when the column has times. `>= start AND < next day` is safer.",
                         DOCS + "t-sql/language-elements/between-transact-sql"),
    "comma_join": ("wrong", "Old-style comma join",
                   "FROM a, b with the join in WHERE is easy to get wrong: a missing condition multiplies rows. "
                   "Use explicit JOIN ... ON.", DOCS + "relational-databases/performance/joins"),
    "distinct_join": ("wrong", "SELECT DISTINCT over joins",
                      "DISTINCT can hide a join that multiplies rows. Check the join keys give one match.",
                      DOCS + "t-sql/queries/select-transact-sql"),
    "join_no_condition": ("wrong", "JOIN without ON",
                          "A join with no join condition pairs every row with every row. Add ON, or write CROSS "
                          "JOIN if that's what you mean.", DOCS + "relational-databases/performance/joins"),
    "divide_no_guard": ("wrong", "Division without a zero guard",
                        "Dividing by a column fails (or gives NULL with ANSI settings off) when it's 0. "
                        "NULLIF(x, 0) avoids the error.", DOCS + "t-sql/functions/nullif-transact-sql"),
    "non_unique_join": ("wrong", "Join on columns that aren't unique",
                        "Neither side of the join is unique (from the profiles), so rows multiply.",
                        DOCS + "relational-databases/performance/joins"),
    "type_mismatch": ("wrong", "Join compares different types",
                      "The joined columns have different data types, so SQL Server converts one side: values can "
                      "fail to match, and indexes may not be used.",
                      DOCS + "t-sql/data-types/data-type-conversion-database-engine"),
    "missing_object": ("wrong", "Table not found",
                       "This table isn't in the scanned database any more (or never was). It may have been dropped "
                       "or renamed.", DOCS + "t-sql/statements/drop-table-transact-sql"),
    "deprecated_object": ("wrong", "Uses a table marked deprecated",
                          "The table is marked deprecated or retired in the catalogue; see its page for what replaces "
                          "it.", DOCS + "t-sql/statements/drop-table-transact-sql"),
    "union_not_all": ("slow", "UNION without ALL",
                      "UNION removes duplicates, which means sorting everything. UNION ALL is faster when there "
                      "can't be duplicates or they don't matter.", DOCS + "t-sql/language-elements/set-operators-union-transact-sql"),
    "hard_coded_date": ("risky", "Hard-coded date",
                        "A fixed date in the SQL goes stale. Use a parameter or a date worked out from today.",
                        DOCS + "t-sql/functions/date-and-time-data-types-and-functions-transact-sql"),
    "missing_schema": ("risky", "Table without a schema name",
                       "Without the schema (dbo.), SQL Server looks the name up in the user's default schema first, "
                       "which can pick a different table and stops plans being reused.",
                       DOCS + "relational-databases/security/authentication-access/ownership-and-user-schema-separation"),
    "function_on_column": ("slow", "Function on a column in WHERE or JOIN",
                           "Wrapping a column in a function (YEAR(), CONVERT(), ISNULL()...) stops SQL Server using "
                           "an index on it. Compare the bare column with a range instead.",
                           DOCS + "relational-databases/sql-server-index-design-guide"),
    "leading_wildcard": ("slow", "LIKE starting with %",
                         "LIKE '%text' can't use an index, so the whole table is read.",
                         DOCS + "t-sql/language-elements/like-transact-sql"),
    "select_star": ("slow", "SELECT *",
                    "Reads every column, including ones you don't need, and changes shape when the table does.",
                    DOCS + "t-sql/queries/select-transact-sql"),
    "cursor": ("slow", "Cursor",
               "Cursors work row by row. A set-based query is usually much faster.",
               DOCS + "t-sql/language-elements/declare-cursor-transact-sql"),
    "built_from_variables": ("risky", "SQL built from variables",
                             "The SQL text is put together from variables (an f-string, + or .format). If any value "
                             "can come from outside, that's SQL injection; pass values as parameters instead.",
                             DOCS + "relational-databases/security/sql-injection"),
    "dynamic_sql": ("risky", "Dynamic SQL (EXEC of a string)",
                    "EXEC(@sql) or sp_executesql with joined-up text runs whatever the string holds. "
                    "Use parameters for values.", DOCS + "relational-databases/security/sql-injection"),
    "no_where_change": ("risky", "UPDATE or DELETE without WHERE",
                        "Changes every row in the table.", DOCS + "t-sql/statements/delete-transact-sql"),
    "drop_or_truncate": ("risky", "Drops or empties a table",
                         "DROP TABLE or TRUNCATE TABLE on a permanent table removes its data.",
                         DOCS + "t-sql/statements/truncate-table-transact-sql"),
    "linked_server": ("risky", "Uses a linked server (four-part name)",
                      "Queries across servers depend on the link and can pull whole tables across the network.",
                      DOCS + "relational-databases/linked-servers/linked-servers-database-engine"),
    # Version 2.10: more slow patterns. Text checks first, then ones that need the catalogue.
    "select_subquery": ("slow", "Subquery in the SELECT list",
                        "A (SELECT ...) inside the column list usually runs once for every row returned. A join, "
                        "or a window function such as ROW_NUMBER() or SUM() OVER (...), works on the whole set at "
                        "once.", DOCS + "relational-databases/performance/subqueries"),
    "scalar_udf": ("slow", "Calls a user function per row",
                   "A schema-named function (dbo.fnSomething(...)) in the column list, WHERE or JOIN runs once per "
                   "row and hides its work from the optimiser. Inline the logic or use an inline table-valued "
                   "function with CROSS APPLY.", DOCS + "relational-databases/user-defined-functions/scalar-udf-inlining"),
    "or_across_columns": ("slow", "OR across different columns",
                          "WHERE a = 1 OR b = 2 (or a join ON ... OR ...) usually stops SQL Server seeking on one "
                          "index, so it reads the whole table. Two queries joined with UNION ALL, or IN (...) on one "
                          "column, can seek.", DOCS + "relational-databases/sql-server-index-design-guide"),
    "order_in_view": ("slow", "ORDER BY inside a view or subquery",
                      "ORDER BY in a view or subquery (often with TOP 100 PERCENT) sorts for nothing: the order isn't "
                      "kept in the result. Sort in the query that reads it.",
                      DOCS + "t-sql/queries/select-order-by-clause-transact-sql"),
    "join_on_expression": ("slow", "Join on a worked-out value",
                           "Joining on joined-up text or arithmetic (a.x + a.y = b.z) can't use an index on either "
                           "side. Store the combined key in a column, or join on the parts.",
                           DOCS + "relational-databases/performance/joins"),
    "count_for_exists": ("slow", "COUNT(*) used to test for rows",
                         "COUNT(*) > 0 counts every matching row just to see if there is one. EXISTS (SELECT 1 ...) "
                         "stops at the first.", DOCS + "t-sql/language-elements/exists-transact-sql"),
    "long_in_list": ("slow", "Very long IN (...) list",
                     "Hundreds of values in IN (...) make a slow, hard-to-cache plan. Put the values in a table (or "
                     "a temp table) and join to it.", DOCS + "t-sql/language-elements/in-transact-sql"),
    "query_hints": ("slow", "Forces a plan with hints",
                    "Index hints, FORCESEEK, LOOP/HASH/MERGE JOIN or OPTION (FORCE ORDER) fix the plan, which can "
                    "help once and hurt after the data grows. Check it still helps.",
                    DOCS + "t-sql/queries/hints-transact-sql"),
    "implicit_conversion": ("slow", "Text column compared with a number",
                            "The column holds text but is compared with a number, so SQL Server converts every row's "
                            "value before comparing: no index seek, and text that isn't a number makes the query "
                            "fail. Compare with a quoted value.",
                            DOCS + "t-sql/data-types/data-type-conversion-database-engine"),
    "nested_views": ("slow", "Views built on views",
                     "This reads a view that reads other views, three or more levels deep. The optimiser often gives "
                     "up on deep stacks and reads tables it doesn't need. Read the tables directly, or flatten the "
                     "views.", DOCS + "relational-databases/views/views"),
    "big_table_no_filter": ("slow", "Reads a big table with no filter",
                            "The query reads a large table (from your scans' row counts) with no WHERE or TOP, so "
                            "every row is read. Filter to what you need.",
                            DOCS + "t-sql/queries/where-transact-sql"),
}

# Severity order for sorting findings.
SEVERITY_ORDER = {"wrong": 0, "risky": 1, "slow": 2}
BIG_TABLE_ROWS = 1_000_000   # default for "Reads a big table with no filter" (a Settings value on Slow SQL)
LONG_IN_LIST = 50            # values in IN (...) before it's flagged

_STRINGS = re.compile(r"'(?:[^']|'')*'")


def _code(sql: str) -> str:
    """SQL without comments, with string contents blanked, on one line, lower case."""
    text = strip_comments(sql or "")
    text = _STRINGS.sub("''", text)
    return re.sub(r"\s+", " ", text).lower()


@lru_cache(maxsize=32768)
def check_sql(sql: str) -> tuple[tuple[str, str], ...]:
    """(rule, about) pairs found in one piece of SQL. `about` says what it's about (a table, a column)."""
    raw = re.sub(r"\s+", " ", strip_comments(sql or "")).lower()
    code = _code(sql)
    found: list[tuple[str, str]] = []
    if re.search(r"\bwith\s*\(\s*nolock\s*\)|\(\s*nolock\s*\)|\bread\s*uncommitted\b", code):
        found.append(("nolock", ""))
    if re.search(r"(?<![<>!])(=|<>|!=)\s*null\b", code):
        found.append(("null_compare", ""))
    if re.search(r"\bnot\s+in\s*\(\s*select\b", code):
        found.append(("not_in_subquery", ""))
    for alias in _left_join_aliases(code):
        where = re.search(r"\bwhere\b(.*?)(?:\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\bunion\b|$)", code)
        if where:
            cond = where.group(1)
            for m in re.finditer(rf"\b{re.escape(alias)}\.(\w+)\s*(=|<>|!=|>=|<=|>|<|\blike\b|\bin\b)", cond):
                found.append(("left_join_where", f"{alias}.{m.group(1)}"))
                break
    if re.search(r"\bselect\s+(distinct\s+)?top\s*\(?\s*\d+", code) and not re.search(r"\border\s+by\b", code):
        found.append(("top_without_order", ""))
    if re.search(r"\bbetween\s+'\d{4}-?\d{2}-?\d{2}'\s+and\s+'\d{4}-?\d{2}-?\d{2}'", raw):
        found.append(("between_datetime", ""))
    if re.search(r"\bfrom\s+[\w\[\]\.]+(\s+(as\s+)?\w+)?\s*,\s*[\w\[\]\.]+", code) and " join " not in code:
        found.append(("comma_join", ""))
    if re.search(r"\bselect\s+distinct\b", code) and re.search(r"\bjoin\b", code):
        found.append(("distinct_join", ""))
    for m in re.finditer(r"\b(?:where|and|or|on)\s+(year|month|day|convert|cast|isnull|coalesce|upper|lower|"
                         r"ltrim|rtrim|trim|left|right|substring|datepart|format)\s*\(", code):
        found.append(("function_on_column", m.group(1).upper()))
        break
    if re.search(r"\blike\s+n?'%", raw):
        found.append(("leading_wildcard", ""))
    if re.search(r"\bselect\s+(distinct\s+)?(top\s*\(?\s*\d+\s*\)?\s+)?(\w+\.)?\*", code) \
            and not re.search(r"\bexists\s*\(\s*select\s+\*", code):
        found.append(("select_star", ""))
    if re.search(r"\bcursor\s+(local\s+|global\s+|fast_forward\s+|static\s+|forward_only\s+)*for\b", code):
        found.append(("cursor", ""))
    if re.search(r"\bexec(ute)?\s*\(\s*@\w+|\bsp_executesql\b", code):
        found.append(("dynamic_sql", ""))
    for st in re.split(r";|\bgo\b", code):
        st = st.strip()
        m = re.match(r"(?:with\b.*?\)\s*)?(update\s+[\w\[\]\.#@]+\s+set\b|delete\s+(?:from\s+)?[\w\[\]\.#@]+)(.*)", st)
        if m and " where " not in f" {m.group(2)} " and not re.search(r"(update|delete\s+(from\s+)?)\s*[#@]", st):
            found.append(("no_where_change", m.group(1).split()[-2 if m.group(1).endswith(" set") else -1]))
    for m in re.finditer(r"\b(drop\s+table(?:\s+if\s+exists)?|truncate\s+table)\s+([\w\[\]\.]+)", code):
        if not m.group(2).lstrip("[").startswith("#"):
            found.append(("drop_or_truncate", m.group(2).strip("[]")))
    if re.search(r"\b(?:from|join)\s+(?:\[?\w+\]?\.){3}\[?\w+\]?", code):
        found.append(("linked_server", ""))
    for m in re.finditer(r"(?<!cross )(?<!natural )\bjoin\s+[\w\[\]\.#@]+(?:\s+(?:as\s+)?(?!on\b)\w+)?\s+"
                         r"(join|left|right|inner|full|where|group|order|union|$)", code):
        found.append(("join_no_condition", ""))
        break
    if re.search(r"\bunion\s+(?!all\b)", code):
        found.append(("union_not_all", ""))
    for m in re.finditer(r"(?<![/*])/\s*(?!\s*nullif\b)(\(?\s*(?:[a-z_\[][\w\].]*))", code):
        operand = m.group(1).strip("( ")
        if operand and not operand[0].isdigit() and not operand.startswith("nullif"):
            found.append(("divide_no_guard", operand))
            break
    if re.search(r"(=|<|>|between|and)\s*'(?:19|20)\d\d-?[01]\d-?[0-3]\d", raw):
        found.append(("hard_coded_date", ""))
    found += _slow_checks(code, raw)
    return tuple(dict.fromkeys(found))


# Words that start a part of a statement, for working out where a "(select" sits. AS is left out so an alias
# ("x AS name, (SELECT ...)") doesn't hide the SELECT list; WITH covers a CTE ("WITH x AS (SELECT").
_CLAUSE = re.compile(r"\b(select|from|where|join|on|in|exists|having|union|with|apply|values|into|by|when|then|"
                     r"else|case|any|all|some)\b")
_HINT = re.compile(r"\bwith\s*\(\s*(?:nolock\s*,\s*)?(index\s*[(=]|forceseek|forcescan)|"
                   r"\b(?:inner|left|right|full)?\s*(?:outer\s+)?(loop|hash|merge|remote)\s+join\b|"
                   r"\boption\s*\([^)]*\b(force\s+order|loop\s+join|hash\s+join|merge\s+join|maxdop|"
                   r"optimize\s+for|use\s+plan|querytraceon)\b")
_SECTION_END = r"(?=\b(?:group\s+by|order\s+by|having|union|where|join|left|right|inner|full|cross|outer|select|" \
               r"from|into|option)\b|\)|;|$)"


def _parens(code: str, start: int) -> str:
    """The text inside the brackets that open at `start` (or to the end, when they never close)."""
    depth = 0
    for i in range(start, len(code)):
        if code[i] == "(":
            depth += 1
        elif code[i] == ")":
            depth -= 1
            if depth == 0:
                return code[start + 1:i]
    return code[start + 1:]


def _flat(text: str) -> str:
    """Text with everything inside brackets taken out, so a clause's own words are left."""
    out, depth = [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


_TOKENS = re.compile(r"\(\s*select\b|\(|\)|" + _CLAUSE.pattern)


def _subquery_in_select_list(code: str) -> bool:
    """True when a (SELECT ...) sits in a SELECT list: the last clause word at its bracket depth is SELECT.
    One pass over the text, so very long SQL with many subqueries stays quick."""
    last = [""]  # the last clause word seen at each bracket depth
    for m in _TOKENS.finditer(code):
        tok = m.group(0)
        if tok.startswith("("):
            if tok != "(" and last[-1] == "select":
                return True
            last.append("select" if tok != "(" else "")
        elif tok == ")":
            if len(last) > 1:
                last.pop()
        else:
            last[-1] = m.group(1)
    return False


def _no_subqueries(code: str) -> str:
    """Text with each (SELECT ...) replaced by (), so a clause's own conditions are left (brackets kept)."""
    out, i = [], 0
    for m in re.finditer(r"\(\s*select\b", code):
        if m.start() < i:
            continue
        out.append(code[i:m.start()] + "()")
        i = m.start() + len(_parens(code, m.start())) + 2
    return "".join(out) + code[i:]


_COMPARED = r"([a-z_][\w.]*)\s*(?:=|<>|!=|>=|<=|>|<|\blike\b|\bin\b|\bis\b)"


def _or_columns(code: str) -> str | None:
    """The columns an OR in a WHERE or ON clause compares, when it spans more than one column (any OR in ON)."""
    for clause in re.finditer(r"\b(where|on)\b(.*?)" + _SECTION_END.replace("|\\)", ""), code):
        parts = re.split(r"\bor\b", clause.group(2))
        if len(parts) < 2:
            continue
        cols = set()
        for i, part in enumerate(parts):
            hits = re.findall(_COMPARED, part)
            if hits:
                cols.add((hits[-1] if i == 0 else hits[0]).split(".")[-1])
        if len(cols) > 1 or clause.group(1) == "on":
            return ", ".join(sorted(cols))[:80]
    return None


def _slow_checks(code: str, raw: str) -> list[tuple[str, str]]:
    """The 2.10 slow patterns on the cleaned-up text."""
    found: list[tuple[str, str]] = []
    if _subquery_in_select_list(code):
        found.append(("select_subquery", ""))
    for m in re.finditer(r"(?<![\w.@#])((?:\[?\w+\]?\.)+)\[?(\w+)\]?\s*\(", code):
        before = code[:m.start()].rstrip()
        if re.search(r"\b(from|join|apply|exec|execute|into|table|references|on\s+table)$", before) \
                or m.group(2) in ("value", "query", "nodes", "exist", "modify", "write"):  # xml / .write() methods
            continue
        if before.endswith(("create function", "alter function", "procedure", "proc")):
            continue
        found.append(("scalar_udf", (m.group(1) + m.group(2)).replace("[", "").replace("]", "")))
        break
    flat = _flat(code)
    pieces = [code] + [_parens(code, m.start()) for m in re.finditer(r"\(\s*select\b", code)]
    for piece in pieces:
        about = _or_columns(_no_subqueries(piece))
        if about is not None:
            found.append(("or_across_columns", about))
            break
    if re.search(r"\b(create|alter)\s+(or\s+alter\s+)?view\b", code) and re.search(r"\border\s+by\b", flat) \
            and not re.search(r"\boffset\b|\bfor\s+(xml|json)\b", code):
        found.append(("order_in_view", ""))
    else:
        for m in re.finditer(r"\(\s*select\b", code):
            inner = _parens(code, m.start())
            mine = _flat(inner)
            if re.search(r"\border\s+by\b", mine) and re.search(r"\btop\s*\(?\s*100\s*\)?\s*percent\b", mine):
                found.append(("order_in_view", ""))
                break
            if re.search(r"\border\s+by\b", mine) and not re.search(r"\btop\b|\boffset\b|\bfor\s+(xml|json)\b",
                                                                   mine) \
                    and not re.search(r"\bover\s*\(", inner):
                found.append(("order_in_view", ""))
                break
    for m in re.finditer(r"\bon\b(.*?)" + _SECTION_END, flat):
        cond = m.group(1)
        if re.search(r"[\w\]]\s*(\+|\|\|)\s*[\w\[']", cond) or re.search(r"\bconcat\s*\(", code[m.start():m.end() + 40]):
            found.append(("join_on_expression", ""))
            break
    test = r"\s*(?:>\s*0|>=\s*1|<>\s*0|!=\s*0|=\s*0)(?!\s*[.\d])"
    if re.search(r"\(\s*select\s+count\s*\(\s*(?:\*|1)\s*\)[^()]*(?:\([^()]*\)[^()]*)*\)" + test, code) \
            or re.search(r"(?<!having )\bcount\s*\(\s*(?:\*|1)\s*\)" + test, code):
        found.append(("count_for_exists", ""))
    for m in re.finditer(r"\bin\s*\(([^()]*)\)", raw):
        body = m.group(1)
        if "select" in body:
            continue
        n = body.count(",") + 1
        if n >= LONG_IN_LIST:
            found.append(("long_in_list", f"{n} values"))
            break
    h = _HINT.search(code)
    if h:
        found.append(("query_hints", next(g for g in h.groups() if g).upper().split("(")[0].strip()))
    return found


# Saved check results are thrown away whenever this file changes, so a new or changed check runs on every query.
_CHECKS_VERSION = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]


def _sql_hash(sql: str) -> str:
    return hashlib.sha1((sql or "").encode("utf-8", "replace")).hexdigest()


class _CheckCache:
    """check_sql results kept in the catalogue, so a re-run only checks SQL that is new or changed."""

    def __init__(self, cat: Catalogue):
        self.cat = cat
        self.known = {r[0]: r[1] for r in cat.conn.execute(
            "SELECT sql_hash, found FROM sql_check_cache WHERE version = ?", (_CHECKS_VERSION,))}
        self.new: list[tuple[str, str, str]] = []
        self.seen: set[str] = set()

    def checks(self, sql: str) -> tuple[tuple[str, str], ...]:
        h = _sql_hash(sql)
        self.seen.add(h)
        if h in self.known:
            return tuple(tuple(p) for p in json.loads(self.known[h]))
        found = check_sql(sql)
        self.known[h] = json.dumps(found)
        self.new.append((h, _CHECKS_VERSION, self.known[h]))
        return found

    def save(self, *, prune: bool = False) -> None:
        """Keep what was worked out. `prune` (after checking the whole library) drops SQL no longer in it."""
        with self.cat.conn:
            if self.new:
                self.cat.conn.executemany("INSERT OR REPLACE INTO sql_check_cache (sql_hash, version, found) "
                                          "VALUES (?, ?, ?)", self.new)
            if prune:
                gone = [(h,) for h in self.known if h not in self.seen]
                self.cat.conn.executemany("DELETE FROM sql_check_cache WHERE sql_hash = ?", gone)
                self.cat.conn.execute("DELETE FROM sql_check_cache WHERE version != ?", (_CHECKS_VERSION,))
        self.new = []


def cached_checks(cat: Catalogue, sqls: list[str]) -> list[tuple[tuple[str, str], ...]]:
    """check_sql for many pieces of SQL, using (and filling) the saved results."""
    cache = _CheckCache(cat)
    out = [cache.checks(s) for s in sqls]
    cache.save()
    return out


def _left_join_aliases(code: str) -> list[str]:
    out = []
    for m in re.finditer(r"\bleft\s+(?:outer\s+)?join\s+([\w\[\]\.]+)(?:\s+(?:as\s+)?(\w+))?", code):
        alias = m.group(2)
        if not alias or alias in ("on", "with", "where"):
            alias = m.group(1).strip("[]").split(".")[-1].strip("[]")
        out.append(alias)
    return out


def ok_marks(cat: Catalogue, uid: str | None = None) -> set[tuple[str, str, str]]:
    sql = "SELECT query_uid, rule, detail FROM sql_findings_ok" + (" WHERE query_uid = ?" if uid else "")
    return {(r[0], r[1], r[2]) for r in cat.conn.execute(sql, (uid,) if uid else ())}


def mark_ok(cat: Catalogue, uid: str, rule: str, about: str = "", *, ok: bool = True) -> None:
    """Mark a finding as fine for this query (or take that back)."""
    with cat.conn:
        if ok:
            cat.conn.execute("INSERT OR REPLACE INTO sql_findings_ok (query_uid, rule, detail, author, at) "
                             "VALUES (?, ?, ?, ?, ?)", (uid, rule, about, cat.author(), now()))
        else:
            cat.conn.execute("DELETE FROM sql_findings_ok WHERE query_uid = ? AND rule = ? AND detail = ?",
                             (uid, rule, about))
    cat.changed()


class _Lookups:
    """What the catalogue checks look up, read once per run instead of once per query and join."""

    def __init__(self, cat: Catalogue):
        self.cat = cat
        self._tables: dict | None = None
        self._columns: dict[str, dict[str, str]] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._lifecycle: dict[str, str | None] = {}
        self._rows: dict[str, int] | None = None
        self.names: dict[str, str] = {}  # table key -> its catalogued name, filled with the row counts
        self._views: dict[str, list[str]] | None = None
        self._depth: dict[str, tuple[int, list[str]]] = {}
        self.big_rows = int(cat.setting("slow_sql_big_rows", BIG_TABLE_ROWS) or BIG_TABLE_ROWS)

    @property
    def tables(self) -> dict:
        if self._tables is None:
            self._tables = self.cat.table_index()
        return self._tables

    def col_type(self, key: str, column: str) -> str | None:
        if key not in self._columns:
            self._columns[key] = {r[0].lower(): r[1] for r in self.cat.conn.execute(
                "SELECT name, data_type FROM columns WHERE asset_key = ?", (key,)) if r[1]}
        t = self._columns[key].get(column.lower())
        return t.lower().split("(")[0] if t else None

    def profile(self, key: str, column: str):
        if key not in self._profiles:
            self._profiles[key] = {r["column_name"].lower(): r for r in self.cat.conn.execute(
                "SELECT column_name, row_count, distinct_count, null_count FROM profiles WHERE asset_key = ?", (key,))}
        return self._profiles[key].get(column.lower())

    def lifecycle(self, key: str) -> str | None:
        if key not in self._lifecycle:
            r = self.cat.conn.execute("SELECT lifecycle FROM item_info WHERE asset_key = ?", (key,)).fetchone()
            self._lifecycle[key] = r[0] if r else None
        return self._lifecycle[key]


    def rows(self, key: str) -> int:
        """The table's row count from the latest scan (0 when not known)."""
        if self._rows is None:
            self._rows = {}
            for k, name, attrs in self.cat.conn.execute(
                    "SELECT key, name, attrs FROM assets WHERE kind IN ('table', 'view') AND status = 'active'"):
                self.names[k] = name
                try:
                    n = json.loads(attrs or "{}").get("row_count")
                    if isinstance(n, (int, float)) or (isinstance(n, str) and n.isdigit()):
                        self._rows[k] = int(n)
                except (ValueError, TypeError):
                    continue
        return self._rows.get(key, 0)

    @property
    def views(self) -> dict[str, list[str]]:
        """view key -> the catalogued tables and views its definition reads."""
        if self._views is None:
            self._views = {}
            for item, tables in self.cat.conn.execute(
                    "SELECT p.item, q.tables FROM sql_query_places p JOIN sql_queries q ON q.uid = p.query_uid "
                    "WHERE p.location = 'view definition' AND q.status = 'active'"):
                reads = [t["key"] for t in json.loads(tables or "[]") if t.get("key") and t.get("key") != item]
                self._views.setdefault(item, []).extend(reads)
        return self._views

    def view_depth(self, key: str, seen: tuple = ()) -> tuple[int, list[str]]:
        """How many views deep `key` goes (0 for a table), with the deepest chain of view keys."""
        if key not in self.views or key in seen:
            return 0, []
        if key not in self._depth:
            best: tuple[int, list[str]] = (0, [])
            for k in self.views[key]:
                d = self.view_depth(k, (*seen, key))
                if d[0] > best[0]:
                    best = d
            self._depth[key] = (best[0] + 1, [key, *best[1]])
        return self._depth[key]


def biggest_table(q: dict[str, Any], look: _Lookups) -> tuple[int, str]:
    """(rows, name) of the largest catalogued table the query reads, for putting the worst first."""
    best = (0, "")
    for t in q.get("tables") or []:
        if t.get("key") and t.get("relation") != "calls":
            n = look.rows(t["key"])
            if n > best[0]:
                best = (n, look.names.get(t["key"], t["name"]))
    return best


_NUMBER_COMPARE = re.compile(r"(?:\bwhere|\band|\bor|\bon|\bnot|\bhaving|\()\s*(?:(\w+)\.)?(\w+)\s*"
                             r"(?:=|<>|!=|>=|<=|>|<|\bin\s*\()\s*-?\d+(?![\w.'])")


def _slow_catalogue_checks(q: dict[str, Any], look: _Lookups) -> list[tuple[str, str]]:
    """2.10 checks that need the catalogue: hidden conversions, view stacks and big tables read whole."""
    out: list[tuple[str, str]] = []
    tables = [t for t in q.get("tables") or [] if t.get("key") and t.get("relation") != "calls"]
    if not tables:
        return out
    code = _code(q["sql"])
    by_alias: dict[str, str] = {}
    for t in tables:
        by_alias[t["name"].lower().split(".")[-1]] = t["key"]
        if t.get("alias"):
            by_alias[t["alias"].lower()] = t["key"]
    for m in _NUMBER_COMPARE.finditer(code):
        qual, col = m.group(1), m.group(2)
        keys = [by_alias[qual]] if qual and qual in by_alias else ([] if qual else [t["key"] for t in tables])
        types = {look.col_type(k, col) for k in keys} - {None}
        if len(types) == 1 and _type_family(next(iter(types))) == "text":
            shown = re.search(rf"\b{re.escape(col)}\b", q["sql"], re.I)
            out.append(("implicit_conversion", f"{shown.group(0) if shown else col} ({next(iter(types))})"))
            break
    places = q.get("places") or []
    own = [p["item"] for p in places if p.get("location") == "view definition"]
    deepest: tuple[int, list[str]] = (0, [])
    for t in tables:
        d = look.view_depth(t["key"])
        if d[0] > deepest[0]:
            deepest = d
    levels = deepest[0] + (1 if own else 0)
    if levels >= 3:
        chain = ([own[0]] if own else []) + deepest[1]
        out.append(("nested_views", " → ".join(k.rsplit("/", 1)[-1] for k in chain)))
    only_views = bool(places) and len(own) == len(places)
    if not only_views and not re.search(r"\bwhere\b|\btop\b|\boffset\b|\bfetch\b", code) \
            and re.search(r"\bselect\b", code):
        for t in tables:
            n = look.rows(t["key"])
            if t.get("relation", "reads") == "reads" and n >= look.big_rows:
                out.append(("big_table_no_filter", f"{look.names.get(t['key'], t['name'])} ({n:,} rows)"))
                break
    return out


def findings_for(cat: Catalogue, q: dict[str, Any], marks: set | None = None,
                 look: _Lookups | None = None, cache: _CheckCache | None = None) -> list[dict[str, Any]]:
    """Findings for one library query (as from sqllibrary.get), with the ones you marked fine flagged ok."""
    marks = ok_marks(cat, q["uid"]) if marks is None else marks
    pairs = list(cache.checks(q["sql"]) if cache else check_sql(q["sql"]))
    places = q.get("places") or []
    if any(p.get("built_from_variables") for p in places):
        pairs.append(("built_from_variables", ""))
    look = look or _Lookups(cat)
    pairs += _catalogue_checks(cat, q, look)
    pairs += _slow_catalogue_checks(q, look)
    out = []
    for rule, about in pairs:
        sev, title, why, article = RULES[rule]
        out.append({"rule": rule, "about": about, "severity": sev, "title": title + (f": {about}" if about else ""),
                    "detail": why, "article": article, "ok": (q["uid"], rule, about) in marks})
    return sorted(out, key=lambda f: (f["ok"], SEVERITY_ORDER[f["severity"]]))


def _catalogue_checks(cat: Catalogue, q: dict[str, Any], look: _Lookups) -> list[tuple[str, str]]:
    """Checks that need what's catalogued: missing or deprecated tables, schema names, and joins
    compared with the columns' types and profiles."""
    out: list[tuple[str, str]] = []
    tables = q.get("tables") or []
    analysis = q.get("analysis") or {}
    ctes = {c.lower() for c in re.findall(r"(?:\bwith|,)\s*(\w+)\s*(?:\([^)]*\))?\s+as\s*\(", q["sql"] or "", re.I)}
    resolved = [t for t in tables if t.get("key")]
    for t in tables:
        name = t["name"]
        if "." not in name and not name.startswith(("#", "@")) and name not in ctes and t.get("relation") != "calls":
            out.append(("missing_schema", name))
    if resolved:
        for t in resolved:
            if look.lifecycle(t["key"]) in ("deprecated", "retired"):
                out.append(("deprecated_object", t["name"]))
        # a table named in the same database as ones we know, but not found there
        known_dbs = {k.rsplit("/", 1)[0] for k in (t["key"] for t in resolved)}
        for t in tables:
            if not t.get("key") and t.get("relation") != "calls" and not t["name"].startswith(("#", "@")) \
                    and t["name"] not in ctes and t["name"].count(".") <= 1 and len(known_dbs) == 1:
                if not cat.match_table(t["name"], index=look.tables):
                    out.append(("missing_object", t["name"]))
    keys = {t["name"]: t.get("key") for t in tables}
    for j in analysis.get("joins", []):
        a, b = keys.get(j["left_table"]), keys.get(j["right_table"])
        if not a or not b:
            continue
        pa, pb = look.profile(a, j["left_column"]), look.profile(b, j["right_column"])
        if pa and pb and _not_unique(pa) and _not_unique(pb):
            out.append(("non_unique_join", f"{j['left_column']} = {j['right_column']}"))
        ta, tb = look.col_type(a, j["left_column"]), look.col_type(b, j["right_column"])
        if ta and tb and _type_family(ta) != _type_family(tb):
            out.append(("type_mismatch", f"{j['left_column']} ({ta}) = {j['right_column']} ({tb})"))
    return out


def _not_unique(p) -> bool:
    rows, distinct = p["row_count"] or 0, p["distinct_count"] or 0
    return rows > 0 and distinct > 0 and distinct < rows - (p["null_count"] or 0)


def _type_family(t: str) -> str:
    if any(x in t for x in ("char", "text", "string", "object")):
        return "text"
    if any(x in t for x in ("date", "time")):
        return "date"
    if any(x in t for x in ("int", "dec", "num", "float", "real", "money", "bit")):
        return "number"
    return t


def check_all(cat: Catalogue) -> int:
    """Check every query in the library; returns how many have open findings. (Results are worked out
    when needed, so this just warms the cache and reports.)"""
    return sum(1 for q in _all(cat) if any(not f["ok"] for f in q["findings"]))


def _all(cat: Catalogue) -> list[dict[str, Any]]:
    from .sqllibrary import all_with_places
    marks, look, cache = ok_marks(cat), _Lookups(cat), _CheckCache(cat)
    out = [{**q, "findings": findings_for(cat, q, marks, look, cache)} for q in all_with_places(cat)]
    cache.save(prune=True)
    return out


def review_tasks(cat: Catalogue, task) -> list:
    """One To review item per check (SELECT *, NOLOCK, a missing schema...) across every query with a wrong-result
    or risky finding, so a pattern you accept is answered once. Queries only found in old or not-relevant items
    are left out."""
    groups = findings_by_rule(cat)
    out = []
    for rule, g in groups.items():
        sev, title, why, _ = RULES[rule]
        queries = g["queries"]
        names = [q["name"] for q in queries[:5]]
        out.append(task("sql_check", f"sqlrule://{rule}",
                        f"SQL: {title} in {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}",
                        f"{why} For example {', '.join(names)}{' …' if len(queries) > 5 else ''}. Open a query to fix "
                        "or mark each one fine, or accept this pattern everywhere.",
                        version=str(len(queries))))
    return out


def _shown(cat: Catalogue) -> list[dict[str, Any]]:
    """Every checked query, less the ones only found in old, archived or not-relevant items."""
    from .curation import hidden_keys
    from .folderroles import archived
    from .groups import older_versions
    hidden = hidden_keys(cat, old=True) | archived(cat) | older_versions(cat)
    return [q for q in _all(cat) if not (q["places"] and all(p["item"] in hidden for p in q["places"]))]


def findings_by_rule(cat: Catalogue, severities: tuple[str, ...] = ("wrong", "risky"),
                     queries: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """rule -> {"queries": [{"uid", "name", "where", "abouts"}], "severity"} for every open finding of these
    severities (wrong-result and risky by default), queries in old or not-relevant items left out."""
    groups: dict[str, dict[str, Any]] = {}
    for q in _shown(cat) if queries is None else queries:
        serious = [f for f in q["findings"] if not f["ok"] and f["severity"] in severities]
        if not serious:
            continue
        where = (cat.get_asset(q["places"][0]["item"]) or {}).get("name", "") if q["places"] else ""
        for rule in {f["rule"] for f in serious}:
            g = groups.setdefault(rule, {"queries": [], "severity": RULES[rule][0]})
            g["queries"].append({"uid": q["uid"], "name": q["name"], "where": where,
                                 "abouts": [f["about"] for f in serious if f["rule"] == rule],
                                 "rows": q.get("rows", 0), "biggest": q.get("biggest", "")})
    return groups


def slow_sql(cat: Catalogue) -> dict[str, Any]:
    """Review › Slow SQL (2.10): every query with an open slow-pattern finding, worst first.

    Worst first means: the biggest table the query reads (row counts from your scans), then how many slow checks
    it trips, then how many places use it. Returns {"queries": [...], "by_rule": {rule: group}} where each query
    has "uid", "name", "where", "places", "rows", "biggest", "slow" (its open slow findings)."""
    look = _Lookups(cat)
    out = []
    for q in _shown(cat):
        slow = [f for f in q["findings"] if not f["ok"] and f["severity"] == "slow"]
        if not slow:
            continue
        rows, biggest = biggest_table(q, look)
        where = (cat.get_asset(q["places"][0]["item"]) or {}).get("name", "") if q["places"] else ""
        out.append({**q, "slow": slow, "rows": rows, "biggest": biggest, "where": where})
    out.sort(key=lambda q: (-q["rows"], -len(q["slow"]), -len(q["places"]), q["name"].lower()))
    by_rule = findings_by_rule(cat, ("slow",), out)
    return {"queries": out, "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -len(kv[1]["queries"])))}


def slow_csv_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per open slow finding, for the CSV download."""
    return [{"query": q["name"], "where": q["where"], "check": f["title"], "why": f["detail"],
             "biggest table": q["biggest"], "rows": q["rows"] or "", "places": len(q["places"]), "uid": q["uid"]}
            for q in result["queries"] for f in q["slow"]]


def accept_rule_everywhere(cat: Catalogue, rule: str) -> int:
    """Mark every open finding of one check fine, in every query. Returns how many were marked."""
    n = 0
    for q in findings_by_rule(cat, (RULES[rule][0],)).get(rule, {}).get("queries", []):
        for about in q["abouts"]:
            mark_ok(cat, q["uid"], rule, about)
            n += 1
    return n


def summary(cat: Catalogue) -> dict[str, int]:
    """How many queries have each kind of finding (not counting ones marked fine)."""
    out = {"wrong": 0, "risky": 0, "slow": 0}
    for q in _all(cat):
        for sev in {f["severity"] for f in q["findings"] if not f["ok"]}:
            out[sev] += 1
    return out
