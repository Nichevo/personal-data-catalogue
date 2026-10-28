"""Checks on the SQL in the query library: things that can give wrong results, run slowly, or are risky.

Each check is a simple pattern on the SQL text (comments and string contents are ignored where it
matters), so it can be wrong; a finding you mark "fine here" is not raised again for that query.
Every check says why it matters and links to the matching SQL Server article in Microsoft's docs
(the SQL Server good-practice starter pack in Knowledge carries the same topics).

Wrong-result and risky findings go on the To review list, one item per query; slow-pattern
findings show on the query's page.
"""
from __future__ import annotations

import re
from functools import lru_cache
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
}

_STRINGS = re.compile(r"'(?:[^']|'')*'")


def _code(sql: str) -> str:
    """SQL without comments, with string contents blanked, on one line, lower case."""
    text = strip_comments(sql or "")
    text = _STRINGS.sub("''", text)
    return re.sub(r"\s+", " ", text).lower()


@lru_cache(maxsize=4096)
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
    return tuple(dict.fromkeys(found))


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


def findings_for(cat: Catalogue, q: dict[str, Any], marks: set | None = None) -> list[dict[str, Any]]:
    """Findings for one library query (as from sqllibrary.get), with the ones you marked fine flagged ok."""
    marks = ok_marks(cat, q["uid"]) if marks is None else marks
    pairs = list(check_sql(q["sql"]))
    places = q.get("places") or []
    if any(p.get("built_from_variables") for p in places):
        pairs.append(("built_from_variables", ""))
    out = []
    for rule, about in pairs:
        sev, title, why, article = RULES[rule]
        out.append({"rule": rule, "about": about, "severity": sev, "title": title + (f": {about}" if about else ""),
                    "detail": why, "article": article, "ok": (q["uid"], rule, about) in marks})
    order = {"wrong": 0, "risky": 1, "slow": 2}
    return sorted(out, key=lambda f: (f["ok"], order[f["severity"]]))


def check_all(cat: Catalogue) -> int:
    """Check every query in the library; returns how many have open findings. (Results are worked out
    when needed, so this just warms the cache and reports.)"""
    return sum(1 for q in _all(cat) if any(not f["ok"] for f in q["findings"]))


def _all(cat: Catalogue) -> list[dict[str, Any]]:
    from .sqllibrary import all_with_places
    marks = ok_marks(cat)
    return [{**q, "findings": findings_for(cat, q, marks)} for q in all_with_places(cat)]


def review_tasks(cat: Catalogue, task) -> list:
    """One To review item per query with wrong-result or risky findings, unless everywhere it's found
    is marked old or not relevant."""
    from .curation import hidden_keys
    from .folderroles import archived
    from .groups import older_versions
    hidden = hidden_keys(cat, old=True) | archived(cat) | older_versions(cat)
    out = []
    for q in _all(cat):
        if q["places"] and all(p["item"] in hidden for p in q["places"]):
            continue
        serious = [f for f in q["findings"] if not f["ok"] and f["severity"] in ("wrong", "risky")]
        if not serious:
            continue
        where = (cat.get_asset(q["places"][0]["item"]) or {}).get("name", "") if q["places"] else ""
        out.append(task("sql_check", f"query://{q['uid']}",
                        f"SQL “{q['name']}”: " + ", ".join(f["title"] for f in serious[:3])
                        + (" …" if len(serious) > 3 else ""),
                        (f"In {where}. " if where else "") + " ".join(f["detail"] for f in serious[:2])
                        + " Open the query to see each one, or mark it fine here.",
                        version=",".join(sorted(f"{f['rule']}{f['about']}" for f in serious))))
    return out


def summary(cat: Catalogue) -> dict[str, int]:
    """How many queries have each kind of finding (not counting ones marked fine)."""
    out = {"wrong": 0, "risky": 0, "slow": 0}
    for q in _all(cat):
        for sev in {f["severity"] for f in q["findings"] if not f["ok"]}:
            out[sev] += 1
    return out
